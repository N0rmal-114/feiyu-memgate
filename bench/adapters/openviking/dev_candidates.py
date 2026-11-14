"""Development-set candidate lists of the feiyu-memgate openviking backend (for bench/dev_eval.py).

    python dev_candidates.py --out DIR      (inside the benchmark container, `run.sh dev` does this)

Loads the DEVELOPMENT set (BENCH_DATA, default /repo/bench/dev; never bench/data) into the throw-away
OpenViking exactly as feiyu_memgate_openviking.py loads the held-out set (OpenVikingBackend.put, one
memory file per memory, oldest first, then a wait until a sample finds itself), then records for every
question the backend's top 15 hits (the fetch size of a gateway limit of 5) with their calibrated scores
(default floor 0.5 / ceil 1.0) and texts. Writes <out>/openviking-dev-candidates.json; the threshold
search runs offline on that file:

    python bench/dev_eval.py --backend openviking --candidates FILE --search
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ.get("FMG_SRC", "/repo/src"))

import ov_common as ov  # noqa: E402
from feiyu_memgate.backends.openviking import OpenVikingBackend  # noqa: E402

DATA_DIR = os.environ.get("BENCH_DATA", "/repo/bench/dev")
WARMUP_QUERY = "这是一个用来热身的问题"
FETCH = 15


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if os.path.basename(os.path.normpath(DATA_DIR)) == "data":
        raise SystemExit("bench/data is the held-out set: it must not be used to choose thresholds")
    memories, queries = load("memories.json"), load("queries.json")
    key = json.load(open("/work/keys.json", encoding="utf-8"))["gate"]

    backend = OpenVikingBackend(ov.BASE, key, timeout=30.0, writable=True)
    failures = 0
    for mid, m in sorted(memories.items(), key=lambda kv: (kv[1]["date"], kv[0])):
        try:
            backend.put(m["text"], ref=ov.event_uri("~", mid, m["date"]), date=m["date"])
        except Exception:  # noqa: BLE001
            failures += 1
    waited, confirmed = ov.wait_indexed(key, memories)
    print("ingested %d memories (%d put failures), index wait %.1f s, %d confirmed"
          % (len(memories), failures, waited, confirmed))
    try:
        backend.find(WARMUP_QUERY, 1)
    except Exception:  # noqa: BLE001
        pass

    ids, questions, errors = {}, {}, 0
    for qid, q in queries.items():
        try:
            hits = sorted(backend.find(q["query"], FETCH), key=lambda h: -h.score)
        except Exception:  # noqa: BLE001
            hits, errors = [], errors + 1
        listed = []
        for h in hits:
            ids[h.ref] = ov.memory_id(h.ref)
            text = h.text
            if not text.strip():
                try:
                    text = backend.read(h.ref)
                except Exception:  # noqa: BLE001
                    text = ""
            listed.append({"ref": h.ref, "score": h.score, "text": text, "date": h.date})
        questions[qid] = listed
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "openviking-dev-candidates.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"fetch": FETCH, "ids": ids, "questions": questions}, f, ensure_ascii=False)
        f.write("\n")
    print("wrote %s; %d query error(s)" % (os.path.basename(path), errors))


if __name__ == "__main__":
    main()
