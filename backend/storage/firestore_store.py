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
import hashlib
import re

# Local development only. Must match VITE_FIREBASE_PROJECT_ID in the
# frontend's .env - a token minted for one project id is rejected by an
# admin SDK initialized with another, which shows up as an "aud" claim
# error at login rather than anything obviously project-related.
EMULATOR_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "pos-app-dev")


# ---- document ids made from user-typed text -----------------------------
# Firestore will not accept just any string as a document id: no "/", not
# "." or "..", nothing wrapped in double underscores, and 1,500 bytes at
# most. Menu names are typed by a shop owner, and "ชา/กาแฟ" is a perfectly
# ordinary thing to call a menu item - it just cannot be an id. Using the
# name directly meant that saving a recipe for it failed outright, with an
# error from deep inside the SDK that says nothing about menu names.

_ID_MAX_BYTES = 1000          # well under Firestore's 1,500, room for growth
_RESERVED = re.compile(r"^__.*__$")


def is_safe_doc_id(name: str) -> bool:
    if not name or "/" in name:
        return False
    if name in (".", ".."):
        return False
    if _RESERVED.match(name):
        return False
    return len(name.encode("utf-8")) <= _ID_MAX_BYTES


def doc_key(name: str) -> str:
    """A usable id for a document keyed by a name.

    A name Firestore already accepts is kept as its own id. That is
    deliberate and worth the branch: every recipe, category and skip
    written before this existed is stored under its raw name, so keeping
    them means no migration and nothing to go wrong during one - and it
    keeps the console readable, where "ผัดไท" says what it is and a hash
    says nothing.

    Anything Firestore would reject is hashed instead. Those documents
    could never have been written before, so there is no old data in that
    shape to worry about. The real name is stored INSIDE the document
    either way, and that - not the id - is what reads return.
    """
    if is_safe_doc_id(name):
        return name
    return "~" + hashlib.sha1((name or "").encode("utf-8")).hexdigest()[:32]


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

    # ---- public dynamic QR links --------------------------------------
    # Public slugs exist before a request belongs to a tenant, so only the
    # root Store and super-admin API may manage this application-wide data.
    def _qr_links_col(self):
        return self.db.collection("public_qr_links")

    def get_qr_link(self, slug: str) -> dict | None:
        doc = self._qr_links_col().document(slug).get()
        return (doc.to_dict() | {"slug": doc.id}) if doc.exists else None

    def set_qr_link(self, slug: str, data: dict):
        self._qr_links_col().document(slug).set(data, merge=True)
        return self.get_qr_link(slug)

    def record_qr_scan(self, slug: str, spot: str, day: str, scanned_at: str):
        link_ref = self._qr_links_col().document(slug)
        link_ref.set({
            "total_scans": self.increment(1),
            "last_scanned_at": scanned_at,
        }, merge=True)

        daily_ref = link_ref.collection("daily").document(day)
        daily_ref.set({
            "date": day,
            "total": self.increment(1),
            "last_scanned_at": scanned_at,
        }, merge=True)
        daily_ref.collection("spots").document(spot).set({
            "spot": spot,
            "total": self.increment(1),
            "last_scanned_at": scanned_at,
        }, merge=True)

    def get_qr_stats(self, slug: str) -> dict | None:
        link = self.get_qr_link(slug)
        if not link:
            return None
        daily = []
        link_ref = self._qr_links_col().document(slug)
        for day_doc in link_ref.collection("daily").stream():
            row = day_doc.to_dict() or {}
            spots_ref = link_ref.collection("daily").document(day_doc.id).collection("spots")
            row["spots"] = [d.to_dict() for d in spots_ref.stream()]
            daily.append(row)
        daily.sort(key=lambda row: row.get("date", ""), reverse=True)
        return {"link": link, "daily": daily}

    # ---- Loyverse connections -----------------------------------------
    # One access token is one Loyverse ACCOUNT, and an account is not the
    # same thing as a business. Shops that grew branch by branch often
    # opened a separate Loyverse account for each one, so a single
    # business can hold several tokens - and the earlier design, which
    # kept one token per tenant, simply had no way to express that. The
    # owner picked one branch and the others were unreachable.
    #
    # Branches stay completely separate: every branch already stores its
    # data under stores/{store_id}, and a store id from one Loyverse
    # account never collides with one from another. Nothing is merged
    # across accounts, and nothing needs to be.

    def _connections_col(self):
        return self._tenant_doc().collection("loyverse_connections")

    def list_connections(self) -> list[dict]:
        return [d.to_dict() | {"id": d.id} for d in self._connections_col().stream()]

    def get_connection(self, conn_id: str) -> dict | None:
        doc = self._connections_col().document(conn_id).get()
        return (doc.to_dict() | {"id": doc.id}) if doc.exists else None

    def add_connection(self, token: str, label: str, created_at: str) -> dict:
        _, ref = self._connections_col().add({
            "token": token, "label": label, "created_at": created_at,
            "last_error": None,
        })
        return {"id": ref.id, "label": label, "created_at": created_at}

    def update_connection(self, conn_id: str, data: dict):
        self._connections_col().document(conn_id).set(data, merge=True)

    def delete_connection(self, conn_id: str):
        """Removes the connection only.

        Everything synced from it - sales, stock movements, recipes,
        counts - stays exactly where it is, under its branch. Removing a
        token means "stop talking to this Loyverse account", not "throw
        away the history it produced", and those two would be very
        different buttons to press by accident.
        """
        self._connections_col().document(conn_id).delete()
        index = {k: v for k, v in self.get_store_index().items() if v != conn_id}
        self._tenant_doc().collection("app_settings").document("store_index").set(
            index)

    # Which connection a branch came from. Kept as one small document so
    # answering "whose token do I use for this branch" is a single read,
    # not a walk through every account asking each one if it owns it.
    def get_store_index(self) -> dict:
        doc = self._tenant_doc().collection("app_settings").document("store_index").get()
        return (doc.to_dict() or {}) if doc.exists else {}

    def set_store_index(self, mapping: dict):
        self._tenant_doc().collection("app_settings").document("store_index").set(
            mapping, merge=True)

    def migrate_legacy_token(self, created_at: str) -> bool:
        """Turns the old single `loyverse_token` setting into connection #1.

        Runs itself, once, the first time a business with the old shape
        is used - nobody has to reconnect anything or know this happened.
        The old setting is left in place rather than deleted: it costs
        nothing, and if this release has to be rolled back the previous
        version finds what it expects.
        """
        if self.list_connections():
            return False
        token = self.get_setting("loyverse_token")
        if not token:
            return False
        self.add_connection(token, "บัญชีหลัก", created_at)
        return True

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
        self._col(store_id, "item_categories").document(doc_key(item_name)).set(
            {"category_id": category_id, "item_name": item_name})

    def get_item_categories(self, store_id: str) -> dict:
        """Returns {item_name: category_id} for every assignment made."""
        out = {}
        for d in self._col(store_id, "item_categories").stream():
            data = d.to_dict() or {}
            out[data.get("item_name") or d.id] = data.get("category_id")
        return out

    # ---- materials (raw ingredients) ----
    # Stock is NOT stored on the material document anymore - it's derived
    # from the movement ledger. The material doc holds only the things that
    # describe the material itself: name, unit, par level, and (for new
    # materials with no deliveries yet) a fallback cost.

    # ---- derived stock snapshot -------------------------------------
    # Kept on the material doc, maintained by MovementLedger inside the
    # same batch as the movement it comes from. See that module for why.

    SNAPSHOT_FIELDS = ("stock_qty", "recv_qty", "recv_value")

    def increment(self, value):
        """An atomic add for a numeric field.

        Asked of the database rather than imported from the SDK, so the
        in-memory test double can supply its own and the tests exercise
        the same write path production takes instead of a simpler one."""
        factory = getattr(self.db, "increment", None)
        if factory is not None:
            return factory(value)
        from google.cloud import firestore
        return firestore.Increment(value)

    def _field_value(self, name: str, *args):
        """Firestore's server-side field operations, asked of the database
        rather than imported from the SDK - same reason as increment():
        the in-memory test double supplies its own, so tests exercise the
        write path production takes instead of a simpler one."""
        factory = getattr(self.db, name, None)
        if factory is not None:
            return factory(*args)
        from google.cloud import firestore
        if name == "array_union":
            return firestore.ArrayUnion(*args)
        if name == "array_remove":
            return firestore.ArrayRemove(*args)
        if name == "delete_field":
            return firestore.DELETE_FIELD
        raise KeyError(name)

    def field_path(self, *segments):
        """A path to one field inside a document.

        Built through the SDK rather than by joining with dots, because a
        segment is arbitrary user-derived text: an id containing a dot or
        a space would otherwise be read as two field names and write to
        the wrong place."""
        factory = getattr(self.db, "field_path", None)
        if factory is not None:
            return factory(*segments)
        from google.cloud.firestore_v1.field_path import FieldPath
        return FieldPath(*segments)

    def material_ref(self, store_id: str, material_id: str):
        return self._col(store_id, "materials").document(material_id)

    def material_snapshot(self, store_id: str, material_id: str) -> dict | None:
        doc = self.material_ref(store_id, material_id).get()
        return doc.to_dict() if doc.exists else None

    def set_material_snapshot(self, store_id: str, material_id: str, totals: dict):
        self.material_ref(store_id, material_id).set(
            {k: totals.get(k, 0) for k in self.SNAPSHOT_FIELDS}, merge=True)

    def list_material_ids(self, store_id: str) -> list[str]:
        return [d.id for d in self._col(store_id, "materials").stream()]

    def list_materials(self, store_id: str) -> list[dict]:
        """Materials with their current stock and cost filled in.

        Both numbers come off the snapshot on each material doc, so this
        is one read per material and nothing else. It used to sum the
        whole movement ledger for the stock figure and then run a second
        query PER MATERIAL for the average cost - which on a branch a
        year in meant six figures of document reads to draw a list of
        two dozen ingredients, on the screen staff open most often.

        A branch whose materials predate the snapshot has no totals to
        read, so it falls back to the ledger: slower, but never wrong.
        Rebuilding (Settings, or the rebuild endpoint) moves it onto the
        fast path permanently.
        """
        docs = [(d.id, d.to_dict() or {}) for d in self._col(store_id, "materials").stream()]
        needs_rebuild = any("stock_qty" not in data for _, data in docs)

        if needs_rebuild:
            return self._list_materials_from_ledger(docs, store_id)

        materials = []
        for material_id, data in docs:
            mat = data | {"id": material_id}
            mat["stock"] = data.get("stock_qty") or 0
            recv_qty = data.get("recv_qty") or 0
            if recv_qty > 0:
                mat["cost"] = (data.get("recv_value") or 0) / recv_qty
            mat["snapshot"] = True
            materials.append(mat)
        return materials

    def _list_materials_from_ledger(self, docs: list[tuple[str, dict]],
                                    store_id: str) -> list[dict]:
        """The pre-snapshot path, kept as the fallback rather than deleted.

        A branch that hasn't been rebuilt yet must still show correct
        numbers - being slow is a cost, being wrong about how much stock
        is on the shelf is a different kind of problem entirely."""
        from storage.movement_ledger import MovementLedger
        ledger = MovementLedger(self)
        stock_by_id = ledger.all_current_stock(store_id)

        materials = []
        for material_id, data in docs:
            mat = data | {"id": material_id}
            mat["stock"] = stock_by_id.get(material_id, 0)
            ledger_cost = ledger.average_cost(store_id, material_id)
            if ledger_cost is not None:
                mat["cost"] = ledger_cost
            mat["snapshot"] = False
            materials.append(mat)
        return materials

    def upsert_material(self, store_id: str, material_id: str, data: dict):
        """Stock never comes in through here - use the ledger for that, so
        every change to stock has a recorded reason. The derived totals
        are refused for the same reason, and more bluntly: they are the
        ledger's arithmetic, and letting a request set them would make
        them a second, editable copy of the truth."""
        if not is_safe_doc_id(material_id):
            # The frontend builds these; a slug made from a name like
            # "น้ำปลา/ซีอิ๊ว" contains a slash. Refusing here with a
            # readable message beats an SDK error that mentions neither
            # the material nor the name it came from.
            raise ValueError(
                f"รหัสวัตถุดิบใช้ไม่ได้: {material_id!r} - ห้ามมี / และต้องไม่ยาวเกินไป")

        blocked = {"stock", *self.SNAPSHOT_FIELDS}
        data = {k: v for k, v in data.items() if k not in blocked}

        # Increment-by-zero creates the field at 0 on a brand new material
        # and leaves an existing one untouched - so a new ingredient joins
        # the fast path immediately, and editing a name never resets what
        # is on the shelf.
        for field in self.SNAPSHOT_FIELDS:
            data.setdefault(field, self.increment(0))

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
        """A general alternate name for a material, from any supplier.

        ArrayUnion, not read-append-write. Confirming a scanned delivery
        learns an alias for every matched line at once, and the old
        version read the list, added one name and wrote the whole list
        back - so two lines finishing at the same moment each saved a
        list that did not contain the other's name, and one of the two
        was simply lost. Silently: nothing failed, the alias just wasn't
        there next time."""
        self._col(store_id, "materials").document(material_id).update(
            {"aliases": self._field_value("array_union", [alias])})

    def remove_alias(self, store_id: str, material_id: str, alias: str):
        self._col(store_id, "materials").document(material_id).update(
            {"aliases": self._field_value("array_remove", [alias])})

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

    def deduct_stock_bulk(self, store_id: str, rows: list[dict]) -> int:
        """rows: [{material_id, quantity, ref}]. What a sync uses - see
        MovementLedger.record_sales_bulk."""
        if not rows:
            return 0
        from storage.movement_ledger import MovementLedger
        return MovementLedger(self).record_sales_bulk(store_id, rows)

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

    def get_receiving(self, store_id: str, receiving_id: str) -> dict | None:
        doc = self._col(store_id, "receivings").document(receiving_id).get()
        return (doc.to_dict() | {"id": doc.id}) if doc.exists else None

    def replace_receiving(self, store_id: str, receiving_id: str, data: dict):
        """Overwrites the delivery document itself. The stock movements
        behind it are the caller's job - they are not fields on this
        document, they are entries in the ledger, and correcting one
        means taking the old entries out and putting new ones in."""
        self._col(store_id, "receivings").document(receiving_id).set(data)

    def delete_receiving(self, store_id: str, receiving_id: str):
        self._col(store_id, "receivings").document(receiving_id).delete()

    def add_receiving_movements(self, store_id: str, receiving_id: str,
                                supplier: str, date: str, items: list[dict]):
        for item in items:
            self.receive_stock(
                store_id, item["material_id"], item["quantity"], item["unit_cost"],
                note=f"รับของจาก {supplier}", occurred_at=date, ref=receiving_id,
            )

    def list_receivings(self, store_id: str, start: str | None = None,
                        end: str | None = None) -> list[dict]:
        """Deliveries, newest first, optionally within a date range.

        The range is pushed down to Firestore. A screen that only wants
        one month should not pay for every delivery the shop has ever
        taken - that cost grows forever while the question stays the same
        size, which is the same trap list_sales used to have.

        Bounds are YYYY-MM-DD, matching what normalize_date stores.
        """
        query = self._col(store_id, "receivings")
        if start:
            query = query.where("date", ">=", start)
        if end:
            query = query.where("date", "<=", end)
        records = [d.to_dict() | {"id": d.id} for d in query.stream()]
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

    # ---- recipes (menu item -> ingredient quantities) ----
    def get_recipe(self, store_id: str, item_name: str) -> list[dict]:
        doc = self._col(store_id, "recipes").document(doc_key(item_name)).get()
        return (doc.to_dict() or {}).get("ingredients", [])

    def all_recipes(self, store_id: str) -> dict[str, list[dict]]:
        """Every recipe in one read, keyed by menu name.

        A sales report needs the recipe behind each menu that sold, and
        fetching them one at a time is a read per distinct dish - dozens
        of round trips to answer one screen. A restaurant's whole recipe
        book is small enough to fetch at once and far cheaper that way."""
        # Keyed by the stored name, not the document id - for a menu whose
        # name Firestore can't use as an id those are different strings,
        # and the caller is looking up by what sold. Documents written
        # before the name was stored fall back to the id, which for them
        # is the name.
        out = {}
        for d in self._col(store_id, "recipes").stream():
            data = d.to_dict() or {}
            out[data.get("item_name") or d.id] = data.get("ingredients", [])
        return out

    def set_recipe(self, store_id: str, item_name: str, ingredients: list[dict]):
        self._col(store_id, "recipes").document(doc_key(item_name)).set(
            {"ingredients": ingredients, "item_name": item_name})

    # ---- expenses ----
    # Typed in by a person, which means typed in wrong sometimes - a digit
    # dropped, the wrong month, the same bill entered twice. Every one of
    # those lands straight in the profit figure, so being able to correct
    # them is not a nicety.

    EXPENSE_FIELDS = ("category", "name", "amount", "date")

    def add_expense(self, store_id: str, category: str, name: str, amount: float,
                    date: str) -> dict:
        _, ref = self._col(store_id, "expenses").add({
            "category": category, "name": name, "amount": amount, "date": date,
        })
        return {"id": ref.id}

    def get_expense(self, store_id: str, expense_id: str) -> dict | None:
        doc = self._col(store_id, "expenses").document(expense_id).get()
        return (doc.to_dict() | {"id": doc.id}) if doc.exists else None

    def update_expense(self, store_id: str, expense_id: str, data: dict):
        """Only the four fields that make up an expense can be written.

        Filtered rather than trusted: this takes a request body, and a
        write that passes the whole body through is how a field nobody
        meant to expose ends up editable."""
        clean = {k: v for k, v in data.items() if k in self.EXPENSE_FIELDS}
        self._col(store_id, "expenses").document(expense_id).set(clean, merge=True)

    def delete_expense(self, store_id: str, expense_id: str):
        self._col(store_id, "expenses").document(expense_id).delete()

    def list_expenses(self, store_id: str, category: str | None = None,
                      start: str | None = None,
                      end: str | None = None) -> list[dict]:
        """Expenses, optionally by category and within a date range.

        The range is pushed down to Firestore for the same reason
        list_receivings and list_sales push theirs: a question about one
        month should not cost every expense the shop has ever recorded.
        Reading them all and filtering here meant the assistant grew more
        expensive every month the shop stayed open while answering the
        same-sized question.

        Bounds are YYYY-MM-DD. The upper one is widened by one character
        so a row whose date was saved as a full timestamp - nothing
        normalises this field on the way in - still falls inside the last
        day of the range instead of dropping out of every month it
        belongs to. That exact bug cost the receiving figures a month of
        purchases before (see NOTES 7.9).

        A row with no date at all is left out, which is what the caller's
        own filtering already did: an expense with no date cannot be put
        in a period, and guessing one would be worse than omitting it.
        """
        query = self._col(store_id, "expenses")
        if category:
            query = query.where("category", "==", category)
        if start:
            query = query.where("date", ">=", start)
        if end:
            query = query.where("date", "<=", end + "\uf8ff")
        return [d.to_dict() | {"id": d.id} for d in query.stream()]

    # ---- processed receipts (avoid double-deducting stock on re-sync) ----
    def is_receipt_processed(self, store_id: str, receipt_number: str) -> bool:
        return self._col(store_id, "processed_receipts").document(receipt_number).get().exists

    def mark_receipt_processed(self, store_id: str, receipt_number: str):
        self._col(store_id, "processed_receipts").document(receipt_number).set({"processed": True})

    # ---- sync cursor (avoid re-pulling a branch's entire sales history) ----
    # Without this, every sync - manual or automatic - asks Loyverse for
    # every receipt since the branch's first day on Loyverse, then checks
    # each one against processed_receipts. That's fine for a test store with
    # a handful of receipts; for a real branch with months of history it's
    # thousands of API calls and Firestore reads on every single sync, which
    # is what "syncing... stuck" looks like from the outside - it isn't
    # frozen, it's working through a backlog nothing needed it to fetch.
    #
    # The cursor also encodes a deliberate business decision, not just a
    # performance one: a receipt from before this branch had recipes or
    # tracked stock has nothing to deduct against, so there was never a
    # reason to fetch it. A newly connected branch starts counting from the
    # moment it connects, not from its entire Loyverse history.
    def get_sync_cursor(self, store_id: str) -> str | None:
        doc = self._col(store_id, "sync_state").document("cursor").get()
        return (doc.to_dict() or {}).get("synced_up_to") if doc.exists else None

    def set_sync_cursor(self, store_id: str, synced_up_to: str):
        self._col(store_id, "sync_state").document("cursor").set({"synced_up_to": synced_up_to})

    # ---- saved sales (our own copy of what sold) ----
    # Loyverse's free plan refuses receipts older than 31 days, so a
    # business that relies on reading them back loses its own history a
    # month at a time. Keeping a copy as each receipt is synced turns that
    # into a limit on how far back a NEW connection can see, rather than a
    # ceiling that never moves: after a month of use there's a month of
    # history here, after a year there's a year.
    #
    # Keyed by receipt number so re-syncing the same receipt overwrites
    # rather than double-counting - the overlap window in sync_branch
    # deliberately re-fetches a few minutes of receipts every time.

    def save_sale(self, store_id: str, receipt_number: str, data: dict):
        self._col(store_id, "sales").document(receipt_number).set(data)

    def get_sale(self, store_id: str, receipt_number: str) -> dict | None:
        doc = self._col(store_id, "sales").document(receipt_number).get()
        return (doc.to_dict() | {"receipt_number": doc.id}) if doc.exists else None

    def delete_sale(self, store_id: str, receipt_number: str):
        self._col(store_id, "sales").document(receipt_number).delete()

    def save_sales_bulk(self, store_id: str, rows: list[tuple[str, dict]]):
        """Write many sales in batched round trips.

        One write per receipt is fine for a five-minute sync of a dozen
        bills and hopeless for a first sync of several thousand - the
        request simply times out, which is what "ดึงซ้ำแล้วค้าง" was.
        Firestore takes 500 writes per batch, so this turns thousands of
        round trips into a handful."""
        col = self._col(store_id, "sales")
        CHUNK = 400   # under Firestore's 500 limit, with headroom
        for i in range(0, len(rows), CHUNK):
            batch = self.db.batch()
            for number, data in rows[i:i + CHUNK]:
                batch.set(col.document(number), data)
            batch.commit()

    def mark_receipts_processed_bulk(self, store_id: str, numbers: list[str]):
        """Same reasoning as save_sales_bulk - one write each is what made
        a large sync unusable."""
        col = self._col(store_id, "processed_receipts")
        CHUNK = 400
        for i in range(0, len(numbers), CHUNK):
            batch = self.db.batch()
            for number in numbers[i:i + CHUNK]:
                batch.set(col.document(number), {"processed": True})
            batch.commit()

    def processed_receipts_among(self, store_id: str,
                                 numbers: list[str]) -> set[str]:
        """Which of THESE receipt numbers have already been processed.

        Deliberately not "all of them". The previous version read the
        entire processed_receipts collection on every sync, which is a
        collection that only ever grows: it holds one document per bill
        the branch has ever rung up, forever. A shop doing 100 bills a
        day has 36,000 of them after a year, and the automatic sync runs
        every five minutes - so answering "have I seen these 25 bills"
        cost ten million document reads a day, against a free quota of
        fifty thousand. The answer was correct and the bill was ruinous,
        which is the kind of bug no test about the ANSWER can catch (see
        tests/test_sync_cost.py, which asserts the cost instead).

        A sync only ever asks about the receipts it just fetched, so
        that's what this reads: one batched get of exactly those ids.
        Cost now follows how busy the last few hours were, not how long
        the shop has been open.
        """
        col = self._col(store_id, "processed_receipts")
        wanted = [n for n in numbers if n]
        found: set[str] = set()
        # get_all takes a list of refs in one round trip. Chunked because
        # a single request still has a size ceiling, and a full repair can
        # ask about thousands at once.
        CHUNK = 300
        for i in range(0, len(wanted), CHUNK):
            refs = [col.document(n) for n in wanted[i:i + CHUNK]]
            for snap in self.db.get_all(refs):
                if snap.exists:
                    found.add(snap.id)
        return found

    def list_sales(self, store_id: str, start: str | None = None,
                   end: str | None = None) -> list[dict]:
        """Sales in a date window, oldest first.

        The range is pushed down to Firestore rather than filtered here.
        Reading the whole collection and discarding most of it meant
        looking at today cost more every week the shop stayed open -
        a page that got slower forever while doing the same work.

        Dates are stored in one canonical format (see normalize_time), so
        an ordered string comparison is a correct date comparison, which
        is what lets this be a simple indexed range query.
        """
        query = self._col(store_id, "sales")
        if start:
            query = query.where("date", ">=", start)
        if end:
            query = query.where("date", "<=", end)
        # Ordered by Firestore too - the range query already walks the
        # index in this order, so sorting again in Python would be work
        # for nothing.
        query = query.order_by("date")
        return [d.to_dict() or {} for d in query.stream()]

    def has_backfilled_sales(self, store_id: str) -> bool:
        doc = self._col(store_id, "sync_state").document("backfill").get()
        return bool((doc.to_dict() or {}).get("done")) if doc.exists else False

    def mark_sales_backfilled(self, store_id: str, at: str):
        self._col(store_id, "sync_state").document("backfill").set(
            {"done": True, "at": at})

    # ---- daily rollups (one document per trading day) ------------------
    # See core/daily_rollup.py for what a row holds and, more importantly,
    # what it deliberately does not. Here it is only storage: the day is
    # the document id, so a day can be thrown away by name when something
    # that fed it changes.

    def _daily_col(self, store_id: str):
        return self._col(store_id, "sales_daily")

    def list_daily(self, store_id: str, start_day: str, end_day: str) -> list[dict]:
        """Stored days in a range, oldest first.

        A range query, so days that were never built cost nothing to ask
        about - which is what makes it safe to ask for a whole month and
        let the caller fill in whatever came back missing.
        """
        query = (self._daily_col(store_id)
                 .where("date", ">=", start_day)
                 .where("date", "<=", end_day)
                 .order_by("date"))
        return [d.to_dict() or {} for d in query.stream()]

    def set_daily_many(self, store_id: str, rows: list[dict]):
        """Write built days in one batch - a month is one round trip."""
        col = self._daily_col(store_id)
        BATCH = 400
        for i in range(0, len(rows), BATCH):
            batch = self.db.batch()
            for row in rows[i:i + BATCH]:
                batch.set(col.document(row["date"]), row)
            batch.commit()

    def delete_daily(self, store_id: str, days):
        """Throw days away so they are rebuilt from the bills next time.

        Deleting rather than rewriting is the point: whatever changed -
        a late bill, a cancelled delivery order, a repair - the honest
        answer is "recount that day", and a row that is absent cannot be
        subtly wrong the way a row patched by hand can.
        """
        if isinstance(days, str):
            days = [days]
        col = self._daily_col(store_id)
        briefs = self._col(store_id, "daily_briefs")
        for day in days:
            if day:
                col.document(day).delete()
                # The morning brief is written from the day it summarises,
                # so a day worth rebuilding is a brief worth rebuilding.
                # Kept here rather than left to each caller: there is one
                # place a day gets thrown away, and everything derived
                # from that day should go with it.
                briefs.document(day).delete()

    # ---- what the assistant has been asked today -----------------------
    # Per business, per day, because that is what a bill arrives for. It
    # is a ceiling rather than a meter: nobody is charged, and the number
    # exists so a runaway loop or a bored teenager cannot spend the
    # shop's quota in an afternoon.

    def assistant_asks_today(self, day: str) -> int:
        doc = self._tenant_doc().collection("assistant_usage").document(day).get()
        return int((doc.to_dict() or {}).get("asks") or 0) if doc.exists else 0

    def record_assistant_ask(self, day: str):
        self._tenant_doc().collection("assistant_usage").document(day).set(
            {"asks": self.increment(1), "date": day}, merge=True)

    # ---- owner-controlled follow-up to assistant recommendations ------
    # These records do not grant the assistant a write capability. They are
    # created and changed only after an authenticated person clicks.

    def add_advice_tracking(self, store_id: str, record: dict) -> dict:
        _, ref = self._col(store_id, "advice_tracking").add(record)
        return {**record, "id": ref.id}

    def get_advice_tracking(self, store_id: str, tracking_id: str) -> dict | None:
        doc = self._col(store_id, "advice_tracking").document(tracking_id).get()
        return (doc.to_dict() | {"id": doc.id}) if doc.exists else None

    def list_advice_tracking(self, store_id: str) -> list[dict]:
        rows = [doc.to_dict() | {"id": doc.id}
                for doc in self._col(store_id, "advice_tracking").stream()]
        return sorted(rows, key=lambda row: row.get("created_at") or "", reverse=True)

    def update_advice_tracking(self, store_id: str, tracking_id: str, data: dict):
        allowed = {"status", "note", "updated_at", "evaluation", "evaluated_at"}
        clean = {key: value for key, value in data.items() if key in allowed}
        self._col(store_id, "advice_tracking").document(tracking_id).set(
            clean, merge=True)

    def get_brief(self, store_id: str, day: str) -> dict | None:
        doc = self._col(store_id, "daily_briefs").document(day).get()
        return doc.to_dict() if doc.exists else None

    def set_brief(self, store_id: str, day: str, brief: dict):
        self._col(store_id, "daily_briefs").document(day).set(brief)

    # ---- where the shop is ---------------------------------------------
    # Until now the browser sent its own offset with every request, which
    # works for a screen someone is looking at and not at all for anything
    # the server does alone - a nightly rollup or a morning summary has no
    # browser to ask. So the shop's offset is stored once, the first time
    # a browser is around to tell us, and used from then on.

    def get_timezone(self, default: int = 420) -> int:
        value = self.get_setting("timezone_offset")
        return default if value is None else int(value)

    def set_timezone(self, offset_minutes: int, only_if_unset: bool = True) -> int:
        """Records the shop's offset. By default the first answer wins.

        A browser in a different country - the owner travelling, a
        developer looking at a customer's account - would otherwise
        silently redraw every day boundary in the shop's history.
        """
        current = self.get_setting("timezone_offset")
        if current is not None and only_if_unset:
            return int(current)
        offset = int(offset_minutes)
        if not -840 <= offset <= 840:
            raise ValueError("timezone_offset อยู่นอกช่วงที่เป็นไปได้")
        self.set_setting("timezone_offset", offset)
        return offset

    # ---- AI recipe drafts (step 3.3) ----
    # A draft is a proposal, not a recipe. It holds which ingredients a
    # menu probably uses; the quantities are still blank because a person
    # has to supply them. Nothing here affects stock or cost until it's
    # saved as a real recipe, which is why drafts can sit here for days
    # without doing harm.

    def set_recipe_draft(self, store_id: str, item_name: str, kind: str,
                         ingredients: list[dict]):
        self._col(store_id, "recipe_drafts").document(doc_key(item_name)).set({
            "item_name": item_name, "kind": kind, "ingredients": ingredients,
        })

    def get_recipe_draft(self, store_id: str, item_name: str) -> dict | None:
        doc = self._col(store_id, "recipe_drafts").document(doc_key(item_name)).get()
        return doc.to_dict() if doc.exists else None

    def list_recipe_drafts(self, store_id: str) -> list[dict]:
        return [d.to_dict() for d in self._col(store_id, "recipe_drafts").stream()]

    def delete_recipe_draft(self, store_id: str, item_name: str):
        self._col(store_id, "recipe_drafts").document(doc_key(item_name)).delete()

    # ---- menu items deliberately excluded from recipes ----
    # Service charges and the like never consume stock. Marking them keeps
    # the "no recipe linked" warning meaningful: what's left flagged is
    # genuinely forgotten, not a corkage fee. Without this the warning
    # list fills with items that are fine, and then nobody reads it.

    def skip_recipe(self, store_id: str, item_name: str):
        self._col(store_id, "recipe_skips").document(doc_key(item_name)).set(
            {"item_name": item_name})

    def unskip_recipe(self, store_id: str, item_name: str):
        self._col(store_id, "recipe_skips").document(doc_key(item_name)).delete()

    def list_recipe_skips(self, store_id: str) -> list[str]:
        return [(d.to_dict() or {}).get("item_name") or d.id
                for d in self._col(store_id, "recipe_skips").stream()]

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
        """Writes one entry, by field path.

        Counting a kitchen is the one job in this system that two people
        genuinely do at the same time - one takes the dry store, one
        takes the fridge. The old version read the whole entries map,
        set one key and wrote the map back, so whoever saved second
        erased everything the other had counted since their own read.
        Nothing errored; the numbers were simply gone, and the count
        looked finished."""
        self._col(store_id, "stock_counts").document(session_id).update(
            {self.field_path("entries", material_id): counted})

    def clear_count_entry(self, store_id: str, session_id: str, material_id: str):
        self._col(store_id, "stock_counts").document(session_id).update(
            {self.field_path("entries", material_id): self._field_value("delete_field")})

    def close_count_session(self, store_id: str, session_id: str, closed_at: str):
        self._col(store_id, "stock_counts").document(session_id).update({
            "status": "closed", "closed_at": closed_at,
        })

    def delete_count_session(self, store_id: str, session_id: str):
        """Discard an unfinished count without touching the stock ledger."""
        self._col(store_id, "stock_counts").document(session_id).delete()

    def previous_closed_session(self, store_id: str, before: str) -> dict | None:
        """The count immediately before this one - the start of the period
        being measured."""
        closed = [s for s in self.list_count_sessions(store_id)
                  if s.get("status") == "closed" and (s.get("closed_at") or "") < before]
        closed.sort(key=lambda s: s.get("closed_at") or "")
        return closed[-1] if closed else None


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
