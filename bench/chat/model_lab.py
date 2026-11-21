"""A lab for recall helped by a language model's texts (experimental, not product code). Standard library only.

    python -B bench/chat/model_lab.py --split bench/chat/dev --cache <file> --texts replies.json --baseline
    python -B bench/chat/model_lab.py --split bench/chat/dev --cache <file> --texts replies.json --texts cues.json \\
        --run "second:src=reply-mem,q=text,merge=fill,extra=2;cue2:min=0.5" --table

What it does. The first step of an association ("最近天气有点热" has to reach "主人喜欢吃冰淇淋") is where vector
similarity alone often fails. Three ways to let a language model help are compared on equal terms, each as a pure
function over cached vectors. The model's texts are made elsewhere and arrive as files (--texts); this script never
calls a model. It reuses bench/chat/vector_lab.py (the pipeline copy, the cache, the metrics) instead of copying it.

    second   a second lookup with a generated text (the chat model's reply, or the need a friend would think of);
             what it finds is added to the first lookup's list, or merged with it by score.
    cue      every memory carries a generated "situations that would remind someone of it" text with its own
             vector; the cue changes the score of the memory before the lookup.
    cue2     the cue ranking alone is consulted after the lookup, and its best hits are appended.

Texts files: JSON {"kind": "<name>", "items": {"<world>/<id>": "<text>"}}. A kind whose ids are case ids is a set of
query texts (it is the `src` of a `second` variant); the kind `cue` has memory ids. A missing id, or an empty text,
means "no text": the case (or memory) behaves like the baseline. Query texts are embedded with the model's query
prefix and cues with its document prefix; they go into the same cache file as the other vectors.

The baseline, `product`, is the shipped pipeline: vector_lab's baseline plus
`append:top=2,minimum=0.6,max_added=2,mode=within` (the `linked` stage). A "lookup" is that pipeline for one query
vector and one list of per-memory cosines: fetch min(3 x limit, 30), the default stages with the broad lines, the
admit loop, the linked append within the limit. The wrapper is a subclass of vector_lab.Engine, which stays as it is.

A variant is a spec string, parts separated by ";" (one part per family; applied in the order cue, second, cue2),
each part `name:key=value,key=value`:

    second   src=<kind> q=text|both merge=fill|rank extra=2 mode=within|beyond min=<score>
             The first lookup is the product lookup for the remark. The second lookup is the product lookup for the
             generated text alone (q=text) or for the remark, a newline and the text (q=both). A hit of the second
             lookup (delivered or linked) counts only when its score is at least `min` (default: no minimum).
             merge=fill keeps the first list and appends the second lookup's hits that are not in it, best first, at
             most `extra`; within allows items only up to the limit in total, beyond up to limit + extra.
             merge=rank takes the union of both lists, each memory with the higher of its two scores, best first,
             cut at the limit. Without a text for the case: the first lookup alone.
    cue      mode=blend w=0.3 | mode=max d=0.05
             The cosine of a memory with a cue becomes (1-w) cos(text) + w cos(cue) (blend) or
             max(cos(text), cos(cue) - d) (max); a memory without a cue keeps cos(text). Every lookup of the
             variant then uses those cosines. This acts before the score lines.
    cue2     extra=2 mode=within|beyond min=0.5 ratio=0.85
             The memories ranked by score(cos(cue, remark)) alone; hits with score >= min and >= ratio x the best cue
             score that are not already in the list are appended like merge=fill. This acts after the lines.

Reported for a run: the headline numbers of vector_lab (plus the recall of named remarks), the flips against
`product` (cases gained and lost, with an exact two-sided sign test on the association flips), the association
direct hit per remark kind, the rank metrics that do not pass the score lines, and the association direct hit
per world. --sweep tries a fixed grid per family and names a row by a rule fixed beforehand.

    noise    hub=<lambda> k=10 | top=<T> | each=<Z> (bench/chat/noise.py, any subset in one part)
             Filters that only REMOVE memories from what the product delivers (none raises a score or adds a
             candidate): hub lowers the cosine of memories that are close to many others, top silences a lookup when
             nothing in the store stands out for the query, each keeps only delivered memories that stand out. They
             act on every lookup of the run (the first and the second); the rank metrics stay before them. lead and
             cap are for session_lab.py only (an error here).

--sweep noise tries each filter alone over a fixed grid (one row per setting, no winner rule); --diagnose noise prints,
per case type, the distribution of the largest z of the world for a case's query, the ROC-AUC of that and of the plain
top score for telling quiet cases from association remarks, and how many association remarks have their `direct`
memory at z >= 1, 1.5, 2.

Deterministic. A split folder named `test` is refused unless --final is given, and --sweep and --diagnose refuse it
even then.
"""
import argparse
import importlib.util
import json
import math
import os
import statistics
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


lab = _sibling("vector_lab")
noise_lab = _sibling("noise")
run = lab.run
methods = lab.methods

LIMIT = lab.LIMIT
PRODUCT_SPEC = "append:top=2,minimum=0.6,max_added=2,mode=within"
PRODUCT_APPEND = dict(lab.parse_spec(PRODUCT_SPEC)["append"])
CUE_KIND = "cue"
TOP = 10  # rows of a scoring shown by --explain
EXTRA_ITEMS = 1.5  # the sweep rule: items per association remark may grow by this much
KIND_ORDER = ("craving", "body", "trigger", "time", "trait", "emotion")
UNLABELLED = "unlabelled"


# ------------------------------------------------------------------ specs

FAMILIES = ("cue", "second", "cue2", "noise")  # the order in which a combined spec applies them
DEFAULTS = {
    "cue": {"mode": "blend", "w": 0.3, "d": 0.05},
    "second": {"src": None, "q": "text", "merge": "fill", "extra": 2, "mode": "within", "min": None},
    "cue2": {"extra": 2, "mode": "within", "min": 0.5, "ratio": 0.85},
    "noise": dict(noise_lab.DEFAULTS),
}
_INTS = {"extra"} | noise_lab.INTS
_STRINGS = {"mode", "src", "q", "merge"}
_CHOICES = {
    ("cue", "mode"): ("blend", "max"),
    ("second", "mode"): ("within", "beyond"),
    ("cue2", "mode"): ("within", "beyond"),
    ("second", "q"): ("text", "both"),
    ("second", "merge"): ("fill", "rank"),
}
PRODUCT_NAMES = ("", "product", "baseline")


