import calendar
import contextlib
import http.client
import io
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import unquote

from feiyu_memgate import registry
from feiyu_memgate.backends.openviking import OpenVikingBackend as _OpenVikingBackend
from feiyu_memgate.core import redact
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.recall import RecallContext, recall as run_recall
from feiyu_memgate.core.write import DailyCapGuard
from feiyu_memgate.gateway import app as gateway_app
from feiyu_memgate.gateway import server as gateway_server
from feiyu_memgate.gateway.app import Gateway
from feiyu_memgate.model import ClientPolicy

class OpenVikingBackend(_OpenVikingBackend):
    """The real backend, with the thresholds these fixtures' scores were written for (min_score 0.5, ratio 0.6);
    the backend's own defaults are chosen on bench/dev and are pinned in test_backend_openviking_manage."""
    default_min_score = 0.5
    default_relative_ratio = 0.6
    broad_min_score = None  # no broad lines: the default lines above apply under every profile
    broad_relative_ratio = None

CHAN_KEY = "chan-key-fake"
OTHER_KEY = "other-key-fake"
TOKEN_OWNER = "tok-owner-fake-".ljust(40, "0")
TOKEN_LIVE = "tok-live-fake-".ljust(40, "0")
TOKEN_PUB = "tok-pub-fake-".ljust(40, "0")
TOKEN_BOTH = "tok-both-fake-".ljust(40, "0")


def mem(uri, score, abstract):
    return {"uri": uri, "score": score, "abstract": abstract}


