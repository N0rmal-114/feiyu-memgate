"""Gateway tests for tenant clients: one store per tenant, key file reloads, budgets and isolation.

Self-contained: a fake OpenViking server on 127.0.0.1 tells accounts apart by their key.
"""
import calendar
import dataclasses
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from feiyu_memgate.backends.openviking import OpenVikingBackend as _OpenVikingBackend
from feiyu_memgate.core import limits
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.write import DailyCapGuard, RedactGuard
from feiyu_memgate.errors import ConfigError
from feiyu_memgate.gateway import app as gateway
from feiyu_memgate.gateway import server as http_server
from feiyu_memgate.model import ClientPolicy
from feiyu_memgate.tenants import keyfile
from feiyu_memgate.tenants.keyfile import KeyFileTenants

class OpenVikingBackend(_OpenVikingBackend):
    """The real backend, with the thresholds these fixtures' scores were written for (min_score 0.5, ratio 0.6);
    the backend's own defaults are chosen on bench/dev and are pinned in test_backend_openviking_manage."""
    default_min_score = 0.5
    default_relative_ratio = 0.6
    broad_min_score = None  # no broad lines: the default lines above apply under every profile
    broad_relative_ratio = None

CHAN_KEY = "chan-key-fake"
ARCHIVE_KEY = "archive-key-fake"
TOKEN_OWNER = "tok-owner-fake-" + "0" * 20
TOKEN_LIVE = "tok-live-fake-" + "0" * 20
TOKEN_GROUP = "tok-group-fake-" + "0" * 20
GROUP_A, GROUP_B = "10001", "20002"
KEY_A, KEY_B = "grp-key-a-fake", "grp-key-b-fake"
PHONE = "13812345678"

OWNER = ClientPolicy("owner", recall=("channel",), write="channel", daily_writes=50)
LIVE = ClientPolicy("live", recall=(), public=True)
GROUP = ClientPolicy("group", recall=("@tenant",), write="@tenant", tenants="group", public=True, daily_writes=20)

TENANT_POOL_TOTAL = 3
TENANT_POOL_PER_TENANT = 2


def mem(uri, score, abstract):
    return {"uri": uri, "score": score, "abstract": abstract}


class FakeOV:
    """Fake OpenViking on 127.0.0.1, telling accounts apart by Authorization."""

    def __init__(self):
        self.memories = {CHAN_KEY: [], ARCHIVE_KEY: []}
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


