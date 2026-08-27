from __future__ import annotations

"""Turn a free-form shop question into evidence-backed decision support.

The model does not classify the question and then invent an analysis.  This
pure module does both deterministically from the same snapshots used by the
reports.  Gemini receives the result only to explain it naturally in Thai.
"""

from core import advisor
from core import assistant


INTENT_LABELS = {
    "menu_decision": "ตัดสินใจเรื่องเมนู",
    "cost_reduction": "ลดต้นทุน",
    "diagnosis": "หาสาเหตุ",
    "comparison": "เปรียบเทียบช่วงเวลา",
    "anomaly": "ตรวจความผิดปกติ",
    "planning": "วางแผนล่วงหน้า",
    "how_to": "วิธีใช้งานระบบ",
    "summary": "สรุปข้อมูล",
    "general": "คำถามทั่วไปเกี่ยวกับร้าน",
}


def analyze(question: str, current: dict, previous: dict | None = None) -> dict:
    question = (question or "").strip()
    intent = classify(question)
    deep = advisor.build_deep_analysis(current, previous)
    recommendations = advisor.build_recommendations(current, previous, limit=3)

    if intent == "menu_decision":
        result = _menu_decision(question, current, previous)
    elif intent in {"cost_reduction", "diagnosis"}:
        result = _recommendation_answer(intent, recommendations)
    elif intent == "comparison":
        result = _comparison(deep)
    elif intent == "anomaly":
        result = _anomaly(deep)
    elif intent == "planning":
        result = _planning(current)
    elif intent == "how_to":
        result = _how_to(question)
    elif intent == "summary":
        result = _summary(current)
    else:
        result = _general()

    return {
        "intent": intent,
        "intent_label": INTENT_LABELS[intent],
        "response_contract": {
            "order": ["conclusion", "evidence", "missing_data",
                      "confidence", "next_action", "limitation"],
            "instruction": "ตอบข้อสรุปก่อน ใช้เฉพาะหลักฐานที่ให้ และกล่าวเฉพาะข้อมูลขาดที่เกี่ยวข้องกับคำถาม",
        },
        **result,
    }


def classify(question: str) -> str:
    q = (question or "").lower()
    if _has(q, "เลิกขาย", "หยุดขาย", "ตัดออก", "เอาออกจากเมนู", "ควรขายต่อ"):
        return "menu_decision"
    if _has(q, "ลดต้นทุน", "ลดค่าใช้จ่าย", "ประหยัด", "ลด cost"):
        return "cost_reduction"
    if _has(q, "ทำไม", "สาเหตุ", "หายไปไหน", "เพราะอะไร"):
        return "diagnosis"
    if _has(q, "ผิดปกติ", "แปลก", "สูงเกิน", "ต่ำเกิน"):
        return "anomaly"
    if _has(q, "เทียบ", "เมื่อเทียบ", "ดีกว่า", "แย่กว่า", "เพิ่มขึ้น", "ลดลง"):
        return "comparison"
    if _has(q, "เดือนหน้า", "ควรซื้อ", "ต้องซื้อ", "วางแผน", "คาดการณ์", "พยากรณ์"):
        return "planning"
    if _has(q, "ทำยังไง", "อย่างไร", "เพิ่มสูตร", "บันทึก", "อยู่หน้าไหน"):
        return "how_to"
    if _has(q, "เท่าไหร่", "กี่", "เมนูไหน", "สรุป", "เป็นยังไง"):
        return "summary"
    return "general"


