"""
Sales that did not come from the till.

A shop selling through Grab, LINE MAN or over the phone has orders the
POS never sees. Today they go in a paper notebook: the ingredients leave
the kitchen, nothing records it, and every one of those orders makes the
variance report a little more wrong while looking exactly as confident
as before. A month of that and "ของหาย 8 กิโล" means nothing, because
nobody can tell theft from thirty Grab orders.

So these are recorded as ordinary sales - same collection, same shape,
same reports - and deduct stock through the same recipes. What marks
them is `source`.

Sales synced from the POS carry source "loyverse". Rows written before
this existed carry nothing at all, which is why `source_of` treats an
absent value as the POS rather than as unknown: an old row is a POS row,
and reading it as anything else would quietly drop a year of history out
of the reconcile check.
"""

from __future__ import annotations

POS_SOURCE = "loyverse"

# What the shop picks from. Kept as a fixed list rather than free text:
# these become a filter and a label on every screen, and "grab", "Grab"
# and "แกร็บ" typed on different days are three channels that should
# have been one.
CHANNELS = {
    "grab": "Grab",
    "lineman": "LINE MAN",
    "shopeefood": "ShopeeFood",
    "phone": "โทรสั่ง",
    "online_menu": "เมนูออนไลน์",
    "walk_in": "รับหน้าร้าน",
    "other": "อื่น ๆ",
}


class DeliveryError(ValueError):
    """Message is meant to be shown to the person who typed it."""


def source_of(sale: dict) -> str:
    return sale.get("source") or POS_SOURCE


def is_pos_sale(sale: dict) -> bool:
    """Whether this sale came from the POS.

    Used to keep the reconcile check honest: it compares what the POS
    reports against what we saved, so a sale the POS never had would
    show up as missing forever - and the home screen's "อัปเดตข้อมูล"
    button would try to repair it on every press, every time.
    """
    return source_of(sale) == POS_SOURCE


def clean_order(order_id, source, items, date, note="") -> dict:
    """The shape a recorded order has to have before it touches stock.

    Every field is checked here rather than at the screen, because the
    screen is not the only thing that will call this - the delivery-file
    importer, when it exists, has to produce exactly the same rows or the
    two will drift into two kinds of sale.
    """
    order_id = (order_id or "").strip()
    if not order_id:
        raise DeliveryError("ไม่มีรหัสออเดอร์")
    # It becomes a document id and has to be distinguishable from a POS
    # receipt number, which is what keys the same collection.
    if "/" in order_id or len(order_id) > 200:
        raise DeliveryError("รหัสออเดอร์ไม่ถูกต้อง")

    if source not in CHANNELS or source == POS_SOURCE:
        raise DeliveryError("ช่องทางการขายไม่ถูกต้อง")

    if not (date or "").strip():
        raise DeliveryError("กรุณาใส่วันที่และเวลาที่ขาย")

    clean_items = []
    for raw in items or []:
        name = (raw.get("name") or "").strip()
        if not name:
            raise DeliveryError("มีรายการที่ยังไม่ได้เลือกเมนู")
        try:
            qty = float(raw.get("qty") or 0)
            price = float(raw.get("price") or 0)
        except (TypeError, ValueError):
            raise DeliveryError(f"จำนวนหรือราคาของ {name} ไม่ใช่ตัวเลข")
        if qty <= 0:
            raise DeliveryError(f"จำนวนของ {name} ต้องมากกว่า 0")
        if price < 0:
            raise DeliveryError(f"ราคาของ {name} ติดลบไม่ได้")
        clean_items.append({"name": name, "qty": qty, "price": price})

    if not clean_items:
        raise DeliveryError("ยังไม่ได้เลือกเมนูสักรายการ")

    return {
        "receipt_number": order_id,
        "date": date.strip(),
        "recorded_at": date.strip(),
        "is_refund": False,
        # What the customer actually paid on the platform, not the shop's
        # own menu price. The platform's cut is a separate cost and
        # belongs in รายรับรายจ่าย, where the monthly invoice already goes
        # - recording it per-order would mean guessing a percentage for
        # every line and getting a number that agrees with nothing.
        "total": round(sum(i["qty"] * i["price"] for i in clean_items), 2),
        "items": clean_items,
        "source": source,
        "note": (note or "").strip(),
    }
