"""Development-set evaluation and threshold search for a backend + recall stage list.

    python bench/dev_eval.py --backend sqlite --search              # grid search on bench/dev, writes results/dev/sqlite.md
                                                                    # (broad, the default profile) and sqlite-precise.md
    python bench/dev_eval.py --backend sqlite                       # one run with the default stages
    python bench/dev_eval.py --backend sqlite --ratio 0.7 --min-score 0.3
    python bench/dev_eval.py --backend sqlite --sweep               # the whole min_score x ratio table
    python bench/dev_eval.py --backend hybrid --embed-url http://127.0.0.1:11434/v1 --search
    python bench/dev_eval.py --backend openviking --candidates FILE --search    # lists made by the openviking adapter

Every default threshold of the keyword, embedding, hybrid and openviking backends was chosen with this
tool, on a DEVELOPMENT set, never on bench/data (the held-out test set, which must not influence any
setting). `--data DIR` picks the development set (default bench/dev: 160 memories, 110 questions, 30 of
them without an answer; the older examples/eval, 46 memories / 30 questions, also works).

How it works: the candidate lists (the backend's top 15 with scores and texts for every question, the same
fetch size the gateway uses for a limit of 5) are computed ONCE. Every grid point is then run through the
real gateway and the real recall stages on those lists (a replay store answers find/read from the lists),
so a search costs one embedding request per question however large the grid is. `--save-candidates FILE`
keeps the lists, `--candidates FILE` reads them instead of asking a backend (that is how the openviking
backend is searched: its adapter writes the lists from a throw-away server).

The search rule is fixed in advance and is the only one used:

    delivered_F1 = harmonic mean of deployed hit and precision
    balanced     = (n_answerable * delivered_F1 + n_quiet * quiet_rate) / (n_answerable + n_quiet)

deployed hit: share of answerable questions whose expected memory is among the delivered items (stages
applied, limit 5). precision: share of delivered items that are expected, averaged over the questions that
delivered something (bench/score.py's definition; the metric code is imported from there). quiet_rate:
share of the no-answer questions that delivered nothing. The grid is min_score 0.05..0.80 step 0.05 x
relative_score ratio 0.50..0.95 step 0.05 or "stage off"; the rest of the default stage list is unchanged.
The chosen point has the highest balanced; ties go to the higher deployed hit, then to the lower min_score,
then to the lower ratio ("off" counts as the lowest: the rule left this last
tie open and the choice follows the direction of the one before it, the more permissive point).

Second profile (`--search --profile broad`, writes results/dev/<name>.md: since 2026-11-16 broad is the default
profile of the gateway, so its record carries the plain name; the balanced search above writes
results/dev/<name>-precise.md with `--profile precise`; `--search` alone writes both): the same candidate lists and
grid, another objective, also written down before any number was looked at. Among the grid points with
precision >= 50 % and quiet rate >= half of the quiet rate of the backend's declared default ("precise") point,
take the highest deployed hit; ties go to the higher balanced, then the higher min_score, then the higher ratio
("stage off" counts as the lowest ratio). If the chosen point is the precise point the backend has no distinct
broad profile. The precise point is what the backend declares (default_min_score / default_relative_ratio, for
embedding the preset of --model), not a re-run of the first search.

Columns of a single run or sweep, same definitions as bench/score.py:
    Hit@1, Hit@5   the right memory is first / among the first five of the backend's own ranking
    dep.hit        deployed hit      prec  precision      quiet  quiet rate      bal  balanced
    items          items handed over per question, all questions

Backends: sqlite and inmemory need nothing. embedding and hybrid (sqlite + embedding) need an
OpenAI-compatible embeddings endpoint: --embed-url and --model. openviking only through --candidates.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)

import score  # noqa: E402  (bench/score.py: the benchmark's own metric code)
from feiyu_memgate import config, registry  # noqa: E402
from feiyu_memgate.core import stages as stages_module  # noqa: E402
from feiyu_memgate.model import Hit  # noqa: E402

DEV_DIR = os.path.join(HERE, "dev")
OUT_DIR = os.path.join(HERE, "results", "dev")
FAKE_TOKEN = "dev-eval-only-fake-token-000000000000"  # obviously fake: the gateway only needs the length
WARMUP_QUERY = "这是一个用来热身的问题"
DEFAULT_EMBED_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "bge-m3"
BACKENDS = ("sqlite", "inmemory", "embedding", "hybrid", "openviking")
LIMIT = 5
FETCH = LIMIT * 3  # what Gateway.recall asks every store for
TOP = 10
GRID_SCORES = tuple(round(0.05 * i, 2) for i in range(1, 17))                # 0.05 .. 0.80
GRID_RATIOS = tuple(round(0.50 + 0.05 * i, 2) for i in range(10)) + (None,)  # 0.50 .. 0.95, None = stage off
SWEEP_SCORES = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70)
SWEEP_RATIOS = (None, 0.9, 0.8, 0.7, 0.6, 0.5)
EMBED_TIMEOUT = 60.0
# The values that were in the code before the re-derivation on bench/dev (chosen on examples/eval).
# openviking declared no ratio, so the stage's own 0.6 applied.
PREVIOUS = {"sqlite": (0.20, 0.6), "inmemory": (0.20, 0.6), "embedding": (0.40, 0.8),
            "hybrid": (0.55, 0.8), "openviking": (0.5, 0.6)}


def load(name, directory=DEV_DIR):
    with open(os.path.join(directory, name), encoding="utf-8") as f:
        return json.load(f)


def memory_text(entry):
    return entry["text"] if isinstance(entry, dict) else entry


def memory_date(entry):
    return entry.get("date") if isinstance(entry, dict) else None


def parse_options(pairs):
    """["floor=0.4", "model=x"] -> {"floor": 0.4, "model": "x"} (values are JSON when they parse)."""
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


def store_spec(backend, embed_url=DEFAULT_EMBED_URL, model=DEFAULT_MODEL, options=None, hybrid_options=None):
    """The [stores.private] table for a live backend. `options` go to the vector child of a hybrid,
    to the store itself otherwise; `hybrid_options` go to the hybrid."""
    options = dict(options or {})
    embedding = dict({"type": "embedding", "path": ":memory:", "url": embed_url, "model": model,
                      "timeout": EMBED_TIMEOUT}, **options)
    if backend == "sqlite":
        return dict({"type": "sqlite", "path": ":memory:"}, **options)
    if backend == "inmemory":
        return dict({"type": "inmemory"}, **options)
    if backend == "embedding":
        return embedding
    if backend == "hybrid":
        return dict({"type": "hybrid", "children": [{"type": "sqlite", "path": ":memory:"}, embedding]},
                    **(hybrid_options or {}))
    raise SystemExit("backend %r has no live store here (use --candidates)" % backend)


def build(spec, stages=None):
    """The in-process gateway: one store called "private", one owner client."""
    cfg = {
        "stores": {"private": spec},
        "clients": {"owner": {"token_env": "GATEWAY_TOKEN_OWNER", "recall": ["private"], "write": "private"}},
    }
    if stages is not None:
        cfg["recall"] = {"stages": stages}
    return config.build_gateway(cfg, env={"GATEWAY_TOKEN_OWNER": FAKE_TOKEN}, log=lambda line: None)


def ingest(backend, memories):
    """Every memory once, oldest first, under its id as the ref."""
    rows = sorted(memories.items(), key=lambda kv: (memory_date(kv[1]) or "", kv[0]))
    for ref, entry in rows:
        backend.put(memory_text(entry), ref=ref, date=memory_date(entry))


# ------------------------------------------------------------------ candidate lists

def collect(spec, memories, queries):
    """The candidate lists of every question from a live backend: the top FETCH hits with scores and texts.

    Returns {"fetch": FETCH, "ids": {ref: memory id}, "questions": {qid: [{ref, score, text, date}]}}.
    """
    gateway = build(spec)
    try:
        backend = gateway.stores["private"]
        backend.find(WARMUP_QUERY, 1, timeout=EMBED_TIMEOUT)  # loads the model before anything counts
        ingest(backend, memories)
        questions = {}
        for qid, q in queries.items():
            hits = sorted(backend.find(q["query"], FETCH, timeout=EMBED_TIMEOUT), key=lambda h: -h.score)
            questions[qid] = [{"ref": h.ref, "score": float(h.score), "text": h.text, "date": h.date}
                              for h in hits]
        return {"fetch": FETCH, "ids": {ref: ref for ref in memories}, "questions": questions}
    finally:
        gateway.close()


def save_candidates(path, cand):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cand, f, ensure_ascii=False)
        f.write("\n")


def load_candidates(path):
    with open(path, encoding="utf-8") as f:
        cand = json.load(f)
    if not {"ids", "questions"} <= set(cand):
        raise SystemExit("%s is not a candidate file" % path)
    return cand


class ReplayStore:
    """Answers find() and read() from recorded candidate lists, so the real stages run on them offline."""

    writable = False

    def __init__(self, cand, queries):
        self.by_query = {}
        self.texts = {}
        for qid, q in queries.items():
            self.by_query[q["query"]] = [
                Hit(ref=c["ref"], score=c["score"], text=c.get("text") or "", date=c.get("date"))
                for c in cand["questions"][qid]]
            for c in cand["questions"][qid]:
                if c.get("text"):
                    self.texts[c["ref"]] = c["text"]

    def find(self, query, limit, timeout=None, ctl=None):
        return list(self.by_query.get(query, []))[:limit]

    def read(self, ref, timeout=None, ctl=None):
        return self.texts[ref]

    def health(self, timeout=2.0):
        return True

    def close(self):
        pass


def replay_gateway(cand, queries):
    gateway = build({"type": "inmemory"})
    gateway.stores["private"] = ReplayStore(cand, queries)
    return gateway


# ------------------------------------------------------------------ stages

def stage_list(stages_arg, min_score=None, ratio=None, gap=None):
    """The stage specs to run: DEFAULT_STAGES, or the --stages list (JSON, or comma-separated names);
    min_score / relative_score entries are given the values asked for."""
    if stages_arg is None:
        names = list(stages_module.DEFAULT_STAGES)
    elif stages_arg.lstrip().startswith("["):
        names = json.loads(stages_arg)
    else:
        names = [n.strip() for n in stages_arg.split(",") if n.strip()]
    out = []
    for item in names:
        spec = {"type": item} if isinstance(item, str) else dict(item)
        if spec["type"] == "min_score" and min_score is not None:
            spec["value"] = min_score
        if spec["type"] == "relative_score":
            if ratio is not None:
                spec["ratio"] = ratio
            if gap is not None:
                spec["gap"] = gap
        out.append(spec)
    if ratio is not None and not any(s["type"] == "relative_score" for s in out):
        position = next((i + 1 for i, s in enumerate(out) if s["type"] == "min_score"), 0)
        spec = {"type": "relative_score", "ratio": ratio}
        if gap is not None:
            spec["gap"] = gap
        out.insert(position, spec)
    return out


def without_relative(specs):
    return [s for s in specs if s["type"] != "relative_score"]


def set_stages(gateway, specs):
    """Swap the gateway's recall stages for the ones `specs` describe."""
    gateway.stages = [registry.create("recall_stage", dict(s)) for s in specs]


