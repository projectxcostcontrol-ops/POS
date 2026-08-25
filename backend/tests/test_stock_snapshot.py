"""
Tests for the running stock/cost snapshot on each material.

Current stock is the sum of every movement ever recorded, and every sale
writes one movement per ingredient. A shop doing 100 bills a day of
three-ingredient dishes writes roughly 110,000 movements a year - and the
materials page, the one staff open most often, used to read all of them
to answer "how much is left", plus a second query per material for its
average cost.

So there are two things to protect here and they pull in opposite
directions:

  correctness - the snapshot must equal the ledger, always, including
                after a mix of receives, sales, waste and counts, and
                including on a branch that has never been rebuilt

  cost        - reading it must not get more expensive as the ledger
                grows, which is the entire reason it exists

Both are asserted below. Offline, in-memory. Run with:

    cd backend
    python tests/test_stock_snapshot.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store, FakeDb
from storage.movement_ledger import MovementLedger

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def check_close(label, actual, expected, tol=1e-9):
    ok = abs(actual - expected) < tol
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def check_at_most(label, actual, ceiling):
    ok = actual <= ceiling
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected <= {ceiling!r}")


def section(title):
    print(f"\n=== {title} ===")


def kitchen(materials=3):
    db = FakeDb()
    store = make_test_store(db=db)
    for i in range(materials):
        store.upsert_material("b1", f"m{i}", {"name": f"ของ {i}", "unit": "kg"})
    return db, store, MovementLedger(store)


def test_the_snapshot_equals_the_ledger():
    section("The snapshot is the ledger's arithmetic, not a second opinion")
    db, store, ledger = kitchen()

    ledger.record_receive("b1", "m0", 10, unit_cost=100)
    ledger.record_receive("b1", "m0", 30, unit_cost=140)
    ledger.record_sale("b1", "m0", 4)
    ledger.record_waste("b1", "m0", 1, note="หก")

    from_ledger = sum(m["quantity"] for m in ledger.list_movements("b1", "m0"))
    snap = store.material_snapshot("b1", "m0")

    check_close("stock matches the sum of movements", snap["stock_qty"], from_ledger)
    check_close("...which is 10 + 30 - 4 - 1", snap["stock_qty"], 35)

    # Weighted, not a plain mean: 40kg cost 10x100 + 30x140 = 5,200.
    check_close("average cost is weighted by quantity",
                ledger.average_cost("b1", "m0"), 5200 / 40)


def test_a_count_lands_on_the_counted_number():
    section("A physical count still lands exactly on what was counted")
    # record_count stores a delta, and the delta is computed from current
    # stock - so if the snapshot were wrong, counting would write the
    # wrong correction and quietly leave the shelf figure wrong while
    # looking like it had just been verified.
    db, store, ledger = kitchen()
    ledger.record_receive("b1", "m0", 20, unit_cost=50)
    ledger.record_sale("b1", "m0", 7)

    ledger.record_count("b1", "m0", 11.5)

    check_close("stock is what was counted", ledger.current_stock("b1", "m0"), 11.5)
    check_close("and the ledger agrees",
                sum(m["quantity"] for m in ledger.list_movements("b1", "m0")), 11.5)


def test_editing_a_material_does_not_touch_its_stock():
    section("Renaming an ingredient does not reset what is on the shelf")
    # upsert_material seeds the totals with increment-by-zero. Seeding
    # with a literal 0 would zero the shelf every time someone fixed a
    # typo in a name.
    db, store, ledger = kitchen()
    ledger.record_receive("b1", "m0", 12, unit_cost=80)

    store.upsert_material("b1", "m0", {"name": "ชื่อใหม่", "unit": "kg"})

    check_close("stock survived the edit", ledger.current_stock("b1", "m0"), 12)
    check("the new name took", store.material_snapshot("b1", "m0")["name"], "ชื่อใหม่")


def test_a_request_cannot_write_the_totals_itself():
    section("The totals are refused as input")
    db, store, ledger = kitchen()
    ledger.record_receive("b1", "m0", 5, unit_cost=10)

    store.upsert_material("b1", "m0", {"name": "ของ 0", "stock": 999,
                                       "stock_qty": 999, "recv_value": 999})

    check_close("stock came from the ledger, not the request",
                ledger.current_stock("b1", "m0"), 5)


def test_reading_the_stock_page_does_not_grow_with_history():
    section("The materials page costs the same on day one and a year in")
    # The property that matters. Same 3 ingredients, wildly different
    # amounts of history behind them.
    def cost_after(sales):
        db, store, ledger = kitchen()
        ledger.record_receive("b1", "m0", 500, unit_cost=100)
        ledger.record_sales_bulk("b1", [{"material_id": "m0", "quantity": 0.1,
                                         "ref": f"r{i}"} for i in range(sales)])
        db.reset_meter()
        mats = store.list_materials("b1")
        return db.reads, mats

    light_reads, mats = cost_after(20)
    heavy_reads, _ = cost_after(2000)

    check("100x the movements costs the same to read", heavy_reads, light_reads)
    check_at_most("and it is one read per ingredient", heavy_reads, 3)
    check_close("the number shown is still right",
                [m for m in mats if m["id"] == "m0"][0]["stock"], 500 - 20 * 0.1)


def test_a_branch_without_a_snapshot_is_slow_but_never_wrong():
    section("A branch that predates the snapshot still shows the right numbers")
    # Materials created before this existed have no totals on them. The
    # page must fall back to summing the ledger rather than reporting
    # zero - being slow is a cost, being confidently wrong about stock
    # is a different kind of problem.
    db, store, ledger = kitchen(materials=1)
    ledger.record_receive("b1", "m0", 40, unit_cost=25)
    ledger.record_sale("b1", "m0", 6)

    # Strip the snapshot, as a pre-existing material would be.
    raw = store.material_snapshot("b1", "m0")
    for field in store.SNAPSHOT_FIELDS:
        raw.pop(field, None)
    store._col("b1", "materials").document("m0").set(raw)

    mats = store.list_materials("b1")
    check("it knows it is on the slow path", mats[0]["snapshot"], False)
    check_close("stock is still correct", mats[0]["stock"], 34)
    check_close("cost is still correct", mats[0]["cost"], 25)

    rebuilt = ledger.rebuild_snapshots("b1")
    after = store.list_materials("b1")
    check("rebuilding covered the material", rebuilt, 1)
    check("now on the fast path", after[0]["snapshot"], True)
    check_close("and reports the same stock as before", after[0]["stock"], 34)
    check_close("and the same cost", after[0]["cost"], 25)


def test_rebuild_matches_what_the_ledger_says():
    section("Rebuild reproduces the ledger exactly, from scratch")
    db, store, ledger = kitchen(materials=2)
    ledger.record_receive("b1", "m0", 10, unit_cost=100)
    ledger.record_sale("b1", "m0", 3)
    ledger.record_receive("b1", "m1", 8, unit_cost=55)
    ledger.record_waste("b1", "m1", 2)

    # Corrupt them, then rebuild - the ledger is the record, so this must
    # always be recoverable.
    store.set_material_snapshot("b1", "m0", {"stock_qty": -999, "recv_qty": 0,
                                             "recv_value": 0})
    ledger.rebuild_snapshots("b1")

    check_close("m0 stock restored", ledger.current_stock("b1", "m0"), 7)
    check_close("m0 cost restored", ledger.average_cost("b1", "m0"), 100)
    check_close("m1 stock untouched", ledger.current_stock("b1", "m1"), 6)


def test_sync_deducts_in_batches_and_reports_missing_ingredients():
    section("A sync deducts in batches, and says so when a recipe names a deleted ingredient")
    from core.stock_engine import sync_branch

    db, store, ledger = kitchen(materials=1)
    store.set_recipe("b1", "ผัดไท", [{"material_id": "m0", "qty": 0.1},
                                     {"material_id": "gone", "qty": 0.05}])
    store.set_sync_cursor("b1", "2026-08-20T00:00:00.000Z")

    receipts = [{"receipt_number": str(n), "created_at": "2026-08-20T10:00:00.000Z",
                 "recorded_at": "2026-08-20T10:00:00.000Z", "total": 70,
                 "line_items": [{"item_name": "ผัดไท", "quantity": 2}]}
                for n in range(50)]

    class P:
        def get_receipts(self, store_id, created_at_min=None):
            return receipts

    result = sync_branch(P(), store, "b1")

    check("every bill was counted", result["deducted"], 50)
    check_close("the real ingredient was deducted", ledger.current_stock("b1", "m0"),
                -50 * 2 * 0.1)
    check("the deleted one is named, not swallowed",
          result["unknown_materials"], ["gone"])


def main():
    print("Running stock snapshot tests (offline)")

    test_the_snapshot_equals_the_ledger()
    test_a_count_lands_on_the_counted_number()
    test_editing_a_material_does_not_touch_its_stock()
    test_a_request_cannot_write_the_totals_itself()
    test_reading_the_stock_page_does_not_grow_with_history()
    test_a_branch_without_a_snapshot_is_slow_but_never_wrong()
    test_rebuild_matches_what_the_ledger_says()
    test_sync_deducts_in_batches_and_reports_missing_ingredients()

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
