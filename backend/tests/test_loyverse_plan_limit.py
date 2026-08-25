"""
Tests for how the Loyverse client handles the free plan's 31-day
sales-history limit.

Loyverse refuses receipts older than 31 days with a 402
PAYMENT_REQUIRED - and it does so *midway through pagination*, after
several pages of perfectly good recent receipts have already come back.
The bug these cover: treating that refusal as a total failure threw away
everything already fetched, so a screen that should have shown the last
month of sales showed nothing at all.

Offline, in-memory. Run with:

    cd backend
    python tests/test_loyverse_plan_limit.py
"""

import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests

from adapters._loyverse_client import (LoyverseClient, FREE_PLAN_HISTORY_DAYS,
                                       _days_ago, normalize_time)

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


def make_client(pages):
    """A client whose _get walks a canned list of pages. An entry that is
    an int is raised as an HTTPError with that status instead."""
    client = LoyverseClient.__new__(LoyverseClient)
    client.token = "test"
    client.session = None
    state = {"i": 0}
    calls = []

    def fake_get(path, params=None):
        calls.append(dict(params or {}))
        page = pages[state["i"]]
        state["i"] += 1
        if isinstance(page, int):
            resp = requests.Response()
            resp.status_code = page
            raise requests.HTTPError(f"{page} error", response=resp)
        return page

    client._get = fake_get
    client._calls = calls
    return client


def test_receipts_already_fetched_survive_a_402_on_a_later_page():
    section("A 402 partway through pagination keeps the pages already fetched")
    # This is the production failure: page 1 returned real receipts, page 2
    # was refused because it reached past 31 days, and the whole request
    # died - showing an empty sales screen to someone whose recent sales
    # had loaded fine.
    client = make_client([
        {"receipts": [{"receipt_number": "1-1001"}, {"receipt_number": "1-1002"}],
         "cursor": "abc"},
        402,
    ])

    out = client.get_receipts()

    check("the recent receipts are returned, not discarded", len(out), 2)
    check("and they're the right ones", out[0]["receipt_number"], "1-1001")


def test_a_402_on_the_very_first_page_still_raises():
    section("A 402 with nothing fetched is a real failure, not partial success")
    # Returning an empty list here would be indistinguishable from "this
    # branch made no sales" - a quiet wrong answer. With nothing to
    # salvage, the caller needs to know the plan refused the request so it
    # can say so.
    client = make_client([402])

    try:
        client.get_receipts()
        check("raised", False, True)
    except requests.HTTPError as e:
        check("raised", True, True)
        check("with the status the caller checks for", e.response.status_code, 402)


def test_other_errors_are_never_swallowed():
    section("Only 402 is treated as a plan limit - other errors still raise")
    # Silently returning partial data on a 500 or a 401 would hide a real
    # outage or a bad token behind a screen that merely looks a bit empty.
    client = make_client([
        {"receipts": [{"receipt_number": "1-1001"}], "cursor": "abc"},
        500,
    ])

    try:
        client.get_receipts()
        check("a 500 mid-pagination still raises", False, True)
    except requests.HTTPError:
        check("a 500 mid-pagination still raises", True, True)


def test_receipts_default_to_the_plan_window():
    section("With no date given, the query asks for a recent window - not everything")
    # Without this the fetch paginates backwards until Loyverse refuses,
    # which costs a round trip to learn a limit we already know about.
    client = make_client([{"receipts": [], "cursor": None}])
    client.get_receipts()

    sent = client._calls[0]
    check("a created_at_min was set", "created_at_min" in sent, True)
    check("in Loyverse's required format",
          bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z",
                            sent["created_at_min"])), True)


def test_the_window_stays_inside_the_cliff_edge():
    section("The default window is 30 days, not 31 - the limit is a cliff, not a range")
    # Loyverse refuses anything "created earlier than 31 days ago".
    # Requesting exactly 31 lands ON that boundary and the whole request
    # is refused, because the timestamp is already fractionally too old by
    # the time it arrives. This is the bug that made the previous fix look
    # like it had done nothing.
    check("window leaves a day of headroom", FREE_PLAN_HISTORY_DAYS, 30)
    check("and is safely under the refusal threshold", FREE_PLAN_HISTORY_DAYS < 31, True)


