"""Forgetting in the gateway and the configuration: pin_writes, the sweep, the [forgetting] section,
and one run from write to restore through config.build_gateway with a fake clock."""
import os
import sqlite3
import tempfile
import threading
import unittest

from feiyu_memgate import config, registry
from feiyu_memgate.core.watch import ExtractionWatch
from feiyu_memgate.errors import ConfigError
from feiyu_memgate.forgetting.archive import Archive
from feiyu_memgate.forgetting.curve import Ebbinghaus
from feiyu_memgate.forgetting.engine import Forgetting
from feiyu_memgate.forgetting.ledger import Ledger
from feiyu_memgate.forgetting.stage import Freshness
from feiyu_memgate.gateway.app import Gateway
from feiyu_memgate.model import ClientPolicy
from feiyu_memgate.tenants.base import TenantProvider

from feiyu_memgate.forgetting.engine import date_to_epoch
from .test_forgetting_engine import DAY, T0, Clock, FakeStore, PlainStore, date_of

TOKEN = "tok-owner-fake-".ljust(40, "0")
ENV = {"TOKEN_OWNER": TOKEN}
MESSAGE = {"messages": [{"role": "user", "content": "I like green tea"}]}

registry.register("backend", "test_forgettable")(FakeStore)
registry.register("backend", "test_plain")(PlainStore)


class FakeTenants(TenantProvider):
    def __init__(self, tracks=False, listing=True):
        self.stores = {}
        self.tracks = tracks
        if not listing:
            self.list = None  # a provider that cannot list its tenants

    def valid_id(self, tenant_id):
        return tenant_id.isdigit()

    def store(self, tenant_id):
        if tenant_id not in self.stores:
            self.stores[tenant_id] = FakeStore(tracks=self.tracks)
        return self.stores[tenant_id]

    def list(self):
        return sorted(self.stores)


class GatewayCase(unittest.TestCase):
    def setUp(self):
        self.wall = Clock()
        self.clock = Clock(1000.0)
        self.logs = []
        self.engine = Forgetting(Ledger(":memory:"), Archive(":memory:"), Ebbinghaus(half_life_days=60),
                                 stores=("private", "group"), max_forget_fraction=1.0, wall=self.wall)
        self.addCleanup(self.engine.close)

    def manage(self, key, store):
        """Make the ledger know the memories of `store` as managed since each memory's own date."""
        for ref, date in store.refs():
            epoch = date_to_epoch(date)
            self.engine.ledger.touch(key, [(ref, epoch)], epoch)

    def gateway(self, stores, clients, tenants=None, forgetting="engine", **kw):
        kw.setdefault("sweep_every_hours", 0)
        engine = self.engine if forgetting == "engine" else forgetting
        gw = Gateway(stores, clients, {}, tenants=tenants, forgetting=engine, clock=self.clock,
                     log=self.logs.append, **kw)
        self.addCleanup(gw.close)
        return gw

    def pinned(self, key):
        return {e.ref for e in self.engine.ledger.entries(key) if e.pinned}


