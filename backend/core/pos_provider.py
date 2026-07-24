"""
POS provider interface (the "port" in ports-and-adapters).

Business logic and the API layer only ever talk to this interface -
never to Loyverse (or any future POS) directly. Today LoyverseAdapter
implements it. A future own-built POS would implement the same
interface as a new adapter, and nothing above this layer would change.

READ-ONLY BY DESIGN: this interface has no write/create methods. We
only ever pull data from the POS (items, categories for reference,
receipts, stores) - we never write back to it. This keeps testing safe
against a live Loyverse account, and means our own category/recipe/
stock management (in storage/firestore_store.py) is fully independent
of Loyverse, which is exactly what's needed to swap in an own-built POS
adapter later without this layer changing at all.

Kept intentionally small: only what we actually use today. Expand it
only when a second real adapter needs a method this one doesn't have.
"""

from abc import ABC, abstractmethod


class PosProvider(ABC):
    @abstractmethod
    def get_stores(self) -> list[dict]:
        """Each store: {id, name}"""

    @abstractmethod
    def get_items(self) -> list[dict]:
        """Each item: {id, name, category_id, price} - category_id refers to
        the POS's own category, kept only for reference/display. Our own
        category system lives in firestore_store.py and is independent."""

    @abstractmethod
    def get_categories(self) -> list[dict]:
        """Each category: {id, name} - read-only, for reference/display only."""

    @abstractmethod
    def get_receipts(self, store_id: str, created_at_min: str | None = None) -> list[dict]:
        """Each receipt: {receipt_number, store_id, created_at, total, line_items: [{item_name, quantity, price}]}"""
