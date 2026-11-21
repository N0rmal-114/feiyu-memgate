"""Audience levels: who a store is for, who sees what a client shows, and the start-up check between them.

The matrix below is written by hand from the two rules, not computed by the code under test:
  R1  a client lists a store in recall only if the store's audience is at least as wide as the client's
  R2  a tenant client of audience "group" serves many groups: its shared stores must be "public"
(a store that declares nothing is "owner", a tenants provider that declares nothing is "group").
"""
import copy
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from feiyu_memgate import cli, config, registry
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.client import LocalClient, MemoryClient
from feiyu_memgate.core.recall import KEEP, RecallStage
from feiyu_memgate.core.write import WriteGuard
from feiyu_memgate.errors import ConfigError
from feiyu_memgate.gateway import server as gateway_server
from feiyu_memgate.gateway.app import Gateway
from feiyu_memgate.model import AUDIENCES, ClientPolicy
from feiyu_memgate.tenants.base import TenantProvider
from tests.test_client_http import FakeGateway
from tests.test_examples import ROOT, _env_names, _move_paths, keep_sys_path

TOKEN = "tok-aud-fake-".ljust(40, "0")
ENV = {"TOKEN_C": TOKEN, "TOKEN_OWNER": "tok-aud-own-".ljust(40, "0")}
MARKER = "zebra quartz marker about the owner's tea"
FACT = "the shared fact about oolong tea"
GROUP_ID = "10001"


class MemTenants(TenantProvider):
    """Tenant "10001" with an in-memory store."""

    def __init__(self):
        self.stores = {GROUP_ID: InMemoryBackend()}

    def valid_id(self, tenant_id):
        return tenant_id in self.stores

    def store(self, tenant_id):
        return self.stores.get(tenant_id)

    def list(self):
        return list(self.stores)


registry.register("tenants", "audience_test_mem")(MemTenants)

# kind -> (public, audience, has tenants, reach)
KINDS = {
    "owner": (False, None, False, "owner"),
    "owner+tenants": (False, None, True, "owner"),
    "group+tenants": (True, "group", True, "group"),
    "group": (True, "group", False, "group"),
    "public": (True, None, False, "public"),
    "public+tenants": (True, None, True, "group"),  # a public tenant client serves groups: reach "group"
    "declared public+tenants": (True, "public", True, "public"),  # says its tenants are open to anyone
}
# entry -> (shared store or "@tenant", declared audience or None)
ENTRIES = {
    "store owner": ("s", "owner"), "store group": ("s", "group"), "store public": ("s", "public"),
    "store undeclared": ("s", None),
    "tenant owner": ("@tenant", "owner"), "tenant group": ("@tenant", "group"),
    "tenant public": ("@tenant", "public"), "tenant undeclared": ("@tenant", None),
}
Y, N = True, False
# Written by hand. Columns: owner, owner+tenants, group+tenants, group, public, public+tenants,
# declared public+tenants.
_ROWS = {
    #                   owner  own+t  grp+t  group  public pub+t  decl. pub+t
    "store owner":      (Y,     Y,     N,     N,     N,     N,     N),
    "store group":      (Y,     Y,     N,     Y,     N,     N,     N),  # R2 refuses group+tenants, public+tenants
    "store public":     (Y,     Y,     Y,     Y,     Y,     Y,     Y),
    "store undeclared": (Y,     Y,     N,     N,     N,     N,     N),  # undeclared = owner
    "tenant owner":     (N,     Y,     N,     N,     N,     N,     N),  # no tenants: cannot name "@tenant"
    "tenant group":     (N,     Y,     Y,     N,     N,     Y,     N),  # a group's store is not for anyone
    "tenant public":    (N,     Y,     Y,     N,     N,     Y,     Y),
    "tenant undeclared": (N,    Y,     Y,     N,     N,     Y,     N),  # undeclared = group
}
MATRIX = {(kind, entry): row[i] for entry, row in _ROWS.items() for i, kind in enumerate(KINDS)}


def client_policy(kind, entry):
    public, audience, tenants, _ = KINDS[kind]
    return ClientPolicy("c", recall=(ENTRIES[entry][0],), tenants="g" if tenants else None, public=public,
                        audience=audience)


