"""Hardening tests: the gateway stays answerable and leaks nothing under hostile or broken peers.

Self-contained: a fake OpenViking server on 127.0.0.1, plus a raw-socket upstream that hangs or dribbles.
"""
import calendar
import http.client
import json
import re
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import unquote

from feiyu_memgate import config
from feiyu_memgate.backends.openviking import OpenVikingBackend as _OpenVikingBackend
from feiyu_memgate.core import limits, redact
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.recall import RecallContext, recall as run_recall
from feiyu_memgate.core.write import DailyCapGuard, RedactGuard
from feiyu_memgate.gateway import app as gateway
from feiyu_memgate.gateway import server as http_server
from feiyu_memgate.model import ClientPolicy

class OpenVikingBackend(_OpenVikingBackend):
    """The real backend, with the thresholds these fixtures' scores were written for (min_score 0.5, ratio 0.6);
    the backend's own defaults are chosen on bench/dev and are pinned in test_backend_openviking_manage."""
    default_min_score = 0.5
    default_relative_ratio = 0.6

CHAN_KEY = "chan-key-fake"
TOKEN_OWNER = "tok-owner-fake-" + "0" * 20
TOKEN_LIVE = "tok-live-fake-" + "0" * 20

OWNER = ClientPolicy("owner", recall=("channel",), write="channel", daily_writes=50)
LIVE = ClientPolicy("live", recall=(), public=True)


def mem(uri, score, abstract):
    return {"uri": uri, "score": score, "abstract": abstract}


class FakeOV:
    """Fake OpenViking on 127.0.0.1, telling accounts apart by Authorization."""

    def __init__(self):
        self.memories = {CHAN_KEY: []}
        self.contents = {}
        self.mode = "ok"  # ok | error
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
        self.clock = Clock()
        self.wall = Clock()
        self.wall.t = calendar.timegm((2026, 11, 12, 4, 0, 0))
        self.logs = []
        self.gw = gateway.Gateway(
            {"channel": OpenVikingBackend(self.ov.url, CHAN_KEY, writable=True, floor=0, ceil=1)},
            {"owner": OWNER, "live": LIVE},
            {"owner": TOKEN_OWNER, "live": TOKEN_LIVE},
            guards=[RedactGuard(), DailyCapGuard(utc_offset_hours=8, wall=self.wall)],
            clock=self.clock,
            log=self.logs.append,
        )
        self.server = http_server.make_server(self.gw, "127.0.0.1", 0)
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


DISCONNECT = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, socket.timeout)

# Non-whitespace characters that used to split a long secret in two for the pre-pass.
SPLITTERS = {"cjk": "中", "full-width": "Ａ", "zero-width": "​", "nul": "\x00", "dotless": "ı"}
SECRET_LEAK = re.compile(r"a{5,}|b{5,}")


def split_secret(splitter):
    return "token=" + "a" * 300 + splitter + "b" * 200


def raw_http(port, payload, timeout=5):
    """Send raw bytes, return the status code of the answer (0 if none)."""
    data = b""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        try:
            sock.sendall(payload)
            while b"\r\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        except DISCONNECT:
            pass
    parts = data.split(b" ", 2)
    return int(parts[1]) if len(parts) > 1 else 0


FAST_FIND = (
    b'{"result": {"memories": [{"uri": "viking://~/memories/preferences/user/a.md", "score": 0.9, "abstract": ""}]}}'
)


class RawUpstream:
    """Raw-socket upstream. mode: hang (never answers), headers or body (dribble forever).

    With fast_find the find call is answered at once, so only the read dribbles.
    """

    def __init__(self, mode, fast_find=False):
        self.mode, self.fast_find = mode, fast_find
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(64)
        self.srv.settimeout(0.05)
        self.url = "http://127.0.0.1:%d" % self.srv.getsockname()[1]
        self.halt = threading.Event()
        self.conns = []
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self):
        while not self.halt.is_set():
            try:
                conn, _ = self.srv.accept()
            except OSError:
                continue
            self.conns.append(conn)
            if self.mode != "hang":
                threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            head, _, rest = data.partition(b"\r\n\r\n")
            m = re.search(rb"(?i)content-length:\s*(\d+)", head)
            need = int(m.group(1)) if m else 0
            while len(rest) < need:
                rest += conn.recv(4096)
            path = head.split(b" ", 2)[1]
            if self.fast_find and path.startswith(b"/api/v1/search/find"):
                conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: %d\r\n\r\n" % len(FAST_FIND) + FAST_FIND)
                return
            if self.mode == "headers":
                conn.sendall(b"HTTP/1.0 200 OK\r\nX-Pad: ")
                pad = b"a"
            else:
                conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 100000\r\n\r\n")
                pad = b"x"
            while not self.halt.is_set():
                conn.sendall(pad)
                time.sleep(0.02)
        except OSError:
            pass
        finally:
            conn.close()

    def stop(self):
        self.halt.set()
        self.srv.close()
        for c in self.conns:
            try:
                c.close()
            except OSError:
                pass
        self.thread.join(2)


