from __future__ import annotations

"""
The assistant asks for a slice of the shop's data. Python cuts it.

WHY THIS EXISTS.

Before this, the model was handed a fixed pack of figures for one period
and nothing else, so it could only ever answer the questions somebody had
thought to pre-compute. Every real question outside that list - which day
of the week is busiest, is Grab growing, what was last month like, which
dish is slipping - came back as "there is no data", which was not even
true: the data was there, the cut was not.

The obvious fix is to send the rows and let the model add them up. That
is the one thing this whole design refuses to do, and not out of
squeamishness: verify_numbers can catch a figure that appears nowhere in
the data, but it cannot catch a correct-looking figure that was divided
wrongly. 4,250 arrived at by mistake and 4,250 arrived at correctly are
the same string.

So the model writes the order and Python fills it. It chooses WHICH
numbers; it never produces one. That means the rule it works under -
never do arithmetic - does not have to be relaxed by a single word, while
the range of answerable questions goes from a fixed list to a space.

WHY A SPEC AND NOT CODE.

The order is a dict, checked field by field against the lists below.
There is no eval, no query string, nothing the model writes that this
module executes. A spec asking for something that cannot be answered is
REFUSED with the reason - never quietly answered with the nearest thing
that could be computed, which is how a question about Grab's menu mix
would come back as the whole shop's menu mix and read as an answer.
"""

from datetime import datetime

from core import daily_rollup


THAI_WEEKDAYS = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")

# What may be asked for. The model is shown this, and every spec is
# checked against it - an unknown field is a refusal, not an ignored key,
# because a silently dropped filter turns "Grab only" into "everything".
DATASETS = {
    "sales": {
        "group_by": ("day", "weekday", "month", "menu", "channel", "none"),
        "metrics": ("sales", "bills", "qty", "avg_bill",
                    "ingredient_cost", "gross_profit", "gross_margin_pct"),
        "filter": ("channel",),
    },
    "expenses": {
        "group_by": ("category", "name", "month", "none"),
        "metrics": ("amount", "count"),
        "filter": ("category",),
    },
}

MAX_ROWS = 50


class QueryError(ValueError):
    """A spec that cannot be answered, with the reason the model is shown."""


def describe() -> dict:
    """What the model is told it can ask for.

    Sent with every question. It is the schema, not the data - a few
    hundred characters that replace guessing.
    """
    return {
        "how_it_works": (
            "ส่ง spec มาแล้วระบบจะคำนวณให้ ตัวเลขทุกตัวที่ได้กลับไปคำนวณเสร็จแล้ว "
            "ถ้าถามในสิ่งที่ตอบไม่ได้ จะได้เหตุผลกลับไป ไม่ใช่ตัวเลขที่ใกล้เคียง"),
        "datasets": {
            "sales": {
                "คือ": "บิลขายทั้งหมด รวมออเดอร์นอกร้าน แยกตามวันของร้าน",
                "group_by": list(DATASETS["sales"]["group_by"]),
                "metrics": list(DATASETS["sales"]["metrics"]),
                "filter": {"channel": "loyverse | grab | lineman | shopeefood | "
                                      "phone | online_menu | walk_in | other"},
            },
            "expenses": {
                "คือ": "รายจ่ายที่บันทึกเอง (ไม่รวมค่าวัตถุดิบที่มาจากใบรับของ)",
                "group_by": list(DATASETS["expenses"]["group_by"]),
                "metrics": list(DATASETS["expenses"]["metrics"]),
                "filter": {"category": "fixed | variable"},
            },
        },
        "spec": {"dataset": "sales", "from": "2026-07-01", "to": "2026-07-31",
                 "group_by": "weekday", "metrics": ["sales", "bills"],
                 "filter": {"channel": "grab"}, "sort": "sales", "limit": 10},
        "ไม่ได้เก็บไว้": [
            "เวลาในวัน (กี่โมงขายดี) - rollup เก็บเป็นรายวัน",
            "ค่าคอมของแอปเดลิเวอรี",
            "ข้อมูลลูกค้า พนักงาน โต๊ะ",
            "เมนูแยกตามช่องทาง (รู้ยอดรวมของแต่ละช่องทาง แต่ไม่รู้ว่าช่องทางนั้นขายเมนูไหน)",
        ],
    }


