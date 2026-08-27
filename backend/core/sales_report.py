from __future__ import annotations

"""
Turns saved sales into the figures the home screen and sales page show.

Reads our own copy of what sold (see Store.list_sales), never the POS
directly. That's the whole point of keeping the copy: reports work on
history the POS has already dropped, and they don't slow down or fail
because an external API is having a bad day.

On gross profit: it's sales minus the cost of ingredients the recipes say
were consumed. Menu items with no recipe contribute revenue but no cost,
which makes profit look better than it is - so every profit figure comes
back with the list of menus that weren't costed. A number that's quietly
incomplete is worse than one that says so.
"""

from datetime import datetime, timedelta, timezone


def _fmt(dt: datetime) -> str:
    """The one timestamp format used across storage and queries."""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _parse(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def summarise(sales: list[dict], recipes: dict, materials: list[dict],
              granularity: str = "day", tz_offset_minutes: int = 0) -> dict:
    """One summary over a set of sales.

    `recipes` maps menu name -> [{material_id, qty}]
    `materials` is the branch's material list, for unit costs.
    """
    cost_by_material = {m["id"]: (m.get("cost") or 0) for m in materials}

    total = 0.0
    ingredient_cost = 0.0
    uncosted = set()
    buckets: dict[str, float] = {}

    refunds = 0
    for sale in sales:
        # Refund totals were already negated when the receipt was read, so
        # they subtract here without any special case - a refunded sale
        # lowers takings the way it lowers the till.
        total += sale.get("total") or 0
        if sale.get("is_refund"):
            refunds += 1
        key = _bucket_key(sale.get("date") or "", granularity, tz_offset_minutes)
        if key:
            buckets[key] = buckets.get(key, 0) + (sale.get("total") or 0)

        for item in sale.get("items", []):
            name = item.get("name") or ""
            recipe = recipes.get(name) or []
            if not recipe:
                if name:
                    uncosted.add(name)
                continue
            for ing in recipe:
                ingredient_cost += (ing.get("qty") or 0) * (item.get("qty") or 0) \
                    * cost_by_material.get(ing.get("material_id"), 0)

    points = [{"t": k, "sales": round(v, 2)} for k, v in sorted(buckets.items())]

    return {
        "total": round(total, 2),
        # Refunds aren't sales, so they don't inflate the bill count - but
        # they're reported, because "12 bills" hiding 3 refunds behind it
        # is a different day from 12 clean sales.
        "bill_count": len(sales) - refunds,
        "refund_count": refunds,
        "gross_profit": round(total - ingredient_cost, 2),
        "ingredient_cost": round(ingredient_cost, 2),
        # Named, not just counted: "3 menus weren't costed" leaves the
        # reader unable to act, while naming them points at the fix.
        "uncosted_menus": sorted(uncosted),
        "points": points,
    }


def _bucket_key(iso: str, granularity: str, tz_offset_minutes: int = 0) -> str | None:
    """Group by the shop's local clock, not UTC.

    Timestamps are stored in UTC, which is right for storage and wrong for
    a chart: in Thailand (UTC+7) an evening sale falls into the next UTC
    day, so "today" would start at 7am and the busiest hours would land
    on the wrong bar. The caller passes its own offset rather than the
    server assuming one, since the server has no idea where the shop is."""
    dt = _parse(iso)
    if dt is None:
        return None
    # Normalize to UTC before shifting. _parse keeps whatever offset the
    # string carried, and strftime on an aware datetime prints THAT
    # offset's wall time - so adding the shop's offset to a "+07:00"
    # timestamp would apply the shift twice.
    local = dt.astimezone(timezone.utc) + timedelta(minutes=tz_offset_minutes)
    if granularity == "hour":
        return local.strftime("%Y-%m-%dT%H:00")
    return local.strftime("%Y-%m-%d")


def top_items(sales: list[dict], limit: int = 5) -> list[dict]:
    """Best sellers by quantity sold.

    Quantity, not revenue, because the question this answers is "what does
    the kitchen make most of" - which drives prep and purchasing. Revenue
    ranking is a different question and comes back in the same rows for
    whoever wants it."""
    tally: dict[str, dict] = {}
    for sale in sales:
        for item in sale.get("items", []):
            name = item.get("name") or ""
            if not name:
                continue
            row = tally.setdefault(name, {"name": name, "qty": 0, "revenue": 0.0})
            qty = item.get("qty") or 0
            row["qty"] += qty
            row["revenue"] += qty * (item.get("price") or 0)

    rows = sorted(tally.values(), key=lambda r: r["qty"], reverse=True)
    for r in rows:
        r["revenue"] = round(r["revenue"], 2)
    return rows[:limit] if limit else rows


def daily_breakdown(sales: list[dict]) -> list[dict]:
    """Per-day totals, newest first - what the sales page lists."""
    days: dict[str, dict] = {}
    for sale in sales:
        dt = _parse(sale.get("date") or "")
        if dt is None:
            continue
        key = dt.strftime("%Y-%m-%d")
        row = days.setdefault(key, {"date": key, "total": 0.0, "bill_count": 0})
        row["total"] += sale.get("total") or 0
        row["bill_count"] += 1

    rows = sorted(days.values(), key=lambda r: r["date"], reverse=True)
    for r in rows:
        r["total"] = round(r["total"], 2)
    return rows


def compare_previous(current: dict, previous: dict,
                     basis: str = "previous_span") -> dict | None:
    """Percentage change against whatever comparison_window picked.

    A bare "฿4,250" can't tell anyone whether today went well. Returns
    None when there's nothing to compare against, so the caller shows
    nothing rather than a made-up 0% or a misleading +100%.

    `basis` travels with the number so the screen can name the thing it
    is measured against - see comparison_window."""
    prev_total = previous.get("total") or 0
    if prev_total <= 0:
        return None
    change = (current.get("total", 0) - prev_total) / prev_total * 100
    return {"pct": round(abs(change), 1), "up": change >= 0, "basis": basis}


def previous_window(start: str, end: str) -> tuple[str, str]:
    """The equally-long window immediately before this one."""
    s, e = _parse(start), _parse(end)
    if s is None or e is None:
        return start, end
    span = e - s
    # Same canonical format as everything else - these bounds are compared
    # as strings against saved sale dates.
    return _fmt(s - span), _fmt(s)


def same_hours_previous_day(start: str, end: str) -> tuple[str, str]:
    """The same hours of the clock, one day earlier."""
    s, e = _parse(start), _parse(end)
    if s is None or e is None:
        return start, end
    day = timedelta(days=1)
    return _fmt(s - day), _fmt(e - day)


def comparison_window(start: str, end: str) -> tuple[str, str, str]:
    """Which window this one should be measured against, and why.

    For anything a day or longer, the equally-long span immediately
    before: this month against the month before it.

    For anything shorter - which in practice means "today so far" - the
    SAME HOURS of yesterday. The obvious rule, an equally-long span
    immediately before, quietly compares the wrong thing here: at nine in
    the morning it measures nine hours of breakfast against the nine
    hours ending at midnight, which for a restaurant is the dinner rush.
    A shop doing perfectly normal trade would open the app every single
    morning to a large red number, forever, and the figure was arithmetic
    that could not be argued with - which is the worst kind of wrong,
    because there is nothing to notice.

    The basis comes back with the bounds so the screen can say which
    comparison it is showing. A percentage that does not say what it is
    measured against is a percentage the reader has to guess at.
    """
    s, e = _parse(start), _parse(end)
    if s is None or e is None:
        return start, end, "previous_span"
    if e - s <= timedelta(days=1):
        return (*same_hours_previous_day(start, end), "same_hours_yesterday")
    return (*previous_window(start, end), "previous_span")


def build_alerts(materials: list[dict], pending_drafts: int,
                 last_count_at: str | None, now: datetime | None = None,
                 count_reminder_days: int = 7) -> dict:
    """What the home screen's แจ้งเตือน section shows.

    Only things someone can act on today. Anything that's merely
    interesting belongs on a page they chose to open, not in a list whose
    whole value is that it's short enough to read every morning."""
    now = now or datetime.now(timezone.utc)

    low = [
        {"id": m["id"], "name": m.get("name", ""), "unit": m.get("unit", ""),
         "stock": m.get("stock", 0), "par_level": m.get("par_level", 0)}
        for m in materials
        if (m.get("par_level") or 0) > 0 and (m.get("stock") or 0) <= m["par_level"]
    ]
    low.sort(key=lambda m: (m["stock"] / m["par_level"]) if m["par_level"] else 0)

    negative = [
        {"id": m["id"], "name": m.get("name", ""), "unit": m.get("unit", ""),
         "stock": m.get("stock", 0)}
        for m in materials if (m.get("stock") or 0) < 0
    ]

    last = _parse(last_count_at or "")
    days_since = (now - last).days if last else None

    return {
        "low_stock": low,
        "negative_stock": negative,
        "pending_drafts": pending_drafts,
        "days_since_count": days_since,
        # None means "never counted", which needs a different message from
        # "counted a while ago" - one is a setup step, the other a habit
        # that slipped.
        "count_due": days_since is None or days_since >= count_reminder_days,
    }
