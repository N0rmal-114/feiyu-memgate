"""A lab for recall over a conversation, replaying scripted sessions (experimental, not product code). Standard library only.

    python -B bench/chat/session_lab.py --split bench/chat/dev --cache <file> --texts replies.json --baseline
    python -B bench/chat/session_lab.py --split bench/chat/dev --cache <file> --texts replies.json \\
        --run "after:max=2;pool:size=3;compact:every=3" --explain dev-teacher/s01

What it does. The single-turn cases cannot tell what happens over a conversation, so a world may carry `sessions`
(bench/chat/README.md): a few turns each, scripted user lines and companion replies, labelled per turn with the cluster
the turn is about and the memories that should be in front of the model. This script replays every session turn by turn
as a chat plugin would meet it and compares ways to give memories over time, each as a pure function over cached vectors.
It is the third bench of the family after vector_lab.py and model_lab.py and reuses them (the pipeline copy and the
product lookup of model_lab.LabEngine, the cache, the sign test) instead of copying. Worlds without sessions are
skipped. It never calls a model: the texts a model wrote (the companion's real replies) arrive as files, `--texts`,
in model_lab's format, kind `reply`, ids "<world>/<session id>/<turn number>" (turns count from 1 through the session).

State of a conversation: `history` (the earlier turns of this conversation), `shown` (the memories a block in the
history shows: a memory given in a turn stays in front of the model, and one already shown is not given again) and the
conversation pool. A turn:

    1. a `new_conversation` turn empties history, shown and pool (the old pool is kept aside for `recent`);
    2. compact:every=M empties `shown` before turn i of a conversation when i > 1 and (i - 1) % M == 0 (the host
       compacted the history: the blocks are gone, the lines stay);
    3. the pool ages and strengthens (decay, +1 for a memory the user's line mentions);
    4. main lookup: the product lookup (model_lab.LabEngine.lookup) for the v0 query of the turn; `main` is what it
       finds that is not in `shown`;
    5. extra block, in this order, never a memory in `shown` or `main`: the memories the second lookup of the
       previous turn left (after), the pool's pick (pool), at a new conversation the old pool's strongest (recent);
    6. `shown` grows by main and extra, all of them enter the pool;
    7. turn end, variant after: one product lookup with the turn's reply text as the query; what it finds that is not
       in `shown`, the first N, is left for the next turn of the same conversation. A turn without a reply text
       leaves nothing.

The product, `product`, has no extra block. A variant is a spec string, parts separated by ";", each part
`name:key=value,key=value` (one part per family):

    after    max=2        prototype 1, the second lookup with the assistant's reply, at most `max` memories
    pool     size=3       prototype 2, the conversation pool gives back at most `size` memories after a compaction
    recent   size=3       not built yet: at a new conversation, the strongest memories of the previous conversation's pool
    compact  every=<M>    the history is compacted before turn i of a conversation when i > 1 and (i - 1) % M == 0
    noise    hub top each lead cap   filters that only REMOVE memories from what the product delivers
             (bench/chat/noise.py describes them; any subset in one part, all off by default). hub, top and each act
             inside every lookup (main and second); in a turn the memories already shown are taken out, then lead,
             then cap. `noise` combines with every other family.

`compact` alone is allowed (it shows what compaction costs); the defaults are the plugin's. The report puts the
variant next to the product (and, when the variant compacts, next to the product under the same compaction): per kind
of turn the share of turns with a `direct` memory in front of the model after the turn (`in front`) and the share where
one arrived at this very turn (`fresh`), what arrived per turn and how much of it is related to the turn, flips of
`in front` against the product with an exact sign test, idle turns, the memories in front at the end of a conversation,
and the result per world. --sweep tries a small fixed grid on the development split (it refuses the test split even
with --final). --explain <world>/<session> traces one session under every --run.

Deterministic. A split folder named `test` is refused unless --final is given.
"""
import argparse
import importlib.util
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))


def _sibling(name):
    """A module of this folder under the name bench_chat_<name> (see run.py for why it is not a plain import)."""
    key = "bench_chat_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, os.path.join(HERE, name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


model_lab = _sibling("model_lab")
lab = model_lab.lab
noise_lab = model_lab.noise_lab
run = model_lab.run
methods = model_lab.methods

REPLY_KIND = "reply"
KIND_ORDER = ("open", "named", "continue", "idle", "return", "nextday")
ALL = "all"

# The conversation pool, as plugins/dsh-feiyu-memgate/src/pool.js has it (the constants and the rules below).
DECAY = 0.8  # multiplied into every strength at the start of a turn
FORGET_BELOW = 0.2  # an item weaker than this is dropped
PICK_FROM = 1  # an item at least this strong may be brought back
MAX_ITEMS = 24  # per conversation; beyond it the weakest go
MENTION_MIN = 3  # characters a message and a memory must share to count as a mention

# ASCII words too common to show that a message is about a memory (pool.js STOP_WORDS).
STOP_WORDS = frozenset((
    "the and for are was were but not you your his her its our their with that this these those have has had from they "
    "them what when who will would can could about into than then there here also just all any user users").split())

_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_NOT_ASCII_RE = re.compile(r"[^\x00-\x7f]+")


# ------------------------------------------------------------------ the pool (mirrors pool.js)

def _runs_of(text):
    """The runs of meaningful characters of a text: whatever lies between two characters that `meaningful` drops (a
    space, punctuation, an emoji) ends a run (pool.js runsOf; the character test is the client's `meaningful`)."""
    if not isinstance(text, str):
        return []
    runs, current = [], []
    for ch in text:
        if methods.meaningful(ch):
            current.append(ch)
        elif current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    return runs


