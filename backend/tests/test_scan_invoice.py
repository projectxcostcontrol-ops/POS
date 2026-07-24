"""
Point the scanner at a real photo of a delivery note and see what comes back.

This is how you find out whether the AI actually reads YOUR suppliers'
invoices well - the offline tests only prove the parsing works, not that
the reading is accurate.

Usage:
    source venv/bin/activate

    # straight to Gemini, no backend needed:
    python tests/test_scan_invoice.py path/to/invoice.jpg

    # or through your running backend (tests the real endpoint):
    python tests/test_scan_invoice.py path/to/invoice.jpg <store_id> [api_url]

Needs GEMINI_API_KEY in backend/.env for the direct mode. Get a free key
at https://aistudio.google.com/apikey (no credit card).

Nothing is written to stock - this only reads.
"""

import json
import os
import sys

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp", ".pdf": "application/pdf",
}


def scan_direct(path: str) -> dict:
    from core.vision_chain import build_default_chain

    chain = build_default_chain()
    configured = chain.available_providers()
    if not configured:
        print("No vision provider configured.")
        print("Add GEMINI_API_KEY to backend/.env, then re-run.")
        print("Free key (no card): https://aistudio.google.com/apikey")
        sys.exit(1)
    print(f"Providers in fallback order: {', '.join(configured)}")

    with open(path, "rb") as f:
        image_bytes = f.read()
    mime = MIME_BY_EXT.get(os.path.splitext(path)[1].lower(), "image/jpeg")
    return chain.read_invoice(image_bytes, mime)


def scan_via_backend(path: str, store_id: str, api_url: str) -> dict:
    url = f"{api_url}/api/{store_id}/receiving/scan"
    mime = MIME_BY_EXT.get(os.path.splitext(path)[1].lower(), "image/jpeg")
    with open(path, "rb") as f:
        resp = requests.post(url, files={"file": (os.path.basename(path), f, mime)}, timeout=120)
    if not resp.ok:
        print(f"Backend returned {resp.status_code}: {resp.text[:400]}")
        sys.exit(1)
    return resp.json()


def show(result: dict):
    print(f"\nRead by: {result.get('provider')}")
    if result.get("fallback_from"):
        print(f"  (fell back from: {', '.join(result['fallback_from'])})")

    print("\nHeader:")
    print(f"  supplier: {result.get('supplier')}")
    print(f"  invoice:  {result.get('invoice')}")
    print(f"  date:     {result.get('date')}")

    items = result.get("items") or []
    print(f"\nLine items ({len(items)}):")
    if not items:
        print("  (none read - check the photo is sharp and the whole note is in frame)")

    low_confidence = []
    for i, item in enumerate(items, 1):
        conf = item.get("confidence")
        conf_str = f"{conf:.0%}" if isinstance(conf, (int, float)) else "?"
        flag = ""
        if isinstance(conf, (int, float)) and conf < 0.7:
            flag = "  <-- low confidence, check this one"
            low_confidence.append(item.get("name"))
        price = item.get("price")
        price_str = f"฿{price:,.2f}/unit" if isinstance(price, (int, float)) else "no price"
        print(f"  {i}. {item.get('name')}: {item.get('qty')} {item.get('unit')} @ {price_str} ({conf_str}){flag}")

    if low_confidence:
        print(f"\n{len(low_confidence)} line(s) the model wasn't sure about: {', '.join(low_confidence)}")
        print("In the app these get highlighted so you can correct them before confirming.")

    print("\nWhat to check against the actual paper:")
    print("  - is every product line present, and none invented?")
    print("  - is `price` the per-unit price, not the line total?")
    print("  - do the units match how you store that material?")

    if "--raw" in sys.argv:
        print("\nRaw model output:")
        print(result.get("raw_text", "")[:2000])
    else:
        print("\n(add --raw to see the model's raw output)")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)

    path = args[0]
    if not os.path.exists(path):
        print(f"File not found: {path}")
        sys.exit(1)

    print(f"Scanning {path}")

    if len(args) >= 2:
        store_id = args[1]
        api_url = args[2] if len(args) > 2 else "http://127.0.0.1:8000"
        print(f"Going through the backend at {api_url}")
        result = scan_via_backend(path, store_id, api_url)
    else:
        print("Calling the vision provider directly")
        result = scan_direct(path)

    show(result)
    print(f"\nFull JSON:\n{json.dumps(result, ensure_ascii=False, indent=2)[:1500]}")


if __name__ == "__main__":
    main()
