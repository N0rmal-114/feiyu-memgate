"""The backend slot: where memories live and how they are searched.

A backend is one store of memories (one account, one database file, one index).
The gateway never assumes how it searches: a vector index, a keyword index and a
plain list are all fine, as long as find() returns scores in 0..1.

Standard library only.
"""
import abc
import datetime
import uuid

from ..model import Hit

DEFAULT_BUDGET = 10.0  # seconds for one find() when the caller gives none (the backends that use it)

HAS_META = "meta" in getattr(Hit, "__dataclass_fields__", {})


def message_date(message, default=None):
    """The date ("YYYY-MM-DD") a message says it was written, else `default`.

    Taken from the optional `time` field when it is a string that starts with a real calendar date
    in YYYY-MM-DD form, followed by nothing, "T" or a space ("2026-11-17", "2026-11-17T08:30:00+08:00",
    "2026-11-17 08:30"). Anything else gives `default`. Never raises.
    """
    if not isinstance(message, dict):
        return default
    value = message.get("time")
    if not isinstance(value, str) or len(value) < 10 or (len(value) > 10 and value[10] not in "T "):
        return default
    head = value[:10]
    if not (head[:4].isdigit() and head[4] == "-" and head[5:7].isdigit() and head[7] == "-" and head[8:].isdigit()):
        return default
    try:
        datetime.date.fromisoformat(head)
    except ValueError:
        return default
    return head


