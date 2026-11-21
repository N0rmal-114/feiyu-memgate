"""bench/chat: query variants, question test, link expansion, metrics, the runner, the sweep and the validator.

The runner tests use the small world in tests/fixtures/chat: the keyword (sqlite) backend needs no
embeddings, and the embedding and hybrid runs use the fake embeddings server of the backend tests.
"""
import contextlib
import copy
import importlib.util
import io
import json
import logging
import os
import sys
import tempfile
import unittest
from unittest import mock

from feiyu_memgate.client import build_query, previous_user_text

from tests.test_backend_embedding import FakeEmbeddingServer

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_CHAT = os.path.join(_ROOT, "bench", "chat")
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "chat")


def _load(name):
    """A module of bench/chat under the name run.py and sweep.py give it, so all of them share one copy.
    (Not a plain `import run`: bench/external has a run.py too.)"""
    key = "bench_chat_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, os.path.join(_CHAT, name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


methods = _load("methods")
run = _load("run")
sweep = _load("sweep")
validate = _load("validate")

# the fake model has no preset: the backend warns once, which the tests need not print
logging.getLogger("feiyu_memgate.backends.embedding").addHandler(logging.NullHandler())

USER = "我家的咖啡机最近老是出问题，早上做咖啡的时候总是漏水"
ASSISTANT = "那你有没有找过售后，也许还在保修期内可以免费修"
HISTORY = [{"role": "user", "content": USER}, {"role": "assistant", "content": ASSISTANT}]


def turn(role, content):
    return {"role": role, "content": content}


class QueryVariantsTest(unittest.TestCase):
    def test_v0_is_what_the_integrations_do(self):
        for message in ("后来呢", "今天打算做什么饭呢，想吃点清淡的", "嗯", "/help"):
            self.assertEqual(methods.query_v0(HISTORY, message), build_query(message, previous_user_text(HISTORY)))
        # a short message gets the whole previous user turn, not a tail
        self.assertEqual(methods.query_v0(HISTORY, "后来呢"), USER + "\n后来呢")

    def test_an_empty_history_gives_v0_for_every_variant(self):
        for message in ("后来呢", "那它多少钱", "今天打算做什么饭呢，想吃点清淡的", "嗯", "/help", "ok", "  "):
            wanted = build_query(message, None)
            for variant in methods.VARIANTS:
                found = methods.queries_for(variant, [], message)
                self.assertEqual(found, [wanted] if wanted is not None else [], (variant, message))
        self.assertEqual(methods.query_v1([], "后来呢"), "后来呢")
        self.assertEqual(methods.query_v2([], "后来呢"), "后来呢")
        # nothing to borrow from: assistant turns only, v1 has no user turn to take
        only_assistant = [turn("assistant", ASSISTANT)]
        self.assertEqual(methods.query_v1(only_assistant, "后来呢"), "后来呢")
        self.assertEqual(methods.query_v0(only_assistant, "后来呢"), "后来呢")

    def test_a_message_that_is_not_worth_a_lookup_stays_none(self):
        for variant in methods.VARIANTS:
            self.assertEqual(methods.queries_for(variant, HISTORY, "/help"), [])
            self.assertEqual(methods.queries_for(variant, HISTORY, "嗯"), [])

    def test_a_long_message_without_a_marker_is_not_extended(self):
        message = "我今天打算去超市买点水果和蔬菜回家做饭吃"
        self.assertGreaterEqual(len(message), 8)
        self.assertFalse(methods.needs_context(message))
        for query in (methods.query_v0(HISTORY, message), methods.query_v1(HISTORY, message),
                      methods.query_v2(HISTORY, message)):
            self.assertEqual(query, message)
        self.assertEqual(methods.queries_for("v3", HISTORY, message), [message])  # both queries are the same: one lookup

    def test_v1_takes_the_tail_of_the_last_user_turn(self):
        long_user = "".join(chr(0x4e00 + n) for n in range(200))
        history = [turn("user", long_user), turn("assistant", ASSISTANT)]
        query = methods.query_v1(history, "后来呢")
        self.assertEqual(query, long_user[-120:] + "\n后来呢")
        self.assertEqual(methods.query_v1(history, "后来呢", context_chars=10), long_user[-10:] + "\n后来呢")
        # the assistant's words are not used by v1
        self.assertNotIn(ASSISTANT, query)

    def test_a_marker_extends_a_long_message_and_the_lists_are_configurable(self):
        long_message = "那件事你还记得多少细节吗请说得详细一些"
        self.assertGreaterEqual(len(long_message), 8)
        self.assertEqual(methods.query_v1(HISTORY, long_message), USER[-120:] + "\n" + long_message)
        self.assertEqual(methods.query_v1(HISTORY, long_message, markers=("后来",)), long_message)
        self.assertEqual(methods.query_v1(HISTORY, "我想知道它的价格是多少钱", markers=()), "我想知道它的价格是多少钱")
        # short is a threshold on the meaningful length: 7 meaningful characters here
        message = "你觉得怎么样啊好吗"
        self.assertEqual(len(message), 9)
        self.assertEqual(methods.query_v1(HISTORY, message, short=8), message)
        self.assertEqual(methods.query_v1(HISTORY, message, short=12), USER[-120:] + "\n" + message)
        for marker in methods.DEICTIC:
            self.assertTrue(methods.needs_context("我想想" + marker + "个事情的经过怎么样", short=1))

    def test_v2_uses_both_turns_in_conversation_order_each_cut_to_half(self):
        user, assistant = "甲" * 100, "乙" * 100
        query = methods.query_v2([turn("user", user), turn("assistant", assistant)], "后来呢")
        self.assertEqual(query, "甲" * 60 + "\n" + "乙" * 60 + "\n后来呢")
        # the assistant spoke first: it comes first
        query = methods.query_v2([turn("assistant", assistant), turn("user", user)], "后来呢", context_chars=20)
        self.assertEqual(query, "乙" * 10 + "\n" + "甲" * 10 + "\n后来呢")
        # only the last turn of each role counts
        history = [turn("user", "旧的"), turn("assistant", "旧答"), turn("user", user), turn("assistant", assistant)]
        self.assertEqual(methods.query_v2(history, "后来呢"), "甲" * 60 + "\n" + "乙" * 60 + "\n后来呢")
        # one role only
        self.assertEqual(methods.query_v2([turn("assistant", "只有答复")], "后来呢"), "只有答复\n后来呢")

    def test_v3_asks_the_message_alone_and_the_v2_query(self):
        found = methods.queries_for("v3", HISTORY, "后来呢")
        self.assertEqual(found, ["后来呢", methods.query_v2(HISTORY, "后来呢")])
        self.assertEqual(methods.queries_for("v0", HISTORY, "后来呢"), [USER + "\n后来呢"])
        with self.assertRaises(ValueError):
            methods.queries_for("v9", HISTORY, "后来呢")

    def test_merge_keeps_the_higher_score_per_memory_sorted_and_cut(self):
        first = [("a", 0.5), ("b", 0.4)]
        second = [("b", 0.7), ("c", 0.6), ("a", 0.2)]
        self.assertEqual(methods.merge_scored([first, second], 5), [("b", 0.7), ("c", 0.6), ("a", 0.5)])
        self.assertEqual(methods.merge_scored([first, second], 2), [("b", 0.7), ("c", 0.6)])
        self.assertEqual(methods.merge_scored([[("x", 0.5)], [("y", 0.5)]], 5), [("x", 0.5), ("y", 0.5)])  # ties: first seen first
        self.assertEqual(methods.merge_scored([[], []], 5), [])

    def test_no_query_passes_500_characters(self):
        previous = "字" * 700
        history = [turn("user", previous), turn("assistant", previous)]
        messages = ["那" * 480, "后来呢", "那" + "好" * 498, "那" + "好" * 499, "那" * 600]
        for message in messages:
            for variant in methods.VARIANTS:
                for query in methods.queries_for(variant, history, message):
                    self.assertLessEqual(len(query), 500, (variant, len(message)))
                    self.assertTrue(query.endswith(message.strip()[:500]) or len(message) > 500)
        # 480 characters of message leave room for 19 characters of context and the newline
        query = methods.query_v1(history, "那" * 480)
        self.assertEqual(query, "字" * 19 + "\n" + "那" * 480)
        self.assertEqual(len(query), 500)
        self.assertEqual(len(methods.query_v2(history, "那" * 480)), 500)
        # a message of 499 leaves no room for context: the message alone
        self.assertEqual(methods.query_v1(history, "那" * 499), "那" * 499)
        self.assertEqual(methods.query_v2(history, "那" * 600), "那" * 500)

    def test_last_turn_reads_text_parts_and_skips_blank_turns(self):
        parts = [{"type": "text", "text": "第一"}, {"type": "image"}, {"type": "text", "text": "第二"}]
        self.assertEqual(methods.last_turn([turn("user", parts)], "user"), (0, "第一第二"))
        self.assertEqual(methods.last_turn([turn("user", "  "), turn("assistant", "答")], "user"), (None, None))
        self.assertEqual(methods.last_turn([], "user"), (None, None))
        self.assertEqual(methods.last_turn(None, "user"), (None, None))


