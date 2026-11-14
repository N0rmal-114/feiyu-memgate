"""The gateway called in-process: tenant stores in recall, skipped writes, failing pieces, provenance,
workers and budgets, limits that rotating tenant ids cannot dodge, and piece lifecycles.

No HTTP and no network: in-memory stores and a small tenants provider.
"""
import threading
import time
import unittest

from feiyu_memgate import registry
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.core.write import DailyCapGuard, RedactGuard, WriteGuard
from feiyu_memgate.errors import ConfigError, WriteRejected
from feiyu_memgate.gateway.app import Gateway
from feiyu_memgate.model import ClientPolicy, Hit
from feiyu_memgate.pieces import KEEP, RecallStage
from feiyu_memgate.tenants.base import TenantProvider

TOKEN = "tok-core-fake-".ljust(40, "0")
MESSAGE = {"messages": [{"role": "user", "content": "I like green tea"}]}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Tenants(TenantProvider):
    """Tenants "1".."9..." with an in-memory store each; only ids in `known` exist."""

    def __init__(self, known=("1", "2")):
        self.known = set(known)
        self.stores = {}
        self.asked = []

    def valid_id(self, tenant_id):
        return tenant_id.isdigit() and len(tenant_id) <= 12

    def store(self, tenant_id):
        self.asked.append(tenant_id)
        if tenant_id not in self.known:
            return None
        if tenant_id not in self.stores:
            self.stores[tenant_id] = InMemoryBackend(memories=["tenant %s likes green tea" % tenant_id])
        return self.stores[tenant_id]


class SlowStore(InMemoryBackend):
    def __init__(self, delay, **kw):
        super().__init__(**kw)
        self.delay = delay

    def find(self, query, limit, timeout=None, ctl=None):
        time.sleep(self.delay)
        return super().find(query, limit, timeout, ctl)


