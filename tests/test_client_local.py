"""LocalClient against MemoryClient: one set of assertions, two transports.

Contract tests run the same checks through MemoryClient over a real loopback HTTP server and
through LocalClient in process, both on a gateway built from the same configuration. The
local-only tests cover what has no HTTP counterpart: construction, lifecycle, timeouts, the
example."""
import contextlib
import importlib.util
import io
import logging
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from feiyu_memgate import config
from feiyu_memgate.backends.base import MemoryBackend
from feiyu_memgate.client import LocalClient, MemoryClient, format_block
from feiyu_memgate.errors import BackendError, ConfigError
from feiyu_memgate.gateway import server as gateway_server
from feiyu_memgate.registry import register

ROOT = Path(__file__).resolve().parent.parent
SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
TOKENS = {"GATEWAY_TOKEN_OWNER": "o" * 40, "GATEWAY_TOKEN_READER": "r" * 40, "GATEWAY_TOKEN_TIGHT": "t" * 40,
          "GATEWAY_TOKEN_FLAKY": "f" * 40, "GATEWAY_TOKEN_GROUP": "g" * 40}
NAMES = ("owner", "reader", "tight", "flaky", "group")


@register("backend", "contract_failing")
class FailingStore(MemoryBackend):
    """A store that is down: every search and write fails."""
    writable = True

    def __init__(self):
        pass

    def find(self, query, limit, timeout=None, ctl=None):
        raise BackendError("down")

    def remember(self, messages, timeout=None, ctl=None):
        raise BackendError("down")


@register("backend", "contract_slow")
class SlowStore(MemoryBackend):
    """A store that answers after a second."""
    writable = True

    def __init__(self):
        pass

    def find(self, query, limit, timeout=None, ctl=None):
        time.sleep(1.0)
        return []

    def remember(self, messages, timeout=None, ctl=None):
        time.sleep(1.0)
        return "late"


def make_config(tmp):
    return {
        "stores": {"main": {"type": "inmemory"}, "broken": {"type": "contract_failing"}},
        "tenants": {"groups": {"type": "directory", "path": os.path.join(tmp, "groups"), "id_pattern": "[0-9]{5}",
                               "create": False}},
        "clients": {
            "owner": {"token_env": "GATEWAY_TOKEN_OWNER", "recall": ["main"], "write": "main", "daily_writes": 3,
                      "rate_limit": 1000},
            "reader": {"token_env": "GATEWAY_TOKEN_READER", "recall": ["main"], "rate_limit": 1000},
            "tight": {"token_env": "GATEWAY_TOKEN_TIGHT", "recall": ["main"], "write": "main", "rate_limit": 3},
            "flaky": {"token_env": "GATEWAY_TOKEN_FLAKY", "recall": ["broken"], "write": "broken", "rate_limit": 1000},
            "group": {"token_env": "GATEWAY_TOKEN_GROUP", "tenants": "groups", "recall": ["@tenant"],
                      "write": "@tenant", "daily_writes": 5, "rate_limit": 1000, "public": True},
        },
        "recall": {"stages": ["min_score", "redact", "record_audience", "dedupe", "budget"]},
        "write": {"guards": ["redact", "daily_cap"]},
        "forgetting": {"ledger": os.path.join(tmp, "ledger.db"), "archive": os.path.join(tmp, "archive.db"),
                       "stores": ["main"], "sweep_every_hours": 24},
    }


