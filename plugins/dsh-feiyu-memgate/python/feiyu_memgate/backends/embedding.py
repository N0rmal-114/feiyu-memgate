"""Embedding backend: vector search with nothing but an embeddings endpoint. Standard library only.

What it is: every memory is kept verbatim in one SQLite file together with its embedding
(float32, L2-normalised when written). A search embeds the query through any
OpenAI-compatible `POST <url>/embeddings` endpoint (Ollama, vLLM, a hosted API), then ranks
every stored vector by cosine similarity (a dot product of unit vectors, `math.sumprod`
when the interpreter has it). No separate vector server, no extra package.

What it is not: an index. Search is a brute-force scan of all stored vectors, which are
cached in memory as float32 arrays (4 bytes per dimension: 50 000 memories of 1024 dimensions
take about 200 MB). Measured on one CPU core (CPython 3.14, random 1024-d vectors, scan
only, query embedding excluded): 10 000 memories take about 0.14 s and 50 000 about 0.9 s per
search, plus about 0.1 s the first time to decode the vectors from the database. That is fine for a personal
store (thousands to a few tens of thousands of memories) and too slow beyond about 100 000;
use a real vector database (the openviking backend) there. Every memory text is sent to the
embeddings endpoint, so pick an endpoint you trust with that text.

Scores: raw cosine of unrelated texts is far from 0 for most models (0.2 to 0.4 for bge-m3,
more for others), so the backend maps it to a relevance in 0..1,
`score = clamp((cosine - floor) / (ceil - floor))`, and keeps the raw cosine in `Hit.meta["cosine"]`
when `Hit` has a `meta` field.

Models: the cosine range, the best score line and the prefixes the model's authors ask for differ a lot
from model to model, so the backend carries a small preset table (PRESETS below) keyed by a pattern of the
model name: floor, ceil, default_min_score, default_relative_ratio, query_prefix, document_prefix. The bge-m3
numbers (floor 0.40, ceil 0.80, min_score 0.50 = cosine 0.60, ratio 0.85) were chosen with bge-m3 on the
development sets (examples/eval for floor and ceil, bench/dev for the other two, bench/dev_eval.py); the
other rows come from experiment bench/experiments/light_models (bench/dev only): floor and ceil from a
fixed recipe (floor = mean cosine between stored memories - 0.05, rounded to 0.05; ceil = floor + 2/3 of
the rest of the range), min_score and ratio from the best grid point of bench/dev_eval.py --search. A
model that is not in the table runs with the bge-m3 numbers and ONE warning in the log at startup: the
numbers are then a guess, calibrate them (bench/dev_eval.py --search on your own data). Every preset value
can be overridden by an option of the same name. Each preset also carries the model's recall-leaning score
lines (broad_min_score, broad_ratio) that the `min_score` / `relative_score` stages use for `profile = "broad"`
(rule and numbers: docs/backends.md, bench/results/dev/*-broad.md); they are not options of the backend.

Prefixes: some models want the text wrapped before it is embedded (a query instruction, a document
tag). `query_prefix` is put in front of every query, `document_prefix` in front of every stored text, only
for the embeddings request; stored and returned texts are never changed. The store remembers its
document prefix like its model and refuses to open with another one (the vectors would not be comparable).

HTTP hygiene (same as the openviking backend): http or https only, no environment proxies,
no redirects (any non-2xx answer is a failure), the response size is bounded, one total
deadline per call, the socket is attached to `ctl` so the gateway can cut a late call.
Errors are BackendError and never carry upstream text, memory text or the key.

Thread safety: one writer connection under a lock (`_lock`), and a bounded pool of read-only reader
connections (`max_readers`) for everything that reads, so a search never waits for a write (file
databases use WAL; a ":memory:" database cannot be shared between connections and keeps the one
connection under the lock). The vectors are loaded on a reader connection without the writer's lock; only
the swap of the in-memory cache is guarded (`_cache_lock`), and one search loads while the others wait for
it. HTTP calls use a connection each and run outside every lock. close() closes every connection.

How a change is noticed: the `info` table holds a write counter, `gen`, that every write of this backend
raises by one inside its own transaction (the transaction takes the file's write lock first, so the number
read there is exact). The cache remembers the `gen` it was loaded at, and a load reads it in the same read
transaction as the vectors. A search reads `gen` on a reader connection; the cache is used when it is the
same and reloaded when it is not. A write of this backend applies itself to the cache and moves the cache's
`gen` forward when the cache was current, so a search right after remember() needs no reload; a write of
another backend object or process (to the same file) raises `gen` and is noticed by the next search. (SQLite's
`PRAGMA data_version` is not used: it is per connection and counts every other connection's commits, the
backend's own writer included, so it cannot be compared between pooled readers.)
"""
import array
import collections
import contextlib
import heapq
import http.client
import json
import logging
import math
import socket
import ssl
import threading
import time
import urllib.parse
from typing import Any