class HardeningTests(GatewayTestCase):
    def real_server(self, ov_url=None, pool_size=None, **kw):
        """A second gateway on the real clock, with short deadlines."""
        ov_url = ov_url or self.ov.url
        extra = {"pool_size": pool_size} if pool_size else {}
        deadline = kw.pop("recall_deadline", 0.3)
        gw = gateway.Gateway(
            {"channel": OpenVikingBackend(ov_url, CHAN_KEY, writable=True, floor=0, ceil=1)},
            {"owner": OWNER, "live": LIVE},
            {"owner": TOKEN_OWNER, "live": TOKEN_LIVE},
            log=self.logs.append,
            recall_deadline=deadline,
            cold_recall_deadline=deadline,  # these tests measure the deadline itself, cold or not
            remember_deadline=kw.pop("remember_deadline", 0.3),
            **extra,
        )
        server = http_server.make_server(gw, "127.0.0.1", 0, **kw)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(5)

        self.addCleanup(stop)
        return server

    def post(self, server, path, body, token=TOKEN_OWNER):
        payload = json.dumps(body).encode()
        head = "POST %s HTTP/1.0\r\nAuthorization: Bearer %s\r\nContent-Length: %d\r\n\r\n" % (
            path,
            token,
            len(payload),
        )
        data = b""
        with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5) as sock:
            try:
                sock.sendall(head.encode() + payload)
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    data += chunk
            except DISCONNECT:
                pass
        status = int(data.split(b" ", 2)[1])
        return status, json.loads(data.split(b"\r\n\r\n", 1)[1])

    def test_slow_ov_recall_returns_empty_within_deadline(self):
        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/preferences/user/a.md", 0.9, "chan fact")]
        self.ov.delay = {CHAN_KEY: 1.0}
        server = self.real_server()
        t0 = time.monotonic()
        status, body = self.post(server, "/v1/recall", {"query": "x"})
        self.assertLess(time.monotonic() - t0, 0.8)
        self.assertEqual((status, body), (503, {"error": "unavailable", "code": "unavailable"}))

    def test_read_fallbacks_capped_at_three(self):
        self.ov.memories[CHAN_KEY] = [
            mem("viking://~/memories/preferences/user/%d.md" % i, 0.9 - i / 100, "") for i in range(6)
        ]
        for i in range(6):
            self.ov.contents["viking://~/memories/preferences/user/%d.md" % i] = "body %d" % i
        status, body = self.recall(limit=10)
        self.assertEqual(len(body["results"]), 3)
        reads = [c for c in self.ov.calls if "content/read" in c[2]]
        self.assertEqual(len(reads), 3)

    def test_slow_ov_remember_is_502_within_deadline(self):
        self.ov.delay = {CHAN_KEY: 1.0}
        server = self.real_server()
        t0 = time.monotonic()
        status, body = self.post(server, "/v1/remember", {"messages": [{"role": "user", "content": "hi"}]})
        self.assertLess(time.monotonic() - t0, 0.9)
        self.assertEqual(status, 502)

    def test_remember_bounds_message_count_and_total_size(self):
        many = [{"role": "user", "content": "x"}] * (gateway.MAX_MESSAGES + 1)
        self.assertEqual(self.request("POST", "/v1/remember", {"messages": many})[0], 400)
        big = [{"role": "user", "content": "y" * 3500}] * 5
        self.assertEqual(self.request("POST", "/v1/remember", {"messages": big})[0], 400)
        self.assertEqual(self.ov.calls, [])

    def test_dribbling_client_is_disconnected_by_deadline(self):
        server = self.real_server(read_deadline=0.5)
        t0 = time.monotonic()
        closed = False
        with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5) as sock:
            sock.sendall(b"POST /v1/recall HTTP/1.0\r\nContent-Length: 100\r\n")
            while time.monotonic() - t0 < 4 and not closed:
                try:
                    sock.sendall(b"X")
                    sock.settimeout(0.1)
                    if sock.recv(1) == b"":
                        closed = True
                except socket.timeout:
                    pass
                except OSError:  # includes aborted and reset connections
                    closed = True
        self.assertTrue(closed)
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_connections_beyond_cap_are_refused_promptly(self):
        server = self.real_server(max_connections=2, read_deadline=3.0)
        port = server.server_address[1]
        idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(2)]
        for s in idle:
            self.addCleanup(s.close)
        end = time.monotonic() + 3
        while server.active < 2 and time.monotonic() < end:
            time.sleep(0.01)
        self.assertEqual(server.active, 2)
        t0 = time.monotonic()
        self.assertEqual(raw_http(port, b"GET /healthz HTTP/1.0\r\n\r\n"), 503)
        self.assertLess(time.monotonic() - t0, 1.0)
        for s in idle:
            s.close()
        end = time.monotonic() + 3
        while server.active and time.monotonic() < end:
            time.sleep(0.01)
        self.assertEqual(raw_http(port, b"GET /healthz HTTP/1.0\r\n\r\n"), 200)

    def test_malformed_content_length(self):
        auth = "Authorization: Bearer %s\r\n" % TOKEN_OWNER
        for value, want in (("-5", 400), ("abc", 400), (None, 411)):
            cl = "" if value is None else "Content-Length: %s\r\n" % value
            req = ("POST /v1/recall HTTP/1.0\r\n" + auth + cl + "\r\n").encode()
            self.assertEqual(raw_http(self.port, req), want, value)

    def send_in_two_writes(self, port, method, path, body, token, length=None):
        """Headers and body in separate writes, then read the answer to EOF. Returns the status."""
        length = len(body) if length is None else length
        head = "%s %s HTTP/1.0\r\nAuthorization: Bearer %s\r\nContent-Length: %d\r\n\r\n" % (method, path, token, length)
        data = b""
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(head.encode())
            sock.sendall(body)
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        return int(data.split(b" ", 2)[1])

    def test_bad_token_with_a_body_always_gets_401(self):
        sizes = (1, 1024, 16 * 1024, http_server.MAX_BODY)
        for i in range(200):
            body = b"x" * sizes[i % len(sizes)]
            self.assertEqual(self.send_in_two_writes(self.port, "POST", "/v1/recall", body, "wrong-token"), 401, i)
        self.assertEqual(self.ov.calls, [])

    def test_other_early_rejections_with_a_body_are_always_received(self):
        body = b"x" * http_server.MAX_BODY
        cases = (
            ("POST", "/v1/remember", TOKEN_LIVE, 403),
            ("POST", "/v1/forget", TOKEN_OWNER, 404),
            ("GET", "/v1/recall", TOKEN_OWNER, 405),
        )
        for method, path, token, want in cases:
            for i in range(50):
                self.clock.t += 61  # stay clear of the rate limit
                self.assertEqual(self.send_in_two_writes(self.port, method, path, body, token), want, (path, i))
        for _ in range(limits.RATE_LIMIT):
            self.assertEqual(self.request("POST", "/v1/recall", {"query": "x"})[0], 200)
        self.assertEqual(self.send_in_two_writes(self.port, "POST", "/v1/recall", body, TOKEN_OWNER), 429)

    def test_rejected_body_is_neither_parsed_nor_logged(self):
        marker = b"not-json-body-marker"
        status = self.send_in_two_writes(self.port, "POST", "/v1/recall", marker, "wrong-token")
        self.assertEqual(status, 401)
        self.assertEqual(self.ov.calls, [])
        end = time.monotonic() + 3
        while not any("401" in ln for ln in self.logs) and time.monotonic() < end:
            time.sleep(0.01)
        self.assertTrue(any("401" in ln for ln in self.logs))
        self.assertFalse(any("marker" in ln for ln in self.logs))

    def test_nothing_is_drained_without_a_valid_content_length(self):
        for cl in ("", "Content-Length: abc\r\n", "Content-Length: -5\r\n"):
            req = "POST /v1/recall HTTP/1.0\r\nAuthorization: Bearer wrong-token\r\n%s\r\n" % cl
            t0 = time.monotonic()
            self.assertEqual(raw_http(self.port, req.encode()), 401, cl)
            self.assertLess(time.monotonic() - t0, 1.0, cl)

    def test_drain_stops_at_64kb(self):
        server = self.real_server()
        t0 = time.monotonic()
        status = self.send_in_two_writes(
            server.server_address[1], "POST", "/v1/recall", b"x" * http_server.MAX_BODY, "wrong-token",
            length=1024 * 1024,
        )
        self.assertEqual(status, 401)
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_drain_is_bounded_by_the_read_deadline(self):
        server = self.real_server(read_deadline=0.3)
        head = b"POST /v1/recall HTTP/1.0\r\nAuthorization: Bearer wrong-token\r\nContent-Length: 1000\r\n\r\n"
        t0 = time.monotonic()
        with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5) as sock:
            sock.sendall(head)  # the declared body never comes
            try:
                while sock.recv(4096):
                    pass
            except OSError:
                pass
        self.assertLess(time.monotonic() - t0, 1.5)
        end = time.monotonic() + 3
        while server.active and time.monotonic() < end:
            time.sleep(0.01)
        self.assertEqual(server.active, 0)

    def test_100kb_digit_string_through_recall_is_fast(self):
        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/entities/a.md", 0.9, "1" * 100000)]
        t0 = time.monotonic()
        status, body = self.recall()
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(status, 200)

    def test_empty_abstract_read_is_redacted(self):
        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/preferences/user/a.md", 0.9, "")]
        self.ov.contents["viking://~/memories/preferences/user/a.md"] = "token=abcdef123456 ok"
        status, body = self.recall()
        self.assertEqual(len(body["results"]), 1)
        text = body["results"][0]["text"]
        self.assertNotIn("abcdef123456", text)
        self.assertIn("ok", text)
        self.assertEqual(len([c for c in self.ov.calls if "content/read" in c[2]]), 1)

    def test_redaction_and_1500_cap_together(self):
        self.ov.memories[CHAN_KEY] = [
            mem("viking://~/memories/preferences/user/a.md", 0.9, "word " * 290),
            mem("viking://~/memories/preferences/user/b.md", 0.8, "pad token=abcdef123456 more sk-" + "z" * 40),
        ]
        status, body = self.recall()
        self.assertLessEqual(sum(len(r["text"]) for r in body["results"]), 1500)
        joined = " ".join(r["text"] for r in body["results"])
        self.assertNotIn("abcdef123456", joined)
        self.assertNotIn("zzzz", joined)

    def upstream(self, mode, fast_find=False):
        up = RawUpstream(mode, fast_find)
        self.addCleanup(up.stop)
        return up

    def test_dribbled_upstream_returns_at_the_deadline(self):
        budget = 0.2
        for mode in ("headers", "body"):
            for fast_find in (False, True):
                up = self.upstream(mode, fast_find)
                server = self.real_server(up.url, recall_deadline=budget, remember_deadline=budget)
                t0 = time.monotonic()
                status, body = self.post(server, "/v1/recall", {"query": "x"})
                took = time.monotonic() - t0
                # a find that answers but whose read dribbles is a lookup that worked: empty, not unavailable
                expected = (200, {"results": [], "more": 0, "failed": 0, "busy": 0}) if fast_find else (503, {"error": "unavailable", "code": "unavailable"})
                self.assertEqual((status, body), expected, (mode, fast_find))
                self.assertLess(took, budget + 0.15, ("recall", mode, fast_find))
            t0 = time.monotonic()
            status, _ = self.post(server, "/v1/remember", {"messages": [{"role": "user", "content": "hi"}]})
            took = time.monotonic() - t0
            self.assertEqual(status, 502, mode)
            self.assertLess(took, budget + 0.15, ("remember", mode))

    def test_threads_stay_bounded_and_workers_are_freed_after_cancel(self):
        up = self.upstream("hang")
        server = self.real_server(up.url, recall_deadline=0.1, pool_size=3)
        pool = server.gw.pool
        baseline = threading.active_count()
        peak = 0
        for _ in range(15):
            status, body = self.post(server, "/v1/recall", {"query": "x"})
            self.assertEqual((status, body), (503, {"error": "unavailable", "code": "unavailable"}))
            peak = max(peak, threading.active_count() - baseline)
        # at most the pool workers plus the one handler thread serving us
        self.assertLessEqual(peak, pool.size + 1)
        end = time.monotonic() + 3
        while pool.in_flight and time.monotonic() < end:
            time.sleep(0.01)
        self.assertEqual(pool.in_flight, 0)
        self.assertLessEqual(pool.workers, pool.size)
        self.assertLessEqual(threading.active_count() - baseline, pool.size)

    def test_busy_pool_gives_partial_results_not_a_queue(self):
        up = self.upstream("hang")
        server = self.real_server(up.url, recall_deadline=0.2, pool_size=1)
        t0 = time.monotonic()
        status, body = self.post(server, "/v1/recall", {"query": "x"})
        self.assertEqual((status, body), (503, {"error": "unavailable", "code": "unavailable"}))
        self.assertLess(time.monotonic() - t0, 0.35)

    JWT = "eyJ" + "a" * 1000 + "." + "b" * 200 + "." + "c" * 100
    EMAIL = "e" * 300 + "@example.com"
    FRAGMENTS = ("eyJ", "aaaa", "bbbb", "cccc", "eeee", "example.com")

    def shrinking_prefix(self, runs):
        """Long runs become [REDACTED], so the secret still lands inside the 1500-char output."""
        return ("x" * 300 + " ") * runs

    def test_long_secrets_at_many_offsets_leak_nothing_through_abstracts_and_reads(self):
        for secret in (self.JWT, self.EMAIL):
            for runs in (0, 1, 13, 25, 26, 27, 28):
                text = self.shrinking_prefix(runs) + secret + " tail words"
                self.ov.contents["viking://~/memories/entities/r.md"] = text
                for abstract in (text, ""):
                    uri = "viking://~/memories/entities/%s.md" % ("a" if abstract else "r")
                    self.ov.memories[CHAN_KEY] = [mem(uri, 0.9, abstract)]
                    status, body = self.recall()
                    self.assertEqual(status, 200)
                    out = body["results"][0]["text"]
                    for frag in self.FRAGMENTS:
                        self.assertNotIn(frag, out, (runs, bool(abstract)))
                    self.assertGreaterEqual(out.count("[REDACTED]"), runs + 1)
                    self.assertTrue(out.endswith("tail words"))

    def test_oversize_item_is_dropped_whole_and_logged_without_its_length(self):
        big = "word " * 14000
        self.assertGreater(len(big), redact.MAX_ITEM_CHARS)
        self.ov.memories[CHAN_KEY] = [
            mem("viking://~/memories/entities/big.md", 0.9, big),
            mem("viking://~/memories/entities/ok.md", 0.8, "fine"),
        ]
        status, body = self.recall()
        self.assertEqual([r["text"] for r in body["results"]], ["fine"])
        # the server writes its access-log line after it has sent the answer: wait for it
        end = time.monotonic() + 3
        while not any("/v1/recall" in ln for ln in self.logs) and time.monotonic() < end:
            time.sleep(0.01)
        line = [ln for ln in self.logs if "/v1/recall" in ln][-1]
        self.assertIn("oversize-dropped", line)
        self.assertNotIn(str(len(big)), line)

    def test_remember_masks_long_runs_before_sending(self):
        self.request("POST", "/v1/remember", {"messages": [{"role": "user", "content": "keep " + "q" * 300 + " end"}]})
        sent = json.dumps(self.ov.bodies)
        self.assertNotIn("qqqq", sent)
        self.assertIn("keep [REDACTED] end", sent)

    def test_unicode_split_secret_leaks_nothing_through_recall(self):
        tail = " tail" + " more" * 20
        uri = "viking://~/memories/entities/s.md"
        for name, splitter in SPLITTERS.items():
            for prefix in ("", "word " * 288):  # the second is too long to fit whole, so it is left out
                text = prefix + split_secret(splitter) + tail
                self.ov.contents[uri] = text
                for abstract in (text, ""):
                    where = (name, bool(prefix), bool(abstract))
                    self.clock.t += 61  # stay clear of the rate limit
                    self.ov.memories[CHAN_KEY] = [mem(uri, 0.9, abstract)]
                    status, body = self.recall()
                    self.assertEqual(status, 200, where)
                    if prefix:
                        self.assertEqual((body["results"], body["more"]), ([], 1), where)
                        continue
                    out = body["results"][0]["text"]
                    self.assertIsNone(SECRET_LEAK.search(out), where)
                    self.assertIn("[REDACTED]", out, where)
                    self.assertTrue(out.endswith("more"), where)

    def test_unicode_split_secret_leaks_nothing_into_remember(self):
        tail = " tail" + " more" * 20
        for name, splitter in SPLITTERS.items():
            for prefix in ("", "word " * 288):
                where = (name, bool(prefix))
                self.clock.t += 61
                self.ov.bodies.clear()
                content = prefix + split_secret(splitter) + tail
                status, _ = self.request("POST", "/v1/remember", {"messages": [{"role": "user", "content": content}]})
                self.assertEqual(status, 200, where)
                stored = json.dumps(self.ov.bodies)
                self.assertIsNone(SECRET_LEAK.search(stored), where)
                self.assertIn("[REDACTED] tail", stored, where)

    def test_pool_start_failures_do_not_leak_capacity(self):
        pool = Pool(8)
        with mock.patch.object(threading.Thread, "start", side_effect=RuntimeError("no threads")):
            for _ in range(8):
                self.assertIsNone(pool.submit(lambda t: 1))
            self.assertEqual((pool.in_flight, pool.workers), (0, 0))
            stores = {"channel": OpenVikingBackend(self.ov.url, CHAN_KEY, floor=0, ceil=1)}
            ctx = RecallContext("x", 5, pool=pool)
            out = run_recall(stores, gateway.default_stages(), ctx)
            # the find never started: busy, not a broken store
            self.assertEqual(out, {"results": [], "more": 0, "failed": 0, "busy": 1})
        for _ in range(9):
            task = pool.submit(lambda t: 42)
            self.assertIsNotNone(task)
            self.assertTrue(task.wait(2))
            self.assertEqual(task.result, 42)
        pool._workers.append(threading.Thread(target=lambda: None))  # never started
        pool.close()

    def test_the_connection_cap_and_read_deadline_are_the_documented_defaults(self):
        self.assertEqual(http_server.MAX_CONNECTIONS, 32)
        self.assertEqual(http_server.READ_DEADLINE, 10.0)
        self.assertEqual(http_server.MAX_BODY, 64 * 1024)

    def test_oversize_body_is_413_and_never_reaches_a_store(self):
        body = b"x" * (http_server.MAX_BODY + 1)
        self.assertEqual(self.send_in_two_writes(self.port, "POST", "/v1/recall", body, TOKEN_OWNER), 413)
        self.assertEqual(self.ov.calls, [])

    def test_a_refusal_over_the_connection_cap_is_json_with_a_code(self):
        server = self.real_server(max_connections=1, read_deadline=3.0)
        port = server.server_address[1]
        idle = socket.create_connection(("127.0.0.1", port))
        self.addCleanup(idle.close)
        end = time.monotonic() + 3
        while server.active < 1 and time.monotonic() < end:
            time.sleep(0.01)
        status, headers, body = raw_reply(port, b"GET /healthz HTTP/1.0\r\n\r\n")
        self.assertEqual((status, body), (503, {"error": "busy", "code": "busy"}))
        self.assertIn(b"content-type: application/json", headers.lower())