def clean_spec(spec: dict) -> dict:
    """Check the order before anything is computed from it."""
    if not isinstance(spec, dict):
        raise QueryError("spec ต้องเป็น object")

    dataset = spec.get("dataset") or "sales"
    if dataset not in DATASETS:
        raise QueryError(f"ไม่มี dataset ชื่อ '{dataset}' - มีแค่ {list(DATASETS)}")
    allowed = DATASETS[dataset]

    group_by = spec.get("group_by") or "none"
    if group_by not in allowed["group_by"]:
        raise QueryError(
            f"{dataset} แบ่งกลุ่มตาม '{group_by}' ไม่ได้ - ได้แค่ {list(allowed['group_by'])}")

    metrics = spec.get("metrics") or list(allowed["metrics"][:1])
    if isinstance(metrics, str):
        metrics = [metrics]
    unknown = [m for m in metrics if m not in allowed["metrics"]]
    if unknown:
        raise QueryError(
            f"{dataset} ไม่มี metric {unknown} - มีแค่ {list(allowed['metrics'])}")

    filters = spec.get("filter") or {}
    if not isinstance(filters, dict):
        raise QueryError("filter ต้องเป็น object")
    unknown = [k for k in filters if k not in allowed["filter"]]
    if unknown:
        raise QueryError(
            f"{dataset} กรองด้วย {unknown} ไม่ได้ - ได้แค่ {list(allowed['filter'])}")

    # The one combination that would otherwise return a real-looking lie.
    # A day's rollup knows what each channel took and what each menu sold,
    # but not which menu each channel sold - so "Grab's best dish" would
    # quietly come back as the whole shop's best dish.
    if dataset == "sales" and filters.get("channel") and group_by == "menu":
        raise QueryError(
            "ระบบไม่ได้เก็บว่าแต่ละช่องทางขายเมนูไหน "
            "รู้แค่ยอดรวมของแต่ละช่องทาง กับยอดของแต่ละเมนูรวมทุกช่องทาง")
    if dataset == "sales" and filters.get("channel"):
        blocked = [m for m in metrics if m not in ("sales", "bills", "avg_bill")]
        if blocked:
            raise QueryError(
                f"กรองตามช่องทางแล้วดู {blocked} ไม่ได้ "
                f"เพราะระบบไม่ได้แยกเมนูตามช่องทาง - ได้แค่ sales, bills, avg_bill")
    if dataset == "sales" and group_by == "channel":
        blocked = [m for m in metrics if m not in ("sales", "bills", "avg_bill")]
        if blocked:
            raise QueryError(
                f"แบ่งตามช่องทางแล้วดู {blocked} ไม่ได้ - ได้แค่ sales, bills, avg_bill")
    if dataset == "sales" and group_by != "menu" and "qty" in metrics:
        raise QueryError("qty ดูได้เฉพาะตอนแบ่งกลุ่มตามเมนู")
    if dataset == "sales" and group_by == "menu":
        # One bill holds several dishes, so "bills per menu" and "average
        # bill per menu" are not quantities that exist.
        blocked = [m for m in metrics if m in ("bills", "avg_bill")]
        if blocked:
            raise QueryError(
                f"แบ่งตามเมนูแล้วดู {blocked} ไม่ได้ เพราะหนึ่งบิลมีได้หลายเมนู - "
                f"ถ้าอยากได้ราคาเฉลี่ยต่อจาน ใช้ sales กับ qty แล้วดูคู่กัน")

    limit = spec.get("limit")
    try:
        limit = MAX_ROWS if limit is None else min(int(limit), MAX_ROWS)
    except (TypeError, ValueError):
        raise QueryError("limit ต้องเป็นตัวเลข")

    sort = spec.get("sort") or (metrics[0] if group_by != "day" else "group")
    if sort not in metrics and sort != "group":
        raise QueryError(f"เรียงตาม '{sort}' ไม่ได้ - ต้องเป็น group หรือหนึ่งใน {metrics}")

    return {"dataset": dataset, "from": _day(spec.get("from")), "to": _day(spec.get("to")),
            "group_by": group_by, "metrics": metrics, "filter": filters,
            "sort": sort, "limit": limit}


