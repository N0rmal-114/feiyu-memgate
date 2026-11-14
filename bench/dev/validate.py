"""Checks the development set (dev-v1) and reports how much each question shares with its answer.

Run from the repository root:  python bench/dev/validate.py

Same checks as bench/data/validate.py, with this set's counts and id prefixes (memories d###,
questions q###). The checks are copied, not imported, so this file never reads the held-out data.
Ids, counts, references, duplicates, lengths, dates and update gaps are enforced (exit code 1 on
any problem). The character-bigram overlap between a question and its expected memory is only
REPORTED, so the lexical / paraphrase split is visible, never forced.
"""
import json
import os
import statistics
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))

from feiyu_memgate.core.tidy import bigram_similarity  # noqa: E402

KINDS = {"identity", "preference", "event", "plan", "entity"}
SOURCES = {"private", "group"}
EXPECTED_COUNTS = {"lexical": 30, "paraphrase": 38, "updated": 12, "quiet": 30}
TOTAL_MEMORIES, GROUP_MEMORIES = 160, 40
MIN_CHARS, MAX_CHARS = 15, 80
MIN_UPDATE_GAP_DAYS = 90
DATE_FROM, DATE_TO = date(2025, 1, 1), date(2026, 10, 31)


def load(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return json.load(f)


def parse_date(text):
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def check(memories, queries):
    problems = []

    def bad(message):
        problems.append(message)

    if len(memories) != TOTAL_MEMORIES:
        bad("memories: %d, expected %d" % (len(memories), TOTAL_MEMORIES))
    if sum(1 for m in memories.values() if m.get("source") == "group") != GROUP_MEMORIES:
        bad("group memories: expected %d" % GROUP_MEMORIES)
    seen_text = {}
    for mid, m in memories.items():
        if not (mid.startswith("d") and mid[1:].isdigit() and len(mid) == 4):
            bad("%s: id is not d###" % mid)
        if set(m) != {"text", "date", "source", "kind"}:
            bad("%s: wrong fields %s" % (mid, sorted(m)))
            continue
        if m["source"] not in SOURCES:
            bad("%s: source %r" % (mid, m["source"]))
        if m["kind"] not in KINDS:
            bad("%s: kind %r" % (mid, m["kind"]))
        d = parse_date(m["date"])
        if d is None or not DATE_FROM <= d <= DATE_TO:
            bad("%s: date %r outside 2025-01..2026-10" % (mid, m["date"]))
        if not MIN_CHARS <= len(m["text"]) <= MAX_CHARS:
            bad("%s: %d characters, allowed %d-%d" % (mid, len(m["text"]), MIN_CHARS, MAX_CHARS))
        if m["text"] in seen_text:
            bad("%s duplicates the text of %s" % (mid, seen_text[m["text"]]))
        seen_text[m["text"]] = mid
    dates = [m["date"] for _, m in sorted(memories.items())]
    if dates != sorted(dates):
        bad("ids are not in date order")
    if sorted(memories) != ["d%03d" % i for i in range(1, len(memories) + 1)]:
        bad("ids are not consecutive from d001")

    counts = {}
    seen_query = {}
    stale_used = set()
    for qid, q in queries.items():
        if not (qid.startswith("q") and qid[1:].isdigit() and len(qid) == 4):
            bad("%s: id is not q###" % qid)
        extra = set(q) - {"query", "expect", "type", "stale"}
        if extra or not {"query", "expect", "type"} <= set(q):
            bad("%s: wrong fields %s" % (qid, sorted(q)))
            continue
        counts[q["type"]] = counts.get(q["type"], 0) + 1
        if q["type"] not in EXPECTED_COUNTS:
            bad("%s: type %r" % (qid, q["type"]))
        if q["query"] in seen_query:
            bad("%s duplicates the question of %s" % (qid, seen_query[q["query"]]))
        seen_query[q["query"]] = qid
        for ref in list(q["expect"]) + list(q.get("stale", [])):
            if ref not in memories:
                bad("%s: unknown memory %s" % (qid, ref))
        if q["type"] == "quiet":
            if q["expect"] or "stale" in q:
                bad("%s: a quiet question must expect nothing" % qid)
        else:
            if not 1 <= len(q["expect"]) <= 2:
                bad("%s: expects %d memories" % (qid, len(q["expect"])))
        if q["type"] == "updated":
            if len(q["expect"]) != 1 or len(q.get("stale", [])) != 1:
                bad("%s: updated needs one expect and one stale" % qid)
            else:
                new, old = memories.get(q["expect"][0]), memories.get(q["stale"][0])
                if new and old:
                    gap = (parse_date(new["date"]) - parse_date(old["date"])).days
                    if gap < MIN_UPDATE_GAP_DAYS:
                        bad("%s: newer memory is only %d days after the stale one" % (qid, gap))
                    if q["stale"][0] in stale_used:
                        bad("%s: stale memory used twice" % qid)
                    stale_used.add(q["stale"][0])
        elif "stale" in q:
            bad("%s: only updated questions have stale" % qid)
    for kind, want in EXPECTED_COUNTS.items():
        if counts.get(kind, 0) != want:
            bad("%s questions: %d, expected %d" % (kind, counts.get(kind, 0), want))
    if len(queries) != sum(EXPECTED_COUNTS.values()):
        bad("questions: %d" % len(queries))
    return problems


def overlap_report(memories, queries):
    """Per type: mean/median/min/max of the best bigram_similarity to an expected memory."""
    lines = ["type        n   mean  median   min   max  share with overlap 0",
             "----------  --  ----  ------  ----  ----  --------------------"]
    for kind in ("lexical", "paraphrase", "updated"):
        scores = []
        for q in queries.values():
            if q["type"] == kind and q["expect"]:
                scores.append(max(bigram_similarity(q["query"], memories[r]["text"]) for r in q["expect"]))
        if not scores:
            continue
        zero = sum(1 for s in scores if s == 0) / len(scores)
        lines.append("%-10s  %2d  %.2f  %.2f    %.2f  %.2f  %.0f%%" % (
            kind, len(scores), statistics.mean(scores), statistics.median(scores),
            min(scores), max(scores), zero * 100))
    # Quiet questions: how close the nearest memory is (hard negatives should not be zero).
    best = [max(bigram_similarity(q["query"], m["text"]) for m in memories.values())
            for q in queries.values() if q["type"] == "quiet"]
    lines.append("quiet (best overlap with ANY memory): mean %.2f, max %.2f, above 0.3: %d of %d" % (
        statistics.mean(best), max(best), sum(1 for b in best if b > 0.3), len(best)))
    return lines


def main():
    memories, queries = load("memories.json"), load("queries.json")
    problems = check(memories, queries)
    kinds = {}
    for m in memories.values():
        kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
    print("memories: %d (group %d), kinds %s" % (
        len(memories), sum(1 for m in memories.values() if m["source"] == "group"), dict(sorted(kinds.items()))))
    types = {}
    for q in queries.values():
        types[q["type"]] = types.get(q["type"], 0) + 1
    print("questions: %d, types %s" % (len(queries), dict(sorted(types.items()))))
    print()
    print("\n".join(overlap_report(memories, queries)))
    print()
    if problems:
        print("PROBLEMS (%d):" % len(problems))
        for p in problems:
            print("  " + p)
        return 1
    print("OK: no problems")
    return 0


if __name__ == "__main__":
    sys.exit(main())
