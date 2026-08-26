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


def normalize_date(value) -> str:
    """A delivery date, as YYYY-MM-DD.

    One shape, because this field is queried as a range and Firestore
    compares strings: "2026-08-15" and "2026-08-15T00:00:00Z" are the
    same day that sort as different text, and a delivery written in the
    second form silently falls out of every month that should contain
    it - taking its cost with it, which makes profit look better than it
    was. The date input sends the short form and the invoice scanner is
    asked for it, so this mostly guards the odd one that slips through.

    Anything unrecognisable is passed along untouched rather than
    dropped: a date we cannot read is still the shop's data, and losing
    it is worse than sorting it oddly.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        head = text[:10]
        try:
            y, m, d = int(head[:4]), int(head[5:7]), int(head[8:10])
        except ValueError:
            return text
        if 1 <= m <= 12 and 1 <= d <= 31 and y > 1900:
            return head
    return text


class ReceivingError(ValueError):
    """Message is meant to be shown to the person who typed it."""


def clean_receiving(supplier, date, items, note="") -> dict:
    date = normalize_date(date)
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