class PinWritesTests(GatewayCase):
    OWNER = ClientPolicy("owner", recall=("private",), write="private", pin_writes=True)
    PLAIN = ClientPolicy("plain", recall=("private",), write="private")

    def test_pins_at_once_when_the_store_can_name_the_memories(self):
        store = FakeStore()
        gw = self.gateway({"private": store}, {"owner": self.OWNER})
        reply = gw.remember("owner", MESSAGE)
        self.assertEqual(reply.status, 200)
        self.assertEqual(self.pinned("private"), set(store.items))
        self.assertEqual(len(store.items), 1)

    def test_a_client_without_pin_writes_pins_nothing(self):
        store = FakeStore()
        gw = self.gateway({"private": store}, {"plain": self.PLAIN})
        gw.remember("plain", MESSAGE)
        self.assertEqual(self.engine.ledger.entries("private"), [])

    def test_an_unmanaged_store_is_not_pinned(self):
        store = FakeStore()
        client = ClientPolicy("owner", recall=("other",), write="other", pin_writes=True)
        gw = self.gateway({"other": store}, {"owner": client})
        self.assertEqual(gw.remember("owner", MESSAGE).status, 200)
        self.assertEqual(self.engine.ledger.stores(), [])

    def test_without_forgetting_pin_writes_is_harmless(self):
        gw = self.gateway({"private": FakeStore()}, {"owner": self.OWNER}, forgetting=None)
        self.assertEqual(gw.remember("owner", MESSAGE).status, 200)

    def test_pins_later_through_the_write_watch(self):
        store = FakeStore(tracks=True)
        store.late = True
        gw = self.gateway({"private": store}, {"owner": self.OWNER})
        receipt = gw.remember("owner", MESSAGE).body["receipt"]
        self.assertEqual(self.pinned("private"), set())
        self.assertEqual(gw.writes.counts()["pending"], 1)
        store.receipts[receipt] = store.later[receipt]
        store.state[receipt] = "saved"
        self.clock.t += 31
        gw.writes.tick()
        self.assertEqual(self.pinned("private"), set(store.items))
        self.assertEqual(gw.writes.counts()["pending"], 0)

    def test_a_store_that_never_tracks_and_cannot_name_refs_is_left_alone(self):
        store = FakeStore(tracks=False)
        store.late = True
        gw = self.gateway({"private": store}, {"owner": self.OWNER})
        self.assertEqual(gw.remember("owner", MESSAGE).status, 200)
        self.assertEqual(gw.writes.counts()["pending"], 0)
        self.assertEqual(self.pinned("private"), set())

    def test_a_pin_failure_never_fails_the_write(self):
        store = FakeStore()

        def boom(receipt):
            raise RuntimeError("boom")

        store.receipt_refs = boom
        gw = self.gateway({"private": store}, {"owner": self.OWNER})
        reply = gw.remember("owner", MESSAGE)
        self.assertEqual(reply.status, 200)
        self.assertEqual(len(store.items), 1)
        self.assertTrue(any("pin of a write failed" in line for line in self.logs))
        self.assertFalse(any("I like green tea" in line for line in self.logs))

    def test_a_tenant_client_pins_under_the_tenant_key(self):
        tenants = FakeTenants()
        client = ClientPolicy("grp", recall=(), write="@tenant", tenants="group", pin_writes=True)
        gw = self.gateway({}, {"grp": client}, tenants={"group": tenants})
        reply = gw.remember("grp", dict(MESSAGE, tenant_id="10001"))
        self.assertEqual(reply.status, 200)
        self.assertEqual(self.pinned("group:10001"), set(tenants.stores["10001"].items))


class WatchCallbackTests(unittest.TestCase):
    class Store:
        def __init__(self, state):
            self.state = state

        def write_state(self, receipt, timeout=None):
            return self.state

    def watch(self):
        self.clock = Clock(0.0)
        return ExtractionWatch(self.clock, wait=300, poll=30)

    def test_on_saved_is_called_once_with_receipt_and_store(self):
        watch, calls = self.watch(), []
        store = self.Store("saved")
        watch.track("r1", store, lambda receipt, s: calls.append((receipt, s)))
        self.clock.t = 31
        watch.tick()
        watch.tick()
        self.assertEqual(calls, [("r1", store)])

    def test_not_called_for_empty_or_missing(self):
        watch, calls = self.watch(), []
        watch.track("r1", self.Store("empty"), lambda *a: calls.append(a))
        watch.track("r2", self.Store(None), lambda *a: calls.append(a))
        self.clock.t = 31
        watch.tick()
        self.clock.t = 400
        watch.tick()
        self.assertEqual(calls, [])
        self.assertEqual(watch.counts()["missing"], 1)

    def test_a_failing_callback_is_swallowed(self):
        watch = self.watch()

        def boom(receipt, store):
            raise RuntimeError("boom")

        watch.track("r1", self.Store("saved"), boom)
        self.clock.t = 31
        watch.tick()
        self.assertEqual(watch.counts()["saved"], 1)

    def test_track_without_a_callback_still_works(self):
        watch = self.watch()
        watch.track("r1", self.Store("saved"))
        self.clock.t = 31
        watch.tick()
        self.assertEqual(watch.counts()["saved"], 1)


