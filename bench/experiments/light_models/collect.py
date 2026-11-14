"""Step 1 of the light-model experiment: embed one development set with one model and keep the raw numbers.

    python bench/experiments/light_models/collect.py --embed-url URL --model NAME --cache DIR \
        [--data bench/dev] [--query-prefix TEXT] [--document-prefix TEXT] [--tag TAG]

Everything is measured through the real EmbeddingBackend (a SQLite file, put_many in date order, batches of 32),
with floor -1 and ceil 1 so that every memory comes back with its raw cosine in Hit.meta. The cache file
(<cache>/<dataset>__<tag>.json) holds, for the later offline steps (analyze.py): the cosine of every question to
every memory, the mean cosine over all pairs of stored memories, the ingest time, the size of the database file
and the latency of the query embedding (distinct queries, sequential, warm). No memory text is stored in it.

A cache is only for this experiment and is deleted afterwards; it is not part of the repository.
"""
import argparse
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "bench"))

import dev_eval as dev  # noqa: E402
import score  # noqa: E402
sys.path.insert(0, HERE)
import models  # noqa: E402
from feiyu_memgate.backends.embedding import EmbeddingBackend, _sumprod  # noqa: E402

TIMEOUT = 120.0
WARMUPS = 3


def dataset_name(data_dir):
    return os.path.basename(os.path.normpath(data_dir))


def pair_mean(vectors):
    """Mean cosine over all pairs of distinct stored memories (vectors are unit length)."""
    total, pairs = 0.0, 0
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            total += _sumprod(vectors[i], vectors[j])
            pairs += 1
    return total / pairs if pairs else None


def collect(url, model, query_prefix, document_prefix, data_dir):
    memories = dev.load("memories.json", data_dir)
    queries = dev.load("queries.json", data_dir)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "vectors.db")
        be = EmbeddingBackend(path, url, model, timeout=TIMEOUT, floor=-1.0, ceil=1.0,
                              query_prefix=query_prefix, document_prefix=document_prefix)
        try:
            for _ in range(WARMUPS):  # loads the model; nothing is timed before this
                be._embed(["这是一个用来热身的问题"], TIMEOUT, None, query_prefix)
            ordered = sorted(memories.items(), key=lambda kv: (dev.memory_date(kv[1]) or "", kv[0]))
            start = time.perf_counter()
            be.put_many([(dev.memory_text(m), mid, dev.memory_date(m)) for mid, m in ordered])
            ingest_seconds = time.perf_counter() - start
            refs = [mid for mid, _ in ordered]
            cos = {}
            for qid, q in queries.items():
                hits = be.find(q["query"], len(refs), timeout=TIMEOUT)
                cos[qid] = {h.ref: h.meta["cosine"] for h in hits}
                if set(cos[qid]) != set(refs):
                    raise SystemExit("a memory is missing from the ranking of %s" % qid)
            millis = []
            for q in queries.values():  # distinct queries: nothing comes from the query cache
                t0 = time.perf_counter()
                be._embed([q["query"]], TIMEOUT, None, query_prefix)
                millis.append((time.perf_counter() - t0) * 1000.0)
            mean_pairs = pair_mean([v for _, v in be._snapshot()])
            dim = be._dim
            be._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # fold the write-ahead log into the one file
        finally:
            be.close()
        files = {f: os.path.getsize(os.path.join(tmp, f)) for f in os.listdir(tmp)}
        size = files.get("vectors.db", 0)
    return {
        "model": model, "query_prefix": query_prefix, "document_prefix": document_prefix,
        "dataset": dataset_name(data_dir), "memories": len(refs), "dim": dim, "refs": refs, "cos": cos,
        "pair_mean": mean_pairs, "ingest_seconds": ingest_seconds, "db_bytes": size,
        "query_ms": {"n": len(millis), "p50": score.percentile(millis, 50), "p95": score.percentile(millis, 95),
                     "mean": sum(millis) / len(millis)},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model-key", required=True, choices=sorted(models.BY_KEY), help="entry of models.py")
    parser.add_argument("--variant", choices=("plain", "prefixed"), required=True,
                        help="no prefix, or the prefixes the model card recommends (models.py)")
    parser.add_argument("--embed-url", help="default: the endpoint models.py names for this model")
    parser.add_argument("--cache", required=True, help="directory for the cache file")
    parser.add_argument("--data", default=dev.DEV_DIR)
    args = parser.parse_args(argv)
    if os.path.normcase(os.path.abspath(args.data)) == os.path.normcase(os.path.join(ROOT, "bench", "data")):
        raise SystemExit("bench/data is the held-out test set: it must not be used here")
    model = models.BY_KEY[args.model_key]
    qp, dp = ("", "") if args.variant == "plain" else (model["recommended"]["query_prefix"],
                                                      model["recommended"]["document_prefix"])
    url = args.embed_url or models.ENDPOINTS[model["endpoint"]]
    result = collect(url, model["name"], qp, dp, args.data)
    args.tag = "%s__%s" % (args.model_key, args.variant)
    os.makedirs(args.cache, exist_ok=True)
    path = os.path.join(args.cache, "%s__%s.json" % (result["dataset"], args.tag))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False)
    print("%s %s: dim %d, %d memories, pair mean cosine %.4f, ingest %.2f s, db %d bytes, query p50 %.1f ms p95 %.1f ms"
          % (result["dataset"], args.tag, result["dim"], result["memories"], result["pair_mean"],
             result["ingest_seconds"], result["db_bytes"], result["query_ms"]["p50"], result["query_ms"]["p95"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
