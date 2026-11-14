"""The common scorer of the memory benchmark. Standard library only.

    python bench/score.py

Reads bench/data/memories.json, bench/data/queries.json and every
bench/results/raw/<system>.json (the contract is in bench/CONTRACT.md), checks the
raw files, and writes bench/results/summary.json and bench/results/summary.md.

The scorer knows nothing about any system: it only turns ranked and deployed id lists
into numbers. A raw file that breaks the contract is reported and left out; the exit
code is then 1 (the other systems are still scored).
"""
import json
import math
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
RAW_DIR = os.path.join(HERE, "results", "raw")
OUT_DIR = os.path.join(HERE, "results")
SYSTEMS_PATH = os.path.join(HERE, "systems.json")

ANSWERABLE = ("lexical", "paraphrase", "updated")
TYPES = ANSWERABLE + ("quiet",)
MAX_RANKED = 10
TOP_K = 5
# Paid modes that were not re-run in the final sequential pass: their timings come from a loaded machine.
LOADED_MACHINE_SYSTEMS = ("mem0", "graphiti")
META_FIELDS = ("system", "version", "mode", "how", "ingest_seconds", "llm_tokens", "llm_cost_cny", "answers", "notes")


# ------------------------------------------------------------------ loading and validation

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dataset_version(data_dir):
    """The version string the README of the dataset declares ("v1"), or "unknown"."""
    try:
        with open(os.path.join(data_dir, "README.md"), encoding="utf-8") as f:
            found = re.search(r"版本[:：]\s*`?(v\d+)", f.read())
        return found.group(1) if found else "unknown"
    except OSError:
        return "unknown"


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_items(where, items, memories, limit, errors):
    if not isinstance(items, list):
        errors.append("%s: must be a list" % where)
        return
    if limit is not None and len(items) > limit:
        errors.append("%s: %d items, at most %d allowed" % (where, len(items), limit))
    seen = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict) or "id" not in item or not _is_number(item.get("score")):
            errors.append("%s[%d]: needs {\"id\": str or null, \"score\": number}" % (where, i))
            continue
        mid = item["id"]
        if mid is None:
            continue
        if not isinstance(mid, str) or mid not in memories:
            errors.append("%s[%d]: unknown memory id" % (where, i))
        elif mid in seen:
            errors.append("%s[%d]: memory %s listed twice (collapse duplicates, keep the best)" % (where, i, mid))
        seen.add(mid)


def validate_result(result, memories, queries):
    """A list of contract violations of one raw result (empty when it is fine)."""
    errors = []
    if not isinstance(result, dict):
        return ["the file must hold a JSON object"]
    for field in META_FIELDS:
        if field not in result:
            errors.append("missing field %s" % field)
    if errors:
        return errors
    for field in ("system", "version", "mode", "how"):
        if not isinstance(result[field], str) or not result[field].strip():
            errors.append("%s must be a non-empty string" % field)
    for field in ("ingest_seconds", "llm_tokens", "llm_cost_cny"):
        if not _is_number(result[field]) or result[field] < 0:
            errors.append("%s must be a number >= 0" % field)
    if not isinstance(result["notes"], list) or not all(isinstance(n, str) for n in result["notes"]):
        errors.append("notes must be a list of strings")
    answers = result["answers"]
    if not isinstance(answers, dict):
        return errors + ["answers must be an object"]
    missing = sorted(set(queries) - set(answers))
    if missing:
        errors.append("%d questions have no answer (first: %s)" % (len(missing), missing[0]))
    extra = sorted(set(answers) - set(queries))
    if extra:
        errors.append("%d answers for unknown questions (first: %s)" % (len(extra), extra[0]))
    for qid in sorted(set(answers) & set(queries)):
        answer = answers[qid]
        if not isinstance(answer, dict) or not {"ranked", "deployed", "ms"} <= set(answer):
            errors.append("%s: needs ranked, deployed and ms" % qid)
            continue
        _check_items("%s.ranked" % qid, answer["ranked"], memories, MAX_RANKED, errors)
        _check_items("%s.deployed" % qid, answer["deployed"], memories, None, errors)
        if not _is_number(answer["ms"]) or answer["ms"] < 0:
            errors.append("%s.ms must be a number >= 0" % qid)
        if len(errors) > 20:
            errors.append("... (more errors not shown)")
            break
    return errors