def run(spec: dict, *, rollups=None, recipes=None, materials=None,
        expenses=None) -> dict:
    """Answer one order. Every number in the result was computed here."""
    clean = clean_spec(spec)
    if clean["dataset"] == "expenses":
        rows, note = _expenses(clean, expenses or [])
    else:
        rows, note = _sales(clean, rollups or [], recipes or {}, materials or [])

    # A menu with no recipe has no profit to rank by. It sorts to the
    # bottom either way rather than to whichever end the direction happens
    # to send it, so "the least profitable dish" is never a dish whose
    # profit is simply unknown.
    key = clean["sort"]
    ranked = [r for r in rows if key == "group" or r.get(key) is not None]
    unknown = [r for r in rows if key != "group" and r.get(key) is None]
    ranked.sort(key=lambda r: r.get("group") if key == "group" else r.get(key),
                reverse=key != "group")
    rows = ranked + unknown
    shown = rows[:clean["limit"]]
    return {
        "spec": clean,
        "rows": shown,
        "row_count": len(rows),
        "truncated": len(rows) > len(shown),
        # The totals of the WHOLE result, not of the rows shown, so a
        # top-ten never reads as the whole shop.
        "totals": _totals(rows, clean["metrics"]),
        "note": note,
    }


# ---- sales ---------------------------------------------------------------

def _sales(spec: dict, rollups: list[dict], recipes: dict,
           materials: list[dict]) -> tuple[list[dict], str | None]:
    rows = [r for r in rollups if _within(r.get("date"), spec)]
    channel = (spec["filter"] or {}).get("channel")
    note = None

    if spec["group_by"] == "menu":
        perf = daily_rollup.menu_performance(rows, recipes, materials, limit=0)
        note = ("ยอดของแต่ละเมนูคิดจากราคาต่อรายการ อาจไม่เท่ากับยอดบิลรวมเป๊ะ "
                "ถ้ามีส่วนลดทั้งบิล")
        return [{"group": m["name"], **_menu_metrics(m, spec["metrics"])}
                for m in perf], note

    if spec["group_by"] == "channel":
        tally: dict[str, dict] = {}
        for row in rows:
            for src, bucket in (row.get("by_source") or {}).items():
                acc = tally.setdefault(src, {"sales": 0.0, "bills": 0})
                acc["sales"] += bucket.get("total") or 0
                acc["bills"] += bucket.get("count") or 0
        return [{"group": src, **_basic(v["sales"], v["bills"], spec["metrics"])}
                for src, v in tally.items()], None

    buckets: dict[str, dict] = {}
    for row in rows:
        key = _bucket(row.get("date"), spec["group_by"])
        acc = buckets.setdefault(key, {"sales": 0.0, "bills": 0, "days": 0,
                                       "items": {}})
        if channel:
            bucket = (row.get("by_source") or {}).get(channel)
            if not bucket:
                continue
            acc["sales"] += bucket.get("total") or 0
            acc["bills"] += bucket.get("count") or 0
        else:
            acc["sales"] += row.get("total") or 0
            acc["bills"] += row.get("bill_count") or 0
            for name, entry in (row.get("items") or {}).items():
                acc["items"][name] = acc["items"].get(name, 0) + (entry.get("qty") or 0)
        acc["days"] += 1

    out = []
    for key, acc in buckets.items():
        metrics = _basic(acc["sales"], acc["bills"], spec["metrics"])
        if not channel:
            metrics.update(_costs(acc["items"], recipes, materials,
                                  acc["sales"], spec["metrics"]))
        out.append({"group": key, "days": acc["days"], **metrics})
    if channel:
        note = "กรองเฉพาะช่องทางนี้ จึงดูได้แค่ยอดขายกับจำนวนบิล"
    return out, note


def _menu_metrics(menu: dict, wanted) -> dict:
    out = {}
    for m in wanted:
        if m == "sales":
            out["sales"] = menu["revenue"]
        elif m == "qty":
            out["qty"] = menu["qty"]
        elif m == "bills":
            out["bills"] = None  # a bill can hold several menus
        elif m == "avg_bill":
            out["avg_bill"] = menu["average_price"]
        elif m in ("ingredient_cost", "gross_profit", "gross_margin_pct"):
            # None rather than zero for a menu with no recipe: zero cost
            # would make an uncosted dish the shop's most profitable one.
            out[m] = menu.get(m if m != "ingredient_cost" else "ingredient_cost")
    return out


