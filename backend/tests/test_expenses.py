"""
Tests for recording, correcting and removing an expense.

An expense is the one figure in this system with nothing behind it: no
delivery note, no POS receipt, just what someone typed. It lands straight
in the profit figure, and in real use it gets typed wrong - a digit
dropped, the wrong month, the same bill entered twice. Until now there
was no way to fix one: add and list, nothing else. A wrong number stayed
wrong, in the profit figure, forever.

Two things are worth protecting here:

  the rules       - correcting must not be able to save a shape that
                    recording would have refused, or the validation is a
                    door with a window next to it

  the categories  - a correction can move an entry between categories,
                    and each category is fetched separately, so an entry
                    that moves must not leave a copy behind

Offline, in-memory. Run with:

    cd backend
    python tests/test_expenses.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store, FakeDb
from core.expenses import clean_expense, ExpenseError

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def refuses(label, **kwargs):
    args = {"category": "fixed", "name": "ค่าเช่า", "amount": 100,
            "date": "2026-08-25", **kwargs}
    try:
        clean_expense(**args)
        check(label, "accepted", "refused")
    except ExpenseError as e:
        _results.append(True)
        print(f"  [PASS] {label}: refused - {e}")


def section(title):
    print(f"\n=== {title} ===")


def store_with(*expenses):
    store = make_test_store(db=FakeDb())
    ids = [store.add_expense("b1", **e)["id"] for e in expenses]
    return store, ids


RENT = {"category": "fixed", "name": "ค่าเช่า", "amount": 12000, "date": "2026-08-01"}
ELEC = {"category": "variable", "name": "ค่าไฟ", "amount": 3400, "date": "2026-08-05"}


def test_a_wrong_amount_can_be_corrected():
    section("A wrong amount can be corrected")
    # The reported case: 3,400 typed as 34,000, and the month's profit
    # wrong by thirty thousand baht until someone can fix it.
    store, (rent_id,) = store_with({**ELEC, "amount": 34000})

    store.update_expense("b1", rent_id, clean_expense(
        category="variable", name="ค่าไฟ", amount=3400, date="2026-08-05"))

    saved = store.get_expense("b1", rent_id)
    check("the amount is fixed", saved["amount"], 3400)
    check("and nothing else moved", saved["name"], "ค่าไฟ")
    check("still one entry, not two", len(store.list_expenses("b1")), 1)


def test_moving_an_entry_between_categories_leaves_nothing_behind():
    section("An entry moved to another category leaves no copy behind")
    # Each category is fetched with its own query, so a move that wrote
    # the new one without clearing the old would show the same spend
    # twice and inflate the month's total.
    store, (eid,) = store_with(ELEC)

    store.update_expense("b1", eid, clean_expense(
        category="fixed", name="ค่าไฟ", amount=3400, date="2026-08-05"))

    check("gone from the old category", store.list_expenses("b1", "variable"), [])
    check("present in the new one", len(store.list_expenses("b1", "fixed")), 1)
    check("counted once in total", len(store.list_expenses("b1")), 1)


def test_a_correction_cannot_write_fields_of_its_own():
    section("A correction writes the four fields of an expense, nothing else")
    # This takes a request body. Passing the whole body through is how a
    # field nobody meant to expose becomes editable from the browser.
    store, (eid,) = store_with(RENT)

    store.update_expense("b1", eid, {
        "amount": 9000, "tenant_id": "someone-else", "id": "hijacked",
    })

    saved = store.get_expense("b1", eid)
    check("the real field was written", saved["amount"], 9000)
    check("the invented one was not", "tenant_id" in saved, False)
    check("and the id is still its own", saved["id"], eid)


def test_deleting_removes_it_from_the_books():
    section("A deleted expense is gone from the list and the totals")
    store, (rent_id, elec_id) = store_with(RENT, ELEC)
    check("both there to begin with", len(store.list_expenses("b1")), 2)

    store.delete_expense("b1", rent_id)

    remaining = store.list_expenses("b1")
    check("one left", len(remaining), 1)
    check("and it is the right one", remaining[0]["name"], "ค่าไฟ")
    check("the deleted one is unreadable", store.get_expense("b1", rent_id), None)
    check("its category is empty now", store.list_expenses("b1", "fixed"), [])


def test_branches_do_not_share_expenses():
    section("One branch's expenses are not another's")
    store, (eid,) = store_with(RENT)
    check("the other branch has none", store.list_expenses("b2"), [])
    check("and cannot read this one", store.get_expense("b2", eid), None)


def test_the_shapes_that_are_refused():
    section("What recording - and correcting - will not accept")
    refuses("no name", name="")
    refuses("name of only spaces", name="   ")
    refuses("zero baht", amount=0)
    # Not a way to cancel an earlier entry: that leaves both rows in the
    # list forever. Deleting the wrong one is how to undo it.
    refuses("negative amount", amount=-500)
    refuses("amount that isn't a number", amount="สามพัน")
    refuses("amount missing", amount=None)
    refuses("no date", date="")
    refuses("unknown category", category="อื่น ๆ")
    refuses("category missing", category=None)


def test_what_is_accepted_comes_back_tidy():
    section("Accepted input is normalised on the way in")
    e = clean_expense(category="fixed", name="  ค่าเช่า  ", amount="12000",
                      date=" 2026-08-01 ")
    check("name trimmed", e["name"], "ค่าเช่า")
    check("date trimmed", e["date"], "2026-08-01")
    check("amount is a number, not text", e["amount"], 12000.0)


def test_a_material_entry_can_still_be_corrected():
    section("An old ค่าวัตถุดิบ entry can still be corrected")
    # Material cost is computed from deliveries now, so it can't be
    # recorded by hand any more. Entries made before that rule still
    # exist, and refusing the category outright would make them
    # uneditable - or worse, silently move them somewhere else.
    store, (eid,) = store_with({"category": "material", "name": "ซื้อกุ้ง",
                                "amount": 800, "date": "2026-07-02"})

    store.update_expense("b1", eid, clean_expense(
        category="material", name="ซื้อกุ้ง", amount=850, date="2026-07-02"))

    saved = store.get_expense("b1", eid)
    check("corrected in place", saved["amount"], 850)
    check("still in its own category", saved["category"], "material")


def main():
    print("Running expense tests (offline)")

    test_a_wrong_amount_can_be_corrected()
    test_moving_an_entry_between_categories_leaves_nothing_behind()
    test_a_correction_cannot_write_fields_of_its_own()
    test_deleting_removes_it_from_the_books()
    test_branches_do_not_share_expenses()
    test_the_shapes_that_are_refused()
    test_what_is_accepted_comes_back_tidy()
    test_a_material_entry_can_still_be_corrected()

    passed = sum(1 for r in _results if r)
    total = len(_results)
    print(f"\n{'=' * 50}")
    print(f"{passed}/{total} checks passed")
    if passed != total:
        print("SOME CHECKS FAILED")
        sys.exit(1)
    print("All good.")


if __name__ == "__main__":
    main()
