from __future__ import annotations

"""
Yesterday, in the few lines someone will actually read over coffee.

THE TEMPLATE WRITES IT. THE MODEL ONLY REPHRASES IT.

That order is the whole design, and it is the opposite of the obvious
one. Handing a model the figures and asking it to write the summary
puts every number in the brief at the mercy of the model's worst day -
and a brief is read at six in the morning by someone who is not
checking. So the brief is written here, in Python, from the same
rollups the screens are drawn from. Every figure in it is already true
before any model is involved.

The model gets the finished brief and is asked to make it read like a
person wrote it. What comes back is checked against the same figures
(assistant.verify_numbers), and anything that does not survive that
check is thrown away in favour of the template text. So the failure
modes are: the model is down (the shop gets the plain version), the
model invents something (the shop gets the plain version), the model
does its job (the shop gets a nicer version of exactly the same facts).
There is no path where the shop gets a wrong number.

WHAT GOES IN IT.

Only things worth knowing before opening today - the same rule
build_alerts follows, for the same reason: a summary long enough to
skim is a summary nobody reads, and then the one morning it says
something urgent, nobody reads that either.

A quiet day is reported as a quiet day. A brief that finds something
encouraging to say about a day the shop took nothing is a brief that
has started performing rather than reporting.
"""

from datetime import datetime

from core import assistant
from core import daily_rollup


# Written for the polish step. The model is given a brief that is
# already correct and asked only to make it read better - so every rule
# here is about what it must not touch.
POLISH_INSTRUCTIONS = """คุณคือผู้ช่วยของร้านอาหาร กำลังเรียบเรียงสรุปประจำวันให้เจ้าของร้านอ่านตอนเช้า

คุณจะได้ "สรุปฉบับร่าง" ที่ตัวเลขถูกต้องแล้วทั้งหมด หน้าที่คุณคือ**เรียบเรียงให้อ่านลื่นขึ้น**

กฎที่ห้ามฝ่าฝืน:
1. **ห้ามเปลี่ยนตัวเลขใด ๆ ห้ามเพิ่มตัวเลขใหม่ ห้ามคิดเลขเอง** ใช้ตัวเลขในร่างเท่านั้น
2. **ห้ามตัดข้อควรระวัง (caveats) หรือรายการที่ต้องทำออก** ย่อได้ ตัดทิ้งไม่ได้
3. ห้ามเพิ่มความเห็นที่ข้อมูลไม่ได้บอก เช่น สาเหตุที่ยอดตก หรือคำแนะนำการตลาด
4. ถ้าเป็นวันที่ขายได้น้อยหรือไม่ได้ขายเลย **บอกตรง ๆ** ห้ามหาแง่ดีมาใส่

วิธีเขียน:
- ภาษาพูด สั้น ๆ 3-5 บรรทัด เหมือนลูกน้องรายงานเจ้าของ
- ไม่ต้องทักทาย ไม่ต้องขึ้นหัวข้อ ไม่ต้องสรุปซ้ำท้าย
- ตัวเลขสำคัญคงไว้ให้ครบ
"""

_THAI_DAYS = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")
_THAI_MONTHS = ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
                "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.")

# Enough days to say whether yesterday was normal, few enough that a
# shop open a fortnight still gets a comparison.
BASELINE_DAYS = 7
MIN_BASELINE_DAYS = 2


def build(*, day: str, rollups: list[dict], recipes: dict,
          materials: list[dict], days_since_count: int | None = None,
          count_reminder_days: int = 7) -> dict:
    """One day's brief, written from facts.

    `rollups` should cover the day and the days before it - the extra
    days are the baseline yesterday is measured against, and are not
    reported themselves.
    """
    rows = {r.get("date"): r for r in rollups if r.get("date")}
    today_row = rows.get(day) or daily_rollup.empty(day)
    baseline = _baseline(rows, day)

    total = round(today_row.get("total") or 0, 2)
    bills = today_row.get("bill_count") or 0
    sold_nothing = bills == 0 and total == 0

    brief = {
        "date": day,
        "weekday": _weekday(day),
        "sales": {
            "total": total,
            "bill_count": bills,
            "refund_count": today_row.get("refund_count") or 0,
            "average_per_bill": round(total / bills, 2) if bills else 0.0,
        },
        # No comparison on a day the shop took nothing. It was shut, or
        # the till was not used - neither is "down 100%", and that is a
        # sentence a model handed the figure would happily write.
        "compare": None if sold_nothing else _compare(total, baseline),
        "channels": _channels(today_row),
        "top": _top(today_row),
        "actions": _actions(materials, days_since_count, count_reminder_days),
        "caveats": _caveats(today_row, recipes, materials),
        "closed": sold_nothing,
    }
    brief["text"] = _write(brief)
    # Named fields rather than an absent key: whoever reads this later
    # can tell "the model was never asked" from "the model was asked and
    # its answer was thrown away", and those are different problems.
    brief["polished"] = None
    brief["polish_status"] = "not_attempted"
    return brief


