from __future__ import annotations

"""
What the assistant is allowed to know, and what it is allowed to say.

Two jobs live here, and both exist for the same reason: a wrong number
delivered in a confident sentence is worse than no assistant at all. A
shop owner cannot check an answer against anything - that is why they
asked - so an assistant that is right most of the time teaches them to
trust it, and then the one wrong answer is the one they act on.

BUILD_SNAPSHOT works out every figure before the model is called.

Not just the totals: the averages, the shares, the differences, the best
day. Anything a question might reasonably want is computed here in
Python, from the same rollups the screens are drawn from, so the model
is never in the position of having to work something out. Arithmetic is
where invented figures come from, and a model asked to divide will
always produce a number - it just will not always be the right one.

VERIFY_NUMBERS checks the answer against that snapshot afterwards.

It reports rather than blocks. A figure the snapshot cannot account for
is not proof the answer is wrong, and swallowing a good answer over a
rounding difference would be its own kind of unhelpful - but it is worth
saying out loud, in the same spirit as uncosted_menus: the caveat is
named, not hidden and not merely counted.

CAVEATS travel with the numbers, in the numbers' own dict.

Every figure this system produces already comes with the reasons it
might be incomplete - menus with no recipe, stock never counted, a
period that includes an unfinished today. The assistant gets those in
the same payload as the totals and is told to volunteer them, because
"กำไร 42,000 บาท" and "กำไร 42,000 บาท แต่มี 3 เมนูที่ยังไม่ได้ผูกสูตร
เลยยังไม่ได้หักต้นทุนส่วนนั้น" are different answers, and only one of
them is honest.
"""

import re

from core import daily_rollup


# How the model is told to behave. Kept next to the snapshot builder on
# purpose: the two are one design. Every rule here is only keepable
# because the snapshot already did the work the rule forbids.
INSTRUCTIONS = """คุณคือผู้ช่วยของร้านอาหารร้านหนึ่ง คุยกับเจ้าของร้านเป็นภาษาไทย

กฎที่ห้ามฝ่าฝืน:
1. ใช้ได้เฉพาะตัวเลขที่อยู่ในข้อมูลที่ให้มาเท่านั้น ห้ามคิดเลขเอง
   ห้ามบวก ลบ คูณ หาร หรือประมาณค่าใหม่ ทุกอย่างคำนวณมาให้แล้ว
2. ถ้าข้อมูลที่ให้มาไม่มีคำตอบ ให้บอกตรง ๆ ว่าไม่มีข้อมูลส่วนนั้น
   แล้วบอกว่าต้องไปดูที่หน้าไหนหรือต้องบันทึกอะไรเพิ่ม
   **ห้ามเดา ห้ามยกตัวเลขใกล้เคียงมาตอบแทน**
3. ถ้าใน caveats มีข้อไหนที่เกี่ยวกับสิ่งที่ถูกถาม **ต้องพูดถึงด้วยเสมอ**
   ตัวเลขที่ไม่ครบแล้วไม่บอกว่าไม่ครบ คือตัวเลขที่หลอกคนอ่าน
4. ห้ามแนะนำให้ทำอะไรที่ระบบทำไม่ได้ หรืออ้างว่ามีปุ่ม/หน้าจอที่ไม่ได้บอกไว้
5. คุณเป็นผู้วิเคราะห์แบบอ่านอย่างเดียว ห้ามอ้างว่าได้แก้ไข บันทึก ลบ หรืออนุมัติ
   ข้อมูลใดในระบบแล้ว ถ้าผู้ใช้ขอให้แก้ ให้บอกว่าทำไม่ได้และแนะนำหน้าที่ผู้ใช้ตรวจเอง

วิธีตอบ:
- สั้น ตรงคำถาม เหมือนคุยกัน ไม่ใช่รายงาน
- ใส่ตัวเลขจริงเสมอ อย่าตอบลอย ๆ ว่า "ดีขึ้น" หรือ "ลดลง"
- ถ้าเห็นอะไรน่าห่วงในข้อมูล บอกได้ แต่บอกครั้งเดียว อย่าย้ำ
- ไม่ต้องทักทาย ไม่ต้องสรุปซ้ำท้ายคำตอบ
"""


