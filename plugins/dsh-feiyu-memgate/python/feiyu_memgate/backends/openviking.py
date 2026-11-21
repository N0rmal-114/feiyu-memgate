"""OpenViking backend: one OpenViking account over HTTP. Standard library only.

OpenViking searches with a vector model and, on a write, lets a language model
extract memories from the messages in the background. Read-only unless built with
writable=True. The key only ever goes to base_url: no environment proxies, no
redirects.
"""
import datetime
import http.client
import json
import math
import re
import socket
import time
import urllib.parse
import uuid

from ..core.pool import _NoCtl
from ..errors import BackendError
from ..registry import register
from ..model import Hit
from .base import MemoryBackend, require_writable

MAX_RESPONSE = 1024 * 1024
TARGET_URI = "viking://~/memories"
URI_PREFIX = "viking://"
# Files OpenViking keeps next to the memories that are not memories themselves.
SKIP_NAMES = (".abstract.md", ".overview.md", "soul.md", "identity.md")
SESSION_PREFIX = "gw-"

_EVENT_DATE = re.compile(r"/events/(\d{4})/(\d{2})/(\d{2})/")

# Kept under its old name: other modules and tests raise and catch it as OVError.
OVError = BackendError

LIST_PAGE = 500  # entries per directory-listing page (directories count)
TREE_DEPTH = 10  # directory levels below the memories root that refs() walks (the server's default is 3)
MAX_LIST_PAGES = 200 # a listing that needs more pages than this is treated as broken
MAX_DIFF_ENTRIES = 1000  # directory entries read when looking for a session's memory diff

# Where a memory lives: the account's own memories directory, spelled with "~" or with the user id.
_MEMORY_URI = re.compile(r"^viking://(?:~|user/[^/]+)/memories/")
_ID_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class _Missing(BackendError):
    """The server answered 404 (only raised when the caller asked for it)."""


def _clean_uri(uri):
    """True for a URI string with no path tricks or line breaks."""
    return (isinstance(uri, str) and 0 < len(uri) < 1000 and "\n" not in uri and "\r" not in uri
            and ".." not in uri.split("/"))


def segments(uri):
    """Path segments of a viking:// URI, or None when it cannot be parsed."""
    if not isinstance(uri, str) or not uri.startswith(URI_PREFIX):
        return None
    parts = uri[len(URI_PREFIX):].split("/")
    return [p for p in parts if p] or None


