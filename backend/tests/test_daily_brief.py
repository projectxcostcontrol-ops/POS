"""
Tests for the morning brief.

The brief is read at six in the morning by someone who is not checking
it. That single fact decides the design: the template writes the brief
and the model only rephrases it, so every figure is already true before
any model is involved. What is protected here is that ordering, and the
two ways the rephrasing is allowed to fail.

  the model is down      -> the shop gets the plain version
  the model invents      -> the shop gets the plain version
  the model does its job -> a nicer version of exactly the same facts

There is deliberately no fourth case, and no test can be written for a
model behaving well - so what is tested is that a badly behaved one
cannot reach the shop.

The other thing worth protecting is what the brief refuses to do: find
something encouraging to say about a day the shop took nothing, and
report an unfinished today as if it were a day.

Offline, in-memory. Run with:

    cd backend
    python tests/test_daily_brief.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import daily_brief as brief_lib
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


# Four ordinary days at about 600, then a good Sunday with a Grab order.
SALES = [
    sale("2026-09-02T05:00:00.000Z", 600, [item("ข้าวผัด", 10, 60)]),
    sale("2026-09-03T05:00:00.000Z", 620, [item("ข้าวผัด", 10, 62)]),
    sale("2026-09-04T05:00:00.000Z", 580, [item("ข้าวผัด", 10, 58)]),
    sale("2026-09-05T05:00:00.000Z", 600, [item("ข้าวผัด", 10, 60)]),
    sale("2026-09-06T05:00:00.000Z", 900, [item("ข้าวผัด", 12, 60),
                                           item("น้ำเปล่า", 5, 10)]),
    sale("2026-09-06T07:00:00.000Z", 300, [item("ผัดไทย", 4, 75)], source="grab"),
]

RECIPES = {"ข้าวผัด": [{"material_id": "rice", "qty": 0.2}],
           "ผัดไทย": [{"material_id": "noodle", "qty": 0.15}]}

MATERIALS = [
    {"id": "rice", "name": "ข้าวสาร", "cost": 40, "stock": 30, "par_level": 5},
    {"id": "noodle", "name": "เส้นจันท์", "cost": 60, "stock": 2, "par_level": 10},
]


def rollups(sales=None):
    return list(roll.build_many(sales or SALES, BKK).values())


def build(**overrides):
    args = {"day": "2026-09-06", "rollups": rollups(), "recipes": RECIPES,
            "materials": MATERIALS, "days_since_count": 2}
    args.update(overrides)
    return brief_lib.build(**args)


class Rewriter(AssistantProvider):
    """A model that behaves - it rephrases and keeps every figure."""
    name = "rewriter"

    def __init__(self, text=None):
        self.text = text
        self.calls = []

    def ask(self, instructions, snapshot, question):
        self.calls.append(snapshot)
        return self.text or (
            "เมื่อวานขายได้ 1,200 บาท 2 บิล เฉลี่ยบิลละ 600 บาท "
            "ดีกว่าค่าเฉลี่ย 4 วันก่อน 100% ครับ "
            "ต้องสั่งเส้นจันท์ด้วย และน้ำเปล่ายังไม่ได้ผูกสูตร")


class Dead(AssistantProvider):
    name = "dead"

    def ask(self, instructions, snapshot, question):
        raise AssistantError("ต่อผู้ช่วยไม่ติด")


def test_the_template_writes_a_whole_brief_on_its_own():
    section("The template writes a whole brief on its own")

    b = build()
    text = b["text"]

    check("takings", b["sales"]["total"], 1200)
    check("bills - a refund would not be one", b["sales"]["bill_count"], 2)
    check("average per bill is worked out, not left to the reader",
          b["sales"]["average_per_bill"], 600)
    check("the day is named as a person would say it",
          text.startswith("อาทิตย์ 6 ก.ย."), True)
    check("the takings are in the text", "1,200" in text, True)
    check("the delivery order is broken out", "Grab" in text, True)
    check("best sellers are there", "ข้าวผัด" in text, True)
    check("what has to be ordered is there", "เส้นจันท์" in text, True)
    check("and the menu with no recipe", "น้ำเปล่า" in text, True)
    check("no model was asked", b["polish_status"], "not_attempted")
    check("and none was needed - display_text has something to show",
          brief_lib.display_text(b), text)

    check("it stays short enough to read standing up",
          len(text.splitlines()) <= 7, True)


def test_yesterday_is_measured_against_the_days_that_traded():
    section("Yesterday is measured against the days that traded")

    c = build()["compare"]
    check("four ordinary days make the baseline", c["days"], 4)
    check("averaged", c["baseline_average"], 600)
    check("and the good Sunday reads as up", c["up"], True)
    check("by how much", c["pct"], 100)

    # A shop that shuts on Mondays would look like it was collapsing
    # every Tuesday if the closed days were averaged in as zeros.
    with_gaps = rollups() + [roll.empty("2026-09-01"), roll.empty("2026-08-31")]
    check("days the shop was closed are not averaged in as zeros",
          brief_lib.build(day="2026-09-06", rollups=with_gaps, recipes=RECIPES,
                          materials=MATERIALS)["compare"]["baseline_average"],
          600)

    thin = [r for r in rollups() if r["date"] >= "2026-09-05"]
    check("one day of history is not enough to compare against - nothing "
          "is claimed rather than a made-up percentage",
          brief_lib.build(day="2026-09-06", rollups=thin, recipes=RECIPES,
                          materials=MATERIALS)["compare"], None)


def test_a_quiet_day_is_reported_as_a_quiet_day():
    section("A quiet day is reported as a quiet day")

    b = build(day="2026-09-07")
    check("the shop is marked closed", b["closed"], True)
    check("and told so plainly",
          "ไม่มียอดขายบันทึกไว้เลย" in b["text"], True)
    check("with no comparison dressed around it", b["compare"], None)
    check("but what has to be ordered still shows - that is the part "
          "worth knowing on a closed day",
          "เส้นจันท์" in b["text"], True)


def test_what_the_brief_will_not_let_a_model_do():
    section("What the brief will not let a model do")

    b = build()

    invented = brief_lib.polish(
        Rewriter("เมื่อวานขายได้ 8,900 บาท ต้องสั่งเส้นจันท์ และน้ำเปล่ายังไม่ผูกสูตร"), b)
    check("a figure that was never in the data is refused",
          invented["polish_status"], "rejected")
    check("and named", invented["polish_rejected_numbers"], [8900.0])
    check("the shop still gets the brief it would have got",
          brief_lib.display_text(invented) == b["text"], True)

    tidied = brief_lib.polish(Rewriter("เมื่อวานขายได้ 1,200 บาท วันที่ดีมาก"), b)
    check("a rewrite that drops what has to be ordered is refused too",
          tidied["polish_status"], "rejected")
    check("and says what it dropped",
          sorted(tidied["polish_dropped"]), ["low_stock", "uncosted_menus"])
    check("the shop still gets the full version",
          "เส้นจันท์" in brief_lib.display_text(tidied), True)

    down = brief_lib.polish(Dead(), b)
    check("a provider that is down does not take the brief with it",
          down["polish_status"], "failed")
    check("the plain version is shown",
          brief_lib.display_text(down) == b["text"], True)

    good = brief_lib.polish(Rewriter(), b)
    check("a rewrite that keeps the figures and the warnings is kept",
          good["polish_status"], "ok")
    check("and is what gets shown",
          brief_lib.display_text(good) != b["text"], True)
    check("with the provider recorded, so a bad run can be traced",
          good["provider"], "rewriter")

    # Polishing an already-polished brief must not feed the model its own
    # previous attempt, or a small drift compounds every time it runs.
    sent = Rewriter()
    brief_lib.polish(sent, good)
    check("the model is never shown its own previous attempt",
          "polished" in sent.calls[0], False)
    check("nor the status of one", "polish_status" in sent.calls[0], False)
    check("it is shown the figures it must not change",
          sent.calls[0]["sales"]["total"], 1200)


def test_the_brief_is_written_once_and_thrown_away_with_its_day():
    section("The brief is written once and thrown away with its day")

    store = make_test_store(db=FakeDb())
    b = build()
    store.set_brief("b1", "2026-09-06", b)

    check("it can be read back", store.get_brief("b1", "2026-09-06")["sales"],
          b["sales"])
    check("a day never written is absent, not empty",
          store.get_brief("b1", "2026-09-05"), None)

    # A late bill throws the day away. The brief was written from that
    # day, so it has to go with it - otherwise the figures on the screen
    # and the figures in the brief disagree, and the brief is the one
    # nobody re-checks.
    store.delete_daily("b1", "2026-09-06")
    check("throwing the day away throws the brief away too",
          store.get_brief("b1", "2026-09-06"), None)

    other = make_test_store(tenant_id="t2", db=store.db)
    store.set_brief("b1", "2026-09-06", b)
    check("another business cannot read this one's brief",
          other.get_brief("b1", "2026-09-06"), None)


def main():
    print("Daily brief - phase 2")
    print("=" * 50)

    test_the_template_writes_a_whole_brief_on_its_own()
    test_yesterday_is_measured_against_the_days_that_traded()
    test_a_quiet_day_is_reported_as_a_quiet_day()
    test_what_the_brief_will_not_let_a_model_do()
    test_the_brief_is_written_once_and_thrown_away_with_its_day()

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
