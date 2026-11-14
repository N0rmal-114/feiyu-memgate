"""Adapter: feiyu-memgate with its openviking backend, in front of a real (throw-away) OpenViking.

    python feiyu_memgate_openviking.py --out DIR      (inside the benchmark container, see run.sh)

Writes <out>/feiyu-memgate-openviking.json (contract: bench/CONTRACT.md).

- ingest: OpenVikingBackend(writable=True).put(text, ref=<memory URI>) per memory, oldest first, into the
  user `gate` (its own space, separate from the one the direct adapter uses). The URI is
  viking://~/memories/events/YYYY/MM/DD/<memory id>.md, so the id travels in the file name.
  Then wait until a sample of memories finds itself.
- ranked: the backend's find(query, 10) on a read-only store (writable false).
- deployed: Gateway.recall('owner', {'query': q}) built by config.build_gateway from a dict with the
  DEFAULT recall stages and the default limit.
- ids: recall items carry "id" = the memory's URI; the memory id is the file name. If an item has no "id"
  (older gateway), it falls back to exact text match.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ.get("FMG_SRC", "/repo/src"))

import ov_common as ov  # noqa: E402
from feiyu_memgate import __version__, config  # noqa: E402
from feiyu_memgate.backends.openviking import OpenVikingBackend  # noqa: E402
from feiyu_memgate.core import stages as stages_module  # noqa: E402

DATA_DIR = os.environ.get("BENCH_DATA", "/repo/bench/data")
# Obviously fake: the gateway only needs a token of the minimum length to enable the client.
FAKE_TOKEN = "bench-only-fake-token-0000000000000000"
WARMUP_QUERY = "这是一个用来热身的问题"
TOP = 10


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def build(key):
    """The in-process gateway: one read-only openviking store, one owner client, default stages."""
    cfg = {
        "stores": {"ov": {"type": "openviking", "url": ov.BASE, "key": key, "writable": False}},
        "clients": {"owner": {"token_env": "GATEWAY_TOKEN_OWNER", "recall": ["ov"]}},
    }
    return config.build_gateway(cfg, env={"GATEWAY_TOKEN_OWNER": FAKE_TOKEN}, log=lambda line: None)


def item(mid, score):
    return {"id": mid, "score": round(float(score), 4)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    memories, queries = load("memories.json"), load("queries.json")
    key = json.load(open("/work/keys.json", encoding="utf-8"))["gate"]
    by_text = {m["text"]: mid for mid, m in memories.items()}

    writer = OpenVikingBackend(ov.BASE, key, timeout=30.0, writable=True)
    start = time.perf_counter()
    put_failures = 0
    for mid, m in sorted(memories.items(), key=lambda kv: (kv[1]["date"], kv[0])):
        try:
            writer.put(m["text"], ref=ov.event_uri("~", mid, m["date"]), date=m["date"])
        except Exception:  # noqa: BLE001
            put_failures += 1
    write_seconds = time.perf_counter() - start
    waited, confirmed = ov.wait_indexed(key, memories)
    ingest_seconds = time.perf_counter() - start

    gw = build(key)
    backend = gw.stores["ov"]
    gw.recall("owner", {"query": WARMUP_QUERY})  # one warm-up query before timing
    try:
        backend.find(WARMUP_QUERY, TOP)
    except Exception:  # noqa: BLE001
        pass

    answers, unmapped, failures, ranked_failures = {}, 0, 0, 0
    for qid, q in queries.items():
        ranked, deployed = [], []
        t0 = time.perf_counter()
        reply = gw.recall("owner", {"query": q["query"]})
        ms = (time.perf_counter() - t0) * 1000.0
        if reply.status == 200:
            for r in reply.body["results"]:
                mid = ov.memory_id(r.get("id")) if r.get("id") else by_text.get(r.get("text"))
                unmapped += mid is None
                deployed.append(item(mid, r["score"]))
        else:
            failures += 1
        try:
            seen = set()
            for h in backend.find(q["query"], TOP):
                mid = ov.memory_id(h.ref)
                if mid is None or mid in seen:
                    continue
                seen.add(mid)
                ranked.append(item(mid, h.score))
        except Exception:  # noqa: BLE001
            ranked_failures += 1
        answers[qid] = {"ranked": ranked, "deployed": deployed, "ms": round(ms, 3)}

    notes = [
        "Memories written with OpenVikingBackend.put (content/write, mode create) as memory files, no session or "
        "extraction, so no language model is used; llm_tokens is 0. Ingest went into its own OpenViking user.",
        "ingest_seconds includes the wait until a sample of %d memories (and the last one written) found themselves "
        "(wait %.1f s; writes alone %.1f s, %d put failure(s))." % (confirmed, waited, write_seconds, put_failures),
        "ranked: backend.find(query, 10) of the read-only store; directory summaries (.abstract.md, .overview.md) "
        "are skipped by the backend itself, so ranked can hold fewer than 10 items.",
        "deployed uses the default recall stages (%s) and the default limit of 5; the gateway asks the backend for "
        "15 candidates; ms times that Gateway.recall call only. The backend maps OpenViking's raw score with its "
        "default floor %.2f / ceil %.2f (raw kept in Hit.meta); its defaults are min_score %.2f and relative ratio "
        "%.2f, so min_score %.2f means raw %.3f." % (
            ", ".join(stages_module.DEFAULT_STAGES), backend.floor, backend.ceil, backend.default_min_score,
            backend.default_relative_ratio, backend.default_min_score,
            backend.floor + backend.default_min_score * (backend.ceil - backend.floor)),
        "Backend and gateway timeouts are the defaults (backend socket timeout 2 s) except for ingest writes (30 s).",
        "Recall item ids: the memory id is the file name of the item's URI (id field); %d item(s) unmapped." % unmapped,
        "%d recall call(s) did not answer 200; %d backend find call(s) for ranked failed." % (failures, ranked_failures),
        "Gateway.recall was called directly, so the per-client rate limiter was not involved.",
    ]
    version = json.load(urllib.request.urlopen(ov.BASE + "/health")).get("version", "unknown")
    result = {
        "system": "feiyu-memgate-openviking", "version": "%s (OpenViking %s)" % (__version__, version),
        "mode": "verbatim memory-file ingest into OpenViking, feiyu-memgate openviking backend (read-only store), "
                "default recall stages, bge-m3 embeddings inside OpenViking, no LLM",
        "how": "config.build_gateway(dict); OpenVikingBackend.put(text, ref=viking://~/memories/events/Y/M/D/<id>.md) "
               "per memory; OpenVikingBackend.find(query, 10) for ranked; Gateway.recall('owner', {'query': q}) "
               "for deployed; id = file name of the item's URI",
        "ingest_seconds": round(ingest_seconds, 3), "llm_tokens": 0, "llm_cost_cny": 0.0,
        "answers": answers, "notes": notes,
    }
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "feiyu-memgate-openviking.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("wrote feiyu-memgate-openviking.json, recall failures=%d" % failures)


if __name__ == "__main__":
    main()
