"""Charts of the public-dataset benchmark (PerLTQA and LoCoMo). Standard library only; writes static SVG.

    python bench/external/charts.py [--out DIR]

Reads bench/external/results/summary.json (written by report.py), bench/results/summary.json (this project's own
question set), and bench/systems.json, and writes docs/assets/charts/ext-<name>.<lang>.<theme>.svg for every chart,
language (zh, en) and theme (light, dark). The PNGs the READMEs embed are rendered from these files by
bench/render_charts.sh.

The building blocks and the visual conventions are those of bench/charts.py (it is loaded as a module): the same
palette, fonts, margins, row height, legend and footer; this project's configurations in the palette's accent blue,
every other project in its neutral grey; text never wears a series colour. No figure is typed here: every number
drawn comes from the three input files.

Heatmaps (overview, by-type): one sequential ramp from the chart surface to a blue of the own-project hue, one
ramp per theme (the dark theme has its own, it is not the light one inverted). The text of a cell is the palette's
dark or light ink, whichever contrasts more with the cell, and the contrast is at least 4.5:1: where the ramp passes
through the narrow band of backgrounds for which neither ink reaches that, the cell takes the nearest ramp step
outside the band (a shift of a few thousandths of the scale).
"""
import argparse
import importlib.util
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def _load(name, *parts):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *parts))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


charts = _load("bench_charts", "bench", "charts.py")
report = _load("bench_external_report", "bench", "external", "report.py")

EXT_SUMMARY_PATH = os.path.join(ROOT, "bench", "external", "results", "summary.json")
OWN_SUMMARY_PATH = os.path.join(ROOT, "bench", "results", "summary.json")
SYSTEMS_PATH = os.path.join(ROOT, "bench", "systems.json")
OUT_DIR = os.path.join(ROOT, "docs", "assets", "charts")
SOURCE = "bench/external/results/summary.md"

LANGS = charts.LANGS
THEMES = charts.THEMES
CHARTS = ("overview", "ranking", "delivered", "by-type", "thresholds", "cost", "latency", "samples")

PALETTE, W, MARGIN = charts.PALETTE, charts.W, charts.MARGIN
SIZE_LABEL, SIZE_SMALL = charts.SIZE_LABEL, charts.SIZE_SMALL
ROW_H, BAR_H, LABEL_X, TINT = charts.ROW_H, charts.BAR_H, charts.LABEL_X, charts.TINT
text_width, pct = charts.text_width, charts.pct

DATASET_NAMES = {"perltqa": "PerLTQA", "locomo": "LoCoMo"}
LABEL_W = 200                   # width of the column of row labels (x from LABEL_X)
MODEL_ORDER = ("deepseek-flash", "claude-haiku-5-5")   # legend order of the two models of the cost chart
MODEL_SLOTS = (1, 2)            # palette["arm"] slots of the two models: slot 0 is the own-project blue
BASE_ORDER = ("mem0-llm", "graphiti", "hindsight-llm", "dsh-fish-memory-expand")
BASE_LABELS = {                 # the system a cost row stands for, without the model suffix
    "mem0-llm": ("mem0（LLM 提取）", "mem0 (LLM extraction)"),
    "graphiti": ("Graphiti", "Graphiti"),
    "hindsight-llm": ("Hindsight", "Hindsight"),
    "dsh-fish-memory-expand": ("dsh-fish-memory 扩词", "dsh-fish-memory expansion"),
}
RAMP_END = {"light": "#2670cc", "dark": "#2d64b6"}   # full end of the sequential ramp, per theme

TXT = {
    "zh": {
        "own_set": "自有题库", "own_set_n": "%d 个可答问题，%d 个无答案问题",
        "ds_sub": "%d 个库，%s 个可答问题", "ds_sub_na": "%d 个库，%s 个可答问题，%s 个无答案问题",
        "t_overview": "三个数据集上的检索排名和默认交付：Hit@5、交付命中、每题交付条数",
        "g_hit5": "Hit@5", "g_dh": "交付命中", "g_items": "平均交付条数",
        "ramp_note": "格子的颜色表示取值，0% 到 100% 用同一条色阶",
        "overview_note": "每个系统都按默认设置运行，只列出不调用大模型、三个数据源里都有的配置。自有题库的可答问题 %d 个。",
        "t_ranking": "检索排名：PerLTQA 与 LoCoMo",
        "hit1_tick": "刻度：Hit@1", "hit10_tint": "浅色：Hit@10",
        "ranking_note": "柱：Hit@5，浅色延伸到 Hit@10，刻度：Hit@1。系统的顺序按 PerLTQA 的 Hit@5 排，两栏相同，便于对照同一行。",
        "t_delivered": "默认交付：每题交付条数与交付命中",
        "delivered_x": "每题平均交付条数（对数轴）", "delivered_y": "交付命中",
        "hollow_precise": "空心：精确档",
        "delivered_note": "越靠左上，交得越少而命中越高。实心是默认（宽松）档，空心是同一配置固定为精确档，箭头指向默认档。",
        "t_bytype": "分题型的 Hit@5",
        "types": {"profile": "人物档案", "social_relationship": "社会关系", "events": "事件", "dialogues": "对话",
                  "4-single-hop": "单跳", "1-multi-hop": "多跳", "2-temporal": "时间", "3-open-domain": "开放域"},
        "bytype_note": "颜色表示 Hit@5，0% 到 100% 用同一条色阶；行的顺序与排名图相同。每个类型下面是该类型的可答问题数。",
        "t_thresholds": "本项目两档在三个数据集上的表现",
        "m_items": "平均交付条数", "m_dh": "交付命中", "m_quiet": "安静率",
        "precise_hollow": "空心：精确档", "default_filled": "实心：默认档",
        "thresholds_note": "PerLTQA 没有无答案的问题，安静率没有定义，所以那一栏画“—”。"
                           "分数线是在本项目自己的开发集（中文短句）上选的，没有为这两个数据集调整。",
        "t_cost": "调用大模型的配置：每千条记忆的写入花费（美元，对数轴）",
        "cost_x": "每千条记忆的写入花费（美元，对数轴）",
        "c_calls": "每条记忆的调用次数", "c_stores": "运行的库数", "c_order": "两个模型，顺序同图例",
        "cost_note": "按 2026-11-16 的公开标价计算，两个模型都关闭了思考模式；只含有效的运行。",
        "cost_below": "低于 0.01 的点画在坐标轴上。",
        "t_latency": "一次召回的延迟（毫秒，对数轴）",
        "lat_sub": "库 %s，%s 条记忆", "lat_stem": "点：p50，短竖线：p95",
        "lat_hollow": "空心：并行运行时测得，有其他负载",
        "latency_note": "每个数据集取记忆数居中的一个库，空闲机器上一题一题顺序提问；用向量的配置每次召回都真实请求一次向量模型。",
        "t_samples": "抽样运行的配置，与本项目在同一批库上的结果",
        "s_dh": "交付命中", "s_items": "平均交付条数", "s_questions": "%s 个可答问题",
        "samples_note": "柱：Hit@5，浅色延伸到 Hit@10，刻度：Hit@1。每组里的所有配置都只在那几个库上评分，本项目的 sqlite 和 hybrid 也是。",
        "source": "%s · PerLTQA %d 个库、%s 个问题，LoCoMo %d 个库、%s 个问题 · %s",
    },
    "en": {
        "own_set": "own question set", "own_set_n": "%d answerable and %d no-answer questions",
        "ds_sub": "%d stores, %s answerable questions",
        "ds_sub_na": "%d stores, %s answerable and %s no-answer questions",
        "t_overview": "Ranking and default delivery on three datasets: Hit@5, delivered hit, items per question",
        "g_hit5": "Hit@5", "g_dh": "delivered hit", "g_items": "items per question",
        "ramp_note": "cell colour shows the value, one ramp from 0% to 100%",
        "overview_note": "Every system at its default settings; only configurations without a language model that exist in all three sources are listed. Own question set: %d answerable questions.",
        "t_ranking": "Retrieval ranking: PerLTQA and LoCoMo",
        "hit1_tick": "tick: Hit@1", "hit10_tint": "light: Hit@10",
        "ranking_note": "Bar: Hit@5, light extension to Hit@10, tick: Hit@1. Systems are ordered by PerLTQA Hit@5 and the order is the same in both panels, so a row can be followed across.",
        "t_delivered": "Default delivery: items per question and delivered hit",
        "delivered_x": "items delivered per question (log scale)", "delivered_y": "delivered hit",
        "hollow_precise": "hollow: precise profile",
        "delivered_note": "The nearer the upper left, the fewer items delivered and the higher the hit. Filled: the default (broad) profile; hollow: the same configuration pinned to the precise profile; the arrow points to the default.",
        "t_bytype": "Hit@5 by question type",
        "types": {"profile": "profile", "social_relationship": "relationships", "events": "events",
                  "dialogues": "dialogues", "4-single-hop": "single-hop", "1-multi-hop": "multi-hop",
                  "2-temporal": "temporal", "3-open-domain": "open-domain"},
        "bytype_note": "Colour shows Hit@5, one ramp from 0% to 100%; rows are in the order of the ranking chart. Under each type: its number of answerable questions.",
        "t_thresholds": "This project's two profiles on three datasets",
        "m_items": "items per question", "m_dh": "delivered hit", "m_quiet": "quiet rate",
        "precise_hollow": "hollow: precise profile", "default_filled": "filled: default profile",
        "thresholds_note": "PerLTQA has no no-answer questions, so its quiet rate is not defined and shows a dash. "
                           "The score thresholds were chosen on this project's own development set (short Chinese sentences) and were not tuned for these two datasets.",
        "t_cost": "Configurations that call a language model: write cost per 1,000 memories (USD, log scale)",
        "cost_x": "write cost per 1,000 memories (USD, log scale)",
        "c_calls": "calls per memory", "c_stores": "stores run", "c_order": "two models, legend order",
        "cost_note": "Computed at the public list prices of 2026-11-16; thinking mode was off for both models; valid runs only.",
        "cost_below": "A point below 0.01 is drawn on the axis.",
        "t_latency": "Latency of one recall (ms, log scale)",
        "lat_sub": "store %s, %s memories", "lat_stem": "dot: p50, short bar: p95",
        "lat_hollow": "hollow: measured during the parallel runs, under load",
        "latency_note": "One store per dataset, the one with the median number of memories; questions asked one at a time on an idle machine; a configuration that uses vectors makes a real request to the embedding model on every recall.",
        "t_samples": "Sampled configurations next to this project on the same stores",
        "s_dh": "delivered hit", "s_items": "items per question", "s_questions": "%s answerable questions",
        "samples_note": "Bar: Hit@5, light extension to Hit@10, tick: Hit@1. Every configuration of a group is scored on those stores only, this project's sqlite and hybrid included.",
        "source": "%s · PerLTQA %d stores, %s questions; LoCoMo %d stores, %s questions · %s",
    },
}