def raw_reply(port, payload, timeout=5):
    """Send raw bytes, read to the end; returns (status, header bytes, parsed JSON body or None)."""
    data = b""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        try:
            sock.sendall(payload)
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        except DISCONNECT:
            pass
    head, _, rest = data.partition(b"\r\n\r\n")
    try:
        body = json.loads(rest)
    except ValueError:
        body = None
    return int(head.split(b" ", 2)[1]), head, body


class ErrorCodeTests(GatewayTestCase):
    """Every error over HTTP is {"error": text, "code": stable code}."""

    def test_each_refusal_carries_its_code(self):
        cases = (
            (self.request("POST", "/v1/recall", {"query": "x"}, token="wrong-token"), 401, "unauthorized"),
            (self.request("POST", "/v1/recall", {"query": "x"}, token=None), 401, "unauthorized"),
            (self.request("GET", "/nowhere"), 404, "not_found"),
            (self.request("GET", "/v1/recall"), 405, "method_not_allowed"),
            (self.request("POST", "/v1/remember", {"messages": []}, token=TOKEN_LIVE), 403, "forbidden"),
            (self.request("POST", "/v1/recall", raw=b"{not json"), 400, "bad_request"),
            (self.request("POST", "/v1/recall", raw=b"[1, 2]"), 400, "bad_request"),
            (self.request("POST", "/v1/recall", {"query": ""}), 400, "bad_query"),
            (self.request("POST", "/v1/recall", {"query": "x", "limit": "3"}), 400, "bad_limit"),
            (self.request("POST", "/v1/recall", {"query": "x", "context": {"k": [1]}}), 400, "bad_context"),
            (self.request("POST", "/v1/remember", {"messages": "hi"}), 400, "bad_messages"),
            (self.request("POST", "/v1/recall", {"query": "x", "tenant_id": "1"}), 400, "bad_tenant"),
        )
        for (status, body), want_status, want_code in cases:
            self.assertEqual(status, want_status, want_code)
            self.assertEqual(sorted(body), ["code", "error"], want_code)
            self.assertEqual(body["code"], want_code)
            self.assertIsInstance(body["error"], str)

    def test_length_size_and_number_problems_carry_codes(self):
        auth = "Authorization: Bearer %s\r\n" % TOKEN_OWNER
        no_length = ("POST /v1/recall HTTP/1.0\r\n" + auth + "\r\n").encode()
        self.assertEqual(raw_reply(self.port, no_length)[::2], (411, {"error": "length required",
                                                                      "code": "length_required"}))
        bad_length = ("POST /v1/recall HTTP/1.0\r\n" + auth + "Content-Length: abc\r\n\r\n").encode()
        self.assertEqual(raw_reply(self.port, bad_length)[2]["code"], "bad_request")
        big = b"x" * (http_server.MAX_BODY + 1)
        too_big = ("POST /v1/recall HTTP/1.0\r\n" + auth + "Content-Length: %d\r\n\r\n" % len(big)).encode() + big
        self.assertEqual(raw_reply(self.port, too_big)[::2], (413, {"error": "body too large", "code": "too_large"}))

    def test_an_unexpected_server_error_is_500_internal(self):
        with mock.patch.object(self.gw, "identify", side_effect=RuntimeError("bug")):
            self.assertEqual(self.recall(), (500, {"error": "internal error", "code": "internal"}))

    def test_the_rate_limit_carries_its_code(self):
        for _ in range(limits.RATE_LIMIT):
            self.recall()
        self.assertEqual(self.recall(), (429, {"error": "rate limited", "code": "rate_limited"}))


