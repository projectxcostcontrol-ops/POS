"""
API for the frontend. Run with: uvicorn api.main:app --reload

Multi-tenant (V3 step 3.2): one deployment serves many restaurant
businesses. Every request is bound to exactly one tenant, taken from the
signed-in user's own record - never from a parameter - and all data access
goes through a Store already scoped to that tenant. See api/deps.py.
"""

import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import requests
from fastapi import FastAPI, HTTPException, UploadFile, File, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from adapters.loyverse_adapter import LoyverseAdapter
from adapters._loyverse_client import normalize_time
from storage.firestore_store import Store
from storage.movement_ledger import MovementLedger
from core.stock_engine import sync_branch
from core.vision_chain import build_default_chain
from core.vision_provider import VisionError
from core.matching_engine import MatchingEngine
from core.pos_registry import PosRegistry
from core.recipe_suggester import RecipeSuggester
from core import variance as variance_lib
from core import sales_report
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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# The unscoped root store. Only auth, signup, and the admin overview use it
# directly; every business endpoint works through a tenant-scoped view.
root_store = Store()
vision = build_default_chain()
suggester = RecipeSuggester()

current_claims, current_user, current_admin, _require, check_store_access = \
    make_auth_dependencies(root_store)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ---- per-request context ----------------------------------------------
# Everything an endpoint needs, already scoped to the caller's business.
# Endpoints ask for a Ctx instead of reaching for module-level state, which
# is what makes tenant isolation structural rather than a rule to remember.

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


@app.get("/api/invites/{token}")
def peek_invite(token: str, claims: dict = Depends(current_claims)):
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
    """The single-token endpoint, kept so an older frontend still works
    through a deploy. Adds a connection like any other."""
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
    c.store.upsert_material(store_id, material_id, data)
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
def list_receivings(store_id: str, c: Ctx = Depends(store_ctx)):
    return c.store.list_receivings(store_id)


@app.post("/api/{store_id}/receivings")
def add_receiving(store_id: str, data: dict, c: Ctx = Depends(store_ctx)):
    return c.store.add_receiving(
        store_id,
        supplier=data.get("supplier", ""),
        date=data.get("date", ""),
        items=data.get("items", []),
        note=data.get("note", ""),
    )


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

    result = c.store.add_receiving(
        store_id, supplier=draft.get("supplier") or "", date=draft.get("date") or "",
        items=receiving_items, note=f"จากสแกน AI (draft {draft_id})",
    )
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


@app.get("/api/{store_id}/sales/overview")
def sales_overview(store_id: str, from_: str | None = None, to: str | None = None,
                   granularity: str = "day", tz_offset: int = 0, top: int = 5,
                   c: Ctx = Depends(store_money)):
    """Everything the sales screens show, from one read of the data.

    The summary, the chart and the best-sellers used to be three
    endpoints, and the page called all three at once - so the same window
    of sales was read from Firestore twice over, plus the comparison
    window again. On a busy month that was thousands of documents fetched
    to answer one screen.

    `tz_offset` is minutes ahead of UTC (Bangkok = 420); chart buckets
    follow the shop's clock, not the server's - see _bucket_key.
    """
    start, end = _window(from_, to)
    sales = c.store.list_sales(store_id, start, end)
    materials = c.store.list_materials(store_id)
    recipes = _recipes_for(c, store_id, sales)

    current = sales_report.summarise(sales, recipes, materials, granularity, tz_offset)

    # The comparison only needs a total, so it skips the recipe lookups
    # and material costing that the current window does.
    p_start, p_end = sales_report.previous_window(start, end)
    previous = sales_report.summarise(
        c.store.list_sales(store_id, p_start, p_end), {}, [], granularity, tz_offset)

    return {
        **current,
        "from": start, "to": end, "granularity": granularity,
        "compare": sales_report.compare_previous(current, previous),
        "top_items": sales_report.top_items(sales, top),
    }


@app.get("/api/{store_id}/sales/daily")
def sales_daily(store_id: str, from_: str | None = None, to: str | None = None,
                c: Ctx = Depends(store_money)):
    start, end = _window(from_, to)
    return sales_report.daily_breakdown(c.store.list_sales(store_id, start, end))


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

    saved = c.store.list_sales(store_id, start_iso, normalize_time(now.isoformat()))
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


@app.post("/api/{store_id}/expenses")
def add_expense(store_id: str, category: str, name: str, amount: float, date: str,
                c: Ctx = Depends(store_money)):
    c.store.add_expense(store_id, category, name, amount, date)
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
    }


@app.get("/api/users")
def list_users(c: Ctx = Depends(require_users)):
    return {
        "users": root_store.list_users(c.tenant_id),
        "pending_invites": root_store.list_invites(c.tenant_id),
    }


@app.post("/api/users/invite")
def invite_user(email: str, role: str, store_ids: str = "",
                c: Ctx = Depends(require_users)):
    """Creates an invite and returns its token. The owner copies the link and
    sends it however they like - there's no email delivery to configure, and
    nothing to go wrong silently in a spam folder."""
    if role not in ROLES:
        raise HTTPException(400, f"สิทธิ์ไม่ถูกต้อง - ต้องเป็นหนึ่งใน {', '.join(ROLES)}")
    if root_store.get_user_by_email(email):
        raise HTTPException(400, "อีเมลนี้มีบัญชีอยู่แล้ว")

    ids = [s.strip() for s in store_ids.split(",") if s.strip()]
    token = secrets.token_urlsafe(16)
    root_store.create_invite(token, email, role, c.tenant_id, ids,
                             invited_by=c.user["uid"], created_at=_now())
    return {"ok": True, "token": token, "email": email.lower(), "role": role,
            "store_ids": ids}


@app.delete("/api/users/invite")
def cancel_invite(token: str, c: Ctx = Depends(require_users)):
    invite = root_store.get_invite(token)
    if invite and invite.get("tenant_id") != c.tenant_id:
        raise HTTPException(403, "คำเชิญนี้ไม่ใช่ของธุรกิจคุณ")
    root_store.delete_invite(token)
    return {"ok": True}


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
