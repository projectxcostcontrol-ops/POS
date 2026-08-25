from __future__ import annotations

"""
Suggests which ingredients a menu item probably uses, so filling in a
restaurant's recipes doesn't start from a blank page for every dish.

What this deliberately does NOT do: guess quantities.

A recipe here isn't documentation - it drives automatic stock deduction
and gross-profit figures. "Fried rice uses 200g of rice" is a plausible
sentence and an unverifiable one: the number depends entirely on how
this kitchen portions, and nobody can tell a guessed 200 from a measured
200 once it's saved. So the model proposes WHICH ingredients and in what
unit; every quantity is typed by a person who knows the kitchen.

The one exception is resale items - bottled water, canned drinks, snacks
bought in and sold on. Selling one bottle consumes exactly one bottle,
which isn't a portioning judgement at all, so a 1:1 recipe is safe to
propose complete. That also means these items get proper stock tracking
and real margins instead of being skipped as "no recipe needed".
"""

import json
import os
import re

import requests

from core.vision_provider import VisionError

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.5-flash"

PROMPT = """คุณคือผู้ช่วยของร้านอาหาร ทำหน้าที่เดาว่าเมนูแต่ละอย่างใช้วัตถุดิบอะไรบ้าง

ฉันจะให้รายชื่อเมนู คุณต้องตอบกลับเป็น JSON เท่านั้น ห้ามมีข้อความอื่น ห้ามใส่ markdown code fence

รูปแบบ JSON:
{
  "menus": [
    {
      "menu": "ชื่อเมนูตามที่ได้รับมา ห้ามแก้ไขตัวอักษร",
      "kind": "cooked" หรือ "resale" หรือ "service",
      "ingredients": [
        {"name": "ชื่อวัตถุดิบภาษาไทย", "unit": "หน่วยที่ร้านมักเก็บ เช่น กรัม, มล., ฟอง, ขวด, ต้น"}
      ]
    }
  ]
}

ความหมายของ kind:
- "cooked"  = อาหารที่ปรุงเอง ต้องมีวัตถุดิบหลายอย่าง เช่น ข้าวผัด ต้มยำ
- "resale"  = ซื้อมาขายไปทั้งชิ้น ไม่ได้ปรุง เช่น น้ำเปล่า เบียร์ ขนมซอง บุหรี่
              ให้ใส่ ingredients เป็นตัวสินค้านั้นเองรายการเดียว
              เช่น เมนู "น้ำเปล่า 600ml" -> ingredients: [{"name": "น้ำเปล่า 600ml", "unit": "ขวด"}]
- "service" = ไม่ใช่ของกิน ไม่ต้องตัดสต๊อก เช่น ค่าบริการ ค่าเปิดขวด ค่าส่ง
              ให้ใส่ ingredients เป็น []

กฎสำคัญ:
- ห้ามใส่ปริมาณหรือตัวเลขจำนวนใด ๆ ทั้งสิ้น เจ้าของร้านจะกรอกเอง
- ชื่อวัตถุดิบต้องเป็นภาษาไทยเสมอ ถึงแม้ชื่อเมนูจะเป็นภาษาอังกฤษ
  (เพราะต้องนำไปจับคู่กับคลังวัตถุดิบที่เป็นภาษาไทย)
- ใส่เฉพาะวัตถุดิบหลักที่ใช้จริงและตัดสต๊อกได้ ไม่ต้องใส่ น้ำ เกลือ น้ำแข็ง
  หรือเครื่องปรุงจิ๊บจ๊อยที่ร้านไม่นับสต๊อก
- ถ้าไม่แน่ใจว่าเมนูคืออะไร ให้ใส่ kind เป็น "cooked" และ ingredients เป็น []
- ต้องตอบทุกเมนูที่ได้รับ ครบทุกอัน เรียงตามลำดับเดิม

รายชื่อเมนู:
"""


class RecipeSuggester:
    """Text-only Gemini call. Kept separate from the invoice vision adapter
    because they fail for different reasons and are worth swapping out
    independently."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

    def available(self) -> bool:
        return bool(self.api_key)

    def suggest(self, menu_names: list[str]) -> list[dict]:
        """Returns one entry per menu name:
            {menu, kind, ingredients: [{name, unit}]}

        Menus the model skipped or renamed are returned with empty
        ingredients rather than dropped, so the caller always gets a
        result for everything it asked about."""
        if not self.api_key:
            raise VisionError("GEMINI_API_KEY ยังไม่ได้ตั้งค่า - ตั้งค่าก่อนถึงจะใช้ AI ช่วยร่างสูตรได้")
        if not menu_names:
            return []

        listing = "\n".join(f"- {name}" for name in menu_names)
        payload = {
            "contents": [{"parts": [{"text": PROMPT + listing}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
                "maxOutputTokens": 8192,
            },
        }

        url = f"{API_BASE}/{self.model}:generateContent"
        try:
            resp = requests.post(url, json=payload, timeout=120,
                                 headers={"x-goog-api-key": self.api_key})
        except requests.RequestException as e:
            raise VisionError(f"เรียก AI ไม่สำเร็จ: {e}") from e

        if resp.status_code == 429:
            raise VisionError("AI เกินโควต้าชั่วคราว - รอสักครู่แล้วลองใหม่")
        if not resp.ok:
            raise VisionError(f"AI ตอบกลับ {resp.status_code}: {resp.text[:300]}")

        parsed = _parse_json(_extract_text(resp.json()))
        return _align_to_request(menu_names, parsed.get("menus") or [])


def _extract_text(response: dict) -> str:
    try:
        parts = response["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError) as e:
        raise VisionError(f"อ่านผลลัพธ์จาก AI ไม่ได้: {str(response)[:300]}") from e


def _parse_json(text: str) -> dict:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass
    raise VisionError(f"ผลลัพธ์จาก AI ไม่ใช่ JSON ที่อ่านได้: {text[:300]}")


VALID_KINDS = ("cooked", "resale", "service")


def _align_to_request(requested: list[str], returned: list) -> list[dict]:
    """Match the model's answers back to the menu names we asked about.

    Models occasionally reorder, reword, or skip entries. Trusting the
    order would silently attach one dish's ingredients to another - a
    quiet, plausible-looking wrong answer, which is the worst kind here
    because a chicken recipe under a pork dish reads fine until stock
    goes wrong. So we match by name and drop anything unrecognized."""
    by_name = {}
    for entry in returned:
        if isinstance(entry, dict) and entry.get("menu"):
            by_name[str(entry["menu"]).strip()] = entry

    aligned = []
    for name in requested:
        entry = by_name.get(name.strip()) or {}
        kind = entry.get("kind")
        if kind not in VALID_KINDS:
            kind = "cooked"
        aligned.append({
            "menu": name,
            "kind": kind,
            "ingredients": _clean_ingredients(entry.get("ingredients") or [], kind),
        })
    return aligned


def _clean_ingredients(ingredients: list, kind: str) -> list[dict]:
    """Strip anything quantity-shaped that slipped through despite the
    prompt. A stray number reaching the form would be indistinguishable
    from one the owner typed, which is exactly the confusion this feature
    is built to avoid.

    Resale items are the exception: qty 1 is a fact about selling one
    bottle, not a portioning guess, so it's filled in and marked."""
    cleaned = []
    for ing in ingredients:
        if not isinstance(ing, dict):
            continue
        name = (ing.get("name") or "").strip()
        if not name:
            continue
        cleaned.append({
            "name": name,
            "unit": (ing.get("unit") or "").strip(),
            "qty": 1 if kind == "resale" else None,
            "qty_prefilled": kind == "resale",
        })
    return cleaned
