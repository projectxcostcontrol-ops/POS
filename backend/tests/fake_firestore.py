"""
In-memory stand-in for Firestore, supporting the subset of the API that
Store and MovementLedger actually use. Lets the tests run instantly with
no emulator, no Firebase project, and no network.

Paths nest to any depth (tenants/{id}/stores/{id}/materials/{id}), which
is what multi-tenancy needs - each level is just a flattened key in one
dict, so a document under one tenant can't be reached from another.
"""


class FakeIncrement:
    """Stands in for firestore.Increment.

    Atomic add, applied server-side. Two important behaviours the code
    under test relies on, both mirrored here: incrementing a field that
    does not exist yet creates it at the increment value, and two
    increments never lose each other the way a read-modify-write does.
    """

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class FakeArrayOp:
    """firestore.ArrayUnion / ArrayRemove. Adds or removes members
    server-side, so two people editing the same list don't overwrite each
    other's edit the way read-modify-write does."""

    __slots__ = ("values", "remove")

    def __init__(self, values, remove=False):
        self.values = list(values)
        self.remove = remove


class FakeFieldPath(tuple):
    """A path to one field inside a document, e.g. ("entries", "m1").

    Updating by path touches only that field. The alternative - read the
    whole map, change one key, write the map back - loses whatever
    someone else wrote in between, which for a stock count means two
    people counting different shelves and one of them losing their work.
    """

    def __new__(cls, *segments):
        return super().__new__(cls, segments)


DELETE_FIELD = object()


def _resolve(current, value):
    if isinstance(value, FakeIncrement):
        return (current or 0) + value.value
    if isinstance(value, FakeArrayOp):
        existing = list(current or [])
        if value.remove:
            return [x for x in existing if x not in value.values]
        return existing + [v for v in value.values if v not in existing]
    return value


def _apply_increments(current: dict, data: dict) -> dict:
    """Resolve field values against what is stored. Plain keys replace;
    increments and array ops combine; field paths reach into a nested
    map, and DELETE_FIELD removes."""
    out = dict(current)
    for k, v in data.items():
        if isinstance(k, FakeFieldPath):
            target = out
            for seg in k[:-1]:
                nxt = dict(target.get(seg) or {})
                target[seg] = nxt
                target = nxt
            if v is DELETE_FIELD:
                target.pop(k[-1], None)
            else:
                target[k[-1]] = _resolve(target.get(k[-1]), v)
            continue
        if v is DELETE_FIELD:
            out.pop(k, None)
            continue
        out[k] = _resolve(current.get(k), v)
    return out


class FakeDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)

    @property
    def exists(self):
        return bool(self._data)


class FakeDocRef:
    def __init__(self, collection, doc_id):
        self._col = collection
        self.id = doc_id

    def set(self, data, merge=False):
        self._col.count("writes")
        stored = self._col._docs().get(self.id, {})
        # merge keeps what is already there; a plain set replaces the
        # document, and an increment in that case starts from zero.
        base = dict(stored) if merge else {}
        self._col._docs()[self.id] = _apply_increments(base, data)

    def update(self, data):
        self._col.count("writes")
        stored = self._col._docs().setdefault(self.id, {})
        resolved = _apply_increments(stored, data)
        stored.clear()
        stored.update(resolved)

    def get(self):
        self._col.count("reads")
        return FakeDoc(self.id, self._col._docs().get(self.id, {}))

    def delete(self):
        self._col.count("deletes")
        self._col._docs().pop(self.id, None)

    def collection(self, sub_name):
        return FakeCollection(self._col.storage, f"{self._col.name}/{self.id}/{sub_name}",
                              meter=self._col.meter)


class FakeCollection:
    def __init__(self, storage, name, meter=None):
        self.storage = storage
        self.name = name
        self.meter = meter if meter is not None else {}

    def count(self, kind, n=1):
        """Firestore bills per DOCUMENT touched, not per call, so that's
        what gets counted here."""
        self.meter[kind] = self.meter.get(kind, 0) + n

    def _docs(self):
        return self.storage.setdefault(self.name, {})



    def _new_id(self):
        """Auto-ids must not be reused after a delete, the way real ones
        aren't - a recycled id would let a deleted movement's slot be
        silently overwritten by the next one."""
        self.storage.setdefault("__ids__", {})
        n = self.storage["__ids__"].get(self.name, 0)
        self.storage["__ids__"][self.name] = n + 1
        return f"doc{n}"

    def add(self, entry):
        self.count("writes")
        doc_id = self._new_id()
        self._docs()[doc_id] = dict(entry)
        return None, FakeDocRef(self, doc_id)

    def document(self, doc_id=None):
        """No id means "give me a fresh one", as on the real client - which
        is what lets a document be created inside a batch alongside other
        writes instead of needing its own round trip first."""
        return FakeDocRef(self, doc_id if doc_id is not None else self._new_id())

    def where(self, field, op, value):
        return FakeQuery(self).where(field, op, value)

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self).order_by(field, direction)

    def stream(self):
        docs = self._docs()
        self.count("reads", len(docs))
        return [FakeDoc(i, d) for i, d in docs.items()]


