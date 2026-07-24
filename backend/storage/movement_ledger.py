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
        entry = {
            "material_id": material_id,
            "kind": kind,
            "quantity": quantity,
            "unit_cost": unit_cost,
            "note": note,
            "ref": ref,
            "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
        }
        _, doc_ref = self._col(store_id).add(entry)
        return entry | {"id": doc_ref.id}

    def record_receive(self, store_id: str, material_id: str, quantity: float,
                       unit_cost: float, note: str = "", occurred_at: str | None = None,
                       ref: str | None = None) -> dict:
        return self.record(store_id, material_id, RECEIVE, abs(quantity),
                           unit_cost=unit_cost, note=note, occurred_at=occurred_at, ref=ref)

    def record_sale(self, store_id: str, material_id: str, quantity: float,
                    ref: str | None = None) -> dict:
        """quantity = amount consumed (positive); stored negative."""
        return self.record(store_id, material_id, SALE, -abs(quantity), ref=ref)

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
        return sum(m.get("quantity", 0) for m in self.list_movements(store_id, material_id))

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