def _number(name, text):
    try:
        value = int(text) if name in _INTS else float(text)
    except ValueError:
        raise SystemExit("%s must be a %s, not %r" % (name, "whole number" if name in _INTS else "number", text))
    return value


def _check_part(part, values):
    for name, value in values.items():
        if name not in _STRINGS and value is not None and not math.isfinite(value):
            raise SystemExit("%s: %s must be a finite number" % (part, name))
        if (part, name) in _CHOICES and value not in _CHOICES[(part, name)]:
            raise SystemExit("%s: %s must be one of %s" % (part, name, ", ".join(_CHOICES[(part, name)])))
    if "extra" in values and values["extra"] < 0:
        raise SystemExit("%s: extra must be at least 0" % part)
    for name in ("w", "ratio"):
        if name in values and not 0 <= values[name] <= 1:
            raise SystemExit("%s: %s must be between 0 and 1" % (part, name))
    if part == "noise":
        noise_lab.check(values, sessions=False)
    if part == "second" and not values["src"]:
        raise SystemExit("second: src=<kind> is required (the kind of the texts to look up with)")
    if part == "second" and values["src"] == CUE_KIND:
        raise SystemExit("second: src=%s holds memory cues, not query texts" % CUE_KIND)


def parse_spec(text):
    """{"cue": dict or None, "second": ..., "cue2": ...} from a spec string; "", "product" and "baseline" are the
    product pipeline. SystemExit with a message when the text is not a spec."""
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
            values[name] = raw.strip() if name in _STRINGS else _number(name, raw.strip())
        _check_part(part, values)
        config[part] = values if part != "noise" or noise_lab.active(values) else None
    return config


def spec_label(config):
    """The canonical spec string of a parsed config ("product" when nothing differs)."""
    parts = []
    for part in FAMILIES:
        values = config.get(part)
        if not values:
            continue
        shown = (noise_lab.label(values) if part == "noise" else
                 ",".join("%s=%s" % (name, values[name]) for name in DEFAULTS[part] if values[name] is not None))
        parts.append("%s:%s" % (part, shown))
    return ";".join(parts) or "product"


def _room(extra, mode, limit, have):
    """How many items a fill may add: `extra` beyond the limit, or only up to the limit in total (within)."""
    return extra if mode == "beyond" else max(0, min(extra, limit - have))


# ------------------------------------------------------------------ texts files

def load_texts(paths):
    """{kind: {"<world>/<id>": text}} from the texts files. A text is stripped and an empty one is dropped (it means
    "no text"). Several files of one kind are merged, a later file winning. SystemExit when a file has the wrong shape."""
    out = {}
    for path in paths or []:
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as handle:
                document = json.load(handle)
        except OSError as error:
            raise SystemExit("%s: cannot be read (%s)" % (name, error.strerror or error))
        except ValueError as error:
            raise SystemExit("%s: not valid JSON (%s)" % (name, error))
        if not isinstance(document, dict):
            raise SystemExit("%s: expected an object with \"kind\" and \"items\"" % name)
        kind, items = document.get("kind"), document.get("items")
        if not isinstance(kind, str) or not kind.strip():
            raise SystemExit("%s: \"kind\" must be a non-empty string" % name)
        if not isinstance(items, dict):
            raise SystemExit("%s: \"items\" must be an object of \"<world>/<id>\": text" % name)
        bucket = out.setdefault(kind.strip(), {})
        for key, text in items.items():
            if not isinstance(text, str):
                raise SystemExit("%s: the text of %r is not a string" % (name, key))
            if text.strip():
                bucket[key] = text.strip()
            else:
                bucket.pop(key, None)
    return out


def check_sources(configs, texts):
    """SystemExit when a config asks for a kind of text that was not given."""
    for config in configs:
        if config["second"] and config["second"]["src"] not in texts:
            raise SystemExit("second: no texts of kind %r were given (--texts); known kinds: %s" % (
                config["second"]["src"], ", ".join(sorted(texts)) or "none"))
        if (config["cue"] or config["cue2"]) and CUE_KIND not in texts:
            raise SystemExit("cue and cue2 need a texts file of kind %r (--texts)" % CUE_KIND)


# ------------------------------------------------------------------ the engine

