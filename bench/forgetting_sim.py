"""What forgetting buys and what it costs: a deterministic simulation on the frozen benchmark data.

    python bench/forgetting_sim.py                              # keyword store (sqlite), no network
    python bench/forgetting_sim.py --backend hybrid --embed-url http://HOST:11434/v1

Reads bench/data/memories.json and queries.json (never edited). Writes, under --out (default
bench/results/forgetting): <backend>/timeline.csv and <backend>/summary.json, and, when a backend has been
run, one combined summary.md (Chinese) of every backend found. The reading at the end of summary.md comes
from reading.md in the same folder.

Method
------
One in-process gateway per arm with a fake wall clock (config.build_gateway(..., wall=clock)); the store is
`sqlite` (keyword search only) or `hybrid` (sqlite + an embedding child, as in examples/hybrid/gateway.toml).
Recall stages: the gateway's default list without `linked`, the stage that was added after these runs were
recorded (so: min_score, relative_score, newer_first, fill_text,
drop_oversize, redact, dedupe, budget) with freshness inserted right after min_score, so the relative cut-off
sees freshness-adjusted scores; no hand-set thresholds: each backend's own lines of the "precise" profile apply (pinned, see PROFILE; the baseline has no [forgetting] section and
therefore no freshness stage). The 240 memories are loaded with their own dates; 540 days are simulated
starting the day after the newest memory. Every day 0 to 6 questions are asked, drawn from the answerable
questions (lexical, paraphrase, updated): a fixed "hot" third gets about 80% of the traffic, a "warm" third
the other 20%, a "cold" third is never asked. After the day's questions the clock moves 24 hours on and one
sweep runs through the gateway (the background sweep, which never lifts the rate limit). Every arm sees
exactly the same schedule of questions.

    baseline         no forgetting
    default          forgetting with its default settings (half-life 90 days, reinforce 1.6, lines
                     0.3 / 0.05, freshness floor 0.7, credit "top", rate limit max(5, 2% of the store)
                     per sweep)
    default_pinned   the default settings, with the memories whose kind is "identity" (39 of 240) pinned: the
                     documented practice, identity facts are pinned and never forgotten
    aggressive       the default settings with every half-life divided by 3 (30 days)

The four arms were decided before the first run of this version and are not changed after seeing results
(the "brake off" arms of the previous version are gone: the brake is now a rate limit).

Measurements ("probes") are taken on day 0, day 1 and every 30 days, with a recall that does NOT count as
a use, so measuring never changes what is measured: every answerable question is asked through the
gateway's own stages and pool with the default limit of 5.

The scenario below (seed, rates, split, days, arms, the per-backend recall settings) was fixed before any
result of this version was looked at; it is not adjusted afterwards.
"""
import argparse
import copy
import csv
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from feiyu_memgate import config  # noqa: E402
from feiyu_memgate.core.recall import RecallContext, recall  # noqa: E402
from feiyu_memgate.core.stages import DEFAULT_STAGES  # noqa: E402
from feiyu_memgate.forgetting.engine import date_to_epoch  # noqa: E402

# The stage list the recorded results were measured with: today's defaults without `linked`, which was added
# later and would give the hybrid arm extra memories.
STAGES = [name for name in DEFAULT_STAGES if name != "linked"]

DATA_DIR = os.path.join(ROOT, "bench", "data")
OUT_DIR = os.path.join(HERE, "results", "forgetting")
# The score-line profile of every arm. The committed results were measured when "precise" was what the gateway used
# without a setting; the default has been "broad" since 2026-11-16, so the profile is pinned here and the numbers
# stay reproducible. The simulation has not been repeated with the broad profile.
PROFILE = "precise"
DAY = 86400.0

# ---- the scenario (fixed before looking at results)
SEED = 20261114
DAYS = 540
CHECK_EVERY = 30
MAX_PER_DAY = 6          # 0..6 questions a day, uniform
HOT_SHARE = 0.8          # share of the traffic that goes to the hot third
LIMIT = 5
ANSWERABLE = ("lexical", "paraphrase", "updated")
GROUPS = ("hot", "warm", "cold")
FAKE_TOKEN = "sim-only-fake-token-000000000000000000"
STORE = "private"

