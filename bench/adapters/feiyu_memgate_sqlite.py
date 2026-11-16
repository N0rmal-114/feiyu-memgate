"""Reference adapter: feiyu-memgate with its built-in sqlite backend, no model, no network.

    python bench/adapters/feiyu_memgate_sqlite.py [--out DIR] [--data DIR] [--profile precise]

Writes bench/results/raw/feiyu-memgate-sqlite.json (the contract is bench/CONTRACT.md): the gateway's defaults, which
are the broad profile since 2026-11-16 (the sqlite backend's recall-leaning lines, min_score 0.25 and ratio 0.70,
chosen on the development set); no profile is passed.
--profile precise writes feiyu-memgate-sqlite-precise.json: the same run with `[recall] profile = "precise"` pinned
(min_score 0.30 and ratio 0.75, also chosen on the development set).

How the system is driven, all in-process:
- ingest: backend.put(text, ref=<memory id>, date=<date>) on the sqlite backend, in date order;
- ranked: backend.find(query, 10) (the backend's own scores, no threshold);
- deployed: Gateway.recall("owner", {"query": ...}) built by config.build_gateway from a dict, with the
  DEFAULT recall stages (stages.DEFAULT_STAGES: min_score, relative_score, fill_text, drop_oversize, redact,
  dedupe, budget) and the default limit.
Each recall item carries the id of its memory (the ref it was stored under), so deployed
answers are reported by that id; an item whose id is not a dataset memory would be id null.

Every setting is the documented default of the backend and the stages (broad_min_score 0.25 and
broad_relative_ratio 0.70 by default; default_min_score 0.30 and default_relative_ratio 0.75 are the precise lines),
chosen on the development set bench/dev with bench/dev_eval.py, never on bench/data.
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
OUT_PATH = os.path.join(ROOT, "bench", "results", "raw", "feiyu-memgate-sqlite.json")
# Obviously fake: the gateway only needs a token of the minimum length to enable the client.
FAKE_TOKEN = "bench-only-fake-token-0000000000000000"
WARMUP_QUERY = "这是一个用来热身的问题"
TOP = 10


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def build(profile=None):
    """The in-process gateway: one in-memory sqlite store, one owner client, the default recall stages."""
    cfg = {
        "stores": {"private": {"type": "sqlite", "path": ":memory:"}},
        "clients": {"owner": {"token_env": "GATEWAY_TOKEN_OWNER", "recall": ["private"],
                              "write": "private"}},
    }
    if profile is not None:  # an explicit profile is always passed on, "precise" included
        cfg["recall"] = {"profile": profile}
    return config.build_gateway(cfg, env={"GATEWAY_TOKEN_OWNER": FAKE_TOKEN}, log=lambda line: None)


def ingest(backend, memories):
    """Every memory once, oldest first, with its id as the ref."""
    for mid, m in sorted(memories.items(), key=lambda kv: (kv[1]["date"], kv[0])):
        backend.put(m["text"], ref=mid, date=m["date"])


def item(mid, score):
    return {"id": mid, "score": round(float(score), 4)}


def run(out_path=OUT_PATH, profile=None):
    memories, queries = load("memories.json"), load("queries.json")
    precise = profile == "precise"
    gw = build(profile)
    backend = gw.stores["private"]
    start = time.perf_counter()
    ingest(backend, memories)
    ingest_seconds = time.perf_counter() - start
    unmapped = 0
    failures = 0

    gw.recall("owner", {"query": WARMUP_QUERY})  # one warm-up query before timing
    backend.find(WARMUP_QUERY, TOP)

    answers = {}
    for qid, q in queries.items():
        ranked = [item(h.ref, h.score) for h in backend.find(q["query"], TOP)]
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
        answers[qid] = {"ranked": ranked, "deployed": deployed, "ms": round(ms, 3)}
    gw.close()

    notes = [
        "Backend is the sqlite keyword backend: IDF-weighted containment of character bigrams and CJK single "
        "characters (a gram's weight is its BM25 inverse document frequency in the store); it does not understand "
        "meaning, so paraphrase questions are expected to be weak.",
        "ranked scores are the backend's own weighted-containment values, not comparable across systems.",
        None,  # the stage note, filled in below
        "Deployed items are reported by the id field of each recall item; %d item(s) named no dataset memory."
        % unmapped,
        "%d recall call(s) did not answer 200." % failures,
        "Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.",
    ]
    if precise:
        notes[2] = ("deployed uses the default recall stages with [recall] profile = 'precise' pinned (min_score with "
                    "the sqlite backend's precise line 0.30, relative_score with its ratio 0.75, fill_text, "
                    "drop_oversize, redact, dedupe, budget) and the default limit of 5; ms times that Gateway.recall "
                    "call only. The plain run of this backend uses the broad lines 0.25 and 0.70 instead.")
        notes.append("Precise profile: [recall] profile = 'precise': min_score 0.30 and relative_score ratio 0.75 (the "
                     "backend's default_min_score and default_relative_ratio, the precise lines) instead of the broad "
                     "0.25 and 0.70, chosen on the development set by the balanced rule of bench/dev_eval.py "
                     "(delivered F1 weighted with the quiet rate). It answers fewer questions and stays quiet more "
                     "often than the default.")
    else:
        notes[2] = ("deployed uses the default recall stages, whose default profile is broad (min_score with the "
                    "sqlite backend's broad line 0.25, relative_score with its ratio 0.70, fill_text, drop_oversize, "
                    "redact, dedupe, budget) and the default limit of 5; no profile is passed; ms times that "
                    "Gateway.recall call only. The broad lines were chosen on the development set by the broad rule "
                    "of bench/dev_eval.py (precision >= 50 % and at least half of the precise quiet rate, best "
                    "delivered hit); the precise lines are 0.30 and 0.75 (the -precise run).")
    result = {
        "system": "feiyu-memgate-sqlite-precise" if precise else "feiyu-memgate-sqlite", "version": __version__,
        "mode": "verbatim ingest, sqlite keyword backend (IDF-weighted grams), default recall stages%s, no embeddings, "
                "no LLM" % (", precise recall profile" if precise else " (broad profile)"),
        "how": "config.build_gateway(dict); SQLiteBackend.put(text, ref, date) per memory; "
               "SQLiteBackend.find(query, 10) for ranked; Gateway.recall('owner', {'query': q}) for deployed",
        "ingest_seconds": round(ingest_seconds, 3), "llm_tokens": 0, "llm_cost_cny": 0.0,
        "answers": answers, "notes": notes,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return out_path


def main(argv=None):
    global DATA_DIR
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=os.path.dirname(OUT_PATH), help="directory for the result file")
    parser.add_argument("--data", default=DATA_DIR, help="dataset directory (default: bench/data)")
    parser.add_argument("--profile", choices=("precise",), default=None,
                        help="pin the precise score lines (writes feiyu-memgate-sqlite-precise.json); without it the "
                             "gateway's defaults run, which are the broad profile")
    args = parser.parse_args(argv)
    DATA_DIR = args.data
    name = "feiyu-memgate-sqlite-precise.json" if args.profile == "precise" else os.path.basename(OUT_PATH)
    print("wrote " + run(os.path.join(args.out, name), args.profile))


if __name__ == "__main__":
    main()
