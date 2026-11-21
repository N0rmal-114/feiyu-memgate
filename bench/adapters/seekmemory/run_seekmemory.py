"""Benchmark adapter: SeekMemory kernel, no model, no network (runs inside a container).

    python run_seekmemory.py --data /data --out /out/seekmemory.json [--aged-days 365] [--level fact]

How the kernel is driven (all in-process, the kernel is imported as top-level packages):
- write: store.open_store(":memory:"); Source row (the memory text, as the owner's message in one private
  session) via db.sources.append_sources; then maintain.link.persist(artifact, db.artifacts, cache, graph),
  the kernel's own commit + cache + graph-link writer. The artifact is built verbatim (no extraction, no
  model): core.domain.memory.artifact.make("memory", {...}, id=<memory id>), created/updated = memory date.
- deployed: recall.pipeline.query_block(...) with the kernel's default config (app.config.defaults.DEFAULTS),
  a persistent HybridRetriever (as the host adapters build it) and the ids in info["final_ids"].
- ranked: the same pool and candidate weighting as query_block, then recall.score.select with k=10,
  gate=False (no evidence threshold), explore=0 and no character budget / disclosure assembly.
- aged mode: maintain.decay.decay_all(db.artifacts, cache, today=<date + N days>), the kernel's own decay
  sweep, which takes the date as a parameter; recall reads date.today() only inside recall.score,
  recall.temporal and recall.assemble, so those three module-level `date` names are rebound to a fixed-day
  subclass for the run (no kernel source is modified).
"""
import argparse
import datetime
import json
import os
import sys
import time
from dataclasses import replace
from unittest import mock

KERNEL = os.environ.get("SEEKMEMORY_DIR", "/app/seekmemory")
sys.path.insert(0, KERNEL)

from app.config.defaults import DEFAULTS  # noqa: E402
from core.domain.memory.artifact import TimeSpan, make  # noqa: E402
from core.domain.memory.disclosure import DEFAULT_PRIVACY_POLICY  # noqa: E402
from core.domain.memory.graph import Graph  # noqa: E402
from core.domain.memory.source import Source  # noqa: E402
from maintain.decay import decay_all  # noqa: E402
from maintain.link import persist  # noqa: E402
from recall import pipeline, score as score_mod  # noqa: E402
from recall import assemble as assemble_mod, temporal as temporal_mod  # noqa: E402
from recall.designated import current_heads, deliverable  # noqa: E402
from recall.retriever import HybridRetriever  # noqa: E402
from recall.scope import visible  # noqa: E402
from recall.temporal import filter_valid, in_window, query_window  # noqa: E402
from store import open_store  # noqa: E402
from store.cache import Cache  # noqa: E402

SESSION = "bench:FriendMessage:owner"   # one private chat; also the owner session
SPEAKER = "owner"
WARMUP = "这是一个用来热身的问题"
TOP = 10


def conf(key, default):
    """The kernel's own defaults, read the way the host config adapter exposes them."""
    return DEFAULTS.get(key, default)


def load(data_dir, name):
    with open(os.path.join(data_dir, name), encoding="utf-8") as f:
        return json.load(f)


def freeze_today(day):
    """Rebind `date` in the three recall modules that call date.today(); returns the patchers."""
    class Fixed(datetime.date):
        @classmethod
        def today(cls):
            return cls(day.year, day.month, day.day)

    return [mock.patch.object(m, "date", Fixed) for m in (score_mod, temporal_mod, assemble_mod)]


