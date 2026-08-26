"""
What a delivery of ingredients has to look like before it is recorded.

A receiving moves two things at once: stock onto the shelf, and the
price paid into the cost history that every profit figure is built on.
Both are hard to see afterwards - a quantity typed with an extra zero
looks like a good month until someone counts the shelf - so the shapes
that are obviously wrong are refused at the door.

Kept out of the API layer so the rules can be tested without a web
framework, and so recording and correcting a delivery cannot drift
apart: a correction that could save a shape recording would have refused
is a hole in the same wall.
"""

from __future__ import annotations


class ReceivingError(ValueError):
    """Message is meant to be shown to the person who typed it."""


def clean_receiving(supplier, date, items, note="") -> dict:
    date = (date or "").strip()
    if not date:
        raise ReceivingError("กรุณาใส่วันที่รับของ")

    clean_items = []
    for raw in items or []:
        material_id = (raw.get("material_id") or "").strip()
        if not material_id:
            raise ReceivingError("มีรายการที่ยังไม่ได้เลือกวัตถุดิบ")
        try:
            quantity = float(raw.get("quantity") or 0)
            unit_cost = float(raw.get("unit_cost") or 0)
        except (TypeError, ValueError):
            raise ReceivingError("จำนวนหรือราคาต้องเป็นตัวเลข")
        if quantity <= 0:
            raise ReceivingError("จำนวนต้องมากกว่า 0")
        # Zero is allowed: samples and free replacements arrive at no
        # charge and still land on the shelf. Negative is not - a
        # delivery that pays the shop is a return, and returns are their
        # own thing, not a receiving with a minus sign that would drag
        # the material's average cost below what anyone ever paid.
        if unit_cost < 0:
            raise ReceivingError("ราคาต่อหน่วยติดลบไม่ได้")
        clean_items.append({"material_id": material_id, "quantity": quantity,
                            "unit_cost": unit_cost})

    if not clean_items:
        raise ReceivingError("ยังไม่ได้ใส่รายการวัตถุดิบสักรายการ")

    return {
        "supplier": (supplier or "").strip(),
        "date": date,
        "items": clean_items,
        "note": (note or "").strip(),
        "total": round(sum(i["quantity"] * i["unit_cost"] for i in clean_items), 2),
    }
