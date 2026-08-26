"""
What makes a recorded expense valid.

Expenses are the one number in this system a person types in free-hand,
and they go straight into the profit figure - there is no delivery note
or POS receipt behind them to disagree with. So the shapes that are
obviously wrong get refused at the door: no name, nothing to spend, an
amount that isn't a number.

Kept here rather than in the API layer so the rules can be tested
without a web framework, and so recording and correcting an expense
cannot drift apart - a correction that could save a shape recording
would have refused is a hole in the same wall.
"""

CATEGORIES = ("fixed", "variable", "material")

# Material cost is computed from deliveries already recorded, so typing
# one in by hand counts the same spend twice. Existing entries in that
# category can still be corrected - they predate that rule - which is why
# this list is about what may be CREATED, not what may exist.
RECORDABLE = ("fixed", "variable")


class ExpenseError(ValueError):
    """Message is meant to be shown to the person who typed it."""


def clean_expense(category, name, amount, date) -> dict:
    name = (name or "").strip()
    if not name:
        raise ExpenseError("กรุณาใส่ชื่อรายการ")

    if category not in CATEGORIES:
        raise ExpenseError("หมวดค่าใช้จ่ายไม่ถูกต้อง")

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise ExpenseError("จำนวนเงินต้องเป็นตัวเลข")
    # Zero and negative are both refused. A zero-baht expense is a
    # half-finished entry, and a negative one is someone trying to cancel
    # an earlier entry by adding its opposite - which leaves both rows in
    # the list forever. Deleting the wrong one is the way to undo it.
    if amount <= 0:
        raise ExpenseError("จำนวนเงินต้องมากกว่า 0")

    date = (date or "").strip()
    if not date:
        raise ExpenseError("กรุณาใส่วันที่จ่าย")

    return {"category": category, "name": name, "amount": amount, "date": date}
