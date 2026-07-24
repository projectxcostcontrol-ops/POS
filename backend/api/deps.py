from __future__ import annotations

"""
FastAPI dependencies that turn an incoming request into a known user of a
known business (tenant), and refuse the request when that user's role
doesn't allow it.

The single most important rule here: a request is scoped to a tenant by
the USER'S OWN RECORD, never by anything the caller sends. There is no
tenant_id parameter on any endpoint, so there is nothing for a caller to
tamper with - business A cannot reach business B's data by guessing an id,
because the id is never accepted as input in the first place.

Signing up is deliberately NOT handled here. The old "first user to sign
in becomes the owner" bootstrap made sense when the system served one
restaurant; with many businesses it would hand the first stranger to
arrive ownership of nothing useful and block everyone else. Instead a
person with a Firebase account but no user record yet is simply "not
signed up", and must go through one of the two explicit signup endpoints
in main.py - create a new business, or accept an invite.
"""

from fastapi import Header, HTTPException

from core.auth import AuthError, verify_token, can, is_super_admin


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        return ""
    return authorization[7:].strip()


def make_auth_dependencies(root_store):
    """Built as a factory so the store instance is injected rather than
    imported, keeping this module independent of app wiring."""

    def current_claims(authorization: str | None = Header(default=None)) -> dict:
        """Proves who the caller is via Firebase, and nothing more. Used by
        the signup screens, where the person has an identity but does not
        yet belong to any business."""
        try:
            claims = verify_token(_bearer(authorization))
        except AuthError as e:
            raise HTTPException(e.status, str(e))
        return {
            "uid": claims.get("uid") or claims.get("user_id"),
            "email": (claims.get("email") or "").lower(),
            "name": claims.get("name", ""),
        }

    def current_user(authorization: str | None = Header(default=None)) -> dict:
        claims = current_claims(authorization)
        user = root_store.get_user(claims["uid"])
        if not user:
            # 409 rather than 403: nothing is wrong with this person, they
            # just haven't finished signing up. The frontend uses this
            # exact status to send them to the signup screen instead of
            # showing a permission error.
            raise HTTPException(409, "ยังไม่ได้สร้างบัญชีธุรกิจ")
        if not user.get("tenant_id"):
            raise HTTPException(409, "บัญชีนี้ยังไม่ได้ผูกกับธุรกิจใด")
        return user

    def current_admin(authorization: str | None = Header(default=None)) -> dict:
        """Our own back office. Gated on the email in the verified Firebase
        token against SUPER_ADMIN_EMAILS - not on anything stored in the
        database, so no amount of tampering with a tenant's own records can
        grant it."""
        claims = current_claims(authorization)
        if not is_super_admin(claims["email"]):
            raise HTTPException(404, "ไม่พบหน้านี้")
        return claims

    def require(capability: str):
        def checker(user: dict):
            if not can(user["role"], capability):
                raise HTTPException(403, "สิทธิ์ของคุณไม่สามารถใช้งานส่วนนี้ได้")
            return user
        return checker

    def check_store_access(user: dict, store_id: str):
        """Which branches within their own business this person may touch.
        Cross-BUSINESS access isn't checked here because it can't happen -
        the Store handed to the endpoint is already bound to their tenant.
        This is the second, narrower question: which branch."""
        if can(user["role"], "all_stores"):
            return
        if store_id not in (user.get("store_ids") or []):
            raise HTTPException(403, "คุณไม่มีสิทธิ์เข้าถึงสาขานี้")

    return current_claims, current_user, current_admin, require, check_store_access