def config_for(kind, entry, extra_stores=None):
    public, audience, tenants, _ = KINDS[kind]
    target, declared = ENTRIES[entry]
    store = {"type": "inmemory"}
    provider = {"type": "audience_test_mem"}
    if declared is not None:
        (store if target == "s" else provider)["audience"] = declared
    client = {"token_env": "TOKEN_C", "recall": [target]}
    if tenants:
        client["tenants"] = "g"
    if public:
        client["public"] = True
    if audience:
        client["audience"] = audience
    cfg = {"stores": dict({"s": store}, **(extra_stores or {})), "clients": {"c": client}}
    if tenants:
        cfg["tenants"] = {"g": provider}
    return cfg


def tokens_of(clients):
    return {name: ("tok-aud-%s-" % name).ljust(40, "0") for name in clients}


def gateway_for(kind, entry, extra_stores=None, extra_clients=None):
    """The same gateway built in code, with Gateway(..., audiences=)."""
    _, _, tenants, _ = KINDS[kind]
    target, declared = ENTRIES[entry]
    stores = dict({"s": InMemoryBackend()}, **(extra_stores or {}))
    clients = dict({"c": client_policy(kind, entry)}, **(extra_clients or {}))
    audiences = {} if declared is None else {("s" if target == "s" else "g"): declared}
    return Gateway(stores, clients, tokens_of(clients), tenants={"g": MemTenants()} if tenants else None,
                   audiences=audiences, log=lambda line: None)


class Built(unittest.TestCase):
    def build(self, cfg, env=None, **kw):
        self.logs = []
        gw = config.build_gateway(cfg, env=ENV if env is None else env, log=self.logs.append, **kw)
        self.addCleanup(gw.close)
        return gw

    def code(self, **kw):
        gw = Gateway(log=lambda line: None, **kw)
        self.addCleanup(gw.close)
        return gw

    def refused(self, build, *needles):
        with self.assertRaises(ConfigError) as caught:
            build()
        message = str(caught.exception)
        for needle in needles:
            self.assertIn(needle, message)
        for secret in (TOKEN, ENV["TOKEN_OWNER"], MARKER):
            self.assertNotIn(secret, message)
        return message


class MatrixTests(Built):
    def test_the_table_covers_every_combination(self):
        self.assertEqual(len(MATRIX), len(KINDS) * len(ENTRIES))

    def test_the_matrix_through_the_configuration_and_through_the_code(self):
        for (kind, entry), allowed in MATRIX.items():
            for how in ("config", "code"):
                with self.subTest(kind=kind, entry=entry, how=how):
                    if how == "config":
                        build = lambda: config.build_gateway(config_for(kind, entry), env=ENV, log=lambda line: None)
                    else:
                        build = lambda: gateway_for(kind, entry)
                    if allowed:
                        build().close()
                    else:
                        with self.assertRaises(ConfigError):
                            build().close()