def _words_of(runs):
    words = set()
    for run_text in runs:
        for word in _WORD_RE.findall(run_text):
            lower = word.lower()
            if len(lower) >= MENTION_MIN and lower not in STOP_WORDS:
                words.add(lower)
    return words


def _windows_of(runs):
    windows = set()
    for run_text in runs:
        for part in _NOT_ASCII_RE.findall(run_text):
            for i in range(len(part) - MENTION_MIN + 1):
                windows.add(part[i:i + MENTION_MIN])
    return windows


def mentions(message, memory_text):
    """Whether a user message mentions a memory: they share a run of at least MENTION_MIN meaningful characters. Text
    without spaces (Chinese, Japanese) is compared by any shared run of that length; for words written in ASCII letters
    and digits the shared run must be a whole word, and the commonest English words do not count (pool.js mentions)."""
    a, b = _runs_of(message), _runs_of(memory_text)
    if not a or not b:
        return False
    words = _words_of(b)
    if words and not words.isdisjoint(_words_of(a)):
        return True
    windows = _windows_of(b)
    return bool(windows) and not windows.isdisjoint(_windows_of(a))


def new_pool():
    """The state of one conversation's pool: the turn count and {memory id: {"strength", "turn"}} in entry order."""
    return {"turn": 0, "items": {}}


def advance(state, message=None, text_of=None, decay=DECAY):
    """A new user turn begins (pool.js advance with decay on, which is what the plugin does with the pool enabled): count
    it, weaken every item and drop the ones below FORGET_BELOW, then every item the message mentions gains 1.
    `text_of` maps a memory id to its text; `decay` is the rules' per-turn factor."""
    state["turn"] += 1
    items = state["items"]
    for key in list(items):
        items[key]["strength"] *= decay
        if items[key]["strength"] < FORGET_BELOW:
            del items[key]
    if isinstance(message, str) and message:
        for key, item in items.items():
            if mentions(message, (text_of or {}).get(key, "")):
                item["strength"] += 1


def enter(state, ids, turn=None, scores=None):
    """Memories were given this turn (pool.js enter): a new one enters at strength 1, a known one gains 1; both take the
    turn number. Over MAX_ITEMS the weakest go; of equal strength the one given longest ago, then the one entered first.
    With `scores` ({id: value}, the rules' score=on) the value stands in for the 1 of both cases."""
    turn = state["turn"] if turn is None else turn
    items = state["items"]
    for mid in ids:
        value = 1 if scores is None else scores.get(mid, 1)
        known = items.get(mid)
        if known:
            known["strength"] += value
            known["turn"] = turn
        else:
            items[mid] = {"strength": value, "turn": turn}
    if len(items) > MAX_ITEMS:
        order = sorted(enumerate(items), key=lambda p: (items[p[1]]["strength"], items[p[1]]["turn"], p[0]))
        for _, mid in order[:len(items) - MAX_ITEMS]:
            del items[mid]


def pick(state, limit, skip=None, floor=PICK_FROM):
    """The items to bring back (pool.js pick): at least `floor` (PICK_FROM) strong, not skipped (a predicate over the id),
    strongest first, then most recently given, then in the order they entered; at most `limit`."""
    items = state["items"]
    order = [(index, mid) for index, mid in enumerate(items)
             if items[mid]["strength"] >= floor and not (skip and skip(mid))]
    order.sort(key=lambda p: (-items[p[1]]["strength"], -items[p[1]]["turn"], p[0]))
    return [mid for _, mid in order[:max(0, limit)]]


# ------------------------------------------------------------------ specs

FAMILIES = ("after", "pool", "recent", "prime", "rules", "noise", "compact")  # the order of a spec's label
DEFAULTS = {
    "after": {"max": 2},
    "pool": {"size": 3},
    "recent": {"size": 3},
    "prime": {"bonus": 0.15, "size": 3},
    "rules": {"from": PICK_FROM + 0.0, "decay": DECAY, "used": "none", "score": "off"},
    "noise": dict(noise_lab.DEFAULTS),
    "compact": {"every": None},
}
_TYPES = {"max": int, "size": int, "every": int, "bonus": float, "from": float, "decay": float, "used": str,
          "score": str, "hub": float, "k": int, "top": float, "each": float, "lead": float, "cap": int}
_CHOICES = {("rules", "used"): ("none", "reply"), ("rules", "score"): ("off", "on")}
PRODUCT_NAMES = ("", "product", "baseline")
EXTRA_SCORE = 0.5  # rules score=on: the strength of an extra-block memory, which has no lookup score at that turn
PRIME_TURNS = 2  # prime: the turns of a new conversation in which the previous conversation's pool is still consulted


def _check_part(part, values):
    for name, value in values.items():
        if value is None:
            continue
        kind = _TYPES[name]
        if kind is int and value < 1:
            raise SystemExit("%s: %s must be at least 1" % (part, name))
        if kind is float and not math.isfinite(value):
            raise SystemExit("%s: %s must be a finite number" % (part, name))
        if (part, name) in _CHOICES and value not in _CHOICES[(part, name)]:
            raise SystemExit("%s: %s must be one of %s" % (part, name, ", ".join(_CHOICES[(part, name)])))
    if part == "compact" and values["every"] is None:
        raise SystemExit("compact: every=<turns> is required (the history is compacted every that many turns)")
    if part == "noise":
        noise_lab.check(values, sessions=True)
    if part == "rules" and not values["from"] > 0:
        raise SystemExit("rules: from must be above 0")
    if part == "rules" and not 0 < values["decay"] <= 1:
        raise SystemExit("rules: decay must be above 0 and at most 1")
    if part == "prime" and values["bonus"] < 0:
        raise SystemExit("prime: bonus must be at least 0")


