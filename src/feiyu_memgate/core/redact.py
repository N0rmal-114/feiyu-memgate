"""Redactors: text cleaners used on everything that is stored or returned.

A redactor is one piece of the puzzle: a function str -> str. Two are built in,
"secrets" (keys, tokens, passwords) and "public" (secrets plus phone numbers, e-mail
addresses, ID numbers and street addresses, for anything shown to other people), and
"none" switches cleaning off. Standard library only.
"""
import re

from ..registry import register

MARK = "[REDACTED]"

# A key that names a secret. It may be the tail of a longer name (access_token, client_secret, x-api-key).
_SECRET_KEY = (
    r"(?:(?:api|access|secret|private|auth|client)[_-]?key|token|secret|password|passwd|passphrase"
    r"|authorization|密码|口令|密钥|令牌)"
)

# key = value, key: value, and the JSON / Python / YAML forms of the same: "key": "value", 'key': 'value',
# \"key\":\"value\" (JSON inside a JSON string). A quoted value goes whole, spaces and escaped quotes included
# ("ab\"cd" is one value; inside \"...\" the escaped quote is \\\"), at any length. A bare value needs 6
# characters (so "token=abc" stays readable) and is never a JSON literal (true, false, null). Where it ends
# depends on where it stands:
#   - after a quoted key ("secret": 12345678) it is a JSON value and ends at , } ] (so {"secret":false,"n":1}
#     stays valid JSON);
#   - in a URL query (?a=1&token=...) it ends at & or #;
#   - anywhere else (YAML, env files, prose: password: abc;def) it runs to the next whitespace or quote,
#     punctuation included, so a password with ; or , in it is never cut short.
# The key, the separator and the quotes are kept. No alternative can swallow a line break, and the separator is
# kept as it was, so redaction never changes the number of lines.
_HEAD = re.compile(r"(?P<head>" + _SECRET_KEY + r"(?P<kq>\\?[\"'])?\s*[:=：＝]\s*)", re.IGNORECASE)
_QUOTED_VALUE = (
    r"(?P<edq>\\\")(?:\\\\\\\"|\\(?!\")[^\n]|[^\"\\\n])+(?P<edqe>\\\")"
    r"|(?P<dq>\")(?:\\[^\n]|[^\"\\\n])+(?P<dqe>\")"
    r"|(?P<sq>')(?:\\[^\n]|[^'\\\n])+(?P<sqe>')"
)


def _value_pattern(bare_chars):
    return re.compile(
        r"(?:" + _QUOTED_VALUE
        + r"|(?P<open>\\?[\"']?)(?!(?:true|false|null)(?![A-Za-z0-9_]))(?P<bare>" + bare_chars + r"{6,}))",
        re.IGNORECASE,
    )


# The three places a bare value can stand (see above). The head is found first, then the value pattern that
# fits the head's context is matched right after it, so a URL query value never swallows the parameters that
# follow it (no re-scan of the rest, no recursion, linear in the text).
_VALUE_JSON = _value_pattern(r"[^\s'\",}\]]")
_VALUE_QUERY = _value_pattern(r"[^\s'\"&#]")
_VALUE_PLAIN = _value_pattern(r"[^\s'\"]")


def _in_query(text, start):
    """True when the key starts a URL query parameter: the word holding it follows ? or &."""
    i = start
    while i > 0 and (text[i - 1].isalnum() or text[i - 1] in "_.-"):
        i -= 1
    return i > 0 and text[i - 1] in "?&"


def _redact_key_values(text):
    """Replace the value of every key = value pair. A loop, never recursion."""
    out = []
    last = pos = 0
    while True:
        head = _HEAD.search(text, pos)
        if head is None:
            break
        if head.group("kq"):
            pattern = _VALUE_JSON
        elif _in_query(text, head.start()):
            pattern = _VALUE_QUERY
        else:
            pattern = _VALUE_PLAIN
        value = pattern.match(text, head.end())
        if value is None:
            pos = head.start() + 1
            continue
        if value.group("edq") is not None:
            new = value.group("edq") + MARK + value.group("edqe")
        elif value.group("dq") is not None:
            new = value.group("dq") + MARK + value.group("dqe")
        elif value.group("sq") is not None:
            new = value.group("sq") + MARK + value.group("sqe")
        else:
            new = value.group("open") + MARK
        out.append(text[last:head.end()])
        out.append(new)
        last = pos = value.end()
    if not out:
        return text
    out.append(text[last:])
    return "".join(out)


