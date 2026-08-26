"""
Tests for sales the till never saw.

A shop selling through Grab keeps those orders in a paper notebook. The
ingredients leave the kitchen and nothing records it, so every delivery
order makes the variance report slightly more wrong while looking
exactly as confident as it did before. After a month, "ของหาย 8 กิโล"
means nothing at all - nobody can tell theft from thirty Grab orders.

Recording them has to satisfy three things at once:

  they are ordinary sales   - same collection, same shape, so every
                              report picks them up with no changes

  they deduct identically   - a Grab order and a walk-in for the same
                              dish take the same ingredients

  the POS check stays clean - reconcile compares the POS against what we
                              saved, and a sale the POS never had must
                              not read as one it lost

Offline, in-memory. Run with:

    cd backend
    python tests/test_delivery_orders.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store, FakeDb
from storage.movement_ledger import MovementLedger
from core.delivery import clean_order, is_pos_sale, source_of, DeliveryError
from core.stock_engine import deductions_for
from core import sales_report

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def check_close(label, actual, expected, tol=1e-9):
    ok = abs(actual - expected) < tol
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def refuses(label, **kwargs):
    args = {"order_id": "grab-1", "source": "grab", "date": "2026-08-26T12:00:00.000Z",
            "items": [{"name": "ข้าวหน้าเป็ด", "qty": 1, "price": 75}], **kwargs}
    try:
        clean_order(**args)
        check(label, "accepted", "refused")
    except DeliveryError as e:
        _results.append(True)
        print(f"  [PASS] {label}: refused - {e}")


def section(title):
    print(f"\n=== {title} ===")


def kitchen():
    """A branch that sells one dish made of two ingredients."""
    db = FakeDb()
    store = make_test_store(db=db)
    ledger = MovementLedger(store)
    store.upsert_material("b1", "duck", {"name": "เป็ด", "unit": "kg"})
    store.upsert_material("b1", "rice", {"name": "ข้าวสาร", "unit": "kg"})
    ledger.record_receive("b1", "duck", 10, unit_cost=200)
    ledger.record_receive("b1", "rice", 20, unit_cost=25)
    store.set_recipe("b1", "ข้าวหน้าเป็ด", [
        {"material_id": "duck", "qty": 0.2}, {"material_id": "rice", "qty": 0.3}])
    return db, store, ledger


def record(store, row):
    """Exactly what the endpoint does, in the same order: the sale is
    saved before stock moves, so a failure in between leaves an order
    with no deduction rather than ingredients gone with no record."""
    store.save_sale("b1", row["receipt_number"], row)
    rows, unknown = deductions_for(
        store.all_recipes("b1"), set(store.list_material_ids("b1")),
        [(i["name"], i["qty"]) for i in row["items"]],
        ref=f"receipt:{row['receipt_number']}")
    store.deduct_stock_bulk("b1", rows)
    return unknown


GRAB = dict(order_id="grab-001", source="grab", date="2026-08-26T12:00:00.000Z",
            items=[{"name": "ข้าวหน้าเป็ด", "qty": 3, "price": 85}])


def test_a_grab_order_takes_the_same_ingredients_as_a_walk_in():
    section("A Grab order deducts exactly what the recipe says")
    db, store, ledger = kitchen()

    record(store, clean_order(**GRAB))

    check_close("duck went down by 3 x 200g", ledger.current_stock("b1", "duck"), 10 - 0.6)
    check_close("rice went down by 3 x 300g", ledger.current_stock("b1", "rice"), 20 - 0.9)


def test_the_platform_price_is_what_gets_recorded():
    section("The total is what the customer paid on the platform")
    # Grab's menu price is marked up from the shop's own. The shop's
    # price is not the money that arrived, so it is not what is recorded;
    # the platform's cut is a separate cost and goes in รายรับรายจ่าย.
    row = clean_order(**GRAB)
    check("3 x 85", row["total"], 255)
    check("marked as a Grab sale", row["source"], "grab")
    check("and not as a POS sale", is_pos_sale(row), False)


def test_it_shows_up_in_the_sales_reports_untouched():
    section("A recorded order counts in the sales figures like any other")
    # The whole reason these are saved as ordinary sales: every screen
    # reads list_sales, so nothing had to be changed for them to appear.
    db, store, ledger = kitchen()
    record(store, clean_order(**GRAB))
    record(store, clean_order(**{**GRAB, "order_id": "phone-002", "source": "phone",
                                 "items": [{"name": "ข้าวหน้าเป็ด", "qty": 1, "price": 60}]}))

    sales = store.list_sales("b1")
    summary = sales_report.summarise(sales, store.all_recipes("b1"),
                                     store.list_materials("b1"), "day", 420)

    check("both orders counted", summary["bill_count"], 2)
    check("takings add up", summary["total"], 315)

    # And their ingredients are costed, which is what makes the profit
    # figure on รายรับรายจ่าย true. Four dishes: 4 x (0.2kg duck @ 200 +
    # 0.3kg rice @ 25) = 4 x 47.5.
    check("ingredients are costed too", summary["ingredient_cost"], 190)
    check("so profit is takings minus that", summary["gross_profit"], 125)
    check("nothing went uncosted", summary["uncosted_menus"], [])


def test_a_sale_the_pos_never_had_is_not_reported_as_missing():
    section("The POS reconcile check ignores orders the POS never had")
    # reconcile compares what the POS reports against what we saved and
    # lists the difference. Counting a Grab order there would report it
    # missing forever, and the home screen's "อัปเดตข้อมูล" button would
    # try to repair it on every press.
    db, store, ledger = kitchen()
    record(store, clean_order(**GRAB))
    store.save_sale("b1", "1-1001", {"receipt_number": "1-1001", "source": "loyverse",
                                     "date": "2026-08-26T11:00:00.000Z", "total": 60,
                                     "items": []})
    # A row written before `source` existed at all.
    store.save_sale("b1", "1-1000", {"receipt_number": "1-1000",
                                     "date": "2026-08-26T10:00:00.000Z", "total": 60,
                                     "items": []})

    saved = store.list_sales("b1")
    pos_only = [s for s in saved if is_pos_sale(s)]

    check("three sales in total", len(saved), 3)
    check("two of them are the POS's", len(pos_only), 2)
    check("an old row with no source counts as the POS's", source_of({}), "loyverse")


def test_deleting_an_order_puts_the_ingredients_back():
    section("Deleting a mistyped order returns exactly what it took")
    db, store, ledger = kitchen()
    before_duck = ledger.current_stock("b1", "duck")
    record(store, clean_order(**GRAB))
    check_close("stock moved", ledger.current_stock("b1", "duck"), before_duck - 0.6)

    returned = ledger.delete_by_ref("b1", "receipt:grab-001")
    store.delete_sale("b1", "grab-001")

    check("both ingredients returned", returned, 2)
    check_close("duck is back where it was", ledger.current_stock("b1", "duck"), before_duck)
    check_close("rice is back too", ledger.current_stock("b1", "rice"), 20)
    check("the order is gone from the books", store.get_sale("b1", "grab-001"), None)
    # The snapshot on the material doc is what the stock page reads, so
    # it has to agree with the ledger after a delete, not just before.
    snap = store.material_snapshot("b1", "duck")
    check_close("the fast figure agrees with the ledger", snap["stock_qty"], before_duck)


def test_deleting_leaves_other_orders_alone():
    section("Deleting one order does not touch another")
    db, store, ledger = kitchen()
    record(store, clean_order(**GRAB))
    record(store, clean_order(**{**GRAB, "order_id": "grab-002"}))

    ledger.delete_by_ref("b1", "receipt:grab-001")
    store.delete_sale("b1", "grab-001")

    check_close("only one order's worth came back",
                ledger.current_stock("b1", "duck"), 10 - 0.6)
    check("the other order is still recorded",
          store.get_sale("b1", "grab-002")["total"], 255)


def test_a_dish_with_no_recipe_records_but_deducts_nothing():
    section("A dish with no recipe is recorded, and says it deducted nothing")
    # Drinks and resale goods often have no recipe. Not an error - but
    # worth reporting, because "stock didn't move" looks like a bug.
    db, store, ledger = kitchen()
    row = clean_order(order_id="grab-003", source="grab",
                      date="2026-08-26T12:00:00.000Z",
                      items=[{"name": "น้ำเปล่า", "qty": 2, "price": 15}])
    record(store, row)

    check("the sale is recorded", store.get_sale("b1", "grab-003")["total"], 30)
    check_close("nothing came off the shelf", ledger.current_stock("b1", "duck"), 10)


def test_a_recipe_naming_a_deleted_ingredient_is_reported():
    section("A recipe pointing at a deleted ingredient is named, not swallowed")
    db, store, ledger = kitchen()
    store.set_recipe("b1", "ข้าวหน้าเป็ด", [
        {"material_id": "duck", "qty": 0.2}, {"material_id": "gone", "qty": 0.1}])

    unknown = record(store, clean_order(**GRAB))

    check("the missing one is named", sorted(unknown), ["gone"])
    check_close("the real one still deducted", ledger.current_stock("b1", "duck"), 10 - 0.6)


def test_the_shapes_that_are_refused():
    section("What recording an order will not accept")
    refuses("no order id", order_id="")
    refuses("an id that can't be a document", order_id="grab/001")
    refuses("no channel", source=None)
    refuses("a channel we don't know", source="ubereats")
    # "loyverse" is not a channel anyone may record by hand: it would put
    # a sale the POS never had into the POS's own reconcile check.
    refuses("pretending to be the POS", source="loyverse")
    refuses("no date", date="")
    refuses("no items", items=[])
    refuses("an item with no name", items=[{"name": "", "qty": 1, "price": 10}])
    refuses("zero quantity", items=[{"name": "ข้าว", "qty": 0, "price": 10}])
    refuses("negative quantity", items=[{"name": "ข้าว", "qty": -1, "price": 10}])
    refuses("negative price", items=[{"name": "ข้าว", "qty": 1, "price": -10}])
    refuses("quantity that isn't a number", items=[{"name": "ข้าว", "qty": "สอง", "price": 10}])

    # Free items happen - a promotion, a replacement for a wrong order -
    # and they still consume ingredients, so zero is allowed on price.
    free = clean_order(order_id="grab-free", source="grab",
                       date="2026-08-26T12:00:00.000Z",
                       items=[{"name": "ข้าว", "qty": 1, "price": 0}])
    check("a free item is allowed", free["total"], 0)


def main():
    print("Running delivery order tests (offline)")

    test_a_grab_order_takes_the_same_ingredients_as_a_walk_in()
    test_the_platform_price_is_what_gets_recorded()
    test_it_shows_up_in_the_sales_reports_untouched()
    test_a_sale_the_pos_never_had_is_not_reported_as_missing()
    test_deleting_an_order_puts_the_ingredients_back()
    test_deleting_leaves_other_orders_alone()
    test_a_dish_with_no_recipe_records_but_deducts_nothing()
    test_a_recipe_naming_a_deleted_ingredient_is_reported()
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
