"""Shared helpers of the reply-quality evaluation (the section in bench/chat/README.md describes the pipeline).

Not run by itself. The other scripts of this folder load it by path (the way the scripts of bench/chat load their
siblings) for: the file layout of a work folder, JSON files in the repository's style, the keys of hand-overs and of
pairs, the seeded coin that decides which reply is shown as `a`, the parser of a judgement string and the loader of
session_lab.py (replay and sign test are imported from it, never copied). Standard library only.
"""
import hashlib
import importlib.util
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHAT = os.path.abspath(os.path.join(HERE, ".."))

SEED = 20261121  # the one seed of the evaluation: shuffles, coins and the choice of repeated pairs
REPLY_ITEMS = 24  # hand-overs per writer file
JUDGE_ITEMS = 20  # pairs per judge file
REPLY_MIN, REPLY_MAX = 20, 60  # characters of a reply
REPEAT_ONE_IN = 10  # one pair in this many is judged a second time with the order swapped
BASE = "base"

JUDGEMENT_RE = re.compile(r"^a:u([01-]),d([01]),w([01]);b:u([01-]),d([01]),w([01]);better:(a|b|same)$")


def sibling(name, folder=CHAT):
    """A module of `folder` (default bench/chat) under the name bench_chat_<name>, the way the neighbouring scripts load
    one another (a plain import would clash with bench/external, which has modules of the same names)."""
    key = "bench_chat_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, os.path.join(folder, name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def session_lab():
    """session_lab.py, loaded once."""
    return sibling("session_lab")


def sign_test(gained, lost):
    """The exact two-sided sign test of session_lab (through model_lab)."""
    return session_lab().model_lab.sign_test(gained, lost)


def read_json(path):
    """The JSON document in `path` (UTF-8)."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, document):
    """Write `document` as UTF-8 JSON, characters as they are, LF, one final newline; make the folder when needed."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)
        handle.write("\n")


def emit(text=""):
    """Print a line as UTF-8 whatever the console's code page."""
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))


def digest(text):
    """The hex SHA-1 of a text."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def handover_text(handover):
    """The canonical text of a hand-over {persona, history, said, notes}: what a writer is shown, as one string."""
    return json.dumps({name: handover[name] for name in ("persona", "history", "said", "notes")},
                      ensure_ascii=False, sort_keys=True)


def front_of(row):
    """[{id, related}] of the memories in front of the model at a turn row of plan.json (this turn's blocks and the earlier
    blocks of the conversation); a row without `front` (an older plan) counts what it gave this turn."""
    return row["front"] if "front" in row else row["given"]


def reply_key(handover, salt=""):
    """The key of the reply to a hand-over: ten hex characters of the hash of its text plus the configuration's salt
    (only `aa` has one). Equal hand-overs share one reply."""
    return digest(handover_text(handover) + salt)[:10]


def pair_id(base_key, other_key):
    """The id of a pair (base reply key, configuration reply key). The same pair in two configurations is one pair."""
    return "p" + digest("%s|%s" % (base_key, other_key))[:10]


def repeat_id(pid):
    """The id the judge sees for the swapped second showing of pair `pid` (it does not look like the first)."""
    return "p" + digest("repeat|" + pid)[:10]


def _number(label, pid):
    return int(digest("%d:%s:%s" % (SEED, label, pid))[:8], 16)


def base_side(pid):
    """Where the base reply is shown for pair `pid`: "a" or "b", decided by a seeded coin (recorded only in pairs.json)."""
    return "a" if _number("coin", pid) % 2 == 0 else "b"


def repeat_selected(pid):
    """Whether pair `pid` is also shown a second time with the order swapped (about one pair in REPEAT_ONE_IN)."""
    return _number("repeat", pid) % REPEAT_ONE_IN == 0


def other_side(side):
    """The other of "a" and "b"."""
    return "b" if side == "a" else "a"


def chunks(items, size):
    """The list cut into lists of at most `size`."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def shuffled(items, seed=SEED):
    """The items in a seeded random order (a new list)."""
    items = list(items)
    random.Random(seed).shuffle(items)
    return items


def spread(items, size, signature, seed=SEED):
    """The items in the same order, except that items with the same `signature(item)` are swapped apart so that no chunk of
    `size` holds two of them (a writer or a judge must not see the same material twice in one file: twins would show how
    the files are built and could be answered in step). A swap partner is chosen with `seed` among the items elsewhere that
    create no new twins; when there is none the item stays. Deterministic."""
    items = list(items)
    sigs = [signature(item) for item in items]
    rng = random.Random(seed)

    def chunk_of(k):
        return range((k // size) * size, min(len(items), (k // size + 1) * size))

    def twins(k, sig, skip):
        return any(sigs[m] == sig for m in chunk_of(k) if m != skip)

    for i in range(len(items)):
        if not any(sigs[m] == sigs[i] for m in chunk_of(i) if m < i):
            continue
        candidates = [j for j in range(len(items)) if j // size != i // size
                      and not twins(i, sigs[j], i) and not twins(j, sigs[i], j)]
        if candidates:
            j = rng.choice(candidates)
            items[i], items[j] = items[j], items[i]
            sigs[i], sigs[j] = sigs[j], sigs[i]
    return items


def numbered_files(folder, prefix):
    """[(number, path)] of <prefix>-NN.json in `folder`, in number order."""
    found = []
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            match = re.fullmatch(re.escape(prefix) + r"-(\d+)\.json", name)
            if match:
                found.append((int(match.group(1)), os.path.join(folder, name)))
    return sorted(found)


def next_number(folder, prefix):
    """The number after the highest NN of <prefix>-NN.json in `folder` (1 when there is none): file numbers are never
    reused, so an output file always belongs to the input file of the same name."""
    files = numbered_files(folder, prefix)
    return files[-1][0] + 1 if files else 1


def parse_judgement(text):
    """{"a": {"u", "d", "w"}, "b": {...}, "better": "a"|"b"|"same"} from a judgement string such as
    `a:u1,d0,w0;b:u0,d1,w0;better:a` (u is None for `-`); None when the text does not parse."""
    match = JUDGEMENT_RE.match(text.strip()) if isinstance(text, str) else None
    if not match:
        return None
    g = match.groups()

    def side(u, d, w):
        return {"u": None if u == "-" else int(u), "d": int(d), "w": int(w)}

    return {"a": side(*g[0:3]), "b": side(*g[3:6]), "better": g[6]}


def reply_problem(text):
    """Why a reply is unusable (a short phrase), or None: not a string, more than one line, a double quote, outside the
    length limits."""
    if not isinstance(text, str):
        return "not a string"
    if "\n" in text or "\r" in text:
        return "more than one line"
    if '"' in text or "“" in text or "”" in text:
        return "double quote"
    if not REPLY_MIN <= len(text) <= REPLY_MAX:
        return "length %d" % len(text)
    return None
