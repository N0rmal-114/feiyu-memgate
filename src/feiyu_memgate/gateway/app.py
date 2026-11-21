"""The gateway: the one place that holds backend keys and decides who may read or write what.

It can be used in two ways: behind the HTTP server (gateway/server.py), or in the
same process, by calling admit(), recall() and remember() directly. Either way a
caller is a named client with a ClientPolicy (plus, for a client whose tokens are bound
to a tenant, the tenant its token names: see identify()), and every answer is a Reply
(status, body, note) with the HTTP meaning of `status`. recall() and remember() never
raise; an error body is always {"error": <human text>, "code": <stable code>}
(the codes are listed in docs/api.md).

Standard library only.
"""
import base64
import collections
import hashlib
import hmac
import math
import re
import struct
import sys
import threading
import time

from .. import registry
from ..core import limits, stages as stages_module, write as write_module
from ..core.pool import POOL_SIZE, Pool
from ..core.recall import BUSY, OK, RECALL_DEADLINE, MAX_WAIT, RecallContext, StageError, call, piece_name, recall
from ..core.watch import ExtractionWatch, RecallFailures
from ..errors import ConfigError, WriteRejected
from ..model import AUDIENCES, TENANT_BINDINGS, TENANT_STORE

DEFAULT_LIMIT = 5
MAX_LIMIT = 10
MAX_QUERY_CHARS = 500
MAX_MESSAGES = 20
MAX_MESSAGE_CHARS = 4000
MAX_TOTAL_CHARS = 16000
MAX_TIME_CHARS = 40
MAX_SPEAKER_CHARS = 64
MAX_CONTEXT_KEYS = 16
MAX_CONTEXT_KEY_CHARS = 64
MAX_CONTEXT_VALUE_CHARS = 200
MAX_USED = 20
MAX_USED_SOURCE_CHARS = 128
MAX_USED_ID_CHARS = 512
USED_DEADLINE = 3.0
COLD_RECALL_DEADLINE = 8.0
COLD_AFTER = 600.0  # a local embedding model is often unloaded after 10 idle minutes
REMEMBER_DEADLINE = 10.0
TENANT_PER_TENANT = 2
# Tokens shorter than this, or the placeholder of the example configuration, are not loaded,
# so a hand-copied example never guards a route with a public string.
MIN_TOKEN_CHARS = 32
PLACEHOLDER_TOKEN = "change-me"

Reply = collections.namedtuple("Reply", "status body note", defaults=("",))


def error_reply(status, code, text, note=""):
    """An error answer: a human text and a stable code clients match on."""
    return Reply(status, {"error": text, "code": code}, note)


def stderr_log(line):
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def usable_token(value):
    """value when it can guard a route, "" when it is missing, the placeholder or shorter than MIN_TOKEN_CHARS."""
    if not isinstance(value, str) or value in ("", PLACEHOLDER_TOKEN) or len(value) < MIN_TOKEN_CHARS:
        return ""
    return value


# ---------------------------------------------------------------- tenant-bound tokens
#
# A client with tenant_binding = "token" holds a minting secret that never authenticates a
# request by itself. A request token is
#
#     fmg1.<base64url(tenant id, UTF-8), no padding>.<base64url(HMAC-SHA256(secret, context)), no padding>
#
# where context frames, each as a 4-byte big-endian length followed by the bytes: TENANT_TOKEN_LABEL,
# the client name (UTF-8), the client's tenants provider name (UTF-8), the token generation (decimal
# ASCII) and the tenant id (UTF-8). The length prefixes make the framing unambiguous; the label keeps
# these MACs apart from any other use of the secret; the client name keeps one client's tokens from
# authenticating as another's; the provider name keeps a token from following its client when the
# client is pointed at another provider (the same id there may be someone else); the generation lets
# the operator void every token of a client at once. Verification needs no state. The tenant id is
# encoded, not encrypted: whoever holds a token can read which tenant it names.
#
# Anyone holding one token can test guesses of the minting secret offline, at no cost to the gateway,
# so the secret must be random (secrets.token_urlsafe(32)), never a phrase: see minting_secret_looks_weak().

TENANT_TOKEN_PREFIX = "fmg1"
TENANT_TOKEN_LABEL = b"feiyu-memgate tenant token v1"
MAX_TENANT_ID_BYTES = 256
_MAC_BYTES = 32  # HMAC-SHA256


def _b64_chars(n):
    """Characters of n bytes in unpadded base64url."""
    return (4 * n + 2) // 3


MAX_TENANT_TOKEN_CHARS = len(TENANT_TOKEN_PREFIX) + 1 + _b64_chars(MAX_TENANT_ID_BYTES) + 1 + _b64_chars(_MAC_BYTES)
_B64URL = re.compile(r"[A-Za-z0-9_-]+")


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text):
    """The bytes of unpadded base64url text written in its one canonical spelling, else None.
    Padding, the standard alphabet, whitespace and non-zero unused trailing bits are all refused, so
    a token has exactly one accepted spelling."""
    if not text or not text.isascii() or len(text) % 4 == 1 or _B64URL.fullmatch(text) is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:  # binascii.Error
        return None
    return raw if _b64(raw) == text else None


def _secret_bytes(secret):
    # As the presented plain tokens are compared: an odd environment value still has one byte form.
    return secret.encode("utf-8", "surrogateescape")


def tenant_token_mac(secret, client, provider, generation, tenant_id):
    """HMAC-SHA256 of one (client, tenants provider, generation, tenant) under a client's minting secret."""
    fields = (TENANT_TOKEN_LABEL, client.encode("utf-8"), provider.encode("utf-8"), str(generation).encode("ascii"),
              tenant_id.encode("utf-8"))
    context = b"".join(struct.pack(">I", len(field)) + field for field in fields)
    return hmac.new(_secret_bytes(secret), context, hashlib.sha256).digest()