class Contract:
    """The assertions. A subclass sets up self.gateway and implements make(name, tenant_name=None)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.makedirs(os.path.join(self.tmp, "groups"))
        for tenant in ("11111", "22222"):  # create=false: only these tenants exist
            self.gateway_tenant_files = open(os.path.join(self.tmp, "groups", tenant + ".db"), "wb").close()
        logging.getLogger("feiyu_memgate.client").addHandler(logging.NullHandler())
        self.cfg = make_config(self.tmp)

    def stored(self, source="main"):
        return [text for text, _ in self.gateway.stores[source]._items.values()]

    def test_remember_then_recall(self):
        owner = self.make("owner")
        self.assertTrue(owner.configured)
        self.assertEqual(owner.remember_status("I like oolong tea in the evening"), "saved")
        detail = owner.recall_detail("oolong tea")
        self.assertEqual(set(detail), {"results", "more", "failed", "busy"})
        self.assertEqual((detail["more"], detail["failed"], detail["busy"]), (0, 0, 0))
        self.assertEqual(len(detail["results"]), 1)
        item = detail["results"][0]
        self.assertEqual(set(item) - {"date"}, {"id", "source", "text", "score"})
        self.assertEqual(item["source"], "main")
        self.assertIn("oolong", item["text"])
        self.assertEqual(owner.recall("oolong tea"), detail["results"])
        self.assertTrue(owner.remember("I also like jasmine tea"))
        self.assertIn("oolong", format_block(owner.recall("oolong tea")))

    def test_quiet_recall_is_empty_not_failed(self):
        owner = self.make("owner")
        owner.remember_status("I like oolong tea in the evening")
        detail = owner.recall_detail("quantum chromodynamics lecture")
        self.assertEqual(detail, {"results": [], "more": 0, "failed": 0, "busy": 0})
        self.assertEqual(owner.recall("quantum chromodynamics lecture"), [])

    def test_write_over_the_daily_cap_is_limited(self):
        owner = self.make("owner")
        words = ["alpha river", "bravo mountain", "charlie desert"]
        self.assertEqual([owner.remember_status("I visited the %s" % w) for w in words], ["saved"] * 3)
        self.assertEqual(owner.remember_status("I visited the delta coast"), "limited")
        self.assertFalse(owner.remember("I visited the echo island"))
        self.assertEqual(len(self.stored()), 3)
        self.assertEqual(owner.remember_messages([{"role": "user", "content": "foxtrot lake"}]), "limited")
        self.assertTrue(owner.recall("alpha river"))  # reading is not capped

    def test_a_client_without_write_permission_fails_for_good(self):
        reader = self.make("reader")
        self.assertEqual(reader.remember_status("I like oolong tea"), "failed")
        self.assertFalse(reader.remember("I like oolong tea"))
        self.assertEqual(self.stored(), [])
        self.make("owner").remember_status("I like oolong tea in the evening")
        self.assertEqual(len(reader.recall("oolong tea")), 1)

    def test_a_failing_store_is_failed_not_empty(self):
        flaky = self.make("flaky")
        self.assertIsNone(flaky.recall_detail("oolong tea"))
        self.assertEqual(flaky.recall("oolong tea"), [])
        self.assertEqual(flaky.remember_status("I like oolong tea"), "unsure")  # 502 is not a definite code: the write may have reached the store

    def test_rate_limit(self):
        tight = self.make("tight")
        self.assertEqual([tight.recall_detail("oolong tea") is not None for _ in range(3)], [True] * 3)
        self.assertIsNone(tight.recall_detail("oolong tea"))
        self.assertEqual(tight.remember_status("I like oolong tea"), "failed")  # 429 is a definite refusal
        self.assertFalse(tight.report_used([{"source": "main", "id": "x"}]))

    def test_tenants(self):
        group = self.make("group", "group")
        self.assertTrue(group.tenant_configured)
        self.assertFalse(self.make("owner").tenant_configured)
        self.assertEqual(group.remember_status("the garden club meets on friday", tenant_id="11111"), "saved")
        self.assertEqual(len(group.recall("garden club", tenant_id="11111")), 1)
        self.assertEqual(group.recall("garden club", tenant_id="22222"), [])   # another tenant: nothing
        self.assertEqual(group.remember_status("x tenant", tenant_id="33333"), "failed")  # unknown tenant
        self.assertIsNone(group.recall_detail("garden club", tenant_id="abc"))             # bad id
        self.assertIsNone(group.recall_detail("garden club", tenant_id=" "))
        self.assertEqual(group.recall("garden club"), [])  # no tenant_id: the main policy is not set
        owner = self.make("owner")
        self.assertEqual(owner.recall("garden club", tenant_id="11111"), [])  # no tenant token / policy

    def test_report_used_with_forgetting(self):
        owner = self.make("owner")
        owner.remember_status("I like oolong tea in the evening")
        results = owner.recall("oolong tea")
        self.assertTrue(results)
        self.assertTrue(owner.report_used(results))
        ledger = self.gateway.forgetting.ledger
        self.assertIn(results[0]["id"], ledger.get_many("main", [results[0]["id"]]))
        self.assertTrue(owner.report_used([{"source": "main", "id": "made-up"}]))  # accepted, records nothing
        self.assertNotIn("made-up", ledger.get_many("main", ["made-up"]))
        self.assertFalse(owner.report_used([{"source": "elsewhere", "id": "x"}]))  # a store it may not read
        self.assertFalse(owner.report_used([]))
        self.assertFalse(owner.report_used([{"source": "main"}]))
        self.assertFalse(owner.report_used([{"source": "main", "id": "x" * 600}]))  # the gateway's own limit

    def test_redaction(self):
        owner = self.make("owner")
        self.assertEqual(owner.remember_status("my api key is %s remember it" % SECRET), "saved")
        self.assertFalse(any(SECRET in text for text in self.stored()))  # the write guard cleaned it
        self.assertTrue(any("[REDACTED]" in text for text in self.stored()))
        self.assertFalse(any(SECRET in r["text"] for r in owner.recall("my api key is")))
        group = self.make("group", "group")  # a public client: the public redactor
        group.remember_status("call me on 13812345678 anytime", tenant_id="11111")
        texts = [r["text"] for r in group.recall("call me anytime", tenant_id="11111")]
        self.assertTrue(texts)
        self.assertFalse(any("13812345678" in t for t in texts))

    def test_nothing_raises(self):
        owner = self.make("owner")
        for query in (None, "", "   ", 5, [], "q" * 501):
            self.assertIsNone(owner.recall_detail(query))
            self.assertEqual(owner.recall(query), [])
        for messages in (None, "x", [], [{}], [{"role": "system", "content": "x"}],
                         [{"role": "user", "content": "x" * 4001}], [{"role": "user", "content": "x"}] * 21,
                         [{"role": "user", "content": "x", "time": "t" * 41}]):
            self.assertEqual(owner.remember_messages(messages), "failed")
        for text in (None, "", "  ", 3):
            self.assertEqual(owner.remember_status(text), "failed")
            self.assertFalse(owner.remember(text))
        for items in (None, "x", [None], [1], [{"source": 1, "id": "a"}]):
            self.assertFalse(owner.report_used(items))
        for tenant in (True, "", " ", 1.5, object()):
            self.assertEqual(owner.recall("oolong", tenant_id=tenant), [])
            self.assertEqual(owner.remember_status("oolong", tenant_id=tenant), "failed")
        self.assertEqual(owner.remember_status("x", deadline=time.monotonic() - 1), "failed")  # deadline passed

    def test_messages_with_time_and_speaker(self):
        owner = self.make("owner")
        status = owner.remember_messages([{"role": "user", "content": "we met on the pier", "time": "2026-01-02",
                                           "speaker": "Ann"}, {"role": "assistant", "content": "noted"}])
        self.assertIn(status, ("saved", "skipped"))


class HttpContract(Contract, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.gateway = config.build_gateway(self.cfg, env=TOKENS, log=lambda line: None)
        self.server = gateway_server.make_server(self.gateway, "127.0.0.1", 0)
        self.gateway.start()
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(self._stop, thread)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def _stop(self, thread):
        self.server.shutdown()
        thread.join(5)
        self.server.server_close()  # closes the gateway

    def make(self, name, tenant_name=None):
        token = lambda n: TOKENS["GATEWAY_TOKEN_" + n.upper()]  # noqa: E731
        if tenant_name is None:
            return MemoryClient(self.url, token(name))
        return MemoryClient(self.url, token("owner"), tenant_token=token(tenant_name))


class LocalContract(Contract, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # No token in the environment at all: an embedded setup needs none.
        self.base = LocalClient.from_config(self.cfg, client="owner", env={}, log=lambda line: None)
        self.addCleanup(self.base.close)
        self.gateway = self.base.gateway

    def make(self, name, tenant_name=None):
        if tenant_name is None:
            return self.base.for_client(name)
        return self.base.for_client("owner", tenant_name)


class LocalOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.makedirs(os.path.join(self.tmp, "groups"))
        self.cfg = make_config(self.tmp)
        logging.getLogger("feiyu_memgate.client").addHandler(logging.NullHandler())

    def build(self, **options):
        client = LocalClient.from_config(self.cfg, client="owner", env={}, log=lambda line: None, **options)
        self.addCleanup(client.close)
        return client

    def test_unknown_client_is_an_error_at_construction(self):
        with self.assertRaises(ConfigError) as caught:
            LocalClient.from_config(self.cfg, client="nobody", env={}, log=lambda line: None)
        self.assertIn("nobody", str(caught.exception))
        gateway = self.build().gateway
        with self.assertRaises(ConfigError):
            LocalClient(gateway, client="owner", tenant_client="nope")
        with self.assertRaises(ConfigError):
            LocalClient("not a gateway", client="owner")
        with self.assertRaises(ConfigError):
            self.build().for_client("nobody")

    def test_a_failed_build_leaves_no_gateway_behind(self):
        self.cfg["clients"]["owner"]["write"] = "nonexistent"
        with self.assertRaises(ConfigError):
            self.build()

    def test_a_token_bound_tenant_client_works_in_process(self):
        # Over HTTP the tenant comes from the minted token; in process the caller names it.
        for tenant in ("11111", "22222"):
            open(os.path.join(self.tmp, "groups", tenant + ".db"), "wb").close()
        self.cfg["clients"]["group"]["tenant_binding"] = "token"
        client = LocalClient.from_config(self.cfg, client="owner", tenant_client="group",
                                         env={"GATEWAY_TOKEN_GROUP": "fake-minting-secret-" + "x" * 32},
                                         log=lambda line: None)
        self.addCleanup(client.close)
        self.assertTrue(client.gateway.clients["group"].token_bound)
        self.assertEqual(client.remember_status("the garden club meets on friday", tenant_id="11111"), "saved")
        self.assertEqual(len(client.recall("garden club", tenant_id="11111")), 1)
        self.assertEqual(client.recall("garden club", tenant_id="22222"), [])
        self.assertEqual(client.remember_status("x tenant", tenant_id="33333"), "failed")  # unknown tenant

    def test_no_token_env_is_needed(self):
        for spec in self.cfg["clients"].values():
            del spec["token_env"]
        client = self.build()
        self.assertEqual(client.remember_status("I like oolong tea in the evening"), "saved")
        self.assertEqual(len(client.recall("oolong tea")), 1)

    def test_the_missing_token_note_is_not_logged_but_other_lines_are(self):
        lines = []
        client = LocalClient.from_config(self.cfg, client="owner", env={}, log=lines.append)
        self.addCleanup(client.close)
        client.remember_status("I like oolong tea")
        self.assertFalse([line for line in lines if "has no token" in line])
        self.assertTrue([line for line in lines if line.startswith("LOCAL /v1/remember 200 client=owner")])
        self.assertTrue(all("oolong" not in line and "sk-" not in line for line in lines))

    def test_a_token_in_the_environment_is_harmless(self):
        client = LocalClient.from_config(self.cfg, client="owner", env=TOKENS, log=lambda line: None)
        self.addCleanup(client.close)
        self.assertEqual(client.remember_status("I like oolong tea in the evening"), "saved")

    def test_lifecycle_and_the_sweep_thread(self):
        with self.build() as client:
            gateway = client.gateway
            thread = gateway._sweep_thread
            self.assertIsNotNone(thread)
            self.assertTrue(thread.is_alive())
            self.assertTrue(client.configured)
        self.assertFalse(thread.is_alive())
        self.assertFalse(client.configured)
        self.assertEqual(client.recall("oolong tea"), [])  # after close: soft failure
        self.assertEqual(client.remember_status("oolong tea"), "failed")
        self.assertIsNone(client.health())
        client.close()  # twice is fine

    def test_a_sibling_does_not_own_the_gateway(self):
        owner = self.build()
        group = owner.for_client("reader")
        group.close()
        self.assertFalse(owner.gateway._closed)
        self.assertEqual(owner.remember_status("I like oolong tea in the evening"), "saved")
        self.assertEqual(len(owner.for_client("reader").recall("oolong tea")), 1)
        self.assertFalse(group.configured)

    def test_a_gateway_you_built_is_not_closed_by_the_client(self):
        gateway = config.build_gateway(self.cfg, env={}, log=lambda line: None)
        self.addCleanup(gateway.close)
        client = LocalClient(gateway, client="owner")
        self.assertTrue(client.remember("I like oolong tea in the evening"))
        client.close()
        self.assertFalse(gateway._closed)
        self.assertEqual(len(LocalClient(gateway, client="reader").recall("oolong tea")), 1)

    def test_health(self):
        client = self.build()
        self.assertEqual(client.health(), {"status": "ok"})
        deep = client.health(deep=True)
        self.assertEqual(deep["stores"]["main"], "ok")
        self.assertEqual(deep["stores"]["broken"], "ok")  # the failing store answers health()

    def test_threads(self):
        client = self.build()
        errors = []

        def work(n):
            try:
                for i in range(5):
                    client.remember_status("note %d %d about harbor lights" % (n, i))
                    client.recall("harbor lights")
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.assertEqual(errors, [])

    def test_timeouts_cut_the_wait_as_a_closed_socket_would(self):
        self.cfg["stores"]["slow"] = {"type": "contract_slow"}
        self.cfg["clients"]["owner"]["recall"] = ["slow"]
        self.cfg["clients"]["owner"]["write"] = "slow"
        client = self.build()
        started = time.monotonic()
        self.assertIsNone(client.recall_detail("oolong tea", timeout=0.2))
        self.assertLess(time.monotonic() - started, 0.8)
        started = time.monotonic()
        self.assertEqual(client.remember_status("oolong tea", deadline=time.monotonic() + 0.2), "unsure")
        self.assertLess(time.monotonic() - started, 0.8)

    def test_an_off_client(self):
        off = LocalClient(None)
        self.assertFalse(off.configured)
        self.assertFalse(off.tenant_configured)
        self.assertEqual(off.recall("x"), [])
        self.assertIsNone(off.recall_detail("x"))
        self.assertEqual(off.remember_status("x"), "failed")
        self.assertFalse(off.report_used([{"source": "a", "id": "b"}]))
        self.assertIsNone(off.health())
        off.close()
        with self.assertRaises(ConfigError):
            LocalClient(None, client="owner")
        with self.assertRaises(ConfigError):
            off.for_client("owner")

    def test_from_env(self):
        path = os.path.join(self.tmp, "gateway.toml")
        Path(path).write_text('[stores.main]\ntype = "inmemory"\n[clients.owner]\nrecall = ["main"]\nwrite = "main"\n',
                              encoding="utf-8")
        self.assertFalse(LocalClient.from_env({}).configured)
        client = LocalClient.from_env({"FM_CONFIG": path, "FM_CLIENT": "owner"}, prefix="FM", log=lambda line: None)
        self.addCleanup(client.close)
        self.assertTrue(client.configured)
        self.assertFalse(client.tenant_configured)
        self.assertEqual(client.remember_status("I like oolong tea in the evening"), "saved")
        with self.assertRaises(ConfigError):
            LocalClient.from_env({"FM_CONFIG": path, "FM_CLIENT": "nobody"}, prefix="FM", log=lambda line: None)
        with self.assertRaises(ConfigError):
            LocalClient.from_env({"FM_CONFIG": os.path.join(self.tmp, "missing.toml")}, prefix="FM")

    def test_same_interface_as_the_http_client(self):
        for name in ("configured", "tenant_configured", "recall_detail", "recall", "remember_messages",
                     "remember_status", "remember", "report_used", "from_env"):
            self.assertTrue(hasattr(LocalClient, name), name)
        self.assertTrue(issubclass(LocalClient, MemoryClient))


class EmbeddedExample(unittest.TestCase):
    def test_the_bot_runs_in_process(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        folder = os.path.join(tmp, "embedded")
        shutil.copytree(ROOT / "examples" / "embedded", folder)
        saved = os.getcwd()
        os.chdir(folder)
        self.addCleanup(os.chdir, saved)
        os.makedirs("data")
        spec = importlib.util.spec_from_file_location("embedded_bot_example", os.path.join(folder, "bot.py"))
        bot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bot)
        with contextlib.redirect_stderr(io.StringIO()):  # the gateway's access log
            memory = bot.make_memory({})
            self.assertIsInstance(memory, LocalClient)
            try:
                self.assertIn("Nothing", bot.reply_to(memory, "I like oolong tea"))
                self.assertIn("oolong", bot.reply_to(memory, "which tea do I like, oolong?"))
            finally:
                memory.close()
        http = bot.make_memory({"FEIYU_MEMGATE_URL": "http://127.0.0.1:9", "FEIYU_MEMGATE_TOKEN": "x"})
        self.assertIsInstance(http, MemoryClient)
        self.assertNotIsInstance(http, LocalClient)


if __name__ == "__main__":
    unittest.main()
