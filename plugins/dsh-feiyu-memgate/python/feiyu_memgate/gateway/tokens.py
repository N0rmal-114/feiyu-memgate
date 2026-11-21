"""Tokens that need no Gateway: what may guard a route, and the tenant-bound token format.

usable_token() and MIN_TOKEN_CHARS are the floor every configured token must pass (the
configuration loader and the Gateway both apply it). The rest is the format of a minted
tenant token (mint, parse, MAC), which is pure functions of its arguments. It is its own
file so that the Gateway and the tools that mint tokens (tools/tenants.py, bench/design_probe.py)
share one definition that is kept apart from the gateway's request handling.

Standard library only.
"""
import base64
import hashlib
import hmac
import re
import struct

# Tokens shorter than this, or the placeholder of the example configuration, are not loaded,
# so a hand-copied example never guards a route with a public string.
MIN_TOKEN_CHARS = 32
PLACEHOLDER_TOKEN = "change-me"


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

