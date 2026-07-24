from __future__ import annotations

"""
Variance analysis - the gap between what the recipes say should have been
used and what a physical count says actually went.

The ledger already deducts ingredients on every sale, so the running stock
figure IS the "should have" number. A physical count is the only source of
"actually". Their difference is recorded as the delta on the count
movement, which means the arithmetic here needs no new bookkeeping - it
reads what counting already writes.

Two things this deliberately does NOT do:

Attribute causes. A shortfall might be over-portioning, unrecorded waste,
theft, or - most often in practice - a recipe that doesn't match how the
dish is actually cooked. The system can see the gap and cannot see the
reason, so it reports the gap and leaves the reason to someone who was
in the kitchen.

Hide its own blind spots. Sales of menu items with no recipe consume
ingredients that nothing accounted for, so those ingredients show up as
"missing" when they were simply sold unrecorded. That makes the whole
report misleading in a way the numbers themselves can't reveal, so the
caller gets an explicit list of unmeasurable menus to show alongside.
"""

SALE = "sale"
WASTE = "waste"
COUNT = "count"


def analyse_session(ledger, store_id: str, session: dict,
                    previous_session: dict | None,
                    materials: list[dict],
                    threshold_pct: float = 10.0,
                    threshold_value: float = 200.0) -> list[dict]:
    """One row per material that was counted in `session`.

    The period runs from the previous count to this one. Without a previous
    count there's no meaningful window - stock could have moved at any point
    since the system was set up - so usage is reported as unknown rather
    than measured from an arbitrary start.
    """
    window_start = (previous_session or {}).get("closed_at")
    window_end = session.get("closed_at")
    session_id = session.get("id")

    movements = ledger.list_movements(store_id)
    by_material: dict[str, list[dict]] = {}
    for m in movements:
        by_material.setdefault(m.get("material_id"), []).append(m)

    material_by_id = {m["id"]: m for m in materials}
    rows = []

    for material_id, counted in (session.get("entries") or {}).items():
        material = material_by_id.get(material_id)
        if material is None:
            continue   # deleted since the count; nothing sensible to report

        entries = by_material.get(material_id, [])
        delta = _count_delta(entries, session_id)
        if delta is None:
            continue   # this material wasn't actually committed to the ledger

        usage = _sum_in_window(entries, SALE, window_start, window_end)
        waste = _sum_in_window(entries, WASTE, window_start, window_end)
        cost = material.get("cost") or 0

        # usage is the honest denominator: 1kg missing out of 8kg used is a
        # real problem, while 1kg out of a 50kg sack that barely moved is
        # more likely a measuring difference. Expressing it against stock
        # on hand would rank those the other way round.
        pct = (abs(delta) / usage * 100) if usage > 0 else None

        rows.append({
            "material_id": material_id,
            "name": material.get("name", material_id),
            "unit": material.get("unit", ""),
            "counted": counted,
            "variance_qty": round(delta, 4),
            "variance_value": round(delta * cost, 2),
            "expected_usage": round(usage, 4),
            "recorded_waste": round(waste, 4),
            "variance_pct": round(pct, 1) if pct is not None else None,
            "measurable": usage > 0,
            "flagged": _is_flagged(delta, cost, pct, threshold_pct, threshold_value),
        })

    # Biggest money first - that's the order the person reading this can act
    # on, and it keeps cheap high-percentage items from crowding the top.
    rows.sort(key=lambda r: r["variance_value"])
    return rows


def _count_delta(entries: list[dict], session_id: str | None) -> float | None:
    """The correction this count applied. Negative means less was found
    than the recipes predicted."""
    for e in entries:
        if e.get("kind") == COUNT and e.get("ref") == session_id:
            return e.get("quantity", 0)
    return None


def _sum_in_window(entries: list[dict], kind: str,
                   start: str | None, end: str | None) -> float:
    """Total absolute quantity of one movement kind inside the period.

    ISO-8601 timestamps compare correctly as strings when they share a
    format and timezone, which everything written here does."""
    total = 0.0
    for e in entries:
        if e.get("kind") != kind:
            continue
        at = e.get("occurred_at") or ""
        if start and at <= start:
            continue
        if end and at > end:
            continue
        total += abs(e.get("quantity", 0))
    return total


def _is_flagged(delta: float, cost: float, pct: float | None,
                threshold_pct: float, threshold_value: float) -> bool:
    """Both thresholds must be crossed, and only shortfalls are flagged.

    Percentage alone would flag pepper going missing at 30% of 40 baht
    every single week; value alone would flag any expensive ingredient
    whose count was rounded. Requiring both keeps the flagged list short
    enough that someone still reads it - a warning nobody reads protects
    nothing.

    A surplus (more found than expected) is worth showing but not
    flagging: it usually means the recipe overstates what the dish uses,
    which is a recipe to fix rather than a loss to chase."""
    if delta >= 0 or pct is None:
        return False
    return pct >= threshold_pct and abs(delta * cost) >= threshold_value


def summarise(rows: list[dict]) -> dict:
    shortfall_value = sum(r["variance_value"] for r in rows if r["variance_qty"] < 0)
    return {
        "shortfall_value": round(abs(shortfall_value), 2),
        "flagged_count": len([r for r in rows if r["flagged"]]),
        "unmeasurable_count": len([r for r in rows if not r["measurable"]]),
        "counted_count": len(rows),
    }


def unmeasured_menus(sold_item_names: set[str], recipes: dict,
                     skipped: list[str]) -> list[str]:
    """Menus that sold during the period but have no recipe.

    Their ingredients left the kitchen with nothing recording it, so those
    ingredients surface as unexplained losses. Naming them is the
    difference between a report that's incomplete and one that's
    misleading - the numbers look equally confident either way."""
    skip_set = set(skipped)
    return sorted(
        name for name in sold_item_names
        if name not in skip_set and not (recipes.get(name) or [])
    )
