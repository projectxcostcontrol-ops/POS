"""
Fills a store with realistic sample data so you can click around the UI
with something to look at. Unlike test_stock.py, this hits your ACTUAL
backend (emulator or production), so you can see the result in the app.

Usage:
    cd backend
    python tests/seed_sample_data.py <store_id> [api_url]

Find <store_id> in the app: Settings page, or the browser URL when a
store is selected. api_url defaults to http://127.0.0.1:8000.

Safe to re-run: it adds more deliveries rather than resetting anything.
To start clean, delete the store's data in the Firebase console (or
just restart the emulator, which wipes it).
"""

import sys
from datetime import datetime, timedelta, timezone

import requests

MATERIALS = [
    {"id": "shrimp", "name": "กุ้งสด", "unit": "กรัม", "par": 3000, "cost": 0.22},
    {"id": "rice", "name": "ข้าวสาร", "unit": "กก.", "par": 20, "cost": 28},
    {"id": "noodle", "name": "เส้นก๋วยเตี๋ยว", "unit": "กรัม", "par": 3000, "cost": 0.045},
    {"id": "egg", "name": "ไข่ไก่", "unit": "ชิ้น", "par": 60, "cost": 4.5},
    {"id": "morning-glory", "name": "ผักบุ้ง", "unit": "กรัม", "par": 2000, "cost": 0.015},
]

# two deliveries a month apart, at different prices - so monthly average
# cost has something real to separate
DELIVERIES = [
    {
        "days_ago": 45,
        "supplier": "Makro",
        "items": [
            {"material_id": "shrimp", "quantity": 5000, "unit_cost": 0.20},
            {"material_id": "rice", "quantity": 50, "unit_cost": 26},
            {"material_id": "noodle", "quantity": 4000, "unit_cost": 0.042},
        ],
    },
    {
        "days_ago": 10,
        "supplier": "ตลาดสด",
        "items": [
            {"material_id": "shrimp", "quantity": 3000, "unit_cost": 0.26},
            {"material_id": "egg", "quantity": 120, "unit_cost": 4.8},
            {"material_id": "morning-glory", "quantity": 5000, "unit_cost": 0.018},
        ],
    },
]

RECIPES = {
    "ต้มยำกุ้ง": [{"material_id": "shrimp", "qty": 150}],
    "ข้าวผัดกุ้ง": [
        {"material_id": "shrimp", "qty": 80},
        {"material_id": "rice", "qty": 0.25},
        {"material_id": "egg", "qty": 1},
    ],
    "ผัดไทย": [
        {"material_id": "noodle", "qty": 120},
        {"material_id": "shrimp", "qty": 60},
        {"material_id": "egg", "qty": 1},
    ],
    "ผัดผักบุ้ง": [{"material_id": "morning-glory", "qty": 200}],
}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    store_id = sys.argv[1]
    api_url = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000"
    base = f"{api_url}/api/{store_id}"

    print(f"Seeding store {store_id} at {api_url}\n")

    print("Materials:")
    for m in MATERIALS:
        body = {k: v for k, v in m.items() if k != "id"}
        r = requests.put(f"{base}/materials/{m['id']}", json=body)
        r.raise_for_status()
        print(f"  {m['name']}")

    print("\nDeliveries:")
    for d in DELIVERIES:
        date = (datetime.now(timezone.utc) - timedelta(days=d["days_ago"])).isoformat()
        r = requests.post(f"{base}/receivings", json={
            "supplier": d["supplier"], "date": date, "items": d["items"],
        })
        r.raise_for_status()
        total = sum(i["quantity"] * i["unit_cost"] for i in d["items"])
        print(f"  {d['supplier']} ({d['days_ago']} days ago) - {len(d['items'])} lines, ฿{total:,.2f}")

    print("\nRecipes:")
    for item_name, ingredients in RECIPES.items():
        r = requests.put(f"{base}/recipes/{item_name}", json=ingredients)
        r.raise_for_status()
        print(f"  {item_name} ({len(ingredients)} ingredients)")

    print("\nDone. Things worth checking in the app:")
    print("  - Materials page: stock and average cost came from the deliveries")
    print("  - 'ประวัติ' button on a material: the movement trail")
    print("  - Receiving page: both deliveries listed")
    print("  - Recipes page: cost per dish, calculated from ingredient prices")
    print("\nNote: recipe names must match your real Loyverse menu item names")
    print("to deduct on sale. Rename them in the script if yours differ.")


if __name__ == "__main__":
    main()
