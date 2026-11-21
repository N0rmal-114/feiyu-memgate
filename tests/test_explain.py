"""Explained recall: the engine's trace, POST /v1/explain, its policy, the clients and `check`.

An explained recall answers what a recall answers and also lists every candidate it looked at, each with
what became of it. It must not change the answer and must not count as a use.
"""
import copy
import logging
import threading
import unittest

from feiyu_memgate import cli, config, registry
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.client import LocalClient, MemoryClient
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.recall import KEEP, RecallContext, RecallStage, recall
from feiyu_memgate.core.redact import MARK, redact
from feiyu_memgate.errors import ConfigError
from feiyu_memgate.forgetting.stage import Freshness
from feiyu_memgate.gateway import server as gateway_server
from feiyu_memgate.gateway.app import Gateway, default_stages
from feiyu_memgate.model import Candidate, ClientPolicy, Hit
from tests import test_forgetting_engine as fe
from tests.test_audience import GROUP_ID, MemTenants
from tests.test_gateway import GatewayTestCase
from tests.test_stages import FakeStore, StageTestCase

TOKEN_OWNER = "tok-explain-own-".ljust(40, "0")
TOKEN_PLAIN = "tok-explain-pln-".ljust(40, "0")
SECRET_TEXT = "the key is sk-abcdefghijklmnop1234567890 keep it"


def make(name, **options):
    return registry.create("recall_stage", {"type": name, **options})


def hits(*rows):
    """(ref, score, text) rows as Hit objects."""
    return [Hit(ref, score, text) for ref, score, text in rows]


class Stage(RecallStage):
    """A stage whose behaviour a test hands in: prepare and admit are plain functions."""

    def __init__(self, name, prepare=None, admit=None, cheap=False):
        self.name = name
        self.cheap = cheap
        self._prepare, self._admit = prepare, admit
        self.finished = 0

    def prepare(self, candidates, ctx):
        return candidates if self._prepare is None else self._prepare(candidates, ctx)

    def admit(self, candidate, ctx):
        return KEEP if self._admit is None else self._admit(candidate, ctx)

    def finish(self, ctx):
        self.finished += 1


class EngineCase(StageTestCase):
    def explain(self, store_hits, stage_list, limit=5, **kw):
        out = recall({"a": FakeStore(store_hits)}, stage_list, self.ctx(limit=limit, trace=True, **kw))
        return out, {c["id"]: c for c in out["candidates"]}


