"""
Our own data lives here - materials (raw ingredients + stock), recipes
(menu item -> ingredient quantities), and expenses. None of this is
Loyverse's data; Loyverse only tells us what got sold (receipts). This
keeps us free of Loyverse's inventory/composite-item API, which had
inconsistent write schemas during testing.

Uses the Firebase emulator when USE_FIREBASE_EMULATOR=true. For a real
project, set FIREBASE_CREDENTIALS_JSON to the full service-account key
as a JSON string (works on Render, where uploading files is awkward) or
FIREBASE_CREDENTIALS_PATH to a local file path (easier for local dev).
"""

import os
import json

# Local development only. Must match VITE_FIREBASE_PROJECT_ID in the
# frontend's .env - a token minted for one project id is rejected by an
# admin SDK initialized with another, which shows up as an "aud" claim
# error at login rather than anything obviously project-related.
EMULATOR_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "pos-app-dev")


def init_firestore():
    if os.environ.get("USE_FIREBASE_EMULATOR", "false").lower() == "true":
        os.environ.setdefault("FIRESTORE_EMULATOR_HOST",
                               os.environ.get("FIRESTORE_EMULATOR_HOST", "localhost:8080"))

        # firebase_admin still has to exist even in emulator mode, because
        # verifying an ID token goes through it - Firestore access and token
        # verification are two different SDKs, and only the first one is
        # replaced by the emulator client below. The project id must match
        # what the frontend sends, or every token is rejected on its "aud".
        import firebase_admin
        if not firebase_admin._apps:
            os.environ.setdefault("FIREBASE_AUTH_EMULATOR_HOST", "127.0.0.1:9099")
            firebase_admin.initialize_app(options={"projectId": EMULATOR_PROJECT_ID})

        from google.auth.credentials import AnonymousCredentials
        from google.cloud import firestore as gcf
        return gcf.Client(project=EMULATOR_PROJECT_ID, credentials=AnonymousCredentials())

    import firebase_admin
    from firebase_admin import credentials, firestore
    if not firebase_admin._apps:
        if os.environ.get("FIREBASE_CREDENTIALS_JSON"):
            key_dict = json.loads(os.environ["FIREBASE_CREDENTIALS_JSON"])
            cred = credentials.Certificate(key_dict)
        else:
            cred = credentials.Certificate(os.environ["FIREBASE_CREDENTIALS_PATH"])
        options = {}
        bucket = os.environ.get("FIREBASE_STORAGE_BUCKET")
        if bucket:
            options["storageBucket"] = bucket
        firebase_admin.initialize_app(cred, options)
    return firestore.client()


