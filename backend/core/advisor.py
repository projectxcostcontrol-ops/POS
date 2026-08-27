from __future__ import annotations

"""Deterministic, read-only recommendations for a restaurant owner.

This module receives an already-computed assistant snapshot and returns
navigation advice only.  It deliberately has no Store, database, HTTP, or
model dependency, so adding a recommendation can never become a back door
for changing sales, stock, recipes, or expenses.
"""

READ_ONLY_ROUTES = {
    "recipes": "/recipes",
    "receiving": "/receiving",
    "stock_count": "/stock-count",
    "income_expense": "/income-expense",
}


def build_recommendations(current: dict, previous: dict | None = None,
                          limit: int = 3) -> list[dict]:
    candidates: list[dict] = []
    caveats = {c.get("kind"): c for c in current.get("caveats", [])}
    cost = current.get("cost", {})
    sales = current.get("sales", {})
    expenses = current.get("expenses", {})
    menu_rows = [row for row in current.get("menus", {}).get("performance", [])
                 if row.get("costed") and (row.get("revenue") or 0) > 0]

    uncosted = caveats.get("uncosted_menus", {}).get("items", [])
    if uncosted:
        candidates.append(_recommendation(
            "complete_recipes", 100, "ข้อมูลต้นทุน", "ผูกสูตรเมนูที่ยังไม่ครบก่อน",
            "ยังคำนวณต้นทุนและกำไรของบางเมนูไม่ได้ จึงยังไม่ควรตัดสินใจลดต้นทุนจากตัวเลขรวม",
            f"พบ {len(uncosted)} เมนูที่ยังไม่ได้ผูกสูตร: {_names(uncosted)}",
            "สูง", None, "recipes", "ไปหน้าสูตรอาหาร",
            "ยังระบุผลกระทบเป็นเงินบาทไม่ได้จนกว่าจะผูกสูตรครบ"))

    negative = caveats.get("negative_stock", {}).get("items", [])
    if negative:
        candidates.append(_recommendation(
            "fix_negative_stock", 95, "ความถูกต้องสต๊อก", "ตรวจวัตถุดิบที่สต๊อกติดลบ",
            "สต๊อกติดลบทำให้การเทียบของใช้จริงกับสูตรคลาดเคลื่อน ควรแก้ความถูกต้องก่อนหาจุดสูญเสีย",
            f"พบ {len(negative)} รายการติดลบ: {_names(negative)}",
            "สูง", None, "stock_count", "ไปหน้าเช็กสต๊อก",
            "ต้องนับของจริงและตรวจสูตร จึงจะรู้ว่าเกิดจากการนับ สูตร หรือการสูญเสีย"))

    gap = float(cost.get("purchased_minus_recipe") or 0)
    recipe_cost = float(cost.get("ingredient_cost_by_recipe") or 0)
    purchased = float(cost.get("purchased_actual") or 0)
    if gap > 0 and recipe_cost > 0 and "negative_stock" not in caveats:
        confidence = "ปานกลาง" if "uncosted_menus" in caveats else "สูง"
        candidates.append(_recommendation(
            "review_purchase_gap", 80 + min(gap / 1000, 10), "วัตถุดิบ",
            "ตรวจส่วนต่างยอดซื้อกับต้นทุนตามสูตร",
            "ยอดซื้อวัตถุดิบสูงกว่าต้นทุนที่สูตรคำนวณได้ ควรเริ่มตรวจรายการซื้อและของคงเหลือ",
            f"ซื้อจริง {_baht(purchased)} บาท ต้นทุนตามสูตร {_baht(recipe_cost)} บาท ส่วนต่าง {_baht(gap)} บาท",
            confidence, gap, "receiving", "ไปหน้าซื้อของเข้าร้าน",
            "ส่วนต่างนี้ไม่ใช่ยอดประหยัดที่รับประกัน เพราะอาจเป็นของที่ซื้อเก็บไว้หรือสูตรยังไม่ครบ"))

    total = float(expenses.get("total") or 0)
    variable = float(expenses.get("variable") or 0)
    if variable > 0:
        candidates.append(_recommendation(
            "review_variable_expenses", 55 + min(variable / 2000, 10), "รายจ่าย",
            "ทบทวนรายจ่ายผันแปร",
            "รายจ่ายผันแปรเป็นรายการที่มักปรับได้เร็วกว่าค่าใช้จ่ายคงที่",
            f"รายจ่ายผันแปร {_baht(variable)} บาท จากรายจ่ายรวม {_baht(total)} บาท",
            "ปานกลาง", variable, "income_expense", "ไปหน้ารายรับ–รายจ่าย",
            "ต้องเปิดดูรายการย่อยก่อน จึงจะบอกได้ว่ารายการใดลดได้จริง"))

    margin = float(cost.get("gross_margin_pct") or 0)
    if sales.get("total") and margin < 50:
        candidates.append(_recommendation(
            "review_low_margin", 70, "กำไรขั้นต้น", "ตรวจต้นทุนสูตรและราคาขาย",
            "อัตรากำไรขั้นต้นอยู่ในระดับที่ควรตรวจว่าต้นทุนสูตรหรือราคาขายของเมนูใดเป็นสาเหตุ",
            f"อัตรากำไรขั้นต้นของช่วงที่เลือก {margin:g}%",
            "ปานกลาง", None, "recipes", "ไปหน้าสูตรอาหาร",
            "ข้อมูลสรุปยังไม่แยกกำไรต่อเมนู จึงยังระบุเมนูที่ควรปรับไม่ได้"))

    if previous:
        previous_variable = float(previous.get("expenses", {}).get("variable") or 0)
        increase = round(variable - previous_variable, 2)
        if increase > 0:
            candidates.append(_recommendation(
                "variable_expense_increase", 75 + min(increase / 1000, 10), "รายจ่าย",
                "ดูรายจ่ายผันแปรที่เพิ่มจากช่วงก่อน",
                "การเริ่มจากรายการที่เพิ่มขึ้นช่วยจำกัดวงตรวจสอบได้เร็วกว่าดูค่าใช้จ่ายทั้งหมด",
                f"รายจ่ายผันแปรเพิ่ม {_baht(increase)} บาท จากช่วงก่อน",
                "สูง", increase, "income_expense", "ไปหน้ารายรับ–รายจ่าย",
                "ต้องดูรายการย่อยเพื่อแยกการขึ้นราคาตามปกติออกจากค่าใช้จ่ายที่ควบคุมได้"))

    low_margin = sorted(menu_rows,
                        key=lambda row: (row.get("gross_margin_pct") or 0,
                                         -(row.get("revenue") or 0)))
    if low_margin and (low_margin[0].get("gross_margin_pct") or 0) < 50:
        menu = low_margin[0]
        candidates.append(_recommendation(
            "review_menu_margin", 88, "กำไรต่อเมนู",
            f"ตรวจต้นทุนของ {menu['name']}",
            "เมนูนี้มีอัตรากำไรขั้นต้นต่ำที่สุดในกลุ่มเมนูที่ผูกสูตรแล้ว",
            f"ขาย {_baht(menu['revenue'])} บาท กำไรขั้นต้น {_baht(menu['gross_profit'])} บาท "
            f"หรือ {menu['gross_margin_pct']:g}%",
            "สูง", menu["ingredient_cost"], "recipes", "ไปตรวจสูตรอาหาร",
            "กำไรนี้คำนวณจากสูตรและราคาวัตถุดิบล่าสุด ยังไม่รวมค่าแรง ค่าเช่า และค่าธรรมเนียมช่องทาง",
            subject=menu["name"]))

    candidates.sort(key=lambda item: (-item.pop("_score"), item["id"]))
    return candidates[:max(0, limit)]


