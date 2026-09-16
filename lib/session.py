"""Tracks requests a tool handler can't answer immediately (openDiff blocks
until the user acts). Keyed by (client_id, request_id) since several agent
CLI sessions may be connected at once and JSON-RPC ids are only unique
per client."""

import threading


class PendingRequests:
    def __init__(self):
        self._lock = threading.Lock()
        self._pending = {}

    def add(self, client_id, request_id, meta=None):
        with self._lock:
            self._pending[(client_id, request_id)] = meta

    def get_meta(self, client_id, request_id):
        with self._lock:
            return self._pending.get((client_id, request_id))

    def resolve(self, client_id, request_id):
        """Remove the entry if present. Returns True if it was pending
        (so the caller should send a response); False if it was already
        resolved or never existed, so double-resolution is harmless."""
        with self._lock:
            return self._pending.pop((client_id, request_id), _MISSING) is not _MISSING

    def resolve_all_for(self, client_id):
        """One client disconnected: remove and return its request ids so
        the caller can synthesize rejections. No response is sent to a
        client that's already gone."""
        with self._lock:
            keys = [k for k in self._pending if k[0] == client_id]
            for k in keys:
                del self._pending[k]
        return [req_id for (_cid, req_id) in keys]

    def resolve_all(self):
        """Server stopping: clear everything, returned as (client_id,
        request_id) pairs."""
        with self._lock:
            keys = list(self._pending.keys())
            self._pending.clear()
        return keys


_MISSING = object()