def parse_spec(text):
    """{"after": dict or None, "pool": ..., "recent": ..., "prime": ..., "rules": ..., "compact": ...} from a spec
    string; "", "product" and "baseline" are the product (no extra block, no compaction). SystemExit with a message
    when the text is not a spec."""
    config = {name: None for name in FAMILIES}
    text = (text or "").strip()
    if text in PRODUCT_NAMES:
        return config
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        part, _, rest = chunk.partition(":")
        part = part.strip()
        if part not in DEFAULTS:
            raise SystemExit("unknown variant part %r (known: %s)" % (part, ", ".join(DEFAULTS)))
        if config[part] is not None:
            raise SystemExit("%s given twice: a variant has one part per family" % part)
        values = dict(DEFAULTS[part])
        for pair in filter(None, (p.strip() for p in rest.split(","))):
            name, equals, raw = pair.partition("=")
            name = name.strip()
            if not equals or name not in values:
                raise SystemExit("%s: unknown or malformed setting %r (known: %s)" % (part, pair, ", ".join(values)))
            kind = _TYPES[name]
            try:
                values[name] = kind(raw.strip())
            except ValueError:
                raise SystemExit("%s: %s must be a %s, not %r" % (
                    part, name, {int: "whole number", float: "number", str: "word"}[kind], raw.strip()))
        _check_part(part, values)
        config[part] = values if part != "noise" or noise_lab.active(values) else None
    if config["prime"] and (config["pool"] or config["recent"]):
        raise SystemExit("prime cannot be combined with pool or recent: it replaces them")
    return config


def spec_label(config):
    """The canonical spec string of a parsed config ("product" when nothing differs)."""
    parts = []
    for part in FAMILIES:
        values = config.get(part)
        if values:
            shown = (noise_lab.label(values) if part == "noise"
                     else ",".join("%s=%s" % (name, values[name]) for name in DEFAULTS[part]))
            parts.append("%s:%s" % (part, shown))
    return ";".join(parts) or "product"


def rules_of(config):
    """The pool rules of a config: the `rules` part with its defaults (today's behaviour) when there is none."""
    return dict(config["rules"] or DEFAULTS["rules"])


def reference_config(config):
    """The product under the same compaction as `config` (None when the variant does not compact or compacts alone:
    the product itself is then the only reference)."""
    if config["compact"] and any(config[name] for name in ("after", "pool", "recent", "prime", "noise")):
        return {**{name: None for name in FAMILIES}, "compact": config["compact"]}
    return None


def check_sources(configs, texts):
    """SystemExit when a config asks for reply texts and none were given."""
    for config in configs:
        for part, wanted in (("after", config["after"]), ("rules: used=reply", rules_of(config)["used"] == "reply")):
            if wanted and REPLY_KIND not in texts:
                raise SystemExit("%s: no texts of kind %r were given (--texts); known kinds: %s" % (
                    part, REPLY_KIND, ", ".join(sorted(texts)) or "none"))


# ------------------------------------------------------------------ the engine and the replay

class SessionEngine(model_lab.LabEngine):
    """model_lab's LabEngine for one world with sessions, with its product lookups kept per query string."""

    def __init__(self, world, vectors_of, scale):
        super().__init__(world, vectors_of, scale)
        self.sessions = world.get("sessions") or []
        self.text_of = dict(zip(self.ids, self.texts))
        self.position = {mid: i for i, mid in enumerate(self.ids)}
        self._found = {}

    def found(self, query, noise=None):
        """[(id, score)] of one product lookup for `query` (delivered, then the linked additions), each memory once;
        with the `noise` filters that act inside a lookup (hub, top, each) when they are on."""
        key = (query,) + tuple((noise or {}).get(name) for name in noise_lab.LOOKUP_KEYS)
        if key not in self._found:
            out, seen = [], set()
            for mid, score in self.noisy_lookup(self.cosines_for(query, None), noise)["items"]:
                if mid not in seen:
                    seen.add(mid)
                    out.append((mid, score))
            self._found[key] = out
        return self._found[key]

    def members(self, cluster):
        return {mid for mid, memory in self.world["memories"].items() if cluster and memory.get("cluster") == cluster}


def walk_turns(session):
    """(number, turn, history, new, conversation, turn in conversation) for every turn of a session. `history` is the
    list of the earlier turns of this conversation as {"role", "content"}; it grows after the consumer is done with the
    turn, so it must not be kept. `new` tells that the turn starts a new conversation."""
    history = []
    conversation = 0
    position = 0
    for number, turn in enumerate(session.get("turns") or [], 1):
        new = bool(turn.get("new_conversation"))
        if number == 1 or new:
            conversation += 1
            position = 0
            history = []
        position += 1
        yield number, turn, history, new, conversation, position
        if turn.get("user"):
            history.append({"role": "user", "content": turn["user"]})
        if turn.get("assistant"):
            history.append({"role": "assistant", "content": turn["assistant"]})


def turn_queries(session):
    """[(turn number, the v0 query or None)] for a session, the history being that of the turn's conversation."""
    return [(number, (methods.queries_for("v0", history, turn.get("user") or "") or [None])[0])
            for number, turn, history, _, _, _ in walk_turns(session)]