class SweepTests(GatewayCase):
    OWNER = ClientPolicy("owner", recall=("private",), write="private")

    def old(self):
        return FakeStore({"x": ("old text", date_of(T0 - 400 * DAY)), "y": ("new text", date_of(T0))})

    def test_sweep_targets_cover_managed_stores_and_every_tenant(self):
        tenants = FakeTenants()
        tenants.store("10001")
        tenants.store("20002")
        gw = self.gateway({"private": FakeStore(), "other": FakeStore()}, {"owner": self.OWNER},
                          tenants={"group": tenants})
        self.assertEqual(set(gw.sweep_targets()), {"private", "group:10001", "group:20002"})

    def test_a_provider_without_list_is_skipped(self):
        gw = self.gateway({}, {}, tenants={"group": FakeTenants(listing=False)})
        self.assertEqual(gw.sweep_targets(), {})

    def test_sweep_forgets_and_dry_run_only_counts(self):
        store = self.old()
        self.manage("private", store)
        gw = self.gateway({"private": store}, {"owner": self.OWNER})
        dry = gw.sweep(dry_run=True)
        self.assertEqual(dry["private"]["due"], 1)
        self.assertEqual(set(store.items), {"x", "y"})
        report = gw.sweep()
        self.assertEqual(report["private"]["forgotten"], 1)
        self.assertEqual(set(store.items), {"y"})

    def test_sweep_goes_through_tenant_stores(self):
        tenants = FakeTenants()
        tenants.store("10001").put("old", ref="x", date=date_of(T0 - 400 * DAY))
        self.manage("group:10001", tenants.store("10001"))
        gw = self.gateway({}, {}, tenants={"group": tenants})
        report = gw.sweep()
        self.assertEqual(report["group:10001"]["forgotten"], 1)

    def test_the_gateway_sweep_never_forces_the_limit(self):
        self.engine.max_forget_fraction = 0.2
        self.engine.max_forget_min = 0
        store = self.old()
        self.manage("private", store)  # one of two is due, the limit is 0.2 * 2 = 0 memories
        gw = self.gateway({"private": store}, {"owner": self.OWNER})
        report = gw.sweep()
        self.assertEqual((report["private"]["forgotten"], report["private"]["held"]), (0, 1))
        self.assertEqual(set(store.items), {"x", "y"})
        forced = gw.forgetting.sweep(gw.sweep_targets(), force=True)
        self.assertEqual(forced["private"]["forgotten"], 1)

    def test_without_forgetting_there_is_nothing_to_sweep(self):
        gw = self.gateway({"private": FakeStore()}, {"owner": self.OWNER}, forgetting=None)
        self.assertIsNone(gw.sweep())
        self.assertEqual(gw.sweep_targets(), {})
        gw.start()  # no thread, no error

    def test_recall_through_the_gateway_uses_the_tenant_key(self):
        tenants = FakeTenants()
        tenants.store("10001").put("green tea", ref="a")
        client = ClientPolicy("grp", recall=("@tenant",), tenants="group")
        gw = self.gateway({}, {"grp": client}, tenants={"group": tenants},
                          stages=[Freshness(self.engine)])
        reply = gw.recall("grp", {"query": "green", "tenant_id": "10001"})
        self.assertEqual(reply.status, 200)
        self.assertEqual(len(reply.body["results"]), 1)
        self.assertEqual(self.engine.ledger.stores(), ["group:10001"])