class IsQuestionTest(unittest.TestCase):
    def test_marks_and_words(self):
        for text in ("你在哪？", "你在哪里?", "真的吗", "这是什么", "怎么办", "谁来了", "有多少人", "是不是下雨了",
                     "有没有伞", "为什么这样", "你还记得吗", "几点了"):
            self.assertTrue(methods.is_question(text), text)
        for text in ("最近天气有点热", "年糕呢", "今天好累", "", "咖啡机又响了"):
            self.assertFalse(methods.is_question(text), text)

    def test_the_word_list_is_configurable_and_the_marks_are_not(self):
        self.assertFalse(methods.is_question("真的吗", words=("什么",)))
        self.assertTrue(methods.is_question("真的吧", words=("吧",)))
        self.assertTrue(methods.is_question("真的？", words=()))
        self.assertFalse(methods.is_question(None))


class ExpandTest(unittest.TestCase):
    NEIGHBOURS = {
        "a": [("b", 0.9), ("c", 0.5), ("a", 1.0), ("x", 0.4)],
        "b": [("d", 0.7), ("c", 0.8), ("a", 0.9)],
        "c": [("e", 0.99)],
    }

    def neighbours(self, memory):
        return list(self.NEIGHBOURS.get(memory, []))

    def test_best_first_with_the_source_of_each(self):
        added = methods.expand(["a", "b"], self.neighbours, top=2, max_added=5, minimum=0.0)
        # b and a are delivered; a is its own neighbour; c came from a (0.5) and from b (0.8): the higher one counts
        self.assertEqual(added, [{"id": "c", "score": 0.8, "source": "b"}, {"id": "d", "score": 0.7, "source": "b"},
                                 {"id": "x", "score": 0.4, "source": "a"}])

    def test_cap_minimum_and_top(self):
        self.assertEqual([m["id"] for m in methods.expand(["a", "b"], self.neighbours, 2, 2, 0.0)], ["c", "d"])
        self.assertEqual([m["id"] for m in methods.expand(["a", "b"], self.neighbours, 2, 5, 0.7)], ["c", "d"])
        self.assertEqual([m["id"] for m in methods.expand(["a", "b"], self.neighbours, 2, 5, 0.8)], ["c"])  # 0.8 itself passes
        # only the first memory is followed with top=1
        self.assertEqual([m["id"] for m in methods.expand(["a", "b"], self.neighbours, 1, 5, 0.0)], ["c", "x"])
        self.assertEqual(methods.expand(["a"], self.neighbours, 0, 5, 0.0), [])
        self.assertEqual(methods.expand(["a"], self.neighbours, 3, 0, 0.0), [])
        self.assertEqual(methods.expand([], self.neighbours, 3, 2, 0.0), [])

    def test_delivered_memories_beyond_top_are_not_added_either(self):
        # c is delivered third: it is not followed with top=2 but it is never added as a neighbour
        added = methods.expand(["a", "b", "c"], self.neighbours, top=2, max_added=5, minimum=0.0)
        self.assertEqual([m["id"] for m in added], ["d", "x"])

    def test_ties_go_to_the_neighbour_met_first(self):
        tied = {"a": [("p", 0.5), ("q", 0.5)], "b": [("r", 0.5)]}
        added = methods.expand(["a", "b"], lambda m: tied[m], 2, 5, 0.0)
        self.assertEqual([m["id"] for m in added], ["p", "q", "r"])


def item(world, case, kind, final, direct, chain=(), also_ok=(), added=()):
    return {"world": world, "id": case, "type": kind, "final": list(final), "added": [{"id": a} for a in added],
            "labels": {"direct": list(direct), "chain": list(chain), "also_ok": list(also_ok)}}


