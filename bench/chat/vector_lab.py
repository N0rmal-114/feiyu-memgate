"""A lab for associative recall on raw vectors (experimental, not product code). Standard library only.

    python -B bench/chat/vector_lab.py --split bench/chat/dev --cache <file> --url http://127.0.0.1:11434/v1 --baseline
    python -B bench/chat/vector_lab.py --split bench/chat/dev --cache <file> --run "smooth:alpha=0.5,k=3,tau=0.6"
    python -B bench/chat/vector_lab.py --split bench/chat/dev --cache <file> --sweep all
    python -B bench/chat/vector_lab.py --split bench/chat/dev --cache <file> --explain dev-teacher/r03 --variant "spread:beta=0.5"

What it does. The memories of a split and the queries of its cases (built as `run.py --query v0` builds them) are
embedded once through an OpenAI-compatible endpoint and kept in a cache file (--cache, never under bench/chat).
With the cache present nothing else is requested. Every variant below is then a pure function over those vectors,
so a grid costs seconds instead of a model pass.

The faithful copy of the pipeline as it was before linked recall (`baseline`): cosine of the unit vectors (stored as float32, as the
embedding backend stores them), the backend's floor/ceil mapping to a 0..1 score for the model's preset, a fetch
of min(3 x limit, 30) candidates with a score above 0, then the gateway's own default stages (min_score,
relative_score, newer_first, fill_text, drop_oversize, redact, dedupe, budget, with the broad profile: the
gateway's DEFAULT_STAGES without `linked`, which is the stage that does what the `append` variant below tries, so
the baseline stays the "before") and its own admit loop, all taken from the package instead of copied, and the
limit (5). `append:top=2,minimum=0.6,max_added=2,mode=within` is the behaviour the gateway's `linked` stage
implements. The metrics come from run.py
(`case_metrics`, `summarize`, `format_table`), so a number means here what it means there.

A variant is a spec string, parts separated by ";", each part `name:key=value,key=value`:

    smooth   alpha=0.5 k=3 tau=0.6 rounds=1
             write time. v_i' = norm(v_i + alpha * sum_j w_ij v_j) over the k nearest other memories j with
             cosine >= tau, w_ij the cosine. rounds=2 applies it twice (the neighbours are found again on the
             vectors of the first round). The neighbour sums use the vectors of the round before, never half
             updated ones.
    spread   beta=0.5 k=5 tau=0.6 hops=1
             query time. After scoring every memory s_i: s_i' = min(1, s_i + beta * max_j(s_j * n_ij)) over the
             neighbours j of i (cosine >= tau, at most k), n_ij the memory-memory cosine mapped to 0..1 like a
             score. hops=2 repeats it on the result of the first hop: the second hop is damped by beta once more.
             The fetch ranks by s' (raw cosine breaks ties), the lines then see s'.
    widen    min_score= ratio= limit=5
             today's search with other lines or another limit. A line that is not given stays the model's broad
             line; the fetch size follows the limit as in the gateway.
    append   top=3 minimum=0.6 max_added=2 mode=beyond
             after the search, neighbours (similarity score >= minimum) of the first `top` delivered memories,
             at most max_added; `beyond` adds them on top of the limit, `within` only while the list stays
             within the limit. Uses methods.expand, the function run.py's link expansion uses.

Parts combine: "smooth:alpha=1;append:minimum=0.6". Neighbours of smooth, spread and append are found on the
vectors in effect (smoothed ones when smooth is part of the spec): a product that stores only the smoothed
vectors has nothing else to compare with.

Deterministic. A split folder named `test` is refused in every mode unless --final is given. The cache holds
vectors only (strings are its keys), memory texts are printed only with --verbose.
"""
import argparse
import array
import importlib.util
import json
import math
import os
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


run = _sibling("run")
sweep = _sibling("sweep")
methods = _sibling("methods")

from feiyu_memgate.backends.embedding import _Http, preset_for  # noqa: E402
from feiyu_memgate.core import recall as recall_module  # noqa: E402
from feiyu_memgate.core import stages as stages_module  # noqa: E402
from feiyu_memgate.core.redact import redact as private_redact  # noqa: E402
from feiyu_memgate.errors import BackendError  # noqa: E402
from feiyu_memgate.model import Candidate  # noqa: E402

