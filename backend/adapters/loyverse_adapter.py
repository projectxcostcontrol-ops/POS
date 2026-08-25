from __future__ import annotations

from core.pos_provider import PosProvider
from adapters._loyverse_client import LoyverseClient


class LoyverseAdapter(PosProvider):
    """Implements PosProvider on top of the Loyverse API.

    This is the only file that should ever import _loyverse_client -
    everything above this layer (business logic, API routes) talks to
    the PosProvider interface only, so a future own-built POS can be
    added as a sibling adapter without touching anything else.
    """

    def __init__(self, access_token: str | None = None):
        self.client = LoyverseClient(access_token)

    def get_stores(self) -> list[dict]:
        return [{"id": s["id"], "name": s["name"]} for s in self.client.get_stores()]

    def get_items(self) -> list[dict]:
        out = []
        for item in self.client.get_items():
            variant = item["variants"][0] if item.get("variants") else {}
            price = None
            if variant.get("stores"):
                price = variant["stores"][0].get("price")
            out.append({
                "id": item["id"],
                "name": item["item_name"],
                "category_id": item.get("category_id"),
                "price": price,
            })
        return out

    def get_categories(self) -> list[dict]:
        return [{"id": c["id"], "name": c["name"]} for c in self.client.get_categories()]

    def get_receipts(self, store_id: str, created_at_min: str | None = None) -> list[dict]:
        """Receipts for one branch.

        Two timestamps matter and they are NOT the same:

        - receipt_date: when the sale was rung up. This is what belongs on
          a sales report - a sale made at 7pm is 7pm's business however
          late it reached the server.
        - created_at: when Loyverse recorded it. This is what the API
          filters on, and what a sync cursor has to follow. A till that
          was offline uploads its backlog hours later, so its receipts
          carry an old receipt_date and a new created_at.

        Conflating them is how receipts from a delayed terminal vanish:
        the cursor advances past a sale time that the receipt only reaches
        us long after. Both are returned so the caller can use the right
        one for the right job.
        """
        out = []
        for r in self.client.get_receipts(created_at_min=created_at_min):
            if r.get("store_id") != store_id:
                continue

            # Loyverse marks a refund with receipt_type REFUND and reports
            # its money as a positive number. Counted as-is it would ADD to
            # takings - a refund would look like a sale. The sign is
            # flipped here, once, so nothing downstream has to remember.
            is_refund = (r.get("receipt_type") or "").upper() == "REFUND"
            total = r.get("total_money") or 0
            sign = -1 if is_refund else 1

            out.append({
                "receipt_number": r.get("receipt_number"),
                "store_id": r.get("store_id"),
                "created_at": r.get("receipt_date") or r.get("created_at"),
                "recorded_at": r.get("created_at") or r.get("receipt_date"),
                "is_refund": is_refund,
                "total": sign * total,
                "line_items": [
                    {
                        "item_name": li.get("item_name") or li.get("variant_name"),
                        "quantity": sign * (li.get("quantity") or 0),
                        "price": li.get("price"),
                    }
                    for li in r.get("line_items", [])
                ],
            })
        return out
