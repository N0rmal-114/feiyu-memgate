"""Adapter: OpenViking on its own, through its search API (POST /api/v1/search/find).

    python openviking_direct.py --out DIR      (inside the benchmark container, see run.sh)

Writes <out>/openviking.json and <out>/openviking-unfiltered.json (contract: bench/CONTRACT.md).

- ingest: POST /api/v1/content/write {uri, content, mode: "create"} per memory, oldest first, under
  viking://user/owner/memories/events/YYYY/MM/DD/<memory id>.md. No session, no extraction, so no model
  is called and the memory id is in the URI. Then wait until a sample of memories finds itself.
- ranked: find {query, limit: 10, target_uri: viking://~/memories}.
- deployed: the same call with no limit and no score_threshold (the API defaults: limit 10, no threshold).
  The default limit/threshold of OpenViking's own plugin could not be verified (no package available
  offline), so the API default stands in for it.
- ids: the id is read back from the file name of the returned URI.

The two files come from the same calls. `openviking` drops results that are not memory files
(the .abstract.md / .overview.md directory summaries that OpenViking returns inside the memory tree);
`openviking-unfiltered` keeps them as items with id null, as the raw API returns them.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ov_common as ov  # noqa: E402

DATA_DIR = os.environ.get("BENCH_DATA", "/repo/bench/data")
WARMUP_QUERY = "这是一个用来热身的问题"
TOP = 10


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def find(key, query, limit=None):
    body = {"query": query, "target_uri": ov.TARGET}
    if limit is not None:
        body["limit"] = limit
    status, reply = ov.call("POST", "/api/v1/search/find", key, body)
    if status != 200:
        raise RuntimeError("HTTP %s" % status)
    return reply["result"]["memories"]


def items(raw, keep_structural):
    """Contract items from raw results: memory files by id, others as id null (or dropped)."""
    out, seen = [], set()
    for r in raw:
        mid = ov.memory_id(r["uri"])
        if mid is None:
            if keep_structural:
                out.append({"id": None, "score": round(float(r["score"]), 4)})
            continue
        if mid in seen:
            continue
        seen.add(mid)
        out.append({"id": mid, "score": round(float(r["score"]), 4)})
    return out


def write(path, result):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
        f.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    memories, queries = load("memories.json"), load("queries.json")
    key = json.load(open("/work/keys.json", encoding="utf-8"))["owner"]
    version = json.load(urllib.request.urlopen(ov.BASE + "/health")).get("version", "unknown")

    start = time.perf_counter()
    write_failures = ov.ingest_direct(key, "owner", memories)
    write_seconds = time.perf_counter() - start
    waited, confirmed = ov.wait_indexed(key, memories)
    ingest_seconds = time.perf_counter() - start

    find(key, WARMUP_QUERY, TOP)
    find(key, WARMUP_QUERY)
    filtered, unfiltered, failures, structural = {}, {}, 0, 0
    for qid, q in queries.items():
        try:
            t0 = time.perf_counter()
            deployed_raw = find(key, q["query"])
            ms = (time.perf_counter() - t0) * 1000.0
            ranked_raw = find(key, q["query"], TOP)
        except Exception:  # noqa: BLE001 - counted, the question gets empty lists
            failures += 1
            filtered[qid] = unfiltered[qid] = {"ranked": [], "deployed": [], "ms": 0.0}
            continue
        structural += sum(ov.memory_id(r["uri"]) is None for r in ranked_raw)
        for target, keep in ((filtered, False), (unfiltered, True)):
            target[qid] = {"ranked": items(ranked_raw, keep), "deployed": items(deployed_raw, keep),
                           "ms": round(ms, 3)}

    notes = [
        "Memories were written directly as memory files (content/write, mode create) under events/YYYY/MM/DD/<id>.md "
        "with the memory date in the path; OpenViking's session/commit extraction (which needs a language model) "
        "was not used, so llm_tokens is 0. A real deployment would extract facts with a model, which this does not measure.",
        "Embeddings: bge-m3 (1024) through the shared embedder; indexing is asynchronous, ingest_seconds includes "
        "the wait until a sample of %d memories (and the last one written) found themselves (wait %.1f s; "
        "writes alone %.1f s, %d write failure(s))." % (confirmed, waited, write_seconds, write_failures),
        "Scores are OpenViking's own search scores, not comparable across systems.",
        "deployed: the plugin's default limit/threshold is UNVERIFIED (its package is not available offline); "
        "the API defaults stand in for it: limit 10, no score threshold. So deployed equals ranked in size.",
        "The search over viking://~/memories also returns directory summaries (.abstract.md/.overview.md): %d such "
        "item(s) among the ranked results of all questions." % structural,
        "%d question(s) failed (empty lists)." % failures,
        "ms is one find call with the API defaults, measured inside the container, including the server's "
        "query embedding through the shared embedder.",
        "An OV background task may try to summarise directories with its language model; no key and no internet "
        "are available, so those attempts fail harmlessly and do not affect file search.",
    ]
    base = {
        "version": version, "ingest_seconds": round(ingest_seconds, 3), "llm_tokens": 0, "llm_cost_cny": 0.0,
        "how": "POST /api/v1/content/write per memory (mode create, URI viking://user/owner/memories/events/Y/M/D/<id>.md); "
               "POST /api/v1/search/find {query, limit 10, target_uri viking://~/memories} for ranked; "
               "the same call without limit and score_threshold for deployed; id = file name of the result URI",
    }
    os.makedirs(args.out, exist_ok=True)
    write(os.path.join(args.out, "openviking.json"), dict(
        base, system="openviking",
        mode="direct memory-file writes (no extraction), bge-m3 embeddings via ollama provider, API defaults, no LLM; "
             "directory summaries dropped from results",
        answers=filtered, notes=notes + ["This file drops results that are not memory files (id would be null)."]))
    write(os.path.join(args.out, "openviking-unfiltered.json"), dict(
        base, system="openviking-unfiltered",
        mode="same calls as openviking, directory summaries kept as id null items",
        answers=unfiltered, notes=notes + ["This file keeps non-memory results as id null (noise) in ranked and deployed."]))
    print("wrote openviking.json and openviking-unfiltered.json, failures=%d" % failures)


if __name__ == "__main__":
    main()