from ..core.pool import _NoCtl
from ..errors import BackendError
from ..registry import register
from . import _sqlfile
from ._sqlfile import SCAN_BATCH, Readers
from .base import (DEFAULT_BUDGET, MemoryBackend, as_roles, clamp01, kept_messages, make_hit, new_receipt,  # noqa: F401
                   parse_item, receipt_ref, require_writable)
from .base import today as _today

PRESETS = (
    # (pattern, values): the first pattern contained in the lower-cased model name wins. "min_score" and
    # "ratio" become default_min_score / default_relative_ratio of the instance. See the docstring above.
    ("bge-m3", {"floor": 0.40, "ceil": 0.80, "min_score": 0.50, "ratio": 0.85,
                "broad_min_score": 0.30, "broad_ratio": 0.60,
                "query_prefix": "", "document_prefix": ""}),
    ("bge-small-zh", {"floor": 0.40, "ceil": 0.80, "min_score": 0.30, "ratio": 0.90,
                      "broad_min_score": 0.10, "broad_ratio": 0.65,
                      "query_prefix": "", "document_prefix": ""}),
    # BAAI's retrieval instruction for the v1.5 Chinese models; it helped the base model on bench/dev and not
    # the small one (ranked MRR@10), so only the base preset uses it.
    ("bge-base-zh", {"floor": 0.35, "ceil": 0.80, "min_score": 0.25, "ratio": 0.75,
                     "broad_min_score": 0.05, "broad_ratio": 0.50,
                     "query_prefix": "为这个句子生成表示以用于检索相关文章：", "document_prefix": ""}),
    # Google's prompts for embeddinggemma (`task: search result | query: ` and `title: none | text: `) lowered
    # ranked MRR@10 on bench/dev, so the preset sends no prefix.
    ("embeddinggemma", {"floor": 0.30, "ceil": 0.75, "min_score": 0.55, "ratio": 0.85,
                        "broad_min_score": 0.30, "broad_ratio": 0.60,
                        "query_prefix": "", "document_prefix": ""}),
    ("qwen3-embedding", {"floor": 0.35, "ceil": 0.80, "min_score": 0.20, "ratio": 0.95,
                         "broad_min_score": 0.15, "broad_ratio": 0.65,
                         "query_prefix": "Instruct: Given a web search query, retrieve relevant passages that "
                                         "answer the query\nQuery:", "document_prefix": ""}),
)
# What an unknown model gets: the bge-m3 numbers, without prefixes.
# The broad numbers are the bge-m3 ones too (a guess for an unknown model, like the rest).
GENERIC: dict[str, Any] = {"floor": 0.40, "ceil": 0.80, "min_score": 0.50, "ratio": 0.85, "broad_min_score": 0.30,
           "broad_ratio": 0.60, "query_prefix": "", "document_prefix": ""}