class SweepThreadTests(GatewayCase):
    OWNER = ClientPolicy("owner", recall=("private",), write="private")

    def test_no_thread_when_never(self):
        gw = self.gateway({"private": FakeStore()}, {"owner": self.OWNER}, sweep_every_hours=0)
        gw.start()
        self.assertIsNone(gw._sweep_thread)

    def test_the_first_sweep_is_after_one_interval_not_at_start(self):
        gw = self.gateway({"private": FakeStore()}, {"owner": self.OWNER}, sweep_every_hours=1)
        calls = []
        gw.sweep = lambda: calls.append(1) or {"purged": 0}
        gw.start()
        threading.Event().wait(0.2)
        self.assertEqual(calls, [])
        self.assertTrue(gw._sweep_thread.daemon)

    def test_it_sweeps_repeatedly_survives_errors_and_logs_numbers_only(self):
        store = FakeStore({"x": ("old secret text", date_of(T0 - 400 * DAY))})
        self.manage("private", store)
        gw = self.gateway({"private": store}, {"owner": self.OWNER}, sweep_every_hours=0.00002)
        done = threading.Event()
        calls = []
        real = gw.sweep

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom old secret text")
            report = real()
            done.set()
            return report

        gw.sweep = flaky
        gw.start()
        self.assertTrue(done.wait(5))
        self.assertGreaterEqual(len(calls), 2)
        self.assertNotIn("x", store.items)
        joined = "\n".join(self.logs)
        self.assertIn("forgetting sweep failed: RuntimeError", joined)
        self.assertIn("forgetting sweep: stores=1 forgotten=1 held=0 failed=0 purged=0", joined)
        self.assertNotIn("secret", joined)

    def test_close_stops_the_thread_and_closes_forgetting(self):
        gw = self.gateway({"private": FakeStore()}, {"owner": self.OWNER}, sweep_every_hours=1)
        gw.start()
        thread = gw._sweep_thread
        gw.close()
        self.assertFalse(thread.is_alive())
        with self.assertRaises(sqlite3.ProgrammingError):
            self.engine.ledger.entries("private")


class ConfigCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wall = Clock()
        self.logs = []

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def cfg(self, forgetting=None, stages=None, **extra):
        section = {"ledger": self.path("ledger.db"), "archive": self.path("forgotten.db"), "stores": ["private"]}
        section.update(forgetting or {})
        cfg = {
            "stores": {"private": {"type": "test_forgettable"}},
            "clients": {"owner": {"token_env": "TOKEN_OWNER", "recall": ["private"], "write": "private"}},
            "forgetting": section,
        }
        if stages is not None:
            cfg["recall"] = {"stages": stages}
        cfg.update(extra)
        return cfg

    def build(self, cfg, **kw):
        gw = config.build_gateway(cfg, env=ENV, log=self.logs.append, wall=self.wall, **kw)
        self.addCleanup(gw.close)
        return gw