try:
    _dot = math.sumprod  # Python 3.12+
except AttributeError:  # pragma: no cover - Python 3.11
    def _dot(a, b):
        return sum(x * y for x, y in zip(a, b))

BATCH = 32  # texts per embeddings request, as the embedding backend
FETCH_TIMEOUT = 120.0
DEFAULT_MODEL = "bge-m3"
LIMIT = 5
MAX_FETCH = recall_module.MAX_FETCH
FORBIDDEN_SPLIT = sweep.FORBIDDEN_SPLIT

# What the project measured before this lab existed (development split, bge-m3, broad profile, v0).
EXPECTED = (("assoc direct hit", 29.2), ("assoc chain recall", 33.0), ("items per assoc remark", 3.56),
            ("noise per assoc remark", 2.42), ("follow-up hit", 71.9), ("quiet rate", 12.5))
EXPECTED_APPEND = (("assoc direct hit", 54.2), ("assoc chain recall", 47.2), ("items per assoc remark", 5.02))


# ------------------------------------------------------------------ specs

DEFAULTS = {
    "smooth": {"alpha": 0.5, "k": 3, "tau": 0.6, "rounds": 1},
    "spread": {"beta": 0.5, "k": 5, "tau": 0.6, "hops": 1},
    "widen": {"min_score": None, "ratio": None, "limit": LIMIT},
    "append": {"top": 3, "minimum": 0.6, "max_added": 2, "mode": "beyond"},
}
_INTS = {"k", "rounds", "hops", "limit", "top", "max_added"}
_MODES = ("beyond", "within")


def _number(name, text):
    try:
        value = int(text) if name in _INTS else float(text)
    except ValueError:
        raise SystemExit("%s must be a %s, not %r" % (name, "whole number" if name in _INTS else "number", text))
    return value


def _check_part(part, values):
    for name in ("alpha", "beta", "tau", "minimum", "min_score", "ratio"):
        value = values.get(name)
        if value is not None and not math.isfinite(value):
            raise SystemExit("%s: %s must be a finite number" % (part, name))
    for name in ("k", "rounds", "hops", "limit"):
        if name in values and values[name] < 1:
            raise SystemExit("%s: %s must be at least 1" % (part, name))
    for name in ("top", "max_added"):
        if name in values and values[name] < 0:
            raise SystemExit("%s: %s must be at least 0" % (part, name))
    if part == "append" and values["mode"] not in _MODES:
        raise SystemExit("append: mode must be one of %s" % ", ".join(_MODES))
    for name in ("ratio",):
        if values.get(name) is not None and not 0 <= values[name] <= 1:
            raise SystemExit("%s: %s must be between 0 and 1" % (part, name))


def parse_spec(text):
    """{"smooth": dict or None, "spread": ..., "widen": dict, "append": ...} from a spec string; "" and
    "baseline" are today's pipeline. SystemExit with a message when the text is not a spec."""
    config = {"smooth": None, "spread": None, "widen": dict(DEFAULTS["widen"]), "append": None}
    text = (text or "").strip()
    if text in ("", "baseline"):
        return config
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        part, _, rest = chunk.partition(":")
        part = part.strip()
        if part not in DEFAULTS:
            raise SystemExit("unknown variant part %r (known: %s)" % (part, ", ".join(DEFAULTS)))
        values = dict(DEFAULTS[part])
        for pair in filter(None, (p.strip() for p in rest.split(","))):
            name, equals, raw = pair.partition("=")
            name = name.strip()
            if not equals or name not in values:
                raise SystemExit("%s: unknown or malformed setting %r (known: %s)" % (part, pair, ", ".join(values)))
            values[name] = raw.strip() if name == "mode" else _number(name, raw.strip())
        _check_part(part, values)
        config[part] = values
    return config


