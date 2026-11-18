"""Run the chat test set (bench/chat/README.md) against a real gateway and score what it hands over.

    python -B bench/chat/run.py --split bench/chat/dev --backend sqlite --table
    python -B bench/chat/run.py --split bench/chat/dev --backend hybrid --model bge-m3 --query v2 --out out.json
    python -B bench/chat/run.py --split bench/chat/dev --backend hybrid --links either --link-min-kw 0.2 --table
    python -B bench/chat/run.py --split bench/chat/dev --backend embedding --linked off --table

For every world file in the split folder: a fresh temporary store, the 60 memories written through the
real gateway in process (LocalClient.from_config, remember_messages, `time` = the memory's date, at most
20 messages per call), then every case is looked up through recall_detail with the gateway's own recall
stages. No threshold is re-implemented here: --profile picks the gateway's "broad" or "precise" lines,
and --remark-profile precise sends the messages that are not questions (methods.is_question) to a second
client over the same store files with profile "precise". The delivered texts are mapped back to memory ids
through the refs the store gave them.

--linked on|off picks the gateway's recall stages: on (the default) is the gateway's own default list, which
holds the `linked` stage (it adds, for a store that has `neighbours`, the memories linked to the first two
delivered ones); off is the same list without `linked`, so before and after the stage are measured through
the real gateway. The --links flags below are another thing: an experiment that adds neighbours on top of
whatever was delivered, after the fact, outside the gateway.

Query variants, the question test and link expansion live in methods.py. Link expansion (--links) asks for
the neighbours of the first --link-top delivered memories and adds up to --link-max of them to what was
delivered; the metrics below are computed on the list the user would get, delivered memories first and
added ones after. How the neighbours are found:
    kw      a keyword (sqlite) store of the same world, asked with the delivered memory's text as the query
    vec     the embedding store of the same world (the main store, or the embedding child of a hybrid), asked the same
            way; not available with --backend sqlite
    either  both. The two scores are on different scales (IDF-weighted keyword containment against a
            floor/ceil-mapped cosine), so they are never compared with each other: a neighbour needs its own
            kind's minimum (--link-min-kw, --link-min-vec), and the keyword neighbours are ranked before the
            vector ones (each kind best score first); a memory found by both counts as keyword. Its record says
            which kind found it.
The defaults of the two minimums are placeholders (0.15, 0.5); choose them on the development split.

Deterministic: no clock and no randomness reach the output. The refs the stores give to written memories
come from a counter instead of uuid4 while the memories are written, so ties in a store's ordering fall
the same way in every run.

Metrics. Every number is computed per case from the final list (delivered, then added), then averaged over
the cases of its group. Groups: each case type, each world, and overall (all worlds pooled: a mean over
cases, not a mean of world means). D = the final list, direct / chain / also_ok = the labels of the case,
labelled = their union, noise = the members of D that are not in labelled.
    remark-assoc  direct_hit      1 when D holds at least one direct memory
                  direct_recall   |D & direct| / |direct|
                  chain_hit       1 when D holds at least one chain memory
                  chain_recall    |D & chain| / |chain|    (both chain numbers leave out the cases without a
                                  chain memory; "chain_n" says how many cases count)
                  items           |D|
                  noise           |D - labelled|
                  precision       |D & labelled| / |D|, over the cases with |D| > 0; "precision_n" counts
                                  them and "precision_empty" the cases left out because nothing was delivered
    remark-named  direct_recall, direct_hit (as above), items, noise
    follow-up     hit             1 when D holds at least one direct memory
                  items, noise
    quiet         quiet_rate      1 when D is empty
                  items
    all           n, items and noise (mean over all cases), hit (mean of direct_hit / hit over the cases that
                  are not quiet), quiet_rate (over the quiet cases)
    links         only with --links: added (memories added, summed over the group), added_chain, added_direct,
                  added_also_ok, added_noise (how many of them are chain, direct, also_ok or in no label)
A mean over no case is null. "n" is the number of cases of the group.

--ranks adds, per case, the rank (1 = best) and the score of every labelled memory in the store's own
unthresholded ranking of the same query (for v3 the two rankings are merged by memory id, best score wins);
a memory the store does not return at all gets null.

The output JSON holds the settings, one record per case (queries used, delivered ids with scores, added ids
with their source, ranks when asked, the labels) and the summary. Memory texts are not printed or stored
unless --verbose.
"""
import argparse
import contextlib
import importlib.util
import itertools
import json
import os
import sys
import tempfile
import threading
import uuid
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))


