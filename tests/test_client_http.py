import json
import logging
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from feiyu_memgate import config
from feiyu_memgate.client import MemoryClient, format_block
from feiyu_memgate.client import http as client_http
from feiyu_memgate.gateway import server as gateway_server

TOKEN = "tok-owner-fake"
TENANT_TOKEN = "tok-tenant-fake"


class FakeGateway:
    """A gateway that answers as told. mode: ok | 401 | 403 | 429 | 500 | 503 | slow | redirect | garbage | drip."""

    def __init__(self):
        self.requests = []
        self.sizes = []  # body bytes of each request
        self.mode = "ok"
        self.stop = threading.Event()  # set on close: ends drip handlers
        self.results = [{"source": "core", "text": "likes tea", "score": 0.9}]
        self.status = 200  # status and body of the reply in mode "ok"
        self.body = None   # None: the usual reply for the path
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def handle(self):
                try:
                    super().handle()
                except OSError:
                    pass  # client gave up (timeout tests)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                owner.requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                       "body": body, "ctype": self.headers.get("Content-Type")})
                owner.sizes.append(n)
                m = owner.mode
                if m in ("401", "403", "429", "500"):
                    return self._send(int(m), {"error": "x"})
                if m == "503":
                    return self._send(503, {"error": "unavailable"})
                if m == "slow":
                    threading.Event().wait(1.0)
                if m == "drip":
                    # Valid headers, then one body byte every 0.2s for 30s.
                    self.send_response(200)
                    self.send_header("Content-Length", "1000")
                    self.end_headers()
                    for _ in range(150):
                        if owner.stop.is_set():
                            break
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        owner.stop.wait(0.2)
                    return
                if m == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/steal")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if m == "garbage":
                    raw = b"not json"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if owner.status != 200 or owner.body is not None:
                    return self._send(owner.status, owner.body if owner.body is not None else {"error": "x"})
                if self.path == "/v1/recall":
                    return self._send(200, {"results": owner.results})
                return self._send(200, {"status": "ok"})

            def _send(self, code, obj):
                raw = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.stop.set()
        self.srv.shutdown()
        self.srv.server_close()


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        self.gw = FakeGateway()
        self.addCleanup(self.gw.close)
        # Keep expected warnings off stderr; assertLogs still sees them.
        logging.getLogger("feiyu_memgate.client").addHandler(logging.NullHandler())
        self.client = MemoryClient(self.gw.url, TOKEN, tenant_token=TENANT_TOKEN)


class RecallTests(ClientTestCase):
    def test_recall_returns_results_and_sends_auth(self):
        out = self.client.recall("hello")
        self.assertEqual(out, self.gw.results)
        req = self.gw.requests[0]
        self.assertEqual(req["path"], "/v1/recall")
        self.assertEqual(req["auth"], "Bearer " + TOKEN)
        self.assertEqual(req["body"], {"query": "hello", "limit": 5})
        self.assertEqual(req["ctype"], "application/json")

    def test_recall_limit_option(self):
        MemoryClient(self.gw.url, TOKEN, recall_limit=3).recall("hello")
        self.assertEqual(self.gw.requests[0]["body"]["limit"], 3)

    def test_recall_detail_returns_the_whole_reply(self):
        self.gw.results = [{"source": "private", "text": "x", "score": 1, "date": "2026-11-03"}]
        out = self.client.recall_detail("hello")
        self.assertEqual(out, {"results": self.gw.results, "more": 0, "failed": 0, "busy": 0})

    def test_recall_detail_503_is_none(self):
        self.gw.mode = "503"
        self.assertIsNone(self.client.recall_detail("hello"))
        self.assertEqual(self.client.recall("hello"), [])

    def test_empty_query_sends_nothing(self):
        for query in ("", "  ", None, 5):
            self.assertIsNone(self.client.recall_detail(query))
        self.assertEqual(self.gw.requests, [])

    def test_a_query_over_500_characters_is_never_cut_and_never_sent(self):
        self.assertIsNone(self.client.recall_detail("x" * 501))
        self.assertEqual(self.client.recall("x" * 900), [])
        self.assertEqual(self.gw.requests, [])
        self.client.recall("x" * 500)
        self.assertEqual(len(self.gw.requests[0]["body"]["query"]), 500)

    def test_recall_timeout_returns_empty(self):
        self.gw.mode = "slow"
        self.assertEqual(self.client.recall("hello", timeout=0.3), [])

    def test_recall_drip_feed_ends_near_budget(self):
        self.gw.mode = "drip"
        t0 = time.monotonic()
        self.assertEqual(self.client.recall("hello", timeout=0.5), [])
        self.assertLess(time.monotonic() - t0, 0.5 + 0.5)

    def test_recall_http_error_returns_empty(self):
        for mode in ("500", "401", "429", "503", "garbage"):
            with self.subTest(mode=mode):
                self.gw.mode = mode
                self.assertEqual(self.client.recall("hello"), [])

    def test_recall_401_is_not_retried(self):
        self.gw.mode = "401"
        self.client.recall("hello")
        self.assertEqual(len(self.gw.requests), 1)

    def test_recall_connection_refused_returns_empty(self):
        self.assertEqual(MemoryClient("http://127.0.0.1:1", TOKEN).recall("hello"), [])

    def test_recall_ignores_malformed_results(self):
        self.gw.results = "oops"
        self.assertEqual(self.client.recall("hello"), [])
        self.gw.results = [{"source": "core", "text": "ok", "score": 1}, 5, {"text": 3}]
        self.assertEqual(self.client.recall("hello"), [{"source": "core", "text": "ok", "score": 1}])

    def test_more_and_failed_are_counts_only(self):
        self.gw.body = {"results": [], "more": True, "failed": -2, "busy": "1"}
        self.assertEqual(self.client.recall_detail("hello"), {"results": [], "more": 0, "failed": 0, "busy": 0})
        self.gw.body = {"results": [], "more": 3, "failed": 1, "busy": 2}
        self.assertEqual(self.client.recall_detail("hello"), {"results": [], "more": 3, "failed": 1, "busy": 2})


