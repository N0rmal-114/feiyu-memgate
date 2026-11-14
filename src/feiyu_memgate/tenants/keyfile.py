"""Key-file tenants: every tenant has its own key, and so its own store.

A tenant is whoever must not see anyone else's memories: a group chat, a guild, a
user. The file maps a tenant id to that tenant's own key: {"<tenant id>": "<key>"}.
The store of a tenant is built from the `store` template plus its key, so each
tenant talks to its own account. The file is re-read when its (mtime, size, inode, mode) changes, so
adding a tenant needs no restart. A file that fails to load never replaces the last good
one; if no good one exists every lookup returns None. Keys are never logged.

Two kinds of failure are told apart:
- the content is bad (too large, not JSON, wrong shape): remembered per file version,
  retried only when the file changes;
- the file cannot be read or stat'ed (I/O): never remembered, retried on a later request,
  at most once per RETRY_SECONDS so a broken file adds no disk reads to every request.

Standard library only.
"""
import json
import os
import re
import sys
import threading
import time

from ..registry import create, register, resolve
from .base import TenantProvider

# Digits only by default (chat group numbers). Whatever the pattern, an id is matched whole.
DEFAULT_ID_PATTERN = r"[0-9]{5,12}"
MAX_KEYS_BYTES = 1024 * 1024
RETRY_SECONDS = 5.0


def stderr_log(line):
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


@register("tenants", "keyfile")
class KeyFileTenants(TenantProvider):
    """Options: path (the JSON key file), store (a backend spec; each tenant's key is
    added to it as `key`, and `writable` is set), id_pattern (a regular expression a
    tenant id must match whole, default 5 to 12 digits)."""

    def __init__(self, path, store, id_pattern=DEFAULT_ID_PATTERN, log=stderr_log, clock=time.monotonic):
        if isinstance(store, str):
            store = {"type": store}
        self._path = str(path)
        self._store_spec = dict(store)
        # A typo in the store type fails now, at start-up, not on the first tenant request. Only the
        # piece is looked up: no backend is built, there is no key yet.
        resolve("backend", self._store_spec.get("type"))
        self._id_re = re.compile(id_pattern)
        self._log = log
        self._clock = clock
        self._lock = threading.Lock()
        self._signature = None
        self._bad_signature = None  # content of this file version is bad
        self._retry_at = None  # I/O failure: do not try again before this time
        self._io_warned = False
        self._keys = {}
        self._clients = {}
        self._reload()

    def valid_id(self, tenant_id):
        """True for a str that matches the id pattern whole (no whitespace, no trailing newline)."""
        return isinstance(tenant_id, str) and self._id_re.fullmatch(tenant_id) is not None

    def _stat(self):
        st = os.stat(self._path)
        return (st.st_mtime_ns, st.st_size, getattr(st, "st_ino", 0), st.st_mode)

    def _read_bytes(self):
        """The file's bytes, read at most MAX_KEYS_BYTES + 1 so a huge file is never loaded."""
        with open(self._path, "rb") as f:
            return f.read(MAX_KEYS_BYTES + 1)

    def _parse(self, raw):
        if len(raw) > MAX_KEYS_BYTES:
            raise ValueError("too large")
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        for gid, key in data.items():
            if not self.valid_id(gid) or not isinstance(key, str) or not key:
                raise ValueError("bad entry")
        return data

    def _keeping(self):
        return "previous keys" if self._signature is not None else "none"

    def _io_failed(self, err):
        self._retry_at = self._clock() + RETRY_SECONDS
        if not self._io_warned:
            self._io_warned = True
            self._log("warn: tenant keys file unreadable (%s); keeping %s" % (type(err).__name__, self._keeping()))

    def _reload(self):
        if self._retry_at is not None and self._clock() < self._retry_at:
            return
        try:
            sig = self._stat()
        except OSError as err:
            self._io_failed(err)
            return
        if sig == self._signature:
            self._retry_at = None
            self._io_warned = False
            return
        if sig == self._bad_signature:
            return
        if sig[1] > MAX_KEYS_BYTES:
            # Refused from the size alone, before any read.
            self._bad_signature = sig
            self._log("warn: tenant keys file failed to load (too large); keeping %s" % self._keeping())
            return
        try:
            raw = self._read_bytes()
        except OSError as err:
            self._io_failed(err)
            return
        try:
            keys = self._parse(raw)
        except Exception as err:  # noqa: BLE001 - only the error type is logged, never content
            self._bad_signature = sig
            self._retry_at = None
            self._log("warn: tenant keys file failed to load (%s); keeping %s" % (type(err).__name__, self._keeping()))
            return
        self._bad_signature = None
        self._retry_at = None
        self._io_warned = False
        self._signature = sig
        self._keys = keys
        self._clients = {gid: c for gid, c in self._clients.items() if gid in keys and c[0] == keys[gid]}

    def list(self):
        """The tenant ids in the key file now, sorted. Never the keys."""
        with self._lock:
            self._reload()
            return sorted(self._keys)

    def store(self, tenant_id):
        """This tenant's own writable store, or None when the tenant is not registered."""
        if not self.valid_id(tenant_id):
            return None
        with self._lock:
            self._reload()
            key = self._keys.get(tenant_id)
            if key is None:
                return None
            cached = self._clients.get(tenant_id)
            if cached is None or cached[0] != key:
                cached = (key, create("backend", self._store_spec, key=key, writable=True))
                self._clients[tenant_id] = cached
            return cached[1]