class OutcomeTests(EngineCase):
    def test_delivered(self):
        out, rows = self.explain(hits(("m1", 0.9, "likes tea")), [])
        self.assertEqual(rows["m1"], {"source": "a", "id": "m1", "found": 0.9, "score": 0.9, "outcome": "delivered",
                                      "stage": None, "added_by": None, "text": "likes tea"})
        self.assertEqual([r["id"] for r in out["results"]], ["m1"])

    def test_dropped_in_prepare_names_the_stage(self):
        out, rows = self.explain(hits(("m1", 0.9, "good"), ("m2", 0.2, "weak")), [make("min_score", value=0.5)])
        self.assertEqual((rows["m2"]["outcome"], rows["m2"]["stage"]), ("dropped", "min_score"))
        self.assertEqual((rows["m1"]["outcome"], rows["m1"]["stage"]), ("delivered", None))
        self.assertEqual(out["stages"], ["min_score"])

    def test_dropped_by_a_verdict_names_the_stage(self):
        _, rows = self.explain(hits(("m1", 0.9, "likes oolong tea a lot"), ("m2", 0.8, "likes oolong tea a lot")),
                               [make("dedupe")])
        self.assertEqual((rows["m1"]["outcome"], rows["m1"]["stage"]), ("delivered", None))
        self.assertEqual((rows["m2"]["outcome"], rows["m2"]["stage"]), ("dropped", "dedupe"))

    def test_deferred_names_the_stage_and_is_counted_in_more(self):
        out, rows = self.explain(hits(("m1", 0.9, "short"), ("m2", 0.8, "x" * 30)), [make("budget", max_chars=10)])
        self.assertEqual((rows["m2"]["outcome"], rows["m2"]["stage"]), ("deferred", "budget"))
        self.assertEqual(out["more"], 1)

    def test_over_limit_has_no_stage(self):
        out, rows = self.explain(hits(("m1", 0.9, "one"), ("m2", 0.8, "two"), ("m3", 0.7, "three")), [], limit=1)
        self.assertEqual([rows[r]["outcome"] for r in ("m1", "m2", "m3")], ["delivered", "over_limit", "over_limit"])
        self.assertEqual((rows["m2"]["stage"], out["more"]), (None, 2))

    def test_a_cheap_stage_dropping_past_the_limit_is_a_drop(self):
        out, rows = self.explain(hits(("m1", 0.9, "likes oolong tea a lot"), ("m2", 0.8, "likes oolong tea a lot"),
                                      ("m3", 0.7, "the cat sleeps all day")), [make("dedupe")], limit=1)
        self.assertEqual((rows["m2"]["outcome"], rows["m2"]["stage"]), ("dropped", "dedupe"))
        self.assertEqual((rows["m3"]["outcome"], rows["m3"]["stage"]), ("over_limit", None))
        self.assertEqual(out["more"], 1)

    def test_not_reached_when_the_deadline_passes_in_the_admit_loop(self):
        slow = Stage("slow", admit=lambda c, ctx: setattr(self.clock, "t", self.clock.t + 10) or KEEP)
        out, rows = self.explain(hits(("m1", 0.9, "one"), ("m2", 0.8, "two")), [slow], deadline=self.clock() + 1)
        self.assertEqual((rows["m1"]["outcome"], rows["m2"]["outcome"]), ("delivered", "not_reached"))
        self.assertEqual((rows["m2"]["stage"], out["more"]), (None, 1))

    def test_not_reached_when_the_deadline_passes_among_the_candidates_past_the_limit(self):
        def tick(candidate, ctx):
            if candidate.ref == "m2":
                self.clock.t += 10
            return KEEP
        cheap = Stage("ticker", admit=tick, cheap=True)
        out, rows = self.explain(hits(("m1", 0.9, "one"), ("m2", 0.8, "two"), ("m3", 0.7, "three")), [cheap],
                                 limit=1, deadline=self.clock() + 1)
        self.assertEqual([rows[r]["outcome"] for r in ("m1", "m2", "m3")], ["delivered", "over_limit", "not_reached"])
        self.assertEqual(out["more"], 2)

    def test_a_stage_that_raises_in_admit_is_the_one_that_dropped_it(self):
        def boom(candidate, ctx):
            raise RuntimeError("no")
        notes = []
        _, rows = self.explain(hits(("m1", 0.9, "one")), [Stage("boom", admit=boom)], notes=notes)
        self.assertEqual((rows["m1"]["outcome"], rows["m1"]["stage"]), ("dropped", "boom"))
        self.assertIn("stage-error:boom", notes)

    def test_a_bad_verdict_is_a_drop_by_that_stage(self):
        _, rows = self.explain(hits(("m1", 0.9, "one")), [Stage("forgot", admit=lambda c, ctx: None)])
        self.assertEqual((rows["m1"]["outcome"], rows["m1"]["stage"]), ("dropped", "forgot"))

    def test_a_candidate_added_in_prepare_says_who_added_it(self):
        def add(candidates, ctx):
            store = candidates[0].store
            return list(candidates) + [Candidate(score=0.5, source="a", store=store, ref="extra", text="added text")]
        out, rows = self.explain(hits(("m1", 0.9, "one")), [Stage("adder", prepare=add)])
        self.assertEqual((rows["extra"]["added_by"], rows["extra"]["found"], rows["extra"]["outcome"]),
                         ("adder", 0.5, "delivered"))
        self.assertIsNone(rows["m1"]["added_by"])

    def test_a_stage_that_returns_new_objects_does_not_split_a_candidate(self):
        def rebuild(candidates, ctx):
            return [Candidate(score=c.score, source=c.source, store=c.store, ref=c.ref, text=c.text + "!")
                    for c in candidates]
        out, rows = self.explain(hits(("m1", 0.9, "one")), [Stage("new", prepare=rebuild)])
        self.assertEqual(len(out["candidates"]), 1)
        self.assertEqual(rows["m1"]["text"], "one!")

    def test_the_linked_stage_adds_and_is_named(self):
        class Neighbours(FakeStore):
            def neighbours(self, ref, limit, timeout=None, ctl=None):
                return [Hit("near", 0.8, "a memory next to it")]
        store = Neighbours(hits(("m1", 0.9, "the first")))
        out = recall({"a": store}, [make("linked")], self.ctx(trace=True))
        rows = {c["id"]: c for c in out["candidates"]}
        self.assertEqual((rows["near"]["added_by"], rows["near"]["outcome"]), ("linked", "delivered"))
        self.assertEqual(out["stages"], ["linked"])