class TenantTestCase(unittest.TestCase):
    KEYS_TEXT = None  # default: both tenants registered

    def setUp(self):
        self.ov = FakeOV()
        self.addCleanup(self.ov.stop)
        for key in (KEY_A, KEY_B):
            self.ov.memories[key] = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.keys_path = Path(self.tmp.name) / "keys.json"
        text = self.KEYS_TEXT if self.KEYS_TEXT is not None else json.dumps({GROUP_A: KEY_A, GROUP_B: KEY_B})
        self.keys_path.write_text(text, encoding="utf-8")
        self.clock = Clock()
        self.wall = Clock()
        self.wall.t = calendar.timegm((2026, 11, 12, 4, 0, 0))
        self.logs = []
        self.keys = KeyFileTenants(
            str(self.keys_path), {"type": "openviking", "url": self.ov.url, "floor": 0, "ceil": 1}, id_pattern=r"[0-9]{5,12}",
            log=self.logs.append,
        )
        stores = {
            "channel": OpenVikingBackend(self.ov.url, CHAN_KEY, writable=True, floor=0, ceil=1),
            # A shared store that no client of this module may reach.
            "archive": OpenVikingBackend(self.ov.url, ARCHIVE_KEY, writable=True, floor=0, ceil=1),
        }
        self.gw = gateway.Gateway(
            stores,
            {"owner": OWNER, "live": LIVE, "group": GROUP},
            {"owner": TOKEN_OWNER, "live": TOKEN_LIVE, "group": TOKEN_GROUP},
            tenants={"group": self.keys},
            guards=[RedactGuard(), DailyCapGuard(utc_offset_hours=8, wall=self.wall)],
            clock=self.clock,
            log=self.logs.append,
            tenant_pool_total=TENANT_POOL_TOTAL,
            tenant_pool_per_tenant=TENANT_POOL_PER_TENANT,
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

    def request(self, path, body, token=TOKEN_GROUP):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("POST", path, body=json.dumps(body).encode(), headers={"Authorization": "Bearer " + token})
            resp = conn.getresponse()
            payload = resp.read()
            try:
                parsed = json.loads(payload)
            except ValueError:
                parsed = None
            return resp.status, parsed
        finally:
            conn.close()

    def recall(self, tenant_id=GROUP_A, token=TOKEN_GROUP, **extra):
        body = dict(query="hello", **extra)
        if tenant_id is not None:
            body["tenant_id"] = tenant_id
        return self.request("/v1/recall", body, token)

    def remember(self, tenant_id=GROUP_A, content="hi", token=TOKEN_GROUP, **extra):
        body = dict(messages=[{"role": "user", "content": content}], **extra)
        if tenant_id is not None:
            body["tenant_id"] = tenant_id
        return self.request("/v1/remember", body, token)

    def replace_keys(self, obj_or_text, bump=10):
        text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text)
        tmp = self.keys_path.with_name("keys.json.new")
        tmp.write_text(text, encoding="utf-8")
        st = self.keys_path.stat()
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns + bump * 10**9))
        os.replace(tmp, self.keys_path)