def primed(engine, settings, query, pool, old, skipped):
    """[(id, value)] for prime: every pool item (this conversation's, and `old`, the previous conversation's pool as it
    ended, when it is still consulted; the stronger strength counts) that is not skipped gets score(query, memory) +
    bonus x min(1, strength), the score being the memory's on the store's scale (0 without a query). Those whose value
    reaches the store's broad minimum score line are kept, best value first, at most `size`."""
    strengths = {}
    for source in (old, pool):
        for mid, item in (source["items"].items() if source else ()):
            strengths[mid] = max(strengths.get(mid, 0.0), item["strength"])
    cosines = engine.cosines_for(query, None) if query else None
    found = []
    for index, (mid, strength) in enumerate(strengths.items()):
        if skipped(mid):
            continue
        base = engine.scale.score(cosines[engine.position[mid]]) if cosines else 0.0
        value = base + settings["bonus"] * min(1.0, strength)
        if value >= engine.scale.broad_min_score:
            found.append((-value, index, mid))
    found.sort()
    return [(mid, -negative) for negative, _, mid in found[:settings["size"]]]


def replay_session(engine, session, config, replies, trace=False):
    """One record per turn of the session under `config` (see the module docstring for the turn's steps). `replies` maps
    "<world>/<session id>/<turn number>" to the reply text. With `trace` a record also holds the pool's strengths and
    the ids in `shown` after the turn."""
    sid = session["id"]
    shown = {}  # an ordered set
    pool, old, pending = new_pool(), None, []
    old_left = 0  # turns left in which prime still consults the previous conversation's pool
    rules = rules_of(config)
    records = []
    previous_cluster = None
    turns = list(session.get("turns") or [])
    for number, turn, history, new, conversation, position in walk_turns(session):
        if new:
            old, pool, shown, pending, old_left = pool, new_pool(), {}, [], PRIME_TURNS
        use_old = old_left > 0
        old_left = max(0, old_left - 1)
        compact = config["compact"]
        if compact and position > 1 and (position - 1) % compact["every"] == 0:
            shown = {}
        user = turn.get("user") or ""
        advance(pool, user, engine.text_of, rules["decay"])
        queries = methods.queries_for("v0", history, user)
        query = queries[0] if queries else None
        main = noise_lab.keep_session_part(engine.found(query, config["noise"]) if query else [], shown,
                                           config["noise"], position)
        taken = {mid for mid, _ in main}
        extra = []

        def add(mid, source, score):
            if mid not in shown and mid not in taken:
                taken.add(mid)
                extra.append((mid, source, score))

        def skipped(mid):
            return mid in shown or mid in taken

        if config["after"]:
            for mid, score in pending:
                add(mid, "after", score)
        if config["pool"]:
            for mid in pick(pool, config["pool"]["size"], skipped, rules["from"]):
                add(mid, "pool", None)
        if config["recent"] and new and old is not None:
            for mid in pick(old, config["recent"]["size"], skipped, rules["from"]):
                add(mid, "recent", None)
        if config["prime"]:
            for mid, value in primed(engine, config["prime"], query, pool, old if use_old else None, skipped):
                add(mid, "prime", value)
        arrived = [mid for mid, _ in main] + [mid for mid, _, _ in extra]
        for mid in arrived:
            shown[mid] = None
        values = None
        if rules["score"] == "on":
            values = {mid: score for mid, score in main}
            values.update({mid: EXTRA_SCORE for mid, _, _ in extra})
        enter(pool, arrived, scores=values)
        reply = replies.get("%s/%s/%d" % (engine.name, sid, number))
        if rules["used"] == "reply" and reply:
            for mid, item in pool["items"].items():
                if mentions(reply, engine.text_of.get(mid, "")):
                    item["strength"] += 1

        pending = []
        if config["after"]:
            if reply:
                pending = [(mid, score) for mid, score in engine.found(reply, config["noise"]) if mid not in shown][:config["after"]["max"]]

        cluster = turn.get("cluster")
        direct = list(turn.get("direct") or [])
        also_ok = list(turn.get("also_ok") or [])
        related = set(direct) | set(also_ok) | engine.members(cluster)
        before = engine.members(previous_cluster)
        record = {
            "world": engine.name, "session": sid, "turn": number, "kind": turn.get("kind"),
            "conversation": conversation, "new_conversation": new, "last_of_conversation": number == len(turns)
            or bool(turns[number].get("new_conversation")),
            "query": query, "cluster": cluster, "prev_cluster": previous_cluster, "direct": direct, "also_ok": also_ok,
            "main": [{"id": mid, "score": round(score, 6), "related": mid in related} for mid, score in main],
            "extra": [{"id": mid, "from": source, "score": None if score is None else round(score, 6),
                       "related": mid in related, "prev": mid in before, "neither": mid not in related and mid not in before}
                      for mid, source, score in extra],
            "shown": len(shown),
            "in_front": any(d in shown for d in direct) if direct else None,
            "fresh": any(d in arrived for d in direct) if direct else None,
            "left": [mid for mid, _ in pending],
            "sources": {name: sum(1 for _, source, _ in extra if source == name)
                        for name in ("after", "pool", "recent", "prime")},
        }
        if trace:
            record["pool"] = {mid: round(item["strength"], 4) for mid, item in pool["items"].items()}
            record["shown_ids"] = list(shown)
        records.append(record)
        previous_cluster = cluster
    return records


def run_variant(engines, config, replies, label=None, trace=False):
    records = [r for engine in engines for session in engine.sessions
               for r in replay_session(engine, session, config, replies, trace)]
    return {"label": label or spec_label(config), "config": config, "records": records, "summary": summarize(records)}


# ------------------------------------------------------------------ summaries

def _share(count, total):
    return None if not total else count / total