# ------------------------------------------------------------------ colours (heatmap ramp, contrast)

def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in rgb)


def _linear(v):
    v /= 255.0
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def luminance(color):
    r, g, b = (_linear(v) for v in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def ramp_color(theme, t):
    """The sequential ramp: the chart surface at 0, the theme's blue at 1, a straight blend between."""
    a, b = _rgb(PALETTE[theme]["surface"]), _rgb(RAMP_END[theme])
    t = min(max(t, 0.0), 1.0)
    return _hex([x + (y - x) * t for x, y in zip(a, b)])


def cell_colors(theme, value):
    """(fill, ink) of a heatmap cell: the palette's dark or light ink, at least 4.5:1 on the fill."""
    pal = PALETTE[theme]
    inks = (pal["ink"], pal["surface"])
    for k in range(0, 401):
        for sign in ((0,) if k == 0 else (-1, 1)):
            fill = ramp_color(theme, value + sign * k * 0.0025)
            ink = max(inks, key=lambda c: contrast(c, fill))
            if contrast(ink, fill) >= 4.5:
                return fill, ink
    raise ValueError("no ink reaches 4.5:1 on the ramp")


# ------------------------------------------------------------------ data helpers

def is_own(name):
    return name.startswith("feiyu-memgate")


def is_precise(name):
    return name.endswith("-precise")


def thousands(n):
    return format(int(round(n)), ",")


def label_for(name, lang, registry, short=False):
    text = report.label(name, lang, registry)
    if short:
        text = text.replace("feiyu-memgate ", "").replace("LLM 提取，", "").replace("LLM extraction, ", "")
    return text


def full_rows(ext, dataset, with_precise=False):
    """{system: row} of the configurations that ran on every store of a dataset."""
    rows = (ext.get(dataset) or {}).get("full") or []
    return {r["system"]: r for r in rows if with_precise or not is_precise(r["system"])}


def ranked_names(ext):
    """The row order of the ranking-type charts: this project's three, then the others by PerLTQA Hit@5, highest
    first (a system missing from PerLTQA follows by its LoCoMo Hit@5)."""
    rows = list(full_rows(ext, "perltqa").values())
    seen = {r["system"] for r in rows}
    rows += [r for r in full_rows(ext, "locomo").values() if r["system"] not in seen]
    return [r["system"] for r in report.order(rows)]


def own_charted(own):
    return {s["system"]: s for s in charts.charted(own)}


def own_question_counts(own):
    types = own["dataset"]["types"]
    return sum(v for k, v in types.items() if k != "quiet"), types.get("quiet", 0)


def own_values(rec):
    """(Hit@5, delivered hit, items per question, quiet rate) of a record of the own question set."""
    o = rec["overall"]
    return o.get("hit5"), o.get("deployed_hit"), o.get("avg_items"), (rec["by_type"].get("quiet") or {}).get("quiet_rate")


def ext_values(row):
    return row.get("hit5"), row.get("delivered_hit"), row.get("items"), row.get("quiet_rate")


def dataset_subtitle(ext, dataset, t):
    d = ext[dataset]
    rows = d["full"]
    n, na = rows[0]["answerable"], rows[0].get("no_answer")
    if na:
        return t["ds_sub_na"] % (len(d["stores"]), thousands(n), thousands(na))
    return t["ds_sub"] % (len(d["stores"]), thousands(n))


def overview_rows(ext, own):
    """The configurations without a language model that exist in the own question set, PerLTQA and LoCoMo:
    [(name, own record, PerLTQA row, LoCoMo row)], this project's first, then by the mean of the three Hit@5."""
    base, perltqa, locomo = own_charted(own), full_rows(ext, "perltqa"), full_rows(ext, "locomo")
    names = [n for n in base if n in perltqa and n in locomo and not base[n].get("llm")
             and not perltqa[n].get("llm_model") and not locomo[n].get("llm_model")]

    def key(n):
        mean = sum(v or 0.0 for v in (base[n]["overall"].get("hit5"), perltqa[n]["hit5"], locomo[n]["hit5"])) / 3.0
        if is_own(n):
            return (0, report.OWN.index(n) if n in report.OWN else 9, 0.0, n)
        return (1, 0, -mean, n)
    return [(n, base[n], perltqa[n], locomo[n]) for n in sorted(names, key=key)]


# ------------------------------------------------------------------ drawing helpers

def new_doc(theme, lang, height=400):
    return charts.new_doc(None, theme, lang, height)


def legend(doc, title, entries):
    """The title and a row of legend entries (kind, text[, colour]). Kinds: fa, fo, tint (bar swatches), tick,
    ring (hollow dot), dot (filled dot in a colour), stem (dot, line and short bar)."""
    doc.text(MARGIN, charts.TITLE_Y, title, "t")
    x, y = MARGIN, charts.TITLE_Y + 24
    pal = doc.pal
    for entry in entries:
        kind, text = entry[0], entry[1]
        if kind == "tick":
            doc.line(x + 6, y - 11, x + 6, y + 1, "tick")
            x += 18
        elif kind == "ring":
            doc.add('<circle cx="%.1f" cy="%.1f" r="5" fill="%s" stroke="%s" stroke-width="2.5"/>' % (
                x + 6, y - 4.5, pal["surface"], entry[2] if len(entry) > 2 else pal["neutral"]))
            x += 20
        elif kind == "dot":
            doc.circle(x + 6, y - 4.5, 5, entry[2])
            x += 20
        elif kind == "stem":
            color = entry[2]
            doc.line(x + 6, y - 4.5, x + 30, y - 4.5, "lead", color=color)
            doc.line(x + 30, y - 9, x + 30, y, "lead", width=1.5, color=color)
            doc.circle(x + 6, y - 4.5, 5, color)
            x += 40
        else:
            doc.add('<rect x="%.1f" y="%.1f" width="14" height="8" rx="2" class="%s"/>' % (x, y - 9, kind))
            x += 22
        doc.text(x, y, text, "s")
        x += text_width(text, SIZE_SMALL) + 22
    return y


def family_entries(doc):
    return charts.family_legend(doc.t)


def ink_text(doc, x, y, content, color, anchor="middle", cls="l"):
    doc.add('<text x="%.1f" y="%.1f" class="%s" text-anchor="%s" style="fill:%s">%s</text>' % (
        x, y, cls, anchor, color, charts.esc(content)))


def name_label(doc, name, yc, registry, x=LABEL_X, max_w=LABEL_W, short=False):
    """A row label; a long one wraps onto two lines the way bench/charts.py does it."""
    label = label_for(name, doc.lang, registry, short)
    lines = charts.label_lines(label, max_w)
    if len(lines) == 1:
        doc.text(x, yc + 4.5, label, "l")
    else:
        doc.text(x, yc - 1.5, lines[0], "l")
        doc.text(x, yc + 10.5, lines[1], "s")


def family_swatch(doc, x, yc, own):
    doc.add('<rect x="%.1f" y="%.1f" width="5" height="14" rx="2" class="%s"/>' % (x, yc - 7, "fa" if own else "fo"))


def fill_of(doc, name):
    return doc.pal["accent"] if is_own(name) else doc.pal["neutral"]


def source_text(ext, lang):
    p, l = ext["perltqa"], ext["locomo"]
    date = ext.get("generated") if isinstance(ext.get("generated"), str) else None
    fmt = TXT[lang]["source"]
    if date is None:             # the summary carries no date: the line ends after the counts
        fmt = fmt[:-len(" · %s")]
    args = (SOURCE, len(p["stores"]), thousands(p["full"][0]["answerable"]),
            len(l["stores"]), thousands(l["full"][0]["answerable"]))
    return fmt % (args + ((date,) if date else ()))


def footer(doc, ext, y, note=None):
    """The wrapped note (if any) and the source line, as bench/charts.py does; sets the document height."""
    for line in charts.note_lines(note) if note else ():
        doc.text(MARGIN, y, line, "s")
        y += 18
    doc.text(MARGIN, y, source_text(ext, doc.lang), "s")
    doc.h = int(y + 16)


def heat_cell(doc, x, y, w, h, value, text):
    """A filled cell with the value printed in it; 2 units of surface between neighbours."""
    fill, ink = cell_colors(doc.theme, value)
    doc.add('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="3" fill="%s"/>' % (x + 1, y + 1, w - 2, h - 2, fill))
    ink_text(doc, x + w / 2.0, y + h / 2.0 + 4.5, text, ink)


def ramp_legend(doc, x_right, y):
    """0% - strip - 100% with a 50% label under it, its right edge at x_right."""
    steps, step_w = 11, 12
    width = steps * step_w
    x = x_right - width
    for k in range(steps):
        doc.add('<rect x="%.1f" y="%.1f" width="%d" height="8" fill="%s"/>' % (
            x + k * step_w, y - 9, step_w, ramp_color(doc.theme, k / float(steps - 1))))
    doc.add('<rect x="%.1f" y="%.1f" width="%d" height="8" fill="none" class="axis"/>' % (x, y - 9, width))
    doc.text(x - 6, y, "0%", "s", "end")
    doc.text(x_right + 6, y, "100%", "s")
    doc.text(x + width / 2.0, y + 14, "50%", "s", "middle")


def short_type(doc, name, width):
    return "l" if text_width(name, SIZE_LABEL) <= width else "s"


# ------------------------------------------------------------------ 1. overview (heatmap)

def chart_overview(ext, own, systems, theme, lang):
    t = TXT[lang]
    rows = overview_rows(ext, own)
    top, cw = 142, 78
    plot_h = ROW_H * len(rows)
    doc = new_doc(theme, lang)
    legend(doc, t["t_overview"], family_entries(doc))
    ramp_legend(doc, W - MARGIN - 40, charts.TITLE_Y + 24)
    x0, gap = 236, 14
    groups = ((t["g_hit5"], True), (t["g_dh"], True), (t["g_items"], False))
    sets = ((t["own_set"], 0), ("PerLTQA", 1), ("LoCoMo", 2))
    for g, (gtitle, rate) in enumerate(groups):
        gx = x0 + g * (3 * cw + gap)
        doc.text(gx + 3 * cw / 2.0, top - 50, gtitle, "lb", "middle")
        doc.line(gx + 4, top - 42, gx + 3 * cw - 4, top - 42, "axis")
        for j, (stitle, _) in enumerate(sets):
            doc.text(gx + j * cw + cw / 2.0, top - 22, stitle, "s", "middle")
    for i, (name, base, perltqa, locomo) in enumerate(rows):
        y = top + i * ROW_H
        yc = y + ROW_H / 2.0
        family_swatch(doc, LABEL_X, yc, is_own(name))
        name_label(doc, name, yc, systems, LABEL_X + 12, LABEL_W - 12)
        sources = (own_values(base), ext_values(perltqa), ext_values(locomo))
        for g, (_, rate) in enumerate(groups):
            gx = x0 + g * (3 * cw + gap)
            for j, vals in enumerate(sources):
                v = vals[g]
                cx = gx + j * cw
                if v is None:
                    ink_text(doc, cx + cw / 2.0, yc + 4.5, "–", doc.pal["ink2"], cls="s")
                elif rate:
                    heat_cell(doc, cx, y, cw, ROW_H, v, pct(v))
                else:
                    ink_text(doc, cx + cw / 2.0, yc + 4.5, "%.2f" % v, doc.pal["ink"])
    footer(doc, ext, top + plot_h + 28, t["overview_note"] % own_question_counts(own)[0])
    return doc.render()


# ------------------------------------------------------------------ shared: a row of the ranking charts

def ranking_marks(doc, x0, scale, yc, row, fill):
    """Hit@10 as the light extension, Hit@5 as the bar, Hit@1 as the tick; the Hit@5 value after the marks."""
    hit1, hit5, hit10 = row.get("hit1") or 0.0, row.get("hit5") or 0.0, row.get("hit10") or 0.0
    doc.bar(x0, yc - BAR_H / 2, hit10 * scale, BAR_H, fill, TINT)
    doc.bar(x0, yc - BAR_H / 2, hit5 * scale, BAR_H, fill)
    x1 = x0 + hit1 * scale
    doc.line(x1, yc - BAR_H / 2 - 3, x1, yc + BAR_H / 2 + 3, "tick")
    end = x0 + max(hit1, hit5, hit10) * scale + 8
    doc.text(end, yc + 4.5, pct(hit5), "lb")
    return end + text_width(pct(hit5), SIZE_LABEL)


def ranking_legend(doc, t):
    return family_entries(doc) + [("tick", t["hit1_tick"]), ("tint", t["hit10_tint"])]


# ------------------------------------------------------------------ 2. ranking

def chart_ranking(ext, own, systems, theme, lang):
    t = TXT[lang]
    names = ranked_names(ext)
    top = 112
    plot_h = ROW_H * len(names)
    doc = new_doc(theme, lang)
    legend(doc, t["t_ranking"], ranking_legend(doc, t))
    scale = 280.0
    panels = (("perltqa", 240.0), ("locomo", 608.0))
    for dataset, x0 in panels:
        doc.text(x0, top - 36, DATASET_NAMES[dataset], "lb")
        doc.text(x0, top - 20, dataset_subtitle(ext, dataset, t), "s")
        charts.percent_axis(doc, x0, scale, top - 6, top + plot_h, label_y=top + plot_h + 18)
    rows = {d: full_rows(ext, d) for d, _ in panels}
    for i, name in enumerate(names):
        yc = top + i * ROW_H + ROW_H / 2.0
        name_label(doc, name, yc, systems)
        for dataset, x0 in panels:
            row = rows[dataset].get(name)
            if row is None:
                doc.text(x0 + 4, yc + 4, "–", "s")
            else:
                ranking_marks(doc, x0, scale, yc, row, fill_of(doc, name))
    footer(doc, ext, top + plot_h + 50, t["ranking_note"])
    return doc.render()


# ------------------------------------------------------------------ 3. delivered (two scatter panels, stacked)

_PLACED = {}
MAX_LEADER = 40.0               # a leader line is allowed only when no adjacent position is free, and then it is short


def _cross(a, b):
    """True when the segments a and b (x1, y1, x2, y2) cross."""
    def side(px, py, qx, qy, rx, ry):
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)
    d1 = side(*a, b[0], b[1])
    d2 = side(*a, b[2], b[3])
    d3 = side(*b, a[0], a[1])
    d4 = side(*b, a[2], a[3])
    return d1 * d2 < 0 and d3 * d4 < 0