class ConsistencyTests(EngineCase):
    def test_every_candidate_once_and_the_counts_add_up(self):
        rows = hits(("m1", 0.9, "likes oolong tea a lot"), ("m2", 0.85, "likes oolong tea a lot"),
                    ("m3", 0.8, "x" * 40), ("m4", 0.7, "the cat sleeps"), ("m5", 0.6, "a dog barks"),
                    ("m6", 0.55, "birds sing"), ("m7", 0.1, "noise"))
        out, by_id = self.explain(rows, [make("min_score", value=0.5), make("dedupe"), make("budget", max_chars=60)],
                                  limit=3)
        ids = [c["id"] for c in out["candidates"]]
        self.assertEqual(sorted(ids), sorted(r.ref for r in rows))
        self.assertEqual(len(ids), len(set(ids)))
        def count(outcome):
            return sum(c["outcome"] == outcome for c in out["candidates"])

        self.assertEqual(count("delivered"), len(out["results"]))
        self.assertEqual(count("deferred") + count("over_limit") + count("not_reached"), out["more"])
        self.assertGreater(out["more"], 0)

    def test_found_and_score_for_a_stage_that_rescales(self):
        out, rows = self.explain(hits(("m1", 0.8, "one")), [make("source_weight", weights={"a": 0.5})])
        self.assertEqual((rows["m1"]["found"], round(rows["m1"]["score"], 6)), (0.8, 0.4))
        self.assertEqual(round(out["results"][0]["score"], 6), 0.4)


class SameAnswerTests(StageTestCase):
    def sets(self):
        return {
            "none": lambda: [],
            "default": default_stages,
            "lines": lambda: [make("min_score", value=0.4), make("dedupe"), make("budget", max_chars=50)],
            "weighted": lambda: [make("source_weight", weights={"a": 0.9, "b": 0.6}), make("relative_score")],
        }

    def store(self, source):
        rows = [("%s%d" % (source, i), 0.95 - i * 0.07, "memory %d about oolong tea from %s" % (i, source))
                for i in range(8)] + [("%sdup" % source, 0.5, "memory 0 about oolong tea from %s" % source),
                                      ("%sempty" % source, 0.45, "")]
        contents = {"%sempty" % source: "filled in by a read"}
        return FakeStore(hits(*rows), contents=contents)

    def test_the_same_query_answers_the_same_with_and_without_tracing(self):
        for name, make_stages in self.sets().items():
            for limit in (2, 5):
                with self.subTest(stages=name, limit=limit):
                    plain = recall({"a": self.store("a"), "b": self.store("b")}, make_stages(), self.ctx(limit=limit))
                    traced = recall({"a": self.store("a"), "b": self.store("b")}, make_stages(),
                                    self.ctx(limit=limit, trace=True))
                    for key in ("results", "more", "failed", "busy"):
                        self.assertEqual(plain[key], traced[key], key)
                    self.assertNotIn("candidates", plain)
                    self.assertNotIn("stages", plain)

    def test_no_trace_by_default(self):
        self.assertIsNone(self.ctx().trace)
        self.assertIsNotNone(self.ctx(trace=True).trace)