def stats(records):
    """The numbers of a group of turn records: shares are None where their denominator is empty. `in_front` and `fresh`
    count the turns that have a `direct` memory; the related shares count memories."""
    n = len(records)
    directed = [r for r in records if r["in_front"] is not None]
    main = [m for r in records for m in r["main"]]
    extra = [m for r in records for m in r["extra"]]
    return {
        "n": n, "n_direct": len(directed),
        "in_front": _share(sum(r["in_front"] for r in directed), len(directed)),
        "fresh": _share(sum(r["fresh"] for r in directed), len(directed)),
        "main_per_turn": _share(len(main), n), "extra_per_turn": _share(len(extra), n),
        "arrived_per_turn": _share(len(main) + len(extra), n),
        "main_related": _share(sum(m["related"] for m in main), len(main)),
        "extra_related": _share(sum(m["related"] for m in extra), len(extra)),
        "extra_prev": _share(sum(m["prev"] for m in extra), len(extra)),
        "extra_neither": _share(sum(m["neither"] for m in extra), len(extra)),
        "nothing": _share(sum(1 for r in records if not r["main"] and not r["extra"]), n),
        "unrelated_per_turn": _share(sum(1 for m in main + extra if not m["related"]), n),
    }


def summarize(records):
    """{"kinds": {kind: stats}, "all": stats, "worlds": {world: in front}, "conversations": {"n", "shown_end"}}."""
    by_kind, by_world = {}, {}
    for record in records:
        by_kind.setdefault(record["kind"] or "unlabelled", []).append(record)
        by_world.setdefault(record["world"], []).append(record)
    ends = [r["shown"] for r in records if r["last_of_conversation"]]
    return {"kinds": {kind: stats(group) for kind, group in by_kind.items()}, "all": stats(records),
            "worlds": {world: stats(group)["in_front"] for world, group in sorted(by_world.items())},
            "conversations": {"n": len(ends), "shown_end": sum(ends) / len(ends) if ends else None}}


def _key(record):
    return record["world"], record["session"], record["turn"]


def flips(base_records, records):
    """How `in front` differs from the base's, matched by world, session and turn (turns without a `direct` memory are
    not counted): gained and lost overall and per kind, and the exact two-sided sign test on the overall flips."""
    by_key = {_key(r): r for r in base_records}
    out = {"gained": 0, "lost": 0, "kinds": {}}
    for record in records:
        if record["in_front"] is None:
            continue
        was, now = by_key[_key(record)]["in_front"], record["in_front"]
        row = out["kinds"].setdefault(record["kind"] or "unlabelled", [0, 0])
        row[0] += int(now and not was)
        row[1] += int(was and not now)
    out["gained"] = sum(g for g, _ in out["kinds"].values())
    out["lost"] = sum(ln for _, ln in out["kinds"].values())
    out["p"] = model_lab.sign_test(out["gained"], out["lost"])
    return out


# ------------------------------------------------------------------ the report

def _pct(value):
    return "-" if value is None else "%.1f" % (value * 100)


def _num(value, places=2):
    return "-" if value is None else ("%." + str(places) + "f") % value


def _names(results):
    present = {kind for result in results for kind in result["summary"]["kinds"]}
    known = [k for k in KIND_ORDER if k in present]
    return known + sorted(present - set(known))


def _stat(result, kind):
    return result["summary"]["all"] if kind == ALL else result["summary"]["kinds"].get(kind) or {}


def _table(header, rows):
    return ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"] + [
        "| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]


def report_lines(result, refs, name="variant"):
    """Markdown lines for one run: `refs` are the (name, result) the variant is shown next to (the product first, the
    product under the same compaction second when there is one); `name` is the variant's column title."""
    columns = list(refs) + [(name, result)]
    names = _names([r for _, r in columns]) + [ALL]
    totals = result["summary"]["all"]
    sessions = len({(r["world"], r["session"]) for r in result["records"]})
    lines = ["variant: %s" % result["label"], "",
             "%d turns of %d sessions in %d worlds" % (totals["n"], sessions, len(result["summary"]["worlds"])), ""]

    header = ["kind", "turns"] + ["in front %% (%s)" % name for name, _ in columns] + [
        "fresh %% (%s)" % name for name, _ in columns]
    rows = [[kind, _stat(result, kind).get("n", 0)] + [_pct(_stat(r, kind).get("in_front")) for _, r in columns]
            + [_pct(_stat(r, kind).get("fresh")) for _, r in columns] for kind in names]
    lines += _table(header, rows)

    found = [(ref_name, flips(r["records"], result["records"])) for ref_name, r in refs]
    if found:
        header = ["flips of in front"] + sum(([("gained vs %s" % n), ("lost vs %s" % n)] for n, _ in found), [])
        rows = [[kind] + sum(([f["gained"], f["lost"]] if kind == ALL else f["kinds"].get(kind, [0, 0])
                              for _, f in found), []) for kind in names]
        lines += [""] + _table(header, rows) + [""]
        for ref_name, f in found:
            lines.append("sign test on the overall flips against %s (exact, two-sided): p = %.4f" % (ref_name, f["p"]))

    header = ["arrived per turn", "turns"] + ["main (%s)" % n for n, _ in columns] + [
        "main related %% (%s)" % n for n, _ in columns] + [
        "extra block", "extra related %", "extra of the previous turn's cluster %", "extra related to neither %"] + [
        "unrelated arrived (%s)" % n for n, _ in columns]
    rows = []
    for kind in names:
        mine = _stat(result, kind)
        rows.append([kind, mine.get("n", 0)] + [_num(_stat(r, kind).get("main_per_turn")) for _, r in columns]
                    + [_pct(_stat(r, kind).get("main_related")) for _, r in columns]
                    + [_num(mine.get("extra_per_turn")), _pct(mine.get("extra_related")), _pct(mine.get("extra_prev")),
                       _pct(mine.get("extra_neither"))]
                    + [_num(_stat(r, kind).get("unrelated_per_turn")) for _, r in columns])
    lines += [""] + _table(header, rows)

    idle = [_stat(r, "idle") for _, r in columns]
    if idle[-1].get("n"):
        lines += ["", *_table(["idle turns"] + [name for name, _ in columns],
                              [["turns"] + [s.get("n", 0) for s in idle],
                               ["nothing arrived %"] + [_pct(s.get("nothing")) for s in idle],
                               ["arrived per turn"] + [_num(s.get("arrived_per_turn")) for s in idle]])]

    ends = [r["summary"]["conversations"] for _, r in columns]
    lines += ["", *_table(["per conversation"] + [name for name, _ in columns],
                          [["conversations"] + [c["n"] for c in ends],
                           ["memories in front at its end (mean)"] + [_num(c["shown_end"]) for c in ends]])]

    worlds = list(result["summary"]["worlds"])
    lines += ["", *_table(["world"] + ["in front %% (%s)" % name for name, _ in columns],
                          [[w] + [_pct(r["summary"]["worlds"].get(w)) for _, r in columns] for w in worlds])]
    return lines