def polish(provider, brief: dict) -> dict:
    """Ask a provider to rewrite the brief, and keep the result only if
    it survives checking. Never raises: a brief that cannot be polished
    is still a brief."""
    out = dict(brief)
    try:
        text = provider.ask(POLISH_INSTRUCTIONS, _polishable(brief),
                            "เรียบเรียงสรุปนี้ให้อ่านลื่นขึ้น โดยไม่เปลี่ยนตัวเลข")
    except Exception:
        # Any provider failure at all. The brief is already written and
        # already correct, so there is nothing here worth failing over.
        out["polish_status"] = "failed"
        return out

    invented = assistant.verify_numbers(text, _polishable(brief))
    if invented:
        out["polish_status"] = "rejected"
        out["polish_rejected_numbers"] = invented
        return out

    dropped = _dropped_warnings(brief, text)
    if dropped:
        # Rewriting away the caveats is the one edit that makes the brief
        # worse than not polishing it at all.
        out["polish_status"] = "rejected"
        out["polish_rejected_numbers"] = []
        out["polish_dropped"] = dropped
        return out

    out["polished"] = text.strip()
    out["polish_status"] = "ok"
    out["provider"] = getattr(provider, "name", "unknown")
    return out


def display_text(brief: dict) -> str:
    """What to show. The polished version when there is one, the
    template otherwise - so every caller gets this decision right by
    not making it."""
    return brief.get("polished") or brief.get("text") or ""


# ---- writing it ----------------------------------------------------------

def _write(brief: dict) -> str:
    when = f"{brief['weekday']} {_thai_date(brief['date'])}"
    if brief["closed"]:
        # Said plainly. The shop was shut, or the till was not used, and
        # both are worth knowing without a sentence of cushioning.
        lines = [f"{when} ไม่มียอดขายบันทึกไว้เลย"]
        lines += _action_lines(brief)
        return "\n".join(lines)

    s = brief["sales"]
    lines = [f"{when} ขายได้ {_baht(s['total'])} บาท "
             f"จาก {s['bill_count']} บิล เฉลี่ยบิลละ {_baht(s['average_per_bill'])} บาท"]

    if s["refund_count"]:
        lines.append(f"มีคืนเงิน {s['refund_count']} รายการ (หักออกจากยอดแล้ว)")

    c = brief["compare"]
    if c:
        word = "สูงกว่า" if c["up"] else "ต่ำกว่า"
        if c["pct"] < 5:
            lines.append(f"พอ ๆ กับค่าเฉลี่ย {c['days']} วันก่อนหน้า "
                         f"({_baht(c['baseline_average'])} บาท)")
        else:
            lines.append(f"{word}ค่าเฉลี่ย {c['days']} วันก่อนหน้า {_pct(c['pct'])}% "
                         f"(เฉลี่ย {_baht(c['baseline_average'])} บาท)")

    channels = [c for c in brief["channels"] if c["source"] != "loyverse"]
    if channels:
        parts = ", ".join(f"{_channel_name(c['source'])} {_baht(c['total'])} บาท"
                          for c in channels)
        lines.append(f"ในนั้นเป็นออเดอร์นอกร้าน {parts}")

    if brief["top"]:
        parts = ", ".join(f"{t['name']} ({_qty(t['qty'])})" for t in brief["top"])
        lines.append(f"ขายดีสุด: {parts}")

    lines += _action_lines(brief)
    lines += [c["message"] for c in brief["caveats"]]
    return "\n".join(lines)


def _action_lines(brief: dict) -> list[str]:
    return [a["message"] for a in brief["actions"]]


# ---- the pieces ----------------------------------------------------------

def _baseline(rows: dict, day: str) -> list[dict]:
    """The days before this one that the shop actually traded.

    Closed days are left out rather than averaged in as zeros - a shop
    that shuts on Mondays would otherwise look like it is in decline
    every Tuesday.
    """
    earlier = sorted((d for d in rows if d < day), reverse=True)[:BASELINE_DAYS]
    return [rows[d] for d in earlier if (rows[d].get("total") or 0) != 0]


