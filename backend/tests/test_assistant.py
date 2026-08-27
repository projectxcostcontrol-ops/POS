"""
Tests for what the assistant is allowed to know and to say.

The thing being protected here is not "does it give a good answer" -
that is the model's job and no test can hold it to it. It is the two
walls either side of the model, both of which exist because a shop owner
cannot check an answer against anything. That is why they asked.

  the snapshot   - every figure worked out in Python before the call, so
                   the model never has to do arithmetic to answer. A
                   model asked to divide always produces a number; it
                   does not always produce the right one.

  verify_numbers - a figure in the answer that the data cannot account
                   for gets named afterwards, in the same spirit as
                   uncosted_menus.

And a third thing, quieter but worth a test of its own: what must NEVER
end up in the snapshot. It is sent to Google on every question, and the
next person to add a field is deciding what leaves the shop.

Offline, in-memory. Run with:

    cd backend
    python tests/test_assistant.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import assistant
from core import daily_rollup as roll
from core.assistant_provider import AssistantProvider, AssistantError
from tests.fake_firestore import make_test_store, FakeDb

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


SALES = [
    sale("2026-08-03T05:00:00.000Z", 600, [item("ข้าวผัด", 10, 60)]),
    sale("2026-08-04T05:00:00.000Z", 300, [item("ข้าวผัด", 5, 60)]),
    sale("2026-08-04T06:00:00.000Z", 400, [item("ผัดไทย", 5, 80)], source="grab"),
    sale("2026-08-05T05:00:00.000Z", 100, [item("น้ำเปล่า", 10, 10)]),
]

RECIPES = {
    "ข้าวผัด": [{"material_id": "rice", "qty": 0.2}],
    "ผัดไทย": [{"material_id": "noodle", "qty": 0.15}],
}

MATERIALS = [
    {"id": "rice", "name": "ข้าวสาร", "cost": 40, "stock": 20, "par_level": 5},
    {"id": "noodle", "name": "เส้นจันท์", "cost": 60, "stock": 3, "par_level": 10},
]

EXPENSES = [
    {"category": "fixed", "name": "ค่าเช่า", "amount": 5000, "date": "2026-08-01"},
    {"category": "variable", "name": "ค่าไฟ", "amount": 1200, "date": "2026-08-05"},
]

RECEIVINGS = [{"supplier": "ตลาด", "date": "2026-08-02", "total": 800, "items": []}]


def snapshot(**overrides):
    args = {
        "branch": "b1",
        "rollups": list(roll.build_many(SALES, BKK).values()),
        "recipes": RECIPES,
        "materials": MATERIALS,
        "expenses": EXPENSES,
        "receivings": RECEIVINGS,
        "period_from": "2026-08-01",
        "period_to": "2026-08-31",
        "today": "2026-09-15",
    }
    args.update(overrides)
    return assistant.build_snapshot(**args)


def test_every_figure_is_worked_out_before_the_model_sees_it():
    section("Every figure is worked out before the model sees it")

    s = snapshot()

    check("takings", s["sales"]["total"], 1400)
    check("bills", s["sales"]["bill_count"], 4)
    # The point of these three: each is a division the model would
    # otherwise have to do, and would sometimes get wrong.
    check("average per bill is computed, not left to be divided",
          s["sales"]["average_per_bill"], 350)
    check("average per trading day too",
          s["sales"]["average_per_open_day"], round(1400 / 3, 2))
    check("and the margin as a percentage",
          s["profit"]["net_margin_pct"],
          round((1400 - 5000 - 1200 - 800) / 1400 * 100, 1))

    check("the best day is named, not left to be sorted",
          s["sales"]["best_day"]["total"], 700)
    check("with its date", s["sales"]["best_day"]["date"], "2026-08-04")
    check("and the worst", s["sales"]["worst_day"]["total"], 100)

    check("channels carry their share",
          {c["source"]: c["share_pct"] for c in s["channels"]},
          {"loyverse": 71.4, "grab": 28.6})
    check("best sellers carry theirs",
          s["menus"]["top"][0]["share_of_sales_pct"], 64.3)
    check("how many menus sold at all", s["menus"]["distinct_sold"], 3)

    check("net profit uses what was actually bought",
          s["profit"]["net"], 1400 - 5000 - 1200 - 800)
    check("and the recipe figure is reported beside it, not instead",
          s["cost"]["ingredient_cost_by_recipe"], 165.0)
    check("with the difference already taken",
          s["cost"]["purchased_minus_recipe"], round(800 - 165.0, 2))

    check("stock value as it stands", s["stock"]["value_now"], 20 * 40 + 3 * 60)
    check("what is running out is named", s["stock"]["below_par"], ["เส้นจันท์"])

    check("an empty period does not divide by zero",
          snapshot(rollups=[], receivings=[], expenses=[])["sales"]
          ["average_per_bill"], 0.0)


def test_the_numbers_arrive_with_the_reasons_they_might_be_wrong():
    section("The numbers arrive with the reasons they might be wrong")

    kinds = [c["kind"] for c in snapshot()["caveats"]]
    check("a menu with no recipe is called out",
          "uncosted_menus" in kinds, True)
    check("and named, not counted",
          [c["items"] for c in snapshot()["caveats"]
           if c["kind"] == "uncosted_menus"], [["น้ำเปล่า"]])
    check("the recipe-cost caveat rides along with any recipe cost",
          "recipe_cost_at_latest_price" in kinds, True)
    check("a finished period is not flagged as unfinished",
          "includes_today" in kinds, False)

    today = [c["kind"] for c in snapshot(today="2026-08-10")["caveats"]]
    check("a period running up to today is",
          "includes_today" in today, True)

    none_bought = [c["kind"] for c in snapshot(receivings=[])["caveats"]]
    check("selling with nothing bought is called out - profit looks far "
          "better than it is",
          "no_purchases" in none_bought, True)

    no_bills = [c["kind"] for c in snapshot(expenses=[])["caveats"]]
    check("so is a period with no rent or electricity recorded",
          "no_expenses" in no_bills, True)

    owed = snapshot(materials=MATERIALS + [
        {"id": "egg", "name": "ไข่", "cost": 5, "stock": -12}])
    check("stock that has gone negative is called out by name",
          [c["items"] for c in owed["caveats"]
           if c["kind"] == "negative_stock"], [["ไข่"]])

    check("every caveat says what to make of it, not just that it exists",
          all(len(c["message"]) > 20 for c in owed["caveats"]), True)


def test_an_invented_figure_is_named_afterwards():
    section("An invented figure is named afterwards")

    s = snapshot()

    check("an answer built from the data passes clean",
          assistant.verify_numbers("ยอดขาย 1,400 บาท จาก 4 บิล "
                                   "เฉลี่ยบิลละ 350 บาท", s), [])
    check("rounding is not invention",
          assistant.verify_numbers("ยอดขายประมาณ 1,400 บาท", s), [])
    check("a figure that is nowhere in the data is reported",
          assistant.verify_numbers("เดือนนี้ขายได้ 98,000 บาท", s), [98000.0])
    check("even sitting inside a true sentence",
          assistant.verify_numbers("ยอดขาย 1,400 บาท กำไร 77,500 บาท", s),
          [77500.0])

    check("percentages and counts are left alone - they are legitimately "
          "derived and would drown the signal",
          assistant.verify_numbers("โต 12% จาก 4 บิล", s), [])
    check("a date in the answer is not an invented number",
          assistant.verify_numbers("ช่วง 2026-08-01 ถึง 2026-08-31", s), [])
    check("an empty answer reports nothing", assistant.verify_numbers("", s), [])

    check("it reports rather than blocks - the caller still has the answer",
          isinstance(assistant.verify_numbers("กำไร 99,999 บาท", s), list), True)


def test_what_must_never_leave_the_shop():
    section("What must never leave the shop")

    s = snapshot()
    flat = str(s)

    # The snapshot is sent to Google on every question. These are the
    # things that must not be in it, checked by content rather than by
    # field name so that adding them under a new name still fails.
    check("no tenant id", "t1" in flat, False)
    check("no Loyverse token",
          any(k in flat.lower() for k in ("token", "secret", "bearer")), False)
    check("no customer or staff names",
          any(k in s for k in ("customers", "staff", "users", "employees")), False)
    check("no raw receipts - the assistant's questions are about days",
          any(k in s for k in ("sales_rows", "receipts", "bills")), False)

    check("the branch is there, because an answer has to say which shop",
          s["branch"], "b1")
    check("and the period, because an answer has to say when",
          s["period"]["from"], "2026-08-01")


def test_the_port_holds_its_shape():
    section("The port holds its shape")

    class Recording(AssistantProvider):
        name = "recording"

        def __init__(self):
            self.calls = []

        def ask(self, instructions, snapshot, question):
            self.calls.append((instructions, snapshot, question))
            return "ตอบแล้ว"

    provider = Recording()
    answer = provider.ask(assistant.INSTRUCTIONS, snapshot(), "เดือนนี้เป็นไง")
    check("a provider answers in plain text for a person to read",
          answer, "ตอบแล้ว")
    check("and is handed the rules, the data and the question separately",
          len(provider.calls[0]), 3)

    class Broken(AssistantProvider):
        name = "broken"

        def ask(self, instructions, snapshot, question):
            raise AssistantError("ต่อผู้ช่วยไม่ติดตอนนี้")

    try:
        Broken().ask("", {}, "")
        check("a failure is refused, not answered with silence",
              "answered", "raised")
    except AssistantError as e:
        _results.append(True)
        print(f"  [PASS] a failure is refused, not answered with silence: {e}")

    try:
        AssistantProvider()
        check("the port cannot be used without implementing it",
              "instantiated", "refused")
    except TypeError:
        _results.append(True)
        print("  [PASS] the port cannot be used without implementing it")

    check("the rules forbid the arithmetic the snapshot already did",
          "ห้ามคิดเลขเอง" in assistant.INSTRUCTIONS, True)
    check("and require the caveats to be volunteered",
          "caveats" in assistant.INSTRUCTIONS, True)
    check("the model is explicitly read-only",
          "อ่านอย่างเดียว" in assistant.INSTRUCTIONS, True)
    check("and may not claim it changed shop data",
          "ห้ามอ้างว่าได้แก้ไข" in assistant.INSTRUCTIONS, True)


class Answering(AssistantProvider):
    name = "answering"

    def __init__(self, text="ยอดขาย 1,400 บาท ครับ"):
        self.text = text
        self.calls = []

    def ask(self, instructions, snapshot, question):
        self.calls.append((instructions, snapshot, question))
        return self.text


class Refusing(AssistantProvider):
    name = "refusing"

    def ask(self, instructions, snapshot, question):
        raise AssistantError("ผู้ช่วยถูกใช้งานเยอะเกินโควต้าชั่วคราว")


def test_the_difference_between_two_periods_is_already_taken():
    section("The difference between two periods is already taken")

    now = snapshot()
    before = snapshot(rollups=list(roll.build_many(SALES[:1], BKK).values()),
                      receivings=[], expenses=[])

    ctx = assistant.build_context(current=now, previous=before, series=[])
    change = ctx["change"]

    check("the change in takings is subtracted here, not by the model",
          change["sales_baht"], 1400 - 600)
    check("as a percentage too", change["sales_pct"], 133.3)
    check("and named, so a sign cannot be read backwards",
          change["sales_direction"], "up")
    check("bills as well", change["bills"], 3)
    check("and what was bought", change["purchased_baht"], 800)

    check("a first period with nothing before it claims no percentage "
          "rather than being up infinitely",
          assistant.compare_snapshots(now, snapshot(rollups=[], receivings=[],
                                                    expenses=[]))["sales_pct"],
          None)
    check("a period with no previous one at all simply has no change block",
          "change" in assistant.build_context(current=now, previous=None,
                                              series=[]), False)
    check("the days themselves ride along for 'which day was best'",
          assistant.build_context(current=now, previous=None,
                                  series=[{"date": "2026-08-03"}])["series"],
          [{"date": "2026-08-03"}])


def test_an_answer_comes_back_with_what_could_not_be_checked():
    section("An answer comes back with what could not be checked")

    ctx = assistant.build_context(current=snapshot(), previous=None, series=[])

    good = assistant.answer(Answering(), ctx, "เดือนนี้ขายได้เท่าไหร่")
    check("a clean answer is returned", good["ok"], True)
    check("with the text", good["answer"], "ยอดขาย 1,400 บาท ครับ")
    check("and nothing flagged", good["unverified_numbers"], [])
    check("the provider is recorded", good["provider"], "answering")

    made_up = assistant.answer(Answering("เดือนนี้ขายได้ 88,000 บาท"), ctx, "ถามหน่อย")
    check("an invented figure does not block the answer", made_up["ok"], True)
    check("it is handed to the reader instead - only they can decide "
          "whether to act on it",
          made_up["unverified_numbers"], [88000.0])

    check("an empty question is refused before anything is spent",
          assistant.answer(Answering(), ctx, "   ")["error"], "ยังไม่ได้พิมพ์คำถาม")
    long_one = assistant.answer(Answering(), ctx, "ก" * 500)
    check("so is one long enough to hide instructions in", long_one["ok"], False)
    check("and it says why", "ยาวเกินไป" in long_one["error"], True)

    down = assistant.answer(Refusing(), ctx, "เดือนนี้เป็นไง")
    check("a provider that refuses does not raise into the endpoint",
          down["ok"], False)
    check("its message is in Thai and says what to do",
          "โควต้า" in down["error"], True)

    sent = Answering()
    assistant.answer(sent, ctx, "เดือนนี้เป็นไง")
    check("the rules, the data and the question go separately",
          sent.calls[0][2], "เดือนนี้เป็นไง")
    check("and the data is the context, not the raw bills",
          sorted(sent.calls[0][1]), ["period", "series"])

    followup = Answering("ยอดขาย 1,400 บาท ครับ")
    continued = assistant.answer(
        followup, ctx, "แล้วเมนูนั้นล่ะ",
        previous_questions=["เมนูไหนขายดีที่สุด", "กำไร 88,000 บาทจริงไหม"])
    check("a follow-up carries only previous questions",
          followup.calls[0][1]["conversation"]["previous_questions_only"],
          ["เมนูไหนขายดีที่สุด", "กำไร 88,000 บาทจริงไหม"])
    check("history is a copy and does not contaminate the shop facts",
          "conversation" in ctx, False)
    check("a number typed in history is not authorised as a shop fact",
          assistant.answer(Answering("กำไร 88,000 บาท"), ctx, "จริงไหม",
                           previous_questions=["กำไร 88,000 บาทจริงไหม"])
          ["unverified_numbers"], [88000.0])
    check("the continued answer still succeeds", continued["ok"], True)


def test_a_business_cannot_ask_forever():
    section("A business cannot ask forever")

    store = make_test_store(db=FakeDb())
    check("a business that has not asked today is at zero",
          store.assistant_asks_today("2026-08-27"), 0)

    for _ in range(3):
        store.record_assistant_ask("2026-08-27")
    check("asking is counted", store.assistant_asks_today("2026-08-27"), 3)
    check("yesterday's count is not today's",
          store.assistant_asks_today("2026-08-26"), 0)

    other = make_test_store(tenant_id="t2", db=store.db)
    check("one business asking does not spend another's allowance",
          other.assistant_asks_today("2026-08-27"), 0)
    other.record_assistant_ask("2026-08-27")
    check("and the two are counted apart",
          [store.assistant_asks_today("2026-08-27"),
           other.assistant_asks_today("2026-08-27")], [3, 1])


# A shop with a long menu, which is where what-travels starts to matter.
LONG_SALES = [
    sale("2026-08-03T05:00:00.000Z", 76500,
         [item(f"เมนู{i:02d}", 40 - i, 100) for i in range(30)]),
]
LONG_RECIPES = {f"เมนู{i:02d}": [{"material_id": "rice", "qty": 0.1}]
                for i in range(30)}


def long_menu_snapshot():
    return assistant.build_snapshot(
        branch="b1", rollups=list(roll.build_many(LONG_SALES, BKK).values()),
        recipes=LONG_RECIPES, materials=MATERIALS, expenses=[], receivings=[],
        period_from="2026-08-01", period_to="2026-08-31", today="2026-09-15")


def test_the_whole_menu_is_known_but_only_part_of_it_travels():
    section("The whole menu is known, but only part of it travels")

    full = long_menu_snapshot()
    check("the shop's full menu is worked out", len(full["menus"]["performance"]), 30)

    ctx = assistant.build_context(current=full, previous=None, series=[],
                                  question="เดือนนี้เป็นไง")
    shown = ctx["period"]["menus"]
    check("but the model is shown the cap",
          len(shown["performance"]), assistant.MODEL_MENU_LIMIT)
    check("and told how many it is not seeing",
          shown["performance_omitted"], 30 - assistant.MODEL_MENU_LIMIT)
    check("in words, so it cannot report the cap as the whole menu",
          "ห้ามสรุปว่านี่คือเมนูทั้งหมด" in shown["performance_note"], True)
    check("the snapshot itself is not damaged - it is copied, not edited",
          len(full["menus"]["performance"]), 30)

    # เมนู29 is the worst seller. Capping must not make it unaskable, which
    # was the whole reason the full list was being sent.
    asked = assistant.build_context(current=full, previous=None, series=[],
                                    question="ควรเลิกขายเมนู29ไหม")
    names = [row["name"] for row in asked["period"]["menus"]["performance"]]
    check("a menu the question names travels even from the bottom of the list",
          "เมนู29" in names, True)
    check("without displacing the best sellers",
          names[0], full["menus"]["performance"][0]["name"])
    check("and it is found in the full list, not the shown one",
          assistant.menus_named_in("ควรเลิกขายเมนู29ไหม",
                                   [r["name"] for r in full["menus"]["performance"]]),
          ["เมนู29"])
    check("a longer name wins over the shorter one inside it",
          assistant.menus_named_in("ข้าวผัดหมูราคาเท่าไหร่",
                                   ["ข้าวผัด", "ข้าวผัดหมู"]),
          ["ข้าวผัดหมู", "ข้าวผัด"])

    check("no figure the model is told changed",
          ctx["period"]["sales"], full["sales"])
    check("nor the profit", ctx["period"]["profit"], full["profit"])


def test_capping_narrows_what_counts_as_a_verified_number():
    section("Capping narrows what counts as a verified number")

    full = long_menu_snapshot()
    hidden = full["menus"]["performance"][-1]
    ctx = assistant.build_context(current=full, previous=None, series=[],
                                  question="เดือนนี้เป็นไง")

    check("a menu below the cap is not shown",
          hidden["name"] in [r["name"] for r in ctx["period"]["menus"]["performance"]],
          False)
    # Its revenue was an authorised number purely because the whole list
    # travelled. That is the leak: every extra row is another few numbers
    # an invented figure can land within a percent of.
    check("so its revenue is no longer authorised by simply existing",
          assistant.verify_numbers(f"ยอดขาย {hidden['revenue']:.0f} บาท", ctx),
          [hidden["revenue"]])
    check("while the figures that did travel still pass",
          assistant.verify_numbers(f"ยอดขาย {full['sales']['total']:.0f} บาท", ctx),
          [])

    asked = assistant.build_context(current=full, previous=None, series=[],
                                    question=f"ควรเลิกขาย{hidden['name']}ไหม")
    check("and asking about it authorises it again",
          assistant.verify_numbers(f"ยอดขาย {hidden['revenue']:.0f} บาท", asked), [])


def main():
    print("Assistant - phase 1")
    print("=" * 50)

    test_every_figure_is_worked_out_before_the_model_sees_it()
    test_the_numbers_arrive_with_the_reasons_they_might_be_wrong()
    test_an_invented_figure_is_named_afterwards()
    test_what_must_never_leave_the_shop()
    test_the_port_holds_its_shape()
    test_the_difference_between_two_periods_is_already_taken()
    test_an_answer_comes_back_with_what_could_not_be_checked()
    test_a_business_cannot_ask_forever()
    test_the_whole_menu_is_known_but_only_part_of_it_travels()
    test_capping_narrows_what_counts_as_a_verified_number()

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