def place_labels(points, obstacles, bounds, size=SIZE_SMALL, dot_r=6.0, dots=None, max_leader=MAX_LEADER,
                 passes=3, tries=24, bad=None, lines=()):
    """Label boxes for a scatter, next to the dots: 24 directions at six distances (9 to 36 units). A label that is
    not touching its dot gets a leader line, never longer than `max_leader`, and the cost forbids a leader or a label
    that crosses another leader, label, dot or obstacle (arrow). Every label is placed again a few times with the
    others in place, from several starting orders, and the cheapest arrangement is kept. `dots` are the boxes of the
    dots to keep clear (default: those of `points`). The indices of the labels that still clash are appended to
    `bad` when it is a list. `lines` are segments (x1, y1, x2, y2), the arrows, that no leader may cross. Returns, in the order of `points`, (x_text, y_baseline, anchor, box, leader), where
    leader is the point on the box edge the line from the dot runs to, or None."""
    if dots is None:
        dots = [(x - dot_r, y - dot_r, x + dot_r, y + dot_r) for x, y, _ in points]
    key = (tuple(points), tuple(obstacles), tuple(bounds), size, dot_r, tuple(dots), max_leader, passes, tries, tuple(lines))
    if key not in _PLACED:
        _PLACED[key] = _place(points, obstacles, bounds, size, dots, max_leader, passes, tries, lines)
    result, clashing = _PLACED[key]
    if bad is not None:
        bad.extend(clashing)
    return result


