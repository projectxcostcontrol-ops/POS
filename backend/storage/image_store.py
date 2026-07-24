from __future__ import annotations

"""
Uploads scanned invoice photos to Firebase Storage so the review screen
can show the original alongside the AI's reading. Actual deletion after
7 days is handled by a Storage LIFECYCLE RULE set once in the Firebase
Console (see DEPLOY.md) - not by any code here, since a bucket-level
rule is more reliable than a scheduled job that might not run.

In emulator mode this quietly returns None instead of failing - the
local Firestore emulator doesn't include Storage, and wiring that up
just for local dev isn't worth it. The rest of the app already treats
a missing image_url as "no image was kept," which is also exactly
what happens if a real upload fails, so nothing downstream needs to
know which case it is.
"""

import os
import uuid


def upload_receipt_image(store_id: str, image_bytes: bytes, content_type: str = "image/jpeg") -> str | None:
    """Returns the storage path on success, or None if storage isn't
    available/configured (emulator mode, or upload failure).

    No signed URL is generated - the backend proxies image bytes through
    its own API instead (see GET .../drafts/{id}/image in main.py), so
    the frontend never talks to Google Storage directly and there's no
    CORS configuration to get right."""
    if os.environ.get("USE_FIREBASE_EMULATOR", "false").lower() == "true":
        return None

    try:
        from firebase_admin import storage
        bucket = storage.bucket()
    except Exception as e:
        print(f"[image_store] Storage not available: {e}")
        return None

    path = f"receipts/{store_id}/{uuid.uuid4().hex}.jpg"
    try:
        blob = bucket.blob(path)
        blob.upload_from_string(image_bytes, content_type=content_type)
        return path
    except Exception as e:
        print(f"[image_store] Upload failed: {e}")
        return None


def storage_status() -> str:
    """Why images might not be available: "emulator", "unconfigured", or
    "ok". Collapsing these into a single None was what let the UI tell
    people their photo had expired when in fact the bucket was never set
    up - a confident explanation that sent them looking in the wrong
    place. Naming the cause costs one call and saves that."""
    if os.environ.get("USE_FIREBASE_EMULATOR", "false").lower() == "true":
        return "emulator"
    try:
        from firebase_admin import storage
        storage.bucket()
        return "ok"
    except Exception:
        return "unconfigured"


def download_receipt_image(path: str) -> tuple[bytes | None, str | None]:
    """Fetches the image back out of Storage server-to-server, for the
    backend to stream to the frontend. Returns (None, None) if storage
    isn't configured or the object is gone (e.g. past the 7-day
    lifecycle rule); call storage_status() to tell those apart."""
    if os.environ.get("USE_FIREBASE_EMULATOR", "false").lower() == "true":
        return None, None
    try:
        from firebase_admin import storage
        blob = storage.bucket().blob(path)
        if not blob.exists():
            return None, None
        return blob.download_as_bytes(), (blob.content_type or "image/jpeg")
    except Exception as e:
        print(f"[image_store] Download failed for {path}: {e}")
        return None, None


def delete_receipt_image(path: str) -> None:
    """Best-effort immediate delete - used when a draft is explicitly
    discarded, so there's no reason to wait out the 7-day lifecycle rule.
    Never raises: a failed cleanup here shouldn't block discarding the
    draft itself, and the lifecycle rule is the backstop either way."""
    if os.environ.get("USE_FIREBASE_EMULATOR", "false").lower() == "true":
        return
    try:
        from firebase_admin import storage
        storage.bucket().blob(path).delete()
    except Exception as e:
        print(f"[image_store] Delete failed for {path}: {e}")
