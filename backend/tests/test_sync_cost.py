"""
Tests for what a sync COSTS, not what it returns.

Every other test file here asks "is the answer right". These ask "what
did the answer cost", because the two worst problems this codebase has
had were both cases where the answer was perfectly right and the bill
was not:

  1. Every sync read the whole processed_receipts collection to find out
     which of the ~25 receipts it just fetched had been seen before. That
     collection holds one document per bill the branch has EVER rung up,
     so the cost of a five-minute sync grew forever while the work it
     did stayed the same.

  2. Every sync re-saved every receipt inside the six-hour overlap
     window, unchanged, having already saved them the last twelve times.

Neither is visible from the outside. The sync returns the same numbers,
the screens show the same figures, and the only symptom is a Firebase
bill or a quota that runs out at four in the afternoon. So the assertions
here are about read and write counts, and specifically about how those
counts GROW - a cost that doesn't depend on how long the shop has been
open is the property worth protecting.

Offline, in-memory. Run with:

    cd backend
    python tests/test_sync_cost.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store, FakeDb

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def check_at_most(label, actual, ceiling):
    ok = actual <= ceiling
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected <= {ceiling!r}")


def section(title):
    print(f"\n=== {title} ===")


class FakeProvider:
    def __init__(self, receipts=None):
        self.receipts = receipts or []

    def get_receipts(self, store_id, created_at_min=None):
        return self.receipts


def receipt(n, total=100):
    """A bill with no line items - these tests measure sync overhead, and
    recipe lookups would be a different cost with a different fix."""
    return {"receipt_number": str(n), "created_at": "2026-08-20T10:00:00.000Z",
            "recorded_at": "2026-08-20T10:00:00.000Z", "total": total,
            "line_items": []}


def branch_with_history(bills: int):
    """A branch that has been open a while: `bills` receipts already
    synced, saved, and marked processed - exactly the state that used to
    make every subsequent sync more expensive than the last."""
    db = FakeDb()
    store = make_test_store(db=db)
    numbers = [str(n) for n in range(bills)]
    store.save_sales_bulk("b1", [(n, {"receipt_number": n, "date": "2026-01-01T00:00:00.000Z",
                                      "total": 100, "items": []}) for n in numbers])
    store.mark_receipts_processed_bulk("b1", numbers)
    store.set_sync_cursor("b1", "2026-08-20T09:00:00.000Z")
    return db, store


def test_a_repeat_sync_writes_nothing():
    section("A sync that fetched nothing new writes nothing")
    # The overlap window deliberately re-fetches the last six hours on
    # every run, so this is not an edge case - it is what almost every
    # sync of the day looks like.
    from core.stock_engine import sync_branch

    db, store = branch_with_history(30)
    provider = FakeProvider([receipt(n) for n in range(25)])   # all already known

    db.reset_meter()
    result = sync_branch(provider, store, "b1")

    check("nothing was saved again", result["saved"], 0)
    check("nothing was deducted again", result["deducted"], 0)
    # One write remains, and should: the cursor moved.
    check_at_most("writes stayed at the cursor update", db.writes, 1)


def test_cost_does_not_grow_with_history():
    section("The same sync costs the same after a year as on day one")
    # This is the actual property. A branch that has rung up 2,000 bills
    # and a branch that has rung up 200 are doing identical work when
    # they sync the same 5 new receipts - so they must pay the same.
    from core.stock_engine import sync_branch

    new_ones = [receipt(9000 + n) for n in range(5)]

    small_db, small_store = branch_with_history(200)
    small_db.reset_meter()
    sync_branch(FakeProvider(list(new_ones)), small_store, "b1")
    small_cost = small_db.reads

    big_db, big_store = branch_with_history(2000)
    big_db.reset_meter()
    sync_branch(FakeProvider(list(new_ones)), big_store, "b1")
    big_cost = big_db.reads

    check("a 10x longer history costs the same to sync", big_cost, small_cost)
    # And in absolute terms it is small: the cursor, plus one lookup per
    # receipt actually fetched.
    check_at_most("cost is proportional to receipts fetched", big_cost, 10)


def test_new_receipts_are_still_saved_and_counted():
    section("Skipping the re-saves does not skip the real work")
    from core.stock_engine import sync_branch

    db, store = branch_with_history(20)
    # 18 the branch has seen before, 2 genuinely new.
    provider = FakeProvider([receipt(n) for n in range(18)] +
                            [receipt(500), receipt(501)])

    result = sync_branch(provider, store, "b1")

    check("only the new bills were saved", result["saved"], 2)
    check("only the new bills were deducted", result["deducted"], 2)
    check("the new bills are readable back", len(store.list_sales("b1")), 22)
    check("the old bills were not lost", store.list_sales("b1")[0]["receipt_number"], "0")


def test_repair_still_overwrites_everything():
    section("full=True still rewrites every bill - that is what repair is for")
    # The one case the optimisation must not cover. A row saved wrong, or
    # by a version before a field existed, is invisible to "have I seen
    # this receipt" - it has been seen. Repair has to overwrite anyway.
    from core.stock_engine import sync_branch

    db, store = branch_with_history(5)
    # Simulate a row saved by an older version: right receipt, wrong total.
    store.save_sale("b1", "3", {"receipt_number": "3", "date": "2026-08-20T10:00:00.000Z",
                                "total": 0, "items": []})
    before = [s for s in store.list_sales("b1") if s["receipt_number"] == "3"][0]
    check("the bad row is there to begin with", before["total"], 0)

    provider = FakeProvider([receipt(n, total=250) for n in range(5)])

    normal = sync_branch(provider, store, "b1")
    still_bad = [s for s in store.list_sales("b1") if s["receipt_number"] == "3"][0]
    check("a normal sync leaves it alone", normal["saved"], 0)
    check("...so the bad total survives", still_bad["total"], 0)

    repaired = sync_branch(provider, store, "b1", full=True)
    fixed = [s for s in store.list_sales("b1") if s["receipt_number"] == "3"][0]
    check("repair rewrote every bill", repaired["saved"], 5)
    check("...including the bad one", fixed["total"], 250)


def test_first_sync_of_a_new_branch_saves_everything():
    section("A brand new branch still gets its full history saved")
    from core.stock_engine import sync_branch

    db = FakeDb()
    store = make_test_store(db=db)
    provider = FakeProvider([receipt(n) for n in range(40)])

    result = sync_branch(provider, store, "b1")

    check("every bill was saved", result["saved"], 40)
    check("none were deducted (they predate the recipes)", result["deducted"], 0)
    check("all readable back", len(store.list_sales("b1")), 40)


def main():
    print("Running sync cost tests (offline)")

    test_a_repeat_sync_writes_nothing()
    test_cost_does_not_grow_with_history()
    test_new_receipts_are_still_saved_and_counted()
    test_repair_still_overwrites_everything()
    test_first_sync_of_a_new_branch_saves_everything()

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