def build_deep_analysis(current: dict, previous: dict | None = None) -> dict:
    """Phase-2 analysis: menu economics, period changes, and data signals.

    Every value is copied or subtracted here.  The model is not involved and
    the result contains no operation other than allow-listed navigation.
    """
    menus = current.get("menus", {}).get("performance", [])
    previous_menus = {
        row.get("name"): row for row in (previous or {}).get("menus", {}).get("performance", [])
    }
    compared = []
    for row in menus:
        old = previous_menus.get(row.get("name"), {})
        compared.append({
            **row,
            "revenue_change_baht": _difference(row.get("revenue"), old.get("revenue"))
            if old else None,
            "qty_change": _difference(row.get("qty"), old.get("qty")) if old else None,
            "margin_change_points": _difference(row.get("gross_margin_pct"),
                                                  old.get("gross_margin_pct"))
            if row.get("costed") and old.get("costed") else None,
        })

    costed = [row for row in compared if row.get("costed") and row.get("revenue", 0) > 0]
    lowest_margin = sorted(costed,
                           key=lambda row: (row.get("gross_margin_pct", 0),
                                            -row.get("revenue", 0)))[:5]
    biggest_contributors = sorted(costed,
                                  key=lambda row: row.get("gross_profit", 0),
                                  reverse=True)[:5]
    declines = sorted([row for row in compared
                       if row.get("revenue_change_baht") is not None
                       and row["revenue_change_baht"] < 0],
                      key=lambda row: row["revenue_change_baht"])[:5]

    changes = _period_changes(current, previous) if previous else None
    signals = _signals(current, changes)
    return {
        "period_changes": changes,
        "menus": {
            "lowest_margin": lowest_margin,
            "biggest_gross_profit": biggest_contributors,
            "revenue_declines": declines,
            "uncosted": [row["name"] for row in compared if not row.get("costed")],
        },
        "signals": signals,
        "method": "กำไรต่อเมนูคำนวณจากยอดขาย ลบต้นทุนตามสูตรที่ราคาวัตถุดิบล่าสุด",
        "read_only": True,
    }