class FakeBatch:
    """Batched writes, mirroring Firestore's real batch API so the code
    under test takes the same path it takes in production."""

    def __init__(self):
        self._ops = []

    def set(self, doc_ref, data, merge=False):
        self._ops.append(("set", doc_ref, data, merge))

    def update(self, doc_ref, data):
        self._ops.append(("update", doc_ref, data, None))

    def commit(self):
        for op, doc_ref, data, merge in self._ops:
            if op == "set":
                doc_ref.set(data, merge=merge)
            else:
                doc_ref.update(data)
        self._ops = []


class FakeQuery:
    """Chainable where/order_by, matching the real client.

    The earlier stand-in accepted any operator and compared with == , so
    a range query in production behaved nothing like the same call in a
    test - the tests would pass while the code was wrong. A fake that
    lies about the thing it stands in for is worse than no fake."""

    OPS = {
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
        ">": lambda a, b: a is not None and a > b,
        ">=": lambda a, b: a is not None and a >= b,
        "<": lambda a, b: a is not None and a < b,
        "<=": lambda a, b: a is not None and a <= b,
        "in": lambda a, b: a in b,
    }

    def __init__(self, collection, filters=None, order=None):
        self._col = collection
        self._filters = list(filters or [])
        self._order = order

    def where(self, field, op, value):
        if op not in self.OPS:
            raise ValueError(f"unsupported operator in fake: {op!r}")
        return FakeQuery(self._col, self._filters + [(field, op, value)], self._order)

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self._col, self._filters, (field, direction))

    def stream(self):
        rows = [(i, d) for i, d in self._col._docs().items()]
        for field, op, value in self._filters:
            test = self.OPS[op]
            rows = [(i, d) for i, d in rows if test(d.get(field), value)]
        if self._order:
            field, direction = self._order
            rows.sort(key=lambda r: (r[1].get(field) is None, r[1].get(field)),
                      reverse=(direction == "DESCENDING"))
        self._col.count("reads", len(rows))
        return [FakeDoc(i, d) for i, d in rows]


class FakeDb:
    """Mimics db.collection(...).document(...).collection(...) chaining,
    to arbitrary depth."""

    def __init__(self):
        self.storage = {}
        self.meter = {}

    def collection(self, name):
        return FakeCollection(self.storage, name, meter=self.meter)

    def batch(self):
        return FakeBatch()

    def array_union(self, values):
        return FakeArrayOp(values)

    def array_remove(self, values):
        return FakeArrayOp(values, remove=True)

    def field_path(self, *segments):
        return FakeFieldPath(*segments)

    def delete_field(self):
        return DELETE_FIELD

    def increment(self, value):
        """Store asks the db for this rather than importing the SDK, so a
        test never needs google-cloud-firestore installed to exercise the
        same write path production takes."""
        return FakeIncrement(value)

    def get_all(self, references):
        """Batch document fetch, as on the real client.

        Returns a snapshot for every reference including the ones that
        don't exist - which is the whole point: asking "which of these 25
        receipts have I already seen" must cost 25 reads, not the size of
        the collection.
        """
        return [ref.get() for ref in references]

    # ---- test-side accounting ----
    def reset_meter(self):
        self.meter.clear()

    @property
    def reads(self):
        return self.meter.get("reads", 0)

    @property
    def writes(self):
        return self.meter.get("writes", 0)


def make_test_store(tenant_id: str = "t1", db=None):
    """A Store wired to the fake db and bound to a tenant, ready to use in
    tests. Pass the same db to make_test_store twice with different tenant
    ids to test isolation between two businesses sharing one database."""
    from storage.firestore_store import Store
    store = Store.__new__(Store)
    store.db = db or FakeDb()
    store.tenant_id = tenant_id
    return store