# No hand-set thresholds: min_score, relative_score and the embedding floor/ceil are the backends' own defaults.
BACKENDS = ("sqlite", "hybrid")
EMBED_MODEL = "bge-m3"

# An arm is None (no forgetting) or {"curve": {...curve options...}, "section": {...[forgetting] options...},
# "pin_kind": a memory kind whose memories are pinned at the start}.
ARMS = {
    "baseline": None,
    "default": {},
    "default_pinned": {"pin_kind": "identity"},
    "aggressive": {"curve": {"half_life_days": 30}},                      # the default is 90: divided by 3
}
ARM_TITLES = {
    "baseline": "无遗忘（基线）",
    "default": "默认设置",
    "default_pinned": "默认设置 + 钉住身份类（identity）",
    "aggressive": "激进设置（半衰期除以 3）",
}
BACKEND_TITLES = {"sqlite": "关键词后端（sqlite）", "hybrid": "混合后端（sqlite + 向量 bge-m3）"}
TABLE_DAYS = (0, 30, 90, 180, 360, 540)


# ------------------------------------------------------------------ the scenario

def load(name, data_dir=DATA_DIR):
    with open(os.path.join(data_dir, name), encoding="utf-8") as f:
        return json.load(f)


def make_scenario(queries, *, seed=SEED, days=DAYS, max_per_day=MAX_PER_DAY, hot_share=HOT_SHARE):
    """Split the answerable questions into hot, warm and cold thirds and draw the daily schedule.

    Returns {"groups": {name: [qid]}, "schedule": [[qid, ...] per day]}. The same draws serve every arm."""
    rng = random.Random(seed)
    answerable = sorted(qid for qid, q in queries.items() if q["type"] in ANSWERABLE)
    rng.shuffle(answerable)
    third = len(answerable) // 3
    groups = {"hot": sorted(answerable[:third]), "warm": sorted(answerable[third:2 * third]),
              "cold": sorted(answerable[2 * third:])}
    schedule = []
    for _ in range(days):
        asked = []
        for _ in range(rng.randint(0, max_per_day)):
            pool = groups["hot"] if rng.random() < hot_share else groups["warm"]
            asked.append(rng.choice(pool))
        schedule.append(asked)
    return {"groups": groups, "schedule": schedule}


# ------------------------------------------------------------------ the store of a backend

def store_spec(backend, embed_url=None):
    """The [stores.private] table of a backend."""
    if backend == "sqlite":
        return {"type": "sqlite", "path": ":memory:"}
    if backend == "hybrid":
        if not embed_url:
            raise ValueError("the hybrid backend needs an embeddings url (--embed-url)")
        return {"type": "hybrid", "children": [
            {"type": "sqlite", "path": ":memory:"},
            {"type": "embedding", "path": ":memory:", "url": embed_url, "model": EMBED_MODEL,
             "timeout": 60.0},
        ]}
    raise ValueError("unknown backend %r" % backend)


# ------------------------------------------------------------------ one arm

class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _silent(line):
    pass


def _quiet(stage):
    """A copy of a stage whose finish() does nothing: a probe must not count as a use."""
    probe = copy.copy(stage)
    if hasattr(probe, "finish"):
        probe.finish = lambda ctx: None
    return probe