class NoUseTests(EngineCase):
    def test_finish_is_not_run_when_tracing(self):
        stage = Stage("watch")
        self.explain(hits(("m1", 0.9, "one")), [stage])
        self.assertEqual(stage.finished, 0)
        recall({"a": FakeStore(hits(("m1", 0.9, "one")))}, [stage], self.ctx())
        self.assertEqual(stage.finished, 1)


class ForgettingUseTests(fe.EngineCase):
    def setUp(self):
        super().setUp()
        self.pool = Pool()
        self.addCleanup(self.pool.close)
        self.stage = Freshness(self.engine, floor=0.3, credit="all")
        self.store = fe.FakeStore({"best": ("tea best", fe.date_of(fe.T0 - fe.DAY))})

    def run_recall(self, trace):
        ctx = RecallContext("tea", 5, pool=self.pool, trace=trace)
        return recall({"private": self.store}, [self.stage], ctx)

    def used(self):
        return {r for r, e in self.ledger.get_many("private", ["best"]).items() if e.uses}

    def test_an_explained_recall_credits_no_use_and_an_ordinary_one_does(self):
        out = self.run_recall(trace=True)
        self.assertEqual(out["candidates"][0]["outcome"], "delivered")
        self.assertEqual(self.used(), set())
        self.run_recall(trace=False)
        self.assertEqual(self.used(), {"best"})


class TextTests(EngineCase):
    def test_text_is_redacted_even_when_no_stage_did_it(self):
        self.assertNotEqual(redact(SECRET_TEXT), SECRET_TEXT)  # the premise: this text holds a secret
        _, rows = self.explain(hits(("m1", 0.2, SECRET_TEXT)), [make("min_score", value=0.5)], redact=redact)
        self.assertEqual(rows["m1"]["outcome"], "dropped")
        self.assertIn(MARK, rows["m1"]["text"])
        self.assertNotIn("sk-abc", rows["m1"]["text"])

    def test_text_is_redacted_before_it_is_cut(self):
        text = "word " * 58 + "sk-abcdefghijklmnop1234567890 tail"
        _, rows = self.explain(hits(("m1", 0.9, text)), [], redact=redact)
        self.assertNotIn("sk-", rows["m1"]["text"])
        self.assertEqual(len(rows["m1"]["text"]), 300)

    def test_long_text_is_cut_at_300_and_says_so(self):
        _, rows = self.explain(hits(("m1", 0.9, "x" * 400), ("m2", 0.8, "y" * 300)), [])
        self.assertEqual((len(rows["m1"]["text"]), rows["m1"]["cut"]), (300, True))
        self.assertEqual(len(rows["m2"]["text"]), 300)
        self.assertNotIn("cut", rows["m2"])

    def test_no_text_no_key(self):
        _, rows = self.explain(hits(("m1", 0.9, "")), [make("min_score", value=0.5)])
        self.assertNotIn("text", rows["m1"])

    def test_a_redactor_that_fails_shows_no_text(self):
        def bad(text):
            raise ValueError("boom")
        _, rows = self.explain(hits(("m1", 0.9, "secret")), [], redact=bad)
        self.assertNotIn("text", rows["m1"])

    def test_date_only_when_there_is_one(self):
        store = FakeStore([Hit("m1", 0.9, "one", date="2026-11-01"), Hit("m2", 0.8, "two")])
        out = recall({"a": store}, [], self.ctx(trace=True))
        rows = {c["id"]: c for c in out["candidates"]}
        self.assertEqual(rows["m1"]["date"], "2026-11-01")
        self.assertNotIn("date", rows["m2"])


