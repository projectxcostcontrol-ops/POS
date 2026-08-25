"""
Pulling sales from the POS: stock deduction and our own copy of history.

The POS tells us what sold; recipes and stock are entirely ours. That
makes the sales path fully automatic - only restocking and physical
counts are ever typed in by a person.

There is ONE way in: sync_branch(). It behaves differently on a branch's
first run than afterwards, but that's a branch inside one function rather
than a separate code path, because the earlier design - a backfill
routine beside a cursor sync beside a manual re-sync - meant a branch
that connected before saving existed fell through every one of them and
kept no history at all. Today's figures were right and last week's were
missing, with nothing to show why.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

from core.pos_provider import PosProvider
from storage.firestore_store import Store

# How far back a cursor is rewound each time, to catch receipts that reach
# the POS after the sale.
#
# This was 5 minutes and that was badly wrong. A restaurant running
# several tills has receipts that arrive long after they were rung up: a
# terminal that dropped off wifi uploads its backlog when it reconnects,
# and the receipt still carries the original sale time. With a 5-minute
# window those receipts land behind a cursor that has already moved past
# them, and nothing looks there again - lost silently, with no error.
#
# The two sides of this trade are not equal. Re-fetching a receipt costs
# a skipped comparison and one idempotent overwrite. Missing one loses a
# sale from the books permanently.
SYNC_OVERLAP_SECONDS = int(os.environ.get("SYNC_OVERLAP_SECONDS", 6 * 3600))


def sync_branch(provider: PosProvider, store: Store, store_id: str,
                overlap_seconds: int = SYNC_OVERLAP_SECONDS,
                full: bool = False) -> dict:
    """Sync one branch. The only entry point.

    First run for a branch (or `full=True`): asks for everything the POS
    will give. On a plan with a history limit that's however far back the
    limit reaches - the client caps it and returns what it can rather
    than failing.

    Every run after that: asks for receipts since the cursor, rewound by
    the overlap window.

    Returns a summary rather than a bare count, because "0 processed" is
    ambiguous on its own - it means both "nothing sold" and "everything
    had already been counted", and telling those apart matters when
    something looks wrong.
    """
    cursor = store.get_sync_cursor(store_id)
    first_run = full or cursor is None

    since = None if first_run else _minus_seconds(cursor, overlap_seconds)
    receipts = provider.get_receipts(store_id, created_at_min=since)

    result = _apply(store, store_id, receipts, deduct_stock=not first_run)

    # The cursor follows the newest arrival time we actually saw, not the
    # wall clock. Wall-clock time is a guess about when receipts show up;
    # recorded_at is the fact. Falling back to now only when nothing came
    # back at all.
    newest = max((r.get("recorded_at") or r.get("created_at") or ""
                  for r in receipts), default="")
    store.set_sync_cursor(store_id, _advance(cursor, newest))

    result["first_run"] = first_run
    result["fetched"] = len(receipts)
    return result


def _apply(store: Store, store_id: str, receipts: list[dict],
           deduct_stock: bool) -> dict:
    """Save every receipt; deduct stock for the ones not yet counted.

    Saving and deducting answer different questions and are deliberately
    separate. "Processed" means stock has already moved and must not move
    twice. Saving is just a record, and re-saving overwrites harmlessly -
    so a receipt that was already processed still gets its copy kept,
    which is what stops gaps appearing everywhere the overlap window lands.

    On a first run stock is NOT deducted: those sales happened before the
    branch had recipes, so deducting them would invent a shortage against
    ingredients nobody was tracking.
    """
    already = store.list_processed_receipts(store_id)

    sales_rows = []
    to_mark = []
    deducted = 0
    skipped_refunds = 0

    for receipt in receipts:
        number = receipt.get("receipt_number")
        if not number:
            continue

        sales_rows.append((number, _sale_row(receipt)))

        if number in already:
            continue

        if not deduct_stock:
            to_mark.append(number)
            continue

        # A refund returns money, but the food was already cooked and the
        # ingredients already gone. Putting stock back would invent
        # inventory that isn't on the shelf. The money is corrected; the
        # stock deliberately isn't.
        if receipt.get("is_refund"):
            to_mark.append(number)
            skipped_refunds += 1
            continue

        for line in receipt.get("line_items", []):
            for ing in store.get_recipe(store_id, line.get("item_name")):
                store.deduct_stock(store_id, ing["material_id"],
                                   ing["qty"] * (line.get("quantity") or 0),
                                   ref=f"receipt:{number}")
        to_mark.append(number)
        deducted += 1

    # Batched: one write per receipt is fine for a dozen bills and times
    # out on a first sync of several thousand.
    if sales_rows:
        store.save_sales_bulk(store_id, sales_rows)
    if to_mark:
        store.mark_receipts_processed_bulk(store_id, to_mark)

    return {
        "saved": len(sales_rows),
        "deducted": deducted,
        "already_counted": len(receipts) - len(to_mark),
        "refunds": skipped_refunds,
    }


def _sale_row(receipt: dict) -> dict:
    """The fields reports need. Everything else is re-fetchable from the
    POS while it's still in their window, and dead weight afterwards."""
    return {
        "receipt_number": receipt.get("receipt_number"),
        # The sale time - what reports group by. A 7pm sale belongs to 7pm
        # even if a delayed terminal only uploaded it at midnight.
        "date": receipt.get("created_at") or "",
        # When the POS recorded it - what the cursor follows.
        "recorded_at": receipt.get("recorded_at") or receipt.get("created_at") or "",
        "is_refund": bool(receipt.get("is_refund")),
        "total": receipt.get("total") or 0,
        "items": [
            {
                "name": li.get("item_name") or "",
                "qty": li.get("quantity") or 0,
                "price": li.get("price") or 0,
            }
            for li in receipt.get("line_items", [])
        ],
    }


def _advance(cursor: str | None, newest_seen: str) -> str:
    """Where the cursor lands after a sync.

    Never moves backward: a sync fired twice in quick succession would
    otherwise re-open a window already covered. Compared as instants, not
    text, since a cursor written by an older version may be in a
    different format and string comparison would pick nonsense.
    """
    candidates = [c for c in (cursor, newest_seen) if c]
    if not candidates:
        return _utcnow_iso()
    return _to_loyverse_time(max(_parse_time(c) for c in candidates))


# ---- timestamps ----
# The POS demands YYYY-MM-DDTHH:mm:ss.sssZ. Python's isoformat() produces
# microseconds and '+00:00', which it rejects outright.

def _utcnow_iso() -> str:
    return _to_loyverse_time(datetime.now(timezone.utc))


def _to_loyverse_time(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _parse_time(iso: str) -> datetime:
    """Accepts both the 'Z' form written now and the '+00:00' form written
    by earlier versions, so an existing cursor stays readable after an
    upgrade instead of crashing the first sync."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _minus_seconds(iso: str, seconds: int) -> str:
    return _to_loyverse_time(_parse_time(iso) - timedelta(seconds=seconds))