def mint_tenant_token(secret, client, provider, generation, tenant_id):
    """The request token of `client` (whose tenants provider is `provider`) bound to `tenant_id`.
    Raises ValueError for an empty tenant id or one over MAX_TENANT_ID_BYTES bytes of UTF-8 (the
    message holds no secret)."""
    if not isinstance(tenant_id, str):
        raise ValueError("the tenant id must be text")
    raw = tenant_id.encode("utf-8")  # strict: a lone surrogate has no UTF-8 form
    if not 0 < len(raw) <= MAX_TENANT_ID_BYTES:
        raise ValueError("the tenant id must be 1 to %d bytes of UTF-8" % MAX_TENANT_ID_BYTES)
    mac = tenant_token_mac(secret, client, provider, generation, tenant_id)
    return "%s.%s.%s" % (TENANT_TOKEN_PREFIX, _b64(raw), _b64(mac))


# A heuristic, not a measure of entropy: secrets.token_urlsafe(32) gives 43 characters drawn from 64,
# so a shorter secret, or one built from few distinct characters, was probably not made that way.
RANDOM_SECRET_CHARS = 43
RANDOM_SECRET_DISTINCT = 16


def minting_secret_looks_weak(secret):
    """True when a minting secret does not look randomly generated (shorter than 43 characters or
    fewer than 16 distinct ones). A token lets its holder guess the secret offline, unlike a plain
    token, which can only be guessed against the gateway and its rate limit."""
    return len(secret) < RANDOM_SECRET_CHARS or len(set(secret)) < RANDOM_SECRET_DISTINCT


def parse_tenant_token(presented):
    """(tenant id, MAC bytes) of a token in the tenant-bound format, else None. Only the public
    structure is checked here, its length first; the MAC is checked by the gateway, which knows the
    secrets, and the tenant id by the client's provider."""
    if not isinstance(presented, str) or len(presented) > MAX_TENANT_TOKEN_CHARS:
        return None
    parts = presented.split(".")
    if len(parts) != 3 or parts[0] != TENANT_TOKEN_PREFIX:
        return None
    raw, mac = _unb64(parts[1]), _unb64(parts[2])
    if raw is None or mac is None or len(mac) != _MAC_BYTES or not 0 < len(raw) <= MAX_TENANT_ID_BYTES:
        return None
    try:
        return raw.decode("utf-8"), mac  # strict: one tenant id, one byte form
    except UnicodeDecodeError:
        return None


def _audience_choices():
    return ", ".join('"%s"' % level for level in AUDIENCES)


def default_stages():
    return [registry.create("recall_stage", name) for name in stages_module.DEFAULT_STAGES]


def default_guards():
    return [registry.create("write_guard", name) for name in write_module.DEFAULT_GUARDS]


def _join(notes):
    """The access-log note of a request: its distinct words, in order, as one token."""
    seen = []
    for word in notes:
        if word and word not in seen:
            seen.append(word)
    return ",".join(seen)


def _valid_context(value):
    """The request's "context" object as a dict ({} when absent), or None when it breaks
    the rules: at most MAX_CONTEXT_KEYS keys, each value a short string, a number or a boolean."""
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_CONTEXT_KEYS:
        return None
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > MAX_CONTEXT_KEY_CHARS:
            return None
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            if isinstance(item, float) and not math.isfinite(item):
                return None
            continue
        if not isinstance(item, str) or len(item) > MAX_CONTEXT_VALUE_CHARS:
            return None
    return dict(value)


def _valid_used(items):
    """The "used" list as [(source, id)], or None when it breaks the rules: a list of at most
    MAX_USED objects, each with a non-empty string "source" and "id" of sane length. An empty
    list is fine (nothing used). Other keys are ignored, so a recall result can be passed as it is."""
    if not isinstance(items, list) or len(items) > MAX_USED:
        return None
    clean = []
    for item in items:
        if not isinstance(item, dict):
            return None
        source, ref = item.get("source"), item.get("id")
        if not isinstance(source, str) or not source or len(source) > MAX_USED_SOURCE_CHARS:
            return None
        if not isinstance(ref, str) or not ref or len(ref) > MAX_USED_ID_CHARS:
            return None
        clean.append((source, ref))
    return clean


def _valid_messages(messages):
    """The messages as [{"role", "content", optional "time", "speaker"}], or None when the
    shape is wrong. Over-long content is rejected, not truncated; other keys are dropped."""
    if not isinstance(messages, list) or not 0 < len(messages) <= MAX_MESSAGES:
        return None
    clean = []
    total = 0
    for m in messages:
        if not isinstance(m, dict):
            return None
        role, content = m.get("role"), m.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            return None
        if not content.strip() or len(content) > MAX_MESSAGE_CHARS:
            return None
        total += len(content)
        if total > MAX_TOTAL_CHARS:
            return None
        item = {"role": role, "content": content}
        for key, cap in (("time", MAX_TIME_CHARS), ("speaker", MAX_SPEAKER_CHARS)):
            value = m.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or len(value) > cap:
                return None
            item[key] = value
        clean.append(item)
    return clean


class _BadMessages(Exception):
    """A guard handed back something the backends cannot take (a forgotten return included)."""


def _check_shape(messages):
    if not isinstance(messages, list) or not all(
            isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
            for m in messages):
        raise _BadMessages()
    return messages