class LabEngine(lab.Engine):
    """vector_lab's Engine for one world with the generated texts of the run: every lookup works on a list of
    cosines (one per memory) instead of a query string, so a cue can change them and a second query can be asked."""

    def __init__(self, world, vectors_of, scale, generated=None):
        super().__init__(world, vectors_of, scale)
        self.name = world["world"]
        self.generated = generated or {}  # not `texts`: Engine keeps the memory texts there
        self._cue_vectors = None
        self._cue_cosines = {}

    # ---- the generated texts

    def case_text(self, case, kind):
        text = self.generated.get(kind, {}).get("%s/%s" % (self.name, case["id"]))
        return text.strip() if isinstance(text, str) and text.strip() else None

    def cue_text(self, mid):
        text = self.generated.get(CUE_KIND, {}).get("%s/%s" % (self.name, mid))
        return text.strip() if isinstance(text, str) and text.strip() else None

    def cue_vectors(self):
        """One unit vector per memory (in `ids` order), None for a memory without a cue."""
        if self._cue_vectors is None:
            out = []
            for mid in self.ids:
                text = self.cue_text(mid)
                out.append(lab.unit32(self._vectors_of(self.scale.document_prefix + text)) if text else None)
            self._cue_vectors = out
        return self._cue_vectors

    # ---- cosines

    def cue_cosines(self, query):
        """cos(cue, query) per memory, None where the memory has no cue."""
        if query not in self._cue_cosines:
            vector = self.query_vector(query)
            self._cue_cosines[query] = [None if cue is None else lab._dot(vector, cue) for cue in self.cue_vectors()]
        return self._cue_cosines[query]

    def cosines_for(self, query, cue):
        """The cosines of the memories to `query`; with a `cue` setting, changed by the memories' cues."""
        text = self.base.cosines(query, self.query_vector(query))
        if not cue:
            return text
        out = []
        for cosine, other in zip(text, self.cue_cosines(query)):
            if other is None:
                out.append(cosine)
            elif cue["mode"] == "blend":
                out.append((1.0 - cue["w"]) * cosine + cue["w"] * other)
            else:
                out.append(max(cosine, other - cue["d"]))
        return out

    # ---- one lookup, as the product does it

    def noisy_lookup(self, cosines, noise=None):
        """lookup() with the filters of a parsed `noise` part (None: the product lookup itself)."""
        return noise_lab.lookup(self, cosines, noise)

    def lookup(self, cosines):
        """What Engine.evaluate does with the product config, for arbitrary cosines: the ranking before the lines, the
        delivered list, the linked additions and `items` = [(id, score)] of delivered then added."""
        scale = self.scale
        scores = [scale.score(c) for c in cosines]
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], -cosines[i], i))
        widen = lab.DEFAULTS["widen"]
        limit = widen["limit"]
        fetch = min(limit * 3, lab.MAX_FETCH)
        candidates = [lab.Candidate(score=scores[i], source="private", store=scale, ref=self.ids[i], text=self.texts[i],
                                    date=self.dates[i], meta={}) for i in order[:fetch] if scores[i] > 0.0]
        ctx = lab._Ctx(limit)
        stages = self.stage_list(widen)
        for stage in stages:
            candidates = list(stage.prepare(candidates, ctx))
        candidates.sort(key=lambda c: -c.score)
        lab.recall_module._admit_all(candidates, stages, ctx)
        delivered = [(c.ref, c.score) for c in ctx.accepted]
        delivered_ids = list(dict.fromkeys(mid for mid, _ in delivered))
        added = self._append(self.base, delivered_ids, limit, PRODUCT_APPEND) if delivered_ids else []
        return {"order": [self.ids[i] for i in order], "delivered": delivered, "added": added,
                "items": delivered + [(a["id"], a["score"]) for a in added]}

    # ---- the scorings (before the lines)

    def _scoring(self, case, config):
        """The queries and the per-memory scorings of a case under `config`; "scores" and "cos" are the combined one
        the rank metrics use (the maximum over the queries, and over the cue for cue2)."""
        queries = methods.queries_for("v0", case.get("history") or [], case["message"])
        if not queries:
            return {"queries": queries}
        remark = queries[0]
        cue, second = config["cue"], config["second"]
        first = self.cosines_for(remark, cue)
        text = query2 = second_cos = None
        if second:
            text = self.case_text(case, second["src"])
            if text:
                query2 = text if second["q"] == "text" else remark + "\n" + text
                second_cos = self.cosines_for(query2, cue)
        cue_cos = self.cue_cosines(remark) if config["cue2"] else None
        scores = [self.scale.score(c) for c in first]
        cos = list(first)
        for i in range(len(scores)):
            if second_cos is not None:
                scores[i] = max(scores[i], self.scale.score(second_cos[i]))
                cos[i] = max(cos[i], second_cos[i])
            if cue_cos is not None and cue_cos[i] is not None:
                scores[i] = max(scores[i], self.scale.score(cue_cos[i]))
                cos[i] = max(cos[i], cue_cos[i])
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], -cos[i], i))
        return {"queries": queries, "remark": remark, "text": text, "query2": query2, "first": first,
                "second": second_cos, "cue": cue_cos, "scores": scores, "cos": cos, "order": order}

    def ranking(self, case, config):
        """The memory ids of the world, best first, by the combined scoring (no lines); None without a query."""
        scoring = self._scoring(case, config)
        if "scores" not in scoring:
            return None
        return [self.ids[i] for i in scoring["order"]]

    def cue_hits(self, remark, settings):
        """[(id, score)] of the cue ranking alone: score >= min and >= ratio x the best cue score, best first."""
        scored = [(self.scale.score(c), i) for i, c in enumerate(self.cue_cosines(remark)) if c is not None]
        if not scored:
            return []
        best = max(score for score, _ in scored)
        return [(self.ids[i], score) for score, i in sorted(scored, key=lambda p: (-p[0], p[1]))
                if score > 0.0 and score >= settings["min"] and score >= settings["ratio"] * best]

    # ---- one case

    @staticmethod
    def _merge(items, hits, settings, limit):
        """The first list's items [(id, score, origin)] with the second lookup's hits [(id, score)] merged in."""
        if settings["merge"] == "rank":
            best = {mid: score for mid, score, _ in items}
            origin = {mid: how for mid, _, how in items}
            for mid, score in hits:
                if mid not in best:
                    best[mid], origin[mid] = score, "second"
                elif score > best[mid]:
                    best[mid] = score
            ranked = sorted(best, key=lambda mid: -best[mid])  # stable: the first list's order breaks a tie
            return [(mid, best[mid], origin[mid]) for mid in ranked[:limit]]
        have = {mid for mid, _, _ in items}
        room = _room(settings["extra"], settings["mode"], limit, len(items))
        fresh = []
        for mid, score in sorted(hits, key=lambda p: -p[1]):
            if mid in have or len(fresh) >= room:
                continue
            fresh.append((mid, score, "second"))
            have.add(mid)
        return items + fresh

    def evaluate(self, case, config):
        """Everything one case needs under `config` (a parsed spec). Besides the keys Engine.evaluate gives
        ("queries", "order", "scores", "cosines", "delivered", "added", "final"): "first" and "second" (the lookups),
        "text", "query2", "second_kept", "cue2_hits" and "ranking"; "delivered" is the first lookup's."""
        scoring = self._scoring(case, config)
        out = {"queries": scoring["queries"], "remark": None, "text": None, "query2": None, "first": None,
               "second": None, "second_kept": [], "cue2_hits": [], "order": [], "scores": {}, "cosines": {},
               "delivered": [], "added": [], "final": [], "ranking": [], "scoring": scoring}
        if "scores" not in scoring:
            return out
        n = len(self.ids)
        out["remark"], out["text"], out["query2"] = scoring["remark"], scoring["text"], scoring["query2"]
        out["order"] = out["ranking"] = [self.ids[i] for i in scoring["order"]]
        out["scores"] = {self.ids[i]: scoring["scores"][i] for i in range(n)}
        out["cosines"] = {self.ids[i]: scoring["cos"][i] for i in range(n)}

        noise = config["noise"]
        first = self.noisy_lookup(scoring["first"], noise)
        out["first"], out["delivered"] = first, first["delivered"]
        items = [(mid, score, "first") for mid, score in first["delivered"]]
        items += [(a["id"], a["score"], "link") for a in first["added"]]
        if scoring["second"] is not None:
            second = self.noisy_lookup(scoring["second"], noise)
            settings = config["second"]
            kept = [(mid, score) for mid, score in second["items"]
                    if settings["min"] is None or score >= settings["min"]]
            out["second"], out["second_kept"] = second, kept
            items = self._merge(items, kept, settings, LIMIT)
        if config["cue2"]:
            hits = self.cue_hits(scoring["remark"], config["cue2"])
            out["cue2_hits"] = hits
            have = {mid for mid, _, _ in items}
            room = _room(config["cue2"]["extra"], config["cue2"]["mode"], LIMIT, len(items))
            items += [(mid, score, "cue2") for mid, score in hits if mid not in have][:room]

        delivered_ids = {mid for mid, _ in first["delivered"]}
        sources = {a["id"]: a["source"] for a in first["added"]}
        out["final"] = [mid for mid, _, _ in items]
        out["added"] = [{"id": mid, "source": sources.get(mid) if how == "link" else None,
                         "score": round(score, 6), "kind": "vec" if how == "link" else how}
                        for mid, score, how in items if mid not in delivered_ids]
        return out

    def records(self, config):
        out = []
        for case in self.cases:
            result = self.evaluate(case, config)
            labels = {key: list(case.get(key) or []) for key in ("direct", "chain", "also_ok")}
            out.append({
                "world": self.name, "id": case["id"], "type": case["type"], "kind": case.get("kind"),
                "profile": "broad", "queries": result["queries"],
                "delivered": [{"id": mid, "score": round(score, 6)} for mid, score in result["delivered"]],
                "added": result["added"], "final": result["final"], "labels": labels,
            })
        return out


