"""
Tests for V3 step 3.2 - multi-tenancy.

The point of these tests is a single claim: two restaurant businesses can
share one deployment and one database, and neither can see or touch the
other's data. Everything else here supports that claim.

Offline, in-memory. Run with:

    cd backend
    python tests/test_tenant.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store, FakeDb
from core.auth import OWNER, MANAGER, STAFF, is_super_admin

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


def two_businesses():
    """Two tenants sharing ONE database - which is the situation that
    actually matters. Isolation that only holds because the data is in
    different databases proves nothing."""
    db = FakeDb()
    return make_test_store("biz-a", db=db), make_test_store("biz-b", db=db)


def test_materials_do_not_leak_between_businesses():
    section("Two businesses' stock is invisible to each other")
    a, b = two_businesses()

    a.upsert_material("branch1", "m1", {"name": "หมูสับ", "unit": "kg", "cost": 120})
    b.upsert_material("branch1", "m1", {"name": "กุ้ง", "unit": "kg", "cost": 300})

    a_names = [m["name"] for m in a.list_materials("branch1")]
    b_names = [m["name"] for m in b.list_materials("branch1")]

    check("business A sees only its own material", a_names, ["หมูสับ"])
    check("business B sees only its own material", b_names, ["กุ้ง"])


def test_same_branch_id_in_two_businesses_stays_separate():
    section("Identical branch ids in different businesses are still different data")
    # This is the case that breaks a naive design: once we ship our own POS,
    # branch ids are ours to generate, so two businesses WILL both have a
    # "branch1". The tenant has to be part of the path, not just the branch.
    a, b = two_businesses()

    a.set_recipe("branch1", "ข้าวผัด", [{"material_id": "m1", "quantity": 2}])
    b.set_recipe("branch1", "ข้าวผัด", [{"material_id": "m9", "quantity": 7}])

    check("A's recipe is A's", a.get_recipe("branch1", "ข้าวผัด")[0]["quantity"], 2)
    check("B's recipe is B's", b.get_recipe("branch1", "ข้าวผัด")[0]["quantity"], 7)


def test_expenses_and_categories_are_separate():
    section("Money and categories don't cross businesses either")
    a, b = two_businesses()

    a.add_expense("branch1", "fixed", "ค่าเช่า", 30000, "2026-07-01")
    a.create_category("branch1", "ของทอด")

    check("B has no expenses of A's", b.list_expenses("branch1"), [])
    check("B has no categories of A's", b.list_categories("branch1"), [])
    check("A still has its own expense", len(a.list_expenses("branch1")), 1)


def test_loyverse_token_is_per_business():
    section("Each business connects its own Loyverse account")
    a, b = two_businesses()

    a.set_setting("loyverse_token", "token-for-a")
    check("A's token saved", a.get_setting("loyverse_token"), "token-for-a")
    check("B does NOT inherit A's token", b.get_setting("loyverse_token"), None)

    b.set_setting("loyverse_token", "token-for-b")
    check("A's token is unchanged by B connecting", a.get_setting("loyverse_token"), "token-for-a")


def test_sync_interval_is_per_business():
    section("One business changing its sync interval doesn't change another's")
    a, b = two_businesses()
    a.set_setting("sync_interval_seconds", 60)
    check("A's interval", a.get_setting("sync_interval_seconds"), 60)
    check("B keeps its own (unset)", b.get_setting("sync_interval_seconds"), None)


def test_users_are_listed_per_business():
    section("A business can't enumerate another business's staff")
    a, b = two_businesses()

    a.set_user("u1", "owner@a.com", OWNER, "biz-a")
    a.set_user("u2", "staff@a.com", STAFF, "biz-a")
    b.set_user("u3", "owner@b.com", OWNER, "biz-b")

    check("A lists 2 users", len(a.list_users("biz-a")), 2)
    check("B lists 1 user", len(b.list_users("biz-b")), 1)
    check("unscoped listing sees all 3 (admin overview only)", len(a.list_users()), 3)


def test_user_record_carries_its_tenant():
    section("A user is looked up by uid before we know their business - so the user carries it")
    a, _ = two_businesses()
    a.set_user("u1", "someone@a.com", MANAGER, "biz-a", ["branch1"])

    user = a.get_user("u1")
    check("tenant on the user record", user["tenant_id"], "biz-a")
    check("role preserved", user["role"], MANAGER)
    check("branch assignment preserved", user["store_ids"], ["branch1"])


def test_tenant_record_and_rename():
    section("The business record itself: created, read, renamed")
    a, b = two_businesses()
    tid = a.create_tenant("ร้านอาหาร ABC", owner_uid="u1", created_at="2026-07-01T00:00:00Z")
    scoped = a.for_tenant(tid)

    check("business name stored", scoped.get_tenant()["name"], "ร้านอาหาร ABC")
    scoped.update_tenant({"name": "ABC Bistro"})
    check("business renamed", scoped.get_tenant()["name"], "ABC Bistro")
    check("a business that doesn't exist reads as None",
          a.for_tenant("nope").get_tenant(), None)


def test_activity_is_recorded_once_a_day():
    section("Activity tracking writes at most once a day, not once per request")
    a, _ = two_businesses()
    tid = a.create_tenant("X", owner_uid="u1", created_at="2026-07-01T00:00:00Z")
    scoped = a.for_tenant(tid)

    scoped.touch_tenant_activity("2026-07-24")
    check("today recorded", scoped.get_tenant()["last_active_date"], "2026-07-24")

    writes = []
    original = scoped.update_tenant
    scoped.update_tenant = lambda data: (writes.append(data), original(data))[1]
    scoped.touch_tenant_activity("2026-07-24")  # same day again
    check("no second write on the same day", len(writes), 0)

    scoped.touch_tenant_activity("2026-07-25")
    check("new day updates", scoped.get_tenant()["last_active_date"], "2026-07-25")


def test_unscoped_store_refuses_to_touch_business_data():
    section("A Store with no business attached fails loudly instead of guessing")
    # Silently reading from some default location would be far worse than an
    # error: it's how one business's data ends up in another's screen.
    from storage.firestore_store import Store
    unscoped = Store.__new__(Store)
    unscoped.db = FakeDb()
    unscoped.tenant_id = None

    try:
        unscoped.list_materials("branch1")
        check("unscoped access raised", False, True)
    except RuntimeError:
        check("unscoped access raised", True, True)


def test_image_paths_are_scoped_by_business():
    section("Scanned invoice images are stored under the business, not just the branch")
    a, b = two_businesses()
    check("A's path", a.scoped_id("branch1"), "biz-a/branch1")
    check("B's path differs for the same branch id", b.scoped_id("branch1"), "biz-b/branch1")


def test_super_admin_is_not_a_role():
    section("Super admin comes from configuration, never from stored data")
    os.environ["SUPER_ADMIN_EMAILS"] = "me@example.com, other@example.com"
    check("configured email is admin", is_super_admin("Me@Example.com"), True)
    check("second configured email is admin", is_super_admin("other@example.com"), True)
    check("an owner of some business is NOT admin", is_super_admin("owner@a.com"), False)
    check("empty email is not admin", is_super_admin(""), False)

    os.environ["SUPER_ADMIN_EMAILS"] = ""
    check("with nothing configured, nobody is admin", is_super_admin("me@example.com"), False)


def test_processed_receipts_do_not_collide():
    section("Two businesses can process a receipt with the same number")
    # Loyverse receipt numbers restart per account, so "1-1001" exists in
    # both. Sharing that flag would make one business's sale silently skip
    # stock deduction in the other.
    a, b = two_businesses()
    a.mark_receipt_processed("branch1", "1-1001")

    check("A knows it processed 1-1001", a.is_receipt_processed("branch1", "1-1001"), True)
    check("B has not processed 1-1001", b.is_receipt_processed("branch1", "1-1001"), False)


def main():
    print("Running multi-tenant isolation tests (offline)")

    test_materials_do_not_leak_between_businesses()
    test_same_branch_id_in_two_businesses_stays_separate()
    test_expenses_and_categories_are_separate()
    test_loyverse_token_is_per_business()
    test_sync_interval_is_per_business()
    test_users_are_listed_per_business()
    test_user_record_carries_its_tenant()
    test_tenant_record_and_rename()
    test_activity_is_recorded_once_a_day()
    test_unscoped_store_refuses_to_touch_business_data()
    test_image_paths_are_scoped_by_business()
    test_super_admin_is_not_a_role()
    test_processed_receipts_do_not_collide()

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