class Arm:
    """One gateway with its fake clock; step(day) plays one simulated day, snapshot() measures."""

    def __init__(self, name, memories, queries, scenario, spec, start_epoch, backend="sqlite", embed_url=None):
        self.name = name
        self.memories, self.queries, self.scenario = memories, queries, scenario
        self.start = start_epoch
        self.clock = Clock(start_epoch)
        self.purged = 0
        self.forgotten = 0
        self.held_now = 0        # memories due but left for later by the rate limit at the latest sweep
        self.held_sweeps = 0     # sweeps that held something back
        self.pinned = 0
        stages = list(STAGES)
        if spec is not None:
            stages.insert(stages.index("min_score") + 1, "freshness")
        cfg = {
            "stores": {STORE: store_spec(backend, embed_url)},
            "clients": {"owner": {"token_env": "SIM_TOKEN", "recall": [STORE], "write": STORE}},
            "recall": {"stages": stages, "profile": PROFILE},
        }
        if spec is not None:
            curve = dict({"type": "ebbinghaus"}, **spec.get("curve", {}))
            section = {"ledger": ":memory:", "archive": ":memory:", "stores": [STORE],
                       "sweep_every_hours": 0, "curve": curve}
            section.update(spec.get("section", {}))
            cfg["forgetting"] = section
        self.gateway = config.build_gateway(cfg, env={"SIM_TOKEN": FAKE_TOKEN}, log=_silent, wall=self.clock)
        self.store = self.gateway.stores[STORE]
        for mid, m in sorted(memories.items(), key=lambda kv: (kv[1]["date"], kv[0])):
            self.store.put(m["text"], ref=mid, date=m["date"])
        kind = (spec or {}).get("pin_kind")
        if kind:
            refs = sorted(mid for mid, m in memories.items() if m.get("kind") == kind)
            self.gateway.forgetting.pin(STORE, refs)
            self.pinned = len(refs)
        self.probe_stages = [_quiet(s) for s in self.gateway.stages]

    def close(self):
        self.gateway.close()

    # -- a day
    def step(self, day):
        """Day 1 is the first simulated day: questions at noon, then the sweep at the end of the day."""
        self.clock.t = self.start + (day - 1) * DAY + DAY / 2
        for qid in self.scenario["schedule"][day - 1]:
            self.gateway.recall("owner", {"query": self.queries[qid]["query"], "limit": LIMIT})
        self.clock.t = self.start + day * DAY
        report = self.gateway.sweep()  # the background sweep: never lifts the rate limit
        if report is not None:
            self.purged += report["purged"]
            rows = [r for r in report.values() if isinstance(r, dict)]
            self.forgotten += sum(r["forgotten"] for r in rows)
            self.held_now = sum(r["held"] for r in rows)
            self.held_sweeps += 1 if self.held_now else 0

    # -- measuring
    def probe(self, qid):
        """The refs a question gets back right now, best first, without counting a use."""
        ctx = RecallContext(self.queries[qid]["query"], LIMIT, pool=self.gateway.pool,
                            redact=self.gateway.redactors["private"], client="owner", notes=[])
        recall({STORE: self.store}, self.probe_stages, ctx)
        return [c.ref for c in ctx.accepted]

    def _states(self):
        """How many memories are demoted (rank low) and due (deletable now) at the current time."""
        f = self.gateway.forgetting
        if f is None:
            return 0, 0
        entries = {e.ref: e for e in f.ledger.entries(STORE)}
        demoted = due = 0
        for ref, date in self.store.refs():
            state = f.status(STORE, ref, entries.get(ref), date, self.clock.t)
            demoted += state == "demoted"
            due += state == "due"
        return demoted, due

    def snapshot(self, day):
        groups = self.scenario["groups"]
        answers = {qid: self.probe(qid) for g in GROUPS for qid in groups[g]}
        row = {"arm": self.name, "day": day, "date": _date(self.start + max(day - 1, 0) * DAY)}
        in_store = {ref for ref, _ in self.store.refs()}
        row["in_store"] = len(in_store)
        row["demoted"], row["due"] = self._states()
        row["held"] = self.held_now
        row["pinned"] = self.pinned
        row["held_sweeps"] = self.held_sweeps
        archive = self.gateway.forgetting.archive if self.gateway.forgetting else None
        row["archived"] = archive.count() if archive else 0
        row["purged"] = self.purged
        for g in GROUPS:
            qids = groups[g]
            hits = sum(1 for qid in qids if set(self.queries[qid]["expect"]) & set(answers[qid]))
            row["hit5_" + g] = _ratio(hits, len(qids))
            wanted = {m for qid in qids for m in self.queries[qid]["expect"]}
            gone = {m for m in wanted if m not in in_store}
            row["deleted_" + g] = len(gone)
            row["unrecoverable_" + g] = sum(1 for m in gone if archive is None or archive.get(STORE, m) is None)
        returned = noise = 0
        for qid, refs in answers.items():
            expect = set(self.queries[qid]["expect"])
            returned += len(refs)
            noise += sum(1 for r in refs if r not in expect)
        row["avg_items"] = _ratio(returned, len(answers))
        row["noise_rate"] = _ratio(noise, returned)
        row["updated_new_first"], row["updated_detail"] = self._updated(answers)
        return row

    def _updated(self, answers):
        """Among the "updated" questions: how often the newer memory is returned above the stale one
        (a stale memory that is not returned counts as below)."""
        both = new_only = stale_only = neither = 0
        wins = 0
        qids = [qid for qid, q in self.queries.items() if q["type"] == "updated" and qid in answers]
        for qid in qids:
            refs = answers[qid]
            new, stale = self.queries[qid]["expect"][0], self.queries[qid].get("stale", [])
            has_new = new in refs
            old_positions = [refs.index(s) for s in stale if s in refs]
            if has_new and old_positions:
                both += 1
                wins += refs.index(new) < min(old_positions)
            elif has_new:
                new_only += 1
                wins += 1
            elif old_positions:
                stale_only += 1
            else:
                neither += 1
        detail = {"questions": len(qids), "both_returned": both, "new_only": new_only,
                  "stale_only": stale_only, "neither": neither}
        return _ratio(wins, len(qids)), detail