def _place(points, obstacles, bounds, size, dots, max_leader, passes, tries, lines):
    h = size + 3
    n = len(points)
    widths = [text_width(p[2], size) for p in points]

    def candidates(i):
        x, y, _ = points[i]
        w = widths[i]
        for rank, dist in enumerate((9, 12, 16, 22, 29, 36)):
            for k in range(24):
                ang = k * math.pi / 12
                dx, dy = math.cos(ang) * dist, math.sin(ang) * dist
                if dx > 0.35 * dist:
                    anchor, x0, by = "start", x + dx, y + dy - h / 2
                elif dx < -0.35 * dist:
                    anchor, x0, by = "end", x + dx - w, y + dy - h / 2
                else:
                    anchor, x0, by = "middle", x + dx - w / 2, (y + dy - h if dy < 0 else y + dy)
                yield anchor, (x0, by, x0 + w, by + h), rank + k * 0.01

    def anchor_x(anchor, box):
        return box[0] if anchor == "start" else box[2] if anchor == "end" else (box[0] + box[2]) / 2

    def near(dot, x, y):
        """A dot of the label's own group (its own, or one that coincides with it) does not block its leader."""
        return math.hypot((dot[0] + dot[2]) / 2 - x, (dot[1] + dot[3]) / 2 - y) < 9.0

    def cost_of(i, box, rank, others, limit=float("inf")):
        """(cost, leader end); the sum is abandoned as soon as it passes `limit` (the best so far)."""
        x, y, _ = points[i]
        cost = rank * 0.3
        if box[0] < bounds[0] or box[2] > bounds[2] or box[1] < bounds[1] or box[3] > bounds[3]:
            return 1e5, None
        leader = charts._leader_end(x, y, box)
        if leader and math.hypot(leader[0] - x, leader[1] - y) > max_leader:
            return 1e6, None
        cost += sum(charts._overlap(box, c[1]) for _, c in others) * 50
        cost += sum(charts._overlap(box, d, 1.0) for d in dots) * 50
        cost += sum(charts._overlap(box, o, 0.0) for o in obstacles) * 400
        cost += sum(charts._segment_in_box(c[2], box, 2.0, 8) for _, c in others if c[2]) * 3000
        if leader and cost < limit:
            seg = (x, y) + leader
            cost += 3 + math.hypot(leader[0] - x, leader[1] - y) * 1.6
            cost += sum(charts._segment_in_box(seg, c[1], 2.0, 12) for _, c in others) * 3000
            cost += sum(_cross(seg, c[2]) for _, c in others if c[2]) * 2000
            cost += sum(_cross(seg, ln) for ln in lines) * 3000
            cost += sum(charts._segment_in_box(seg, o, 0.0, 8) for o in obstacles) * 3000
            cost += sum(1 for d in dots if not near(d, x, y) and charts._segment_in_box(seg, d, 1.0, 16) > 1) * 800
        return cost, leader

    def run(order):
        chosen = [None] * n
        for _ in range(passes):
            for i in order:
                others = [(j, chosen[j]) for j in range(n) if j != i and chosen[j]]
                best = None
                for anchor, box, rank in candidates(i):
                    cost, leader = cost_of(i, box, rank, others, best[0] if best else float("inf"))
                    if best is None or cost < best[0]:
                        best = (cost, anchor, box, (points[i][0], points[i][1]) + leader if leader else None, leader)
                chosen[i] = best[1:]
        costs = [cost_of(i, c[1], 0, [(j, chosen[j]) for j in range(n) if j != i])[0] for i, c in enumerate(chosen)]
        return sum(costs), chosen, costs

    crowded = sorted(range(n), key=lambda i: -sum(1 for j in range(n) if j != i and abs(points[j][0] - points[i][0]) < 90
                                                  and abs(points[j][1] - points[i][1]) < 30))
    orders = [crowded, crowded[::-1]]
    for seed in range(tries - 2):
        shuffled = list(range(n))
        random.Random(seed).shuffle(shuffled)
        orders.append(shuffled)
    _, chosen, costs = min((run(order) for order in orders), key=lambda r: r[0])
    placed = [(anchor_x(c[0], c[1]), c[1][1] + size, c[0], c[1], c[3]) for c in chosen]
    def clashes(i):
        """Whether label i truly clashes: leaves the bounds, overlaps another label, a dot or an obstacle, or has a
        leader that crosses another leader, a label, a dot or an obstacle (no padding: touching is not a clash)."""
        _, box, seg, leader = chosen[i]
        if box[0] < bounds[0] or box[2] > bounds[2] or box[1] < bounds[1] or box[3] > bounds[3]:
            return True
        others = [chosen[j] for j in range(n) if j != i]
        if any(charts._overlap(box, c[1], 0.0) > 0 for c in others):
            return True
        if any(charts._overlap(box, d, -1.5) > 0 for d in dots) or any(charts._overlap(box, o, 0.0) > 0 for o in obstacles):
            return True
        if seg:
            if any(charts._segment_in_box(seg, c[1], 0.0, 24) for c in others):
                return True
            if any(c[2] and _cross(seg, c[2]) for c in others) or any(_cross(seg, ln) for ln in lines):
                return True
            if any(charts._segment_in_box(c[2], box, 0.0, 24) for c in others if c[2]):
                return True
            if any(charts._segment_in_box(seg, o, 0.0, 24) for o in obstacles):
                return True
            if any(charts._segment_in_box(seg, d, 0.0, 24) > 1 for d in dots if not near(d, points[i][0], points[i][1])):
                return True
        return False

    return placed, [i for i in range(n) if clashes(i)]


