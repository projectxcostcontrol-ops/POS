"""
API for the frontend. Run with: uvicorn api.main:app --reload

Multi-tenant (V3 step 3.2): one deployment serves many restaurant
businesses. Every request is bound to exactly one tenant, taken from the
signed-in user's own record - never from a parameter - and all data access
goes through a Store already scoped to that tenant. See api/deps.py.
"""

import asyncio
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, HTTPException, UploadFile, File, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from adapters.loyverse_adapter import LoyverseAdapter
from adapters._loyverse_client import normalize_time
from storage.firestore_store import Store
from storage.movement_ledger import MovementLedger
from core.stock_engine import sync_branch, deductions_for
from core.vision_chain import build_default_chain
from core.vision_provider import VisionError
from core.matching_engine import MatchingEngine
from core.pos_registry import PosRegistry
from core.recipe_suggester import RecipeSuggester
from core import variance as variance_lib
from core import sales_report
from core import daily_rollup
from core import daily_brief
from core import assistant as assistant_lib
from core import advisor as advisor_lib
from core.expenses import clean_expense, ExpenseError
from core.receiving import clean_receiving, normalize_date, ReceivingError
from core.delivery import (CHANNELS, DeliveryError, clean_order,
                           is_pos_sale)
from core.unit_conversion import apply_unit_conversion
from storage.image_store import (upload_receipt_image, delete_receipt_image,
                                 download_receipt_image, storage_status)
from core.auth import can, CAPABILITIES, OWNER, ROLES
from api.deps import make_auth_dependencies

load_dotenv()

# Closed beta: cap how many businesses can sign up. A join-by-invite never
# creates a new tenant, so invited staff are never blocked by this - only
# the "create a new business" door has a queue.
MAX_TENANTS = int(os.environ.get("MAX_TENANTS", "10"))

app = FastAPI()

# Which sites the browser may call this API from.
#
# The risk here is smaller than "*" usually implies: every request is
# authorised by a Bearer token the page has to attach deliberately, not
# by a cookie the browser sends on its own, so a hostile site loading
# this API in the background gets nothing. It is still worth naming the
# real front end - defence in depth costs one environment variable, and
# "*" is the kind of default that stops being harmless the day someone
# adds cookie auth without re-reading this line.
#
# Unset means "*", with a warning, so an existing deployment does not
# break the moment this ships. Set ALLOWED_ORIGINS to the frontend's URL
# (comma-separated for more than one) - see DEPLOY.md.
_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if not _origins:
    _origins = ["*"]
    print("[cors] ALLOWED_ORIGINS is not set - allowing every origin. "
          "Set it to the frontend URL in production.")

app.add_middleware(CORSMiddleware, allow_origins=_origins,
                   allow_methods=["*"], allow_headers=["*"])

# The unscoped root store. Only auth, signup, and the admin overview use it
# directly; every business endpoint works through a tenant-scoped view.
root_store = Store()
vision = build_default_chain()


def _assistant():
    """The assistant provider, or None when there is no key for one.

    None rather than a stub that returns apologies: the brief is written
    without a model and is complete without one, so "no provider" is a
    normal state, not a degraded one.
    """
    if not os.environ.get("GEMINI_API_KEY"):
        return None
    from adapters.gemini_assistant import GeminiAssistantAdapter
    return GeminiAssistantAdapter()

suggester = RecipeSuggester()

current_claims, current_user, current_admin, _require, check_store_access = \
    make_auth_dependencies(root_store)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _thai_today() -> str:
    return datetime.now(timezone(timedelta(hours=7))).date().isoformat()


_QR_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_QR_SPOT = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_QR_DEFAULTS = {
    "hong-duck": {
        "label": "ฮง เป็ดย่าง",
        "target_url": "https://rankrua.vercel.app/menu/hong-duck",
    },
}


def _clean_qr_slug(value: str) -> str:
    value = (value or "").strip().lower()
    if not _QR_SLUG.fullmatch(value):
        raise HTTPException(404, "ไม่พบ QR Code นี้")
    return value


def _clean_qr_spot(value: str) -> str:
    value = (value or "default").strip().lower()
    return value if _QR_SPOT.fullmatch(value) else "default"