def _compare(total: float, baseline: list[dict]) -> dict | None:
    if len(baseline) < MIN_BASELINE_DAYS:
        return None
    average = sum(r.get("total") or 0 for r in baseline) / len(baseline)
    if average <= 0:
        return None
    change = (total - average) / average * 100
    return {"baseline_average": round(average, 2), "days": len(baseline),
            "pct": round(abs(change), 1), "up": change >= 0}


def _channels(row: dict) -> list[dict]:
    rows = [{"source": source, "total": round(bucket.get("total") or 0, 2),
             "count": bucket.get("count") or 0}
            for source, bucket in (row.get("by_source") or {}).items()]
    return sorted(rows, key=lambda r: r["total"], reverse=True)


def _top(row: dict, limit: int = 3) -> list[dict]:
    return daily_rollup.top_items([row], limit)


def _actions(materials: list[dict], days_since_count: int | None,
             count_reminder_days: int) -> list[dict]:
    """Only things someone can do something about today."""
    out = []

    negative = sorted(m.get("name") or m["id"] for m in materials
                      if (m.get("stock") or 0) < 0)
    if negative:
        out.append({
            "kind": "negative_stock",
            "items": negative,
            "message": f"สต๊อกติดลบ: {', '.join(negative)} - "
                       f"แปลว่าสูตรหรือยอดนับไม่ตรงกับของจริง",
        })

    low = sorted(m.get("name") or m["id"] for m in materials
                 if (m.get("par_level") or 0) > 0
                 and 0 <= (m.get("stock") or 0) <= m["par_level"])
    if low:
        out.append({
            "kind": "low_stock",
            "items": low,
            "message": f"ต้องสั่งของ: {', '.join(low)}",
        })

    if days_since_count is None:
        out.append({
            "kind": "never_counted",
            "items": [],
            "message": "ยังไม่เคยนับสต๊อกเลย - ตัวเลขของคงเหลือยังเชื่อไม่ได้",
        })
    elif days_since_count >= count_reminder_days:
        out.append({
            "kind": "count_due",
            "items": [],
            "message": f"ไม่ได้นับสต๊อกมา {days_since_count} วันแล้ว",
        })

    return out


def _caveats(row: dict, recipes: dict, materials: list[dict]) -> list[dict]:
    uncosted = sorted(name for name in (row.get("items") or {})
                      if not recipes.get(name))
    if not uncosted:
        return []
    shown = uncosted[:3]
    more = "" if len(uncosted) <= 3 else f" และอีก {len(uncosted) - 3} รายการ"
    return [{
        "kind": "uncosted_menus",
        "items": uncosted,
        "message": f"เมนูที่ยังไม่ได้ผูกสูตร: {', '.join(shown)}{more} - "
                   f"ยอดนี้จึงยังไม่ได้หักต้นทุนส่วนนั้น",
    }]


# ---- polishing -----------------------------------------------------------

def _polishable(brief: dict) -> dict:
    """What the model is shown. The draft and its figures, nothing else -
    it is rewriting a paragraph, not answering a question."""
    return {k: v for k, v in brief.items()
            if k not in ("polished", "polish_status", "provider",
                         "polish_rejected_numbers", "polish_dropped")}


def _dropped_warnings(brief: dict, text: str) -> list[str]:
    """Which actions and caveats the rewrite lost.

    Matched on the names inside them rather than on the wording, since
    rephrasing is the whole point - but an ingredient that has to be
    ordered is a word that has to survive.
    """
    dropped = []
    for entry in brief["actions"] + brief["caveats"]:
        names = entry.get("items") or []
        if names:
            if not any(name in text for name in names):
                dropped.append(entry["kind"])
        elif entry["kind"] in ("never_counted", "count_due") \
                and "นับสต๊อก" not in text:
            dropped.append(entry["kind"])
    return dropped


# ---- formatting ----------------------------------------------------------

def _weekday(day: str) -> str:
    try:
        return _THAI_DAYS[datetime.strptime(day, "%Y-%m-%d").weekday()]
    except ValueError:
        return ""


def _thai_date(day: str) -> str:
    try:
        dt = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return day
    return f"{dt.day} {_THAI_MONTHS[dt.month - 1]}"


def _baht(value: float) -> str:
    """No decimals when there are none - '12,450 บาท' reads, '12,450.00
    บาท' is a spreadsheet talking."""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _pct(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"


def _qty(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _channel_name(source: str) -> str:
    from core.delivery import CHANNELS
    return CHANNELS.get(source) or source
