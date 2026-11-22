"""Recent turns in a recall request: the optional body key `history` of POST /v1/recall and POST /v1/explain.

The rule (written by hand here, not computed by the code under test): a list of at most 6 items, each
{"role": "user" | "assistant", "content": a string of 1 to 500 characters that is not blank}; other keys of an
item are dropped; anything else is 400 `bad_history`; null or absent means none. It reaches the recall stages as
`RecallContext.history` and nothing else: no built-in stage reads it, it is not stored, not logged, and the answer
does not depend on it.
"""
import http.client
import json
import logging
import threading
import unittest

from feiyu_memgate import model
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.client import LocalClient, MemoryClient
from feiyu_memgate.core.recall import RecallStage
from feiyu_memgate.gateway import server as gateway_server, validate
from feiyu_memgate.gateway.app import Gateway
from feiyu_memgate.model import ClientPolicy
from tests.test_client_http import FakeGateway

TOKEN = "tok-history-fake-".ljust(40, "0")
FACT = "the shared fact about oolong tea"
MARKER = "zebra quartz marker about the harbour lantern"


def turn(role="user", content="hello"):
    return {"role": role, "content": content}


GOOD = (
    [],
    [turn()],
    [turn("assistant", "hi there"), turn("user", "what do I like to drink")],
    [turn(content="x" * 500)],
    [turn(content="a") for _ in range(6)],
    [dict(turn(), extra="dropped", id=5)],
    [turn(content=" padded ")],
)
BAD = {
    "a string": "hello",
    "an object": {"role": "user", "content": "hello"},
    "a number": 5,
    "true": True,
    "seven items": [turn(content=str(i)) for i in range(7)],
    "an item that is a string": ["hello"],
    "an item that is a list": [[turn()]],
    "an item that is null": [None],
    "no role": [{"content": "hello"}],
    "a role that is not one": [turn("system")],
    "a role in capitals": [turn("User")],
    "a role that is null": [turn(None)],
    "a role that is a number": [turn(5)],
    "no content": [{"role": "user"}],
    "content that is null": [turn(content=None)],
    "content that is a number": [turn(content=5)],
    "content that is a list": [turn(content=["hi"])],
    "empty content": [turn(content="")],
    "blank content": [turn(content="   \n\t ")],
    "501 characters": [turn(content="x" * 501)],
    "one bad item among good ones": [turn(), turn(), {"role": "user", "content": ""}],
}


class Look(RecallStage):
    """Keeps what each request's context says about the history."""

    def __init__(self):
        self.seen = []

    def prepare(self, candidates, ctx):
        self.seen.append(ctx.history)
        return candidates


class Case(unittest.TestCase):
    def gateway(self, stages=None, lines=None, **policy):
        policy = dict({"recall": ("s",), "write": "s", "explain": True}, **policy)
        gw = Gateway({"s": InMemoryBackend(memories=[FACT])}, {"o": ClientPolicy("o", **policy)}, {"o": TOKEN},
                     stages=stages, log=(lines.append if lines is not None else lambda line: None))
        self.addCleanup(gw.close)
        return gw


class ValidationTests(Case):
    def ask(self, gw, call, history):
        body = {"query": FACT}
        if history is not ...:
            body["history"] = history
        return getattr(gw, call)("o", body)

    def test_the_good_ones_are_answered(self):
        gw = self.gateway()
        for call in ("recall", "explain"):
            for history in GOOD:
                with self.subTest(call=call, history=str(history)[:40]):
                    self.assertEqual(self.ask(gw, call, history).status, 200)

    def test_absent_and_null_mean_none(self):
        gw = self.gateway()
        for call in ("recall", "explain"):
            for history in (..., None):
                with self.subTest(call=call, history=history):
                    self.assertEqual(self.ask(gw, call, history).status, 200)

    def test_the_bad_ones_are_400_bad_history(self):
        gw = self.gateway()
        for call in ("recall", "explain"):
            for what, history in BAD.items():
                with self.subTest(call=call, what=what):
                    reply = self.ask(gw, call, history)
                    self.assertEqual((reply.status, reply.body["code"]), (400, "bad_history"))
                    self.assertEqual(reply.body["error"], "bad history")

    def test_the_other_checks_keep_their_own_codes(self):
        gw = self.gateway()
        reply = gw.recall("o", {"query": FACT, "history": BAD["seven items"], "context": {"k": [1]}})
        self.assertEqual(reply.body["code"], "bad_context")  # the checks run in the order of the request's keys
        self.assertEqual(gw.recall("o", {"query": "", "history": BAD["seven items"]}).body["code"], "bad_query")

    def test_the_cleaned_value(self):
        self.assertEqual(model.clean_history(None), [])
        self.assertEqual(model.clean_history([]), [])
        self.assertEqual(model.clean_history([dict(turn("assistant", "hi"), extra=1)]), [turn("assistant", "hi")])
        self.assertIsNone(model.clean_history("x"))
        self.assertIs(validate._valid_history, model.clean_history)  # one rule for the gateway and the clients
        self.assertEqual((model.MAX_HISTORY, model.MAX_HISTORY_CHARS), (6, 500))

    def test_a_refused_request_stores_and_runs_nothing(self):
        look = Look()
        gw = self.gateway(stages=[look])
        gw.recall("o", {"query": FACT, "history": BAD["seven items"]})
        self.assertEqual(look.seen, [])