def place_with_merging(points, obstacles, bounds, dots, same_family=None, reach=45.0, lines=()):
    """Place the labels of `points`; a dot whose label cannot sit next to it without a clash or a long leader is
    merged with its nearest neighbour of the same family (within `reach` units) into one label ("a / b", at the
    middle of the two), and the labels are placed again. A dot with no such neighbour keeps its best placement.
    Returns (points as labelled, placements)."""
    pts = list(points)
    while True:
        bad = []
        placed = place_labels(pts, obstacles, bounds, dots=dots, bad=bad, lines=lines)
        pair = None
        for i in bad:
            near = [k for k in range(len(pts)) if k != i
                    and math.hypot(pts[k][0] - pts[i][0], pts[k][1] - pts[i][1]) <= reach
                    and (same_family is None or same_family(pts[k][2]) == same_family(pts[i][2]))]
            if near:
                pair = (i, min(near, key=lambda k: math.hypot(pts[k][0] - pts[i][0], pts[k][1] - pts[i][1])))
                break
        if pair is None:
            placed = list(placed)
            for i in bad:       # no neighbour to merge with: place this label again, the others as fixed obstacles
                fixed = list(obstacles)
                for j, (_, _, _, box, leader) in enumerate(placed):
                    if j == i:
                        continue
                    fixed.append(box)
                    if leader:
                        x, y = pts[j][0], pts[j][1]
                        steps = max(2, int(math.hypot(leader[0] - x, leader[1] - y) / 4))
                        fixed += [(x + (leader[0] - x) * k / steps - 1, y + (leader[1] - y) * k / steps - 1,
                                   x + (leader[0] - x) * k / steps + 1, y + (leader[1] - y) * k / steps + 1)
                                  for k in range(steps + 1)]
                again_bad = []
                again = place_labels([pts[i]], fixed, bounds, dots=dots, bad=again_bad, lines=lines)
                if not again_bad:
                    placed[i] = again[0]
            return pts, placed
        i, j = pair
        a, b = pts[min(i, j)], pts[max(i, j)]
        text = charts.merge_coincident([a, b], 1e9)[0][2]
        merged = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, text)
        pts = [p for k, p in enumerate(pts) if k not in (i, j)] + [merged]


def merge_labels(points, within=7.0, widest=420.0):
    """bench/charts.py's merge_coincident, except that a shared label never grows wider than `widest`: a long list
    of names would not fit beside the dots, so the dots beyond it keep a label of their own."""
    merged = []
    for point in points:
        for k, (mx, my, mtext) in enumerate(merged):
            if math.hypot(point[0] - mx, point[1] - my) > within:
                continue
            both = charts.merge_coincident([(mx, my, mtext), point], within)
            if len(both) == 1 and text_width(both[0][2], SIZE_SMALL) <= widest:
                merged[k] = both[0]
                break
        else:
            merged.append(point)
    return merged


DELIVERED_BOX = {"left": 80.0, "right": 940.0, "top": 130.0, "plot_h": 400.0, "gap": 82.0}


def delivered_layout(ext, systems, lang):
    """{dataset: panel} where a panel holds the mapping functions px and py, the rows drawn, the arrows
    (x1, y1, x2, y2) from the precise dot to the default dot, the obstacles, and the labels as placed:
    `points` (x, y, text) and `placement` (x_text, y_baseline, anchor, box, leader) in the same order."""
    names = ranked_names(ext)
    box = DELIVERED_BOX
    out = {}
    for n, dataset in enumerate(DATASET_NAMES):
        top = box["top"] + n * (box["plot_h"] + box["gap"])
        left, right, plot_h = box["left"], box["right"], box["plot_h"]
        rows = full_rows(ext, dataset, with_precise=True)

        def px(v, left=left, right=right):
            return min(charts.log_pos(v, 1.0, 32.0, left, right - left), right)

        def py(v, top=top, plot_h=plot_h):
            return top + plot_h - v * plot_h

        drawn = [rows[k] for k in names if k in rows and rows[k].get("items") and rows[k].get("delivered_hit") is not None]
        arrows, obstacles = [], []
        for row in drawn:
            twin = rows.get(row["system"] + "-precise")
            if not is_own(row["system"]) or twin is None or not twin.get("items") or twin.get("delivered_hit") is None:
                continue
            x1, y1, x2, y2 = px(twin["items"]), py(twin["delivered_hit"]), px(row["items"]), py(row["delivered_hit"])
            arrows.append((x1, y1, x2, y2))
            obstacles.append((x1 - 7, y1 - 7, x1 + 7, y1 + 7))
            length = math.hypot(x2 - x1, y2 - y1) or 1.0
            steps = max(4, int(length / 6))       # the arrow is an obstacle for the labels: a small box every 6 units
            for k in range(1, steps):
                ax, ay = x1 + (x2 - x1) * k / float(steps), y1 + (y2 - y1) * k / float(steps)
                obstacles.append((ax - 1, ay - 1, ax + 1, ay + 1))
        points = [(px(r["items"]), py(r["delivered_hit"]), label_for(r["system"], lang, systems, short=True))
                  for r in drawn]
        dots = [(x - 6.0, y - 6.0, x + 6.0, y + 6.0) for x, y, _ in points]
        own_names = {label_for(r["system"], lang, systems, short=True) for r in drawn if is_own(r["system"])}
        labelled, placement = place_with_merging(
            merge_labels(points), obstacles, (left, top, right, top + plot_h), dots,
            same_family=lambda text: all(part in own_names for part in text.split(" / ")), lines=arrows)
        out[dataset] = {"top": top, "px": px, "py": py, "drawn": drawn, "arrows": arrows, "obstacles": obstacles,
                        "points": labelled, "placement": placement}
    return out


