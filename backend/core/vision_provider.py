from __future__ import annotations

"""
Vision provider interface (a "port", same idea as PosProvider).

The receiving flow only ever talks to this interface, never to Gemini
(or any other vision API) directly. That's what lets us fall back to a
second provider when the first is rate-limited, or swap providers
entirely, without the matching engine or the API layer changing.

The shape returned by read_invoice() is fixed regardless of provider:

    {
      "supplier": "Makro",
      "invoice": "MK12345",
      "date": "2026-07-23",
      "items": [
        {"name": "กุ้งสด", "qty": 2, "unit": "kg", "price": 350,
         "confidence": 0.94}
      ],
      "raw_text": "...",       # what the provider actually saw
      "provider": "gemini",     # which one answered
    }

Every field can be None/missing - invoices are messy, and the user
reviews a draft before anything is committed, so a partial read is
still useful. Downstream code must not assume any field is present.
"""

from abc import ABC, abstractmethod


class VisionProvider(ABC):
    name: str = "unknown"

    @abstractmethod
    def read_invoice(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
        """Read a delivery note / invoice image and return the structured
        dict described above. Raises on failure so the caller can fall
        through to the next provider."""


class VisionError(Exception):
    """Raised when a provider can't produce a usable result - quota hit,
    network failure, or output that isn't parseable."""