def _valid_public_target(value: str) -> str:
    value = (value or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise HTTPException(400, "ปลายทาง QR ต้องเป็น URL แบบ https")
    return value


# ---- per-request context ----------------------------------------------
# Everything an endpoint needs, already scoped to the caller's business.
# Endpoints ask for a Ctx instead of reaching for module-level state, which
# is what makes tenant isolation structural rather than a rule to remember.

# Bangkok. Every shop using this today is in Thailand, and a default has
# to be something - but it is only used until a browser says otherwise.
DEFAULT_TZ_OFFSET = 420

# Distinguishes "not looked up yet" from "looked up, the shop has not
# said" - both of which are None-ish, and only one of which should cost
# a read.
_UNREAD = object()


class Ctx:
    def __init__(self, user: dict):
        self.user = user
        self.tenant_id = user["tenant_id"]
        self.store = root_store.for_tenant(self.tenant_id)
        self.ledger = MovementLedger(self.store)
        self.matcher = MatchingEngine(self.store)
        self.pos = PosRegistry(self.store,
                               lambda conn: _adapter_for(self.tenant_id, conn),
                               _now())
        self._tz = _UNREAD

    # ---- where the shop is -------------------------------------------
    # The browser's offset is the browser's. A summary of a Bangkok
    # shop's Tuesday has to be the same Tuesday whether the owner opens
    # it from the shop, from a hotel in Europe, or not at all - and that
    # last case is what decides it, because nothing the server does on
    # its own has a browser to ask. Read once per request, not per call.

    @property
    def stored_tz(self) -> int | None:
        if self._tz is _UNREAD:
            value = self.store.get_setting("timezone_offset")
            self._tz = None if value is None else int(value)
        return self._tz

    @property
    def tz_offset(self) -> int:
        """The shop's offset, falling back to Bangkok."""
        stored = self.stored_tz
        return DEFAULT_TZ_OFFSET if stored is None else stored

    def tz_or(self, requested: int) -> int:
        """The shop's offset if it has one, otherwise what the caller
        says. A browser that is here now beats a default that is a
        guess - but it never overrides an answer the shop has given."""
        stored = self.stored_tz
        return requested if stored is None else stored

    # ---- Loyverse accounts -------------------------------------------
    # There is deliberately no `c.provider`. A business can hold several
    # Loyverse accounts, so "the provider" is not a thing that exists -
    # asking for one without saying which branch is a question with no
    # correct answer, and the old property answered it anyway by handing
    # back whichever account happened to be first. Every caller has a
    # store_id in its path already, so every caller can say which.

    @property
    def connections(self) -> list[dict]:
        return self.pos.connections

    def provider_for(self, store_id: str) -> LoyverseAdapter:
        adapter = self.pos.provider_for(store_id)
        if adapter is None:
            if not self.connections:
                raise HTTPException(
                    400, "ยังไม่ได้เชื่อมต่อ Loyverse - เพิ่ม token ในหน้าตั้งค่าก่อน")
            raise HTTPException(
                400, "ไม่รู้ว่าสาขานี้มาจากบัญชี Loyverse ไหน - เปิดหน้าตั้งค่าเพื่อโหลดรายชื่อสาขาใหม่")
        return adapter

    def branches(self) -> tuple[list[dict], list[dict]]:
        return self.pos.branches()


def ctx(user: dict = Depends(current_user)) -> Ctx:
    c = Ctx(user)
    try:
        c.store.touch_tenant_activity(_today())
    except Exception:
        pass  # activity tracking is for our admin view; never fail a request over it
    return c


def store_ctx(store_id: str, c: Ctx = Depends(ctx)) -> Ctx:
    """For any endpoint with {store_id} in its path: confirms the caller may
    use that branch. The branch belonging to their business is already
    guaranteed by the scoped Store."""
    check_store_access(c.user, store_id)
    return c


def _cap(capability: str, message: str):
    def dep(c: Ctx = Depends(ctx)) -> Ctx:
        if not can(c.user["role"], capability):
            raise HTTPException(403, message)
        return c
    return dep


def _store_cap(capability: str, message: str):
    def dep(store_id: str, c: Ctx = Depends(ctx)) -> Ctx:
        if not can(c.user["role"], capability):
            raise HTTPException(403, message)
        check_store_access(c.user, store_id)
        return c
    return dep


require_money = _cap("view_money", "สิทธิ์ของคุณไม่สามารถดูข้อมูลด้านการเงินได้")
require_settings = _cap("manage_settings", "เฉพาะเจ้าของร้านเท่านั้นที่แก้ไขการตั้งค่าได้")
require_users = _cap("manage_users", "เฉพาะเจ้าของร้านเท่านั้นที่จัดการผู้ใช้ได้")
store_money = _store_cap("view_money", "สิทธิ์ของคุณไม่สามารถดูข้อมูลด้านการเงินได้")
store_settings = _store_cap("manage_settings", "เฉพาะเจ้าของร้านเท่านั้นที่แก้ไขการตั้งค่าได้")


# ---- Loyverse providers, one per business ------------------------------
# Cached by tenant so a busy account isn't rebuilding an HTTP client on
# every request, and keyed by the token itself so changing the token in
# Settings takes effect immediately without a restart.

_providers: dict[tuple[str, str], tuple[str, LoyverseAdapter]] = {}


def _adapter_for(tenant_id: str, conn: dict) -> LoyverseAdapter:
    """Cached per (business, Loyverse account) so a busy shop isn't
    rebuilding an HTTP client on every request, and keyed by the token
    itself so replacing one in Settings takes effect immediately without
    a restart."""
    key = (tenant_id, conn["id"])
    token = conn["token"]
    cached = _providers.get(key)
    if cached and cached[0] == token:
        return cached[1]
    adapter = LoyverseAdapter(token)
    _providers[key] = (token, adapter)
    return adapter


def _sync_interval(store: Store) -> int:
    return int(store.get_setting("sync_interval_seconds")
               or os.environ.get("SYNC_INTERVAL_SECONDS", "300"))


def _sync_everything_once() -> None:
    """One pass over every branch of every Loyverse account of every
    business. Ordinary blocking code - see auto_sync_loop for where it
    runs.

    Failures are contained at each level on purpose: one account with a
    dead token must not stop that business's other accounts, and one
    business must not stop the rest.
    """
    try:
        tenants = root_store.list_tenants()
    except Exception as e:
        print(f"[auto_sync] could not list tenants: {e}")
        return

    for tenant in tenants:
        tenant_id = tenant.get("id")
        try:
            scoped = root_store.for_tenant(tenant_id)
            scoped.migrate_legacy_token(_now())
            connections = scoped.list_connections()
        except Exception as e:
            print(f"[auto_sync] tenant {tenant_id} error: {e}")
            continue

        for conn in connections:
            try:
                adapter = _adapter_for(tenant_id, conn)
                for branch in adapter.get_stores():
                    sync_branch(adapter, scoped, branch["id"])
            except Exception as e:
                print(f"[auto_sync] tenant {tenant_id} "
                      f"connection {conn.get('id')} error: {e}")


def _shortest_interval() -> int:
    default = int(os.environ.get("SYNC_INTERVAL_SECONDS", "300"))
    try:
        intervals = [_sync_interval(root_store.for_tenant(t["id"]))
                     for t in root_store.list_tenants()]
    except Exception:
        return default
    return min([default] + intervals)


async def auto_sync_loop():
    """Syncs every branch on an interval, so stock deducts without anyone
    pressing a button.

    The work runs in a thread. It used to be called directly from this
    coroutine, and every line of it is blocking - HTTP to Loyverse,
    Firestore reads and writes - so for as long as a pass took, the whole
    API was frozen. Not slow: stopped. Every request from every other
    business waited for it, and the more businesses signed up the longer
    the freeze, which is exactly backwards from how it should scale.
    Nothing about the sync itself changed; it just no longer holds the
    event loop hostage while it runs.

    One caveat worth writing down: this loop lives inside the API
    process, so running more than one instance means each one syncs
    everything. Saving is idempotent, but two instances could both read
    "not yet processed" for the same receipt and both deduct it. Before
    scaling past a single instance, this moves to a scheduler calling an
    endpoint, or takes a lock in Firestore.
    """
    while True:
        try:
            await asyncio.to_thread(_sync_everything_once)
        except Exception as e:
            print(f"[auto_sync] pass failed: {e}")
        interval = await asyncio.to_thread(_shortest_interval)
        await asyncio.sleep(max(30, interval))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(auto_sync_loop())
    yield
    task.cancel()


app.router.lifespan_context = lifespan


# ---- public dynamic QR links ------------------------------------------

@app.get("/api/public/qr/{slug}")
def resolve_public_qr(slug: str, spot: str = "default"):
    """Resolve and count a scan without collecting IP or device data."""
    slug = _clean_qr_slug(slug)
    spot = _clean_qr_spot(spot)
    link = root_store.get_qr_link(slug)
    if not link:
        default = _QR_DEFAULTS.get(slug)
        if not default:
            raise HTTPException(404, "ไม่พบ QR Code นี้")
        link = root_store.set_qr_link(slug, {
            **default,
            "enabled": True,
            "total_scans": 0,
            "created_at": _now(),
            "updated_at": _now(),
        })
    if not link.get("enabled", True):
        raise HTTPException(404, "QR Code นี้ยังไม่เปิดใช้งาน")
    scanned_at = _now()
    root_store.record_qr_scan(slug, spot, _thai_today(), scanned_at)
    return {"target_url": link["target_url"]}


@app.put("/api/admin/qr-links/{slug}")
def update_public_qr(slug: str, data: dict, _admin: dict = Depends(current_admin)):
    """Change a printed QR's destination without changing the QR itself."""
    slug = _clean_qr_slug(slug)
    return root_store.set_qr_link(slug, {
        "label": (data.get("label") or slug).strip()[:120],
        "target_url": _valid_public_target(data.get("target_url")),
        "enabled": bool(data.get("enabled", True)),
        "updated_at": _now(),
    })


@app.get("/api/admin/qr-links/{slug}/stats")
def public_qr_stats(slug: str, _admin: dict = Depends(current_admin)):
    stats = root_store.get_qr_stats(_clean_qr_slug(slug))
    if not stats:
        raise HTTPException(404, "ไม่พบ QR Code นี้")
    return stats


# ---- signup ------------------------------------------------------------
# Two doors in, and no others. Either you start a business (and own it), or
# someone who already owns one invited you.

@app.get("/api/signup/status")
def signup_status(claims: dict = Depends(current_claims)):
    """Lets the signup screen show remaining slots (or a full state) before
    someone fills in a business name, rather than only failing on submit."""
    used = len(root_store.list_tenants())
    return {"open": used < MAX_TENANTS, "slots_left": max(0, MAX_TENANTS - used), "cap": MAX_TENANTS}


@app.post("/api/signup/business")
def signup_business(data: dict, claims: dict = Depends(current_claims)):
    """data: {business_name, display_name}"""
    existing = root_store.get_user(claims["uid"])
    if existing:
        raise HTTPException(400, "บัญชีนี้อยู่ในธุรกิจอื่นอยู่แล้ว")

    if len(root_store.list_tenants()) >= MAX_TENANTS:
        raise HTTPException(
            403, f"ตอนนี้เปิดทดลองใช้งานครบ {MAX_TENANTS} ร้านแล้วสำหรับช่วง Close Beta "
                 f"ขอบคุณที่สนใจ Rankrua เร็ว ๆ นี้จะเปิดรับเพิ่มครับ")

    name = (data.get("business_name") or "").strip()
    if not name:
        raise HTTPException(400, "กรุณาใส่ชื่อธุรกิจ")

    tenant_id = root_store.create_tenant(name, owner_uid=claims["uid"], created_at=_now())
    root_store.set_user(claims["uid"], claims["email"], OWNER, tenant_id,
                        store_ids=[], display_name=(data.get("display_name") or "").strip())
    return {"tenant_id": tenant_id, "business_name": name, "role": OWNER}


def _peek_invite(token: str) -> dict:
    """What the join screen shows before the person commits: which business
    invited them, as which role. Returns only that - no data belonging to
    the business itself."""
    invite = root_store.get_invite(token)
    if not invite:
        raise HTTPException(404, "คำเชิญนี้ไม่ถูกต้องหรือถูกใช้ไปแล้ว")
    tenant = root_store.for_tenant(invite["tenant_id"]).get_tenant()
    return {
        "business_name": (tenant or {}).get("name", ""),
        "email": invite["email"],
        "role": invite["role"],
    }


@app.post("/api/invites/peek")
def peek_invite_v2(data: dict, claims: dict = Depends(current_claims)):
    """data: {token}. The token is already in the invited person's own
    address bar - that is how the link works - but it does not have to be
    in our access logs as well, and a log is where it would sit readable
    long after the invite was used."""
    return _peek_invite((data.get("token") or "").strip())


@app.get("/api/invites/{token}")
def peek_invite(token: str, claims: dict = Depends(current_claims)):
    """Deprecated: token in the path, and therefore in access logs. Kept
    for a frontend deployed before this release."""
    return _peek_invite(token)


@app.post("/api/signup/join")
def signup_join(data: dict, claims: dict = Depends(current_claims)):
    """data: {token, display_name}. The role and the business both come from
    the invite, never from the request - so accepting an invite can't be
    turned into a way to pick your own permissions."""
    if root_store.get_user(claims["uid"]):
        raise HTTPException(400, "บัญชีนี้อยู่ในธุรกิจอื่นอยู่แล้ว")

    invite = root_store.get_invite((data.get("token") or "").strip())
    if not invite:
        raise HTTPException(404, "คำเชิญนี้ไม่ถูกต้องหรือถูกใช้ไปแล้ว")
    if invite["email"] != claims["email"]:
        raise HTTPException(403, "คำเชิญนี้ออกให้กับอีเมลอื่น - เข้าสู่ระบบด้วยอีเมลที่ได้รับเชิญ")

    root_store.set_user(claims["uid"], claims["email"], invite["role"],
                        invite["tenant_id"], invite.get("store_ids", []),
                        display_name=(data.get("display_name") or "").strip())
    root_store.delete_invite(invite["token"])
    tenant = root_store.for_tenant(invite["tenant_id"]).get_tenant()
    return {"tenant_id": invite["tenant_id"],
            "business_name": (tenant or {}).get("name", ""),
            "role": invite["role"]}


# ---- settings ----------------------------------------------------------

@app.get("/api/settings")
def get_settings(c: Ctx = Depends(ctx)):
    tenant = c.store.get_tenant() or {}
    return {
        "connected": len(c.connections) > 0,
        "connection_count": len(c.connections),
        "sync_interval_seconds": _sync_interval(c.store),
        "business_name": tenant.get("name", ""),
        "created_at": tenant.get("created_at", ""),
        "user_count": len(root_store.list_users(c.tenant_id)),
    }


@app.post("/api/settings/business-name")
def set_business_name(name: str, c: Ctx = Depends(require_settings)):
    name = name.strip()
    if not name:
        raise HTTPException(400, "ชื่อธุรกิจว่างไม่ได้")
    c.store.update_tenant({"name": name})
    return {"business_name": name}


@app.get("/api/settings/connections")
def list_connections(c: Ctx = Depends(require_settings)):
    """The connected Loyverse accounts and the branches each one brings.

    The token itself is never returned. It went in once; there is no
    screen that needs to show it again, and anything that displays a
    credential is somewhere it can be read over a shoulder or copied out
    of a screenshot."""
    branches, failures = c.branches()
    failed = {f["connection_id"]: f["error"] for f in failures}
    # Recorded from the branches just fetched rather than by calling
    # refresh_index(), which would ask every Loyverse account the same
    # question a second time for the same screen.
    c.store.set_store_index({b["id"]: b["connection_id"] for b in branches})

    return {"connections": [{
        "id": conn["id"],
        "label": conn.get("label", ""),
        "created_at": conn.get("created_at", ""),
        "error": failed.get(conn["id"]),
        "stores": [{"id": b["id"], "name": b["name"]}
                   for b in branches if b["connection_id"] == conn["id"]],
    } for conn in c.connections]}


def _add_connection(c: Ctx, token: str, label: str) -> dict:
    token = (token or "").strip()
    if not token:
        raise HTTPException(400, "กรุณาใส่ access token")

    # Proved before it is stored: a token that doesn't work is worth
    # rejecting at the moment someone can still paste the right one.
    try:
        stores = LoyverseAdapter(token).get_stores()
    except Exception as e:
        raise HTTPException(400, f"เชื่อมต่อไม่สำเร็จ - เช็ค token: {e}")

    for existing in c.store.list_connections():
        if existing.get("token") == token:
            raise HTTPException(400, "บัญชี Loyverse นี้เชื่อมต่ออยู่แล้ว")

    if not label.strip():
        # Name it after what it actually contains, so a list of accounts
        # reads as a list of shops rather than "บัญชี 1, บัญชี 2".
        names = [st["name"] for st in stores]
        label = " · ".join(names[:2]) + ("…" if len(names) > 2 else "") or "บัญชี Loyverse"

    conn = c.store.add_connection(token, label.strip(), _now())
    c.store.set_store_index({st["id"]: conn["id"] for st in stores})
    c.pos.invalidate()
    return {"connection": {**conn, "stores": stores}, "connected": True}


@app.post("/api/settings/connections")
def add_connection(data: dict, c: Ctx = Depends(require_settings)):
    """data: {token, label}. Adds one more Loyverse account.

    In the body rather than the query string, unlike the endpoint below:
    a query string is written to access logs by every proxy it passes
    through, which is not where an access token should end up."""
    return _add_connection(c, data.get("token", ""), data.get("label", ""))


@app.post("/api/settings/token")
def set_token(token: str, c: Ctx = Depends(require_settings)):
    """Deprecated: puts the access token in the query string, where every
    proxy in the path writes it to an access log. Use POST
    /api/settings/connections instead. Kept only so a frontend deployed
    before this release keeps working."""
    _add_connection(c, token, "")
    return {"connected": True}


@app.delete("/api/settings/connections/{conn_id}")
def remove_connection(conn_id: str, c: Ctx = Depends(require_settings)):
    """Stops syncing this Loyverse account. Everything already synced
    from it stays where it is - see Store.delete_connection."""
    if not c.store.get_connection(conn_id):
        raise HTTPException(404, "ไม่พบบัญชีนี้")
    c.store.delete_connection(conn_id)
    _providers.pop((c.tenant_id, conn_id), None)
    c.pos.invalidate()
    return {"ok": True}


@app.post("/api/settings/disconnect")
def disconnect(c: Ctx = Depends(require_settings)):
    """Disconnects every account. Kept for the older frontend, which had
    only one to disconnect."""
    for conn in c.store.list_connections():
        c.store.delete_connection(conn["id"])
        _providers.pop((c.tenant_id, conn["id"]), None)
    c.store.set_setting("loyverse_token", None)
    c.pos.invalidate()
    return {"connected": False}


@app.post("/api/settings/sync-interval")
def set_sync_interval(seconds: int, c: Ctx = Depends(require_settings)):
    c.store.set_setting("sync_interval_seconds", seconds)
    return {"sync_interval_seconds": seconds}


# ---- stores / items / categories ---------------------------------------

@app.get("/api/stores")
def list_stores(c: Ctx = Depends(ctx)):
    """Every branch this person can use, from every connected account.

    Which Loyverse account a branch came from travels with it, because
    two accounts can easily hold a branch called "สาขา 1" and the person
    switching between them needs to be able to tell which is which.
    """
    branches, _ = c.branches()
    c.store.set_store_index({b["id"]: b["connection_id"] for b in branches})

    if not can(c.user["role"], "all_stores"):
        allowed = set(c.user.get("store_ids") or [])
        branches = [b for b in branches if b["id"] in allowed]

    show_account = len({b["connection_id"] for b in branches}) > 1
    return [{**b, "show_account": show_account} for b in branches]


@app.get("/api/{store_id}/items")
def list_items(store_id: str, c: Ctx = Depends(store_ctx)):
    """Items come read-only from Loyverse; category assignment is ours."""
    items = c.provider_for(store_id).get_items()
    assignments = c.store.get_item_categories(store_id)
    for item in items:
        item["category_id"] = assignments.get(item["name"])
    return items


@app.get("/api/{store_id}/loyverse-categories")
def list_loyverse_categories(store_id: str, c: Ctx = Depends(store_ctx)):
    return c.provider_for(store_id).get_categories()


@app.get("/api/{store_id}/categories")
def list_categories(store_id: str, c: Ctx = Depends(store_ctx)):
    return c.store.list_categories(store_id)


@app.post("/api/{store_id}/categories")
def create_category(store_id: str, name: str, c: Ctx = Depends(store_ctx)):
    return c.store.create_category(store_id, name)


@app.put("/api/{store_id}/categories/{category_id}")
def rename_category(store_id: str, category_id: str, name: str, c: Ctx = Depends(store_ctx)):
    c.store.rename_category(store_id, category_id, name)
    return {"ok": True}


@app.delete("/api/{store_id}/categories/{category_id}")
def delete_category(store_id: str, category_id: str, c: Ctx = Depends(store_ctx)):
    c.store.delete_category(store_id, category_id)
    return {"ok": True}


@app.put("/api/{store_id}/items/{item_name}/category")
def set_item_category(store_id: str, item_name: str, category_id: str,
                      c: Ctx = Depends(store_ctx)):
    c.store.set_item_category(store_id, item_name, category_id)
    return {"ok": True}


# ---- materials ---------------------------------------------------------

@app.get("/api/{store_id}/materials")
def list_materials(store_id: str, c: Ctx = Depends(store_ctx)):
    return c.store.list_materials(store_id)


@app.put("/api/{store_id}/materials/{material_id}")
def upsert_material(store_id: str, material_id: str, data: dict, c: Ctx = Depends(store_ctx)):
    try:
        c.store.upsert_material(store_id, material_id, data)
    except ValueError as e:
        # An id Firestore cannot use. A 400 saying so beats a 500 from
        # inside the SDK, which names neither the material nor the id.
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/{store_id}/materials/{material_id}/adjust")
def adjust_stock(store_id: str, material_id: str, new_stock: float, reason: str = "",
                 c: Ctx = Depends(store_ctx)):
    c.store.adjust_stock(store_id, material_id, new_stock, reason=reason)
    return {"ok": True}


@app.get("/api/{store_id}/materials/{material_id}/movements")
def list_movements(store_id: str, material_id: str, c: Ctx = Depends(store_ctx)):
    return c.ledger.list_movements(store_id, material_id)


@app.get("/api/{store_id}/materials/{material_id}/cost-history")
def cost_history(store_id: str, material_id: str, c: Ctx = Depends(store_money)):
    return c.ledger.cost_history(store_id, material_id)


@app.get("/api/{store_id}/materials/{material_id}/average-cost")
def average_cost(store_id: str, material_id: str, year: int | None = None,
                 month: int | None = None, c: Ctx = Depends(store_money)):
    return {"average_cost": c.ledger.average_cost(store_id, material_id, year, month)}


@app.post("/api/{store_id}/materials/{material_id}/waste")
def record_waste(store_id: str, material_id: str, quantity: float, note: str = "",
                 c: Ctx = Depends(store_ctx)):
    c.ledger.record_waste(store_id, material_id, quantity, note=note)
    return {"ok": True}


@app.post("/api/{store_id}/migrate-stock")
def migrate_stock(store_id: str, c: Ctx = Depends(store_settings)):
    return {"migrated_materials": c.store.migrate_stock_to_ledger(store_id)}


@app.post("/api/{store_id}/rebuild-stock-snapshot")
def rebuild_stock_snapshot(store_id: str, c: Ctx = Depends(store_settings)):
    """Recompute each material's running stock and cost totals from the
    ledger.

    Needed once per branch whose materials predate those totals - until
    then the stock page still shows correct figures, it just pays for
    them by summing the whole movement history on every visit. Afterwards
    this stays available as the way to check the fast numbers against the
    ledger they came from, rather than having to trust them.

    Best run while the branch is quiet: it writes totals read a moment
    earlier, so a sale landing in between is overwritten and needs
    another run (or the next stock count) to settle."""
    return {"rebuilt_materials": c.ledger.rebuild_snapshots(store_id)}


# ---- receiving ---------------------------------------------------------

@app.get("/api/{store_id}/receivings")
def list_receivings(store_id: str, from_: str | None = None, to: str | None = None,
                    c: Ctx = Depends(store_ctx)):
    """Dates are YYYY-MM-DD. Without them, every delivery ever taken."""
    return c.store.list_receivings(store_id, normalize_date(from_) or None,
                                   normalize_date(to) or None)


def _clean_receiving(data: dict) -> dict:
    try:
        return clean_receiving(data.get("supplier"), data.get("date"),
                               data.get("items"), data.get("note", ""))
    except ReceivingError as e:
        raise HTTPException(400, str(e))


@app.post("/api/{store_id}/receivings")
def add_receiving(store_id: str, data: dict, c: Ctx = Depends(store_ctx)):
    r = _clean_receiving(data)
    return c.store.add_receiving(store_id, supplier=r["supplier"], date=r["date"],
                                 items=r["items"], note=r["note"])


@app.put("/api/{store_id}/receivings/{receiving_id}")
def update_receiving(store_id: str, receiving_id: str, data: dict,
                     c: Ctx = Depends(store_ctx)):
    """Corrects a delivery that was recorded wrong.

    The old stock movements come out and new ones go in, rather than the
    document being edited and the ledger left as it was. A delivery IS
    its movements as far as the shelf and the cost history are concerned;
    changing 5kg to 50kg on the paperwork while the ledger still says 5
    would leave two answers to the same question, and the wrong one is
    the one every report reads.
    """
    existing = c.store.get_receiving(store_id, receiving_id)
    if not existing:
        raise HTTPException(404, "ไม่พบรายการซื้อของนี้")
    r = _clean_receiving(data)
    _refuse_if_counted_since(c, store_id, existing.get("date"), "รายการซื้อของนี้")

    c.ledger.delete_by_ref(store_id, receiving_id)
    c.store.replace_receiving(store_id, receiving_id, r)
    c.store.add_receiving_movements(store_id, receiving_id, r["supplier"],
                                    r["date"], r["items"])
    return {"ok": True, "id": receiving_id, **r}


@app.delete("/api/{store_id}/receivings/{receiving_id}")
def delete_receiving(store_id: str, receiving_id: str, c: Ctx = Depends(store_ctx)):
    """Removes the delivery and takes its stock back off the shelf.

    Both halves, or neither would be true: the ingredients were never
    delivered, so they are not there, and the price was never paid, so it
    should not be pulling the material's average cost around."""
    existing = c.store.get_receiving(store_id, receiving_id)
    if not existing:
        raise HTTPException(404, "ไม่พบรายการซื้อของนี้")
    _refuse_if_counted_since(c, store_id, existing.get("date"), "รายการซื้อของนี้")

    removed = c.ledger.delete_by_ref(store_id, receiving_id)
    c.store.delete_receiving(store_id, receiving_id)
    return {"ok": True, "reverted_materials": removed}


@app.post("/api/{store_id}/receiving/scan")
async def scan_invoice(store_id: str, file: UploadFile = File(...),
                       c: Ctx = Depends(store_ctx)):
    """Read a photo of a delivery note, match each line against the material
    catalog, and save the result as a draft for review. Nothing touches
    stock here - that only happens on confirm."""
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(400, "ไม่พบไฟล์รูป")

    try:
        scan = vision.read_invoice(image_bytes, file.content_type or "image/jpeg")
    except VisionError as e:
        raise HTTPException(502, str(e))

    # Scoped by tenant too, so two businesses can never land on the same
    # image path even if their branch ids collide under our own POS later.
    image_path = upload_receipt_image(c.store.scoped_id(store_id), image_bytes,
                                      file.content_type or "image/jpeg")

    items = c.matcher.match_all(store_id, scan.get("items", []), scan.get("supplier"))
    items = _apply_unit_conversions(c, store_id, items)
    items = _fill_missing_prices(c, store_id, items)
    return c.store.create_draft(
        store_id, supplier=scan.get("supplier"), invoice=scan.get("invoice"),
        date=scan.get("date"), items=items,
        raw_text=scan.get("raw_text", ""), provider=scan.get("provider", ""),
        image_path=image_path, warning=scan.get("warning"),
    )


@app.get("/api/{store_id}/receiving/drafts/{draft_id}/image")
def get_draft_image(store_id: str, draft_id: str, c: Ctx = Depends(store_ctx)):
    """Streams the scanned photo through the backend, so the frontend never
    talks to Google Storage directly - no CORS setup needed."""
    draft = c.store.get_draft(store_id, draft_id)
    if not draft or not draft.get("image_path"):
        raise HTTPException(404, "ไม่พบรูปสำหรับร่างนี้")
    data, content_type = download_receipt_image(draft["image_path"])
    if data is None:
        status = storage_status()
        if status == "unconfigured":
            raise HTTPException(503, "ยังไม่ได้ตั้งค่า Firebase Storage "
                                     "(ตั้ง FIREBASE_STORAGE_BUCKET ที่ backend)")
        if status == "emulator":
            raise HTTPException(503, "โหมดทดสอบไม่ได้เก็บรูป")
        raise HTTPException(404, "รูปนี้อาจถูกลบไปแล้ว (เกิน 7 วัน) หรือดึงไม่สำเร็จ")
    return Response(content=data, media_type=content_type)


def _apply_unit_conversions(c: Ctx, store_id: str, items: list[dict]) -> list[dict]:
    materials = {m["id"]: m for m in c.store.list_materials(store_id)}
    out = []
    for item in items:
        material_id = (item.get("match") or {}).get("material_id")
        if material_id and material_id in materials:
            match = item["match"]
            item = apply_unit_conversion(item, materials[material_id]["unit"])
            item["match"] = match
        out.append(item)
    return out


def _fill_missing_prices(c: Ctx, store_id: str, items: list[dict]) -> list[dict]:
    """When the AI couldn't read a price, DON'T silently record cost as 0 -
    that would drag the material's average cost toward zero. Suggest the
    last known price instead, flagged clearly; a line with no price and no
    history is flagged as needing input before it can be confirmed."""
    out = []
    for item in items:
        if item.get("price") is not None:
            item["price_source"] = "scanned"
            out.append(item)
            continue

        material_id = (item.get("match") or {}).get("material_id")
        suggested = c.ledger.average_cost(store_id, material_id) if material_id else None
        if suggested is not None:
            item = {**item, "price": suggested, "price_source": "history"}
        else:
            item = {**item, "price_source": "missing"}
        out.append(item)
    return out


@app.post("/api/{store_id}/receiving/convert-unit")
def convert_unit_for_material(store_id: str, item: dict, material_id: str,
                              c: Ctx = Depends(store_ctx)):
    materials = {m["id"]: m for m in c.store.list_materials(store_id)}
    mat = materials.get(material_id)
    if not mat:
        raise HTTPException(404, "ไม่พบวัตถุดิบนี้")
    converted = apply_unit_conversion(item, mat.get("unit", ""))
    if converted.get("price") is None:
        suggested = c.ledger.average_cost(store_id, material_id)
        if suggested is not None:
            converted["price"] = suggested
            converted["price_source"] = "history"
        else:
            converted["price_source"] = "missing"
    return converted


@app.get("/api/{store_id}/receiving/drafts")
def list_drafts(store_id: str, c: Ctx = Depends(store_ctx)):
    return c.store.list_drafts(store_id)


@app.get("/api/{store_id}/receiving/drafts/{draft_id}")
def get_draft(store_id: str, draft_id: str, c: Ctx = Depends(store_ctx)):
    draft = c.store.get_draft(store_id, draft_id)
    if not draft:
        raise HTTPException(404, "ไม่พบร่างใบรับของนี้")
    return draft


@app.put("/api/{store_id}/receiving/drafts/{draft_id}")
def update_draft(store_id: str, draft_id: str, data: dict, c: Ctx = Depends(store_ctx)):
    c.store.update_draft(store_id, draft_id, data)
    return {"ok": True}


@app.delete("/api/{store_id}/receiving/drafts/{draft_id}")
def discard_draft(store_id: str, draft_id: str, c: Ctx = Depends(store_ctx)):
    draft = c.store.get_draft(store_id, draft_id)
    if draft and draft.get("image_path"):
        delete_receipt_image(draft["image_path"])
    c.store.delete_draft(store_id, draft_id)
    return {"ok": True}


@app.post("/api/{store_id}/receiving/drafts/{draft_id}/confirm")
def confirm_draft(store_id: str, draft_id: str, c: Ctx = Depends(store_ctx)):
    """Turns a reviewed draft into a real receiving: stock and cost update,
    and every matched line reinforces its alias. Unmatched or price-less
    lines are skipped rather than blocking the whole confirm."""
    draft = c.store.get_draft(store_id, draft_id)
    if not draft:
        raise HTTPException(404, "ไม่พบร่างใบรับของนี้")

    receiving_items = []
    skipped = []
    for item in draft.get("items", []):
        material_id = (item.get("match") or {}).get("material_id")
        if not material_id:
            skipped.append(f"{item.get('name')} (ยังไม่ได้เลือกวัตถุดิบ)")
            continue
        price = item.get("price")
        if price is None:
            skipped.append(f"{item.get('name')} (ไม่มีราคา)")
            continue
        receiving_items.append({
            "material_id": material_id,
            "quantity": item.get("qty", 0),
            "unit_cost": price,
        })
        c.matcher.learn(store_id, item.get("name", ""), material_id, draft.get("supplier"))

    if not receiving_items:
        raise HTTPException(400, "ไม่มีรายการที่จับคู่วัตถุดิบแล้วเลย - เลือกวัตถุดิบให้แต่ละรายการก่อน")

    # Through the same validation the form uses, so a scanned delivery
    # cannot enter in a shape a typed one would have been refused in.
    # A scan that found no date falls back to today rather than storing
    # an empty one, which would sort below everything and belong to no
    # month at all - invisible in every report that filters by period.
    r = _clean_receiving({
        "supplier": draft.get("supplier") or "",
        "date": draft.get("date") or _today(),
        "items": receiving_items,
        "note": f"จากสแกน AI (draft {draft_id})",
    })
    result = c.store.add_receiving(store_id, supplier=r["supplier"], date=r["date"],
                                   items=r["items"], note=r["note"])
    c.store.delete_draft(store_id, draft_id)
    return {**result, "skipped_items": skipped}


@app.get("/api/vision/status")
def vision_status(c: Ctx = Depends(ctx)):
    return {"providers": vision.available_providers()}


# ---- matching engine ---------------------------------------------------

@app.get("/api/{store_id}/match")
def match_one(store_id: str, name: str, supplier: str | None = None,
              c: Ctx = Depends(store_ctx)):
    return c.matcher.match(store_id, name, supplier)


@app.post("/api/{store_id}/match/all")
def match_all(store_id: str, data: dict, c: Ctx = Depends(store_ctx)):
    return c.matcher.match_all(store_id, data.get("items", []), data.get("supplier"))


@app.post("/api/{store_id}/match/learn")
def learn_match(store_id: str, raw_name: str, material_id: str,
                supplier: str | None = None, c: Ctx = Depends(store_ctx)):
    c.matcher.learn(store_id, raw_name, material_id, supplier)
    return {"ok": True}


@app.post("/api/{store_id}/materials/{material_id}/aliases")
def add_alias(store_id: str, material_id: str, alias: str, c: Ctx = Depends(store_ctx)):
    c.store.add_alias(store_id, material_id, alias)
    return {"ok": True}


@app.delete("/api/{store_id}/materials/{material_id}/aliases")
def remove_alias(store_id: str, material_id: str, alias: str, c: Ctx = Depends(store_ctx)):
    c.store.remove_alias(store_id, material_id, alias)
    return {"ok": True}


# ---- AI recipe suggestions (step 3.3) ----------------------------------
# The model proposes which ingredients a dish uses. It never proposes how
# much, because that depends on how this kitchen portions and a guessed
# number is indistinguishable from a measured one once saved. The one
# exception is resale goods, where selling one bottle consumes one bottle.
#
# Bulk drafting (many menus in one Gemini call) was tried and removed for
# the closed beta - a single request covering dozens of menus is exactly
# the shape that can exceed the model's output token budget. Per-menu
# suggestion asks for one dish at a time, which is a small, predictable
# request regardless of how many menus the business has.

def _suggest_for(c: Ctx, store_id: str, menu_names: list[str]) -> list[dict]:
    """Runs the suggestion and attaches, for each proposed ingredient,
    the material it matches in this branch's catalog - reusing the same
    matching engine the invoice scanner uses, so wording learned there
    pays off here too."""
    suggestions = suggester.suggest(menu_names)
    for entry in suggestions:
        for ing in entry["ingredients"]:
            ing["match"] = c.matcher.match(store_id, ing["name"])
    return suggestions


@app.get("/api/{store_id}/recipes/suggest/status")
def suggest_status(store_id: str, c: Ctx = Depends(store_ctx)):
    return {"available": suggester.available()}


@app.post("/api/{store_id}/recipes/suggest")
def suggest_recipe(store_id: str, item_name: str, c: Ctx = Depends(store_ctx)):
    """Draft one menu item. Returns the proposal without storing it - a
    single item is reviewed immediately, so there's nothing to come back to."""
    try:
        result = _suggest_for(c, store_id, [item_name])
    except VisionError as e:
        raise HTTPException(502, str(e))
    return result[0] if result else {"menu": item_name, "kind": "cooked", "ingredients": []}


@app.get("/api/{store_id}/recipes/drafts")
def list_recipe_drafts(store_id: str, c: Ctx = Depends(store_ctx)):
    return c.store.list_recipe_drafts(store_id)


@app.delete("/api/{store_id}/recipes/drafts/{item_name}")
def delete_recipe_draft(store_id: str, item_name: str, c: Ctx = Depends(store_ctx)):
    c.store.delete_recipe_draft(store_id, item_name)
    return {"ok": True}


@app.get("/api/{store_id}/recipes/skips")
def list_recipe_skips(store_id: str, c: Ctx = Depends(store_ctx)):
    return c.store.list_recipe_skips(store_id)


@app.post("/api/{store_id}/recipes/skips/{item_name}")
def skip_recipe(store_id: str, item_name: str, c: Ctx = Depends(store_ctx)):
    """Mark a menu item as one that legitimately has no recipe."""
    c.store.skip_recipe(store_id, item_name)
    c.store.delete_recipe_draft(store_id, item_name)
    return {"ok": True}


@app.delete("/api/{store_id}/recipes/skips/{item_name}")
def unskip_recipe(store_id: str, item_name: str, c: Ctx = Depends(store_ctx)):
    c.store.unskip_recipe(store_id, item_name)
    return {"ok": True}


# ---- sales reporting (from our own saved copy) -------------------------
# These read Store.list_sales, never Loyverse. That's what lets a report
# cover history the POS has already dropped, and keeps the home screen
# from failing because an external API is slow.

def _window(from_: str | None, to: str | None) -> tuple[str, str]:
    """Defaults to today when no range is given.

    Both ends come back in the same format the saved sale dates use.
    list_sales compares these as strings, and '2026-08-24T00:00:00.000Z'
    and '2026-08-24T00:00:00+00:00' are the same instant that compare as
    different text - so a boundary sale falls in or out of the window
    depending on which format happened to build it."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (normalize_time(from_) or normalize_time(start.isoformat()),
            normalize_time(to) or normalize_time(now.isoformat()))


def _recipes_for(c: Ctx, store_id: str, sales: list[dict]) -> dict:
    """Recipes for the menus that sold in this window.

    Fetched as one collection read rather than a lookup per dish. The
    per-dish version cost a round trip for every distinct menu on the
    receipt list, which on a varied day was dozens of reads to answer a
    single screen."""
    names = {i.get("name") for s in sales for i in s.get("items", []) if i.get("name")}
    if not names:
        return {}
    all_recipes = c.store.all_recipes(store_id)
    return {n: all_recipes.get(n, []) for n in names}


def _recipes_for_menus(c: Ctx, store_id: str, names: set) -> dict:
    """Same as _recipes_for, for menus already known by name."""
    if not names:
        return {}
    all_recipes = c.store.all_recipes(store_id)
    return {n: all_recipes.get(n, []) for n in names}


def _rollups_for(c: Ctx, store_id: str, start: str, end: str,
                 tz: int) -> list[dict]:
    """The stored day-rows covering a window given as two instants."""
    first = daily_rollup.local_day(start, tz)
    last = daily_rollup.local_day(end, tz)
    if not first or not last:
        return []
    return daily_rollup.ensure_daily(
        c.store, store_id, first, last, tz, daily_rollup.local_day(_now(), tz))


@app.get("/api/{store_id}/sales/overview")
def sales_overview(store_id: str, from_: str | None = None, to: str | None = None,
                   granularity: str = "day", tz_offset: int = 0, top: int = 5,
                   compare: bool = True, c: Ctx = Depends(store_money)):
    """Everything the sales screens show, from one read of the data.

    The summary, the chart and the best-sellers used to be three
    endpoints, and the page called all three at once - so the same window
    of sales was read from Firestore twice over, plus the comparison
    window again. On a busy month that was thousands of documents fetched
    to answer one screen.

    Now a window of whole days is answered from one row per day (see
    core/daily_rollup), so a month costs about thirty reads instead of
    three thousand. An hour-by-hour view still reads the bills: it is
    always today or yesterday, so it is one day of them, and storing an
    hourly summary would be twenty-four times the rows to save a page
    nobody opens for a month at a time.

    `tz_offset` is what the browser thinks; the shop's own offset wins if
    it has one. See Ctx.tz_offset.
    """
    start, end = _window(from_, to)
    tz = c.tz_or(tz_offset)
    materials = c.store.list_materials(store_id)

    if granularity == "hour":
        sales = c.store.list_sales(store_id, start, end)
        recipes = _recipes_for(c, store_id, sales)
        current = sales_report.summarise(sales, recipes, materials, granularity, tz)
        best = sales_report.top_items(sales, top)
    else:
        rollups = _rollups_for(c, store_id, start, end, tz)
        recipes = _recipes_for_menus(c, store_id,
                                     {n for r in rollups for n in (r.get("items") or {})})
        current = daily_rollup.summarise(rollups, recipes, materials)
        best = daily_rollup.top_items(rollups, top)

    # The comparison reads a second window of the same length, which for
    # a month is as many documents again as the answer itself. Only the
    # home screen shows it; the sales and income pages were paying twice
    # for a figure they never displayed, so they now ask for it off.
    #
    # It skips the recipe lookups and material costing either way - a
    # percentage against last month needs a total, nothing more.
    comparison = None
    if compare:
        p_start, p_end = sales_report.previous_window(start, end)
        if granularity == "hour":
            previous = sales_report.summarise(
                c.store.list_sales(store_id, p_start, p_end), {}, [], granularity, tz)
        else:
            previous = daily_rollup.summarise(
                _rollups_for(c, store_id, p_start, p_end, tz), {}, [])
        comparison = sales_report.compare_previous(current, previous)

    return {
        **current,
        "from": start, "to": end, "granularity": granularity,
        "compare": comparison,
        "top_items": best,
    }


@app.get("/api/{store_id}/sales/daily")
def sales_daily(store_id: str, from_: str | None = None, to: str | None = None,
                c: Ctx = Depends(store_money)):
    """Per-day totals for the list beside the chart.

    From the same rows the chart uses, which is not a detail: this used
    to group by UTC while the chart grouped by the shop's clock, so an
    evening bill appeared on one date in the list and the next date on
    the graph directly above it.
    """
    start, end = _window(from_, to)
    return daily_rollup.breakdown(
        _rollups_for(c, store_id, start, end, c.tz_offset))


@app.get("/api/{store_id}/alerts")
def alerts(store_id: str, c: Ctx = Depends(store_ctx)):
    """Deliberately NOT gated on view_money - staff need to know stock is
    running out, and none of this exposes takings."""
    sessions = c.store.list_count_sessions(store_id)
    last_closed = next((s.get("closed_at") for s in sessions
                        if s.get("status") == "closed"), None)
    return sales_report.build_alerts(
        materials=c.store.list_materials(store_id),
        pending_drafts=len(c.store.list_drafts(store_id)),
        last_count_at=last_closed,
    )


@app.get("/api/{store_id}/brief")
def daily_brief_for(store_id: str, date: str | None = None,
                    c: Ctx = Depends(store_money)):
    """Yesterday in a few lines, written once and kept.

    Built the first time anyone asks for a given day and stored, so the
    second person to open the app that morning - and the same person
    opening it again at noon - reads one document instead of rebuilding
    it. Thrown away with the day it summarises whenever a late bill
    changes that day (see Store.delete_daily).

    Deliberately lazy rather than scheduled. A nightly job would need a
    scheduler this app does not have, would run for branches nobody
    looks at, and would go wrong quietly at four in the morning; asking
    for it is what proves someone wants it.
    """
    tz = c.tz_offset
    today = daily_rollup.local_day(_now(), tz)
    day = date or _shift_day(today, -1)

    if day >= today:
        # Today is still selling. A brief for it would be a half-day
        # reported as a day, which is the one shape of wrong that looks
        # exactly like a bad day.
        return {"date": day, "ready": False,
                "reason": "วันนี้ยังขายไม่จบ สรุปจะมีให้พรุ่งนี้เช้า"}

    stored = c.store.get_brief(store_id, day)
    if stored:
        return {**stored, "ready": True, "cached": True}

    # The day itself plus the run it is measured against, in one read.
    first = _shift_day(day, -daily_brief.BASELINE_DAYS)
    rollups = daily_rollup.ensure_daily(c.store, store_id, first, day, tz, today)

    sessions = c.store.list_count_sessions(store_id)
    last_closed = next((s.get("closed_at") for s in sessions
                        if s.get("status") == "closed"), None)

    brief = daily_brief.build(
        day=day,
        rollups=rollups,
        recipes=c.store.all_recipes(store_id),
        materials=c.store.list_materials(store_id),
        days_since_count=_days_since(last_closed),
    )

    provider = _assistant()
    if provider is not None:
        brief = daily_brief.polish(provider, brief)

    c.store.set_brief(store_id, day, brief)
    return {**brief, "ready": True, "cached": False}


def _shift_day(day: str, days: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=days)) \
        .strftime("%Y-%m-%d")


def _days_since(iso: str | None) -> int | None:
    """None means never, which needs a different sentence from 'a while
    ago' - one is a setup step, the other a habit that slipped."""
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).days