class FakeOV:
    """Fake OpenViking on 127.0.0.1, telling accounts apart by Authorization."""

    def __init__(self):
        self.memories = {CHAN_KEY: [], OTHER_KEY: []}
        self.contents = {}
        self.mode = "ok"  # ok | error | nonjson | nofields
        self.calls = []  # (key, method, path)
        self.bodies = []
        self.session_status = {"commit_count": 0}
        self.delay = {}  # key -> seconds to sleep before answering
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _handle(self, method):
                key = (self.headers.get("Authorization") or "").replace("Bearer ", "")
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                owner.calls.append((key, method, self.path))
                if self.path == "/health":
                    return self._send(200, b'{"status": "ok"}')
                if method == "GET" and self.path.startswith("/api/v1/sessions/"):
                    return self._send(200, json.dumps({"result": owner.session_status}).encode())
                if body:
                    owner.bodies.append(json.loads(body))
                if owner.delay.get(key):
                    time.sleep(owner.delay[key])
                if owner.mode == "error":
                    return self._send(500, b'{"error":"upstream-secret-detail"}')
                if owner.mode == "nonjson":
                    return self._send(200, b"<html>not json</html>")
                if owner.mode == "nofields":
                    return self._send(200, b'{"result": {"memories": [{"foo": 1}, 7, {"uri": "x"}]}}')
                if self.path.startswith("/api/v1/search/find"):
                    res = {"result": {"memories": owner.memories.get(key, [])}}
                elif self.path.startswith("/api/v1/content/read"):
                    uri = unquote(self.path.split("uri=", 1)[1])
                    res = {"result": owner.contents.get(uri, "")}
                else:
                    res = {"result": {"ok": True}}
                self._send(200, json.dumps(res).encode())

            def _send(self, code, data):
                try:
                    self.send_response(code)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self._stopped = False

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def calls_with(self, key):
        return [c for c in self.calls if c[0] == key]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class GatewayTestCase(unittest.TestCase):
    def setUp(self):
        self.ov = FakeOV()
        self.addCleanup(self.ov.stop)
        dead = FakeOV()
        dead_url = dead.url
        dead.stop()  # a store whose port refuses connections
        self.clock = Clock()
        self.wall = Clock()
        self.wall.t = calendar.timegm((2026, 11, 12, 4, 0, 0))  # noon in UTC+8
        self.logs = []
        stores = {
            "channel": OpenVikingBackend(self.ov.url, CHAN_KEY, writable=True, floor=0, ceil=1),
            "other": OpenVikingBackend(self.ov.url, OTHER_KEY, floor=0, ceil=1),
            "dead": OpenVikingBackend(dead_url, "dead-key-fake", floor=0, ceil=1),
        }
        clients = {
            "owner": ClientPolicy("owner", recall=("channel",), write="channel", daily_writes=50),
            "live": ClientPolicy("live", recall=(), public=True),
            "pub": ClientPolicy("pub", recall=("channel",), public=True),
            "both": ClientPolicy("both", recall=("channel", "dead")),
        }
        tokens = {"owner": TOKEN_OWNER, "live": TOKEN_LIVE, "pub": TOKEN_PUB, "both": TOKEN_BOTH}
        self.cap_guard = DailyCapGuard(utc_offset_hours=8, wall=self.wall)
        guards = [registry.create("write_guard", "redact"), self.cap_guard]
        self.gw = Gateway(stores, clients, tokens, guards=guards, clock=self.clock, log=self.logs.append)
        self.addCleanup(self.gw.close)
        self.server = gateway_server.make_server(self.gw, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.port = self.server.server_address[1]

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(self, method, path, body=None, token=TOKEN_OWNER, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            h = dict(headers or {})
            if token is not None:
                h["Authorization"] = "Bearer " + token
            data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
            conn.request(method, path, body=data, headers=h)
            resp = conn.getresponse()
            payload = resp.read()
            try:
                parsed = json.loads(payload)
            except ValueError:
                parsed = None
            return resp.status, parsed
        finally:
            conn.close()

    def recall(self, query="hello", token=TOKEN_OWNER, **extra):
        return self.request("POST", "/v1/recall", dict(query=query, **extra), token=token)

    def remember_once(self, token=TOKEN_OWNER, **extra):
        body = dict(messages=[{"role": "user", "content": "hi"}], **extra)
        return self.request("POST", "/v1/remember", body, token=token)


class DailyCapTests(GatewayTestCase):
    def use_up(self, n=50):
        for _ in range(n):
            self.assertEqual(self.remember_once()[0], 200)

    def test_owner_remember_stops_after_50_a_day(self):
        self.use_up()
        self.assertEqual(self.remember_once(), (429, {"error": "daily limit", "code": "daily_limit"}))

    def test_recall_is_never_capped(self):
        self.use_up()
        self.assertEqual(self.recall()[0], 200)

    def test_caps_reset_at_local_midnight(self):
        self.wall.t = calendar.timegm((2026, 11, 12, 15, 59, 0))  # 23:59 in UTC+8
        self.use_up()
        self.assertEqual(self.remember_once()[0], 429)
        self.wall.t = calendar.timegm((2026, 11, 12, 16, 0, 1))  # 00:00:01 next day in UTC+8
        self.assertEqual(self.remember_once()[0], 200)

    def test_failed_submit_is_not_counted(self):
        self.ov.mode = "error"
        for _ in range(5):
            self.assertEqual(self.remember_once()[0], 502)
        self.assertEqual(self.cap_guard.caps._counts.get("owner", 0), 0)
        self.ov.mode = "ok"
        self.use_up()
        self.assertEqual(self.remember_once()[0], 429)

    def test_bad_request_is_not_counted(self):
        for _ in range(3):
            self.assertEqual(self.request("POST", "/v1/remember", {"messages": []})[0], 400)
        self.use_up()

    def test_daily_limit_log_has_no_content(self):
        self.use_up()
        self.remember_once()
        self.assertTrue(any(" 429 " in line and "daily-limit" in line for line in self.logs))


class RecallTests(GatewayTestCase):
    def test_recall_merges_by_score_and_labels_source(self):
        self.ov.memories[CHAN_KEY] = [
            mem("viking://~/memories/events/2026/11/03/c.md", 0.7, "low fact"),
            mem("viking://~/memories/preferences/owner/m.md", 0.9, "high fact"),
        ]
        status, body = self.recall()
        self.assertEqual(status, 200)
        self.assertEqual(
            [(r["source"], r["text"], r["score"]) for r in body["results"]],
            [("channel", "high fact", 0.9), ("channel", "low fact", 0.7)],
        )

    def test_recall_drops_low_score_and_dir_summaries(self):
        self.ov.memories[CHAN_KEY] = [
            mem("viking://~/memories/preferences/owner/low.md", 0.49, "too low"),
            mem("viking://~/memories/preferences/owner/.abstract.md", 0.9, "dir summary"),
            mem("viking://~/memories/entities/soul.md", 0.9, "soul"),
            mem("viking://~/memories/entities/identity.md", 0.9, "identity"),
            mem("viking://~/memories/preferences/owner/ok.md", 0.5, "kept"),
        ]
        status, body = self.recall()
        self.assertEqual([r["text"] for r in body["results"]], ["kept"])

    def test_recall_caps_output_at_1500_chars(self):
        self.ov.memories[CHAN_KEY] = [
            mem("viking://~/memories/preferences/owner/%d.md" % i, 0.9 - i / 100, ("w%d " % i) * 163) for i in range(5)
        ]
        status, body = self.recall(limit=10)
        total = sum(len(r["text"]) for r in body["results"])
        self.assertLessEqual(total, 1500)
        self.assertGreater(total, 1400)

    def test_recall_reads_with_the_stores_own_key_and_never_touches_another_store(self):
        self.ov.memories[OTHER_KEY] = [mem("viking://~/memories/preferences/owner/a.md", 0.99, "other fact")]
        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/events/2026/11/03/b.md", 0.8, "")]
        self.ov.contents["viking://~/memories/events/2026/11/03/b.md"] = "chan body"
        status, body = self.recall()
        self.assertEqual([(r["source"], r["text"]) for r in body["results"]], [("channel", "chan body")])
        self.assertEqual(self.ov.calls_with(OTHER_KEY), [])
        self.assertTrue(any("content/read" in c[2] for c in self.ov.calls_with(CHAN_KEY)))

    def test_a_client_only_ever_searches_the_stores_in_its_policy(self):
        self.ov.memories[OTHER_KEY] = [mem("viking://~/memories/preferences/owner/a.md", 0.99, "other fact")]
        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/preferences/owner/b.md", 0.9, "chan fact")]
        for token in (TOKEN_OWNER, TOKEN_PUB, TOKEN_BOTH):
            status, body = self.recall(token=token)
            self.assertEqual(status, 200)
            self.assertNotIn("other fact", json.dumps(body))
        self.assertEqual(self.ov.calls_with(OTHER_KEY), [])
        # a request cannot name a store: extra fields are ignored
        status, body = self.recall(stores=["other"], source="other")
        self.assertEqual([r["source"] for r in body["results"]], ["channel"])
        self.assertEqual(self.ov.calls_with(OTHER_KEY), [])

    def test_recall_sends_target_uri_and_clamps_limit(self):
        self.recall(limit=99)
        find = [b for b in self.ov.bodies if "target_uri" in b]
        self.assertTrue(find)
        self.assertTrue(all(b["target_uri"] == "viking://~/memories" for b in find))
        self.assertTrue(all(b["limit"] <= 30 for b in find))

    def test_recall_bad_inputs_are_400(self):
        for body in ({"query": 5}, {"query": ""}, {"query": "x", "limit": "3"}, {"query": "x", "limit": True}, {}):
            self.assertEqual(self.request("POST", "/v1/recall", body)[0], 400, body)
        self.assertEqual(self.request("POST", "/v1/recall", raw=b"{not json")[0], 400)
        self.assertEqual(self.request("POST", "/v1/recall", raw=b"[1,2]")[0], 400)
        self.assertEqual(self.request("POST", "/v1/recall", raw=b"\xff\xfe")[0], 400)

    def test_secret_shaped_text_is_redacted(self):
        self.ov.memories[CHAN_KEY] = [
            mem(
                "viking://~/memories/preferences/owner/s.md",
                0.9,
                "key sk-abcdefghijklmnop1234 jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0NTY.sig_abc-123 "
                "token=abcdef123456 end",
            )
        ]
        status, body = self.recall()
        text = body["results"][0]["text"]
        self.assertNotIn("sk-abcdef", text)
        self.assertNotIn("eyJhbGci", text)
        self.assertNotIn("abcdef123456", text)
        self.assertIn("token=[REDACTED]", text)


class RecallTidyTests(GatewayTestCase):
    def test_reply_has_results_more_failed_and_busy(self):
        status, body = self.recall()
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"results", "more", "failed", "busy"})

    def test_every_result_carries_the_memory_id(self):
        uri = "viking://~/memories/preferences/owner/a.md"
        self.ov.memories[CHAN_KEY] = [mem(uri, 0.9, "fact")]
        _, body = self.recall()
        self.assertEqual(body["results"][0]["id"], uri)

    def test_near_duplicates_keep_the_higher_score(self):
        self.ov.memories[CHAN_KEY] = [
            mem("viking://~/memories/preferences/owner/a.md", 0.8, "用户喜欢喝乌龙茶"),
            mem("viking://~/memories/preferences/owner/b.md", 0.9, "用户喜欢喝乌龙茶。"),
        ]
        _, body = self.recall()
        self.assertEqual([r["score"] for r in body["results"]], [0.9])
        self.assertEqual(body["more"], 0)

    def test_event_results_carry_a_date(self):
        self.ov.memories[CHAN_KEY] = [
            mem("viking://~/memories/events/2026/11/03/a.md", 0.9, "去了公园"),
            mem("viking://~/memories/preferences/owner/b.md", 0.8, "喜欢吃苹果"),
        ]
        _, body = self.recall()
        by_text = {r["text"]: r for r in body["results"]}
        self.assertEqual(by_text["去了公园"]["date"], "2026-11-03")
        self.assertNotIn("date", by_text["喜欢吃苹果"])

    def test_item_that_does_not_fit_is_left_out_whole(self):
        self.ov.memories[CHAN_KEY] = [
            mem("viking://~/memories/preferences/owner/a.md", 0.9, "甲" * 1000),
            mem("viking://~/memories/preferences/owner/b.md", 0.8, "乙" * 800),
        ]
        _, body = self.recall()
        self.assertEqual([len(r["text"]) for r in body["results"]], [1000])
        self.assertEqual(body["more"], 1)

    def test_a_smaller_item_after_one_that_does_not_fit_still_goes_in(self):
        self.ov.memories[CHAN_KEY] = [
            mem("viking://~/memories/preferences/owner/a.md", 0.9, "甲" * 1000),
            mem("viking://~/memories/preferences/owner/b.md", 0.8, "乙" * 800),
            mem("viking://~/memories/preferences/owner/c.md", 0.7, "丙" * 100),
        ]
        _, body = self.recall()
        self.assertEqual([len(r["text"]) for r in body["results"]], [1000, 100])
        self.assertEqual(body["more"], 1)

    def test_more_counts_what_the_limit_left_out(self):
        self.ov.memories[CHAN_KEY] = [
            mem("viking://~/memories/preferences/owner/%d.md" % i, 0.9 - i / 100, "第%s件事" % "零一二三四五六七"[i] * 3)
            for i in range(8)
        ]
        _, body = self.recall(limit=5)
        self.assertEqual(len(body["results"]), 5)
        self.assertEqual(body["more"], 3)

    def test_low_scores_are_not_counted_in_more(self):
        self.ov.memories[CHAN_KEY] = [
            mem("viking://~/memories/preferences/owner/a.md", 0.9, "高分的事情"),
            mem("viking://~/memories/preferences/owner/b.md", 0.2, "低分的别的事"),
        ]
        _, body = self.recall(limit=1)
        self.assertEqual(body["more"], 0)

    def with_drop_marker(self):
        # drop_marker is not a default stage: a deployment that writes probes adds it after fill_text.
        names = ("min_score", "fill_text", "drop_marker", "drop_oversize", "redact", "dedupe", "budget")
        self.gw.stages = [registry.create("recall_stage", name) for name in names]

    def test_probe_marker_items_are_dropped(self):
        self.with_drop_marker()
        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/events/2026/11/03/a.md", 0.95, "测试编号是 FEIYU-PROBE-ABC123")]
        _, body = self.recall()
        self.assertEqual(body["results"], [])
        self.assertEqual(body["more"], 0)

    def test_probe_marker_found_only_after_a_read_is_dropped(self):
        self.with_drop_marker()
        uri = "viking://~/memories/events/2026/11/03/a.md"
        self.ov.memories[CHAN_KEY] = [mem(uri, 0.95, "")]
        self.ov.contents[uri] = "FEIYU-PROBE-ZZ9 round trip"
        _, body = self.recall()
        self.assertEqual(body["results"], [])