def _sibling(name):
    """A module of this folder under the name bench_chat_<name>. Not a plain import: other benchmark folders
    have a run.py of their own, and a shared module name would make one replace the other in one process."""
    key = "bench_chat_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, os.path.join(HERE, name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


methods = _sibling("methods")
from feiyu_memgate.backends.embedding import EmbeddingBackend  # noqa: E402
from feiyu_memgate.backends.sqlite import SQLiteBackend  # noqa: E402
from feiyu_memgate.client import LocalClient  # noqa: E402
from feiyu_memgate.core import stages as stages_module  # noqa: E402

BACKENDS = ("sqlite", "embedding", "hybrid")
TYPES = ("remark-assoc", "remark-named", "follow-up", "quiet")
DEFAULT_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "bge-m3"
EMBED_TIMEOUT = 60.0
WARMUP_QUERY = "这是一个用来热身的问题"
WRITE_BATCH = 20  # the gateway's limit of messages per remember call
SETTING_NAMES = ("profile", "limit", "query", "short", "context_chars", "remark_profile", "linked", "links",
                 "link_top", "link_max", "link_min_kw", "link_min_vec", "ranks")
DEFAULTS = {
    "profile": "broad", "limit": 5, "query": "v0", "short": methods.SHORT, "context_chars": methods.CONTEXT_CHARS,
    "remark_profile": "none", "linked": "on", "links": "none", "link_top": 3, "link_max": 2, "link_min_kw": 0.15,
    "link_min_vec": 0.5, "ranks": False,
}


class RunError(Exception):
    """Something that would make the numbers wrong: the run stops instead of scoring around it."""


# ------------------------------------------------------------------ settings

def merged_settings(*layers):
    """DEFAULTS with every layer on top (a layer is a dict; None values and unknown names are an error or ignored)."""
    out = dict(DEFAULTS)
    for layer in layers:
        for key, value in (layer or {}).items():
            if key not in DEFAULTS:
                raise SystemExit("unknown setting %r (known: %s)" % (key, ", ".join(SETTING_NAMES)))
            if value is not None:
                out[key] = value
    return out


def check_settings(settings, backend):
    """The settings, or SystemExit with a message that says what is wrong."""
    if backend not in BACKENDS:
        raise SystemExit("backend must be one of %s" % ", ".join(BACKENDS))
    for key, allowed in (("profile", ("broad", "precise")), ("query", methods.VARIANTS),
                         ("remark_profile", ("none", "precise")), ("linked", ("on", "off")),
                         ("links", ("none", "kw", "vec", "either"))):
        if settings[key] not in allowed:
            raise SystemExit("%s must be one of %s, not %r" % (key, ", ".join(allowed), settings[key]))
    for key in ("limit", "short", "context_chars", "link_top", "link_max"):
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < (1 if key == "limit" else 0):
            raise SystemExit("%s must be a whole number (at least %d)" % (key, 1 if key == "limit" else 0))
    for key in ("link_min_kw", "link_min_vec"):
        if isinstance(settings[key], bool) or not isinstance(settings[key], (int, float)):
            raise SystemExit("%s must be a number" % key)
    if settings["links"] in ("vec", "either") and backend == "sqlite":
        raise SystemExit("--links %s needs the embedding store of the world, which --backend sqlite does not have: "
                         "use --backend embedding or hybrid, or --links kw" % settings["links"])
    return settings


