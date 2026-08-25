from __future__ import annotations

"""
Stock movement ledger - the foundation of V2.

Instead of storing one mutable `stock` number per material, every change
is recorded as an immutable movement. Current stock is the sum of all
movements. This is what makes the rest of V2 possible:

- negative stock detection: sum < 0 means the recipe or a count is wrong
- monthly average cost: average the `receive` movements in that month,
  so past months keep the cost they actually had (instead of every
  historical profit figure silently changing when a price updates)
- cost history: query receive movements by date

Movement kinds:
  receive  - stock coming in (from a delivery), carries unit_cost
  sale     - deducted automatically by the recipe engine when items sell
  count    - a physical stock count; stored as the delta needed to reach
             the counted number, so the running sum still works
  waste    - spoilage/loss written off deliberately

Current stock is a SUM, and that is the problem this file also has to
solve. Every sale writes one movement per ingredient, so a shop doing
100 bills a day of three-ingredient dishes writes ~110,000 movements a
year - and "what is on the shelf right now" meant reading every one of
them, every time anyone opened the materials page. The answer was right
and it got more expensive every day the shop stayed open.

So each material doc carries a running snapshot:

  stock_qty   - sum of every movement quantity
  recv_qty    - sum of receive quantities
  recv_value  - sum of quantity x unit_cost over receives

These are DERIVED, never authoritative: the ledger is still the truth and
rebuild_snapshots() can regenerate them from it at any time. They are
maintained with Firestore's atomic Increment, in the same batch that
writes the movement - so two sales landing at once cannot lose each
other's deduction the way a read-modify-write would, and a snapshot
cannot drift from the ledger by a rounding error or a missed path.

Anything reading a snapshot checks it exists first and falls back to
summing the ledger when it doesn't. That keeps a branch that has not
been rebuilt yet slow rather than wrong.
"""

from datetime import datetime, timezone

RECEIVE = "receive"
SALE = "sale"
COUNT = "count"
WASTE = "waste"