class ConfigurationTests(ClientTestCase):
    def test_missing_settings_mean_off(self):
        for client in (MemoryClient("", TOKEN), MemoryClient(self.gw.url, ""), MemoryClient(None, None)):
            self.assertFalse(client.configured)
            self.assertEqual(client.recall("hello"), [])
            self.assertFalse(client.remember("hello"))
        self.assertEqual(self.gw.requests, [])

    def test_bad_url_scheme_means_off(self):
        client = MemoryClient("file:///etc/passwd", TOKEN)
        self.assertFalse(client.configured)
        self.assertEqual(client.recall("hello"), [])

    def test_configured_flags(self):
        self.assertTrue(self.client.configured)
        self.assertTrue(self.client.tenant_configured)
        only_main = MemoryClient(self.gw.url, TOKEN)
        self.assertTrue(only_main.configured)
        self.assertFalse(only_main.tenant_configured)

    def test_from_env(self):
        env = {"FEIYU_MEMGATE_URL": " " + self.gw.url + "/ ", "FEIYU_MEMGATE_TOKEN": TOKEN,
               "FEIYU_MEMGATE_TENANT_TOKEN": TENANT_TOKEN}
        client = MemoryClient.from_env(env)
        self.assertEqual((client.base_url, client.token, client.tenant_token), (self.gw.url, TOKEN, TENANT_TOKEN))
        other = MemoryClient.from_env({"MINE_URL": self.gw.url, "MINE_TOKEN": TOKEN}, prefix="MINE")
        self.assertTrue(other.configured)
        self.assertFalse(other.tenant_configured)
        self.assertFalse(MemoryClient.from_env({}).configured)

    def test_env_proxy_is_ignored(self):
        env = {"HTTP_PROXY": "http://127.0.0.1:1", "http_proxy": "http://127.0.0.1:1",
               "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}
        with mock.patch.dict("os.environ", env):
            self.assertEqual(self.client.recall("hello"), self.gw.results)

    def test_redirect_is_not_followed(self):
        self.gw.mode = "redirect"
        self.assertEqual(self.client.recall("hello"), [])
        self.assertFalse(self.client.remember("hello"))
        self.assertEqual(len(self.gw.requests), 2)


class TenantTests(ClientTestCase):
    def test_tenant_call_uses_only_the_tenant_token_and_names_the_tenant(self):
        self.client.recall("hello", tenant_id="g1")
        self.client.remember("hello", tenant_id="g1")
        for req in self.gw.requests:
            self.assertEqual(req["auth"], "Bearer " + TENANT_TOKEN)
            self.assertEqual(req["body"]["tenant_id"], "g1")

    def test_plain_call_never_sends_tenant_id(self):
        self.client.recall("hello")
        self.client.remember("hello")
        for req in self.gw.requests:
            self.assertEqual(req["auth"], "Bearer " + TOKEN)
            self.assertNotIn("tenant_id", req["body"])

    def test_numeric_tenant_id_is_sent_as_text(self):
        self.client.recall("hello", tenant_id=12345)
        self.assertEqual(self.gw.requests[0]["body"]["tenant_id"], "12345")

    def test_no_fallback_between_tokens(self):
        only_main = MemoryClient(self.gw.url, TOKEN)
        self.assertEqual(only_main.recall("hello", tenant_id="g1"), [])
        self.assertEqual(only_main.remember_status("hello", tenant_id="g1"), "failed")
        only_tenant = MemoryClient(self.gw.url, "", tenant_token=TENANT_TOKEN)
        self.assertEqual(only_tenant.recall("hello"), [])
        self.assertEqual(self.gw.requests, [])

    def test_odd_tenant_ids_send_nothing(self):
        for tenant_id in ("", "  ", True, 1.5, ["g"], {"g": 1}):
            with self.subTest(tenant_id=tenant_id):
                self.assertEqual(self.client.recall("hello", tenant_id=tenant_id), [])
                self.assertEqual(self.client.remember_status("hello", tenant_id=tenant_id), "failed")
        self.assertEqual(self.gw.requests, [])


class RememberTests(ClientTestCase):
    def test_remember_ok_sends_user_message(self):
        self.assertTrue(self.client.remember("my name is Fish"))
        req = self.gw.requests[0]
        self.assertEqual(req["path"], "/v1/remember")
        self.assertEqual(req["auth"], "Bearer " + TOKEN)
        self.assertEqual(req["body"], {"messages": [{"role": "user", "content": "my name is Fish"}]})

    def test_a_message_over_4000_characters_is_never_cut_and_never_sent(self):
        self.assertEqual(self.client.remember_status("y" * 4019), "failed")
        self.assertEqual(self.client.remember_messages([{"role": "user", "content": "y" * 4001}]), "failed")
        self.assertEqual(self.gw.requests, [])
        self.assertEqual(self.client.remember_status("y" * 4000), "saved")
        self.assertEqual(len(self.gw.requests[0]["body"]["messages"][0]["content"]), 4000)

    def test_more_than_16000_characters_in_one_write_is_never_sent(self):
        messages = [{"role": "user", "content": "z" * 4000}] * 4 + [{"role": "user", "content": "z"}]
        self.assertEqual(self.client.remember_messages(messages), "failed")
        self.assertEqual(self.gw.requests, [])

    def test_time_and_speaker_are_sent_when_valid(self):
        good = [{"role": "user", "content": "hi", "time": "2026-11-14T12:00:00+08:00", "speaker": "guest",
                 "extra": "dropped"}]
        self.assertEqual(self.client.remember_messages(good), "saved")
        self.assertEqual(self.gw.requests[0]["body"]["messages"],
                         [{"role": "user", "content": "hi", "time": "2026-11-14T12:00:00+08:00", "speaker": "guest"}])
        for bad in ({"time": "t" * 41}, {"speaker": "s" * 65}, {"speaker": 5}):
            self.assertEqual(self.client.remember_messages([dict({"role": "user", "content": "hi"}, **bad)]), "failed")
        self.assertEqual(len(self.gw.requests), 1)

    def test_chinese_text_is_sent_as_utf8_not_escapes(self):
        text = "我喜欢乌龙茶" * 600  # 3600 characters: within the limits
        self.assertEqual(self.client.remember_status(text), "saved")
        self.assertEqual(self.gw.requests[0]["body"]["messages"][0]["content"], text)
        self.assertLess(self.gw.sizes[0], 3 * len(text) + 200)

    def test_skipped_is_its_own_outcome(self):
        self.gw.body = {"status": "skipped"}
        self.assertEqual(self.client.remember_status("x"), "skipped")
        self.assertFalse(self.client.remember("x"))
        self.gw.body = {"status": "who knows"}
        self.assertEqual(self.client.remember_status("x"), "unsure")

    def test_remember_messages(self):
        messages = [{"role": "user", "content": "I moved to Hangzhou"}, {"role": "assistant", "content": "Nice"}]
        self.assertEqual(self.client.remember_messages(messages), "saved")
        self.assertEqual(self.gw.requests[0]["body"], {"messages": messages})

    def test_remember_messages_rejects_a_bad_list_without_sending(self):
        good = {"role": "user", "content": "x"}
        for bad in (None, "x", [], [good] * 21, [{"role": "system", "content": "x"}],
                    [{"role": "user", "content": " "}], [{"role": "user", "content": 5}], [good, "x"]):
            self.assertEqual(self.client.remember_messages(bad), "failed", repr(bad)[:40])
        self.assertEqual(self.gw.requests, [])

    def test_remember_status_limited_matches_on_the_code_not_the_text(self):
        self.gw.status = 429
        self.gw.body = {"error": "daily limit", "code": "daily_limit"}
        self.assertEqual(self.client.remember_status("x"), "limited")
        self.gw.body = {"error": "anything at all", "code": "daily_limit"}
        self.assertEqual(self.client.remember_status("x"), "limited")
        self.gw.body = {"error": "daily limit"}  # the text alone means nothing
        self.assertEqual(self.client.remember_status("x"), "failed")
        self.gw.body = {"error": "rate limited", "code": "rate_limited"}
        self.assertEqual(self.client.remember_status("x"), "failed")
        self.gw.body = {"error": "x", "code": ["daily_limit"]}
        self.assertEqual(self.client.remember_status("x"), "failed")

    def test_definite_failures_and_unsure_answers_by_code(self):
        definite = (("guard_error", 500), ("bad_messages", 400), ("bad_context", 400), ("forbidden", 403),
                    ("unknown_tenant", 403), ("too_large", 413), ("busy", 503))
        for code, status in definite:
            self.gw.status, self.gw.body = status, {"error": "x", "code": code}
            self.assertEqual(self.client.remember_status("x"), "failed", code)
        for code, status in (("backend_unavailable", 502), ("internal", 500), ("something_new", 500)):
            self.gw.status, self.gw.body = status, {"error": "x", "code": code}
            self.assertEqual(self.client.remember_status("x"), "unsure", code)
        self.gw.status, self.gw.body = 409, {"error": "x", "code": "something_new"}
        self.assertEqual(self.client.remember_status("x"), "failed")

    def test_remember_status_429_with_odd_bodies_is_failed(self):
        self.gw.status = 429
        for body in ([1, 2], "daily limit", {"nope": 1}):
            self.gw.body = body
            self.assertEqual(self.client.remember_status("x"), "failed", body)

    def test_remember_status_429_non_json_body_is_failed(self):
        self.gw.mode = "429"
        self.assertEqual(self.client.remember_status("x"), "failed")

    def test_remember_status_outcomes(self):
        self.assertEqual(self.client.remember_status("hello"), "saved")
        for mode in ("401", "429"):
            self.gw.mode = mode
            self.assertEqual(self.client.remember_status("hello"), "failed", mode)
        self.gw.mode = "500"
        self.assertEqual(self.client.remember_status("hello"), "unsure")
        self.gw.mode = "drip"
        self.assertEqual(self.client.remember_status("hello", deadline=time.monotonic() + 0.4), "unsure")
        self.assertEqual(MemoryClient("http://127.0.0.1:1", TOKEN).remember_status("hello"), "failed")

    def test_remember_drip_feed_ends_near_budget(self):
        self.gw.mode = "drip"
        t0 = time.monotonic()
        with mock.patch.object(client_http, "REMEMBER_TIMEOUT", 0.5):
            self.assertFalse(self.client.remember("hello"))
        self.assertLess(time.monotonic() - t0, 0.5 + 0.5)

    def test_remember_past_deadline_sends_nothing(self):
        out = self.client.remember_status("hello", deadline=time.monotonic() - 1)
        self.assertEqual(out, "failed")
        self.assertEqual(self.gw.requests, [])

    def test_remember_deadline_passing_during_connect_sends_nothing(self):
        real = client_http.http.client.HTTPConnection.connect

        def slow_connect(conn):
            real(conn)
            time.sleep(0.4)  # stands in for a delayed DNS lookup

        with mock.patch.object(client_http.http.client.HTTPConnection, "connect", slow_connect):
            out = self.client.remember_status("hello", deadline=time.monotonic() + 0.2)
        self.assertEqual(out, "failed")
        self.assertEqual(self.gw.requests, [])

    def test_remember_empty_is_false_without_request(self):
        self.assertFalse(self.client.remember("   "))
        self.assertEqual(self.gw.requests, [])

    def test_remember_failure_returns_false(self):
        for mode in ("500", "401", "429"):
            with self.subTest(mode=mode):
                self.gw.mode = mode
                self.assertFalse(self.client.remember("hello"))

    def test_remember_timeout_returns_false(self):
        self.gw.mode = "slow"
        with mock.patch.object(client_http, "REMEMBER_TIMEOUT", 0.3):
            self.assertFalse(self.client.remember("hello"))


class SpeakerAndKeyTests(ClientTestCase):
    """The speaker on a recall and the message keys the gateway turns into record fields."""

    def test_the_speaker_is_sent_on_recall_detail_recall_and_explain_when_given(self):
        self.client.recall_detail("hello", speaker="dsh:local")
        self.client.recall("hello", speaker="aiocqhttp:10001")
        self.client.explain("hello", speaker="dsh:local")
        self.assertEqual([r["body"].get("speaker") for r in self.gw.requests],
                         ["dsh:local", "aiocqhttp:10001", "dsh:local"])
        self.assertEqual(self.gw.requests[0]["body"], {"query": "hello", "limit": 5, "speaker": "dsh:local"})

    def test_without_a_speaker_the_bodies_are_what_they_were(self):
        self.client.recall_detail("hello", context={"chat": "private"})
        self.client.explain("hello")
        self.assertEqual(self.gw.requests[0]["body"], {"query": "hello", "limit": 5, "context": {"chat": "private"}})
        self.assertEqual(self.gw.requests[1]["body"], {"query": "hello", "limit": 5})

    def test_a_speaker_the_gateway_cannot_take_is_not_sent(self):
        for bad in ("", "s" * 65, 5, ["dsh:local"]):
            self.assertIsNone(self.client.recall_detail("hello", speaker=bad))
            self.assertEqual(self.client.recall("hello", speaker=bad), [])
            self.assertIsNone(self.client.explain("hello", speaker=bad))
        self.assertEqual(self.gw.requests, [])

    def test_remember_sends_speaker_origin_kind_and_event_date_on_its_one_message(self):
        self.assertEqual(self.client.remember_status("likes oolong", speaker="dsh:local", origin="tool",
                                                     kind="preference", event_date="2026-11-17"), "saved")
        self.assertTrue(self.client.remember("likes oolong", speaker="dsh:local", origin="tool"))
        self.assertEqual(self.gw.requests[0]["body"], {"messages": [
            {"role": "user", "content": "likes oolong", "speaker": "dsh:local", "origin": "tool",
             "kind": "preference", "event_date": "2026-11-17"}]})
        self.assertEqual(self.gw.requests[1]["body"]["messages"][0],
                         {"role": "user", "content": "likes oolong", "speaker": "dsh:local", "origin": "tool"})

    def test_remember_messages_passes_every_key_the_gateway_knows(self):
        message = {"role": "user", "content": "hi", "time": "2026-11-14 12:00", "speaker": "dsh:local",
                   "about": "aiocqhttp:10001", "channel": "qq.group", "origin": "said", "kind": "event",
                   "event_date": "2026-11-14", "audience": "group", "extra": "dropped", "kind_none": None}
        self.assertEqual(self.client.remember_messages([message]), "saved")
        sent = self.gw.requests[0]["body"]["messages"][0]
        self.assertEqual(sent, {k: v for k, v in message.items() if k not in ("extra", "kind_none")})
        self.gw.requests.clear()
        nulls = {"role": "user", "content": "hi", "channel": None, "origin": None, "kind": None,
                 "event_date": None, "audience": None, "about": None}
        self.assertEqual(self.client.remember_messages([nulls]), "saved")
        self.assertEqual(self.gw.requests[0]["body"], {"messages": [{"role": "user", "content": "hi"}]})

    def test_a_key_that_breaks_the_rule_fails_the_call_and_sends_nothing(self):
        for bad in ({"channel": "Bad Channel"}, {"channel": "c" * 65}, {"origin": "guessed"}, {"origin": 5},
                    {"kind": "mood"}, {"audience": "everyone"}, {"event_date": "2026-02-30"},
                    {"event_date": "2026-11-17T08:00"}, {"about": "a" * 65}, {"about": 5}):
            self.assertEqual(self.client.remember_messages([dict({"role": "user", "content": "hi"}, **bad)]),
                             "failed", repr(bad))
        self.assertEqual(self.client.remember_status("hi", origin="guessed"), "failed")
        self.assertEqual(self.client.remember_status("hi", kind="mood"), "failed")
        self.assertEqual(self.client.remember_status("hi", event_date="yesterday"), "failed")
        self.assertEqual(self.client.remember_status("hi", speaker="s" * 65), "failed")
        self.assertEqual(self.gw.requests, [])

    def test_the_client_and_the_gateway_agree_on_every_message(self):
        """The client checks with the gateway's own rule (one definition); this keeps it that way."""
        from feiyu_memgate.gateway.validate import _valid_messages
        base = {"role": "user", "content": "hi"}
        extras = [{}, {"time": "2026-11-14"}, {"time": "t" * 41}, {"speaker": "dsh:local"}, {"speaker": "s" * 65},
                  {"speaker": 5}, {"about": "aiocqhttp:10001"}, {"about": "a" * 65}, {"channel": "qq.group:1"},
                  {"channel": "QQ"}, {"channel": ""}, {"origin": "curated"}, {"origin": "other"}, {"kind": "plan"},
                  {"kind": "other"}, {"event_date": "2026-11-14"}, {"event_date": "2026-13-01"},
                  {"event_date": "2026-1-1"}, {"audience": "public"}, {"audience": "nobody"},
                  {"channel": None, "origin": None}, {"role": "system"}, {"content": " "}]
        for extra in extras:
            message = dict(base, **extra)
            self.assertEqual(client_http._clean_messages([message]) is None, _valid_messages([message]) is None,
                             repr(extra))
            self.assertEqual(client_http._clean_messages([message]), _valid_messages([message]), repr(extra))


class ReportUsedTests(ClientTestCase):
    ITEMS = [{"id": "a", "source": "private", "text": "never sent", "score": 0.9}, {"id": "b", "source": "private"}]

    def test_it_sends_source_and_id_only_and_returns_true(self):
        self.assertTrue(self.client.report_used(self.ITEMS))
        req = self.gw.requests[0]
        self.assertEqual(req["path"], "/v1/used")
        self.assertEqual(req["auth"], "Bearer " + TOKEN)
        self.assertEqual(req["body"], {"used": [{"source": "private", "id": "a"}, {"source": "private", "id": "b"}]})

    def test_a_tenant_call_uses_the_tenant_token_and_names_the_tenant(self):
        self.assertTrue(self.client.report_used([{"source": "group", "id": "n1"}], tenant_id="10001"))
        req = self.gw.requests[0]
        self.assertEqual(req["auth"], "Bearer " + TENANT_TOKEN)
        self.assertEqual(req["body"]["tenant_id"], "10001")

    def test_a_bad_list_is_not_sent(self):
        bad = (None, "a", [], [{"source": "private"}], [{"id": "a"}], ["a"], [{"source": "", "id": "a"}],
               [{"source": "private", "id": 5}], [{"source": "private", "id": "a"}] * 21)
        for items in bad:
            with self.subTest(items=items):
                self.assertFalse(self.client.report_used(items))
        self.assertEqual(self.gw.requests, [])
        self.assertTrue(self.client.report_used([{"source": "private", "id": "a"}] * 20))

    def test_every_failure_is_false_and_never_raises(self):
        for mode in ("401", "403", "429", "500", "503", "garbage", "redirect"):
            with self.subTest(mode=mode):
                self.gw.mode = mode
                self.assertFalse(self.client.report_used(self.ITEMS))
        self.gw.mode = "ok"
        self.gw.body = {"status": "skipped"}
        self.assertFalse(self.client.report_used(self.ITEMS))
        self.gw.mode = "slow"
        self.assertFalse(self.client.report_used(self.ITEMS, timeout=0.2))

    def test_an_unconfigured_client_and_a_missing_tenant_token_are_false(self):
        self.assertFalse(MemoryClient("", "").report_used(self.ITEMS))
        self.assertFalse(MemoryClient(self.gw.url, TOKEN).report_used(self.ITEMS, tenant_id="10001"))
        self.assertEqual(self.gw.requests, [])

    def test_logs_hold_no_ids_or_tokens(self):
        self.gw.mode = "403"
        with self.assertLogs("feiyu_memgate.client", level="WARNING") as logs:
            self.assertFalse(self.client.report_used([{"source": "private", "id": "secret-id"}]))
        text = "\n".join(logs.output)
        self.assertNotIn("secret-id", text)
        self.assertNotIn(TOKEN, text)


class LoggingTests(ClientTestCase):
    def test_warnings_do_not_leak_secrets(self):
        self.gw.mode = "500"
        secret_query = "my-secret-query-text"
        with self.assertLogs("feiyu_memgate.client", level="WARNING") as cm:
            self.client.recall(secret_query)
            self.client.remember("my-secret-memory-text")
            MemoryClient("http://127.0.0.1:1", TOKEN).recall(secret_query)
        text = "\n".join(cm.output)
        for s in (secret_query, "my-secret-memory-text", TOKEN):
            self.assertNotIn(s, text)

    def test_custom_logger_gets_the_warnings(self):
        log = mock.Mock()
        self.gw.mode = "500"
        MemoryClient(self.gw.url, TOKEN, logger=log).recall("hello")
        log.warning.assert_called()

    def test_never_raises_on_odd_token(self):
        client = MemoryClient(self.gw.url, "tok-中文")
        self.assertEqual(client.recall("hello"), [])
        self.assertFalse(client.remember("hello"))


# ---- the real gateway, in this process ----

OWNER_TOKEN = "tok-owner-fake-" + "o" * 20
CONFIG = {
    "stores": {"private": {"type": "inmemory"}},
    "clients": {"owner": {"token_env": "T_OWNER", "recall": ["private"], "write": "private", "daily_writes": 1}},
}


class RealGatewayTests(unittest.TestCase):
    def setUp(self):
        gw = config.build_gateway(CONFIG, env={"T_OWNER": OWNER_TOKEN}, log=lambda line: None)
        self.server = gateway_server.make_server(gw, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.client = MemoryClient("http://127.0.0.1:%d" % self.server.server_address[1], OWNER_TOKEN)

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def test_remember_recall_and_format_a_block(self):
        self.assertEqual(self.client.remember_status("The user drinks oolong tea every morning"), "saved")
        detail = self.client.recall_detail("what tea does the user drink in the morning")
        self.assertIsNotNone(detail)
        texts = [r["text"] for r in detail["results"]]
        self.assertIn("The user drinks oolong tea every morning", texts)
        self.assertEqual(detail["results"][0]["source"], "private")
        block = format_block(detail["results"], detail["more"], labels={"private": "from your chats"}, lang="en")
        self.assertTrue(block.startswith("<memory_data>") and block.endswith("</memory_data>"))
        self.assertIn("(from your chats) The user drinks oolong tea every morning", block)

    def test_wrong_token_is_rejected(self):
        client = MemoryClient(self.client.base_url, "tok-wrong-" + "x" * 30)
        self.assertEqual(client.remember_status("hello there"), "failed")
        self.assertIsNone(client.recall_detail("hello there"))

    def test_tenant_id_to_a_gateway_without_tenants_is_refused(self):
        client = MemoryClient(self.client.base_url, OWNER_TOKEN, tenant_token=OWNER_TOKEN)
        self.assertEqual(client.remember_status("hello there", tenant_id="g1"), "failed")

    def test_daily_limit_is_reported_as_limited(self):
        self.assertEqual(self.client.remember_status("first memory of the day"), "saved")
        self.assertEqual(self.client.remember_status("second memory of the day"), "limited")

    def test_eleven_thousand_chinese_characters_fit_the_body_cap(self):
        # Within every limit; as \uXXXX escapes the body was over 64 KiB and came back 413.
        messages = [{"role": "user", "content": "记" * 3700}] * 3
        self.assertEqual(self.client.remember_messages(messages), "saved")

    def test_report_used_to_a_gateway_without_forgetting_is_still_true(self):
        self.assertTrue(self.client.report_used([{"source": "private", "id": "anything"}]))

    def test_the_real_gateway_reports_busy_as_failed(self):
        with mock.patch.object(self.server.gw.pool, "submit", return_value=None):
            self.assertEqual(self.client.remember_status("nothing reaches the store"), "failed")


if __name__ == "__main__":
    unittest.main()
