"""
Tests for step 4.3 - draft receiving: create draft -> edit -> confirm (or
discard). Offline, in-memory. Run with:

    cd backend
    python tests/test_draft_receiving.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store
from core.matching_engine import MatchingEngine

STORE_ID = "test-store"
_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


def setup():
    store = make_test_store()
    store.upsert_material(STORE_ID, "shrimp", {"name": "กุ้ง", "unit": "กก.", "par": 5})
    store.upsert_material(STORE_ID, "rice", {"name": "ข้าวสาร", "unit": "กก.", "par": 10})
    return store, MatchingEngine(store)


def confirm_draft(store, matcher, draft):
    """Mirrors the confirm endpoint's logic, so this test exercises the
    same behaviour without needing FastAPI running."""
    receiving_items, skipped = [], []
    for item in draft["items"]:
        material_id = (item.get("match") or {}).get("material_id")
        if not material_id:
            skipped.append(f"{item['name']} (ยังไม่ได้เลือกวัตถุดิบ)")
            continue
        price = item.get("price")
        if price is None:
            skipped.append(f"{item['name']} (ไม่มีราคา)")
            continue
        receiving_items.append({
            "material_id": material_id,
            "quantity": item.get("qty", 0),
            "unit_cost": price,
        })
        matcher.learn(STORE_ID, item["name"], material_id, draft.get("supplier"))

    if not receiving_items:
        return None, skipped

    result = store.add_receiving(STORE_ID, draft.get("supplier") or "", draft.get("date") or "",
                                  receiving_items)
    store.delete_draft(STORE_ID, draft["id"])
    return result, skipped


def test_draft_created_from_scan_shape():
    section("A draft holds the scan output plus per-item matches")
    store, matcher = setup()

    scanned_items = [
        {"name": "กุ้ง", "qty": 2, "unit": "kg", "price": 300, "confidence": 0.95},
        {"name": "ของแปลกๆ", "qty": 1, "unit": "ea", "price": 50, "confidence": 0.4},
    ]
    matched = matcher.match_all(STORE_ID, scanned_items)
    draft = store.create_draft(STORE_ID, "Makro", "INV001", "2026-07-20", matched,
                               raw_text="...", provider="gemini")

    check("draft has an id", "id" in draft, True)
    check("first item auto-matched", draft["items"][0]["match"]["matched"], True)
    check("second item unmatched, has suggestions field", "suggestions" in draft["items"][1]["match"], True)

    fetched = store.get_draft(STORE_ID, draft["id"])
    check("draft is retrievable", fetched["supplier"], "Makro")
    check("status is draft", fetched["status"], "draft")


def test_confirm_updates_stock_and_deletes_draft():
    section("Confirming a fully-matched draft updates stock and removes the draft")
    store, matcher = setup()

    items = [{"name": "กุ้ง", "qty": 3, "unit": "kg", "price": 280, "confidence": 0.9}]
    matched = matcher.match_all(STORE_ID, items)
    draft = store.create_draft(STORE_ID, "Makro", None, "2026-07-20", matched)

    result, skipped = confirm_draft(store, matcher, draft)
    check("no items skipped", skipped, [])
    check("receiving created", result is not None, True)

    material = store.list_materials(STORE_ID)[0]
    check("stock updated", [m["stock"] for m in store.list_materials(STORE_ID) if m["id"] == "shrimp"][0], 3)
    check("draft gone after confirm", store.get_draft(STORE_ID, draft["id"]), None)
    check("no longer in the draft list", store.list_drafts(STORE_ID), [])


def test_user_picks_a_match_before_confirming():
    section("An unmatched line the user manually assigns still confirms correctly")
    store, matcher = setup()

    items = [{"name": "กุ้งลาย", "qty": 2, "unit": "kg", "price": 300, "confidence": 0.8}]
    matched = matcher.match_all(STORE_ID, items)
    draft = store.create_draft(STORE_ID, "ตลาดสด", None, "2026-07-20", matched)
    check("starts unmatched", draft["items"][0]["match"]["matched"], False)

    # simulate the frontend calling pickMaterial() then saving the draft
    draft["items"][0]["match"] = {"matched": True, "material_id": "shrimp",
                                  "material_name": "กุ้ง", "via": "manual"}
    store.update_draft(STORE_ID, draft["id"], {"items": draft["items"]})

    refetched = store.get_draft(STORE_ID, draft["id"])
    result, skipped = confirm_draft(store, matcher, refetched)
    check("confirmed successfully", skipped, [])
    check("stock updated via manual pick", store.list_materials(STORE_ID)[0]["stock"], 2)

    # the manual pick should have been learned - same wording matches automatically now
    second_match = matcher.match(STORE_ID, "กุ้งลาย", supplier="ตลาดสด")
    check("manual pick was learned as a supplier alias", second_match["matched"], True)


def test_unmatched_items_are_skipped_not_lost_silently():
    section("Confirming with some items still unmatched skips only those")
    store, matcher = setup()

    items = [
        {"name": "กุ้ง", "qty": 1, "unit": "kg", "price": 300, "confidence": 0.9},
        {"name": "สิ่งลึกลับ", "qty": 5, "unit": "ea", "price": 10, "confidence": 0.3},
    ]
    matched = matcher.match_all(STORE_ID, items)
    draft = store.create_draft(STORE_ID, "Makro", None, "2026-07-20", matched)

    result, skipped = confirm_draft(store, matcher, draft)
    check("one item skipped", skipped, ["สิ่งลึกลับ (ยังไม่ได้เลือกวัตถุดิบ)"])
    stored = store.list_receivings(STORE_ID)[0]
    check("the matched item still went through", stored["items"][0]["material_id"], "shrimp")
    check("stock updated for the matched item only", store.list_materials(STORE_ID)[0]["stock"], 1)


def test_confirming_nothing_matched_returns_no_receiving():
    section("A draft where nothing is matched produces no receiving")
    store, matcher = setup()
    items = [{"name": "งงมาก", "qty": 1, "unit": "ea", "price": 1, "confidence": 0.2}]
    matched = matcher.match_all(STORE_ID, items)
    draft = store.create_draft(STORE_ID, "Makro", None, "2026-07-20", matched)

    result, skipped = confirm_draft(store, matcher, draft)
    check("nothing confirmed", result, None)
    check("everything skipped", skipped, ["งงมาก (ยังไม่ได้เลือกวัตถุดิบ)"])


def test_discard_removes_draft_without_touching_stock():
    section("Discarding a draft leaves stock untouched")
    store, matcher = setup()
    items = [{"name": "กุ้ง", "qty": 10, "unit": "kg", "price": 300, "confidence": 0.9}]
    matched = matcher.match_all(STORE_ID, items)
    draft = store.create_draft(STORE_ID, "Makro", None, "2026-07-20", matched)

    store.delete_draft(STORE_ID, draft["id"])

    check("draft gone", store.get_draft(STORE_ID, draft["id"]), None)
    check("stock never moved", store.list_materials(STORE_ID)[0]["stock"], 0)


def test_multiple_drafts_stay_independent():
    section("Two drafts from two scans don't interfere with each other")
    store, matcher = setup()
    d1 = store.create_draft(STORE_ID, "Makro", None, "2026-07-01",
                            matcher.match_all(STORE_ID, [{"name": "กุ้ง", "qty": 1, "unit": "kg", "price": 300}]))
    d2 = store.create_draft(STORE_ID, "ตลาดสด", None, "2026-07-02",
                            matcher.match_all(STORE_ID, [{"name": "ข้าวสาร", "qty": 5, "unit": "kg", "price": 28}]))

    check("both listed", len(store.list_drafts(STORE_ID)), 2)

    confirm_draft(store, matcher, store.get_draft(STORE_ID, d1["id"]))
    remaining = store.list_drafts(STORE_ID)
    check("one confirmed, one remains", len(remaining), 1)
    check("the remaining one is d2", remaining[0]["id"], d2["id"])


def fill_missing_prices(store, ledger, items):
    """Mirrors the API's _fill_missing_prices for testing without FastAPI."""
    out = []
    for item in items:
        if item.get("price") is not None:
            out.append({**item, "price_source": "scanned"})
            continue
        material_id = (item.get("match") or {}).get("material_id")
        suggested = ledger.average_cost(STORE_ID, material_id) if material_id else None
        if suggested is not None:
            out.append({**item, "price": suggested, "price_source": "history"})
        else:
            out.append({**item, "price_source": "missing"})
    return out