class MovementLedger:
    def __init__(self, store):
        self.store = store

    def _col(self, store_id: str):
        return self.store._col(store_id, "stock_movements")

    # ---- writing ----

    def record(self, store_id: str, material_id: str, kind: str, quantity: float,
               unit_cost: float | None = None, note: str = "",
               occurred_at: str | None = None, ref: str | None = None) -> dict:
        """Append one movement. `quantity` is signed: positive adds stock,
        negative removes it. Never updates or deletes existing movements -
        corrections are themselves new movements, so history stays intact."""
        return self.record_many(store_id, [{
            "material_id": material_id, "kind": kind, "quantity": quantity,
            "unit_cost": unit_cost, "note": note, "ref": ref,
            "occurred_at": occurred_at,
        }])[0]

    def record_many(self, store_id: str, movements: list[dict]) -> list[dict]:
        """Append several movements together.

        One sale of a three-ingredient dish is three movements, and a
        sync of 200 bills used to be 600 separate round trips - which is
        what a sync that "hangs" actually looks like from the outside.
        Batched, the same work is a handful of requests.

        Each batch carries both halves of the write: the movement itself
        and the increment to its material's snapshot. Firestore commits a
        batch atomically, so there is no window where the ledger and the
        snapshot disagree - a crash leaves neither, not one without the
        other.
        """
        now = datetime.now(timezone.utc).isoformat()
        col = self._col(store_id)
        entries = []
        for m in movements:
            entries.append({
                "material_id": m.get("material_id"),
                "kind": m.get("kind"),
                "quantity": m.get("quantity") or 0,
                "unit_cost": m.get("unit_cost"),
                "note": m.get("note") or "",
                "ref": m.get("ref"),
                "occurred_at": m.get("occurred_at") or now,
            })

        # Firestore takes 500 operations per batch and each movement uses
        # two (the doc plus its snapshot bump), so 200 movements a batch.
        CHUNK = 200
        out = []
        for i in range(0, len(entries), CHUNK):
            chunk = entries[i:i + CHUNK]
            batch = self.store.db.batch()
            for entry in chunk:
                ref = col.document()
                batch.set(ref, entry)
                bump = self._snapshot_bump(entry)
                if bump:
                    # set(merge=True), not update(): update() fails the whole
                    # batch if the material document is gone, which would
                    # turn one deleted ingredient still named in a recipe
                    # into a sync that stops dead for every other ingredient
                    # too. Merging touches only these fields.
                    batch.set(self.store.material_ref(store_id, entry["material_id"]),
                              bump, merge=True)
                out.append(entry | {"id": ref.id})
            batch.commit()
        return out

    def _snapshot_bump(self, entry: dict) -> dict:
        """The running totals this one movement moves.

        Only additive quantities live in a snapshot. Weighted average cost
        is not additive on its own, but its two halves are - keep the
        total quantity received and the total value paid, and the average
        is a division at read time that stays exactly right no matter what
        order deliveries arrived in.
        """
        material_id = entry.get("material_id")
        if not material_id:
            return {}
        inc = self.store.increment
        bump = {"stock_qty": inc(entry.get("quantity") or 0)}
        if entry.get("kind") == RECEIVE and entry.get("unit_cost") is not None:
            qty = entry.get("quantity") or 0
            bump["recv_qty"] = inc(qty)
            bump["recv_value"] = inc(qty * entry["unit_cost"])
        return bump

    def record_receive(self, store_id: str, material_id: str, quantity: float,
                       unit_cost: float, note: str = "", occurred_at: str | None = None,
                       ref: str | None = None) -> dict:
        return self.record(store_id, material_id, RECEIVE, abs(quantity),
                           unit_cost=unit_cost, note=note, occurred_at=occurred_at, ref=ref)

    def record_sale(self, store_id: str, material_id: str, quantity: float,
                    ref: str | None = None) -> dict:
        """quantity = amount consumed (positive); stored negative."""
        return self.record(store_id, material_id, SALE, -abs(quantity), ref=ref)

    def record_sales_bulk(self, store_id: str, rows: list[dict]) -> int:
        """rows: [{material_id, quantity, ref}] - quantity consumed, positive.

        What a sync uses. Deducting one ingredient at a time is correct
        and unusably slow once a sync covers more than a few bills."""
        return len(self.record_many(store_id, [{
            "material_id": r["material_id"],
            "kind": SALE,
            "quantity": -abs(r.get("quantity") or 0),
            "ref": r.get("ref"),
        } for r in rows]))

    def record_waste(self, store_id: str, material_id: str, quantity: float,
                     note: str = "") -> dict:
        return self.record(store_id, material_id, WASTE, -abs(quantity), note=note)

    def record_count(self, store_id: str, material_id: str, counted_quantity: float,
                     note: str = "") -> dict:
        """A physical count. Stores the delta between what the ledger thinks
        we have and what was actually counted, so the running sum lands on
        the counted number while still showing that a correction happened."""
        current = self.current_stock(store_id, material_id)
        delta = counted_quantity - current
        return self.record(store_id, material_id, COUNT, delta,
                           note=note or f"นับได้ {counted_quantity}")

    # ---- reading ----

    def list_movements(self, store_id: str, material_id: str | None = None) -> list[dict]:
        col = self._col(store_id)
        query = col.where("material_id", "==", material_id) if material_id else col
        movements = [d.to_dict() | {"id": d.id} for d in query.stream()]
        movements.sort(key=lambda m: m.get("occurred_at", ""), reverse=True)
        return movements

    def current_stock(self, store_id: str, material_id: str) -> float:
        """One read when the snapshot is there, a full scan of this
        material's history when it isn't - correct either way."""
        snap = self.store.material_snapshot(store_id, material_id)
        if snap is not None and "stock_qty" in snap:
            return snap["stock_qty"] or 0
        return sum(m.get("quantity", 0) for m in self.list_movements(store_id, material_id))

    def rebuild_snapshots(self, store_id: str) -> int:
        """Recompute every material's snapshot from the ledger.

        The ledger is the record; a snapshot is an optimisation, so this
        can always regenerate one and nothing is lost if it has to.
        Needed once for branches whose materials predate the snapshot,
        and available afterwards as the answer to "are these numbers
        right?" - which should be checkable, not taken on trust.

        Run it while the branch is quiet. It writes absolute totals read
        a moment earlier, so a sale landing in between would be
        overwritten; re-running it, or the next stock count, corrects
        that. The window is milliseconds and the fix is to run it again,
        which is why this is a button rather than something automatic in
        the middle of service.
        """
        totals: dict[str, dict] = {}
        for m in self.list_movements(store_id):
            mid = m.get("material_id")
            if not mid:
                continue
            t = totals.setdefault(mid, {"stock_qty": 0.0, "recv_qty": 0.0,
                                        "recv_value": 0.0})
            qty = m.get("quantity") or 0
            t["stock_qty"] += qty
            if m.get("kind") == RECEIVE and m.get("unit_cost") is not None:
                t["recv_qty"] += qty
                t["recv_value"] += qty * m["unit_cost"]

        rebuilt = 0
        for material_id in self.store.list_material_ids(store_id):
            t = totals.get(material_id, {"stock_qty": 0.0, "recv_qty": 0.0,
                                         "recv_value": 0.0})
            self.store.set_material_snapshot(store_id, material_id, t)
            rebuilt += 1
        return rebuilt

    def all_current_stock(self, store_id: str) -> dict:
        """{material_id: current stock} for every material with movements.
        One read for the whole store instead of one per material."""
        totals: dict[str, float] = {}
        for m in self.list_movements(store_id):
            mid = m.get("material_id")
            totals[mid] = totals.get(mid, 0) + m.get("quantity", 0)
        return totals

    # ---- costing ----

    def average_cost(self, store_id: str, material_id: str,
                     year: int | None = None, month: int | None = None) -> float | None:
        """Weighted average cost per unit from `receive` movements.

        Scoped to a month when year/month are given - that's the point of
        this: a dish sold in May is costed with May's prices, so last
        month's profit doesn't shift when this month's delivery is pricier.
        Falls back to the most recent receive if that month had none.
        """
        if year is None and month is None:
            # The common case by far - every material row on the stock
            # page asks exactly this - and the one the snapshot answers
            # in a single read instead of a walk through every delivery
            # this material has ever had.
            snap = self.store.material_snapshot(store_id, material_id)
            if snap and (snap.get("recv_qty") or 0) > 0:
                return snap["recv_value"] / snap["recv_qty"]

        receives = [m for m in self.list_movements(store_id, material_id)
                    if m.get("kind") == RECEIVE and m.get("unit_cost") is not None]
        if not receives:
            return None

        if year is not None and month is not None:
            in_month = [m for m in receives if _matches_month(m.get("occurred_at"), year, month)]
            if in_month:
                receives = in_month
            else:
                # no delivery that month - carry the latest known price forward
                return receives[0].get("unit_cost")

        total_qty = sum(m["quantity"] for m in receives)
        if total_qty == 0:
            return receives[0].get("unit_cost")
        total_value = sum(m["quantity"] * m["unit_cost"] for m in receives)
        return total_value / total_qty

    def cost_history(self, store_id: str, material_id: str) -> list[dict]:
        """Every receive with its price, newest first - for showing how a
        material's cost moved over time."""
        return [
            {
                "occurred_at": m.get("occurred_at"),
                "quantity": m.get("quantity"),
                "unit_cost": m.get("unit_cost"),
                "note": m.get("note"),
            }
            for m in self.list_movements(store_id, material_id)
            if m.get("kind") == RECEIVE
        ]


def _matches_month(occurred_at: str | None, year: int, month: int) -> bool:
    if not occurred_at:
        return False
    try:
        d = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        return d.year == year and d.month == month
    except ValueError:
        return False