class MetricsTest(unittest.TestCase):
    def test_case_numbers_worked_out_by_hand(self):
        m = run.case_metrics("remark-assoc", ["d1", "c1", "x"], ["d1", "d2"], ["c1"], ["a1"])
        self.assertEqual((m["direct_hit"], m["direct_recall"], m["chain_hit"], m["chain_recall"]), (1.0, 0.5, 1.0, 1.0))
        self.assertEqual((m["items"], m["noise"]), (3, 1))
        self.assertAlmostEqual(m["precision"], 2 / 3)
        m = run.case_metrics("remark-assoc", [], ["d1"], ["c1"], [])
        self.assertEqual((m["direct_hit"], m["chain_hit"], m["precision"], m["items"], m["noise"]), (0.0, 0.0, None, 0, 0))
        m = run.case_metrics("remark-assoc", ["d1", "a1"], ["d1"], [], ["a1"])
        self.assertEqual((m["chain_hit"], m["chain_recall"], m["precision"], m["noise"]), (None, None, 1.0, 0))
        m = run.case_metrics("follow-up", ["y"], ["d"], [], [])
        self.assertEqual((m["hit"], m["noise"]), (0.0, 1))
        self.assertEqual(run.case_metrics("quiet", [], [], [], [])["quiet_rate"], 1.0)
        self.assertEqual(run.case_metrics("quiet", ["q"], [], [], [])["quiet_rate"], 0.0)

    def test_summary_of_a_hand_made_result(self):
        records = [
            item("w1", "r1", "remark-assoc", ["d1", "c1", "x"], ["d1", "d2"], ["c1"], ["a1"]),
            item("w1", "r2", "remark-assoc", [], ["d3"], ["c2", "c3"]),
            item("w2", "r3", "remark-assoc", ["d4", "a2"], ["d4"], [], ["a2"]),
            item("w1", "n1", "remark-named", ["n1", "n2", "z"], ["n1", "n2"]),
            item("w1", "f1", "follow-up", ["d"], ["d"]),
            item("w2", "f2", "follow-up", ["y1", "y2"], ["d"]),
            item("w1", "q1", "quiet", [], []),
            item("w2", "q2", "quiet", ["q"], []),
        ]
        summary = run.summarize(records)
        assoc = summary["overall"]["remark-assoc"]
        self.assertEqual(assoc["n"], 3)
        self.assertAlmostEqual(assoc["direct_hit"], 2 / 3, places=4)
        self.assertAlmostEqual(assoc["direct_recall"], 0.5, places=4)
        self.assertEqual((assoc["chain_hit"], assoc["chain_recall"], assoc["chain_n"]), (0.5, 0.5, 2))
        self.assertAlmostEqual(assoc["items"], 5 / 3, places=4)
        self.assertAlmostEqual(assoc["noise"], 1 / 3, places=4)
        self.assertAlmostEqual(assoc["precision"], (2 / 3 + 1) / 2, places=4)
        self.assertEqual((assoc["precision_n"], assoc["precision_empty"]), (2, 1))
        self.assertEqual(summary["overall"]["remark-named"], {"n": 1, "direct_recall": 1.0, "direct_hit": 1.0,
                                                              "items": 3.0, "noise": 1.0})
        self.assertEqual(summary["overall"]["follow-up"], {"n": 2, "hit": 0.5, "items": 1.5, "noise": 1.0})
        self.assertEqual(summary["overall"]["quiet"], {"n": 2, "quiet_rate": 0.5, "items": 0.5})
        # per world: w2 holds one assoc case and nothing of remark-named
        self.assertEqual(summary["worlds"]["w2"]["remark-assoc"]["n"], 1)
        self.assertNotIn("remark-named", summary["worlds"]["w2"])
        self.assertEqual(summary["worlds"]["w1"]["remark-assoc"]["precision_empty"], 1)
        # all: the hit of the six cases that are not quiet is 1,0,1,1,1,0 -> 4/6
        everything = summary["all"]
        self.assertEqual(everything["n"], 8)
        self.assertAlmostEqual(everything["hit"], 4 / 6, places=4)
        self.assertEqual(everything["quiet_rate"], 0.5)
        self.assertAlmostEqual(everything["items"], (3 + 0 + 2 + 3 + 1 + 2 + 0 + 1) / 8, places=4)
        self.assertNotIn("added", assoc)

    def test_added_memories_are_counted_by_label(self):
        records = [item("w", "r1", "remark-assoc", ["d", "c", "a", "z", "d2"], ["d", "d2"], ["c"], ["a"],
                        added=["c", "a", "z", "d2"]),
                   item("w", "n1", "remark-named", ["n"], ["n"], added=["q"])]
        summary = run.summarize(records, with_links=True)
        assoc = summary["overall"]["remark-assoc"]
        self.assertEqual((assoc["added"], assoc["added_chain"], assoc["added_direct"], assoc["added_also_ok"],
                          assoc["added_noise"]), (4, 1, 1, 1, 1))
        self.assertEqual(summary["overall"]["remark-named"]["added_noise"], 1)
        self.assertEqual(summary["all"]["added"], 5)

    def test_a_mean_over_no_case_is_null_and_the_table_prints(self):
        summary = run.summarize([item("w", "r1", "remark-assoc", ["d"], ["d"])])
        self.assertIsNone(summary["overall"]["remark-assoc"]["chain_hit"])
        table = run.format_table(summary)
        self.assertIn("| overall | remark-assoc | 1 |", table)
        self.assertIn("100.0", table)