def spec_label(config):
    """The canonical spec string of a parsed config ("baseline" when nothing differs)."""
    parts = []
    for part in ("smooth", "spread", "widen", "append"):
        values = config.get(part)
        if not values or (part == "widen" and values == DEFAULTS["widen"]):
            continue
        shown = ",".join("%s=%s" % (name, values[name]) for name in DEFAULTS[part] if values[name] is not None)
        parts.append("%s:%s" % (part, shown))
    return ";".join(parts) or "baseline"


# ------------------------------------------------------------------ vectors

def unit32(values):
    """`values` as an L2-normalised float32 array, the way the embedding backend stores a vector (None for
    a zero or non-finite vector)."""
    norm = math.sqrt(math.fsum(v * v for v in values))
    if not math.isfinite(norm) or norm == 0.0:
        return None
    return array.array("f", [v / norm for v in values])


class Scale:
    """How a cosine becomes a score, and the model's broad score lines (the embedding backend's own numbers)."""

    def __init__(self, floor, ceil, min_score, ratio):
        if not ceil > floor:
            raise ValueError("ceil must be greater than floor")
        self.floor, self.ceil = float(floor), float(ceil)
        # the attribute names the stages look for on a store (stages._declared)
        self.default_min_score = self.broad_min_score = float(min_score)
        self.default_relative_ratio = self.broad_relative_ratio = float(ratio)

    @classmethod
    def for_model(cls, model):
        preset, known = preset_for(model)
        if not known:
            raise SystemExit("model %r has no preset in the embedding backend: this lab would have to guess the "
                             "score mapping and lines" % model)
        scale = cls(preset["floor"], preset["ceil"], preset["broad_min_score"], preset["broad_ratio"])
        scale.query_prefix, scale.document_prefix = preset["query_prefix"], preset["document_prefix"]
        return scale

    query_prefix = ""
    document_prefix = ""

    def score(self, cosine):
        return min(1.0, max(0.0, (cosine - self.floor) / (self.ceil - self.floor)))


class Space:
    """Unit vectors of one world's memories (in a fixed order) with the cosines between them, found on demand."""

    def __init__(self, vectors):
        self.vectors = list(vectors)
        self._order = None
        self._links = {}
        self._queries = {}

    def _neighbours(self):
        """For every memory the other memories by cosine, best first (a tie by position)."""
        if self._order is None:
            n = len(self.vectors)
            pair = [[0.0] * n for _ in range(n)]
            for i in range(n):
                for j in range(i + 1, n):
                    pair[i][j] = pair[j][i] = _dot(self.vectors[i], self.vectors[j])
            self._order = [sorted(((j, pair[i][j]) for j in range(n) if j != i), key=lambda p: (-p[1], p[0]))
                           for i in range(n)]
        return self._order

    def links(self, k, tau):
        """For every memory the (position, cosine) of at most k other memories with cosine >= tau, best first."""
        key = (k, tau)
        if key not in self._links:
            out = []
            for row in self._neighbours():
                keep = []
                for j, cosine in row:
                    if len(keep) >= k or cosine < tau:
                        break
                    keep.append((j, cosine))
                out.append(keep)
            self._links[key] = out
        return self._links[key]

    def ranked(self, i):
        """All other memories by cosine to memory i, best first."""
        return self._neighbours()[i]

    def cosines(self, query, vector):
        if query not in self._queries:
            self._queries[query] = [_dot(vector, v) for v in self.vectors]
        return self._queries[query]


def smooth(space, alpha, k, tau, rounds):
    """The space with every vector replaced by norm(v_i + alpha * sum_j cos_ij * v_j), `rounds` times."""
    current = space
    for _ in range(rounds):
        out = []
        for i, vector in enumerate(current.vectors):
            acc = list(vector)
            for j, cosine in current.links(k, tau)[i]:
                weight = alpha * cosine
                acc = [a + weight * b for a, b in zip(acc, current.vectors[j])]
            out.append(unit32(acc) or vector)
        current = Space(out)
    return current


