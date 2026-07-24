"""
Tests for the V2 stock system. Run with:

    cd backend
    python tests/test_stock.py

No Firebase, emulator, or Loyverse connection needed - everything runs
against an in-memory fake. Each test prints PASS/FAIL and the script
exits non-zero if anything failed, so it also works in CI later.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store
from storage.movement_ledger import MovementLedger
from core.stock_engine import sync_and_deduct

STORE_ID = "test-store"

_results = []


def check(label, actual, expected, tolerance=0.001):
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        ok = abs(actual - expected) < tolerance
    else:
        ok = actual == expected
    _results.append(ok)
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------- ledger

def test_basic_movements():
    section("Stock is the sum of its movements")
    store = make_test_store()
    ledger = MovementLedger(store)

    ledger.record_receive(STORE_ID, "shrimp", 5, 200)
    check("after receiving 5", ledger.current_stock(STORE_ID, "shrimp"), 5)

    ledger.record_sale(STORE_ID, "shrimp", 1.5)
    check("after selling 1.5", ledger.current_stock(STORE_ID, "shrimp"), 3.5)

    ledger.record_waste(STORE_ID, "shrimp", 0.5, note="ของเสีย")
    check("after 0.5 waste", ledger.current_stock(STORE_ID, "shrimp"), 3.0)


def test_physical_count_stores_delta():
    section("A physical count corrects the total without erasing history")
    store = make_test_store()
    ledger = MovementLedger(store)

    ledger.record_receive(STORE_ID, "rice", 10, 28)
    ledger.record_sale(STORE_ID, "rice", 2)
    # ledger thinks 8, but only 7.5 is actually on the shelf
    ledger.record_count(STORE_ID, "rice", 7.5)

    check("stock matches the counted number", ledger.current_stock(STORE_ID, "rice"), 7.5)
    movements = ledger.list_movements(STORE_ID, "rice")
    check("all three movements kept", len(movements), 3)
    count_move = [m for m in movements if m["kind"] == "count"][0]
    check("count stored as a -0.5 delta", count_move["quantity"], -0.5)


def test_negative_stock_detection():
    section("Overselling shows up as negative stock")
    store = make_test_store()
    ledger = MovementLedger(store)

    ledger.record_receive(STORE_ID, "squid", 2, 180)
    ledger.record_sale(STORE_ID, "squid", 3)  # sold more than we had

    stock = ledger.current_stock(STORE_ID, "squid")
    check("stock went negative", stock, -1)
    check("negative is detectable", stock < 0, True)


# ------------------------------------------------------------------ cost

def test_weighted_average_cost():
    section("Average cost is weighted by quantity, not a plain average")
    store = make_test_store()
    ledger = MovementLedger(store)

    ledger.record_receive(STORE_ID, "shrimp", 5, 200, occurred_at="2026-07-01T09:00:00+00:00")
    ledger.record_receive(STORE_ID, "shrimp", 3, 260, occurred_at="2026-07-15T09:00:00+00:00")

    expected = (5 * 200 + 3 * 260) / 8  # 222.50, not (200+260)/2 = 230
    check("weighted average", ledger.average_cost(STORE_ID, "shrimp"), expected)


def test_monthly_cost_isolation():
    section("Each month keeps its own cost - this is the bug V2 fixes")
    store = make_test_store()
    ledger = MovementLedger(store)

    ledger.record_receive(STORE_ID, "shrimp", 10, 200, occurred_at="2026-06-05T09:00:00+00:00")
    ledger.record_receive(STORE_ID, "shrimp", 10, 300, occurred_at="2026-07-05T09:00:00+00:00")

    check("June uses June's price", ledger.average_cost(STORE_ID, "shrimp", 2026, 6), 200)
    check("July uses July's price", ledger.average_cost(STORE_ID, "shrimp", 2026, 7), 300)
    check("August (no delivery) carries the latest forward",
          ledger.average_cost(STORE_ID, "shrimp", 2026, 8), 300)


def test_cost_history():
    section("Cost history lists every delivery with its price")
    store = make_test_store()
    ledger = MovementLedger(store)

    ledger.record_receive(STORE_ID, "shrimp", 5, 200, occurred_at="2026-06-01T09:00:00+00:00")
    ledger.record_receive(STORE_ID, "shrimp", 5, 240, occurred_at="2026-07-01T09:00:00+00:00")
    ledger.record_sale(STORE_ID, "shrimp", 2)  # sales must not appear in cost history

    history = ledger.cost_history(STORE_ID, "shrimp")
    check("only deliveries listed", len(history), 2)
    check("newest first", history[0]["unit_cost"], 240)


# ------------------------------------------------------------- receiving

def test_receiving_updates_stock_and_cost_together():
    section("One receiving entry updates stock AND cost - no separate step")
    store = make_test_store()

    store.upsert_material(STORE_ID, "shrimp", {"name": "กุ้ง", "unit": "กก.", "par": 5, "cost": 0})
    store.add_receiving(STORE_ID, "Makro", "2026-07-20T09:00:00+00:00", [
        {"material_id": "shrimp", "quantity": 6, "unit_cost": 260},
    ])

    material = store.list_materials(STORE_ID)[0]
    check("stock rose from the delivery", material["stock"], 6)
    check("cost came from the delivery price", material["cost"], 260)
    check("delivery is on record", len(store.list_receivings(STORE_ID)), 1)


def test_receiving_multiple_lines():
    section("A delivery with several lines updates each material")
    store = make_test_store()

    store.upsert_material(STORE_ID, "shrimp", {"name": "กุ้ง", "unit": "กก.", "par": 5})
    store.upsert_material(STORE_ID, "rice", {"name": "ข้าวสาร", "unit": "กก.", "par": 10})

    store.add_receiving(STORE_ID, "Makro", "2026-07-20T09:00:00+00:00", [
        {"material_id": "shrimp", "quantity": 4, "unit_cost": 250},
        {"material_id": "rice", "quantity": 20, "unit_cost": 30},
    ])

    by_id = {m["id"]: m for m in store.list_materials(STORE_ID)}
    check("shrimp stock", by_id["shrimp"]["stock"], 4)
    check("rice stock", by_id["rice"]["stock"], 20)
    receiving = store.list_receivings(STORE_ID)[0]
    check("delivery total", receiving["total"], 4 * 250 + 20 * 30)


# ------------------------------------------------------------- migration

def test_migration_preserves_old_stock():
    section("Migrating pre-V2 data doesn't lose the stock that was there")
    store = make_test_store()

    # simulate a material saved by the old version, with stock on the doc
    store._col(STORE_ID, "materials").document("shrimp").set({
        "name": "กุ้ง", "unit": "กก.", "stock": 4, "cost": 200, "par": 5,
    })

    migrated = store.migrate_stock_to_ledger(STORE_ID)
    check("one material migrated", migrated, 1)

    material = store.list_materials(STORE_ID)[0]
    check("stock survived the move", material["stock"], 4)

    # running it twice must not double the stock
    store.migrate_stock_to_ledger(STORE_ID)
    material = store.list_materials(STORE_ID)[0]
    check("re-running migration is safe", material["stock"], 4)


# ---------------------------------------------------- recipe integration

class FakeProvider:
    """Stands in for the Loyverse adapter - returns one fixed sale."""

    def __init__(self, receipts):
        self._receipts = receipts

    def get_receipts(self, store_id, created_at_min=None):
        return self._receipts


def test_sales_deduct_via_recipe():
    section("Selling a dish deducts its ingredients automatically")
    store = make_test_store()

    store.upsert_material(STORE_ID, "shrimp", {"name": "กุ้ง", "unit": "กรัม", "par": 1000})
    store.add_receiving(STORE_ID, "Makro", "2026-07-20T09:00:00+00:00", [
        {"material_id": "shrimp", "quantity": 1000, "unit_cost": 0.25},
    ])
    store.set_recipe(STORE_ID, "ต้มยำกุ้ง", [{"material_id": "shrimp", "qty": 150}])

    provider = FakeProvider([{
        "receipt_number": "0001",
        "store_id": STORE_ID,
        "created_at": "2026-07-20T12:00:00+00:00",
        "total": 180,
        "line_items": [{"item_name": "ต้มยำกุ้ง", "quantity": 2, "price": 90}],
    }])

    processed = sync_and_deduct(provider, store, STORE_ID)
    check("one receipt processed", processed, 1)

    material = store.list_materials(STORE_ID)[0]
    check("2 dishes used 300g", material["stock"], 700)

    # syncing again must not deduct the same receipt twice
    processed_again = sync_and_deduct(provider, store, STORE_ID)
    check("re-sync skips processed receipts", processed_again, 0)
    check("stock unchanged after re-sync", store.list_materials(STORE_ID)[0]["stock"], 700)


def test_recipe_without_material_is_harmless():
    section("A recipe pointing at a deleted material doesn't crash the sync")
    store = make_test_store()
    store.set_recipe(STORE_ID, "เมนูผี", [{"material_id": "ghost", "qty": 10}])

    provider = FakeProvider([{
        "receipt_number": "0009",
        "store_id": STORE_ID,
        "created_at": "2026-07-20T12:00:00+00:00",
        "total": 50,
        "line_items": [{"item_name": "เมนูผี", "quantity": 1, "price": 50}],
    }])

    processed = sync_and_deduct(provider, store, STORE_ID)
    check("sync completed anyway", processed, 1)


def main():
    print("Running V2 stock tests (in-memory, no Firebase needed)")

    test_basic_movements()
    test_physical_count_stores_delta()
    test_negative_stock_detection()
    test_weighted_average_cost()
    test_monthly_cost_isolation()
    test_cost_history()
    test_receiving_updates_stock_and_cost_together()
    test_receiving_multiple_lines()
    test_migration_preserves_old_stock()
    test_sales_deduct_via_recipe()
    test_recipe_without_material_is_harmless()

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