class LiveTests(GatewayTestCase):
    def test_live_reads_no_account(self):
        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/preferences/owner/a.md", 0.9, "x")]
        status, body = self.recall(token=TOKEN_LIVE)
        self.assertEqual(status, 200)
        self.assertEqual(body["results"], [])
        self.assertEqual([c for c in self.ov.calls if "/search/find" in c[2]], [])

    def test_policies_decide_which_stores_a_client_may_search(self):
        self.assertEqual(self.gw.clients["owner"].recall, ("channel",))
        self.assertEqual(self.gw.clients["live"].recall, ())
        self.assertEqual(self.gw.clients["both"].recall, ("channel", "dead"))

    def test_a_client_that_is_not_configured_has_no_token(self):
        self.assertIsNone(self.gw.authenticate("tok-nobody-fake-".ljust(40, "0")))
        self.assertEqual(self.gw.authenticate(TOKEN_OWNER), "owner")

    def test_owner_output_keeps_phone_numbers(self):
        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/events/2026/11/03/a.md", 0.9, "call 13812345678")]
        status, body = self.recall()
        self.assertIn("13812345678", body["results"][0]["text"])

    def test_public_client_output_hides_phone_numbers(self):
        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/events/2026/11/03/a.md", 0.9, "call 13812345678")]
        status, body = self.recall(token=TOKEN_PUB)
        self.assertEqual(status, 200)
        self.assertNotIn("13812345678", body["results"][0]["text"])
        self.assertIn("[REDACTED]", body["results"][0]["text"])

    def test_live_token_cannot_remember(self):
        status, _ = self.request(
            "POST", "/v1/remember", {"messages": [{"role": "user", "content": "hi"}]}, token=TOKEN_LIVE
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.ov.calls, [])

    def test_client_without_write_policy_cannot_remember(self):
        status, _ = self.request(
            "POST", "/v1/remember", {"messages": [{"role": "user", "content": "hi"}]}, token=TOKEN_PUB
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.ov.calls, [])