def spread(scores, space, scale, beta, k, tau, hops):
    """Scores with what the neighbours earned added: s_i' = min(1, s_i + beta * max_j(s_j * n_ij)), `hops` times,
    each hop reading the result of the one before (so the second hop is damped by beta once more)."""
    links = [[(j, scale.score(cosine)) for j, cosine in row] for row in space.links(k, tau)]
    current = list(scores)
    for _ in range(hops):
        current = [min(1.0, s + beta * max((current[j] * n for j, n in links[i]), default=0.0))
                   for i, s in enumerate(scores)]
    return current


# ------------------------------------------------------------------ the pipeline copy

class _Ctx:
    """The little the gateway's stages and admit loop ask of a request context."""

    def __init__(self, limit):
        self.limit = limit
        self.accepted = []
        self.notes = []
        self.redact = private_redact

    def expired(self):
        return False

    def note(self, word):
        if word not in self.notes:
            self.notes.append(word)

    def fault(self, note, detail):
        raise RuntimeError("a stage failed in the lab: %s %s" % (note, detail))


def default_stages(min_score=None, ratio=None):
    """The gateway's DEFAULT_STAGES without `linked` (the lab's baseline is the pipeline before linked recall),
    with the broad profile; min_score and ratio, when given, are the explicit options that win over the profile
    (as in a configuration)."""
    return [stages_module.MinScore(value=min_score, profile="broad"),
            stages_module.RelativeScore(ratio=ratio, profile="broad"),
            stages_module.NewerFirst(), stages_module.RecordAudience(), stages_module.FillText(),
            stages_module.DropOversize(), stages_module.Redact(), stages_module.Dedupe(), stages_module.Budget()]


class Engine:
    """One world: its memories, its vectors, and every variant over them."""

    def __init__(self, world, vectors_of, scale):
        self.world = world
        self.scale = scale
        self.ids = sorted(world["memories"], key=lambda mid: (world["memories"][mid]["date"], mid))
        self.texts = [world["memories"][mid]["text"] for mid in self.ids]
        self.dates = [world["memories"][mid]["date"] for mid in self.ids]
        self._vectors_of = vectors_of
        self._spaces = {}
        self._stages = {}
        self.base = Space([unit32(vectors_of(scale.document_prefix + text)) for text in self.texts])
        self.cases = world["cases"]

    def query_vector(self, query):
        return unit32(self._vectors_of(self.scale.query_prefix + query))

    def space(self, smoothing):
        if not smoothing:
            return self.base
        key = tuple(sorted(smoothing.items()))
        if key not in self._spaces:
            self._spaces[key] = smooth(self.base, smoothing["alpha"], smoothing["k"], smoothing["tau"],
                                       smoothing["rounds"])
        return self._spaces[key]

    def stage_list(self, widen):
        key = (widen["min_score"], widen["ratio"])
        if key not in self._stages:
            self._stages[key] = default_stages(*key)
        return self._stages[key]

    def evaluate(self, case, config):
        """Everything one case needs: the queries, the ranking before the lines ("order", "scores", "cosines"),
        what the pipeline delivered, what append added and the final list."""
        queries = methods.queries_for("v0", case.get("history") or [], case["message"])
        out = {"queries": queries, "order": [], "scores": {}, "cosines": {}, "delivered": [], "added": [], "final": []}
        if not queries:
            return out
        space = self.space(config["smooth"])
        cosines = space.cosines(queries[0], self.query_vector(queries[0]))
        scores = [self.scale.score(c) for c in cosines]
        if config["spread"]:
            scores = spread(scores, space, self.scale, **config["spread"])
        # best score first; the raw cosine breaks ties (scores are clamped at 1, cosines are not)
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], -cosines[i], i))
        out["order"] = [self.ids[i] for i in order]
        out["scores"] = {self.ids[i]: scores[i] for i in range(len(scores))}
        out["cosines"] = {self.ids[i]: cosines[i] for i in range(len(scores))}

        widen = config["widen"]
        limit = widen["limit"]
        fetch = min(limit * 3, MAX_FETCH)
        store = self.scale
        candidates = [Candidate(score=scores[i], source="private", store=store, ref=self.ids[i], text=self.texts[i],
                                date=self.dates[i], meta={}) for i in order[:fetch] if scores[i] > 0.0]
        ctx = _Ctx(limit)
        stages = self.stage_list(widen)
        for stage in stages:
            candidates = list(stage.prepare(candidates, ctx))
        candidates.sort(key=lambda c: -c.score)
        recall_module._admit_all(candidates, stages, ctx)
        out["delivered"] = [(c.ref, c.score) for c in ctx.accepted]
        delivered_ids = list(dict.fromkeys(mid for mid, _ in out["delivered"]))

        added = []
        if config["append"] and delivered_ids:
            added = self._append(space, delivered_ids, limit, config["append"])
        out["added"] = added
        out["final"] = delivered_ids + [item["id"] for item in added]
        return out

    def _append(self, space, delivered, limit, settings):
        position = {mid: i for i, mid in enumerate(self.ids)}

        def neighbours_of(mid):
            row = space.ranked(position[mid])
            # a score of 0 is not a hit of the store (find drops it), whatever the minimum says
            scored = [(self.ids[j], self.scale.score(cosine)) for j, cosine in row]
            return sorted(((m, s) for m, s in scored if s > 0.0), key=lambda p: (-p[1], p[0]))

        room = settings["max_added"]
        if settings["mode"] == "within":
            room = max(0, min(room, limit - len(delivered)))
        found = methods.expand(delivered, neighbours_of, settings["top"], room, settings["minimum"])
        return [{"id": f["id"], "source": f["source"], "score": round(f["score"], 6), "kind": "vec"} for f in found]

    def records(self, config):
        out = []
        for case in self.cases:
            result = self.evaluate(case, config)
            labels = {key: list(case.get(key) or []) for key in ("direct", "chain", "also_ok")}
            out.append({
                "world": self.world["world"], "id": case["id"], "type": case["type"], "profile": "broad",
                "queries": result["queries"],
                "delivered": [{"id": mid, "score": round(score, 6)} for mid, score in result["delivered"]],
                "added": result["added"], "final": result["final"], "labels": labels,
            })
        return out