class OrderTests(EngineCase):
    def test_delivered_first_in_delivery_order_then_the_rest_by_score(self):
        rows = hits(("m1", 0.9, "one"), ("m2", 0.8, "two"), ("m3", 0.7, "three"), ("m4", 0.6, "four"),
                    ("low", 0.3, "weak"))
        out, _ = self.explain(rows, [make("min_score", value=0.5)], limit=2)
        self.assertEqual([(c["id"], c["outcome"]) for c in out["candidates"]],
                         [("m1", "delivered"), ("m2", "delivered"), ("m3", "over_limit"), ("m4", "over_limit"),
                          ("low", "dropped")])

    def test_a_dropped_one_with_a_better_score_comes_after_the_delivered(self):
        out, _ = self.explain(hits(("m1", 0.5, "likes oolong tea a lot"), ("m2", 0.9, "likes oolong tea a lot")),
                              [make("dedupe")])
        self.assertEqual([(c["id"], c["outcome"]) for c in out["candidates"]],
                         [("m2", "delivered"), ("m1", "dropped")])

    def test_at_most_100_and_the_cut_is_said(self):
        rows = hits(*[("m%03d" % i, 1.0 - i / 1000, "text %d" % i) for i in range(120)])
        out, _ = self.explain(rows, [], limit=3)
        self.assertEqual(len(out["candidates"]), 100)
        self.assertTrue(out["candidates_cut"])
        self.assertEqual([c["id"] for c in out["candidates"][:3]], ["m000", "m001", "m002"])
        self.assertEqual(out["candidates"][-1]["id"], "m099")

    def test_exactly_100_is_not_a_cut(self):
        rows = hits(*[("m%03d" % i, 1.0 - i / 1000, "text %d" % i) for i in range(100)])
        out, _ = self.explain(rows, [], limit=3)
        self.assertEqual(len(out["candidates"]), 100)
        self.assertNotIn("candidates_cut", out)


# ---------------------------------------------------------------------------------------------- gateway


def build_gateway(log):
    memories = ["likes oolong tea", "likes oolong tea with milk", "the cat is named Mochi"]
    stores = {"m": InMemoryBackend(memories=memories)}
    clients = {
        "owner": ClientPolicy("owner", recall=("m",), explain=True),
        "plain": ClientPolicy("plain", recall=("m",)),
    }
    gateway = Gateway(stores, clients, {"owner": TOKEN_OWNER, "plain": TOKEN_PLAIN},
                      stages=[make("min_score", value=0.2), make("dedupe"), make("budget")], log=log)
    return gateway