class StageTests(Case):
    def test_a_stage_sees_the_history_as_a_list_of_dicts(self):
        look = Look()
        gw = self.gateway(stages=[look])
        history = [dict(turn("user", "I like tea"), extra="dropped"), turn("assistant", "noted")]
        self.assertEqual(gw.recall("o", {"query": FACT, "history": history}).status, 200)
        self.assertEqual(gw.explain("o", {"query": FACT, "history": history}).status, 200)
        expected = [turn("user", "I like tea"), turn("assistant", "noted")]
        self.assertEqual(look.seen, [expected, expected])
        self.assertTrue(all(isinstance(item, dict) for item in look.seen[0]))

    def test_without_history_it_is_an_empty_list(self):
        look = Look()
        gw = self.gateway(stages=[look])
        gw.recall("o", {"query": FACT})
        gw.recall("o", {"query": FACT, "history": None})
        gw.recall("o", {"query": FACT, "history": []})
        self.assertEqual(look.seen, [[], [], []])
        self.assertTrue(all(isinstance(item, list) for item in look.seen))

    def test_each_request_has_its_own_list(self):
        seen = []

        class Scribble(RecallStage):
            def prepare(self, candidates, ctx):
                seen.append(ctx.history)
                ctx.history.append(turn("user", "scribbled"))
                ctx.history[0]["content"] = "changed"
                return candidates

        gw = self.gateway(stages=[Scribble()])
        history = [turn("user", "mine")]
        gw.recall("o", {"query": FACT, "history": history})
        gw.recall("o", {"query": FACT, "history": history})
        self.assertEqual(history, [turn("user", "mine")])  # the caller's list is not touched
        self.assertEqual(seen[0], seen[1])

    def test_the_context_default_is_empty(self):
        from feiyu_memgate.core.pool import Pool
        from feiyu_memgate.core.recall import RecallContext
        pool = Pool()
        self.addCleanup(pool.close)
        self.assertEqual(RecallContext("q", 3, pool=pool).history, [])
        self.assertEqual(RecallContext("q", 3, pool=pool, history=[turn()]).history, [turn()])


