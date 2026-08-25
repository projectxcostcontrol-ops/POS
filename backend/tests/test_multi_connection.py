"""
Tests for a business holding more than one Loyverse account.

This came from real use: a shop owner with several branches had opened a
separate Loyverse account for each of them rather than adding branches to
one account. One token, one branch. The system assumed one token per
business, so she could connect exactly one of her shops and the others
were unreachable - not broken, just impossible to express.

What matters here is that the branches stay SEPARATE. Nothing is merged
across accounts: each branch keeps its own stock, sales and recipes under
its own store id, and asking about one branch must reach the account that
branch actually came from - never "whichever account was connected
first", which is what the single-token version effectively did.

Offline, in-memory. Run with:

    cd backend
    python tests/test_multi_connection.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store, FakeDb
from core.pos_registry import PosRegistry

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


NOW = "2026-08-25T00:00:00.000Z"


class FakeAdapter:
    """One Loyverse account. `stores` is what its token can see; a token
    that has been revoked raises, the way the real client would."""

    def __init__(self, stores, broken=False):
        self.stores = stores
        self.broken = broken

    def get_stores(self):
        if self.broken:
            raise RuntimeError("401 Unauthorized")
        return [{"id": i, "name": n} for i, n in self.stores]


def registry(store, accounts):
    """accounts: {token: FakeAdapter}"""
    return PosRegistry(store, lambda conn: accounts[conn["token"]], NOW)


def two_shops():
    store = make_test_store(db=FakeDb())
    a = store.add_connection("tok-silom", "สาขาสีลม", NOW)
    b = store.add_connection("tok-thonglor", "สาขาทองหล่อ", NOW)
    accounts = {
        "tok-silom": FakeAdapter([("s1", "ร้านสีลม")]),
        "tok-thonglor": FakeAdapter([("s2", "ร้านทองหล่อ")]),
    }
    return store, registry(store, accounts), a, b, accounts


def test_each_branch_reaches_its_own_account():
    section("A branch reaches the account it actually came from")
    store, reg, a, b, accounts = two_shops()

    check("both branches are listed", len(reg.branches()[0]), 2)
    check("สีลม resolves to its own token",
          reg.provider_for("s1") is accounts["tok-silom"], True)
    check("ทองหล่อ resolves to its own token",
          reg.provider_for("s2") is accounts["tok-thonglor"], True)
    check("and they are not the same account",
          reg.provider_for("s1") is reg.provider_for("s2"), False)


def test_a_branch_carries_which_account_it_came_from():
    section("Each branch says which account it belongs to")
    # Two accounts can easily both hold a branch called "สาขา 1", so the
    # person switching between them has to be able to tell which is which.
    store, reg, a, b, _ = two_shops()
    by_id = {x["id"]: x for x in reg.branches()[0]}

    check("สีลม is labelled", by_id["s1"]["connection_label"], "สาขาสีลม")
    check("ทองหล่อ is labelled", by_id["s2"]["connection_label"], "สาขาทองหล่อ")
    check("and they point at different connections",
          by_id["s1"]["connection_id"] != by_id["s2"]["connection_id"], True)


def test_one_dead_token_does_not_hide_the_other_shop():
    section("An expired token takes down its own account and nothing else")
    store = make_test_store(db=FakeDb())
    good = store.add_connection("tok-ok", "สาขาที่ใช้ได้", NOW)
    bad = store.add_connection("tok-dead", "สาขาที่ token หมดอายุ", NOW)
    accounts = {"tok-ok": FakeAdapter([("s1", "ร้านที่ยังเปิดอยู่")]),
                "tok-dead": FakeAdapter([], broken=True)}
    reg = registry(store, accounts)

    branches, failures = reg.branches()

    check("the working shop is still there", [b["id"] for b in branches], ["s1"])
    check("the broken account is reported", len(failures), 1)
    check("...by name, not just as a count", failures[0]["label"], "สาขาที่ token หมดอายุ")
    check("and the reason is recorded on it",
          "401" in (store.get_connection(bad["id"]).get("last_error") or ""), True)
    check("the working one carries no error",
          store.get_connection(good["id"]).get("last_error"), None)


def test_a_token_that_starts_working_again_clears_its_error():
    section("A replaced token clears the warning")
    store = make_test_store(db=FakeDb())
    conn = store.add_connection("tok", "ร้าน", NOW)
    adapter = FakeAdapter([("s1", "ร้าน")], broken=True)
    reg = registry(store, {"tok": adapter})

    reg.branches()
    check("error recorded while broken",
          bool(store.get_connection(conn["id"]).get("last_error")), True)

    adapter.broken = False
    reg.invalidate()
    reg.branches()
    check("cleared once it works again",
          store.get_connection(conn["id"]).get("last_error"), None)


def test_the_old_single_token_becomes_the_first_account():
    section("A business on the old single-token shape needs to do nothing")
    store = make_test_store(db=FakeDb())
    store.set_setting("loyverse_token", "tok-legacy")
    reg = registry(store, {"tok-legacy": FakeAdapter([("s1", "ร้านเดิม")])})

    check("it now has one connection", len(reg.connections), 1)
    check("built from the old token", reg.connections[0]["token"], "tok-legacy")
    check("its branch still resolves", reg.provider_for("s1") is not None, True)

    # Running again must not produce a second copy of the same account.
    reg.invalidate()
    check("migrating twice adds nothing", len(reg.connections), 1)


def test_a_branch_added_later_in_loyverse_is_found():
    section("A branch opened in Loyverse after connecting is picked up")
    # The index is a cache of "which account owns which branch". A branch
    # missing from it must trigger a refresh, not an error - nobody knows
    # to go and press something after adding a shop on the Loyverse side.
    store = make_test_store(db=FakeDb())
    store.add_connection("tok", "บัญชี", NOW)
    adapter = FakeAdapter([("s1", "สาขาแรก")])
    reg = registry(store, {"tok": adapter})

    reg.refresh_index()
    adapter.stores.append(("s2", "สาขาใหม่"))

    check("the new branch resolves without anyone refreshing",
          reg.provider_for("s2") is adapter, True)


def test_removing_an_account_keeps_the_history_it_produced():
    section("Disconnecting an account does not delete its shop's data")
    store, reg, a, b, _ = two_shops()
    reg.refresh_index()

    store.upsert_material("s1", "m1", {"name": "กุ้ง", "unit": "kg"})
    store.upsert_material("s2", "m1", {"name": "หมู", "unit": "kg"})

    store.delete_connection(a["id"])
    reg.invalidate()

    check("only one account is left", len(reg.connections), 1)
    check("its branch still resolves", reg.provider_for("s2") is not None, True)
    check("the removed account's branch no longer does",
          reg.provider_for("s1"), None)
    # The point: stopping the sync is not the same as throwing away the
    # stock, sales and recipes already recorded for that shop.
    check("the disconnected shop's data is untouched",
          store.list_materials("s1")[0]["name"], "กุ้ง")


def test_two_shops_never_share_data():
    section("Branches from different accounts keep entirely separate books")
    store, reg, a, b, _ = two_shops()

    store.set_recipe("s1", "ผัดไท", [{"material_id": "m1", "qty": 0.1}])
    store.upsert_material("s1", "m1", {"name": "กุ้ง", "unit": "kg"})

    check("the other shop has no materials", store.list_materials("s2"), [])
    check("and no recipes", store.get_recipe("s2", "ผัดไท"), [])
    check("while the first one does", len(store.list_materials("s1")), 1)


def main():
    print("Running multi-connection tests (offline)")

    test_each_branch_reaches_its_own_account()
    test_a_branch_carries_which_account_it_came_from()
    test_one_dead_token_does_not_hide_the_other_shop()
    test_a_token_that_starts_working_again_clears_its_error()
    test_the_old_single_token_becomes_the_first_account()
    test_a_branch_added_later_in_loyverse_is_found()
    test_removing_an_account_keeps_the_history_it_produced()
    test_two_shops_never_share_data()

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