class Bench:
    def __init__(self, level):
        self.level = level
        self.db = open_store(":memory:")
        self.cache = Cache(self.db.artifacts)
        self.graph = Graph(self.db.artifacts)
        self.retriever = HybridRetriever(self.graph, self.cache)

    def ingest(self, memories):
        for mid, m in sorted(memories.items(), key=lambda kv: (kv[1]["date"], kv[0])):
            sid = "src_" + mid
            self.db.sources.append_sources([Source(sid, sid, SESSION, SPEAKER, "user", m["text"], m["date"])])
            art = make(
                "memory",
                {"text": m["text"], "level": self.level, "shape": "point", "chrono": "once", "about": "other"},
                id=mid, scope="private", said_by=SPEAKER, subject_id=SPEAKER,
                time=TimeSpan(created=m["date"], updated=m["date"]),
            )
            art = replace(art, header=replace(art.header, lineage=replace(art.header.lineage, sources=(sid,))))
            persist(art, self.db.artifacts, self.cache, self.graph)

    def deployed(self, text):
        block, info = pipeline.query_block(
            self.cache, self.graph, self.db.artifacts, self.db.sources, SESSION, text, True, conf,
            SESSION, SPEAKER, hybrid_retriever=self.retriever)
        ids = [i for i in info["final_ids"]]
        return block, ids

    def ranked(self, text):
        """Candidate pool and weighting exactly as in query_block, selection without threshold/budget."""
        lookup = self.db.sources.source_umo
        pool = [a for a in visible(current_heads(self.cache.active(), self.db.artifacts), SESSION, SESSION, lookup)
                if a.header.family != "relationship" and deliverable(a, text, True)]
        pool = filter_valid(pool)
        window = query_window(text)
        if window is not None:
            pool = [a for a in pool if a.header.family != "memory" or in_window(a, window)]
        if not pool:
            return []
        cands = []
        for c in self.retriever.retrieve(text, pool):
            prior = DEFAULT_PRIVACY_POLICY.scope_prior(c.artifact)
            cands.append(type(c)(c.artifact, c.activation * prior, c.lexical * prior))
        picked = score_mod.select(cands, text, 0.0, k=TOP, explore=0.0, gate=False,
                                  lexical_weight=float(conf("bm25_weight", 1.0)))
        return [a.header.id for a in picked]


def items(ids):
    """Rank-derived score (the kernel's select() returns no scores); ids are already memory ids."""
    out, seen = [], set()
    for i in ids:
        if i in seen:
            continue
        seen.add(i)
        out.append({"id": i, "score": round(1.0 / (len(out) + 1), 4)})
    return out


