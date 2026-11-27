"""Summary tables of the external runs, generated from the metrics files (no figure is typed by hand).

    python -I bench/external/report.py                 # writes results/summary.md and results/summary.json
    python -I bench/external/report.py --readme zh     # prints the tables of the README section (zh or en)

It reads only files that are in the repository: results/<dataset>/<system>.metrics.json (every configuration on the
stores it ran on), same-stores-<label>.json (every configuration that covers a given set of stores, scored on exactly
those stores), latency-summary.json and write-audit.json (per dataset and configuration: memories sent and memories
stored on the stores of the published run). A configuration that stored less than 95% of what it was sent on a
dataset is left out of every table and chart of that dataset and named under the first table with the reason; the
write audit itself is the last table. Per dataset it gives:
  - the configurations that ran on every store, scored on all questions, with the figures per question type;
  - the configurations that ran on a sample of stores (those that call a language model, and Hindsight because of its
    running time), each group scored on exactly its stores next to this project's default configurations on the same
    stores (SAMPLES below lists the groups that were run; a prefix of the stores in ascending id order);
  - what the paid configurations used: calls, tokens, money, seconds per memory;
  - the latency figures of the separate sequential pass.
Definitions of the figures: score.py. Delivered F1 is the harmonic mean of delivered hit and precision, as bench/score.py.
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

RESULTS = os.path.join(HERE, "results")
DATASETS = ("perltqa", "locomo")
OWN = ["feiyu-memgate-sqlite", "feiyu-memgate-embedding", "feiyu-memgate-hybrid"]
REFERENCE = ["feiyu-memgate-sqlite", "feiyu-memgate-hybrid"]  # shown next to every sampled group
# The samples that were run: (number of leading stores in id order, label of the same-stores file, configurations).
SAMPLES = {
    "perltqa": [(10, "p01-p10", ["graphiti-deepseek-flash", "graphiti-claude-haiku-5-5"]),
                (5, "p01-p05", ["hindsight-chunks", "hindsight-llm-deepseek-flash", "hindsight-llm-claude-haiku-5-5"]),
                (3, "p01-p03", ["dsh-fish-memory", "dsh-fish-memory-expand-deepseek-flash",
                                "dsh-fish-memory-expand-claude-haiku-5-5"])],
    "locomo": [(3, "first3", ["graphiti-deepseek-flash", "graphiti-claude-haiku-5-5"]),
               (1, "conv-26", ["hindsight-chunks", "hindsight-llm-deepseek-flash", "hindsight-llm-claude-haiku-5-5",
                               "dsh-fish-memory", "dsh-fish-memory-expand-deepseek-flash",
                               "dsh-fish-memory-expand-claude-haiku-5-5"])],
}
NOT_A_TYPE = ("ALL answerable", "no-answer")
EXTRA_LABELS = {  # configurations that exist only here (bench/systems.json has the others)
    "mem0-llm-deepseek-flash": ("mem0（LLM 提取，deepseek-flash）", "mem0 (LLM extraction, deepseek-flash)", "extraction"),
    "mem0-llm-claude-haiku-5-5": ("mem0（LLM 提取，claude-haiku-5-5）", "mem0 (LLM extraction, claude-haiku-5-5)",
                                  "extraction"),
    "graphiti-deepseek-flash": ("Graphiti（deepseek-flash）", "Graphiti (deepseek-flash)", "graph"),
    "graphiti-claude-haiku-5-5": ("Graphiti（claude-haiku-5-5）", "Graphiti (claude-haiku-5-5)", "graph"),
}
KIND = {"keyword": ("关键词", "keyword"), "vector": ("向量", "vector"), "hybrid": ("混合", "hybrid"),
        "extraction": ("提取", "extraction"), "graph": ("图", "graph")}
TEXT = {
    "zh": {"system": "系统", "kind": "检索类型", "stores": "库", "n": "可答问题", "hit1": "Hit@1", "hit5": "Hit@5",
           "hit10": "Hit@10", "dh": "交付命中", "prec": "精确率", "f1": "交付 F1", "items": "平均交付条数",
           "chars": "平均字数", "quiet": "安静率", "model": "模型", "memories": "记忆", "calls": "调用",
           "input": "输入 tokens（其中缓存命中）", "output": "输出 tokens", "usd": "花费（美元）",
           "per1k": "每千条记忆（美元）", "spm": "写入耗时（秒/条）", "dataset": "数据集", "p50": "p50", "p95": "p95",
           "questions": "问题", "world": "库（记忆数）", "kind_lat": "测量方式", "idle": "空闲机器上单独顺序测量",
           "load": "并行运行时测得（有其他负载）"},
    "en": {"system": "System", "kind": "Retrieval", "stores": "Stores", "n": "Answerable", "hit1": "Hit@1",
           "hit5": "Hit@5", "hit10": "Hit@10", "dh": "Delivered hit", "prec": "Precision", "f1": "Delivered F1",
           "items": "Items delivered", "chars": "Characters", "quiet": "Quiet rate", "model": "Model",
           "memories": "Memories", "calls": "Calls", "input": "Input tokens (of which cache hits)",
           "output": "Output tokens", "usd": "Cost (USD)", "per1k": "Per 1,000 memories (USD)",
           "spm": "Write time (s per memory)", "dataset": "Dataset", "p50": "p50", "p95": "p95",
           "questions": "Questions", "world": "Store (memories)", "kind_lat": "How measured",
           "idle": "separate sequential pass on an idle machine", "load": "measured during the parallel runs (under load)"},
}


REASONS = {  # why fewer memories were stored than sent: (short zh, short en, long zh, long en)
    "daily-quota": (
        "该项目每天收记忆的上限",
        "the project's daily limit on new memories",
        "该项目每天最多收 8 条记忆（它的 dailyQuota 设置的默认值）。本测试按每条记忆的日期写入，一天里超过 8 条的被它拒收："
        "LoCoMo 的一次会话有几十句话，都在同一天；PerLTQA 里没有日期的记忆被本测试记在库里最早的那一天。"
        "这是本测试的喂法和这个设置不相配造成的，这些库上的结果不代表该项目。",
        "The project accepts at most 8 memories a day (the default of its dailyQuota setting). This benchmark writes each "
        "memory on its own date, and the writes of a day beyond the eighth were refused: a LoCoMo session has dozens of "
        "turns on one day, and the PerLTQA memories without a date were given the earliest day of their store. This comes "
        "from the way the benchmark fed the store, which does not fit that setting: a result on these stores says nothing "
        "about the project."),
    "write-gate": (
        "该项目的写入关卡把与已有记忆相近的新记忆放进待审队列",
        "the project's write gate puts a memory that resembles a stored one into a review queue",
        "该项目的写入关卡把与已有记忆相近的新记忆放进待审队列，或要求确认。测试中没有人审核，这些记忆不在库里。",
        "The project's write gate puts a new memory that resembles a stored one into a review queue, or asks for "
        "confirmation. Nobody reviews during a run, so these memories are not in the store."),
    "duplicates": ("与库中另一条一字不差的记忆被跳过", "a memory identical to another one of the store is skipped",
                   "与库中另一条一字不差的记忆被跳过。", "A memory identical to another one of the store is skipped."),
    "merged": ("被该项目并入相近的记忆", "merged into a similar memory by the project",
               "这些记忆被该项目并入相近的记忆。", "These memories were merged into similar ones by the project."),
    "model-output": ("模型的输出不合格式，这几条没有写入", "the model's output did not parse, so these were not written",
                     "模型的输出不合格式，这几条没有写入。", "The model's output did not parse, so these were not written."),
}
BASIS = {"record": ("运行记录", "record of the run"),
         "count": ("重跑写入后数库里的条数", "rows counted after the ingest was run again"),
         "probe": ("重跑写入后逐条问回", "every memory asked back after the ingest was run again")}
AUDIT_TEXT = {
    "zh": {"title": "写入对账：发去的记忆和存下的记忆", "excluded_title": "%s：未列入的配置",
           "excluded_intro": "下列配置在这个数据集上存下的记忆比发去的少 5% 以上，不列入这个数据集的表和图：",
           "bullet": "- %s：%s 条记忆存下 %s 条（%s）。", "stores": "%s 库", "pair": "%s 存下 / 发去",
           "basis": "依据", "note": "说明", "failed": "%s / %s（失败 %s）", "processed": "调用模型的配置：数的是处理成功的记忆",
           "no_count": "运行记录里只有失败数，没有入库数", "left_out": "未列入 %s 的表和图", "join": "；", "and": "、"},
    "en": {"title": "Write audit: memories sent and memories stored", "excluded_title": "%s: configurations left out",
           "excluded_intro": "On this dataset these configurations stored over 5% fewer memories than they were sent; "
                             "they are in no table and no chart of this dataset:",
           "bullet": "- %s: %s of %s memories stored (%s).", "stores": "%s stores", "pair": "%s stored / sent",
           "basis": "How established", "note": "Note", "failed": "%s / %s (%s failed)",
           "processed": "calls a model: the count is of memories processed without a failure",
           "no_count": "the record of the run has failures only, no count of what was stored",
           "left_out": "left out of the tables and charts of %s", "join": "; ", "and": ", "},
}
DATASET_NAMES = {"perltqa": "PerLTQA", "locomo": "LoCoMo"}


def systems():
    with open(os.path.join(ROOT, "bench", "systems.json"), encoding="utf-8") as f:
        return json.load(f)


def label(name, lang, registry):
    col = 0 if lang == "zh" else 1
    if name in EXTRA_LABELS:
        return EXTRA_LABELS[name][col]
    entry = registry.get(name)
    return entry["label"][lang] if entry else name


def kind(name, lang, registry):
    key = EXTRA_LABELS[name][2] if name in EXTRA_LABELS else (registry.get(name) or {}).get("kind")
    return KIND[key][0 if lang == "zh" else 1] if key in KIND else "-"


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def metrics_files(dataset, results=None):
    """{configuration: its scored record} from the <configuration>.metrics.json files of a dataset."""
    folder = os.path.join(results or RESULTS, dataset)
    return {n[:-len(".metrics.json")]: load(os.path.join(folder, n)) for n in sorted(os.listdir(folder))
            if n.endswith(".metrics.json") and n.count(".") == 2}


def audited_name(name):
    """The configuration whose write audit covers `name`: a precise twin reads the store of its default."""
    return name[:-len("-precise")] if name.endswith("-precise") else name


def load_audit(results=None):
    """results/write-audit.json, or {} where there is none (then nothing is left out)."""
    path = os.path.join(results or RESULTS, "write-audit.json")
    return load(path) if os.path.exists(path) else {}


def stored_too_little(row, threshold):
    """True when a write-audit row has a count of stored memories and it is short of what was sent by more than
    `threshold` (a fraction). A row without a count (a record with failures only) is never left out by this rule."""
    return bool(row) and row.get("stored") is not None and row["sent"] - row["stored"] > threshold * row["sent"]


def harmonic(a, b):
    return None if a is None or b is None or a + b == 0 else 2 * a * b / (a + b)


def row_of(name, sc):
    """The figures of one scored record (score.score_file), overall and per question type."""
    m = sc["metrics"].get("ALL answerable", {})
    q = sc["metrics"].get("no-answer", {})
    u = sc.get("usage") or {}
    by_type = {t: {k: v.get(k) for k in ("questions", "hit1", "hit5", "hit10", "delivered_hit", "precision", "items")}
               for t, v in sc["metrics"].items() if t not in NOT_A_TYPE}
    return {"system": name, "stores": len(sc["world_ids"]), "store_ids": sc["world_ids"], "answerable": m.get("questions"),
            "hit1": m.get("hit1"), "hit5": m.get("hit5"), "hit10": m.get("hit10"), "delivered_hit": m.get("delivered_hit"),
            "precision": m.get("precision"), "delivered_f1": harmonic(m.get("delivered_hit"), m.get("precision")),
            "items": m.get("items"), "chars": m.get("chars"), "no_answer": q.get("questions"),
            "quiet_rate": q.get("quiet_rate"), "unmapped_items": sc["unmapped_items"], "llm_model": sc.get("llm_model"),
            "by_type": by_type,
            "usage": {k: u.get(k) for k in ("llm_calls", "llm_input_tokens", "llm_cached_input_tokens", "llm_output_tokens",
                                            "llm_reasoning_tokens", "llm_cost_usd", "ingest_seconds", "question_seconds")}}


def build(results=None):
    """Everything the tables and charts need:
    {dataset: {"stores", "full": [rows], "samples": [{"n", "stores", "rows"}], "cost", "latency", "writes",
    "excluded"}}. "writes" is the write audit of the dataset and "excluded" names the configurations that stored
    too little of what they were sent (stored_too_little): they are in none of the other lists."""
    results = results or RESULTS
    audit = load_audit(results)
    threshold = audit.get("threshold", 0.05)
    out = {}
    for dataset in DATASETS:
        scored = metrics_files(dataset, results)
        audited = audit.get(dataset) or {}
        excluded = sorted(name for name in scored if stored_too_little(audited.get(audited_name(name)), threshold))
        writes = [{"system": name, "stores": a["stores"], "sent": a["sent"], "stored": a.get("stored"),
                   "counts": a.get("counts"), "basis": a.get("basis"), "reason": a.get("reason"),
                   "failed": a.get("failed"), "excluded": stored_too_little(a, threshold)}
                  for name, a in audited.items()]
        ids = max((sc["world_ids"] for sc in scored.values()), key=len)
        sampled = {name for _, _, names in SAMPLES[dataset] for name in names}
        full = [row_of(name, sc) for name, sc in scored.items()
                if len(sc["world_ids"]) == len(ids) and name not in excluded]
        unlisted = sorted(name for name, sc in scored.items() if len(sc["world_ids"]) < len(ids) and name not in sampled)
        if unlisted:
            raise SystemExit("%s: partial results that are in no sample group: %s" % (dataset, unlisted))
        samples = []
        for n, label, names in SAMPLES[dataset]:
            names = [name for name in names if name not in excluded]
            if not names:  # nothing left of the group but this project's reference rows
                continue
            same = load(os.path.join(results, dataset, "same-stores-%s.json" % label))
            if same["stores"] != ids[:n]:
                raise SystemExit("%s: same-stores-%s.json is not the first %d stores" % (dataset, label, n))
            rows = []
            for name in names + [r for r in REFERENCE if r not in names]:
                if name not in same["systems"]:
                    raise SystemExit("%s %s does not cover the first %d stores" % (dataset, name, n))
                rows.append(row_of(name, same["systems"][name]))
            samples.append({"n": n, "stores": ids[:n], "rows": rows})
        cost = []
        for name, sc in scored.items():
            if not sc.get("llm_model") or name in excluded:
                continue
            u, memories = sc["usage"], sc["memories"]
            cost.append({"system": name, "model": sc["llm_model"], "thinking": sc.get("llm_thinking"),
                         "stores": len(sc["world_ids"]), "memories": memories, "calls": u["llm_calls"],
                         "input_tokens": u["llm_input_tokens"], "cached_input_tokens": u["llm_cached_input_tokens"],
                         "output_tokens": u["llm_output_tokens"], "reasoning_tokens": u.get("llm_reasoning_tokens"),
                         "usd": u["llm_cost_usd"], "usd_per_1000_memories": 1000 * u["llm_cost_usd"] / memories,
                         "ingest_seconds_per_memory": u["ingest_seconds"] / memories,
                         "failed_stores": sc.get("failed_worlds") or []})
        lat_path = os.path.join(results, dataset, "latency-summary.json")
        latency = load(lat_path) if os.path.exists(lat_path) else None
        if latency:
            latency = {name: v for name, v in latency.items() if name not in excluded}
        out[dataset] = {"stores": ids, "full": full, "samples": samples, "cost": cost, "latency": latency,
                        "writes": writes, "excluded": excluded}
    return out


# ------------------------------------------------------------------------------------------------ markdown

def pct(x):
    return "-" if x is None else "%.1f%%" % (100 * x)


def num(x, digits=2):
    return "-" if x is None else "%.*f" % (digits, x)


def thousands(x):
    return "-" if x is None else format(int(round(x)), ",")


def order(rows):
    """This project's rows first (default profile, then its precise twin), then the others by Hit@5, highest first."""
    def key(r):
        name = r["system"]
        if name.startswith("feiyu-memgate"):
            base = name[:-len("-precise")] if name.endswith("-precise") else name
            return (0, OWN.index(base) if base in OWN else 9, name.endswith("-precise"), 0)
        return (1, 0, False, -(r["hit5"] or 0))
    return sorted(rows, key=key)