def today():
    """Today's date in UTC as "YYYY-MM-DD"."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def make_hit(ref, score, text, date, meta):
    """A Hit, with `meta` (a dict) when this version of Hit has a meta field."""
    if HAS_META:
        try:
            return Hit(ref=ref, score=score, text=text, date=date, meta=meta)
        except TypeError:  # pragma: no cover - a Hit whose meta has another shape
            pass
    return Hit(ref=ref, score=score, text=text, date=date)


def require_writable(backend):
    """PermissionError unless `backend.writable` is true."""
    if not backend.writable:
        raise PermissionError("this backend is read-only")


def as_roles(roles):
    """`roles` as given, except that one role name (a string) becomes a one-element tuple."""
    if isinstance(roles, str):
        roles = (roles,)
    return roles


def clamp01(value):
    """`value` held to the range 0..1."""
    return min(1.0, max(0.0, value))


def new_ref():
    """A fresh ref for a memory stored without one."""
    return "mem-" + uuid.uuid4().hex


def new_receipt():
    """A fresh receipt id for one write."""
    return "w-" + uuid.uuid4().hex


def receipt_ref(receipt, index):
    """The ref of message number `index` of the write `receipt`."""
    return "%s-%d" % (receipt, index)


def kept_messages(messages, roles, today):
    """[(index, content, date)] of the messages whose role is in `roles`; a message without a date of
    its own is dated `today`."""
    return [(i, m["content"], message_date(m, today)) for i, m in enumerate(messages) if m["role"] in roles]


def parse_item(item):
    """(text, ref, date) of one item given to put_many(): a text, a (text, ref, date) tuple (ref and
    date optional) or a dict with "text" and optionally "ref" and "date". The ref is made when missing.
    ValueError when the text is not a non-empty string."""
    if isinstance(item, str):
        item = (item,)
    if isinstance(item, dict):
        text, ref, date = item.get("text"), item.get("ref"), item.get("date")
    else:
        text, ref, date = (tuple(item) + (None, None))[:3]
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a memory text must be a non-empty string")
    return text, ref or new_ref(), date


class MemoryBackend(abc.ABC):
    """Subclass this (or just provide the same methods) and register it:

        @register("backend", "mine")
        class MyBackend(MemoryBackend):
            ...

    Every call takes `timeout` (seconds, the total budget for the call; None means
    the backend's own default) and `ctl`. `ctl` is a cancellation handle: a backend
    that opens a socket calls ctl.attach(sock) right after connecting and
    ctl.detach() when done, so the gateway can cut a call that ran out of time.
    ctl also has `cancelled` (bool, True once the caller gave up) and `on_cancel(fn)` (calls fn
    from the cancelling thread); a backend that works in-process (a database query) checks them
    to stop early. Read them with getattr: ctl may be None or lack them. A backend without
    sockets and without long work ignores ctl.

    Score lines: `default_min_score` and `default_relative_ratio` (optional) are the lines of the "precise"
    profile; the `default_` prefix is historical, it does not mean the default profile. `broad_min_score` and
    `broad_relative_ratio` are those of "broad", which is the default profile of the stages. A backend that
    declares no broad line keeps its `default_` line under either profile.

    Failures raise BackendError (or any exception): the gateway counts the store
    as failed for this request and goes on with the others. Error messages must not
    contain memory text or keys.

    Optional capabilities (not abstract; a backend without them simply cannot be managed
    by forgetting, and the caller checks with hasattr):

        refs()                          -> iterable of (ref, date or None) for EVERY memory
                                           in the store; date is "YYYY-MM-DD" when known.
                                           Must not load all texts into memory.
        delete(ref)                     -> True when the memory existed and is gone now.
                                           Needs writable=True (PermissionError otherwise).
        put(text, ref=None, date=None)  -> ref. Stores one memory text as it is (creates or
                                           replaces). Needs writable=True.
        receipt_refs(receipt)           -> list of the refs the write `receipt` produced,
                                           or None when not known (yet) or not supported.
        neighbours(ref, limit, timeout=None, ctl=None)
                                        -> the `limit` memories most similar to the stored memory
                                           `ref`, best first, as Hit objects whose `score` is on the
                                           backend's own 0..1 relevance scale (the scale find() uses),
                                           never including `ref` itself; [] when `ref` is unknown.
                                           It takes the same `timeout` and `ctl` as find() and raises
                                           (BackendError) rather than answer partly. The `linked` recall
                                           stage uses it to bring back the memories connected with the
                                           best ones; a backend without it is simply left out of that.
    """

    # False makes remember() refuse; the gateway also checks it before writing.
    writable = False
    # True when write_state() can tell what became of a write. The gateway then
    # follows each write for a few minutes and reports the outcome in /healthz?deep=1.
    tracks_writes = False
    # The min_score a recall should use for this backend when the policy names none (the gateway's
    # `min_score` stage reads it). The `default_` prefix is historical: it names the line of the "precise"
    # profile, not the default profile (the default profile is "broad", see broad_* below). Scores of different backends mean different things (keyword
    # overlap, semantic similarity), so a backend states a sensible floor for its own scale.
    # Optional: a backend that does not set it gets the gateway's own default.
    default_min_score = None
    # The recall-leaning counterparts, used by the `min_score` / `relative_score` stages when their option
    # `profile` is "broad", which is the stages' default (they trade precision and silence for more answered
    # questions). None means this backend has no distinct broad setting: the stages then use the default_ line
    # above under either profile.
    broad_min_score = None
    broad_relative_ratio = None

    @abc.abstractmethod
    def find(self, query, limit, timeout=None, ctl=None):
        """Up to `limit` Hit objects for `query`, any order."""

    def read(self, ref, timeout=None, ctl=None):
        """The full text of one memory. Only called for a hit whose text was empty."""
        raise NotImplementedError

    def remember(self, messages, timeout=None, ctl=None):
        """Store `messages` ([{"role": "user" | "assistant", "content": str}]).

        Returns a receipt id (a string) for this write. What "store" means is up to
        the backend: keep the text as it is, or hand it to a model that extracts facts.
        Returns None when nothing was kept (no message matched the roles it keeps).
        """
        raise PermissionError("this backend is read-only")

    def health(self, timeout=2.0):
        """True when the backend can answer. Never raises."""
        return True

    def write_state(self, receipt, timeout=None):
        """What became of the write `receipt`: "saved", "empty" (processed, nothing
        kept) or None (not finished yet). Only used when tracks_writes is True."""
        return "saved"

    def close(self):
        """Release what the backend holds (files, connections)."""