def build_lab_engines(worlds, cache, scale, texts=None):
    vectors = cache["vectors"]

    def vectors_of(text):
        try:
            return vectors[text]
        except KeyError:
            raise SystemExit("a vector is missing from the cache (run with --url once to fetch it)")

    return [LabEngine(world, vectors_of, scale, texts) for world in worlds]


# ------------------------------------------------------------------ vectors for the texts

def wanted_texts(configs):
    """({(src, q)}, cue wanted) over the parsed configs: which text vectors the run needs."""
    queries = {(c["second"]["src"], c["second"]["q"]) for c in configs if c["second"]}
    return queries, any(c["cue"] or c["cue2"] for c in configs)


def needed_strings(worlds, scale, texts, configs):
    """The strings the lab sends to the model: vector_lab's (memory texts and remark queries) and the generated texts
    the configs use (query texts with the query prefix, cues with the document prefix)."""
    wanted = list(lab.needed_strings(worlds, scale))
    queries, cues = wanted_texts(configs)
    for world in worlds:
        name = world["world"]
        for case in world["cases"]:
            found = methods.queries_for("v0", case.get("history") or [], case["message"])
            if not found:
                continue
            for src, how in sorted(queries):
                text = texts.get(src, {}).get("%s/%s" % (name, case["id"]))
                if text:
                    wanted.append(scale.query_prefix + (text if how == "text" else found[0] + "\n" + text))
        if cues:
            for mid in world["memories"]:
                text = texts.get(CUE_KIND, {}).get("%s/%s" % (name, mid))
                if text:
                    wanted.append(scale.document_prefix + text)
    return list(dict.fromkeys(wanted))


def ensure_cache(worlds, path, url, model, scale, texts, configs, say=print):
    """vector_lab.ensure_cache with the generated texts' strings added to what is needed."""
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


def coverage_lines(engines, texts):
    """One line per kind of text: how many cases (or memories, for the cues) of the split it covers."""
    lines = []
    for kind in sorted(texts):
        if kind == CUE_KIND:
            total = sum(len(e.ids) for e in engines)
            have = sum(1 for e in engines for mid in e.ids if e.cue_text(mid))
            lines.append("texts %s: %d of %d memories have a cue" % (kind, have, total))
        else:
            total = sum(len(e.cases) for e in engines)
            have = sum(1 for e in engines for case in e.cases if e.case_text(case, kind))
            lines.append("texts %s: %d of %d cases have a text" % (kind, have, total))
    return lines


# ------------------------------------------------------------------ results and comparison

def sign_test(gained, lost):
    """The exact two-sided p value of `gained` against `lost` flips under a fair coin (1.0 when there are none)."""
    n = gained + lost
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(gained, lost) + 1))
    return min(1.0, 2.0 * tail / 2 ** n)


def _hit(record):
    metrics = run.case_metrics(record["type"], record["final"], record["labels"]["direct"], record["labels"]["chain"],
                               record["labels"]["also_ok"])
    return metrics.get("direct_hit", metrics.get("hit"))


def flips(base_records, records):
    """How the cases of a run differ from the product's, matched by world and id."""
    by_key = {(r["world"], r["id"]): r for r in base_records}
    out = {"assoc_gained": 0, "assoc_lost": 0, "quiet_broken": 0, "quiet_gained": 0, "followup_gained": 0,
           "followup_lost": 0}
    for record in records:
        before = by_key[(record["world"], record["id"])]
        kind = record["type"]
        if kind == "remark-assoc":
            was, now = _hit(before), _hit(record)
            out["assoc_gained"] += int(now > was)
            out["assoc_lost"] += int(now < was)
        elif kind == "follow-up":
            was, now = _hit(before), _hit(record)
            out["followup_gained"] += int(now > was)
            out["followup_lost"] += int(now < was)
        elif kind == "quiet":
            out["quiet_broken"] += int(not before["final"] and bool(record["final"]))
            out["quiet_gained"] += int(bool(before["final"]) and not record["final"])
    out["assoc_p"] = sign_test(out["assoc_gained"], out["assoc_lost"])
    return out