def name_cell(row, lang, registry):
    text = label(row["system"], lang, registry)
    own_default = row["system"].startswith("feiyu-memgate") and not row["system"].endswith("-precise")
    return "**%s**" % text if own_default else text


def quality_table(rows, lang, registry, quiet, with_stores=False):
    t = TEXT[lang]
    head = [t["system"], t["kind"]] + ([t["stores"]] if with_stores else []) + [
        t["hit1"], t["hit5"], t["hit10"], t["items"], t["dh"], t["prec"], t["f1"], t["chars"]] + ([t["quiet"]] if quiet else [])
    lines = ["| " + " | ".join(head) + " |", "|---|---|" + "---:|" * (len(head) - 2)]
    for r in rows:
        cells = [name_cell(r, lang, registry), kind(r["system"], lang, registry)] + ([str(r["stores"])] if with_stores else []) + [
            pct(r["hit1"]), pct(r["hit5"]), pct(r["hit10"]), num(r["items"]), pct(r["delivered_hit"]), pct(r["precision"]),
            pct(r["delivered_f1"]), thousands(r["chars"])] + ([pct(r["quiet_rate"])] if quiet else [])
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


TYPES = {  # question types in the order the tables and charts show them: (key, Chinese, English)
    "perltqa": [("profile", "人物档案", "profile"), ("social_relationship", "社会关系", "relationships"),
                ("events", "事件", "events"), ("dialogues", "对话", "dialogues")],
    "locomo": [("4-single-hop", "单跳", "single-hop"), ("1-multi-hop", "多跳", "multi-hop"),
               ("2-temporal", "时间", "temporal"), ("3-open-domain", "开放域", "open-domain")],
}