def run_document(result, refs):
    """The JSON form of a run: spec, summary, flips against every reference and the per-turn records."""
    return {"spec": result["label"], "summary": result["summary"],
            "flips": {name: flips(r["records"], result["records"]) for name, r in refs}, "records": result["records"]}


# ------------------------------------------------------------------ sweep

def _canonical(specs):
    return [spec_label(parse_spec(spec)) for spec in specs]


def grid_after():
    return ["after:max=%d" % n for n in (1, 2, 3)]


def grid_pool():
    return ["pool:size=%d;compact:every=%d" % (size, every) for every in (2, 3, 4) for size in (1, 2, 3, 5)]


def grid_recent():
    return ["recent:size=%d" % n for n in (1, 2, 3, 5)]


def _rule_grid(base, tail=""):
    return _canonical("%s;rules:from=%s,used=%s,score=%s%s" % (base, low, used, score, tail)
                      for low in (0.3, 0.5, 1.0) for used in ("none", "reply") for score in ("off", "on"))


def grid_rules_pool():
    return _rule_grid("pool:size=3", ";compact:every=3")


def grid_rules_recent():
    return _rule_grid("recent:size=3")


def grid_prime():
    return _canonical("prime:bonus=%s,size=%d;rules:used=%s%s" % (bonus, size, used, compact)
                      for compact in ("", ";compact:every=3") for used in ("reply", "none")
                      for bonus in (0.05, 0.1, 0.15, 0.2, 0.3) for size in (2, 3))


def grid_noise():
    return _canonical(
        ["noise:hub=%s,k=%d" % (lam, k) for lam in (0.25, 0.5, 1.0, 2.0) for k in (5, 10, 20)]
        + ["noise:top=%s" % t for t in (1.5, 2.0, 2.5, 3.0, 3.5)]
        + ["noise:each=%s" % z for z in (0.5, 1.0, 1.5, 2.0)]
        + ["noise:lead=%s" % r for r in (0.7, 0.8, 0.9, 0.95, 1.01)]
        + ["noise:cap=%d" % n for n in (1, 2, 3)])


GRIDS = {"after": grid_after, "pool": grid_pool, "recent": grid_recent, "rules-pool": grid_rules_pool,
         "rules-recent": grid_rules_recent, "prime": grid_prime, "noise": grid_noise}
SWEEP_FAMILIES = tuple(GRIDS)
# The kinds of turn each family is about (their `in front` is shown per row); nextday and return are always shown.
MATTER = {"after": ("continue", "return"), "pool": ("continue", "return", "nextday"),
          "recent": ("nextday",), "rules-pool": ("continue", "return", "nextday"), "rules-recent": ("nextday",),
          "prime": ("nextday", "return", "continue"),
          "noise": ("open", "named", "continue", "return", "nextday")}
ALWAYS = ("nextday", "return")


def _shown_kinds(family):
    return tuple(MATTER[family]) + tuple(k for k in ALWAYS if k not in MATTER[family])


def sweep_table(family, base, results, refs):
    """One table: the product, the compaction-only references (when a setting compacts), then a row per grid setting.
    `refs` maps the label of a reference to its result."""
    matter = _shown_kinds(family)
    with_ref = bool(refs)
    header = (["variant"] + ["in front %% (%s)" % kind for kind in matter]
              + ["in front % (all)", "gained", "lost", "p"] + (["gained vs same compaction", "lost vs same compaction"]
                                                               if with_ref else [])
              + (["lost (%s)" % kind for kind in matter] if family == "noise" else [])
              + ["extra per turn", "extra related %", "main related %", "arrived per turn", "unrelated arrived per turn",
                 "idle: nothing arrived %", "memories in front at the end"])

    def row(result, reference=None, is_base=False):
        mine = result["summary"]
        f = flips(base["records"], result["records"])
        cells = [result["label"]] + [_pct(_stat(result, kind).get("in_front")) for kind in matter]
        cells += [_pct(mine["all"]["in_front"])]
        cells += ["-", "-", "-"] if is_base else [f["gained"], f["lost"], "%.4f" % f["p"]]
        if with_ref:
            g = flips(reference["records"], result["records"]) if reference else None
            cells += [g["gained"], g["lost"]] if g and not is_base else ["-", "-"]
        if family == "noise":
            cells += ["-" if is_base else f["kinds"].get(kind, [0, 0])[1] for kind in matter]
        cells += [_num(mine["all"]["extra_per_turn"]), _pct(mine["all"]["extra_related"]),
                  _pct(mine["all"]["main_related"]), _num(mine["all"]["arrived_per_turn"]),
                  _num(mine["all"]["unrelated_per_turn"]), _pct(_stat(result, "idle").get("nothing")),
                  _num(mine["conversations"]["shown_end"])]
        return cells

    rows = [row(base, is_base=True)]
    rows += [row(ref) for ref in refs.values()]
    for result in results:
        wanted = reference_config(result["config"])
        rows.append(row(result, refs.get(spec_label(wanted)) if wanted else None))
    return "\n".join(_table(header, rows))


