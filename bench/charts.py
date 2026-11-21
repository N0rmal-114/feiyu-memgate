"""Chart generator of the memory benchmark. Standard library only; writes static SVG.

    python bench/charts.py

Reads bench/results/summary.json, bench/systems.json and bench/results/forgetting/*/summary.json and
writes docs/assets/charts/<name>.<lang>.<theme>.svg for every chart, language (zh, en) and theme
(light, dark). The PNGs the README embeds are rendered from these files by bench/render_charts.sh.

Design rules (they follow the dataviz method used for this project):
- identity is carried by a direct label on a sorted bar, never by a hue per system; colour carries
  only the family (feiyu-memgate configurations: one accent hue; everything else: one neutral);
- the retrieval kind is a text tag after the label, not a colour;
- bars <= 24 px, 4 px rounded data end, square at the baseline; lines 2 px; hairline solid grid;
- text is always in an ink colour, never in a series colour; one axis per panel, no dual axes;
- every chart has a one-line title saying what is measured and a source line; the Markdown tables
  in bench/results/summary.md are the table view.
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SUMMARY_PATH = os.path.join(HERE, "results", "summary.json")
SYSTEMS_PATH = os.path.join(HERE, "systems.json")
FORGETTING_DIR = os.path.join(HERE, "results", "forgetting")
TIERS_PATH = os.path.join(HERE, "tiers.json")
OUT_DIR = os.path.join(ROOT, "docs", "assets", "charts")

LANGS = ("zh", "en")
THEMES = ("light", "dark")
CHARTS = ("ranking", "by-type", "delivered", "quiet", "cost", "forgetting", "tiers", "quality", "hit-at-k",
          "delivered-by-type", "context", "fresh", "profiles")

# ------------------------------------------------------------------ palette and type (shared by every chart)

# Colours are validated with the dataviz palette validator (see the report of the job that added this
# file): accent = categorical slot 1, the three forgetting arms = slots 1-3 (all-pairs safe), neutral =
# the documented de-emphasis gray. Text never wears these colours.
PALETTE = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "grid": "#e1e0d9", "axis": "#c3c2b7",
              "accent": "#2a78d6", "neutral": "#898781", "arm": ("#2a78d6", "#eb6834", "#1baf7a")},
    "dark": {"surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "grid": "#2c2c2a", "axis": "#383835",
             "accent": "#3987e5", "neutral": "#898781", "arm": ("#3987e5", "#d95926", "#199e70")},
}
FONT = ('-apple-system, "Segoe UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", '
        '"Helvetica Neue", Arial, sans-serif')

W = 1000                  # every chart is 1000 units wide, so every PNG is scaled the same way
MARGIN = 24               # left and right margin
TITLE_Y = 32
SIZE_TITLE, SIZE_LABEL, SIZE_SMALL = 17, 13, 11
ROW_H = 28                # one system per row in the bar charts
BAR_H = 14
LABEL_X = MARGIN          # system names
TAG_X = 212               # retrieval-kind tag column
PLOT_X = 330              # where the bars of the single-measure charts start
SOURCE = "bench/results/summary.md"
TINT = 0.35               # opacity of the lighter tint of a series colour (the part of a bar that is not "right")

TXT = {
    "zh": {
        "family_fmg": "feiyu-memgate 的配置", "family_other": "其他项目",
        "kinds": {"keyword": "关键词", "vector": "向量", "hybrid": "混合", "graph": "图", "extraction": "提取"},
        "llm": "用 LLM",
        "memories": "条记忆", "questions": "个问题", "questions_short": "题",
        "types": {"lexical": "字面", "paraphrase": "改写", "updated": "更新"},
        "t_ranking": "各系统的 Hit@5（柱）和 Hit@1（刻度）：前几条里有正确记忆的问题所占比例",
        "t_bytype": "分题型的 Hit@5：字面题、改写题、更新题各一栏，系统顺序同上图",
        "t_delivered": "默认交付的内容：每题平均交付条数（横，对数轴）与其中正确的占比（纵）",
        "t_quiet": "安静率：记忆里没有答案的问题，系统什么都不返回的比例",
        "t_cost": "成本：每题延迟的中位数（左）和 240 条记忆的入库时间（右），两栏都是对数轴",
        "t_forget": "遗忘模拟：库内记忆条数（上）和常被问的问题的 Hit@5（下），横轴是模拟天数；每条线是一组设置",
        "t_tiers": "部署档位：从不用模型到最大的模型，每一档的得失（左四栏）和 embedder 的内存（右两栏）",
        "tiers_cols": ("排名 Hit@5", "交付命中", "精确率", "安静率"),
        "tiers_mem": "embedder 内存（MiB，对数轴）", "tiers_file": "模型文件", "tiers_none": "无",
        "tiers_note": "四栏柱子来自留出集的运行（各档用各自模型的预设分数线、默认召回步骤，默认是宽松档）；后三栏里的黑色短竖线是同一档固定为精确档（profile = precise）的值。内存和模型文件大小是在开发集运行时测的"
                      "（见 bench/tiers.json 和 bench/experiments/light_models/）：模型加载后 embedder 容器的常驻内存，测量时机器上还有别的容器，只当量级看。"
                      "按内存从小到大排列；sqlite 不需要模型和服务。",
        "hit1_tick": "刻度：Hit@1", "bar_hit5": "柱：Hit@5",
        "ranking_note": "答案在前 10 条里才算命中；Hit@5 看前 5 条。可答问题 %d 个，每题约 0.8 个百分点。",
        "experiments": "另有 %d 个实验变体只在表里，不在图里。",
        "delivered_hint": "每个标签后的数字：默认交付的命中率",
        "delivered_corner": "左上：交付得少，而且交付的基本是对的",
        "delivered_x": "每题平均交付条数（对数轴）", "delivered_y": "交付条目里正确的占比",
        "delivered_skip": "没有画出：%s（什么都没交，精确率没有定义）",
        "quiet_note": "总是返回前 k 条的系统，安静率按定义是 0。安静率高不等于好：它和命中率此消彼长。",
        "quiet_n": "安静题 %d 个。",
        "cost_lat": "延迟 p50（毫秒，对数轴）", "cost_ing": "入库时间（秒，对数轴）", "cost_tok": "LLM tokens",
        "cost_note": "点的位置表示数值，所以对数轴上不用柱子。入库时间由各系统自报；lingshu 的数字不含 1831 秒的限速等待。",
        "f_store": "库内记忆条数", "f_hit": "Hit@5，常被问的 40 个问题", "f_days": "模拟天数",
        "f_overlap": "重合的线（读数相同）：%s",
        "arms": {"baseline": "基线（不遗忘）", "default": "默认设置", "default_pinned": "默认 + 钉住身份类",
                 "aggressive": "激进（半衰期 30 天）"},
        "backends": {"sqlite": "关键词后端（sqlite）", "hybrid": "混合后端（关键词 + bge-m3 向量）"},
        "f_note": "Hit@5 的纵轴从 %d%% 起，不是从 0 起。40 个题里一题是 2.5 个百分点，图里看到的差别最多一两个题。",
        "source": "%s · %d 条记忆，%d 个问题 · %s",
        "log_zero": "低于坐标轴下限",
        "t_quality": "到达模型的内容：交付 F1、精确率、安静率（柱）和每题平均交付条数（右），按交付 F1 从高到低",
        "quality_cols": ("交付 F1", "精确率", "安静率"), "quality_items": "条数/题",
        "quality_note": "交付 F1 是交付命中和精确率的调和平均，两个都高才高。精确率是交付条目里正确的占比；什么都没交的系统没有精确率，画“–”。",
        "t_hitk": "Hit@k 曲线：k 从 1 到 10，前 k 条里有正确记忆的问题所占比例（线越早到顶，要翻的条数越少）",
        "hitk_x": "k：看前几条", "hitk_y": "Hit@k",
        "hitk_note": "蓝色是 feiyu-memgate 的配置，不同虚线样式只用来区分它们，名字写在线尾；灰色是其他项目。完全重合的线只画一条，标签写在一起。",
        "t_dbytype": "分题型的交付命中：字面题、改写题、更新题各一栏，系统顺序同排名图（与分题型 Hit@5 对照着看）",
        "dbytype_note": "交付命中是系统按默认设置交给模型的内容里有正确记忆的问题占比，和排名类的 Hit@5 不同：总是交一堆的系统在这里偏高，该安静的时候交不交不算在内。",
        "t_context": "每题占多少上下文：平均交付字数（柱），深色部分是正确条目的字数，浅色是其余，按字数从少到多",
        "context_chars": "字", "context_correct": "%s 字 · %s 正确", "context_undefined": "%s 字 · 无精确率",
        "context_light": "浅色：不正确的部分",
        "t_context_log": "每题占多少上下文：平均交付字数（淡色点）和其中正确条目的字数（实色点），对数轴，按字数从少到多",
        "context_light_log": "淡色点：交付的全部字数；实色点：其中正确的字数",
        "context_note_log": "实色点的位置 = 平均字数 × 精确率（按条目数算的正确占比，用来近似字数）。精确率没有定义的系统只画淡色点。",
        "context_axis_log": "有的系统交付的字数是别的系统的几十倍，线性轴上小的柱子看不清，所以用对数轴和点，不截断任何数值。",
        "context_note": "深色部分的长度 = 平均字数 × 精确率（按条目数算的正确占比，用来近似字数）。精确率没有定义的系统只画浅色。",
        "t_fresh": "更新题的交付新旧优先：新记忆交付了的题里，旧记忆没一起交付或排在新记忆后面的占比",
        "fresh_note": "标签是 成立题数/题数。分母只数新记忆被交付了的更新题，所以每个系统不同；这样的题不足 5 个的系统没有画，列在这里：%s。",
        "fresh_variants": "其余变体：%s。",
        "fresh_precise_in": "五个配置的精确档变体也在图里。",
        "t_profiles": "宽松档（默认）与精确档：同一配置换到精确档，命中降多少、精确率升多少（横轴每题平均交付条数，对数轴）",
        "profiles_hint": "标签后的数字：交付命中率；实心是宽松档（默认），空心是精确档，箭头从精确档指向宽松档，也就是多交付的方向。",
        "profiles_others": "灰点是其他项目；未标注的：%s。",
        "profiles_off": "横轴只到 32 条；每题交得更多的点画在右边缘（实际条数：%s）。",
        "profiles_full": "full 的两个点和 hybrid 的几乎重合（只多了新鲜度），没有单独画。",
    },
    "en": {
        "family_fmg": "feiyu-memgate configurations", "family_other": "other projects",
        "kinds": {"keyword": "keyword", "vector": "vector", "hybrid": "hybrid", "graph": "graph",
                  "extraction": "extraction"},
        "llm": "LLM",
        "memories": "memories", "questions": "questions", "questions_short": "q",
        "types": {"lexical": "lexical", "paraphrase": "paraphrase", "updated": "updated"},
        "t_ranking": "Hit@5 per system (bar) and Hit@1 (tick): share of questions with a right memory in the top results",
        "t_bytype": "Hit@5 by question type: lexical, paraphrase, updated; systems in the order of the chart above",
        "t_delivered": "What is delivered by default: items per question (x, log) and the share of them that are right (y)",
        "t_quiet": "Quiet rate: share of questions with no answer in memory that get nothing back",
        "t_cost": "Cost: median latency per question (left) and ingest time for 240 memories (right), both log axes",
        "t_forget": "Forgetting simulation: memories in the store (top) and Hit@5 on frequently asked questions (bottom); one line per setting",
        "t_tiers": "Deployment tiers, no model to largest: what each gives (left) and what its embedder needs (right)",
        "tiers_cols": ("ranked Hit@5", "delivered hit", "precision", "quiet rate"),
        "tiers_mem": "embedder memory (MiB, log scale)", "tiers_file": "model file", "tiers_none": "none",
        "tiers_note": "The four bar columns come from the held-out run (each tier with its model's preset score lines and the default recall stages, whose profile is broad); the short black tick in the last three columns is the same tier with the precise profile pinned. "
                      "Memory and model file size were measured on the development run (bench/tiers.json, bench/experiments/light_models/): resident memory of the embedder container with the model loaded; "
                      "other containers were running, so read them as orders of magnitude. Ordered by memory use; sqlite needs no model and no service.",
        "hit1_tick": "tick: Hit@1", "bar_hit5": "bar: Hit@5",
        "ranking_note": "A hit needs the right memory among the top 10 results; Hit@5 looks at the top 5. %d answerable questions, about 0.8 points per question.",
        "experiments": "%d experiment variants appear in the tables only, not in the charts.",
        "delivered_hint": "number after each label: deployed hit rate",
        "delivered_corner": "upper left: delivers little, and what it delivers is right",
        "delivered_x": "average items delivered per question (log scale)", "delivered_y": "share of delivered items that are right",
        "delivered_skip": "not drawn: %s (delivered nothing, precision undefined)",
        "quiet_note": "A system that always returns its top-k scores 0 by construction. A high rate is not simply good: it trades off against hit rate.",
        "quiet_n": "%d no-answer questions.",
        "cost_lat": "latency p50 (ms, log scale)", "cost_ing": "ingest time (s, log scale)", "cost_tok": "LLM tokens",
        "cost_note": "Position carries the value, so a log axis uses dots, not bars. Ingest time is self-reported; lingshu's figure excludes 1831 s of rate-limit waiting.",
        "f_store": "memories in the store", "f_hit": "Hit@5, the 40 frequently asked questions", "f_days": "simulated days",
        "f_overlap": "lines that coincide (same readings): %s",
        "arms": {"baseline": "baseline (no forgetting)", "default": "defaults", "default_pinned": "defaults + identity pinned",
                 "aggressive": "aggressive (half-life 30 d)"},
        "backends": {"sqlite": "keyword backend (sqlite)", "hybrid": "hybrid backend (keyword + bge-m3 vectors)"},
        "f_note": "The Hit@5 axis starts at %d%%, not at 0. One question of 40 is 2.5 points; the differences visible here are one or two questions.",
        "source": "%s · %d memories, %d questions · %s",
        "log_zero": "below the axis",
        "t_quality": "What reaches the model: delivered F1, precision, quiet rate and items per question; by delivered F1",
        "quality_cols": ("delivered F1", "precision", "quiet rate"), "quality_items": "items/q",
        "quality_note": "Delivered F1 is the harmonic mean of delivered hit and precision: both must be high. Precision is the share of delivered items that are right; a system that delivered nothing has none, shown as a dash.",
        "t_hitk": "Hit@k: share of questions with a right memory in the top k results, k = 1 to 10",
        "hitk_x": "k: how many top results are read", "hitk_y": "Hit@k",
        "hitk_note": "Blue lines are feiyu-memgate configurations; the dash styles only tell them apart and each name is written at the line end. Grey lines are other projects. Lines that coincide are drawn once with their labels together.",
        "t_dbytype": "Delivered hit by question type, systems in the order of the ranking chart",
        "dbytype_note": "Delivered hit is the share of questions where what the system hands the model by default holds a right memory. It differs from ranked Hit@5: a system that hands over a pile scores high here, and staying quiet when it should is not counted.",
        "t_context": "Context spent per question: characters delivered (light) and those that are right (dark)",
        "context_chars": "chars", "context_correct": "%s chars · %s correct", "context_undefined": "%s chars · no precision",
        "context_light": "light: the part that is not right",
        "t_context_log": "Context per question: characters delivered (faded dot), correct ones (solid dot), log scale",
        "context_light_log": "faded dot: all characters delivered; solid dot: those that are right",
        "context_note_log": "The solid dot sits at average characters x precision (the correct share by item count, used as an estimate for characters). A system whose precision is undefined gets the faded dot only.",
        "context_axis_log": "One system delivers dozens of times what the smallest ones do, so small bars would be unreadable on a linear axis; the axis is logarithmic with dots and no value is truncated.",
        "context_note": "The dark part is average characters x precision (the correct share by item count, used as an estimate for characters). A system whose precision is undefined is drawn light only.",
        "t_fresh": "Fresh-first among delivered answers to updated questions: new memory before the old one",
        "fresh_note": "Label = wins/questions; a win is a question where the old memory was not delivered with the new one or came after it. The denominator counts only updated questions where the new memory was delivered, so it differs per system; systems with fewer than 5 such questions are not drawn: %s.",
        "fresh_variants": "Other variants: %s.",
        "fresh_precise_in": "The precise-profile variants of the five configurations are drawn too.",
        "t_profiles": "Broad (default) versus precise profile: switching to precise lowers the hit, raises precision (log x)",
        "profiles_hint": "number after a label: deployed hit; filled = broad profile (the default), hollow = precise profile, the arrow points from precise to broad, the direction of delivering more.",
        "profiles_others": "Grey dots are other projects; unlabelled: %s.",
        "profiles_off": "The axis ends at 32 items; points that deliver more are drawn at the right edge (actual items: %s).",
        "profiles_full": "full sits almost on top of hybrid at both profiles (it only adds freshness) and is not drawn separately.",
    },
}


# ------------------------------------------------------------------ small helpers

def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def text_width(text, size):
    """Rough rendered width of a string. CJK is a full em; Latin letters about 0.56 em."""
    total = 0.0
    for ch in str(text):
        if ord(ch) >= 0x2E80:
            total += 1.0
        elif ch in "il.,:;'|!1 ":
            total += 0.32
        elif ch in "mwMW":
            total += 0.85
        elif ch.isupper() or ch.isdigit():
            total += 0.62
        else:
            total += 0.54
    return total * size


def pct(value, digits=1):
    return "%.*f%%" % (digits, value * 100)


def fmt_number(value):
    """Plain number for log-axis labels and values: 0.025, 3.6, 330, 1347."""
    if value >= 100:
        return "%d" % round(value)
    if value >= 10:
        return "%.0f" % value
    if value >= 1:
        return "%.1f" % value
    if value >= 0.1:
        return "%.2f" % value
    return "%.3f" % value


def fmt_tokens(value):
    return "%.2f M" % (value / 1e6) if value >= 1e6 else "%d k" % round(value / 1e3)


def label_of(name, systems, lang):
    info = systems.get(name, {})
    return (info.get("label") or {}).get(lang) or name


def is_fmg(record):
    return record.get("family") == "feiyu-memgate"


def charted(summary):
    """The systems that are drawn: everything except the marked experiments and variants (other tiers of the
    same project: they have their own chart, `tiers`, and stay in the tables)."""
    return [s for s in summary["systems"].values() if not s.get("experiment") and not s.get("variant")]


def rank_order(records):
    """Hit@5 descending, then Hit@1, then name: the one order every chart uses."""
    return sorted(records, key=lambda s: (-(s["overall"].get("hit5") or 0), -(s["overall"].get("hit1") or 0),
                                          s["system"]))


# ------------------------------------------------------------------ SVG document

class Doc:
    def __init__(self, width, height, theme, lang):
        self.w, self.h, self.theme, self.lang = width, height, theme, lang
        self.pal = PALETTE[theme]
        self.t = TXT[lang]
        self.body = []

    def add(self, markup):
        self.body.append(markup)

    def text(self, x, y, content, cls="l", anchor="start"):
        self.add('<text x="%.1f" y="%.1f" class="%s"%s>%s</text>' % (
            x, y, cls, "" if anchor == "start" else ' text-anchor="%s"' % anchor, esc(content)))

    def line(self, x1, y1, x2, y2, cls="grid", width=None, color=None):
        extra = (' stroke-width="%s"' % width if width else "") + (' stroke="%s"' % color if color else "")
        self.add('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" class="%s"%s/>' % (x1, y1, x2, y2, cls, extra))

    def circle(self, x, y, r, fill):
        # the 2 px ring in the surface colour keeps dots legible where they meet a line or another dot
        self.add('<circle cx="%.1f" cy="%.1f" r="%s" fill="%s" stroke="%s" stroke-width="2"/>' % (
            x, y, r, fill, self.pal["surface"]))

    def bar(self, x, y, w, h, fill, opacity=None):
        """Horizontal bar: square at the baseline (left), 4 px rounded at the data end. `opacity` draws the
        light tint of the same colour."""
        if w <= 0:
            return
        r = min(4.0, w / 2, h / 2)
        self.add('<path d="M%.1f,%.1f H%.1f A%.1f,%.1f 0 0 1 %.1f,%.1f V%.1f A%.1f,%.1f 0 0 1 %.1f,%.1f H%.1f Z" fill="%s"%s/>' % (
            x, y, x + w - r, r, r, x + w, y + r, y + h - r, r, r, x + w - r, y + h, x, fill,
            "" if opacity is None else ' fill-opacity="%s"' % opacity))

    def style(self):
        p = self.pal
        return (
            "text{font-family:%(font)s;fill:%(ink2)s;font-size:%(sl)dpx}"
            ".t{font-size:%(st)dpx;font-weight:600;fill:%(ink)s}"
            ".l{font-size:%(sl)dpx;fill:%(ink)s}"
            ".lb{font-size:%(sl)dpx;font-weight:600;fill:%(ink)s}"
            ".s{font-size:%(ss)dpx;fill:%(ink2)s}"
            ".grid{stroke:%(grid)s;stroke-width:1;fill:none}"
            ".axis{stroke:%(axis)s;stroke-width:1;fill:none}"
            ".tick{stroke:%(ink)s;stroke-width:2;fill:none}"
            ".lead{stroke-width:1;fill:none}"
            ".fa{fill:%(accent)s}.fo{fill:%(neutral)s}.tint{fill:%(neutral)s;fill-opacity:%(tint)s}"
        ) % {"font": FONT, "ink": p["ink"], "ink2": p["ink2"], "grid": p["grid"], "axis": p["axis"],
             "accent": p["accent"], "neutral": p["neutral"], "tint": TINT, "sl": SIZE_LABEL, "ss": SIZE_SMALL, "st": SIZE_TITLE}

    def render(self):
        head = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
                'role="img" lang="%s">' % (self.w, self.h, self.w, self.h, self.lang))
        return "\n".join([head, "<style>%s</style>" % self.style(),
                          '<rect width="%d" height="%d" fill="%s"/>' % (self.w, self.h, self.pal["surface"])]
                         + self.body + ["</svg>", ""])


def source_line(summary, lang):
    ds = summary["dataset"]
    return TXT[lang]["source"] % (SOURCE, ds["memories"], ds["queries"], ds.get("run_date") or "?")


def header(doc, title, legend=()):
    """Title, then a legend row of (kind, text) entries: kind is 'fa', 'fo', 'tint' (bar swatch) or 'tick'."""
    doc.text(MARGIN, TITLE_Y, title, "t")
    x, y = MARGIN, TITLE_Y + 24
    for kind, text in legend:
        if kind == "tick":
            doc.line(x + 6, y - 11, x + 6, y + 1, "tick")
            x += 18
        else:
            doc.add('<rect x="%.1f" y="%.1f" width="14" height="8" rx="2" class="%s"/>' % (x, y - 9, kind))
            x += 22
        doc.text(x, y, text, "s")
        x += text_width(text, SIZE_SMALL) + 22
    return y


def note_lines(text, size=SIZE_SMALL, max_width=W - 2 * MARGIN):
    """Break a note into lines no wider than max_width: at a space when one is near, else between characters."""
    lines, line = [], ""
    for ch in text:
        if text_width(line + ch, size) > max_width and line:
            cut = line.rfind(" ")
            if cut > len(line) // 2 and ch != " ":
                lines.append(line[:cut].rstrip())
                line = line[cut + 1:]
            else:
                lines.append(line.rstrip())
                line = ""
        line += ch
    if line.strip():
        lines.append(line.strip())
    return lines


def footer(doc, summary, y, note=None):
    """The wrapped note (if any) and the source line; sets the document height to fit them."""
    for line in note_lines(note) if note else ():
        doc.text(MARGIN, y, line, "s")
        y += 18
    doc.text(MARGIN, y, source_line(summary, doc.lang), "s")
    doc.h = int(y + 16)


def new_doc(summary, theme, lang, height):
    return Doc(W, height, theme, lang)  # `summary` is unused today; kept so every chart builds its doc one way


def family_legend(t):
    return [("fa", t["family_fmg"]), ("fo", t["family_other"])]


def label_lines(label, max_width, size=SIZE_LABEL):
    """One line when the label fits; otherwise two: the name, and a smaller second line that starts at the first
    ' + ', full-width bracket or ' (' (a long configuration name must not run into the next column)."""
    if text_width(label, size) <= max_width:
        return [label]
    for sep, skip in ((" + ", 1), ("（", 0), (" (", 1)):
        i = label.find(sep)
        if i > 0:
            return [label[:i], label[i + skip:]]
    return [label]


def row_label(doc, rec, systems, y_center, with_tag=True):
    t = doc.t
    label = label_of(rec["system"], systems, doc.lang)
    lines = label_lines(label, (TAG_X if with_tag else 214) - LABEL_X - 8)
    if len(lines) == 1:
        doc.text(LABEL_X, y_center + 4.5, label, "l")
    else:
        doc.text(LABEL_X, y_center - 1.5, lines[0], "l")
        doc.text(LABEL_X, y_center + 10.5, lines[1], "s")
    if with_tag:
        tag = t["kinds"].get(rec.get("kind"), rec.get("kind") or "")
        if rec.get("llm"):
            tag += " · " + t["llm"]
        doc.text(TAG_X, y_center + 4, tag, "s")


def fam_class(rec):
    return "fa" if is_fmg(rec) else "fo"


def percent_axis(doc, x0, scale, top, bottom, ticks=(0, 0.25, 0.5, 0.75, 1.0), label_y=None):
    for tick in ticks:
        x = x0 + tick * scale
        doc.line(x, top, x, bottom, "axis" if tick == 0 else "grid")
        if label_y is not None:
            doc.text(x, label_y, "%d%%" % round(tick * 100), "s", "middle")


# ------------------------------------------------------------------ a. ranking

def chart_ranking(summary, systems, theme, lang):
    t = TXT[lang]
    recs = rank_order(charted(summary))
    top = 86
    plot_h = ROW_H * len(recs)
    height = top + plot_h + 22 + 26 + 40
    doc = new_doc(summary, theme, lang, height)
    scale = 440.0
    header(doc, t["t_ranking"], family_legend(t) + [("tick", t["hit1_tick"])])
    percent_axis(doc, PLOT_X, scale, top - 6, top + plot_h, label_y=top + plot_h + 18)
    for i, rec in enumerate(recs):
        yc = top + i * ROW_H + ROW_H / 2
        o = rec["overall"]
        row_label(doc, rec, systems, yc)
        hit5, hit1 = o.get("hit5") or 0.0, o.get("hit1") or 0.0
        doc.bar(PLOT_X, yc - BAR_H / 2, hit5 * scale, BAR_H, doc.pal["accent"] if is_fmg(rec) else doc.pal["neutral"])
        x1 = PLOT_X + hit1 * scale
        doc.line(x1, yc - BAR_H / 2 - 3, x1, yc + BAR_H / 2 + 3, "tick")
        end = PLOT_X + max(hit5, hit1) * scale + 8
        doc.text(end, yc + 4.5, pct(hit5), "lb")
        doc.text(end + text_width(pct(hit5), SIZE_LABEL) + 8, yc + 4, "@1 " + pct(hit1), "s")
    note = t["ranking_note"] % (sum(v for k, v in summary["dataset"]["types"].items() if k != "quiet"))
    hidden = len(summary["systems"]) - len(recs)
    if hidden:
        note += " " + t["experiments"] % hidden
    footer(doc, summary, top + plot_h + 52, note)
    return doc.render()


# ------------------------------------------------------------------ b. by type

def chart_by_type(summary, systems, theme, lang, measure="hit5", title="t_bytype", note=None):
    """Three panels (lexical, paraphrase, updated), one bar per system; `measure` is the by_type key drawn."""
    t = TXT[lang]
    recs = rank_order(charted(summary))
    top = 112
    plot_h = ROW_H * len(recs)
    height = top + plot_h + 22 + 40
    doc = new_doc(summary, theme, lang, height)
    header(doc, t[title], family_legend(t))
    left = 214
    panel_w = (W - MARGIN - left) / 3.0
    scale = 150.0
    kinds = ("lexical", "paraphrase", "updated")
    for j, kind in enumerate(kinds):
        x0 = left + j * panel_w + 10
        n = summary["dataset"]["types"].get(kind, 0)
        doc.text(x0, top - 22, "%s · %d %s" % (t["types"][kind], n, t["questions_short"]), "lb")
        percent_axis(doc, x0, scale, top - 6, top + plot_h, ticks=(0, 0.5, 1.0), label_y=top + plot_h + 18)
    for i, rec in enumerate(recs):
        yc = top + i * ROW_H + ROW_H / 2
        row_label(doc, rec, systems, yc, with_tag=False)
        for j, kind in enumerate(kinds):
            x0 = left + j * panel_w + 10
            v = (rec["by_type"].get(kind) or {}).get(measure) or 0.0
            doc.bar(x0, yc - BAR_H / 2, v * scale, BAR_H, doc.pal["accent"] if is_fmg(rec) else doc.pal["neutral"])
            doc.text(x0 + v * scale + 6, yc + 4, pct(v), "s")
    footer(doc, summary, top + plot_h + 50, t[note] if note else None)
    return doc.render()


def chart_delivered_by_type(summary, systems, theme, lang):
    return chart_by_type(summary, systems, theme, lang, "deployed_hit", "t_dbytype", "dbytype_note")


# ------------------------------------------------------------------ c. delivered (scatter)

def _overlap(a, b, pad=2.0):
    """Overlap area of two boxes (x0, y0, x1, y1), each grown by pad."""
    w = min(a[2], b[2]) + pad - max(a[0], b[0]) + pad
    h = min(a[3], b[3]) + pad - max(a[1], b[1]) + pad
    return w * h if w > 0 and h > 0 else 0.0


def place_labels(points, obstacles, bounds, size=SIZE_LABEL, dot_r=5.0):
    """Choose a box for each label so that labels collide neither with each other, nor with a dot, nor
    with the obstacles. `points` is a list of (x, y, text); returns, in the same order, tuples
    (x_text, y_text_baseline, anchor, box, leader) where leader is a (x, y) end point on the label
    side or None. Candidates are tried near-first; a far candidate gets a leader line."""
    h = size + 2
    placed, leaders = [], []
    dots =[(x - dot_r, y - dot_r, x + dot_r, y + dot_r) for x, y, _ in points]
    # crowded points first: they have the fewest free candidates
    order = sorted(range(len(points)), key=lambda i: -sum(
        1 for j, p in enumerate(points) if j != i and abs(p[0] - points[i][0]) < 90 and abs(p[1] - points[i][1]) < 30))
    result = [None] * len(points)
    for i in order:
        x, y, text = points[i]
        w = text_width(text, size)
        cands = []
        for dist in (9, 20, 34, 50, 70, 95):
            cands += [("start", x + dist, y - h / 2), ("end", x - dist, y - h / 2),
                      ("middle", x, y - dist - h + 4), ("middle", x, y + dist - 2),
                      ("start", x + dist * 0.7, y - dist - h * 0.5), ("start", x + dist * 0.7, y + dist * 0.7 - 2),
                      ("end", x - dist * 0.7, y - dist - h * 0.5), ("end", x - dist * 0.7, y + dist * 0.7 - 2)]
        best = None
        for rank, (anchor, bx, by) in enumerate(cands):
            x0 = bx if anchor == "start" else bx - w if anchor == "end" else bx - w / 2
            box = (x0, by, x0 + w, by + h)
            cost = 0.0
            if box[0] < bounds[0] or box[2] > bounds[2] or box[1] < bounds[1] or box[3] > bounds[3]:
                cost += 1e5
            cost += sum(_overlap(box, other) for other in placed) * 50
            cost += sum(_overlap(box, d, 1.0) for d in dots) * 50
            cost += sum(_overlap(box, o) for o in obstacles) * 50
            # a leader line must not run through a label: neither this one's through an earlier
            # label, nor an earlier one's through this label
            end = _leader_end(x, y, box)
            if end:
                cost += sum(_segment_in_box((x, y) + end, other) for other in placed) * 5000
            cost += sum(_segment_in_box(seg, box) for seg in leaders) * 5000
            cost += rank * 0.5
            if best is None or cost < best[0]:
                best = (cost, anchor, bx, by, box)
            if cost < 1.0:
                break
        _, anchor, bx, by, box = best
        placed.append(box)
        leader = _leader_end(x, y, box)
        if leader:
            leaders.append((x, y) + leader)
        result[i] = (bx, by + size, anchor, box, leader)
    return result


def merge_coincident(points, within=7.0):
    """Dots that sit on top of each other share one label: "a b 65% / c 65%", the words the second
    label has in common with the first left out. `points` is a list of (x, y, text)."""
    merged = []
    for x, y, text in points:
        for k, (mx, my, mtext) in enumerate(merged):
            if math.hypot(x - mx, y - my) <= within:
                first, words = mtext.split(" "), text.split(" ")
                common = 0
                while common < min(len(first), len(words)) - 1 and first[common] == words[common]:
                    common += 1
                merged[k] = (mx, my, "%s / %s" % (mtext, " ".join(words[common:])))
                break
        else:
            merged.append((x, y, text))
    return merged


def _leader_end(x, y, box):
    """Where the leader line from the dot (x, y) meets its label box, or None when the label is close."""
    if math.hypot(max(box[0] - x, 0, x - box[2]), max(box[1] - y, 0, y - box[3])) <= 9:
        return None
    if box[1] <= y <= box[3]:
        return (box[0] if x < box[0] else box[2]), y
    return min(max(x, box[0]), box[2]), (box[3] if y > box[3] else box[1])


def _segment_in_box(seg, box, pad=2.0, steps=16):
    """How many of the sample points along a segment (x1, y1, x2, y2) fall inside a box."""
    x1, y1, x2, y2 = seg
    return sum(1 for k in range(steps + 1)
               if box[0] - pad <= x1 + (x2 - x1) * k / steps <= box[2] + pad
               and box[1] - pad <= y1 + (y2 - y1) * k / steps <= box[3] + pad)


def items_precision_frame(doc, left, right, top, plot_h, hi=32.0):
    """Grid and axis titles of the `delivered` scatter (x: items per question, log; y: precision), shared with
    `profiles`; returns the functions mapping a value to x and to y."""
    t = doc.t
    lo = 0.25

    def px(v):
        return left + (math.log2(v) - math.log2(lo)) / (math.log2(hi) - math.log2(lo)) * (right - left)

    def py(v):
        return top + plot_h - v * plot_h

    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        doc.line(left, py(tick), right, py(tick), "axis" if tick == 0 else "grid")
        doc.text(left - 8, py(tick) + 4, "%d%%" % round(tick * 100), "s", "end")
    for v in [x for x in (0.25, 0.5, 1, 2, 4, 8, 16, 32, 64, 128, 256) if x <= hi]:
        doc.line(px(v), top, px(v), top + plot_h, "grid")
        doc.text(px(v), top + plot_h + 18, ("%g" % v), "s", "middle")
    doc.text((left + right) / 2, top + plot_h + 38, t["delivered_x"], "s", "middle")
    doc.text(MARGIN, top - 14, t["delivered_y"], "s")
    return px, py


def chart_delivered(summary, systems, theme, lang):
    t = TXT[lang]
    recs = rank_order(charted(summary))
    pts, skipped = [], []
    for rec in recs:
        o = rec["overall"]
        if o.get("precision") is None or not o.get("avg_items"):
            skipped.append(label_of(rec["system"], systems, lang))
        else:
            pts.append(rec)
    left, right, top, plot_h = 70, W - MARGIN - 10, 104, 380
    height = top + plot_h + 78 + 40
    doc = new_doc(summary, theme, lang, height)
    header(doc, t["t_delivered"], family_legend(t))
    # the axis ends at 32 items; a system that delivers more (Hindsight: 84 to 94) widens it to the next power of two
    most = max([r["overall"]["avg_items"] for r in pts] or [1])
    px, py = items_precision_frame(doc, left, right, top, plot_h, hi=32.0 if most <= 32 else 2.0 ** math.ceil(math.log2(most)))
    corner = t["delivered_corner"]
    corner_box = (left + 6, top + 4, left + 6 + text_width(corner, SIZE_SMALL), top + 20)
    doc.text(left + 6, top + 16, corner, "s")
    points = [(px(r["overall"]["avg_items"]), py(r["overall"]["precision"]),
               "%s %s" % (label_of(r["system"], systems, lang), pct(r["overall"].get("deployed_hit") or 0.0, 0)))
              for r in pts]
    for rec, (x, y, _) in zip(pts, points):
        doc.circle(x, y, 5, doc.pal["accent"] if is_fmg(rec) else doc.pal["neutral"])
    labelled = merge_coincident(points)
    axis_band = (left, py(0) - 1.0, right, py(0) + 1.0)  # a label must not sit on the 0% axis (a dot on it, like lingshu's)
    placement = place_labels(labelled, [corner_box, axis_band], (left, top, right, top + plot_h + 14))
    for (x, y, _), (tx, ty, anchor, box, leader) in zip(labelled, placement):
        if leader:
            doc.line(x, y, leader[0], leader[1], "lead", color=doc.pal["ink2"])
    for (x, y, label), (tx, ty, anchor, box, leader) in zip(labelled, placement):
        doc.text(tx, ty - 2.5, label, "l", anchor)
    note = t["delivered_hint"]
    if skipped:
        note += " · " + t["delivered_skip"] % ", ".join(skipped)
    footer(doc, summary, top + plot_h + 62, note)
    return doc.render()


# ------------------------------------------------------------------ d. quiet

def chart_quiet(summary, systems, theme, lang):
    t = TXT[lang]
    recs = sorted(charted(summary), key=lambda s: (-(s["by_type"]["quiet"].get("quiet_rate") or 0), s["system"]))
    top = 86
    plot_h = ROW_H * len(recs)
    height = top + plot_h + 22 + 44 + 24
    doc = new_doc(summary, theme, lang, height)
    scale = 440.0
    header(doc, t["t_quiet"], family_legend(t))
    percent_axis(doc, PLOT_X, scale, top - 6, top + plot_h, label_y=top + plot_h + 18)
    for i, rec in enumerate(recs):
        yc = top + i * ROW_H + ROW_H / 2
        row_label(doc, rec, systems, yc)
        v = rec["by_type"]["quiet"].get("quiet_rate") or 0.0
        doc.bar(PLOT_X, yc - BAR_H / 2, v * scale, BAR_H, doc.pal["accent"] if is_fmg(rec) else doc.pal["neutral"])
        doc.text(PLOT_X + v * scale + 8, yc + 4.5, pct(v), "lb")
    n = summary["dataset"]["types"].get("quiet", 0)
    footer(doc, summary, top + plot_h + 50, t["quiet_note"] + " " + t["quiet_n"] % n)
    return doc.render()


# ------------------------------------------------------------------ e. cost

def log_pos(value, lo, hi, x0, width):
    value = max(value, lo)
    return x0 + (math.log10(value) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)) * width


def chart_cost(summary, systems, theme, lang):
    t = TXT[lang]
    recs = rank_order(charted(summary))
    top = 112
    plot_h = ROW_H * len(recs)
    height = top + plot_h + 22 + 44 + 24
    doc = new_doc(summary, theme, lang, height)
    header(doc, t["t_cost"], family_legend(t))
    panels = (
        {"x0": 214, "w": 230, "lo": 1.0, "hi": 2000.0, "ticks": (1, 10, 100, 1000), "title": t["cost_lat"],
         "get": lambda r: r["overall"].get("latency_p50_ms")},
        {"x0": 530, "w": 230, "lo": 0.01, "hi": 2000.0, "ticks": (0.01, 0.1, 1, 10, 100, 1000),
         "title": t["cost_ing"], "get": lambda r: r.get("ingest_seconds")},
    )
    tokens_x = W - MARGIN
    for p in panels:
        doc.text(p["x0"], top - 24, p["title"], "lb")
        for tick in p["ticks"]:
            x = log_pos(tick, p["lo"], p["hi"], p["x0"], p["w"])
            doc.line(x, top - 8, x, top + plot_h, "axis" if tick == p["ticks"][0] else "grid")
            doc.text(x, top + plot_h + 18, ("%g" % tick), "s", "middle")
    doc.text(tokens_x, top - 24, t["cost_tok"], "lb", "end")
    for i, rec in enumerate(recs):
        yc = top + i * ROW_H + ROW_H / 2
        row_label(doc, rec, systems, yc, with_tag=False)
        fill = doc.pal["accent"] if is_fmg(rec) else doc.pal["neutral"]
        for p in panels:
            v = p["get"](rec)
            if v is None:
                continue
            x = log_pos(v, p["lo"], p["hi"], p["x0"], p["w"])
            doc.line(p["x0"], yc, x, yc, "lead", color=fill)       # stem: where the dot sits relative to the axis
            doc.circle(x, yc, 5, fill)
            doc.text(x + 10, yc + 4, fmt_number(v), "s")
        if rec.get("llm_tokens"):
            doc.text(tokens_x, yc + 4, fmt_tokens(rec["llm_tokens"]), "s", "end")
    footer(doc, summary, top + plot_h + 50, t["cost_note"])
    return doc.render()


# ------------------------------------------------------------------ f. forgetting

def load_forgetting(directory=FORGETTING_DIR):
    """{backend: summary dict} for every <backend>/summary.json that exists and parses."""
    found = {}
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return found
    for name in names:
        path = os.path.join(directory, name, "summary.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("timeline"):
            found[name] = data
    return found


def arm_series(timeline, key):
    """{arm: [(day, value), ...]} in day order; rows without the key are skipped."""
    series = {}
    for row in timeline:
        if key in row and row[key] is not None and "arm" in row and "day" in row:
            series.setdefault(row["arm"], []).append((row["day"], row[key]))
    return {arm: sorted(points) for arm, points in series.items()}


def arm_colors(arms, doc):
    """The baseline wears the secondary ink; the other arms take categorical slots 1-3 in a fixed order
    by arm name, so an arm keeps its colour whichever other arms are present."""
    order = ("default", "default_pinned", "aggressive")
    colors = {}
    extra = [a for a in arms if a not in order and a != "baseline"]
    for arm in arms:
        if arm == "baseline":
            colors[arm] = doc.pal["ink2"]
        else:
            idx = order.index(arm) if arm in order else len(order) + extra.index(arm)
            colors[arm] = doc.pal["arm"][idx % len(doc.pal["arm"])]
    return colors


def spread_labels(ys, min_gap, lo, hi):
    """Vertical label positions as close as possible to ys with at least min_gap between neighbours,
    kept inside [lo, hi]. Input and output are in the same order."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    pos = [ys[i] for i in order]
    for _ in range(50):
        moved = False
        for k in range(1, len(pos)):
            if pos[k] - pos[k - 1] < min_gap - 1e-6:
                shift = (min_gap - (pos[k] - pos[k - 1])) / 2
                pos[k - 1] -= shift
                pos[k] += shift
                moved = True
        pos = [min(max(p, lo), hi) for p in pos]
        if not moved:
            break
    # labels pinned against a bound can end up closer than min_gap: push them away from the lower bound
    for k in range(len(pos)):
        pos[k] = max(pos[k], lo if k == 0 else pos[k - 1] + min_gap)
    out = [0.0] * len(ys)
    for k, i in enumerate(order):
        out[i] = pos[k]
    return out