def _menu_decision(question: str, current: dict,
                   previous: dict | None) -> dict:
    menus = current.get("menus", {}).get("performance", [])
    menu = _mentioned_menu(question, menus)
    if not menu:
        return _result(
            "insufficient_data", "ยังไม่ทราบว่าเป็นเมนูใด", [],
            ["ชื่อเมนูที่ต้องการประเมิน"], "ต่ำ",
            None, "ระบุชื่อเมนูในคำถาม เช่น ควรเลิกขายข้าวขาหมูไหม")

    evidence = [
        _evidence("จำนวนขาย", menu.get("qty"), "จาน"),
        _evidence("ยอดขาย", menu.get("revenue"), "บาท"),
        _evidence("สัดส่วนยอดขาย", menu.get("share_of_sales_pct"), "%"),
    ]
    old = next((row for row in (previous or {}).get("menus", {}).get("performance", [])
                if row.get("name") == menu.get("name")), None)
    if old:
        evidence.append(_evidence(
            "ยอดขายเทียบช่วงก่อน",
            round(float(menu.get("revenue") or 0) - float(old.get("revenue") or 0), 2),
            "บาท"))

    if not menu.get("costed"):
        return _result(
            "insufficient_data", "ข้อมูลไม่พอตัดสินใจเลิกขาย", evidence,
            ["ต้นทุนต่อจาน", "กำไรขั้นต้นของเมนู",
             "วัตถุดิบเฉพาะเมนูและของเสียที่เกี่ยวข้อง"], "ต่ำ",
            _action("recipes", "/recipes", f"ผูกสูตร{menu['name']}"),
            "ยอดขายน้อยอย่างเดียวไม่บอกว่าเมนูนี้ควรเลิกขาย") | {
                "subject": menu["name"]}

    evidence.extend([
        _evidence("ต้นทุนต่อจาน", menu.get("unit_cost"), "บาท"),
        _evidence("กำไรขั้นต้น", menu.get("gross_profit"), "บาท"),
        _evidence("อัตรากำไรขั้นต้น", menu.get("gross_margin_pct"), "%"),
    ])
    margin = float(menu.get("gross_margin_pct") or 0)
    share = float(menu.get("share_of_sales_pct") or 0)
    if margin < 20 and share < 5:
        code, label = "consider_stop", "ควรทดลองปรับก่อน แล้วจึงพิจารณาหยุดขาย"
    elif share < 5:
        code, label = "experiment_first", "ยังไม่ควรเลิกขายทันที ควรทดลองปรับก่อน"
    else:
        code, label = "keep", "ยังไม่มีเหตุผลพอให้เลิกขาย"
    return _result(
        code, label, evidence,
        ["วัตถุดิบเฉพาะเมนูและของเสียที่เกี่ยวข้อง"], "ปานกลาง",
        _action("tracking", None, "เก็บเป็นแผนติดตาม"),
        "กำไรขั้นต้นยังไม่รวมค่าแรง ค่าเช่า และผลที่เมนูมีต่อยอดขายรายการอื่น") | {
            "subject": menu["name"]}


def _recommendation_answer(intent: str, rows: list[dict]) -> dict:
    if not rows:
        return _result("insufficient_data", "ยังไม่มีข้อมูลพอจัดลำดับ", [],
                       ["ยอดขาย ต้นทุน หรือรายจ่ายในช่วงที่เลือก"], "ต่ำ", None,
                       "ต้องมีข้อมูลในช่วงที่เลือกก่อนจึงจะวิเคราะห์ได้")
    evidence = [_evidence(row["title"], row.get("observed_baht"), "บาท",
                          detail=row["evidence"]) for row in rows]
    label = ("ควรเริ่มจากรายการอันดับแรกก่อน"
             if intent == "cost_reduction" else "พบปัจจัยที่ควรตรวจตามลำดับ")
    return _result("ranked_actions", label, evidence, [], "ปานกลาง",
                   rows[0].get("action"), rows[0].get("limitation", ""))


def _comparison(deep: dict) -> dict:
    changes = deep.get("period_changes") or {}
    sales = changes.get("sales_baht")
    if not sales:
        return _result("insufficient_data", "ไม่มีช่วงก่อนหน้าให้เปรียบเทียบ", [],
                       ["ข้อมูลช่วงก่อนหน้าที่มีความยาวเท่ากัน"], "ต่ำ", None, "")
    direction = "ดีขึ้น" if sales["change"] > 0 else "ลดลง" if sales["change"] < 0 else "ใกล้เคียงเดิม"
    evidence = [_evidence("ยอดขายเปลี่ยน", sales["change"], "บาท")]
    for key, label in (("net_profit_baht", "กำไรสุทธิเปลี่ยน"),
                       ("purchases_baht", "ยอดซื้อเปลี่ยน")):
        if key in changes:
            evidence.append(_evidence(label, changes[key]["change"], "บาท"))
    return _result("comparison", f"ยอดขาย{direction}จากช่วงก่อน", evidence, [],
                   "สูง", None, "ช่วงทั้งสองมีจำนวนวันเท่ากัน แต่ปัจจัยตามฤดูกาลอาจต่างกัน")