# How many questions one business may ask in a day. A ceiling, not a
# meter - nobody is billed for these. It exists so a page left open in a
# retry loop, or someone curious, cannot spend the shop's whole quota
# before lunch.
ASSISTANT_DAILY_LIMIT = int(os.environ.get("ASSISTANT_DAILY_LIMIT", "50"))

# The longest window one question may cover, in the shop's days.
MAX_ASK_DAYS = 400


@app.post("/api/{store_id}/assistant/ask")
def assistant_ask(store_id: str, data: dict, c: Ctx = Depends(store_money)):
    """One question about one branch, over a period the caller chose.

    The period is a parameter rather than something the model works out
    for itself. That is deliberate: it means the answer is about a
    window the person can see on the screen in front of them, instead of
    one they have to trust was picked correctly - and it keeps every
    figure pre-computed, which is the whole design (core/assistant.py).
    """
    provider = _assistant()
    if provider is None:
        raise HTTPException(503, "ยังไม่ได้ตั้งค่า GEMINI_API_KEY - ผู้ช่วยจึงยังใช้ไม่ได้")

    tz = c.tz_offset
    today = daily_rollup.local_day(_now(), tz)

    asked = c.store.assistant_asks_today(today)
    if asked >= ASSISTANT_DAILY_LIMIT:
        raise HTTPException(
            429, f"วันนี้ถามครบ {ASSISTANT_DAILY_LIMIT} คำถามแล้ว พรุ่งนี้ถามต่อได้")

    first, last, span = _validated_assistant_window(
        data.get("from"), data.get("to"), today)
    # Fetched once and shared by both periods. The recipe book and the
    # material list are the same whichever window is being summarised,
    # and they are a collection read each.
    recipes = c.store.all_recipes(store_id)
    materials = c.store.list_materials(store_id)

    current, rollups = _period_snapshot(c, store_id, first, last, tz, today,
                                        recipes, materials)
    # The period immediately before, of the same length, so "เทียบกับช่วง
    # ก่อนหน้า" is answerable without a second request.
    prev_last = _shift_day(first, -1)
    prev_first = _shift_day(prev_last, -(len(span) - 1))
    previous, _ = _period_snapshot(c, store_id, prev_first, prev_last, tz, today,
                                   recipes, materials)

    context = assistant_lib.build_context(
        current=current, previous=_trim_previous(previous),
        series=daily_rollup.breakdown(rollups))

    history = data.get("previous_questions") or []
    if not isinstance(history, list):
        raise HTTPException(400, "ประวัติคำถามไม่ถูกต้อง")
    result = assistant_lib.answer(provider, context, data.get("question", ""),
                                  previous_questions=history)
    if result["ok"]:
        # Counted only when a question actually reached the provider. A
        # refused empty box should not cost anyone their allowance.
        c.store.record_assistant_ask(today)

    return {**result, "from": first, "to": last,
            "asks_today": asked + (1 if result["ok"] else 0),
            "daily_limit": ASSISTANT_DAILY_LIMIT,
            "caveats": current.get("caveats", [])}


