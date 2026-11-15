"""Directory tenants: one SQLite database per tenant, in one directory.

A tenant is whoever must not see anyone else's memories: a group chat, a guild, a
user. Tenant `<id>` lives in `<path>/<id>.db`; that is the whole registry, so adding a
tenant is creating a file (`add`, or `create=True` to create on first use) and
removing one is deleting it. No model, no service.

The id becomes a file name, so `valid_id` is strict whatever pattern is configured: it
must also be a plain file name that cannot leave the directory or mean something to
the operating system (separators, "..", a leading dot, NUL, trailing space or dot,
Windows device names such as CON or COM1). Existing files are matched by exact name,
so on a case-insensitive file system "Abc" can never open the database of "abc".

Bounded: at most `max_open` tenant stores are open at once (the least recently used is closed;
a request that was still using it fails once and the next one reopens it), and with create=True
at most `max_tenants` database files are made. A request costs one stat of the tenant's file, never
a directory listing: the directory's names are cached and re-read only when its modification time
changed by someone else (this provider accounts for its own changes).

Standard library only.
"""
import collections
import os
import re
import threading
import time

from ..backends.sqlite import SQLiteBackend
from ..registry import register
from .base import TenantProvider

DEFAULT_ID_PATTERN = r"[0-9A-Za-z_-]{1,64}"
SUFFIX = ".db"
FORCE_SYNC_SECONDS = 1.0
MAX_ID_CHARS = 128
_FORBIDDEN = set('<>:"/\\|?*')
_RESERVED = ({"con", "prn", "aux", "nul", "conin$", "conout$"}
             | {"com%d" % i for i in range(10)} | {"lpt%d" % i for i in range(10)})


def plain_file_name(name):
    """True when `name` is safe as a single file name on every platform."""
    if not isinstance(name, str) or not name or len(name) > MAX_ID_CHARS:
        return False
    if name in (".", "..") or name.startswith(".") or name[-1] in " .":
        return False
    if any(c in _FORBIDDEN or ord(c) < 32 or ord(c) == 127 for c in name):
        return False
    # "con.txt" is the device too: the part before the first dot decides.
    return name.split(".", 1)[0].rstrip().lower() not in _RESERVED