def type_table(rows, dataset, lang, registry, measure="hit5"):
    """One measure per question type: a column per type, with the number of questions in the header."""
    t = TEXT[lang]
    first = rows[0]["by_type"]
    head = [t["system"], t["kind"]] + [
        "%s (n = %s)" % (zh if lang == "zh" else en, thousands((first.get(key) or {}).get("questions")))
        for key, zh, en in TYPES[dataset]]
    lines = ["| " + " | ".join(head) + " |", "|---|---|" + "---:|" * (len(head) - 2)]
    for r in rows:
        cells = [name_cell(r, lang, registry), kind(r["system"], lang, registry)] + [
            pct((r["by_type"].get(key) or {}).get(measure)) for key, _, _ in TYPES[dataset]]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def cost_table(data, lang, registry):
    t = TEXT[lang]
    head = [t["system"], t["dataset"], t["stores"], t["memories"], t["calls"], t["input"], t["output"], t["usd"],
            t["per1k"], t["spm"]]
    lines = ["| " + " | ".join(head) + " |", "|---|---|" + "---:|" * (len(head) - 2)]
    for dataset in DATASETS:
        for c in sorted(data[dataset]["cost"], key=lambda c: c["system"]):
            lines.append("| %s | %s | %d | %s | %s | %s（%s） | %s | %s | %s | %s |" % (
                label(c["system"], lang, registry), {"perltqa": "PerLTQA", "locomo": "LoCoMo"}[dataset], c["stores"],
                thousands(c["memories"]), thousands(c["calls"]), thousands(c["input_tokens"]),
                thousands(c["cached_input_tokens"]), thousands(c["output_tokens"]), num(c["usd"], 3),
                num(c["usd_per_1000_memories"], 2), num(c["ingest_seconds_per_memory"], 1)))
    text = "\n".join(lines)
    return text if lang == "zh" else text.replace("（", " (").replace("）", ")")