@app.get("/api/{store_id}/assistant/insights")
def assistant_insights(store_id: str, from_: str = "", to: str = "",
                       c: Ctx = Depends(store_money)):
    """Top actions computed from shop data, with no model or business-data write.

    The response can only contain navigation actions from advisor.READ_ONLY_ROUTES.
    Nothing in this path accepts an update payload or has access to a mutation
    callback, which keeps recommendations separate from business operations.
    The reporting layer may still refresh its derived daily cache; it cannot
    alter source sales, stock, recipes, purchases, or expenses.
    """
    tz = c.tz_offset
    today = daily_rollup.local_day(_now(), tz)
    first, last, span = _validated_assistant_window(from_, to, today)
    recipes = c.store.all_recipes(store_id)
    materials = c.store.list_materials(store_id)
    current, _ = _period_snapshot(c, store_id, first, last, tz, today,
                                  recipes, materials)
    prev_last = _shift_day(first, -1)
    prev_first = _shift_day(prev_last, -(len(span) - 1))
    previous, _ = _period_snapshot(c, store_id, prev_first, prev_last, tz, today,
                                   recipes, materials)
    previous_trimmed = _trim_previous(previous)
    return {
        "from": first,
        "to": last,
        "recommendations": advisor_lib.build_recommendations(
            current, previous_trimmed, limit=3),
        "analysis": advisor_lib.build_deep_analysis(current, previous_trimmed),
        "read_only": True,
    }