_log = logging.getLogger("feiyu_memgate.backends.embedding")
_warned: set[str] = set()  # model names already warned about in this process

MAX_RESPONSE = 16 * 1024 * 1024  # a batch of 32 vectors of 3072 floats is about 2 MB of JSON
QUERY_CACHE = 256
MAX_READERS = 8  # read connections of one store (default of the max_readers option)
MAX_PREFIX = 512  # characters; the longest recommended instruction is about 100
SCORE_CHECK_EVERY = 256  # vectors scored between two deadline checks

try:
    _sumprod = math.sumprod  # type: ignore[attr-defined]  # Python 3.12+: the dot product in C
except AttributeError:  # pragma: no cover - Python 3.11
    def _sumprod(a, b):
        return sum(x * y for x, y in zip(a, b))

_SCHEMA = _sqlfile.memories_table(",\n    vector BLOB NOT NULL") + _sqlfile.INFO_TABLE


def preset_for(model):
    """(values, known) for a model name: the matching row of PRESETS, or GENERIC with known False."""
    lowered = model.lower()
    for pattern, values in PRESETS:
        if pattern in lowered:
            return values, True
    return GENERIC, False


def _unit(values):
    """`values` (floats) as an L2-normalised float32 array. BackendError for a zero or non-finite vector."""
    norm = math.sqrt(math.fsum(v * v for v in values))
    if not math.isfinite(norm) or norm == 0.0:
        raise BackendError("unusable embedding")
    return array.array("f", [v / norm for v in values])


def _generation(db):
    """The write counter of the store as `db` sees it (0 for a store nothing has written to yet)."""
    row = db.execute("SELECT value FROM info WHERE key = 'gen'").fetchone()
    return int(row[0]) if row else 0


def _blob_to_vector(blob):
    vec = array.array("f")
    vec.frombytes(blob)
    return vec


class _Http:
    """The only network code of this backend: one POST to <url>/embeddings per call."""

    def __init__(self, url, model, key, timeout):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("url must be http(s)://host[:port][/path]")
        self.https = parts.scheme == "https"
        self.host = parts.hostname
        self.port = parts.port or (443 if self.https else 80)
        self.path = parts.path.rstrip("/") + "/embeddings"
        self.model = model
        self.key = key
        self.timeout = timeout
        self._ssl = ssl.create_default_context() if self.https else None

    def embed(self, texts, total, ctl):
        """Embeddings (lists of floats, in input order) for `texts`, inside `total` seconds."""
        if total <= 0:
            raise BackendError("deadline exceeded")
        ctl = ctl if ctl is not None else _NoCtl()
        attach, detach = getattr(ctl, "attach", None), getattr(ctl, "detach", None)
        deadline = time.monotonic() + total

        def left():
            return max(min(self.timeout, deadline - time.monotonic()), 0.01)

        body = json.dumps({"model": self.model, "input": list(texts)}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Connection": "close"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        raw = sock = conn = resp = None
        try:
            raw = socket.create_connection((self.host, self.port), timeout=left())
            if attach is not None:
                attach(raw)
            sock = raw
            if self.https:
                assert self._ssl is not None  # built in __init__ whenever https is on
                sock = self._ssl.wrap_socket(raw, server_hostname=self.host)
            conn = (http.client.HTTPSConnection if self.https else http.client.HTTPConnection)(self.host, self.port)
            conn.sock = sock
            sock.settimeout(left())
            conn.request("POST", self.path, body=body, headers=headers)
            sock.settimeout(left())
            resp = conn.getresponse()
            if not 200 <= resp.status < 300:  # includes 3xx: redirects are never followed
                raise BackendError("embeddings request failed")
            chunks, size = [], 0
            while not resp.isclosed():  # the response closes the socket itself at the end
                sock.settimeout(left())
                chunk = resp.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE or time.monotonic() > deadline:
                    raise BackendError("embeddings response too large or too slow")
                chunks.append(chunk)
            parsed = json.loads(b"".join(chunks).decode("utf-8"))
        except BackendError:
            raise
        except Exception:  # noqa: BLE001 - never leak upstream detail
            raise BackendError("embeddings request failed") from None
        finally:
            if detach is not None:
                detach()
            for thing in (resp, conn, sock, raw):
                try:
                    if thing is not None:
                        thing.close()
                except Exception:  # noqa: BLE001
                    pass
        return self._vectors(parsed, len(texts))

    @staticmethod
    def _vectors(parsed, expected):
        data = parsed.get("data") if isinstance(parsed, dict) else None
        if not isinstance(data, list) or len(data) != expected:
            raise BackendError("unexpected embeddings response")
        rows = []
        for position, item in enumerate(data):
            vec = item.get("embedding") if isinstance(item, dict) else None
            index = item.get("index", position) if isinstance(item, dict) else position
            if (not isinstance(vec, list) or not vec or not isinstance(index, int) or isinstance(index, bool)
                    or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in vec)):
                raise BackendError("unexpected embeddings response")
            rows.append((index, [float(x) for x in vec]))
        rows.sort(key=lambda r: r[0])
        if [r[0] for r in rows] != list(range(expected)):
            raise BackendError("unexpected embeddings response")
        dims = {len(r[1]) for r in rows}
        if len(dims) != 1:
            raise BackendError("unexpected embeddings response")
        return [r[1] for r in rows]


