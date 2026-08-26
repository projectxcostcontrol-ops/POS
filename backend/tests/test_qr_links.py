"""Offline checks for editable public QR links and privacy-safe scan totals."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from storage.firestore_store import Store
from tests.fake_firestore import FakeDb

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def root_store():
    store = Store.__new__(Store)
    store.db = FakeDb()
    store.tenant_id = None
    return store


def test_editable_destination():
    store = root_store()
    store.set_qr_link("hong-duck", {
        "label": "ฮง เป็ดย่าง",
        "target_url": "https://rankrua.vercel.app/menu/hong-duck",
        "enabled": True,
        "total_scans": 0,
    })
    store.set_qr_link("hong-duck", {"target_url": "https://example.com/new-menu"})
    link = store.get_qr_link("hong-duck")
    check("destination changes without changing slug", link["target_url"], "https://example.com/new-menu")
    check("existing enabled state is preserved", link["enabled"], True)


def test_scan_statistics():
    store = root_store()
    store.set_qr_link("hong-duck", {"target_url": "https://example.com", "total_scans": 0})
    store.record_qr_scan("hong-duck", "hospital-a", "2026-08-26", "2026-08-26T08:00:00Z")
    store.record_qr_scan("hong-duck", "hospital-a", "2026-08-26", "2026-08-26T09:00:00Z")
    store.record_qr_scan("hong-duck", "office-b", "2026-08-26", "2026-08-26T10:00:00Z")

    stats = store.get_qr_stats("hong-duck")
    spots = {row["spot"]: row["total"] for row in stats["daily"][0]["spots"]}
    check("total scans increment atomically", stats["link"]["total_scans"], 3)
    check("daily total", stats["daily"][0]["total"], 3)
    check("counts split by installation spot", spots, {"hospital-a": 2, "office-b": 1})
    check("no IP or device fields are stored", set(stats["daily"][0]) & {"ip", "user_agent", "device"}, set())


if __name__ == "__main__":
    test_editable_destination()
    test_scan_statistics()
    passed = sum(_results)
    print(f"\n{passed}/{len(_results)} checks passed")
    raise SystemExit(0 if all(_results) else 1)
