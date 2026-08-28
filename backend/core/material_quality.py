from __future__ import annotations

"""Validation for material master data.

The UI catches mistakes early, but these checks also live at the API
boundary so an old client or a direct request cannot save values that make
stock and recipe costs impossible to trust.
"""

ALLOWED_CATEGORIES = {"ingredient", "drink", "packaging", "consumable"}


def validate_material(data: dict) -> dict:
    cleaned = dict(data)
    name = str(cleaned.get("name") or "").strip()
    unit = str(cleaned.get("unit") or "").strip()
    if not name:
        raise ValueError("กรุณาระบุชื่อวัตถุดิบ")
    if not unit:
        raise ValueError("กรุณาระบุหน่วยสต๊อก")

    category = cleaned.get("category") or "ingredient"
    if category not in ALLOWED_CATEGORIES:
        raise ValueError("ประเภทวัตถุดิบไม่ถูกต้อง")

    for key, label in (("cost", "ต้นทุน"), ("par", "จำนวนที่ควรมี")):
        if key in cleaned and cleaned[key] is not None:
            try:
                value = float(cleaned[key])
            except (TypeError, ValueError):
                raise ValueError(f"{label}ต้องเป็นตัวเลข")
            if value < 0:
                raise ValueError(f"{label}ต้องไม่ติดลบ")
            cleaned[key] = value

    purchase_unit = str(cleaned.get("purchase_unit") or unit).strip()
    try:
        raw_conversion = cleaned.get("purchase_to_stock")
        conversion = float(1 if raw_conversion is None or raw_conversion == "" else raw_conversion)
    except (TypeError, ValueError):
        raise ValueError("อัตราแปลงหน่วยต้องเป็นตัวเลข")
    if conversion <= 0:
        raise ValueError("อัตราแปลงหน่วยต้องมากกว่า 0")

    cleaned.update({
        "name": name,
        "unit": unit,
        "category": category,
        "purchase_unit": purchase_unit,
        "purchase_to_stock": conversion,
    })
    conversions = []
    for row in cleaned.get("purchase_conversions") or []:
        label = str(row.get("label") or "").strip()
        if not label:
            raise ValueError("หน่วยซื้อที่จำไว้ต้องมีชื่อ")
        try:
            row_factor = float(row.get("factor"))
        except (TypeError, ValueError):
            raise ValueError("อัตราแปลงหน่วยที่จำไว้ต้องเป็นตัวเลข")
        if row_factor <= 0:
            raise ValueError("อัตราแปลงหน่วยที่จำไว้ต้องมากกว่า 0")
        conversions.append({"label": label, "factor": row_factor})
    cleaned["purchase_conversions"] = conversions
    return cleaned


def validate_recipe(ingredients: list[dict]) -> list[dict]:
    if not ingredients:
        raise ValueError("สูตรต้องมีวัตถุดิบอย่างน้อย 1 รายการ")
    cleaned = []
    seen = set()
    for row in ingredients:
        material_id = str(row.get("material_id") or "").strip()
        if not material_id:
            raise ValueError("สูตรมีรายการที่ยังไม่ได้เลือกวัตถุดิบ")
        if material_id in seen:
            raise ValueError("สูตรมีวัตถุดิบซ้ำ กรุณารวมเป็นรายการเดียว")
        try:
            qty = float(row.get("qty"))
        except (TypeError, ValueError):
            raise ValueError("ปริมาณในสูตรต้องเป็นตัวเลข")
        if qty <= 0:
            raise ValueError("ปริมาณในสูตรต้องมากกว่า 0")
        seen.add(material_id)
        cleaned.append({"material_id": material_id, "qty": qty})
    return cleaned
