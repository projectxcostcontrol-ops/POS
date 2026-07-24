"""
Tests the full sale -> stock deduction loop against your REAL running system.

What it does:
  1. reads current stock for the materials in a menu item's recipe
  2. creates a real test sale in Loyverse for that item
  3. triggers a sync
  4. re-reads stock and checks it dropped by exactly the recipe amount

Usage:
    source venv/bin/activate
    python tests/test_sale_flow.py <store_id> <menu_item_name> [api_url]

Example:
    python tests/test_sale_flow.py f5550886-... "ต้มยำกุ้ง" https://your-backend-url

api_url defaults to http://127.0.0.1:8000.

Requires LOYVERSE_ACCESS_TOKEN in backend/.env (or exported) because it
creates a real receipt in Loyverse - that receipt WILL appear in your
Loyverse sales history, so use a test item or a test store.
"""

import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

LOYVERSE_API = "https://api.loyverse.com/v1.0"


def loyverse_headers():
    token = os.environ.get("LOYVERSE_ACCESS_TOKEN")
    if not token:
        print("LOYVERSE_ACCESS_TOKEN not set - put it in backend/.env or export it.")
        print("(The app itself stores the token in Firestore, but this script")
        print(" talks to Loyverse directly to create the test sale.)")
        sys.exit(1)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def create_test_sale(store_id, item_name, quantity=1):
    """Rings up a real sale in Loyverse so the app has something to sync."""
    headers = loyverse_headers()

    items = requests.get(f"{LOYVERSE_API}/items", headers=headers,
                         params={"limit": 250}).json().get("items", [])
    match = next((i for i in items if i["item_name"] == item_name), None)
    if not match:
        print(f"Menu item '{item_name}' not found in Loyverse.")
        print("Available:", ", ".join(i["item_name"] for i in items[:15]))
        sys.exit(1)
    variant_id = match["variants"][0]["variant_id"]

    payment_types = requests.get(f"{LOYVERSE_API}/payment_types",
                                 headers=headers).json().get("payment_types", [])
    if not payment_types:
        print("No payment types configured in Loyverse.")
        sys.exit(1)

    resp = requests.post(f"{LOYVERSE_API}/receipts", headers=headers, json={
        "store_id": store_id,
        "line_items": [{"variant_id": variant_id, "quantity": quantity}],
        "payments": [{"payment_type_id": payment_types[0]["id"]}],
    })
    if not resp.ok:
        print(f"Couldn't create the sale: {resp.status_code} {resp.text}")
        sys.exit(1)
    return resp.json()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    store_id = sys.argv[1]
    item_name = sys.argv[2]
    api_url = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8000"
    base = f"{api_url}/api/{store_id}"
    quantity = 2

    print(f"Testing sale flow for '{item_name}' x{quantity}\n")

    # --- what does the recipe say this sale should consume?
    recipe = requests.get(f"{base}/recipes/{item_name}").json()
    if not recipe:
        print(f"'{item_name}' has no recipe linked yet - nothing would be deducted.")
        print("Link one on the สูตรอาหาร page first, then re-run.")
        sys.exit(1)

    materials = {m["id"]: m for m in requests.get(f"{base}/materials").json()}
    print("Recipe:")
    expected = {}
    for ing in recipe:
        mat = materials.get(ing["material_id"])
        name = mat["name"] if mat else ing["material_id"]
        unit = mat["unit"] if mat else ""
        used = ing["qty"] * quantity
        expected[ing["material_id"]] = used
        print(f"  {name}: {ing['qty']} {unit} x{quantity} = {used} {unit}")

    before = {mid: materials.get(mid, {}).get("stock", 0) for mid in expected}
    print("\nStock before:")
    for mid, qty in before.items():
        print(f"  {materials.get(mid, {}).get('name', mid)}: {qty}")

    # --- ring up a real sale
    print("\nCreating the sale in Loyverse...")
    receipt = create_test_sale(store_id, item_name, quantity)
    receipt_no = receipt.get("receipt_number", "?")
    print(f"  receipt #{receipt_no}")

    # --- sync (Loyverse needs a moment before the receipt is queryable)
    print("\nSyncing...")
    time.sleep(3)
    result = requests.post(f"{base}/sync").json()
    print(f"  processed {result.get('processed_receipts')} new receipt(s)")

    # --- did stock move by the right amount?
    after_materials = {m["id"]: m for m in requests.get(f"{base}/materials").json()}
    print("\nStock after:")
    all_ok = True
    for mid, used in expected.items():
        name = after_materials.get(mid, {}).get("name", mid)
        after = after_materials.get(mid, {}).get("stock", 0)
        actual_drop = before[mid] - after
        ok = abs(actual_drop - used) < 0.001
        all_ok = all_ok and ok
        mark = "OK  " if ok else "FAIL"
        print(f"  [{mark}] {name}: {before[mid]} -> {after} (dropped {actual_drop}, expected {used})")

    print()
    if all_ok:
        print("Sale flow works: the sale synced and deducted exactly what the recipe says.")
    else:
        print("Stock didn't move as expected. Things worth checking:")
        print("  - does the recipe's menu name match the Loyverse item name exactly?")
        print("  - did the sync run (processed count above should be at least 1)?")
        print("  - is the app pointed at the same store_id you passed here?")
        sys.exit(1)

    print(f"\nNote: receipt #{receipt_no} is a real sale in your Loyverse history.")


if __name__ == "__main__":
    main()