def add_setting_arguments(parser):
    """The flags that make up one setting. None means "not given": the default (or a grid entry) applies."""
    parser.add_argument("--profile", choices=("broad", "precise"), help="the gateway's recall profile (default broad)")
    parser.add_argument("--limit", type=int, help="results asked for per lookup (default 5)")
    parser.add_argument("--query", choices=methods.VARIANTS, help="query variant (default v0)")
    parser.add_argument("--short", type=int, help="v1-v3: a message with fewer meaningful characters is extended (default 8)")
    parser.add_argument("--context-chars", type=int, help="v1-v3: characters of context (default 120)")
    parser.add_argument("--remark-profile", choices=("none", "precise"),
                        help="precise: a message that is not a question is looked up with the precise profile")
    parser.add_argument("--linked", choices=("on", "off"),
                        help="on: the gateway's default stages, with `linked` (default); off: the same without it")
    parser.add_argument("--links", choices=("none", "kw", "vec", "either"), help="link expansion (default none)")
    parser.add_argument("--link-top", type=int, help="delivered memories followed (default 3)")
    parser.add_argument("--link-max", type=int, help="memories added at most (default 2)")
    parser.add_argument("--link-min-kw", type=float, help="keyword neighbour minimum (default 0.15)")
    parser.add_argument("--link-min-vec", type=float, help="vector neighbour minimum (default 0.5)")
    parser.add_argument("--ranks", action="store_true", default=None,
                        help="also record rank and score of every labelled memory in an unthresholded ranking")


def settings_from_args(args):
    return merged_settings({name: getattr(args, name, None) for name in SETTING_NAMES})


