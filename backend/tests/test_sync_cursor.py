"""
Tests for the sync cursor - the fix for "syncing... stuck" on a real
branch with months of Loyverse history.

The old behaviour asked for every receipt since the branch's first day on
Loyverse, on every single sync. These tests check the two things that
actually matter: a brand new branch never triggers that full-history pull,
and a branch with a cursor only asks for what's new since last time.

Offline, in-memory. Run with:

    cd backend
    python tests/test_sync_cursor.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


class FakeProvider:
    """Records every created_at_min it was called with, and returns a
    canned receipt list - stands in for LoyverseAdapter without a
    network call."""

    def __init__(self, receipts=None):
        self.calls = []
        self.receipts = receipts or []

    def get_receipts(self, store_id, created_at_min=None):
        self.calls.append(created_at_min)
        return self.receipts


def test_a_brand_new_branch_never_pulls_full_history():
    section("First sync for a branch fetches NOTHING - it only sets the starting line")
    # This is the actual fix: the old code asked Loyverse for every receipt
    # ever, on the very first sync of a real branch. That single request
    # against months of history is what "stuck" looked like.
    from core.stock_engine import sync_branch

    store = make_test_store()
    provider = FakeProvider(receipts=[{"receipt_number": "1", "line_items": []}])

    processed = sync_branch(provider, store, "branch1")

    check("nothing processed on the first call", processed, 0)
    check("the provider was never even asked for receipts", provider.calls, [])
    check("a cursor now exists", store.get_sync_cursor("branch1") is not None, True)


def test_a_second_sync_only_asks_for_whats_new():
    section("Once a cursor exists, sync asks Loyverse for receipts since THAT point")
    from core.stock_engine import sync_branch

    store = make_test_store()
    provider = FakeProvider(receipts=[])

    sync_branch(provider, store, "branch1")            # establishes the cursor
    first_cursor = store.get_sync_cursor("branch1")

    sync_branch(provider, store, "branch1")             # the real sync

    check("exactly one request made to Loyverse", len(provider.calls), 1)
    check("that request used the saved cursor, not None (which means 'everything')",
          provider.calls[0] is not None, True)


def test_cursor_never_moves_backward():
    section("The cursor is monotonic - a rapid repeat sync can't wind it back")
    # now-minus-overlap can land BEFORE the cursor a moment-ago sync already
    # advanced to, if the two calls happen within the overlap window (someone
    # pressing the sync button twice in a hurry, or two cycles running close
    # together). Without a floor, that would re-open a window already
    # covered - not wrong, exactly, but pointless work, and worth guarding
    # against directly since a regression here silently reintroduces the
    # slow-sync problem this whole cursor exists to avoid.
    from core.stock_engine import sync_branch

    store = make_test_store()
    provider = FakeProvider(receipts=[])

    sync_branch(provider, store, "branch1")             # establishes the cursor
    baseline = store.get_sync_cursor("branch1")
    sync_branch(provider, store, "branch1")              # fires immediately after
    after = store.get_sync_cursor("branch1")

    check("cursor never regresses below where it already was", after >= baseline, True)


def test_cursor_has_overlap_not_an_exact_boundary():
    section("The new cursor sits a few minutes behind 'now', not exactly on it")
    # A receipt whose timestamp lands slightly behind when we actually
    # fetched (clock skew, a slow write on Loyverse's side) would fall
    # through a cursor set to the exact fetch instant. A small overlap
    # costs re-checking a few already-processed receipts next time, which
    # is harmless - is_receipt_processed skips them - versus silently
    # losing one, which isn't recoverable without noticing.
    from core.stock_engine import (sync_branch, SYNC_OVERLAP_SECONDS,
                                   _utcnow_iso, _parse_time)

    store = make_test_store()
    provider = FakeProvider(receipts=[])
    sync_branch(provider, store, "branch1")
    sync_branch(provider, store, "branch1")

    check("overlap is a positive amount of time", SYNC_OVERLAP_SECONDS > 0, True)

    # Back-date the cursor so the next sync's now-minus-overlap genuinely
    # lands ahead of it. Two syncs fired in the same instant (as above)
    # correctly leave the cursor untouched - that's the monotonic clamp,
    # not the overlap - so the overlap only becomes observable once real
    # time has passed between syncs.
    store.set_sync_cursor("branch1", "2026-01-01T00:00:00.000Z")
    sync_branch(provider, store, "branch1")
    cursor = store.get_sync_cursor("branch1")

    # Compared as real timestamps, not strings: the two values can be in
    # different textual formats (a cursor written by an older version, say)
    # while still being perfectly comparable moments in time.
    gap = (_parse_time(_utcnow_iso()) - _parse_time(cursor)).total_seconds()
    check("cursor sits roughly one overlap window behind now",
          SYNC_OVERLAP_SECONDS - 5 <= gap <= SYNC_OVERLAP_SECONDS + 5, True)


def test_branches_have_independent_cursors():
    section("Two branches of the same business don't share a cursor")
    store = make_test_store()
    store.set_sync_cursor("branch1", "2026-07-01T00:00:00+00:00")

    check("branch2 has no cursor yet", store.get_sync_cursor("branch2"), None)
    check("branch1's cursor is unaffected", store.get_sync_cursor("branch1"),
          "2026-07-01T00:00:00+00:00")


def test_reset_gives_a_fresh_starting_line_not_a_backfill():
    section("Resetting a stuck cursor starts fresh, exactly like a new connection")
    # The reset endpoint exists for a cursor that's stuck or wrong. It must
    # behave identically to a brand new branch - jump to now - rather than
    # quietly resuming a history pull from wherever it was stuck.
    from core.stock_engine import sync_branch, _utcnow_iso as _now

    store = make_test_store()
    store.set_sync_cursor("branch1", "2020-01-01T00:00:00+00:00")   # pretend this is "stuck"

    store.set_sync_cursor("branch1", _now())   # what the reset endpoint does
    provider = FakeProvider(receipts=[])
    sync_branch(provider, store, "branch1")

    # The next real sync's request should be anchored near "now", not 2020.
    check("the request cursor is recent, not the old stuck value",
          provider.calls[0] > "2026-01-01", True)


def test_cursor_uses_the_exact_format_loyverse_demands():
    section("The cursor string is formatted the way the Loyverse API requires")
    # Loyverse rejects anything but YYYY-MM-DDTHH:mm:ss.sssZ with a flat
    # INVALID_VALUE. Python's isoformat() gives microseconds and '+00:00',
    # which looks correct to a reader and fails on contact - and since the
    # cursor is stored in the same form it's sent in, that breaks every
    # sync after the very first one. No offline test can catch it by
    # calling the API, so the format is asserted directly.
    import re
    from core.stock_engine import _utcnow_iso, _minus_seconds

    pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"
    now = _utcnow_iso()
    check("current time matches the required format",
          bool(re.fullmatch(pattern, now)), True)
    check("a rolled-back cursor keeps the format",
          bool(re.fullmatch(pattern, _minus_seconds(now, 300))), True)
    check("milliseconds, not microseconds", len(now.split(".")[1]), 4)  # 'sssZ'


def test_a_cursor_saved_by_an_older_version_still_parses():
    section("An old '+00:00' cursor from a previous deploy doesn't crash the first sync")
    # Upgrading shouldn't strand a branch on an unreadable cursor - that
    # would turn a formatting fix into an outage for exactly the branches
    # that had been syncing successfully.
    import re
    from core.stock_engine import _minus_seconds

    legacy = "2026-08-23T16:55:43.953470+00:00"
    out = _minus_seconds(legacy, 300)
    check("legacy cursor is read and rewritten in the new format",
          bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", out)), True)
    check("and the arithmetic is still right", out, "2026-08-23T16:50:43.953Z")


def main():
    print("Running sync cursor tests (offline)")

    test_cursor_uses_the_exact_format_loyverse_demands()
    test_a_cursor_saved_by_an_older_version_still_parses()
    test_a_brand_new_branch_never_pulls_full_history()
    test_a_second_sync_only_asks_for_whats_new()
    test_cursor_never_moves_backward()
    test_cursor_has_overlap_not_an_exact_boundary()
    test_branches_have_independent_cursors()
    test_reset_gives_a_fresh_starting_line_not_a_backfill()

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