class MessageTests(Built):
    def test_shared_store_that_declared_nothing(self):
        message = self.refused(lambda: gateway_for("group", "store undeclared"), "clients.c.recall",
                               "the store 's'", 'whose audience is "owner" (the default: it declares none)',
                               'the client\'s audience is "group"', "set audience on [stores.s]")
        self.assertIn("take the store out of recall", message)

    def test_shared_store_that_declared_owner(self):
        message = self.refused(lambda: gateway_for("public", "store owner"), "clients.c.recall", "the store 's'",
                               'whose audience is "owner", but', 'client\'s audience is "public"')
        self.assertNotIn("the default", message)

    def test_tenant_store(self):
        self.refused(lambda: gateway_for("group+tenants", "tenant owner"), "clients.c.recall",
                     'names "@tenant", and the audience of tenants.g is "owner", but', 'audience is "group"',
                     "set audience on [tenants.g]")

    def test_tenant_store_default_is_said(self):
        # the default of a tenants provider is "group", which a "public" client may not read
        gw_args = dict(stores={}, clients={"c": ClientPolicy("c", recall=("@tenant",), tenants="g", public=True,
                                                             audience="public")},
                       tokens={"c": TOKEN}, tenants={"g": MemTenants()})
        self.refused(lambda: Gateway(log=lambda line: None, **gw_args),
                     'the audience of tenants.g is "group" (the default: it declares none)')

    def test_group_store_for_a_client_serving_every_group(self):
        self.refused(lambda: gateway_for("group+tenants", "store group"), "clients.c.recall", "the store 's'",
                     'whose audience is "group" (one group)', "every tenant of 'g'",
                     'must have audience "public"')

    def test_audience_without_public(self):
        for level in ("group", "public"):
            with self.subTest(level=level):
                cfg = config_for("owner", "store public")
                cfg["clients"]["c"].update(audience=level, public=False)
                self.refused(lambda: config.build_gateway(cfg, env=ENV, log=lambda line: None),
                             'clients.c.audience is "%s" but public is false' % level,
                             "shows what it recalls to other people")

    def test_owner_with_public(self):
        cfg = config_for("owner", "store public")
        cfg["clients"]["c"].update(audience="owner", public=True)
        self.refused(lambda: config.build_gateway(cfg, env=ENV, log=lambda line: None),
                     'clients.c.audience is "owner" but public is true: an owner-level client is not public')

    def test_values_outside_the_three_levels(self):
        choices = 'must be one of "owner", "group", "public"'
        cfg = config_for("owner", "store public")
        cfg["clients"]["c"]["audience"] = "everyone"
        self.refused(lambda: config.build_gateway(cfg, env=ENV, log=lambda line: None), "clients.c.audience", choices)
        cfg = config_for("owner", "store public")
        cfg["stores"]["s"]["audience"] = "everyone"
        self.refused(lambda: config.build_gateway(cfg, env=ENV, log=lambda line: None), "stores.s.audience", choices)
        cfg = config_for("owner+tenants", "tenant group")
        cfg["tenants"]["g"]["audience"] = ["group"]
        self.refused(lambda: config.build_gateway(cfg, env=ENV, log=lambda line: None), "tenants.g.audience", choices)
        self.refused(lambda: self.code(stores={"s": InMemoryBackend()}, clients={"c": ClientPolicy("c", audience="x")},
                                       tokens={}),
                     "clients.c.audience", choices)
        self.refused(lambda: self.code(stores={"s": InMemoryBackend()}, clients={}, tokens={},
                                       audiences={"s": "x"}), "stores.s.audience", choices)
        self.refused(lambda: self.code(stores={}, clients={}, tokens={}, tenants={"g": MemTenants()},
                                       audiences={"g": "x"}), "tenants.g.audience", choices)

    def test_audiences_naming_something_else(self):
        self.refused(lambda: self.code(stores={"s": InMemoryBackend()}, clients={}, tokens={},
                                       audiences={"nope": "public"}),
                     "audiences names nope, which is neither a store nor a tenants provider")


class DerivationTests(Built):
    def policy(self, **spec):
        return config.client_policy("c", dict({"token_env": "TOKEN_C"}, **spec))

    def test_defaults(self):
        for spec, public, reach in (({}, False, "owner"), ({"public": True}, True, "public"),
                                    ({"public": True, "tenants": "g"}, True, "group"),
                                    ({"tenants": "g"}, False, "owner")):
            with self.subTest(spec=spec):
                policy = self.policy(**spec)
                self.assertEqual((policy.public, policy.reach, policy.audience), (public, reach, None))

    def test_public_is_derived_from_audience(self):
        for level, public in (("owner", False), ("group", True), ("public", True)):
            with self.subTest(level=level):
                policy = self.policy(audience=level)
                self.assertEqual((policy.public, policy.reach, policy.audience), (public, level, level))

    def test_a_stated_audience_wins_over_the_default(self):
        policy = self.policy(public=True, tenants="g", audience="public")
        self.assertEqual(policy.reach, "public")
        self.assertEqual(self.policy(public=True, audience="group").reach, "group")

    def test_disagreements_are_refused(self):
        for audience, public in (("owner", True), ("group", False), ("public", False)):
            with self.subTest(audience=audience, public=public):
                cfg = config_for("owner", "store public")
                cfg["clients"]["c"].update(audience=audience, public=public)
                with self.assertRaises(ConfigError):
                    config.build_gateway(cfg, env=ENV, log=lambda line: None)
                with self.assertRaises(ConfigError):
                    self.code(stores={"s": InMemoryBackend()}, tokens={},
                              clients={"c": ClientPolicy("c", recall=("s",), public=public, audience=audience)},
                              audiences={"s": "public"})

    def test_agreement_is_accepted(self):
        for audience, public in (("owner", False), ("group", True), ("public", True)):
            with self.subTest(audience=audience):
                cfg = config_for("owner", "store public")
                cfg["clients"]["c"].update(audience=audience, public=public)
                self.build(cfg)

    def test_the_declared_audience_is_policy_data_and_reach_is_last_field(self):
        self.assertEqual(AUDIENCES, ("owner", "group", "public"))
        self.assertEqual(ClientPolicy("c", None, None, None, True, None, 60, False, None, None, "request", 1,
                                      "group").reach, "group")