def event_date(uri):
    """'YYYY-MM-DD' when the URI sits under events/YYYY/MM/DD/ and the date is real, else None."""
    if not isinstance(uri, str):
        return None
    m = _EVENT_DATE.search(uri)
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def _count(value):
    """value as a non-negative int count, or 0 for anything else (bool is not a count)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def extraction_state(status):
    """"saved", "empty" or None (not finished yet) for a session status."""
    if not isinstance(status, dict) or _count(status.get("commit_count")) < 1:
        return None
    extracted, usage = status.get("memories_extracted"), status.get("llm_token_usage")
    if isinstance(extracted, dict) and _count(extracted.get("total")) > 0:
        return "saved"
    if isinstance(usage, dict) and _count(usage.get("total_tokens")) > 0:
        return "empty"
    return None


@register("backend", "openviking")
class OpenVikingBackend(MemoryBackend):
    """One OpenViking account.

    Options: url (http://host[:port]), key, writable (default False), timeout (socket
    timeout in seconds, default 2.0), skip_names (file names that are never memories),
    floor and ceil (the raw score range mapped onto 0..1, defaults 0.5 and 1.0).

    Scores: OpenViking's `find` scores are high for every query (0.64 to 0.79 for the best
    memory of a question that memory cannot answer, 0.79 to 0.89 for a memory that can), so
    the raw value is no relevance. The backend maps it,
    `score = clamp((raw - floor) / (ceil - floor))`, and keeps the raw value in
    `Hit.meta["raw_score"]`. With the defaults, the gateway's default min_score of 0.5 means
    a raw score of 0.75 (the backend's own default is 0.55, raw 0.775).

    The defaults are model dependent: they were derived with bge-m3 (1024 dimensions) as
    OpenViking's embedding model, on the 46 memories / 30 questions of examples/eval only:
    all 22 answerable questions kept a right memory and 7 of 8 questions without an answer
    got nothing back (the eighth best wrong memory scored 0.787). That set is small and the
    separation between right and wrong memories is narrow (the best wrong memory of an
    answerable question reaches 0.84), so another embedding model or a much larger store needs
    the range re-derived: load a development set into a throw-away server, print the raw
    scores of right memories against the best wrong memory and against the unanswerable
    questions, and put floor and the wanted cut-off between them.
    """

    tracks_writes = True
    # With the default floor 0.5 / ceil 1.0 this is a raw score of 0.775. Both numbers were chosen on
    # bench/dev with bench/dev_eval.py (the balanced rule), bge-m3 inside OpenViking.
    default_min_score = 0.55
    default_relative_ratio = 0.95
    # The "broad" profile (bench/dev_eval.py --search --profile broad, on lists from a throw-away server).
    broad_min_score = 0.55
    broad_relative_ratio = 0.90

    def __init__(self, url, key, timeout=2.0, writable=False, skip_names=SKIP_NAMES, floor=0.5, ceil=1.0):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "http" or not parts.hostname:
            raise ValueError("url must be http://host[:port]")
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        self._host = parts.hostname
        self._port = parts.port or 80
        self._prefix = parts.path.rstrip("/")
        self._key = key
        self._timeout = timeout
        self.writable = bool(writable)
        self._skip = {name.lower() for name in skip_names}
        floor, ceil = float(floor), float(ceil)
        if not (math.isfinite(floor) and math.isfinite(ceil) and ceil > floor):
            raise ValueError("ceil must be greater than floor")
        self.floor, self.ceil = floor, ceil

    def _call(self, method, path, body=None, timeout=None, ctl=None, allow_404=False):
        """One HTTP call bounded by a wall-clock total (default 2x the socket timeout).

        `ctl` (a pool Task) lets another thread cancel the call by shutting its socket.
        With `allow_404` a 404 raises _Missing (a BackendError) instead of a plain failure.
        """
        ctl = ctl or _NoCtl()
        total = self._timeout * 2 if timeout is None else timeout
        if total <= 0:
            raise BackendError("deadline exceeded")
        deadline = time.monotonic() + total

        def left():
            return max(min(self._timeout, deadline - time.monotonic()), 0.01)

        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Authorization": "Bearer " + self._key, "Connection": "close"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        sock = None
        conn = None
        resp = None
        try:
            sock = socket.create_connection((self._host, self._port), timeout=left())
            ctl.attach(sock)
            conn = http.client.HTTPConnection(self._host, self._port)
            conn.sock = sock
            conn.request(method, self._prefix + path, body=data, headers=headers)
            sock.settimeout(left())
            resp = conn.getresponse()
            if resp.status == 404 and allow_404:
                raise _Missing("not found")
            if not 200 <= resp.status < 300:
                raise BackendError("upstream request failed")
            chunks, size = [], 0
            while not resp.isclosed():
                sock.settimeout(left())
                chunk = resp.read(8192)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE or time.monotonic() > deadline:
                    raise BackendError("response too large or too slow")
                chunks.append(chunk)
            parsed = json.loads(b"".join(chunks).decode("utf-8"))
        except BackendError:
            raise
        except Exception:  # noqa: BLE001 - any transport or parse failure becomes a BackendError, no upstream detail leaks
            raise BackendError("upstream request failed") from None
        finally:
            ctl.detach()
            if resp is not None:
                resp.close()
            if conn is not None:
                conn.close()
            if sock is not None:
                sock.close()
        if not isinstance(parsed, dict):
            raise BackendError("unexpected response")
        return parsed

    def health(self, timeout=2.0):
        """True when GET /health answers 200. Never raises."""
        try:
            conn = http.client.HTTPConnection(self._host, self._port, timeout=timeout)
            try:
                conn.request("GET", self._prefix + "/health", headers={"Connection": "close"})
                resp = conn.getresponse()
                resp.read(1024)
                return resp.status == 200
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            return False

    def session_status(self, session_id, timeout=None, ctl=None):
        parsed = self._call("GET", "/api/v1/sessions/" + urllib.parse.quote(session_id, safe=""), None, timeout, ctl)
        result = parsed.get("result")
        if not isinstance(result, dict):
            raise BackendError("unexpected response")
        return result

    def write_state(self, receipt, timeout=None):
        return extraction_state(self.session_status(receipt, timeout))

    def find_raw(self, query, limit, timeout=None, ctl=None):
        """[{"uri", "score", "abstract"}] as OpenViking returns them; malformed items among valid ones are skipped."""
        parsed = self._call(
            "POST",
            "/api/v1/search/find",
            {"query": query, "limit": limit, "target_uri": TARGET_URI},
            timeout,
            ctl,
        )
        result = parsed.get("result")
        memories = result.get("memories") if isinstance(result, dict) else None
        if not isinstance(memories, list):
            raise BackendError("unexpected response")
        out = []
        for m in memories:
            if not isinstance(m, dict):
                continue
            uri, score, abstract = m.get("uri"), m.get("score"), m.get("abstract")
            if not isinstance(uri, str) or isinstance(score, bool):
                continue
            if not isinstance(score, (int, float)) or not math.isfinite(score):
                continue
            out.append({"uri": uri, "score": float(score), "abstract": abstract if isinstance(abstract, str) else ""})
        if memories and not out:
            # Items came back but none is usable: the answer's shape changed, which is a failure.
            raise BackendError("unexpected response")
        return out

    def find(self, query, limit, timeout=None, ctl=None):
        """Hits for the memory files: an unparsable URI or a skipped file name is left out."""
        hits = []
        for item in self.find_raw(query, limit, timeout, ctl):
            parts = segments(item["uri"])
            if parts is None or parts[-1].lower() in self._skip:
                continue
            hits.append(Hit(ref=item["uri"], score=self._calibrate(item["score"]), text=item["abstract"],
                            date=event_date(item["uri"]), meta={"raw_score": item["score"]}))
        return hits

    def _calibrate(self, raw):
        """The raw find score mapped linearly from floor..ceil onto 0..1.

        The mapping must never change the ORDER of two hits: a raw score at or below the floor
        becomes a tiny positive number that still grows with the raw score, instead of a flat 0
        (a flat 0 made every weak hit a tie, and ties reorder a ranking).
        """
        if raw <= self.floor:
            return max(raw, 0.0) * 1e-9
        return min(1.0, (raw - self.floor) / (self.ceil - self.floor))

    def read(self, ref, timeout=None, ctl=None):
        parsed = self._call(
            "GET", "/api/v1/content/read?uri=" + urllib.parse.quote(ref, safe=""), None, timeout, ctl
        )
        result = parsed.get("result")
        if isinstance(result, dict):
            result = result.get("content")
        if not isinstance(result, str):
            raise BackendError("unexpected response")
        return result

    def remember(self, messages, timeout=None, ctl=None):
        """Submit messages in one session; `timeout` is the total budget for all calls.
        Returns the session id: OpenViking extracts memories from it in the background."""
        require_writable(self)
        end = None if timeout is None else time.monotonic() + timeout

        def left():
            return None if end is None else max(end - time.monotonic(), 0)

        session_id = SESSION_PREFIX + uuid.uuid4().hex
        self._call("POST", "/api/v1/sessions", {"session_id": session_id}, left(), ctl)
        for m in messages:
            self._call(
                "POST",
                "/api/v1/sessions/" + session_id + "/messages",
                {"role": m["role"], "content": m["content"]},
                left(),
                ctl,
            )
        self._call("POST", "/api/v1/sessions/" + session_id + "/commit", {}, left(), ctl)
        return session_id

    # ------------------------------------------------------------ managing (optional capabilities)

    def _is_memory_file(self, uri):
        """True for a file URI that is a memory: *.md, not hidden, not in skip_names."""
        parts = segments(uri)
        if parts is None or uri.endswith("/") or not _clean_uri(uri):
            return False
        name = parts[-1]
        return name.endswith(".md") and not name.startswith(".") and name.lower() not in self._skip

    def _own_memory_uri(self, ref):
        if not _clean_uri(ref) or _MEMORY_URI.match(ref) is None:
            raise ValueError("ref must be a memory URI")
        return ref

    def refs(self, timeout=None):
        """[(uri, date or None)] for every memory file, listed page by page with offset.

        Uses GET /api/v1/fs/tree (level_limit, node_limit, offset). The server's own
        /openapi.json offers `recursive` only on fs/ls, and there (v0.5.3) it lists
        directories and no files at all, so a walk over it looks fine and finds nothing.
        Files deeper than TREE_DEPTH levels below the memories root are not listed.

        Only URIs are kept, never texts. A URI that shows up twice means the server ignored
        offset, so the listing would be incomplete: that is an error, as is a listing that
        does not end within MAX_LIST_PAGES pages.
        """
        page_size = LIST_PAGE
        out, seen = [], set()
        for page in range(MAX_LIST_PAGES):
            query = urllib.parse.urlencode({
                "uri": TARGET_URI, "level_limit": TREE_DEPTH, "node_limit": page_size,
                "offset": page * page_size,
            })
            parsed = self._call("GET", "/api/v1/fs/tree?" + query, None, timeout)
            entries = parsed.get("result")
            if not isinstance(entries, list):
                raise BackendError("unexpected response")
            for entry in entries:
                uri = entry.get("uri") if isinstance(entry, dict) else None
                if isinstance(uri, str):
                    if uri in seen:
                        raise BackendError("listing is not paged (a uri repeated)")
                    seen.add(uri)
                if not isinstance(entry, dict) or entry.get("isDir") or entry.get("is_dir"):
                    continue
                if self._is_memory_file(uri):
                    out.append((uri, event_date(uri)))
            if len(entries) < page_size and parsed.get("has_more") is not True:
                return out
        raise BackendError("listing needs too many pages")

    def delete(self, ref, timeout=None):
        """True when the memory existed and is gone now; False when it was already gone."""
        require_writable(self)
        uri = self._own_memory_uri(ref)
        quoted = urllib.parse.quote(uri, safe="")
        # DELETE answers 200 for a path that is not there (it never says 404), and its
        # estimated_deleted_count counts index records, so it is 0 for a file written a moment
        # ago that is not indexed yet. Whether the file existed is therefore asked first.
        try:
            self._call("GET", "/api/v1/fs/stat?uri=" + quoted, None, timeout, allow_404=True)
        except _Missing:
            return False
        try:
            self._call("DELETE", "/api/v1/fs?uri=" + quoted, None, timeout, allow_404=True)
        except _Missing:
            return False  # removed by someone else between the two calls
        return True

    def put(self, text, ref=None, date=None, timeout=None):
        """Write one memory file as it is. `ref` (its URI) is required; `date` is ignored
        because this store dates a memory by where its URI sits."""
        require_writable(self)
        if ref is None:
            raise ValueError("ref is required for this backend")
        uri = self._own_memory_uri(ref)
        # mode "create" replaces an existing file on this server.
        self._call("POST", "/api/v1/content/write", {"uri": uri, "content": text, "mode": "create"}, timeout)
        return uri

    def receipt_refs(self, receipt, timeout=None):
        """URIs of the memory files the session `receipt` added or updated, or None when
        that is not known: no memory diff yet, more than one, or one that cannot be trusted.

        OpenViking records what a session changed in
        <session>/history/archive_NNN/memory_diff.json. The diff counts only when it is the
        session's single one, names its own archive, its totals add up, and every operation
        sits under the session owner's memories directory.
        """
        if not isinstance(receipt, str) or _ID_SAFE.match(receipt) is None:
            return None
        query = urllib.parse.urlencode({
            "uri": "viking://~/sessions/" + receipt, "recursive": "true", "simple": "true",
            "node_limit": MAX_DIFF_ENTRIES,
        })
        try:
            parsed = self._call("GET", "/api/v1/fs/ls?" + query, None, timeout, allow_404=True)
        except _Missing:
            return None
        listing = parsed.get("result")
        if not isinstance(listing, list):
            raise BackendError("unexpected response")
        if len(listing) >= MAX_DIFF_ENTRIES or parsed.get("has_more") is True:
            return None  # a listing this long was cut off: the single-diff check cannot be made
        pattern = re.compile(
            r"^viking://(~|user/[^/]+)/sessions/" + re.escape(receipt) + r"/history/archive_[0-9]+/memory_diff\.json$"
        )
        found: list[tuple[str, str]] = []
        for entry in listing:
            uri = entry.get("uri") if isinstance(entry, dict) else entry
            m = pattern.match(uri) if isinstance(uri, str) else None
            if m and isinstance(uri, str):
                found.append((uri, m.group(1)))
        if len(found) != 1:
            return None
        diff_uri, owner = found[0]
        raw = self.read(diff_uri, timeout)
        return self._diff_refs(raw, diff_uri.rsplit("/", 1)[0], "viking://%s/memories/" % owner)

    def _diff_refs(self, raw, archive_uri, owner_prefix):
        """Memory URIs added or updated by a diff, or None when its shape cannot be trusted."""
        try:
            data = json.loads(raw)
            if data.get("archive_uri") != archive_uri:
                return None
            operations = data["operations"]
            groups = [operations["adds"], operations["updates"], operations["deletes"]]
            if not all(isinstance(g, list) for g in groups):
                return None
            summary = data.get("summary") or {}
            totals = (summary.get("total_adds", len(groups[0])), summary.get("total_updates", len(groups[1])),
                      summary.get("total_deletes", len(groups[2])))
            if totals != tuple(len(g) for g in groups):
                return None
            uris = []
            for group in groups:
                for item in group:
                    uri = item["uri"]
                    if not _clean_uri(uri) or not uri.startswith(owner_prefix):
                        return None
                    uris.append(uri)
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            return None
        deleted = {item["uri"] for item in groups[2]}
        out = []
        for item in groups[0] + groups[1]:
            uri = item["uri"]
            # A file the same session also deleted is not there any more.
            if uri not in deleted and uri not in out and self._is_memory_file(uri):
                out.append(uri)
        return out
