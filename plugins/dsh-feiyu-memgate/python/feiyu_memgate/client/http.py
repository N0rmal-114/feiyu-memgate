"""Client for the memory gateway. Standard library only, never raises.

Blocking calls: a chat bot runs them in a worker thread (asyncio.to_thread or a
pool). Warnings log the failure kind only, never the query, memory text or token.

    client = MemoryClient("http://127.0.0.1:8080", "owner-token", tenant_token="tenant-token")
    detail = client.recall_detail("what did I say about tea?")        # the owner's stores
    status = client.remember_status("likes oolong", tenant_id="g1")   # one tenant's own store

A call with a tenant_id uses only the tenant token and always sends tenant_id; a call
without one uses only the main token and never sends it.

A program given one token minted for one tenant (a client with tenant_binding = "token")
says so with tenant_bound=True and has no main token; every call then uses that token and may
leave tenant_id out (the gateway takes the tenant from the token, and refuses a different one
with "tenant_mismatch"):

    client = MemoryClient("http://127.0.0.1:8080", None, tenant_token=minted, tenant_bound=True)
    client.recall("what did we plan for Saturday?")

Nothing is ever cut to fit: a query or a message over the gateway's limits is not sent
at all (recall gives None, remember "failed"). Error answers are told apart by their
stable "code", never by their text.
"""
import http.client
import json
import logging
import os
import time
import urllib.parse

# The gateway's own rule for a message's optional keys, one definition (a small file of pure checks:
# it loads the model and nothing of the gateway's running parts).
from ..model import REQUEST_FIELDS, clean_history, valid_request_field

RECALL_LIMIT = 8
MAX_QUERY_CHARS = 500       # gateway limit for /v1/recall
MAX_REMEMBER_CHARS = 4000   # gateway limit per message for /v1/remember
MAX_TOTAL_CHARS = 16000     # gateway limit for all messages of one /v1/remember
MAX_MESSAGES = 20           # gateway limit of messages per /v1/remember
MAX_TIME_CHARS = 40         # gateway limit of a message's optional "time"
MAX_SPEAKER_CHARS = 64      # gateway limit of a message's optional "speaker" and "about"
MAX_USED = 20               # gateway limit of items per /v1/used
USED_TIMEOUT = 5.0
REMEMBER_TIMEOUT = 12.0     # gateway's own deadline is 10s
MAX_RESPONSE_BYTES = 256 * 1024
MAX_ERROR_BYTES = 4096

# Outcomes of one POST. NOT_SENT and REJECTED are definite failures;
# UNSURE means the request may have reached the gateway and done its work.
OK, NOT_SENT, REJECTED, UNSURE = "ok", "not_sent", "rejected", "unsure"
# The day's write cap is used up: a definite failure, told apart.
LIMITED = "limited"

# Error codes that say for sure nothing was stored (besides every bad_* code).
DEFINITE_CODES = frozenset({"guard_error", "forbidden", "unknown_tenant", "tenant_mismatch", "too_large",
                            "unauthorized", "rate_limited", "busy", "length_required", "not_found",
                            "method_not_allowed"})