class ConfigTests(ConfigCase):
    def test_the_section_builds_the_service(self):
        cfg = self.cfg({"demote_below": 0.4, "forget_below": 0.1, "archive_days": 30, "sweep_every_hours": 6,
                        "pin_refs": ["*/profile.md"], "max_forget_fraction": 0.5,
                        "curve": {"type": "ebbinghaus", "half_life_days": 10, "per_store": {"private": 20},
                                  "reinforce": 2.0, "max_uses": 3}})
        gw = self.build(cfg)
        f = gw.forgetting
        self.assertIsInstance(f, Forgetting)
        self.assertEqual((f.stores, f.demote_below, f.forget_below, f.archive_days, f.pin_refs),
                         (("private",), 0.4, 0.1, 30, ("*/profile.md",)))
        self.assertEqual(gw.sweep_every_hours, 6)
        self.assertEqual(f.max_forget_fraction, 0.5)
        self.assertIsInstance(f.curve, Ebbinghaus)
        self.assertEqual((f.curve.half_life_days, f.curve.per_store, f.curve.reinforce, f.curve.max_uses),
                         (10, {"private": 20}, 2.0, 3))
        self.assertTrue(os.path.exists(self.path("ledger.db")))
        self.assertTrue(os.path.exists(self.path("forgotten.db")))

    def test_defaults(self):
        gw = self.build(self.cfg())
        f = gw.forgetting
        self.assertEqual((f.demote_below, f.forget_below, f.archive_days, gw.sweep_every_hours), (0.3, 0.05, 90, 24))
        self.assertEqual(f.curve.half_life_days, 90)
        self.assertEqual((f.max_forget_fraction, f.max_forget_min), (0.02, 5))

    def test_no_section_means_no_forgetting_and_unchanged_behaviour(self):
        cfg = self.cfg()
        del cfg["forgetting"]
        gw = self.build(cfg)
        self.assertIsNone(gw.forgetting)
        self.assertEqual(gw.remember("owner", MESSAGE).status, 200)
        self.assertEqual(gw.recall("owner", {"query": "green"}).status, 200)

    def test_pin_writes_is_accepted_and_checked(self):
        cfg = self.cfg()
        cfg["clients"]["owner"]["pin_writes"] = True
        self.assertTrue(self.build(cfg).clients["owner"].pin_writes)
        cfg["clients"]["owner"]["pin_writes"] = "yes"
        with self.assertRaises(ConfigError):
            config.build_gateway(cfg, env=ENV, log=self.logs.append)

    def test_a_tenants_provider_may_be_managed(self):
        cfg = self.cfg({"stores": ["private", "group"]})
        cfg["tenants"] = {"group": {"type": "directory", "path": self.path("groups"), "create": True}}
        gw = self.build(cfg)
        self.assertTrue(gw.forgetting.manages("group"))

    def bad(self, cfg, *words):
        with self.assertRaises(ConfigError) as caught:
            config.build_gateway(cfg, env=ENV, log=self.logs.append, wall=self.wall)
        for word in words:
            self.assertIn(word, str(caught.exception))

    def test_unknown_store_name(self):
        self.bad(self.cfg({"stores": ["nowhere"]}), "nowhere")

    def test_a_store_without_a_capability_is_named(self):
        cfg = self.cfg()
        cfg["stores"]["private"] = {"type": "test_plain"}
        self.bad(cfg, "private", "refs")

    def test_missing_one_capability_names_that_one(self):
        class NoDelete(FakeStore):
            delete = None

        registry.register("backend", "test_nodelete")(NoDelete)
        cfg = self.cfg()
        cfg["stores"]["private"] = {"type": "test_nodelete"}
        self.bad(cfg, "private", "delete")

    def test_bad_settings(self):
        cases = (
            {"ledger": ""}, {"archive": 5}, {"stores": []}, {"stores": "private"}, {"stores": [1]},
            {"demote_below": "x"}, {"demote_below": 2}, {"forget_below": 0.5}, {"archive_days": -1},
            {"sweep_every_hours": True}, {"pin_refs": "a"}, {"pin_refs": [1]}, {"curve": "ebbinghaus"},
            {"curve": {"type": "nonexistent"}}, {"curve": {"type": "ebbinghaus", "bogus": 1}},
            {"curve": {"half_life_days": -3}}, {"unknown_key": 1},
            {"max_forget_fraction": 1.5}, {"max_forget_fraction": -0.1}, {"max_forget_fraction": "x"},
            {"max_forget_fraction": True},
            {"max_forget_min": -1}, {"max_forget_min": 1.5}, {"max_forget_min": "5"}, {"max_forget_min": True},
        )
        for case in cases:
            with self.subTest(case=case):
                self.bad(self.cfg(case))

    def test_a_missing_path_is_an_error(self):
        cfg = self.cfg()
        del cfg["forgetting"]["ledger"]
        self.bad(cfg, "ledger")

    def test_an_unwritable_path_is_a_config_error(self):
        self.bad(self.cfg({"ledger": os.path.join(self.tmp.name, "no", "such", "dir", "l.db")}), "ledger")

    def test_the_freshness_stage_gets_the_service(self):
        gw = self.build(self.cfg(stages=["min_score", {"type": "freshness", "floor": 0.5}, "redact"]))
        stage = gw.stages[1]
        self.assertIsInstance(stage, Freshness)
        self.assertIs(stage.forgetting, gw.forgetting)
        self.assertEqual(stage.floor, 0.5)
        self.assertEqual((stage.credit, stage.credit_ratio), ("top", 0.9))

    def test_the_freshness_stage_defaults_and_credit_options(self):
        gw = self.build(self.cfg(stages=["min_score", "freshness"]))
        self.assertEqual(gw.stages[1].floor, 0.7)
        gw = self.build(self.cfg(stages=[{"type": "freshness", "credit": "none"}]))
        self.assertEqual(gw.stages[0].credit, "none")
        gw = self.build(self.cfg(stages=[{"type": "freshness", "credit": "all", "credit_ratio": 0.5}]))
        self.assertEqual((gw.stages[0].credit, gw.stages[0].credit_ratio), ("all", 0.5))
        for bad in ({"credit": "best"}, {"credit_ratio": 0}, {"credit_ratio": 2}, {"floor": 2}):
            with self.subTest(bad=bad):
                self.bad(self.cfg(stages=[dict({"type": "freshness"}, **bad)]), "freshness")

    def test_max_forget_min_is_read(self):
        f = self.build(self.cfg({"max_forget_min": 12, "max_forget_fraction": 0.1})).forgetting
        self.assertEqual((f.max_forget_min, f.max_forget_fraction), (12, 0.1))
        self.assertEqual(self.build(self.cfg({"max_forget_min": 0})).forgetting.max_forget_min, 0)

    def test_a_piece_that_needs_a_missing_service_names_the_section(self):
        cfg = self.cfg(stages=["min_score", "freshness"])
        del cfg["forgetting"]
        self.bad(cfg, "freshness", "forgetting", "add a [forgetting] section")

    def test_a_failed_build_does_not_leave_the_files_open(self):
        cfg = self.cfg(stages=["min_score", "no_such_stage"])
        self.bad(cfg, "no_such_stage")
        os.remove(self.path("ledger.db"))  # would fail on Windows if the file were still open

    def test_a_custom_piece_with_needs_gets_the_service(self):
        gw = self.build(self.cfg(stages=["tests.test_forgetting_gateway:NeedsForgetting"]))
        self.assertIs(gw.stages[0].forgetting, gw.forgetting)


