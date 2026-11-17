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

Thread safety: one SQLite connection shared by all threads under a lock; HTTP calls use a
connection each and run outside the lock.
"""
import array
import collections
import heapq
import http.client
import json
import logging
import math
import os
import socket
import sqlite3
import ssl
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone

from ..core.pool import _NoCtl
from ..errors import BackendError
from ..model import Hit
from ..registry import register
from .base import MemoryBackend, message_date

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
GENERIC = {"floor": 0.40, "ceil": 0.80, "min_score": 0.50, "ratio": 0.85, "broad_min_score": 0.30,
           "broad_ratio": 0.60, "query_prefix": "", "document_prefix": ""}

_log = logging.getLogger("feiyu_memgate.backends.embedding")
_warned = set()  # model names already warned about in this process

DEFAULT_BUDGET = 10.0  # seconds for one find() when neither the caller nor the options say
MAX_RESPONSE = 16 * 1024 * 1024  # a batch of 32 vectors of 3072 floats is about 2 MB of JSON
QUERY_CACHE = 256
MAX_PREFIX = 512  # characters; the longest recommended instruction is about 100
SCAN_BATCH = 500
SCORE_CHECK_EVERY = 256  # vectors scored between two deadline checks

_HAS_META = "meta" in getattr(Hit, "__dataclass_fields__", {})

try:
    _sumprod = math.sumprod  # Python 3.12+: the dot product in C
except AttributeError:  # pragma: no cover - Python 3.11
    def _sumprod(a, b):
        return sum(x * y for x, y in zip(a, b))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    ref TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    date TEXT,
    vector BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def preset_for(model):
    """(values, known) for a model name: the matching row of PRESETS, or GENERIC with known False."""
    lowered = model.lower()
    for pattern, values in PRESETS:
        if pattern in lowered:
            return values, True
    return GENERIC, False


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _make_hit(ref, score, text, date, cosine):
    """A Hit, with the raw cosine in meta when this version of Hit has a meta field."""
    if _HAS_META:
        try:
            return Hit(ref=ref, score=score, text=text, date=date, meta={"cosine": cosine})
        except TypeError:  # pragma: no cover - a Hit whose meta has another shape
            pass
    return Hit(ref=ref, score=score, text=text, date=date)


def _unit(values):
    """`values` (floats) as an L2-normalised float32 array. BackendError for a zero or non-finite vector."""
    norm = math.sqrt(math.fsum(v * v for v in values))
    if not math.isfinite(norm) or norm == 0.0:
        raise BackendError("unusable embedding")
    return array.array("f", [v / norm for v in values])


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
    warning for a model that has none."""

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
                 batch=32, floor=None, ceil=None, query_prefix=None, document_prefix=None):
        if isinstance(roles, str):
            roles = (roles,)
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
        self._lock = threading.Lock()
        self._closed = False
        self._dim = None
        # Scan cache: ref -> float32 unit vector, loaded on the first search, then kept in step
        # with our own writes. PRAGMA data_version tells when another connection wrote.
        self._vectors = None
        self._data_version = None
        self._qcache = collections.OrderedDict()
        self._qlock = threading.Lock()
        if self._path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)
        self._db = sqlite3.connect(self._path, check_same_thread=False, timeout=10.0)
        try:
            if self._path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()
            self._check_model()
        except Exception:
            self._db.close()
            raise

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
        out = []
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

    def _require_writable(self):
        if not self.writable:
            raise PermissionError("this backend is read-only")

    def put(self, text, ref=None, date=None, timeout=None, ctl=None):
        """Store one memory text as it is and return its ref (an existing ref is replaced)."""
        return self.put_many([(text, ref, date)], timeout=timeout, ctl=ctl)[0]

    def put_many(self, items, timeout=None, ctl=None):
        """Store several memories with batched embedding requests and return their refs.

        Each item is a text, a (text, ref, date) tuple (ref and date optional) or a dict with
        "text" and optionally "ref" and "date". All embeddings are fetched before anything is
        written, so a failing endpoint leaves the store unchanged."""
        self._require_writable()
        rows = [self._item(i) for i in items]
        if not rows:
            return []
        vectors = self._embed([r[0] for r in rows], timeout, ctl, self._doc_prefix)
        now = int(time.time())
        refs = []
        with self._lock:
            self._bind_dim(len(vectors[0]))
            with self._db:
                for (text, ref, date), vec in zip(rows, vectors):
                    self._db.execute(
                        "INSERT OR REPLACE INTO memories (ref, text, created_at, date, vector) VALUES (?, ?, ?, ?, ?)",
                        (ref, text, now, date, vec.tobytes()),
                    )
                    refs.append(ref)
            if self._vectors is not None:
                for (text, ref, date), vec in zip(rows, vectors):
                    self._vectors[ref] = vec
        return refs

    @staticmethod
    def _item(item):
        if isinstance(item, str):
            item = (item,)
        if isinstance(item, dict):
            text, ref, date = item.get("text"), item.get("ref"), item.get("date")
        else:
            text, ref, date = (tuple(item) + (None, None))[:3]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("a memory text must be a non-empty string")
        return text, ref or "mem-" + uuid.uuid4().hex, date

    def delete(self, ref):
        """Remove one memory. True when it existed."""
        self._require_writable()
        with self._lock:
            with self._db:
                cur = self._db.execute("DELETE FROM memories WHERE ref = ?", (ref,))
            if self._vectors is not None:
                self._vectors.pop(ref, None)
            return cur.rowcount > 0

    def remember(self, messages, timeout=None, ctl=None):
        """Keep the messages whose role is in `roles`. Returns a receipt id, or None when
        nothing matched (nothing was embedded or stored)."""
        self._require_writable()
        today = _today()
        kept = [(i, m["content"], message_date(m, today)) for i, m in enumerate(messages) if m["role"] in self.roles]
        if not kept:
            return None
        receipt = "w-" + uuid.uuid4().hex
        texts = [content for _, content, _ in kept]
        if any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError("a memory text must be a non-empty string")
        vectors = self._embed(texts, timeout, ctl, self._doc_prefix)
        now = int(time.time())
        with self._lock:
            self._bind_dim(len(vectors[0]))
            with self._db:
                for (index, content, date), vec in zip(kept, vectors):
                    self._db.execute(
                        "INSERT OR REPLACE INTO memories (ref, text, created_at, date, vector) VALUES (?, ?, ?, ?, ?)",
                        ("%s-%d" % (receipt, index), content, now, date, vec.tobytes()),
                    )
            if self._vectors is not None:
                for (index, _, _), vec in zip(kept, vectors):
                    self._vectors["%s-%d" % (receipt, index)] = vec
        return receipt

    # ------------------------------------------------------------ reading

    def count(self):
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def items(self):
        """Iterate (ref, text, date) over everything stored, oldest first."""
        with self._lock:
            rows = self._db.execute("SELECT ref, text, date FROM memories ORDER BY created_at, ref").fetchall()
        return iter(rows)

    def refs(self):
        """Iterate (ref, date or None) over every memory, ordered by ref, without texts or vectors.
        Read in batches by key with the lock released in between, so deleting while iterating is safe."""
        last = None
        while True:
            with self._lock:
                if last is None:
                    rows = self._db.execute("SELECT ref, date FROM memories ORDER BY ref LIMIT ?",
                                            (SCAN_BATCH,)).fetchall()
                else:
                    rows = self._db.execute("SELECT ref, date FROM memories WHERE ref > ? ORDER BY ref LIMIT ?",
                                            (last, SCAN_BATCH)).fetchall()
            for row in rows:
                yield row[0], row[1]
            if len(rows) < SCAN_BATCH:
                return
            last = rows[-1][0]

    def receipt_refs(self, receipt):
        """The refs remember() stored for `receipt` (named "<receipt>-<index>"); a memory deleted since is absent."""
        if not isinstance(receipt, str) or not receipt:
            return None
        prefix = receipt + "-"
        with self._lock:
            rows = self._db.execute(
                "SELECT ref FROM memories WHERE substr(ref, 1, ?) = ? ORDER BY ref", (len(prefix), prefix)
            ).fetchall()
        return [r[0] for r in rows]

    def read(self, ref, timeout=None, ctl=None):
        with self._lock:
            row = self._db.execute("SELECT text FROM memories WHERE ref = ?", (ref,)).fetchone()
        if row is None:
            raise KeyError(ref)
        return row[0]

    def _snapshot(self):
        """[(ref, vector)] of the whole store. Loads the cache on first use and again when
        another connection has written since."""
        with self._lock:
            version = self._db.execute("PRAGMA data_version").fetchone()[0]
            if self._vectors is None or version != self._data_version:
                self._vectors = {
                    ref: _blob_to_vector(blob) for ref, blob in self._db.execute("SELECT ref, vector FROM memories")
                }
                self._data_version = version
            return list(self._vectors.items())

    def _score(self, cosine):
        return min(1.0, max(0.0, (cosine - self.floor) / (self.ceil - self.floor)))

    def find(self, query, limit, timeout=None, ctl=None):
        """Up to `limit` hits, best first; memories at or below `floor` cosine are left out.
        BackendError when the endpoint fails or the budget runs out (never a partial ranking)."""
        total = self._timeout if timeout is None else max(0.0, float(timeout))
        deadline = time.monotonic() + total
        if not isinstance(query, str) or not query.strip() or limit < 1:
            return []
        qvec = self._query_vector(query, total, ctl)
        snapshot = self._snapshot()
        if snapshot and len(snapshot[0][1]) != len(qvec):
            raise BackendError("embedding size differs from the store")
        scored = []
        for start in range(0, len(snapshot), SCORE_CHECK_EVERY):
            if time.monotonic() > deadline:
                raise BackendError("deadline exceeded")
            for ref, vec in snapshot[start:start + SCORE_CHECK_EVERY]:
                scored.append((_sumprod(qvec, vec), ref))
        best = [(c, r) for c, r in heapq.nlargest(limit, scored) if self._score(c) > 0.0]
        texts = self._texts([r for _, r in best])
        hits = []
        for cosine, ref in best:
            if ref in texts:  # deleted by another connection between scan and fetch: skip
                text, date = texts[ref]
                hits.append(_make_hit(ref, self._score(cosine), text, date, cosine))
        return hits

    def _texts(self, refs):
        out = {}
        with self._lock:
            for start in range(0, len(refs), SCAN_BATCH):
                chunk = refs[start:start + SCAN_BATCH]
                for ref, text, date in self._db.execute(
                    "SELECT ref, text, date FROM memories WHERE ref IN (%s)" % ",".join("?" * len(chunk)), chunk
                ):
                    out[ref] = (text, date)
        return out

    # ------------------------------------------------------------ lifecycle

    def health(self, timeout=2.0):
        """True when the store is open and readable. The embeddings endpoint is not called."""
        try:
            if not self._lock.acquire(timeout=max(0.0, float(timeout))):
                return False
            try:
                return not self._closed and self._db.execute("SELECT 1").fetchone()[0] == 1
            finally:
                self._lock.release()
        except Exception:  # noqa: BLE001 - health never raises
            return False

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._vectors = None
            self._db.close()