@app.get("/api/{store_id}/assistant/tracking")
def list_assistant_tracking(store_id: str, c: Ctx = Depends(store_money)):
    return c.store.list_advice_tracking(store_id)


@app.post("/api/{store_id}/assistant/tracking")
def create_assistant_tracking(store_id: str, data: dict,
                              c: Ctx = Depends(store_money)):
    """A person, not the assistant, chooses to save one visible recommendation."""
    tz = c.tz_offset
    today = daily_rollup.local_day(_now(), tz)
    first, last, span = _validated_assistant_window(
        data.get("from"), data.get("to"), today)
    if last >= today:
        raise HTTPException(400, "การเก็บค่าก่อนเริ่มต้องเลือกช่วงที่ขายจบแล้ว ไม่รวมวันนี้")
    recipes = c.store.all_recipes(store_id)
    materials = c.store.list_materials(store_id)
    current, _ = _period_snapshot(c, store_id, first, last, tz, today,
                                  recipes, materials)
    prev_last = _shift_day(first, -1)
    prev_first = _shift_day(prev_last, -(len(span) - 1))
    previous, _ = _period_snapshot(c, store_id, prev_first, prev_last, tz, today,
                                   recipes, materials)
    visible = advisor_lib.build_recommendations(
        current, _trim_previous(previous), limit=3)
    recommendation = next((row for row in visible
                           if row.get("id") == data.get("recommendation_id")), None)
    if not recommendation:
        raise HTTPException(400, "คำแนะนำนี้ไม่ได้อยู่ในรายการปัจจุบันแล้ว")
    already_active = any(
        row.get("recommendation", {}).get("id") == recommendation["id"]
        and row.get("status") not in {"completed", "cancelled"}
        for row in c.store.list_advice_tracking(store_id))
    if already_active:
        raise HTTPException(409, "คำแนะนำนี้อยู่ในแผนติดตามแล้ว")
    note = str(data.get("note") or "").strip()[:300]
    now = _now()
    return c.store.add_advice_tracking(store_id, {
        "recommendation": recommendation,
        "baseline": advisor_lib.tracking_baseline(current, recommendation),
        "status": "planned",
        "note": note,
        "created_at": now,
        "updated_at": now,
        "created_by": c.user["uid"],
    })


