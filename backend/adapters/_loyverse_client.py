from __future__ import annotations

"""
Thin client for the Loyverse API.

Docs: https://developer.loyverse.com/docs/
Auth: Bearer token (Settings > Access Tokens in the Loyverse Back Office)
"""

import os
import time
from datetime import datetime, timedelta, timezone

import requests

BASE_URL = "https://api.loyverse.com/v1.0"

# Without an explicit timeout, requests will wait forever on a connection
# that stalls - not slow, literally unbounded. That's indistinguishable
# from a frozen UI to whoever is looking at a spinner that never resolves,
# and it was the actual cause of a real sync hanging on its very first
# call, before any data volume could even be a factor. (connect, read)
DEFAULT_TIMEOUT = (10, 30)

# A cursor that never goes empty - a malformed response, an API change we
# haven't seen - would otherwise loop forever. This is deliberately far
# above anything a real sync should ever need (250k+ records) so it never
# fires in normal use; it exists purely so a broken response fails loudly
# instead of hanging just as silently as the missing timeout did.
MAX_PAGES = 1000

# Loyverse's free plan refuses receipts "created earlier than 31 days
# ago" with a 402. 31 is their cliff edge, not a safe window: asking for
# exactly 31 days lands on the boundary and gets the whole request
# refused, since the timestamp is already fractionally too old by the
# time it reaches them. Asking for 30 stays clearly inside it.
#
# The window also has to exist at all - without one, every receipt query
# pages backwards until the API refuses, spending calls to discover a
# limit we already know about.
FREE_PLAN_HISTORY_DAYS = 30


def _fmt(dt: datetime) -> str:
    """Loyverse's required timestamp format: YYYY-MM-DDTHH:mm:ss.sssZ."""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _days_ago(days: int) -> str:
    return _fmt(datetime.now(timezone.utc) - timedelta(days=days))


def normalize_time(value: str | None) -> str | None:
    """Coerce any ISO-ish timestamp into the one format Loyverse accepts.

    Python's isoformat() produces '+00:00' and microseconds, which
    Loyverse rejects outright with INVALID_VALUE. That mistake has now
    been made three times in three different call sites, so the
    conversion lives HERE - at the single door every request goes
    through - rather than depending on each caller remembering. Callers
    can pass whatever they have."""
    if not value:
        return value
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value   # not something we can parse; let the API judge it
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return _fmt(dt)


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
        resp = self.session.get(url, params=params or {}, timeout=DEFAULT_TIMEOUT)
        if resp.status_code == 429:
            # rate limited -> back off and retry once
            time.sleep(2)
            resp = self.session.get(url, params=params or {}, timeout=DEFAULT_TIMEOUT)
        if not resp.ok:
            print(f"Loyverse API error {resp.status_code} on GET {path}: {resp.text}")
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{BASE_URL}{path}"
        resp = self.session.post(url, json=payload, timeout=DEFAULT_TIMEOUT)
        if not resp.ok:
            print(f"Loyverse API error {resp.status_code} on POST {path}: {resp.text}")
        resp.raise_for_status()
        return resp.json()

    def _paginate(self, path: str, key: str, params: dict | None = None,
                  stop_on_payment_required: bool = False) -> list[dict]:
        """Loop through cursor-based pagination until all records are
        collected, or until MAX_PAGES is hit - see its comment above.

        `stop_on_payment_required` handles Loyverse's plan limits: a free
        account refuses receipts older than 31 days with a 402 midway
        through pagination. The pages already fetched are perfectly good
        data, so throwing them away because the NEXT page was refused
        would show an empty screen to someone whose recent sales loaded
        fine. Stop and return what we have instead."""
        params = dict(params or {})
        params.setdefault("limit", 250)
        results = []
        for _ in range(MAX_PAGES):
            try:
                data = self._get(path, params)
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if stop_on_payment_required and status == 402 and results:
                    return results
                raise
            results.extend(data.get(key, []))
            cursor = data.get("cursor")
            if not cursor:
                return results
            params["cursor"] = cursor
        raise RuntimeError(
            f"เรียก {path} เกิน {MAX_PAGES} หน้าโดยยังไม่จบ - "
            f"Loyverse อาจตอบกลับผิดปกติ (cursor ไม่มีวันหมด)"
        )

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

        With no created_at_min, this defaults to the recent window the
        free plan allows rather than everything - see
        FREE_PLAN_HISTORY_DAYS.

        If an explicit date is older than that and the account turns out
        to be on the free plan, the request is retried once against the
        allowed window. The alternative - clamping every date up front -
        would quietly cripple accounts that pay for Unlimited sales
        history, so the further-back request is attempted first and only
        narrowed if Loyverse actually refuses it.
        """
        window_start = _days_ago(FREE_PLAN_HISTORY_DAYS)
        # Normalized here so no caller can get the format wrong.
        params = {"created_at_min": normalize_time(created_at_min) or window_start}
        if created_at_max:
            params["created_at_max"] = normalize_time(created_at_max)

        try:
            return self._paginate("/receipts", "receipts", params,
                                  stop_on_payment_required=True)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status != 402 or params["created_at_min"] >= window_start:
                raise
            params["created_at_min"] = window_start
            return self._paginate("/receipts", "receipts", params,
                                  stop_on_payment_required=True)

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