def forgetting_panel(doc, x0, top, w, h, series, colors, names, ymin, ymax, ytitle, yfmt, yticks, days_max,
                     with_xlabels):
    """One panel: a line per arm, directly labelled at its right end (with leader lines when they crowd)."""
    def px(day):
        return x0 + day / float(days_max) * w

    def py(v):
        return top + h - min(max((v - ymin) / float(ymax - ymin), 0.0), 1.0) * h

    doc.text(x0 - 46, top - 14, ytitle, "lb")
    for tick in yticks:
        doc.line(x0, py(tick), x0 + w, py(tick), "axis" if tick == ymin else "grid")
        doc.text(x0 - 8, py(tick) + 4, yfmt(tick), "s", "end")
    for day in range(0, days_max + 1, 90):
        doc.line(px(day), top + h, px(day), top + h + 4, "axis")
        if with_xlabels:
            doc.text(px(day), top + h + 18, str(day), "s", "middle")
    arms = [a for a in names if a in series]
    ends = {a: series[a][-1] for a in arms}
    for arm in arms:   # the baseline first, so it sits under the arms that coincide with it
        pts = " ".join("%.1f,%.1f" % (px(d), py(v)) for d, v in series[arm])
        doc.add('<polyline points="%s" fill="none" stroke="%s" stroke-width="2" stroke-linejoin="round" '
                'stroke-linecap="round"/>' % (pts, colors[arm]))
    label_x = x0 + w + 34
    ys = spread_labels([py(ends[a][1]) for a in arms], 16, top + 6, top + h - 2)
    for arm, ly in zip(arms, ys):
        ex, ey = px(ends[arm][0]), py(ends[arm][1])
        doc.line(ex, ey, label_x - 6, ly, "lead", color=colors[arm])
        doc.circle(ex, ey, 4, colors[arm])
        doc.text(label_x, ly + 4, names[arm], "s")