class TenantRecallTests(TenantTestCase):
    def test_tenant_reads_only_its_own_account(self):
        self.ov.memories[KEY_A] = [
            mem("viking://~/memories/events/2026/11/03/a.md", 0.8, "tenant a event"),
            mem("viking://~/memories/entities/x.md", 0.78, "tenant a entity"),
        ]
        status, body = self.recall()
        self.assertEqual(status, 200)
        self.assertEqual({r["source"] for r in body["results"]}, {"group"})
        self.assertEqual(sorted(r["text"] for r in body["results"]), ["tenant a entity", "tenant a event"])

    def test_tenant_client_never_reaches_a_shared_store_outside_its_policy(self):
        # Shared stores with top scores: neither is named by the group or live policy.
        self.ov.memories[CHAN_KEY] = [
            mem("viking://~/memories/profile.md", 0.99, "channel profile"),
            mem("viking://~/memories/events/2026/11/03/m.md", 0.97, "channel event"),
        ]
        self.ov.memories[ARCHIVE_KEY] = [mem("viking://~/memories/entities/m.md", 0.96, "archive entity")]
        for token, extra in ((TOKEN_GROUP, {"tenant_id": GROUP_A}), (TOKEN_LIVE, {})):
            status, body = self.request("/v1/recall", dict(query="q", limit=10, **extra), token)
            self.assertEqual(status, 200, token)
            self.assertEqual(body["results"], [], token)  # only the tenant's own account is searched
        self.assertEqual(self.ov.calls_with(CHAN_KEY), [])
        self.assertEqual(self.ov.calls_with(ARCHIVE_KEY), [])

    def test_a_store_no_client_names_is_never_called(self):
        self.ov.memories[ARCHIVE_KEY] = [mem("viking://~/memories/entities/m.md", 0.96, "archive entity")]
        self.recall()
        self.remember()
        self.request("/v1/recall", {"query": "hello"}, TOKEN_OWNER)
        self.request("/v1/remember", {"messages": [{"role": "user", "content": "x"}]}, TOKEN_OWNER)
        self.assertEqual(self.ov.calls_with(ARCHIVE_KEY), [])

    def test_tenant_output_is_redact_public(self):
        self.ov.memories[KEY_A] = [mem("viking://~/memories/events/2026/11/03/e.md", 0.9, "call " + PHONE + " ok")]
        status, body = self.recall()
        self.assertEqual(len(body["results"]), 1)
        for r in body["results"]:
            self.assertNotIn(PHONE, r["text"])

    def test_tenant_redacts_phone_email_address_room_and_id(self):
        self.ov.memories[KEY_A] = [
            mem(
                "viking://~/memories/entities/a.md",
                0.9,
                "phone 13812345678 mail me@example.com id 110101199001011234 "
                "home 北京市海淀区中关村大街12号 "
                "room 3栋2单元501室 done",
            )
        ]
        status, body = self.recall()
        self.assertEqual(status, 200)
        text = body["results"][0]["text"]
        for leaked in ("13812345678", "me@example.com", "110101199001011234", "中关村", "501室"):
            self.assertNotIn(leaked, text)
        self.assertIn("done", text)

    def test_tenant_never_reads_channel_or_other_tenant(self):
        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/events/2026/11/03/c.md", 0.9, "channel secret")]
        self.ov.memories[KEY_B] = [mem("viking://~/memories/events/2026/11/03/b.md", 0.9, "tenant b secret")]
        self.ov.memories[KEY_A] = [mem("viking://~/memories/events/2026/11/03/a.md", 0.9, "tenant a")]
        status, body = self.recall()
        self.assertEqual([r["text"] for r in body["results"]], ["tenant a"])
        self.assertEqual(self.ov.calls_with(CHAN_KEY), [])
        self.assertEqual(self.ov.calls_with(KEY_B), [])
        self.assertTrue(self.ov.calls_with(KEY_A))

    def test_tenant_client_requires_tenant_id(self):
        self.assertEqual(self.recall(tenant_id=None)[0], 400)
        self.assertEqual(self.remember(tenant_id=None)[0], 400)
        self.assertEqual(self.ov.calls, [])

    def test_clients_without_tenants_that_send_tenant_id_get_400(self):
        for token in (TOKEN_OWNER, TOKEN_LIVE):
            self.assertEqual(self.recall(token=token)[0], 400)
        self.assertEqual(self.remember(token=TOKEN_OWNER)[0], 400)
        self.assertEqual(self.ov.calls, [])

    def test_non_string_tenant_id_is_400(self):
        for bad in (10001, [GROUP_A], None, True, {"a": 1}):
            body = {"query": "hello", "tenant_id": bad}
            self.assertEqual(self.request("/v1/recall", body)[0], 400, bad)
            body = {"messages": [{"role": "user", "content": "x"}], "tenant_id": bad}
            self.assertEqual(self.request("/v1/remember", body)[0], 400, bad)
        self.assertEqual(self.ov.calls, [])

    def test_malformed_tenant_is_400_and_unknown_tenant_is_403(self):
        for bad in ("../x", "123", "1234567890123", "10001/../20002", "1000a", " 10001", "10001\n"):
            self.assertEqual(self.recall(tenant_id=bad), (400, {"error": "bad tenant_id", "code": "bad_tenant"}),
                             repr(bad))
            self.assertEqual(self.remember(tenant_id=bad)[0], 400, repr(bad))
        unknown = (403, {"error": "unknown tenant", "code": "unknown_tenant"})
        self.assertEqual(self.recall(tenant_id="99999"), unknown)
        self.assertEqual(self.remember(tenant_id="99999"), unknown)
        self.assertEqual(self.ov.calls, [])

    def test_removed_tenant_gets_403_after_reload(self):
        self.assertEqual(self.recall(GROUP_B)[0], 200)
        self.replace_keys({GROUP_A: KEY_A})
        self.assertEqual(self.recall(GROUP_B)[0], 403)
        self.assertEqual(self.remember(GROUP_B)[0], 403)
        self.assertEqual(self.recall(GROUP_A)[0], 200)

    def test_keys_reload_after_atomic_replace(self):
        self.assertEqual(self.recall("30003")[0], 403)
        self.ov.memories["grp-key-c-fake"] = [mem("viking://~/memories/events/2026/11/03/c.md", 0.9, "tenant c")]
        self.replace_keys({GROUP_A: KEY_A, "30003": "grp-key-c-fake"})
        status, body = self.recall("30003")
        self.assertEqual(status, 200)
        self.assertEqual([r["text"] for r in body["results"]], ["tenant c"])

    def test_corrupt_keys_keep_last_good_and_warn(self):
        self.replace_keys("{not json", bump=10)
        self.assertEqual(self.recall(GROUP_A)[0], 200)
        self.assertTrue(any("tenant" in l.lower() and "keys" in l.lower() for l in self.logs), self.logs)
        for bad in ("[]", json.dumps({GROUP_A: 5}), json.dumps({"abc": KEY_A})):
            self.replace_keys(bad, bump=20)
            self.assertEqual(self.recall(GROUP_A)[0], 200)
        joined = "\n".join(self.logs)
        for secret in (KEY_A, KEY_B):
            self.assertNotIn(secret, joined)

    def test_rate_limit_is_per_tenant(self):
        self.gw.clients["group"] = dataclasses.replace(GROUP, rate_limit=2)
        self.assertEqual(self.recall(GROUP_A)[0], 200)
        self.assertEqual(self.recall(GROUP_A)[0], 200)
        self.assertEqual(self.recall(GROUP_A)[0], 429)
        self.assertEqual(self.recall(GROUP_B)[0], 200)
        self.assertEqual(self.remember(GROUP_A)[0], 429)

    def test_logs_never_contain_tenant_keys(self):
        self.recall(GROUP_A)
        self.remember(GROUP_B)
        self.recall("30003")
        self.recall("../x")
        self.replace_keys("garbage")
        self.recall(GROUP_A)
        joined = "\n".join(self.logs)
        for secret in (KEY_A, KEY_B, TOKEN_GROUP):
            self.assertNotIn(secret, joined)

    def test_access_log_names_the_client(self):
        self.recall(GROUP_A)
        self.assertTrue(any("/v1/recall 200 client=group" in line for line in self.logs), self.logs)