def test_a_too_old_explicit_date_is_retried_within_the_allowed_window():
    section("A date beyond the plan's reach is retried against what IS allowed")
    # A sync cursor can easily be older than the plan allows (a branch
    # that sat idle, say). Failing outright would strand it forever, since
    # the cursor only advances after a successful fetch. Retrying narrowed
    # lets the branch recover on its own.
    client = make_client([
        402,                                        # the far-back request
        {"receipts": [{"receipt_number": "1"}], "cursor": None},   # the retry
    ])

    out = client.get_receipts(created_at_min="2020-01-01T00:00:00.000Z")

    check("data comes back rather than an error", len(out), 1)
    check("the first attempt used the caller's date",
          client._calls[0]["created_at_min"], "2020-01-01T00:00:00.000Z")
    check("the retry narrowed to the allowed window",
          client._calls[1]["created_at_min"] > "2026-01-01", True)


def test_a_recent_date_is_not_retried():
    section("A 402 on an already-recent date isn't retried - there's nowhere narrower to go")
    # Retrying the same window would just fail again, and looping on a
    # refusal is worse than reporting it.
    client = make_client([402])

    try:
        client.get_receipts()
        check("raises instead of retrying forever", False, True)
    except requests.HTTPError:
        check("raises instead of retrying forever", True, True)
    check("only one attempt was made", len(client._calls), 1)


def test_an_explicit_date_is_respected():
    section("An explicit created_at_min overrides the default window")
    # Accounts on Unlimited sales history can reach further back, and the
    # sync cursor passes its own date - neither should be overwritten.
    client = make_client([{"receipts": [], "cursor": None}])
    client.get_receipts(created_at_min="2026-01-01T00:00:00.000Z")

    check("the caller's date is used", client._calls[0]["created_at_min"],
          "2026-01-01T00:00:00.000Z")


def test_pagination_still_completes_normally():
    section("Nothing about the plan handling breaks an ordinary multi-page fetch")
    client = make_client([
        {"receipts": [{"receipt_number": "1"}], "cursor": "p2"},
        {"receipts": [{"receipt_number": "2"}], "cursor": "p3"},
        {"receipts": [{"receipt_number": "3"}], "cursor": None},
    ])

    out = client.get_receipts()
    check("all pages collected", [r["receipt_number"] for r in out], ["1", "2", "3"])
    check("the cursor was passed along", client._calls[1]["cursor"], "p2")


def test_every_timestamp_is_normalized_at_the_door():
    section("Whatever format a caller passes, Loyverse receives its own")
    # Python's isoformat() gives '+00:00' and microseconds; Loyverse
    # rejects both with a flat INVALID_VALUE. That mistake was made in
    # three separate call sites before the conversion was moved here, to
    # the one place every request passes through. Callers can now be
    # careless and still be correct.
    pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"

    for raw in ["2026-08-23T00:00:00+00:00",          # isoformat, no micros
                "2026-08-23T00:00:00.123456+00:00",   # isoformat with micros
                "2026-08-23T00:00:00.000Z",           # already correct
                "2026-08-23T00:00:00"]:               # naive, no zone
        out = normalize_time(raw)
        check(f"{raw[:28]:28} normalizes",
              bool(re.fullmatch(pattern, out)), True)

    check("None passes through untouched", normalize_time(None), None)


def test_a_callers_bad_format_never_reaches_the_api():
    section("A sloppy caller can't send a rejected timestamp any more")
    client = make_client([{"receipts": [], "cursor": None}])
    client.get_receipts(created_at_min="2026-08-23T00:00:00+00:00")

    sent = client._calls[0]["created_at_min"]
    check("what actually went out is in Loyverse's format",
          bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", sent)), True)
    check("and it's the same moment", sent, "2026-08-23T00:00:00.000Z")


def main():
    print("Running Loyverse plan-limit tests (offline, no network)")

    test_receipts_already_fetched_survive_a_402_on_a_later_page()
    test_a_402_on_the_very_first_page_still_raises()
    test_other_errors_are_never_swallowed()
    test_every_timestamp_is_normalized_at_the_door()
    test_a_callers_bad_format_never_reaches_the_api()
    test_receipts_default_to_the_plan_window()
    test_the_window_stays_inside_the_cliff_edge()
    test_a_too_old_explicit_date_is_retried_within_the_allowed_window()
    test_a_recent_date_is_not_retried()
    test_an_explicit_date_is_respected()
    test_pagination_still_completes_normally()

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