def build_snapshot(*, branch: str, rollups: list[dict], recipes: dict,
                   materials: list[dict], expenses: list[dict],
                   receivings: list[dict], period_from: str, period_to: str,
                   today: str) -> dict:
    """Everything the assistant may know about one branch, one period.

    Pure: takes what has already been fetched and returns a dict. That
    keeps it testable without a database, and keeps the decision about
    what leaves the shop in one readable place rather than spread across
    whichever endpoint happened to assemble it.
    """
    summary = daily_rollup.summarise(rollups, recipes, materials)
    days = daily_rollup.days_between(period_from, period_to)
    open_days = [r for r in rollups if (r.get("total") or 0) != 0]

    total = summary["total"]
    bills = summary["bill_count"]

    purchased = round(sum(r.get("total") or 0 for r in receivings), 2)
    fixed = round(sum(e.get("amount") or 0 for e in expenses
                      if e.get("category") == "fixed"), 2)
    variable = round(sum(e.get("amount") or 0 for e in expenses
                         if e.get("category") == "variable"), 2)
    # Material cost is what was actually bought, not what the recipes say
    # was used. The owner chose that deliberately: a profit figure that
    # disagrees with the bank is a profit figure they stop trusting.
    net = round(total - fixed - variable - purchased, 2)

    stock_value = round(sum((m.get("stock") or 0) * (m.get("cost") or 0)
                            for m in materials), 2)

    snapshot = {
        "branch": branch,
        "period": {
            "from": period_from,
            "to": period_to,
            "days": len(days),
            "days_with_sales": len(open_days),
            "includes_today": period_to >= today,
        },
        "sales": {
            "total": total,
            "bill_count": bills,
            "refund_count": summary["refund_count"],
            "average_per_bill": _div(total, bills),
            "average_per_open_day": _div(total, len(open_days)),
            "best_day": _extreme(open_days, best=True),
            "worst_day": _extreme(open_days, best=False),
        },
        "channels": _channels(rollups, total),
        "menus": {
            "distinct_sold": len({n for r in rollups
                                  for n in (r.get("items") or {})}),
            "top": _top_menus(rollups, total),
            "performance": daily_rollup.menu_performance(
                rollups, recipes, materials, limit=20),
        },
        "cost": {
            "ingredient_cost_by_recipe": summary["ingredient_cost"],
            "gross_profit": summary["gross_profit"],
            "gross_margin_pct": _pct(summary["gross_profit"], total),
            "purchased_actual": purchased,
            "purchased_minus_recipe": round(purchased - summary["ingredient_cost"], 2),
        },
        "expenses": {
            "fixed": fixed,
            "variable": variable,
            "material_purchased": purchased,
            "total": round(fixed + variable + purchased, 2),
        },
        "profit": {
            "net": net,
            "net_margin_pct": _pct(net, total),
        },
        "stock": {
            "value_now": stock_value,
            "material_count": len(materials),
            "negative": sorted(m.get("name") or m["id"] for m in materials
                               if (m.get("stock") or 0) < 0),
            "below_par": sorted(m.get("name") or m["id"] for m in materials
                                if (m.get("par_level") or 0) > 0
                                and (m.get("stock") or 0) <= m["par_level"]),
        },
    }
    snapshot["caveats"] = _caveats(snapshot, summary)
    return snapshot


def _caveats(snapshot: dict, summary: dict) -> list[dict]:
    """Why these numbers might not mean what they look like.

    Typed rather than free text so a screen can render them and a test
    can assert on them, and each one carries the items it is about -
    "3 menus weren't costed" leaves the reader with nothing to do.
    """
    out = []
    sales = snapshot["sales"]
    cost = snapshot["cost"]

    if summary["uncosted_menus"]:
        out.append({
            "kind": "uncosted_menus",
            "message": "เมนูเหล่านี้ยังไม่ได้ผูกสูตร จึงยังไม่ได้หักต้นทุนวัตถุดิบ "
                       "กำไรขั้นต้นจึงสูงกว่าความจริง",
            "items": summary["uncosted_menus"],
        })

    if snapshot["period"]["includes_today"]:
        out.append({
            "kind": "includes_today",
            "message": "ช่วงนี้รวมวันนี้ซึ่งยังขายไม่จบ ตัวเลขวันนี้จะเพิ่มขึ้นอีก",
            "items": [],
        })

    if cost["ingredient_cost_by_recipe"] > 0:
        out.append({
            "kind": "recipe_cost_at_latest_price",
            "message": "ต้นทุนตามสูตรคิดที่ราคาวัตถุดิบล่าสุด ไม่ใช่ราคาที่จ่ายจริง"
                       "ในช่วงนั้น ถ้าราคาวัตถุดิบเพิ่งขยับ ตัวเลขนี้จะขยับตามด้วย",
            "items": [],
        })

    if snapshot["stock"]["negative"]:
        out.append({
            "kind": "negative_stock",
            "message": "วัตถุดิบเหล่านี้ติดลบ แปลว่าสูตรหรือยอดนับไม่ตรงกับของจริง "
                       "ตัวเลขต้นทุนที่เกี่ยวข้องยังเชื่อไม่ได้เต็มที่",
            "items": snapshot["stock"]["negative"],
        })

    if sales["total"] > 0 and cost["purchased_actual"] == 0:
        out.append({
            "kind": "no_purchases",
            "message": "ช่วงนี้ไม่มีการบันทึกซื้อของเข้าร้านเลย กำไรสุทธิจึงยังไม่ได้"
                       "หักค่าวัตถุดิบ และจะดูสูงกว่าความจริงมาก",
            "items": [],
        })

    if sales["total"] > 0 and snapshot["expenses"]["fixed"] == 0 \
            and snapshot["expenses"]["variable"] == 0:
        out.append({
            "kind": "no_expenses",
            "message": "ช่วงนี้ไม่มีรายจ่ายคงที่หรือผันแปรบันทึกไว้เลย "
                       "(ค่าเช่า ค่าไฟ ค่าแรง) กำไรสุทธิจึงยังไม่ครบ",
            "items": [],
        })

    return out