def kind_rows(base_records, records):
    """[(kind, n, product direct hit, variant direct hit)] over the association remarks; the cases without a "kind"
    are `unlabelled`."""
    by_key = {(r["world"], r["id"]): r for r in base_records}
    groups = {}
    for record in records:
        if record["type"] == "remark-assoc":
            groups.setdefault(record.get("kind") or UNLABELLED, []).append(record)
    known = [k for k in KIND_ORDER if k in groups]
    names = known + sorted(k for k in groups if k not in KIND_ORDER and k != UNLABELLED)
    names += [UNLABELLED] if UNLABELLED in groups else []
    rows = []
    for name in names:
        members = groups[name]
        before = sum(_hit(by_key[(r["world"], r["id"])]) for r in members) / len(members)
        now = sum(_hit(r) for r in members) / len(members)
        rows.append((name, len(members), before, now))
    return rows


def rank_metrics(engines, config):
    """Association remarks only, on the scoring that does not pass the lines (the maximum of the queries' scores
    for second, of text and cue for cue2): the share whose best memory of the target cluster (the cluster of any
    direct or chain memory; the labelled memories themselves when a world has no clusters) is in the top 5, and the
    median rank of the fact (the best-ranked direct memory). A case without a lookup counts as a miss."""
    in_top = []
    ranks = []
    for engine in engines:
        memories = engine.world["memories"]
        for case in engine.cases:
            if case["type"] != "remark-assoc":
                continue
            direct = [m for m in case.get("direct") or [] if m in memories]
            labelled = set(direct) | {m for m in case.get("chain") or [] if m in memories}
            clusters = {memories[m].get("cluster") for m in labelled} - {None}
            target = labelled | {m for m, v in memories.items() if v.get("cluster") in clusters}
            ranking = engine.ranking(case, config)
            position = {mid: i + 1 for i, mid in enumerate(ranking or [])}
            miss = len(engine.ids) + 1
            if target:
                in_top.append(1.0 if min(position.get(m, miss) for m in target) <= 5 else 0.0)
            if direct:
                ranks.append(min(position.get(m, miss) for m in direct))
    return {"n": len(in_top), "top5": sum(in_top) / len(in_top) if in_top else None,
            "n_fact": len(ranks), "median_rank": statistics.median(ranks) if ranks else None}


def run_variant(engines, config, label=None):
    records = [r for engine in engines for r in engine.records(config)]
    return {"label": label or spec_label(config), "config": config, "records": records,
            "summary": run.summarize(records, with_links=True), "ranks": rank_metrics(engines, config)}


def counts(result):
    """The case counts the sweep rule works with: quiet cases left quiet and follow-ups hit."""
    records = result["records"]
    return {"quiet": sum(1 for r in records if r["type"] == "quiet" and not r["final"]),
            "followup": sum(1 for r in records if r["type"] == "follow-up" and _hit(r) == 1.0)}


# ------------------------------------------------------------------ the report

def _assoc(summary):
    return summary["overall"].get("remark-assoc") or {}


def _percent(value):
    return None if value is None else round(value * 100, 1)


def _fmt(value, places=1):
    return "-" if value is None else ("%." + str(places) + "f") % value


def headline(summary):
    """vector_lab's six numbers plus the recall of named remarks."""
    named = (summary["overall"].get("remark-named") or {}).get("direct_recall")
    return _vector_headline(summary) + [("named remark recall", _percent(named))]


def _vector_headline(summary):
    """vector_lab.headline's numbers (the same rounding), None where a split lacks the case type."""
    overall = summary["overall"]
    assoc, follow, quiet = _assoc(summary), overall.get("follow-up") or {}, overall.get("quiet") or {}
    items, noise = assoc.get("items"), assoc.get("noise")
    return [("assoc direct hit", _percent(assoc.get("direct_hit"))),
            ("assoc chain recall", _percent(assoc.get("chain_recall"))),
            ("items per assoc remark", None if items is None else round(items, 2)),
            ("noise per assoc remark", None if noise is None else round(noise, 2)),
            ("follow-up hit", _percent(follow.get("hit"))), ("quiet rate", _percent(quiet.get("quiet_rate")))]


def flip_lines(flip):
    return ["| flips against product | gained | lost |", "|---|---|---|",
            "| association direct hit | %d | %d |" % (flip["assoc_gained"], flip["assoc_lost"]),
            "| follow-up hit | %d | %d |" % (flip["followup_gained"], flip["followup_lost"]),
            "| quiet cases (stopped being quiet / became quiet) | %d | %d |" % (flip["quiet_broken"],
                                                                                flip["quiet_gained"]),
            "", "sign test on the association flips (exact, two-sided): p = %.4f" % flip["assoc_p"]]


def report_lines(result, base, table=False):
    """Markdown lines for one run: headline, flips, per kind, rank metrics, per world."""
    lines = ["variant: %s" % result["label"], "", "| metric | product | variant |", "|---|---|---|"]
    for (name, was), (_, now) in zip(headline(base["summary"]), headline(result["summary"])):
        lines.append("| %s | %s | %s |" % (name, _fmt(was, 2), _fmt(now, 2)))
    lines.append("")
    lines.extend(flip_lines(flips(base["records"], result["records"])))
    lines += ["", "| kind | n | product direct hit | variant direct hit |", "|---|---|---|---|"]
    for name, n, before, now in kind_rows(base["records"], result["records"]):
        lines.append("| %s | %d | %.1f | %.1f |" % (name, n, before * 100, now * 100))
    lines += ["", "| rank metric (association remarks, before the lines) | product | variant |", "|---|---|---|"]
    before, now = base["ranks"], result["ranks"]
    lines.append("| best cluster member in the top 5 (%%) | %s | %s |" % (
        _fmt(None if before["top5"] is None else before["top5"] * 100), _fmt(None if now["top5"] is None
                                                                             else now["top5"] * 100)))
    lines.append("| median rank of the fact | %s | %s |" % (_fmt(before["median_rank"]), _fmt(now["median_rank"])))
    lines += ["", "| world | product assoc direct hit | variant assoc direct hit |", "|---|---|---|"]
    for world, by_type in result["summary"]["worlds"].items():
        was = (base["summary"]["worlds"].get(world, {}).get("remark-assoc") or {}).get("direct_hit")
        now = (by_type.get("remark-assoc") or {}).get("direct_hit")
        lines.append("| %s | %s | %s |" % (world, _fmt(_percent(was)), _fmt(_percent(now))))
    if table:
        lines += ["", run.format_table(result["summary"])]
    return lines


