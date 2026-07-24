"""
Tests for step 4.1 - the vision adapter's parsing and the fallback chain.

Runs offline with no API key: the parsing helpers are tested directly,
and the chain is tested with fake providers. Run with:

    cd backend
    python tests/test_vision.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.gemini_vision import _parse_json, _clean_items, _to_float
from core.vision_chain import VisionChain
from core.vision_provider import VisionProvider, VisionError

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


# ------------------------------------------------------- JSON extraction

def test_plain_json():
    section("Plain JSON parses")
    out = _parse_json('{"supplier": "Makro", "items": []}')
    check("supplier read", out["supplier"], "Makro")


def test_json_in_code_fence():
    section("JSON wrapped in a markdown fence still parses")
    text = '```json\n{"supplier": "Makro", "items": []}\n```'
    check("fence stripped", _parse_json(text)["supplier"], "Makro")


def test_json_with_surrounding_chatter():
    section("Model adds a sentence anyway - salvage the JSON")
    text = 'นี่คือผลลัพธ์ครับ:\n{"supplier": "ตลาดสด", "items": []}\nหวังว่าจะช่วยได้'
    check("object extracted", _parse_json(text)["supplier"], "ตลาดสด")


def test_unparseable_raises():
    section("Genuinely unreadable output raises rather than guessing")
    try:
        _parse_json("ขออภัย ไม่สามารถอ่านรูปนี้ได้")
        check("should have raised", False, True)
    except VisionError:
        check("raised VisionError", True, True)


# ------------------------------------------------------ number cleaning

def test_number_parsing():
    section("Numbers arrive in messy shapes")
    check("plain number", _to_float(350), 350.0)
    check("string number", _to_float("350"), 350.0)
    check("thousands separator", _to_float("1,250.50"), 1250.50)
    check("number with unit attached", _to_float("12 kg"), 12.0)
    check("currency symbol", _to_float("฿250"), 250.0)
    check("none stays none", _to_float(None), None)
    check("empty string", _to_float(""), None)


# --------------------------------------------------------- item cleaning

def test_items_cleaning():
    section("Line items are normalized")
    items = _clean_items([
        {"name": " กุ้งสด ", "qty": 2, "unit": "kg", "price": "350", "confidence": 0.9},
        {"name": "ข้าวสาร", "qty": "20", "unit": " กก. ", "price": 28, "confidence": 0.8},
    ])
    check("both kept", len(items), 2)
    check("name trimmed", items[0]["name"], "กุ้งสด")
    check("price coerced to number", items[0]["price"], 350.0)
    check("unit trimmed", items[1]["unit"], "กก.")
    check("qty coerced", items[1]["qty"], 20.0)


def test_unusable_lines_dropped():
    section("Lines with no usable quantity are dropped")
    items = _clean_items([
        {"name": "กุ้งสด", "qty": 2, "price": 350},
        {"name": "ส่วนลด", "qty": None, "price": -50},      # no qty
        {"name": "ยอดรวม", "qty": 0, "price": 1000},         # zero qty
        "not even a dict",                                    # malformed
    ])
    check("only the real product line kept", len(items), 1)
    check("kept the right one", items[0]["name"], "กุ้งสด")


# ---------------------------------------------------------- chain logic

class FakeProvider(VisionProvider):
    def __init__(self, name, api_key="key", result=None, error=None):
        self.name = name
        self.api_key = api_key
        self._result = result
        self._error = error
        self.called = False

    def read_invoice(self, image_bytes, mime_type="image/jpeg"):
        self.called = True
        if self._error:
            raise VisionError(self._error)
        return dict(self._result)


def test_chain_uses_first_working():
    section("Chain stops at the first provider that works")
    first = FakeProvider("first", result={"supplier": "A", "items": []})
    second = FakeProvider("second", result={"supplier": "B", "items": []})
    out = VisionChain([first, second]).read_invoice(b"img")

    check("used the first", out["supplier"], "A")
    check("never called the second", second.called, False)


def test_chain_falls_through_on_error():
    section("A rate-limited provider falls through to the next")
    first = FakeProvider("gemini", error="เกินโควต้าชั่วคราว (429)")
    second = FakeProvider("backup", result={"supplier": "B", "items": []})
    out = VisionChain([first, second]).read_invoice(b"img")

    check("second answered", out["supplier"], "B")
    check("fallback recorded", out.get("fallback_from"), ["gemini"])


def test_chain_skips_unconfigured():
    section("A provider with no API key is skipped, not treated as broken")
    unconfigured = FakeProvider("no-key", api_key=None)
    working = FakeProvider("working", result={"supplier": "C", "items": []})
    chain = VisionChain([unconfigured, working])

    check("only configured ones listed", chain.available_providers(), ["working"])
    check("chain still works", chain.read_invoice(b"img")["supplier"], "C")
    check("unconfigured never called", unconfigured.called, False)


def test_chain_with_nothing_configured():
    section("No providers configured gives a clear message")
    chain = VisionChain([FakeProvider("none", api_key=None)])
    try:
        chain.read_invoice(b"img")
        check("should have raised", False, True)
    except VisionError as e:
        check("mentions the missing key", "GEMINI_API_KEY" in str(e), True)


def test_chain_all_fail():
    section("Every provider failing reports each reason")
    chain = VisionChain([
        FakeProvider("gemini", error="429"),
        FakeProvider("backup", error="timeout"),
    ])
    try:
        chain.read_invoice(b"img")
        check("should have raised", False, True)
    except VisionError as e:
        msg = str(e)
        check("names both providers", "gemini" in msg and "backup" in msg, True)


def main():
    print("Running vision tests (offline, no API key needed)")

    test_plain_json()
    test_json_in_code_fence()
    test_json_with_surrounding_chatter()
    test_unparseable_raises()
    test_number_parsing()
    test_items_cleaning()
    test_unusable_lines_dropped()
    test_chain_uses_first_working()
    test_chain_falls_through_on_error()
    test_chain_skips_unconfigured()
    test_chain_with_nothing_configured()
    test_chain_all_fail()

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