@app.patch("/api/{store_id}/assistant/tracking/{tracking_id}")
def update_assistant_tracking(store_id: str, tracking_id: str, data: dict,
                              c: Ctx = Depends(store_money)):
    record = c.store.get_advice_tracking(store_id, tracking_id)
    if not record:
        raise HTTPException(404, "ไม่พบแผนติดตามนี้")
    status = data.get("status", record.get("status"))
    if status not in {"planned", "in_progress", "cancelled"}:
        raise HTTPException(400, "สถานะไม่ถูกต้อง — การจบแผนต้องกดวัดผล")
    update = {"status": status, "updated_at": _now()}
    if "note" in data:
        update["note"] = str(data.get("note") or "").strip()[:300]
    c.store.update_advice_tracking(store_id, tracking_id, update)
    return {**record, **update}


@app.post("/api/{store_id}/assistant/tracking/{tracking_id}/evaluate")
def evaluate_assistant_tracking(store_id: str, tracking_id: str, data: dict,
                                c: Ctx = Depends(store_money)):
    record = c.store.get_advice_tracking(store_id, tracking_id)
    if not record:
        raise HTTPException(404, "ไม่พบแผนติดตามนี้")
    tz = c.tz_offset
    today = daily_rollup.local_day(_now(), tz)
    first, last, span = _validated_assistant_window(
        data.get("from"), data.get("to"), today)
    if last >= today:
        raise HTTPException(400, "ช่วงวัดผลต้องเป็นวันที่ขายจบแล้ว ไม่รวมวันนี้")
    baseline_period = record.get("baseline", {}).get("period", {})
    baseline_days = int(baseline_period.get("days") or 0)
    if first <= (baseline_period.get("to") or ""):
        raise HTTPException(400, "ช่วงวัดผลต้องเริ่มหลังช่วงข้อมูลก่อนทำแผน")
    if baseline_days and len(span) != baseline_days:
        raise HTTPException(400, "ช่วงก่อนและหลังต้องมีจำนวนวันเท่ากันเพื่อเปรียบเทียบได้")
    current, _ = _period_snapshot(
        c, store_id, first, last, tz, today,
        c.store.all_recipes(store_id), c.store.list_materials(store_id))
    evaluation = {
        "period": current["period"],
        **advisor_lib.measure_outcome(record.get("baseline") or {}, current),
    }
    now = _now()
    update = {"status": "completed", "evaluation": evaluation,
              "evaluated_at": now, "updated_at": now}
    c.store.update_advice_tracking(store_id, tracking_id, update)
    return {**record, **update}


def _validated_assistant_window(first_value: str | None,
                                last_value: str | None,
                                today: str) -> tuple[str, str, list[str]]:
    """Validate before touching rollups so malformed/future dates cannot write cache."""
    first = (first_value or "")[:10] or f"{today[:7]}-01"
    last = (last_value or "")[:10] or today
    try:
        span = daily_rollup.days_between(first, last)
    except (TypeError, ValueError):
        raise HTTPException(400, "รูปแบบวันที่ไม่ถูกต้อง")
    if not span:
        raise HTTPException(400, "ช่วงวันที่ไม่ถูกต้อง")
    if last > today:
        raise HTTPException(400, "ยังวิเคราะห์วันที่ในอนาคตไม่ได้")
    if len(span) > MAX_ASK_DAYS:
        raise HTTPException(
            400, f"ช่วงที่ถามยาวเกิน {MAX_ASK_DAYS} วัน - เลือกช่วงให้แคบลง")
    return first, last, span


def _period_snapshot(c: Ctx, store_id: str, first: str, last: str, tz: int,
                     today: str, recipes: dict,
                     materials: list[dict]) -> tuple[dict, list[dict]]:
    rollups = daily_rollup.ensure_daily(c.store, store_id, first, last, tz, today)
    snapshot = assistant_lib.build_snapshot(
        branch=store_id,
        rollups=rollups,
        recipes=recipes,
        materials=materials,
        expenses=[e for e in c.store.list_expenses(store_id)
                  if first <= (e.get("date") or "")[:10] <= last],
        receivings=c.store.list_receivings(store_id, first, last),
        period_from=first, period_to=last, today=today)
    return snapshot, rollups


def _trim_previous(snapshot: dict) -> dict:
    """Last month without the parts that are not last month's.

    Stock is a balance as it stands right now, and the caveats are about
    the current period - carrying both into the previous period's block
    would put two different "stock" figures in front of the model and
    invite it to compare them as if one were historical.
    """
    return {k: v for k, v in snapshot.items() if k not in ("stock", "caveats")}


