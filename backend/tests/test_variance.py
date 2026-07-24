"""
Tests for V3 step 3.4 - stock counts and variance analysis.

Variance is a number people may act on: chase a supplier, question a
cook, rewrite a recipe. So most of these tests are about the report
refusing to look more certain than it is - staying quiet when it has no
baseline, when nothing was used, or when a menu's sales were never
accounted for.

Offline, in-memory. Run with:

    cd backend
    python tests/test_variance.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store
from storage.movement_ledger import MovementLedger
from core.variance import analyse_session, summarise, unmeasured_menus, _is_flagged

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


def build(store_id="branch1"):
    store = make_test_store()
    return store, MovementLedger(store), store_id


def material(mid, name, unit="kg", cost=100):
    return {"id": mid, "name": name, "unit": unit, "cost": cost}


def session(sid, closed_at, entries):
    return {"id": sid, "closed_at": closed_at, "status": "closed", "entries": entries}


def test_shortfall_is_measured_against_usage_not_stock():
    section("A shortfall is a percentage of what was USED, not of what's on the shelf")
    # 1kg missing from 8kg used is a real problem; 1kg from a 50kg sack
    # that barely moved is probably a measuring difference. Measuring
    # against stock on hand would rank those the wrong way round.
    store, ledger, sid = build()
    ledger.record(sid, "m1", "receive", 10, unit_cost=100, occurred_at="2026-07-01T00:00:00")
    ledger.record(sid, "m1", "sale", -8, occurred_at="2026-07-05T00:00:00")
    ledger.record(sid, "m1", "count", -1.7, occurred_at="2026-07-08T00:00:00", ref="s2")

    rows = analyse_session(ledger, sid, session("s2", "2026-07-08T00:00:00", {"m1": 0.3}),
                           session("s1", "2026-07-01T00:00:00", {}),
                           [material("m1", "กุ้งขาว")])

    check("one row", len(rows), 1)
    check("shortfall quantity", rows[0]["variance_qty"], -1.7)
    check("usage in the window", rows[0]["expected_usage"], 8.0)
    check("percentage is of usage", rows[0]["variance_pct"], 21.2)
    check("value uses average cost", rows[0]["variance_value"], -170.0)


def test_both_thresholds_must_be_crossed():
    section("Flagging needs BOTH a big percentage and real money")
    # Percentage alone flags 40 baht of pepper every week; value alone
    # flags any expensive item whose count was rounded. A warning list
    # nobody reads protects nothing.
    check("big % but trivial value -> not flagged",
          _is_flagged(-3, 2, 30.0, 10.0, 200.0), False)
    check("big value but small % -> not flagged",
          _is_flagged(-5, 200, 2.0, 10.0, 200.0), False)
    check("both crossed -> flagged",
          _is_flagged(-2, 300, 25.0, 10.0, 200.0), True)


def test_surplus_is_reported_but_never_flagged():
    section("Finding MORE than expected is shown, not alarmed about")
    # A surplus usually means the recipe overstates the dish - something
    # to correct, not a loss to chase.
    store, ledger, sid = build()
    ledger.record(sid, "m1", "sale", -5, occurred_at="2026-07-05T00:00:00")
    ledger.record(sid, "m1", "count", 0.9, occurred_at="2026-07-08T00:00:00", ref="s2")

    rows = analyse_session(ledger, sid, session("s2", "2026-07-08T00:00:00", {"m1": 4}),
                           session("s1", "2026-07-01T00:00:00", {}),
                           [material("m1", "หมูสับ", cost=300)],
                           threshold_pct=1.0, threshold_value=1.0)

    check("surplus recorded", rows[0]["variance_qty"], 0.9)
    check("not flagged even below both thresholds", rows[0]["flagged"], False)


def test_no_usage_means_no_percentage_and_no_flag():
    section("A material nothing was made from can't have a meaningful variance")
    # Dividing by zero usage would either crash or invent a number. Neither
    # is worth it: with no consumption there's nothing to compare against.
    store, ledger, sid = build()
    ledger.record(sid, "m1", "count", -5, occurred_at="2026-07-08T00:00:00", ref="s2")

    rows = analyse_session(ledger, sid, session("s2", "2026-07-08T00:00:00", {"m1": 1}),
                           None, [material("m1", "เกลือ", cost=1000)],
                           threshold_pct=1.0, threshold_value=1.0)

    check("percentage is unknown, not zero", rows[0]["variance_pct"], None)
    check("marked unmeasurable", rows[0]["measurable"], False)
    check("and never flagged on a guess", rows[0]["flagged"], False)


def test_only_movements_inside_the_period_count():
    section("Usage before the previous count belongs to the previous period")
    store, ledger, sid = build()
    ledger.record(sid, "m1", "sale", -100, occurred_at="2026-06-01T00:00:00")  # last period
    ledger.record(sid, "m1", "sale", -4, occurred_at="2026-07-05T00:00:00")    # this one
    ledger.record(sid, "m1", "sale", -50, occurred_at="2026-07-20T00:00:00")   # after the count
    ledger.record(sid, "m1", "count", -1, occurred_at="2026-07-08T00:00:00", ref="s2")

    rows = analyse_session(ledger, sid, session("s2", "2026-07-08T00:00:00", {"m1": 2}),
                           session("s1", "2026-07-01T00:00:00", {}),
                           [material("m1", "น้ำมันพืช")])

    check("only this period's usage", rows[0]["expected_usage"], 4.0)


def test_waste_is_shown_separately_not_netted_off():
    section("Recorded waste is reported beside the variance, not folded into it")
    # Waste already left the ledger, so it isn't part of the unexplained
    # gap. Showing it separately answers "was any of this accounted for?"
    # without quietly shrinking the number.
    store, ledger, sid = build()
    ledger.record(sid, "m1", "sale", -6, occurred_at="2026-07-05T00:00:00")
    ledger.record(sid, "m1", "waste", -0.5, occurred_at="2026-07-06T00:00:00")
    ledger.record(sid, "m1", "count", -1, occurred_at="2026-07-08T00:00:00", ref="s2")

    rows = analyse_session(ledger, sid, session("s2", "2026-07-08T00:00:00", {"m1": 1}),
                           session("s1", "2026-07-01T00:00:00", {}),
                           [material("m1", "ผักกาด")])

    check("variance untouched by waste", rows[0]["variance_qty"], -1.0)
    check("waste reported alongside", rows[0]["recorded_waste"], 0.5)


def test_a_counts_correction_is_matched_by_session():
    section("Each report reads its OWN count, not whatever happened that day")
    # An unrelated adjustment on the same day must not be mistaken for
    # this count's correction, or one session's number lands in another's
    # report.
    store, ledger, sid = build()
    ledger.record(sid, "m1", "sale", -5, occurred_at="2026-07-05T00:00:00")
    ledger.record(sid, "m1", "count", -9, occurred_at="2026-07-08T01:00:00", ref="other")
    ledger.record(sid, "m1", "count", -1, occurred_at="2026-07-08T02:00:00", ref="s2")

    rows = analyse_session(ledger, sid, session("s2", "2026-07-08T02:00:00", {"m1": 1}),
                           session("s1", "2026-07-01T00:00:00", {}),
                           [material("m1", "กุ้ง")])

    check("used this session's correction", rows[0]["variance_qty"], -1.0)


def test_rows_are_ordered_by_money_lost():
    section("Biggest loss first - that's the order someone can act on")
    store, ledger, sid = build()
    for mid, sale, delta in [("m1", -10, -0.5), ("m2", -10, -3), ("m3", -10, 2)]:
        ledger.record(sid, mid, "sale", sale, occurred_at="2026-07-05T00:00:00")
        ledger.record(sid, mid, "count", delta, occurred_at="2026-07-08T00:00:00", ref="s2")

    rows = analyse_session(
        ledger, sid, session("s2", "2026-07-08T00:00:00", {"m1": 1, "m2": 1, "m3": 1}),
        session("s1", "2026-07-01T00:00:00", {}),
        [material("m1", "A", cost=100), material("m2", "B", cost=100), material("m3", "C", cost=100)])

    check("worst loss first", [r["name"] for r in rows], ["B", "A", "C"])


def test_materials_deleted_since_the_count_are_skipped():
    section("A material removed after being counted is dropped, not shown as blank")
    store, ledger, sid = build()
    ledger.record(sid, "gone", "count", -1, occurred_at="2026-07-08T00:00:00", ref="s2")
    rows = analyse_session(ledger, sid, session("s2", "2026-07-08T00:00:00", {"gone": 1}),
                           None, [])
    check("no row for a material that no longer exists", rows, [])


def test_unmeasured_menus_are_named():
    section("Menus sold without a recipe are named, not silently ignored")
    # Their ingredients left the kitchen with nothing recording it, so they
    # surface as unexplained loss. A report that doesn't say so looks just
    # as confident as one that's complete.
    sold = {"ข้าวผัดกุ้ง", "ต้มยำ", "ค่าเปิดขวด", "ผัดไท"}
    recipes = {"ข้าวผัดกุ้ง": [{"material_id": "m1", "qty": 1}], "ต้มยำ": [], "ผัดไท": []}

    check("only the unaccounted cooked menus",
          unmeasured_menus(sold, recipes, ["ค่าเปิดขวด"]), ["ต้มยำ", "ผัดไท"])
    check("nothing to report when all are covered",
          unmeasured_menus({"ข้าวผัดกุ้ง"}, recipes, []), [])


def test_summary_totals():
    section("The headline figures")
    rows = [
        {"variance_qty": -2, "variance_value": -300, "flagged": True, "measurable": True},
        {"variance_qty": -1, "variance_value": -50, "flagged": False, "measurable": True},
        {"variance_qty": 1, "variance_value": 80, "flagged": False, "measurable": True},
        {"variance_qty": -1, "variance_value": -10, "flagged": False, "measurable": False},
    ]
    s = summarise(rows)
    check("shortfall value sums only losses", s["shortfall_value"], 360.0)
    check("flagged count", s["flagged_count"], 1)
    check("unmeasurable count", s["unmeasurable_count"], 1)
    check("counted count", s["counted_count"], 4)


def test_count_sessions_stay_out_of_the_ledger_until_closed():
    section("An open count writes nothing - a half-done count is worse than none")
    # If a partial count committed, every material nobody had reached yet
    # would read as "counted and correct".
    store = make_test_store()
    ledger = MovementLedger(store)
    s = store.create_count_session("branch1", "2026-07-24T09:00:00")
    store.set_count_entry("branch1", s["id"], "m1", 4.5)

    check("entry saved on the session", store.get_count_session("branch1", s["id"])["entries"],
          {"m1": 4.5})
    check("but nothing in the ledger yet", ledger.list_movements("branch1"), [])
    check("session still open", store.get_count_session("branch1", s["id"])["status"], "open")


def test_only_one_count_runs_at_a_time():
    section("Two open counts would each hold a different idea of the same shelf")
    store = make_test_store()
    store.create_count_session("branch1", "2026-07-24T09:00:00")
    check("an open session is found", store.open_count_session("branch1") is not None, True)

    store.close_count_session("branch1", store.open_count_session("branch1")["id"],
                              "2026-07-24T11:00:00")
    check("none open after closing", store.open_count_session("branch1"), None)


def test_previous_session_is_the_one_just_before():
    section("The period starts at the count immediately before this one")
    store = make_test_store()
    for started, closed in [("2026-06-01", "2026-06-01T10:00:00"),
                            ("2026-07-01", "2026-07-01T10:00:00")]:
        s = store.create_count_session("branch1", started)
        store.close_count_session("branch1", s["id"], closed)

    prev = store.previous_closed_session("branch1", "2026-07-08T10:00:00")
    check("picks the most recent earlier count", prev["closed_at"], "2026-07-01T10:00:00")
    check("nothing before the first count",
          store.previous_closed_session("branch1", "2026-01-01T00:00:00"), None)


def test_count_sessions_are_per_branch():
    section("One branch's count doesn't appear in another's")
    store = make_test_store()
    store.create_count_session("branch1", "2026-07-24T09:00:00")
    check("branch2 has no session", store.open_count_session("branch2"), None)


def main():
    print("Running variance and stock count tests (offline)")

    test_shortfall_is_measured_against_usage_not_stock()
    test_both_thresholds_must_be_crossed()
    test_surplus_is_reported_but_never_flagged()
    test_no_usage_means_no_percentage_and_no_flag()
    test_only_movements_inside_the_period_count()
    test_waste_is_shown_separately_not_netted_off()
    test_a_counts_correction_is_matched_by_session()
    test_rows_are_ordered_by_money_lost()
    test_materials_deleted_since_the_count_are_skipped()
    test_unmeasured_menus_are_named()
    test_summary_totals()
    test_count_sessions_stay_out_of_the_ledger_until_closed()
    test_only_one_count_runs_at_a_time()
    test_previous_session_is_the_one_just_before()
    test_count_sessions_are_per_branch()

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