def run_document(result, base):
    """The JSON form of a run: spec, summary, flips, per kind, rank metrics and the per-case records."""
    return {"spec": result["label"], "summary": result["summary"], "ranks": result["ranks"],
            "flips": flips(base["records"], result["records"]),
            "kinds": [{"kind": k, "n": n, "product": before, "variant": now}
                      for k, n, before, now in kind_rows(base["records"], result["records"])],
            "records": result["records"]}


# ------------------------------------------------------------------ sweep

def grid_second(src):
    out = []
    for q in ("text", "both"):
        for mode in ("within", "beyond"):
            for extra in (1, 2):
                for minimum in (None, 0.5, 0.6):
                    out.append("second:src=%s,q=%s,merge=fill,extra=%d,mode=%s%s" % (
                        src, q, extra, mode, "" if minimum is None else ",min=%s" % minimum))
        for minimum in (None, 0.5, 0.6):
            out.append("second:src=%s,q=%s,merge=rank%s" % (src, q, "" if minimum is None else ",min=%s" % minimum))
    return out


def grid_cue(src=None):
    return (["cue:mode=blend,w=%s" % w for w in (0.1, 0.2, 0.3, 0.5)]
            + ["cue:mode=max,d=%s" % d for d in (0.0, 0.05, 0.1, 0.15)])


def grid_cue2(src=None):
    return ["cue2:extra=%d,mode=%s,min=%s,ratio=%s" % (extra, mode, minimum, ratio)
            for mode in ("within", "beyond") for extra in (1, 2) for minimum in (0.4, 0.5, 0.6)
            for ratio in (0.8, 0.85, 0.9, 0.95)]


def grid_noise(src=None):
    return (["noise:hub=%s,k=%d" % (lam, k) for lam in (0.25, 0.5, 1.0, 2.0) for k in (5, 10, 20)]
            + ["noise:top=%s" % t for t in (1.5, 2.0, 2.5, 3.0, 3.5)]
            + ["noise:each=%s" % z for z in (0.5, 1.0, 1.5, 2.0)])


GRIDS = {"second": grid_second, "cue": grid_cue, "cue2": grid_cue2, "noise": grid_noise}
NO_RULE = ("noise",)  # families whose rows are read from the table, not picked by the rule
SWEEP_FAMILIES = tuple(GRIDS)
RULE = ("precision >= product's, items per association remark <= product's + %s, quiet cases left quiet >= product's "
        "- 1, follow-ups hit >= product's - 1; then the highest association direct hit + chain recall, ties to "
        "fewer items, then grid order" % EXTRA_ITEMS)


def _numbers(result):
    assoc = _assoc(result["summary"])
    return (assoc.get("direct_hit") or 0.0, assoc.get("chain_recall") or 0.0, assoc.get("precision") or 0.0,
            assoc.get("items") or 0.0)


def eligible(result, base):
    """The sweep rule (fixed before the experiments; RULE says it in words)."""
    _, _, precision, items = _numbers(result)
    _, _, base_precision, base_items = _numbers(base)
    mine, theirs = counts(result), counts(base)
    return (precision >= base_precision and items <= base_items + EXTRA_ITEMS
            and mine["quiet"] >= theirs["quiet"] - 1 and mine["followup"] >= theirs["followup"] - 1)


def winner(results, base):
    """The eligible result with the highest direct hit + chain recall (fewer items break a tie, then grid order);
    None when no row is eligible."""
    best = None
    for result in results:
        if not eligible(result, base):
            continue
        direct, chain, _, items = _numbers(result)
        if best is None or (direct + chain, -items) > best[0]:
            best = ((direct + chain, -items), result)
    return best[1] if best else None


SWEEP_HEADER = ("| variant | assoc direct hit | chain recall | precision | items per assoc remark | noise per assoc remark "
                "| follow-up hit | quiet rate | named recall | assoc gained | assoc lost | p | quiet broken "
                "| quiet gained | follow-up gained | follow-up lost | rule |")


def sweep_table(results, base, show_rule=True):
    lines = [SWEEP_HEADER, "|" + "|".join(["---"] * (SWEEP_HEADER.count("|") - 1)) + "|"]
    for result in [base] + list(results):
        values = dict(headline(result["summary"]))
        flip = flips(base["records"], result["records"])
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %d | %d | %.4f | %d | %d | %d | %d | %s |" % (
            result["label"], _fmt(values["assoc direct hit"]), _fmt(values["assoc chain recall"]),
            _fmt(_percent(_assoc(result["summary"]).get("precision"))), _fmt(values["items per assoc remark"], 2),
            _fmt(values["noise per assoc remark"], 2), _fmt(values["follow-up hit"]), _fmt(values["quiet rate"]),
            _fmt(values["named remark recall"]), flip["assoc_gained"], flip["assoc_lost"], flip["assoc_p"],
            flip["quiet_broken"], flip["quiet_gained"], flip["followup_gained"], flip["followup_lost"],
            "base" if result is base else ("ok" if show_rule and eligible(result, base) else "-")))
    return "\n".join(lines)


def run_sweep(engines, families, src, say):
    """Runs the grid of every family; prints one table (the product first) and the row each family's rule picks.
    Returns (rows, {family: winning spec or None}, the product's result)."""
    base = run_variant(engines, parse_spec("product"))
    rows = []
    winners = {}
    for family in families:
        specs = GRIDS[family](src)
        family_rows = [run_variant(engines, parse_spec(spec), spec) for spec in specs]
        rows.extend(family_rows)
        best = winner(family_rows, base) if family not in NO_RULE else None
        winners[family] = best["label"] if best else None
    ruled = [f for f in families if f not in NO_RULE]
    say(sweep_table(rows, base, show_rule=bool(ruled)))
    say("")
    if ruled:
        say("chosen row per family by the rule: %s" % RULE)
        for family in ruled:
            say("  %s: %s" % (family, winners[family] or "none eligible"))
    if len(ruled) < len(families):
        say("no rule picks a row for: %s (read the table)" % ", ".join(f for f in families if f in NO_RULE))
    return rows, winners, base


# ------------------------------------------------------------------ explain