def add_store_arguments(parser):
    parser.add_argument("--split", required=True, help="folder with the world files (*.json)")
    parser.add_argument("--backend", choices=BACKENDS, required=True)
    parser.add_argument("--url", default=DEFAULT_URL, help="OpenAI-compatible embeddings base URL (embedding, hybrid)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="embedding model name (embedding, hybrid)")


# ------------------------------------------------------------------ worlds

def world_files(split):
    if not os.path.isdir(split):
        raise SystemExit("%s is not a folder" % split)
    names = sorted(name for name in os.listdir(split) if name.endswith(".json"))
    if not names:
        raise SystemExit("no world files in %s" % split)
    return [os.path.join(split, name) for name in names]


def load_world(path):
    with open(path, encoding="utf-8") as handle:
        world = json.load(handle)
    for key in ("world", "memories", "cases"):
        if key not in world:
            raise SystemExit("%s: missing %r" % (os.path.basename(path), key))
    return world


@contextlib.contextmanager
def counted_ids():
    """uuid.uuid4 as a counter while the memories are written: the stores name a write after it, and the
    names break ties between equal scores."""
    counter = itertools.count(1)
    lock = threading.Lock()

    def fake():
        with lock:
            number = next(counter)
        return uuid.UUID(int=number)

    with mock.patch.object(uuid, "uuid4", fake):
        yield


class _Warnings:
    """The client's logger: keeps the one-line warnings (they never carry texts) for an error message."""

    def __init__(self):
        self.lines = []

    def warning(self, fmt, *args):
        try:
            self.lines.append(fmt % args)
        except Exception:  # noqa: BLE001
            self.lines.append(str(fmt))


def store_spec(backend, directory, url, model):
    keyword = {"type": "sqlite", "path": os.path.join(directory, "keyword.db")}
    vector = {"type": "embedding", "path": os.path.join(directory, "vector.db"), "url": url, "model": model,
              "timeout": EMBED_TIMEOUT}
    if backend == "sqlite":
        return keyword
    if backend == "embedding":
        return vector
    return {"type": "hybrid", "children": [keyword, vector]}  # sqlite first, then embedding: the project's order


class Session:
    """One world: its store files, the gateways over them, the maps from refs to memory ids, the neighbour stores."""

    def __init__(self, world, backend, url, model, directory):
        self.world = world
        self.backend = backend
        self.directory = directory
        self.spec = store_spec(backend, directory, url, model)
        self.memories = world["memories"]
        self.by_text = {m["text"]: mid for mid, m in self.memories.items()}
        self.refs = {}
        self.warnings = _Warnings()
        self._clients = {}
        self._written = False
        self._keyword = None
        self._neighbours = {}

    # ---- gateways

    def client(self, profile, limit, linked="on"):
        key = (profile, limit, linked)
        if key not in self._clients:
            recall = {"profile": profile}
            if linked == "off":  # the gateway's default list without the `linked` stage
                recall["stages"] = [name for name in stages_module.DEFAULT_STAGES if name != "linked"]
            config = {
                "stores": {"private": self.spec},
                # no rate limit that could stop a long run; no daily cap is set either
                "clients": {"owner": {"recall": ["private"], "write": "private", "rate_limit": 1000000}},
                "recall": recall,
            }
            client = LocalClient.from_config(config, client="owner", env={}, log=lambda line: None,
                                             logger=self.warnings, recall_limit=limit)
            self._clients[key] = client
            if not self._written:
                self._write(client)
        return self._clients[key]

    def store(self):
        return next(iter(self._clients.values())).gateway.stores["private"]

    def _write(self, client):
        store = client.gateway.stores["private"]
        if self.backend != "sqlite":
            store.find(WARMUP_QUERY, 1, timeout=EMBED_TIMEOUT)  # loads the model before anything counts
        rows = sorted(self.memories.items(), key=lambda item: (item[1]["date"], item[0]))
        with counted_ids():
            for start in range(0, len(rows), WRITE_BATCH):
                batch = rows[start:start + WRITE_BATCH]
                status = client.remember_messages(
                    [{"role": "user", "content": m["text"], "time": m["date"]} for _, m in batch])
                if status != "saved":
                    raise RunError("%s: writing memories %s..%s gave %r (%s)" % (
                        self.world["world"], batch[0][0], batch[-1][0], status, "; ".join(self.warnings.lines[-3:])))
        for ref, text, _ in store.items():
            mid = self.by_text.get(text)
            if mid is None:
                raise RunError("%s: a stored memory differs from every memory of the world (a write guard changed it?)"
                               % self.world["world"])
            self.refs[ref] = mid
        if len(self.refs) != len(self.memories):
            raise RunError("%s: %d memories stored, %d expected" % (
                self.world["world"], len(self.refs), len(self.memories)))
        self._written = True

    # ---- lookups

    def lookup(self, client, queries, limit, case_id):
        """[(memory id, score)] delivered for the queries (merged by memory id when there are two)."""
        answers = []
        for query in queries:
            detail = client.recall_detail(query)
            if detail is None:
                raise RunError("%s/%s: the lookup failed (%s)" % (
                    self.world["world"], case_id, "; ".join(self.warnings.lines[-3:]) or "no reason logged"))
            answer = []
            for item in detail["results"]:
                mid = self.refs.get(item.get("id"))
                if mid is None:
                    mid = self.by_text.get(item.get("text"), "unknown:%s" % item.get("id"))
                answer.append((mid, float(item["score"])))
            answers.append(answer)
        if len(answers) == 1:
            return answers[0]
        return methods.merge_scored(answers, limit)

    def ranking(self, queries):
        """[(memory id, score)]: the main store's unthresholded ranking of the queries, merged for two."""
        store = self.store()
        size = len(self.memories)
        answers = [[(self.refs[hit.ref], float(hit.score)) for hit in store.find(query, size, timeout=EMBED_TIMEOUT)]
                   for query in queries]
        return methods.merge_scored(answers, size)

    # ---- neighbours

    def _keyword_store(self):
        if self._keyword is None:
            store = SQLiteBackend(":memory:")
            for mid, memory in self.memories.items():
                store.put(memory["text"], ref=mid, date=memory["date"])
            self._keyword = store
        return self._keyword

    def _vector_store(self):
        main = self.store()
        for candidate in [main] + list(getattr(main, "children", [])):
            if isinstance(candidate, EmbeddingBackend):
                return candidate
        raise RunError("this backend has no embedding store")

    def neighbours(self, kind, mid):
        """[(memory id, score)] for one memory, best first (a tie by id), without the memory itself."""
        key = (kind, mid)
        if key not in self._neighbours:
            text = self.memories[mid]["text"]
            if kind == "kw":
                hits = [(hit.ref, hit.score) for hit in self._keyword_store().find(text, len(self.memories))]
            else:
                hits = [(self.refs[hit.ref], hit.score) for hit in
                        self._vector_store().find(text, len(self.memories), timeout=EMBED_TIMEOUT)]
            self._neighbours[key] = sorted(((m, float(s)) for m, s in hits if m != mid), key=lambda p: (-p[1], p[0]))
        return self._neighbours[key]

    def close(self):
        for client in self._clients.values():
            client.close()
        if self._keyword is not None:
            self._keyword.close()


# ------------------------------------------------------------------ one setting over one world

def expansion(session, settings, delivered):
    """The memories link expansion adds to `delivered` (ids), as records with "kind" and the real score."""
    links = settings["links"]
    if links == "none" or not delivered:
        return []
    own = {}  # id -> (kind, real score) for the "either" case, where the score is a ranking key

    if links == "either":
        def neighbours_of(mid):
            out = []
            for memory, score in session.neighbours("kw", mid):
                if score >= settings["link_min_kw"]:
                    out.append((memory, 1.0 + score))  # keyword neighbours rank before every vector neighbour
                    own[(mid, memory)] = ("kw", score)
            for memory, score in session.neighbours("vec", mid):
                if score >= settings["link_min_vec"] and (mid, memory) not in own:
                    out.append((memory, score))
                    own[(mid, memory)] = ("vec", score)
            return out
        minimum = 0.0
    else:
        def neighbours_of(mid):
            return session.neighbours(links, mid)
        minimum = settings["link_min_kw"] if links == "kw" else settings["link_min_vec"]

    added = methods.expand(delivered, neighbours_of, settings["link_top"], settings["link_max"], minimum)
    out = []
    for item in added:
        if links == "either":
            kind, score = own[(item["source"], item["id"])]
        else:
            kind, score = links, item["score"]
        out.append({"id": item["id"], "source": item["source"], "score": round(score, 6), "kind": kind})
    return out


def run_case(session, case, settings, verbose=False):
    kind = case["type"]
    message = case["message"]
    history = case.get("history") or []
    labels = {key: list(case.get(key) or []) for key in ("direct", "chain", "also_ok")}
    queries = methods.queries_for(settings["query"], history, message, settings["short"], settings["context_chars"])
    profile = settings["profile"]
    if settings["remark_profile"] == "precise" and not methods.is_question(message):
        profile = "precise"
    client = session.client(profile, settings["limit"], settings["linked"])
    delivered = session.lookup(client, queries, settings["limit"], case["id"]) if queries else []
    delivered_ids = list(dict.fromkeys(mid for mid, _ in delivered))
    added = expansion(session, settings, delivered_ids)
    record = {
        "world": session.world["world"], "id": case["id"], "type": kind, "profile": profile, "queries": queries,
        "delivered": [{"id": mid, "score": round(score, 6)} for mid, score in delivered],
        "added": added,
        "final": delivered_ids + [item["id"] for item in added],
        "labels": labels,
    }
    if settings["ranks"]:
        ranking = session.ranking(queries) if queries else []
        position = {mid: (index + 1, round(score, 6)) for index, (mid, score) in enumerate(ranking)}
        wanted = [mid for key in ("direct", "chain", "also_ok") for mid in labels[key]]
        record["ranks"] = {mid: {"rank": position[mid][0] if mid in position else None,
                                 "score": position[mid][1] if mid in position else None} for mid in wanted}
    if verbose:
        record["texts"] = {mid: session.memories[mid]["text"] for mid in record["final"] if mid in session.memories}
    return record


def run_split(split, backend, url, model, settings_list, verbose=False):
    """One result per settings dict: {"settings", "cases", "summary"}. Every world is written once and
    asked for every setting, so a grid costs one embedding pass per world."""
    for settings in settings_list:
        check_settings(settings, backend)
    results = [{"settings": settings, "cases": []} for settings in settings_list]
    for path in world_files(split):
        world = load_world(path)
        with tempfile.TemporaryDirectory(prefix="chat-bench-", ignore_cleanup_errors=True) as directory:
            session = Session(world, backend, url, model, directory)
            try:
                for result in results:
                    for case in world["cases"]:
                        result["cases"].append(run_case(session, case, result["settings"], verbose))
            finally:
                session.close()
    for result in results:
        result["summary"] = summarize(result["cases"], result["settings"]["links"] != "none")
    return results


# ------------------------------------------------------------------ metrics

def case_metrics(kind, final, direct, chain, also_ok):
    """The numbers of one case (see the module docstring); None where a number is not defined."""
    final = list(dict.fromkeys(final))
    direct, chain, also_ok = set(direct), set(chain), set(also_ok)
    labelled = direct | chain | also_ok
    got = set(final)
    out = {"items": len(final), "noise": sum(1 for mid in final if mid not in labelled)}
    if kind in ("remark-assoc", "remark-named"):
        out["direct_hit"] = 1.0 if got & direct else 0.0
        out["direct_recall"] = len(got & direct) / len(direct) if direct else None
    if kind == "remark-assoc":
        out["chain_hit"] = (1.0 if got & chain else 0.0) if chain else None
        out["chain_recall"] = len(got & chain) / len(chain) if chain else None
        out["precision"] = len(got & labelled) / len(final) if final else None
    if kind == "follow-up":
        out["hit"] = 1.0 if got & direct else 0.0
    if kind == "quiet":
        out["quiet_rate"] = 1.0 if not final else 0.0
    return out


def _mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def _group(kind, records, with_links):
    per_case = [case_metrics(kind, r["final"], r["labels"]["direct"], r["labels"]["chain"], r["labels"]["also_ok"])
                for r in records]
    names = {"remark-assoc": ("direct_hit", "direct_recall", "chain_hit", "chain_recall", "items", "noise", "precision"),
             "remark-named": ("direct_recall", "direct_hit", "items", "noise"),
             "follow-up": ("hit", "items", "noise"),
             "quiet": ("quiet_rate", "items")}[kind]
    out = {"n": len(records)}
    for name in names:
        out[name] = _mean([m.get(name) for m in per_case])
    if kind == "remark-assoc":
        out["chain_n"] = sum(1 for m in per_case if m["chain_hit"] is not None)
        out["precision_n"] = sum(1 for m in per_case if m["precision"] is not None)
        out["precision_empty"] = len(per_case) - out["precision_n"]
    if with_links:
        out.update(_added(records))
    return out


def _added(records):
    out = {"added": 0, "added_chain": 0, "added_direct": 0, "added_also_ok": 0, "added_noise": 0}
    for record in records:
        labels = record["labels"]
        for item in record["added"]:
            out["added"] += 1
            for key in ("chain", "direct", "also_ok"):
                if item["id"] in labels[key]:
                    out["added_" + key] += 1
                    break
            else:
                out["added_noise"] += 1
    return out


def _by_type(records, with_links):
    return {kind: _group(kind, [r for r in records if r["type"] == kind], with_links)
            for kind in TYPES if any(r["type"] == kind for r in records)}


def _all(records, with_links):
    per_case = [(r["type"], case_metrics(r["type"], r["final"], r["labels"]["direct"], r["labels"]["chain"],
                                         r["labels"]["also_ok"])) for r in records]
    hits = [m.get("direct_hit", m.get("hit")) for kind, m in per_case if kind != "quiet"]
    out = {"n": len(records), "items": _mean([m["items"] for _, m in per_case]),
           "noise": _mean([m["noise"] for _, m in per_case]), "hit": _mean(hits),
           "quiet_rate": _mean([m.get("quiet_rate") for _, m in per_case])}
    if with_links:
        out.update(_added(records))
    return out


def summarize(records, with_links=False):
    """{"overall": {type: numbers}, "all": numbers, "worlds": {world: {type: numbers}}}."""
    worlds = list(dict.fromkeys(r["world"] for r in records))
    return {"overall": _by_type(records, with_links), "all": _all(records, with_links),
            "worlds": {w: _by_type([r for r in records if r["world"] == w], with_links) for w in worlds}}


# ------------------------------------------------------------------ output

TABLE_COLUMNS = ("direct_hit", "direct_recall", "chain_hit", "chain_recall", "hit", "quiet_rate", "precision", "items",
                 "noise")
_COUNTS = ("items", "noise")


def _cell(name, value):
    if value is None:
        return "-"
    return "%.2f" % value if name in _COUNTS else "%.1f" % (value * 100)


def format_table(summary):
    """A Markdown table: rates in percent, items and noise per case."""
    with_links = any("added" in numbers for numbers in summary["overall"].values())
    header = ["scope", "type", "n"] + list(TABLE_COLUMNS) + (["added", "chain", "direct", "also_ok", "noise+"] if with_links else [])
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]

    def rows(scope, by_type):
        for kind, numbers in by_type.items():
            cells = [scope, kind, str(numbers["n"])] + [_cell(name, numbers.get(name)) for name in TABLE_COLUMNS]
            if with_links:
                cells += [str(numbers[key]) for key in ("added", "added_chain", "added_direct", "added_also_ok", "added_noise")]
            lines.append("| " + " | ".join(cells) + " |")

    rows("overall", summary["overall"])
    for world, by_type in summary["worlds"].items():
        rows(world, by_type)
    return "\n".join(lines)