def chart_delivered(ext, own, systems, theme, lang):
    t = TXT[lang]
    box = DELIVERED_BOX
    plot_h, left, right = box["plot_h"], box["left"], box["right"]
    layout = delivered_layout(ext, systems, lang)
    top_last = layout["locomo"]["top"]
    doc = new_doc(theme, lang)
    legend(doc, t["t_delivered"], family_entries(doc) + [("ring", t["hollow_precise"], doc.pal["accent"])])
    accent, neutral, surface = doc.pal["accent"], doc.pal["neutral"], doc.pal["surface"]
    for dataset, panel in layout.items():
        top, px, py = panel["top"], panel["px"], panel["py"]
        doc.text(MARGIN, top - 46, DATASET_NAMES[dataset], "lb")
        doc.text(MARGIN, top - 30, dataset_subtitle(ext, dataset, t), "s")
        doc.text(MARGIN, top - 12, t["delivered_y"], "s")
        for tick in (0, 0.25, 0.5, 0.75, 1.0):
            doc.line(left, py(tick), right, py(tick), "axis" if tick == 0 else "grid")
            doc.text(left - 8, py(tick) + 4, "%d%%" % round(tick * 100), "s", "end")
        for v in (1, 2, 4, 8, 16, 32):
            doc.line(px(v), top, px(v), top + plot_h, "axis" if v == 1 else "grid")
            doc.text(px(v), top + plot_h + 18, "%d" % v, "s", "middle")
        for row in panel["drawn"]:
            if not is_own(row["system"]):
                doc.circle(px(row["items"]), py(row["delivered_hit"]), 5, neutral)
        for x1, y1, x2, y2 in panel["arrows"]:
            length = math.hypot(x2 - x1, y2 - y1) or 1.0
            ux, uy = (x2 - x1) / length, (y2 - y1) / length
            doc.line(x1 + ux * 6, y1 + uy * 6, x2 - ux * 10, y2 - uy * 10, "lead", width=1.5, color=accent)
            charts.arrow_head(doc, x1, y1, x2 - ux * 6, y2 - uy * 6, accent)
            doc.add('<circle cx="%.1f" cy="%.1f" r="5" fill="%s" stroke="%s" stroke-width="2.5"/>' % (x1, y1, surface, accent))
        for row in panel["drawn"]:
            if is_own(row["system"]):
                doc.circle(px(row["items"]), py(row["delivered_hit"]), 6, accent)
        for (x, y, _), (tx, ty, anchor, bx, leader) in zip(panel["points"], panel["placement"]):
            if leader:
                doc.line(x, y, leader[0], leader[1], "lead", color=doc.pal["ink2"])
        for (x, y, label), (tx, ty, anchor, bx, leader) in zip(panel["points"], panel["placement"]):
            doc.add('<text x="%.1f" y="%.1f" class="l" style="font-size:11px"%s>%s</text>' % (
                tx, ty - 2.5, "" if anchor == "start" else ' text-anchor="%s"' % anchor, charts.esc(label)))
    doc.text((left + right) / 2.0, top_last + plot_h + 38, t["delivered_x"], "s", "middle")
    footer(doc, ext, top_last + plot_h + 62, t["delivered_note"])
    return doc.render()


# ------------------------------------------------------------------ 4. by type (heatmap)

def chart_by_type(ext, own, systems, theme, lang):
    t = TXT[lang]
    names = ranked_names(ext)
    top, cw, x0, gap = 144, 88, 236, 14
    plot_h = ROW_H * len(names)
    doc = new_doc(theme, lang)
    legend(doc, t["t_bytype"], family_entries(doc))
    ramp_legend(doc, W - MARGIN - 40, charts.TITLE_Y + 24)
    rows = {d: full_rows(ext, d) for d in DATASET_NAMES}
    columns = []     # (dataset, type, questions)
    for d in DATASET_NAMES:
        types = sorted({k for r in rows[d].values() for k in r["by_type"]}, key=lambda k: (_type_pos(d, k), k))
        for k in types:
            n = next((r["by_type"][k]["questions"] for r in rows[d].values() if k in r["by_type"]), 0)
            columns.append((d, k, n))
    group_x = {}
    for c, (d, k, n) in enumerate(columns):
        group_x.setdefault(d, []).append(x0 + c * cw + (gap if d != "perltqa" else 0))
    for d, xs in group_x.items():
        doc.text((xs[0] + xs[-1] + cw) / 2.0, top - 58, "%s" % DATASET_NAMES[d], "lb", "middle")
        doc.line(xs[0] + 4, top - 50, xs[-1] + cw - 4, top - 50, "axis")
    for c, (d, k, n) in enumerate(columns):
        cx = group_x[d][[kk for dd, kk, _ in columns if dd == d].index(k)]
        name = t["types"].get(k, k)
        doc.text(cx + cw / 2.0, top - 30, name, short_type(doc, name, cw - 6), "middle")
        doc.text(cx + cw / 2.0, top - 14, "n = %s" % thousands(n), "s", "middle")
    for i, name in enumerate(names):
        y = top + i * ROW_H
        yc = y + ROW_H / 2.0
        family_swatch(doc, LABEL_X, yc, is_own(name))
        name_label(doc, name, yc, systems, LABEL_X + 12, LABEL_W - 12)
        for d, k, n in columns:
            cx = group_x[d][[kk for dd, kk, _ in columns if dd == d].index(k)]
            cell = ((rows[d].get(name) or {}).get("by_type") or {}).get(k)
            v = None if cell is None else cell.get("hit5")
            if v is None:
                ink_text(doc, cx + cw / 2.0, yc + 4.5, "–", doc.pal["ink2"], cls="s")
            else:
                heat_cell(doc, cx, y, cw, ROW_H, v, pct(v))
    footer(doc, ext, top + plot_h + 28, t["bytype_note"])
    return doc.render()


TYPE_ORDER = {"perltqa": ("profile", "social_relationship", "events", "dialogues"),
              "locomo": ("4-single-hop", "1-multi-hop", "2-temporal", "3-open-domain")}


def _type_pos(dataset, kind):
    order = TYPE_ORDER[dataset]
    return order.index(kind) if kind in order else len(order)


# ------------------------------------------------------------------ 5. thresholds (dumbbells, this project only)