def latency_table(data, lang, registry):
    t = TEXT[lang]
    head = [t["system"]]
    for dataset in DATASETS:
        name = {"perltqa": "PerLTQA", "locomo": "LoCoMo"}[dataset]
        head += ["%s %s" % (name, t["p50"]), "%s %s" % (name, t["p95"])]
    head.append(t["kind_lat"])
    names = []
    for dataset in DATASETS:
        for n in (data[dataset]["latency"] or {}):
            if n not in names:
                names.append(n)
    rows = []
    for n in names:
        cells, kinds, p50 = [label(n, lang, registry)], set(), None
        for dataset in DATASETS:
            v = (data[dataset]["latency"] or {}).get(n)
            cells += [num(v["p50_ms"], 1) + " ms", num(v["p95_ms"], 1) + " ms"] if v else ["-", "-"]
            if v:
                kinds.add(t["idle"] if v["kind"].startswith("sequential") else t["load"])
                p50 = v["p50_ms"] if p50 is None else p50
        own = n.startswith("feiyu-memgate")
        rows.append(((0 if own else 1, p50 or 0), "| " + " | ".join(cells + ["; ".join(sorted(kinds))]) + " |"))
    lines = ["| " + " | ".join(head) + " |", "|---|" + "---:|" * (len(head) - 2) + "---|"]
    return "\n".join(lines + [r for _, r in sorted(rows)])