class ExpectContinueTests(GatewayTestCase):
    """A client that sends Expect: 100-continue waits for our go-ahead before it sends the body."""

    def head(self, path="/v1/recall", token=TOKEN_OWNER, length=0):
        return ("POST %s HTTP/1.1\r\nHost: gateway\r\nAuthorization: Bearer %s\r\nContent-Type: application/json\r\n"
                "Content-Length: %d\r\nExpect: 100-continue\r\n\r\n" % (path, token, length)).encode()

    def read_until(self, sock, marker, limit=5):
        data = b""
        end = time.monotonic() + limit
        while marker not in data and time.monotonic() < end:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        return data

    def test_the_go_ahead_comes_before_the_body_is_read(self):
        body = json.dumps({"query": "hello"}).encode()
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            t0 = time.monotonic()
            sock.sendall(self.head(length=len(body)))
            first = self.read_until(sock, b"\r\n\r\n")
            self.assertEqual(first, b"HTTP/1.1 100 Continue\r\n\r\n")
            self.assertLess(time.monotonic() - t0, 0.5)
            sock.sendall(body)
            rest = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                rest += chunk
        self.assertEqual(int(rest.split(b" ", 2)[1]), 200)
        self.assertEqual(json.loads(rest.partition(b"\r\n\r\n")[2]), {"results": [], "more": 0, "failed": 0,
                                                                     "busy": 0})

    def test_an_early_refusal_is_answered_at_once_without_the_body(self):
        cases = ((self.head(token="wrong-token", length=100), 401), (self.head("/v1/remember", TOKEN_LIVE, 100), 403),
                 (self.head(length=http_server.MAX_BODY + 1), 413))
        for head, want in cases:
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
                t0 = time.monotonic()
                sock.sendall(head)
                data = self.read_until(sock, b"}")
                self.assertLess(time.monotonic() - t0, 0.5)
            self.assertNotIn(b"100 Continue", data)
            self.assertEqual(int(data.split(b" ", 2)[1]), want)
        self.assertEqual(self.ov.calls, [])

    def test_a_client_that_sends_the_body_anyway_still_gets_the_refusal(self):
        body = b"x" * 16 * 1024
        for i in range(30):
            status, _, reply = raw_reply(self.port, self.head(token="wrong-token", length=len(body)) + body)
            self.assertEqual((status, reply["code"]), (401, "unauthorized"), i)

    def test_http_1_0_gets_no_interim_answer(self):
        body = json.dumps({"query": "hello"}).encode()
        head = self.head(length=len(body)).replace(b"HTTP/1.1", b"HTTP/1.0")
        status, headers, reply = raw_reply(self.port, head + body)
        self.assertEqual(status, 200)
        self.assertNotIn(b"100 Continue", headers)