class HungStore(InMemoryBackend):
    """A store whose calls never come back on their own (no socket the gateway could cut)."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def find(self, query, limit, timeout=None, ctl=None):
        self.release.wait(10)
        return []


class Case(unittest.TestCase):
    def gateway(self, stores, clients, **kw):
        kw.setdefault("log", self.logs.append)
        kw.setdefault("clock", time.monotonic)
        gw = Gateway(stores, clients, {name: None for name in clients}, **kw)
        self.addCleanup(gw.close)
        return gw

    def setUp(self):
        self.logs = []


class TenantStoreInRecallTests(Case):
    def build(self, recall):
        self.tenants = Tenants()
        shared = InMemoryBackend(memories=["shared memory about green tea"])
        policy = ClientPolicy("grp", recall=recall, write="@tenant", tenants="group")
        return self.gateway({"shared": shared}, {"grp": policy}, tenants={"group": self.tenants})

    def sources(self, gw):
        reply = gw.recall("grp", {"query": "green tea", "tenant_id": "1"})
        self.assertEqual(reply.status, 200)
        return [r["source"] for r in reply.body["results"]]

    def test_the_tenant_store_is_searched_only_when_named(self):
        self.assertEqual(self.sources(self.build(("shared",))), ["shared"])
        self.assertEqual(sorted(self.sources(self.build(("shared", "@tenant")))), ["group", "shared"])
        self.assertEqual(self.sources(self.build(("@tenant",))), ["group"])

    def test_an_empty_recall_list_may_ask_and_searches_nothing(self):
        gw = self.build(())
        reply = gw.recall("grp", {"query": "green tea", "tenant_id": "1"})
        self.assertEqual(reply, (200, {"results": [], "more": 0, "failed": 0, "busy": 0}, ""))

    def test_the_tenant_must_still_be_known_without_its_store_in_recall(self):
        gw = self.build(("shared",))
        self.assertEqual(gw.recall("grp", {"query": "tea", "tenant_id": "7"}).body["code"], "unknown_tenant")

    def test_tenant_source_is_set_only_when_the_tenant_store_is_searched(self):
        seen = []

        class Look(RecallStage):
            def admit(self, candidate, ctx):
                seen.append(ctx.tenant_source)
                return KEEP

        gw = self.build(("shared",))
        gw.stages = [Look()]
        gw.recall("grp", {"query": "green tea", "tenant_id": "1"})
        gw = self.build(("@tenant",))
        gw.stages = [Look()]
        gw.recall("grp", {"query": "green tea", "tenant_id": "1"})
        self.assertEqual(seen, [None, "group"])


class SkippedTests(Case):
    def test_guards_that_keep_nothing_skip_the_write(self):
        class KeepNothing(WriteGuard):
            def before(self, messages, ctx):
                return []

        store = InMemoryBackend()
        cap = DailyCapGuard()
        policy = ClientPolicy("o", recall=("s",), write="s", daily_writes=1)
        gw = self.gateway({"s": store}, {"o": policy}, guards=[cap, KeepNothing()])
        for _ in range(3):
            self.assertEqual(gw.remember("o", MESSAGE), (200, {"status": "skipped"}, "skipped"))
        self.assertEqual(store.refs(), [])
        self.assertEqual(cap.caps._counts, {})  # rolled back: a skipped write is not counted
        gw.guards = [cap]
        self.assertEqual(gw.remember("o", MESSAGE).status, 200)

    def test_a_backend_that_kept_nothing_is_skipped_and_after_hooks_do_not_run(self):
        calls = []

        class Nothing(InMemoryBackend):
            tracks_writes = True

            def remember(self, messages, timeout=None, ctl=None):
                return None

        class Watch(WriteGuard):
            def rollback(self, ctx):
                calls.append("rollback")

            def after(self, receipt, ctx):
                calls.append("after")

        cap = DailyCapGuard()
        policy = ClientPolicy("o", recall=("s",), write="s", daily_writes=5)
        gw = self.gateway({"s": Nothing()}, {"o": policy}, guards=[cap, Watch()])
        self.assertEqual(gw.remember("o", MESSAGE).body, {"status": "skipped"})
        self.assertEqual(calls, ["rollback"])
        self.assertEqual(cap.caps._counts, {})
        self.assertEqual(gw.writes.counts()["pending"], 0)  # nothing to follow


class GuardFailureTests(Case):
    def build(self, *guards, store=None):
        self.store = store or InMemoryBackend()
        policy = ClientPolicy("o", recall=("s",), write="s", daily_writes=10)
        return self.gateway({"s": self.store}, {"o": policy}, guards=list(guards))

    def recorder(self, name, calls, fail=None):
        class Recorder(WriteGuard):
            def before(self, messages, ctx):
                if fail == "before":
                    raise KeyError("secret memory text")
                if fail == "none":
                    return None
                if fail == "reject":
                    raise WriteRejected(400, "links are not stored", "has-link", code="has_link")
                if fail == "bad-status":
                    raise WriteRejected(200, "fine?")
                calls.append("before " + name)
                return messages

            def rollback(self, ctx):
                calls.append("rollback " + name)
                if fail == "rollback":
                    raise RuntimeError("boom")

            def after(self, receipt, ctx):
                calls.append("after " + name)
                if fail == "after":
                    raise RuntimeError("boom")

        guard = Recorder()
        guard.name = name
        return guard

    def test_a_guard_that_raises_fails_the_write_and_rolls_back_the_earlier_ones(self):
        calls = []
        gw = self.build(self.recorder("a", calls), self.recorder("b", calls), self.recorder("c", calls, "before"))
        reply = gw.remember("o", MESSAGE)
        self.assertEqual(reply, (500, {"error": "guard error", "code": "guard_error"}, "guard-error:c"))
        self.assertEqual(calls, ["before a", "before b", "rollback b", "rollback a"])
        self.assertEqual(self.store.refs(), [])
        self.assertIn("piece-error client=o guard-error:c where=before error=KeyError", self.logs)
        self.assertNotIn("secret", " ".join(self.logs))

    def test_a_forgotten_return_is_a_guard_error_not_a_write(self):
        calls = []
        gw = self.build(self.recorder("a", calls), self.recorder("lazy", calls, "none"))
        self.assertEqual(gw.remember("o", MESSAGE).body["code"], "guard_error")
        self.assertEqual(calls, ["before a", "rollback lazy", "rollback a"])
        self.assertEqual(self.store.refs(), [])

    def test_a_refusal_keeps_its_status_and_code(self):
        calls = []
        gw = self.build(self.recorder("a", calls), self.recorder("links", calls, "reject"))
        self.assertEqual(gw.remember("o", MESSAGE), (400, {"error": "links are not stored", "code": "has_link"},
                                                     "has-link"))
        self.assertEqual(calls, ["before a", "rollback a"])

    def test_a_refusal_with_a_success_status_fails_closed(self):
        gw = self.build(self.recorder("odd", [], "bad-status"))
        self.assertEqual(gw.remember("o", MESSAGE).status, 500)
        self.assertEqual(self.store.refs(), [])

    def test_the_default_code_follows_the_status(self):
        self.assertEqual(WriteRejected(429, "slow down").code, "rate_limited")
        self.assertEqual(WriteRejected(403, "no").code, "forbidden")
        self.assertEqual(WriteRejected(418, "teapot").code, "bad_request")
        self.assertEqual(WriteRejected(500, "x").code, "internal")

    def test_a_failing_after_hook_is_logged_and_the_write_stands(self):
        calls = []
        gw = self.build(self.recorder("a", calls, "after"), self.recorder("b", calls))
        self.assertEqual(gw.remember("o", MESSAGE).status, 200)
        self.assertEqual(calls, ["before a", "before b", "after a", "after b"])
        self.assertEqual(len(self.store.refs()), 1)
        self.assertIn("piece-error client=o guard-error:a where=after error=RuntimeError", self.logs)

    def test_a_failing_rollback_does_not_stop_the_others(self):
        calls = []
        gw = self.build(self.recorder("a", calls), self.recorder("b", calls, "rollback"),
                        self.recorder("c", calls, "before"))
        gw.remember("o", MESSAGE)
        self.assertEqual(calls, ["before a", "before b", "rollback b", "rollback a"])
        self.assertIn("piece-error client=o guard-error:b where=rollback error=RuntimeError", self.logs)

    def test_a_backend_failure_rolls_the_guards_back(self):
        class Down(InMemoryBackend):
            def remember(self, messages, timeout=None, ctl=None):
                raise OSError("down")

        cap = DailyCapGuard()
        gw = self.build(cap, store=Down())
        for _ in range(3):
            self.assertEqual(gw.remember("o", MESSAGE).body["code"], "backend_unavailable")
        self.assertEqual(cap.caps._counts, {})
        self.assertIn("piece-error client=o store=s where=remember error=OSError", self.logs)

    def test_in_process_calls_never_raise(self):
        class Exploding(Tenants):
            def valid_id(self, tenant_id):
                raise RuntimeError("provider bug")

        policy = ClientPolicy("g", recall=("@tenant",), write="@tenant", tenants="t")
        gw = self.gateway({}, {"g": policy}, tenants={"t": Exploding()})
        body = {"query": "x", "tenant_id": "1", "messages": MESSAGE["messages"]}
        self.assertEqual(gw.recall("g", body), (500, {"error": "internal error", "code": "internal"}, ""))
        self.assertEqual(gw.remember("g", body).status, 500)
        self.assertEqual(gw.recall("nobody", body).body["code"], "unauthorized")
        self.assertEqual(gw.remember("g", ["not", "a", "dict"]).body["code"], "bad_request")
        self.assertEqual(gw.admit("nobody", "recall").body["code"], "unauthorized")


class DailyCapRaceTests(Case):
    def test_eight_concurrent_writes_with_a_cap_of_one_store_one(self):
        class Slow(InMemoryBackend):
            def remember(self, messages, timeout=None, ctl=None):
                time.sleep(0.05)  # widen the window between the check and the count
                return super().remember(messages, timeout, ctl)

        store = Slow()
        policy = ClientPolicy("o", recall=("s",), write="s", daily_writes=1)
        gw = self.gateway({"s": store}, {"o": policy}, guards=[RedactGuard(), DailyCapGuard()])
        barrier = threading.Barrier(8)
        statuses = []

        def go():
            barrier.wait()
            statuses.append(gw.remember("o", MESSAGE).status)

        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(sorted(statuses), [200] + [429] * 7)
        self.assertEqual(len(store.refs()), 1)


class ProvenanceTests(Case):
    def build(self, guards=None, stages=None):
        self.store = InMemoryBackend(memories=["likes green tea"])
        self.received = []
        store, received = self.store, self.received
        real = store.remember

        def remember(messages, timeout=None, ctl=None):
            received.append(messages)
            return real(messages, timeout, ctl)

        store.remember = remember
        policy = ClientPolicy("o", recall=("s",), write="s")
        return self.gateway({"s": store}, {"o": policy}, guards=guards, stages=stages)

    def test_time_and_speaker_reach_guards_and_backends_and_other_keys_are_dropped(self):
        seen = []

        class Look(WriteGuard):
            def before(self, messages, ctx):
                seen.append([dict(m) for m in messages])
                return messages

        gw = self.build(guards=[RedactGuard(), Look()])
        message = {"role": "user", "content": "I like tea", "time": "2026-11-14T12:00:00+08:00",
                   "speaker": "guest", "mood": "dropped"}
        self.assertEqual(gw.remember("o", {"messages": [message]}).status, 200)
        expected = [{"role": "user", "content": "I like tea", "time": "2026-11-14T12:00:00+08:00", "speaker": "guest"}]
        self.assertEqual(seen, [expected])
        self.assertEqual(self.received, [expected])

    def test_time_and_speaker_are_checked(self):
        gw = self.build()
        for extra in ({"time": "t" * 41}, {"speaker": "s" * 65}, {"time": 5}, {"speaker": ["x"]}):
            body = {"messages": [dict({"role": "user", "content": "x"}, **extra)]}
            self.assertEqual(gw.remember("o", body).body["code"], "bad_messages", extra)
        body = {"messages": [{"role": "user", "content": "x", "time": None}]}
        self.assertEqual(gw.remember("o", body).status, 200)  # null is the same as absent

    def test_the_public_redactor_also_cleans_the_speaker(self):
        guard = RedactGuard()

        class Ctx:
            redact = staticmethod(lambda text: text.replace("13812345678", "[REDACTED]"))

        out = guard.before([{"role": "user", "content": "hi", "speaker": "13812345678"}], Ctx())
        self.assertEqual(out[0]["speaker"], "[REDACTED]")

    def test_context_reaches_stages_and_guards(self):
        seen = []

        class LookStage(RecallStage):
            def admit(self, candidate, ctx):
                seen.append(("stage", ctx.context))
                return KEEP

        class LookGuard(WriteGuard):
            def before(self, messages, ctx):
                seen.append(("guard", ctx.context))
                return messages

        gw = self.build(guards=[LookGuard()], stages=[LookStage()])
        context = {"channel": "voice", "turn": 3, "ratio": 0.5, "live": True}
        gw.recall("o", {"query": "green tea", "context": context})
        gw.remember("o", dict(MESSAGE, context=context))
        self.assertEqual(seen, [("stage", context), ("guard", context)])

    def test_context_is_checked(self):
        gw = self.build()
        bad = ({"k%d" % i: 1 for i in range(17)}, {"k": "v" * 201}, {"k": [1]}, {"k": {"a": 1}}, {"k": None},
               {"": 1}, {"k" * 65: 1}, {"k": float("inf")}, "text", [1])
        for context in bad:
            with self.subTest(context=repr(context)[:40]):
                self.assertEqual(gw.recall("o", {"query": "tea", "context": context}).body["code"], "bad_context")
                self.assertEqual(gw.remember("o", dict(MESSAGE, context=context)).body["code"], "bad_context")
        self.assertEqual(gw.recall("o", {"query": "tea", "context": {"k" * 64: "v" * 200}}).status, 200)

    def test_results_carry_the_memory_id(self):
        gw = self.build()
        reply = gw.recall("o", {"query": "green tea"})
        ref = self.store.refs()[0][0]
        self.assertEqual(reply.body["results"][0]["id"], ref)


class WorkerTests(Case):
    def test_sixteen_concurrent_recalls_of_one_store_all_answer(self):
        store = SlowStore(0.05, memories=["likes green tea"])
        gw = self.gateway({"s": store}, {"o": ClientPolicy("o", recall=("s",))})
        barrier = threading.Barrier(16)
        replies = []

        def go():
            barrier.wait()
            replies.append(gw.recall("o", {"query": "green tea"}))

        threads = [threading.Thread(target=go) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual([r.status for r in replies], [200] * 16)
        self.assertTrue(all(r.body["failed"] == r.body["busy"] == 0 for r in replies))
        self.assertEqual(gw.recall_failures.count(), 0)

    def test_one_hung_store_cannot_hold_every_worker(self):
        hung = HungStore()
        self.addCleanup(hung.release.set)
        good = InMemoryBackend(memories=["likes green tea"])
        clients = {"o": ClientPolicy("o", recall=("hung",)), "p": ClientPolicy("p", recall=("good",))}
        gw = self.gateway({"hung": hung, "good": good}, clients, pool_size=4, recall_deadline=0.2,
                          cold_recall_deadline=0.2)
        for _ in range(6):  # each leaves a call stuck on the hung store
            self.assertIn(gw.recall("o", {"query": "x"}).status, (503,))
        self.assertLessEqual(gw.store_slots.in_flight(hung), 2)  # pool_size - 2
        reply = gw.recall("p", {"query": "green tea"})
        self.assertEqual(reply.status, 200)
        self.assertEqual([r["text"] for r in reply.body["results"]], ["likes green tea"])

    def test_a_store_that_could_not_be_searched_for_want_of_a_worker_is_busy(self):
        hung = HungStore()
        self.addCleanup(hung.release.set)
        gw = self.gateway({"hung": hung}, {"o": ClientPolicy("o", recall=("hung",))}, pool_size=3,
                          store_max_inflight=1, recall_deadline=0.2, cold_recall_deadline=0.2)
        first = gw.recall("o", {"query": "x"})
        self.assertEqual(first.body["code"], "unavailable")  # timed out: broken
        second = gw.recall("o", {"query": "x"})
        self.assertEqual(second, (503, {"error": "busy", "code": "busy"}, ""))  # never started: busy
        health = gw.health(deep=False)
        self.assertEqual(health, {"status": "ok"})
        self.assertEqual((gw.recall_failures.count(), gw.recall_busy.count()), (1, 1))

    def test_tenant_work_leaves_workers_to_the_other_clients(self):
        tenants = Tenants(known=[str(i) for i in range(1, 20)])
        for tid in tenants.known:
            tenants.stores[tid] = HungStore()
        self.addCleanup(lambda: [s.release.set() for s in tenants.stores.values()])
        clients = {"g": ClientPolicy("g", recall=("@tenant",), tenants="t"),
                   "o": ClientPolicy("o", recall=("s",))}
        gw = self.gateway({"s": InMemoryBackend(memories=["owner memory"])}, clients, tenants={"t": tenants},
                          pool_size=4, tenant_pool_total=2, recall_deadline=0.3, cold_recall_deadline=0.3)
        threads = [threading.Thread(target=gw.recall, args=("g", {"query": "x", "tenant_id": str(i)}))
                   for i in range(1, 9)]
        for t in threads:
            t.start()
        time.sleep(0.1)
        self.assertLessEqual(gw.pool.in_flight, 2)  # tenant calls never take the last pool_size - total
        reply = gw.recall("o", {"query": "owner memory"})
        self.assertEqual(reply.status, 200)
        for t in threads:
            t.join(5)

    def test_the_tenant_budget_is_taken_once_per_request(self):
        tenants = Tenants()
        policy = ClientPolicy("g", recall=("s", "@tenant"), tenants="t")
        gw = self.gateway({"s": InMemoryBackend(memories=["shared green tea"])}, {"g": policy},
                          tenants={"t": tenants}, tenant_pool_total=1, tenant_pool_per_tenant=1)
        reply = gw.recall("g", {"query": "green tea", "tenant_id": "1"})
        self.assertEqual((reply.status, reply.body["failed"], reply.body["busy"]), (200, 0, 0))
        self.assertEqual(len(reply.body["results"]), 2)


class LimitTests(Case):
    def build(self, **policy):
        self.clock = Clock()
        self.tenants = Tenants(known=[str(i) for i in range(1, 3000)])
        options = dict(recall=("@tenant",), write="@tenant", tenants="t", rate_limit=2)
        options.update(policy)
        return self.gateway({}, {"g": ClientPolicy("g", **options)}, tenants={"t": self.tenants},
                            clock=self.clock, guards=[DailyCapGuard()])

    def test_rotating_tenant_ids_cannot_dodge_the_client_wide_rate_limit(self):
        gw = self.build(daily_writes=1)
        stored = 0
        for i in range(1, 1501):
            if gw.admit("g", "remember") is not None:
                continue
            if gw.remember("g", dict(MESSAGE, tenant_id=str(i))).status == 200:
                stored += 1
        self.assertEqual(stored, 20)  # rate_limit_total defaults to 10 x rate_limit
        self.assertEqual(gw.admit("g", "recall").body["code"], "rate_limited")

    def test_rate_limit_total_can_be_set(self):
        gw = self.build(rate_limit_total=3)
        results = [gw.admit("g", "recall") for _ in range(5)]
        self.assertEqual([r is None for r in results], [True, True, True, False, False])

    def test_an_unknown_tenant_costs_rate_limit_budget(self):
        gw = self.build()
        self.tenants.known.discard("2000")
        for _ in range(2):
            self.assertEqual(gw.recall("g", {"query": "x", "tenant_id": "2000"}).body["code"], "unknown_tenant")
        self.assertEqual(gw.recall("g", {"query": "x", "tenant_id": "2000"}).body["code"], "rate_limited")
        self.assertEqual(self.tenants.asked.count("2000"), 2)  # limited before the provider is asked

    def test_a_malformed_tenant_id_never_reaches_the_limiter_or_the_provider(self):
        gw = self.build()
        self.assertEqual(gw.recall("g", {"query": "x", "tenant_id": "../1"}).body["code"], "bad_tenant")
        self.assertEqual(self.tenants.asked, [])
        self.assertEqual(len(gw.limiter), 0)

    def test_daily_writes_total_caps_the_whole_client(self):
        gw = self.build(daily_writes=5, daily_writes_total=3, rate_limit=100)
        statuses = [gw.remember("g", dict(MESSAGE, tenant_id=str(i))).status for i in range(1, 6)]
        self.assertEqual(statuses, [200, 200, 200, 429, 429])

    def test_daily_writes_total_also_applies_to_a_plain_client(self):
        store = InMemoryBackend()
        gw = self.gateway({"s": store}, {"o": ClientPolicy("o", write="s", daily_writes_total=2)},
                          guards=[DailyCapGuard()])
        self.assertEqual([gw.remember("o", MESSAGE).status for _ in range(3)], [200, 200, 429])

    def test_the_limiter_forgets_old_keys(self):
        gw = self.build(rate_limit=100)
        for i in range(1, 200):
            gw.recall("g", {"query": "x", "tenant_id": str(i)})
        self.assertGreater(len(gw.limiter), 100)
        self.clock.t += 61
        gw.recall("g", {"query": "x", "tenant_id": "1"})
        self.assertEqual(len(gw.limiter), 1)


class LifecycleTests(Case):
    def piece(self, kind, calls, fail=None):
        class Piece:
            def start(self, gateway):
                calls.append(("start", kind, gateway is not None))
                if fail == "start":
                    raise RuntimeError("cannot start")

            def close(self):
                calls.append(("close", kind))
                if fail == "close":
                    raise RuntimeError("cannot close")

        return Piece()

    def test_start_and_close_reach_every_piece_once(self):
        calls = []

        class Store(InMemoryBackend):
            def start(self, gateway):
                calls.append(("start", "store", True))

            def close(self):
                calls.append(("close", "store"))

        stage = self.piece("stage", calls)
        guard = self.piece("guard", calls)
        gw = self.gateway({"s": Store()}, {"o": ClientPolicy("o", recall=("s",))}, stages=[stage, stage],
                          guards=[guard])
        gw.start()
        gw.start()
        self.assertEqual(calls, [("start", "store", True), ("start", "stage", True), ("start", "guard", True)])
        gw.close()
        gw.close()
        self.assertEqual(calls[3:], [("close", "guard"), ("close", "stage"), ("close", "store")])

    def test_a_failing_close_is_logged_and_never_fatal(self):
        calls = []
        gw = self.gateway({}, {"o": ClientPolicy("o", recall=())},
                          stages=[self.piece("bad", calls, "close")], guards=[self.piece("good", calls)])
        gw.close()
        self.assertIn(("close", "good"), calls)
        self.assertTrue(any(line.startswith("piece-error close stage=") and "RuntimeError" in line
                            for line in self.logs))

    def test_a_failing_start_is_logged_and_raised(self):
        calls = []
        gw = self.gateway({}, {"o": ClientPolicy("o", recall=())}, stages=[self.piece("bad", calls, "start")])
        with self.assertRaises(RuntimeError):
            gw.start()
        self.assertTrue(any(line.startswith("piece-error start stage=") for line in self.logs))


class TokenTests(unittest.TestCase):
    def test_two_clients_with_one_token_are_refused(self):
        clients = {"a": ClientPolicy("a", recall=()), "b": ClientPolicy("b", recall=())}
        with self.assertRaises(ConfigError) as ctx:
            Gateway({}, clients, {"a": TOKEN, "b": TOKEN}, log=lambda line: None)
        self.assertIn("a and b", str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_clients_without_a_token_do_not_clash(self):
        clients = {"a": ClientPolicy("a", recall=()), "b": ClientPolicy("b", recall=())}
        Gateway({}, clients, {"a": "", "b": ""}, log=lambda line: None).close()


class StageNameTests(unittest.TestCase):
    def test_registered_stage_names_reach_the_notes(self):
        notes = []
        gw = Gateway({"s": InMemoryBackend(memories=["green tea"])}, {"o": ClientPolicy("o", recall=("s",))},
                     {"o": TOKEN}, stages=[registry.create("recall_stage", "redact")],
                     redactors={"private": lambda text: 1 / 0}, log=notes.append)
        self.addCleanup(gw.close)
        reply = gw.recall("o", {"query": "green tea"})
        self.assertEqual((reply.status, reply.body["results"], reply.note), (200, [], "stage-error:redact"))
        self.assertEqual(notes, ["piece-error client=o stage-error:redact where=admit error=ZeroDivisionError"])


class HitMetaTests(unittest.TestCase):
    def test_a_hit_without_meta_has_an_empty_dict(self):
        self.assertEqual(Hit("r", 0.5).meta, {})


if __name__ == "__main__":
    unittest.main()