def excluded_text(d, lang, registry):
    """The configurations left out of a dataset: one bullet each with how much was stored, grouped by the reason,
    which follows its group once."""
    t, col = AUDIT_TEXT[lang], 2 if lang == "zh" else 3
    groups = {}
    for w in d["writes"]:
        if w["system"] in d["excluded"]:
            groups.setdefault(w["reason"], []).append(w)
    lines = [t["excluded_intro"]]
    for reason, rows in groups.items():
        lines.append("")
        for w in sorted(rows, key=lambda w: label(w["system"], lang, registry)):
            counts = (thousands(w["sent"]), thousands(w["stored"])) if lang == "zh" else (
                thousands(w["stored"]), thousands(w["sent"]))
            lines.append(t["bullet"] % ((label(w["system"], lang, registry),) + counts + (pct(w["stored"] / w["sent"]),)))
        lines += ["", REASONS[reason][col]]
    return "\n".join(lines)


def writes_table(data, lang, registry):
    """The write audit: per configuration and dataset the stores, memories stored and sent, how the count was
    established and, where fewer were stored than sent, why."""
    t, col = AUDIT_TEXT[lang], 0 if lang == "zh" else 1
    head = [TEXT[lang]["system"]]
    for dataset in DATASETS:
        head += [t["stores"] % DATASET_NAMES[dataset], t["pair"] % DATASET_NAMES[dataset]]
    head += [t["basis"], t["note"]]
    by_name = {}
    for dataset in DATASETS:
        for w in data[dataset]["writes"]:
            by_name.setdefault(w["system"], {})[dataset] = w
    rows = []
    for name, per in by_name.items():
        cells, notes, basis, left_out = [name_cell({"system": name}, lang, registry)], [], [], []
        for dataset in DATASETS:
            w = per.get(dataset)
            if w is None:
                cells += ["-", "-"]
                continue
            if w["stored"] is None:
                pair = t["failed"] % ("–", thousands(w["sent"]), thousands(w["failed"]))
                extra = t["no_count"]
            else:
                pair = "%s / %s" % (thousands(w["stored"]), thousands(w["sent"]))
                extra = t["processed"] if w["counts"] == "processed" else None
            cells += [str(w["stores"]), pair]
            for note in (REASONS[w["reason"]][col] if w["reason"] else None, extra):
                if note and note not in notes:
                    notes.append(note)
            if BASIS[w["basis"]][col] not in basis:
                basis.append(BASIS[w["basis"]][col])
            if w["excluded"]:
                left_out.append(DATASET_NAMES[dataset])
        if left_out:
            notes.append(t["left_out"] % t["and"].join(left_out))
        own = name.startswith("feiyu-memgate")
        key = (0, OWN.index(name) if name in OWN else 9, "") if own else (1, 0, label(name, lang, registry).lower())
        rows.append((key, "| " + " | ".join(cells + [t["join"].join(basis), t["join"].join(notes)]) + " |"))
    lines = ["| " + " | ".join(head) + " |", "|---|" + "---:|" * (2 * len(DATASETS)) + "---|---|"]
    return "\n".join(lines + [r for _, r in sorted(rows)])