def chart_thresholds(ext, own, systems, theme, lang):
    t = TXT[lang]
    base = {s["system"]: s for s in own["systems"].values()}
    groups = []        # (title, subtitle, [(name, default values, precise values)])
    n_ans, n_quiet = own_question_counts(own)
    own_rows = [(n, own_values(base[n]), own_values(base[n + "-precise"]) if n + "-precise" in base else None)
                for n in report.OWN if n in base]
    groups.append((t["own_set"], t["own_set_n"] % (n_ans, n_quiet), own_rows))
    for d in DATASET_NAMES:
        rows = full_rows(ext, d, with_precise=True)
        groups.append((DATASET_NAMES[d], dataset_subtitle(ext, d, t),
                       [(n, ext_values(rows[n]), ext_values(rows[n + "-precise"]) if n + "-precise" in rows else None)
                        for n in report.OWN if n in rows]))
    head_h = 34
    top = 142
    plot_h = sum(head_h + ROW_H * len(g[2]) for g in groups) + 8 * (len(groups) - 1)
    doc = new_doc(theme, lang)
    accent, surface = doc.pal["accent"], doc.pal["surface"]
    legend(doc, t["t_thresholds"], [("dot", t["default_filled"], accent), ("ring", t["precise_hollow"], accent)])
    axis_w = 170.0
    panels = (  # title, index in the values tuple, max, ticks, formatter
        (t["m_items"], 2, 5.0, (0, 1, 2, 3, 4, 5), lambda v: "%.2f" % v),
        (t["m_dh"], 1, 1.0, (0, 0.25, 0.5, 0.75, 1.0), lambda v: pct(v)),
        (t["m_quiet"], 3, 1.0, (0, 0.25, 0.5, 0.75, 1.0), lambda v: pct(v)))
    starts = (184.0, 460.0, 736.0)
    for (title, _, vmax, ticks, _), x0 in zip(panels, starts):
        doc.text(x0, top - 22, title, "lb")
        for tick in ticks:
            x = x0 + tick / vmax * axis_w
            doc.line(x, top - 6, x, top + plot_h, "axis" if tick == 0 else "grid")
            doc.text(x, top + plot_h + 18, ("%d" % tick) if vmax == 5.0 else "%d%%" % round(tick * 100), "s", "middle")
    y = top
    for title, subtitle, rows in groups:
        y += 8 if y > top else 0
        doc.add('<rect x="%.1f" y="%.1f" width="%.1f" height="18" fill="%s"/>' % (
            LABEL_X - 4, y + 3, text_width(title, SIZE_LABEL) + text_width(subtitle, SIZE_SMALL) + 22, surface))
        doc.text(LABEL_X, y + 17, title, "lb")
        doc.text(LABEL_X + text_width(title, SIZE_LABEL) + 10, y + 17, subtitle, "s")
        doc.line(LABEL_X, y + 24, W - MARGIN, y + 24, "grid")
        y += head_h
        for name, default, precise in rows:
            yc = y + ROW_H / 2.0
            doc.text(LABEL_X + 12, yc + 4.5, label_for(name, lang, systems, short=True), "l")
            for (_, idx, vmax, _, fmt), x0 in zip(panels, starts):
                v, pv = default[idx], (precise[idx] if precise else None)
                if v is None:
                    doc.text(x0 + axis_w / 2.0, yc + 4, "—", "s", "middle")
                    continue
                xv = x0 + v / vmax * axis_w
                if pv is None:
                    doc.circle(xv, yc, 5, accent)
                    doc.text(xv + 9, yc + 4, fmt(v), "s")
                    continue
                xp = x0 + pv / vmax * axis_w
                doc.line(min(xv, xp), yc, max(xv, xp), yc, "lead", width=1.5, color=accent)
                close = abs(xv - xp) < 6
                if close:    # the two dots would hide each other: a ring around the filled dot
                    doc.add('<circle cx="%.1f" cy="%.1f" r="7" fill="none" stroke="%s" stroke-width="2"/>' % (xp, yc, accent))
                    doc.circle(xv, yc, 4, accent)
                else:
                    doc.add('<circle cx="%.1f" cy="%.1f" r="5" fill="%s" stroke="%s" stroke-width="2.5"/>' % (xp, yc, surface, accent))
                    doc.circle(xv, yc, 5, accent)
                left_is_default = xv < xp
                lx, rx = min(xv, xp), max(xv, xp)
                lv, rv = (v, pv) if left_is_default else (pv, v)
                doc.text(lx - 10, yc + 4, fmt(lv), "s", "end")
                doc.text(rx + 10, yc + 4, fmt(rv), "s")
            y += ROW_H
    footer(doc, ext, top + plot_h + 52, t["thresholds_note"])
    return doc.render()


# ------------------------------------------------------------------ 6. cost

def cost_groups(ext, dataset):
    """[(base id, {model: cost entry})]: a system's entries are its models; the base id is the system id without
    the model suffix."""
    out = {}
    for c in (ext.get(dataset) or {}).get("cost") or []:
        base = c["system"][:-len("-" + c["model"])] if c["system"].endswith("-" + c["model"]) else c["system"]
        out.setdefault(base, {})[c["model"]] = c
    return sorted(out.items(), key=lambda kv: (BASE_ORDER.index(kv[0]) if kv[0] in BASE_ORDER else len(BASE_ORDER), kv[0]))


def cost_label(base, lang, registry):
    if base in BASE_LABELS:
        return BASE_LABELS[base][0 if lang == "zh" else 1]
    return label_for(base, lang, registry)


def fmt_usd(v):
    return "<0.01" if v < 0.01 else "%.2f" % v


def chart_cost(ext, own, systems, theme, lang):
    t = TXT[lang]
    per_dataset = [(d, cost_groups(ext, d)) for d in DATASET_NAMES]
    models = sorted({m for _, groups in per_dataset for _, by in groups for m in by},
                    key=lambda m: (MODEL_ORDER.index(m) if m in MODEL_ORDER else len(MODEL_ORDER), m))
    doc = new_doc(theme, lang)
    colors = {m: doc.pal["arm"][MODEL_SLOTS[i]] if i < len(MODEL_SLOTS) else doc.pal["ink2"] for i, m in enumerate(models)}
    legend(doc, t["t_cost"], [("dot", m, colors[m]) for m in models])
    row_h, head_h, top = 36, 32, 132
    group_gap = 12
    plot_h = sum(group_gap + head_h + row_h * len(g) for _, g in per_dataset) - group_gap
    x0, width, lo, hi = 250.0, 380.0, 0.01, 10.0
    calls_x, stores_x = 842.0, W - MARGIN
    for tick in (0.01, 0.1, 1, 10):
        x = charts.log_pos(tick, lo, hi, x0, width)
        doc.line(x, top - 6, x, top + plot_h, "axis" if tick == 0.01 else "grid")
        doc.text(x, top + plot_h + 18, "%g" % tick, "s", "middle")
    doc.text(x0 + width / 2.0, top + plot_h + 38, t["cost_x"], "s", "middle")
    doc.text(calls_x, top - 26, t["c_calls"], "lb", "end")
    doc.text(calls_x, top - 11, t["c_order"], "s", "end")
    doc.text(stores_x, top - 26, t["c_stores"], "lb", "end")
    below = False
    y = top
    for n, (dataset, groups) in enumerate(per_dataset):
        y += group_gap if n else 0
        doc.text(LABEL_X, y + 17, DATASET_NAMES[dataset], "lb")
        doc.line(LABEL_X, y + 24, W - MARGIN, y + 24, "grid")
        y += head_h
        for base, by in groups:
            yc = y + row_h / 2.0
            doc.text(LABEL_X + 12, yc + 4.5, cost_label(base, lang, systems), "l")
            pts = []
            for k, m in enumerate(models):
                c = by.get(m)
                if c is None:
                    continue
                v = c["usd_per_1000_memories"]
                below = below or v < lo
                pts.append((charts.log_pos(v, lo, hi, x0, width), yc + (-4.5 if k == 0 else 4.5), m, v))
            pts.sort(key=lambda p: p[0])
            for i, (x, yy, m, v) in enumerate(pts):
                doc.circle(x, yy, 5, colors[m])
                left_side = len(pts) == 2 and i == 0
                doc.text(x - 9 if left_side else x + 9, yy + 4, fmt_usd(v), "s", "end" if left_side else "start")
            doc.text(calls_x, yc + 4, " / ".join(
                ("%.1f" % (by[m]["calls"] / float(by[m]["memories"]))) if m in by else "–" for m in models), "s", "end")
            doc.text(stores_x, yc + 4, " / ".join(str(by[m]["stores"]) if m in by else "–" for m in models), "s", "end")
            y += row_h
    footer(doc, ext, top + plot_h + 62, t["cost_note"] + (" " + t["cost_below"] if below else ""))
    return doc.render()