class TenantRememberTests(TenantTestCase):
    def test_tenant_remember_only_uses_own_key(self):
        status, body = self.remember(GROUP_A, "hello")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["receipt"].startswith("gw-"))
        self.assertEqual(self.ov.calls_with(CHAN_KEY), [])
        self.assertEqual(self.ov.calls_with(ARCHIVE_KEY), [])
        self.assertEqual(self.ov.calls_with(KEY_B), [])
        self.assertTrue(self.ov.calls_with(KEY_A))

    def test_tenant_remember_applies_redact_public(self):
        status, _ = self.remember(GROUP_A, "my number is " + PHONE + " ok")
        self.assertEqual(status, 200)
        sent = [b for b in self.ov.bodies if "content" in b]
        self.assertEqual(len(sent), 1)
        self.assertNotIn(PHONE, sent[0]["content"])


class TenantDailyCapTests(TenantTestCase):
    def test_each_tenant_has_its_own_20_a_day(self):
        for _ in range(20):
            self.assertEqual(self.remember(GROUP_A)[0], 200)
        self.assertEqual(self.remember(GROUP_A), (429, {"error": "daily limit", "code": "daily_limit"}))
        self.assertEqual(self.remember(GROUP_B)[0], 200)
        self.assertEqual(self.recall(GROUP_A)[0], 200)

    def test_tenant_cap_is_separate_from_the_owner_cap(self):
        for _ in range(20):
            self.remember(GROUP_A)
        status, _ = self.request("/v1/remember", {"messages": [{"role": "user", "content": "x"}]}, TOKEN_OWNER)
        self.assertEqual(status, 200)