def summary_of(engines, config):
    records = [r for engine in engines for r in engine.records(config)]
    return run.summarize(records, with_links=bool(config["append"]))


# ------------------------------------------------------------------ split, cache, vectors

def refuse_test(split, final):
    """SystemExit when `split` is the frozen test folder and --final was not given (every mode, fetching included)."""
    for path in (split, os.path.realpath(split)):
        if os.path.basename(os.path.normpath(path)).lower() == FORBIDDEN_SPLIT and not final:
            raise SystemExit("refusing %s: the test split is frozen and not for choosing rules. Work on the "
                             "development split; --final is for the single run after the rules are fixed." % split)


def refuse_cache_path(path):
    """SystemExit when the cache would live under bench/chat (vectors of the split must not enter the repository)."""
    real = os.path.normcase(os.path.realpath(path))
    inside = os.path.normcase(os.path.realpath(os.path.join(ROOT, "bench", "chat")))
    if real == inside or real.startswith(inside + os.sep):
        raise SystemExit("the vector cache must not be written under bench/chat: %s" % path)


def load_worlds(split, final=False):
    refuse_test(split, final)
    return [run.load_world(path) for path in run.world_files(split)]


def needed_strings(worlds, scale):
    """Every string the lab sends to the model: the memory texts and the v0 queries of the cases."""
    wanted = []
    for world in worlds:
        wanted.extend(scale.document_prefix + m["text"] for m in world["memories"].values())
        for case in world["cases"]:
            wanted.extend(scale.query_prefix + q for q in
                          methods.queries_for("v0", case.get("history") or [], case["message"]))
    return list(dict.fromkeys(wanted))


def load_cache(path, model):
    if not os.path.isfile(path):
        return {"model": model, "vectors": {}}
    with open(path, encoding="utf-8") as handle:
        cache = json.load(handle)
    if cache.get("model") != model:
        raise SystemExit("the cache %s was built with model %r, not %r" % (path, cache.get("model"), model))
    return cache


def save_cache(path, cache):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(cache, handle, ensure_ascii=False)
        handle.write("\n")


