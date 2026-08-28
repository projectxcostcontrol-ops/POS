"""
Tests for the slice of shop data the assistant is allowed to ask for.

The point of this module is that the model chooses WHICH numbers and
never produces one. So what is protected here is not really the
arithmetic - it is the two ways a query engine lets a wrong number
through anyway.

  a refusal that isn't      - a spec asking for something the data cannot
                              answer must come back as a refusal with the
                              reason, never as the nearest thing that
                              could be computed. "Grab's best dish" that
                              quietly returns the whole shop's best dish
                              is a lie with a number attached.

  a total that isn't        - the sum of seven daily averages is not the
                              average, and the sum of seven margins is not
                              the margin. Both look exactly like totals.

Offline, in-memory. Run with:

    cd backend
    python tests/test_shop_query.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import shop_query as query
from core import daily_rollup as roll

BKK = 420

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def refuses(label, spec, contains):
    try:
        query.clean_spec(spec)
        check(label, "accepted", "refused")
    except query.QueryError as e:
        ok = contains in str(e)
        _results.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {e}")


def section(title):
    print(f"\n=== {title} ===")


def sale(date, total, items, **extra):
    row = {"date": date, "total": total, "items": items}
    row.update(extra)
    return row


def item(name, qty, price):
    return {"name": name, "qty": qty, "price": price}


# 2026-07-01 is a Wednesday. Two ordinary days, one Grab order, and a
# menu with no recipe.
SALES = [
    sale("2026-07-01T05:00:00.000Z", 200, [item("ข้าวผัด", 2, 100)]),
    sale("2026-07-01T06:00:00.000Z", 100, [item("ข้าวผัด", 1, 100)]),
    sale("2026-07-02T05:00:00.000Z", 300, [item("ผัดไทย", 3, 100)]),
    sale("2026-07-02T07:00:00.000Z", 150, [item("น้ำเปล่า", 15, 10)], source="grab"),
    sale("2026-07-08T05:00:00.000Z", 400, [item("ข้าวผัด", 4, 100)]),
]
RECIPES = {"ข้าวผัด": [{"material_id": "rice", "qty": 0.5}],
           "ผัดไทย": [{"material_id": "rice", "qty": 0.2}]}
MATERIALS = [{"id": "rice", "name": "ข้าวสาร", "cost": 40}]
EXPENSES = [
    {"category": "fixed", "name": "ค่าเช่า", "amount": 12000, "date": "2026-07-01"},
    {"category": "variable", "name": "ค่าไฟ", "amount": 3400, "date": "2026-07-05"},
    {"category": "variable", "name": "ค่าไฟ", "amount": 3600, "date": "2026-08-05"},
]


def rollups():
    return list(roll.build_many(SALES, BKK).values())


def run(**spec):
    return query.run(spec, rollups=rollups(), recipes=RECIPES,
                     materials=MATERIALS, expenses=EXPENSES)


def test_the_cuts_that_were_impossible_before():
    section("The cuts that were impossible before")

    week = run(group_by="weekday", metrics=["sales", "bills"], sort="sales")
    rows = {r["group"]: r for r in week["rows"]}
    check("a day of the week gathers its dates",
          rows["พุธ"]["sales"], 300 + 400)
    check("counting the days it gathered", rows["พุธ"]["days"], 2)
    check("and Thursday keeps its own", rows["พฤหัสบดี"]["sales"], 450)
    check("ranked by the metric asked for",
          [r["group"] for r in week["rows"]], ["พุธ", "พฤหัสบดี"])

    menus = run(group_by="menu", metrics=["sales", "qty", "gross_profit"],
                sort="gross_profit")
    check("per-menu revenue",
          {r["group"]: r["sales"] for r in menus["rows"]},
          {"ข้าวผัด": 700, "ผัดไทย": 300, "น้ำเปล่า": 150})
    check("and quantity", {r["group"]: r["qty"] for r in menus["rows"]}["ข้าวผัด"], 7)

    channels = run(group_by="channel", metrics=["sales", "bills"])
    check("each channel's takings",
          {r["group"]: r["sales"] for r in channels["rows"]},
          {"loyverse": 1000, "grab": 150})

    months = run(group_by="month", metrics=["sales"])
    check("months group too", months["rows"][0], {"group": "2026-07", "days": 3,
                                                  "sales": 1150})

    check("a window narrows it",
          run(from_="x", **{}) if False else
          query.run({"from": "2026-07-02", "to": "2026-07-02",
                     "group_by": "none", "metrics": ["sales"]},
                    rollups=rollups())["totals"]["sales"], 450)


def test_what_it_refuses_rather_than_answers_with_something_near():
    section("What it refuses rather than answering with something near")

    # The day's rollup knows what each channel took, and what each menu
    # sold, but never which menu each channel sold. Answering this from
    # the whole shop's menu mix would be a real-looking lie.
    refuses("a channel's menu mix is not recorded",
            {"group_by": "menu", "filter": {"channel": "grab"}, "metrics": ["sales"]},
            "ไม่ได้เก็บว่าแต่ละช่องทางขายเมนูไหน")
    refuses("nor a channel's profit",
            {"group_by": "month", "filter": {"channel": "grab"},
             "metrics": ["gross_profit"]},
            "ได้แค่ sales, bills, avg_bill")
    refuses("a bill count per menu is not a quantity that exists",
            {"group_by": "menu", "metrics": ["bills"]},
            "หนึ่งบิลมีได้หลายเมนู")
    refuses("quantity only means something per menu",
            {"group_by": "day", "metrics": ["qty"]},
            "เฉพาะตอนแบ่งกลุ่มตามเมนู")
    refuses("an invented metric", {"group_by": "day", "metrics": ["profit_margin"]},
            "ไม่มี metric")
    refuses("an invented dataset", {"dataset": "customers"}, "ไม่มี dataset")
    refuses("an invented filter is refused, not ignored - a dropped filter "
            "turns 'Grab only' into 'everything'",
            {"group_by": "day", "filter": {"employee": "somchai"}}, "กรองด้วย")
    refuses("an unparseable date", {"from": "07/2026"}, "ไม่ถูกรูปแบบ")
    refuses("sorting by something not asked for",
            {"group_by": "day", "metrics": ["sales"], "sort": "bills"}, "เรียงตาม")

    ok = run(group_by="month", filter={"channel": "grab"}, metrics=["sales", "bills"])
    check("what a channel CAN answer still answers",
          ok["totals"], {"sales": 150, "bills": 1})
    check("and says why it is limited",
          "ได้แค่ยอดขายกับจำนวนบิล" in (ok["note"] or ""), True)


def test_a_total_that_is_actually_the_total():
    section("A total that is actually the total")

    week = run(group_by="weekday", metrics=["sales", "bills", "avg_bill"])
    check("takings add up", week["totals"]["sales"], 1150)
    check("bills add up", week["totals"]["bills"], 5)
    # Two weekday rows with averages of 233.33 and 225. Adding them gives
    # 458.33, which looks like a number and is not one.
    check("the average bill is recomputed from the totals, not summed",
          week["totals"]["avg_bill"], round(1150 / 5, 2))

    menus = run(group_by="menu", metrics=["sales", "gross_profit", "gross_margin_pct"],
                sort="gross_profit")
    check("margin comes from the totals too",
          menus["totals"]["gross_margin_pct"],
          round(menus["totals"]["gross_profit"] / menus["totals"]["sales"] * 100, 1))

    check("a menu with no recipe has no profit, not zero profit",
          [r["gross_profit"] for r in menus["rows"] if r["group"] == "น้ำเปล่า"],
          [None])
    check("and sorts last rather than first - 'least profitable' must not "
          "mean 'profit unknown'",
          menus["rows"][-1]["group"], "น้ำเปล่า")

    top = run(group_by="menu", metrics=["sales"], sort="sales", limit=1)
    check("a top-one shows one row", len(top["rows"]), 1)
    check("but the total is still the whole result, not the row shown",
          top["totals"]["sales"], 1150)
    check("and says it was cut", top["truncated"], True)


def test_expenses_are_their_own_shape():
    section("Expenses are their own shape")

    by_cat = query.run({"dataset": "expenses", "from": "2026-07-01",
                        "to": "2026-07-31", "group_by": "category",
                        "metrics": ["amount", "count"]}, expenses=EXPENSES)
    check("grouped by category",
          {r["group"]: r["amount"] for r in by_cat["rows"]},
          {"fixed": 12000, "variable": 3400})
    check("August stayed out of July", by_cat["totals"]["amount"], 15400)
    check("it says what it does not include",
          "ไม่รวมค่าวัตถุดิบ" in (by_cat["note"] or ""), True)

    refuses("expenses cannot be grouped by a sales dimension",
            {"dataset": "expenses", "group_by": "menu"}, "แบ่งกลุ่มตาม")

    months = query.run({"dataset": "expenses", "group_by": "month",
                        "metrics": ["amount"], "sort": "group"}, expenses=EXPENSES)
    check("month by month, in order rather than by size - a time series "
          "sorted by value is not a time series",
          [r["group"] for r in months["rows"]], ["2026-07", "2026-08"])


def test_the_schema_tells_the_model_what_is_missing():
    section("The schema tells the model what is missing")

    schema = query.describe()
    check("both datasets are described", sorted(schema["datasets"]), ["expenses", "sales"])
    check("with an example to copy", schema["spec"]["group_by"], "weekday")
    missing = " ".join(schema["ไม่ได้เก็บไว้"])
    # Naming the gaps is what turns "there is no data" into "this is not
    # recorded, and here is what to record".
    check("time of day is named as missing", "กี่โมง" in missing, True)
    check("and the menu-by-channel gap the engine refuses on",
          "เมนูแยกตามช่องทาง" in missing, True)

    # The delivery commission IS recorded - as an ordinary expense the
    # owner types in. Listing it as missing would have had the assistant
    # telling shops their own bookkeeping does not exist.
    check("the delivery commission is NOT listed as missing",
          "ค่าคอม" in missing, False)
    check("the expenses dataset says it holds it",
          "ค่าคอม" in schema["datasets"]["expenses"]["คือ"], True)
    check("with the two-sided answer spelled out, since neither dataset "
          "alone holds a channel's profit",
          any("กำไรของช่องทางเดลิเวอรี" in line
              for line in schema["ตอบได้แต่ต้องประกอบสองฝั่ง"]), True)

    named = query.describe(expense_names=["ค่าเช่า", "ค่าคอม Grab", "ค่าเช่า"])
    check("the names that actually exist are shown, because an expense is "
          "whatever the owner typed and no field name can find it",
          named["datasets"]["expenses"]["ชื่อรายการที่มีอยู่จริงในช่วงนี้"],
          ["ค่าคอม Grab", "ค่าเช่า"])
    check("and the model is told the pairing is by name, so it can say so",
          "จับคู่จากชื่อ" in named["datasets"]["expenses"]["หมายเหตุ"], True)


def main():
    print("Shop query")
    print("=" * 50)

    test_the_cuts_that_were_impossible_before()
    test_what_it_refuses_rather_than_answers_with_something_near()
    test_a_total_that_is_actually_the_total()
    test_expenses_are_their_own_shape()
    test_the_schema_tells_the_model_what_is_missing()

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
