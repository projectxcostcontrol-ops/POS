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
        store.set_sync_cursor(store_id, now)
        return 0

    processed = sync_and_deduct(provider, store, store_id, created_at_min=cursor)

    # The cursor must never move backward. A sync fired again within the
    # overlap window - someone pressing "ซิงก์ตอนนี้" twice in a hurry, or
    # a background cycle running slightly early - would otherwise compute
    # now-minus-overlap as earlier than the cursor it already advanced to,
    # re-opening a window that was already covered. Clamping to the
    # existing cursor makes a rapid repeat a safe no-op instead.
    candidate = _minus_seconds(now, overlap_seconds)
    store.set_sync_cursor(store_id, max(candidate, cursor))
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
