"""POST /v1/used: the chat program reports which recalled memories its reply relied on.

Tested at three levels: Gateway.used() in process, the HTTP route, and the MemoryClient call
(client against a real gateway). The ledger is the only thing the route may change."""
import http.client
import json
import threading
import unittest

from feiyu_memgate.gateway import app as gateway_app
from feiyu_memgate.gateway import server as http_server
from feiyu_memgate.model import ClientPolicy
from feiyu_memgate.client import MemoryClient

from .test_forgetting_engine import DAY, FakeStore, date_of
from .test_forgetting_gateway import TOKEN, FakeTenants, GatewayCase
from tests.waiting import wait_until

OWNER = ClientPolicy("owner", recall=("private",), write="private")
TENANT_CLIENT = ClientPolicy("group", recall=("@tenant",), write="@tenant", tenants="group")
NO_RECALL = ClientPolicy("writer", recall=None, write="private")


def item(source, ref):
    return {"source": source, "id": ref}


class UsedCase(GatewayCase):
    def setUp(self):
        super().setUp()
        self.store = FakeStore({
            "a": ("alpha", date_of(self.wall.t - 400 * DAY)),
            "b": ("beta", date_of(self.wall.t - 400 * DAY)),
            "fresh": ("fresh", date_of(self.wall.t)),
        })
        self.manage("private", self.store)

    def gw(self, clients=None, stores=None, **kw):
        return self.gateway(stores or {"private": self.store}, clients or {"owner": OWNER}, **kw)

    def uses(self, key, *refs):
        return {r: e.uses for r, e in self.engine.ledger.get_many(key, list(refs)).items()}


class UsedInProcessTests(UsedCase):
    def test_it_records_a_use_for_each_item_and_counts_them(self):
        gw = self.gw()
        reply = gw.used("owner", {"used": [item("private", "a"), item("private", "b")]})
        self.assertEqual((reply.status, reply.body), (200, {"status": "ok", "recorded": 2}))
        self.assertEqual(self.uses("private", "a", "b", "fresh"), {"a": 1, "b": 1, "fresh": 0})
        row = self.engine.ledger.get_many("private", ["a"])["a"]
        self.assertEqual(row.last_used, self.wall.t)

    def test_both_clocks_restart(self):
        gw = self.gw()
        self.wall.advance(500)  # everything unused is now due
        entry = self.engine.ledger.get_many("private", ["a"])["a"]
        self.assertEqual(self.engine.status("private", "a", entry, None, self.wall.t), "due")
        gw.used("owner", {"used": [item("private", "a")]})
        entry = self.engine.ledger.get_many("private", ["a"])["a"]
        self.assertEqual(self.engine.status("private", "a", entry, None, self.wall.t), "active")
        self.assertEqual(self.engine.ranking_retention("private", entry, None, self.wall.t), 1.0)
        self.assertEqual(self.engine.deletion_retention("private", entry, self.wall.t), 1.0)

    def test_the_same_item_twice_counts_once(self):
        gw = self.gw()
        reply = gw.used("owner", {"used": [item("private", "a"), item("private", "a")]})
        self.assertEqual(reply.body["recorded"], 1)
        self.assertEqual(self.uses("private", "a"), {"a": 1})

    def test_a_memory_the_ledger_has_not_met_yet_is_recorded_when_the_store_has_it(self):
        self.store.items["new"] = ("brand new", None)
        gw = self.gw()
        self.assertEqual(gw.used("owner", {"used": [item("private", "new")]}).body["recorded"], 1)
        self.assertEqual(self.uses("private", "new"), {"new": 1})

    def test_made_up_ids_record_nothing_and_leave_no_row(self):
        gw = self.gw()
        reply = gw.used("owner", {"used": [item("private", "nonsense"), item("private", "a")]})
        self.assertEqual((reply.status, reply.body["recorded"]), (200, 1))
        self.assertNotIn("nonsense", {e.ref for e in self.engine.ledger.entries("private")})
        # the answer for an id that does not exist has the same shape as for one that does
        self.assertEqual(gw.used("owner", {"used": [item("private", "nonsense")]}).body,
                         {"status": "ok", "recorded": 0})

    def test_a_store_that_cannot_read_records_nothing_and_does_not_fail(self):
        self.store.items["new"] = ("brand new", None)

        def boom(ref, timeout=None, ctl=None):
            raise RuntimeError("down")

        self.store.read = boom
        gw = self.gw()
        reply = gw.used("owner", {"used": [item("private", "new"), item("private", "a")]})
        self.assertEqual((reply.status, reply.body["recorded"]), (200, 1))  # "a" has a ledger row already

    def test_a_source_forgetting_does_not_manage_is_accepted_and_not_counted(self):
        other = FakeStore({"x": ("x", None)})
        client = ClientPolicy("owner", recall=("private", "other"), write="private")
        gw = self.gw({"owner": client}, {"private": self.store, "other": other})
        reply = gw.used("owner", {"used": [item("other", "x"), item("private", "a")]})
        self.assertEqual(reply.body["recorded"], 1)
        self.assertEqual(self.engine.ledger.stores(), ["private"])

    def test_without_forgetting_it_replies_ok_with_zero(self):
        gw = self.gw(forgetting=None)
        reply = gw.used("owner", {"used": [item("private", "a")]})
        self.assertEqual((reply.status, reply.body), (200, {"status": "ok", "recorded": 0}))

    def test_an_empty_list_is_fine(self):
        reply = self.gw().used("owner", {"used": []})
        self.assertEqual((reply.status, reply.body), (200, {"status": "ok", "recorded": 0}))

    def test_other_keys_of_a_recall_result_are_ignored(self):
        result = {"id": "a", "source": "private", "text": "alpha", "score": 0.9, "date": "2026-01-01"}
        self.assertEqual(self.gw().used("owner", {"used": [result]}).body["recorded"], 1)

    def test_the_route_never_deletes_or_changes_a_store(self):
        before = dict(self.store.items)
        gw = self.gw()
        gw.used("owner", {"used": [item("private", "a"), item("private", "zzz")]})
        self.assertEqual(self.store.items, before)
        self.assertEqual(self.store.deleted, [])
        self.assertEqual(self.engine.archive.count(), 0)