@app.get("/api/{store_id}/sales/reconcile")
def reconcile_sales(store_id: str, days: int = 1, c: Ctx = Depends(store_money)):
    """Compare what the POS reports against what we saved, for the last
    N days.

    Built after saved totals came in at a third of the real figure and it
    took several rounds of guessing to find out why. A number that
    disagrees with the POS is worse than no number, and the only way to
    trust it again is to be able to check it on demand rather than reason
    about it.

    Reads the POS live, so it's limited by the plan's history window the
    same way everything else is."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    start_iso = normalize_time(start.isoformat())

    try:
        live = c.provider_for(store_id).get_receipts(store_id, created_at_min=start_iso)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 402:
            raise HTTPException(402, "แพ็กเกจ Loyverse ดึงย้อนหลังได้ไม่เกิน 30 วัน")
        raise

    # Only what the POS produced. Orders recorded by hand - Grab, phone,
    # the online menu - are real sales in the same collection, and the
    # POS has never heard of them: counted here they would report as
    # missing forever, and the home screen's "อัปเดตข้อมูล" button would
    # try to repair them on every single press.
    saved = [s for s in c.store.list_sales(store_id, start_iso,
                                           normalize_time(now.isoformat()))
             if is_pos_sale(s)]
    saved_by_number = {s.get("receipt_number"): s for s in saved}

    missing = []
    for r in live:
        num = r.get("receipt_number")
        if num and num not in saved_by_number:
            missing.append({
                "receipt_number": num,
                "sold_at": r.get("created_at"),
                "recorded_at": r.get("recorded_at"),
                "total": r.get("total"),
                "is_refund": bool(r.get("is_refund")),
            })

    live_total = sum(r.get("total") or 0 for r in live)
    saved_total = sum(s.get("total") or 0 for s in saved)

    return {
        "window_from": start_iso,
        "pos": {"count": len(live), "total": round(live_total, 2)},
        "saved": {"count": len(saved), "total": round(saved_total, 2)},
        "missing_count": len(missing),
        # The receipts themselves, so a gap can be traced to a terminal or
        # a time of day instead of guessed at.
        "missing": missing[:50],
        "cursor": c.store.get_sync_cursor(store_id),
    }


@app.post("/api/{store_id}/sales/repair")
def repair_sales(store_id: str, c: Ctx = Depends(store_settings)):
    """Re-read everything the POS still has and re-save it.

    Replaces the old backfill/resync pair. Both existed to patch the same
    hole from different angles, and a branch could miss both - which is
    exactly what left a month of history unsaved while today's figures
    looked fine."""
    try:
        result = sync_branch(c.provider_for(store_id), c.store, store_id, full=True)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 402:
            raise HTTPException(402, "แพ็กเกจ Loyverse ดึงย้อนหลังได้ไม่เกิน 30 วัน")
        raise
    return result


@app.get("/api/{store_id}/counts")
def list_count_sessions(store_id: str, c: Ctx = Depends(store_ctx)):
    return c.store.list_count_sessions(store_id)


@app.get("/api/{store_id}/counts/open")
def get_open_count(store_id: str, c: Ctx = Depends(store_ctx)):
    return c.store.open_count_session(store_id) or {}


@app.post("/api/{store_id}/counts")
def start_count(store_id: str, c: Ctx = Depends(store_ctx)):
    """Only one count runs at a time - two open sessions would each hold a
    different idea of the same shelf."""
    existing = c.store.open_count_session(store_id)
    if existing:
        return existing
    return c.store.create_count_session(store_id, _now())


@app.put("/api/{store_id}/counts/{session_id}/entry")
def set_count_entry(store_id: str, session_id: str, material_id: str, counted: float,
                    c: Ctx = Depends(store_ctx)):
    session = c.store.get_count_session(store_id, session_id)
    if not session or session.get("status") != "open":
        raise HTTPException(400, "รอบนับนี้ปิดไปแล้ว")
    c.store.set_count_entry(store_id, session_id, material_id, counted)
    return {"ok": True}


@app.delete("/api/{store_id}/counts/{session_id}/entry")
def clear_count_entry(store_id: str, session_id: str, material_id: str,
                      c: Ctx = Depends(store_ctx)):
    c.store.clear_count_entry(store_id, session_id, material_id)
    return {"ok": True}


@app.delete("/api/{store_id}/counts/{session_id}")
def cancel_count(store_id: str, session_id: str, c: Ctx = Depends(store_ctx)):
    """Discard only an open count. Closed counts are audit history and are
    never removable through this action."""
    session = c.store.get_count_session(store_id, session_id)
    if not session:
        raise HTTPException(404, "ไม่พบรอบตรวจนับนี้")
    if session.get("status") != "open":
        raise HTTPException(400, "ยกเลิกได้เฉพาะรอบที่กำลังตรวจนับ")
    c.store.delete_count_session(store_id, session_id)
    return {"ok": True}


@app.post("/api/{store_id}/counts/{session_id}/close")
def close_count(store_id: str, session_id: str, c: Ctx = Depends(store_ctx)):
    """Commits every counted line to the ledger in one go, tagged with this
    session so the variance report can find exactly this count's
    corrections later."""
    session = c.store.get_count_session(store_id, session_id)
    if not session:
        raise HTTPException(404, "ไม่พบรอบนับนี้")
    if session.get("status") != "open":
        raise HTTPException(400, "รอบนับนี้ปิดไปแล้ว")
    entries = session.get("entries") or {}
    if not entries:
        raise HTTPException(400, "ยังไม่ได้นับอะไรเลย")

    closed_at = _now()
    for material_id, counted in entries.items():
        movement = c.ledger.record_count(store_id, material_id, float(counted),
                                         note=f"รอบนับ {closed_at[:10]}")
        # Tag it so variance can tell this count's correction from any
        # other adjustment made on the same day.
        #
        # The id comes back from the write. Re-reading the material's
        # whole movement history to find the row we had just written was
        # a query per counted ingredient, and it identified the movement
        # by "newest first" - which is a guess, not a fact, when two
        # things are written in the same second.
        c.store._col(store_id, "stock_movements").document(movement["id"]).update({
            "ref": session_id, "occurred_at": closed_at,
        })

    c.store.close_count_session(store_id, session_id, closed_at)
    return {"ok": True, "counted": len(entries), "closed_at": closed_at}


@app.get("/api/{store_id}/variance/{session_id}")
def variance_report(store_id: str, session_id: str, c: Ctx = Depends(store_ctx)):
    """Not gated on view_money, deliberately.

    Variance is the point of counting, and staff are the ones who count.
    Sending them to a screen that reports nothing back would make the job
    feel pointless and the counts would stop happening.

    The trade-off is real and was taken knowingly: shortfall value is
    derived from ingredient costs, so this does reveal roughly what
    things cost. It stops well short of the takings and margin figures
    that view_money guards."""
    session = c.store.get_count_session(store_id, session_id)
    if not session or session.get("status") != "closed":
        raise HTTPException(400, "ต้องปิดรอบนับก่อนถึงจะวิเคราะห์ได้")

    previous = c.store.previous_closed_session(store_id, session["closed_at"])
    pct, value = _thresholds(c)
    rows = variance_lib.analyse_session(
        c.ledger, store_id, session, previous, c.store.list_materials(store_id),
        threshold_pct=pct, threshold_value=value)

    return {
        "session": {"id": session["id"], "closed_at": session.get("closed_at")},
        "previous_closed_at": (previous or {}).get("closed_at"),
        "has_baseline": previous is not None,
        "thresholds": {"pct": pct, "value": value},
        "summary": variance_lib.summarise(rows),
        "rows": rows,
        "unmeasured_menus": _unmeasured_menus(c, store_id, session, previous),
        "offcycle_adjustments": variance_lib.count_offcycle_adjustments(
            c.ledger.list_movements(store_id),
            (previous or {}).get("closed_at"), session.get("closed_at"), session["id"]),
    }


def _unmeasured_menus(c: Ctx, store_id: str, session: dict,
                      previous: dict | None) -> list[str]:
    """Menus sold in the period with no recipe behind them.

    Their ingredients walked out of the kitchen unaccounted for, so they
    land in the report as unexplained losses. If Loyverse can't be reached
    we return nothing rather than a guess - an empty list here reads as
    "none found", so it's better to be silent than wrong about which
    menus are covered."""
    try:
        receipts = c.provider_for(store_id).get_receipts(
            store_id, created_at_min=(previous or {}).get("closed_at"))
    except Exception:
        return []

    end = session.get("closed_at")
    sold = set()
    for r in receipts:
        if end and (r.get("created_at") or "") > end:
            continue
        for line in r.get("line_items", []):
            name = line.get("item_name")
            if name:
                sold.add(name)

    recipes = {name: c.store.get_recipe(store_id, name) for name in sold}
    return variance_lib.unmeasured_menus(sold, recipes, c.store.list_recipe_skips(store_id))


@app.get("/api/{store_id}/variance-settings")
def get_variance_settings(store_id: str, c: Ctx = Depends(store_ctx)):
    pct, value = _thresholds(c)
    return {"pct": pct, "value": value}


@app.post("/api/{store_id}/variance-settings")
def set_variance_settings(store_id: str, pct: float, value: float,
                          c: Ctx = Depends(store_settings)):
    c.store.set_setting("variance_threshold_pct", pct)
    c.store.set_setting("variance_threshold_value", value)
    return {"pct": pct, "value": value}


# ---- recipes -----------------------------------------------------------

@app.get("/api/{store_id}/recipes/{item_name}")
def get_recipe(store_id: str, item_name: str, c: Ctx = Depends(store_ctx)):
    return c.store.get_recipe(store_id, item_name)


@app.put("/api/{store_id}/recipes/{item_name}")
def set_recipe(store_id: str, item_name: str, ingredients: list[dict],
               c: Ctx = Depends(store_ctx)):
    c.store.set_recipe(store_id, item_name, ingredients)
    # A confirmed recipe supersedes its draft - leaving the draft around
    # would offer the same suggestion again over a recipe that's now real.
    c.store.delete_recipe_draft(store_id, item_name)
    return {"ok": True}


# ---- expenses / receipts ----------------------------------------------

@app.get("/api/{store_id}/expenses")
def list_expenses(store_id: str, category: str | None = None,
                  c: Ctx = Depends(store_money)):
    return c.store.list_expenses(store_id, category)


def _clean_expense(category, name, amount, date) -> dict:
    """The rules live in core/expenses.py; this only turns a refusal into
    the 400 the browser expects. Its message is already written for the
    person who typed it, so it is passed straight through."""
    try:
        return clean_expense(category, name, amount, date)
    except ExpenseError as e:
        raise HTTPException(400, str(e))


@app.post("/api/{store_id}/expenses")
def add_expense(store_id: str, category: str, name: str, amount: float, date: str,
                c: Ctx = Depends(store_money)):
    e = _clean_expense(category, name, amount, date)
    return {"ok": True, **c.store.add_expense(store_id, **e)}


@app.put("/api/{store_id}/expenses/{expense_id}")
def update_expense(store_id: str, expense_id: str, data: dict,
                   c: Ctx = Depends(store_money)):
    """data: {category, name, amount, date}

    Corrects an entry that was typed wrong. Any month, not just this one:
    recording is restricted to the current month so nobody back-dates
    spending by accident, but a wrong number from last month is wrong
    until someone fixes it, and refusing to let them fix it just leaves
    the profit figure wrong for good.
    """
    if not c.store.get_expense(store_id, expense_id):
        raise HTTPException(404, "ไม่พบรายจ่ายนี้")
    e = _clean_expense(data.get("category"), data.get("name"),
                       data.get("amount"), data.get("date"))
    c.store.update_expense(store_id, expense_id, e)
    return {"ok": True, **e}


@app.delete("/api/{store_id}/expenses/{expense_id}")
def delete_expense(store_id: str, expense_id: str, c: Ctx = Depends(store_money)):
    """Removes it outright - no hidden record, nothing left in the list.

    Deliberate, and different from how stock corrections work: a stock
    count writes a movement rather than editing a number, because the
    discrepancy itself is information about the kitchen. An expense typed
    by mistake is not information about anything - it is a typo, and
    keeping a tombstone for it would just make the list harder to read.
    """
    if not c.store.get_expense(store_id, expense_id):
        raise HTTPException(404, "ไม่พบรายจ่ายนี้")
    c.store.delete_expense(store_id, expense_id)
    return {"ok": True}


@app.get("/api/{store_id}/receipts")
def list_receipts(store_id: str, created_at_min: str | None = None,
                  c: Ctx = Depends(store_money)):
    """Defaults to the last 31 days - Loyverse's free plan won't return
    anything older. A 402 with no results at all means the requested
    window is entirely beyond the plan's reach, which is a billing fact
    to explain rather than a server error to dump on the user."""
    try:
        return c.provider_for(store_id).get_receipts(store_id, created_at_min=created_at_min)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 402:
            raise HTTPException(
                402, "แพ็กเกจ Loyverse ที่ใช้อยู่ดูประวัติการขายย้อนหลังได้ไม่เกิน 31 วัน "
                     "- ถ้าต้องการมากกว่านี้ ต้องสมัคร Unlimited sales history ที่ Loyverse")
        raise


def _refuse_if_counted_since(c: Ctx, store_id: str, at: str | None, what: str):
    """Stops stock being taken back out from under a physical count.

    A count writes a correction that lands the shelf figure on a number
    someone measured by hand, and that number already included whatever
    these movements put there. Removing them afterwards moves the figure
    away from the one value in this system that was actually observed
    rather than derived. Correcting it from here would be silently
    overruling the person who counted."""
    closed = [s.get("closed_at") for s in c.store.list_count_sessions(store_id)
              if s.get("status") == "closed" and s.get("closed_at")]
    if closed and max(closed) > (at or ""):
        raise HTTPException(
            400, f"แก้ไขหรือลบ{what}ไม่ได้ - มีการนับสต๊อกหลังจากนั้นแล้ว "
                 "ถ้ายอดไม่ตรงให้แก้ด้วยการนับสต๊อกรอบใหม่แทน")


# ---- orders the till never saw --------------------------------------
# Grab, LINE MAN, the phone, the online menu. Recorded as ordinary sales
# so every report already includes them, and deducted through the same
# recipes so the shelf figure stays true. See core/delivery.py.

@app.get("/api/{store_id}/delivery-orders")
def list_delivery_orders(store_id: str, from_: str | None = None,
                         to: str | None = None, c: Ctx = Depends(store_money)):
    start, end = _window(from_, to)
    orders = [s for s in c.store.list_sales(store_id, start, end)
              if not is_pos_sale(s)]
    orders.sort(key=lambda s: s.get("date") or "", reverse=True)
    return {"channels": CHANNELS, "from": start, "to": end, "orders": orders}


@app.post("/api/{store_id}/delivery-orders")
def add_delivery_order(store_id: str, data: dict, c: Ctx = Depends(store_money)):
    """data: {order_id, source, items: [{name, qty, price}], date, note}

    `order_id` comes from the browser rather than being generated here,
    which is what makes a retry safe: a request that times out and is
    sent again carries the same id, and the second one is refused
    instead of deducting the same dish twice.
    """
    try:
        row = clean_order(data.get("order_id"), data.get("source"),
                          data.get("items"), data.get("date"), data.get("note", ""))
    except DeliveryError as e:
        raise HTTPException(400, str(e))

    # Into the one canonical timestamp format the saved sales use. Range
    # queries on this collection are ordered string comparisons, so
    # '...T12:00:00.000Z' and '...T12:00:00+00:00' are the same instant
    # that compare as different text - and an order written in the wrong
    # one falls out of every date range that should contain it.
    row["date"] = normalize_time(row["date"]) or row["date"]
    row["recorded_at"] = row["date"]

    number = row["receipt_number"]
    if c.store.get_sale(store_id, number):
        raise HTTPException(409, "บันทึกออเดอร์นี้ไปแล้ว")

    # Saved before stock moves, deliberately. That order means a failure
    # in between leaves an order with no deduction - visible, and fixable
    # by deleting it - rather than ingredients off the shelf with nothing
    # recording why.
    c.store.save_sale(store_id, number, row)
    # An order for yesterday - keyed in this morning from the notebook -
    # lands in a day that is already summarised. See invalidate_for_sales.
    daily_rollup.invalidate_for_sales(c.store, store_id, [row], _now(),
                                      c.tz_offset)

    recipes = c.store.all_recipes(store_id)
    known = set(c.store.list_material_ids(store_id))
    rows, unknown = deductions_for(
        recipes, known, [(i["name"], i["qty"]) for i in row["items"]],
        ref=f"receipt:{number}")
    c.store.deduct_stock_bulk(store_id, rows)

    return {
        "ok": True,
        "order": row,
        "deducted_materials": len(rows),
        # A dish with no recipe deducts nothing. That is not an error -
        # drinks and resale goods often have none - but it is worth
        # saying, because "stock didn't move" otherwise looks like a bug.
        "no_recipe": sorted({i["name"] for i in row["items"]
                             if not recipes.get(i["name"])}),
        "unknown_materials": sorted(m for m in unknown if m),
    }


@app.delete("/api/{store_id}/delivery-orders/{order_id}")
def delete_delivery_order(store_id: str, order_id: str,
                          c: Ctx = Depends(store_money)):
    """Removes the order and puts its ingredients back, as if it was
    never typed.

    Only orders recorded by hand. A receipt that came from the POS is
    refused with a 404 - deleting one here would not remove it from
    Loyverse, so the next sync would bring it straight back, having
    deducted its stock a second time on the way.
    """
    sale = c.store.get_sale(store_id, order_id)
    if not sale or is_pos_sale(sale):
        raise HTTPException(404, "ไม่พบออเดอร์นี้")

    _refuse_if_counted_since(c, store_id, sale.get("date"), "ออเดอร์นี้")

    returned = c.ledger.delete_by_ref(store_id, f"receipt:{order_id}")
    c.store.delete_sale(store_id, order_id)
    daily_rollup.invalidate_for_sales(c.store, store_id, [sale], _now(),
                                      c.tz_offset)
    return {"ok": True, "returned_materials": returned}


@app.post("/api/{store_id}/sync")
def sync(store_id: str, full: bool = False, c: Ctx = Depends(store_ctx)):
    """Pull new sales now instead of waiting for the next cycle.

    `full=true` re-reads everything the POS will give and re-saves it -
    the repair for a branch whose history has gaps. It's safe to run any
    time: saving is keyed by receipt number so it overwrites rather than
    duplicating, and stock is never deducted twice."""
    try:
        result = sync_branch(c.provider_for(store_id), c.store, store_id, full=full)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 402:
            raise HTTPException(
                402, "แพ็กเกจ Loyverse ดึงย้อนหลังได้ไม่เกิน 30 วัน")
        raise
    # processed_receipts kept for the existing UI copy; the rest is new
    # detail that makes "0" readable.
    return {"processed_receipts": result["deducted"], **result}


@app.post("/api/{store_id}/sync/reset-cursor")
def reset_sync_cursor(store_id: str, c: Ctx = Depends(store_settings)):
    """Escape hatch for a branch whose cursor is stuck or wrong (e.g. it
    was accidentally set far in the past and the next sync would try to
    pull months of history again). Re-establishes the cursor at "now",
    the same as a brand new connection - it does NOT back-fill anything
    skipped in between."""
    c.store.set_sync_cursor(store_id, _now())
    return {"ok": True}


# ---- user management ---------------------------------------------------

@app.get("/api/me")
def get_me(c: Ctx = Depends(ctx)):
    tenant = c.store.get_tenant() or {}
    return {
        "uid": c.user["uid"],
        "email": c.user.get("email"),
        "display_name": c.user.get("display_name", ""),
        "role": c.user["role"],
        "store_ids": c.user.get("store_ids", []),
        "capabilities": sorted(CAPABILITIES.get(c.user["role"], set())),
        "tenant_id": c.tenant_id,
        "business_name": tenant.get("name", ""),
        # None means nobody has said yet, which is the browser's cue to
        # say. A number here is the shop's answer and is not asked again.
        "timezone_offset": c.stored_tz,
    }


@app.post("/api/settings/timezone")
def set_timezone(data: dict, c: Ctx = Depends(ctx)):
    """Records where the shop is, once, from the first browser to load.

    Nobody is asked for this: a browser knows its own offset, and asking
    a shop owner to pick a timezone from a list is a setup step that
    earns nothing over reading it. Only the first answer is kept - see
    Store.set_timezone for why a later browser must not overwrite it.
    """
    try:
        offset = int(data.get("offset_minutes"))
    except (TypeError, ValueError):
        raise HTTPException(400, "offset_minutes ต้องเป็นตัวเลข")
    try:
        return {"timezone_offset": c.store.set_timezone(offset)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/users")
def list_users(c: Ctx = Depends(require_users)):
    return {
        "users": root_store.list_users(c.tenant_id),
        "pending_invites": root_store.list_invites(c.tenant_id),
    }


# An invite token IS a credential: whoever holds it can join this
# business with the role it carries. So it travels in the body, never in
# a URL - every proxy, load balancer and platform between the browser and
# here writes request URLs to an access log, and a log is a place
# credentials survive long after the invite was used or cancelled. The
# invited person's email address is in the body for the same reason,
# minus the escalation: it is someone else's personal data, and it does
# not belong in a log either.

def _create_invite(c: Ctx, email: str, role: str, store_ids: list[str]) -> dict:
    email = (email or "").strip()
    if not email:
        raise HTTPException(400, "กรุณาใส่อีเมล")
    if role not in ROLES:
        raise HTTPException(400, f"สิทธิ์ไม่ถูกต้อง - ต้องเป็นหนึ่งใน {', '.join(ROLES)}")
    if root_store.get_user_by_email(email):
        raise HTTPException(400, "อีเมลนี้มีบัญชีอยู่แล้ว")

    token = secrets.token_urlsafe(16)
    root_store.create_invite(token, email, role, c.tenant_id, store_ids,
                             invited_by=c.user["uid"], created_at=_now())
    return {"ok": True, "token": token, "email": email.lower(), "role": role,
            "store_ids": store_ids}


def _cancel_invite(c: Ctx, token: str) -> dict:
    invite = root_store.get_invite(token)
    if invite and invite.get("tenant_id") != c.tenant_id:
        raise HTTPException(403, "คำเชิญนี้ไม่ใช่ของธุรกิจคุณ")
    root_store.delete_invite(token)
    return {"ok": True}


@app.post("/api/users/invites")
def invite_user_v2(data: dict, c: Ctx = Depends(require_users)):
    """data: {email, role, store_ids: []}

    Creates an invite and returns its token. The owner copies the link and
    sends it however they like - there's no email delivery to configure, and
    nothing to go wrong silently in a spam folder."""
    ids = [str(s).strip() for s in (data.get("store_ids") or []) if str(s).strip()]
    return _create_invite(c, data.get("email", ""), data.get("role", ""), ids)


@app.post("/api/users/invites/cancel")
def cancel_invite_v2(data: dict, c: Ctx = Depends(require_users)):
    """data: {token}. A POST rather than a DELETE because DELETE with a
    body is poorly supported by proxies, and the token must not be in the
    URL - which is the entire reason this endpoint exists."""
    return _cancel_invite(c, (data.get("token") or "").strip())


@app.post("/api/users/invite")
def invite_user(email: str, role: str, store_ids: str = "",
                c: Ctx = Depends(require_users)):
    """Deprecated: puts the email in the query string. Kept only so a
    frontend deployed before this release keeps working; remove once
    everything is on /api/users/invites."""
    ids = [s.strip() for s in store_ids.split(",") if s.strip()]
    return _create_invite(c, email, role, ids)


@app.delete("/api/users/invite")
def cancel_invite(token: str, c: Ctx = Depends(require_users)):
    """Deprecated: puts an invite token in the query string, where it
    lands in access logs. Kept for the older frontend only."""
    return _cancel_invite(c, token)


def _same_tenant_user(c: Ctx, uid: str) -> dict:
    """Every user endpoint goes through here. An owner of business A asking
    to change a uid belonging to business B gets a 404 - the same answer as
    a uid that doesn't exist, so the endpoint can't be used to probe whether
    someone else's account is real."""
    target = root_store.get_user(uid)
    if not target or target.get("tenant_id") != c.tenant_id:
        raise HTTPException(404, "ไม่พบผู้ใช้นี้")
    return target