# ------------------------------------------------------------------ metrics

def _ids(items):
    return [item["id"] for item in items]


def rank_of(ids, wanted):
    """1-based position of the first id in `wanted` within `ids`, or None."""
    for position, mid in enumerate(ids, 1):
        if mid in wanted:
            return position
    return None


def retrieval_metrics(ranked_ids, expect):
    """Hit@1, Hit@5, reciprocal rank (cut at 10) and recall@5 for one answerable question."""
    wanted = set(expect)
    top5 = set(ranked_ids[:TOP_K])
    rank = rank_of(ranked_ids[:MAX_RANKED], wanted)
    return {
        "rank": rank,
        "hit1": 1.0 if rank == 1 else 0.0,
        "hit5": 1.0 if rank is not None and rank <= TOP_K else 0.0,
        "rr": 1.0 / rank if rank else 0.0,
        "recall5": len(wanted & top5) / len(wanted),
    }


def fresh_first(ranked_ids, new, stale):
    """True when the newer memory ranks above every stale one. A missing stale memory
    counts as a win only if the newer one is present."""
    new_rank = rank_of(ranked_ids, {new})
    if new_rank is None:
        return False
    stale_rank = rank_of(ranked_ids, set(stale))
    return stale_rank is None or new_rank < stale_rank


def delivered_fresh_first(deployed_ids, new, stale):
    """None when the newer memory was not delivered (the question says nothing about order); otherwise True
    when no stale memory was delivered or the newer one stands before every delivered stale one. Works on the
    ORDERED deployed list, so it counts what the model is actually shown, not the unthresholded ranking."""
    new_rank = rank_of(deployed_ids, {new})
    if new_rank is None:
        return None
    stale_rank = rank_of(deployed_ids, set(stale))
    return stale_rank is None or new_rank < stale_rank


def _mean(values):
    return sum(values) / len(values) if values else None