def _ratio(a, b):
    return round(a / b, 4) if b else None


def _date(epoch):
    return time.strftime("%Y-%m-%d", time.gmtime(epoch))


# ------------------------------------------------------------------ a whole run

def start_epoch(memories):
    """Midnight UTC of the day after the newest memory."""
    return date_to_epoch(max(m["date"] for m in memories.values())) + DAY


def checkpoints(days, every=CHECK_EVERY):
    days_list = [0, 1] + list(range(every, days + 1, every))
    if days not in days_list:
        days_list.append(days)
    return sorted(set(d for d in days_list if d <= days))


def run_arm(name, spec, memories, queries, scenario, days=DAYS, every=CHECK_EVERY, backend="sqlite",
            embed_url=None):
    arm = Arm(name, memories, queries, scenario, spec, start_epoch(memories), backend, embed_url)
    rows = []
    try:
        wanted = set(checkpoints(days, every))
        if 0 in wanted:
            rows.append(arm.snapshot(0))
        for day in range(1, days + 1):
            arm.step(day)
            if day in wanted:
                rows.append(arm.snapshot(day))
    finally:
        arm.close()
    return rows


def restore_check(spec, memories, queries, scenario, max_days=DAYS, backend="sqlite", embed_url=None):
    """On a fresh arm: play day by day until a cold question, whose memory the baseline finds at the start,
    has that memory in the archive; restore it and ask again."""
    start = start_epoch(memories)
    base = Arm("baseline", memories, queries, scenario, None, start, backend, embed_url)
    arm = Arm("restore", memories, queries, scenario, spec, start, backend, embed_url)
    try:
        found_at_start = sorted(qid for qid in scenario["groups"]["cold"]
                                if set(queries[qid]["expect"]) & set(base.probe(qid)))
        archive = arm.gateway.forgetting.archive
        for day in range(1, max_days + 1):
            arm.step(day)
            for qid in found_at_start:
                ref = queries[qid]["expect"][0]
                if archive.get(STORE, ref) is None:
                    continue
                before = ref in arm.probe(qid)
                restored = arm.gateway.forgetting.restore(STORE, ref, arm.store)
                after = ref in arm.probe(qid)
                return {"question": qid, "memory": ref, "after_days": day, "found_before_restore": before,
                        "restored": restored, "found_after_restore": after,
                        "text_identical": arm.store.read(ref) == memories[ref]["text"]}
        return {"question": None, "note": "没有任何一个冷门题的记忆在 %d 天内被存档" % max_days}
    finally:
        base.close()
        arm.close()