# A secret word, then 是 / 为 / is, then the value: "我的密码是 hunter2hunter2", "password is hunter2hunter2".
# The value is at least 6 non-space characters, so "密码是什么" and "the password is wrong" stay readable;
# the English words below are the prose that usually follows "password is" and are not values.
_SECRET_WORD = (
    r"(?:密码|口令|密钥|令牌|(?<![A-Za-z])(?:passwords?|passwd|passphrase|passcode|pin code|token|secret"
    r"|(?:api|access|secret|private)[ _-]?key))"
)
_IS_VALUE = re.compile(
    r"(?P<head>" + _SECRET_WORD + r"[ \t]*(?:是|为|(?<![A-Za-z])is(?![A-Za-z]))[ \t]*[:：]?[ \t]*)(?P<value>[^\s]{6,})",
    re.IGNORECASE,
)
_PROSE_WORDS = frozenset(
    "required incorrect invalid expired missing empty wrong correct changed hashed encrypted stored "
    "unknown unchanged optional visible hidden private secret useless fine good".split()
)


def _is_value_repl(match):
    if match.group("value").strip(".,;!?").lower() in _PROSE_WORDS:
        return match.group(0)
    return match.group("head") + MARK


# -----BEGIN ... PRIVATE KEY----- up to the matching END line (or to the end of the text when the block is
# cut off). Every line of the block becomes MARK, so the number of lines does not change.
_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----.*?"
    r"(?:-----END [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----|\Z)",
    re.DOTALL,
)


def _pem_repl(match):
    return "\n".join(MARK for _ in match.group(0).split("\n"))


# Credentials inside a URL: scheme://user:password@host. The password may hold an unescaped "@".
_URL_CREDENTIALS = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]+:)[^\s/]+(?=@)")
# curl -u user:password / --user user:password (only looked at when the text mentions curl).
_CURL_USER = re.compile(r"((?<![\w-])(?:--user[ \t=]+|-u[ \t]+)[\"']?[^\s:\"']+:)[^\s\"']+")
_BASIC_AUTH = re.compile(r"(\bauthorization[\"']?[ \t]*[:=][ \t]*[\"']?basic[ \t]+)[A-Za-z0-9+/=_-]{6,}", re.IGNORECASE)


def _curl_user(text):
    if "curl" not in text.lower():
        return text
    return _CURL_USER.sub(lambda m: m.group(1) + MARK, text)


# Secret-shaped strings, bearer tokens and the key/value forms above.
_SECRET_RULES = (
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), MARK),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), MARK),
    (re.compile(r"AKIA[0-9A-Z]{16}"), MARK),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"), MARK),
    (re.compile(r"(\b[Bb]earer[ \t]+)[A-Za-z0-9._~+/=-]{16,}"), r"\1" + MARK),
    (_BASIC_AUTH, r"\1" + MARK),
    (_URL_CREDENTIALS, r"\1" + MARK),
)

_CJK = "一-鿿"
_NUM_CN = "0-9一二三四五六七八九十"

