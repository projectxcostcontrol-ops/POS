from __future__ import annotations

"""
Normalizes unit spellings (Thai, English, invoice shorthand) and converts
between units WITHIN THE SAME FAMILY only.

Why not convert everything: "ขวด" (bottle) could be 500ml or 1.5L
depending on the product - there's no universal factor, so guessing
would silently corrupt stock. Weight and volume have fixed, universal
factors (1 kg is always 1000 g), so those convert automatically. Count
units (bottle, box, piece) only "convert" when they're literally the
same unit spelled differently (e.g. "EA" and "ชิ้น" both mean piece).
"""

WEIGHT = "weight"
VOLUME = "volume"
COUNT = "count"

# canonical unit -> (family, factor relative to the family's base unit)
# base units: gram for weight, millilitre for volume, "1" for count
UNITS = {
    "g": (WEIGHT, 1),
    "hg": (WEIGHT, 100),
    "kg": (WEIGHT, 1000),
    "ml": (VOLUME, 1),
    "l": (VOLUME, 1000),
    "piece": (COUNT, 1),
    "dozen": (COUNT, 12),
    "bottle": (COUNT, 1),
    "box": (COUNT, 1),
}

# raw spelling (lowercased, whitespace-stripped) -> canonical unit
ALIASES = {
    "g": "g", "gram": "g", "grams": "g", "กรัม": "g", "ก.": "g",
    "hg": "hg", "ขีด": "hg",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg", "กก.": "kg", "กก": "kg", "กิโล": "kg", "กิโลกรัม": "kg",
    "ml": "ml", "milliliter": "ml", "มล.": "ml", "มล": "ml", "ซีซี": "ml", "cc": "ml",
    "l": "l", "liter": "l", "litre": "l", "ลิตร": "l",
    "ea": "piece", "each": "piece", "pc": "piece", "pcs": "piece", "piece": "piece",
    "piece(s)": "piece", "ชิ้น": "piece", "อัน": "piece",
    "dozen": "dozen", "โหล": "dozen",
    "bottle": "bottle", "bottles": "bottle", "btl": "bottle", "ขวด": "bottle",
    "box": "box", "boxes": "box", "กล่อง": "box", "ลัง": "box",
}


def normalize_unit(raw: str | None) -> str | None:
    """Returns the canonical unit key, or None if the spelling isn't
    recognized at all (still shown to the user as-is, just can't be
    reasoned about)."""
    if not raw:
        return None
    key = raw.strip().lower().rstrip(".")
    return ALIASES.get(key) or ALIASES.get(raw.strip())


def same_family(unit_a: str | None, unit_b: str | None) -> bool:
    a, b = UNITS.get(unit_a), UNITS.get(unit_b)
    return bool(a and b and a[0] == b[0])


def convert_quantity(qty: float, from_unit_raw: str, to_unit_raw: str) -> dict:
    """Attempts to convert `qty` from from_unit_raw to to_unit_raw.

    Returns one of:
      {"status": "same", "qty": qty}
        - units are already the same (after normalizing spelling)
      {"status": "converted", "qty": new_qty, "factor": f}
        - safely converted (weight<->weight or volume<->volume)
      {"status": "unconvertible", "reason": "..."}
        - different families, or a count-unit that isn't an exact spelling
          match (e.g. "bottle" -> "box") - needs a human to resolve
      {"status": "unrecognized", "reason": "..."}
        - one or both spellings aren't in our unit dictionary at all
    """
    from_unit = normalize_unit(from_unit_raw)
    to_unit = normalize_unit(to_unit_raw)

    if not from_unit or not to_unit:
        unknown = from_unit_raw if not from_unit else to_unit_raw
        return {"status": "unrecognized", "reason": f"ไม่รู้จักหน่วย '{unknown}'"}

    if from_unit == to_unit:
        return {"status": "same", "qty": qty}

    if not same_family(from_unit, to_unit):
        return {"status": "unconvertible",
                "reason": f"'{from_unit_raw}' กับ '{to_unit_raw}' เป็นคนละประเภทหน่วย แปลงอัตโนมัติไม่ได้"}

    family = UNITS[from_unit][0]
    if family == COUNT:
        if {from_unit, to_unit}.issubset({"piece", "dozen"}):
            factor = UNITS[from_unit][1] / UNITS[to_unit][1]
            return {"status": "converted", "qty": qty * factor, "factor": factor}
        # same family but different spelling of a count unit (e.g. bottle
        # vs box) - these aren't numerically convertible, only identity is
        return {"status": "unconvertible",
                "reason": f"'{from_unit_raw}' กับ '{to_unit_raw}' นับหน่วยคนละแบบ ไม่รู้อัตราแปลงที่แน่นอน"}

    factor = UNITS[from_unit][1] / UNITS[to_unit][1]
    return {"status": "converted", "qty": qty * factor, "factor": factor}