@register("tenants", "directory")
class DirectoryTenants(TenantProvider):
    """Options: path (the directory), id_pattern (a regular expression an id must match
    whole, default letters, digits, "_" and "-" up to 64), create (make a tenant's
    database on first use; default False: only existing ones are served), store (extra
    options for each tenant's sqlite backend, e.g. {"roles": ["user", "assistant"]}; this
    provider only makes SQLite stores, so a `type` other than "sqlite" is an error),
    max_open (tenant stores kept open, default 64), max_tenants (database files create=True
    may make, default 10000)."""

    def __init__(self, path, id_pattern=DEFAULT_ID_PATTERN, create=False, store=None, max_open=64,
                 max_tenants=10000):
        self._dir = os.path.abspath(str(path))
        self._id_re = re.compile(id_pattern)
        self._create = bool(create)
        if isinstance(store, str):
            store = {"type": store}
        options = dict(store or {})
        if options.pop("type", "sqlite") != "sqlite":
            raise ValueError("directory tenants only make sqlite stores: store.type must be \"sqlite\"")
        options.pop("path", None)  # the provider decides where a tenant lives
        self._store_options = options
        self._max_open = max(1, int(max_open))
        self._max_tenants = max(0, int(max_tenants))
        self._lock = threading.Lock()
        self._backends = collections.OrderedDict()  # tenant id -> backend, least recently used first
        self._names = set()  # file names in the directory, as of self._sig
        self._folded = {}  # casefolded name -> a name having it
        self._sig = None
        self._forced_at = float("-inf")

    def valid_id(self, tenant_id):
        return (isinstance(tenant_id, str) and plain_file_name(tenant_id)
                and self._id_re.fullmatch(tenant_id) is not None)

    def _file(self, tenant_id):
        return os.path.join(self._dir, tenant_id + SUFFIX)

    def _dir_signature(self):
        try:
            return os.stat(self._dir).st_mtime_ns
        except OSError:
            return None

    def _sync(self, force=False):
        """Bring the cached file names up to date; lists the directory only when it changed."""
        sig = self._dir_signature()
        if sig is not None and sig == self._sig and not force:
            return
        try:
            names = set(os.listdir(self._dir))
        except OSError:
            names = set()
        self._names = names
        self._folded = {n.casefold(): n for n in names}
        self._sig = sig

    def _changed(self, added=(), removed=()):
        """This provider just changed the directory (after _sync made the cache current): update the
        cached names instead of listing the directory again."""
        for name in added:
            self._names.add(name)
            self._folded[name.casefold()] = name
        for name in removed:
            self._names.discard(name)
            if self._folded.get(name.casefold()) == name:
                del self._folded[name.casefold()]
        self._sig = self._dir_signature()

    def _exists(self, tenant_id):
        """True when a file of exactly this name exists (case-exact on any file system): one stat of
        the file, and the cached names to tell "Abc" from "abc" on a case-insensitive file system."""
        if not os.path.isfile(self._file(tenant_id)):
            return False
        name = tenant_id + SUFFIX
        self._sync()
        if name not in self._names and time.monotonic() - self._forced_at > FORCE_SYNC_SECONDS:
            # Either a file made a moment ago that the directory's timestamp has not shown yet, or a
            # name that differs only by case. A listing tells which; at most once a second.
            self._forced_at = time.monotonic()
            self._sync(force=True)
        return name in self._names

    def _clashes(self, tenant_id):
        """True when a file differing from this name only by case exists."""
        self._sync()
        name = tenant_id + SUFFIX
        other = self._folded.get(name.casefold())
        return other is not None and other != name

    def _count(self):
        return sum(1 for n in self._names if n.endswith(SUFFIX))

    def _open(self, tenant_id):
        return SQLiteBackend(self._file(tenant_id), **self._store_options)

    def _remember(self, tenant_id, backend):
        """Keep `backend` as the most recently used and close the least recently used ones over max_open."""
        self._backends[tenant_id] = backend
        self._backends.move_to_end(tenant_id)
        while len(self._backends) > self._max_open:
            _old_id, old = self._backends.popitem(last=False)
            old.close()

    def _serve(self, tenant_id, create):
        """Open the tenant's database (making it when `create`) and keep it among the open ones."""
        if create:
            self._sync()
            os.makedirs(self._dir, exist_ok=True)
        backend = self._open(tenant_id)
        self._remember(tenant_id, backend)
        if create:
            # The backend's own files (-wal, -shm) and the closing of an evicted one change the
            # directory too; only the database file is a name that matters here.
            self._changed(added=(tenant_id + SUFFIX,))
        return backend

    def store(self, tenant_id):
        """This tenant's store, or None: invalid id, no database and create is off (or the
        max_tenants limit is reached)."""
        if not self.valid_id(tenant_id):
            return None
        with self._lock:
            cached = self._backends.get(tenant_id)
            if cached is not None:
                self._backends.move_to_end(tenant_id)
                return cached
            if self._exists(tenant_id):
                return self._serve(tenant_id, create=False)
            if not self._create or self._clashes(tenant_id) or self._count() >= self._max_tenants:
                return None
            return self._serve(tenant_id, create=True)

    def add(self, tenant_id):
        """Create the tenant's database (for command-line tools); an existing one is kept.
        Returns its store."""
        if not self.valid_id(tenant_id):
            raise ValueError("invalid tenant id")
        with self._lock:
            cached = self._backends.get(tenant_id)
            if cached is not None:
                self._backends.move_to_end(tenant_id)
                return cached
            if self._exists(tenant_id):
                return self._serve(tenant_id, create=False)
            if self._clashes(tenant_id):
                raise ValueError("invalid tenant id")
            return self._serve(tenant_id, create=True)

    def remove(self, tenant_id):
        """Close and delete the tenant's database files. True when there was one."""
        if not self.valid_id(tenant_id):
            return False
        with self._lock:
            backend = self._backends.pop(tenant_id, None)
            if backend is not None:
                backend.close()
            if not self._exists(tenant_id):
                return False
            base = self._file(tenant_id)
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(base + suffix)
                except FileNotFoundError:
                    pass
            self._changed(removed=(tenant_id + SUFFIX,))
            return True

    def list(self):
        """Ids of the tenants that have a database, sorted."""
        try:
            names = os.listdir(self._dir)
        except OSError:
            return []
        ids = [n[:-len(SUFFIX)] for n in names if n.endswith(SUFFIX)]
        return sorted(i for i in ids if self.valid_id(i))

    def close(self):
        with self._lock:
            backends, self._backends = list(self._backends.values()), collections.OrderedDict()
        for backend in backends:
            backend.close()