class UsedBadInputTests(UsedCase):
    def bad(self, body, status=400, code="bad_used"):
        reply = self.gw().used("owner", body)
        self.assertEqual((reply.status, reply.body["code"]), (status, code), body)
        self.assertEqual(self.engine.ledger.get_many("private", ["a"])["a"].uses, 0)  # nothing was recorded

    def test_malformed_lists_and_entries(self):
        cases = (
            {}, {"used": None}, {"used": "a"}, {"used": {"source": "private", "id": "a"}},
            {"used": ["a"]}, {"used": [None]}, {"used": [[]]},
            {"used": [{"source": "private"}]}, {"used": [{"id": "a"}]},
            {"used": [{"source": "", "id": "a"}]}, {"used": [{"source": "private", "id": ""}]},
            {"used": [{"source": 5, "id": "a"}]}, {"used": [{"source": "private", "id": 7}]},
            {"used": [{"source": "private", "id": "a" * 513}]}, {"used": [{"source": "p" * 129, "id": "a"}]},
        )
        for case in cases:
            with self.subTest(case=case):
                self.bad(case)

    def test_more_than_twenty_items(self):
        self.bad({"used": [item("private", "a")] * 21})
        reply = self.gw().used("owner", {"used": [item("private", "a")] * 20})
        self.assertEqual(reply.status, 200)

    def test_one_bad_entry_spoils_the_whole_request(self):
        self.bad({"used": [item("private", "a"), {"source": "private"}]})

    def test_a_body_that_is_not_an_object(self):
        reply = self.gw().used("owner", ["a"])
        self.assertEqual((reply.status, reply.body["code"]), (400, "bad_request"))

    def test_a_source_the_client_may_not_recall_is_forbidden_known_or_not(self):
        other = FakeStore({"x": ("x", None)})
        gw = self.gw(stores={"private": self.store, "other": other})
        for source in ("other", "nowhere", "@tenant", "group"):
            reply = gw.used("owner", {"used": [item(source, "x")]})
            self.assertEqual((reply.status, reply.body["code"]), (403, "forbidden"), source)
        self.assertEqual(self.engine.ledger.stores(), ["private"])

    def test_a_forbidden_item_spoils_the_whole_request(self):
        reply = self.gw().used("owner", {"used": [item("private", "a"), item("nowhere", "a")]})
        self.assertEqual(reply.status, 403)
        self.assertEqual(self.engine.ledger.get_many("private", ["a"])["a"].uses, 0)

    def test_a_client_that_may_not_recall_is_forbidden(self):
        gw = self.gw({"writer": NO_RECALL})
        reply = gw.used("writer", {"used": [item("private", "a")]})
        self.assertEqual((reply.status, reply.body["code"]), (403, "forbidden"))

    def test_an_unknown_client_is_unauthorized(self):
        reply = self.gw().used("nobody", {"used": []})
        self.assertEqual((reply.status, reply.body["code"]), (401, "unauthorized"))

    def test_tenant_id_for_a_client_without_tenants_is_bad_tenant(self):
        reply = self.gw().used("owner", {"used": [item("private", "a")], "tenant_id": "10001"})
        self.assertEqual((reply.status, reply.body["code"]), (400, "bad_tenant"))

    def test_errors_hold_no_item_text(self):
        reply = self.gw().used("owner", {"used": [item("private", "secret-ref-name"), item("nowhere", "q")]})
        self.assertNotIn("secret-ref-name", json.dumps(reply.body))
        self.assertFalse(any("secret-ref-name" in line for line in self.logs))


