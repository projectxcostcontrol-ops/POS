"""
Automatic stock deduction from sales, per the earlier decision: the
POS provider only tells us what sold (receipts); recipes and stock are
entirely ours. This is the piece that makes stock updates require zero
manual entry for the sales path - only restocking and physical counts
are ever typed in by a person.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from core.pos_provider import PosProvider
from storage.firestore_store import Store

# A newly connected branch never re-pulls its whole sales history - see
# sync_branch() below. Every advance of the cursor keeps a few minutes of
# overlap with the previous sync, guarding against a receipt whose
# timestamp lands slightly behind when it was actually fetched (clock
# skew between us and Loyverse, or a receipt written a moment late). The
# cost of the overlap is re-checking a few already-processed receipts
# next time, which is_receipt_processed skips cheaply; the alternative -
# an exact boundary - can silently drop a receipt with no sign anything
# went wrong.
SYNC_OVERLAP_SECONDS = 300


def sync_and_deduct(provider: PosProvider, store: Store, store_id: str,
                     created_at_min: str | None = None) -> int:
    """Pull new receipts, deduct recipe ingredients, and keep our own copy
    of what sold. Returns the number of receipts processed. Safe to call
    repeatedly - already-processed receipts are skipped."""
    receipts = provider.get_receipts(store_id, created_at_min=created_at_min)
    processed_count = 0

    for receipt in receipts:
        number = receipt["receipt_number"]
        if not number:
            continue

        # Saving the sale happens BEFORE the processed check, because the
        # two answer different questions. "Processed" means stock was
        # already deducted and must not be deducted twice; the sales copy
        # is just a record, and re-saving it overwrites harmlessly. Putting
        # the save after the check would silently skip every receipt in
        # the overlap window and leave gaps in the history.
        _save_sale(store, store_id, receipt)

        if store.is_receipt_processed(store_id, number):
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


def _save_sale(store: Store, store_id: str, receipt: dict):
    """Keep the fields the reports actually need, not the whole Loyverse
    payload - line items with names and quantities, the total, and when.
    Everything else can be re-fetched from Loyverse while it's still
    within their window, and is dead weight afterwards."""
    number = receipt.get("receipt_number")
    if not number:
        return
    store.save_sale(store_id, number, {
        "receipt_number": number,
        "date": receipt.get("created_at") or "",
        "total": receipt.get("total") or 0,
        "items": [
            {
                "name": li.get("item_name") or "",
                "qty": li.get("quantity") or 0,
                "price": li.get("price") or 0,
            }
            for li in receipt.get("line_items", [])
        ],
    })


def backfill_sales(provider: PosProvider, store: Store, store_id: str) -> int:
    """Pull whatever history the POS plan still allows and save a copy, run
    once when a branch first connects.

    Without this, a business that connects today starts with an empty
    sales history and has to wait a month before any monthly view means
    anything - while the previous month's data was sitting in Loyverse,
    reachable, right up until it aged out. This grabs it while it's there.

    Deliberately does NOT deduct stock: those sales happened before the
    branch had recipes, and deducting them now would drive stock negative
    against ingredients that were never tracked."""
    if store.has_backfilled_sales(store_id):
        return 0

    receipts = provider.get_receipts(store_id)
    for receipt in receipts:
        _save_sale(store, store_id, receipt)
        # Mark as processed so the first real sync doesn't deduct stock for
        # sales that happened before this branch was even set up.
        number = receipt.get("receipt_number")
        if number:
            store.mark_receipt_processed(store_id, number)

    store.mark_sales_backfilled(store_id, _utcnow_iso())
    return len(receipts)


def sync_branch(provider: PosProvider, store: Store, store_id: str,
                overlap_seconds: int = SYNC_OVERLAP_SECONDS) -> int:
    """The entry point both the manual "ซิงก์ตอนนี้" button and the
    background loop use. Wraps sync_and_deduct with a saved cursor so a
    sync only ever asks Loyverse for receipts since last time, never a
    branch's entire history.

    The first call for a branch is special: it has no cursor yet, so
    rather than treat "no cursor" as "fetch everything" (which is exactly
    the full-history pull this function exists to avoid), it establishes
    the cursor at this instant and fetches nothing. That's also the
    correct business behaviour, not just a performance shortcut - a
    receipt from before this branch had recipes or tracked stock has
    nothing to deduct against, so there was never anything useful to
    fetch from before the branch connected."""
    cursor = store.get_sync_cursor(store_id)
    now = _utcnow_iso()

    if cursor is None:
        # Grab whatever history the POS still has before starting the
        # cursor - see backfill_sales. Failing here must not block the
        # branch from syncing going forward, so it's best-effort.
        try:
            backfill_sales(provider, store, store_id)
        except Exception as e:
            print(f"[sync] backfill skipped for {store_id}: {e}")
        store.set_sync_cursor(store_id, now)
        return 0

    # Normalize on READ, not just on write. A cursor saved by an earlier
    # version is in Python's isoformat ('+00:00', microseconds), which
    # Loyverse rejects with INVALID_VALUE - and because that rejection
    # happens before the cursor is ever rewritten, the bad value would
    # survive every future sync and the branch could never recover on its
    # own. Converting here is what breaks that loop.
    cursor = _to_loyverse_time(_parse_time(cursor))

    processed = sync_and_deduct(provider, store, store_id, created_at_min=cursor)

    # The cursor must never move backward. A sync fired again within the
    # overlap window - someone pressing "ซิงก์ตอนนี้" twice in a hurry, or
    # a background cycle running slightly early - would otherwise compute
    # now-minus-overlap as earlier than the cursor it already advanced to,
    # re-opening a window that was already covered. Clamping to the
    # existing cursor makes a rapid repeat a safe no-op instead.
    #
    # Compared as instants rather than strings: mixing the two textual
    # formats would make max() compare '2' against '1' character by
    # character and pick nonsense.
    candidate = _minus_seconds(now, overlap_seconds)
    advanced = max(_parse_time(candidate), _parse_time(cursor))
    store.set_sync_cursor(store_id, _to_loyverse_time(advanced))
    return processed


def _utcnow_iso() -> str:
    return _to_loyverse_time(datetime.now(timezone.utc))


def _to_loyverse_time(dt: datetime) -> str:
    """Format a timestamp the way the Loyverse API demands:
    YYYY-MM-DDTHH:mm:ss.sssZ - milliseconds, and a literal Z.

    Python's own isoformat() produces microseconds and a '+00:00' offset,
    which Loyverse rejects outright with INVALID_VALUE. Since the cursor
    is stored in exactly the form it will be sent in, getting this wrong
    breaks every sync after the first, and the offline tests can't catch
    it because they never call the real API."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") \
        + f"{dt.microsecond // 1000:03d}Z"


def _minus_seconds(iso: str, seconds: int) -> str:
    dt = _parse_time(iso) - timedelta(seconds=seconds)
    return _to_loyverse_time(dt)


def _parse_time(iso: str) -> datetime:
    """Accepts both the Loyverse-style 'Z' suffix we now write and the
    '+00:00' form written by earlier versions, so a cursor saved before
    this fix is still readable rather than crashing the first sync after
    an upgrade."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