def _top_lines(title, engine, case, pairs, verbose):
    """The TOP best (id, score, cosine) rows of a scoring, each marked with the case's label of the memory."""
    lines = ["  %s" % title]
    marks = {}
    for kind in ("direct", "chain", "also_ok"):
        for mid in case.get(kind) or []:
            marks[mid] = kind
    for rank, (mid, score, cosine) in enumerate(pairs[:TOP], 1):
        lines.append("    %2d %s score %.3f cos %s %s" % (rank, mid, score, "-" if cosine is None else "%.3f" % cosine,
                                                      marks.get(mid, "")))
        if verbose:
            lines.append("       %s" % engine.texts[engine.ids.index(mid)])
    return lines


def explain(engine, case, config, verbose=False):
    """Lines for one case under one variant: the queries, the generated text, the top 10 of each scoring, what each
    lookup delivered and what was added."""
    result = engine.evaluate(case, config)
    scoring = result["scoring"]
    lines = ["case %s/%s (%s%s) under %s" % (engine.name, case["id"], case["type"],
                                              ", " + case["kind"] if case.get("kind") else "", spec_label(config)),
             "  labels: direct=%s chain=%s also_ok=%s" % (case.get("direct") or [], case.get("chain") or [],
                                                         case.get("also_ok") or [])]
    if "scores" not in scoring:
        lines.append("  no lookup (the message is not worth one)")
        return lines
    ids, scale = engine.ids, engine.scale

    def pairs(cosines, score_of=scale.score):
        rows = [(ids[i], score_of(c), c) for i, c in enumerate(cosines) if c is not None]
        return sorted(rows, key=lambda p: (-p[1], -p[2], ids.index(p[0])))

    lines.append("  query: %s" % scoring["remark"])
    second = config["second"]
    if second:
        lines.append("  generated text (%s): %s" % (second["src"], scoring["text"] or "none for this case"))
        if scoring["query2"]:
            lines.append("  second query: %s" % scoring["query2"].replace("\n", " / "))
    lines.extend(_top_lines("top %d by the remark's query%s:" % (TOP, " (cue applied)" if config["cue"] else ""),
                            engine, case, pairs(scoring["first"]), verbose))
    if scoring["second"] is not None:
        lines.extend(_top_lines("top %d by the second query:" % TOP, engine, case, pairs(scoring["second"]), verbose))
    if scoring["cue"] is not None:
        lines.extend(_top_lines("top %d by the cue alone:" % TOP, engine, case, pairs(scoring["cue"]), verbose))
    if second or config["cue2"]:
        combined = [(ids[i], scoring["scores"][i], scoring["cos"][i]) for i in scoring["order"]]
        lines.extend(_top_lines("top %d by the combined scoring (rank metrics):" % TOP, engine, case, combined, verbose))
    first = result["first"]
    lines.append("  first lookup delivered: %s, linked added: %s" % (
        [m for m, _ in first["delivered"]], [a["id"] for a in first["added"]]))
    if result["second"] is not None:
        lines.append("  second lookup delivered: %s, linked added: %s, kept after min: %s" % (
            [m for m, _ in result["second"]["delivered"]], [a["id"] for a in result["second"]["added"]],
            [m for m, _ in result["second_kept"]]))
    if config["cue2"]:
        lines.append("  cue hits: %s" % [(m, round(s, 3)) for m, s in result["cue2_hits"]])
    lines.append("  final: %s" % result["final"])
    lines.append("  added to the first lookup's delivered list: %s" % [(a["id"], a["kind"]) for a in result["added"]])
    if verbose:
        for mid in result["final"]:
            lines.append("      %s %s" % (mid, engine.texts[engine.ids.index(mid)]))
    return lines


def find_case(engines, name):
    world_name, slash, case_id = name.rpartition("/")
    found = [(e, c) for e in engines if not slash or e.name == world_name for c in e.cases if c["id"] == case_id]
    if not found:
        raise SystemExit("no case %r in this split" % name)
    if len(found) > 1:
        raise SystemExit("case id %r exists in several worlds: write world/id (%s)" % (
            case_id, ", ".join(e.name for e, _ in found)))
    return found[0]


# ------------------------------------------------------------------ diagnose noise

CASE_TYPES = ("remark-assoc", "remark-named", "follow-up", "quiet")
Z_STEPS = (1.0, 1.5, 2.0)


def diagnose_noise(engines):
    """Per case type the largest z of the world for the case's query (cases without a query are left out and counted),
    the ROC-AUC of that and of the plain top score (the product's score of the best memory) for telling quiet cases from
    association remarks, and the share of association remarks (with a query and a `direct` memory) whose best `direct`
    memory has z at or above 1.0, 1.5 and 2.0."""
    z_by_type, top_by_type, skipped = {}, {}, {}
    direct_z = []
    for engine in engines:
        position = {mid: i for i, mid in enumerate(engine.ids)}
        for case in engine.cases:
            queries = methods.queries_for("v0", case.get("history") or [], case["message"])
            if not queries:
                skipped[case["type"]] = skipped.get(case["type"], 0) + 1
                continue
            cosines = engine.cosines_for(queries[0], None)
            z = noise_lab.zscores(cosines)
            z_by_type.setdefault(case["type"], []).append(max(z))
            top_by_type.setdefault(case["type"], []).append(engine.scale.score(max(cosines)))
            direct = [position[m] for m in case.get("direct") or [] if m in position]
            if case["type"] == "remark-assoc" and direct:
                direct_z.append(max(z[i] for i in direct))
    spread = {}
    for kind, values in z_by_type.items():
        spread[kind] = {"n": len(values), "skipped": skipped.get(kind, 0), "median": noise_lab.quantile(values, 0.5),
                        "q1": noise_lab.quantile(values, 0.25), "q3": noise_lab.quantile(values, 0.75)}
    return {"types": spread,
            "auc_z": noise_lab.auc(z_by_type.get("remark-assoc", []), z_by_type.get("quiet", [])),
            "auc_top_score": noise_lab.auc(top_by_type.get("remark-assoc", []), top_by_type.get("quiet", [])),
            "direct_z": {"n": len(direct_z), "shares": {str(t): (sum(1 for z in direct_z if z >= t) / len(direct_z)
                                                                 if direct_z else None) for t in Z_STEPS}}}


