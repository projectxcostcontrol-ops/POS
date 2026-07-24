"""
In-memory stand-in for Firestore, supporting the subset of the API that
Store and MovementLedger actually use. Lets the tests run instantly with
no emulator, no Firebase project, and no network.

Paths nest to any depth (tenants/{id}/stores/{id}/materials/{id}), which
is what multi-tenancy needs - each level is just a flattened key in one
dict, so a document under one tenant can't be reached from another.
"""


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
        current = self._col._docs().get(self.id, {}) if merge else {}
        current.update(data)
        self._col._docs()[self.id] = current

    def update(self, data):
        self._col._docs().setdefault(self.id, {}).update(data)

    def get(self):
        return FakeDoc(self.id, self._col._docs().get(self.id, {}))

    def delete(self):
        self._col._docs().pop(self.id, None)

    def collection(self, sub_name):
        return FakeCollection(self._col.storage, f"{self._col.name}/{self.id}/{sub_name}")


class FakeCollection:
    def __init__(self, storage, name):
        self.storage = storage
        self.name = name

    def _docs(self):
        return self.storage.setdefault(self.name, {})

    def add(self, entry):
        doc_id = f"doc{len(self._docs())}"
        self._docs()[doc_id] = dict(entry)

        class Ref:
            id = doc_id

        return None, Ref()

    def document(self, doc_id):
        return FakeDocRef(self, doc_id)

    def where(self, field, op, value):
        parent = self

        class Filtered:
            def stream(self):
                return [FakeDoc(i, d) for i, d in parent._docs().items()
                        if d.get(field) == value]

        return Filtered()

    def stream(self):
        return [FakeDoc(i, d) for i, d in self._docs().items()]


class FakeDb:
    """Mimics db.collection(...).document(...).collection(...) chaining,
    to arbitrary depth."""

    def __init__(self):
        self.storage = {}

    def collection(self, name):
        return FakeCollection(self.storage, name)


def make_test_store(tenant_id: str = "t1", db=None):
    """A Store wired to the fake db and bound to a tenant, ready to use in
    tests. Pass the same db to make_test_store twice with different tenant
    ids to test isolation between two businesses sharing one database."""
    from storage.firestore_store import Store
    store = Store.__new__(Store)
    store.db = db or FakeDb()
    store.tenant_id = tenant_id
    return store