# ------------------------------------------------------------------ 7. latency

def latency_names(ext):
    lat = {d: (ext.get(d) or {}).get("latency") or {} for d in DATASET_NAMES}
    names = []
    for d in DATASET_NAMES:
        names += [n for n in lat[d] if n not in names and not is_precise(n)]

    def p50(n):
        for d in DATASET_NAMES:
            if n in lat[d]:
                return lat[d][n]["p50_ms"]
        return float("inf")

    def key(n):
        return (0, report.OWN.index(n) if n in report.OWN else 9, 0.0, n) if is_own(n) else (1, 0, p50(n), n)
    return sorted(names, key=key), lat


def fmt_ms(v):
    return "%.1f" % v if v < 100 else "%d" % round(v)


def chart_latency(ext, own, systems, theme, lang):
    t = TXT[lang]
    names, lat = latency_names(ext)
    top = 132
    row_h = 26
    plot_h = row_h * len(names)
    doc = new_doc(theme, lang)
    ink2 = doc.pal["ink2"]
    legend(doc, t["t_latency"], family_entries(doc) + [("stem", t["lat_stem"], ink2), ("ring", t["lat_hollow"], ink2)])
    lo, hi, width = 0.1, 10000.0, 290.0
    panels = (("perltqa", 240.0), ("locomo", 616.0))
    for dataset, x0 in panels:
        sequential = [v for v in lat[dataset].values() if v["kind"].startswith("sequential")] or list(lat[dataset].values())
        ref = sequential[0] if sequential else {}
        doc.text(x0, top - 36, DATASET_NAMES[dataset], "lb")
        doc.text(x0, top - 20, t["lat_sub"] % (ref.get("world") or "?", thousands(ref["memories"]) if ref.get("memories") else "?"), "s")
        for tick in (0.1, 1, 10, 100, 1000, 10000):
            x = charts.log_pos(tick, lo, hi, x0, width)
            doc.line(x, top - 6, x, top + plot_h, "axis" if tick == 0.1 else "grid")
            doc.text(x, top + plot_h + 18, format(tick, ",") if tick >= 1 else "%g" % tick, "s", "middle")
    for i, name in enumerate(names):
        yc = top + i * row_h + row_h / 2.0
        name_label(doc, name, yc, systems)
        fill = fill_of(doc, name)
        for dataset, x0 in panels:
            v = lat[dataset].get(name)
            if v is None:
                doc.text(x0 + 4, yc + 4, "–", "s")
                continue
            x50 = charts.log_pos(v["p50_ms"], lo, hi, x0, width)
            x95 = charts.log_pos(v["p95_ms"], lo, hi, x0, width)
            doc.line(x50, yc, x95, yc, "lead", color=fill)
            doc.line(x95, yc - 4, x95, yc + 4, "lead", width=1.5, color=fill)
            if v["kind"].startswith("measured under load"):
                doc.add('<circle cx="%.1f" cy="%.1f" r="5" fill="%s" stroke="%s" stroke-width="2.5"/>' % (
                    x50, yc, doc.pal["surface"], fill))
            else:
                doc.circle(x50, yc, 5, fill)
            doc.text(max(x95, x50) + 9, yc + 4, fmt_ms(v["p50_ms"]), "s")
    footer(doc, ext, top + plot_h + 52, t["latency_note"])
    return doc.render()


# ------------------------------------------------------------------ 8. samples

def chart_samples(ext, own, systems, theme, lang):
    t = TXT[lang]
    blocks = []
    for d in DATASET_NAMES:
        for s in (ext.get(d) or {}).get("samples") or []:
            rows = [r for r in s["rows"] if r["system"] not in report.REFERENCE] + \
                   [r for r in s["rows"] if r["system"] in report.REFERENCE]
            blocks.append((d, s, rows))
    head_h, top = 36, 100
    plot_h = sum(head_h + ROW_H * len(rows) + 24 for _, _, rows in blocks)
    doc = new_doc(theme, lang)
    legend(doc, t["t_samples"], ranking_legend(doc, t))
    x0, scale = 240.0, 270.0
    dh_x, items_x = 790.0, W - MARGIN
    y = top
    for d, s, rows in blocks:
        title = report.sample_title(d, s, lang) + " · " + t["s_questions"] % thousands(rows[0]["answerable"])
        doc.text(LABEL_X, y + 14, title, "lb")
        doc.text(dh_x, y + 14, t["s_dh"], "s", "end")
        doc.text(items_x, y + 14, t["s_items"], "s", "end")
        y += head_h - 10
        bottom = y + ROW_H * len(rows)
        charts.percent_axis(doc, x0, scale, y - 2, bottom, label_y=bottom + 16)
        for r in rows:
            yc = y + ROW_H / 2.0
            name_label(doc, r["system"], yc, systems)
            ranking_marks(doc, x0, scale, yc, r, fill_of(doc, r["system"]))
            doc.text(dh_x, yc + 4, pct(r["delivered_hit"]) if r.get("delivered_hit") is not None else "–", "l", "end")
            doc.text(items_x, yc + 4, "%.2f" % r["items"] if r.get("items") is not None else "–", "l", "end")
            y += ROW_H
        y = bottom + 34
    footer(doc, ext, y + 8, t["samples_note"])
    return doc.render()


# ------------------------------------------------------------------ driver

BUILDERS = {"overview": chart_overview, "ranking": chart_ranking, "delivered": chart_delivered,
            "by-type": chart_by_type, "thresholds": chart_thresholds, "cost": chart_cost,
            "latency": chart_latency, "samples": chart_samples}


def render_chart(name, summary, own_summary, systems, theme, lang):
    """SVG text of one chart."""
    return BUILDERS[name](summary, own_summary, systems, theme, lang)


def render_all(summary, own_summary, systems, out_dir, charts=CHARTS, langs=LANGS, themes=THEMES):
    """Write every chart as ext-<name>.<lang>.<theme>.svg; returns the list of written paths."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name in charts:
        for lang in langs:
            for theme in themes:
                svg = render_chart(name, summary, own_summary, systems, theme, lang)
                path = os.path.join(out_dir, "ext-%s.%s.%s.svg" % (name, lang, theme))
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(svg)
                written.append(path)
    return written


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the public-dataset benchmark charts as SVG.")
    parser.add_argument("--out", default=OUT_DIR, help="output directory (default docs/assets/charts)")
    args = parser.parse_args(argv)
    try:
        summary, own, systems = _read(EXT_SUMMARY_PATH), _read(OWN_SUMMARY_PATH), _read(SYSTEMS_PATH)
    except (OSError, ValueError) as error:
        print("cannot read the input files: %s" % type(error).__name__, file=sys.stderr)
        return 1
    paths = render_all(summary, own, systems, args.out)
    print("wrote %d chart file(s) to %s" % (len(paths), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
