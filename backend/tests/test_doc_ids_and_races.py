"""
Tests for two problems that share a shape: they are invisible until the
day they aren't.

DOCUMENT IDS MADE FROM TYPED TEXT
Firestore will not accept just any string as a document id - no "/", not
"." or "..", 1,500 bytes at most. Recipes, categories and skips were all
stored under the menu's name, and materials under a slug of the material
name built in the browser. "ชา/กาแฟ" is an ordinary thing to call a menu
item and an impossible document id, so saving one failed outright, with
an error from inside the SDK that mentions neither menus nor names. It
worked for every shop until the first one that sold coffee and tea off
one line.

READ-MODIFY-WRITE ON SHARED DOCUMENTS
Two places read a value, changed part of it, and wrote the whole thing
back. Whoever saved second erased what the first had written in between -
no error, no sign, just missing data. One of them is the stock count,
which is the single job in this system that two people genuinely do at
the same time.

Offline, in-memory. Run with:

    cd backend
    python tests/test_doc_ids_and_races.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fake_firestore import make_test_store, FakeDb
from storage.firestore_store import doc_key, is_safe_doc_id

_results = []


def check(label, actual, expected):
    ok = actual == expected
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")


def section(title):
    print(f"\n=== {title} ===")


AWKWARD = ["ชา/กาแฟ", "..", ".", "__proto__", "a" * 2000, "น้ำปลา/ซีอิ๊ว"]


def test_awkward_names_produce_usable_ids():
    section("Names Firestore would reject still get a usable id")
    for name in AWKWARD:
        check(f"{name[:14]!r} is not used raw", is_safe_doc_id(name), False)
        key = doc_key(name)
        check(f"{name[:14]!r} becomes usable", is_safe_doc_id(key), True)

    check("distinct names get distinct ids",
          len({doc_key(n) for n in AWKWARD}), len(AWKWARD))
    check("and the id is stable across calls",
          doc_key("ชา/กาแฟ"), doc_key("ชา/กาแฟ"))


def test_ordinary_names_stay_their_own_id():
    section("An ordinary name is still its own id - so old data needs no migration")
    # Every recipe written before this existed is stored under its raw
    # name. Keeping that means nothing has to be migrated, and the console
    # still reads as menu names rather than hashes.
    for name in ["ผัดไท", "Pad Thai", "ข้าวผัด 1", "ต้มยำกุ้ง (ใหญ่)"]:
        check(f"{name!r} unchanged", doc_key(name), name)


def test_a_recipe_for_an_awkward_menu_round_trips():
    section("A recipe for ชา/กาแฟ saves, reads back, and appears in the recipe book")
    store = make_test_store(db=FakeDb())
    store.set_recipe("b1", "ชา/กาแฟ", [{"material_id": "m1", "qty": 0.02}])
    store.set_recipe("b1", "ผัดไท", [{"material_id": "m2", "qty": 0.1}])

    check("read back by name", store.get_recipe("b1", "ชา/กาแฟ"),
          [{"material_id": "m1", "qty": 0.02}])
    # all_recipes is what a sales report looks the sold menu up in, so it
    # has to be keyed by the NAME, which for this menu is not the id.
    check("keyed by name in the recipe book",
          sorted(store.all_recipes("b1")), ["ชา/กาแฟ", "ผัดไท"])
    check("the ordinary one is unaffected",
          store.all_recipes("b1")["ผัดไท"], [{"material_id": "m2", "qty": 0.1}])


def test_awkward_names_work_everywhere_a_name_is_a_key():
    section("Categories, drafts and skips handle the same names")
    store = make_test_store(db=FakeDb())

    store.set_item_category("b1", "ชา/กาแฟ", "cat1")
    check("category assignment reads back",
          store.get_item_categories("b1").get("ชา/กาแฟ"), "cat1")

    store.set_recipe_draft("b1", "ชา/กาแฟ", "cooked", [{"name": "ผงชา"}])
    check("draft reads back",
          (store.get_recipe_draft("b1", "ชา/กาแฟ") or {}).get("kind"), "cooked")
    store.delete_recipe_draft("b1", "ชา/กาแฟ")
    check("and deletes", store.get_recipe_draft("b1", "ชา/กาแฟ"), None)

    store.skip_recipe("b1", "ชา/กาแฟ")
    check("skip lists the real name", store.list_recipe_skips("b1"), ["ชา/กาแฟ"])
    store.unskip_recipe("b1", "ชา/กาแฟ")
    check("and unskips", store.list_recipe_skips("b1"), [])


def test_a_material_id_that_cannot_work_is_refused_clearly():
    section("A material id with a slash is refused, by name")
    store = make_test_store(db=FakeDb())
    try:
        store.upsert_material("b1", "น้ำปลา/ซีอิ๊ว", {"name": "น้ำปลา", "unit": "ขวด"})
        check("should have refused", True, False)
    except ValueError as e:
        check("refused", "น้ำปลา/ซีอิ๊ว" in str(e), True)


def test_two_people_counting_at_once_keep_both_counts():
    section("Two people counting different shelves keep both counts")
    # The real scenario: one person takes the dry store, one takes the
    # fridge, both have the session open. The old code read the whole
    # entries map, set one key and wrote it all back - so the second save
    # erased everything the first had entered since their own read, with
    # nothing to show it had happened.
    store = make_test_store(db=FakeDb())
    session = store.create_count_session("b1", "2026-08-25T09:00:00.000Z")
    sid = session["id"]

    # Interleaved on purpose: each "person" acts without re-reading.
    store.set_count_entry("b1", sid, "rice", 18)
    store.set_count_entry("b1", sid, "shrimp", 2.5)
    store.set_count_entry("b1", sid, "oil", 3)
    store.set_count_entry("b1", sid, "milk", 6)

    entries = store.get_count_session("b1", sid)["entries"]
    check("all four survived", sorted(entries), ["milk", "oil", "rice", "shrimp"])
    check("with the right numbers", entries["shrimp"], 2.5)

    store.clear_count_entry("b1", sid, "oil")
    entries = store.get_count_session("b1", sid)["entries"]
    check("clearing removes only that one", sorted(entries), ["milk", "rice", "shrimp"])


def test_learning_two_aliases_at_once_keeps_both():
    section("Confirming a delivery learns every line's alias, not the last one")
    # Confirming a scanned invoice learns an alias per matched line. The
    # old version read the list, appended one and wrote the list back, so
    # names learned close together overwrote each other.
    store = make_test_store(db=FakeDb())
    store.upsert_material("b1", "m1", {"name": "กุ้งขาว", "unit": "kg"})

    store.add_alias("b1", "m1", "กุ้ง ขาว")
    store.add_alias("b1", "m1", "SHRIMP WHITE")
    store.add_alias("b1", "m1", "กุ้ง ขาว")   # already known

    aliases = store.material_snapshot("b1", "m1")["aliases"]
    check("both names kept", sorted(aliases), sorted(["กุ้ง ขาว", "SHRIMP WHITE"]))
    check("and no duplicate", len(aliases), 2)

    store.remove_alias("b1", "m1", "SHRIMP WHITE")
    check("removing takes one only",
          store.material_snapshot("b1", "m1")["aliases"], ["กุ้ง ขาว"])


def main():
    print("Running doc-id and race tests (offline)")

    test_awkward_names_produce_usable_ids()
    test_ordinary_names_stay_their_own_id()
    test_a_recipe_for_an_awkward_menu_round_trips()
    test_awkward_names_work_everywhere_a_name_is_a_key()
    test_a_material_id_that_cannot_work_is_refused_clearly()
    test_two_people_counting_at_once_keep_both_counts()
    test_learning_two_aliases_at_once_keeps_both()

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