class TenantCorruptStartTests(TenantTestCase):
    KEYS_TEXT = "{not json"

    def test_corrupt_keys_at_start_deny_tenants_only(self):
        self.assertEqual(self.recall(GROUP_A)[0], 403)
        self.assertEqual(self.remember(GROUP_A)[0], 403)
        status, _ = self.request("/v1/recall", {"query": "hello"}, token=TOKEN_OWNER)
        self.assertEqual(status, 200)
        self.assertTrue(any("keys" in l.lower() for l in self.logs))

    def test_fixed_file_recovers_without_restart(self):
        self.replace_keys({GROUP_A: KEY_A})
        self.assertEqual(self.recall(GROUP_A)[0], 200)


class TenantIsolationTests(TenantTestCase):
    """Alternating tenants after both stores are warm: every backend request carries the right key."""

    def _keys_seen(self):
        return {c[0] for c in self.ov.calls}

    def test_alternating_recalls_use_each_tenants_key_and_content(self):
        self.ov.memories[KEY_A] = [mem("viking://~/memories/events/2026/11/03/a.md", 0.9, "only a")]
        self.ov.memories[KEY_B] = [mem("viking://~/memories/events/2026/11/03/b.md", 0.9, "only b")]
        self.recall(GROUP_A)
        self.recall(GROUP_B)  # both stores warm
        for tenant, key, other, text in ((GROUP_A, KEY_A, KEY_B, "only a"), (GROUP_B, KEY_B, KEY_A, "only b")) * 3:
            self.ov.calls.clear()
            status, body = self.recall(tenant)
            self.assertEqual(status, 200)
            self.assertEqual([r["text"] for r in body["results"]], [text])
            self.assertEqual(self._keys_seen(), {key})
            self.assertNotIn(other, self._keys_seen())

    def test_alternating_remembers_use_each_tenants_key_and_content(self):
        self.remember(GROUP_A, "warm a")
        self.remember(GROUP_B, "warm b")
        for tenant, key, text in ((GROUP_A, KEY_A, "from a"), (GROUP_B, KEY_B, "from b")) * 3:
            self.ov.calls.clear()
            self.ov.bodies.clear()
            status, _ = self.remember(tenant, text)
            self.assertEqual(status, 200)
            self.assertEqual(self._keys_seen(), {key})
            self.assertEqual([b["content"] for b in self.ov.bodies if "content" in b], [text])

    def test_key_rotation_uses_new_key_only(self):
        self.recall(GROUP_A)
        self.remember(GROUP_A, "x")
        self.replace_keys({GROUP_A: "grp-key-a2-fake", GROUP_B: KEY_B})
        self.ov.calls.clear()
        self.assertEqual(self.recall(GROUP_A)[0], 200)
        self.assertEqual(self.remember(GROUP_A, "y")[0], 200)
        self.assertEqual(self._keys_seen(), {"grp-key-a2-fake"})

    def test_tenant_fallback_reads_use_the_owning_key(self):
        self.ov.memories[KEY_A] = [mem("viking://~/memories/events/2026/11/03/a.md", 0.8, "")]
        self.ov.memories[KEY_B] = [mem("viking://~/memories/events/2026/11/03/b.md", 0.8, "")]
        self.ov.contents["viking://~/memories/events/2026/11/03/a.md"] = "a body " + PHONE
        self.ov.contents["viking://~/memories/events/2026/11/03/b.md"] = "b body"
        status, body = self.recall(GROUP_A)
        self.assertEqual(sorted(r["text"] for r in body["results"]), ["a body [REDACTED]"])
        reads = [c for c in self.ov.calls if c[2].startswith("/api/v1/content/read")]
        self.assertEqual(sorted(c[0] for c in reads), [KEY_A])


class TenantKeysFileTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.kclock = Clock()
        self.keys._clock = self.kclock

    def test_io_failure_is_retried_not_cached_and_throttled(self):
        self.replace_keys({GROUP_A: KEY_A})
        reads = []

        def boom(_self):
            reads.append(1)
            raise PermissionError("denied")

        with mock.patch.object(KeyFileTenants, "_read_bytes", boom):
            self.assertEqual(self.recall(GROUP_B)[0], 200)  # last valid kept
            self.assertEqual(self.recall(GROUP_B)[0], 200)
            self.assertEqual(len(reads), 1)  # throttled to one attempt
            self.assertTrue(any("PermissionError" in l for l in self.logs))
        # permissions repaired: same mtime, size and inode; the next attempt after the interval must load it
        self.assertEqual(self.recall(GROUP_B)[0], 200)  # still inside the throttle window
        self.kclock.t += keyfile.RETRY_SECONDS + 1
        self.assertEqual(self.recall(GROUP_B)[0], 403)
        self.assertEqual(self.recall(GROUP_A)[0], 200)
        joined = "\n".join(self.logs)
        for secret in (KEY_A, KEY_B):
            self.assertNotIn(secret, joined)

    def test_signature_includes_mode(self):
        before = self.keys._stat()
        os.chmod(self.keys_path, 0o444)
        self.addCleanup(os.chmod, self.keys_path, 0o666)
        self.assertNotEqual(before, self.keys._stat())

    def test_oversize_file_is_corrupt_and_keeps_last_good(self):
        big = json.dumps({GROUP_A: KEY_A, "30003": "grp-key-c-fake"}) + " " * (keyfile.MAX_KEYS_BYTES + 10)
        self.replace_keys(big)
        self.assertEqual(self.recall("30003")[0], 403)
        self.assertEqual(self.recall(GROUP_B)[0], 200)
        self.assertTrue(any("too large" in l for l in self.logs))

    def test_oversize_read_is_bounded(self):
        # Even if the size check were skipped, the read never takes more than the bound plus one byte.
        self.replace_keys("x" * (keyfile.MAX_KEYS_BYTES + 5))
        raw = self.keys._read_bytes()
        self.assertEqual(len(raw), keyfile.MAX_KEYS_BYTES + 1)
        with self.assertRaises(ValueError):
            self.keys._parse(raw)

    def test_the_id_pattern_decides_which_ids_are_valid(self):
        provider = KeyFileTenants(
            str(self.keys_path), {"type": "openviking", "url": self.ov.url, "floor": 0, "ceil": 1}, id_pattern=r"[a-z]{3,8}",
            log=self.logs.append,
        )
        for good in ("abc", "guildone"):
            self.assertTrue(provider.valid_id(good), good)
        for bad in ("ab", "toolongname", "ABC", "abc\n", "", None, 123):
            self.assertFalse(provider.valid_id(bad), repr(bad))
        # A registered key does not help an id the pattern refuses.
        self.assertIsNone(provider.store(GROUP_A))


class TenantOversizeStartTests(TenantTestCase):
    KEYS_TEXT = json.dumps({GROUP_A: KEY_A}) + " " * (keyfile.MAX_KEYS_BYTES + 10)

    def test_oversize_at_start_denies_tenants_only(self):
        self.assertEqual(self.recall(GROUP_A)[0], 403)
        self.assertEqual(self.request("/v1/recall", {"query": "hello"}, token=TOKEN_OWNER)[0], 200)