class HttpCase(GatewayTestCase):
    """The shared helpers of GatewayTestCase (request, _stop) over a gateway of this file's own."""

    def setUp(self):
        self.logs = []
        self.gw = build_gateway(self.logs.append)
        self.addCleanup(self.gw.close)
        self.server = gateway_server.make_server(self.gw, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.port = self.server.server_address[1]

    def request(self, method, path, body=None, token=TOKEN_OWNER, **kw):
        return super().request(method, path, body, token=token, **kw)

    def recall(self, query="oolong tea", token=TOKEN_OWNER, **extra):
        return super().recall(query, token=token, **extra)

    def explain_request(self, token=TOKEN_OWNER, query="oolong tea", **body):
        return self.request("POST", "/v1/explain", dict(query=query, **body), token=token)


class ExplainRouteTests(HttpCase):
    def test_a_client_without_explain_is_forbidden(self):
        status, body = self.explain_request(token=TOKEN_PLAIN)
        self.assertEqual((status, body), (403, {"error": "forbidden", "code": "forbidden"}))

    def test_a_client_with_explain_gets_the_recall_answer_and_the_two_fields(self):
        status, body = self.explain_request()
        self.assertEqual(status, 200)
        _, recalled = self.recall("oolong tea")
        for key in ("results", "more", "failed", "busy"):
            self.assertEqual(body[key], recalled[key], key)
        self.assertEqual(body["stages"], ["min_score", "dedupe", "budget"])
        self.assertEqual(sum(c["outcome"] == "delivered" for c in body["candidates"]), len(body["results"]))
        self.assertTrue(all(c["source"] == "m" and isinstance(c["id"], str) for c in body["candidates"]))
        dropped = [c for c in body["candidates"] if c["outcome"] == "dropped"]
        self.assertEqual([c["stage"] for c in dropped], ["dedupe"])

    def test_no_token_is_unauthorized(self):
        self.assertEqual(self.explain_request(token=None)[0], 401)

    def test_the_body_checks_answer_like_recall(self):
        cases = [dict(query=""), dict(query=123), dict(query="x" * 600), dict(limit="3"), dict(limit=True),
                 dict(context="no"), dict(context={"a": [1]})]
        for case in cases:
            with self.subTest(case=case):
                body = dict(query="oolong tea")
                body.update(case)
                asked = self.request("POST", "/v1/explain", body)
                self.assertEqual(asked, self.request("POST", "/v1/recall", body))
                self.assertEqual(asked[0], 400)

    def test_a_get_is_not_allowed(self):
        status, body = self.request("GET", "/v1/explain")
        self.assertEqual((status, body["code"]), (405, "method_not_allowed"))

    def test_the_access_log_note_starts_with_explain_and_holds_no_content(self):
        self.explain_request(query="oolong tea")
        line = [x for x in self.logs if "/v1/explain" in x][-1]
        self.assertIn("200", line)
        self.assertTrue(line.split("ms", 1)[1].strip().startswith("explain"), line)
        for content in ("oolong", "Mochi", "likes"):
            self.assertNotIn(content, line)

    def test_admit_needs_the_right_to_recall_and_the_explain_setting(self):
        self.assertIsNone(self.gw.admit("owner", "explain"))
        self.assertEqual(self.gw.admit("plain", "explain").status, 403)
        self.assertIsNone(self.gw.admit("plain", "recall"))


class PolicyTests(unittest.TestCase):
    def build(self, clients, **kw):
        gateway = Gateway({"m": InMemoryBackend()}, clients, {n: ("tok-%s-" % n).ljust(40, "0") for n in clients},
                          log=lambda line: None, **kw)
        self.addCleanup(gateway.close)
        return gateway

    def test_explain_needs_recall(self):
        with self.assertRaises(ConfigError) as caught:
            self.build({"c": ClientPolicy("c", recall=None, write="m", explain=True)})
        self.assertIn("clients.c.explain needs recall: there is nothing to explain for a client that may not recall",
                      str(caught.exception))

    def test_explain_is_for_owner_level_clients(self):
        clients = {"c": ClientPolicy("c", recall=("m",), public=True, audience="group", explain=True)}
        with self.assertRaises(ConfigError) as caught:
            self.build(clients, audiences={"m": "public"})
        message = str(caught.exception)
        self.assertIn("clients.c.explain is for owner-level clients: it shows the candidates a recall left out, "
                      "with their scores", message)
        self.assertIn('the client\'s audience is "group"', message)

    def test_a_public_client_is_refused_too(self):
        with self.assertRaises(ConfigError):
            self.build({"c": ClientPolicy("c", recall=("m",), public=True, explain=True)}, audiences={"m": "public"})

    def test_off_by_default(self):
        self.assertFalse(ClientPolicy("c").explain)
        self.assertFalse(config.client_policy("c", {"recall": ["m"]}).explain)

    def test_the_setting_must_be_a_bool(self):
        with self.assertRaises(ConfigError) as caught:
            config.client_policy("c", {"recall": ["m"], "explain": "yes"})
        self.assertIn("clients.c.explain", str(caught.exception))
        self.assertTrue(config.client_policy("c", {"recall": ["m"], "explain": True}).explain)

    def test_the_configuration_builds_a_gateway_with_it(self):
        cfg = {"stores": {"m": {"type": "inmemory"}},
               "clients": {"c": {"token_env": "T", "recall": ["m"], "explain": True}}}
        gateway = config.build_gateway(copy.deepcopy(cfg), env={"T": "tok-cfg-".ljust(40, "0")}, log=lambda line: None)
        self.addCleanup(gateway.close)
        self.assertTrue(gateway.clients["c"].explain)

    def test_an_owner_level_tenant_client_explains_the_tenants_store(self):
        provider = MemTenants()
        provider.stores[GROUP_ID].put("the group likes oolong tea")
        policy = ClientPolicy("t", recall=("@tenant",), tenants="g", explain=True)
        gateway = self.build({"t": policy}, tenants={"g": provider})
        reply = gateway.explain("t", {"query": "oolong tea", "tenant_id": GROUP_ID})
        self.assertEqual(reply.status, 200)
        self.assertEqual([(c["source"], c["outcome"]) for c in reply.body["candidates"]], [("g", "delivered")])
        self.assertTrue(reply.note.startswith("explain"))

    def test_gateway_explain_refuses_a_client_without_it(self):
        gateway = self.build({"c": ClientPolicy("c", recall=("m",))})
        self.assertEqual(gateway.explain("c", {"query": "tea"}).status, 403)


# ---------------------------------------------------------------------------------------------- clients


class ClientTests(HttpCase):
    def setUp(self):
        super().setUp()
        logging.getLogger("feiyu_memgate.client").addHandler(logging.NullHandler())
        self.url = "http://127.0.0.1:%d" % self.port

    def check(self, answer):
        self.assertEqual(sorted(answer), ["busy", "candidates", "failed", "more", "results", "stages"])
        self.assertEqual(answer["stages"], ["min_score", "dedupe", "budget"])
        self.assertTrue(answer["candidates"])
        self.assertTrue(answer["results"])

    def test_memory_client_returns_the_dict(self):
        self.check(MemoryClient(self.url, TOKEN_OWNER).explain("oolong tea"))

    def test_memory_client_returns_none_when_refused_or_misused(self):
        self.assertIsNone(MemoryClient(self.url, TOKEN_PLAIN).explain("oolong tea"))
        client = MemoryClient(self.url, TOKEN_OWNER)
        self.assertIsNone(client.explain("   "))
        self.assertIsNone(client.explain("x" * 600))
        self.assertIsNone(client.explain("oolong tea", limit="3"))
        self.assertIsNone(MemoryClient("", TOKEN_OWNER).explain("oolong tea"))

    def test_memory_client_passes_limit_and_context(self):
        answer = MemoryClient(self.url, TOKEN_OWNER).explain("oolong tea", limit=1, context={"chat": "a"})
        self.assertEqual(len(answer["results"]), 1)

    def test_local_client_returns_the_dict(self):
        with LocalClient(self.gw, client="owner", start=False) as client:
            self.check(client.explain("oolong tea"))

    def test_local_client_returns_none_on_403(self):
        with LocalClient(self.gw, client="plain", start=False) as client:
            self.assertIsNone(client.explain("oolong tea"))
            self.assertTrue(client.recall("oolong tea"))


class DescribeTests(unittest.TestCase):
    def test_only_a_client_with_explain_gets_the_words(self):
        gateway = build_gateway(lambda line: None)
        self.addCleanup(gateway.close)
        lines = {line.split(":")[0].strip(): line for line in cli.describe(gateway) if "令牌" in line}
        self.assertIn("；可以查看召回的解释", lines["owner"])
        self.assertNotIn("可以查看召回的解释", lines["plain"])


if __name__ == "__main__":
    unittest.main()