def coincident_arms(series):
    """Groups (as lists) of arms whose series are identical in every point."""
    groups = {}
    for arm, pts in series.items():
        groups.setdefault(tuple(pts), []).append(arm)
    return [g for g in groups.values() if len(g) > 1]


def hit_axis(all_series):
    """(lo, hi, ticks) of the Hit@5 panels, shared by every backend so they can be compared. The
    axis does not start at 0 (tick labels say so): the arms differ by a few points."""
    values = [v for series in all_series for pts in series.values() for _, v in pts]
    if not values:
        return 0.0, 1.0, (0, 0.25, 0.5, 0.75, 1.0)
    lo = max(0.0, math.floor((min(values) - 0.1) * 10) / 10.0)
    hi = 1.0
    step = 0.1 if hi - lo <= 0.5 else 0.25
    ticks, v = [], lo
    while v <= hi + 1e-9:
        ticks.append(round(v, 4))
        v += step
    return lo, hi, tuple(ticks)


def chart_forgetting(summary, systems, theme, lang, forgetting=None):
    t = TXT[lang]
    forgetting = load_forgetting() if forgetting is None else forgetting
    if not forgetting:
        return None
    blocks = [(b, forgetting[b]) for b in sorted(forgetting)]
    panel_h = 130
    block_h = 58 + panel_h + 56 + panel_h + 56
    top0 = 58
    footer_y = top0 + len(blocks) * block_h + 4
    height = footer_y + 18 * 2 + 30
    doc = new_doc(summary, theme, lang, height)
    doc.text(MARGIN, TITLE_Y, t["t_forget"], "t")
    x0, w = 86, 560
    hit_series = [arm_series(d["timeline"], "hit5_hot") for _, d in blocks]
    lo, hi, hticks = hit_axis(hit_series)
    notes = []
    y = top0
    for (backend, data), hit in zip(blocks, hit_series):
        timeline = data["timeline"]
        known = ("baseline", "default", "default_pinned", "aggressive")
        arms = sorted({r["arm"] for r in timeline if "arm" in r},
                      key=lambda a: (known.index(a) if a in known else 9, a))
        colors = arm_colors(arms, doc)
        names = {a: t["arms"].get(a, a) for a in arms}
        days_max = max((r.get("day", 0) for r in timeline), default=540) or 1
        mems = data.get("memories") or max((r.get("in_store", 0) for r in timeline), default=1) or 1
        doc.text(MARGIN, y + 14, t["backends"].get(backend, backend), "lb")
        top1 = y + 58
        forgetting_panel(doc, x0, top1, w, panel_h, arm_series(timeline, "in_store"), colors, names, 0.0, float(mems),
                         t["f_store"], lambda v: "%d" % v, (0, mems / 2, mems), days_max, False)
        top2 = top1 + panel_h + 56
        forgetting_panel(doc, x0, top2, w, panel_h, hit, colors, names, lo, hi,
                         t["f_hit"], lambda v: "%d%%" % round(v * 100), hticks, days_max, True)
        doc.text(x0 + w / 2, top2 + panel_h + 38, t["f_days"], "s", "middle")
        y += block_h
        for group in coincident_arms(hit):
            notes.append("%s: %s" % (t["backends"].get(backend, backend).split(" (")[0].split("（")[0],
                                     " = ".join(names[a] for a in group)))
    note = t["f_note"] % (lo * 100)
    if notes:
        note += " " + t["f_overlap"] % "; ".join(notes)
    footer(doc, summary, footer_y + 16, note)
    return doc.render()