def result_document(result, split, backend, url, model):
    settings = dict(result["settings"], backend=backend, split=os.path.basename(os.path.normpath(split)))
    if backend != "sqlite":
        settings.update(model=model, url=url)
    return {"settings": settings, "cases": result["cases"], "summary": result["summary"]}


def write_json(path, document):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)
        handle.write("\n")


def say(text):
    """Print as UTF-8 whatever the console's code page is (the output holds Chinese)."""
    stream = sys.stdout
    if hasattr(stream, "buffer"):
        stream.buffer.write((text + "\n").encode("utf-8"))
        stream.buffer.flush()
    else:
        stream.write(text + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    add_store_arguments(parser)
    add_setting_arguments(parser)
    parser.add_argument("--out", metavar="JSON", help="write settings, per-case records and summary here")
    parser.add_argument("--table", action="store_true", help="print a Markdown summary")
    parser.add_argument("--verbose", action="store_true", help="include and print memory texts")
    args = parser.parse_args(argv)
    settings = settings_from_args(args)
    try:
        result = run_split(args.split, args.backend, args.url, args.model, [settings], args.verbose)[0]
    except RunError as error:
        raise SystemExit("run stopped: %s" % error)
    document = result_document(result, args.split, args.backend, args.url, args.model)
    if args.out:
        write_json(args.out, document)
    if args.verbose:
        for record in result["cases"]:
            line = "%s/%s %s delivered=%s added=%s" % (
                record["world"], record["id"], record["type"], [d["id"] for d in record["delivered"]],
                [a["id"] for a in record["added"]])
            say(line)
            for mid, text in record["texts"].items():
                say("    %s %s" % (mid, text))
    if args.table:
        say(format_table(result["summary"]))
    elif not args.out:
        say("%d cases" % len(result["cases"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
