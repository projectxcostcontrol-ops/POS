"""
Tests for step 4.5 - image storage. Offline, no real Firebase Storage
needed (that part is mocked / skipped by design in emulator mode). Run:

    cd backend
    python tests/test_image_store.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


def test_emulator_mode_skips_storage():
    section("Emulator mode skips image storage entirely, doesn't error")
    os.environ["USE_FIREBASE_EMULATOR"] = "true"
    from storage.image_store import upload_receipt_image, delete_receipt_image

    result = upload_receipt_image("store1", b"fake image bytes", "image/jpeg")
    check("upload returns None in emulator mode", result, None)

    # must not raise even though nothing is configured
    try:
        delete_receipt_image("receipts/store1/whatever.jpg")
        check("delete doesn't raise in emulator mode", True, True)
    except Exception:
        check("delete doesn't raise in emulator mode", False, True)

    del os.environ["USE_FIREBASE_EMULATOR"]


def test_missing_storage_config_fails_gracefully():
    section("No real Firebase configured - upload fails soft, returns None, doesn't crash the caller")
    os.environ["USE_FIREBASE_EMULATOR"] = "false"
    # deliberately no FIREBASE_CREDENTIALS_JSON/PATH set, so firebase_admin.storage will error
    import importlib
    import storage.image_store as image_store_module
    importlib.reload(image_store_module)

    result = image_store_module.upload_receipt_image("store1", b"fake bytes")
    check("upload returns None rather than raising", result, None)

    del os.environ["USE_FIREBASE_EMULATOR"]


def test_draft_stores_image_fields_when_present():
    section("A draft created with an image path keeps it; without one, it's None")
    from tests.fake_firestore import make_test_store

    store = make_test_store()
    with_image = store.create_draft("store1", "Makro", None, "2026-07-20", [],
                                     image_path="receipts/store1/abc123.jpg")
    check("image_path kept", with_image["image_path"], "receipts/store1/abc123.jpg")

    without_image = store.create_draft("store1", "Makro", None, "2026-07-20", [])
    check("image_path defaults to None", without_image["image_path"], None)

    fetched = store.get_draft("store1", with_image["id"])
    check("image_path survives a round trip through storage", fetched["image_path"], "receipts/store1/abc123.jpg")


def main():
    print("Running image store tests (offline)")

    test_emulator_mode_skips_storage()
    test_missing_storage_config_fails_gracefully()
    test_draft_stores_image_fields_when_present()

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