def fetch_missing(strings, cache, url, model, say=print):
    """Fetch the vectors the cache lacks, `BATCH` strings per request, with the embedding backend's own
    request code. Returns the number of requests made."""
    missing = [s for s in strings if s not in cache["vectors"]]
    if not missing:
        return 0
    if not url:
        raise SystemExit("%d vectors are missing from the cache and no --url was given" % len(missing))
    http = _Http(url, model, None, FETCH_TIMEOUT)
    requests = 0
    for start in range(0, len(missing), BATCH):
        chunk = missing[start:start + BATCH]
        try:
            vectors = http.embed(chunk, FETCH_TIMEOUT, None)
        except BackendError as error:
            raise SystemExit("embedding request failed (%s) after %d requests" % (error, requests))
        requests += 1
        for text, vector in zip(chunk, vectors):
            cache["vectors"][text] = vector
    return requests


def ensure_cache(worlds, path, url, model, scale, say=print):
    """The cache with every needed vector in it (fetched when --url is given); written when it grew."""
    refuse_cache_path(path)
    cache = load_cache(path, model)
    size = len(cache["vectors"])
    requests = 0
    try:
        requests = fetch_missing(needed_strings(worlds, scale), cache, url, model, say)
    finally:
        if len(cache["vectors"]) != size:
            save_cache(path, cache)
    say("vector cache %s: %d vectors, %d embedding requests this run" % (path, len(cache["vectors"]), requests))
    return cache


def build_engines(worlds, cache, scale):
    vectors = cache["vectors"]

    def vectors_of(text):
        try:
            return vectors[text]
        except KeyError:
            raise SystemExit("a vector is missing from the cache (run with --url once to fetch it)")

    return [Engine(world, vectors_of, scale) for world in worlds]


# ------------------------------------------------------------------ baseline check

def _percent(value):
    return None if value is None else round(value * 100, 1)


def headline(summary):
    """The six numbers of the project's earlier measurement, from a summary."""
    assoc = summary["overall"]["remark-assoc"]
    return (("assoc direct hit", _percent(assoc["direct_hit"])), ("assoc chain recall", _percent(assoc["chain_recall"])),
            ("items per assoc remark", round(assoc["items"], 2)), ("noise per assoc remark", round(assoc["noise"], 2)),
            ("follow-up hit", _percent(summary["overall"]["follow-up"]["hit"])),
            ("quiet rate", _percent(summary["overall"]["quiet"]["quiet_rate"])))


def compare_lines(title, expected, got):
    got = dict(got)
    lines = ["%s" % title, "| metric | expected | copy | match |", "|---|---|---|---|"]
    for name, want in expected:
        have = got[name]
        lines.append("| %s | %s | %s | %s |" % (name, want, have, "yes" if have == want else "NO"))
    return lines


# ------------------------------------------------------------------ sweep

def grid_smooth():
    return ["smooth:alpha=%s,k=%d,tau=%s,rounds=%d" % (a, k, t, r)
            for r in (1, 2) for a in (0.25, 0.5, 1.0) for k in (2, 3, 5) for t in (0.5, 0.6, 0.7)]


def grid_spread():
    return ["spread:beta=%s,k=%d,tau=%s,hops=%d" % (b, k, t, h)
            for h in (1, 2) for b in (0.25, 0.5, 0.75, 1.0) for k in (3, 5) for t in (0.5, 0.6, 0.7)]


def grid_widen():
    return ["widen:min_score=%s,ratio=%s,limit=%d" % (m, r, limit)
            for limit in (5, 8, 10) for m in (0.3, 0.2, 0.1) for r in (0.6, 0.4, 0.2, 0.0)]


def grid_append():
    return ["append:top=%d,minimum=%s,max_added=%d,mode=%s" % (t, m, a, mode)
            for mode in _MODES for t in (1, 2, 3, 5) for m in (0.5, 0.6, 0.7) for a in (1, 2, 3)]


GRIDS = {"smooth": grid_smooth, "spread": grid_spread, "widen": grid_widen, "append": grid_append}
FAMILIES = ("smooth", "spread", "widen", "append", "combo")