def _anomaly(deep: dict) -> dict:
    signals = deep.get("signals") or []
    if not signals:
        return _result("no_signal", "ยังไม่พบสัญญาณผิดปกติจากข้อมูลที่มี", [], [],
                       "ปานกลาง", None, "ระบบตรวจได้เฉพาะสัญญาณที่มีข้อมูลบันทึกไว้")
    return _result("signals_found", "พบรายการที่ควรตรวจ", [
        _evidence(row["title"], None, None, detail=row["detail"]) for row in signals
    ], [], "ปานกลาง", None, "สัญญาณเตือนไม่ใช่หลักฐานยืนยันการสูญเสีย")


def _planning(current: dict) -> dict:
    missing = []
    caveats = {row.get("kind") for row in current.get("caveats", [])}
    if "uncosted_menus" in caveats:
        missing.append("สูตรอาหารให้ครบ")
    missing.extend(["ยอดสต๊อกที่นับล่าสุด", "ของที่สั่งแล้วแต่ยังไม่เข้า",
                    "ยอดขายคาดการณ์ของช่วงที่จะวางแผน"])
    return _result("insufficient_data", "ยังคำนวณแผนซื้อในอนาคตอย่างน่าเชื่อถือไม่ได้",
                   [], missing, "ต่ำ", _action("stock_count", "/stock-count", "เช็กสต๊อกล่าสุด"),
                   "ระบบยังไม่มีแบบจำลองพยากรณ์และข้อมูลของที่กำลังจะเข้า")


def _how_to(question: str) -> dict:
    q = question.lower()
    if "สูตร" in q:
        action = _action("recipes", "/recipes", "ไปหน้าสูตรอาหาร")
    elif "สต๊อก" in q or "นับ" in q:
        action = _action("stock_count", "/stock-count", "ไปหน้าเช็กสต๊อก")
    elif "ซื้อ" in q or "รับของ" in q:
        action = _action("receiving", "/receiving", "ไปหน้าซื้อของเข้าร้าน")
    else:
        action = None
    return _result("navigation", "เปิดหน้าที่เกี่ยวข้องแล้วทำรายการด้วยตัวเอง", [], [],
                   "สูง", action, "ผู้ช่วยไม่มีสิทธิ์บันทึกหรือแก้ไขข้อมูลแทน")


def _summary(current: dict) -> dict:
    return _result("summary", "สรุปจากช่วงที่เลือก", [
        _evidence("ยอดขาย", current.get("sales", {}).get("total"), "บาท"),
        _evidence("จำนวนบิล", current.get("sales", {}).get("bill_count"), "บิล"),
        _evidence("กำไรสุทธิ", current.get("profit", {}).get("net"), "บาท"),
    ], [], "สูง", None, "กำไรอาจไม่ครบถ้ามี caveat ที่เกี่ยวข้อง")


def _general() -> dict:
    return _result("explain_from_data", "ตอบจากข้อมูลที่มีในช่วงที่เลือก", [], [],
                   "ปานกลาง", None, "ถ้าคำถามไม่มีข้อมูลรองรับ ต้องบอกว่าไม่มีข้อมูล")


def _result(code: str, label: str, evidence: list[dict], missing: list[str],
            confidence: str, next_action: dict | None, limitation: str) -> dict:
    return {"conclusion": {"code": code, "label": label},
            "evidence": evidence, "missing_data": missing,
            "confidence": confidence, "next_action": next_action,
            "limitation": limitation}


def _evidence(label: str, value, unit: str | None,
              detail: str | None = None) -> dict:
    return {"label": label, "value": value, "unit": unit, "detail": detail}


def _action(route_key: str, path: str | None, label: str) -> dict:
    return {"type": "navigate" if path else "track", "route_key": route_key,
            "path": path, "label": label}


def _mentioned_menu(question: str, menus: list[dict]) -> dict | None:
    """Matched against the shop's FULL menu list, which is why the list the
    model is shown can be capped without making any dish unaskable."""
    named = assistant.menus_named_in(question, [m.get("name") for m in menus])
    if not named:
        return None
    return next((m for m in menus if m.get("name") == named[0]), None)


def _has(text: str, *terms: str) -> bool:
    return any(term in text for term in terms)