def diagnose_lines(found):
    lines = ["| case type | n | median | first quartile | third quartile | without a query |", "|---|---|---|---|---|---|"]
    for kind in list(CASE_TYPES) + sorted(k for k in found["types"] if k not in CASE_TYPES):
        row = found["types"].get(kind)
        if row:
            lines.append("| %s | %d | %s | %s | %s | %d |" % (kind, row["n"], _fmt(row["median"], 2), _fmt(row["q1"], 2),
                                                              _fmt(row["q3"], 2), row["skipped"]))
    lines += ["", "(largest z of the world's memories for the case's query)", "",
              "| ROC-AUC, association remarks against quiet cases | value |", "|---|---|",
              "| largest z | %s |" % _fmt(found["auc_z"], 3),
              "| plain top score (the product's score of the best memory) | %s |" % _fmt(found["auc_top_score"], 3), "",
              "| association remarks whose best direct memory has z at least | share (%%) of %d |" % found["direct_z"]["n"],
              "|---|---|"]
    for step in Z_STEPS:
        lines.append("| %s | %s |" % (step, _fmt(_percent(found["direct_z"]["shares"][str(step)]))))
    return lines


# ------------------------------------------------------------------ command line

def refuse_sweep(split):
    """SystemExit when --sweep would run on the frozen test split (--final does not lift this)."""
    for path in (split, os.path.realpath(split)):
        if os.path.basename(os.path.normpath(path)).lower() == lab.FORBIDDEN_SPLIT:
            raise SystemExit("refusing --sweep on %s: a grid is a choice of rules, and the test split is not for "
                             "choosing rules, not even with --final" % split)


def refuse_diagnose(split):
    """SystemExit when --diagnose would run on the frozen test split (--final does not lift this)."""
    for path in (split, os.path.realpath(split)):
        if os.path.basename(os.path.normpath(path)).lower() == lab.FORBIDDEN_SPLIT:
            raise SystemExit("refusing --diagnose on %s: it is a study for choosing rules, and the test split is not "
                             "for choosing rules, not even with --final" % split)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--split", required=True, help="folder with the world files (a folder named test needs --final)")
    parser.add_argument("--cache", required=True, help="the vector cache of vector_lab (not under bench/chat)")
    parser.add_argument("--url", help="embeddings endpoint base URL; only used for vectors the cache lacks")
    parser.add_argument("--model", default=lab.DEFAULT_MODEL, help="embedding model name (default bge-m3)")
    parser.add_argument("--final", action="store_true", help="allow the split named 'test' (one run, rules already fixed)")
    parser.add_argument("--texts", action="append", default=[], metavar="FILE",
                        help="a texts file {kind, items} of generated texts (repeatable)")
    parser.add_argument("--baseline", action="store_true", help="print the product baseline's headline")
    parser.add_argument("--run", action="append", default=[], metavar="SPEC", help="metrics of one variant (repeatable)")
    parser.add_argument("--table", action="store_true", help="also print run.py's full Markdown table of each run")
    parser.add_argument("--sweep", metavar="FAMILIES", help="comma list of %s" % ", ".join(SWEEP_FAMILIES))
    parser.add_argument("--src", help="with --sweep second: the kind of the query texts")
    parser.add_argument("--diagnose", metavar="WHAT", help="noise: the largest z per case type and the AUCs (development split)")
    parser.add_argument("--explain", metavar="CASE", help="with --run: one case in detail (world/id)")
    parser.add_argument("--verbose", action="store_true", help="with --explain: print memory texts")
    parser.add_argument("--out", metavar="JSON", help="write the records and summaries of the runs here")
    args = parser.parse_args(argv)

    if args.sweep:
        refuse_sweep(args.split)
    if args.diagnose:
        refuse_diagnose(args.split)
        if args.diagnose != "noise":
            raise SystemExit("unknown --diagnose %r (known: noise)" % args.diagnose)
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
        if "second" in families and not args.src:
            raise SystemExit("--sweep second needs --src <kind> (the kind of the query texts)")
    configs = [parse_spec(spec) for spec in args.run]
    sweep_configs = [parse_spec(spec) for family in families for spec in GRIDS[family](args.src)]
    texts = load_texts(args.texts)
    check_sources(configs + sweep_configs, texts)

    scale = lab.Scale.for_model(args.model)
    worlds = lab.load_worlds(args.split, args.final)
    cache = ensure_cache(worlds, args.cache, args.url, args.model, scale, texts, configs + sweep_configs, run.say)
    engines = build_lab_engines(worlds, cache, scale, texts)
    for line in coverage_lines(engines, texts):
        run.say(line)

    document = {"split": os.path.basename(os.path.normpath(args.split)), "model": args.model}
    base = run_variant(engines, parse_spec("product"))
    if args.baseline:
        run.say("")
        run.say("product: vector_lab baseline + %s" % PRODUCT_SPEC)
        run.say("| metric | product |")
        run.say("|---|---|")
        for name, value in headline(base["summary"]):
            run.say("| %s | %s |" % (name, _fmt(value, 2)))
        if args.table:
            run.say("")
            run.say(run.format_table(base["summary"]))
        document["product"] = {"summary": base["summary"], "ranks": base["ranks"], "records": base["records"]}
    results = []
    for config in configs:
        result = run_variant(engines, config)
        results.append(result)
        run.say("")
        for line in report_lines(result, base, args.table):
            run.say(line)
    if results:
        document["runs"] = [run_document(result, base) for result in results]
    if families:
        run.say("")
        rows, winners, _ = run_sweep(engines, families, args.src, run.say)
        document["sweep"] = {"rule": RULE, "winners": winners,
                             "rows": [{"spec": r["label"], "summary": r["summary"], "ranks": r["ranks"],
                                       "flips": flips(base["records"], r["records"])} for r in rows]}
    if args.diagnose:
        found = diagnose_noise(engines)
        run.say("")
        for line in diagnose_lines(found):
            run.say(line)
        document["diagnose"] = found
    if args.explain:
        engine, case = find_case(engines, args.explain)
        for config in configs:
            run.say("")
            for line in explain(engine, case, config, args.verbose):
                run.say(line)
    if args.out:
        run.write_json(args.out, document)
    return 0


if __name__ == "__main__":
    sys.exit(main())
