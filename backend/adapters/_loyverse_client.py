from __future__ import annotations

"""
Thin client for the Loyverse API.

Docs: https://developer.loyverse.com/docs/
Auth: Bearer token (Settings > Access Tokens in the Loyverse Back Office)
"""

import os
import time
import requests

BASE_URL = "https://api.loyverse.com/v1.0"


class LoyverseClient:
    def __init__(self, access_token: str | None = None):
        self.token = access_token or os.environ["LOYVERSE_ACCESS_TOKEN"]
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })

    # ---------- low-level helpers ----------

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{BASE_URL}{path}"
        resp = self.session.get(url, params=params or {})
        if resp.status_code == 429:
            # rate limited -> back off and retry once
            time.sleep(2)
            resp = self.session.get(url, params=params or {})
        if not resp.ok:
            print(f"Loyverse API error {resp.status_code} on GET {path}: {resp.text}")
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{BASE_URL}{path}"
        resp = self.session.post(url, json=payload)
        if not resp.ok:
            print(f"Loyverse API error {resp.status_code} on POST {path}: {resp.text}")
        resp.raise_for_status()
        return resp.json()

    def _paginate(self, path: str, key: str, params: dict | None = None) -> list[dict]:
        """Loop through cursor-based pagination until all records are collected."""
        params = dict(params or {})
        params.setdefault("limit", 250)
        results = []
        while True:
            data = self._get(path, params)
            results.extend(data.get(key, []))
            cursor = data.get("cursor")
            if not cursor:
                break
            params["cursor"] = cursor
        return results

    # ---------- read endpoints ----------

    def get_stores(self) -> list[dict]:
        return self._paginate("/stores", "stores")

    def get_categories(self) -> list[dict]:
        return self._paginate("/categories", "categories")

    def get_items(self) -> list[dict]:
        return self._paginate("/items", "items")

    def get_inventory(self, store_id: str | None = None) -> list[dict]:
        params = {"store_id": store_id} if store_id else None
        return self._paginate("/inventory", "inventory_levels", params)

    def get_receipts(self, created_at_min: str | None = None,
                      created_at_max: str | None = None) -> list[dict]:
        """
        created_at_min / created_at_max: ISO 8601 strings, e.g. '2026-07-01T00:00:00.000Z'
        Omit both to pull everything (careful on a store with a lot of history).
        """
        params = {}
        if created_at_min:
            params["created_at_min"] = created_at_min
        if created_at_max:
            params["created_at_max"] = created_at_max
        return self._paginate("/receipts", "receipts", params)

    def get_employees(self) -> list[dict]:
        return self._paginate("/employees", "employees")

    def get_customers(self) -> list[dict]:
        return self._paginate("/customers", "customers")

    # ---------- write endpoints (used only for generating test data) ----------

    def create_category(self, name: str, color: str = "GREY") -> dict:
        return self._post("/categories", {"name": name, "color": color})

    def create_item(self, name: str, category_id: str, price: float,
                     store_id: str) -> dict:
        payload = {
            "item_name": name,
            "category_id": category_id,
            "default_pricing_type": "FIXED",
            "variants": [
                {
                    "variant_name": "Regular",
                    "sku": name.replace(" ", "-").upper(),
                    "default_price": price,
                    "stores": [
                        {"store_id": store_id, "price": price}
                    ],
                }
            ],
        }
        return self._post("/items", payload)

    def create_receipt(self, store_id: str, line_items: list[dict],
                        payment_type_id: str) -> dict:
        """
        line_items: [{"variant_id": "...", "quantity": 2}, ...]
        payment_type_id: get one from GET /payment_types
        """
        payload = {
            "store_id": store_id,
            "line_items": line_items,
            "payments": [{"payment_type_id": payment_type_id}],
        }
        return self._post("/receipts", payload)

    def get_payment_types(self) -> list[dict]:
        return self._paginate("/payment_types", "payment_types")