class Store:
    """Data is scoped twice over: by TENANT (one restaurant business, which
    is what a login belongs to) and then by store_id (a branch of that
    business).

    Nothing on this class reaches outside its tenant. That's deliberate:
    isolation between businesses shouldn't depend on every endpoint
    remembering to filter, it should be impossible to express. An endpoint
    holding a Store for tenant A literally cannot address tenant B's data,
    even if it's handed B's store_id.

    A few things genuinely live above tenants - the user directory (we look
    a user up by uid BEFORE we know their tenant) and the tenant registry
    itself. Those are the methods marked "cross-tenant" below, and they're
    the only ones that touch a root collection.
    """

    def __init__(self, db=None, tenant_id: str | None = None):
        self.db = db or init_firestore()
        self.tenant_id = tenant_id

    def for_tenant(self, tenant_id: str) -> "Store":
        """A view of the same database bound to one tenant. Cheap - shares
        the connection, only the scope differs."""
        return Store(db=self.db, tenant_id=tenant_id)

    def _tenant_doc(self):
        if not self.tenant_id:
            raise RuntimeError(
                "Store ใช้งานโดยไม่ได้ระบุ tenant - ต้องเรียก for_tenant() ก่อน"
            )
        return self.db.collection("tenants").document(self.tenant_id)

    def _col(self, store_id: str, name: str):
        return self._tenant_doc().collection("stores").document(store_id).collection(name)

    def scoped_id(self, store_id: str) -> str:
        """A globally-unique id for this branch, for things stored outside
        Firestore (image paths). Branch ids are only unique within a tenant
        once we run our own POS, so the tenant has to be part of the key."""
        return f"{self.tenant_id}/{store_id}"

    # ---- per-tenant settings (Loyverse token, sync interval) ----
    # One Loyverse account belongs to one business, so these sit under the
    # tenant, never app-wide - two businesses each have their own token.
    def get_setting(self, key: str, default=None):
        doc = self._tenant_doc().collection("app_settings").document("config").get()
        return (doc.to_dict() or {}).get(key, default)

    def set_setting(self, key: str, value):
        self._tenant_doc().collection("app_settings").document("config").set(
            {key: value}, merge=True)

    # ---- the tenant record itself ----
    def get_tenant(self) -> dict | None:
        doc = self._tenant_doc().get()
        return (doc.to_dict() | {"id": doc.id}) if doc.exists else None

    def update_tenant(self, data: dict):
        self._tenant_doc().set(data, merge=True)

    def touch_tenant_activity(self, today: str):
        """Records that this business used the system today - the only
        thing the admin overview needs to tell active accounts from
        dormant ones. Written at most once a day per tenant, so it costs
        one write a day rather than one per request."""
        current = (self._tenant_doc().get().to_dict() or {}).get("last_active_date")
        if current != today:
            self._tenant_doc().set({"last_active_date": today}, merge=True)

    # ---- our own categories (independent of Loyverse - read-only there) ----
    def list_categories(self, store_id: str) -> list[dict]:
        return [d.to_dict() | {"id": d.id} for d in self._col(store_id, "categories").stream()]

    def create_category(self, store_id: str, name: str) -> dict:
        _, doc_ref = self._col(store_id, "categories").add({"name": name})
        return {"id": doc_ref.id, "name": name}

    def rename_category(self, store_id: str, category_id: str, name: str):
        self._col(store_id, "categories").document(category_id).update({"name": name})

    def delete_category(self, store_id: str, category_id: str):
        self._col(store_id, "categories").document(category_id).delete()

    # ---- assigning a Loyverse item (by name) to one of our own categories ----
    def set_item_category(self, store_id: str, item_name: str, category_id: str):
        self._col(store_id, "item_categories").document(item_name).set({"category_id": category_id})

    def get_item_categories(self, store_id: str) -> dict:
        """Returns {item_name: category_id} for every assignment made."""
        return {d.id: d.to_dict().get("category_id") for d in self._col(store_id, "item_categories").stream()}

    # ---- materials (raw ingredients) ----
    # Stock is NOT stored on the material document anymore - it's derived
    # from the movement ledger. The material doc holds only the things that
    # describe the material itself: name, unit, par level, and (for new
    # materials with no deliveries yet) a fallback cost.

    def list_materials(self, store_id: str) -> list[dict]:
        """Materials with their current stock and cost filled in from the ledger."""
        from storage.movement_ledger import MovementLedger
        ledger = MovementLedger(self)
        stock_by_id = ledger.all_current_stock(store_id)

        materials = []
        for d in self._col(store_id, "materials").stream():
            mat = d.to_dict() | {"id": d.id}
            mat["stock"] = stock_by_id.get(d.id, 0)
            ledger_cost = ledger.average_cost(store_id, d.id)
            if ledger_cost is not None:
                mat["cost"] = ledger_cost
            materials.append(mat)
        return materials

    def upsert_material(self, store_id: str, material_id: str, data: dict):
        """Stock never comes in through here - use the ledger for that, so
        every change to stock has a recorded reason."""
        data = {k: v for k, v in data.items() if k != "stock"}
        self._col(store_id, "materials").document(material_id).set(data, merge=True)

    def migrate_stock_to_ledger(self, store_id: str) -> int:
        """One-time: turn pre-V2 `stock` values sitting on material docs into
        opening-balance movements, so nothing is lost when stock moves to the
        ledger. Safe to run more than once - it skips materials already migrated."""
        from storage.movement_ledger import MovementLedger
        ledger = MovementLedger(self)
        migrated = 0

        for d in self._col(store_id, "materials").stream():
            mat = d.to_dict()
            legacy_stock = mat.get("stock")
            if legacy_stock is None:
                continue  # already migrated
            if legacy_stock != 0:
                ledger.record(store_id, d.id, "count", legacy_stock,
                              unit_cost=mat.get("cost"),
                              note="ยอดยกมาก่อนเปลี่ยนระบบ")
            # drop the old field so it can't drift out of sync with the ledger
            self._col(store_id, "materials").document(d.id).update({"stock": None})
            migrated += 1
        return migrated

    # ---- aliases (for the matching engine - step 4.2) ----
    def add_alias(self, store_id: str, material_id: str, alias: str):
        """A general alternate name for a material, from any supplier."""
        doc_ref = self._col(store_id, "materials").document(material_id)
        current = (doc_ref.get().to_dict() or {}).get("aliases", [])
        if alias not in current:
            doc_ref.update({"aliases": current + [alias]})

    def remove_alias(self, store_id: str, material_id: str, alias: str):
        doc_ref = self._col(store_id, "materials").document(material_id)
        current = (doc_ref.get().to_dict() or {}).get("aliases", [])
        doc_ref.update({"aliases": [a for a in current if a != alias]})

    def get_supplier_alias(self, store_id: str, supplier: str, normalized_name: str) -> str | None:
        """normalized_name should already be through matching_engine._normalize."""
        doc_id = _alias_key(supplier, normalized_name)
        doc = self._col(store_id, "supplier_aliases").document(doc_id).get()
        return (doc.to_dict() or {}).get("material_id") if doc.exists else None

    def set_supplier_alias(self, store_id: str, supplier: str, normalized_name: str, material_id: str):
        doc_id = _alias_key(supplier, normalized_name)
        self._col(store_id, "supplier_aliases").document(doc_id).set({
            "supplier": supplier, "raw_name": normalized_name, "material_id": material_id,
        })

    def list_supplier_aliases(self, store_id: str) -> list[dict]:
        return [d.to_dict() | {"id": d.id} for d in self._col(store_id, "supplier_aliases").stream()]

    def adjust_stock(self, store_id: str, material_id: str, new_stock: float,
                     reason: str = ""):
        """A one-off correction - a typo, or a delivery someone forgot to
        record. Deliberately NOT the same thing as a stock count.

        A correction made between counts quietly absorbs whatever discrepancy
        had built up, so the next count finds nothing wrong and the variance
        report says everything balanced. That's the most dangerous kind of
        wrong answer: confident and clean. It carries no session ref, which
        is how the report tells these apart from counted corrections and
        warns that its own figures may be understated."""
        from storage.movement_ledger import MovementLedger
        MovementLedger(self).record_count(store_id, material_id, new_stock,
                                          note=_adjust_note(new_stock, reason))

    def deduct_stock(self, store_id: str, material_id: str, amount: float,
                     ref: str | None = None):
        from storage.movement_ledger import MovementLedger
        MovementLedger(self).record_sale(store_id, material_id, amount, ref=ref)

    def receive_stock(self, store_id: str, material_id: str, quantity: float,
                      unit_cost: float, note: str = "", occurred_at: str | None = None,
                      ref: str | None = None):
        """Stock coming in from a delivery, with the price paid - this is what
        feeds average cost and cost history."""
        from storage.movement_ledger import MovementLedger
        MovementLedger(self).record_receive(store_id, material_id, quantity,
                                            unit_cost, note=note,
                                            occurred_at=occurred_at, ref=ref)

    # ---- receiving records (a delivery = one document + its movements) ----
    def add_receiving(self, store_id: str, supplier: str, date: str,
                      items: list[dict], note: str = "") -> dict:
        """items: [{material_id, quantity, unit_cost}]. Records the delivery
        document AND the stock movements for each line in one go, so stock
        and cost update together - no separate 'remember to adjust stock' step."""
        _, doc_ref = self._col(store_id, "receivings").add({
            "supplier": supplier,
            "date": date,
            "items": items,
            "note": note,
            "total": sum(i.get("quantity", 0) * i.get("unit_cost", 0) for i in items),
        })
        for item in items:
            self.receive_stock(
                store_id, item["material_id"], item["quantity"], item["unit_cost"],
                note=f"รับของจาก {supplier}", occurred_at=date, ref=doc_ref.id,
            )
        return {"id": doc_ref.id}

    def list_receivings(self, store_id: str) -> list[dict]:
        records = [d.to_dict() | {"id": d.id} for d in self._col(store_id, "receivings").stream()]
        records.sort(key=lambda r: r.get("date", ""), reverse=True)
        return records

    # ---- users, tenants, invites (cross-tenant) ----
    # These are the only methods that touch root collections. A user is
    # looked up by uid before we know which business they belong to, so
    # the directory can't itself be nested under a tenant - instead each
    # user document CARRIES its tenant_id, and that's what every scoped
    # Store is built from.

    def create_tenant(self, name: str, owner_uid: str, created_at: str) -> str:
        _, ref = self.db.collection("tenants").add({
            "name": name, "owner_uid": owner_uid, "created_at": created_at,
        })
        return ref.id

    def list_tenants(self) -> list[dict]:
        """Admin overview only."""
        return [d.to_dict() | {"id": d.id} for d in self.db.collection("tenants").stream()]

    def get_user(self, uid: str) -> dict | None:
        doc = self.db.collection("app_users").document(uid).get()
        return (doc.to_dict() | {"uid": doc.id}) if doc.exists else None

    def get_user_by_email(self, email: str) -> dict | None:
        for d in self.db.collection("app_users").where("email", "==", email.lower()).stream():
            return d.to_dict() | {"uid": d.id}
        return None

    def list_users(self, tenant_id: str | None = None) -> list[dict]:
        """Without a tenant_id this returns every user in the system, which
        is only ever wanted by the admin overview. Normal callers pass a
        tenant so one business can never enumerate another's staff."""
        col = self.db.collection("app_users")
        query = col.where("tenant_id", "==", tenant_id) if tenant_id else col
        return [d.to_dict() | {"uid": d.id} for d in query.stream()]

    def set_user(self, uid: str, email: str, role: str, tenant_id: str,
                 store_ids: list[str] | None = None, display_name: str = ""):
        self.db.collection("app_users").document(uid).set({
            "email": email.lower(), "role": role,
            "tenant_id": tenant_id,
            "store_ids": store_ids or [],
            "display_name": display_name,
        }, merge=True)

    def delete_user(self, uid: str):
        self.db.collection("app_users").document(uid).delete()

    def count_owners(self, tenant_id: str) -> int:
        """Per tenant - each business needs its own last-owner guard, and
        another business having owners is no help if yours has none."""
        return len([u for u in self.list_users(tenant_id) if u.get("role") == "owner"])

    # ---- pending invitations ----
    # Keyed by a random token rather than by email, because the invite is
    # delivered as a link the owner copies and sends however they like
    # (LINE, chat, anything). The token is the thing that proves the
    # invite is real; the email inside it is what the new account must match.

    def create_invite(self, token: str, email: str, role: str, tenant_id: str,
                      store_ids: list[str], invited_by: str, created_at: str):
        self.db.collection("app_invites").document(token).set({
            "token": token, "email": email.lower(), "role": role,
            "tenant_id": tenant_id, "store_ids": store_ids,
            "invited_by": invited_by, "created_at": created_at,
        })

    def get_invite(self, token: str) -> dict | None:
        doc = self.db.collection("app_invites").document(token).get()
        return doc.to_dict() if doc.exists else None

    def list_invites(self, tenant_id: str) -> list[dict]:
        return [d.to_dict() for d in
                self.db.collection("app_invites").where("tenant_id", "==", tenant_id).stream()]

    def delete_invite(self, token: str):
        self.db.collection("app_invites").document(token).delete()

    # ---- receiving drafts (step 4.3 - AI scan result awaiting review) ----
    def create_draft(self, store_id: str, supplier: str | None, invoice: str | None,
                     date: str | None, items: list[dict], raw_text: str = "",
                     provider: str = "", image_path: str | None = None,
                     warning: str | None = None) -> dict:
        """`warning` travels with the draft so a caveat raised during the
        scan is still in front of the user at the moment they confirm -
        which is the only moment it can change what they do."""
        _, doc_ref = self._col(store_id, "receiving_drafts").add({
            "supplier": supplier, "invoice": invoice, "date": date,
            "items": items, "raw_text": raw_text, "provider": provider,
            "image_path": image_path,
            "warning": warning,
            "status": "draft",
        })
        return {"id": doc_ref.id, "supplier": supplier, "invoice": invoice,
                "date": date, "items": items, "status": "draft",
                "image_path": image_path, "warning": warning}

    def get_draft(self, store_id: str, draft_id: str) -> dict | None:
        doc = self._col(store_id, "receiving_drafts").document(draft_id).get()
        return (doc.to_dict() | {"id": doc.id}) if doc.exists else None

    def list_drafts(self, store_id: str) -> list[dict]:
        return [d.to_dict() | {"id": d.id} for d in self._col(store_id, "receiving_drafts")
                .where("status", "==", "draft").stream()]

    def update_draft(self, store_id: str, draft_id: str, data: dict):
        self._col(store_id, "receiving_drafts").document(draft_id).update(data)

    def delete_draft(self, store_id: str, draft_id: str):
        self._col(store_id, "receiving_drafts").document(draft_id).delete()

    # ---- stock count sessions (step 3.4) ----
    # Counting a whole kitchen takes longer than one sitting, so a session
    # stays open and saves as you go. Nothing reaches the ledger until it's
    # closed - a half-finished count writing corrections would be worse
    # than no count at all, because the untouched materials would read as
    # "counted and correct".

    def create_count_session(self, store_id: str, started_at: str) -> dict:
        _, ref = self._col(store_id, "stock_counts").add({
            "started_at": started_at, "closed_at": None,
            "status": "open", "entries": {},
        })
        return {"id": ref.id, "started_at": started_at, "status": "open", "entries": {}}

    def get_count_session(self, store_id: str, session_id: str) -> dict | None:
        doc = self._col(store_id, "stock_counts").document(session_id).get()
        return (doc.to_dict() | {"id": doc.id}) if doc.exists else None

    def list_count_sessions(self, store_id: str) -> list[dict]:
        sessions = [d.to_dict() | {"id": d.id}
                    for d in self._col(store_id, "stock_counts").stream()]
        sessions.sort(key=lambda s: s.get("started_at") or "", reverse=True)
        return sessions

    def open_count_session(self, store_id: str) -> dict | None:
        for s in self.list_count_sessions(store_id):
            if s.get("status") == "open":
                return s
        return None

    def set_count_entry(self, store_id: str, session_id: str,
                        material_id: str, counted: float):
        doc_ref = self._col(store_id, "stock_counts").document(session_id)
        entries = (doc_ref.get().to_dict() or {}).get("entries", {})
        entries[material_id] = counted
        doc_ref.update({"entries": entries})

    def clear_count_entry(self, store_id: str, session_id: str, material_id: str):
        doc_ref = self._col(store_id, "stock_counts").document(session_id)
        entries = (doc_ref.get().to_dict() or {}).get("entries", {})
        entries.pop(material_id, None)
        doc_ref.update({"entries": entries})

    def close_count_session(self, store_id: str, session_id: str, closed_at: str):
        self._col(store_id, "stock_counts").document(session_id).update({
            "status": "closed", "closed_at": closed_at,
        })

    def previous_closed_session(self, store_id: str, before: str) -> dict | None:
        """The count immediately before this one - the start of the period
        being measured."""
        closed = [s for s in self.list_count_sessions(store_id)
                  if s.get("status") == "closed" and (s.get("closed_at") or "") < before]
        closed.sort(key=lambda s: s.get("closed_at") or "")
        return closed[-1] if closed else None

    # ---- AI recipe drafts (step 3.3) ----
    # A draft is a proposal, not a recipe. It holds which ingredients a
    # menu probably uses; the quantities are still blank because a person
    # has to supply them. Nothing here affects stock or cost until it's
    # saved as a real recipe, which is why drafts can sit here for days
    # without doing harm.

    def set_recipe_draft(self, store_id: str, item_name: str, kind: str,
                         ingredients: list[dict]):
        self._col(store_id, "recipe_drafts").document(item_name).set({
            "item_name": item_name, "kind": kind, "ingredients": ingredients,
        })

    def get_recipe_draft(self, store_id: str, item_name: str) -> dict | None:
        doc = self._col(store_id, "recipe_drafts").document(item_name).get()
        return doc.to_dict() if doc.exists else None

    def list_recipe_drafts(self, store_id: str) -> list[dict]:
        return [d.to_dict() for d in self._col(store_id, "recipe_drafts").stream()]

    def delete_recipe_draft(self, store_id: str, item_name: str):
        self._col(store_id, "recipe_drafts").document(item_name).delete()

    # ---- menu items deliberately excluded from recipes ----
    # Service charges and the like never consume stock. Marking them keeps
    # the "no recipe linked" warning meaningful: what's left flagged is
    # genuinely forgotten, not a corkage fee. Without this the warning
    # list fills with items that are fine, and then nobody reads it.

    def skip_recipe(self, store_id: str, item_name: str):
        self._col(store_id, "recipe_skips").document(item_name).set({"item_name": item_name})

    def unskip_recipe(self, store_id: str, item_name: str):
        self._col(store_id, "recipe_skips").document(item_name).delete()

    def list_recipe_skips(self, store_id: str) -> list[str]:
        return [d.id for d in self._col(store_id, "recipe_skips").stream()]

    # ---- recipes (menu item -> ingredient quantities) ----
    def get_recipe(self, store_id: str, item_name: str) -> list[dict]:
        doc = self._col(store_id, "recipes").document(item_name).get()
        return (doc.to_dict() or {}).get("ingredients", [])

    def set_recipe(self, store_id: str, item_name: str, ingredients: list[dict]):
        self._col(store_id, "recipes").document(item_name).set({"ingredients": ingredients})

    # ---- expenses ----
    def add_expense(self, store_id: str, category: str, name: str, amount: float, date: str):
        self._col(store_id, "expenses").add({
            "category": category, "name": name, "amount": amount, "date": date,
        })

    def list_expenses(self, store_id: str, category: str | None = None) -> list[dict]:
        col = self._col(store_id, "expenses")
        query = col.where("category", "==", category) if category else col
        return [d.to_dict() | {"id": d.id} for d in query.stream()]

    # ---- processed receipts (avoid double-deducting stock on re-sync) ----
    def is_receipt_processed(self, store_id: str, receipt_number: str) -> bool:
        return self._col(store_id, "processed_receipts").document(receipt_number).get().exists

    def mark_receipt_processed(self, store_id: str, receipt_number: str):
        self._col(store_id, "processed_receipts").document(receipt_number).set({"processed": True})


def _adjust_note(new_stock: float, reason: str) -> str:
    """Why someone changed a number matters more three months later than it
    does today, when the history is all anyone has to go on."""
    base = f"แก้ไขจำนวนเป็น {new_stock}"
    return f"{base} ({reason})" if reason else base


def _alias_key(supplier: str, normalized_name: str) -> str:
    """A stable, Firestore-doc-id-safe key for a (supplier, wording) pair."""
    import hashlib
    raw = f"{supplier.strip().lower()}|{normalized_name.lower()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:24]
