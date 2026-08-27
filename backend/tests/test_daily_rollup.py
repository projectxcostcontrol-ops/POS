"""
Tests for the one-document-per-day rollup.

A month of sales is three thousand documents, and every screen that shows
a month was reading all of them. The rollup replaces that with thirty
rows - but only if the thirty rows say the same thing the three thousand
did. That equality is the whole contract, and it is what most of this
file checks: the same bills, summarised both ways, must agree to the
satang.

The second thing worth protecting is what the rollup deliberately does
NOT store. Quantities sold are facts and are stored; ingredient cost is
not - it is those quantities against a recipe and a price that both keep
getting corrected for weeks afterwards. If cost were stored, correcting
last week's onion price would leave last week's profit wrong until
something rebuilt it. So there is a test here that corrects a price and
insists the past moves on its own.

Third: the shop's day, not UTC's. A bill rung at seven in the evening in
Bangkok is already tomorrow in UTC. The old daily_breakdown grouped by
UTC while the chart beside it grouped by local time, so one screen
disagreed with itself about which day an evening sale belonged to.

Offline, in-memory. Run with:

    cd backend
    python tests/test_daily_rollup.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import daily_rollup as roll
from core import sales_report
from tests.fake_firestore import make_test_store, FakeDb
from core.stock_engine import sync_branch

BKK = 420

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


def sale(date, total, items, **extra):
    row = {"date": date, "total": total, "items": items}
    row.update(extra)
    return row


def item(name, qty, price):
    return {"name": name, "qty": qty, "price": price}


# A week of a small shop: two menus, one refund, one delivery order, and
# two evening bills that UTC would file under the following day.
SALES = [
    sale("2026-08-03T04:30:00.000Z", 120, [item("ข้าวผัด", 2, 60)]),
    sale("2026-08-03T14:00:00.000Z", 180, [item("ข้าวผัด", 1, 60), item("ผัดไทย", 2, 60)]),
    sale("2026-08-03T17:30:00.000Z", 60, [item("ข้าวผัด", 1, 60)]),   # 00:30 on the 4th in Bangkok
    sale("2026-08-04T02:00:00.000Z", 240, [item("ผัดไทย", 4, 60)]),
    sale("2026-08-04T03:00:00.000Z", -60, [item("ข้าวผัด", -1, 60)], is_refund=True),
    sale("2026-08-05T06:00:00.000Z", 300, [item("ผัดไทย", 5, 60)], source="grab"),
    sale("2026-08-06T18:00:00.000Z", 90, [item("น้ำเปล่า", 9, 10)]),  # 01:00 on the 7th
]

RECIPES = {
    "ข้าวผัด": [{"material_id": "rice", "qty": 0.2}, {"material_id": "egg", "qty": 1}],
    "ผัดไทย": [{"material_id": "noodle", "qty": 0.15}, {"material_id": "egg", "qty": 1}],
}

MATERIALS = [
    {"id": "rice", "cost": 40},
    {"id": "noodle", "cost": 60},
    {"id": "egg", "cost": 5},
]


def test_a_shop_day_is_the_shops_day():
    section("A shop day is the shop's day, not UTC's")

    check("evening bill belongs to the shop's date",
          roll.local_day("2026-08-03T17:30:00.000Z", BKK), "2026-08-04")
    check("midday bill is unmoved",
          roll.local_day("2026-08-03T06:00:00.000Z", BKK), "2026-08-03")
    check("a timestamp carrying its own offset is not shifted twice",
          roll.local_day("2026-08-04T00:30:00.000+07:00", BKK), "2026-08-04")
    check("a shop at UTC is unshifted",
          roll.local_day("2026-08-03T17:30:00.000Z", 0), "2026-08-03")
    check("an unreadable timestamp is refused, not guessed",
          roll.local_day("not a date", BKK), None)
    check("an absent timestamp is refused, not guessed",
          roll.local_day("", BKK), None)

    start, end = roll.day_bounds("2026-08-04", BKK)
    check("a Bangkok day starts at 5pm UTC the day before",
          start, "2026-08-03T17:00:00.000Z")
    check("and ends a millisecond before the next one starts",
          end, "2026-08-04T16:59:59.999Z")
    check("the day's own bills fall inside its bounds",
          all(start <= s["date"] <= end for s in SALES
              if roll.local_day(s["date"], BKK) == "2026-08-04"), True)
    check("and no others do",
          [s["total"] for s in SALES if start <= s["date"] <= end],
          [60, 240, -60])

    check("a span of days is every day in it, ends included",
          roll.days_between("2026-08-03", "2026-08-06"),
          ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06"])
    check("a single day is a span of one",
          roll.days_between("2026-08-03", "2026-08-03"), ["2026-08-03"])
    check("a backwards span is empty, not a year of days",
          roll.days_between("2026-08-06", "2026-08-03"), [])
    check("a span across a month end keeps counting",
          roll.days_between("2026-07-30", "2026-08-02"),
          ["2026-07-30", "2026-07-31", "2026-08-01", "2026-08-02"])


def test_thirty_rows_say_what_three_thousand_said():
    section("Thirty rows say what three thousand said")

    rollups = list(roll.build_many(SALES, BKK).values())
    from_rollup = roll.summarise(rollups, RECIPES, MATERIALS)
    from_raw = sales_report.summarise(SALES, RECIPES, MATERIALS,
                                      granularity="day", tz_offset_minutes=BKK)

    check("takings agree", from_rollup["total"], from_raw["total"])
    check("bill count agrees", from_rollup["bill_count"], from_raw["bill_count"])
    check("refund count agrees", from_rollup["refund_count"], from_raw["refund_count"])
    check("ingredient cost agrees",
          from_rollup["ingredient_cost"], from_raw["ingredient_cost"])
    check("gross profit agrees", from_rollup["gross_profit"], from_raw["gross_profit"])
    check("the uncosted menu is named by both",
          from_rollup["uncosted_menus"], from_raw["uncosted_menus"])
    check("and it is the one with no recipe",
          from_rollup["uncosted_menus"], ["น้ำเปล่า"])
    check("the chart points agree, day for day",
          from_rollup["points"], from_raw["points"])

    check("best sellers rank the same",
          [r["name"] for r in roll.top_items(rollups)],
          [r["name"] for r in sales_report.top_items(SALES)])
    check("with the same quantities",
          [r["qty"] for r in roll.top_items(rollups)],
          [r["qty"] for r in sales_report.top_items(SALES)])
    check("and the same revenue",
          [r["revenue"] for r in roll.top_items(rollups)],
          [r["revenue"] for r in sales_report.top_items(SALES)])


def test_a_days_facts():
    section("A day's facts")

    days = roll.build_many(SALES, BKK)
    check("the week fell into four shop days",
          sorted(days), ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-07"])

    third = days["2026-08-03"]
    check("the 3rd took what its two daytime bills took", third["total"], 300)
    check("counted as two bills", third["bill_count"], 2)
    check("with no refunds", third["refund_count"], 0)
    check("three plates of ข้าวผัด", third["items"]["ข้าวผัด"]["qty"], 3)
    check("worth 180 baht", third["items"]["ข้าวผัด"]["revenue"], 180)

    fourth = days["2026-08-04"]
    check("the evening bill landed on the 4th, not the 3rd",
          fourth["total"], 240)
    check("a refund is not a bill", fourth["bill_count"], 2)
    check("but it is counted", fourth["refund_count"], 1)
    check("and it subtracts from takings",
          fourth["total"], 60 + 240 - 60)

    fifth = days["2026-08-05"]
    check("a delivery order is filed under its channel",
          fifth["by_source"], {"grab": {"total": 300, "count": 1}})
    check("a POS bill with no source reads as the POS",
          sorted(third["by_source"]), ["loyverse"])

    check("a day the shop was closed is a stored zero, not an absence",
          roll.empty("2026-08-06"),
          {"date": "2026-08-06", "total": 0.0, "bill_count": 0,
           "refund_count": 0, "by_source": {}, "items": {}})
    check("summarising nothing is zero, not a crash",
          roll.summarise([roll.empty("2026-08-06")], RECIPES, MATERIALS)["total"], 0)


def test_a_corrected_price_moves_the_past_on_its_own():
    section("A corrected price moves the past on its own")

    rollups = list(roll.build_many(SALES, BKK).values())
    before = roll.summarise(rollups, RECIPES, MATERIALS)

    # The eggs turn out to have cost twice what was entered. Nothing is
    # rebuilt; the same stored rows are read again.
    dearer = [{**m, "cost": m["cost"] * 2} if m["id"] == "egg" else m
              for m in MATERIALS]
    after = roll.summarise(rollups, RECIPES, dearer)

    eggs = sum(e["qty"] for r in rollups
               for name, e in r["items"].items() if name in RECIPES)
    check("cost rose by exactly one extra baht-per-egg",
          round(after["ingredient_cost"] - before["ingredient_cost"], 2),
          round(eggs * 5, 2))
    check("and profit fell by the same",
          round(before["gross_profit"] - after["gross_profit"], 2),
          round(eggs * 5, 2))
    check("takings did not move - they are a fact",
          after["total"], before["total"])

    # Same for a recipe corrected after the fact.
    fixed = {**RECIPES, "น้ำเปล่า": [{"material_id": "water", "qty": 1}]}
    costed = roll.summarise(rollups, fixed, MATERIALS + [{"id": "water", "cost": 3}])
    check("a menu costed today stops being reported as uncosted",
          costed["uncosted_menus"], [])
    check("and its cost appears in the past",
          round(costed["ingredient_cost"] - before["ingredient_cost"], 2), 27.0)

    check("the stored rows themselves were never touched",
          rollups == list(roll.build_many(SALES, BKK).values()), True)


def test_menu_performance_never_calls_missing_cost_profit():
    section("Profit per menu is computed, and missing cost stays missing")
    rollups = list(roll.build_many(SALES, BKK).values())
    rows = {row["name"]: row for row in
            roll.menu_performance(rollups, RECIPES, MATERIALS)}

    rice = rows["ข้าวผัด"]
    check("recipe unit cost is precomputed", rice["unit_cost"], 13.0)
    check("menu ingredient cost is quantity times unit cost",
          rice["ingredient_cost"], round(rice["qty"] * 13, 2))
    check("gross profit is precomputed", rice["gross_profit"],
          round(rice["revenue"] - rice["ingredient_cost"], 2))
    check("margin is precomputed", rice["gross_margin_pct"],
          round(rice["gross_profit"] / rice["revenue"] * 100, 1))

    water = rows["น้ำเปล่า"]
    check("an uncosted menu is named as uncosted", water["costed"], False)
    check("its unknown cost is not silently zero", water["ingredient_cost"], None)
    check("and its unknown profit is not promoted", water["gross_profit"], None)


def test_the_list_and_the_chart_agree():
    section("The list and the chart agree")

    rollups = list(roll.build_many(SALES, BKK).values())
    rows = roll.breakdown(rollups)
    points = roll.summarise(rollups, RECIPES, MATERIALS)["points"]

    check("the list is newest first",
          [r["date"] for r in rows],
          ["2026-08-07", "2026-08-05", "2026-08-04", "2026-08-03"])
    check("every day in the list is a day on the chart",
          sorted(r["date"] for r in rows), sorted(p["t"] for p in points))
    check("with the same total on both",
          {r["date"]: r["total"] for r in rows},
          {p["t"]: p["sales"] for p in points})
    check("the evening bill is on the 4th in the list too",
          {r["date"]: r["total"] for r in rows}["2026-08-04"], 240)
    check("a refund still counts as a line on the day's bill count",
          {r["date"]: r["bill_count"] for r in rows}["2026-08-04"], 3)


def stored_month(bills_per_day=100, days=30, month="2026-07"):
    """A branch with a real month behind it, saved as raw bills."""
    store = make_test_store(db=FakeDb())
    for d in range(1, days + 1):
        day = f"{month}-{d:02d}"
        for n in range(bills_per_day):
            # 05:00 UTC is midday in Bangkok - safely inside the shop's day
            # whichever way the boundary is worked out.
            store.save_sale("b1", f"{day}-{n}", {
                "date": f"{day}T05:{n % 60:02d}:00.000Z",
                "total": 100,
                "items": [item("ข้าวผัด", 1, 100)],
            })
    return store


def test_a_month_is_read_as_a_month_of_rows():
    section("A month is read as a month of rows, not a month of bills")

    store = stored_month()
    args = ("b1", "2026-07-01", "2026-07-30", BKK, "2026-08-27")

    store.db.reset_meter()
    first = roll.ensure_daily(store, *args)
    build_reads = store.db.reads
    check("building the month covers every day", len(first), 30)
    check("and each day holds its own takings",
          sorted({row["total"] for row in first}), [10000])
    check("building it read the bills once", build_reads >= 3000, True)

    store.db.reset_meter()
    again = roll.ensure_daily(store, *args)
    check("reading it back gives the same days",
          [r["date"] for r in again], [r["date"] for r in first])
    check("saying the same thing", again == first, True)
    check("and costs the rows, not the bills - 30 days under 35 reads",
          store.db.reads <= 35, True)
    check("with nothing rewritten", store.db.writes, 0)

    check("the rollup total is the month's takings",
          roll.summarise(again, RECIPES, MATERIALS)["total"], 300000)


def test_today_is_never_stored():
    section("Today is never stored")

    store = make_test_store(db=FakeDb())
    for n in range(3):
        store.save_sale("b1", f"m-{n}", {
            "date": f"2026-08-27T0{n + 4}:00:00.000Z", "total": 50,
            "items": [item("ข้าวผัด", 1, 50)]})

    rows = roll.ensure_daily(store, "b1", "2026-08-26", "2026-08-27", BKK,
                             "2026-08-27")
    check("today is answered", [r["date"] for r in rows],
          ["2026-08-26", "2026-08-27"])
    check("with the takings so far", rows[1]["total"], 150)
    check("but only yesterday was written down",
          [r["date"] for r in store.list_daily("b1", "2026-08-01", "2026-08-31")],
          ["2026-08-26"])

    # The afternoon's remaining bills arrive.
    store.save_sale("b1", "m-late", {"date": "2026-08-27T10:00:00.000Z",
                                     "total": 500,
                                     "items": [item("ข้าวผัด", 10, 50)]})
    later = roll.ensure_daily(store, "b1", "2026-08-27", "2026-08-27", BKK,
                              "2026-08-27")
    check("and today grows with them rather than being frozen at lunchtime",
          later[0]["total"], 650)

    check("a day beyond today is not invented",
          roll.ensure_daily(store, "b1", "2026-08-28", "2026-08-29", BKK,
                            "2026-08-27"), [])


def test_a_closed_day_is_a_stored_zero():
    section("A closed day is a stored zero")

    store = make_test_store(db=FakeDb())
    store.save_sale("b1", "s1", {"date": "2026-08-03T05:00:00.000Z",
                                 "total": 200,
                                 "items": [item("ข้าวผัด", 2, 100)]})

    rows = roll.ensure_daily(store, "b1", "2026-08-01", "2026-08-05", BKK,
                             "2026-08-27")
    check("every day in the span comes back", len(rows), 5)
    check("the closed ones as zero",
          [r["total"] for r in rows], [0, 0, 200, 0, 0])

    store.db.reset_meter()
    roll.ensure_daily(store, "b1", "2026-08-01", "2026-08-05", BKK, "2026-08-27")
    check("and asking again does not go back to the bills to rediscover it",
          store.db.writes, 0)


def test_a_day_that_changed_is_thrown_away_not_patched():
    section("A day that changed is thrown away, not patched")

    store = make_test_store(db=FakeDb())
    store.save_sale("b1", "s1", {"date": "2026-08-03T05:00:00.000Z",
                                 "total": 200,
                                 "items": [item("ข้าวผัด", 2, 100)]})
    roll.ensure_daily(store, "b1", "2026-08-03", "2026-08-03", BKK, "2026-08-27")

    # A delivery order for that day is keyed in two days later.
    store.save_sale("b1", "grab-1", {"date": "2026-08-03T11:00:00.000Z",
                                     "total": 300, "source": "grab",
                                     "items": [item("ผัดไทย", 5, 60)]})
    stale = roll.ensure_daily(store, "b1", "2026-08-03", "2026-08-03", BKK,
                              "2026-08-27")
    check("a stored day does not notice on its own", stale[0]["total"], 200)

    store.delete_daily("b1", "2026-08-03")
    rebuilt = roll.ensure_daily(store, "b1", "2026-08-03", "2026-08-03", BKK,
                                "2026-08-27")
    check("throwing the day away rebuilds it from the bills",
          rebuilt[0]["total"], 500)
    check("with the new order under its own channel",
          sorted(rebuilt[0]["by_source"]), ["grab", "loyverse"])

    store.delete_daily("b1", ["2026-08-03", "2026-08-04"])
    check("throwing away a day that was never built is not an error",
          store.list_daily("b1", "2026-08-01", "2026-08-31"), [])


def test_two_branches_do_not_share_a_day():
    section("Two branches do not share a day")

    store = make_test_store(db=FakeDb())
    store.save_sale("b1", "s1", {"date": "2026-08-03T05:00:00.000Z",
                                 "total": 200, "items": []})
    store.save_sale("b2", "s1", {"date": "2026-08-03T05:00:00.000Z",
                                 "total": 900, "items": []})

    one = roll.ensure_daily(store, "b1", "2026-08-03", "2026-08-03", BKK, "2026-08-27")
    two = roll.ensure_daily(store, "b2", "2026-08-03", "2026-08-03", BKK, "2026-08-27")
    check("each branch keeps its own takings",
          [one[0]["total"], two[0]["total"]], [200, 900])

    other = make_test_store(tenant_id="t2", db=store.db)
    check("another business sees nothing of either",
          other.list_daily("b1", "2026-08-01", "2026-08-31"), [])


def test_the_shop_remembers_where_it_is():
    section("The shop remembers where it is")

    store = make_test_store(db=FakeDb())
    check("a shop that has never said reads as Bangkok", store.get_timezone(), 420)
    check("the first browser to say sets it", store.set_timezone(420), 420)
    check("and it is stored", store.get_timezone(), 420)
    check("a browser in another country does not redraw the shop's days",
          store.set_timezone(-300), 420)
    check("unless it is meant to", store.set_timezone(-300, only_if_unset=False), -300)
    check("and then that is what is stored", store.get_timezone(), -300)

    try:
        store.set_timezone(9999, only_if_unset=False)
        check("an impossible offset is refused", "accepted", "refused")
    except ValueError:
        _results.append(True)
        print("  [PASS] an impossible offset is refused")


class FakeProvider:
    def __init__(self, receipts):
        self.receipts = receipts

    def get_receipts(self, store_id, created_at_min=None):
        return self.receipts


def test_a_till_that_was_offline_corrects_the_day_it_missed():
    section("A till that was offline corrects the day it missed")

    store = make_test_store(db=FakeDb())
    store.set_timezone(BKK)
    store.save_sale("b1", "1", {"receipt_number": "1", "total": 200,
                                "date": "2026-08-03T05:00:00.000Z",
                                "items": [item("ข้าวผัด", 2, 100)]})
    built = roll.ensure_daily(store, "b1", "2026-08-03", "2026-08-03", BKK,
                              "2026-08-27")
    check("the day was summarised as it stood", built[0]["total"], 200)

    # The second till is plugged back in and its bills for that day
    # arrive days later, through the ordinary sync.
    late = {"receipt_number": "2", "total": 350, "line_items": [],
            "created_at": "2026-08-03T06:00:00.000Z",
            "recorded_at": "2026-08-26T09:00:00.000Z"}
    store.set_sync_cursor("b1", "2026-08-26T00:00:00.000Z")
    sync_branch(FakeProvider([late]), store, "b1")

    check("the stale day was thrown away, not left standing",
          store.list_daily("b1", "2026-08-01", "2026-08-31"), [])
    rebuilt = roll.ensure_daily(store, "b1", "2026-08-03", "2026-08-03", BKK,
                                "2026-08-27")
    check("and rebuilds with the late bill in it", rebuilt[0]["total"], 550)


def test_an_ordinary_sync_does_not_write_to_clear_today():
    section("An ordinary sync does not write to clear today")

    store = make_test_store(db=FakeDb())
    store.set_timezone(BKK)
    store.set_sync_cursor("b1", roll.day_bounds("2026-08-27", BKK)[0])

    from datetime import datetime, timezone as _tz
    now = datetime.now(_tz.utc)
    today_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    fresh = {"receipt_number": "9", "total": 100, "line_items": [],
             "created_at": today_iso, "recorded_at": today_iso}

    # Today is never stored, so there is nothing to throw away - and a
    # delete every five minutes, forever, to remove a document that was
    # never written is exactly the kind of cost that hides in a feature
    # whose answers are all correct.
    cleared = []
    store.delete_daily = lambda store_id, days: cleared.append(days)

    sync_branch(FakeProvider([fresh]), store, "b1")
    check("a sync of today's bills clears nothing", cleared, [])


def main():
    print("Daily rollup")
    print("=" * 50)

    test_a_shop_day_is_the_shops_day()
    test_thirty_rows_say_what_three_thousand_said()
    test_a_days_facts()
    test_a_corrected_price_moves_the_past_on_its_own()
    test_the_list_and_the_chart_agree()
    test_a_month_is_read_as_a_month_of_rows()
    test_today_is_never_stored()
    test_a_closed_day_is_a_stored_zero()
    test_a_day_that_changed_is_thrown_away_not_patched()
    test_two_branches_do_not_share_a_day()
    test_the_shop_remembers_where_it_is()
    test_a_till_that_was_offline_corrects_the_day_it_missed()
    test_an_ordinary_sync_does_not_write_to_clear_today()

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
