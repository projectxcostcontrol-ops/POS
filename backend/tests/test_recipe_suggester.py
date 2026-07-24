"""
Tests for V3 step 3.3 - AI recipe suggestions.

The whole feature rests on one promise: a suggestion never becomes a
number nobody typed. Most of these tests defend that promise from the
directions it can actually break - the model ignoring instructions,
reordering its answer, or dropping entries.

Offline, in-memory. Run with:

    cd backend
    python tests/test_recipe_suggester.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store
from core.recipe_suggester import (_align_to_request, _clean_ingredients,
                                   _parse_json, VALID_KINDS)
from core.vision_provider import VisionError

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


def test_quantities_are_never_filled_for_cooked_dishes():
    section("A cooked dish comes back with NO quantity - the core promise")
    # If the model volunteers "200" for rice, it must not survive to the
    # form. Once saved, a guessed 200 looks exactly like a measured 200,
    # and it drives both stock deduction and gross profit from then on.
    ingredients = _clean_ingredients(
        [{"name": "ข้าวสวย", "unit": "กรัม", "qty": 200},
         {"name": "กุ้ง", "unit": "กรัม", "qty": 80}], "cooked")

    check("both ingredients kept", len(ingredients), 2)
    check("quantity stripped from the first", ingredients[0]["qty"], None)
    check("quantity stripped from the second", ingredients[1]["qty"], None)
    check("not marked prefilled", ingredients[0]["qty_prefilled"], False)
    check("name kept", ingredients[0]["name"], "ข้าวสวย")
    check("unit kept", ingredients[0]["unit"], "กรัม")


def test_resale_items_get_a_one_to_one_recipe():
    section("Resale goods DO get a quantity - selling one bottle uses one bottle")
    # This isn't a portioning judgement, so there's nothing to get wrong.
    # Filling it in is what lets bought-in stock be tracked and earn a real
    # margin instead of being written off as "no recipe needed".
    ingredients = _clean_ingredients([{"name": "น้ำเปล่า 600ml", "unit": "ขวด"}], "resale")

    check("one line", len(ingredients), 1)
    check("quantity is 1", ingredients[0]["qty"], 1)
    check("marked as prefilled", ingredients[0]["qty_prefilled"], True)


def test_service_items_have_no_ingredients():
    section("A service charge consumes nothing")
    check("no ingredients", _clean_ingredients([], "service"), [])


def test_ingredients_are_matched_back_by_name_not_order():
    section("Answers are matched by menu name - order is never trusted")
    # A model that reorders its reply would otherwise attach one dish's
    # ingredients to another. That failure is silent and reads perfectly
    # well: pork under a chicken dish looks like a normal recipe right up
    # until the wrong stock starts moving.
    requested = ["ข้าวผัดหมู", "ข้าวผัดไก่"]
    returned = [
        {"menu": "ข้าวผัดไก่", "kind": "cooked", "ingredients": [{"name": "ไก่", "unit": "กรัม"}]},
        {"menu": "ข้าวผัดหมู", "kind": "cooked", "ingredients": [{"name": "หมู", "unit": "กรัม"}]},
    ]
    aligned = _align_to_request(requested, returned)

    check("order follows the request", [a["menu"] for a in aligned], requested)
    check("pork dish got pork", aligned[0]["ingredients"][0]["name"], "หมู")
    check("chicken dish got chicken", aligned[1]["ingredients"][0]["name"], "ไก่")


def test_menus_the_model_skipped_still_come_back():
    section("Every requested menu gets an entry, even one the model ignored")
    # Returning fewer entries than asked for would leave the UI silently
    # missing a dish, which reads as "AI found nothing" rather than
    # "AI didn't answer".
    aligned = _align_to_request(
        ["ต้มยำกุ้ง", "แกงเขียวหวาน"],
        [{"menu": "ต้มยำกุ้ง", "kind": "cooked", "ingredients": [{"name": "กุ้ง", "unit": "กรัม"}]}])

    check("both menus present", len(aligned), 2)
    check("the skipped one is empty, not missing", aligned[1]["ingredients"], [])
    check("and still named correctly", aligned[1]["menu"], "แกงเขียวหวาน")


def test_renamed_menus_are_not_guessed_at():
    section("A menu the model reworded is dropped rather than fuzzy-matched")
    # Half-matching "ข้าวผัด" to "ข้าวผัดกุ้งพิเศษ" would be a guess about
    # which dish was meant. An empty suggestion costs a few keystrokes;
    # the wrong ingredients cost wrong stock.
    aligned = _align_to_request(
        ["ข้าวผัดกุ้งพิเศษ"],
        [{"menu": "ข้าวผัด", "kind": "cooked", "ingredients": [{"name": "ข้าว", "unit": "กรัม"}]}])

    check("original name preserved", aligned[0]["menu"], "ข้าวผัดกุ้งพิเศษ")
    check("no ingredients borrowed from the renamed entry", aligned[0]["ingredients"], [])


def test_unknown_kind_falls_back_to_cooked():
    section("An unrecognized kind becomes 'cooked' - the option that fills nothing in")
    # Falling back to "resale" would prefill a quantity of 1 on a dish that
    # isn't resale at all. Defaulting to the cautious option keeps a bad
    # label from becoming a bad number.
    aligned = _align_to_request(
        ["อะไรสักอย่าง"],
        [{"menu": "อะไรสักอย่าง", "kind": "ของแปลก",
          "ingredients": [{"name": "ของ", "unit": "ชิ้น"}]}])

    check("kind normalized", aligned[0]["kind"], "cooked")
    check("so no quantity is prefilled", aligned[0]["ingredients"][0]["qty"], None)


def test_malformed_entries_are_dropped_not_crashed_on():
    section("Junk inside the ingredient list is skipped quietly")
    ingredients = _clean_ingredients(
        ["ไม่ใช่ dict", {"unit": "กรัม"}, {"name": "  "},
         {"name": "หมูสับ", "unit": "กรัม"}], "cooked")

    check("only the usable one survives", len(ingredients), 1)
    check("and it's the right one", ingredients[0]["name"], "หมูสับ")


def test_json_parsing_handles_a_code_fence():
    section("A fenced reply still parses - models add fences despite being told not to")
    fenced = '```json\n{"menus": [{"menu": "ก", "kind": "cooked", "ingredients": []}]}\n```'
    check("fence stripped", _parse_json(fenced)["menus"][0]["menu"], "ก")


def test_unparseable_reply_raises():
    section("A reply that isn't JSON fails loudly rather than returning nothing")
    try:
        _parse_json("ขอโทษครับ ผมไม่เข้าใจคำถาม")
        check("raised", False, True)
    except VisionError:
        check("raised", True, True)


def test_drafts_and_skips_are_stored_per_branch():
    section("Drafts and skips are branch data, kept apart like everything else")
    store = make_test_store()
    store.set_recipe_draft("branch1", "ต้มยำกุ้ง", "cooked",
                           [{"name": "กุ้ง", "unit": "กรัม", "qty": None}])

    draft = store.get_recipe_draft("branch1", "ต้มยำกุ้ง")
    check("draft saved", draft["kind"], "cooked")
    check("quantity stays blank in storage", draft["ingredients"][0]["qty"], None)
    check("listed", len(store.list_recipe_drafts("branch1")), 1)
    check("another branch doesn't see it", store.list_recipe_drafts("branch2"), [])

    store.delete_recipe_draft("branch1", "ต้มยำกุ้ง")
    check("draft removed", store.get_recipe_draft("branch1", "ต้มยำกุ้ง"), None)


def test_skips_keep_the_missing_recipe_warning_meaningful():
    section("Skipping a service charge is remembered")
    # The point of skipping isn't to hide work - it's to keep the
    # "no recipe linked" warning trustworthy. A warning list padded with
    # corkage fees is a list nobody checks.
    store = make_test_store()
    store.skip_recipe("branch1", "ค่าเปิดขวด")

    check("skip recorded", store.list_recipe_skips("branch1"), ["ค่าเปิดขวด"])
    store.unskip_recipe("branch1", "ค่าเปิดขวด")
    check("and can be undone", store.list_recipe_skips("branch1"), [])


def test_kinds_are_the_three_we_handle():
    section("Only three kinds exist, and each has defined behaviour")
    check("kinds", sorted(VALID_KINDS), ["cooked", "resale", "service"])


def main():
    print("Running AI recipe suggestion tests (offline, no API key needed)")

    test_quantities_are_never_filled_for_cooked_dishes()
    test_resale_items_get_a_one_to_one_recipe()
    test_service_items_have_no_ingredients()
    test_ingredients_are_matched_back_by_name_not_order()
    test_menus_the_model_skipped_still_come_back()
    test_renamed_menus_are_not_guessed_at()
    test_unknown_kind_falls_back_to_cooked()
    test_malformed_entries_are_dropped_not_crashed_on()
    test_json_parsing_handles_a_code_fence()
    test_unparseable_reply_raises()
    test_drafts_and_skips_are_stored_per_branch()
    test_skips_keep_the_missing_recipe_warning_meaningful()
    test_kinds_are_the_three_we_handle()

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