def simulate(memories, queries, *, seed=SEED, days=DAYS, every=CHECK_EVERY, arms=None, backend="sqlite",
             embed_url=None, progress=None):
    arms = ARMS if arms is None else arms
    scenario = make_scenario(queries, seed=seed, days=days)
    timeline, restores = [], {}
    for name, spec in arms.items():
        started = time.time()
        timeline += run_arm(name, spec, memories, queries, scenario, days, every, backend, embed_url)
        if spec is not None:
            restores[name] = restore_check(spec, memories, queries, scenario, days, backend, embed_url)
        if progress:
            progress("%s / %s: %.0f s" % (backend, name, time.time() - started))
    return {"scenario": scenario, "timeline": timeline, "restore": restores}


# ------------------------------------------------------------------ output

CSV_COLUMNS = ["arm", "day", "date", "in_store", "pinned", "demoted", "due", "held", "held_sweeps", "archived", "purged",
               "hit5_hot", "hit5_warm", "hit5_cold", "avg_items", "noise_rate", "updated_new_first",
               "deleted_hot", "deleted_warm", "deleted_cold",
               "unrecoverable_hot", "unrecoverable_warm", "unrecoverable_cold"]


def write_csv(path, timeline):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(timeline)


def write_run(out_dir, backend, result, memories, seconds, days=DAYS):
    """<out_dir>/<backend>/timeline.csv and summary.json for one backend's run."""
    folder = os.path.join(out_dir, backend)
    os.makedirs(folder, exist_ok=True)
    write_csv(os.path.join(folder, "timeline.csv"), result["timeline"])
    scenario = result["scenario"]
    groups = scenario["groups"]
    schedule = scenario["schedule"]
    summary = {
        "backend": backend, "seed": SEED, "days": days, "check_every_days": CHECK_EVERY, "limit": LIMIT,
        "stages": list(STAGES), "max_questions_per_day": MAX_PER_DAY, "hot_share": HOT_SHARE,
        "start_date": _date(start_epoch(memories)), "memories": len(memories),
        "arms": {name: ARMS.get(name) for name in dict.fromkeys(r["arm"] for r in result["timeline"])},
        "embedding": EMBED_MODEL if backend == "hybrid" else None,
        "groups": {g: groups[g] for g in GROUPS},
        "questions_asked": sum(len(d) for d in schedule),
        "asked_per_group": {g: sum(1 for d in schedule for q in d if q in set(groups[g])) for g in GROUPS},
        "seconds": round(seconds, 1),
        "timeline": result["timeline"], "restore": result["restore"],
    }
    with open(os.path.join(folder, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return summary


def _pct(value):
    return "-" if value is None else "%.1f%%" % (value * 100)


def _by_arm(timeline):
    out = {}
    for row in timeline:
        out.setdefault(row["arm"], []).append(row)
    return out


def _cells(r):
    return [_pct(r["hit5_hot"]), _pct(r["hit5_warm"]), _pct(r["hit5_cold"]), "%.2f" % r["avg_items"],
            _pct(r["noise_rate"]), _pct(r["updated_new_first"]),
            "%d / %d / %d" % (r["deleted_hot"], r["deleted_warm"], r["deleted_cold"])]


def _table(head, rows):
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    lines += ["| " + " | ".join(cells) + " |" for cells in rows]
    return lines


STATE_HEAD = ["库内", "存档", "留下未删 held", "降权（只报告）", "已清除"]
METRIC_HEAD = ["Hit@5 热", "Hit@5 温", "Hit@5 冷", "平均条数", "噪声率", "新在旧前", "被删的 热/温/冷"]


def _state_cells(r):
    return [str(r["in_store"]), str(r["archived"]), str(r["held"]), str(r["demoted"]), str(r["purged"])]


def final_table(summaries):
    """The last day of every arm of every backend."""
    rows = []
    for summary in summaries:
        last = {}
        for row in summary["timeline"]:
            last[row["arm"]] = row  # the timeline is in day order
        for name in summary["arms"]:
            r = last[name]
            rows.append([summary["backend"], ARM_TITLES.get(name, name), str(r["day"])]
                        + _state_cells(r) + _cells(r))
    return _table(["后端", "组", "天"] + STATE_HEAD + METRIC_HEAD, rows)


def timeline_tables(summary):
    lines = []
    for name, rows in _by_arm(summary["timeline"]).items():
        picked = [r for r in rows if r["day"] in TABLE_DAYS]
        lines += ["#### %s" % ARM_TITLES.get(name, name), ""]
        lines += _table(["天"] + STATE_HEAD + METRIC_HEAD,
                        [[str(r["day"])] + _state_cells(r) + _cells(r) for r in picked])
        lines.append("")
    return lines


def restore_lines(summary):
    lines = []
    for name, check in summary["restore"].items():
        title = ARM_TITLES.get(name, name)
        if check.get("question") is None:
            lines.append("- %s：%s。" % (title, check.get("note", "没有可测的题")))
        else:
            lines.append(
                "- %s：运行 %d 天后，冷门题 %s 的记忆 %s 已被遗忘并在存档里（恢复前查得到：%s）；"
                "`forget restore` 之后查得到：%s，全文与原文一致：%s。" % (
                    title, check["after_days"], check["question"], check["memory"],
                    "是" if check["found_before_restore"] else "否",
                    "是" if check["found_after_restore"] else "否", "是" if check["text_identical"] else "否"))
    return lines


def write_combined(out_dir, reading=""):
    """summary.md from the summary.json of every backend run found under out_dir. Returns the path or None."""
    summaries = []
    for backend in BACKENDS:
        path = os.path.join(out_dir, backend, "summary.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                summaries.append(json.load(f))
    if not summaries:
        return None
    first = summaries[0]
    per_group = first["asked_per_group"]
    lines = [
        "# 遗忘曲线模拟：得到什么，付出什么", "",
        "由 `python bench/forgetting_sim.py` 生成（每个后端跑一次），固定种子。数据是冻结的基准数据 v1"
        "（%d 条记忆、160 个问题，见 `bench/data/README.md`），没有改动。向量一路需要一个 embeddings 接口，"
        "只在容器里跑（命令见文末）。" % first["memories"], "",
        "## 方法", "",
        "- 每一组用一个进程内的网关和一个存储：关键词后端 `sqlite`（只有关键词），或混合后端 `hybrid`（sqlite + `bge-m3` 向量）。"
        "没有手设任何分数线：`min_score`、`relative_score` 和向量的 `floor`/`ceil` 都用各后端自己的值，分数线档位固定为精确档"
        "（`[recall] profile = \"precise\"`）：这批数字是网关默认还是精确档时测的，固定后重跑能得到同样的数；默认改成宽松档之后没有用宽松档重做这个模拟。召回阶段是网关的默认一套"
        "（`min_score`、`relative_score`、`newer_first`、`fill_text`、`drop_oversize`、`redact`、`dedupe`、`budget`），有遗忘的组在 `min_score` 后面、"
        "`relative_score` 前面加 `freshness`，让相对分数线看到打过新鲜度的分数；"
        "基线没有 `[forgetting]`，所以也没有 `freshness`。",
        "- 假时钟：`build_gateway(..., wall=时钟)`。%d 条记忆带着自己的日期写入；模拟从最新一条记忆的后一天（%s）开始，"
        "共 %d 天。" % (first["memories"], first["start_date"], first["days"]),
        "- 每天问 0 到 6 个问题（平均 3 个），从 120 个可回答的问题（lexical、paraphrase、updated）里抽："
        "固定的“热”三分之一（%d 题）占 %d%% 的流量，“温”三分之一（%d 题）占其余，“冷”三分之一（%d 题）从不被问。"
        "%d 天共 %d 次提问：热 %d 次，温 %d 次。" % (
            len(first["groups"]["hot"]), first["hot_share"] * 100, len(first["groups"]["warm"]),
            len(first["groups"]["cold"]), first["days"], first["questions_asked"], per_group["hot"], per_group["warm"]),
        "- 每天问完后时钟再走 24 小时，网关跑一次清理（`Gateway.sweep()`，后台清理，从不解除限速），所以每个存储每天"
        "最多忘 max(5, 2%%×条数) 条。所有组用同一份随机抽签（种子 %d）。" % first["seed"],
        "- 四组，跑之前就定下，看到结果后没有改：**基线**（不遗忘）；**默认设置**（半衰期 90 天，巩固系数 1.6，降权线 0.3，"
        "遗忘线 0.05，存档 90 天，`freshness` 保底 0.7、计用规则 `top`，限速 `max_forget_fraction` 0.02 和 `max_forget_min` 5）；"
        "**默认设置 + 钉住身份类**（同上，`kind` 是 `identity` 的 39 条记忆在一开始钉住：文档里的做法，身份类信息钉住、永不遗忘）；"
        "**激进设置**（半衰期 30 天，其余同默认）。",
        "- 测量：第 0 天、第 1 天和每 30 天（完整数据在 `sqlite/timeline.csv`、`hybrid/timeline.csv`）。测量时用网关自己的召回阶段"
        "和线程池问全部 120 个题，默认条数上限 5，但不计一次“用到”，所以测量不会改变被测的东西。",
        "- 指标：**Hit@5** = 期望的记忆在返回的前 5 条里的题所占比例；**平均条数** = 每题返回几条；"
        "**噪声率** = 返回的条目里不是期望记忆的比例（updated 题里被取代的旧记忆也算噪声）；"
        "**新在旧前** = 20 个 updated 题里，新记忆被返回并且排在旧记忆前面的比例（旧的没返回也算新的在前，新的没返回算没有）；"
        "**被删的** = 该组题的期望记忆已经不在存储里的条数（热 / 温 / 冷）；**降权** = 排序用的保持率低于降权线的条数；"
        "**留下未删 held** = 最近一次清理里已经到期、但被限速留到后面几次清理的条数；**降权** = 排序用的保持率低于降权线的条数，"
        "现在只是报告（排序里每条记忆最多只损失 30%），不改变排序。",
        "- 场景（种子、流量、分组、天数、各组设置；召回设置用各后端自己的默认值）在看到本次任何结果之前就定下了，没有为了让结果好看而调过。", ""]
    lines += ["## 最后一天（第 %d 天）：所有组、两个后端" % first["days"], ""]
    lines += final_table(summaries)
    lines.append("")
    for summary in summaries:
        lines += ["## 时间线：%s" % BACKEND_TITLES[summary["backend"]], "",
                  "第 %s 天各一行。" % " / ".join(str(d) for d in TABLE_DAYS), ""]
        lines += timeline_tables(summary)
        lines += ["### 从存档恢复（%s）" % BACKEND_TITLES[summary["backend"]], ""] + restore_lines(summary) + [""]
        lines += ["（这个后端跑了 %.0f 秒。）" % summary["seconds"], ""]
    if reading:
        lines += [reading.rstrip(), ""]
    path = os.path.join(out_dir, "summary.md")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="forgetting simulation on the frozen benchmark data")
    parser.add_argument("--backend", choices=BACKENDS, default="sqlite")
    parser.add_argument("--embed-url", metavar="URL", help="base url of the embeddings endpoint (hybrid only)")
    parser.add_argument("--out", default=OUT_DIR, metavar="DIR", help="results folder (default bench/results/forgetting)")
    parser.add_argument("--combine-only", action="store_true",
                        help="run nothing: rebuild summary.md from the runs already in the folder and reading.md")
    args = parser.parse_args(argv)
    if args.backend == "hybrid" and not args.embed_url and not args.combine_only:
        parser.error("--backend hybrid needs --embed-url")
    result = None
    if not args.combine_only:
        memories, queries = load("memories.json"), load("queries.json")
        started = time.time()
        result = simulate(memories, queries, backend=args.backend, embed_url=args.embed_url,
                          progress=lambda line: print(line, flush=True))
        write_run(args.out, args.backend, result, memories, time.time() - started)
    reading = ""
    reading_path = os.path.join(args.out, "reading.md")
    if os.path.exists(reading_path):
        with open(reading_path, encoding="utf-8") as f:
            reading = f.read()
    write_combined(args.out, reading)
    return result


if __name__ == "__main__":
    main()
    print("wrote results")