class DeepHealthTests(GatewayTestCase):
    def deep(self):
        return self.request("GET", "/healthz?deep=1", token=None)

    def test_deep_health_names_each_store_and_never_content(self):
        status, body = self.deep()
        self.assertEqual(status, 200)
        self.assertEqual(body["stores"], {"channel": "ok"})
        self.assertIn("recall_failed_1h", body)
        self.assertIn("writes_24h", body)
        self.assertNotIn(CHAN_KEY, json.dumps(body))

    def test_deep_health_reports_a_store_that_is_down(self):
        self.ov.stop()
        status, body = self.deep()
        self.assertEqual(status, 200)
        self.assertEqual(body["stores"], {"channel": "down"})


class RedactBoundsTests(unittest.TestCase):
    N = 65000

    def rep(self, unit):
        return (unit * (self.N // len(unit) + 1))[: self.N]

    def test_adversarial_inputs_just_under_64kb_finish_fast(self):
        cases = {
            "digits": "1" * self.N,
            "a1": self.rep("a1"),
            "spaced digits": self.rep("1 "),
            "units": self.rep("省市区路号"),
            "units spaced": self.rep("省 市 区 路 号 "),
            "dots with spaces every 200": self.rep("a." * 100 + " "),
            "token=": self.rep("token="),
            "sk-": self.rep("sk-"),
            "token = spaced": self.rep("token = "),
            "at signs": self.rep("a@" * 50 + " "),
            "street": self.rep("1 a 1 St "),
            "mixed run, 256 letters + cjk": self.rep("a" * 256 + "中"),
            "mixed run, 256 digits + cjk": self.rep("1" * 256 + "中"),
            "all cjk": self.rep("中"),
            "whitespace every 200": self.rep("a" * 199 + " "),
            "whitespace every 200, cjk": self.rep("中" * 199 + " "),
        }
        for name, text in cases.items():
            self.assertLess(len(text), 64 * 1024)
            for fn in (redact.redact, redact.redact_public):
                t0 = time.monotonic()
                fn(text)
                self.assertLess(time.monotonic() - t0, 1.0, (name, fn.__name__))

    def test_long_secrets_at_every_few_offsets_leave_no_fragment(self):
        jwt = "eyJ" + "a" * 1000 + "." + "b" * 200 + "." + "c" * 100
        email = "e" * 300 + "@example.com"
        offsets = [0, 1, 255, 4096, 7999, 8003, 8004, 8005, 8191, 8192, 8193, 8703, 8704, 8705, 12000, 30000, 60000]
        for off in offsets:
            prefix = ("word " * (off // 5 + 1))[:off] + " "
            for secret in (jwt, email):
                out = redact.redact_public(prefix + secret + " tail")
                for frag in ("eyJ", "aaaa", "bbbb", "cccc", "eeee", "example.com"):
                    self.assertNotIn(frag, out, (off, frag))
                self.assertTrue(out.endswith(" tail"))

    def test_runs_up_to_256_are_left_alone_longer_ones_are_masked_whole(self):
        ok = "z" * 256
        self.assertEqual(redact.redact("a " + ok + " b"), "a " + ok + " b")
        self.assertEqual(redact.redact("a " + ok + "z b"), "a [REDACTED] b")
        text = "I like green tea and long walks in the park. " * 20
        self.assertEqual(redact.redact(text), text)

    def test_cjk_prose_without_spaces_is_not_masked(self):
        prose = "我很喜欢绿茶，" * 80
        self.assertGreater(len(prose), 256)
        self.assertEqual(redact.redact(prose), prose)

    def test_long_ascii_run_attached_to_cjk_is_masked_whole(self):
        attached = "密钥" + "k" * 300 + "结束"
        self.assertEqual(redact.redact(attached), "[REDACTED]")
        self.assertEqual(redact.redact("keep " + attached + " keep"), "keep [REDACTED] keep")

    def test_unicode_split_secret_leaves_no_fragment(self):
        for name, splitter in SPLITTERS.items():
            for fn in (redact.redact, redact.redact_public):
                self.assertEqual(fn(split_secret(splitter) + " tail"), "[REDACTED] tail", (name, fn.__name__))

    def test_long_cjk_paragraph_without_long_ascii_is_untouched_by_the_pre_pass(self):
        prose = "我很喜欢绿茶，" * 150
        with_short_ascii = ("我很喜欢" + "n" * 256 + "。") * 5
        for text in (prose, with_short_ascii):
            self.assertGreater(len(text), 1000)
            self.assertEqual(redact.mask_long_runs(text), text)

    def test_attached_ascii_sub_run_limit_is_256(self):
        text = "密" + "z" * 256 + "钥"
        self.assertEqual(redact.mask_long_runs(text), text)
        self.assertEqual(redact.mask_long_runs("密" + "z" * 257 + "钥"), "[REDACTED]")

    def test_runs_end_at_unicode_whitespace_only(self):
        for space in ("　", "\xa0"):
            text = "密" + "k" * 300 + space + "结束"
            self.assertEqual(redact.mask_long_runs(text), "[REDACTED]" + space + "结束")
            # The key=value rule stops at the same character.
            self.assertEqual(redact.redact("token=abcdef" + space + "rest"), "token=[REDACTED]" + space + "rest")
        for joiner in ("​", "\x00"):
            self.assertEqual(redact.mask_long_runs("k" * 300 + joiner + "tail"), "[REDACTED]")


class TokenLoadingTests(unittest.TestCase):
    """build_gateway ignores tokens that are missing, the example placeholder or shorter than 32 characters,
    for every client, so a hand-copied example never guards a route with a public string."""

    GOOD = {"owner": "o" * 43, "live": "l" * 43, "group": "g" * 43}
    VARS = {"owner": "GATEWAY_TOKEN_OWNER", "live": "GATEWAY_TOKEN_LIVE", "group": "GATEWAY_TOKEN_GROUP"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.keys = self.tmp.name + "/keys.json"
        with open(self.keys, "w", encoding="utf-8") as f:
            f.write("{}")

    def cfg(self):
        url = "http://127.0.0.1:9"  # never contacted: building a gateway makes no request
        return {
            "stores": {"channel": {"type": "openviking", "url": url, "key_env": "OV_CHANNEL_KEY", "writable": True}},
            "tenants": {"group": {"type": "keyfile", "path": self.keys, "store": {"type": "openviking", "url": url}}},
            "clients": {
                "owner": {"token_env": self.VARS["owner"], "recall": ["channel"], "write": "channel"},
                "live": {"token_env": self.VARS["live"], "recall": [], "public": True},
                "group": {"token_env": self.VARS["group"], "recall": [], "write": "@tenant", "tenants": "group",
                          "public": True},
            },
        }

    def build(self, tokens):
        env = {"OV_CHANNEL_KEY": CHAN_KEY}
        env.update({self.VARS[name]: value for name, value in tokens.items()})
        lines = []
        gw = config.build_gateway(self.cfg(), env=env, log=lines.append)
        self.addCleanup(gw.close)
        return gw, lines

    def test_real_tokens_are_loaded(self):
        gw, lines = self.build(self.GOOD)
        for name, token in self.GOOD.items():
            self.assertTrue(gw.has_token(name), name)
            self.assertEqual(gw.authenticate(token), name)
        self.assertEqual(lines, [])

    def test_placeholder_short_or_empty_tokens_are_ignored_for_every_client(self):
        for bad in ("change-me", "x" * 31, "", "tok-owner-fake-0123456789"):
            for name in self.GOOD:
                with self.subTest(bad=bad, name=name):
                    gw, lines = self.build(dict(self.GOOD, **{name: bad}))
                    self.assertFalse(gw.has_token(name))
                    self.assertEqual(len(lines), 1)
                    self.assertIn(name, lines[0])
                    if bad:
                        self.assertIsNone(gw.authenticate(bad))
                        self.assertNotIn(bad, lines[0])
                    for other in set(self.GOOD) - {name}:
                        self.assertTrue(gw.has_token(other), other)

    def test_thirty_two_characters_is_enough(self):
        gw, _ = self.build(dict(self.GOOD, owner="o" * 32))
        self.assertTrue(gw.has_token("owner"))
        self.assertEqual(gateway.MIN_TOKEN_CHARS, 32)

    def test_a_client_without_a_token_never_authenticates(self):
        gw, _ = self.build(dict(self.GOOD, owner=""))
        self.assertIsNone(gw.authenticate(""))
        self.assertIsNone(gw.authenticate(None))


if __name__ == "__main__":
    unittest.main()