TRACKED_METRICS = {
    "sales_baht": ("sales", "total"),
    "ingredient_cost_baht": ("cost", "ingredient_cost_by_recipe"),
    "purchases_baht": ("cost", "purchased_actual"),
    "purchase_recipe_gap_baht": ("cost", "purchased_minus_recipe"),
    "variable_expenses_baht": ("expenses", "variable"),
    "net_profit_baht": ("profit", "net"),
    "gross_margin_pct": ("cost", "gross_margin_pct"),
}


def tracking_baseline(snapshot: dict, recommendation: dict) -> dict:
    """Compact immutable facts saved when a person chooses to track advice."""
    values = {name: round(float(snapshot.get(group, {}).get(field) or 0), 2)
              for name, (group, field) in TRACKED_METRICS.items()}
    subject = recommendation.get("subject")
    subject_menu = next((row for row in snapshot.get("menus", {}).get("performance", [])
                         if row.get("name") == subject), None)
    return {
        "period": dict(snapshot.get("period") or {}),
        "metrics": values,
        "subject_menu": _tracked_menu(subject_menu),
    }


def measure_outcome(baseline: dict, after: dict) -> dict:
    """Before/after facts, never a claim that the recommendation caused them."""
    after_values = {name: round(float(after.get(group, {}).get(field) or 0), 2)
                    for name, (group, field) in TRACKED_METRICS.items()}
    before_values = baseline.get("metrics") or {}
    metrics = {
        name: {
            "before": round(float(before_values.get(name) or 0), 2),
            "after": value,
            "change": _difference(value, before_values.get(name)),
            "change_pct": _change_pct(value, before_values.get(name)),
        }
        for name, value in after_values.items()
    }
    subject_before = baseline.get("subject_menu")
    subject_after = None
    if subject_before:
        subject_after = next((row for row in after.get("menus", {}).get("performance", [])
                              if row.get("name") == subject_before.get("name")), None)
    return {
        "metrics": metrics,
        "subject_menu": _compare_tracked_menu(subject_before, subject_after),
        "interpretation": "เป็นการเปรียบเทียบก่อน–หลัง ไม่ได้ยืนยันว่าความเปลี่ยนแปลงเกิดจากแผนนี้เพียงอย่างเดียว",
        "read_only_analysis": True,
    }