@app.put("/api/users/{uid}")
def update_user_role(uid: str, role: str, store_ids: str = "",
                     c: Ctx = Depends(require_users)):
    if role not in ROLES:
        raise HTTPException(400, f"สิทธิ์ไม่ถูกต้อง - ต้องเป็นหนึ่งใน {', '.join(ROLES)}")
    target = _same_tenant_user(c, uid)

    if target["role"] == OWNER and role != OWNER and root_store.count_owners(c.tenant_id) <= 1:
        raise HTTPException(400, "ต้องมีเจ้าของอย่างน้อย 1 คนเสมอ - แต่งตั้งเจ้าของคนใหม่ก่อน")

    ids = [s.strip() for s in store_ids.split(",") if s.strip()]
    root_store.set_user(uid, target["email"], role, c.tenant_id, ids,
                        target.get("display_name", ""))
    return {"ok": True}


@app.delete("/api/users/{uid}")
def remove_user(uid: str, c: Ctx = Depends(require_users)):
    target = _same_tenant_user(c, uid)
    if uid == c.user["uid"]:
        raise HTTPException(400, "ลบบัญชีตัวเองไม่ได้")
    if target["role"] == OWNER and root_store.count_owners(c.tenant_id) <= 1:
        raise HTTPException(400, "ต้องมีเจ้าของอย่างน้อย 1 คนเสมอ")
    root_store.delete_user(uid)
    return {"ok": True}


# ---- our own back office (read-only) -----------------------------------
# Counts and health of the accounts using the system. Intentionally has no
# endpoint that returns a business's own data - the promise that each
# restaurant's data is private has to hold against us too, or it isn't one.

@app.get("/api/admin/overview")
def admin_overview(admin: dict = Depends(current_admin)):
    tenants = root_store.list_tenants()
    users = root_store.list_users()
    users_by_tenant: dict[str, int] = {}
    for u in users:
        tid = u.get("tenant_id")
        if tid:
            users_by_tenant[tid] = users_by_tenant.get(tid, 0) + 1

    today = datetime.now(timezone.utc).date()
    rows = []
    active_7d = 0
    for t in tenants:
        scoped = root_store.for_tenant(t["id"])
        last_active = t.get("last_active_date", "")
        try:
            days = (today - datetime.fromisoformat(last_active).date()).days
        except Exception:
            days = None
        if days is not None and days <= 7:
            active_7d += 1
        rows.append({
            "id": t["id"],
            "name": t.get("name", ""),
            "user_count": users_by_tenant.get(t["id"], 0),
            "loyverse_connected": bool(scoped.get_setting("loyverse_token")),
            "created_at": t.get("created_at", ""),
            "last_active_date": last_active,
        })
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)

    return {
        "tenant_count": len(tenants),
        "user_count": len([u for u in users if u.get("tenant_id")]),
        "active_7d": active_7d,
        "tenants": rows,
    }


@app.get("/api/admin/whoami")
def admin_whoami(admin: dict = Depends(current_admin)):
    """Lets the frontend decide whether to show the admin link at all."""
    return {"is_admin": True, "email": admin["email"]}