def _read_body(resp, sock, deadline, cap):
    """The reply body, at most `cap` bytes, within the deadline. Raises on timeout or overflow."""
    chunks = []
    size = 0
    while not resp.isclosed():
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("deadline")
        sock.settimeout(left)
        chunk = resp.read1(min(8192, cap + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > cap:
            raise ValueError("response too large")
    return b"".join(chunks)


def _error_code(resp, sock, deadline):
    """The "code" of an error reply, or None when the body is not such a JSON object
    (read at most MAX_ERROR_BYTES, within the deadline; any problem is None)."""
    try:
        data = json.loads(_read_body(resp, sock, deadline, MAX_ERROR_BYTES).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    code = data.get("code") if isinstance(data, dict) else None
    return code if isinstance(code, str) else None


def _failure(status, code):
    """The outcome of a non-200 answer, from its code; the status decides when there is none."""
    if code == "daily_limit":
        return LIMITED
    if code is not None and (code in DEFINITE_CODES or code.startswith("bad_")):
        return REJECTED
    return REJECTED if 400 <= status < 500 else UNSURE


def _speaker_ok(speaker):
    """True when `speaker` is absent or a string the gateway can take (non-empty, at most 64 characters)."""
    return speaker is None or (isinstance(speaker, str) and 0 < len(speaker) <= MAX_SPEAKER_CHARS)


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _clean_messages(messages):
    """The messages as the gateway takes them, or None when one breaks a gateway limit:
    nothing is ever truncated."""
    if not isinstance(messages, list) or not 0 < len(messages) <= MAX_MESSAGES:
        return None
    clean = []
    total = 0
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            return None
        content = m.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > MAX_REMEMBER_CHARS:
            return None
        total += len(content)
        if total > MAX_TOTAL_CHARS:
            return None
        item = {"role": m["role"], "content": content}
        for key, cap in (("time", MAX_TIME_CHARS), ("speaker", MAX_SPEAKER_CHARS), ("about", MAX_SPEAKER_CHARS)):
            value = m.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or len(value) > cap:
                return None
            item[key] = value
        for key in REQUEST_FIELDS:  # channel, origin, event_date, kind, audience: the gateway's own check
            value = m.get(key)
            if value is None:
                continue
            if not valid_request_field(key, value):
                return None
            item[key] = value
        clean.append(item)
    return clean


class MemoryClient:
    """One gateway, up to two tokens.

    base_url        http(s) address of the gateway; anything else leaves the client off
    token           the main client's token (calls without tenant_id)
    tenant_token    the tenant client's token (calls with tenant_id)
    logger          anything with .warning(fmt, *args); default logging "feiyu_memgate.client"
    recall_limit    how many results to ask for
    tenant_bound    True when tenant_token was minted for one tenant (tenant_binding = "token"):
                    every call uses it, with or without tenant_id. Such a client takes no main
                    token: given one, it stays off, so a call meant for the main client can never
                    land in the tenant's store
    """

    tenant_bound = False  # a class default too, for subclasses that build themselves

    def __init__(self, base_url, token, tenant_token=None, logger=None, recall_limit=RECALL_LIMIT,
                 tenant_bound=False):
        base = base_url.strip().rstrip("/") if isinstance(base_url, str) else ""
        if base and urllib.parse.urlsplit(base).scheme not in ("http", "https"):
            base = ""
        self.base_url = base
        self.token = token.strip() if isinstance(token, str) else ""
        self.tenant_token = tenant_token.strip() if isinstance(tenant_token, str) else ""
        self.log = logger if logger is not None else logging.getLogger("feiyu_memgate.client")
        self.recall_limit = recall_limit
        self.tenant_bound = bool(tenant_bound)
        if self.tenant_bound and self.token:
            self.log.warning("memory client off: a tenant-bound client takes no main token")
            self.base_url = ""

    @classmethod
    def from_env(cls, env=os.environ, prefix="FEIYU_MEMGATE", **options):
        """Read <prefix>_URL, <prefix>_TOKEN and <prefix>_TENANT_TOKEN."""
        return cls(env.get(prefix + "_URL", ""), env.get(prefix + "_TOKEN", ""),
                   env.get(prefix + "_TENANT_TOKEN", ""), **options)

    @property
    def configured(self):
        """True when calls without a tenant_id can be made (tenant-bound: for the bound tenant)."""
        return bool(self.base_url and (self.tenant_token if self.tenant_bound else self.token))

    @property
    def tenant_configured(self):
        """True when calls with a tenant_id can be made."""
        return bool(self.base_url and self.tenant_token)

    def _config(self, tenant):
        token = self.tenant_token if tenant or self.tenant_bound else self.token
        if not self.base_url or not token:
            return None
        return self.base_url, token

    def _post(self, path, payload, deadline, what, tenant_id=None):
        """POST JSON until an absolute monotonic deadline.

        Returns (decoded JSON object or None, outcome). http.client is used
        directly: it never reads proxy env vars and never follows redirects, and
        connect() is separate from request(), so the deadline is checked after any
        slow DNS/TCP step and right before anything is sent.
        """
        # A tenant call uses only the tenant token and always names the tenant; any
        # other call never carries tenant_id. There is no fallback between tokens: a
        # tenant-bound client has only the tenant token, so every call uses it.
        if tenant_id is not None:
            if not isinstance(tenant_id, (str, int)) or isinstance(tenant_id, bool) or not str(tenant_id).strip():
                return None, NOT_SENT
            payload = dict(payload, tenant_id=str(tenant_id).strip())
        cfg = self._config(tenant=tenant_id is not None)
        if cfg is None:
            return None, NOT_SENT
        base, token = cfg
        sent = False
        conn = None
        try:
            parts = urllib.parse.urlsplit(base)
            headers = {"Content-Type": "application/json", "Authorization": "Bearer " + token}
            for v in headers.values():
                v.encode("latin-1")  # fail before connecting on unusable header values
            # UTF-8 as it is: \uXXXX escapes would triple the size of Chinese text and push
            # a message that is within the limits over the gateway's 64 KiB body cap.
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
            conn = cls(parts.hostname, parts.port, timeout=max(deadline - time.monotonic(), 0.01))
            conn.connect()
            left = deadline - time.monotonic()
            if left <= 0:
                self.log.warning("memory %s not sent: deadline passed", what)
                return None, NOT_SENT
            sock = conn.sock  # conn.sock is reset to None once a response is will_close
            sock.settimeout(left)
            sent = True
            conn.request("POST", parts.path.rstrip("/") + path, body=body, headers=headers)
            resp = conn.getresponse()
            if resp.status != 200:
                code = _error_code(resp, sock, deadline)
                self.log.warning("memory %s failed: http %s %s", what, resp.status, code or "-")
                return None, _failure(resp.status, code)
            # The socket timeout is per operation; enforce a total deadline while
            # reading so a drip-feeding server cannot stretch it.
            data = json.loads(_read_body(resp, sock, deadline, MAX_RESPONSE_BYTES).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return data, OK
        except TimeoutError:
            self.log.warning("memory %s failed: timeout", what)
        except Exception as e:  # noqa: BLE001 - never raise to the caller; log the type only
            self.log.warning("memory %s failed: %s", what, type(e).__name__)
        finally:
            if conn is not None:
                conn.close()
        return None, (UNSURE if sent else NOT_SENT)

    def recall_detail(self, query, timeout=8.5, tenant_id=None, *, context=None, speaker=None, history=None):
        """The gateway's whole recall reply as {"results": [...], "more": int, "failed": int,
        "busy": int}, None on any failure (503 included). A query over 500 characters is
        not sent (None): shorten it yourself, as build_query does. `context` is an optional
        flat dict (say channel, chat) sent as it is; stages and guards may read it, the gateway
        never treats it as identity. `speaker` is who is asking, an account "<platform>:<id>" (at most 64
        characters), sent only when given. A gateway with an identity table resolves it (a speaker the
        client may not name is refused: None); without one it is ignored. `history` is the recent turns of the
        conversation, at most 6 of {"role": "user" or "assistant", "content": 1 to 500 characters, not blank}
        (other keys of an item are dropped), sent only when given; one that breaks the rule is not sent (None),
        like a query that is too long. It only reaches recall stages someone wrote: the built-in ones do not read it."""
        if not isinstance(query, str) or not query.strip():
            return None
        if len(query) > MAX_QUERY_CHARS:
            self.log.warning("memory recall not sent: query over %d characters", MAX_QUERY_CHARS)
            return None
        if not _speaker_ok(speaker):
            self.log.warning("memory recall not sent: bad speaker")
            return None
        turns = clean_history(history)
        if turns is None:
            self.log.warning("memory recall not sent: bad history")
            return None
        payload = {"query": query, "limit": self.recall_limit}
        if context is not None:
            payload["context"] = context
        if speaker is not None:
            payload["speaker"] = speaker
        if history is not None:
            payload["history"] = turns
        data, _ = self._post("/v1/recall", payload, time.monotonic() + timeout, "recall", tenant_id)
        results = data.get("results") if data else None
        if not isinstance(results, list):
            return None
        return {"results": [r for r in results if isinstance(r, dict) and isinstance(r.get("text"), str)],
                "more": _count(data.get("more")), "failed": _count(data.get("failed")),
                "busy": _count(data.get("busy"))}

    def explain(self, query, timeout=8.5, tenant_id=None, *, limit=None, context=None, speaker=None, history=None):
        """An explained recall (POST /v1/explain, only for a client configured with explain = true):
        the recall reply plus "stages" (the recall stages' names, in order) and "candidates" (every
        candidate the recall looked at, with its scores, outcome and the stage that removed it), as a
        dict; None on any failure (403 included). It counts as no use of a memory. `limit` defaults to
        the client's recall_limit; the other arguments (`speaker` and `history` too) are as for recall_detail."""
        if not isinstance(query, str) or not query.strip():
            return None
        if len(query) > MAX_QUERY_CHARS:
            self.log.warning("memory explain not sent: query over %d characters", MAX_QUERY_CHARS)
            return None
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
            return None
        if not _speaker_ok(speaker):
            self.log.warning("memory explain not sent: bad speaker")
            return None
        turns = clean_history(history)
        if turns is None:
            self.log.warning("memory explain not sent: bad history")
            return None
        payload = {"query": query, "limit": self.recall_limit if limit is None else limit}
        if context is not None:
            payload["context"] = context
        if speaker is not None:
            payload["speaker"] = speaker
        if history is not None:
            payload["history"] = turns
        data, _ = self._post("/v1/explain", payload, time.monotonic() + timeout, "explain", tenant_id)
        results, stages, candidates = (data.get(k) if data else None for k in ("results", "stages", "candidates"))
        if not isinstance(results, list) or not isinstance(stages, list) or not isinstance(candidates, list):
            return None
        out = {"results": [r for r in results if isinstance(r, dict) and isinstance(r.get("text"), str)],
               "more": _count(data.get("more")), "failed": _count(data.get("failed")),
               "busy": _count(data.get("busy")), "stages": [s for s in stages if isinstance(s, str)],
               "candidates": [c for c in candidates if isinstance(c, dict)]}
        if data.get("candidates_cut") is True:
            out["candidates_cut"] = True
        return out

    def recall(self, query, timeout=8.5, tenant_id=None, *, context=None, speaker=None, history=None):
        """Return a list of {"id","source","text","score"} dicts (plus "date" when the memory has one),
        [] on any failure."""
        detail = self.recall_detail(query, timeout, tenant_id, context=context, speaker=speaker, history=history)
        return detail["results"] if detail else []

    def remember_messages(self, messages, deadline=None, tenant_id=None, *, context=None):
        """Store a list of {"role": "user"|"assistant", "content": str} messages (optional
        "time", "speaker", "about", "channel", "origin", "event_date", "kind" and "audience" strings, each
        checked by the gateway's own rule; a null counts as absent). Returns the same words as remember_status. A list
        that breaks a gateway limit (count, length, shape) is "failed" and nothing is sent.
        `context` is an optional flat dict, sent as it is (see recall_detail)."""
        clean = _clean_messages(messages)
        if clean is None:
            self.log.warning("memory remember not sent: messages break the gateway's limits")
            return "failed"
        if deadline is None:
            deadline = time.monotonic() + REMEMBER_TIMEOUT
        payload = {"messages": clean}
        if context is not None:
            payload["context"] = context
        data, outcome = self._post("/v1/remember", payload, deadline, "remember", tenant_id)
        if outcome == OK:
            status = data.get("status")
            return {"ok": "saved", "skipped": "skipped"}.get(status, "unsure")
        return {UNSURE: "unsure", LIMITED: "limited"}.get(outcome, "failed")

    def remember_status(self, text, deadline=None, tenant_id=None, *, context=None, speaker=None, origin=None,
                        kind=None, event_date=None):
        """Store one user-side memory. Returns "saved", "skipped" (the gateway looked at it
        and kept nothing: a guard or the store filtered it out), "failed" (definitely not
        stored), "limited" (the day's cap is used up; definitely not stored) or "unsure"
        (sent, but no confirmation; it may have been stored).

        deadline is an absolute time.monotonic() value; nothing is sent after it. `speaker`, `origin`,
        `kind` and `event_date` go on that one message when given (see remember_messages); one that
        breaks the gateway's rule makes the call "failed".
        """
        if not isinstance(text, str) or not text.strip():
            return "failed"
        message = {"role": "user", "content": text, "speaker": speaker, "origin": origin, "kind": kind,
                   "event_date": event_date}
        return self.remember_messages([message], deadline, tenant_id, context=context)

    def remember(self, text, tenant_id=None, *, context=None, speaker=None, origin=None, kind=None,
                 event_date=None):
        """True when the gateway confirmed the write."""
        return self.remember_status(text, tenant_id=tenant_id, context=context, speaker=speaker, origin=origin,
                                    kind=kind, event_date=event_date) == "saved"

    def report_used(self, items, tenant_id=None, timeout=USED_TIMEOUT):
        """Tell the gateway which recalled memories the reply really relied on, so only those
        count as used (optional: the gateway also credits the best recalled memory by itself).
        `items` are recall results, or any dicts with "source" and "id" (at most 20; other
        keys are not sent). True when the gateway confirmed it, False on any failure; a bad
        list is not sent. Never raises."""
        if not isinstance(items, (list, tuple)) or not 0 < len(items) <= MAX_USED:
            self.log.warning("memory used-report not sent: need 1 to %d items", MAX_USED)
            return False
        clean = []
        for item in items:
            source, ref = (item.get("source"), item.get("id")) if isinstance(item, dict) else (None, None)
            if not isinstance(source, str) or not source or not isinstance(ref, str) or not ref:
                self.log.warning("memory used-report not sent: an item has no source or id")
                return False
            clean.append({"source": source, "id": ref})
        data, outcome = self._post("/v1/used", {"used": clean}, time.monotonic() + timeout, "used", tenant_id)
        return outcome == OK and data.get("status") == "ok"