def apply_unit_conversion(item: dict, target_unit: str) -> dict:
    """Given a scanned item {qty, unit, price, ...} and the material's
    actual stored unit, returns the item with a `unit_conversion` field
    describing what happened - and, when a safe conversion was possible,
    with qty/price already adjusted so the review screen shows numbers
    the user can use directly (still editable if wrong)."""
    item = dict(item)
    result = convert_quantity(item.get("qty", 0), item.get("unit", ""), target_unit)

    if result["status"] == "same":
        item["unit_conversion"] = {"status": "same"}
    elif result["status"] == "converted":
        factor = result["factor"]
        item["unit_conversion"] = {
            "status": "converted", "factor": factor,
            "original_qty": item.get("qty"), "original_unit": item.get("unit"),
            "target_unit": target_unit,
        }
        item["qty"] = result["qty"]
        if item.get("price") is not None:
            # price was per original unit; keep it per target unit
            item["price"] = item["price"] / factor
        item["unit"] = target_unit
    else:
        item["unit_conversion"] = {
            "status": result["status"], "reason": result["reason"], "target_unit": target_unit,
        }
    return item


def apply_material_conversion(item: dict, material: dict) -> dict:
    """Apply a product-specific purchase-unit conversion when configured."""
    target = str(material.get("unit") or "").strip()
    purchase = str(material.get("purchase_unit") or target).strip()
    source = str(item.get("unit") or "").strip()
    source_norm = normalize_unit(source)
    target_norm = normalize_unit(target)
    purchase_norm = normalize_unit(purchase)

    already_stock_unit = source == target or bool(source_norm and source_norm == target_norm)
    is_purchase_unit = source == purchase or bool(source_norm and source_norm == purchase_norm)
    factor = float(material.get("purchase_to_stock") or 1)

    remembered = next((row for row in material.get("purchase_conversions") or []
                       if str(row.get("label") or "").strip().lower() == source.lower()), None)
    if not already_stock_unit and remembered:
        factor = float(remembered.get("factor") or 0)
        if factor > 0:
            converted = dict(item)
            converted["unit_conversion"] = {
                "status": "converted", "factor": factor, "source": "remembered",
                "original_qty": item.get("qty"), "original_unit": item.get("unit"),
                "target_unit": target,
            }
            converted["qty"] = (item.get("qty") or 0) * factor
            if item.get("price") is not None:
                converted["price"] = item["price"] / factor
            converted["unit"] = target
            return converted

    if not already_stock_unit and is_purchase_unit and factor > 0:
        converted = dict(item)
        converted["unit_conversion"] = {
            "status": "converted", "factor": factor, "source": "material",
            "original_qty": item.get("qty"), "original_unit": item.get("unit"),
            "target_unit": target,
        }
        converted["qty"] = (item.get("qty") or 0) * factor
        if item.get("price") is not None:
            converted["price"] = item["price"] / factor
        converted["unit"] = target
        return converted

    return apply_unit_conversion(item, target)
