"""Deterministic question classification and decision-support checks."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import question_intent


def menu(name="ข้าวขาหมู", costed=False, margin=None):
    return {"name": name, "qty": 21, "revenue": 1260,
            "share_of_sales_pct": 2, "costed": costed,
            "unit_cost": 32 if costed else None,
            "ingredient_cost": 672 if costed else None,
            "gross_profit": 588 if costed else None,
            "gross_margin_pct": margin}


def snapshot(row=None):
    return {
        "sales": {"total": 63000, "bill_count": 800},
        "cost": {"ingredient_cost_by_recipe": 10000,
                 "purchased_actual": 12000, "purchased_minus_recipe": 2000,
                 "gross_margin_pct": 60},
        "expenses": {"variable": 3000, "total": 15000},
        "profit": {"net": 36000},
        "menus": {"performance": [row or menu()]},
        "caveats": [{"kind": "uncosted_menus", "items": ["ข้าวขาหมู"],
                     "message": "ยังไม่ได้ผูกสูตร"}] if not (row or menu()).get("costed") else [],
    }


def test_classifies_common_question_families():
    cases = {
        "ยอดขายข้าวขาหมูน้อย ควรเลิกขายไหม": "menu_decision",
        "ถ้าอยากลดต้นทุนควรเริ่มตรงไหน": "cost_reduction",
        "ทำไมกำไรลด": "diagnosis",
        "เดือนนี้เทียบเดือนก่อนเป็นยังไง": "comparison",
        "ยอดซื้อสูงผิดปกติไหม": "anomaly",
        "เดือนหน้าควรซื้อของเท่าไร": "planning",
        "เพิ่มสูตรอาหารทำยังไง": "how_to",
        "เดือนนี้ขายได้เท่าไหร่": "summary",
    }
    for question, expected in cases.items():
        assert question_intent.classify(question) == expected


def test_uncosted_low_seller_is_not_told_to_stop():
    result = question_intent.analyze(
        "ยอดขายข้าวขาหมูน้อยมาก ควรเลิกขายเมนูนี้ไหม", snapshot())
    assert result["intent"] == "menu_decision"
    assert result["subject"] == "ข้าวขาหมู"
    assert result["conclusion"]["code"] == "insufficient_data"
    assert result["evidence"][0] == {"label": "จำนวนขาย", "value": 21,
                                      "unit": "จาน", "detail": None}
    assert "ต้นทุนต่อจาน" in result["missing_data"]
    assert result["next_action"]["path"] == "/recipes"


def test_costed_menu_gets_a_cautious_decision():
    result = question_intent.analyze(
        "ควรเลิกขายข้าวขาหมูไหม", snapshot(menu(costed=True, margin=46.7)))
    assert result["conclusion"]["code"] == "experiment_first"
    assert result["confidence"] == "ปานกลาง"
    assert any(row["label"] == "กำไรขั้นต้น" for row in result["evidence"])
    assert "ของเสีย" in " ".join(result["missing_data"])


def test_unknown_menu_asks_for_a_name_instead_of_guessing():
    result = question_intent.analyze("ควรเลิกขายเมนูนี้ไหม", snapshot())
    assert result["conclusion"]["code"] == "insufficient_data"
    assert result["missing_data"] == ["ชื่อเมนูที่ต้องการประเมิน"]


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  [PASS] {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
