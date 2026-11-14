"""Adapter: feiyu-memgate with its built-in embedding backend (vector search through an embeddings API).

    python bench/adapters/feiyu_memgate_embedding.py [--out DIR] [--embed-url URL] [--model NAME]
                                                     [--system-suffix SUFFIX] [--option KEY=JSON ...]
                                                     [--profile precise|broad]

Writes <out>/feiyu-memgate-embedding.json (the contract is bench/CONTRACT.md; default out is
bench/results/raw). With --system-suffix small-zh the system is called feiyu-memgate-embedding-small-zh and the
file <out>/feiyu-memgate-embedding-small-zh.json, so one run per model can sit next to the default one;
--option sets an option of the embedding backend (a JSON value, e.g. --option floor=0.35).
--profile broad sets `[recall] profile = "broad"` of the gateway (the recall-leaning score lines the backend declares,
backends/embedding.py PRESETS; chosen on the development set), and needs a suffix that contains "broad", so the broad
run never overwrites the default one. A model the backend has a preset for (backends/embedding.py) runs with that preset's floor, ceil, prefixes
and default score lines, nothing is passed here; the notes of the result file say which values applied. The embeddings service is not part of this repository: the default URL is the
shared benchmark embedder, which is only reachable from inside the docker network that hosts it.

How the system is driven, all in-process:
- ingest: EmbeddingBackend.put_many([(text, id, date), ...]) in date order (embedding requests are
  batched, 32 texts each); one call, every memory once;
- deployed: Gateway.recall("owner", {"query": ...}) built by config.build_gateway from a dict with the
  DEFAULT recall stages (stages.DEFAULT_STAGES) and the default limit; timed first, so the query embedding
  is a real request;
- ranked: backend.find(query, 10) (the backend's own scores, no threshold), called after the deployed
  call, so the query embedding comes from the backend's small query cache.
Each recall item carries the id of its memory (the ref it was stored under), so deployed answers are
reported by that id; an item whose id is not a dataset memory would be id null.

Every setting is the documented default of the backend and the stages (for bge-m3: floor 0.40, ceil 0.80,
default_min_score 0.50, default_relative_ratio 0.85), chosen on the development sets with bench/dev_eval.py,
never on bench/data.

feiyu_memgate_hybrid.py and feiyu_memgate_full.py reuse run_system() from this file.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from feiyu_memgate import __version__, config  # noqa: E402

DATA_DIR = os.path.join(ROOT, "bench", "data")
DEFAULT_OUT = os.path.join(ROOT, "bench", "results", "raw")
DEFAULT_EMBED_URL = "http://fmg-bench-embedder:11434/v1"
DEFAULT_MODEL = "bge-m3"
# Obviously fake: the gateway only needs a token of the minimum length to enable the client.
FAKE_TOKEN = "bench-only-fake-token-0000000000000000"
WARMUP_QUERY = "这是一个用来热身的问题"
TOP = 10
# The first call may load the model (about 8 s on CPU); the backend's own timeout must allow that.
BACKEND_TIMEOUT = 60.0

SYSTEM = "feiyu-memgate-embedding"
MODE = ("verbatim ingest, embedding backend (bge-m3 via an OpenAI-compatible endpoint, brute-force cosine), "
        "default recall stages, no LLM")
HOW = ("config.build_gateway(dict); EmbeddingBackend.put_many([(text, id, date)]) once for all memories; "
       "Gateway.recall('owner', {'query': q}) for deployed; EmbeddingBackend.find(query, 10) for ranked")


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def store_spec(embed_url, model, options=None):
    """The embedding store; `options` (a dict) are extra backend options such as floor or query_prefix."""
    return dict({"type": "embedding", "path": ":memory:", "url": embed_url, "model": model,
                 "timeout": BACKEND_TIMEOUT}, **(options or {}))


def build(spec, stages=None, forgetting=None, wall=None, profile=None):
    """The in-process gateway: one in-memory store, one owner client. `stages` None means the default
    recall stages; `forgetting` is a [forgetting] table (the stage list then names the freshness stage)."""
    cfg = {
        "stores": {"private": spec},
        "clients": {"owner": {"token_env": "GATEWAY_TOKEN_OWNER", "recall": ["private"], "write": "private"}},
    }
    if stages is not None:
        cfg["recall"] = {"stages": stages}
    if forgetting is not None:
        cfg["forgetting"] = forgetting
    if profile not in (None, "precise"):
        cfg.setdefault("recall", {})["profile"] = profile
    extra = {} if wall is None else {"wall": wall}
    return config.build_gateway(cfg, env={"GATEWAY_TOKEN_OWNER": FAKE_TOKEN}, log=lambda line: None, **extra)


def ingest(backend, memories):
    """Every memory once, oldest first, with its id as the ref and its own date; embeddings fetched in batches."""
    ordered = sorted(memories.items(), key=lambda kv: (kv[1]["date"], kv[0]))
    backend.put_many([(m["text"], mid, m["date"]) for mid, m in ordered])


def item(mid, score):
    return {"id": mid, "score": round(float(score), 4)}


def run_system(spec, out_dir, system, mode, how, notes_head, stages=None, forgetting=None, wall=None,
               prepare=None, after_question=None, rank=None, notes_tail=(), profile=None):
    """Run one configuration over the benchmark and write its raw result file; returns the path.

    Optional hooks, for a configuration with state beyond the store (see feiyu_memgate_full.py):
    prepare(gateway, backend) runs once after ingest; after_question(gateway, reply) runs after the warm-up
    recall (reply None) and after every question's deployed recall; rank(gateway, backend, query) returns
    [(id, score)] for `ranked` (default: backend.find(query, 10))."""
    memories, queries = load("memories.json"), load("queries.json")
    gw = build(spec, stages, forgetting, wall, profile)
    backend = gw.stores["private"]
    try:
        backend.find(WARMUP_QUERY, TOP, timeout=BACKEND_TIMEOUT)  # load the model before anything is timed
        start = time.perf_counter()
        ingest(backend, memories)
        ingest_seconds = time.perf_counter() - start
        if prepare is not None:
            prepare(gw, backend)
        unmapped = failures = ranked_failures = 0
        gw.recall("owner", {"query": WARMUP_QUERY + "。"})  # one warm-up recall before timing
        if after_question is not None:
            after_question(gw, None)
        answers = {}
        for qid, q in queries.items():
            t0 = time.perf_counter()
            reply = gw.recall("owner", {"query": q["query"]})
            ms = (time.perf_counter() - t0) * 1000.0
            deployed = []
            if reply.status == 200:
                for r in reply.body["results"]:
                    mid = r.get("id") if r.get("id") in memories else None
                    unmapped += mid is None
                    deployed.append(item(mid, r["score"]))
            else:
                failures += 1
            try:
                if rank is None:
                    ranked = [item(h.ref, h.score) for h in backend.find(q["query"], TOP, timeout=BACKEND_TIMEOUT)]
                else:
                    ranked = [item(mid, s) for mid, s in rank(gw, backend, q["query"])]
            except Exception:  # noqa: BLE001 - a failed question gets an empty list and a note
                ranked, ranked_failures = [], ranked_failures + 1
            if after_question is not None:
                after_question(gw, reply)
            answers[qid] = {"ranked": ranked, "deployed": deployed, "ms": round(ms, 3)}
    finally:
        gw.close()
    notes = list(notes_head) + [
        "Deployed items are reported by the id field of each recall item; %d item(s) named no dataset memory."
        % unmapped,
        "%d recall call(s) did not answer 200; %d ranked search(es) failed." % (failures, ranked_failures),
        "deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses "
        "the backend's query-embedding cache.",
        "Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.",
        "Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.",
    ] + list(notes_tail)
    result = {
        "system": system, "version": __version__, "mode": mode, "how": how,
        "ingest_seconds": round(ingest_seconds, 3), "llm_tokens": 0, "llm_cost_cny": 0.0,
        "answers": answers, "notes": notes,
    }
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, system + ".json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return path


def system_name(system, suffix):
    """`system` with an optional suffix: feiyu-memgate-embedding + small-zh -> feiyu-memgate-embedding-small-zh."""
    return system + "-" + suffix if suffix else system


def parse_options(pairs):
    """["floor=0.4", "calibration=auto"] -> {"floor": 0.4, "calibration": "auto"} (a value that is not JSON is a string)."""
    out = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit("option %r must look like key=value" % pair)
        try:
            out[key] = json.loads(value)
        except ValueError:
            out[key] = value
    return out


def effective(spec):
    """The calibration the embedding backend of `spec` runs with (a throw-away in-memory store, nothing is
    embedded): (floor, ceil, default_min_score, default_relative_ratio, query_prefix, document_prefix)."""
    from feiyu_memgate.backends.embedding import EmbeddingBackend
    options = {k: v for k, v in spec.items() if k not in ("type", "path", "timeout")}
    be = EmbeddingBackend(":memory:", **options)
    try:
        return (be.floor, be.ceil, be.default_min_score, be.default_relative_ratio,
                be._query_prefix, be._doc_prefix)
    finally:
        be.close()


def profile_notes(args, spec, notes):
    """For a broad run: add a note naming the broad score lines of this backend (changes `notes` in place) and
    return the mode suffix; an empty string for the default profile."""
    if args.profile != "broad":
        return ""
    from feiyu_memgate.backends.embedding import EmbeddingBackend
    options = {k: v for k, v in spec.items() if k not in ("type", "path", "timeout")}
    be = EmbeddingBackend(":memory:", **options)
    try:
        notes.append("Broad profile: [recall] profile = 'broad' makes min_score and relative_score use the recall-leaning "
                     "lines of this backend (min_score %.2f, ratio %.2f instead of %.2f and %.2f), chosen on the development "
                     "set by the rule of bench/dev_eval.py --profile broad: precision >= 50 %% and at least half of the "
                     "default quiet rate, best delivered hit. It trades precision and silence for answered questions."
                     % (be.broad_min_score, be.broad_relative_ratio, be.default_min_score, be.default_relative_ratio))
    finally:
        be.close()
    return ", broad recall profile"


def parse_args(doc, argv=None):
    """The command line shared by the three vector adapters. --data points the run at another dataset
    directory (same file names and shape as bench/data), for a smoke test that must not touch the real one."""
    global DATA_DIR
    parser = argparse.ArgumentParser(description=doc.split("\n")[0])
    parser.add_argument("--out", default=DEFAULT_OUT, help="directory for the result file")
    parser.add_argument("--embed-url", default=DEFAULT_EMBED_URL, help="OpenAI-compatible embeddings base URL")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="embedding model name")
    parser.add_argument("--data", default=DATA_DIR, help="dataset directory (default: bench/data)")
    parser.add_argument("--system-suffix", default="",
                        help="appended to the system name and the result file name (default: none)")
    parser.add_argument("--option", action="append", metavar="KEY=JSON",
                        help="option of the embedding backend (repeatable)")
    parser.add_argument("--profile", choices=("precise", "broad"), default="precise",
                        help="score-line profile of the recall stages (default: precise, the backend's defaults)")
    args = parser.parse_args(argv)
    if args.profile == "broad" and "broad" not in args.system_suffix:
        raise SystemExit("--profile broad needs a --system-suffix that contains 'broad' (its own result file)")
    DATA_DIR = args.data
    return args


def main(argv=None):
    args = parse_args(__doc__, argv)
    notes = [
        "Backend is the embedding backend: bge-m3 vectors (1024 dimensions) from an embeddings endpoint, "
        "cosine mapped to 0..1 with floor 0.40 and ceil 0.80 (chosen on the development sets, not on this data).",
        "ranked scores are the mapped relevance values, not comparable across systems.",
        "deployed uses the default recall stages (min_score with the backend's default 0.50, relative_score with "
        "the backend's default ratio 0.85, fill_text, drop_oversize, redact, dedupe, budget) and the default limit "
        "of 5; ms times that Gateway.recall call only.",
    ]
    spec = store_spec(args.embed_url, args.model, parse_options(args.option))
    if args.model != DEFAULT_MODEL or args.option or args.system_suffix:
        floor, ceil, min_score, ratio, qp, dp = effective(spec)
        notes[0] = ("Backend is the embedding backend: model %s, cosine mapped to 0..1 with floor %.2f and ceil %.2f "
                    "(the backend's values for this model, chosen on the development sets, not on this data); "
                    "query prefix %s, document prefix %s." % (args.model, floor, ceil, "set" if qp else "none",
                                                              "set" if dp else "none"))
        notes[2] = notes[2].replace("the backend's default 0.50", "the backend's default %.2f" % min_score) \
                           .replace("the backend's default ratio 0.85", "the backend's default ratio %.2f" % ratio)
    mode = MODE if args.model == DEFAULT_MODEL else MODE.replace("bge-m3", args.model)
    mode += profile_notes(args, spec, notes)
    path = run_system(spec, args.out, system_name(SYSTEM, args.system_suffix), mode, HOW, notes, profile=args.profile)
    print("wrote " + path)


if __name__ == "__main__":
    main()