# ------------------------------------------------------------------ g. deployment tiers

def load_tiers(path=TIERS_PATH):
    """The rows of bench/tiers.json (a list of dicts), [] when the file is missing or malformed."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    rows = data.get("tiers") if isinstance(data, dict) else None
    return [r for r in rows if isinstance(r, dict) and r.get("system")] if isinstance(rows, list) else []


def fmt_memory(mib):
    return "%.2f GiB" % (mib / 1024.0) if mib >= 1024 else "%d MiB" % round(mib)


def fmt_file(mb):
    if mb >= 1000:
        return "%.2f GB" % (mb / 1000.0)
    return "%.1f MB" % mb if mb < 100 else "%d MB" % round(mb)


def chart_tiers(summary, systems, theme, lang, tiers=None):
    """Six feiyu-memgate configurations in rows, ordered by memory use; small multiples sharing the row labels."""
    t = TXT[lang]
    tiers = load_tiers() if tiers is None else tiers
    rows = [(row, summary["systems"][row["system"]]) for row in tiers if row["system"] in summary["systems"]]
    if len(rows) < 2:
        return None
    rows.sort(key=lambda rr: (rr[0].get("resident_mib") is not None, rr[0].get("resident_mib") or 0.0))
    top, row_h = 112, 32
    plot_h = row_h * len(rows)
    doc = new_doc(summary, theme, lang, top + plot_h + 22 + 80)
    header(doc, t["t_tiers"], [("fa", t["family_fmg"])])
    measures = (lambda r: r["overall"].get("hit5"), lambda r: r["overall"].get("deployed_hit"),
                lambda r: r["overall"].get("precision"), lambda r: (r["by_type"].get("quiet") or {}).get("quiet_rate"))
    x0, pitch, scale = 196, 104, 58.0
    for j, title in enumerate(t["tiers_cols"]):
        px0 = x0 + j * pitch
        doc.text(px0, top - 24, title, "lb")
        percent_axis(doc, px0, scale, top - 8, top + plot_h, ticks=(0, 0.5, 1.0), label_y=top + plot_h + 18)
    mem_x0, mem_w, lo, hi = 640.0, 190.0, 100.0, 2000.0
    doc.text(mem_x0, top - 24, t["tiers_mem"], "lb")
    for tick in (100, 200, 500, 1000, 2000):
        x = log_pos(tick, lo, hi, mem_x0, mem_w)
        doc.line(x, top - 8, x, top + plot_h, "axis" if tick == 100 else "grid")
        doc.text(x, top + plot_h + 18, "%d" % tick, "s", "middle")
    file_x = W - MARGIN
    doc.text(file_x, top - 24, t["tiers_file"], "lb", "end")
    fill = doc.pal["accent"]
    for i, (row, rec) in enumerate(rows):
        yc = top + i * row_h + row_h / 2
        doc.text(LABEL_X, yc + 4.5, (row.get("label") or {}).get(lang) or row["system"], "l")
        twin = summary["systems"].get(row["system"] + "-precise")
        for j, get in enumerate(measures):
            v = get(rec)
            px0 = x0 + j * pitch
            if v is None:
                doc.text(px0 + 4, yc + 4, "-", "s")
                continue
            doc.bar(px0, yc - BAR_H / 2, v * scale, BAR_H, fill)
            tv = get(twin) if twin is not None and j > 0 else None   # precise profile pinned; the ranking is the same
            if tv is not None:
                tx = px0 + tv * scale
                doc.line(tx, yc - BAR_H / 2 - 3, tx, yc + BAR_H / 2 + 3, "lead", width=2.5, color=doc.pal["ink"])
            doc.text(px0 + max(v, tv or 0.0) * scale + 6, yc + 4, pct(v, 0), "s")
        mem, size = row.get("resident_mib"), row.get("file_mb")
        if mem is None:
            doc.text(mem_x0 + 4, yc + 4, t["tiers_none"], "s")
        else:
            x = log_pos(mem, lo, hi, mem_x0, mem_w)
            doc.line(mem_x0, yc, x, yc, "lead", color=fill)
            doc.circle(x, yc, 5, fill)
            doc.text(x + 10, yc + 4, fmt_memory(mem), "s")
        doc.text(file_x, yc + 4, t["tiers_none"] if size is None else fmt_file(size), "s", "end")
    footer(doc, summary, top + plot_h + 52, t["tiers_note"])
    return doc.render()


# ------------------------------------------------------------------ h. quality (what reaches the model)

def chart_quality(summary, systems, theme, lang):
    t = TXT[lang]
    recs = sorted(charted(summary), key=lambda s: (-(s["overall"].get("delivered_f1") or 0), s["system"]))
    top = 112
    plot_h = ROW_H * len(recs)
    doc = new_doc(summary, theme, lang, top + plot_h + 100)
    header(doc, t["t_quality"], family_legend(t))
    left, items_w = 214, 74
    panel_w = (W - MARGIN - left - items_w) / 3.0
    scale = 150.0
    get = (lambda r: r["overall"].get("delivered_f1"), lambda r: r["overall"].get("precision"),
           lambda r: (r["by_type"].get("quiet") or {}).get("quiet_rate"))
    for j, title in enumerate(t["quality_cols"]):
        x0 = left + j * panel_w + 10
        doc.text(x0, top - 22, title, "lb")
        percent_axis(doc, x0, scale, top - 6, top + plot_h, ticks=(0, 0.5, 1.0), label_y=top + plot_h + 18)
    items_x = W - MARGIN
    doc.text(items_x, top - 22, t["quality_items"], "lb", "end")
    for i, rec in enumerate(recs):
        yc = top + i * ROW_H + ROW_H / 2
        row_label(doc, rec, systems, yc, with_tag=False)
        fill = doc.pal["accent"] if is_fmg(rec) else doc.pal["neutral"]
        for j, g in enumerate(get):
            x0 = left + j * panel_w + 10
            v = g(rec)
            if v is None:
                doc.text(x0 + 4, yc + 4, "–", "s")      # undefined: nothing was delivered
                continue
            doc.bar(x0, yc - BAR_H / 2, v * scale, BAR_H, fill)
            doc.text(x0 + v * scale + 6, yc + 4, pct(v), "s")
        doc.text(items_x, yc + 4, "%.2f" % rec["overall"]["avg_items"], "s", "end")
    footer(doc, summary, top + plot_h + 50, t["quality_note"])
    return doc.render()


# ------------------------------------------------------------------ i. Hit@k curves

DASHES = ("", "7 4", "2 4", "9 4 2 4", "14 5")   # solid first: the feiyu-memgate configurations tell apart by dash


def hit_curves(summary):
    """[(record, [hit@1..hit@10])] of the charted systems that have the series, in rank order."""
    out = []
    for rec in rank_order(charted(summary)):
        series = rec["overall"].get("hit_at_k")
        if isinstance(series, list) and series:
            out.append((rec, [float(v) for v in series]))
    return out


def coincident_curves(curves, digits=6):
    """Group curves that are identical at every k: a list of lists of (record, series), first-seen order."""
    groups = {}
    for rec, series in curves:
        groups.setdefault(tuple(round(v, digits) for v in series), []).append((rec, series))
    return list(groups.values())


def chart_hit_at_k(summary, systems, theme, lang):
    t = TXT[lang]
    curves = hit_curves(summary)
    if not curves:
        return None
    groups = coincident_curves(curves)
    n = max(len(series) for _, series in curves)
    left, plot_w, top, plot_h = 70, 520, 112, 400
    label_x = left + plot_w + 46
    doc = new_doc(summary, theme, lang, top + plot_h + 100)
    header(doc, t["t_hitk"], family_legend(t))

    def px(k):
        return left + (k - 1) / float(max(n - 1, 1)) * plot_w

    def py(v):
        return top + plot_h - v * plot_h

    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        doc.line(left, py(tick), left + plot_w, py(tick), "axis" if tick == 0 else "grid")
        doc.text(left - 8, py(tick) + 4, "%d%%" % round(tick * 100), "s", "end")
    for k in range(1, n + 1):
        doc.line(px(k), top, px(k), top + plot_h, "grid")
        doc.text(px(k), top + plot_h + 18, str(k), "s", "middle")
    doc.text(left + plot_w / 2, top + plot_h + 38, t["hitk_x"], "s", "middle")
    doc.text(MARGIN, top - 14, t["hitk_y"], "s")
    # grey first, blue on top: where a blue line coincides with a grey one the blue stays visible
    drawn = [(g, any(is_fmg(r) for r, _ in g), g[0][1]) for g in groups]
    drawn.sort(key=lambda d: d[1])
    fmg_groups = [g for g, fam, _ in drawn if fam]
    ends = []
    for group, fam, series in drawn:
        colour = doc.pal["accent"] if fam else doc.pal["neutral"]
        dash = DASHES[fmg_groups.index(group) % len(DASHES)] if fam else ""
        pts = " ".join("%.1f,%.1f" % (px(k + 1), py(v)) for k, v in enumerate(series))
        doc.add('<polyline points="%s" fill="none" stroke="%s" stroke-width="2" stroke-linejoin="round" '
                'stroke-linecap="round"%s/>' % (pts, colour, (' stroke-dasharray="%s"' % dash) if dash else ""))
        names = [label_of(r["system"], systems, lang) for r, _ in group]
        ends.append((px(len(series)), py(series[-1]), colour, " = ".join(names), fam))
    ys = spread_labels([e[1] for e in ends], 17, top - 8, top + plot_h - 2)
    for (ex, ey, colour, label, fam), ly in zip(ends, ys):
        doc.line(ex, ey, label_x - 6, ly, "lead", color=colour)
        doc.circle(ex, ey, 4, colour)
        doc.text(label_x, ly + 4, label, "lb" if fam else "l")
    footer(doc, summary, top + plot_h + 62, t["hitk_note"])
    return doc.render()


# ------------------------------------------------------------------ j. context spent

def fmt_chars(value):
    return "%d" % round(value)


def chart_context(summary, systems, theme, lang):
    t = TXT[lang]
    recs = sorted(charted(summary), key=lambda s: (s["overall"].get("avg_chars") or 0, s["system"]))
    top = 112
    plot_h = ROW_H * len(recs)
    doc = new_doc(summary, theme, lang, top + plot_h + 100)
    values = [r["overall"].get("avg_chars") or 0 for r in recs]
    top_value = max(values)
    # a linear axis is honest only while the smallest bars stay readable; when one system dwarfs the
    # others (smallest bar under 3 % of the axis) the axis is logarithmic and a bar becomes two dots
    use_log = min(values) < 0.03 * top_value
    if use_log:
        lo, hi, width = 1.0, 10.0 ** math.ceil(math.log10(max(top_value, 10.0))), 400.0
        ticks = [10 ** e for e in range(0, int(math.log10(hi)) + 1)]

        def xpos(v):
            return log_pos(v, lo, hi, PLOT_X, width)
        axis_note = t["context_axis_log"]
        legend = [("tint", t["context_light_log"])]
        title = t["t_context_log"]
    else:
        step = 100 if top_value > 250 else 50
        hi = int(math.ceil(top_value / float(step)) * step)
        ticks = list(range(0, hi + 1, step))

        def xpos(v):
            return PLOT_X + v * 400.0 / hi
        axis_note = None
        legend = [("tint", t["context_light"])]
        title = t["t_context"]
    header(doc, title, family_legend(t) + legend)
    for tick in ticks:
        x = xpos(tick)
        doc.line(x, top - 6, x, top + plot_h, "axis" if tick == ticks[0] else "grid")
        doc.text(x, top + plot_h + 18, "%d" % tick, "s", "middle")
    doc.text(xpos(hi), top + plot_h + 38, t["context_chars"], "s", "end")
    for i, rec in enumerate(recs):
        yc = top + i * ROW_H + ROW_H / 2
        row_label(doc, rec, systems, yc)
        o = rec["overall"]
        chars, precision = o.get("avg_chars") or 0.0, o.get("precision")
        fill = doc.pal["accent"] if is_fmg(rec) else doc.pal["neutral"]
        correct = None if precision is None else chars * precision
        if use_log:
            x_all = xpos(chars)
            if correct:
                doc.line(xpos(correct), yc, x_all, yc, "lead", color=fill)
                doc.circle(xpos(correct), yc, 5, fill)
            doc.circle(x_all, yc, 5, doc.pal["surface"])      # opaque base: the stem must not show through the tint
            doc.add('<circle cx="%.1f" cy="%.1f" r="5" fill="%s" fill-opacity="%s"/>' % (x_all, yc, fill, TINT))
            doc.add('<circle cx="%.1f" cy="%.1f" r="5" fill="none" stroke="%s" stroke-width="1.5"/>' % (x_all, yc, fill))
        else:
            doc.bar(PLOT_X, yc - BAR_H / 2, chars * (xpos(1) - xpos(0)), BAR_H, fill, TINT)
            if correct:
                doc.bar(PLOT_X, yc - BAR_H / 2, correct * (xpos(1) - xpos(0)), BAR_H, fill)
        label = (t["context_correct"] % (fmt_chars(chars), pct(precision, 0)) if precision is not None
                 else t["context_undefined"] % fmt_chars(chars))
        doc.text(xpos(chars) + 12, yc + 4.5, label, "lb")
    note = t["context_note_log" if use_log else "context_note"] + (" " + axis_note if axis_note else "")
    footer(doc, summary, top + plot_h + 62, note)
    return doc.render()


# ------------------------------------------------------------------ k. fresh-first among delivered answers

FRESH_MIN = 5       # fewer such questions than this: the share says nothing, the system is footnoted


def fresh_counts(rec):
    o = rec["overall"]
    return int(o.get("delivered_fresh_first_wins") or 0), int(o.get("delivered_fresh_first_questions") or 0)


def chart_fresh(summary, systems, theme, lang):
    t = TXT[lang]
    twins = {base + "-precise" for base in TWIN_BASES}
    precise = [s for s in summary["systems"].values() if s.get("variant") and s["system"] in twins]
    others = [s for s in summary["systems"].values()
              if s.get("variant") and is_fmg(s) and not s["system"].endswith("-precise")]
    recs, small = [], []
    for rec in rank_order(charted(summary) + precise):
        (recs if fresh_counts(rec)[1] >= FRESH_MIN else small).append(rec)
    if not recs:
        return None
    top = 86
    plot_h = ROW_H * len(recs)
    doc = new_doc(summary, theme, lang, top + plot_h + 120)
    header(doc, t["t_fresh"], family_legend(t))
    scale = 440.0
    percent_axis(doc, PLOT_X, scale, top - 6, top + plot_h, label_y=top + plot_h + 18)
    for i, rec in enumerate(recs):
        yc = top + i * ROW_H + ROW_H / 2
        row_label(doc, rec, systems, yc, with_tag=False)
        wins, questions = fresh_counts(rec)
        v = wins / float(questions)
        doc.bar(PLOT_X, yc - BAR_H / 2, v * scale, BAR_H, doc.pal["accent"] if is_fmg(rec) else doc.pal["neutral"])
        text = "%d/%d" % (wins, questions)
        doc.text(PLOT_X + v * scale + 8, yc + 4.5, text, "lb")
        doc.text(PLOT_X + v * scale + 8 + text_width(text, SIZE_LABEL) + 8, yc + 4, pct(v, 0), "s")
    parts = []
    if small:
        parts.append(t["fresh_note"] % ", ".join(
            "%s %d/%d" % ((label_of(r["system"], systems, lang),) + fresh_counts(r)) for r in small))
    if precise:
        parts.append(t["fresh_precise_in"])
    if others:
        parts.append(t["fresh_variants"] % ", ".join(
            "%s %d/%d" % ((label_of(r["system"], systems, lang).replace("feiyu-memgate embedding", "embedding"),)
                          + fresh_counts(r)) for r in others))
    footer(doc, summary, top + plot_h + 52, " ".join(parts) or None)
    return doc.render()


# ------------------------------------------------------------------ l. precise versus broad profile

# The plain id is the broad (default) profile, base + "-precise" the other. `full` is hybrid plus freshness: its two
# points sit on top of hybrid's, so the `profiles` chart leaves it out (and says so); `fresh` draws all five twins.
PROFILE_BASES = ("feiyu-memgate-embedding", "feiyu-memgate-openviking", "feiyu-memgate-hybrid", "feiyu-memgate-sqlite")
TWIN_BASES = PROFILE_BASES + ("feiyu-memgate-full",)
ORIENTATION = ("memsearch", "mem0-verbatim", "openviking")     # the grey dots that carry a name


def arrow_head(doc, x1, y1, x2, y2, colour, size=8.0):
    """A filled arrowhead whose tip is (x2, y2), pointing away from (x1, y1)."""
    angle = math.atan2(y2 - y1, x2 - x1)
    a, b = angle + math.radians(155), angle - math.radians(155)
    doc.add('<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="%s"/>' % (
        x2, y2, x2 + size * math.cos(a), y2 + size * math.sin(a), x2 + size * math.cos(b), y2 + size * math.sin(b),
        colour))


def chart_profiles(summary, systems, theme, lang):
    t = TXT[lang]
    pairs = [(summary["systems"][b + "-precise"], summary["systems"][b]) for b in PROFILE_BASES
             if b in summary["systems"] and b + "-precise" in summary["systems"]]
    if not pairs:
        return None
    pair_names = {r["system"] for pair in pairs for r in pair}
    greys, skipped = [], []
    for rec in rank_order(charted(summary)):
        if is_fmg(rec) or rec["system"] in pair_names:
            continue
        o = rec["overall"]
        (greys if o.get("precision") is not None and o.get("avg_items") else skipped).append(rec)
    left, right, top, plot_h = 70, W - MARGIN - 10, 104, 380
    doc = new_doc(summary, theme, lang, top + plot_h + 160)
    header(doc, t["t_profiles"], family_legend(t))
    px, py = items_precision_frame(doc, left, right, top, plot_h)
    accent, neutral, surface = doc.pal["accent"], doc.pal["neutral"], doc.pal["surface"]

    def xy(rec):
        o = rec["overall"]
        return min(px(o["avg_items"]), right), py(o["precision"])   # beyond 32 items: pinned to the right edge, said in the note

    off_axis = [r for r in greys if r["overall"]["avg_items"] > 32.0]
    for rec in greys:
        x, y = xy(rec)
        doc.circle(x, y, 4, neutral)
    points, obstacles = [], []
    for rec in greys:
        if rec["system"] in ORIENTATION:
            x, y = xy(rec)
            points.append((x, y, label_of(rec["system"], systems, lang)))
    for precise, wide in pairs:
        (x1, y1), (x2, y2) = xy(precise), xy(wide)
        length = math.hypot(x2 - x1, y2 - y1) or 1.0
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        doc.line(x1 + ux * 6, y1 + uy * 6, x2 - ux * 10, y2 - uy * 10, "lead", width=2, color=accent)
        arrow_head(doc, x1, y1, x2 - ux * 6, y2 - uy * 6, accent)
        doc.add('<circle cx="%.1f" cy="%.1f" r="5" fill="%s" stroke="%s" stroke-width="2.5"/>' % (x1, y1, surface, accent))
        doc.circle(x2, y2, 6, accent)
        short = label_of(wide["system"], systems, lang).replace("feiyu-memgate ", "")
        for rec, (x, y) in ((precise, (x1, y1)), (wide, (x2, y2))):   # hollow = precise, filled = broad: no word needed
            points.append((x, y, "%s %s" % (short, pct(rec["overall"].get("deployed_hit") or 0.0, 0))))
        steps = max(8, int(length / 8))      # the arrow is an obstacle for the labels: a box every 8 px or so
        for k in range(1, steps):
            ax, ay = x1 + (x2 - x1) * k / float(steps), y1 + (y2 - y1) * k / float(steps)
            obstacles.append((ax - 3, ay - 3, ax + 3, ay + 3))
    placement = place_labels(points, obstacles, (left, top, right, top + plot_h + 14))
    for (x, y, _), (tx, ty, anchor, box, leader) in zip(points, placement):
        if leader:
            doc.line(x, y, leader[0], leader[1], "lead", color=doc.pal["ink2"])
    for (x, y, label), (tx, ty, anchor, box, leader) in zip(points, placement):
        doc.text(tx, ty - 2.5, label, "l", anchor)
    unnamed = [label_of(r["system"], systems, lang) for r in greys if r["system"] not in ORIENTATION]
    note = t["profiles_hint"]
    if "feiyu-memgate-full" in summary["systems"]:
        note += " " + t["profiles_full"]
    if unnamed:
        note += " " + t["profiles_others"] % ", ".join(unnamed)
    if off_axis:
        note += " " + t["profiles_off"] % ", ".join("%s %.1f" % (label_of(r["system"], systems, lang), r["overall"]["avg_items"])
                                                    for r in off_axis)
    if skipped:
        note += " " + t["delivered_skip"] % ", ".join(label_of(r["system"], systems, lang) for r in skipped)
    footer(doc, summary, top + plot_h + 62, note)
    return doc.render()


# ------------------------------------------------------------------ driver

BUILDERS = {"ranking": chart_ranking, "by-type": chart_by_type, "delivered": chart_delivered,
            "quiet": chart_quiet, "cost": chart_cost, "quality": chart_quality, "hit-at-k": chart_hit_at_k,
            "delivered-by-type": chart_delivered_by_type, "context": chart_context, "fresh": chart_fresh,
            "profiles": chart_profiles}


def render_chart(name, summary, systems, theme, lang, forgetting=None, tiers=None):
    """SVG text of one chart, or None when its data is missing."""
    if name == "forgetting":
        return chart_forgetting(summary, systems, theme, lang, forgetting)
    if name == "tiers":
        return chart_tiers(summary, systems, theme, lang, tiers)
    return BUILDERS[name](summary, systems, theme, lang)


def render_all(summary, systems, out_dir, forgetting=None, charts=CHARTS, langs=LANGS, themes=THEMES, tiers=None):
    """Write every chart; returns the list of written paths."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name in charts:
        for lang in langs:
            for theme in themes:
                svg = render_chart(name, summary, systems, theme, lang, forgetting, tiers)
                if svg is None:
                    continue
                path = os.path.join(out_dir, "%s.%s.%s.svg" % (name, lang, theme))
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(svg)
                written.append(path)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the benchmark charts as SVG.")
    parser.add_argument("--out", default=OUT_DIR, help="output directory (default docs/assets/charts)")
    args = parser.parse_args(argv)
    try:
        with open(SUMMARY_PATH, encoding="utf-8") as f:
            summary = json.load(f)
    except (OSError, ValueError) as error:
        print("cannot read %s: %s" % (SUMMARY_PATH, type(error).__name__), file=sys.stderr)
        return 1
    try:
        with open(SYSTEMS_PATH, encoding="utf-8") as f:
            systems = json.load(f)
    except (OSError, ValueError):
        systems = {}
    paths = render_all(summary, systems, args.out, load_forgetting())
    print("wrote %d chart file(s) to %s" % (len(paths), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