def verify_numbers(text: str, snapshot: dict, floor: float = 100) -> list[float]:
    """Figures in the answer that the snapshot cannot account for.

    Reports, never blocks - see the module docstring. Small numbers are
    skipped because percentages, counts and quantities are legitimately
    derived and would drown the real signal; what this is watching for
    is a baht figure that was never in the data.

    A tolerance of one percent is allowed, so an answer that rounds
    12,043 to "ประมาณ 12,000" is not reported as invention.
    """
    allowed = _numbers_in(snapshot)
    unsupported = []
    for found in _numbers_in_text(text):
        if abs(found) < floor:
            continue
        if any(abs(found - a) <= max(1.0, abs(a) * 0.01) for a in allowed):
            continue
        unsupported.append(found)
    return unsupported


# ---- working out ---------------------------------------------------------

def _div(value: float, count: float) -> float:
    return round(value / count, 2) if count else 0.0


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _extreme(rollups: list[dict], best: bool) -> dict | None:
    if not rollups:
        return None
    row = (max if best else min)(rollups, key=lambda r: r.get("total") or 0)
    return {"date": row.get("date"), "total": round(row.get("total") or 0, 2),
            "bill_count": row.get("bill_count") or 0}


def _channels(rollups: list[dict], total: float) -> list[dict]:
    tally: dict[str, dict] = {}
    for row in rollups:
        for source, bucket in (row.get("by_source") or {}).items():
            acc = tally.setdefault(source, {"source": source, "total": 0.0,
                                            "count": 0})
            acc["total"] += bucket.get("total") or 0
            acc["count"] += bucket.get("count") or 0
    rows = sorted(tally.values(), key=lambda r: r["total"], reverse=True)
    for r in rows:
        r["total"] = round(r["total"], 2)
        r["share_pct"] = _pct(r["total"], total)
    return rows


def _top_menus(rollups: list[dict], total: float, limit: int = 10) -> list[dict]:
    rows = daily_rollup.top_items(rollups, limit)
    for r in rows:
        r["share_of_sales_pct"] = _pct(r["revenue"], total)
    return rows