def describe(specs):
    """A short label of a stage list: the settings that matter."""
    parts = []
    has_relative = False
    for s in specs:
        if s["type"] == "min_score":
            parts.append("min_score=%s" % s.get("value", "default"))
        elif s["type"] == "relative_score":
            has_relative = True
            parts.append("relative=%s%s" % (s.get("ratio", "default"), ",gap=%s" % s["gap"] if "gap" in s else ""))
    if not has_relative:
        parts.append("relative=off")
    return " ".join(parts)


# ------------------------------------------------------------------ metrics

def run_questions(gateway, cand, queries):
    """answers in the shape bench/score.py reads: ranked (the backend's list, no threshold) and deployed."""
    ids = cand["ids"]
    answers = {}
    for qid, q in queries.items():
        ranked, seen = [], set()
        for c in cand["questions"][qid]:
            mid = ids.get(c["ref"])
            if mid is None or mid in seen:
                continue
            seen.add(mid)
            ranked.append({"id": mid, "score": c["score"]})
        reply = gateway.recall("owner", {"query": q["query"]})
        if reply.status != 200:
            raise SystemExit("recall failed for %s with status %s" % (qid, reply.status))
        deployed = [{"id": ids.get(r["id"]), "score": r["score"]} for r in reply.body["results"]]
        answers[qid] = {"ranked": ranked[:TOP], "deployed": deployed, "ms": 0.0}
    return answers


