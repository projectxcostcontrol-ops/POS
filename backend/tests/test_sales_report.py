"""
Tests for keeping our own copy of sales, and the reports built on it.

The reason this exists: Loyverse's free plan drops receipts after 31
days, so a business reading its history back from the POS loses a month
at a time, permanently. Saving a copy as receipts sync turns that into a
limit on how far back a NEW connection can see.

Most of these guard the places where a quiet wrong number could get out -
double-counting a re-synced receipt, profit that looks better than it is
because a menu had no recipe, or a comparison against nothing.

Offline, in-memory. Run with:

    cd backend
    python tests/test_sales_report.py
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store
from core.stock_engine import sync_and_deduct, sync_branch, backfill_sales
from storage.movement_ledger import MovementLedger
from core import sales_report

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


class FakeProvider:
    def __init__(self, receipts=None):
        self.receipts = receipts or []
        self.calls = []

    def get_receipts(self, store_id, created_at_min=None):
        self.calls.append(created_at_min)
        return self.receipts


def receipt(num, date, total, items):
    return {"receipt_number": num, "created_at": date, "total": total,
            "line_items": [{"item_name": n, "quantity": q, "price": p}
                           for n, q, p in items]}


def material(mid, name, cost, stock=100, par=0):
    return {"id": mid, "name": name, "unit": "kg", "cost": cost,
            "stock": stock, "par_level": par}


# ---------- saving ----------

def test_sales_are_saved_as_receipts_sync():
    section("Every synced receipt leaves a copy behind")
    store = make_test_store()
    provider = FakeProvider([
        receipt("1-1001", "2026-08-24T10:00:00+00:00", 250, [("ผัดไท", 2, 70)]),
        receipt("1-1002", "2026-08-24T11:00:00+00:00", 180, [("ข้าวผัด", 1, 60)]),
    ])

    sync_and_deduct(provider, store, "branch1")

    saved = store.list_sales("branch1")
    check("both receipts saved", len(saved), 2)
    check("the total is kept", saved[0]["total"], 250)
    check("line items are kept", saved[0]["items"][0]["name"], "ผัดไท")


def test_resyncing_the_same_receipt_does_not_double_count():
    section("A receipt seen twice is stored once, not twice")
    # sync_branch deliberately re-fetches a few minutes of overlap every
    # time. If that appended instead of overwriting, every sync would
    # inflate the day's takings by whatever fell in the overlap.
    store = make_test_store()
    provider = FakeProvider([
        receipt("1-1001", "2026-08-24T10:00:00+00:00", 250, [("ผัดไท", 2, 70)]),
    ])

    sync_and_deduct(provider, store, "branch1")
    sync_and_deduct(provider, store, "branch1")

    saved = store.list_sales("branch1")
    check("still one record", len(saved), 1)
    check("and one total, not doubled", saved[0]["total"], 250)


def test_an_already_processed_receipt_is_still_saved():
    section("A receipt whose stock was already deducted is still recorded as a sale")
    # "Processed" (don't deduct stock twice) and "saved" (keep a record)
    # answer different questions. Skipping the save for processed receipts
    # would leave gaps everywhere the overlap window landed.
    store = make_test_store()
    r = receipt("1-1001", "2026-08-24T10:00:00+00:00", 250, [("ผัดไท", 2, 70)])
    store.mark_receipt_processed("branch1", "1-1001")

    sync_and_deduct(FakeProvider([r]), store, "branch1")

    check("the sale was recorded anyway", len(store.list_sales("branch1")), 1)


def test_stock_is_not_deducted_twice_for_the_same_receipt():
    section("Saving the sale didn't break the double-deduction guard")
    store = make_test_store()
    store.upsert_material("branch1", "m1", {"name": "กุ้ง", "unit": "kg", "cost": 300})
    store.set_recipe("branch1", "ผัดไท", [{"material_id": "m1", "qty": 0.1}])
    provider = FakeProvider([
        receipt("1-1001", "2026-08-24T10:00:00+00:00", 250, [("ผัดไท", 2, 70)]),
    ])

    first = sync_and_deduct(provider, store, "branch1")
    second = sync_and_deduct(provider, store, "branch1")

    check("processed once", first, 1)
    check("second pass processes nothing", second, 0)


# ---------- backfill ----------

def test_backfill_saves_history_without_deducting_stock():
    section("Backfilled sales are recorded but never deduct stock")
    # Those sales happened before the branch had recipes. Deducting them
    # now would drive stock negative against ingredients nobody was
    # tracking at the time - a made-up shortage on day one.
    store = make_test_store()
    store.upsert_material("branch1", "m1", {"name": "กุ้ง", "unit": "kg",
                                            "cost": 300, "stock": 10})
    store.set_recipe("branch1", "ผัดไท", [{"material_id": "m1", "qty": 0.1}])
    provider = FakeProvider([
        receipt("1-0900", "2026-07-30T10:00:00+00:00", 250, [("ผัดไท", 2, 70)]),
    ])

    saved = backfill_sales(provider, store, "branch1")

    check("history saved", saved, 1)
    check("the sale is queryable", len(store.list_sales("branch1")), 1)
    check("no stock movement was written",
          MovementLedger(store).list_movements("branch1"), [])


def test_backfill_runs_only_once():
    section("Backfill doesn't repeat on every sync")
    store = make_test_store()
    provider = FakeProvider([
        receipt("1-0900", "2026-07-30T10:00:00+00:00", 250, [("ผัดไท", 2, 70)]),
    ])

    backfill_sales(provider, store, "branch1")
    again = backfill_sales(provider, store, "branch1")

    check("second call does nothing", again, 0)
    check("marked as done", store.has_backfilled_sales("branch1"), True)


def test_a_failing_backfill_does_not_block_the_first_sync():
    section("If history can't be fetched, the branch still starts syncing")
    # A 402 or a network blip on backfill must not leave the branch
    # without a cursor - it would retry the same failing fetch forever and
    # never sync anything new.
    class Boom:
        def get_receipts(self, store_id, created_at_min=None):
            if created_at_min is None:
                raise RuntimeError("history unavailable")
            return []

    store = make_test_store()
    sync_branch(Boom(), store, "branch1")

    check("a cursor was still established",
          store.get_sync_cursor("branch1") is not None, True)


# ---------- reporting ----------

def sample_sales():
    return [
        {"date": "2026-08-24T10:00:00+00:00", "total": 250,
         "items": [{"name": "ผัดไท", "qty": 2, "price": 70},
                   {"name": "น้ำเปล่า", "qty": 1, "price": 10}]},
        {"date": "2026-08-24T14:00:00+00:00", "total": 180,
         "items": [{"name": "ผัดไท", "qty": 1, "price": 70}]},
        {"date": "2026-08-25T10:00:00+00:00", "total": 300,
         "items": [{"name": "ข้าวผัด", "qty": 3, "price": 60}]},
    ]


def test_summary_totals_and_bill_count():
    section("The headline figures")
    out = sales_report.summarise(sample_sales(), {}, [])
    check("total", out["total"], 730)
    check("bill count", out["bill_count"], 3)


def test_gross_profit_names_the_menus_it_could_not_cost():
    section("Profit says which menus had no recipe - it doesn't just look good")
    # A menu with no recipe adds revenue and zero cost, so profit comes out
    # higher than reality. Reporting the number alone would be confidently
    # wrong; naming the gaps points at the fix.
    recipes = {"ผัดไท": [{"material_id": "m1", "qty": 0.1}]}
    materials = [material("m1", "กุ้ง", 300)]

    out = sales_report.summarise(sample_sales(), recipes, materials)

    check("ingredient cost counted for the costed menu",
          out["ingredient_cost"], 90.0)   # 3 plates x 0.1kg x 300
    check("profit is sales minus that", out["gross_profit"], 640.0)
    check("uncosted menus are named",
          out["uncosted_menus"], ["ข้าวผัด", "น้ำเปล่า"])


def test_points_group_by_day_or_hour():
    section("Chart points bucket by the requested granularity")
    daily = sales_report.summarise(sample_sales(), {}, [], "day")
    check("two days", len(daily["points"]), 2)
    check("first day totalled", daily["points"][0]["sales"], 430)

    hourly = sales_report.summarise(sample_sales(), {}, [], "hour")
    check("three distinct hours", len(hourly["points"]), 3)


def test_top_items_rank_by_quantity():
    section("Best sellers rank by plates made, not by revenue")
    # This answers "what does the kitchen make most of", which drives prep
    # and buying. Revenue is a different question and rides along.
    rows = sales_report.top_items(sample_sales())

    check("most plates first", rows[0]["name"], "ผัดไท")
    check("quantity summed across bills", rows[0]["qty"], 3)
    check("revenue rides along", rows[0]["revenue"], 210.0)
    check("a pricier-but-rarer dish ranks below", rows[1]["name"], "ข้าวผัด")


def test_daily_breakdown_is_newest_first():
    section("Day list reads newest first - that's what someone checks")
    rows = sales_report.daily_breakdown(sample_sales())
    check("two days", len(rows), 2)
    check("newest first", rows[0]["date"], "2026-08-25")
    check("bills counted per day", rows[1]["bill_count"], 2)


def test_comparison_returns_nothing_when_there_is_no_baseline():
    section("No previous period means no percentage - not a made-up 0%")
    # Showing "+100%" against an empty previous week would read as a real
    # result rather than an absence of data.
    check("empty baseline gives None",
          sales_report.compare_previous({"total": 500}, {"total": 0}), None)
    check("a real baseline compares",
          sales_report.compare_previous({"total": 550}, {"total": 500}),
          {"pct": 10.0, "up": True})
    check("a drop is marked as down",
          sales_report.compare_previous({"total": 450}, {"total": 500}),
          {"pct": 10.0, "up": False})


def test_previous_window_is_the_same_length_immediately_before():
    section("The comparison window matches the current one in length")
    start, end = sales_report.previous_window(
        "2026-08-18T00:00:00+00:00", "2026-08-25T00:00:00+00:00")
    check("ends exactly where the current window starts",
          end.startswith("2026-08-18"), True)
    check("and starts seven days before that", start.startswith("2026-08-11"), True)


# ---------- alerts ----------

def test_alerts_only_flag_materials_with_a_par_level():
    section("Low stock needs a reorder point - without one there's no 'low'")
    out = sales_report.build_alerts(
        materials=[material("m1", "กุ้ง", 300, stock=1, par=5),
                   material("m2", "เกลือ", 20, stock=1, par=0)],
        pending_drafts=0, last_count_at=None)

    check("only the one with a par level", len(out["low_stock"]), 1)
    check("and it's the right one", out["low_stock"][0]["name"], "กุ้ง")


def test_alerts_surface_negative_stock_separately():
    section("Negative stock is its own alert - it means something is wrong")
    out = sales_report.build_alerts(
        materials=[material("m1", "กุ้ง", 300, stock=-2, par=5)],
        pending_drafts=0, last_count_at=None)
    check("negative stock listed", len(out["negative_stock"]), 1)


def test_never_counted_is_treated_as_due():
    section("A branch that never counted is due, and says so differently")
    # None and "counted 20 days ago" need different wording: one is a setup
    # step nobody has done, the other is a habit that slipped.
    out = sales_report.build_alerts([], 0, None)
    check("days_since is unknown, not zero", out["days_since_count"], None)
    check("counted as due", out["count_due"], True)


def test_a_recent_count_is_not_due():
    section("Counted a few days ago - no reminder")
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    out = sales_report.build_alerts([], 0, "2026-08-22T00:00:00+00:00", now=now)
    check("two days ago", out["days_since_count"], 2)
    check("not due yet", out["count_due"], False)


def test_a_refund_subtracts_instead_of_adding():
    section("A refund lowers takings - it must never read as another sale")
    # Loyverse reports refund money as a positive number. Counted as-is,
    # refunding a 250 baht meal would ADD 250 to the day's takings. The
    # sign is flipped when the receipt is read, so everything downstream
    # just adds.
    sales = [
        {"date": "2026-08-24T10:00:00+00:00", "total": 250, "items": []},
        {"date": "2026-08-24T12:00:00+00:00", "total": -250,
         "is_refund": True, "items": []},
    ]
    out = sales_report.summarise(sales, {}, [])

    check("the day nets to zero", out["total"], 0)
    check("the refund isn't counted as a bill", out["bill_count"], 1)
    check("but it is reported", out["refund_count"], 1)


def test_refunds_do_not_return_stock():
    section("A refunded dish doesn't put ingredients back on the shelf")
    # The food was cooked and the ingredients are gone. Adding them back
    # would invent inventory that isn't there - the money is corrected,
    # the stock deliberately isn't.
    store = make_test_store()
    store.upsert_material("branch1", "m1", {"name": "กุ้ง", "unit": "kg", "cost": 300})
    store.set_recipe("branch1", "ผัดไท", [{"material_id": "m1", "qty": 0.1}])

    refund = receipt("1-1050", "2026-08-24T12:00:00+00:00", -250, [("ผัดไท", -2, 70)])
    refund["is_refund"] = True

    sync_and_deduct(FakeProvider([refund]), store, "branch1")

    check("the refund is recorded as a sale row", len(store.list_sales("branch1")), 1)
    check("no stock movement was written",
          MovementLedger(store).list_movements("branch1"), [])


def test_both_timestamps_are_kept():
    section("Sale time and record time are stored separately")
    # A till that was offline uploads its backlog hours later. The receipt
    # carries the time it was rung up; Loyverse carries the time it
    # arrived. Reports group by the first, the cursor follows the second -
    # conflating them is how a delayed terminal's sales disappear.
    store = make_test_store()
    r = receipt("1-1001", "2026-08-24T19:00:00+00:00", 250, [("ผัดไท", 2, 70)])
    r["recorded_at"] = "2026-08-25T02:00:00+00:00"   # uploaded after midnight

    sync_and_deduct(FakeProvider([r]), store, "branch1")

    saved = store.list_sales("branch1")[0]
    check("reports use the sale time", saved["date"], "2026-08-24T19:00:00+00:00")
    check("the cursor's timestamp is kept too",
          saved["recorded_at"], "2026-08-25T02:00:00+00:00")


def test_the_overlap_is_wide_enough_for_a_delayed_terminal():
    section("The sync overlap is hours, not minutes")
    # 5 minutes was the original value and it silently lost every receipt
    # from a till that reconnected later. Re-fetching costs one skipped
    # comparison; missing a sale loses it permanently, so the window errs
    # heavily toward re-fetching.
    from core.stock_engine import SYNC_OVERLAP_SECONDS
    check("at least an hour of overlap", SYNC_OVERLAP_SECONDS >= 3600, True)


def main():
    print("Running sales copy & reporting tests (offline)")

    test_sales_are_saved_as_receipts_sync()
    test_resyncing_the_same_receipt_does_not_double_count()
    test_an_already_processed_receipt_is_still_saved()
    test_stock_is_not_deducted_twice_for_the_same_receipt()
    test_backfill_saves_history_without_deducting_stock()
    test_backfill_runs_only_once()
    test_a_failing_backfill_does_not_block_the_first_sync()
    test_summary_totals_and_bill_count()
    test_a_refund_subtracts_instead_of_adding()
    test_refunds_do_not_return_stock()
    test_both_timestamps_are_kept()
    test_the_overlap_is_wide_enough_for_a_delayed_terminal()
    test_gross_profit_names_the_menus_it_could_not_cost()
    test_points_group_by_day_or_hour()
    test_top_items_rank_by_quantity()
    test_daily_breakdown_is_newest_first()
    test_comparison_returns_nothing_when_there_is_no_baseline()
    test_previous_window_is_the_same_length_immediately_before()
    test_alerts_only_flag_materials_with_a_par_level()
    test_alerts_surface_negative_stock_separately()
    test_never_counted_is_treated_as_due()
    test_a_recent_count_is_not_due()

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