def test_missing_price_uses_history_not_zero():
    section("A line with no price falls back to the material's known average cost, not zero")
    from storage.movement_ledger import MovementLedger
    store, matcher = setup()
    ledger = MovementLedger(store)
    # shrimp has a purchase history at 260/kg
    ledger.record_receive(STORE_ID, "shrimp", 5, 260)

    items = [{"name": "กุ้ง", "qty": 2, "unit": "kก.", "price": None, "confidence": 0.9}]
    matched = matcher.match_all(STORE_ID, items)
    filled = fill_missing_prices(store, ledger, matched)

    check("price backfilled from history", filled[0]["price"], 260)
    check("flagged as history-sourced, not silently accepted", filled[0]["price_source"], "history")


def test_missing_price_with_no_history_is_flagged_not_zeroed():
    section("A line with no price and no purchase history is flagged, never defaults to 0")
    from storage.movement_ledger import MovementLedger
    store, matcher = setup()
    ledger = MovementLedger(store)
    # rice has never been received before - no cost history exists

    items = [{"name": "ข้าวสาร", "qty": 10, "unit": "kg", "price": None, "confidence": 0.9}]
    matched = matcher.match_all(STORE_ID, items)
    filled = fill_missing_prices(store, ledger, matched)

    check("price stays None (not silently zeroed)", filled[0]["price"], None)
    check("flagged as missing", filled[0]["price_source"], "missing")


