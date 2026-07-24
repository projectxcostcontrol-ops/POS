from __future__ import annotations

"""
Matches names read off an invoice (by AI, or typed by hand) to materials
in our own catalog. Materials already ARE the product catalog - no
separate "Product" entity needed, since a material's `aliases` field
plus per-supplier aliases cover what the ChatGPT plan called
Product/Alias/SupplierAlias.

Match order (first hit wins, most specific first):
  1. supplier alias    - THIS supplier's exact wording for THIS material
                          e.g. Makro always writes "กุ้งสด" for our "กุ้ง"
  2. general alias      - any supplier's known alternate name
  3. material name       - exact match on the material's own name
  4. fuzzy suggestion    - nothing exact matched; offer close guesses by
                          string similarity for the user to pick from

Only 1-3 auto-match. Fuzzy matches are never applied automatically - one
wrong auto-match silently miscounts stock, and the whole point of the
draft-then-confirm flow is to catch that before it does.

When the user picks a suggestion (or types a new material), record it as
a supplier alias so the same wording never needs asking again.
"""

from difflib import SequenceMatcher

SUGGESTION_COUNT = 3
SUGGESTION_MIN_SCORE = 0.35  # below this, not even worth suggesting


class MatchingEngine:
    def __init__(self, store):
        self.store = store

    def match(self, store_id: str, raw_name: str, supplier: str | None = None) -> dict:
        """Returns:
          {matched: True, material_id, material_name, via: "supplier_alias"|"alias"|"name"}
        or:
          {matched: False, suggestions: [{material_id, name, score}, ...]}
        """
        normalized = _normalize(raw_name)
        materials = self.store.list_materials(store_id)

        if supplier:
            alias_material_id = self.store.get_supplier_alias(store_id, supplier, normalized)
            if alias_material_id:
                mat = next((m for m in materials if m["id"] == alias_material_id), None)
                if mat:
                    return {"matched": True, "material_id": mat["id"],
                            "material_name": mat["name"], "via": "supplier_alias"}

        for mat in materials:
            candidates = [mat["name"]] + mat.get("aliases", [])
            if any(_normalize(c) == normalized for c in candidates):
                return {"matched": True, "material_id": mat["id"],
                        "material_name": mat["name"], "via": "alias" if mat["name"] != raw_name else "name"}

        return {"matched": False, "suggestions": self._suggest(normalized, materials)}

    def _suggest(self, normalized: str, materials: list[dict]) -> list[dict]:
        scored = []
        for mat in materials:
            candidates = [mat["name"]] + mat.get("aliases", [])
            best = max(_similarity(normalized, _normalize(c)) for c in candidates)
            if best >= SUGGESTION_MIN_SCORE:
                scored.append({"material_id": mat["id"], "name": mat["name"], "score": round(best, 2)})
        scored.sort(key=lambda s: s["score"], reverse=True)
        return scored[:SUGGESTION_COUNT]

    def match_all(self, store_id: str, items: list[dict], supplier: str | None = None) -> list[dict]:
        """Runs match() over every line from a scan result, attaching the
        match info to each item without losing the original AI fields."""
        results = []
        for item in items:
            match = self.match(store_id, item.get("name", ""), supplier)
            results.append({**item, "match": match})
        return results

    def learn(self, store_id: str, raw_name: str, material_id: str, supplier: str | None = None):
        """Remember this mapping so the same wording auto-matches next time.
        Supplier-specific when we know the supplier (most precise); falls
        back to a general alias otherwise."""
        normalized = _normalize(raw_name)
        if supplier:
            self.store.set_supplier_alias(store_id, supplier, normalized, material_id)
        else:
            self.store.add_alias(store_id, material_id, raw_name)


def _normalize(name: str) -> str:
    return " ".join((name or "").strip().split())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()
