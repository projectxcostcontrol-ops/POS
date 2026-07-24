"""
Automatic stock deduction from sales, per the earlier decision: the
POS provider only tells us what sold (receipts); recipes and stock are
entirely ours. This is the piece that makes stock updates require zero
manual entry for the sales path - only restocking and physical counts
are ever typed in by a person.
"""

from core.pos_provider import PosProvider
from storage.firestore_store import Store


def sync_and_deduct(provider: PosProvider, store: Store, store_id: str,
                     created_at_min: str | None = None) -> int:
    """Pull new receipts and deduct recipe ingredients for each one sold.
    Returns the number of receipts processed. Safe to call repeatedly -
    already-processed receipts are skipped."""
    receipts = provider.get_receipts(store_id, created_at_min=created_at_min)
    processed_count = 0

    for receipt in receipts:
        number = receipt["receipt_number"]
        if not number or store.is_receipt_processed(store_id, number):
            continue

        for line in receipt["line_items"]:
            recipe = store.get_recipe(store_id, line["item_name"])
            for ingredient in recipe:
                amount_used = ingredient["qty"] * line["quantity"]
                store.deduct_stock(store_id, ingredient["material_id"], amount_used,
                                   ref=f"receipt:{number}")

        store.mark_receipt_processed(store_id, number)
        processed_count += 1

    return processed_count