def percentile(values, p):
    """Nearest-rank percentile (p in 0..100) of a list of numbers; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(p / 100 * len(ordered)) - 1)
    return ordered[index]


def hit_curve(ranks, max_k=MAX_RANKED):
    """Hit@k for k = 1..max_k from the 1-based rank of the first expected memory (None = absent)."""
    if not ranks:
        return None
    return [sum(1 for r in ranks if r is not None and r <= k) / len(ranks) for k in range(1, max_k + 1)]


def harmonic_mean(a, b):
    """Harmonic mean of two numbers in 0..1; None when either is unknown, 0 when both are 0."""
    if a is None or b is None:
        return None
    return 0.0 if a + b == 0 else 2 * a * b / (a + b)


def mean_memory_chars(memories):
    """Mean text length over the whole memory set; stands in for a delivered item without an id."""
    return _mean([len(m["text"]) for m in memories.values()]) if memories else None


def delivered_chars(answers, qids, memories):
    """(average characters of memory text delivered per question, number of delivered items
    without an id). An item with id null counts with the mean memory length: the scorer cannot
    know what it contained, and leaving it out would flatter the system."""
    if not qids or not memories:
        return None, 0
    fill = mean_memory_chars(memories)
    total, nulls = 0.0, 0
    for q in qids:
        for mid in _ids(answers[q]["deployed"]):
            if mid is None:
                total += fill
                nulls += 1
            else:
                total += len(memories[mid]["text"])
    return total / len(qids), nulls


def _group_metrics(qids, queries, answers, kind):
    """Metrics for one set of questions. `kind` is a question type, or "answerable" for the
    three answerable types together."""
    out = {"questions": len(qids)}
    if not qids:
        return out
    quiet = kind == "quiet"
    deployed_counts = [len(answers[q]["deployed"]) for q in qids]
    out["avg_items"] = _mean(deployed_counts)
    if quiet:
        out["quiet_rate"] = _mean([1.0 if n == 0 else 0.0 for n in deployed_counts])
        return out
    per = [retrieval_metrics(_ids(answers[q]["ranked"]), queries[q]["expect"]) for q in qids]
    out["hit1"] = _mean([m["hit1"] for m in per])
    out["hit5"] = _mean([m["hit5"] for m in per])
    out["mrr10"] = _mean([m["rr"] for m in per])
    out["recall5"] = _mean([m["recall5"] for m in per])
    out["deployed_hit"] = _mean([
        1.0 if set(_ids(answers[q]["deployed"])) & set(queries[q]["expect"]) else 0.0 for q in qids])
    noise = []
    for q in qids:
        got = _ids(answers[q]["deployed"])
        if got:
            noise.append(sum(1 for mid in got if mid not in queries[q]["expect"]) / len(got))
    out["noise"] = _mean(noise)  # None when nothing was ever returned
    # precision = 1 - noise, over the questions that returned something (the same denominator).
    out["precision"] = None if out["noise"] is None else 1.0 - out["noise"]
    out["precision_questions"] = len(noise)
    out["delivered_f1"] = harmonic_mean(out["deployed_hit"], out["precision"])
    out["hit_at_k"] = hit_curve([m["rank"] for m in per])
    if kind == "updated":
        out["fresh_first"] = _mean([
            1.0 if fresh_first(_ids(answers[q]["ranked"]), queries[q]["expect"][0], queries[q].get("stale", []))
            else 0.0 for q in qids])
        verdicts = [delivered_fresh_first(_ids(answers[q]["deployed"]), queries[q]["expect"][0],
                                          queries[q].get("stale", [])) for q in qids]
        verdicts = [v for v in verdicts if v is not None]
        out["delivered_fresh_first_questions"] = len(verdicts)  # updated questions whose newer memory was delivered
        out["delivered_fresh_first_wins"] = sum(1 for v in verdicts if v)
        out["delivered_fresh_first"] = (out["delivered_fresh_first_wins"] / len(verdicts)) if verdicts else None
    return out


def score_system(result, queries, memories=None, systems=None):
    """All metrics of one valid raw result: overall and per question type. `memories` (the
    memory set) enables the delivered-characters figure; `systems` (bench/systems.json) adds
    the family and the display metadata."""
    answers = result["answers"]
    by_type = {}
    for kind in TYPES:
        qids = sorted(q for q in queries if queries[q]["type"] == kind)
        by_type[kind] = _group_metrics(qids, queries, answers, kind)
    answerable = sorted(q for q in queries if queries[q]["type"] != "quiet")
    overall = _group_metrics(answerable, queries, answers, "answerable") if answerable else {}
    # Overall rows mix three types: the averages are per question, not per type.
    if "updated" in by_type and by_type["updated"].get("questions"):
        overall["fresh_first"] = by_type["updated"].get("fresh_first")
        for key in ("delivered_fresh_first", "delivered_fresh_first_questions", "delivered_fresh_first_wins"):
            overall[key] = by_type["updated"].get(key)
    overall["quiet_rate"] = by_type["quiet"].get("quiet_rate")
    overall["questions"] = len(queries)
    overall["avg_items"] = _mean([len(answers[q]["deployed"]) for q in queries])
    latencies = [answers[q]["ms"] for q in queries]
    overall["latency_p50_ms"] = percentile(latencies, 50)
    overall["latency_p95_ms"] = percentile(latencies, 95)
    if memories:
        overall["avg_chars"], overall["delivered_null_items"] = delivered_chars(answers, sorted(queries), memories)
    record = {
        "system": result["system"], "version": result["version"], "mode": result["mode"],
        "how": result["how"], "notes": result["notes"],
        "ingest_seconds": result["ingest_seconds"], "llm_tokens": result["llm_tokens"],
        "llm_cost_cny": result["llm_cost_cny"],
        "overall": overall, "by_type": by_type,
    }
    record.update(describe_system(result["system"], systems))
    return record


# ------------------------------------------------------------------ grouping

def describe_system(name, systems):
    """family / kind / display metadata of a result name, from bench/systems.json."""
    family = "feiyu-memgate" if name.startswith("feiyu-memgate") else "other"
    info = (systems or {}).get(name, {})
    return {"family": family, "kind": info.get("kind"), "llm": info.get("llm"),
            "project": info.get("project"), "label": info.get("label"),
            "caveat": info.get("caveat"), "experiment": bool(info.get("experiment", False)),
            "variant": bool(info.get("variant", False))}


def load_systems(path=SYSTEMS_PATH):
    """The mapping file, or {} when it is absent (the scorer still works without it)."""
    try:
        return load_json(path)
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------ reports

def _pct(value):
    return "-" if value is None else "%.1f%%" % (value * 100)


def _num(value, digits=3):
    return "-" if value is None else ("%.*f" % (digits, value))


def _cell(text):
    return str(text).replace("|", "/").replace("\n", " ")


def _table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines


HOW_TO_READ = (
    "每个系统读入同一批 240 条记忆，回答同一批 160 个问题；这里只是把各自的原始答案按同一把尺子打分。"
    "“排名”类指标（Hit@1、Hit@5、MRR@10、Recall@5）看的是系统不设阈值时前 10 条里有没有正确的记忆，"
    "反映检索本身的能力；“实际交付”类指标（命中、噪声、条数、安静率）看的是系统按默认设置真正会交给模型的内容，"
    "反映开箱即用的体验。安静率是对“记忆里没有答案”的问题什么都不返回的比例，越高越好，"
    "它和命中率此消彼长，不能只看其中一个。新旧优先率只统计“事实被更新过”的问题，看新记忆是否排在旧记忆前面。"
    "分题型的表更有信息量：关键词检索通常在“字面”题上很强、在“改写”题上很弱，语义检索相反。"
    "延迟是单机上每个问题的墙钟时间，只能在同一台机器上的同一批结果之间比较；"
    "入库时间、模型调用量和费用由各系统自报。240 条记忆的数据集很小，几个百分点的差距不应当被当作结论。"
    "精确率是交给模型的条目里正确的占比（等于 1 减噪声，只统计交付了东西的可答问题，分母见各表下的说明），"
    "交付 F1 是交付命中和精确率的调和平均，两个都高才高：什么都不交精确率没有定义，全交命中高但精确率低。"
    "平均字数是每个问题平均交给模型多少字的记忆原文，直接反映会占掉多少上下文；"
    "Hit@k 曲线是 k 从 1 到 10 时前 k 条里有正确记忆的比例，看一个系统要翻到第几条才够用。"
    "“新旧优先”看不设阈值的排名里新记忆是否排在旧记忆前面；“交付中新旧优先”看实际交给模型的那几条："
    "在期望的新记忆被交付了的更新题里，旧记忆没有被一起交付、或者排在新记忆后面的占比，括号里是“成立数/这样的题数”，"
    "表里的分母每个系统不同（新记忆没交付的题不算），所以要连同题数一起读。"
    "各系统的模式和注意事项见文末的备注，比较前请先读它们。"
)


def _chars(s):
    """Average delivered characters; a dagger marks systems with id-less delivered items."""
    value = s["overall"].get("avg_chars")
    if value is None:
        return "-"
    return "%.0f%s" % (value, "†" if s["overall"].get("delivered_null_items") else "")


def _overall_notes(ordered, answerable):
    """Explanations under the overall table: precision denominators and the id-less footnote."""
    lines = ["精确率和交付 F1 只统计交付了东西的可答问题（共 %d 个可答问题；各系统的分母：%s）；"
             "什么都没交的可答问题不进分母，所以交得很少的系统精确率可以很高。" % (
                 answerable, "，".join("%s %d" % (s["system"], s["overall"].get("precision_questions", 0))
                                      for s in ordered))]
    flagged = [s for s in ordered if s["overall"].get("delivered_null_items")]
    if flagged:
        lines += ["", "† 平均字数：这些系统交付的条目里有没有记忆编号的（%s），"
                  "按全部记忆的平均长度计入。" % "、".join(
                      "%s %d 条" % (s["system"], s["overall"]["delivered_null_items"]) for s in flagged)]
    loaded = [s["system"] for s in ordered if s["system"] in LOADED_MACHINE_SYSTEMS]
    if loaded:
        lines += ["", "‡ 延迟和入库时间：%s 的结果文件是付费模式的结果，没有在最后这次逐个顺序运行里重跑"
                  "（重跑要再花钱），它们的 p50/p95 和入库时间来自一台同时有别的任务在跑的机器，"
                  "不能和其他行的时间直接比。" % "、".join(loaded)]
    return lines + [""]


def build_markdown(summary):
    systems = summary["systems"]
    ds = summary["dataset"]
    lines = ["# 记忆检索基准结果", "",
             "数据集版本 %s：%d 条记忆，%d 个问题（%s）。" % (
                 ds["version"], ds["memories"], ds["queries"],
                 "、".join("%s %d" % (k, v) for k, v in ds["types"].items())), ""]
    lines += ["## 怎么读", "", HOW_TO_READ, ""]
    ordered = sorted(systems.values(), key=lambda s: (-(s["overall"].get("hit5") or 0), s["system"]))
    if not ordered:
        lines += ["还没有任何系统的结果。", ""]
    else:
        lines += ["## 总表（按 Hit@5 排序）", ""]
        header = ["系统", "Hit@1", "Hit@5", "MRR@10", "Recall@5", "交付命中", "噪声", "平均条数", "安静率",
                  "新旧优先", "p50 ms", "p95 ms", "入库 s", "tokens", "费用 CNY",
                  "精确率", "交付 F1", "平均字数", "家族", "检索类型"]
        rows = []
        for s in ordered:
            o = s["overall"]
            rows.append([s["system"], _pct(o.get("hit1")), _pct(o.get("hit5")), _num(o.get("mrr10")),
                         _pct(o.get("recall5")), _pct(o.get("deployed_hit")), _pct(o.get("noise")),
                         _num(o.get("avg_items"), 2), _pct(o.get("quiet_rate")), _pct(o.get("fresh_first")),
                         _num(o.get("latency_p50_ms"), 2), _num(o.get("latency_p95_ms"), 2),
                         _num(s["ingest_seconds"], 1), "%d" % s["llm_tokens"], _num(s["llm_cost_cny"], 2),
                         _pct(o.get("precision")), _pct(o.get("delivered_f1")), _chars(s),
                         s.get("family") or "-", s.get("kind") or "-"])
        lines += _table(header, rows) + [""]
        lines += _overall_notes(ordered, sum(v for k, v in ds["types"].items() if k != "quiet"))
        lines += ["## Hit@k 曲线（前 k 条里有正确记忆的比例）", ""]
        header = ["系统"] + ["@%d" % k for k in range(1, MAX_RANKED + 1)]
        rows = [[s["system"]] + [_pct(v) for v in (s["overall"].get("hit_at_k") or [None] * MAX_RANKED)]
                for s in ordered]
        lines += _table(header, rows) + [""]
        titles = {"lexical": "字面题（lexical）", "paraphrase": "改写题（paraphrase）",
                  "updated": "更新题（updated）"}
        for kind in ANSWERABLE:
            lines += ["## %s" % titles[kind], ""]
            header = ["系统", "Hit@1", "Hit@5", "MRR@10", "Recall@5", "交付命中", "噪声", "平均条数"]
            if kind == "updated":
                header += ["新旧优先", "交付中新旧优先", "题数"]
            rows = []
            for s in ordered:
                t = s["by_type"][kind]
                row = [s["system"], _pct(t.get("hit1")), _pct(t.get("hit5")), _num(t.get("mrr10")),
                       _pct(t.get("recall5")), _pct(t.get("deployed_hit")), _pct(t.get("noise")),
                       _num(t.get("avg_items"), 2)]
                if kind == "updated":
                    n = t.get("delivered_fresh_first_questions")
                    row += [_pct(t.get("fresh_first")), _pct(t.get("delivered_fresh_first")),
                            "-" if n is None else "%d/%d" % (t.get("delivered_fresh_first_wins", 0), n)]
                rows.append(row)
            lines += _table(header, rows) + [""]
        lines += ["## 安静题（quiet）", "", "记忆里没有答案的问题；交付了任何东西都算没有保持安静。", ""]
        rows = [[s["system"], _pct(s["by_type"]["quiet"].get("quiet_rate")),
                 _num(s["by_type"]["quiet"].get("avg_items"), 2)] for s in ordered]
        lines += _table(["系统", "安静率", "平均条数"], rows) + [""]
        lines += ["## 各系统的模式与备注", ""]
        for s in ordered:
            lines += ["### %s（%s）" % (s["system"], s["version"]), "",
                      "- 模式：%s" % s["mode"], "- 调用：%s" % s["how"]]
            if s.get("caveat"):
                lines += ["- 注意：%s" % s["caveat"]["zh"]]
            if s.get("experiment"):
                lines += ["- 这是实验变体，不是默认运行；图表里不画，表里保留。"]
            if s.get("variant"):
                lines += ["- 这是同一个后端的另一档配置（别的模型或宽松档），不在五张基准图里画，表里保留。"]
            lines += ["- 备注：%s" % n for n in s["notes"]]
            lines += [""]
    if summary["errors"]:
        lines += ["## 未计入的结果文件", ""]
        for name, errs in summary["errors"].items():
            lines += ["- %s：%s" % (name, "；".join(errs[:5]))]
        lines += [""]
    return "\n".join(lines)


# ------------------------------------------------------------------ driver

def run_date(raw_dir):
    """Date (YYYY-MM-DD) of the newest raw result file: when the benchmark was last run."""
    try:
        stamps = [os.path.getmtime(os.path.join(raw_dir, n)) for n in os.listdir(raw_dir) if n.endswith(".json")]
    except OSError:
        return None
    return time.strftime("%Y-%m-%d", time.localtime(max(stamps))) if stamps else None


def run(data_dir=DATA_DIR, raw_dir=RAW_DIR, out_dir=OUT_DIR, systems_path=SYSTEMS_PATH):
    """Score everything; returns (summary dict, list of problem messages)."""
    memories = load_json(os.path.join(data_dir, "memories.json"))
    queries = load_json(os.path.join(data_dir, "queries.json"))
    types = {}
    for q in queries.values():
        types[q["type"]] = types.get(q["type"], 0) + 1
    systems = load_systems(systems_path)
    summary = {"dataset": {"version": dataset_version(data_dir), "memories": len(memories),
                           "queries": len(queries), "types": types,
                           "run_date": run_date(raw_dir)},
               "systems": {}, "errors": {}}
    names = sorted(n for n in os.listdir(raw_dir) if n.endswith(".json")) if os.path.isdir(raw_dir) else []
    for name in names:
        try:
            result = load_json(os.path.join(raw_dir, name))
        except (OSError, ValueError) as error:
            summary["errors"][name] = ["cannot read: %s" % type(error).__name__]
            continue
        errors = validate_result(result, memories, queries)
        if not errors and result["system"] in summary["systems"]:
            errors = ["system name %r is already used by another file" % result["system"]]
        if errors:
            summary["errors"][name] = errors
            continue
        summary["systems"][result["system"]] = score_system(result, queries, memories, systems)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
        f.write("\n")
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(build_markdown(summary))
    problems = ["%s: %s" % (n, e[0]) for n, e in summary["errors"].items()]
    return summary, problems


def main():
    summary, problems = run()
    print("scored %d system(s): %s" % (len(summary["systems"]), ", ".join(sorted(summary["systems"])) or "-"))
    for problem in problems:
        print("NOT SCORED " + problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
