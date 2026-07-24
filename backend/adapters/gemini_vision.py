from __future__ import annotations

"""
Gemini implementation of VisionProvider.

Chosen as the first provider because its free tier covers a restaurant's
volume comfortably (a few deliveries a day, well under the daily cap) and
it reads Thai invoices well.

Note on the free tier: Google may use free-tier data for model training.
Delivery notes contain supplier names and purchase prices - not usually
sensitive for a restaurant, but worth knowing. Switching to a paid tier
(or another provider) only means changing which adapter is registered.
"""

import base64
import json
import os
import re

import requests

from core.vision_provider import VisionProvider, VisionError

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
# Google's model lineup moves fast - if this 404s again, check
# https://ai.google.dev/gemini-api/docs/models for the current name and
# either update this default or set GEMINI_MODEL in .env to override it.
DEFAULT_MODEL = "gemini-3.5-flash"

PROMPT = """คุณคือระบบอ่านใบส่งของ/ใบกำกับภาษีของร้านอาหาร

อ่านรูปนี้แล้วตอบกลับเป็น JSON เท่านั้น ห้ามมีข้อความอื่น ห้ามใส่ markdown code fence

รูปแบบ JSON:
{
  "supplier": "ชื่อผู้ขาย หรือ null ถ้าอ่านไม่ได้",
  "invoice": "เลขที่ใบส่งของ หรือ null",
  "date": "วันที่ในรูปแบบ YYYY-MM-DD หรือ null",
  "items": [
    {
      "name": "ชื่อสินค้าตามที่เขียนในใบส่งของ",
      "qty": ตัวเลขจำนวน,
      "unit": "หน่วย เช่น kg, กก., ชิ้น, ขวด",
      "price": ราคาต่อหน่วย (ตัวเลข),
      "confidence": ความมั่นใจ 0.0-1.0
    }
  ]
}

กฎสำคัญ:
- price ต้องเป็นราคาต่อหน่วย ไม่ใช่ราคารวมของบรรทัดนั้น
  ถ้าใบส่งของแสดงแต่ราคารวม ให้หารด้วยจำนวนเอง
- ถ้าตัวเลขไหนอ่านไม่ชัด ให้ใส่ confidence ต่ำ (ต่ำกว่า 0.7) อย่าเดาแบบมั่นใจ
- ถ้าไม่แน่ใจว่าบรรทัดนั้นเป็นสินค้าหรือไม่ (เช่น ส่วนลด ภาษี ยอดรวม) ให้ข้ามไป
- ตอบเป็น JSON ล้วนเท่านั้น
"""


class GeminiVisionAdapter(VisionProvider):
    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

    def read_invoice(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
        if not self.api_key:
            raise VisionError("GEMINI_API_KEY ยังไม่ได้ตั้งค่า")

        payload = {
            "contents": [{
                "parts": [
                    {"text": PROMPT},
                    {"inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(image_bytes).decode(),
                    }},
                ]
            }],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                # A grocery delivery note can run to 30+ lines. The default
                # output cap is far below what that costs in JSON, and when
                # the model runs out mid-object the whole scan used to fail
                # over a missing closing brace - after reading the invoice
                # correctly. Room to finish is cheaper than that.
                "maxOutputTokens": 8192,
            },
        }

        url = f"{API_BASE}/{self.model}:generateContent"
        try:
            resp = requests.post(url, json=payload, timeout=90,
                                 headers={"x-goog-api-key": self.api_key})
        except requests.RequestException as e:
            raise VisionError(f"เรียก Gemini ไม่สำเร็จ: {e}") from e

        if resp.status_code == 429:
            # quota/rate limit - the caller should try the next provider
            raise VisionError("Gemini เกินโควต้าชั่วคราว (429)")
        if resp.status_code == 404:
            raise VisionError(
                f"ไม่พบโมเดล '{self.model}' (404) - Google อาจเปลี่ยนชื่อโมเดลแล้ว "
                f"เช็คชื่อปัจจุบันที่ https://ai.google.dev/gemini-api/docs/models "
                f"แล้วตั้ง GEMINI_MODEL ใน .env เป็นชื่อใหม่"
            )
        if not resp.ok:
            raise VisionError(f"Gemini ตอบกลับ {resp.status_code}: {resp.text[:300]}")

        body = resp.json()
        raw_text = _extract_text(body)
        truncated = _hit_output_limit(body)
        parsed, salvaged = _parse_json(raw_text, allow_salvage=truncated)

        warning = None
        if salvaged:
            # Say so plainly. A short delivery note that silently lost its
            # last two lines is worse than a failed scan, because nothing
            # prompts anyone to look.
            warning = ("AI อ่านใบนี้ไม่จบ (รายการยาวเกินไป) - "
                       "รายการท้ายใบอาจขาดหายไป กรุณาเทียบกับรูปก่อนกด Confirm")

        return {
            "supplier": parsed.get("supplier"),
            "invoice": parsed.get("invoice"),
            "date": parsed.get("date"),
            "items": _clean_items(parsed.get("items") or []),
            "raw_text": raw_text,
            "provider": self.name,
            "warning": warning,
        }