class RunnerTest(unittest.TestCase):
    """The runner end to end on the fixture world with the keyword backend."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURES, "fixture-world.json"), encoding="utf-8") as handle:
            cls.world = json.load(handle)
        cls.texts = [m["text"] for m in cls.world["memories"].values()]

    def go(self, **settings):
        return run.run_split(FIXTURES, "sqlite", run.DEFAULT_URL, run.DEFAULT_MODEL,
                             [run.merged_settings(settings)])[0]

    def by_case(self, result):
        return {record["id"]: record for record in result["cases"]}

    def test_default_run(self):
        result = self.go()
        cases = self.by_case(result)
        self.assertEqual(len(cases), 8)
        ids = set(self.world["memories"])
        for record in result["cases"]:
            self.assertTrue(set(record["final"]) <= ids)
            self.assertEqual(record["final"], [d["id"] for d in record["delivered"]])
            self.assertEqual(record["added"], [])
        self.assertEqual(cases["r01"]["delivered"][0]["id"], "m001")
        self.assertEqual(cases["q01"]["delivered"], [])
        self.assertEqual(cases["f01"]["queries"], ["咖啡机呢\n后来坏过吗"])  # v0: the last user turn is borrowed
        summary = result["summary"]
        self.assertEqual(summary["overall"]["remark-assoc"]["direct_hit"], 1.0)
        self.assertEqual(summary["overall"]["remark-assoc"]["chain_hit"], 0.0)  # the chain is never found by wording
        self.assertEqual(summary["overall"]["quiet"]["quiet_rate"], 1.0)
        self.assertEqual(list(summary["worlds"]), ["fixture-demo"])
        # texts are not in the records
        dumped = json.dumps(result["cases"], ensure_ascii=False)
        for text in self.texts:
            self.assertNotIn(text, dumped)

    def test_same_input_same_output(self):
        first = json.dumps(self.go(links="kw", link_min_kw=0.1, ranks=True)["cases"])
        second = json.dumps(self.go(links="kw", link_min_kw=0.1, ranks=True)["cases"])
        self.assertEqual(first, second)

    def test_writing_in_several_calls_gives_the_same_answers(self):
        plain = self.by_case(self.go())
        with mock.patch.object(run, "WRITE_BATCH", 5):
            split = self.by_case(self.go())
        for case_id in plain:
            self.assertEqual([d["id"] for d in plain[case_id]["delivered"]],
                             [d["id"] for d in split[case_id]["delivered"]])

    def test_keyword_links_add_chain_memories(self):
        base = self.go()
        linked = self.go(links="kw", link_min_kw=0.1)
        record = self.by_case(linked)["r01"]
        self.assertEqual(record["delivered"], self.by_case(base)["r01"]["delivered"])
        first = record["added"][0]
        self.assertEqual((first["id"], first["source"], first["kind"]), ("m002", "m001", "kw"))
        self.assertGreaterEqual(first["score"], 0.1)
        self.assertEqual(record["final"], ["m001"] + [a["id"] for a in record["added"]])
        self.assertLessEqual(len(record["added"]), 2)  # link_max
        before, after = base["summary"]["overall"]["remark-assoc"], linked["summary"]["overall"]["remark-assoc"]
        self.assertGreater(after["chain_hit"], before["chain_hit"])
        self.assertGreaterEqual(after["added_chain"], 1)
        self.assertEqual(linked["summary"]["all"]["added"],
                         sum(len(r["added"]) for r in linked["cases"]))
        # a case that delivered nothing has nothing to follow; a high minimum adds nothing
        self.assertEqual(self.by_case(linked)["q01"]["added"], [])
        self.assertEqual(self.go(links="kw", link_min_kw=0.99)["summary"]["all"]["added"], 0)
        one = self.go(links="kw", link_min_kw=0.1, link_max=1)
        self.assertTrue(all(len(r["added"]) <= 1 for r in one["cases"]))
        self.assertGreaterEqual(one["summary"]["all"]["added"], 1)

    def test_vector_links_need_an_embedding_store(self):
        for links in ("vec", "either"):
            with self.assertRaises(SystemExit) as caught:
                self.go(links=links)
            self.assertIn("embedding store", str(caught.exception))

    def test_remark_profile_precise_sends_remarks_to_the_precise_lines(self):
        cases = self.by_case(self.go(remark_profile="precise"))
        self.assertEqual(cases["r01"]["profile"], "precise")       # a remark
        self.assertEqual(cases["f01"]["profile"], "broad")         # "...吗": a question
        plain = self.by_case(self.go())
        self.assertEqual(plain["r01"]["profile"], "broad")
        # the precise line (0.30) of the keyword store is above the score of r01 (about 0.29): nothing is delivered
        self.assertEqual(cases["r01"]["delivered"], [])
        self.assertEqual([d["id"] for d in plain["r01"]["delivered"]], ["m001"])

    def test_ranks_cover_every_labelled_memory(self):
        cases = self.by_case(self.go(ranks=True))
        ranks = cases["r01"]["ranks"]
        self.assertEqual(set(ranks), {"m001", "m002"})
        self.assertEqual(ranks["m001"]["rank"], 1)
        self.assertIsNone(ranks["m002"]["rank"])
        self.assertIsNone(ranks["m002"]["score"])
        self.assertEqual(set(cases["r03"]["ranks"]), {"m008", "m009", "m012"})
        self.assertNotIn("ranks", self.by_case(self.go())["r01"])

    def test_query_variants_change_the_queries(self):
        self.assertEqual(self.by_case(self.go(query="v2"))["f01"]["queries"], ["咖啡机呢\n在厨房里放着呢\n后来坏过吗"])
        self.assertEqual(self.by_case(self.go(query="v3"))["f01"]["queries"],
                         ["后来坏过吗", "咖啡机呢\n在厨房里放着呢\n后来坏过吗"])
        self.assertEqual(self.by_case(self.go(query="v3"))["r01"]["queries"], ["最近天气有点热"])

    def test_command_line_writes_json_and_a_table(self):
        with tempfile.TemporaryDirectory() as folder:
            out = os.path.join(folder, "out.json")
            stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
            with contextlib.redirect_stdout(stream):
                code = run.main(["--split", FIXTURES, "--backend", "sqlite", "--links", "kw", "--link-min-kw", "0.1",
                                 "--table", "--out", out])
            stream.flush()
            printed = stream.buffer.getvalue().decode("utf-8")
            self.assertEqual(code, 0)
            self.assertIn("| scope | type | n |", printed)
            for text in self.texts:
                self.assertNotIn(text, printed)
            with open(out, encoding="utf-8") as handle:
                document = json.load(handle)
        self.assertEqual(document["settings"]["links"], "kw")
        self.assertEqual(document["settings"]["backend"], "sqlite")
        self.assertEqual(len(document["cases"]), 8)
        self.assertIn("summary", document)

    def test_verbose_includes_texts(self):
        result = run.run_split(FIXTURES, "sqlite", run.DEFAULT_URL, run.DEFAULT_MODEL,
                               [run.merged_settings({})], verbose=True)[0]
        self.assertIn(self.world["memories"]["m001"]["text"], result["cases"][0]["texts"].values())

    def test_linked_on_is_the_default_and_off_drops_only_that_stage(self):
        from feiyu_memgate.core import stages as stages_module
        self.assertEqual(run.DEFAULTS["linked"], "on")
        self.assertEqual(run.merged_settings({})["linked"], "on")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            session = run.Session(self.world, "sqlite", run.DEFAULT_URL, run.DEFAULT_MODEL, directory)
            try:
                on = session.client("broad", 5, "on")
                off = session.client("broad", 5, "off")
                self.assertIsNot(on, off)
                self.assertIs(session.client("broad", 5), on)  # "on" is the default argument too
                self.assertEqual([s.name for s in on.gateway.stages], list(stages_module.DEFAULT_STAGES))
                self.assertEqual([s.name for s in off.gateway.stages],
                                 [n for n in stages_module.DEFAULT_STAGES if n != "linked"])
                self.assertEqual([s.profile for s in off.gateway.stages if hasattr(s, "profile")], ["broad", "broad"])
            finally:
                session.close()

    def test_a_keyword_store_gives_the_same_answers_with_linked_on_and_off(self):
        on, off = self.by_case(self.go(linked="on")), self.by_case(self.go(linked="off"))
        for case_id in on:
            self.assertEqual(on[case_id]["delivered"], off[case_id]["delivered"])
        self.assertEqual(self.go(linked="off")["settings"]["linked"], "off")

    def test_the_links_flags_still_add_on_top_with_either_setting(self):
        for linked in ("on", "off"):
            result = self.go(linked=linked, links="kw", link_min_kw=0.1)
            self.assertTrue(any(record["added"] for record in result["cases"]), linked)

    def test_the_command_line_takes_linked(self):
        for flag in ("on", "off"):
            with tempfile.TemporaryDirectory() as folder:
                out = os.path.join(folder, "out.json")
                with contextlib.redirect_stdout(io.StringIO()):
                    code = run.main(["--split", FIXTURES, "--backend", "sqlite", "--linked", flag, "--out", out])
                with open(out, encoding="utf-8") as handle:
                    self.assertEqual(json.load(handle)["settings"]["linked"], flag)
            self.assertEqual(code, 0)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run.main(["--split", FIXTURES, "--backend", "sqlite", "--linked", "maybe"])

    def test_bad_settings_stop_the_run(self):
        for bad in ({"query": "v9"}, {"links": "x"}, {"limit": 0}, {"profile": "x"}, {"linked": "maybe"},
                    {"linked": True}):
            with self.assertRaises(SystemExit):
                self.go(**bad)
        with self.assertRaises(SystemExit):
            run.merged_settings({"nope": 1})

    def test_missing_split_folder(self):
        with self.assertRaises(SystemExit):
            run.world_files(os.path.join(FIXTURES, "nothing-here"))


class RunnerWithEmbeddingsTest(unittest.TestCase):
    """The same world through the embedding and hybrid backends, with the fake embeddings server."""

    def setUp(self):
        self.server = FakeEmbeddingServer()
        self.addCleanup(self.server.stop)

    def go(self, backend, **settings):
        return run.run_split(FIXTURES, backend, self.server.url, "fake-model", [run.merged_settings(settings)])[0]

    def check(self, result):
        with open(os.path.join(FIXTURES, "fixture-world.json"), encoding="utf-8") as handle:
            ids = set(json.load(handle)["memories"])
        self.assertEqual(len(result["cases"]), 8)
        for record in result["cases"]:
            self.assertTrue(set(record["final"]) <= ids, record["final"])
            self.assertEqual(len(record["final"]), len(set(record["final"])))
            for added in record["added"]:
                self.assertNotIn(added["id"], [d["id"] for d in record["delivered"]])
                self.assertNotEqual(added["id"], added["source"])
        json.dumps(result["cases"])

    def session(self, backend, directory):
        with open(os.path.join(FIXTURES, "fixture-world.json"), encoding="utf-8") as handle:
            world = json.load(handle)
        session = run.Session(world, backend, self.server.url, "fake-model", directory)
        # the fake vectors are far from each other: map the whole cosine range to scores so that every memory has one
        vector = session.spec if backend == "embedding" else session.spec["children"][1]
        vector.update(floor=-1.0, ceil=1.0)
        session.client("broad", 5)  # writes the memories
        self.addCleanup(session.close)
        return session

    def test_embedding_backend_runs_with_every_kind_of_link(self):
        for links in ("none", "kw", "vec", "either"):
            self.check(self.go("embedding", links=links, link_min_kw=0.05, link_min_vec=0.0, ranks=True, query="v3"))
        self.assertTrue(self.server.requests)

    def test_linked_on_adds_to_what_off_delivers_through_the_gateway(self):
        for backend in ("embedding", "hybrid"):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
                with open(os.path.join(FIXTURES, "fixture-world.json"), encoding="utf-8") as handle:
                    world = json.load(handle)
                cases = world["cases"]
                session = run.Session(world, backend, self.server.url, "fake-model", directory)
                # the fake vectors are weak: a narrow cosine range maps them to scores that the lines let through
                vector = session.spec if backend == "embedding" else session.spec["children"][1]
                vector.update(floor=0.1, ceil=0.5)
                session.client("broad", 5)
                self.addCleanup(session.close)
                grew = quiet = 0
                for case in cases:
                    off = run.run_case(session, case, run.merged_settings({"linked": "off"}))
                    on = run.run_case(session, case, run.merged_settings({"linked": "on"}))
                    kept = [d["id"] for d in off["delivered"]]
                    got = [d["id"] for d in on["delivered"]]
                    self.assertEqual(got[:len(kept)], kept, (backend, case["id"]))  # the first hop is as it was
                    self.assertLessEqual(len(got) - len(kept), 2)
                    self.assertLessEqual(len(got), 5)
                    if not kept:
                        self.assertEqual(got, [])  # a quiet turn stays quiet
                        quiet += 1
                    self.assertEqual(on["final"], got)
                    grew += len(got) > len(kept)
                self.assertGreater(grew, 0, backend)
                if backend == "embedding":  # the keyword child of a hybrid always finds something here
                    self.assertGreater(quiet, 0, backend)

    def test_vector_and_either_expansion_on_a_delivered_memory(self):
        # the fake vectors are too weak for the real recall stages to deliver anything, so the expansion is
        # asked for a memory directly
        for backend in ("embedding", "hybrid"):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
                session = self.session(backend, directory)
                settings = run.merged_settings({"links": "vec", "link_min_vec": 0.0, "link_max": 3})
                added = run.expansion(session, settings, ["m001"])
                self.assertEqual(len(added), 3)
                self.assertEqual({a["kind"] for a in added}, {"vec"})
                self.assertEqual({a["source"] for a in added}, {"m001"})
                self.assertNotIn("m001", [a["id"] for a in added])
                scores = [a["score"] for a in added]
                self.assertEqual(scores, sorted(scores, reverse=True))
                # either: the keyword neighbours (m002 shares the wording) come before every vector neighbour
                settings = run.merged_settings({"links": "either", "link_min_kw": 0.1, "link_min_vec": 0.0,
                                                "link_max": 4})
                added = run.expansion(session, settings, ["m001"])
                kinds = [a["kind"] for a in added]
                self.assertEqual(kinds, sorted(kinds))
                self.assertEqual(added[0]["id"], "m002")
                self.assertEqual(kinds[0], "kw")
                self.assertIn("vec", kinds)
                self.assertEqual(len({a["id"] for a in added}), len(added))
                # a minimum nothing reaches adds nothing
                settings = run.merged_settings({"links": "either", "link_min_kw": 2.0, "link_min_vec": 2.0})
                self.assertEqual(run.expansion(session, settings, ["m001"]), [])

    def test_hybrid_backend_and_determinism(self):
        first = self.go("hybrid", links="either", link_min_kw=0.05, link_min_vec=0.0, remark_profile="precise")
        self.check(first)
        second = self.go("hybrid", links="either", link_min_kw=0.05, link_min_vec=0.0, remark_profile="precise")
        self.assertEqual(json.dumps(first["cases"]), json.dumps(second["cases"]))


class SweepTest(unittest.TestCase):
    def test_the_test_split_is_refused_without_final(self):
        with tempfile.TemporaryDirectory() as folder:
            frozen = os.path.join(folder, "test")
            os.makedirs(frozen)
            with self.assertRaises(SystemExit) as caught:
                sweep.refuse_test(frozen, False)
            self.assertIn("test split", str(caught.exception))
            with self.assertRaises(SystemExit):
                sweep.refuse_test(frozen + os.sep, False)
            with self.assertRaises(SystemExit):
                sweep.main(["--split", frozen, "--backend", "sqlite", "--grid", "remarks"])
            self.assertIsNone(sweep.refuse_test(frozen, True))
            self.assertIsNone(sweep.refuse_test(os.path.join(folder, "dev"), False))

    def test_built_in_grids(self):
        queries = sweep.GRIDS["queries"]()
        self.assertEqual(len(queries), 10)
        self.assertEqual(queries[0], ("v0", {"query": "v0"}))
        self.assertEqual({(o["query"], o["short"]) for _, o in queries[1:]},
                         {(v, s) for v in ("v1", "v2", "v3") for s in (8, 12, 16)})
        links = sweep.GRIDS["links"]()
        self.assertEqual([o["links"] for _, o in links], ["none", "kw", "kw", "vec", "vec", "either", "either"])
        remarks = sweep.GRIDS["remarks"]()
        self.assertEqual([o["remark_profile"] for _, o in remarks], ["none", "precise"])

    def test_a_grid_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "grid.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([{"label": "plain"}, {"query": "v1", "short": 12}], handle)
            rows = sweep.load_grid(path)
            self.assertEqual(rows, [("plain", {}), ("query=v1, short=12", {"query": "v1", "short": 12})])
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"not": "a list"}, handle)
            with self.assertRaises(SystemExit):
                sweep.load_grid(path)
        with self.assertRaises(SystemExit):
            sweep.load_grid("no-such-grid")

    def test_a_sweep_prints_one_row_per_setting(self):
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with tempfile.TemporaryDirectory() as folder:
            out = os.path.join(folder, "sweep.json")
            with contextlib.redirect_stdout(stream):
                code = sweep.main(["--split", FIXTURES, "--backend", "sqlite", "--grid", "remarks", "--query", "v1",
                                   "--out", out])
            with open(out, encoding="utf-8") as handle:
                document = json.load(handle)
        stream.flush()
        lines = stream.buffer.getvalue().decode("utf-8").strip().split("\n")
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 4)  # header, rule, two rows
        self.assertTrue(lines[2].startswith("| remark-profile=none |"))
        self.assertTrue(lines[3].startswith("| remark-profile=precise |"))
        self.assertEqual([row["settings"]["query"] for row in document["rows"]], ["v1", "v1"])
        self.assertEqual([row["settings"]["remark_profile"] for row in document["rows"]], ["none", "precise"])

    def test_a_grid_that_cannot_run_stops_before_anything_runs(self):
        with self.assertRaises(SystemExit):
            sweep.main(["--split", FIXTURES, "--backend", "sqlite", "--grid", "links"])


class ValidatorTest(unittest.TestCase):
    """The validator on small hand-made worlds: the fixture passes every rule but the counts of a full world."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURES, "fixture-world.json"), encoding="utf-8") as handle:
            cls.world = json.load(handle)

    def problems(self, mutate=None):
        world = copy.deepcopy(self.world)
        if mutate:
            mutate(world)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "w.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(world, handle, ensure_ascii=False)
            found = validate.problems_of(path)
        return [line for line in found if "expected" not in line]  # the counts belong to a full world

    def case(self, world, case_id):
        return next(c for c in world["cases"] if c["id"] == case_id)

    def test_the_fixture_only_misses_the_counts(self):
        self.assertEqual(self.problems(), [])
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "w.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(self.world, handle, ensure_ascii=False)
            counts = validate.problems_of(path)
        self.assertEqual(len(counts), 6)
        self.assertTrue(all("expected" in line for line in counts))

    def test_each_rule_rejects_what_it_is_for(self):
        def duplicate_text(w):
            w["memories"]["m010"]["text"] = w["memories"]["m011"]["text"]

        def bad_date(w):
            w["memories"]["m001"]["date"] = "2026-13-45"

        def filler_with_cluster(w):
            w["memories"]["m010"]["cluster"] = "c01"

        def term_missing(w):
            w["memories"]["m002"]["text"] = "主人上次在公园里吃了一个甜筒，结果掉在了地上"

        def two_facts(w):
            w["memories"]["m002"]["role"] = "fact"

        def unknown_cluster(w):
            w["memories"]["m002"]["cluster"] = "c99"

        def too_short(w):
            w["memories"]["m010"]["text"] = "手机裂了"

        def assoc_names_cluster(w):
            self.case(w, "r01")["message"] = "最近冰淇淋有点好吃"

        def chain_other_cluster(w):
            self.case(w, "r01")["chain"] = ["m004"]

        def chain_is_a_fact(w):
            self.case(w, "r01")["chain"] = ["m001"]
            self.case(w, "r01")["direct"] = ["m002"]

        def assoc_with_history(w):
            self.case(w, "r01")["history"] = [turn("user", "你好"), turn("assistant", "你好呀")]

        def assoc_without_why(w):
            del self.case(w, "r01")["why"]

        def quiet_with_labels(w):
            self.case(w, "q01")["direct"] = ["m001"]

        def no_direct(w):
            self.case(w, "n01")["direct"] = []

        def named_names_nothing(w):
            self.case(w, "n01")["message"] = "今天又在闹"

        def follow_up_too_long(w):
            self.case(w, "f01")["message"] = "后来那台机器有没有再坏过呢"

        def follow_up_ends_with_user(w):
            self.case(w, "f01")["history"] = [turn("assistant", "好的"), turn("user", "咖啡机呢")]

        def follow_up_one_turn(w):
            self.case(w, "f01")["history"] = [turn("assistant", "好的")]

        def follow_up_names_cluster(w):
            self.case(w, "f01")["message"] = "咖啡机坏过吗"

        def repeated_case_id(w):
            self.case(w, "r02")["id"] = "r01"

        def two_labels(w):
            self.case(w, "r01")["also_ok"] = ["m001"]

        def unknown_memory(w):
            self.case(w, "r01")["chain"] = ["m999"]

        def unknown_type(w):
            self.case(w, "r01")["type"] = "chitchat"

        def empty_message(w):
            self.case(w, "r01")["message"] = "  "

        def bad_history_role(w):
            self.case(w, "f01")["history"][0]["role"] = "system"

        rules = [
            (duplicate_text, "repeats the text"), (bad_date, "bad date"), (filler_with_cluster, "filler has no cluster"),
            (term_missing, "does not mention any term"), (two_facts, "exactly one fact"),
            (unknown_cluster, "unknown cluster"), (too_short, "text must have"),
            (assoc_names_cluster, "names the cluster"), (chain_other_cluster, "not an episode of a direct"),
            (chain_is_a_fact, "not an episode of a direct"), (assoc_with_history, "no history"),
            (assoc_without_why, "needs its reason"), (quiet_with_labels, "no labels"), (no_direct, "no direct memory"),
            (named_names_nothing, "names no cluster"), (follow_up_too_long, "at most 12"),
            (follow_up_ends_with_user, "ending with the assistant"), (follow_up_one_turn, "at least two turns"),
            (follow_up_names_cluster, "names the cluster"), (repeated_case_id, "used twice"),
            (two_labels, "two of direct, chain, also_ok"), (unknown_memory, "unknown memory"),
            (unknown_type, "unknown type"), (empty_message, "empty message"), (bad_history_role, "history must be"),
        ]
        for mutate, expected in rules:
            with self.subTest(rule=mutate.__name__):
                found = self.problems(mutate)
                self.assertTrue(any(expected in line for line in found), (expected, found))

    def test_a_missing_key_and_unreadable_file(self):
        found = self.problems(lambda w: w.pop("persona"))
        self.assertTrue(any("missing 'persona'" in line for line in found))
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "w.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            self.assertIn("cannot read", validate.problems_of(path)[0])