def run_sweep(engines, families, replies, say):
    """Runs the grid of every family and prints one table per family. Returns the document of the sweep."""
    base = run_variant(engines, parse_spec("product"), replies)
    document = {}
    for family in families:
        results = [run_variant(engines, parse_spec(spec), replies, spec) for spec in GRIDS[family]()]
        refs = {}
        for result in results:
            wanted = reference_config(result["config"])
            if wanted and spec_label(wanted) not in refs:
                refs[spec_label(wanted)] = run_variant(engines, wanted, replies)
        say("sweep %s:" % family)
        say("")
        say(sweep_table(family, base, results, refs))
        say("")
        document[family] = {"matter": list(_shown_kinds(family)),
                            "rows": [{"spec": r["label"], "summary": r["summary"],
                                      "flips": flips(base["records"], r["records"])} for r in list(refs.values()) + results]}
    return document


# ------------------------------------------------------------------ explain

def _mark(record, mid, members_now, members_before):
    if mid in record["direct"]:
        return "direct"
    if mid in record["also_ok"]:
        return "also_ok"
    if mid in members_now:
        return "cluster"
    if mid in members_before:
        return "previous cluster"
    return "unrelated"


def explain(engine, session, config, replies, verbose=False):
    """Lines for one session under one variant, turn by turn: the query, what arrived in each block with scores and its
    relation to the turn's labels, what the second lookup left, the pool's strengths. Memory texts only with `verbose`."""
    lines = ["session %s/%s under %s" % (engine.name, session["id"], spec_label(config))]
    for record in replay_session(engine, session, config, replies, trace=True):
        now, before = engine.members(record["cluster"]), engine.members(record["prev_cluster"])
        lines.append("turn %d (%s, conversation %d%s)" % (record["turn"], record["kind"], record["conversation"],
                                                        ", new" if record["new_conversation"] else ""))
        lines.append("  labels: cluster=%s direct=%s also_ok=%s" % (record["cluster"], record["direct"], record["also_ok"]))
        lines.append("  query: %s" % (record["query"].replace("\n", " / ") if record["query"] else "none (no lookup)"))
        lines.append("  main: %s" % (", ".join("%s score %.3f [%s]" % (m["id"], m["score"], _mark(record, m["id"], now, before))
                                               for m in record["main"]) or "nothing"))
        lines.append("  extra block: %s" % (", ".join(
            "%s from %s%s [%s]" % (m["id"], m["from"], "" if m["score"] is None else " score %.3f" % m["score"],
                                   _mark(record, m["id"], now, before)) for m in record["extra"]) or "nothing"))
        lines.append("  in front: %s, fresh: %s, memories in front: %d" % (
            {None: "-", True: "yes", False: "no"}[record["in_front"]], {None: "-", True: "yes", False: "no"}[record["fresh"]],
            record["shown"]))
        if config["after"]:
            lines.append("  left for the next turn: %s" % (record["left"] or "nothing"))
        lines.append("  pool: %s" % (", ".join("%s %.2f" % (mid, s) for mid, s in record["pool"].items()) or "empty"))
        if verbose:
            for mid in [m["id"] for m in record["main"]] + [m["id"] for m in record["extra"]]:
                lines.append("      %s %s" % (mid, engine.text_of[mid]))
    return lines


def find_session(engines, name):
    world_name, slash, session_id = name.rpartition("/")
    found = [(e, s) for e in engines if not slash or e.name == world_name for s in e.sessions if s["id"] == session_id]
    if not found:
        raise SystemExit("no session %r in this split (worlds without sessions are skipped)" % name)
    if len(found) > 1:
        raise SystemExit("session id %r exists in several worlds: write world/id (%s)" % (
            session_id, ", ".join(e.name for e, _ in found)))
    return found[0]


# ------------------------------------------------------------------ vectors

def needed_strings(worlds, scale, texts, configs):
    """The strings the lab sends to the model: the memory texts and the v0 queries of every turn of the sessions, and,
    when a config uses `after`, the reply texts (with the query prefix)."""
    wanted = []
    replies = texts.get(REPLY_KIND, {})
    for world in worlds:
        wanted.extend(scale.document_prefix + m["text"] for m in world["memories"].values())
        for session in world.get("sessions") or []:
            wanted.extend(scale.query_prefix + q for _, q in turn_queries(session) if q)
            if any(c["after"] for c in configs):
                for number in range(1, len(session.get("turns") or []) + 1):
                    text = replies.get("%s/%s/%d" % (world["world"], session["id"], number))
                    if text:
                        wanted.append(scale.query_prefix + text)
    return list(dict.fromkeys(wanted))


