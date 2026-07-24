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
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, UploadFile, File, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from adapters.loyverse_adapter import LoyverseAdapter
from storage.firestore_store import Store
from storage.movement_ledger import MovementLedger
from core.stock_engine import sync_and_deduct
from core.vision_chain import build_default_chain
from core.vision_provider import VisionError
from core.matching_engine import MatchingEngine
from core.recipe_suggester import RecipeSuggester
from core import variance as variance_lib
from core.unit_conversion import apply_unit_conversion
from storage.image_store import (upload_receipt_image, delete_receipt_image,
                                 download_receipt_image, storage_status)
from core.auth import can, CAPABILITIES, OWNER, ROLES
from api.deps import make_auth_dependencies

load_dotenv()

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

    @property
    def provider(self) -> LoyverseAdapter:
        p = get_provider(self.tenant_id, self.store)
        if p is None:
            raise HTTPException(400, "Loyverse ยังไม่ได้เชื่อมต่อ - ใส่ token ในหน้าตั้งค่าก่อน")
        return p

    @property
    def provider_or_none(self) -> LoyverseAdapter | None:
        return get_provider(self.tenant_id, self.store)


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

_providers: dict[str, tuple[str, LoyverseAdapter]] = {}


def get_provider(tenant_id: str, store: Store) -> LoyverseAdapter | None:
    token = store.get_setting("loyverse_token")
    if not token:
        _providers.pop(tenant_id, None)
        return None
    cached = _providers.get(tenant_id)
    if cached and cached[0] == token:
        return cached[1]
    adapter = LoyverseAdapter(token)
    _providers[tenant_id] = (token, adapter)
    return adapter


def _sync_interval(store: Store) -> int:
    return int(store.get_setting("sync_interval_seconds")
               or os.environ.get("SYNC_INTERVAL_SECONDS", "300"))


async def auto_sync_loop():
    """Syncs every branch of every business on an interval, so stock deducts
    without anyone pressing a button. A business with no token connected is
    skipped, and one business's failure never stops the others."""
    while True:
        try:
            tenants = root_store.list_tenants()
        except Exception as e:
            print(f"[auto_sync] could not list tenants: {e}")
            tenants = []

        shortest = int(os.environ.get("SYNC_INTERVAL_SECONDS", "300"))
        for tenant in tenants:
            try:
                scoped = root_store.for_tenant(tenant["id"])
                provider = get_provider(tenant["id"], scoped)
                shortest = min(shortest, _sync_interval(scoped))
                if provider is None:
                    continue
                for s in provider.get_stores():
                    sync_and_deduct(provider, scoped, s["id"])
            except Exception as e:
                print(f"[auto_sync] tenant {tenant.get('id')} error: {e}")
        await asyncio.sleep(max(30, shortest))


@app.on_event("startup")
async def startup():
    asyncio.create_task(auto_sync_loop())


# ---- signup ------------------------------------------------------------
# Two doors in, and no others. Either you start a business (and own it), or
# someone who already owns one invited you.

@app.post("/api/signup/business")
def signup_business(data: dict, claims: dict = Depends(current_claims)):
    """data: {business_name, display_name}"""
    existing = root_store.get_user(claims["uid"])
    if existing:
        raise HTTPException(400, "บัญชีนี้อยู่ในธุรกิจอื่นอยู่แล้ว")

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
        "connected": c.provider_or_none is not None,
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


@app.post("/api/settings/token")
def set_token(token: str, c: Ctx = Depends(require_settings)):
    """Save the Loyverse token and try connecting with it immediately."""
    try:
        LoyverseAdapter(token).get_stores()
    except Exception as e:
        raise HTTPException(400, f"เชื่อมต่อไม่สำเร็จ - เช็ค token: {e}")
    c.store.set_setting("loyverse_token", token)
    _providers.pop(c.tenant_id, None)
    return {"connected": True}