@register("backend", "embedding")
class EmbeddingBackend(MemoryBackend):
    """Options: path (SQLite file for texts and vectors; parent directories are created;
    ":memory:" for tests), url (base URL of an OpenAI-compatible embeddings API, for example
    http://127.0.0.1:11434/v1), model, key (optional bearer key), writable (default True),
    roles (message roles remember() keeps, default ("user",)), timeout (socket timeout and
    default total budget of one call in seconds, default 10), batch (texts per embeddings
    request, default 32), floor and ceil (the cosine range mapped to scores 0..1), query_prefix and
    document_prefix (text put before every query / every stored text when it is embedded). floor, ceil
    and both prefixes default to the preset of the model (PRESETS), or to 0.40, 0.80 and no prefix with a
    warning for a model that has none. max_readers (read connections, default 8)."""

    tracks_writes = False  # a write is final as soon as remember() returns
    # Scores are floor/ceil-mapped relevance: with floor 0.40 and ceil 0.80 a min_score of 0.50 is a cosine
    # of 0.60 (bge-m3, chosen on bench/dev by the balanced rule of bench/dev_eval.py). These two class values
    # are the generic ones; every instance sets its own from the preset of its model (see __init__).
    default_min_score = GENERIC["min_score"]
    # The `relative_score` stage keeps candidates within this share of the best (mapped) score.
    default_relative_ratio = GENERIC["ratio"]
    # The "broad" profile of the `min_score` / `relative_score` stages (recall over precision); per model
    # below, from `bench/dev_eval.py --search --profile broad` (experiments: bench/results/dev/*-broad.md).
    broad_min_score = GENERIC["broad_min_score"]
    broad_relative_ratio = GENERIC["broad_ratio"]

    def __init__(self, path, url, model, key=None, writable=True, roles=("user",), timeout=10.0,
                 batch=32, floor=None, ceil=None, query_prefix=None, document_prefix=None,
                 max_readers=MAX_READERS):
        roles = as_roles(roles)
        if not isinstance(model, str) or not model:
            raise ValueError("model must be a non-empty string")
        if key is not None and not isinstance(key, str):
            raise ValueError("key must be a string")
        preset, known = preset_for(model)
        if not known and floor is None and ceil is None:
            self._warn_unknown(model)
        floor = preset["floor"] if floor is None else floor
        ceil = preset["ceil"] if ceil is None else ceil
        query_prefix = preset["query_prefix"] if query_prefix is None else query_prefix
        document_prefix = preset["document_prefix"] if document_prefix is None else document_prefix
        floor, ceil = float(floor), float(ceil)
        if not (math.isfinite(floor) and math.isfinite(ceil) and ceil > floor):
            raise ValueError("ceil must be greater than floor")
        timeout = float(timeout)
        if not timeout > 0:
            raise ValueError("timeout must be positive")
        for prefix in (query_prefix, document_prefix):
            if not isinstance(prefix, str) or len(prefix) > MAX_PREFIX:
                raise ValueError("a prefix must be a string of at most %d characters" % MAX_PREFIX)
        self.writable = bool(writable)
        self.roles = tuple(roles)
        self.floor, self.ceil = floor, ceil
        # The score lines of this model's scale (the class values are only the generic ones).
        self.default_min_score = preset["min_score"]
        self.default_relative_ratio = preset["ratio"]
        self.broad_min_score = preset["broad_min_score"]
        self.broad_relative_ratio = preset["broad_ratio"]
        self._timeout = timeout
        self._batch = max(1, int(batch))
        self._model = model
        self._query_prefix, self._doc_prefix = query_prefix, document_prefix
        self._http = _Http(url, model, key or None, timeout)
        self._path = str(path)
        self._shared = self._path == ":memory:"  # one connection for everything
        self._lock = threading.Lock()  # the writer (and, for ":memory:", every reader)
        self._closed = False
        self._dim = None
        # Scan cache: ref -> float32 unit vector, loaded on the first search, then kept in step with our
        # own writes. `_generation` is the write counter (see the module docstring) the cache is current at.
        # `_cache_lock` guards `_vectors` and `_generation` (held for a swap or a copy, never for a load);
        # `_load_lock` lets one search load at a time. Order: _lock, then _cache_lock; _load_lock is taken
        # alone (a ":memory:" load takes _lock inside it).
        self._vectors = None
        self._generation = None
        self._cache_lock = threading.Lock()
        self._load_lock = threading.Lock()
        self._qcache = collections.OrderedDict()
        self._qlock = threading.Lock()
        self._db = _sqlfile.open_store(self._path, _SCHEMA)
        try:
            self._check_model()
        except Exception:
            self._db.close()
            raise
        self._readers = None if self._shared else Readers(lambda: _sqlfile.open_reader(self._path), max_readers)

    @staticmethod
    def _warn_unknown(model):
        """One warning per model name and process: the numbers in use are a guess. No text, no keys."""
        if model in _warned:
            return
        _warned.add(model)
        _log.warning("embedding model %r has no preset: using floor 0.40, ceil 0.80, min_score 0.50 and "
                     "ratio 0.85 (bge-m3 numbers) and no prefixes; these are a guess for this model, calibrate "
                     "them with bench/dev_eval.py --search on your own data (docs/backends.md)", model)

    def _check_model(self):
        """Vectors of two models are not comparable: a store remembers the model that built it."""
        info = dict(self._db.execute("SELECT key, value FROM info").fetchall())
        if "model" in info and info["model"] != self._model:
            raise ValueError("this store was built with another embedding model")
        # The document prefix changes every stored vector too. A store from before this option has no
        # record: if it holds vectors they were made without a prefix. A store without vectors takes the
        # prefix it is opened with.
        if "dim" in info:
            if info.get("document_prefix", "") != self._doc_prefix:
                raise ValueError("this store was built with another document prefix "
                                 "(set document_prefix to the one it was built with)")
        if "dim" in info:
            self._dim = int(info["dim"])
        elif self.writable:
            self._remember_build()
            self._db.commit()

    def _remember_build(self):
        self._db.execute("INSERT OR REPLACE INTO info (key, value) VALUES ('model', ?)", (self._model,))
        self._db.execute("INSERT OR REPLACE INTO info (key, value) VALUES ('document_prefix', ?)", (self._doc_prefix,))

    def _bind_dim(self, dim):
        """Remember the vector size at the first write; a later vector of another size is refused."""
        if self._dim is None:
            self._remember_build()
            self._db.execute("INSERT OR REPLACE INTO info (key, value) VALUES ('dim', ?)", (str(dim),))
            self._dim = dim
        elif self._dim != dim:
            raise BackendError("embedding size differs from the store")

    # ------------------------------------------------------------ embedding

    def _embed(self, texts, total, ctl, prefix=""):
        """Unit vectors for `prefix + text` of each text. `total` is the whole budget in seconds; None
        gives every request the default budget (for writes, where the batch count is the caller's choice)."""
        if prefix:
            texts = [prefix + t for t in texts]
        out: list[Any] = []
        end = None if total is None else time.monotonic() + total
        for start in range(0, len(texts), self._batch):
            chunk = texts[start:start + self._batch]
            budget = self._timeout if end is None else end - time.monotonic()
            out.extend(_unit(v) for v in self._http.embed(chunk, budget, ctl))
        return out

    def _query_vector(self, query, total, ctl):
        with self._qlock:
            cached = self._qcache.get(query)
            if cached is not None:
                self._qcache.move_to_end(query)
                return cached
        vec = self._embed([query], total, ctl, self._query_prefix)[0]
        with self._qlock:
            self._qcache[query] = vec
            while len(self._qcache) > QUERY_CACHE:
                self._qcache.popitem(last=False)
        return vec

    # ------------------------------------------------------------ writing

    def put(self, text, ref=None, date=None, timeout=None, ctl=None):
        """Store one memory text as it is and return its ref (an existing ref is replaced)."""
        return self.put_many([(text, ref, date)], timeout=timeout, ctl=ctl)[0]

    def put_many(self, items, timeout=None, ctl=None):
        """Store several memories with batched embedding requests and return their refs.

        Each item is a text, a (text, ref, date) tuple (ref and date optional) or a dict with
        "text" and optionally "ref" and "date". All embeddings are fetched before anything is
        written, so a failing endpoint leaves the store unchanged."""
        require_writable(self)
        rows = [parse_item(i) for i in items]
        if not rows:
            return []
        vectors = self._embed([r[0] for r in rows], timeout, ctl, self._doc_prefix)
        self._store([(ref, text, date, vec) for (text, ref, date), vec in zip(rows, vectors)])
        return [r[1] for r in rows]

    def _store(self, rows):
        """Write `rows` ((ref, text, date, vector), at least one) in one transaction and bring the cache along."""
        now = int(time.time())
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")  # the file's write lock: the counter read below is exact
            try:
                self._bind_dim(len(rows[0][3]))
                before = _generation(self._db)
                self._db.execute("INSERT OR REPLACE INTO info (key, value) VALUES ('gen', ?)", (str(before + 1),))
                for ref, text, date, vec in rows:
                    self._db.execute(
                        "INSERT OR REPLACE INTO memories (ref, text, created_at, date, vector) VALUES (?, ?, ?, ?, ?)",
                        (ref, text, now, date, vec.tobytes()),
                    )
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise
            self._apply(before, [(ref, vec) for ref, _, _, vec in rows], ())

    def _apply(self, before, upserts, removed):
        """Bring the cache along with a write that took the counter from `before` to `before + 1`. A cache
        that was not at `before` (not loaded, or behind another writer) is left for the next search to reload."""
        with self._cache_lock:
            if self._vectors is not None and self._generation == before:
                for ref, vec in upserts:
                    self._vectors[ref] = vec
                for ref in removed:
                    self._vectors.pop(ref, None)
                self._generation = before + 1

    def delete(self, ref):
        """Remove one memory. True when it existed."""
        require_writable(self)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                before = _generation(self._db)
                cur = self._db.execute("DELETE FROM memories WHERE ref = ?", (ref,))
                deleted = cur.rowcount > 0
                if deleted:
                    self._db.execute("INSERT OR REPLACE INTO info (key, value) VALUES ('gen', ?)", (str(before + 1),))
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise
            if deleted:
                self._apply(before, (), (ref,))
            return deleted

    def remember(self, messages, timeout=None, ctl=None):
        """Keep the messages whose role is in `roles`. Returns a receipt id, or None when
        nothing matched (nothing was embedded or stored)."""
        require_writable(self)
        today = _today()
        kept = kept_messages(messages, self.roles, today)
        if not kept:
            return None
        receipt = new_receipt()
        texts = [content for _, content, _ in kept]
        if any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError("a memory text must be a non-empty string")
        vectors = self._embed(texts, timeout, ctl, self._doc_prefix)
        self._store([(receipt_ref(receipt, index), content, date, vec)
                     for (index, content, date), vec in zip(kept, vectors)])
        return receipt

    # ------------------------------------------------------------ reading

    @contextlib.contextmanager
    def _read(self, deadline=None):
        """A connection to read with, used by this thread alone until the block ends: a pooled reader, or
        for ":memory:" the one connection under the lock. Never waits for a write on a file database."""
        if deadline is None:
            deadline = time.monotonic() + DEFAULT_BUDGET
        if self._closed:
            raise BackendError("the store is closed")
        if self._shared:
            if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
                raise BackendError("the store is busy")
            try:
                yield self._db
            finally:
                self._lock.release()
            return
        assert self._readers is not None  # None only for the shared in-memory store, handled above
        conn = self._readers.acquire(deadline)
        try:
            yield conn
        finally:
            self._readers.release(conn)

    def count(self):
        return _sqlfile.count(self._read)

    def items(self):
        """Iterate (ref, text, date) over everything stored, oldest first."""
        return _sqlfile.items(self._read)

    def refs(self):
        """Iterate (ref, date or None) over every memory, ordered by ref, without texts or vectors.
        Read in batches by key with the connection released in between, so deleting while iterating is safe."""
        yield from _sqlfile.iter_refs(self._read, SCAN_BATCH)

    def receipt_refs(self, receipt):
        """The refs remember() stored for `receipt` (named "<receipt>-<index>"); a memory deleted since is absent."""
        return _sqlfile.receipt_refs(self._read, receipt)

    def read(self, ref, timeout=None, ctl=None):
        with self._read() as db:
            row = db.execute("SELECT text FROM memories WHERE ref = ?", (ref,)).fetchone()
        if row is None:
            raise KeyError(ref)
        return row[0]

    def _current(self, generation):
        """[(ref, vector)] from the cache when it is at `generation` or newer, else None."""
        with self._cache_lock:
            if self._vectors is not None and self._generation >= generation:
                return list(self._vectors.items())
        return None

    def _snapshot(self, deadline=None):
        """[(ref, vector)] of the whole store. Loads the cache on first use and again when another writer
        has written since (see the module docstring); the load runs on a reader connection, without the
        writer's lock, and one search loads while the others wait for it."""
        with self._read(deadline) as db:
            generation = _generation(db)
        found = self._current(generation)
        if found is not None:
            return found
        with self._load_lock:
            found = self._current(generation)  # the search that held the load lock did it
            if found is not None:
                return found
            with self._read(deadline) as db:
                db.execute("BEGIN")  # one snapshot for the counter and the vectors
                try:
                    loaded_at = _generation(db)
                    loaded = {ref: _blob_to_vector(blob)
                              for ref, blob in db.execute("SELECT ref, vector FROM memories")}
                finally:
                    db.rollback()
            with self._cache_lock:
                if self._closed:
                    raise BackendError("the store is closed")
                if self._vectors is None or loaded_at > self._generation:
                    self._vectors, self._generation = loaded, loaded_at
                return list(self._vectors.items())

    def _score(self, cosine):
        return clamp01((cosine - self.floor) / (self.ceil - self.floor))

    def find(self, query, limit, timeout=None, ctl=None):
        """Up to `limit` hits, best first; memories at or below `floor` cosine are left out.
        BackendError when the endpoint fails or the budget runs out (never a partial ranking)."""
        total = self._timeout if timeout is None else max(0.0, float(timeout))
        deadline = time.monotonic() + total
        if not isinstance(query, str) or not query.strip() or limit < 1:
            return []
        qvec = self._query_vector(query, total, ctl)
        snapshot = self._snapshot(deadline)
        if snapshot and len(snapshot[0][1]) != len(qvec):
            raise BackendError("embedding size differs from the store")
        scored = []
        for start in range(0, len(snapshot), SCORE_CHECK_EVERY):
            if time.monotonic() > deadline:
                raise BackendError("deadline exceeded")
            for ref, vec in snapshot[start:start + SCORE_CHECK_EVERY]:
                scored.append((_sumprod(qvec, vec), ref))
        return self._top_hits(scored, limit, deadline)

    def neighbours(self, ref, limit, timeout=None, ctl=None):
        """Up to `limit` memories most similar to the stored memory `ref`, best first, never `ref` itself.

        Works on the stored vectors only (no embeddings request): the same cosine and the same score mapping
        as find(), so a score here means what it means there; memories at or below `floor` cosine are left
        out. [] for a ref that is not stored. BackendError when the budget runs out or `ctl` was cancelled
        (never a partial ranking). Costs one pass over the stored vectors."""
        total = self._timeout if timeout is None else max(0.0, float(timeout))
        deadline = time.monotonic() + total
        if not isinstance(ref, str) or limit < 1:
            return []

        def check():
            if time.monotonic() >= deadline:
                raise BackendError("deadline exceeded")
            if getattr(ctl, "cancelled", False):
                raise BackendError("cancelled")

        check()
        snapshot = self._snapshot(deadline)
        own = next((vec for key, vec in snapshot if key == ref), None)
        if own is None:
            return []
        scored = []
        for start in range(0, len(snapshot), SCORE_CHECK_EVERY):
            check()
            for key, vec in snapshot[start:start + SCORE_CHECK_EVERY]:
                if key != ref and len(vec) == len(own):
                    scored.append((_sumprod(own, vec), key))
        return self._top_hits(scored, limit, deadline)

    def _top_hits(self, scored, limit, deadline=None):
        """The `limit` best of `scored` ([(cosine, ref)]) above the floor, as hits with their text."""
        best = [(c, r) for c, r in heapq.nlargest(limit, scored) if self._score(c) > 0.0]
        texts = self._texts([r for _, r in best], deadline)
        hits = []
        for cosine, ref in best:
            if ref in texts:  # deleted by another connection between scan and fetch: skip
                text, date = texts[ref]
                hits.append(make_hit(ref, self._score(cosine), text, date, {"cosine": cosine}))
        return hits

    def _texts(self, refs, deadline=None):
        out = {}
        with self._read(deadline) as db:
            for start in range(0, len(refs), SCAN_BATCH):
                chunk = refs[start:start + SCAN_BATCH]
                for ref, text, date in db.execute(
                    "SELECT ref, text, date FROM memories WHERE ref IN (%s)" % ",".join("?" * len(chunk)), chunk
                ):
                    out[ref] = (text, date)
        return out

    # ------------------------------------------------------------ lifecycle

    def health(self, timeout=2.0):
        """True when the store is open and readable. The embeddings endpoint is not called."""
        try:
            with self._read(time.monotonic() + max(0.0, float(timeout))) as db:
                return not self._closed and db.execute("SELECT 1").fetchone()[0] == 1
        except Exception:  # noqa: BLE001 - health never raises
            return False

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._db.close()
        with self._cache_lock:
            self._vectors = None
        if self._readers is not None:
            self._readers.close()