def _period_changes(current: dict, previous: dict) -> dict:
    fields = {
        "sales_baht": (current.get("sales", {}).get("total"),
                       previous.get("sales", {}).get("total")),
        "ingredient_cost_baht": (current.get("cost", {}).get("ingredient_cost_by_recipe"),
                                 previous.get("cost", {}).get("ingredient_cost_by_recipe")),
        "purchases_baht": (current.get("cost", {}).get("purchased_actual"),
                           previous.get("cost", {}).get("purchased_actual")),
        "variable_expenses_baht": (current.get("expenses", {}).get("variable"),
                                   previous.get("expenses", {}).get("variable")),
        "net_profit_baht": (current.get("profit", {}).get("net"),
                            previous.get("profit", {}).get("net")),
    }
    return {name: {"current": round(float(now or 0), 2),
                   "previous": round(float(old or 0), 2),
                   "change": _difference(now, old),
                   "change_pct": _change_pct(now, old)}
            for name, (now, old) in fields.items()}


def _signals(current: dict, changes: dict | None) -> list[dict]:
    out = []
    gap = float(current.get("cost", {}).get("purchased_minus_recipe") or 0)
    if gap > 0:
        out.append({
            "kind": "purchase_recipe_gap", "level": "watch",
            "title": "ยอดซื้อสูงกว่าต้นทุนตามสูตร",
            "detail": f"ส่วนต่าง {_baht(gap)} บาท ต้องแยกของซื้อเก็บออกก่อนตีความว่าเป็นการสูญเสีย",
        })
    if changes:
        purchases = changes["purchases_baht"]
        sales = changes["sales_baht"]
        if purchases["change"] > 0 and sales["change"] <= 0:
            out.append({
                "kind": "purchases_up_sales_not_up", "level": "attention",
                "title": "ยอดซื้อเพิ่ม แต่ยอดขายไม่ได้เพิ่มตาม",
                "detail": f"ยอดซื้อเปลี่ยน {_signed_baht(purchases['change'])} บาท "
                          f"ขณะที่ยอดขายเปลี่ยน {_signed_baht(sales['change'])} บาท",
            })
    for caveat in current.get("caveats", []):
        if caveat.get("kind") in {"uncosted_menus", "negative_stock", "no_purchases"}:
            out.append({
                "kind": caveat["kind"], "level": "data_quality",
                "title": "ข้อมูลยังไม่พร้อมสรุปเต็มที่", "detail": caveat.get("message", ""),
            })
    return out


def _difference(current, previous) -> float:
    return round(float(current or 0) - float(previous or 0), 2)


def _change_pct(current, previous) -> float | None:
    old = float(previous or 0)
    return round((float(current or 0) - old) / old * 100, 1) if old else None


def _signed_baht(value: float) -> str:
    return f"{value:+,.2f}".rstrip("0").rstrip(".")


def _recommendation(item_id: str, score: float, category: str, title: str,
                    reason: str, evidence: str, confidence: str,
                    observed_baht: float | None, route_key: str,
                    action_label: str, limitation: str,
                    subject: str | None = None) -> dict:
    if route_key not in READ_ONLY_ROUTES:
        raise ValueError("recommendations may only use allow-listed read-only navigation")
    return {
        "id": item_id,
        "_score": score,
        "category": category,
        "title": title,
        "reason": reason,
        "evidence": evidence,
        "confidence": confidence,
        "observed_baht": round(observed_baht, 2) if observed_baht is not None else None,
        "action": {"type": "navigate", "route_key": route_key,
                   "path": READ_ONLY_ROUTES[route_key], "label": action_label},
        "limitation": limitation,
        "subject": subject,
    }


def _baht(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _names(items: list, limit: int = 4) -> str:
    names = [str(item) for item in items[:limit]]
    suffix = " และรายการอื่น" if len(items) > limit else ""
    return ", ".join(names) + suffix


def _tracked_menu(row: dict | None) -> dict | None:
    if not row:
        return None
    return {key: row.get(key) for key in (
        "name", "qty", "revenue", "unit_cost", "ingredient_cost",
        "gross_profit", "gross_margin_pct", "costed")}


def _compare_tracked_menu(before: dict | None, after: dict | None) -> dict | None:
    if not before:
        return None
    clean_after = _tracked_menu(after)
    return {
        "name": before.get("name"),
        "before": before,
        "after": clean_after,
        "gross_profit_change": _difference(
            (clean_after or {}).get("gross_profit"), before.get("gross_profit"))
        if clean_after else None,
        "margin_change_points": _difference(
            (clean_after or {}).get("gross_margin_pct"), before.get("gross_margin_pct"))
        if clean_after else None,
    }
