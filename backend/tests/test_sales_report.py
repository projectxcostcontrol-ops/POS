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
from core.stock_engine import sync_branch
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

    sync_branch(provider, store, "branch1")

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

    sync_branch(provider, store, "branch1")
    sync_branch(provider, store, "branch1")

    saved = store.list_sales("branch1")
    check("still one record", len(saved), 1)
    check("and one total, not doubled", saved[0]["total"], 250)


def test_a_processed_but_unsaved_receipt_is_recovered_by_repair():
    section("A bill marked processed but never saved is recovered - by repair, not by a normal sync")
    # This state is legacy data: branches that synced before the `sales`
    # collection existed marked their receipts processed and saved
    # nothing. It cannot arise from today's code, because _apply saves
    # BEFORE it marks, so a crash between the two leaves the receipt
    # unmarked and the next sync redoes both.
    #
    # A normal sync used to re-save every receipt it fetched, which
    # papered over this - and cost roughly 7,000 pointless writes a day
    # per branch to do it, since the six-hour overlap window meant
    # rewriting the same bills every five minutes (see
    # tests/test_sync_cost.py). It also only ever healed the handful of
    # legacy rows that happened to fall inside that window; anything
    # older was untouched either way.
    #
    # So the healing now runs where it was always designed to run: the
    # home screen's "อัปเดตข้อมูล" button syncs, reconciles POS against
    # saved, and calls repair when it finds a gap. Both halves are
    # asserted here, because dropping the first without the second would
    # be losing history to save writes.
    store = make_test_store()
    r = receipt("1-1001", "2026-08-24T10:00:00+00:00", 250, [("ผัดไท", 2, 70)])
    store.mark_receipt_processed("branch1", "1-1001")

    store.set_sync_cursor("branch1", "2026-01-01T00:00:00.000Z")
    sync_branch(FakeProvider([r]), store, "branch1")
    check("a normal sync leaves the gap alone", len(store.list_sales("branch1")), 0)

    sync_branch(FakeProvider([r]), store, "branch1", full=True)
    saved = store.list_sales("branch1")
    check("repair recovers it", len(saved), 1)
    check("with its real total", saved[0]["total"], 250)


def test_stock_is_not_deducted_twice_for_the_same_receipt():
    section("Saving the sale didn't break the double-deduction guard")
    store = make_test_store()
    store.upsert_material("branch1", "m1", {"name": "กุ้ง", "unit": "kg", "cost": 300})
    store.set_recipe("branch1", "ผัดไท", [{"material_id": "m1", "qty": 0.1}])
    provider = FakeProvider([
        receipt("1-1001", "2026-08-24T10:00:00+00:00", 250, [("ผัดไท", 2, 70)]),
    ])

    # A cursor means this is not a first run, so stock is deducted.
    store.set_sync_cursor("branch1", "2026-01-01T00:00:00.000Z")
    first = sync_branch(provider, store, "branch1")
    second = sync_branch(provider, store, "branch1")

    check("deducted once", first["deducted"], 1)
    check("second pass deducts nothing", second["deducted"], 0)
    # It was already saved on the first pass, and saving happens before
    # marking - so "already processed" implies "already stored", and
    # writing it a second time would buy nothing.
    check("and it is not written a second time", second["saved"], 0)
    check("the stored copy is still there", len(store.list_sales("branch1")), 1)
    check("and reported as already counted", second["already_counted"], 1)


# ---------- backfill ----------

def test_first_run_saves_history_without_deducting_stock():
    section("A first sync records history but never deducts stock")
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

    result = sync_branch(provider, store, "branch1")

    check("marked as a first run", result["first_run"], True)
    check("history saved", result["saved"], 1)
    check("the sale is queryable", len(store.list_sales("branch1")), 1)
    check("no stock movement was written",
          MovementLedger(store).list_movements("branch1"), [])


def test_the_full_history_pull_happens_only_once():
    section("Only the first sync asks for everything")
    # Afterwards the cursor narrows the request. Asking for full history
    # every five minutes is what made the original sync hang.
    store = make_test_store()
    provider = FakeProvider([
        receipt("1-0900", "2026-07-30T10:00:00+00:00", 250, [("ผัดไท", 2, 70)]),
    ])

    first = sync_branch(provider, store, "branch1")
    second = sync_branch(provider, store, "branch1")

    check("first run asked for everything", provider.calls[0], None)
    check("first run flagged as such", first["first_run"], True)
    check("second run is bounded by the cursor", provider.calls[1] is not None, True)
    check("and is not a first run", second["first_run"], False)


def test_a_repair_run_can_be_forced_on_an_established_branch():
    section("full=True re-reads everything, even with a cursor already set")
    # The repair for a branch whose history has gaps - which is how a
    # branch that connected before saving existed gets its past back.
    store = make_test_store()
    store.set_sync_cursor("branch1", "2026-08-20T00:00:00.000Z")
    provider = FakeProvider([
        receipt("1-0900", "2026-07-30T10:00:00+00:00", 250, [("ผัดไท", 2, 70)]),
    ])

    result = sync_branch(provider, store, "branch1", full=True)

    check("asked for everything despite the cursor", provider.calls[0], None)
    check("treated as a first run", result["first_run"], True)
    check("history saved", result["saved"], 1)


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


