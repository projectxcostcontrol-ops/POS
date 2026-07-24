from __future__ import annotations

"""
Tries vision providers in order until one succeeds.

The free tiers we're building on have real daily/per-minute caps, so a
single provider will occasionally refuse. Rather than failing the scan
and making the user retry, fall through to the next configured provider.

Providers with no API key configured are skipped rather than counted as
failures - that way you can run with only Gemini set up and add the
others later without changing any code.
"""

from core.vision_provider import VisionProvider, VisionError


class VisionChain(VisionProvider):
    name = "chain"

    def __init__(self, providers: list[VisionProvider]):
        self.providers = providers

    def available_providers(self) -> list[str]:
        return [p.name for p in self.providers if _is_configured(p)]

    def read_invoice(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
        configured = [p for p in self.providers if _is_configured(p)]
        if not configured:
            raise VisionError(
                "ยังไม่ได้ตั้งค่า AI สำหรับอ่านใบส่งของ - ใส่ GEMINI_API_KEY ก่อน"
            )

        errors = []
        for provider in configured:
            try:
                result = provider.read_invoice(image_bytes, mime_type)
                if errors:
                    # record that earlier providers were tried and failed,
                    # so a scan that silently switched provider is visible
                    result["fallback_from"] = [name for name, _ in errors]
                return result
            except VisionError as e:
                errors.append((provider.name, str(e)))
            except Exception as e:  # a provider bug shouldn't kill the chain
                errors.append((provider.name, f"ข้อผิดพลาดที่ไม่คาดคิด: {e}"))

        detail = "; ".join(f"{name}: {msg}" for name, msg in errors)
        raise VisionError(f"อ่านใบส่งของไม่สำเร็จทุกช่องทาง ({detail})")


def _is_configured(provider: VisionProvider) -> bool:
    """A provider with no key is treated as not set up, not as broken."""
    return bool(getattr(provider, "api_key", None))


def build_default_chain() -> VisionChain:
    """Gemini first (free tier covers a restaurant's volume, reads Thai
    well). Other providers get appended here as they're added - the order
    of this list is the fallback order."""
    from adapters.gemini_vision import GeminiVisionAdapter
    return VisionChain([GeminiVisionAdapter()])