class NeedsForgetting:
    needs = ("forgetting",)

    def __init__(self, forgetting):
        self.forgetting = forgetting

    def prepare(self, candidates, ctx):
        return candidates

    def admit(self, candidate, ctx):
        return "keep"


class EndToEndTests(ConfigCase):
    def test_write_recall_age_sweep_recall_restore(self):
        stages = ["min_score", "freshness", "fill_text", "redact", "dedupe", "budget"]
        cfg = self.cfg({"curve": {"type": "ebbinghaus", "half_life_days": 30}, "max_forget_fraction": 1.0},
                       stages=stages)
        gw = self.build(cfg)
        store = gw.stores["private"]
        store.put("green tea of last year", ref="old", date=date_of(T0 - 10 * DAY))
        self.assertEqual(gw.remember("owner", MESSAGE).status, 200)

        first = gw.recall("owner", {"query": "green tea"})
        self.assertEqual(first.status, 200)
        self.assertEqual(sorted(r["text"] for r in first.body["results"]),
                         ["I like green tea", "green tea of last year"])
        ledger = gw.forgetting.ledger
        self.assertEqual({e.ref: e.uses for e in ledger.entries("private")}.get("old"), 1)

        self.wall.advance(400)
        later = gw.recall("owner", {"query": "green tea"})
        self.assertEqual(len(later.body["results"]), 2)  # still there, only ranked lower
        self.assertLess(later.body["results"][0]["score"], first.body["results"][0]["score"])

        self.wall.advance(400)  # the recall above counted as a use: wait again until they are due
        dry = gw.sweep(dry_run=True)
        self.assertEqual(dry["private"]["due"], 2)
        self.assertEqual(len(store.items), 2)

        report = gw.sweep()
        self.assertEqual(report["private"]["forgotten"], 2)
        self.assertEqual(store.items, {})
        self.assertEqual(gw.forgetting.archive.count(), 2)
        self.assertEqual(gw.recall("owner", {"query": "green tea"}).body["results"], [])

        self.assertTrue(gw.forgetting.restore("private", "old", store))
        self.assertEqual(gw.forgetting.archive.count(), 1)
        again = gw.recall("owner", {"query": "green tea"})
        self.assertEqual([r["text"] for r in again.body["results"]], ["green tea of last year"])
        self.assertEqual(gw.sweep()["private"]["forgotten"], 0)  # restored memories survive the next sweep
        self.assertIn("old", store.items)

        self.wall.advance(91)  # the archive keeps what was not restored for 90 days
        self.assertEqual(gw.sweep()["purged"], 1)
        self.assertEqual(gw.forgetting.archive.count(), 0)

    def test_enabling_forgetting_on_an_old_store_deletes_nothing_at_the_first_sweep(self):
        stages = ["min_score", "freshness", "fill_text", "redact", "dedupe", "budget"]
        gw = self.build(self.cfg(stages=stages))  # every default, rate limit included
        store = gw.stores["private"]
        for i in range(10):
            store.put("green tea %d" % i, ref="old%d" % i, date=date_of(T0 - (500 + i) * DAY))
        report = gw.sweep()
        self.assertEqual(report["private"]["forgotten"], 0)
        self.assertEqual(report["private"]["held"], 0)
        self.assertEqual(report["private"]["demoted"], 10)  # old: demoted in ranking ...
        self.assertEqual(len(store.items), 10)  # ... but all present
        found = gw.recall("owner", {"query": "green tea"})
        self.assertEqual(len(found.body["results"]), 5)  # and recalled (limit 5)
        # the whole lifetime later (default half-life 90 days: 389 days) the unused ones are due, all at once:
        # the default rate limit (5 per sweep) spreads them over sweeps, and the rest is held, not lost
        self.wall.advance(400)
        report = gw.sweep()
        self.assertEqual((report["private"]["forgotten"], report["private"]["held"]), (5, 0))  # the 5 recalled are fresh
        self.assertEqual(len(store.items), 5)

    def test_a_pin_on_an_unseen_memory_uses_the_fake_clock(self):
        gw = self.build(self.cfg())
        self.wall.advance(5)
        gw.forgetting.pin("private", ["unseen"])
        entry = gw.forgetting.ledger.get_many("private", ["unseen"])["unseen"]
        self.assertEqual((entry.first_seen, entry.managed_since), (self.wall.t, self.wall.t))
        self.assertEqual(entry.first_seen, T0 + 5 * DAY)

    def test_pinned_writes_survive_the_end_to_end_sweep(self):
        cfg = self.cfg({"curve": {"type": "ebbinghaus", "half_life_days": 30}, "max_forget_fraction": 1.0})
        cfg["clients"]["owner"]["pin_writes"] = True
        gw = self.build(cfg)
        gw.remember("owner", MESSAGE)
        self.wall.advance(2000)
        report = gw.sweep()
        self.assertEqual(report["private"]["pinned"], 1)
        self.assertEqual(report["private"]["forgotten"], 0)
        self.assertEqual(len(gw.stores["private"].items), 1)


if __name__ == "__main__":
    unittest.main()