def test_list_sales_only_reads_the_window_asked_for():
    section("A date range is filtered by the database, not after the fact")
    # Reading the whole collection and discarding most of it meant looking
    # at today got slower every week the shop stayed open - the same
    # question costing more forever. The fake query enforces the same
    # operators the real client does, so this test fails if the range
    # stops being pushed down.
    store = make_test_store()
    for name, when in [("old", "2026-07-01T10:00:00.000Z"),
                       ("mid", "2026-08-15T10:00:00.000Z"),
                       ("new", "2026-08-30T10:00:00.000Z")]:
        store.save_sale("branch1", name,
                        {"receipt_number": name, "date": when, "total": 10, "items": []})

    window = store.list_sales("branch1",
                              "2026-08-10T00:00:00.000Z", "2026-08-20T00:00:00.000Z")
    check("only the sale inside the window", [r["receipt_number"] for r in window], ["mid"])

    check("an open-ended start still bounds the other side",
          [r["receipt_number"] for r in
           store.list_sales("branch1", "2026-08-10T00:00:00.000Z")], ["mid", "new"])

    check("no window returns everything, oldest first",
          [r["receipt_number"] for r in store.list_sales("branch1")],
          ["old", "mid", "new"])


def test_all_recipes_reads_the_book_once():
    section("Recipes come back in one read, not one per dish")
    store = make_test_store()
    store.set_recipe("branch1", "ผัดไท", [{"material_id": "m1", "qty": 0.1}])
    store.set_recipe("branch1", "ข้าวผัด", [{"material_id": "m2", "qty": 0.2}])

    book = store.all_recipes("branch1")
    check("both recipes returned", sorted(book.keys()), ["ข้าวผัด", "ผัดไท"])
    check("ingredients intact", book["ผัดไท"][0]["qty"], 0.1)
    check("a branch with none gets an empty book", store.all_recipes("branch2"), {})


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


def test_buckets_follow_the_shops_clock_not_utc():
    section("Chart buckets use the shop's local time")
    # Stored timestamps are UTC. In Bangkok (+420 min) a 7pm sale is
    # 12:00 UTC - bucketing by UTC would put the evening rush on the wrong
    # bar, and an 8pm sale on the wrong DAY.
    sales = [{"date": "2026-08-24T19:00:00+07:00", "total": 100, "items": []}]

    utc = sales_report.summarise(sales, {}, [], "hour", 0)
    bkk = sales_report.summarise(sales, {}, [], "hour", 420)

    check("UTC puts it at 12:00", utc["points"][0]["t"], "2026-08-24T12:00")
    check("Bangkok puts it at 19:00", bkk["points"][0]["t"], "2026-08-24T19:00")


def test_a_late_evening_sale_stays_on_the_right_day():
    section("An 8pm sale belongs to that day, not tomorrow")
    sales = [{"date": "2026-08-24T20:30:00+07:00", "total": 100, "items": []}]

    utc = sales_report.summarise(sales, {}, [], "day", 0)
    bkk = sales_report.summarise(sales, {}, [], "day", 420)

    check("UTC rolls it back a day", utc["points"][0]["t"], "2026-08-24")
    check("Bangkok keeps it on the 24th", bkk["points"][0]["t"], "2026-08-24")


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
    # Same canonical format as the saved sale dates it will be compared to.
    import re as _re
    check("emitted in the canonical format",
          bool(_re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", start)), True)


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

    store.set_sync_cursor("branch1", "2026-01-01T00:00:00.000Z")
    sync_branch(FakeProvider([refund]), store, "branch1")

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

    store.set_sync_cursor("branch1", "2026-01-01T00:00:00.000Z")
    sync_branch(FakeProvider([r]), store, "branch1")

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
    test_a_processed_but_unsaved_receipt_is_recovered_by_repair()
    test_stock_is_not_deducted_twice_for_the_same_receipt()
    test_first_run_saves_history_without_deducting_stock()
    test_the_full_history_pull_happens_only_once()
    test_a_repair_run_can_be_forced_on_an_established_branch()
    test_list_sales_only_reads_the_window_asked_for()
    test_all_recipes_reads_the_book_once()
    test_summary_totals_and_bill_count()
    test_a_refund_subtracts_instead_of_adding()
    test_refunds_do_not_return_stock()
    test_both_timestamps_are_kept()
    test_the_overlap_is_wide_enough_for_a_delayed_terminal()
    test_gross_profit_names_the_menus_it_could_not_cost()
    test_buckets_follow_the_shops_clock_not_utc()
    test_a_late_evening_sale_stays_on_the_right_day()
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
