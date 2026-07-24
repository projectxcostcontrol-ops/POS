from __future__ import annotations

import os

"""
Users, roles, and permission checks.

Three roles, matching how a restaurant actually divides responsibility:

  owner    - sees everything across every branch, including money
  manager  - runs one branch: stock, receiving, recipes, AND its money,
             but can't change system settings or manage users
  staff    - does the daily work: photograph delivery notes, count stock,
             record waste. Never sees money - not the dashboard, not
             income/expense, not even unit costs on the stock page.

Enforcement happens on the BACKEND, per endpoint. The frontend hides
pages a role can't use, but hiding a page is a convenience, not a
security boundary - anyone can call the API directly, so every
endpoint that exposes money or mutates data checks the role itself.
"""

OWNER = "owner"
MANAGER = "manager"
STAFF = "staff"

ROLES = (OWNER, MANAGER, STAFF)

# What each role is allowed to do. Kept as explicit capability names
# rather than scattered role comparisons, so adding a role later means
# editing this table instead of hunting through every endpoint.
CAPABILITIES = {
    OWNER: {
        "view_money",        # dashboard figures, costs, profit, income/expense
        "manage_stock",      # counts, waste, materials
        "manage_receiving",  # scan/confirm deliveries
        "manage_recipes",
        "manage_settings",   # Loyverse token, sync interval, store selection
        "manage_users",      # invite/remove people, change roles
        "all_stores",        # not restricted to one branch
    },
    MANAGER: {
        "view_money",
        "manage_stock",
        "manage_receiving",
        "manage_recipes",
    },
    STAFF: {
        "manage_stock",
        "manage_receiving",
        "manage_recipes",
    },
}


def can(role: str, capability: str) -> bool:
    return capability in CAPABILITIES.get(role, set())


class AuthError(Exception):
    """Raised when a token is missing/invalid, or the caller's role
    doesn't permit what they're attempting."""

    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status


def verify_token(id_token: str) -> dict:
    """Verifies a Firebase ID token and returns its claims.
    Raises AuthError(401) if the token is missing or invalid."""
    if not id_token:
        raise AuthError("ต้องเข้าสู่ระบบก่อน", status=401)
    try:
        from firebase_admin import auth
        return auth.verify_id_token(id_token)
    except Exception as e:
        raise AuthError(f"เข้าสู่ระบบไม่ถูกต้อง: {e}", status=401) from e


# ---- super admin (our own back office, not a tenant role) ----
# Deliberately NOT a role in the table above. A super admin isn't a more
# powerful owner - they're outside the tenant model entirely, and can only
# read aggregate counts. They cannot open any business's data, which is
# what makes the promise "your data is yours" true rather than aspirational.

def super_admin_emails() -> set[str]:
    raw = os.environ.get("SUPER_ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_super_admin(email: str) -> bool:
    return bool(email) and email.lower() in super_admin_emails()