def sessions_world(world):
    """The fixture world with two more clusters (six in all) and three valid sessions, two clusters each."""
    world = copy.deepcopy(world)
    world["clusters"]["c05"] = {"entity": "吉他", "terms": ["吉他"]}
    world["clusters"]["c06"] = {"entity": "自行车", "terms": ["自行车"]}
    world["memories"].update({
        "m013": {"text": "主人会弹吉他，大学的时候参加过乐队", "date": "2026-07-05", "cluster": "c05", "role": "fact"},
        "m014": {"text": "主人上个月给吉他换了一套新琴弦", "date": "2026-10-01", "cluster": "c05", "role": "episode"},
        "m015": {"text": "主人每天骑自行车上班，已经骑了快五年了", "date": "2026-07-09", "cluster": "c06", "role": "fact"},
        "m016": {"text": "主人的自行车上周被人偷了，后来又找了回来", "date": "2026-10-14", "cluster": "c06", "role": "episode"},
    })

    def t(kind, user, assistant, cluster, direct, **extra):
        found = {"kind": kind, "user": user, "cluster": cluster, "direct": direct, "also_ok": []}
        if assistant is not None:
            found["assistant"] = assistant
        found.update(extra)
        return found

    world["sessions"] = [
        {"id": "s01", "turns": [
            t("open", "最近天气有点热", "是啊，这几天热得人没精神。", "c01", ["m001"]),
            t("continue", "上回那次真是可惜", "哪一次呀？", "c01", ["m002"]),
            t("idle", "楼下又在修路了", "修路是挺吵的。", None, []),
            t("named", "年糕今天又挠沙发了", "它又调皮了。", "c02", ["m003"]),
            t("return", "对了，刚才说到吃的那个，我想好了", "想好什么了？", "c01", ["m001"]),
            t("nextday", "昨天说的那个还是没买成", None, "c01", ["m001"], new_conversation=True, gap_days=1)]},
        {"id": "s02", "turns": [
            t("named", "咖啡机今天又响了", "它怎么了？", "c03", ["m006", "m007"]),
            t("continue", "后来有没有再坏过", "好像没有。", "c03", ["m007"]),
            t("idle", "今天的云真好看", "是呀，像棉花糖。", None, []),
            t("open", "晚饭吃得有点撑", "那是该动一动。", "c04", ["m008"]),
            t("return", "刚才说的那台机器，我又想了想", "你想怎么办？", "c03", ["m006"]),
            t("nextday", "昨晚说的那个习惯今天又犯懒了", "懒一天也没事。", "c04", ["m008"],
              new_conversation=True, gap_days=1),
            t("idle", "今天早饭吃得晚", None, None, [])]},
        {"id": "s03", "turns": [
            t("open", "好久没碰过那种有弦的东西了", "是吗，什么时候的事？", "c05", ["m013"]),
            t("continue", "上个月刚换过一套", "换得顺手吗？", "c05", ["m014"]),
            t("idle", "今天风有点大", "风大记得多穿点。", None, []),
            t("named", "自行车今天爆胎了", "那可真麻烦。", "c06", ["m015"]),
            t("return", "刚才说的那个，我是不是该再练练", "你想练就练。", "c05", ["m013"]),
            t("nextday", "昨天说的那个后来找着了", None, "c06", ["m016"], new_conversation=True, gap_days=2)]},
    ]
    return world


