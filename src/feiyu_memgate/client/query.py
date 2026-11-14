"""What to ask the memory gateway for one chat message. Standard library only.

The default QueryShaper suits Chinese chat. Other languages pass their own
filler characters, a different "short" length or extra skip words.
"""
import re
import unicodedata

FILLER_CHARS = set("嗯哦噢啊哈呵嘿好行对是")
SHORT_CHARS = 8

_PLACEHOLDER_RE = re.compile(r"\[[^\[\]]{1,8}\]")
# a keycap emoji ("1" + FE0F + 20E3) is one picture, not the digit
_KEYCAP_RE = re.compile("[0-9#*]" + chr(0xFE0F) + "?" + chr(0x20E3))
_VARIATION_SELECTORS = {chr(c) for c in range(0xFE00, 0xFE10)}


def meaningful(text):
    """The characters left after bracket placeholders ([图片]), emoji,
    punctuation and whitespace are removed."""
    if not isinstance(text, str):
        return ""
    text = _KEYCAP_RE.sub("", _PLACEHOLDER_RE.sub("", text))
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat[0] in "PZC" or cat in ("So", "Sk", "Me") or ch in _VARIATION_SELECTORS or ch in "~～":
            continue
        out.append(ch)
    return "".join(out)


class QueryShaper:
    """Decides whether a message is worth a lookup and what the query is.

    filler_chars   a message of at most two meaningful characters, all of them
                   in this set, is not worth a lookup
    short_chars    a message with fewer meaningful characters than this borrows the
                   previous user message for context
    max_chars      the query length cap (the gateway's limit is 500)
    skip_prefixes  a message starting with one of these characters is a command
    skip_words     whole messages (compared in lower case) that are not worth a lookup
    """

    def __init__(self, filler_chars=FILLER_CHARS, short_chars=SHORT_CHARS, max_chars=500,
                 skip_prefixes="/／", skip_words=("ok",)):
        self.filler_chars = set(filler_chars)
        self.short_chars = short_chars
        self.max_chars = max_chars
        self.skip_prefixes = skip_prefixes
        self.skip_words = frozenset(w.lower() for w in skip_words)

    def build(self, current, previous):
        """The query for this message, or None when it is not worth a lookup."""
        if not isinstance(current, str):
            return None
        cur = current.strip()
        if not cur or cur[0] in self.skip_prefixes:
            return None
        core = meaningful(cur)
        if not core or core.lower() in self.skip_words:
            return None
        if len(core) <= 2 and all(ch in self.filler_chars for ch in core):
            return None
        prev = previous.strip() if isinstance(previous, str) else ""
        if len(core) < self.short_chars and prev:
            room = self.max_chars - len(cur) - 1
            if room <= 0:
                return cur[:self.max_chars]
            return prev[-room:] + "\n" + cur
        return cur[:self.max_chars]


_DEFAULT = QueryShaper()


def build_query(current, previous, max_chars=500):
    """The query for this message, or None when it is not worth a lookup."""
    shaper = _DEFAULT if max_chars == _DEFAULT.max_chars else QueryShaper(max_chars=max_chars)
    return shaper.build(current, previous)


def previous_user_text(contexts):
    """Text of the last user message in req.contexts, None when there is none."""
    if not isinstance(contexts, list):
        return None
    for item in reversed(contexts):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(p["text"] for p in content
                           if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str))
        else:
            return None
        return text if text.strip() else None
    return None