def assoc_numbers(summary):
    assoc = summary["overall"]["remark-assoc"]
    return assoc["direct_hit"], assoc["chain_recall"], assoc["precision"], summary["all"]["items"]


def eligible(summary, base, extra_items=1.5):
    """The rule fixed before the experiments: association precision not below the baseline's and mean delivered
    items over all cases at most `extra_items` above the baseline's."""
    _, _, precision, items = assoc_numbers(summary)
    _, _, base_precision, base_items = assoc_numbers(base)
    return precision is not None and base_precision is not None and precision >= base_precision \
        and items <= base_items + extra_items


def score_of(summary):
    direct, chain, _, items = assoc_numbers(summary)
    return (direct or 0.0) + (chain or 0.0), -items


def winner(rows, base):
    """(label, summary) with the highest direct hit + chain recall among the eligible rows (fewer items break a
    tie, then grid order); None when no row is eligible."""
    best = None
    for label, summary in rows:
        if eligible(summary, base) and (best is None or score_of(summary) > score_of(best[1])):
            best = (label, summary)
    return best


def combo_specs(winners):
    """smooth-then-append and spread-then-append on the winners of the single variants (when there are any)."""
    out = []
    for name in ("smooth", "spread"):
        if winners.get(name):
            for mode in _MODES:
                for minimum in (0.5, 0.6, 0.7):
                    for added in (1, 2):
                        out.append("%s;append:top=3,minimum=%s,max_added=%d,mode=%s" % (winners[name], minimum, added, mode))
    return out


def sweep_table(rows, base):
    """sweep.py's table (same columns) plus the mean items over all cases to three places and the rule's verdict."""
    lines = sweep.format_table(rows).split("\n")
    out = [lines[0][:-1] + "| mean items | rule |", lines[1] + "---|---|"]
    for line, (_, summary) in zip(lines[2:], rows):
        out.append(line[:-1] + "| %.3f | %s |" % (summary["all"]["items"], "ok" if eligible(summary, base) else "-"))
    return "\n".join(out)


def run_sweep(engines, families, say):
    """Runs the grids in order (combo last: it builds on the winners of the single variants), prints one table and
    the winner of every family. Returns the rows and {family: winning spec or None}."""
    base = summary_of(engines, parse_spec("baseline"))
    rows = [("baseline", base)]
    winners = {}
    for family in families:
        specs = combo_specs(winners) if family == "combo" else GRIDS[family]()
        family_rows = [(spec, summary_of(engines, parse_spec(spec))) for spec in specs]
        rows.extend(family_rows)
        best = winner(family_rows, base)
        winners[family] = best[0] if best else None
    say(sweep_table(rows, base))
    say("")
    say("winners by the rule (highest assoc direct hit + chain recall; precision >= baseline; "
        "mean items <= baseline + 1.5):")
    for family in families:
        say("  %s: %s" % (family, winners[family] or "none eligible"))
    return rows, winners


# ------------------------------------------------------------------ explain

def find_case(engines, name):
    world_name, slash, case_id = name.rpartition("/")
    found = [(e, c) for e in engines if not slash or e.world["world"] == world_name
             for c in e.cases if c["id"] == case_id]
    if not found:
        raise SystemExit("no case %r in this split" % name)
    if len(found) > 1:
        raise SystemExit("case id %r exists in several worlds: write world/id (%s)" % (
            case_id, ", ".join(e.world["world"] for e, _ in found)))
    return found[0]