class SessionValidatorTest(unittest.TestCase):
    """The rules of the sessions of a world, one failing case each."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURES, "fixture-world.json"), encoding="utf-8") as handle:
            cls.world = sessions_world(json.load(handle))

    def problems(self, mutate=None):
        world = copy.deepcopy(self.world)
        if mutate:
            mutate(world["sessions"])
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "w.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(world, handle, ensure_ascii=False)
            found = validate.problems_of(path)
        return [line for line in found if "expected" not in line]  # the counts belong to a full world

    def test_a_valid_set_of_sessions_passes_and_a_world_without_sessions_still_does(self):
        self.assertEqual(self.problems(), [])
        world = copy.deepcopy(self.world)
        del world["sessions"]
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "w.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(world, handle, ensure_ascii=False)
            self.assertEqual([ln for ln in validate.problems_of(path) if "expected" not in ln], [])

    def test_a_problem_names_the_session_and_the_turn(self):
        found = self.problems(lambda s: s[0]["turns"][1].update(user=""))
        self.assertTrue(any(line == "w.json: session s01 turn 2: user must have 1 to 30 characters" for line in found), found)

    def test_each_rule_rejects_what_it_is_for(self):
        def turns(s, n):
            return s[n]["turns"]

        def two_sessions(s):
            del s[2]

        def repeated_id(s):
            s[1]["id"] = "s01"

        def five_turns(s):
            del turns(s, 1)[2:3]
            del turns(s, 1)[2:3]

        def ten_turns(s):
            turns(s, 0)[-1]["assistant"] = "好的。"
            turns(s, 0).extend({"kind": "idle", "user": "嗯", "assistant": "嗯。", "cluster": None, "direct": [],
                                "also_ok": []} for _ in range(4))

        def unknown_kind(s):
            turns(s, 0)[2]["kind"] = "chat"

        def user_too_long(s):
            turns(s, 0)[2]["user"] = "楼" * 31

        def assistant_missing(s):
            del turns(s, 0)[2]["assistant"]

        def assistant_too_long(s):
            turns(s, 0)[2]["assistant"] = "好" * 51

        def unknown_cluster(s):
            turns(s, 0)[1]["cluster"] = "c99"

        def unknown_memory(s):
            turns(s, 0)[1]["direct"] = ["m999"]

        def both_lists(s):
            turns(s, 0)[1]["also_ok"] = ["m002"]

        def open_not_the_fact(s):
            turns(s, 0)[0]["direct"] = ["m001", "m002"]

        def open_names_cluster(s):
            turns(s, 0)[0]["user"] = "最近冰淇淋有点好吃"

        def named_without_term(s):
            turns(s, 0)[3]["user"] = "今天又在闹"

        def named_direct_elsewhere(s):
            turns(s, 0)[3]["direct"] = ["m001"]

        def continue_after_other_cluster(s):
            turns(s, 0)[1].update(cluster="c02", direct=["m003"])

        def continue_names_cluster(s):
            turns(s, 0)[1]["user"] = "那个冰淇淋真可惜"

        def continue_three_memories(s):
            turns(s, 0)[1]["direct"] = ["m001", "m002", "m001"]

        def idle_with_cluster(s):
            turns(s, 0)[2].update(cluster="c01", direct=["m001"])

        def return_not_seen_before(s):
            turns(s, 0)[4].update(cluster="c03", direct=["m006"])

        def return_right_after(s):
            turns(s, 0)[3].update(kind="named", user="冰淇淋我也想吃", cluster="c01", direct=["m001"])

        def nextday_without_flag(s):
            del turns(s, 0)[5]["new_conversation"]
            del turns(s, 0)[5]["gap_days"]

        def nextday_without_gap(s):
            del turns(s, 0)[5]["gap_days"]

        def gap_of_zero(s):
            turns(s, 0)[5]["gap_days"] = 0

        def gap_without_new_conversation(s):
            turns(s, 0)[2]["gap_days"] = 1

        def new_conversation_elsewhere(s):
            turns(s, 0)[3].update(new_conversation=True, gap_days=1)

        def nextday_unseen_cluster(s):
            turns(s, 0)[5].update(cluster="c03", direct=["m006"])

        def no_nextday(s):
            turns(s, 0)[5].update(kind="idle", cluster=None, direct=[])
            del turns(s, 0)[5]["new_conversation"]
            del turns(s, 0)[5]["gap_days"]

        def two_nextdays(s):
            turns(s, 1)[6].update(kind="nextday", user="那个习惯还是犯懒", cluster="c04", direct=["m008"],
                                  new_conversation=True, gap_days=1)

        def first_turn_idle(s):
            turns(s, 0)[0].update(kind="idle", cluster=None, direct=[])

        def no_return(s):
            turns(s, 0)[4].update(kind="idle", cluster=None, direct=[])

        def one_cluster_only(s):
            turns(s, 0)[3].update(kind="named", user="冰淇淋又想吃了", cluster="c01", direct=["m001"])

        def three_after_nextday(s):
            turns(s, 0)[-1]["assistant"] = "好的。"
            turns(s, 0).extend({"kind": "idle", "user": "嗯", "assistant": "嗯。", "cluster": None, "direct": [],
                                "also_ok": []} for _ in range(3))

        def return_after_nextday(s):
            turns(s, 1)[6].update(kind="return", user="那个再说吧", cluster="c03", direct=["m006"])

        def assistant_names_own_cluster(s):
            turns(s, 0)[0]["assistant"] = "是啊，冰淇淋也该吃了。"

        def assistant_names_later_cluster(s):
            turns(s, 0)[0]["assistant"] = "是啊，年糕也热坏了。"

        def cluster_in_two_sessions(s):
            turns(s, 2)[3].update(user="咖啡机又坏了", cluster="c03", direct=["m006"])

        rules = [
            (two_sessions, "exactly 3"), (repeated_id, "id is used twice"), (five_turns, "a session has 6 to 9"),
            (ten_turns, "a session has 6 to 9"), (unknown_kind, "unknown kind"), (user_too_long, "user must have"),
            (assistant_missing, "needs an assistant line"), (assistant_too_long, "assistant must have"),
            (unknown_cluster, "unknown cluster"), (unknown_memory, "direct must be a list of known"),
            (both_lists, "both direct and also_ok"), (open_not_the_fact, "exactly the fact"),
            (open_names_cluster, "names the cluster"), (named_without_term, "names no term"),
            (named_direct_elsewhere, "non-empty and all of cluster"),
            (continue_after_other_cluster, "turn before must be about the same cluster"),
            (continue_names_cluster, "names the cluster"), (continue_three_memories, "1 or 2 memories"),
            (idle_with_cluster, "idle turn has no cluster"), (return_not_seen_before, "did not appear earlier"),
            (return_right_after, "must have another cluster or none"), (nextday_without_flag, "must carry new_conversation"),
            (nextday_without_gap, "needs gap_days"), (gap_of_zero, "needs gap_days"),
            (gap_without_new_conversation, "gap_days only goes with"),
            (new_conversation_elsewhere, "only the nextday turn starts"),
            (nextday_unseen_cluster, "did not appear in the conversation before"), (no_nextday, "exactly one nextday"),
            (two_nextdays, "exactly one nextday"), (first_turn_idle, "first turn must be open or named"),
            (no_return, "has no return turn"), (one_cluster_only, "at least two different clusters"),
            (three_after_nextday, "at most two turns may follow"), (return_after_nextday, "after the nextday turn only"),
            (assistant_names_own_cluster, "assistant line contains the term"),
            (assistant_names_later_cluster, "assistant line contains the term"),
            (cluster_in_two_sessions, "is also used by session"),
        ]
        for mutate, expected in rules:
            with self.subTest(rule=mutate.__name__):
                found = self.problems(mutate)
                self.assertTrue(any(expected in line for line in found), (expected, found))

    def test_sessions_that_are_not_a_list(self):
        world = copy.deepcopy(self.world)
        world["sessions"] = {"id": "s01"}
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "w.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(world, handle, ensure_ascii=False)
            self.assertTrue(any("sessions must be a list" in line for line in validate.problems_of(path)))

    def test_the_later_term_rule_reaches_across_the_nextday_turn(self):
        found = self.problems(lambda s: s[0]["turns"][1].update(assistant="听起来像年糕的事。"))
        self.assertTrue(any("turn 2: the assistant line contains the term" in line and "(turn 4)" in line for line in found), found)


class SessionsToolTest(unittest.TestCase):
    """bench/chat/sessions_tool.py: show hides what an author must not copy, set writes and checks."""

    @classmethod
    def setUpClass(cls):
        cls.tool = _load("sessions_tool")
        with open(os.path.join(FIXTURES, "fixture-world.json"), encoding="utf-8") as handle:
            cls.world = sessions_world(json.load(handle))

    def run_tool(self, *arguments):
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with contextlib.redirect_stdout(stream):
            code = self.tool.main(list(arguments))
        stream.flush()
        return code, stream.buffer.getvalue().decode("utf-8")

    def write(self, folder, name, document, **dump):
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(document, ensure_ascii=False, **dump) + "\n")
        return path

    def test_show_prints_the_world_but_neither_cases_nor_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write(folder, "w.json", self.world, indent=1)
            code, printed = self.run_tool("show", path)
        self.assertEqual(code, 0)
        self.assertIn(self.world["persona"], printed)
        self.assertIn("c02  entity: 年糕  terms: 年糕, 橘猫", printed)
        self.assertIn("m003  fact", printed)
        self.assertIn(self.world["memories"]["m004"]["text"], printed)
        self.assertIn("fillers", printed)
        self.assertIn(self.world["memories"]["m010"]["text"], printed)
        for case in self.world["cases"]:
            self.assertNotIn(case["message"], printed)
            self.assertNotIn(case["why"], printed)
        for session in self.world["sessions"]:
            for turn in session["turns"]:
                self.assertNotIn(turn["user"], printed)
                self.assertNotIn(turn.get("assistant", "\0"), printed)

    def test_set_writes_the_world_in_its_style_and_reports(self):
        sessions = copy.deepcopy(self.world["sessions"])
        # an old list sits before the cases: it is replaced and the key ends up last
        world = {k: v for k, v in self.world.items() if k not in ("sessions", "cases")}
        world["sessions"] = [{"id": "old", "turns": []}]
        world["cases"] = copy.deepcopy(self.world["cases"])
        with tempfile.TemporaryDirectory() as folder:
            path = self.write(folder, "w.json", world, indent=1)
            given = self.write(folder, "sessions.json", sessions)
            counts = dict(MEMORIES=len(world["memories"]), CLUSTERS=(6, 18),
                          CASES={"remark-assoc": 3, "remark-named": 2, "follow-up": 2, "quiet": 1})
            with mock.patch.object(self.tool, "_validator", return_value=validate), \
                    mock.patch.multiple(validate, **counts):
                code, printed = self.run_tool("set", path, given)
            with open(path, "rb") as handle:
                raw = handle.read()
        self.assertEqual((code, printed), (0, "fixture-demo: 3 sessions, 19 turns, 0 problem(s)\n"))
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.endswith(b"}\n") and not raw.endswith(b"\n\n"))
        text = raw.decode("utf-8")
        self.assertIn("楼下又在修路了", text)  # characters as they are, not escaped
        self.assertNotIn("\\u", text)
        document = json.loads(text)
        self.assertEqual(list(document), ["world", "persona", "clusters", "memories", "cases", "sessions"])
        self.assertEqual(document["sessions"], sessions)
        self.assertEqual(text, json.dumps(document, ensure_ascii=False, indent=1) + "\n")

    def test_set_prints_the_problems_exits_1_and_still_writes(self):
        sessions = copy.deepcopy(self.world["sessions"])
        sessions[0]["turns"][1]["user"] = ""
        with tempfile.TemporaryDirectory() as folder:
            path = self.write(folder, "w.json", {k: v for k, v in self.world.items() if k != "sessions"}, indent=1)
            given = self.write(folder, "sessions.json", sessions)
            code, printed = self.run_tool("set", path, given)
            with open(path, encoding="utf-8") as handle:
                written = json.load(handle)
        self.assertEqual(code, 1)
        self.assertIn("w.json: session s01 turn 2: user must have 1 to 30 characters", printed)
        self.assertTrue(printed.strip().split("\n")[-1].endswith("problem(s)"))
        self.assertNotIn(", 0 problem(s)", printed)
        self.assertEqual(written["sessions"], sessions)

    def test_set_refuses_a_file_that_is_not_a_list_and_a_bad_command_line(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write(folder, "w.json", self.world, indent=1)
            given = self.write(folder, "sessions.json", {"id": "s01"})
            with open(path, "rb") as handle:
                before = handle.read()
            code, _ = self.run_tool("set", path, given)
            self.assertEqual(code, 2)
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), before)
        self.assertEqual(self.run_tool("show")[0], 2)


if __name__ == "__main__":
    unittest.main()