def test_confirm_skips_lines_with_no_price():
    section("Confirming skips a line with no price rather than recording cost 0")
    store, matcher = setup()
    items = [
        {"name": "กุ้ง", "qty": 2, "unit": "kg", "price": 300, "confidence": 0.9},
        {"name": "ข้าวสาร", "qty": 5, "unit": "kg", "price": None, "confidence": 0.9},  # no price, no history
    ]
    matched = matcher.match_all(STORE_ID, items)
    draft = store.create_draft(STORE_ID, "Makro", None, "2026-07-20", matched)

    result, skipped = confirm_draft(store, matcher, draft)
    check("shrimp line went through", result is not None, True)
    check("rice line skipped for missing price", len(skipped), 1)
    check("rice stock untouched", [m for m in store.list_materials(STORE_ID) if m["id"] == "rice"][0]["stock"], 0)


def test_confirm_allows_explicit_zero_price():
    section("An explicitly-entered price of 0 (a free sample) is allowed through, unlike a missing price")
    store, matcher = setup()
    items = [{"name": "กุ้ง", "qty": 1, "unit": "kg", "price": 0, "confidence": 0.9}]  # user typed 0 on purpose
    matched = matcher.match_all(STORE_ID, items)
    draft = store.create_draft(STORE_ID, "ตัวอย่างฟรี", None, "2026-07-20", matched)

    result, skipped = confirm_draft(store, matcher, draft)
    check("not skipped - 0 is a deliberate value, not missing", skipped, [])
    check("receiving created", result is not None, True)


def main():
    print("Running draft receiving tests (offline, no Firebase needed)")

    test_draft_created_from_scan_shape()
    test_confirm_updates_stock_and_deletes_draft()
    test_user_picks_a_match_before_confirming()
    test_unmatched_items_are_skipped_not_lost_silently()
    test_confirming_nothing_matched_returns_no_receiving()
    test_discard_removes_draft_without_touching_stock()
    test_multiple_drafts_stay_independent()
    test_missing_price_uses_history_not_zero()
    test_missing_price_with_no_history_is_flagged_not_zeroed()
    test_confirm_skips_lines_with_no_price()
    test_confirm_allows_explicit_zero_price()

    passed = sum(1 for r in _results if r)
    total = len(_results)
    print(f"\n{'=' * 50}")
    print(f"{passed}/{total} checks passed")
    if passed != total:
        print("SOME CHECKS FAILED")
        sys.exit(1)
    print("All good.")


if __name__ == "__main__":
    main()