class RememberTests(GatewayTestCase):
    def test_remember_only_touches_the_write_store(self):
        status, body = self.request(
            "POST",
            "/v1/remember",
            {"messages": [{"role": "user", "content": "I like tea"}, {"role": "assistant", "content": "ok"}]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.ov.calls_with(OTHER_KEY), [])
        paths = [c[2] for c in self.ov.calls_with(CHAN_KEY)]
        self.assertEqual(len(paths), 4)
        self.assertEqual(paths[0], "/api/v1/sessions")
        self.assertEqual(body, {"status": "ok", "receipt": paths[1].split("/")[4]})
        self.assertTrue(paths[1].endswith("/messages") and paths[2].endswith("/messages"))
        self.assertTrue(paths[3].endswith("/commit"))
        self.assertTrue(paths[1].split("/")[4].startswith("gw-"))

    def test_remember_redacts_secrets_before_sending(self):
        self.request("POST", "/v1/remember", {"messages": [{"role": "user", "content": "my token=abcdef123456"}]})
        self.assertFalse(any("abcdef123456" in json.dumps(b) for b in self.ov.bodies))

    def test_remember_rejects_bad_messages(self):
        bad = (
            {},
            {"messages": []},
            {"messages": "x"},
            {"messages": [1]},
            {"messages": [{"role": "system", "content": "x"}]},
            {"messages": [{"role": "user", "content": 3}]},
        )
        for body in bad:
            self.assertEqual(self.request("POST", "/v1/remember", body)[0], 400, body)
        self.assertEqual(self.ov.calls, [])

    def test_read_only_store_cannot_remember(self):
        other = self.gw.stores["other"]
        with self.assertRaises(PermissionError):
            other.remember([{"role": "user", "content": "x"}])
        self.assertEqual(self.ov.calls, [])

    def test_remember_failure_is_502_without_upstream_text(self):
        self.ov.mode = "error"
        status, body = self.request("POST", "/v1/remember", {"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 502)
        self.assertNotIn("upstream-secret-detail", json.dumps(body))


class HttpTests(GatewayTestCase):
    def test_healthz_needs_no_token(self):
        status, body = self.request("GET", "/healthz", token=None)
        self.assertEqual(status, 200)

    def test_unknown_token_is_401(self):
        self.assertEqual(self.recall(token="nope")[0], 401)
        self.assertEqual(self.recall(token=None)[0], 401)
        self.assertEqual(self.recall(token="")[0], 401)
        self.assertEqual(self.request("POST", "/v1/recall", {"query": "x"}, token=None,
                                      headers={"Authorization": "Bearer tökén".encode("latin-1").decode("latin-1")})[0], 401)

    def test_empty_configured_token_never_matches_empty_presented(self):
        clients = {"owner": ClientPolicy("owner", recall=()), "live": ClientPolicy("live", recall=())}
        gw = Gateway({}, clients, {"owner": "", "live": TOKEN_LIVE}, clock=self.clock, log=self.logs.append)
        self.addCleanup(gw.close)
        self.assertIsNone(gw.authenticate(""))
        self.assertIsNone(gw.authenticate(None))
        self.assertIsNone(gw.authenticate("tökén"))
        self.assertEqual(gw.authenticate(TOKEN_LIVE), "live")

    def test_unknown_path_is_404(self):
        self.assertEqual(self.request("POST", "/v1/forget", {})[0], 404)
        self.assertEqual(self.request("GET", "/api/v1/fs/ls")[0], 404)
        self.assertEqual(self.request("POST", "/v1/forget", {}, token=None)[0], 404)

    def test_removed_routes_are_404(self):
        self.assertEqual(self.request("POST", "/v1/tasks", {})[0], 404)
        self.assertEqual(self.request("POST", "/v1/bridge", {})[0], 404)

    def test_wrong_method_is_405(self):
        self.assertEqual(self.request("GET", "/v1/recall")[0], 405)

    def test_body_over_64kb_is_413(self):
        big = json.dumps({"query": "x" * 70000}).encode()
        self.assertEqual(self.request("POST", "/v1/recall", raw=big)[0], 413)
        self.assertEqual(self.ov.calls, [])

    def test_rate_limit_is_429_after_60_per_minute(self):
        for _ in range(60):
            self.assertEqual(self.recall()[0], 200)
        self.assertEqual(self.recall()[0], 429)
        # per token: the other client is unaffected
        self.assertEqual(self.recall(token=TOKEN_LIVE)[0], 200)
        self.clock.t += 61
        self.assertEqual(self.recall()[0], 200)


class StoreFailureTests(GatewayTestCase):
    UNAVAILABLE = (503, {"error": "unavailable", "code": "unavailable"})

    def test_ov_down_is_503_not_an_empty_recall(self):
        self.ov.mode = "error"
        self.assertEqual(self.recall(), self.UNAVAILABLE)

    def test_ov_unreachable_is_503(self):
        self.ov.stop()
        self.assertEqual(self.recall(), self.UNAVAILABLE)

    def test_ov_non_json_is_503(self):
        self.ov.mode = "nonjson"
        self.assertEqual(self.recall(), self.UNAVAILABLE)

    def test_answer_with_only_malformed_items_is_503(self):
        self.ov.mode = "nofields"
        self.assertEqual(self.recall(), self.UNAVAILABLE)

    def test_malformed_items_among_valid_ones_are_skipped(self):
        uri = "viking://~/memories/preferences/owner/a.md"
        self.ov.memories[CHAN_KEY] = [{"foo": 1}, 7, {"uri": "x"}, mem(uri, 0.9, "有用的事")]
        status, body = self.recall()
        self.assertEqual(status, 200)
        self.assertEqual([r["text"] for r in body["results"]], ["有用的事"])

    def test_an_unexpected_error_is_a_500_logged_by_type(self):
        with mock.patch.object(gateway_app, "recall", side_effect=RuntimeError("boom QUERYTEXT")):
            self.assertEqual(self.recall(), (500, {"error": "internal error", "code": "internal"}))
        self.assertIn("piece-error client=owner where=recall error=RuntimeError", self.logs)
        self.assertNotIn("QUERYTEXT", " ".join(self.logs))

    def test_a_stage_that_raises_is_503_not_a_crash(self):
        class Broken:
            def prepare(self, candidates, ctx):
                raise RuntimeError("boom")

            def admit(self, candidate, ctx):
                return "keep"

        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/preferences/owner/a.md", 0.9, "fact")]
        self.gw.stages = [Broken()]
        self.assertEqual(self.recall(), (503, {"error": "stage error", "code": "stage_error"}))
        self.assertEqual(self.gw.recall_failures.count(), 1)
        self.assertIn("piece-error client=owner stage-error:Broken where=prepare error=RuntimeError", self.logs)
        end = time.monotonic() + 3  # the access line is written just after the answer is sent
        while not any("/v1/recall 503" in line for line in self.logs) and time.monotonic() < end:
            time.sleep(0.01)
        self.assertTrue(any("/v1/recall 503" in line and "stage-error:Broken" in line for line in self.logs))

    def test_empty_lookup_is_200_with_no_results(self):
        status, body = self.recall()
        self.assertEqual((status, body), (200, {"results": [], "more": 0, "failed": 0, "busy": 0}))

    def test_live_with_no_account_is_200_not_503(self):
        self.ov.mode = "error"
        status, body = self.recall(token=TOKEN_LIVE)
        self.assertEqual((status, body["failed"]), (200, 0))
        self.assertEqual(body["results"], [])

    def test_recall_partial_failure_counts_failed(self):
        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/preferences/owner/a.md", 0.9, "fact")]
        status, body = self.recall(token=TOKEN_BOTH)
        self.assertEqual(status, 200)
        self.assertEqual(body["failed"], 1)
        self.assertEqual([r["text"] for r in body["results"]], ["fact"])

    def test_engine_partial_failure_counts_failed(self):
        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/preferences/owner/a.md", 0.9, "fact")]
        pool = Pool()
        self.addCleanup(pool.close)
        stores = {"channel": self.gw.stores["channel"], "dead": self.gw.stores["dead"]}
        ctx = RecallContext("q", 5, pool=pool)
        out = run_recall(stores, gateway_app.default_stages(), ctx)
        self.assertEqual(out["failed"], 1)
        self.assertEqual([r["text"] for r in out["results"]], ["fact"])


class LogTests(GatewayTestCase):
    def test_logs_never_contain_query_or_memory_text(self):
        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/preferences/owner/p.md", 0.9, "MEMORYTEXT-zzz")]
        err = io.StringIO()
        self.gw._log = gateway_app.stderr_log
        with contextlib.redirect_stderr(err):
            self.recall(query="QUERYTEXT-yyy")
            self.request("GET", "/nothing?token=SECRETQ", token=None)
            self.request("POST", "/v1/recall?q=QUERYTEXT-yyy", {"query": "QUERYTEXT-yyy"}, token="BADTOKEN-xxx")
        out = err.getvalue()
        self.assertTrue(out)
        for needle in ("QUERYTEXT", "MEMORYTEXT", "SECRETQ", CHAN_KEY, OTHER_KEY, TOKEN_OWNER, "BADTOKEN"):
            self.assertNotIn(needle, out)
        self.assertIn("/v1/recall", out)

    def test_injected_log_lines_have_no_content(self):
        self.recall(query="QUERYTEXT-yyy")
        # The server writes the request line after it has sent the reply, so the line can arrive a moment
        # after the client has its answer.
        deadline = time.monotonic() + 2.0
        while not self.logs and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.logs)
        self.assertNotIn("QUERYTEXT", " ".join(self.logs))
        self.assertIn("client=owner", " ".join(self.logs))
        self.assertNotIn("scope=", " ".join(self.logs))


class RedactTests(unittest.TestCase):
    def test_five_rules(self):
        cases = [
            "sk-abcdefghijklmnop12",
            "ghp_abcdefghijklmnopqrst12",
            "AKIAABCDEFGHIJKLMNOP",
            "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.abc",
            "API_KEY: abcdef123",
            "密码：abcdef123",
        ]
        for c in cases:
            self.assertIn("[REDACTED]", redact.redact(c), c)
        self.assertEqual(redact.redact("token=abcdef123 x"), "token=[REDACTED] x")
        self.assertEqual(redact.redact("token=abc"), "token=abc")

    def test_json_and_quoted_secret_values_are_redacted(self):
        cases = [
            ('{"token":"syntheticABC123456"}', "syntheticABC"),
            ('{"api_key": "syntheticABC123456"}', "syntheticABC"),
            ('{"apiKey":"syntheticABC123456"}', "syntheticABC"),
            ('"access_token" : "abc123xyz789"', "abc123xyz789"),
            ("{'password': 'correct horse battery'}", "horse"),
            ('secret_key = "abc def ghi"', "def"),
            ('client_secret="s3cr3t-value"', "s3cr3t"),
            ('{\\"token\\":\\"syntheticABC123456\\"}', "syntheticABC"),        # JSON inside a JSON string
            ('{"private_key": "-----BEGIN KEY-----\\nMIIsynthetic\\n"}', "MIIsynthetic"),
            ('"token": "abc"', '"abc"'),                                        # quoted: any length
            ("token = 12345678", "12345678"),
            ('token="syntheticABC123456', "syntheticABC"),                      # unterminated quote
            ("Authorization: Bearer syntheticABC123456789", "syntheticABC"),
            ('{"Authorization": "Bearer syntheticABC123456789"}', "syntheticABC"),
        ]
        for text, fragment in cases:
            out = redact.redact(text)
            self.assertNotIn(fragment, out, text)
            self.assertIn("[REDACTED]", out, text)
        self.assertEqual(redact.redact('{"token":"syntheticABC123456"}'), '{"token":"[REDACTED]"}')
        self.assertEqual(redact.redact("{'password': 'a b c'}"), "{'password': '[REDACTED]'}")

    def test_escaped_quotes_inside_a_quoted_secret_do_not_end_it(self):
        self.assertEqual(redact.redact('{"password":"ab\\"syntheticABC123456"}'), '{"password":"[REDACTED]"}')
        self.assertEqual(redact.redact('{"token": "a\\\\"} tail'), '{"token": "[REDACTED]"} tail')
        nested = '{\\"password\\":\\"ab\\\\\\"syntheticABC123456\\"}'          # the same JSON inside a JSON string
        self.assertNotIn("synthetic", redact.redact(nested))
        self.assertIn("[REDACTED]", redact.redact(nested))

    def test_bare_values_stop_at_punctuation_and_json_literals_stay(self):
        for text in ('{"client_secret":false,"count":12}', '{"token":null}', '{"api_key":true, "x": 1}',
                     '{"password": false}', "secret=true;"):
            self.assertEqual(redact.redact(text), text)
        self.assertEqual(json.loads(redact.redact('{"client_secret":false,"count":12}')),
                         {"client_secret": False, "count": 12})
        self.assertEqual(redact.redact('{"token":12345678,"count":12}'), '{"token":[REDACTED],"count":12}')
        self.assertEqual(redact.redact("https://x.example/cb?access_token=abcdef123456&state=xyz"),
                         "https://x.example/cb?access_token=[REDACTED]&state=xyz")
        self.assertEqual(redact.redact("/cb?token=abc&password=syntheticABC123#frag"),
                         "/cb?token=abc&password=[REDACTED]#frag")

    def test_plain_values_run_to_the_end_of_the_word_punctuation_included(self):
        # YAML and env style: only JSON values and URL queries end at punctuation.
        for text, expected in (
                ("password: abc;syntheticABC123456", "password: [REDACTED]"),
                ("password: abcdefgh;syntheticABC123456", "password: [REDACTED]"),
                ("PASSWORD=abc;syntheticABC123456", "PASSWORD=[REDACTED]"),
                ("api_key: ab,cd}syntheticABC123456 next", "api_key: [REDACTED] next"),
                ("token=abcdef123;next", "token=[REDACTED]")):
            self.assertEqual(redact.redact(text), expected, text)
            self.assertNotIn("synthetic", redact.redact(text))

    def test_json_without_secret_values_is_left_alone(self):
        for text in ('{"type":"token","count":3}', '{"input_tokens": 1234567}', '{"tokenizer": "bpe-model-v2"}',
                     '"secret": true', '{"password": ""}', "the bearer of good news", "tokens: 12345678"):
            self.assertEqual(redact.redact(text), text)

    def test_redaction_never_adds_or_removes_a_line_break(self):
        # A caller can redact a whole event stream at once and then show only its last part.
        for text in ("token=\nsyntheticABC123456\nnext", 'a\n{"token": "x y\nz"}\nb', "sk-" + "a" * 30 + "\n\n",
                     "Bearer \nsyntheticABC123456789", "x" * 300 + "\n" + "y" * 300, "密码：\n\nabcdef123"):
            self.assertEqual(redact.redact(text).count("\n"), text.count("\n"), text[:30])
            self.assertEqual(redact.redact_public(text).count("\n"), text.count("\n"), text[:30])

    def test_a_key_and_its_value_on_separate_lines_are_still_one_secret(self):
        self.assertEqual(redact.redact("token=\nsyntheticABC123456"), "token=\n[REDACTED]")

    def test_public_over_redacts(self):
        for c in ("+86 138-1234-5678", "mail a.b+c@x.co.uk", "ID 11010119900101123X", "12 Main Street"):
            self.assertIn("[REDACTED]", redact.redact_public(c), c)
        self.assertEqual(redact.redact_public("likes green tea"), "likes green tea")

    def test_registered_redactors_are_these_functions(self):
        self.assertIs(registry.create("redactor", "secrets"), redact.redact)
        self.assertIs(registry.create("redactor", "public"), redact.redact_public)


class ColdAndHealthTests(GatewayTestCase):
    EMPTY = {"results": [], "more": 0, "failed": 0, "busy": 0}

    def capture_deadlines(self):
        seen = []

        def fake(stores, stages, ctx):
            seen.append(ctx.deadline - self.clock.t)
            return dict(self.EMPTY)

        return seen, mock.patch.object(gateway_app, "recall", side_effect=fake)

    def test_cold_deadline_first_and_after_ten_minutes(self):
        seen, patch = self.capture_deadlines()
        with patch:
            self.recall()
            self.recall()
            self.clock.t += 600
            self.recall()
        self.assertEqual(seen, [8.0, 3.0, 8.0])

    def test_deep_healthz_reports_counts_and_stores(self):
        status, body = self.request("GET", "/healthz?deep=1", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body["stores"]["channel"], "ok")
        self.assertEqual(body["stores"]["dead"], "down")
        self.assertEqual(set(body["writes_24h"]), {"saved", "empty", "missing", "untracked", "pending"})
        self.assertEqual((body["recall_failed_1h"], body["recall_busy_1h"]), (0, 0))
        self.assertNotIn("ov", body)
        self.assertNotIn("extraction_24h", body)

    def test_deep_healthz_store_down(self):
        self.ov.stop()
        # Called directly: a refused connection can take seconds to fail on some systems,
        # and three stores are checked one after the other.
        body = self.gw.health(deep=True)
        self.assertEqual(body["stores"]["channel"], "down")

    def test_failed_recall_is_counted(self):
        self.ov.mode = "error"
        self.recall()
        _, body = self.request("GET", "/healthz?deep=1", token=None)
        self.assertEqual(body["recall_failed_1h"], 1)

    def test_remember_is_tracked_for_extraction(self):
        self.request("POST", "/v1/remember", {"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(self.gw.writes.counts()["pending"], 1)

    def test_plain_healthz_unchanged(self):
        self.assertEqual(self.request("GET", "/healthz", token=None), (200, {"status": "ok"}))
        self.assertEqual(self.request("GET", "/healthz?deep=0", token=None), (200, {"status": "ok"}))

    def test_live_recall_does_not_warm_the_model(self):
        seen, patch = self.capture_deadlines()
        with patch:
            self.recall(token=TOKEN_LIVE)
            self.recall()
        self.assertEqual(seen[-1], 8.0)

    def test_partial_failure_records_exactly_one(self):
        status, _ = self.recall(token=TOKEN_BOTH)
        self.assertEqual(status, 200)
        self.assertEqual(self.gw.recall_failures.count(), 1)

    def test_clean_recall_records_none(self):
        status, _ = self.recall()
        self.assertEqual(status, 200)
        self.assertEqual(self.gw.recall_failures.count(), 0)


if __name__ == "__main__":
    unittest.main()
