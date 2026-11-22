"""The checks of a request body, and the limits they enforce.

Each function takes what a caller sent and returns the cleaned value, or None when it breaks
the rules (the gateway turns None into a 400). They touch no gateway state, so they are
their own file, away from the request handling that calls them.

Standard library only.
"""
import math

from ..model import REQUEST_FIELDS, valid_account, valid_channel  # noqa: F401 - re-exported for gateway.fields
from ..model import valid_request_field as _valid_field

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


def _valid_messages(messages, accounts=False):
    """The messages as [{"role", "content", optional "time", "speaker", "about", and the REQUEST_FIELDS given}],
    or None when the shape is wrong. Over-long content is rejected, not truncated; other keys are dropped. A
    null value counts as absent. The request keys are only checked here: the gateway turns them into the
    memory's record fields (gateway.fields). `accounts` is True when the gateway has an identity table: then
    a speaker and an about must each be an account (model.valid_account), not just a short string."""
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
        for key, cap in (("time", MAX_TIME_CHARS), ("speaker", MAX_SPEAKER_CHARS), ("about", MAX_SPEAKER_CHARS)):
            value = m.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or len(value) > cap:
                return None
            if accounts and key != "time" and not valid_account(value):
                return None
            item[key] = value
        for key in REQUEST_FIELDS:
            value = m.get(key)
            if value is None:
                continue
            if not _valid_field(key, value):
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