def _extract_text(response: dict) -> str:
    """Pull the text out of Gemini's response envelope."""
    try:
        parts = response["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError) as e:
        raise VisionError(f"อ่านผลลัพธ์จาก Gemini ไม่ได้: {str(response)[:300]}") from e


def _hit_output_limit(response: dict) -> bool:
    """Did Gemini stop because it ran out of room rather than because it
    finished? That distinction decides whether a broken JSON string is
    garbage or just an unfinished good answer."""
    try:
        return response["candidates"][0].get("finishReason") == "MAX_TOKENS"
    except (KeyError, IndexError):
        return False


def _parse_json(text: str, allow_salvage: bool = False) -> tuple[dict, bool]:
    """Returns (parsed, was_salvaged).

    Models sometimes wrap JSON in a code fence or add a stray sentence
    despite being told not to - recover the JSON object rather than
    failing the whole scan over formatting.

    Salvage of a *truncated* reply is separate and deliberately gated on
    allow_salvage: repairing malformed JSON on a guess would risk turning
    a genuinely garbled answer into confident-looking data. We only do it
    when Gemini has told us it stopped early, and even then we keep only
    the line items that were completely written."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    try:
        return json.loads(text), False
    except json.JSONDecodeError:
        pass

    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0)), False
        except json.JSONDecodeError:
            pass

    if allow_salvage:
        repaired = _repair_truncated(text)
        if repaired is not None:
            return repaired, True

    raise VisionError(f"ผลลัพธ์ไม่ใช่ JSON ที่อ่านได้: {text[:300]}")


def _repair_truncated(text: str) -> dict | None:
    """Close off a reply that was cut mid-write, keeping every complete
    item and discarding the half-written one at the end.

    The header fields (supplier, invoice, date) come before the items in
    the requested format, so they survive; what gets lost is the tail of
    the list, which is exactly what the caller warns about."""
    items_key = text.find('"items"')
    if items_key == -1:
        return None
    open_bracket = text.find("[", items_key)
    if open_bracket == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    last_complete_item = None

    for i in range(open_bracket + 1, len(text)):
        char = text[i]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                last_complete_item = i
        elif char == "]" and depth == 0:
            return None   # the array did close - truncation is elsewhere

    if last_complete_item is None:
        return None   # not even one whole item survived

    try:
        return json.loads(text[:last_complete_item + 1] + "]}")
    except json.JSONDecodeError:
        return None


def _clean_items(items: list) -> list[dict]:
    """Normalize each line and drop ones with no usable quantity - a line
    the model couldn't read a number from can't become stock anyway."""
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        qty = _to_float(item.get("qty"))
        if qty is None or qty <= 0:
            continue
        cleaned.append({
            "name": (item.get("name") or "").strip(),
            "qty": qty,
            "unit": (item.get("unit") or "").strip(),
            "price": _to_float(item.get("price")),
            "confidence": _to_float(item.get("confidence")),
        })
    return cleaned


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        # handles "1,250.00" and "12 kg" style values
        cleaned = re.sub(r"[^\d.\-]", "", str(value))
        return float(cleaned) if cleaned else None
    except ValueError:
        return None