class TenantPoolFairnessTests(TenantTestCase):
    TENANTS = {"10001": "k1-fake", "10002": "k2-fake", "10003": "k3-fake", "10004": "k4-fake"}
    KEYS_TEXT = json.dumps(TENANTS)

    def setUp(self):
        super().setUp()
        for key in self.TENANTS.values():
            self.ov.delay[key] = 0.8
        self.ov.memories[CHAN_KEY] = [mem("viking://~/memories/events/2026/11/03/c.md", 0.9, "chan fact")]
        self.peak = 0
        self._sampling = True
        t = threading.Thread(target=self._sample, daemon=True)
        t.start()
        self.addCleanup(self._stop_sampling, t)

    def _stop_sampling(self, t):
        self._sampling = False
        t.join(2)

    def _sample(self):
        while self._sampling:
            self.peak = max(self.peak, self.gw.pool.in_flight)
            time.sleep(0.005)

    def _fire(self, tenants):
        out = []
        threads = []

        def go(g):
            out.append(self.remember(g, "hi")[0])

        for g in tenants:
            t = threading.Thread(target=go, args=(g,))
            threads.append(t)
            t.start()
        return threads, out

    def test_eight_tenant_requests_leave_the_owner_working_and_cap_tenant_workers(self):
        threads, out = self._fire(list(self.TENANTS) * 2)
        time.sleep(0.3)  # all eight are in flight against the slow backend
        tenant_peak = self.peak  # before any owner request adds its own workers
        started = time.monotonic()
        status, body = self.request("/v1/recall", {"query": "hello"}, token=TOKEN_OWNER)
        self.assertEqual(status, 200)
        self.assertEqual(sorted(r["text"] for r in body["results"]), ["chan fact"])
        status, _ = self.request("/v1/remember", {"messages": [{"role": "user", "content": "own"}]}, token=TOKEN_OWNER)
        self.assertEqual(status, 200)
        self.assertLess(time.monotonic() - started, 0.7)
        for t in threads:
            t.join(15)
        self.assertLessEqual(tenant_peak, TENANT_POOL_TOTAL)
        self.assertEqual(out.count(200), TENANT_POOL_TOTAL)
        self.assertEqual(out.count(503), 8 - TENANT_POOL_TOTAL)  # busy: refused up front, nothing written

    def test_one_tenant_cannot_take_all_tenant_workers(self):
        threads, out = self._fire(["10001"] * 4)
        for t in threads:
            t.join(15)
        self.assertLessEqual(self.peak, TENANT_POOL_PER_TENANT)
        self.assertEqual(out.count(200), TENANT_POOL_PER_TENANT)
        self.assertEqual(out.count(503), 4 - TENANT_POOL_PER_TENANT)

    def test_over_budget_recall_is_busy_not_failed(self):
        threads, out = self._fire(["10001"] * 2)
        time.sleep(0.3)
        status, body = self.request("/v1/recall", {"query": "hello", "tenant_id": "10001"})
        self.assertEqual((status, body), (503, {"error": "busy", "code": "busy"}))
        for t in threads:
            t.join(15)
        self.assertEqual((self.gw.recall_failures.count(), self.gw.recall_busy.count()), (0, 1))

    def test_two_concurrent_recalls_of_one_tenant_both_search_every_store(self):
        self.gw.clients["group"] = dataclasses.replace(GROUP, recall=("channel", "@tenant"))
        for key in self.TENANTS.values():
            self.ov.delay[key] = 0.3
        self.ov.memories["k1-fake"] = [mem("viking://~/memories/events/2026/11/03/t.md", 0.9, "tenant fact")]
        results = []

        def go():
            results.append(self.request("/v1/recall", {"query": "hello", "tenant_id": "10001"}))

        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(len(results), 2)
        for status, body in results:
            self.assertEqual(status, 200)
            self.assertEqual((body["failed"], body["busy"]), (0, 0))
            self.assertEqual(sorted(r["text"] for r in body["results"]), ["chan fact", "tenant fact"])
        self.assertEqual(self.gw.recall_failures.count(), 0)