@app.post("/api/settings/disconnect")
def disconnect(c: Ctx = Depends(require_settings)):
    c.store.set_setting("loyverse_token", None)
    _providers.pop(c.tenant_id, None)
    return {"connected": False}


@app.post("/api/settings/sync-interval")
def set_sync_interval(seconds: int, c: Ctx = Depends(require_settings)):
    c.store.set_setting("sync_interval_seconds", seconds)
    return {"sync_interval_seconds": seconds}


# ---- stores / items / categories ---------------------------------------

@app.get("/api/stores")
def list_stores(c: Ctx = Depends(ctx)):
    stores = c.provider.get_stores()
    if can(c.user["role"], "all_stores"):
        return stores
    allowed = set(c.user.get("store_ids") or [])
    return [s for s in stores if s["id"] in allowed]


@app.get("/api/{store_id}/items")
def list_items(store_id: str, c: Ctx = Depends(store_ctx)):
    """Items come read-only from Loyverse; category assignment is ours."""
    items = c.provider.get_items()
    assignments = c.store.get_item_categories(store_id)
    for item in items:
        item["category_id"] = assignments.get(item["name"])
    return items


@app.get("/api/{store_id}/loyverse-categories")
def list_loyverse_categories(store_id: str, c: Ctx = Depends(store_ctx)):
    return c.provider.get_categories()


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


@app.post("/api/{store_id}/recipes/suggest-all")
def suggest_all_recipes(store_id: str, data: dict, c: Ctx = Depends(store_ctx)):
    """data: {item_names: [...]}. Drafts many at once and SAVES them, because
    a batch is worked through over time rather than in one sitting. Saved
    drafts change nothing on their own - each still has to be opened,
    given quantities, and confirmed."""
    names = [n for n in (data.get("item_names") or []) if n]
    if not names:
        raise HTTPException(400, "ไม่มีเมนูให้ร่าง")
    if len(names) > 60:
        # One request per menu costs tokens and time; past this it's better
        # to do it in rounds than to sit on a request that may time out.
        raise HTTPException(400, "ร่างได้ครั้งละไม่เกิน 60 เมนู - แบ่งเป็นหลายรอบ")

    try:
        suggestions = _suggest_for(c, store_id, names)
    except VisionError as e:
        raise HTTPException(502, str(e))

    for entry in suggestions:
        c.store.set_recipe_draft(store_id, entry["menu"], entry["kind"], entry["ingredients"])
    return {"drafted": len(suggestions), "menus": suggestions}


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


# ---- stock counts and variance (step 3.4) ------------------------------

DEFAULT_VARIANCE_PCT = 10.0
DEFAULT_VARIANCE_VALUE = 200.0


def _thresholds(c: Ctx) -> tuple[float, float]:
    return (float(c.store.get_setting("variance_threshold_pct") or DEFAULT_VARIANCE_PCT),
            float(c.store.get_setting("variance_threshold_value") or DEFAULT_VARIANCE_VALUE))


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
        c.ledger.record_count(store_id, material_id, float(counted),
                              note=f"รอบนับ {closed_at[:10]}")
        # Tag it so variance can tell this count's correction from any
        # other adjustment made on the same day.
        movements = c.ledger.list_movements(store_id, material_id)
        if movements:
            latest = movements[0]
            c.store._col(store_id, "stock_movements").document(latest["id"]).update({
                "ref": session_id, "occurred_at": closed_at,
            })

    c.store.close_count_session(store_id, session_id, closed_at)
    return {"ok": True, "counted": len(entries), "closed_at": closed_at}


@app.get("/api/{store_id}/variance/{session_id}")
def variance_report(store_id: str, session_id: str, c: Ctx = Depends(store_money)):
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
        receipts = c.provider.get_receipts(
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
    return c.provider.get_receipts(store_id, created_at_min=created_at_min)


@app.post("/api/{store_id}/sync")
def sync(store_id: str, c: Ctx = Depends(store_ctx)):
    count = sync_and_deduct(c.provider, c.store, store_id)
    return {"processed_receipts": count}


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
