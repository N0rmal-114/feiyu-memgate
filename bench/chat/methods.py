"""The experimental variants the chat test set compares (bench/chat/README.md). Plain functions, no I/O.

What is compared here is what the chat integrations could send to the gateway for one message, and
what could be added to what comes back. Nothing in this file touches a store, a clock or a file: the
runner (run.py) feeds it a conversation and takes the answer.

Query for a case (history, message), `history` being the turns before the message
(`{"role": "user" or "assistant", "content": str}`):

    v0   what the integrations do today: build_query(message, last user turn of the history)
    v1   a message that is short (fewer than `short` meaningful characters) or holds a deictic marker
         ("那", "它", "后来", ...) gets the tail of the last user turn (`context_chars`, default 120)
         and a newline in front of it; any other message goes alone
    v2   like v1, but the context is the last user turn and the last assistant turn, each cut to half
         of `context_chars`, in the order they were said
    v3   two queries: the message alone and the v2 query; the runner merges the two answers by memory id
         (the higher score wins) with `merge_scored`

All of them give None when the message is not worth a lookup at all (`build_query(message, None)` is
None: a command, a bare filler word), and with nothing to borrow from the history they give exactly what
v0 gives. The query never passes 500 characters, the gateway's limit; when it would, the context loses
its oldest characters first and the message is kept (a message of 500 characters or more goes alone, cut
to 500, as build_query does).

`is_question(message)` tells a question from a remark, for the runner's `--remark-profile`.

`expand(...)` is link expansion: after a lookup, follow each of the best delivered memories to the
memories that are close to it (what `neighbours_of` says) and add a few of them.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from feiyu_memgate.client import build_query, meaningful, previous_user_text  # noqa: E402

MAX_QUERY = 500  # the gateway's limit for one query
SHORT = 8  # build_query's own "short" length
CONTEXT_CHARS = 120
# Words that point back at something said before. Single characters on purpose: a remark such as
# "它多少钱来着" has no other mark. A false hit only costs a longer query.
DEICTIC = ("那", "这", "它", "他", "她", "后来", "然后", "还有", "之前", "刚才", "上次那")
QUESTION_MARKS = ("？", "?")
QUESTION_WORDS = ("吗", "什么", "怎么", "哪", "谁", "几", "多少", "是不是", "有没有", "为什么", "记得")
VARIANTS = ("v0", "v1", "v2", "v3")


# ------------------------------------------------------------------ the conversation

def last_turn(history, role):
    """(position, text) of the last turn of `role` in the history, (None, None) when there is none or it
    is empty. Like previous_user_text, it looks at the last turn of that role only and does not go further
    back when that one is blank."""
    if not isinstance(history, list):
        return None, None
    for position in range(len(history) - 1, -1, -1):
        turn = history[position]
        if not isinstance(turn, dict) or turn.get("role") != role:
            continue
        # previous_user_text knows how to read a string or a list of text parts; give it this one turn
        text = previous_user_text([{"role": "user", "content": turn.get("content")}])
        return (position, text.strip()) if text else (None, None)
    return None, None


def tail(text, size):
    return text[-size:] if size > 0 else ""


# ------------------------------------------------------------------ queries

def query_v0(history, message):
    """The query the integrations build today."""
    return build_query(message, previous_user_text(history))


def needs_context(message, short=SHORT, markers=DEICTIC):
    """True when the message is short or holds a deictic marker."""
    return len(meaningful(message)) < short or any(marker in message for marker in markers)


def _with_context(message, context):
    """The context's tail, a newline and the message, within MAX_QUERY."""
    current = message.strip()
    room = MAX_QUERY - len(current) - 1
    if room <= 0:
        return current[:MAX_QUERY]
    if not context.strip():
        return current
    return tail(context, room) + "\n" + current


def query_v1(history, message, short=SHORT, context_chars=CONTEXT_CHARS, markers=DEICTIC):
    alone = build_query(message, None)
    if alone is None or not needs_context(message, short, markers):
        return alone
    _, user = last_turn(history, "user")
    if not user:
        return alone
    return _with_context(message, tail(user, context_chars))


def query_v2(history, message, short=SHORT, context_chars=CONTEXT_CHARS, markers=DEICTIC):
    alone = build_query(message, None)
    if alone is None or not needs_context(message, short, markers):
        return alone
    half = context_chars // 2
    turns = []
    for role in ("user", "assistant"):
        position, text = last_turn(history, role)
        if text:
            turns.append((position, tail(text, half)))
    if not turns:
        return alone
    context = "\n".join(text for _, text in sorted(turns))
    return _with_context(message, context)


def queries_for(variant, history, message, short=SHORT, context_chars=CONTEXT_CHARS, markers=DEICTIC):
    """The list of queries to ask for one message: none (not worth a lookup), one, or for v3 two."""
    if variant == "v0":
        found = [query_v0(history, message)]
    elif variant == "v1":
        found = [query_v1(history, message, short, context_chars, markers)]
    elif variant == "v2":
        found = [query_v2(history, message, short, context_chars, markers)]
    elif variant == "v3":
        found = [build_query(message, None), query_v2(history, message, short, context_chars, markers)]
    else:
        raise ValueError("unknown query variant %r" % (variant,))
    out = []
    for query in found:
        if query is not None and query not in out:  # v3 with nothing to add asks once
            out.append(query)
    return out


def merge_scored(answers, limit):
    """[(memory id, score)] lists from several lookups as one list: each memory once with its highest
    score, best first (a tie keeps the order in which the memory was first seen), cut to `limit`."""
    best = {}
    for answer in answers:
        for memory, score in answer:
            if memory not in best or score > best[memory]:
                best[memory] = score
    return sorted(best.items(), key=lambda pair: -pair[1])[:limit]  # sorted is stable


# ------------------------------------------------------------------ question or remark

def is_question(message, words=QUESTION_WORDS):
    """True when the message holds a question mark (？ or ?) or one of `words`."""
    if not isinstance(message, str):
        return False
    return any(mark in message for mark in QUESTION_MARKS) or any(word in message for word in words)


# ------------------------------------------------------------------ links

def expand(delivered, neighbours_of, top, max_added, minimum):
    """Memories to add after a lookup.

    delivered       memory ids in delivery order
    neighbours_of   callable(memory id) -> [(memory id, score)]
    top             only the first `top` delivered memories are followed
    max_added       at most this many are added
    minimum         a neighbour needs at least this score

    A neighbour is left out when it was delivered already (any delivered memory, not only the first
    `top`) or is the memory it was found from. A memory found from several delivered memories counts once,
    with its highest score and the memory that gave it. Returns [{"id", "score", "source"}], best score
    first (a tie goes to the neighbour met first), at most `max_added`; "source" is the id of the memory it
    was found from.
    """
    if top <= 0 or max_added <= 0:
        return []
    taken = set(delivered)
    found = {}  # id -> [score, order met, source]
    for source in list(delivered)[:top]:
        for memory, score in neighbours_of(source):
            if memory in taken or memory == source or score < minimum:
                continue
            if memory not in found:
                found[memory] = [score, len(found), source]
            elif score > found[memory][0]:
                found[memory][0], found[memory][2] = score, source
    ranked = sorted(found.items(), key=lambda item: (-item[1][0], item[1][1]))
    return [{"id": memory, "score": score, "source": source} for memory, (score, _, source) in ranked[:max_added]]