class TenantBudgetTests(unittest.TestCase):
    """The tenant budget counts requests in flight, keyed "<provider>:<tenant id>"."""

    def test_the_budget_is_shared_per_key_and_total(self):
        budget = limits.TenantBudget(total=2, per_tenant=1)
        self.assertTrue(budget.acquire("group:10001"))
        self.assertFalse(budget.acquire("group:10001"))  # per tenant
        self.assertTrue(budget.acquire("group:20002"))
        self.assertFalse(budget.acquire("group:30003"))  # total
        budget.release("group:10001")
        self.assertTrue(budget.acquire("group:30003"))

    def test_a_failed_request_gives_its_slot_back(self):
        gw = gateway.Gateway({}, {"group": GROUP}, {"group": TOKEN_GROUP}, tenants={"group": _Always(raises=True)},
                             tenant_pool_total=1, tenant_pool_per_tenant=1, log=lambda line: None)
        self.addCleanup(gw.close)
        for _ in range(3):
            reply = gw.remember("group", {"messages": [{"role": "user", "content": "x"}], "tenant_id": "1"})
            self.assertEqual(reply.status, 502)
        self.assertTrue(gw.tenant_budget.acquire("group:1"))


class _Always:
    """A tenants provider whose every tenant has one store that may raise on write."""

    def __init__(self, raises=False):
        self._store = _Store(raises)

    def valid_id(self, tenant_id):
        return tenant_id.isdigit()

    def store(self, tenant_id):
        return self._store

    def close(self):
        pass


class _Store:
    writable = True

    def __init__(self, raises):
        self.raises = raises

    def find(self, query, limit, timeout=None, ctl=None):
        return []

    def remember(self, messages, timeout=None, ctl=None):
        if self.raises:
            raise RuntimeError("down")
        return "w-1"


class _NoParse:
    """Stands in for the json module inside keyfile only (the HTTP server keeps the real one)."""

    @staticmethod
    def loads(*args, **kwargs):
        raise AssertionError("parse started")


class TenantKeysSizeGuardTests(TenantTestCase):
    def _big_valid(self):
        return (json.dumps({GROUP_A: KEY_A, "30003": "grp-key-c-fake"}) + " " * (keyfile.MAX_KEYS_BYTES + 10)).encode()

    def test_parse_refuses_oversize_before_json_starts(self):
        raw = self._big_valid()
        with mock.patch.object(keyfile, "json", _NoParse()):
            with self.assertRaises(ValueError):
                self.keys._parse(raw)

    def test_oversize_valid_file_never_reaches_json(self):
        with mock.patch.object(keyfile, "json", _NoParse()):
            self.replace_keys(self._big_valid().decode())
            self.assertEqual(self.recall("30003")[0], 403)
            self.assertEqual(self.recall(GROUP_B)[0], 200)


class TenantPolicyChecksTests(unittest.TestCase):
    """A policy that could let a client reach a store it should not is refused when the gateway is built."""

    def build(self, policy, tenants=None):
        stores = {"channel": OpenVikingBackend("http://127.0.0.1:9", CHAN_KEY, writable=True, floor=0, ceil=1)}
        return gateway.Gateway(stores, {"c": policy}, {"c": TOKEN_OWNER}, tenants=tenants)

    def test_tenants_must_name_a_known_provider(self):
        with self.assertRaises(ConfigError):
            self.build(ClientPolicy("c", recall=(), tenants="group"))

    def test_tenant_store_write_needs_tenants(self):
        with self.assertRaises(ConfigError):
            self.build(ClientPolicy("c", recall=(), write="@tenant"))

    def test_the_tenant_store_in_recall_needs_tenants(self):
        with self.assertRaises(ConfigError) as ctx:
            self.build(ClientPolicy("c", recall=("@tenant",)))
        self.assertIn("@tenant", str(ctx.exception))

    def test_a_tenants_provider_cannot_share_a_name_with_a_store(self):
        with self.assertRaises(ConfigError):
            self.build(ClientPolicy("c", recall=()), tenants={"channel": _Always()})

    def test_recall_and_write_must_name_known_stores(self):
        with self.assertRaises(ConfigError):
            self.build(ClientPolicy("c", recall=("nowhere",)))
        with self.assertRaises(ConfigError):
            self.build(ClientPolicy("c", recall=(), write="nowhere"))


if __name__ == "__main__":
    unittest.main()