class ParsingTests(Built):
    def tmpdir(self):
        """A folder removed after the gateway is closed (a sqlite file cannot be deleted while open)."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return folder.name

    def test_the_key_does_not_reach_the_backend(self):
        tmp = self.tmpdir()
        cfg = {"stores": {"mem": {"type": "inmemory", "audience": "public"},
                          "lite": {"type": "sqlite", "path": str(Path(tmp) / "a.db"), "audience": "group"}},
               "clients": {"c": {"token_env": "TOKEN_C", "recall": ["mem"], "audience": "public"}}}
        gw = self.build(cfg)
        self.assertEqual((gw.audience_of("mem"), gw.audience_of("lite")), ("public", "group"))

    def test_defaults_of_audience_of(self):
        gw = gateway_for("owner+tenants", "tenant undeclared")
        self.addCleanup(gw.close)
        self.assertEqual((gw.audience_of("s"), gw.audience_of("g")), ("owner", "group"))
        with self.assertRaises(KeyError):
            gw.audience_of("nope")

    def test_the_callers_dict_is_not_mutated(self):
        cfg = config_for("group+tenants", "tenant group")
        cfg["stores"]["s"]["audience"] = "public"
        cfg["tenants"]["g"]["audience"] = "group"
        before = copy.deepcopy(cfg)
        self.build(cfg)
        self.assertEqual(cfg, before)

    def test_a_wrong_value_names_the_setting(self):
        tmp = self.tmpdir()
        cfg = {"stores": {"lite": {"type": "sqlite", "path": str(Path(tmp) / "a.db"), "audience": "all"}},
               "clients": {"c": {"token_env": "TOKEN_C", "recall": []}}}
        self.refused(lambda: config.build_gateway(cfg, env=ENV, log=lambda line: None), "stores.lite.audience")


class IsolationTests(Built):
    """The owner's marker lives in an owner-level store; no allowed non-owner client ever sees it."""

    def setUp(self):
        self.servers = []

    def run_row(self, kind, entry, how):
        private = InMemoryBackend()
        owner = ClientPolicy("owner", recall=("private",), write="private")
        if how == "config":
            cfg = config_for(kind, entry, {"private": {"type": "inmemory", "audience": "owner"}})
            cfg["clients"]["owner"] = {"token_env": "TOKEN_OWNER", "recall": ["private"], "write": "private"}
            gw = self.build(cfg)
            private = gw.stores["private"]
        else:
            gw = gateway_for(kind, entry, {"private": private}, {"owner": owner})
            self.addCleanup(gw.close)
        self.assertEqual(gw.remember("owner", {"messages": [{"role": "user", "content": MARKER}]}).status, 200)
        self.assertTrue(private.find(MARKER, 5))
        target = ENTRIES[entry][0]
        store = gw.stores["s"] if target == "s" else gw.tenants["g"].store(GROUP_ID)
        store.put(FACT)
        body = {"query": MARKER}
        if KINDS[kind][2]:
            body["tenant_id"] = GROUP_ID
        reply = gw.recall("c", body)
        self.assertEqual(reply.status, 200)
        self.assertNotIn("zebra", json.dumps(reply.body, ensure_ascii=False))
        self.assertFalse([r for r in reply.body["results"] if r["source"] == "private"])
        body["query"] = FACT
        found = gw.recall("c", body).body["results"]
        self.assertTrue(found)  # the client does read what it is allowed to read
        self.assertNotIn("private", {r["source"] for r in found})

    def test_every_allowed_non_owner_row(self):
        rows = [(k, e) for (k, e), allowed in MATRIX.items() if allowed and KINDS[k][3] != "owner"]
        self.assertTrue(rows)
        for kind, entry in rows:
            for how in ("config", "code"):
                with self.subTest(kind=kind, entry=entry, how=how):
                    self.run_row(kind, entry, how)

    def test_over_http(self):
        private = InMemoryBackend()
        owner = ClientPolicy("owner", recall=("private",), write="private")
        gw = Gateway({"s": InMemoryBackend(), "private": private},
                     {"owner": owner, "c": client_policy("public", "store public")},
                     {"owner": ENV["TOKEN_OWNER"], "c": TOKEN}, audiences={"s": "public"}, log=lambda line: None)
        self.addCleanup(gw.close)
        gw.stores["s"].put(FACT)
        server = gateway_server.make_server(gw, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(5)

        self.addCleanup(stop)

        def post(path, body, token):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            try:
                conn.request("POST", path, body=json.dumps(body).encode(),
                             headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
                resp = conn.getresponse()
                return resp.status, json.loads(resp.read())
            finally:
                conn.close()

        status, _ = post("/v1/remember", {"messages": [{"role": "user", "content": MARKER}]}, ENV["TOKEN_OWNER"])
        self.assertEqual(status, 200)
        status, body = post("/v1/recall", {"query": MARKER}, TOKEN)
        self.assertEqual(status, 200)
        self.assertNotIn("zebra", json.dumps(body))
        status, body = post("/v1/recall", {"query": FACT}, TOKEN)
        self.assertEqual([r["source"] for r in body["results"]][:1], ["s"])


class ExampleTests(unittest.TestCase):
    def test_no_example_shows_an_owner_level_store_to_other_people(self):
        files = sorted((ROOT / "examples").glob("*/gateway.toml"))
        self.assertTrue(files)
        keep_sys_path(self)
        for path in files:
            with self.subTest(path=path.parent.name), tempfile.TemporaryDirectory() as tmp:
                cfg = config.load(path)
                for section in ("stores", "tenants"):
                    for name, spec in cfg.get(section, {}).items():
                        cfg[section][name] = _move_paths(spec, tmp)
                for spec in cfg.get("tenants", {}).values():
                    if spec.get("type") == "keyfile":
                        Path(spec["path"]).write_text("{}", encoding="utf-8")
                env = {client["token_env"]: "example-test-token-%02d-" % i + "x" * 32
                       for i, client in enumerate(cfg["clients"].values())}
                for var in _env_names(cfg):
                    env.setdefault(var, "fake-key-for-example-tests")
                gw = config.build_gateway(cfg, env=env, log=lambda line: None)
                try:
                    for name, policy in gw.clients.items():
                        if policy.reach == "owner":
                            continue
                        for entry in policy.recall or ():
                            source = policy.tenants if entry == "@tenant" else entry
                            self.assertNotEqual(gw.audience_of(source), "owner", (path.parent.name, name, entry))
                finally:
                    gw.close()


class StageTests(Built):
    def test_a_stage_sees_the_audience_of_the_client(self):
        seen = []

        class Look(RecallStage):
            def prepare(self, candidates, ctx):
                seen.append((ctx.client, ctx.audience))
                return candidates

        s = InMemoryBackend(memories=[FACT])
        clients = {"o": ClientPolicy("o", recall=("s",)), "g": ClientPolicy("g", recall=("s",), public=True, audience="group"),
                   "p": ClientPolicy("p", recall=("s",), public=True),
                   "t": ClientPolicy("t", recall=("s",), public=True, tenants="g")}
        gw = Gateway({"s": s}, clients, {}, tenants={"g": MemTenants()}, audiences={"s": "public"}, stages=[Look()],
                     log=lambda line: None)
        self.addCleanup(gw.close)
        for name in clients:
            body = {"query": FACT}
            if name == "t":
                body["tenant_id"] = GROUP_ID
            self.assertEqual(gw.recall(name, body).status, 200)
        self.assertEqual(seen, [("o", "owner"), ("g", "group"), ("p", "public"), ("t", "group")])
        self.assertEqual([a for _, a in seen], [clients[n].reach for n in clients])


class CheckTests(Built):
    def gateway(self):
        private, shared = InMemoryBackend(), InMemoryBackend()
        clients = {"owner": ClientPolicy("owner", recall=("private", "shared"), write="private"),
                   "curator": ClientPolicy("curator", write="shared"),
                   "bot": ClientPolicy("bot", recall=("shared",), write="private", public=True, audience="public"),
                   "quiet": ClientPolicy("quiet", recall=("shared",), write="shared", public=True),
                   "group": ClientPolicy("group", recall=("@tenant",), write="@tenant", tenants="g", public=True)}
        gw = Gateway({"private": private, "shared": shared}, clients, tokens_of(clients),
                     tenants={"g": MemTenants()}, audiences={"shared": "public"}, log=lambda line: None)
        self.addCleanup(gw.close)
        return gw

    def test_audiences_are_shown(self):
        text = "\n".join(cli.describe(self.gateway()))
        self.assertIn("private: inmemory，可写；受众 主人", text)
        self.assertIn("shared: inmemory，可写；受众 公开", text)
        self.assertIn("g: audience_test_mem；受众 一个群", text)
        by_name = {line.split(":")[0].strip(): line for line in text.splitlines() if "召回" in line and "；令牌" in line}
        self.assertIn("受众 主人", by_name["owner"])
        self.assertIn("受众 公开", by_name["bot"])
        self.assertIn("受众 公开", by_name["quiet"])
        self.assertIn("受众 一个群", by_name["group"])

    def test_the_reminder_only_for_a_non_owner_client_writing_into_an_owner_store(self):
        lines = [line for line in cli.describe(self.gateway()) if "提醒：" in line and "只给主人的存储" in line]
        self.assertEqual(lines, [
            "  提醒：bot 的内容会被别人看到，写入的却是只给主人的存储 private：别人说的话会进到主人的私聊记忆里；"
            "确认这是你要的，否则换一个存储"])


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGateway()
        self.addCleanup(self.fake.close)
        self.client = MemoryClient(self.fake.url, "tok-owner-fake")

    def last(self):
        return self.fake.requests[-1]["body"]

    def test_http_client_sends_context_when_given(self):
        context = {"channel": "dsh", "chat": "private", "via": "voice"}
        calls = (
            lambda: self.client.recall("hello", context=context),
            lambda: self.client.recall_detail("hello", context=context),
            lambda: self.client.remember("hello", context=context),
            lambda: self.client.remember_status("hello", context=context),
            lambda: self.client.remember_messages([{"role": "user", "content": "hello"}], context=context),
        )
        for call in calls:
            call()
            self.assertEqual(self.last()["context"], context)
        self.assertEqual(len(self.fake.requests), len(calls))

    def test_http_client_body_is_unchanged_without_it(self):
        self.client.recall("hello")
        self.assertEqual(self.last(), {"query": "hello", "limit": 5})
        self.client.recall_detail("hello", context=None)
        self.assertEqual(self.last(), {"query": "hello", "limit": 5})
        self.client.remember("hello")
        self.assertEqual(self.last(), {"messages": [{"role": "user", "content": "hello"}]})

    def test_context_is_keyword_only(self):
        with self.assertRaises(TypeError):
            self.client.recall("hello", 8.5, None, {"channel": "x"})

    def test_local_client_sends_context_when_given_and_nothing_when_not(self):
        seen = []

        class Look(RecallStage):
            def prepare(self, candidates, ctx):
                seen.append(("recall", ctx.context))
                return candidates

        class LookGuard(WriteGuard):
            def before(self, messages, ctx):
                seen.append(("remember", ctx.context))
                return messages

        gw = Gateway({"s": InMemoryBackend(memories=[FACT])}, {"o": ClientPolicy("o", recall=("s",), write="s")}, {},
                     stages=[Look()], guards=[LookGuard()], log=lambda line: None)
        client = LocalClient(gw, client="o", close_gateway=True)
        self.addCleanup(client.close)
        context = {"channel": "voice", "chat": "group"}
        self.assertTrue(client.recall(FACT, context=context))
        self.assertEqual(client.remember_status("likes tea", context=context), "saved")
        self.assertTrue(client.recall_detail(FACT, context=context))
        self.assertEqual(client.remember_messages([{"role": "user", "content": "likes tea a lot"}], context=context),
                         "saved")
        self.assertEqual([c for _, c in seen], [context] * 4)
        seen.clear()
        client.recall(FACT)
        client.remember("likes coffee")
        self.assertEqual([c for _, c in seen], [{}, {}])

    def test_a_wrong_context_is_the_gateways_400_like_any_failure(self):
        gw = Gateway({"s": InMemoryBackend(memories=[FACT])}, {"o": ClientPolicy("o", recall=("s",), write="s")}, {},
                     log=lambda line: None)
        client = LocalClient(gw, client="o", close_gateway=True)
        self.addCleanup(client.close)
        with self.assertLogs("feiyu_memgate.client", "WARNING"):
            self.assertEqual(client.recall(FACT, context={"k": [1]}), [])


if __name__ == "__main__":
    unittest.main()
