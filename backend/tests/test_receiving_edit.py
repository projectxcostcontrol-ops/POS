"""
Tests for correcting or removing a delivery of ingredients.

A receiving moves two things at once: stock onto the shelf, and the price
paid into the cost history every profit figure is built on. Both are hard
to notice afterwards - a quantity typed with an extra zero looks like a
good month right up until someone counts the shelf - and until now
neither could be undone. The delivery was recorded and that was that.

What has to hold:

  the ledger follows   - a delivery IS its movements as far as stock and
                         cost are concerned, so editing the paperwork
                         without editing the ledger would leave two
                         answers to one question, and every report reads
                         the wrong one

  cost history follows - taking a delivery back has to take its price out
                         of the weighted average too, or a corrected
                         mistake keeps pulling costs around forever

  a count wins         - a physical count is the one number here that was
                         measured rather than derived; stock must not be
                         withdrawn from under it

Offline, in-memory. Run with:

    cd backend
    python tests/test_receiving_edit.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store, FakeDb
from storage.movement_ledger import MovementLedger
from core.receiving import clean_receiving, ReceivingError

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def check_close(label, actual, expected, tol=1e-9):
    ok = actual is not None and abs(actual - expected) < tol
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def refuses(label, **kwargs):
    args = {"supplier": "ร้านเจ๊หมวย", "date": "2026-08-20",
            "items": [{"material_id": "duck", "quantity": 5, "unit_cost": 200}], **kwargs}
    try:
        clean_receiving(**args)
        check(label, "accepted", "refused")
    except ReceivingError as e:
        _results.append(True)
        print(f"  [PASS] {label}: refused - {e}")


def section(title):
    print(f"\n=== {title} ===")


def kitchen():
    db = FakeDb()
    store = make_test_store(db=db)
    store.upsert_material("b1", "duck", {"name": "เป็ด", "unit": "kg"})
    return db, store, MovementLedger(store)


def replace(store, ledger, rid, data):
    """What the update endpoint does: old movements out, new ones in."""
    r = clean_receiving(**data)
    ledger.delete_by_ref("b1", rid)
    store.replace_receiving("b1", rid, r)
    store.add_receiving_movements("b1", rid, r["supplier"], r["date"], r["items"])
    return r


def test_correcting_a_quantity_moves_the_shelf_with_it():
    section("Correcting a quantity corrects the shelf, not just the paperwork")
    db, store, ledger = kitchen()
    # 50kg typed where 5 was meant - an extra zero.
    rid = store.add_receiving("b1", "ร้านเจ๊หมวย", "2026-08-20",
                              [{"material_id": "duck", "quantity": 50, "unit_cost": 200}])["id"]
    check_close("shelf believes 50", ledger.current_stock("b1", "duck"), 50)

    replace(store, ledger, rid, {
        "supplier": "ร้านเจ๊หมวย", "date": "2026-08-20",
        "items": [{"material_id": "duck", "quantity": 5, "unit_cost": 200}]})

    check_close("shelf now says 5", ledger.current_stock("b1", "duck"), 5)
    check("the document agrees", store.get_receiving("b1", rid)["total"], 1000)
    check("one delivery, not two", len(store.list_receivings("b1")), 1)


def test_correcting_a_price_corrects_the_average_cost():
    section("Correcting a price corrects the cost history behind it")
    # Average cost feeds every profit figure. A price left wrong in the
    # ledger keeps pulling it long after the paperwork was fixed.
    db, store, ledger = kitchen()
    store.add_receiving("b1", "เจ้าประจำ", "2026-08-01",
                        [{"material_id": "duck", "quantity": 10, "unit_cost": 200}])
    rid = store.add_receiving("b1", "เจ้าใหม่", "2026-08-20",
                              [{"material_id": "duck", "quantity": 10, "unit_cost": 900}])["id"]
    check_close("average dragged up by the typo",
                ledger.average_cost("b1", "duck"), (10 * 200 + 10 * 900) / 20)

    replace(store, ledger, rid, {
        "supplier": "เจ้าใหม่", "date": "2026-08-20",
        "items": [{"material_id": "duck", "quantity": 10, "unit_cost": 220}]})

    check_close("average is back to what was really paid",
                ledger.average_cost("b1", "duck"), (10 * 200 + 10 * 220) / 20)
    check_close("and the shelf never moved", ledger.current_stock("b1", "duck"), 20)


def test_deleting_takes_the_stock_and_the_price_back():
    section("Deleting a delivery removes its stock and its price")
    db, store, ledger = kitchen()
    store.add_receiving("b1", "เจ้าประจำ", "2026-08-01",
                        [{"material_id": "duck", "quantity": 10, "unit_cost": 200}])
    rid = store.add_receiving("b1", "ส่งผิดร้าน", "2026-08-20",
                              [{"material_id": "duck", "quantity": 8, "unit_cost": 500}])["id"]

    removed = ledger.delete_by_ref("b1", rid)
    store.delete_receiving("b1", rid)

    check("one line reverted", removed, 1)
    check_close("stock back to the first delivery", ledger.current_stock("b1", "duck"), 10)
    check_close("average cost back to 200", ledger.average_cost("b1", "duck"), 200)
    check("the delivery is gone", store.get_receiving("b1", rid), None)
    # The snapshot on the material doc is what the stock page reads, so it
    # has to agree with the ledger after the reversal, not only before.
    snap = store.material_snapshot("b1", "duck")
    check_close("the fast figure agrees", snap["stock_qty"], 10)
    check_close("...and so does its cost half", snap["recv_value"] / snap["recv_qty"], 200)


def test_a_multi_line_delivery_reverts_every_line():
    section("Every line of a delivery comes back, not just the first")
    db, store, ledger = kitchen()
    store.upsert_material("b1", "rice", {"name": "ข้าวสาร", "unit": "kg"})
    rid = store.add_receiving("b1", "เจ๊หมวย", "2026-08-20", [
        {"material_id": "duck", "quantity": 5, "unit_cost": 200},
        {"material_id": "rice", "quantity": 20, "unit_cost": 25},
    ])["id"]

    removed = ledger.delete_by_ref("b1", rid)
    store.delete_receiving("b1", rid)

    check("both lines reverted", removed, 2)
    check_close("duck back to nothing", ledger.current_stock("b1", "duck"), 0)
    check_close("rice back to nothing", ledger.current_stock("b1", "rice"), 0)


def test_other_deliveries_are_untouched():
    section("Correcting one delivery leaves the others alone")
    db, store, ledger = kitchen()
    keep = store.add_receiving("b1", "เจ้าประจำ", "2026-08-01",
                               [{"material_id": "duck", "quantity": 10, "unit_cost": 200}])["id"]
    rid = store.add_receiving("b1", "เจ้าใหม่", "2026-08-20",
                              [{"material_id": "duck", "quantity": 5, "unit_cost": 300}])["id"]

    ledger.delete_by_ref("b1", rid)
    store.delete_receiving("b1", rid)

    check("the other delivery is still there",
          store.get_receiving("b1", keep)["total"], 2000)
    check_close("and its stock is still counted", ledger.current_stock("b1", "duck"), 10)


def test_the_shapes_that_are_refused():
    section("What recording - and correcting - a delivery will not accept")
    refuses("no date", date="")
    refuses("no lines", items=[])
    refuses("a line with no material", items=[{"material_id": "", "quantity": 1, "unit_cost": 5}])
    refuses("zero quantity", items=[{"material_id": "duck", "quantity": 0, "unit_cost": 5}])
    refuses("negative quantity", items=[{"material_id": "duck", "quantity": -5, "unit_cost": 5}])
    # A delivery that pays the shop is a return, not a receiving with a
    # minus sign - which would drag average cost below anything paid.
    refuses("negative unit cost", items=[{"material_id": "duck", "quantity": 5, "unit_cost": -1}])
    refuses("quantity that isn't a number",
            items=[{"material_id": "duck", "quantity": "ห้า", "unit_cost": 5}])

    # Free samples and replacements arrive at no charge and still land on
    # the shelf, so zero is allowed on price.
    free = clean_receiving(supplier="", date="2026-08-20",
                           items=[{"material_id": "duck", "quantity": 2, "unit_cost": 0}])
    check("a free delivery is allowed", free["total"], 0)
    check("supplier may be blank", free["supplier"], "")


def main():
    print("Running receiving edit/delete tests (offline)")

    test_correcting_a_quantity_moves_the_shelf_with_it()
    test_correcting_a_price_corrects_the_average_cost()
    test_deleting_takes_the_stock_and_the_price_back()
    test_a_multi_line_delivery_reverts_every_line()
    test_other_deliveries_are_untouched()
    test_the_shapes_that_are_refused()

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