def metrics(answers, queries):
    """Hit@1, Hit@5, deployed hit, precision, delivered F1, quiet rate, balanced, items."""
    typed = {qid: {"type": "lexical" if q["expect"] else "quiet", "expect": q["expect"]}
             for qid, q in queries.items()}
    wanted = sorted(q for q in typed if typed[q]["type"] != "quiet")
    silent = sorted(q for q in typed if typed[q]["type"] == "quiet")
    got = score._group_metrics(wanted, typed, answers, "answerable")
    quiet = score._group_metrics(silent, typed, answers, "quiet")
    f1 = got.get("delivered_f1") or 0.0  # nothing ever delivered: precision undefined, F1 counts as 0
    quiet_rate = quiet.get("quiet_rate")
    n_ans, n_quiet = len(wanted), len(silent)
    balanced = (n_ans * f1 + n_quiet * (quiet_rate or 0.0)) / (n_ans + n_quiet) if n_ans + n_quiet else None
    return {
        "hit1": got.get("hit1"), "hit5": got.get("hit5"), "deployed_hit": got.get("deployed_hit"),
        "precision": got.get("precision"), "f1": f1, "quiet": quiet_rate, "balanced": balanced,
        "items": sum(len(a["deployed"]) for a in answers.values()) / len(answers),
    }