# Public rules (group chats, live streams): deliberately err on the side of over-redacting.
# Known limits of redact_public (heuristics, not a guarantee):
#   - landline numbers (e.g. 010-12345678) are not matched;
#   - full-width digits and digits spelled out in words are not matched;
#   - numbers split by other text or by unusual separators are not matched;
#   - addresses without an administrative-unit chain, a street+number or a
#     unit/room number (e.g. "the flat behind the old mill") are not matched.
# Deciding which stores a public client may read at all is the primary control; this is the second line.
_PUBLIC_RULES = (
    # Mainland mobile numbers, with optional +86 and separators.
    re.compile(r"(?<![0-9])(?:\+?86[-\s]?)?1[3-9][0-9][-\s]?[0-9]{4}[-\s]?[0-9]{4}(?![0-9])"),
    # Email addresses.
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"),
    # ID numbers and other long digit runs (15 to 19 digits, trailing X allowed).
    re.compile(r"(?<![0-9])[0-9]{15,18}[0-9Xx]?(?![0-9])"),
    # Chained administrative units, e.g. province + city + district, and what follows.
    re.compile(
        r"(?:[" + _CJK + r"]{2,9}(?:省|自治区|市|区|县|镇|乡)){2,4}"
        r"[" + _CJK + r"0-9\-]{0,24}"
    ),
    # Street-shaped text with a house number.
    re.compile(
        r"[" + _CJK + r"0-9]{1,12}(?:路|街|巷|弄|大道|村|小区|大厦|花园|广场|苑)"
        r"[" + _NUM_CN + r"\-]+号?[" + _CJK + r"0-9\-]{0,12}"
    ),
    # Building, unit and room numbers.
    re.compile(r"[0-9]+(?:栋|幢|号楼|单元|室|层)(?:[0-9]+(?:单元|室|层|号))*"),
    # English street addresses.
    re.compile(r"\b[0-9]+\s+[A-Za-z]+(?:\s+[A-Za-z]+)?\s+(?:St|Street|Rd|Road|Ave|Avenue|Blvd|Lane|Ln)\b\.?",
               re.IGNORECASE),
)


MAX_ITEM_CHARS = 64 * 1024  # recall drops any item longer than this, whole
LONG_RUN = 256
_ASCII_RUN = re.compile(r"[!-~]+")
_NON_SPACE_RUN = re.compile(r"\S+")


def _has_long_ascii_run(text: str, start: int, end: int) -> bool:
    return any(m.end() - m.start() > LONG_RUN for m in _ASCII_RUN.finditer(text, start, end))


def mask_long_runs(text: str) -> str:
    """Replace every non-whitespace run holding over LONG_RUN printable ASCII chars with MARK.

    Linear scan, done before any rule runs: each character is visited once per
    pattern. The whole non-whitespace run goes, not only its ASCII part, so a
    secret with a non-ASCII character in it (CJK, full-width, U+200B, NUL) leaves
    no fragment behind for the rules to miss. Runs use the same Unicode whitespace
    as the rules, so U+3000 and U+00A0 separate runs while U+200B and NUL do not.
    It also bounds how much text any later regex can see in one go. CJK prose has
    no spaces but no long ASCII sub-run either, so it is not affected; prose with
    a long URL in it is lost up to the nearest whitespace.
    """
    out = []
    last = 0
    for run in _NON_SPACE_RUN.finditer(text):
        if _has_long_ascii_run(text, run.start(), run.end()):
            out.append(text[last:run.start()])
            out.append(MARK)
            last = run.end()
    if not out:
        return text
    out.append(text[last:])
    return "".join(out)


def _redact_rules(text: str) -> str:
    text = _PEM.sub(_pem_repl, text)  # before the long-run mask: it needs the BEGIN / END lines
    text = mask_long_runs(text)
    for pattern, repl in _SECRET_RULES:
        text = pattern.sub(repl, text)
    text = _redact_key_values(text)
    text = _IS_VALUE.sub(_is_value_repl, text)
    return _curl_user(text)


def redact(text: str) -> str:
    """Clean secrets out of `text`. A total function: whatever goes wrong inside (a bug, a
    RecursionError, a MemoryError), the whole text is replaced by MARK, never raised and never
    returned half cleaned."""
    try:
        return _redact_rules(text)
    except Exception:  # noqa: BLE001 - redaction must never fail open or crash a caller
        return MARK


def redact_public(text: str) -> str:
    """redact() plus phone numbers, e-mail addresses, ID numbers and addresses. Total like redact()."""
    try:
        text = _redact_rules(text)
        for pattern in _PUBLIC_RULES:
            text = pattern.sub(MARK, text)
        return text
    except Exception:  # noqa: BLE001
        return MARK


def no_redaction(text: str) -> str:
    return text


# A redactor piece is a factory that returns the cleaning function.
register("redactor", "secrets")(lambda: redact)
register("redactor", "public")(lambda: redact_public)
register("redactor", "none")(lambda: no_redaction)
