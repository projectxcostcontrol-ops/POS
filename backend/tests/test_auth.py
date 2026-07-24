"""
Tests for V3 step 3.1 - roles, capabilities, and user/invite storage.
Offline, in-memory. Run with:

    cd backend
    python tests/test_auth.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store
from core.auth import can, OWNER, MANAGER, STAFF, ROLES

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


def test_owner_can_do_everything():
    section("Owner has every capability")
    for cap in ["view_money", "manage_stock", "manage_receiving", "manage_recipes",
                "manage_settings", "manage_users", "all_stores"]:
        check(f"owner can {cap}", can(OWNER, cap), True)


def test_manager_runs_a_branch_but_not_the_system():
    section("Manager runs their branch including money, but can't change system settings or users")
    check("manager sees money", can(MANAGER, "view_money"), True)
    check("manager manages stock", can(MANAGER, "manage_stock"), True)
    check("manager manages receiving", can(MANAGER, "manage_receiving"), True)
    check("manager CANNOT change settings", can(MANAGER, "manage_settings"), False)
    check("manager CANNOT manage users", can(MANAGER, "manage_users"), False)
    check("manager is NOT all-stores", can(MANAGER, "all_stores"), False)


def test_staff_never_sees_money():
    section("Staff does the daily work but never sees money - the key rule of this design")
    check("staff manages stock", can(STAFF, "manage_stock"), True)
    check("staff manages receiving", can(STAFF, "manage_receiving"), True)
    check("staff manages recipes", can(STAFF, "manage_recipes"), True)
    check("staff CANNOT view money", can(STAFF, "view_money"), False)
    check("staff CANNOT change settings", can(STAFF, "manage_settings"), False)
    check("staff CANNOT manage users", can(STAFF, "manage_users"), False)


def test_unknown_role_has_nothing():
    section("An unrecognized role gets no capabilities rather than defaulting open")
    check("unknown role can't view money", can("intern", "view_money"), False)
    check("unknown role can't manage stock", can("intern", "manage_stock"), False)
    check("empty role can't do anything", can("", "manage_stock"), False)


def test_user_storage_roundtrip():
    section("Users save and load with their role and branch assignments")
    store = make_test_store()
    store.set_user("uid1", "Owner@Example.com", OWNER, "t1", [], "เจ้าของ")

    user = store.get_user("uid1")
    check("role stored", user["role"], OWNER)
    check("email lowercased", user["email"], "owner@example.com")
    check("display name kept", user["display_name"], "เจ้าของ")


def test_lookup_by_email_is_case_insensitive():
    section("Finding a user by email ignores case - people type it inconsistently")
    store = make_test_store()
    store.set_user("uid1", "manager@example.com", MANAGER, "t1", ["store-a"])

    found = store.get_user_by_email("Manager@Example.COM")
    check("found despite different case", found["uid"] if found else None, "uid1")


def test_owner_count_guards_against_lockout():
    section("Owner count is tracked, so the last owner can't be removed")
    store = make_test_store()
    check("no owners initially", store.count_owners("t1"), 0)

    store.set_user("uid1", "a@example.com", OWNER, "t1", [])
    check("one owner", store.count_owners("t1"), 1)

    store.set_user("uid2", "b@example.com", OWNER, "t1", [])
    check("two owners", store.count_owners("t1"), 2)

    store.set_user("uid3", "c@example.com", STAFF, "t1", [])
    check("staff doesn't count as an owner", store.count_owners("t1"), 2)

    # Another business's owners are not ours - the last-owner guard has to
    # be counted per business, or business A could be locked out because
    # business B happens to have owners.
    store.set_user("uid4", "d@example.com", OWNER, "t2", [])
    check("other business's owner doesn't count", store.count_owners("t1"), 2)
    check("other business counts its own", store.count_owners("t2"), 1)


def test_invite_flow():
    section("An invite is created before the person has an account, then consumed on signup")
    store = make_test_store()
    store.create_invite("tok-abc", "NewStaff@Example.com", STAFF, "t1", ["store-a"],
                        invited_by="uid1", created_at="2026-01-01T00:00:00Z")

    invite = store.get_invite("tok-abc")
    check("invite found by its token", invite["role"], STAFF)
    check("email normalized", invite["email"], "newstaff@example.com")
    check("branch assignment carried", invite["store_ids"], ["store-a"])
    check("invite belongs to a business", invite["tenant_id"], "t1")

    check("a wrong token finds nothing", store.get_invite("tok-wrong"), None)
    check("invite appears in its business's pending list", len(store.list_invites("t1")), 1)
    check("and NOT in another business's list", len(store.list_invites("t2")), 0)

    store.delete_invite("tok-abc")
    check("consumed invite is gone", store.get_invite("tok-abc"), None)


def test_store_assignment_separates_branches():
    section("A manager assigned to one branch isn't assigned to another")
    store = make_test_store()
    store.set_user("uid1", "m@example.com", MANAGER, "t1", ["store-a"])
    user = store.get_user("uid1")

    check("assigned branch present", "store-a" in user["store_ids"], True)
    check("other branch absent", "store-b" in user["store_ids"], False)


def test_all_roles_are_known():
    section("Every role in ROLES has a capability entry - no role silently does nothing")
    from core.auth import CAPABILITIES
    for role in ROLES:
        check(f"{role} has capabilities defined", role in CAPABILITIES, True)


def main():
    print("Running auth/permission tests (offline)")

    test_owner_can_do_everything()
    test_manager_runs_a_branch_but_not_the_system()
    test_staff_never_sees_money()
    test_unknown_role_has_nothing()
    test_user_storage_roundtrip()
    test_lookup_by_email_is_case_insensitive()
    test_owner_count_guards_against_lockout()
    test_invite_flow()
    test_store_assignment_separates_branches()
    test_all_roles_are_known()

    passed = sum(1 for r in _results if r)
    total = len(_results)
    print(f"\n{'=' * 50}")
    print(f"{passed}/{total} checks passed")
    if passed != total:
        print("SOME CHECKS FAILED")
        sys.exit(1)
    print("All good.")


if __name__ == "__main__":
    main()