def _pct(value):
    return "  -  " if value is None else "%5.1f" % (value * 100)


HEADER = "%-34s %6s %6s %7s %6s %6s %6s %6s %6s" % ("stages", "Hit@1", "Hit@5", "dep.hit", "prec", "F1",
                                                    "quiet", "bal", "items")


def format_row(label, m):
    return "%-34s %6s %6s %7s %6s %6s %6s %6s %6.2f" % (
        label, _pct(m["hit1"]), _pct(m["hit5"]), _pct(m["deployed_hit"]), _pct(m["precision"]), _pct(m["f1"]),
        _pct(m["quiet"]), _pct(m["balanced"]), m["items"])


def evaluate(cand, specs_list, queries):
    """[(label, metrics)] for each stage list in `specs_list`, over one set of candidate lists."""
    gateway = replay_gateway(cand, queries)
    try:
        rows = []
        for specs in specs_list:
            set_stages(gateway, specs)
            rows.append((describe(specs), metrics(run_questions(gateway, cand, queries), queries)))
        return rows
    finally:
        gateway.close()


# ------------------------------------------------------------------ the fixed search

def grid():
    return [(m, r) for m in GRID_SCORES for r in GRID_RATIOS]


def grid_specs(m, r):
    return stage_list(None, m, r) if r is not None else without_relative(stage_list(None, m))


def selection_key(point, m):
    """Higher is better: balanced, then deployed hit, then the LOWER min_score, then the lower ratio."""
    min_score, ratio = point
    return (round(m["balanced"], 12), round(m["deployed_hit"], 12), -min_score, 1.0 if ratio is None else -ratio)


def search(cand, queries):
    """[(point, metrics)] over the whole grid, best first."""
    points = grid()
    rows = evaluate(cand, [grid_specs(m, r) for m, r in points], queries)
    results = [(point, m) for point, (_, m) in zip(points, rows)]
    results.sort(key=lambda pm: selection_key(*pm), reverse=True)
    return results


BROAD_MIN_PRECISION = 0.5
BROAD_QUIET_SHARE = 0.5


def declared_point(backend, model=DEFAULT_MODEL):
    """(min_score, ratio) the backend declares as its precise default."""
    if backend == "embedding":
        from feiyu_memgate.backends import embedding
        preset, _ = embedding.preset_for(model)
        return preset["min_score"], preset["ratio"]
    module, cls = {"sqlite": ("sqlite", "SQLiteBackend"), "inmemory": ("inmemory", "InMemoryBackend"),
                   "hybrid": ("hybrid", "HybridBackend"), "openviking": ("openviking", "OpenVikingBackend")}[backend]
    mod = __import__("feiyu_memgate.backends." + module, fromlist=[cls])
    klass = getattr(mod, cls)
    return klass.default_min_score, klass.default_relative_ratio


def broad_key(point, m):
    """Higher is better: deployed hit, then balanced, then the higher min_score, then the higher ratio."""
    min_score, ratio = point
    return (round(m["deployed_hit"], 12), round(m["balanced"], 12), min_score, -1.0 if ratio is None else ratio)


def broad_choice(results, precise_point):
    """(point, metrics, precise metrics, admissible count) by the broad rule over `results` ([(point, m)])."""
    by_point = dict(results)
    precise_m = by_point[tuple(precise_point)]
    quiet_floor = BROAD_QUIET_SHARE * precise_m["quiet"]
    admissible = [(p, m) for p, m in results
                  if m["precision"] is not None and m["precision"] >= BROAD_MIN_PRECISION and m["quiet"] >= quiet_floor]
    best = max(admissible, key=lambda pm: broad_key(*pm))
    return best[0], best[1], precise_m, len(admissible)