def sample_title(dataset, sample, lang):
    ids = sample["stores"]
    span = ids[0] if len(ids) == 1 else "%s – %s" % (ids[0], ids[-1])
    name = {"perltqa": "PerLTQA", "locomo": "LoCoMo"}[dataset]
    if lang == "zh":
        return ("%s，第 1 个库（%s）" % (name, span)) if len(ids) == 1 else "%s，前 %d 个库（%s）" % (name, len(ids), span)
    return ("%s, the first store (%s)" % (name, span)) if len(ids) == 1 else "%s, the first %d stores (%s)" % (
        name, len(ids), span)


def blocks(data, lang, registry):
    """The markdown tables, in the order the README uses them: [(key, title, table)]."""
    out = []
    for dataset in DATASETS:
        d, quiet = data[dataset], dataset == "locomo"
        name = {"perltqa": "PerLTQA", "locomo": "LoCoMo"}[dataset]
        n = d["full"][0]["answerable"]
        title = ("%s，全部 %d 个库，%s 个可答问题" if lang == "zh" else "%s, all %d stores, %s answerable questions") % (
            name, len(d["stores"]), thousands(n))
        out.append(("%s-full" % dataset, title, quality_table(order(d["full"]), lang, registry, quiet)))
        if d.get("excluded"):
            out.append(("%s-excluded" % dataset, AUDIT_TEXT[lang]["excluded_title"] % name, excluded_text(d, lang, registry)))
        defaults = [r for r in order(d["full"]) if not r["system"].endswith("-precise")]
        title = ("%s，分题型的 Hit@5" if lang == "zh" else "%s, Hit@5 by question type") % name
        out.append(("%s-types" % dataset, title, type_table(defaults, dataset, lang, registry)))
        for s in d["samples"]:
            rows = [r for r in s["rows"] if r["system"] not in REFERENCE] + [r for r in s["rows"] if r["system"] in REFERENCE]
            title = sample_title(dataset, s, lang) + ("，%s 个可答问题" if lang == "zh" else ", %s answerable questions") % (
                thousands(rows[0]["answerable"]))
            out.append(("%s-first%d" % (dataset, s["n"]), title, quality_table(rows, lang, registry, quiet)))
    out.append(("cost", "LLM usage" if lang == "en" else "模型用量", cost_table(data, lang, registry)))
    if any(data[d]["latency"] for d in DATASETS):
        out.append(("latency", "Latency" if lang == "en" else "延迟", latency_table(data, lang, registry)))
    if any(data[d].get("writes") for d in DATASETS):
        out.append(("writes", AUDIT_TEXT[lang]["title"], writes_table(data, lang, registry)))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--readme", choices=("zh", "en"), help="print the README tables instead of writing summary.md")
    parser.add_argument("--date", default=datetime.date.today().isoformat(),
                        help="the date written into the summary and printed under the charts (default: today)")
    args = parser.parse_args(argv)
    data, registry = build(), systems()
    if args.readme:
        sys.stdout.reconfigure(encoding="utf-8")
        for key, title, table in blocks(data, args.readme, registry):
            print("<!-- %s -->\n**%s**\n\n%s\n" % (key, title, table))
        return 0
    with open(os.path.join(RESULTS, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(dict({"generated": args.date}, **data), f, indent=1)  # "generated" is the only key that is no dataset
        f.write("\n")
    text = ["# External datasets: summary\n", "Generated on %s by `bench/external/report.py` from the metrics files of "
            "this folder; definitions in `bench/external/score.py`. Rows of this project in bold are the default "
            "profile.\n" % args.date]
    for key, title, table in blocks(data, "en", registry):
        text.append("## %s\n\n%s\n" % (title, table))
    with open(os.path.join(RESULTS, "summary.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(text))
    print("wrote results/summary.md and results/summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