def _numbers_in(value, out: set | None = None) -> set:
    """Every number anywhere in the snapshot, including the ones inside
    date strings - otherwise an answer that says "สิงหาคม 2026" is
    reported for inventing the year."""
    out = set() if out is None else out
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        out.add(float(value))
    elif isinstance(value, str):
        for chunk in re.findall(r"\d+(?:\.\d+)?", value):
            out.add(float(chunk))
    elif isinstance(value, dict):
        for v in value.values():
            _numbers_in(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _numbers_in(v, out)
    return out


def _numbers_in_text(text: str) -> list[float]:
    found = []
    for chunk in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            found.append(float(chunk.replace(",", "")))
        except ValueError:
            continue
    return found


# ---- asking a question ---------------------------------------------------
# The plan for this step was a set of tools the model could call to fetch
# whatever period a question turned out to be about. It is not what got
# built, and the reason is worth writing down.
#
# A tool that hands back raw rows puts the model straight back into doing
# arithmetic, which is the one thing this whole design exists to avoid -
# so the tools would have had to return pre-computed summaries anyway.
# At which point the only thing they add over packing those summaries up
# front is the ability to answer about an arbitrary period nobody chose.
# And a person asking about last Songkran can pick last Songkran: that is
# a date control, not a protocol, and it has the advantage of showing
# them which period the answer is about instead of leaving them to trust
# that the model picked the right one.
#
# So: the caller says which period, and everything about that period -
# and the one before it, and the difference between them - is worked out
# before the model is called. If a question genuinely needs a period the
# caller did not pick, the honest answer is "ไม่มีข้อมูลช่วงนั้น", which
# rule 2 of the instructions already requires.

# A question long enough to hide instructions in is not a question about
# takings. Short enough to be honest about, long enough for a real one.
MAX_QUESTION_LENGTH = 400


def build_context(*, current: dict, previous: dict | None,
                  series: list[dict]) -> dict:
    """What the model is shown for one question.

    Two snapshots and the difference between them, already taken. The
    subtraction is done here for the same reason every other figure is:
    a model asked what changed will always produce a number.
    """
    context = {"period": current, "series": series}
    if previous is not None:
        context["previous_period"] = previous
        context["change"] = compare_snapshots(current, previous)
    return context


def compare_snapshots(current: dict, previous: dict) -> dict:
    """The differences between two periods, worked out rather than left
    to be worked out."""
    return {
        "sales_baht": _delta(current["sales"]["total"], previous["sales"]["total"]),
        "sales_pct": _change_pct(current["sales"]["total"],
                                 previous["sales"]["total"]),
        "bills": _delta(current["sales"]["bill_count"],
                        previous["sales"]["bill_count"]),
        "average_per_bill_baht": _delta(current["sales"]["average_per_bill"],
                                        previous["sales"]["average_per_bill"]),
        "net_profit_baht": _delta(current["profit"]["net"],
                                  previous["profit"]["net"]),
        "purchased_baht": _delta(current["cost"]["purchased_actual"],
                                 previous["cost"]["purchased_actual"]),
        # Named so the model does not have to infer direction from a sign
        # and get it backwards on a negative profit.
        "sales_direction": _direction(current["sales"]["total"],
                                      previous["sales"]["total"]),
    }


def answer(provider, context: dict, question: str,
           previous_questions: list[str] | None = None) -> dict:
    """Ask one question and report what came back, with its warnings.

    Never raises for a provider failure: the caller has a screen to
    fill either way, and "ผู้ช่วยตอบไม่ได้ตอนนี้" is a better thing to
    put on it than a stack trace.
    """
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "ยังไม่ได้พิมพ์คำถาม"}
    if len(question) > MAX_QUESTION_LENGTH:
        return {"ok": False,
                "error": f"คำถามยาวเกินไป (เกิน {MAX_QUESTION_LENGTH} ตัวอักษร)"}

    try:
        # Previous questions help resolve follow-ups such as "แล้วเมนูนั้นล่ะ".
        # Old answers are excluded: an unverified number from an old answer
        # must never become an allowed input on the next turn.
        model_context = context
        history = [str(q).strip()[:MAX_QUESTION_LENGTH]
                   for q in (previous_questions or [])[-4:] if str(q).strip()]
        if history:
            model_context = {**context, "conversation": {
                "previous_questions_only": history,
                "note": "ใช้เพื่อเข้าใจคำถามต่อเนื่องเท่านั้น ไม่ใช่ข้อมูลตัวเลขของร้าน",
            }}
        text = provider.ask(INSTRUCTIONS, model_context, question)
    except Exception as e:
        return {"ok": False, "error": str(e) or "ผู้ช่วยตอบไม่ได้ตอนนี้"}

    # Verify against shop facts only, never against numbers a person may have
    # typed in a previous question.
    unsupported = verify_numbers(text, context)
    return {
        "ok": True,
        "answer": text.strip(),
        # Shown to the reader, not swallowed. An answer carrying a figure
        # the data cannot account for is still worth reading - it is just
        # not worth acting on without checking, and only the reader can
        # be told that.
        "unverified_numbers": unsupported,
        "provider": getattr(provider, "name", "unknown"),
    }


def _delta(current: float, previous: float) -> float:
    return round((current or 0) - (previous or 0), 2)


def _change_pct(current: float, previous: float) -> float | None:
    """None when there is nothing to compare against - the same choice
    sales_report.compare_previous makes, so a shop's first month is not
    reported as up a hundred percent from nothing."""
    if not previous:
        return None
    return round(((current or 0) - previous) / previous * 100, 1)


def _direction(current: float, previous: float) -> str:
    if current > previous:
        return "up"
    return "down" if current < previous else "flat"