def point_label(point):
    return "min_score=%.2f relative=%s" % (point[0], "off" if point[1] is None else "%.2f" % point[1])


def report(backend, data_dir, queries, memories, results, previous, previous_m):
    """The text of results/dev/<backend>.md (also what is printed)."""
    n_quiet = sum(1 for q in queries.values() if not q["expect"])
    shown = os.path.relpath(data_dir, ROOT).replace(os.sep, "/") if os.path.abspath(data_dir).startswith(ROOT) \
        else os.path.basename(os.path.normpath(data_dir))
    lines = [
        "# 开发集阈值搜索（精确档 precise）：%s" % backend, "",
        "数据：`%s`，%d 条记忆，%d 个问题（%d 个可回答，%d 个没有答案）。" % (
            shown, len(memories), len(queries), len(queries) - n_quiet, n_quiet),
        "网格：min_score 0.05 到 0.80（步长 0.05）x relative_score 比例 0.50 到 0.95（步长 0.05）或不用该步骤，"
        "共 %d 个点；其余召回步骤是默认的，limit 5。" % len(results),
        "目标（事先定好）：`balanced = (n可答 x 交付F1 + n安静 x 安静率) / (n可答 + n安静)`；"
        "交付F1 是交付命中和精确率的调和平均。平手：交付命中高的，再是 min_score 低的，再是比例低的（不用该步骤算最低）。", "",
        "前 5 名：", "", "```",
        "%-34s %6s %6s %7s %6s %6s %6s %6s %6s" % ("point", "Hit@1", "Hit@5", "dep.hit", "prec", "F1", "quiet",
                                                   "bal", "items"),
    ]
    for point, m in results[:5]:
        lines.append(format_row(point_label(point), m))
    lines += ["```", ""]
    best_point, best = results[0]
    lines += ["选定：**%s**，交付命中 %.1f%%，精确率 %s%%，安静率 %.1f%%，balanced %.1f%%。" % (
        point_label(best_point), best["deployed_hit"] * 100, _pct(best["precision"]).strip(),
        best["quiet"] * 100, best["balanced"] * 100), ""]
    lines += ["对照（这次搜索之前代码里的值，同一份候选）：", "", "```", HEADER,
              format_row("before: " + point_label(previous), previous_m),
              format_row("chosen: " + point_label(best_point), best), "```", ""]
    return "\n".join(lines)


