"""Adapter: feiyu-memgate with everything turned on: hybrid backend + default stages + freshness.

    python bench/adapters/feiyu_memgate_full.py [--out DIR] [--embed-url URL] [--model NAME]

Writes <out>/feiyu-memgate-full.json (the contract is bench/CONTRACT.md; default out is bench/results/raw).
This is the configuration a user gets by following the documentation to the end: the hybrid backend of
feiyu_memgate_hybrid.py, the default recall stages with the `freshness` stage of the forgetting curve put
right after `min_score` (docs/forgetting.md), and a [forgetting] section with the documented defaults
(Ebbinghaus curve, freshness floor 0.3). Nothing is tuned.

How it differs from feiyu-memgate-hybrid, and how it is driven:
- the forgetting ledger and archive are temporary files that are deleted afterwards;
- memories are ingested with their own dates, then the ledger is synchronised with the store
  (Forgetting.sync), so each memory's first_seen is its own date; no sweep is ever run, nothing is forgotten;
- the clock of the forgetting service is pinned to the day after the newest memory (the questions are asked
  "just now"), so the result does not depend on the day the benchmark is run;
- `deployed` is Gateway.recall as usual. `ranked` is the backend's top 10 with the freshness stage's scores
  applied (the same prepare() the gateway runs, without recording a use), no threshold;
- a recall records a use of what it returned, which would make a later question look different from a first
  one; after every question the uses it recorded are removed again and the ledger is synchronised afresh, so
  the 160 questions are independent of each other.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feiyu_memgate_embedding as base  # noqa: E402
import feiyu_memgate_hybrid as hybrid  # noqa: E402

from feiyu_memgate.core import stages as stages_module  # noqa: E402
from feiyu_memgate.core.recall import RecallContext  # noqa: E402
from feiyu_memgate.forgetting.engine import DAY, date_to_epoch  # noqa: E402
from feiyu_memgate.model import Candidate  # noqa: E402

SYSTEM = "feiyu-memgate-full"
MODE = ("verbatim ingest with dates, hybrid backend (sqlite keyword + embedding bge-m3, probabilistic-OR fusion), "
        "default recall stages plus freshness, forgetting ledger with default settings, no sweep, no LLM")
HOW = ("config.build_gateway(dict with [forgetting]); HybridBackend.put_many([(text, id, date)]) once; "
       "Forgetting.sync('private', store); Gateway.recall('owner', {'query': q}) for deployed (stages: min_score, "
       "freshness, relative_score, fill_text, drop_oversize, redact, dedupe, budget); HybridBackend.find(query, 10) "
       "scored by the freshness stage's prepare() for ranked")
KEY = "private"


def stage_names():
    """The default stages with `freshness` right after `min_score`."""
    names = list(stages_module.DEFAULT_STAGES)
    names.insert(names.index("min_score") + 1, "freshness")
    return names


def pinned_clock(memories):
    """A wall clock stuck on the day after the newest memory."""
    now = max(date_to_epoch(m["date"]) for m in memories.values()) + DAY
    return lambda: now


def main(argv=None):
    args = base.parse_args(__doc__, argv)
    memories = base.load("memories.json")
    with tempfile.TemporaryDirectory() as tmp:
        forgetting = {"ledger": os.path.join(tmp, "ledger.db"), "archive": os.path.join(tmp, "archive.db"),
                      "stores": [KEY]}

        def prepare(gw, backend):
            gw.forgetting.sync(KEY, backend)  # first_seen = the memory's own date

        def after_question(gw, reply):
            """Undo the uses the recall recorded: the ledger is back to its post-ingest state."""
            if reply is None:
                gw.forgetting.ledger.remove(KEY, list(memories))
            elif reply.status == 200:
                gw.forgetting.ledger.remove(KEY, [r["id"] for r in reply.body["results"]])
            gw.forgetting.sync(KEY, gw.stores[KEY])

        def rank(gw, backend, query):
            hits = backend.find(query, base.TOP, timeout=base.BACKEND_TIMEOUT)
            candidates = [Candidate(score=h.score, source=KEY, store=backend, ref=h.ref, text=h.text, date=h.date,
                                    meta=dict(h.meta)) for h in hits]
            stage = next(s for s in gw.stages if s.name == "freshness")
            candidates = stage.prepare(candidates, RecallContext(query, base.TOP, pool=gw.pool))
            candidates.sort(key=lambda c: -c.score)
            return [(c.ref, c.score) for c in candidates[:base.TOP]]

        notes = hybrid.notes()[:1] + [
            "Everything is turned on with documented defaults: stages min_score, freshness (floor 0.7), "
            "relative_score, fill_text, drop_oversize, redact, dedupe, budget; [forgetting] with the default "
            "Ebbinghaus curve, a temporary ledger and archive; no sweep was run and nothing was forgotten.",
            "Memories carry their own dates; Forgetting.sync made each ledger first_seen the memory's date. The "
            "forgetting clock was pinned to the day after the newest memory so the run does not depend on today's date.",
            "ranked is the backend's top 10 with freshness applied (prepare() only, no use recorded); no threshold.",
            "Uses recorded by each deployed recall were removed again and the ledger re-synchronised, so every "
            "question was asked of the same state.",
            "deployed uses the default limit of 5; ms times that Gateway.recall call only (two children, then "
            "freshness reads the ledger).",
        ]
        path = base.run_system(hybrid.store_spec(args.embed_url, args.model), args.out, SYSTEM, MODE, HOW, notes,
                               stages=stage_names(), forgetting=forgetting, wall=pinned_clock(memories),
                               prepare=prepare, after_question=after_question, rank=rank)
    print("wrote " + path)


if __name__ == "__main__":
    main()
