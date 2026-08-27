from __future__ import annotations

"""
One document per trading day, so a month costs thirty reads.

A month of sales is three thousand documents and the question asked of
them - "what did we take, what did it cost" - has the same size answer
whether the shop has been open a week or a year. Every screen that shows
a month was paying the full price of the history each time it opened.

WHAT IS STORED IS ONLY WHAT CANNOT CHANGE.

That is the whole trick, and it is why this does not need the "recompute
past profit" button NOTES section 7 expected. A day's takings and the
quantity of each dish sold are facts: they were true when the bill was
rung and they stay true. Ingredient cost is not a fact - it is those
quantities multiplied by whatever the recipe says today and whatever the
ingredients cost today, and both of those get corrected for weeks
afterwards. Storing it would freeze a guess; deriving it at read time
from thirty stored facts costs nothing and is right the moment a recipe
is fixed.

Days are the shop's days. A sale at seven in the evening in Bangkok is
already tomorrow in UTC, so grouping by UTC would put a shop's busiest
hours on the wrong date - and unlike a chart, which is at least visibly
odd, a daily total that is silently shifted looks exactly like a real
number.
"""

from datetime import datetime, timedelta, timezone


def local_day(iso: str, tz_offset_minutes: int) -> str | None:
    """The shop's calendar date for a stored (UTC) timestamp."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Normalised to UTC first: the stored string may carry its own offset,
    # and shifting a "+07:00" timestamp by the shop's offset would apply
    # the same seven hours twice.
    return (dt.astimezone(timezone.utc)
            + timedelta(minutes=tz_offset_minutes)).strftime("%Y-%m-%d")


def day_bounds(day: str, tz_offset_minutes: int) -> tuple[str, str]:
    """The UTC instants that bracket one of the shop's days.

    Used to fetch a day's sales, which are stored and queried in UTC.
    """
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc) \
        - timedelta(minutes=tz_offset_minutes)
    end = start + timedelta(days=1) - timedelta(milliseconds=1)
    return _fmt(start), _fmt(end)


def days_between(start_day: str, end_day: str) -> list[str]:
    d = datetime.strptime(start_day, "%Y-%m-%d")
    last = datetime.strptime(end_day, "%Y-%m-%d")
    out = []
    while d <= last:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def empty(day: str) -> dict:
    """A day the shop sold nothing.

    Stored rather than left absent, so the day is not re-queried forever
    on every visit to a month that contains a Sunday it was closed.
    """
    return {"date": day, "total": 0.0, "bill_count": 0, "refund_count": 0,
            "by_source": {}, "items": {}}


def build(day: str, sales: list[dict]) -> dict:
    """One day's facts, from that day's bills."""
    row = empty(day)
    for sale in sales:
        total = sale.get("total") or 0
        row["total"] += total
        # Refund totals were negated when the receipt was read, so they
        # subtract from takings without a special case - but they are not
        # bills, and a day of twelve sales is not a day of twelve sales
        # and three refunds.
        if sale.get("is_refund"):
            row["refund_count"] += 1
        else:
            row["bill_count"] += 1

        source = sale.get("source") or "loyverse"
        bucket = row["by_source"].setdefault(source, {"total": 0.0, "count": 0})
        bucket["total"] += total
        bucket["count"] += 1

        for item in sale.get("items", []):
            name = item.get("name") or ""
            if not name:
                continue
            entry = row["items"].setdefault(name, {"qty": 0.0, "revenue": 0.0})
            qty = item.get("qty") or 0
            entry["qty"] += qty
            entry["revenue"] += qty * (item.get("price") or 0)

    row["total"] = round(row["total"], 2)
    for bucket in row["by_source"].values():
        bucket["total"] = round(bucket["total"], 2)
    for entry in row["items"].values():
        entry["revenue"] = round(entry["revenue"], 2)
    return row


def build_many(sales: list[dict], tz_offset_minutes: int) -> dict[str, dict]:
    """Group a span of bills into one row per shop day."""
    by_day: dict[str, list[dict]] = {}
    for sale in sales:
        day = local_day(sale.get("date") or "", tz_offset_minutes)
        if day:
            by_day.setdefault(day, []).append(sale)
    return {day: build(day, rows) for day, rows in by_day.items()}


def summarise(rollups: list[dict], recipes: dict, materials: list[dict]) -> dict:
    """The same answer sales_report.summarise gives, from stored facts.

    Ingredient cost is worked out here rather than read from the rollup,
    which is what lets a corrected recipe or a corrected delivery price
    change last month's profit immediately instead of needing anything
    rebuilt.
    """
    cost_by_material = {m["id"]: (m.get("cost") or 0) for m in materials}
    total = 0.0
    bill_count = 0
    refund_count = 0
    ingredient_cost = 0.0
    uncosted = set()
    points = []

    for row in sorted(rollups, key=lambda r: r.get("date") or ""):
        total += row.get("total") or 0
        bill_count += row.get("bill_count") or 0
        refund_count += row.get("refund_count") or 0
        points.append({"t": row.get("date"), "sales": round(row.get("total") or 0, 2)})

        for name, entry in (row.get("items") or {}).items():
            recipe = recipes.get(name) or []
            if not recipe:
                uncosted.add(name)
                continue
            qty = entry.get("qty") or 0
            for ing in recipe:
                ingredient_cost += (ing.get("qty") or 0) * qty \
                    * cost_by_material.get(ing.get("material_id"), 0)

    return {
        "total": round(total, 2),
        "bill_count": bill_count,
        "refund_count": refund_count,
        "gross_profit": round(total - ingredient_cost, 2),
        "ingredient_cost": round(ingredient_cost, 2),
        "uncosted_menus": sorted(uncosted),
        "points": points,
    }


def top_items(rollups: list[dict], limit: int = 5) -> list[dict]:
    """Best sellers by quantity, the same ranking sales_report gives."""
    tally: dict[str, dict] = {}
    for row in rollups:
        for name, entry in (row.get("items") or {}).items():
            acc = tally.setdefault(name, {"name": name, "qty": 0.0, "revenue": 0.0})
            acc["qty"] += entry.get("qty") or 0
            acc["revenue"] += entry.get("revenue") or 0

    rows = sorted(tally.values(), key=lambda r: r["qty"], reverse=True)
    for r in rows:
        r["revenue"] = round(r["revenue"], 2)
    return rows if limit <= 0 else rows[:limit]


def breakdown(rollups: list[dict]) -> list[dict]:
    """Per-day totals, newest first - what the sales page lists.

    By the shop's day, which the raw-sales version was not: it grouped by
    UTC while the chart beside it grouped by local time, so the same
    screen disagreed with itself about which day an evening sale belonged
    to.
    """
    return [{"date": r.get("date"), "total": round(r.get("total") or 0, 2),
             "bill_count": (r.get("bill_count") or 0) + (r.get("refund_count") or 0)}
            for r in sorted(rollups, key=lambda r: r.get("date") or "", reverse=True)]


# ---- reading and filling -------------------------------------------------
# The pure functions above know nothing about storage. This one is where
# they meet it, and it is kept here rather than in the API so its cost can
# be measured in a test with a fake database (tests/test_daily_rollup.py).


def ensure_daily(store, store_id: str, start_day: str, end_day: str,
                 tz_offset_minutes: int, today: str) -> list[dict]:
    """The rollups for a span, building whatever is not stored yet.

    Two rules earn their keep here.

    Today is never stored. Bills are still arriving, and a row written at
    two in the afternoon would be read back at closing time as the day's
    takings - a number that is wrong in the one direction nobody checks,
    downwards. Today is recomputed from the bills every time; it is one
    day of them, which is what the page used to pay for a month.

    A missing day is stored even when the shop sold nothing, as a zero.
    Otherwise a Sunday the shop was closed looks identical to a Sunday
    that was never built, and every visit to that month goes back to the
    bills to rediscover the same nothing.
    """
    wanted = days_between(start_day, end_day)
    if not wanted:
        return []

    stored = {row.get("date"): row
              for row in store.list_daily(store_id, start_day, end_day)
              if row.get("date") and row["date"] < today}

    missing = [day for day in wanted if day not in stored and day <= today]
    if not missing:
        return [stored[day] for day in wanted if day in stored]

    # One read for the whole missing span. Usually that span is a tail -
    # the days since the page was last opened - or a month nobody has
    # looked at yet. A gap with stored days either side re-reads their
    # bills too, which costs what the page cost before this existed and
    # only ever happens once, because the gap is filled at the end of it.
    span_start, _ = day_bounds(missing[0], tz_offset_minutes)
    _, span_end = day_bounds(missing[-1], tz_offset_minutes)
    built = build_many(store.list_sales(store_id, span_start, span_end),
                       tz_offset_minutes)

    fresh = {day: built.get(day) or empty(day) for day in missing}
    keep = [row for day, row in fresh.items() if day < today]
    if keep:
        store.set_daily_many(store_id, keep)

    merged = {**stored, **fresh}
    return [merged[day] for day in wanted if day in merged]


def invalidate_for_sales(store, store_id: str, sales, now_iso: str,
                         tz_offset_minutes: int | None = None) -> list[str]:
    """Throw away the stored days that these bills belong to.

    Called wherever a sale appears, changes or disappears after the fact:
    a till that was offline, a delivery order keyed in the next morning,
    a repair re-reading a month.

    Today is skipped on purpose, and not as an optimisation - today is
    never stored in the first place, so deleting it would be a write per
    sync, forever, to remove a document that was never there.
    """
    rows = [s for s in sales if s]
    if not rows:
        return []
    tz = store.get_timezone() if tz_offset_minutes is None else tz_offset_minutes
    today = local_day(now_iso, tz)
    days = {local_day(s.get("date") or "", tz) for s in rows}
    stale = sorted(d for d in days if d and today and d < today)
    if stale:
        store.delete_daily(store_id, stale)
    return stale
