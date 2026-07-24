"""
Tests for step 4.4 - unit conversion. Offline, no dependencies. Run with:

    cd backend
    python tests/test_unit_conversion.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.unit_conversion import normalize_unit, convert_quantity, apply_unit_conversion

_results = []


def check(label, actual, expected, tolerance=0.001):
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        ok = abs(actual - expected) < tolerance
    else:
        ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


def test_normalization():
    section("Recognizing unit spellings - Thai, English, and shorthand")
    check("kg dot", normalize_unit("กก."), "kg")
    check("kg no dot", normalize_unit("กก"), "kg")
    check("english kg", normalize_unit("kg"), "kg")
    check("english KG uppercase", normalize_unit("KG"), "kg")
    check("gram thai", normalize_unit("กรัม"), "g")
    check("EA (invoice shorthand)", normalize_unit("EA"), "piece")
    check("bottle thai", normalize_unit("ขวด"), "bottle")
    check("unrecognized returns None", normalize_unit("แกลลอน-มั้ง"), None)
    check("empty returns None", normalize_unit(""), None)
    check("none returns None", normalize_unit(None), None)


def test_weight_conversion():
    section("Weight converts with a fixed, universal factor")
    result = convert_quantity(2, "kg", "g")
    check("status", result["status"], "converted")
    check("2kg -> 2000g", result["qty"], 2000)

    result = convert_quantity(500, "g", "kg")
    check("500g -> 0.5kg", result["qty"], 0.5)


def test_volume_conversion():
    section("Volume converts the same way")
    result = convert_quantity(1.5, "l", "ml")
    check("1.5L -> 1500ml", result["qty"], 1500)


def test_same_unit_different_spelling():
    section("Same real unit, different spelling - no conversion needed")
    result = convert_quantity(5, "กก.", "kg")
    check("status is same", result["status"], "same")
    check("qty untouched", result["qty"], 5)


def test_weight_and_volume_dont_mix():
    section("Weight and volume are never interchangeable, even if both known")
    result = convert_quantity(1, "kg", "ml")
    check("refuses to convert", result["status"], "unconvertible")


def test_count_units_dont_guess():
    section("Count units with different real sizes are never auto-converted")
    result = convert_quantity(1, "bottle", "box")
    check("refuses to convert bottle->box", result["status"], "unconvertible")

    result = convert_quantity(1, "ขวด", "ชิ้น")
    check("refuses to convert ขวด->ชิ้น", result["status"], "unconvertible")


def test_unrecognized_unit():
    section("An unrecognized spelling is flagged, not silently ignored")
    result = convert_quantity(1, "แกลลอน", "kg")
    check("status", result["status"], "unrecognized")


def test_apply_conversion_updates_price_correctly():
    section("Converting qty also converts price-per-unit so the total stays right")
    item = {"name": "กุ้ง", "qty": 2, "unit": "kg", "price": 300, "confidence": 0.9}
    # material stores shrimp in grams
    converted = apply_unit_conversion(item, "g")

    check("qty converted", converted["qty"], 2000)
    check("price converted (was per-kg, now per-gram)", converted["price"], 0.3)
    check("unit updated", converted["unit"], "g")

    original_total = 2 * 300
    new_total = converted["qty"] * converted["price"]
    check("total value preserved", new_total, original_total)


def test_apply_conversion_same_unit_untouched():
    section("Applying conversion when units already match changes nothing")
    item = {"name": "ข้าวสาร", "qty": 20, "unit": "kg", "price": 28}
    converted = apply_unit_conversion(item, "kg")

    check("qty unchanged", converted["qty"], 20)
    check("price unchanged", converted["price"], 28)
    check("status recorded as same", converted["unit_conversion"]["status"], "same")


def test_apply_conversion_flags_unconvertible_without_blocking():
    section("An unconvertible unit is flagged but the item isn't dropped")
    item = {"name": "น้ำปลา", "qty": 3, "unit": "ขวด", "price": 45}
    converted = apply_unit_conversion(item, "มล.")

    check("original qty left as-is (not guessed)", converted["qty"], 3)
    check("original price left as-is", converted["price"], 45)
    check("flagged for review", converted["unit_conversion"]["status"], "unconvertible")
    check("item still has a name (not dropped)", converted["name"], "น้ำปลา")


def main():
    print("Running unit conversion tests (offline)")

    test_normalization()
    test_weight_conversion()
    test_volume_conversion()
    test_same_unit_different_spelling()
    test_weight_and_volume_dont_mix()
    test_count_units_dont_guess()
    test_unrecognized_unit()
    test_apply_conversion_updates_price_correctly()
    test_apply_conversion_same_unit_untouched()
    test_apply_conversion_flags_unconvertible_without_blocking()

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