def explain(engine, case, specs, verbose=False):
    """Lines: for each labelled memory its rank and score under every spec (the first is the reference)."""
    results = [(spec, engine.evaluate(case, parse_spec(spec))) for spec in specs]
    lines = ["case %s/%s (%s)" % (engine.world["world"], case["id"], case["type"])]
    if verbose:
        lines.append("  message: %s" % case["message"])
    lines.append("  queries: %d, labels: direct=%s chain=%s also_ok=%s" % (
        len(results[0][1]["queries"]), case.get("direct") or [], case.get("chain") or [], case.get("also_ok") or []))
    for spec, result in results:
        lines.append("  [%s] delivered=%s added=%s" % (spec, [m for m, _ in result["delivered"]],
                                                      [a["id"] for a in result["added"]]))
    for kind in ("direct", "chain", "also_ok"):
        for mid in case.get(kind) or []:
            cells = []
            for spec, result in results:
                if mid in result["order"]:
                    rank = result["order"].index(mid) + 1
                    cells.append("rank %d score %.3f cos %.3f %s" % (
                        rank, result["scores"][mid], result["cosines"][mid],
                        "in" if mid in result["final"] else "out"))
                else:
                    cells.append("no lookup")
            lines.append("  %-7s %s: %s" % (kind, mid, "  ->  ".join(cells)))
            if verbose:
                lines.append("          %s" % engine.texts[engine.ids.index(mid)])
    if verbose:
        for spec, result in results:
            shown = result["final"]
            lines.append("  [%s] final texts:" % spec)
            lines.extend("      %s %s" % (mid, engine.texts[engine.ids.index(mid)]) for mid in shown)
    return lines


# ------------------------------------------------------------------ command line

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--split", required=True, help="folder with the world files (a folder named test needs --final)")
    parser.add_argument("--cache", required=True, help="JSON file for the vectors (not under bench/chat)")
    parser.add_argument("--url", help="OpenAI-compatible embeddings base URL; only used for vectors the cache lacks")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="embedding model name (default bge-m3)")
    parser.add_argument("--final", action="store_true", help="allow the split named 'test' (one run, rules already fixed)")
    parser.add_argument("--baseline", action="store_true", help="the faithful copy against the earlier measurement")
    parser.add_argument("--run", action="append", default=[], metavar="SPEC", help="metrics of one variant (repeatable)")
    parser.add_argument("--sweep", metavar="FAMILIES", help="comma list of %s, or all" % ", ".join(FAMILIES))
    parser.add_argument("--explain", metavar="CASE", help="ranks and scores of the labelled memories of one case")
    parser.add_argument("--variant", action="append", default=[], metavar="SPEC",
                        help="with --explain: the variant(s) to compare with the baseline")
    parser.add_argument("--verbose", action="store_true", help="print memory texts")
    parser.add_argument("--out", metavar="JSON", help="with --sweep: write the rows' summaries here")
    args = parser.parse_args(argv)

    scale = Scale.for_model(args.model)
    worlds = load_worlds(args.split, args.final)
    cache = ensure_cache(worlds, args.cache, args.url, args.model, scale, run.say)
    engines = build_engines(worlds, cache, scale)

    if args.baseline:
        base = summary_of(engines, parse_spec("baseline"))
        for line in compare_lines("faithful copy against the earlier measurement:", EXPECTED, headline(base)):
            run.say(line)
        run.say(run.format_table(base))
        reference = summary_of(engines, parse_spec("append:top=3,minimum=0.6,max_added=2,mode=beyond"))
        run.say("")
        for line in compare_lines("after-the-fact expansion (top 3, minimum 0.60, at most 2) against the earlier "
                                  "measurement:", EXPECTED_APPEND, headline(reference)):
            run.say(line)
    for spec in args.run:
        config = parse_spec(spec)
        run.say("")
        run.say("variant: %s" % spec_label(config))
        run.say(run.format_table(summary_of(engines, config)))
    if args.sweep:
        families = list(FAMILIES) if args.sweep == "all" else [f.strip() for f in args.sweep.split(",")]
        unknown = [f for f in families if f not in FAMILIES]
        if unknown:
            raise SystemExit("unknown sweep family %s (known: %s)" % (", ".join(unknown), ", ".join(FAMILIES)))
        rows, winners = run_sweep(engines, families, run.say)
        if args.out:
            run.write_json(args.out, {"split": os.path.basename(os.path.normpath(args.split)), "model": args.model,
                                      "winners": winners,
                                      "rows": [{"spec": label, "summary": summary} for label, summary in rows]})
    if args.explain:
        engine, case = find_case(engines, args.explain)
        for line in explain(engine, case, ["baseline"] + (args.variant or []), args.verbose):
            run.say(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