def _basic(sales: float, bills: int, wanted) -> dict:
    out = {}
    if "sales" in wanted:
        out["sales"] = round(sales, 2)
    if "bills" in wanted:
        out["bills"] = bills
    if "avg_bill" in wanted:
        out["avg_bill"] = round(sales / bills, 2) if bills else 0.0
    return out


def _costs(items: dict, recipes: dict, materials: list[dict],
           sales: float, wanted) -> dict:
    if not any(m in wanted for m in ("ingredient_cost", "gross_profit",
                                     "gross_margin_pct")):
        return {}
    cost_by_material = {m["id"]: (m.get("cost") or 0) for m in materials}
    cost = 0.0
    for name, qty in items.items():
        for ing in recipes.get(name) or []:
            cost += (ing.get("qty") or 0) * qty * cost_by_material.get(
                ing.get("material_id"), 0)
    out = {}
    if "ingredient_cost" in wanted:
        out["ingredient_cost"] = round(cost, 2)
    if "gross_profit" in wanted:
        out["gross_profit"] = round(sales - cost, 2)
    if "gross_margin_pct" in wanted:
        out["gross_margin_pct"] = round((sales - cost) / sales * 100, 1) if sales else 0.0
    return out


# ---- expenses ------------------------------------------------------------

def _expenses(spec: dict, expenses: list[dict]) -> tuple[list[dict], str | None]:
    category = (spec["filter"] or {}).get("category")
    rows = [e for e in expenses
            if _within((e.get("date") or "")[:10], spec)
            and (not category or e.get("category") == category)]
    buckets: dict[str, dict] = {}
    for e in rows:
        key = _expense_bucket(e, spec["group_by"])
        acc = buckets.setdefault(key, {"amount": 0.0, "count": 0})
        acc["amount"] += e.get("amount") or 0
        acc["count"] += 1
    out = []
    for key, acc in buckets.items():
        row = {"group": key}
        if "amount" in spec["metrics"]:
            row["amount"] = round(acc["amount"], 2)
        if "count" in spec["metrics"]:
            row["count"] = acc["count"]
        out.append(row)
    return out, "ไม่รวมค่าวัตถุดิบที่มาจากใบรับของ"


def _expense_bucket(expense: dict, group_by: str) -> str:
    if group_by == "category":
        return expense.get("category") or "?"
    if group_by == "name":
        return expense.get("name") or "?"
    if group_by == "month":
        return (expense.get("date") or "")[:7]
    return "ทั้งหมด"


# ---- shared --------------------------------------------------------------

# Adding these up gives a number that looks like a total and is not one -
# the sum of seven daily averages, or of seven margins.
NOT_ADDABLE = ("avg_bill", "gross_margin_pct")


def _totals(rows: list[dict], metrics) -> dict:
    out = {}
    for m in metrics:
        if m in NOT_ADDABLE:
            continue
        values = [r.get(m) for r in rows if isinstance(r.get(m), (int, float))]
        if values:
            out[m] = round(sum(values), 2)
    # Recomputed from the totals rather than averaged from the rows, which
    # is the difference between "the shop's average bill" and "the average
    # of the daily averages" - two different numbers, one of them wrong.
    if "avg_bill" in metrics and out.get("bills"):
        out["avg_bill"] = round(out.get("sales", 0) / out["bills"], 2)
    if "gross_margin_pct" in metrics and out.get("sales"):
        out["gross_margin_pct"] = round(
            out.get("gross_profit", 0) / out["sales"] * 100, 1)
    return out


def _bucket(day: str, group_by: str) -> str:
    if group_by == "weekday":
        try:
            return THAI_WEEKDAYS[datetime.strptime(day, "%Y-%m-%d").weekday()]
        except (ValueError, TypeError):
            return "?"
    if group_by == "month":
        return (day or "")[:7]
    if group_by == "day":
        return day or "?"
    return "ทั้งหมด"


def _within(day: str | None, spec: dict) -> bool:
    if not day:
        return False
    if spec["from"] and day < spec["from"]:
        return False
    if spec["to"] and day > spec["to"]:
        return False
    return True


def _day(value) -> str | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise QueryError(f"วันที่ '{value}' ไม่ถูกรูปแบบ ต้องเป็น YYYY-MM-DD")
    return text