def ensure_cache(worlds, path, url, model, scale, texts, configs, say=print):
    """model_lab.ensure_cache for this lab's strings: fetch what is missing, write the cache when it grew."""
    lab.refuse_cache_path(path)
    cache = lab.load_cache(path, model)
    size = len(cache["vectors"])
    requests = 0
    try:
        requests = lab.fetch_missing(needed_strings(worlds, scale, texts, configs), cache, url, model, say)
    finally:
        if len(cache["vectors"]) != size:
            lab.save_cache(path, cache)
    say("vector cache %s: %d vectors, %d embedding requests this run" % (path, len(cache["vectors"]), requests))
    return cache


def build_engines(worlds, cache, scale):
    vectors = cache["vectors"]

    def vectors_of(text):
        try:
            return vectors[text]
        except KeyError:
            raise SystemExit("a vector is missing from the cache (run with --url once to fetch it)")

    return [SessionEngine(world, vectors_of, scale) for world in worlds]


def coverage_lines(engines, skipped, texts):
    turns = sum(len(s.get("turns") or []) for e in engines for s in e.sessions)
    sessions = sum(len(e.sessions) for e in engines)
    lines = ["sessions: %d sessions with %d turns in %d worlds (%d worlds without sessions skipped)" % (
        sessions, turns, len(engines), skipped)]
    if REPLY_KIND in texts:
        have = sum(1 for e in engines for s in e.sessions for n in range(1, len(s.get("turns") or []) + 1)
                   if texts[REPLY_KIND].get("%s/%s/%d" % (e.name, s["id"], n)))
        lines.append("texts %s: %d of %d turns have a text" % (REPLY_KIND, have, turns))
    return lines


# ------------------------------------------------------------------ command line

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--split", required=True, help="folder with the world files (a folder named test needs --final)")
    parser.add_argument("--cache", required=True, help="the vector cache of vector_lab (not under bench/chat)")
    parser.add_argument("--url", help="embeddings endpoint base URL; only used for vectors the cache lacks")
    parser.add_argument("--model", default=lab.DEFAULT_MODEL, help="embedding model name (default bge-m3)")
    parser.add_argument("--final", action="store_true", help="allow the split named 'test' (one run, rules already fixed)")
    parser.add_argument("--texts", action="append", default=[], metavar="FILE",
                        help="a texts file {kind, items} (kind %s, ids world/session/turn; repeatable)" % REPLY_KIND)
    parser.add_argument("--baseline", action="store_true", help="print the product's numbers")
    parser.add_argument("--run", action="append", default=[], metavar="SPEC", help="the report of one variant (repeatable)")
    parser.add_argument("--sweep", metavar="FAMILIES", help="comma list of %s" % ", ".join(SWEEP_FAMILIES))
    parser.add_argument("--explain", metavar="SESSION", help="with --run: one session turn by turn (world/session)")
    parser.add_argument("--verbose", action="store_true", help="with --explain: print memory texts")
    parser.add_argument("--out", metavar="JSON", help="write the records and summaries of the runs here")
    args = parser.parse_args(argv)

    if args.sweep:
        model_lab.refuse_sweep(args.split)
    lab.refuse_test(args.split, args.final)
    if args.explain and not args.run:
        raise SystemExit("--explain needs --run (the variant to explain)")
    families = []
    if args.sweep:
        families = [f.strip() for f in args.sweep.split(",") if f.strip()]
        unknown = [f for f in families if f not in GRIDS]
        if unknown or not families:
            raise SystemExit("unknown sweep family %s (known: %s)" % (", ".join(unknown) or "(none)",
                                                                     ", ".join(SWEEP_FAMILIES)))
    configs = [parse_spec(spec) for spec in args.run]
    sweep_configs = [parse_spec(spec) for family in families for spec in GRIDS[family]()]
    texts = model_lab.load_texts(args.texts)
    check_sources(configs + sweep_configs, texts)
    replies = texts.get(REPLY_KIND, {})

    scale = lab.Scale.for_model(args.model)
    everything = lab.load_worlds(args.split, args.final)
    worlds = [w for w in everything if w.get("sessions")]
    if not worlds:
        raise SystemExit("no world of this split has sessions (bench/chat/README.md describes the format)")
    cache = ensure_cache(worlds, args.cache, args.url, args.model, scale, texts, configs + sweep_configs, run.say)
    engines = build_engines(worlds, cache, scale)
    for line in coverage_lines(engines, len(everything) - len(worlds), texts):
        run.say(line)

    document = {"split": os.path.basename(os.path.normpath(args.split)), "model": args.model}
    base = run_variant(engines, parse_spec("product"), replies)
    if args.baseline:
        run.say("")
        for line in report_lines(base, [], "product"):
            run.say(line)
        document["product"] = {"summary": base["summary"], "records": base["records"]}
    runs = []
    for config in configs:
        result = run_variant(engines, config, replies)
        refs = [("product", base)]
        reference = reference_config(config)
        if reference:
            refs.append(("%s only" % spec_label(reference), run_variant(engines, reference, replies)))
        runs.append(run_document(result, refs))
        run.say("")
        for line in report_lines(result, refs):
            run.say(line)
    if runs:
        document["runs"] = runs
    if families:
        run.say("")
        document["sweep"] = run_sweep(engines, families, replies, run.say)
    if args.explain:
        engine, session = find_session(engines, args.explain)
        for config in configs:
            run.say("")
            for line in explain(engine, session, config, replies, args.verbose):
                run.say(line)
    if args.out:
        run.write_json(args.out, document)
    return 0


if __name__ == "__main__":
    sys.exit(main())