def run(args):
    memories = load(args.data, "memories.json")
    queries = load(args.data, "queries.json")
    real_today = datetime.date.today()
    aged = args.aged_days > 0
    bench = Bench(args.level)

    t0 = time.perf_counter()
    bench.ingest(memories)
    ingest_seconds = time.perf_counter() - t0

    notes = []
    forgotten = 0
    patchers = []
    if aged:
        later = real_today + datetime.timedelta(days=args.aged_days)
        forgotten = decay_all(bench.db.artifacts, bench.cache, later.isoformat())
        patchers = freeze_today(later)
        notes.append("decay_all(today=%s) marked %d of %d memories state=forgotten (reversible revision, row kept); "
                     "no recall happened between ingest and the sweep." % (later.isoformat(), forgotten, len(memories)))
        notes.append("Clock for the aged run: container date %s + %d days = %s; decay_all takes it as a parameter, "
                     "recall.score/temporal/assemble `date` rebound for the run." % (real_today, args.aged_days, later))
    else:
        notes.append("Clock: real container date %s (not injected); freshness term exp(-age_days/14) is ~0 for all "
                     "memories (ages %d to %d days)." % (
                         real_today,
                         (real_today - datetime.date.fromisoformat(max(m["date"] for m in memories.values()))).days,
                         (real_today - datetime.date.fromisoformat(min(m["date"] for m in memories.values()))).days))

    for p in patchers:
        p.start()
    try:
        bench.deployed(WARMUP)
        bench.ranked(WARMUP)
        answers, failures, empty_blocks = {}, 0, 0
        for qid, q in queries.items():
            try:
                ranked = items(bench.ranked(q["query"]))
            except Exception as exc:  # noqa: BLE001 - counted, never fatal
                ranked, failures = [], failures + 1
                notes.append("ranked failed for %s: %s" % (qid, type(exc).__name__))
            t1 = time.perf_counter()
            try:
                block, ids = bench.deployed(q["query"])
                ms = (time.perf_counter() - t1) * 1000.0
                deployed = items(ids)
                empty_blocks += not block
            except Exception as exc:  # noqa: BLE001
                ms = (time.perf_counter() - t1) * 1000.0
                deployed, failures = [], failures + 1
                notes.append("deployed failed for %s: %s" % (qid, type(exc).__name__))
            answers[qid] = {"ranked": ranked, "deployed": deployed, "ms": round(ms, 3)}
    finally:
        for p in patchers:
            p.stop()

    commit = os.environ.get("SEEKMEMORY_COMMIT", "794ec5d")
    mode = ("verbatim ingest as level=%s memory artifacts via the kernel's persist(), no extraction, no embeddings "
            "(SeekMemory has no vector search), no LLM; deployed = recall.pipeline.query_block with default config "
            "(recall_k 8, budget 600 chars, evidence gate, disclosure)" % args.level)
    if aged:
        mode += ("; AGED: after ingest, clock advanced %d days with zero recalls, maintain.decay.decay_all swept "
                 "(%d memories forgotten), then the questions were answered" % (args.aged_days, forgotten))
    notes += [
        "Write path: Source row (memory text, owner, one private session) + maintain.link.persist "
        "(commit + cache.put + link_new graph edges). Extraction is skipped because it needs an LLM; the artifact "
        "text is the memory text verbatim. No tags, no emotion values (defaults), shape=point, about=other, "
        "scope=private, said_by/subject_id=owner. The dataset fields source and kind were not given to the system.",
        "level=%s was chosen before the first run: 'fact' is the level for plain statements; maintain.decay.decay_plan "
        "never forgets level=fact (maintain/expiry_policy.py:49-50)." % args.level,
        "Not applied because they need an extraction model: tags, arousal/valence, 'supersedes' (the only mechanism "
        "that retires an older memory when a newer one contradicts it). Updated questions therefore rely on ranking alone.",
        "ids: the artifact id is the memory id; deployed ids come from info['final_ids'] (items that reached the "
        "assembled block, after the 600-char budget). Scores are rank-derived (1/rank) because select() and "
        "query_block return no scores; they carry no meaning across systems.",
        "ranked: pool and scope-prior weighting replicate query_block; select(k=10, gate=False, explore=0). "
        "It bypasses the intent switch (intent 'none' for greetings/clock/operate words) and disclosure so it "
        "measures retrieval only. deployed keeps both.",
        "deployed uses defaults including explore_rate 0.1 (seeded by the query text, may swap the last pick for a "
        "random lower-ranked one) and the recent-context fallback for profile/topic questions.",
        "A persistent HybridRetriever is used (as in the host adapters); BM25 index builds on the warm-up query.",
        "%d question(s) with an error; %d deployed call(s) returned an empty block." % (failures, empty_blocks),
    ]
    name = args.name
    result = {
        "system": name, "version": "NeverENG/SeekMemory commit " + commit, "mode": mode,
        "how": "store.open_store(':memory:'); store.sources.append_sources([Source]); "
               "maintain.link.persist(make('memory', ...), artifacts, cache, graph); "
               + ("maintain.decay.decay_all(artifacts, cache, today); " if aged else "")
               + "deployed: recall.pipeline.query_block(cache, graph, artifacts, sources, umo, text, private=True, "
                 "conf=DEFAULTS, owner_umo, speaker, hybrid_retriever=HybridRetriever) -> info['final_ids']; "
                 "ranked: HybridRetriever.retrieve + recall.score.select(k=10, gate=False, explore=0)",
        "ingest_seconds": round(ingest_seconds, 3), "llm_tokens": 0, "llm_cost_cny": 0.0,
        "answers": answers, "notes": notes,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("wrote %s ingest=%.2fs forgotten=%d failures=%d" % (args.out, ingest_seconds, forgotten, failures))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/data")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="seekmemory")
    ap.add_argument("--level", default="fact", choices=["fact", "scene", "detail"])
    ap.add_argument("--aged-days", type=int, default=0)
    run(ap.parse_args())