class UsedTenantTests(GatewayCase):
    def setUp(self):
        super().setUp()
        self.tenants = FakeTenants()
        for tenant in ("10001", "20002"):
            self.tenants.store(tenant).put("note of " + tenant, ref="n1", date=date_of(self.wall.t - 400 * DAY))
            self.manage("group:" + tenant, self.tenants.store(tenant))

    def gw(self, **kw):
        return self.gateway({}, {"group": TENANT_CLIENT}, tenants={"group": self.tenants}, **kw)

    def test_a_tenant_use_is_keyed_with_the_tenant_and_touches_only_it(self):
        reply = self.gw().used("group", {"used": [item("group", "n1")], "tenant_id": "10001"})
        self.assertEqual((reply.status, reply.body), (200, {"status": "ok", "recorded": 1}))
        led = self.engine.ledger
        self.assertEqual(led.get_many("group:10001", ["n1"])["n1"].uses, 1)
        self.assertEqual(led.get_many("group:20002", ["n1"])["n1"].uses, 0)

    def test_the_tenant_id_is_required_and_checked(self):
        gw = self.gw()
        for body in ({"used": [item("group", "n1")]}, {"used": [item("group", "n1")], "tenant_id": "abc"},
                     {"used": [item("group", "n1")], "tenant_id": 10001}):
            self.assertEqual((gw.used("group", body).status, gw.used("group", body).body["code"]), (400, "bad_tenant"))
        unknown = gw.used("group", {"used": [item("group", "n1")], "tenant_id": "30003"})
        self.assertEqual(unknown.status, 200)  # the fake provider makes every valid id a tenant
        self.assertEqual(self.engine.ledger.get_many("group:10001", ["n1"])["n1"].uses, 0)

    def test_the_source_is_the_provider_name_not_the_at_tenant_marker(self):
        gw = self.gw()
        reply = gw.used("group", {"used": [item("@tenant", "n1")], "tenant_id": "10001"})
        self.assertEqual((reply.status, reply.body["code"]), (403, "forbidden"))

    def test_a_tenant_client_without_at_tenant_in_recall_may_not_report(self):
        client = ClientPolicy("group", recall=(), write="@tenant", tenants="group")
        gw = self.gateway({}, {"group": client}, tenants={"group": self.tenants})
        reply = gw.used("group", {"used": [item("group", "n1")], "tenant_id": "10001"})
        self.assertEqual(reply.status, 403)

    def test_a_provider_forgetting_does_not_manage_counts_zero(self):
        engine_for_private_only = self.engine
        engine_for_private_only.stores = ("private",)
        reply = self.gw().used("group", {"used": [item("group", "n1")], "tenant_id": "10001"})
        self.assertEqual(reply.body["recorded"], 0)


