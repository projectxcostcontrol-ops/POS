from __future__ import annotations

"""
Which Loyverse account a branch belongs to.

A business is not the same thing as a Loyverse account. Shops that grew
one branch at a time very often opened a separate Loyverse account for
each, so a single business can hold several access tokens - and the
earlier design, one token per business, had no way to say that. The
owner connected one branch and the rest were simply unreachable.

So there is no such thing as "the provider" any more. There is a
provider FOR A BRANCH, and this is what answers that question. Every
endpoint that talks to the POS already has a store_id in its path, so
every one of them can say which branch it means; asking without saying
is a question with no correct answer, and the property that used to
answer it did so by handing back whichever account came first.

Branches stay entirely separate. Each one's data already lives under
stores/{store_id}, ids from different Loyverse accounts never collide,
and nothing here merges anything across accounts.

Kept out of the API layer so it can be tested without a web framework -
the interesting behaviour is "one dead token must not hide the other
accounts' branches", and that deserves a test.
"""


class PosRegistry:
    def __init__(self, store, adapter_factory, now: str):
        self.store = store
        self._adapter_for = adapter_factory
        self._now = now
        self._connections = None
        self._index = None

    def invalidate(self):
        self._connections = None
        self._index = None

    @property
    def connections(self) -> list[dict]:
        if self._connections is None:
            # A business still on the single-token shape becomes a
            # business with one connection, here, the first time anything
            # asks. Nobody has to reconnect and nobody has to be told.
            self.store.migrate_legacy_token(self._now)
            self._connections = self.store.list_connections()
        return self._connections

    def branches(self) -> tuple[list[dict], list[dict]]:
        """Every branch across every connected account, and separately,
        whatever went wrong reaching any of them.

        An expired or revoked token must not hide the branches of the
        accounts that are fine - one account failing is exactly the case
        keeping them separate is meant to survive. The failures come back
        alongside rather than as an exception so the caller can show both:
        the branches that work, and the account that needs attention.
        """
        found, failures = [], []
        for conn in self.connections:
            try:
                adapter = self._adapter_for(conn)
                for st in adapter.get_stores():
                    found.append({"id": st["id"], "name": st.get("name", ""),
                                  "connection_id": conn["id"],
                                  "connection_label": conn.get("label", "")})
                if conn.get("last_error"):
                    self.store.update_connection(conn["id"], {"last_error": None})
            except Exception as e:
                self.store.update_connection(conn["id"], {"last_error": str(e)})
                failures.append({"connection_id": conn["id"],
                                 "label": conn.get("label", ""), "error": str(e)})
        return found, failures

    def refresh_index(self) -> dict:
        """Re-ask every account which branches it has and record the
        answer, so a branch added in Loyverse appears here by opening the
        app rather than by anyone knowing to press something."""
        branches, _ = self.branches()
        mapping = {b["id"]: b["connection_id"] for b in branches}
        if mapping:
            self.store.set_store_index(mapping)
            self._index = {**(self._index or {}), **mapping}
        return mapping

    def connection_for(self, store_id: str) -> dict | None:
        if self._index is None:
            self._index = self.store.get_store_index()
        conn = self._lookup(store_id)
        if conn is not None:
            return conn

        # Not in the index: a branch added on the Loyverse side since we
        # last looked, or a business that has never opened the settings
        # page. Rebuild once and answer - a lookup that fails for want of
        # a refresh nobody knew to trigger reads as a broken account.
        self.refresh_index()
        return self._lookup(store_id)

    def _lookup(self, store_id: str) -> dict | None:
        conn_id = (self._index or {}).get(store_id)
        if not conn_id:
            return None
        return next((c for c in self.connections if c["id"] == conn_id), None)

    def provider_for(self, store_id: str):
        """The adapter for this branch, or None if we can't tell which
        account it belongs to. The caller decides what that means to a
        user - it reads differently to someone who has connected nothing
        than to someone whose branch list is stale."""
        conn = self.connection_for(store_id)
        return self._adapter_for(conn) if conn else None