def report_broad(name, data_dir, queries, memories, results, precise_point, broad):
    """The text of results/dev/<name>.md (the broad profile, which is the gateway's default)."""
    point, m, precise_m, count = broad
    n_quiet = sum(1 for q in queries.values() if not q["expect"])
    shown = os.path.relpath(data_dir, ROOT).replace(os.sep, "/") if os.path.abspath(data_dir).startswith(ROOT)         else os.path.basename(os.path.normpath(data_dir))
    distinct = tuple(point) != tuple(precise_point)
    lines = [
        "# 开发集：宽松档（broad，默认档）的选择：%s" % name, "",
        "数据：`%s`，%d 条记忆，%d 个问题（%d 个可回答，%d 个没有答案）。同一份候选列表、同一张网格（%d 个点）、同一套真实的召回步骤"
        "（见 `%s-precise.md` 的搜索）。" % (shown, len(memories), len(queries), len(queries) - n_quiet, n_quiet,
                                           len(results), name),
        "规则（事先写定）：在精确率 >= 50%% 且安静率 >= 精确档安静率的一半（%.1f%%）的格子里取交付命中最高的；"
        "平手取 balanced 高的，再取 min_score 高的，再取比例高的（不用 relative_score 算最低）。"
        "精确档是后端声明的默认点，不是重新搜索的结果。满足条件的格子 %d 个。" % (precise_m["quiet"] * 50, count), "",
        "```", HEADER,
        format_row("precise: " + point_label(precise_point), precise_m),
        format_row("broad:   " + point_label(point), m), "```", ""]
    if distinct:
        lines += ["结论：**%s** 是宽松档；交付命中 %+.1f 点，精确率 %+.1f 点，安静率 %+.1f 点，每题条数 %+.2f。" % (
            point_label(point), (m["deployed_hit"] - precise_m["deployed_hit"]) * 100,
            ((m["precision"] or 0) - (precise_m["precision"] or 0)) * 100,
            (m["quiet"] - precise_m["quiet"]) * 100, m["items"] - precise_m["items"]), ""]
    else:
        lines += ["结论：选中的点就是精确档的点，**没有独立的宽松档**：后端不声明宽松值，默认档（broad）对它回落到精确档的线（`default_min_score` / `default_relative_ratio`）。", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ command line

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--backend", choices=BACKENDS, required=True)
    parser.add_argument("--data", default=DEV_DIR, help="development set directory (default: bench/dev)")
    parser.add_argument("--search", action="store_true",
                        help="the fixed grid search; writes results/dev/<name>.md (broad, the default profile) and "
                             "<name>-precise.md")
    parser.add_argument("--out-dir", default=OUT_DIR, help="where --search writes its reports")
    parser.add_argument("--candidates", metavar="FILE", help="read the candidate lists from FILE instead of a backend")
    parser.add_argument("--save-candidates", metavar="FILE", help="write the candidate lists to FILE")
    parser.add_argument("--stages", help="stage list: comma-separated names or a JSON list "
                                         "(default: the gateway's DEFAULT_STAGES)")
    parser.add_argument("--min-score", type=float, help="value of the min_score stage")
    parser.add_argument("--ratio", type=float, help="ratio of the relative_score stage (added after min_score if absent)")
    parser.add_argument("--gap", type=float, help="gap of the relative_score stage")
    parser.add_argument("--sweep", action="store_true", help="table of min_score x ratio instead of one run")
    parser.add_argument("--embed-url", default=DEFAULT_EMBED_URL, help="OpenAI-compatible embeddings base URL")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="embedding model name")
    parser.add_argument("--option", action="append", metavar="KEY=VALUE",
                        help="backend option (JSON value), for a hybrid: of its embedding child; repeatable")
    parser.add_argument("--profile", choices=("precise", "broad", "both"), default="both",
                        help="with --search: the objective (precise: balanced, writes <name>-precise.md; broad: see "
                             "the docstring, writes <name>.md; both: the default)")
    parser.add_argument("--name", help="base name of the file --search writes (default: the backend)")
    parser.add_argument("--hybrid-option", action="append", metavar="KEY=VALUE",
                        help="option of the hybrid backend itself, e.g. weights=[1,0.5]; repeatable")
    args = parser.parse_args(argv)

    if os.path.normcase(os.path.abspath(args.data)) == os.path.normcase(os.path.join(ROOT, "bench", "data")):
        raise SystemExit("bench/data is the held-out test set: it must not be used to choose anything")
    memories, queries = load("memories.json", args.data), load("queries.json", args.data)
    if args.candidates:
        cand = load_candidates(args.candidates)
    else:
        spec = store_spec(args.backend, args.embed_url, args.model, parse_options(args.option),
                          parse_options(args.hybrid_option))
        cand = collect(spec, memories, queries)
    if args.save_candidates:
        save_candidates(args.save_candidates, cand)
    print("dev set: %d memories, %d questions (%d without an answer); backend %s"
          % (len(memories), len(queries), sum(1 for q in queries.values() if not q["expect"]), args.backend))

    if args.search:
        results = search(cand, queries)
        name = args.name or args.backend
        os.makedirs(args.out_dir, exist_ok=True)
        if args.profile in ("broad", "both"):
            precise_point = declared_point(args.backend, args.model)
            text = report_broad(name, args.data, queries, memories, results, precise_point,
                                broad_choice(results, precise_point))
            print(text)
            with open(os.path.join(args.out_dir, name + ".md"), "w", encoding="utf-8", newline="\n") as f:
                f.write(text + "\n")
        if args.profile in ("precise", "both"):
            prev = PREVIOUS[args.backend]
            previous_m = evaluate(cand, [grid_specs(*prev)], queries)[0][1]
            text = report(args.backend, args.data, queries, memories, results, prev, previous_m)
            print(text)
            with open(os.path.join(args.out_dir, name + "-precise.md"), "w", encoding="utf-8", newline="\n") as f:
                f.write(text + "\n")
        return 0
    if args.sweep:
        runs = [(m, r) for m in SWEEP_SCORES for r in SWEEP_RATIOS]
        specs_list = [stage_list(args.stages, m, r) if r is not None else without_relative(stage_list(args.stages, m))
                      for m, r in runs]
    else:
        specs_list = [stage_list(args.stages, args.min_score, args.ratio, args.gap)]
    print(HEADER)
    for label, m in evaluate(cand, specs_list, queries):
        print(format_row(label, m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