class UsedHttpTests(unittest.TestCase):
    """The route end to end: authentication, rate limit, methods, the access log."""

    def setUp(self):
        from .test_forgetting_engine import Clock
        from feiyu_memgate.forgetting.archive import Archive
        from feiyu_memgate.forgetting.curve import Ebbinghaus
        from feiyu_memgate.forgetting.engine import Forgetting
        from feiyu_memgate.forgetting.ledger import Ledger
        self.wall = Clock()
        self.logs = []
        self.store = FakeStore({"a": ("alpha", date_of(self.wall.t - 400 * DAY))})
        engine = Forgetting(Ledger(":memory:"), Archive(":memory:"), Ebbinghaus(), stores=("private",),
                            wall=self.wall)
        self.engine = engine
        client = ClientPolicy("owner", recall=("private",), write="private", rate_limit=3)
        gw = gateway_app.Gateway({"private": self.store}, {"owner": client}, {"owner": TOKEN},
                                 forgetting=engine, log=self.logs.append, sweep_every_hours=0)
        self.server = http_server.make_server(gw, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.port = self.server.server_address[1]

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def post(self, path, body, token=TOKEN, method="POST"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, data

    def test_the_route_records_a_use(self):
        status, body = self.post("/v1/used", {"used": [item("private", "a")]})
        self.assertEqual((status, body), (200, {"status": "ok", "recorded": 1}))
        self.assertEqual(self.engine.ledger.get_many("private", ["a"])["a"].uses, 1)
        self.assertTrue(wait_until(lambda: any(line.startswith("POST /v1/used 200 client=owner") for line in self.logs)))
        self.assertFalse(any('"a"' in line or "alpha" in line for line in self.logs))

    def test_it_needs_a_token(self):
        self.assertEqual(self.post("/v1/used", {"used": []}, token="")[0], 401)
        self.assertEqual(self.post("/v1/used", {"used": []}, token="tok-wrong-" + "x" * 30)[0], 401)

    def test_only_post_is_allowed(self):
        status, body = self.post("/v1/used", None, method="GET")
        self.assertEqual((status, body["code"]), (405, "method_not_allowed"))

    def test_bad_input_is_a_stable_code(self):
        status, body = self.post("/v1/used", {"used": [{"source": "private"}]})
        self.assertEqual((status, body["code"]), (400, "bad_used"))
        status, body = self.post("/v1/used", {"used": [item("elsewhere", "a")]})
        self.assertEqual((status, body["code"]), (403, "forbidden"))

    def test_other_methods_are_405_everywhere_and_write_nothing(self):
        before = dict(self.store.items)
        for path in ("/healthz", "/v1/recall", "/v1/remember", "/v1/used"):
            for method in ("DELETE", "PUT", "PATCH", "HEAD", "OPTIONS"):
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
                body = json.dumps({"used": [item("private", "a")]})
                conn.request(method, path, body=body, headers={"Authorization": "Bearer " + TOKEN,
                                                               "Content-Type": "application/json"})
                resp = conn.getresponse()
                raw = resp.read()
                conn.close()
                self.assertEqual(resp.status, 405, (method, path))
                if method == "HEAD":
                    self.assertEqual(raw, b"")
                else:
                    self.assertEqual(json.loads(raw.decode("utf-8"))["code"], "method_not_allowed", (method, path))
        self.assertEqual(self.store.items, before)
        self.assertEqual(self.engine.ledger.entries("private"), [])
        self.assertTrue(any(line.startswith("DELETE /healthz 405") for line in self.logs))  # the real method is logged
        status, body = self.post("/nowhere", None, method="DELETE")
        self.assertEqual((status, body["code"]), (404, "not_found"))

    def test_it_shares_the_rate_limit_with_recall(self):
        self.assertEqual(self.post("/v1/recall", {"query": "alpha"})[0], 200)
        self.assertEqual(self.post("/v1/used", {"used": [item("private", "a")]})[0], 200)
        self.assertEqual(self.post("/v1/used", {"used": [item("private", "a")]})[0], 200)
        status, body = self.post("/v1/used", {"used": [item("private", "a")]})
        self.assertEqual((status, body["code"]), (429, "rate_limited"))
        self.assertEqual(self.post("/v1/recall", {"query": "alpha"})[0], 429)


class UsedClientTests(unittest.TestCase):
    """MemoryClient.report_used against a real gateway with forgetting."""

    def setUp(self):
        UsedHttpTests.setUp(self)
        self.client = MemoryClient("http://127.0.0.1:%d" % self.port, TOKEN)

    _stop = UsedHttpTests._stop

    def test_it_reports_and_returns_true(self):
        results = [{"id": "a", "source": "private", "text": "alpha", "score": 0.9}]
        self.assertTrue(self.client.report_used(results))
        self.assertEqual(self.engine.ledger.get_many("private", ["a"])["a"].uses, 1)

    def test_it_returns_false_when_the_gateway_refuses(self):
        self.assertFalse(self.client.report_used([item("elsewhere", "a")]))

    def test_it_never_raises_when_the_gateway_is_gone(self):
        self.server.shutdown()
        self.server.server_close()
        self.assertFalse(MemoryClient("http://127.0.0.1:%d" % self.port, TOKEN).report_used([item("private", "a")],
                                                                                           timeout=1.0))


if __name__ == "__main__":
    unittest.main()
