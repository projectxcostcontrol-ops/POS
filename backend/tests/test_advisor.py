"""Offline checks for deterministic, read-only restaurant advice."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import advisor
from tests.fake_firestore import make_test_store, FakeDb


def snapshot(**changes):
    data = {
        "sales": {"total": 10000},
        "cost": {"ingredient_cost_by_recipe": 3000, "purchased_actual": 5000,
                 "purchased_minus_recipe": 2000, "gross_margin_pct": 70},
        "expenses": {"fixed": 1000, "variable": 800, "total": 6800},
        "profit": {"net": 3200},
        "menus": {"performance": [
            {"name": "ข้าวผัด", "qty": 20, "revenue": 1200,
             "average_price": 60, "share_of_sales_pct": 12,
             "costed": True, "unit_cost": 35, "ingredient_cost": 700,
             "gross_profit": 500, "gross_margin_pct": 41.7},
            {"name": "ชา", "qty": 10, "revenue": 300,
             "average_price": 30, "share_of_sales_pct": 3,
             "costed": False, "unit_cost": None, "ingredient_cost": None,
             "gross_profit": None, "gross_margin_pct": None},
        ]},
        "caveats": [],
    }
    data.update(changes)
    return data


def test_prioritises_missing_cost_data_and_names_it():
    current = snapshot(caveats=[{
        "kind": "uncosted_menus", "items": ["น้ำเปล่า", "ข้าวไข่เจียว"]}])
    rows = advisor.build_recommendations(current)
    assert rows[0]["id"] == "complete_recipes"
    assert "น้ำเปล่า" in rows[0]["evidence"]
    assert rows[0]["observed_baht"] is None


def test_purchase_gap_is_observation_not_promised_saving():
    row = next(r for r in advisor.build_recommendations(snapshot())
               if r["id"] == "review_purchase_gap")
    assert row["observed_baht"] == 2000
    assert "ไม่ใช่ยอดประหยัดที่รับประกัน" in row["limitation"]


def test_every_action_is_navigation_to_an_allowlisted_page():
    current = snapshot(caveats=[
        {"kind": "uncosted_menus", "items": ["A"]},
        {"kind": "negative_stock", "items": ["ไข่"]},
    ])
    rows = advisor.build_recommendations(current, limit=20)
    assert rows
    for row in rows:
        assert row["action"]["type"] == "navigate"
        key = row["action"]["route_key"]
        assert row["action"]["path"] == advisor.READ_ONLY_ROUTES[key]


def test_previous_period_increase_is_precomputed():
    previous = snapshot(expenses={"fixed": 1000, "variable": 300, "total": 4300})
    row = next(r for r in advisor.build_recommendations(snapshot(), previous, 20)
               if r["id"] == "variable_expense_increase")
    assert row["observed_baht"] == 500
    assert "500 บาท" in row["evidence"]


def test_limit_and_empty_data():
    assert len(advisor.build_recommendations(snapshot(), limit=2)) == 2
    assert advisor.build_recommendations(snapshot(
        sales={"total": 0},
        cost={"ingredient_cost_by_recipe": 0, "purchased_actual": 0,
              "purchased_minus_recipe": 0, "gross_margin_pct": 0},
        expenses={"fixed": 0, "variable": 0, "total": 0},
        menus={"performance": []}), limit=3) == []


def test_deep_analysis_compares_menu_without_inventing_missing_margin():
    old = snapshot(menus={"performance": [
        {"name": "ข้าวผัด", "qty": 15, "revenue": 900, "costed": True,
         "gross_margin_pct": 45, "gross_profit": 405},
    ]})
    result = advisor.build_deep_analysis(snapshot(), old)
    rice = result["menus"]["lowest_margin"][0]
    assert rice["revenue_change_baht"] == 300
    assert rice["qty_change"] == 5
    assert rice["margin_change_points"] == -3.3
    assert result["menus"]["uncosted"] == ["ชา"]
    assert result["read_only"] is True


def test_deep_analysis_flags_purchase_growth_without_sales_growth():
    old = snapshot(
        sales={"total": 11000},
        cost={"ingredient_cost_by_recipe": 2800, "purchased_actual": 4000,
              "purchased_minus_recipe": 1200, "gross_margin_pct": 74.5})
    result = advisor.build_deep_analysis(snapshot(), old)
    kinds = [signal["kind"] for signal in result["signals"]]
    assert "purchases_up_sales_not_up" in kinds
    assert result["period_changes"]["sales_baht"]["change"] == -1000


def test_tracking_measures_before_after_without_claiming_causation():
    recommendation = next(r for r in advisor.build_recommendations(snapshot(), limit=20)
                          if r["id"] == "review_menu_margin")
    current = snapshot()
    current["period"] = {"from": "2026-08-01", "to": "2026-08-07", "days": 7}
    baseline = advisor.tracking_baseline(current, recommendation)
    after = snapshot(
        sales={"total": 11500},
        profit={"net": 3900},
        menus={"performance": [{
            "name": "ข้าวผัด", "qty": 22, "revenue": 1430, "costed": True,
            "unit_cost": 34, "ingredient_cost": 748,
            "gross_profit": 682, "gross_margin_pct": 47.7,
        }]})
    result = advisor.measure_outcome(baseline, after)
    assert result["metrics"]["sales_baht"]["change"] == 1500
    assert result["subject_menu"]["gross_profit_change"] == 182
    assert result["subject_menu"]["margin_change_points"] == 6
    assert "ไม่ได้ยืนยัน" in result["interpretation"]


def test_tracking_storage_is_tenant_and_branch_scoped():
    db = FakeDb()
    first = make_test_store(tenant_id="t1", db=db)
    second = make_test_store(tenant_id="t2", db=db)
    saved = first.add_advice_tracking("b1", {"status": "planned", "created_at": "1"})
    assert first.get_advice_tracking("b1", saved["id"])["status"] == "planned"
    assert first.list_advice_tracking("b2") == []
    assert second.list_advice_tracking("b1") == []
    first.update_advice_tracking("b1", saved["id"], {
        "status": "in_progress", "created_by": "must-not-change"})
    updated = first.get_advice_tracking("b1", saved["id"])
    assert updated["status"] == "in_progress"
    assert "created_by" not in updated


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  [PASS] {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