class Gateway:
    """stores    name -> backend (the shared stores)
    clients   name -> ClientPolicy
    tokens    name -> token; a client without a usable token cannot authenticate. For a client
              with tenant_binding = "token" this is its minting secret, which never authenticates
              by itself: only the tokens minted from it do (mint_tenant_token)
    tenants   name -> TenantProvider
    stages    the recall stages, in order (default: the built-in set)
    guards    the write guards, in order (default: redact, daily_cap)
    redactors {"private": fn, "public": fn}: the text cleaner for private and public clients
    audiences   {name: level}: who each store or tenants provider is for, "owner" (default for a
                store), "group" (default for a tenants provider) or "public"; see audience_of
    forgetting  an optional forgetting.Forgetting: ranks older memories lower and, every
                sweep_every_hours hours (0 = never), archives and removes the forgotten ones

    Workers: pool_size calls to the stores at once; tenant_pool_total tenant requests in
    flight (default pool_size - 2), tenant_pool_per_tenant per tenant (default 2), and
    the store calls of tenant requests never take the last pool_size - tenant_pool_total
    workers; store_max_inflight calls in flight per store (default pool_size - 2).
    """

    def __init__(self, stores, clients, tokens, *, tenants=None, stages=None, guards=None, redactors=None,
                 clock=time.monotonic, log=stderr_log, recall_deadline=RECALL_DEADLINE,
                 cold_recall_deadline=None, cold_after=COLD_AFTER, remember_deadline=REMEMBER_DEADLINE,
                 pool_size=POOL_SIZE, tenant_pool_total=None, tenant_pool_per_tenant=TENANT_PER_TENANT,
                 store_max_inflight=None, forgetting=None, sweep_every_hours=24, audiences=None):
        self.stores = dict(stores)
        self.clients = dict(clients)
        self.tenants = dict(tenants or {})
        self.audiences = self._load_audiences(audiences)
        self._check_policies()
        self._tokens, self._minting = self._load_tokens(tokens)
        self.stages = list(stages) if stages is not None else default_stages()
        self.guards = list(guards) if guards is not None else default_guards()
        redactors = dict(redactors or {})
        self.redactors = {
            "private": redactors.get("private") or registry.create("redactor", "secrets"),
            "public": redactors.get("public") or registry.create("redactor", "public"),
        }
        self.pool = Pool(pool_size)
        if tenant_pool_total is None:
            tenant_pool_total = max(pool_size - 2, 1)
        self.tenant_budget = limits.TenantBudget(tenant_pool_total, tenant_pool_per_tenant)
        # Workers a tenant request's store calls always leave to the other clients.
        self.tenant_keep = max(pool_size - tenant_pool_total, 0)
        self.store_slots = limits.StoreSlots(store_max_inflight or max(pool_size - 2, 1))
        self.clock = clock
        self.recall_deadline = recall_deadline
        if cold_recall_deadline is None:
            cold_recall_deadline = max(COLD_RECALL_DEADLINE, recall_deadline)
        self.cold_recall_deadline = cold_recall_deadline
        self.cold_after = cold_after
        self._last_recall = None
        self._recall_lock = threading.Lock()
        self.recall_failures = RecallFailures(clock)
        self.recall_busy = RecallFailures(clock)
        self.writes = ExtractionWatch(clock)
        self.remember_deadline = remember_deadline
        self.limiter = limits.RateLimiter(clock)
        self._log = log
        self.forgetting = forgetting
        self.sweep_every_hours = sweep_every_hours
        self._sweep_halt = threading.Event()
        self._sweep_thread = None
        self._started = []
        self._closed = False

    def _load_audiences(self, audiences):
        """name -> level for every store and tenants provider; what was not declared gets its default."""
        levels = {name: "owner" for name in self.stores}
        levels.update({name: "group" for name in self.tenants})
        self._declared = set()
        for name, level in dict(audiences or {}).items():
            if name not in levels:
                raise ConfigError("audiences names %s, which is neither a store nor a tenants provider" % name)
            kind = "stores" if name in self.stores else "tenants"
            if level not in AUDIENCES:
                raise ConfigError("%s.%s.audience must be one of %s" % (kind, name, _audience_choices()))
            levels[name] = level
            self._declared.add(name)
        return levels

    def audience_of(self, name):
        """The level of a store or a tenants provider: who its content may be shown to."""
        try:
            return self.audiences[name]
        except KeyError:
            raise KeyError("no store or tenants provider named %r" % name) from None

    def _check_audience(self, where, policy):
        """The two audience rules: a client reads only stores at least as wide as its own audience,
        and a client serving many groups reads no store that belongs to one group."""
        if policy.audience is not None and policy.audience not in AUDIENCES:
            raise ConfigError("%s.audience must be one of %s" % (where, _audience_choices()))
        if policy.audience == "owner" and policy.public:
            raise ConfigError("%s.audience is \"owner\" but public is true: an owner-level client is not public"
                              % where)
        if policy.audience in ("group", "public") and not policy.public:
            raise ConfigError("%s.audience is \"%s\" but public is false: a client whose audience is \"group\" or "
                              "\"public\" shows what it recalls to other people" % (where, policy.audience))
        reach = AUDIENCES.index(policy.reach)
        for entry in policy.recall or ():
            shared = entry != TENANT_STORE
            source = entry if shared else policy.tenants
            level, note = self.audiences[source], "" if source in self._declared else " (the default: it declares none)"
            if AUDIENCES.index(level) < reach:
                if shared:
                    head = "%s.recall names the store %r, whose audience is \"%s\"%s" % (where, entry, level, note)
                else:
                    head = "%s.recall names \"%s\", and the audience of tenants.%s is \"%s\"%s" % (
                        where, TENANT_STORE, source, level, note)
                raise ConfigError(
                    "%s, but the client's audience is \"%s\": what it recalls would be shown to people the store "
                    "is not for. take the store out of recall, or, if everything in it may be shown to them, set "
                    "audience on [%s.%s]" % (head, policy.reach, "stores" if shared else "tenants", source))
            if shared and policy.tenants is not None and policy.reach == "group" and level == "group":
                raise ConfigError(
                    "%s.recall names the store %r, whose audience is \"group\" (one group), but the client "
                    "serves every tenant of %r: a store that all of them read must have audience \"public\""
                    % (where, entry, policy.tenants))

    def _check_policies(self):
        for name in self.tenants:
            if name in self.stores:
                raise ConfigError("tenants.%s has the same name as a store" % name)
        for name, policy in self.clients.items():
            where = "clients.%s" % name
            if policy.tenants is not None and policy.tenants not in self.tenants:
                raise ConfigError("%s.tenants names an unknown tenants provider %r" % (where, policy.tenants))
            self._check_binding(where, policy)
            for store in policy.recall or ():
                if store == TENANT_STORE:
                    if policy.tenants is None:
                        raise ConfigError("%s.recall names %r but the client has no tenants" % (where, TENANT_STORE))
                elif store not in self.stores:
                    raise ConfigError("%s.recall names an unknown store %r" % (where, store))
            if policy.write == TENANT_STORE:
                if policy.tenants is None:
                    raise ConfigError("%s.write is %r but the client has no tenants" % (where, TENANT_STORE))
            elif policy.write is not None:
                if policy.write not in self.stores:
                    raise ConfigError("%s.write names an unknown store %r" % (where, policy.write))
                if not getattr(self.stores[policy.write], "writable", False):
                    raise ConfigError("%s.write names the read-only store %r" % (where, policy.write))
            self._check_audience(where, policy)

    @staticmethod
    def _check_binding(where, policy):
        if policy.tenant_binding not in TENANT_BINDINGS:
            raise ConfigError("%s.tenant_binding must be \"request\" or \"token\"" % where)
        if policy.tenant_binding == "token" and policy.tenants is None:
            raise ConfigError("%s.tenant_binding = \"token\" needs tenants: only a tenant client has a tenant "
                              "to bind its tokens to" % where)
        generation = policy.tenant_token_generation
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ConfigError("%s.tenant_token_generation must be a positive integer" % where)

    def _load_tokens(self, tokens):
        """(plain, minting): name -> token bytes of the clients that present their token as it is,
        and name -> minting secret of the tenant-bound clients. Two clients with one secret would be
        indistinguishable, or one could mint the other's tokens: refused, naming both, never the
        value. A configured token may not look like a minted one (TENANT_TOKEN_PREFIX + "."):
        such a string is only ever checked as a minted token, so it could never authenticate.

        A minting secret must pass usable_token(), as the configuration loader requires of every
        token: a gateway built in code gets the same floor, as a ConfigError, because a token's
        holder can guess a weak secret offline."""
        plain, minting, owner = {}, {}, {}
        for name, token in tokens.items():
            if not token or name not in self.clients:
                continue
            if token in owner:
                raise ConfigError("clients %s and %s have the same token: give each client its own"
                                  % tuple(sorted((owner[token], name))))
            if token.startswith(TENANT_TOKEN_PREFIX + "."):
                raise ConfigError("clients.%s: a configured token may not start with \"%s.\", the mark of a "
                                  "minted tenant token" % (name, TENANT_TOKEN_PREFIX))
            try:
                raw = _secret_bytes(token)
            except UnicodeEncodeError:
                raise ConfigError("clients.%s: the token is not valid text" % name) from None
            owner[token] = name
            if self.clients[name].token_bound:
                if not usable_token(token):
                    raise ConfigError("clients.%s: the minting secret is the placeholder or shorter than %d "
                                      "characters" % (name, MIN_TOKEN_CHARS))
                minting[name] = token
            else:
                plain[name] = raw
        return plain, minting

    def weak_minting_secret(self, name):
        """True when the client's minting secret does not look randomly generated
        (minting_secret_looks_weak). Answers yes or no only: the secret and its length never leave
        the gateway."""
        secret = self._minting.get(name)
        return secret is not None and minting_secret_looks_weak(secret)

    # ---------------------------------------------------------------- who is calling

    def identify(self, presented):
        """(client name, tenant id) for a presented token, or None. The tenant id is the one a minted
        tenant token is bound to, None for every other token. A plain token is compared against every
        plain token in constant time; a minting secret is not among them, so presenting one is
        refused like any wrong token. Whatever is presented, the answer is a pair or None, never an
        exception."""
        if not presented or not isinstance(presented, str):
            return None
        if presented.startswith(TENANT_TOKEN_PREFIX + "."):
            return self._identify_bound(presented)
        try:
            given = presented.encode("utf-8", "surrogateescape")
        except UnicodeEncodeError:  # a lone surrogate outside the escape range: no token is spelt so
            return None
        match = None
        for name, token in self._tokens.items():
            if hmac.compare_digest(given, token):
                match = name
        return None if match is None else (match, None)

    def _identify_bound(self, presented):
        """(client name, tenant id) of a minted tenant token, or None. Every tenant-bound client is
        tried and nothing stops early, whichever part is wrong: the answer is the same None for a
        bad structure, a tenant id the provider refuses and a MAC that does not match. The tenant id
        is asked of the client's provider (valid_id) before it can reach a limiter key, a log line
        or a store; computing the MAC over it touches nothing."""
        parsed = parse_tenant_token(presented)
        if parsed is None:
            return None
        tenant_id, mac = parsed
        match = None
        for name, secret in self._minting.items():
            policy = self.clients[name]
            valid = self._valid_tenant_id(policy, tenant_id)
            expected = tenant_token_mac(secret, name, policy.tenants, policy.tenant_token_generation, tenant_id)
            if hmac.compare_digest(mac, expected) and valid:
                match = (name, tenant_id)
        return match

    def _valid_tenant_id(self, policy, tenant_id):
        try:
            return bool(self.tenants[policy.tenants].valid_id(tenant_id))
        except Exception:  # noqa: BLE001 - a provider that raises on an id has not accepted it
            return False

    def authenticate(self, presented):
        """The client name for a presented token, or None. A minted tenant token also names its
        tenant: see identify(), which is what the HTTP server uses."""
        caller = self.identify(presented)
        return None if caller is None else caller[0]

    def has_token(self, name):
        """True when the client can authenticate: it has a usable token, or a minting secret."""
        return name in self._tokens or name in self._minting

    @staticmethod
    def _tenant_rate_key(policy, tenant_id):
        return "%s:%s" % (policy.name, tenant_id)

    def admit(self, name, action, bound_tenant=None):
        """The checks that need no request body: rate limits, then permission.
        `action` is "recall", "used" (both need the right to recall) or "remember"; bound_tenant is
        as for recall(). Returns a Reply to refuse, None to go on.

        A tenant-bound client's whole rate limiting happens here, per tenant and in total, because
        its tenant is known before the body is read; a caller that skips admit() (an in-process call
        straight to recall()) is not rate limited, as it is not for the total of any client."""
        policy = self.clients.get(name)
        if policy is None:
            return error_reply(401, "unauthorized", "unauthorized")
        if policy.token_bound != (bound_tenant is not None):
            return error_reply(401, "unauthorized", "unauthorized")
        if policy.token_bound:
            if not isinstance(bound_tenant, str) or not self._valid_tenant_id(policy, bound_tenant):
                return error_reply(400, "bad_tenant", "bad tenant_id")
            # The tenant's own bucket first, the client's total only when that passes: one tenant's
            # refused requests never spend what the client's other tenants share, so a tenant can take
            # at most rate_limit of rate_limit_total. Tenants of a bound client need not trust each other.
            if not self.limiter.allow(self._tenant_rate_key(policy, bound_tenant), policy.rate_limit):
                return error_reply(429, "rate_limited", "rate limited")
            if not self.limiter.allow(name, policy.total_rate_limit):
                return error_reply(429, "rate_limited", "rate limited")
        else:
            # A request-mode tenant client keeps the total first on purpose: its tenant id is still
            # unread and chosen by the caller, and asking per tenant first would let invented ids
            # multiply the budget. The price, documented in docs/security.md: one caller can use up
            # the shared total. A client without tenants has only its own rate_limit.
            limit = policy.total_rate_limit if policy.tenants is not None else policy.rate_limit
            if not self.limiter.allow(name, limit):
                return error_reply(429, "rate_limited", "rate limited")
        allowed = policy.can_write if action == "remember" else policy.can_recall
        if not allowed:
            return error_reply(403, "forbidden", "forbidden")
        return None

    def _tenant(self, policy, body, bound_tenant=None):
        """(tenant_id, tenant store) for this request ((None, None) without tenants), or a
        Reply to refuse it. For a request-mode client the per-tenant limiter is asked here, before
        the provider, so asking for unknown tenants costs budget too.

        This is the only place a request's tenant is decided. A tenant-bound client's tenant is
        the one its token carries (bound_tenant): the body may leave tenant_id out or repeat it
        exactly; anything else in it is refused before the provider sees it. Its rate limits were
        charged in admit(), its own bucket before the total, and are not charged again here."""
        if policy.tenants is None:
            if "tenant_id" in body:
                return error_reply(400, "bad_tenant", "tenant_id is not allowed for this client")
            return None, None
        if policy.token_bound:
            if "tenant_id" in body:
                asked = body["tenant_id"]
                if not isinstance(asked, str) or asked != bound_tenant:
                    return error_reply(403, "tenant_mismatch", "tenant_id does not match the token", "tenant-mismatch")
            tenant_id = bound_tenant
        else:
            tenant_id = body.get("tenant_id")
        provider = self.tenants[policy.tenants]
        if not isinstance(tenant_id, str) or not provider.valid_id(tenant_id):
            return error_reply(400, "bad_tenant", "bad tenant_id")
        if not policy.token_bound and not self.limiter.allow(self._tenant_rate_key(policy, tenant_id), policy.rate_limit):
            return error_reply(429, "rate_limited", "rate limited")
        store = provider.store(tenant_id)
        if store is None:
            return error_reply(403, "unknown_tenant", "unknown tenant")
        return tenant_id, store

    def _redactor(self, policy):
        return self.redactors["public" if policy.public else "private"]

    def _keep(self, policy):
        return self.tenant_keep if policy.tenants is not None else 0

    def _admit_tenant(self, policy, tenant_id):
        """The budget key of a tenant request once admitted, None for other clients, or
        False when the tenant budget is used up. Keyed by provider and tenant: two clients
        of one provider share the tenant's budget."""
        if policy.tenants is None:
            return None
        key = "%s:%s" % (policy.tenants, tenant_id)
        return key if self.tenant_budget.acquire(key) else False

    def _piece_error(self, client, detail):
        self._log("piece-error client=%s %s" % (client, detail))

    def _guarded(self, method, name, body, bound_tenant):
        """Run method(name, body, bound_tenant); any unexpected error is a 500, logged by type, never raised.

        bound_tenant is the tenant a minted token named (Gateway.identify). A tenant-bound client
        cannot be called without one, and no other client can be called with one: an in-process
        caller that forgets it is refused, not guessed for, and the body never stands in for it."""
        try:
            if name not in self.clients:
                return error_reply(401, "unauthorized", "unauthorized")
            if self.clients[name].token_bound != (bound_tenant is not None):
                return error_reply(401, "unauthorized", "unauthorized")
            if not isinstance(body, dict):
                return error_reply(400, "bad_request", "bad request")
            return method(name, body, bound_tenant)
        except Exception as error:  # noqa: BLE001 - in-process callers must never see an exception
            self._piece_error(name if isinstance(name, str) else "-", "where=%s error=%s" % (
                method.__name__.strip("_"), type(error).__name__))
            return error_reply(500, "internal", "internal error")

    # ---------------------------------------------------------------- recall

    def recall_deadline_now(self):
        """Cold deadline for the first recall or one after cold_after idle seconds, else the usual one.
        Calling it counts as a recall starting."""
        with self._recall_lock:
            now = self.clock()
            cold = self._last_recall is None or now - self._last_recall >= self.cold_after
            self._last_recall = now
        return self.cold_recall_deadline if cold else self.recall_deadline

    def recall(self, name, body, bound_tenant=None):
        """Answer {"query": str, "limit": int, "tenant_id": str, "context": {...}} for the client `name`.
        bound_tenant: the tenant a tenant-bound client's token names (see identify), else None."""
        return self._guarded(self._recall, name, body, bound_tenant)

    def _recall(self, name, body, bound_tenant):
        policy = self.clients[name]
        if not policy.can_recall:
            return error_reply(403, "forbidden", "forbidden")
        tenant = self._tenant(policy, body, bound_tenant)
        if isinstance(tenant, Reply):
            return tenant
        tenant_id, tenant_store = tenant
        query = body.get("query")
        limit = body.get("limit", DEFAULT_LIMIT)
        if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
            return error_reply(400, "bad_query", "bad query")
        if isinstance(limit, bool) or not isinstance(limit, int):
            return error_reply(400, "bad_limit", "bad limit")
        context = _valid_context(body.get("context"))
        if context is None:
            return error_reply(400, "bad_context", "bad context")
        key = self._admit_tenant(policy, tenant_id)
        if key is False:
            self.recall_busy.record()
            return error_reply(503, "busy", "busy", "tenant-budget")
        try:
            return self._search(policy, tenant_id, tenant_store, query, max(1, min(limit, MAX_LIMIT)), context)
        finally:
            if key:
                self.tenant_budget.release(key)

    def _recall_stores(self, policy, tenant_store):
        """source name -> store, in the order of the policy. Rebuilt per request, so only
        this tenant's store is ever used."""
        stores = {}
        for name in policy.recall:
            if name == TENANT_STORE:
                stores[policy.tenants] = tenant_store
            else:
                stores[name] = self.stores[name]
        return stores

    def _search(self, policy, tenant_id, tenant_store, query, limit, context):
        stores = self._recall_stores(policy, tenant_store)
        notes = []
        deadline_s = self.recall_deadline_now() if stores else self.recall_deadline
        ctx = RecallContext(query, limit, pool=self.pool, clock=self.clock, deadline=self.clock() + deadline_s,
                            public=policy.public, redact=self._redactor(policy), client=policy.name,
                            tenant_id=tenant_id, notes=notes, context=context, log=self._log,
                            tenant_source=policy.tenants if TENANT_STORE in policy.recall else None,
                            slots=self.store_slots, keep=self._keep(policy), audience=policy.reach)
        try:
            out = recall(stores, self.stages, ctx)
        except StageError:
            self.recall_failures.record()
            return error_reply(503, "stage_error", "stage error", _join(notes))
        note = _join(notes)
        if out["failed"]:
            self.recall_failures.record()
        elif out["busy"]:
            self.recall_busy.record()
        if stores and out["failed"] + out["busy"] >= len(stores):
            # Nothing could be searched: "found nothing" and "could not look" must stay apart.
            if out["failed"] == 0:
                return error_reply(503, "busy", "busy", note)
            return error_reply(503, "unavailable", "unavailable", note)
        return Reply(200, out, note)

    # ---------------------------------------------------------------- used

    def used(self, name, body, bound_tenant=None):
        """Record that the caller's reply relied on memories: {"used": [{"source", "id"}, ...],
        "tenant_id": str} for the client `name`. Replies {"status": "ok", "recorded": n}.
        bound_tenant: as for recall()."""
        return self._guarded(self._used, name, body, bound_tenant)

    def _used(self, name, body, bound_tenant):
        policy = self.clients[name]
        if not policy.can_recall:
            return error_reply(403, "forbidden", "forbidden")
        tenant = self._tenant(policy, body, bound_tenant)
        if isinstance(tenant, Reply):
            return tenant
        tenant_id, _ = tenant
        items = _valid_used(body.get("used"))
        if items is None:
            return error_reply(400, "bad_used", "bad used")
        # Only sources this client may recall from: an unknown source and a forbidden one look alike.
        sources = {policy.tenants if s == TENANT_STORE else s for s in policy.recall}
        if any(source not in sources for source, _ in items):
            return error_reply(403, "forbidden", "forbidden")
        if self.forgetting is None or not items:
            return Reply(200, {"status": "ok", "recorded": 0})
        key = self._admit_tenant(policy, tenant_id)
        if key is False:
            return error_reply(503, "busy", "busy", "tenant-budget")
        try:
            recorded = self._record_used(policy, tenant_id, items)
        finally:
            if key:
                self.tenant_budget.release(key)
        return Reply(200, {"status": "ok", "recorded": recorded})

    def _exists(self, store, ref, deadline):
        """True when the store can read this memory. Never raises, never waits past the deadline."""
        outcome, text = call(self.pool, lambda t: store.read(ref, max(deadline - self.clock(), 0.05), t),
                             self.clock, deadline, wait=MAX_WAIT, slots=self.store_slots, store=store)
        return outcome == OK and isinstance(text, str)

    def _record_used(self, policy, tenant_id, items):
        """Record a use for each reported memory that exists. Returns how many. A memory is known
        when the ledger has a row for it or its store can read it: made-up ids record nothing."""
        f = self.forgetting
        by_key = {}
        for source, ref in dict.fromkeys(items):
            if not f.manages(source):
                continue
            tenant = source == policy.tenants
            by_key.setdefault((f.key(source, tenant_id if tenant else None), source, tenant), []).append(ref)
        deadline = self.clock() + USED_DEADLINE
        recorded = 0
        for (key, source, tenant), refs in by_key.items():
            store = self.tenants[source].store(tenant_id) if tenant else self.stores[source]
            if store is None:
                continue
            known = f.ledger.get_many(key, refs)
            real = [ref for ref in refs if ref in known or self._exists(store, ref, deadline)]
            if real:
                f.record_use(key, [(ref, None) for ref in real], f.now())
                recorded += len(real)
        return recorded

    # ---------------------------------------------------------------- remember

    def remember(self, name, body, bound_tenant=None):
        """Store {"messages": [{"role", "content", "time"?, "speaker"?}], "tenant_id": str,
        "context": {...}} for the client `name`. bound_tenant: as for recall()."""
        return self._guarded(self._remember, name, body, bound_tenant)

    def _remember(self, name, body, bound_tenant):
        policy = self.clients[name]
        if not policy.can_write:
            return error_reply(403, "forbidden", "forbidden")
        tenant = self._tenant(policy, body, bound_tenant)
        if isinstance(tenant, Reply):
            return tenant
        tenant_id, tenant_store = tenant
        messages = _valid_messages(body.get("messages"))
        if messages is None:
            return error_reply(400, "bad_messages", "bad messages")
        context = _valid_context(body.get("context"))
        if context is None:
            return error_reply(400, "bad_context", "bad context")
        key = self._admit_tenant(policy, tenant_id)
        if key is False:
            return error_reply(503, "busy", "busy", "tenant-budget")
        try:
            store = tenant_store if policy.write == TENANT_STORE else self.stores[policy.write]
            ctx = write_module.WriteContext(policy, tenant_id, self._redactor(policy), context)
            return self._write(policy, store, messages, ctx)
        finally:
            if key:
                self.tenant_budget.release(key)

    def _write(self, policy, store, messages, ctx):
        done = []  # the guards whose before() completed, for rollback
        guard = None
        try:
            for guard in self.guards:
                messages = guard.before(messages, ctx)
                done.append(guard)
                if not _check_shape(messages):
                    break  # nothing left worth keeping: the later guards have nothing to look at
        except WriteRejected as refused:
            self._rollback(done, ctx)
            if isinstance(refused.status, int) and 400 <= refused.status < 600:
                return error_reply(refused.status, refused.code, refused.error, refused.note)
            return self._guard_failed(policy, guard, "where=before error=bad-status")
        except Exception as error:  # noqa: BLE001 - fail closed: nothing is written
            self._rollback(done, ctx)
            what = "bad-messages" if isinstance(error, _BadMessages) else type(error).__name__
            return self._guard_failed(policy, guard, "where=before error=%s" % what)
        if not messages:
            self._rollback(done, ctx)
            return Reply(200, {"status": "skipped"}, "skipped")
        deadline = self.clock() + self.remember_deadline
        outcome, result = call(self.pool, lambda t: store.remember(messages, self.remember_deadline, t),
                               self.clock, deadline, wait=min(MAX_WAIT, self.remember_deadline / 4),
                               keep=self._keep(policy), slots=self.store_slots, store=store)
        if outcome != OK:
            self._rollback(done, ctx)
            if outcome == BUSY:  # never started: nothing reached the store
                return error_reply(503, "busy", "busy")
            if result is not None:  # the exception the backend raised (None: it ran out of time)
                self._piece_error(policy.name, "store=%s where=remember error=%s" % (
                    self._write_source(policy), type(result).__name__))
            return error_reply(502, "backend_unavailable", "memory backend unavailable")
        receipt = result
        if receipt is None:
            # The backend looked at it and kept nothing: a result, not a success.
            self._rollback(done, ctx)
            return Reply(200, {"status": "skipped"}, "skipped")
        for guard in done:
            try:
                guard.after(receipt, ctx)
            except Exception as error:  # noqa: BLE001 - the write happened; bookkeeping must not undo it
                self._piece_error(policy.name, "guard-error:%s where=after error=%s" % (
                    piece_name(guard), type(error).__name__))
        on_saved = self._pin_write(policy, store, ctx.tenant_id, receipt)
        if getattr(store, "tracks_writes", False):
            self.writes.track(receipt, store, on_saved)
        return Reply(200, {"status": "ok", "receipt": receipt})

    def _guard_failed(self, policy, guard, detail):
        note = "guard-error:%s" % piece_name(guard)
        self._piece_error(policy.name, "%s %s" % (note, detail))
        return error_reply(500, "guard_error", "guard error", note)

    def _rollback(self, done, ctx):
        """Undo the guards that let the write through, newest first."""
        for guard in reversed(done):
            rollback = getattr(guard, "rollback", None)
            if rollback is None:
                continue
            try:
                rollback(ctx)
            except Exception as error:  # noqa: BLE001 - one failed rollback must not stop the others
                self._piece_error(ctx.client, "guard-error:%s where=rollback error=%s" % (
                    piece_name(guard), type(error).__name__))

    @staticmethod
    def _write_source(policy):
        return policy.tenants if policy.write == TENANT_STORE else policy.write

    def _pin_write(self, policy, store, tenant_id, receipt):
        """Pin what a pin_writes client just wrote. Returns a callback for the write watch
        when the store cannot name the memories yet, else None. Never raises."""
        if self.forgetting is None or not policy.pin_writes:
            return None
        tenant = policy.write == TENANT_STORE
        source = self._write_source(policy)
        if not self.forgetting.manages(source):
            return None
        key = self.forgetting.key(source, tenant_id if tenant else None)
        try:
            if self.forgetting.pin_receipt(key, store, receipt) is not None:
                return None
        except Exception:  # noqa: BLE001 - a failed pin never fails the write
            self._log("pin of a write failed client=%s" % policy.name)
        if not getattr(store, "tracks_writes", False):
            return None
        forgetting = self.forgetting
        return lambda done_receipt, done_store: forgetting.pin_receipt(key, done_store, done_receipt)

    # ---------------------------------------------------------------- forgetting

    def sweep_targets(self):
        """{ledger key: store} of everything the forgetting task looks after: the managed shared
        stores, and every tenant of each managed tenants provider that can list its tenants."""
        targets = {}
        if self.forgetting is None:
            return targets
        for name, store in self.stores.items():
            if self.forgetting.manages(name):
                targets[self.forgetting.key(name)] = store
        for name, provider in self.tenants.items():
            if not self.forgetting.manages(name) or not callable(getattr(provider, "list", None)):
                continue
            for tenant_id in provider.list():
                store = provider.store(tenant_id)
                if store is not None:
                    targets[self.forgetting.key(name, tenant_id)] = store
        return targets

    def sweep(self, dry_run=False):
        """Run the forgetting task now. Returns its report (numbers only), None without forgetting."""
        if self.forgetting is None:
            return None
        return self.forgetting.sweep(self.sweep_targets(), dry_run=dry_run)

    def _sweep_loop(self, interval):
        while not self._sweep_halt.wait(interval):
            try:
                report = self.sweep()
                rows = [v for k, v in report.items() if isinstance(v, dict)]
                self._log("forgetting sweep: stores=%d forgotten=%d held=%d failed=%d purged=%d" % (
                    len(rows), sum(r.get("forgotten", 0) for r in rows), sum(r.get("held", 0) for r in rows),
                    sum(r.get("failed", 0) for r in rows), report.get("purged", 0)))
            except Exception as error:  # noqa: BLE001 - the sweep thread must not die
                self._log("forgetting sweep failed: %s" % type(error).__name__)

    # ---------------------------------------------------------------- health and lifecycle

    def health(self, deep=False):
        """{"status": "ok"}; with deep, also whether each shared store answers and the
        failure counters. Numbers and names only, never content."""
        if not deep:
            return {"status": "ok"}
        return {
            "status": "ok",
            "stores": self._stores_health(),
            "recall_failed_1h": self.recall_failures.count(),
            "recall_busy_1h": self.recall_busy.count(),
            "writes_24h": self.writes.counts(),
        }

    def _stores_health(self, wait=3.0):
        """name -> "ok" | "down" for every shared store, asked at the same time so several
        dead stores do not add up. A store that has not answered within `wait` seconds is down."""
        answers = {}

        def ask(name, store):
            try:
                answers[name] = "ok" if store.health() else "down"
            except Exception:  # noqa: BLE001 - health() should not raise; a store that does is down
                answers[name] = "down"

        threads = [threading.Thread(target=ask, args=item, daemon=True) for item in self.stores.items()]
        for thread in threads:
            thread.start()
        end = time.monotonic() + wait
        for thread in threads:
            thread.join(max(end - time.monotonic(), 0))
        return {name: answers.get(name, "down") for name in self.stores}

    def _pieces(self):
        """(kind, piece) of every piece with a lifecycle, each object once, in start order."""
        seen, out = set(), []
        groups = (("store", self.stores.values()), ("tenants", self.tenants.values()),
                  ("stage", self.stages), ("guard", self.guards))
        for kind, pieces in groups:
            for piece in pieces:
                if id(piece) not in seen:
                    seen.add(id(piece))
                    out.append((kind, piece))
        return out

    @staticmethod
    def _label(kind, piece):
        return piece_name(piece) if kind in ("stage", "guard") else type(piece).__name__

    def start(self):
        """Start every piece that has start(gateway), then the background watcher of write
        outcomes and, with forgetting, the sweep thread (first sweep after one interval, not
        at start-up). A piece that fails to start stops the start-up (logged by type)."""
        for kind, piece in self._pieces():
            start = getattr(piece, "start", None)
            if not callable(start) or any(p is piece for p in self._started):
                continue
            try:
                start(self)
            except Exception as error:
                self._log("piece-error start %s=%s error=%s" % (kind, self._label(kind, piece), type(error).__name__))
                raise
            self._started.append(piece)
        self.writes.start()
        if self.forgetting is not None and self.sweep_every_hours and self._sweep_thread is None:
            self._sweep_thread = threading.Thread(
                target=self._sweep_loop, args=(self.sweep_every_hours * 3600.0,), name="forgetting-sweep", daemon=True)
            self._sweep_thread.start()

    def close(self):
        """Stop the background threads and close every piece once. A piece that fails to
        close is logged by type and the others are still closed."""
        if self._closed:
            return
        self._closed = True
        self.writes.stop()
        self._sweep_halt.set()
        thread = self._sweep_thread
        if thread is not None:
            thread.join(5)
        pieces = self._pieces()
        for kind, piece in reversed([p for p in pieces if p[0] in ("stage", "guard")]):
            self._close_piece(kind, piece)
        if self.forgetting is not None:
            self.forgetting.close()
        self.pool.close()
        for kind, piece in reversed([p for p in pieces if p[0] in ("store", "tenants")]):
            self._close_piece(kind, piece)

    def _close_piece(self, kind, piece):
        close = getattr(piece, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception as error:  # noqa: BLE001 - closing is never fatal
            self._log("piece-error close %s=%s error=%s" % (kind, self._label(kind, piece), type(error).__name__))

    def log(self, method, path, status, client, started, note=""):
        ms = int((time.monotonic() - started) * 1000)
        self._log("%s %s %d client=%s %dms%s" % (method, path, status, client or "-", ms, " " + note if note else ""))