class NothingElseTests(Case):
    def test_the_answer_does_not_depend_on_it(self):
        gw = self.gateway()
        for call in ("recall", "explain"):
            plain = getattr(gw, call)("o", {"query": FACT})
            for history in GOOD:
                with self.subTest(call=call, history=str(history)[:30]):
                    with_history = getattr(gw, call)("o", {"query": FACT, "history": history})
                    self.assertEqual((with_history.status, with_history.body), (plain.status, plain.body))
                    self.assertEqual(with_history.note, plain.note)
        self.assertTrue(plain.body["results"])  # the comparison is about a real answer

    def test_the_history_reaches_no_built_in_stage_and_no_answer(self):
        gw = self.gateway()
        reply = gw.recall("o", {"query": FACT, "history": [turn(content=MARKER)]})
        self.assertNotIn(MARKER, json.dumps(reply.body))
        self.assertNotIn(MARKER, reply.note)

    def test_it_is_not_stored(self):
        gw = self.gateway()
        gw.recall("o", {"query": FACT, "history": [turn(content=MARKER)]})
        self.assertEqual([hit.text for hit in gw.stores["s"].find(MARKER, 50)], [FACT])  # the one memory it started with
        self.assertEqual([hit.text for hit in gw.stores["s"].find("zebra harbour lantern history", 50) if "zebra" in hit.text], [])

    def test_nothing_of_it_is_in_the_access_log(self):
        lines = []
        gw = self.gateway(lines=lines)
        server = gateway_server.make_server(gw, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(5)

        self.addCleanup(stop)

        def post(path, body):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            try:
                conn.request("POST", path, body=json.dumps(body).encode(),
                             headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
                resp = conn.getresponse()
                return resp.status, json.loads(resp.read())
            finally:
                conn.close()

        history = [turn(content=MARKER), turn("assistant", MARKER + " again")]
        for path in ("/v1/recall", "/v1/explain"):
            status, _ = post(path, {"query": FACT, "history": history})
            self.assertEqual(status, 200)
            status, body = post(path, {"query": FACT, "history": history * 4})
            self.assertEqual((status, body["code"]), (400, "bad_history"))
        self.assertTrue(lines)
        self.assertEqual([line for line in lines if "zebra" in line or "harbour" in line], [])
        self.assertEqual(len(lines), 4)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGateway()
        self.addCleanup(self.fake.close)
        self.client = MemoryClient(self.fake.url, "tok-owner-fake")
        self.fake.body = {"results": [], "stages": [], "candidates": []}
        logging.getLogger("feiyu_memgate.client").addHandler(logging.NullHandler())

    def last(self):
        return self.fake.requests[-1]["body"]

    def test_the_http_client_sends_it_when_given(self):
        history = [dict(turn("assistant", "hi"), extra="dropped"), turn("user", "tea?")]
        calls = (lambda: self.client.recall("hello", history=history),
                 lambda: self.client.recall_detail("hello", history=history),
                 lambda: self.client.explain("hello", history=history))
        for call in calls:
            call()
            self.assertEqual(self.last()["history"], [turn("assistant", "hi"), turn("user", "tea?")])
        self.assertEqual(len(self.fake.requests), 3)
        self.assertEqual([r["path"] for r in self.fake.requests], ["/v1/recall", "/v1/recall", "/v1/explain"])

    def test_the_body_is_unchanged_without_it(self):
        self.client.recall("hello")
        self.assertEqual(self.last(), {"query": "hello", "limit": 5})
        self.client.recall_detail("hello", history=None)
        self.assertEqual(self.last(), {"query": "hello", "limit": 5})
        self.client.explain("hello")
        self.assertEqual(self.last(), {"query": "hello", "limit": 5})

    def test_it_is_keyword_only(self):
        with self.assertRaises(TypeError):
            self.client.recall("hello", 8.5, None, None, None, [turn()])
        with self.assertRaises(TypeError):
            self.client.explain("hello", 8.5, None, [turn()])

    def test_a_bad_one_is_not_sent_and_fails_like_a_query_that_is_too_long(self):
        for what, history in BAD.items():
            with self.subTest(what=what):
                before = len(self.fake.requests)
                self.assertIsNone(self.client.recall_detail("hello", history=history))
                self.assertEqual(self.client.recall("hello", history=history), [])
                self.assertIsNone(self.client.explain("hello", history=history))
                self.assertEqual(len(self.fake.requests), before)
        with self.assertLogs("feiyu_memgate.client", "WARNING") as logged:
            self.client.recall_detail("hello", history=BAD["seven items"])
        self.assertIn("bad history", logged.output[0])
        self.assertNotIn("hello", logged.output[0])

    def test_the_local_client_sends_it_to_the_stages(self):
        look = Look()
        gw = Gateway({"s": InMemoryBackend(memories=[FACT])}, {"o": ClientPolicy("o", recall=("s",), explain=True)},
                     {}, stages=[look], log=lambda line: None)
        client = LocalClient(gw, client="o", close_gateway=True)
        self.addCleanup(client.close)
        history = [turn("user", "hello"), turn("assistant", "hi")]
        self.assertTrue(client.recall(FACT, history=history))
        self.assertTrue(client.recall_detail(FACT, history=history))
        self.assertTrue(client.explain(FACT, history=history))
        self.assertEqual(look.seen, [history] * 3)
        look.seen.clear()
        client.recall(FACT)
        self.assertEqual(look.seen, [[]])
        with self.assertLogs("feiyu_memgate.client", "WARNING"):
            self.assertEqual(client.recall(FACT, history=BAD["seven items"]), [])
        self.assertEqual(look.seen, [[]])  # not even run


if __name__ == "__main__":
    unittest.main()
