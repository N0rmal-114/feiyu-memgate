import os
import sys
import tempfile
import unittest

from feiyu_memgate import config
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.backends.openviking import OpenVikingBackend
from feiyu_memgate.core import stages, write
from feiyu_memgate.errors import ConfigError
from feiyu_memgate.gateway.app import Gateway
from feiyu_memgate.model import ClientPolicy

TOKEN_OWNER = "tok-owner-fake-".ljust(40, "0")
TOKEN_LIVE = "tok-live-fake-".ljust(40, "0")
SECRET_KEY = "secret-store-key-fake"


def base_config(**extra):
    cfg = {
        "stores": {
            "private": {"type": "inmemory", "memories": ["likes green tea"]},
            "shared": {"type": "inmemory", "writable": False},
        },
        "clients": {
            "owner": {"token_env": "TOKEN_OWNER", "recall": ["private", "shared"], "write": "private",
                      "daily_writes": 7},
            "live": {"token_env": "TOKEN_LIVE", "recall": [], "public": True},
        },
    }
    cfg.update(extra)
    return cfg


ENV = {"TOKEN_OWNER": TOKEN_OWNER, "TOKEN_LIVE": TOKEN_LIVE}


class ConfigTestCase(unittest.TestCase):
    def build(self, cfg, env=None, **kw):
        self.logs = []
        gw = config.build_gateway(cfg, env=ENV if env is None else env, log=self.logs.append, **kw)
        self.addCleanup(gw.close)
        return gw


class BuildGatewayTests(ConfigTestCase):
    def test_build_from_a_dict(self):
        gw = self.build(base_config())
        self.assertIsInstance(gw, Gateway)
        self.assertEqual(set(gw.stores), {"private", "shared"})
        self.assertIsInstance(gw.stores["private"], InMemoryBackend)
        self.assertEqual(gw.clients["owner"], ClientPolicy(
            "owner", recall=("private", "shared"), write="private", daily_writes=7))
        self.assertEqual(gw.clients["live"], ClientPolicy("live", recall=(), public=True))
        self.assertEqual(gw.authenticate(TOKEN_OWNER), "owner")
        self.assertEqual(gw.authenticate(TOKEN_LIVE), "live")

    def test_defaults_are_the_built_in_stages_and_guards(self):
        gw = self.build(base_config())
        self.assertEqual([type(s) for s in gw.stages],
                         [stages.MinScore, stages.RelativeScore, stages.NewerFirst, stages.Linked, stages.FillText,
                          stages.DropOversize, stages.Redact, stages.Dedupe, stages.Budget])
        self.assertEqual([s.name for s in gw.stages],
                         ["min_score", "relative_score", "newer_first", "linked", "fill_text", "drop_oversize",
                          "redact", "dedupe", "budget"])
        self.assertEqual([type(g) for g in gw.guards], [write.RedactGuard, write.DailyCapGuard])

    def test_the_gateway_works_end_to_end(self):
        gw = self.build(base_config())
        reply = gw.recall("owner", {"query": "green tea"})
        self.assertEqual(reply.status, 200)
        self.assertEqual([r["text"] for r in reply.body["results"]], ["likes green tea"])
        self.assertEqual(gw.remember("owner", {"messages": [{"role": "user", "content": "I like jam"}]}).status, 200)

    def test_custom_stage_list_with_inline_table_options(self):
        cfg = base_config(recall={"stages": [
            {"type": "min_score", "value": 0.2, "per_store": {"shared": 0.9}},
            "dedupe",
            {"type": "budget", "max_chars": 50},
        ]})
        del cfg["clients"]["live"]  # a public client would need a redact stage
        gw = self.build(cfg)
        self.assertEqual([type(s) for s in gw.stages], [stages.MinScore, stages.Dedupe, stages.Budget])
        self.assertEqual(gw.stages[0].value, 0.2)
        self.assertEqual(gw.stages[0].per_store, {"shared": 0.9})
        self.assertEqual(gw.stages[2].max_chars, 50)

    def test_custom_guard_list_and_options(self):
        cfg = base_config(write={"guards": [{"type": "daily_cap", "utc_offset_hours": 8}], "deadline": 4})
        gw = self.build(cfg)
        self.assertEqual([type(g) for g in gw.guards], [write.DailyCapGuard])
        self.assertEqual(gw.remember_deadline, 4)

    def test_recall_settings_and_overrides(self):
        clock = lambda: 5.0  # noqa: E731
        gw = self.build(base_config(recall={"deadline": 1.5, "cold_deadline": 6, "cold_after": 30}), clock=clock)
        self.assertEqual((gw.recall_deadline, gw.cold_recall_deadline, gw.cold_after), (1.5, 6, 30))
        self.assertIs(gw.clock, clock)

    def test_redaction_choice(self):
        from feiyu_memgate.core import redact
        gw = self.build(base_config(redaction={"private": "none"}))
        self.assertIs(gw.redactors["private"], redact.no_redaction)
        self.assertIs(gw.redactors["public"], redact.redact_public)

    def test_unknown_stage_or_store_type_is_a_config_error_listing_names(self):
        with self.assertRaises(ConfigError) as ctx:
            self.build(base_config(recall={"stages": ["min_score", "no_such_stage"]}))
        self.assertIn("no_such_stage", str(ctx.exception))
        self.assertIn("dedupe", str(ctx.exception))
        cfg = base_config()
        cfg["stores"]["private"] = {"type": "no_such_backend"}
        with self.assertRaises(ConfigError) as ctx:
            self.build(cfg)
        self.assertIn("no_such_backend", str(ctx.exception))

    def test_a_custom_stage_by_dotted_path(self):
        cfg = base_config(recall={"stages": [{"type": __name__ + ":Tagging", "tag": "x"}, "redact"]})
        gw = self.build(cfg)
        self.assertIsInstance(gw.stages[0], Tagging)
        self.assertEqual(gw.stages[0].tag, "x")

    def test_unknown_section(self):
        with self.assertRaises(ConfigError) as ctx:
            self.build(dict(base_config(), servers={}))
        self.assertIn("servers", str(ctx.exception))

    def test_unknown_client_setting(self):
        cfg = base_config()
        cfg["clients"]["owner"]["recal"] = ["private"]
        with self.assertRaises(ConfigError) as ctx:
            self.build(cfg)
        self.assertIn("clients.owner", str(ctx.exception))
        self.assertIn("recal", str(ctx.exception))

    def test_client_setting_types_are_checked(self):
        for key, value in (("recall", "private"), ("recall", [1]), ("daily_writes", "50"), ("daily_writes", True),
                           ("public", "yes"), ("write", 3), ("daily_writes", 0), ("rate_limit", -1),
                           ("rate_limit_total", 1.5), ("daily_writes_total", "x")):
            cfg = base_config()
            cfg["clients"]["owner"][key] = value
            with self.assertRaises(ConfigError, msg=(key, value)) as ctx:
                self.build(cfg)
            self.assertIn("clients.owner.%s" % key, str(ctx.exception))

    def test_no_clients_and_missing_token_env(self):
        with self.assertRaises(ConfigError):
            self.build({"stores": {}})
        # Without token_env a client is a policy with no token: in-process use only.
        cfg = base_config()
        del cfg["clients"]["owner"]["token_env"]
        gw = self.build(cfg)
        self.assertIn("owner", gw.clients)
        self.assertFalse(gw.has_token("owner"))
        self.assertIsNone(gw.authenticate(TOKEN_OWNER))
        for bad in (5, "", ["TOKEN_OWNER"]):
            cfg = base_config()
            cfg["clients"]["owner"]["token_env"] = bad
            with self.assertRaises(ConfigError) as ctx:
                self.build(cfg)
            self.assertIn("clients.owner.token_env", str(ctx.exception))

    def test_tables_must_be_tables(self):
        with self.assertRaises(ConfigError):
            self.build(dict(base_config(), stores=[]))
        with self.assertRaises(ConfigError):
            self.build(dict(base_config(), stores={"private": "inmemory"}))
        with self.assertRaises(ConfigError):
            self.build("not a dict")

    def test_a_tenants_provider_cannot_share_a_name_with_a_store(self):
        cfg = base_config(tenants={"private": {"type": "keyfile", "path": "keys.json",
                                               "store": {"type": "inmemory"}}})
        with self.assertRaises(ConfigError) as ctx:
            self.build(cfg)
        self.assertIn("tenants.private", str(ctx.exception))


class Tagging(stages.RecallStage):
    """A custom stage for the dotted-path test."""

    def __init__(self, tag="t"):
        self.tag = tag


STAGE_MODULE = """from feiyu_memgate.pieces import RecallStage


class Nearby(RecallStage):
    def __init__(self, word="x"):
        self.word = word
"""


class EnforcedTests(ConfigTestCase):
    """A client setting that no configured piece enforces is refused (A)."""

    def assert_rejected(self, cfg, *words):
        with self.assertRaises(ConfigError) as ctx:
            self.build(cfg)
        for word in words:
            self.assertIn(word, str(ctx.exception))

    def test_daily_writes_without_a_daily_cap_guard(self):
        self.assert_rejected(base_config(write={"guards": ["redact"]}), "clients.owner", "daily_cap")

    def test_daily_writes_total_needs_the_guard_too(self):
        cfg = base_config(write={"guards": ["redact"]})
        del cfg["clients"]["owner"]["daily_writes"]
        cfg["clients"]["owner"]["daily_writes_total"] = 100
        self.assert_rejected(cfg, "clients.owner", "daily_cap")

    def test_a_public_client_that_may_recall_needs_a_redacting_stage(self):
        self.assert_rejected(base_config(recall={"stages": ["min_score", "dedupe"]}), "clients.live", "redact")

    def test_a_public_client_that_may_write_needs_a_redacting_guard(self):
        cfg = base_config(write={"guards": ["daily_cap"]})
        cfg["clients"]["live"]["write"] = "private"
        self.assert_rejected(cfg, "clients.live", "redact")

    def test_a_public_client_that_may_not_write_needs_no_guard(self):
        gw = self.build(base_config(write={"guards": ["daily_cap"]}))
        self.assertEqual(len(gw.guards), 1)

    def test_a_custom_piece_can_declare_what_it_enforces(self):
        cfg = base_config(recall={"stages": [__name__ + ":OwnRedaction"]},
                          write={"guards": [__name__ + ":OwnCap", "redact"]})
        gw = self.build(cfg)
        self.assertIsInstance(gw.stages[0], OwnRedaction)

    def test_the_gateway_built_by_hand_is_not_restricted(self):
        Gateway({"a": InMemoryBackend()}, {"c": ClientPolicy("c", recall=("a",), write="a", public=True,
                                                             daily_writes=3)},
                {"c": TOKEN_OWNER}, stages=[], guards=[], log=lambda line: None).close()

    def test_the_built_in_pieces_declare_what_they_enforce(self):
        from feiyu_memgate import registry
        self.assertEqual(registry.create("recall_stage", "redact").enforces, ("redaction",))
        self.assertEqual(registry.create("write_guard", "redact").enforces, ("redaction",))
        self.assertEqual(registry.create("write_guard", "daily_cap").enforces, ("daily_writes",))


class OwnRedaction(stages.RecallStage):
    enforces = ("redaction",)


class OwnCap(write.WriteGuard):
    enforces = ("daily_writes",)


class TokenAndTenantPolicyTests(ConfigTestCase):
    def test_two_clients_with_the_same_token_are_refused_naming_both(self):
        with self.assertRaises(ConfigError) as ctx:
            self.build(base_config(), env={"TOKEN_OWNER": TOKEN_OWNER, "TOKEN_LIVE": TOKEN_OWNER})
        message = str(ctx.exception)
        self.assertIn("live", message)
        self.assertIn("owner", message)
        self.assertNotIn(TOKEN_OWNER, message)

    def test_the_tenant_store_in_recall_needs_tenants(self):
        cfg = base_config()
        cfg["clients"]["owner"]["recall"] = ["private", "@tenant"]
        with self.assertRaises(ConfigError) as ctx:
            self.build(cfg)
        self.assertIn("clients.owner.recall", str(ctx.exception))
        self.assertIn("@tenant", str(ctx.exception))

    def test_recall_sources_follow_the_order_of_the_list(self):
        cfg = base_config()
        cfg["stores"]["shared"]["memories"] = ["likes green tea a lot"]
        cfg["recall"] = {"stages": [{"type": "min_score", "value": 0.0}, "redact"]}
        cfg["clients"]["owner"]["recall"] = ["shared", "private"]
        gw = self.build(cfg)
        self.assertEqual(list(gw._recall_stores(gw.clients["owner"], None)), ["shared", "private"])


class SectionValidationTests(ConfigTestCase):
    """Unknown keys, wrong types and values out of range are refused when the gateway is built (K)."""

    def assert_rejected(self, cfg, *words):
        with self.assertRaises(ConfigError) as ctx:
            self.build(cfg)
        for word in words:
            self.assertIn(word, str(ctx.exception))

    def test_unknown_keys_in_every_fixed_section(self):
        for section, key in (("server", "pool"), ("recall", "stage"), ("write", "guard"), ("redaction", "privat")):
            with self.subTest(section=section):
                self.assert_rejected(base_config(**{section: {key: 1}}), key)

    def test_a_misspelt_stage_list_no_longer_restores_the_defaults_silently(self):
        self.assert_rejected(base_config(recall={"stage": ["min_score"]}), "stage")

    def test_types_and_ranges(self):
        cases = (
            ("recall", "deadline", "3"), ("recall", "deadline", 0), ("recall", "deadline", True),
            ("recall", "cold_deadline", -1), ("recall", "cold_after", "x"), ("write", "deadline", "10"),
            ("server", "pool_size", 0), ("server", "pool_size", 2.5), ("server", "tenant_pool_total", "3"),
            ("server", "tenant_pool_per_tenant", 0), ("server", "store_max_inflight", -2), ("server", "port", 70000),
            ("server", "host", ""), ("server", "max_connections", 0), ("server", "read_deadline", "10"),
        )
        for section, key, value in cases:
            with self.subTest(setting=(section, key, value)):
                self.assert_rejected(base_config(**{section: {key: value}}), "%s.%s" % (section, key))

    def test_the_cold_deadline_is_never_shorter_than_the_eight_second_default(self):
        gw = self.build(base_config(recall={"deadline": 2.0}))
        self.assertEqual((gw.recall_deadline, gw.cold_recall_deadline), (2.0, 8.0))
        gw = self.build(base_config(recall={"deadline": 12}))
        self.assertEqual(gw.cold_recall_deadline, 12)
        gw = self.build(base_config(recall={"deadline": 2.0, "cold_deadline": 3}))
        self.assertEqual(gw.cold_recall_deadline, 3)

    def test_redactors_may_be_tables_with_options_from_the_environment(self):
        from feiyu_memgate import registry
        registry.register("redactor", "test-masking")(make_masking)
        self.addCleanup(lambda: registry._pieces["redactor"].pop("test-masking", None))
        cfg = base_config(redaction={"public": {"type": "test-masking", "word_env": "MASK_WORD"}})
        gw = self.build(cfg, env=dict(ENV, MASK_WORD="tea"))
        self.assertEqual(gw.redactors["public"]("green tea"), "green ***")
        with self.assertRaises(ConfigError):
            self.build(cfg, env=ENV)  # MASK_WORD is not set
        with self.assertRaises(ConfigError):
            self.build(base_config(redaction={"private": 5}))

    def test_store_max_inflight_and_tenant_budget_reach_the_gateway(self):
        gw = self.build(base_config(server={"pool_size": 6, "store_max_inflight": 2, "tenant_pool_total": 3}))
        self.assertEqual((gw.store_slots.limit, gw.tenant_budget.total, gw.tenant_keep), (2, 3, 3))
        gw = self.build(base_config(server={"pool_size": 8}))
        self.assertEqual((gw.store_slots.limit, gw.tenant_budget.total, gw.tenant_budget.per_tenant), (6, 6, 2))

    def test_new_policy_settings_are_read(self):
        cfg = base_config()
        cfg["clients"]["owner"].update({"daily_writes_total": 70, "rate_limit": 5, "rate_limit_total": 9})
        policy = self.build(cfg).clients["owner"]
        self.assertEqual((policy.daily_writes_total, policy.rate_limit, policy.rate_limit_total), (70, 5, 9))
        self.assertEqual(ClientPolicy("c", rate_limit=7).total_rate_limit, 70)

    def test_min_score_uses_each_stores_own_default(self):
        gw = self.build(base_config())
        self.assertIsNone(gw.stages[0].value)


def make_masking(word="x"):
    return lambda text: text.replace(word, "*" * len(word))


class EnvOptionTests(ConfigTestCase):
    def test_env_option_becomes_the_plain_option(self):
        out = config.resolve_env({"url": "http://127.0.0.1:1", "key_env": "STORE_KEY"}, {"STORE_KEY": SECRET_KEY},
                                 "stores.channel")
        self.assertEqual(out, {"url": "http://127.0.0.1:1", "key": SECRET_KEY})

    def test_nested_tables_are_resolved_and_token_env_is_left_alone(self):
        out = config.resolve_env({"store": {"key_env": "STORE_KEY", "type": "openviking"}, "token_env": "T"},
                                 {"STORE_KEY": SECRET_KEY}, "tenants.group")
        self.assertEqual(out, {"store": {"key": SECRET_KEY, "type": "openviking"}, "token_env": "T"})

    def test_missing_variable_names_the_setting_and_never_a_value(self):
        env = {"OTHER_SECRET": SECRET_KEY}
        with self.assertRaises(ConfigError) as ctx:
            config.resolve_env({"key_env": "STORE_KEY"}, env, "stores.channel")
        message = str(ctx.exception)
        self.assertIn("stores.channel.key_env", message)
        self.assertIn("STORE_KEY", message)
        self.assertNotIn(SECRET_KEY, message)

    def test_empty_variable_counts_as_missing(self):
        with self.assertRaises(ConfigError):
            config.resolve_env({"key_env": "STORE_KEY"}, {"STORE_KEY": ""}, "stores.channel")

    def test_non_string_env_name_is_an_error(self):
        with self.assertRaises(ConfigError):
            config.resolve_env({"key_env": 5}, {}, "stores.channel")

    def test_build_gateway_resolves_store_keys_from_the_environment(self):
        cfg = base_config()
        cfg["stores"]["channel"] = {"type": "openviking", "url": "http://127.0.0.1:9", "key_env": "STORE_KEY",
                                    "writable": True}
        cfg["clients"]["owner"]["recall"] = ["channel"]
        cfg["clients"]["owner"]["write"] = "channel"
        gw = self.build(cfg, env=dict(ENV, STORE_KEY=SECRET_KEY))
        store = gw.stores["channel"]
        self.assertIsInstance(store, OpenVikingBackend)
        self.assertTrue(store.writable)
        self.assertEqual(store._key, SECRET_KEY)

    def test_build_gateway_missing_store_key_is_a_config_error(self):
        cfg = base_config()
        cfg["stores"]["channel"] = {"type": "openviking", "url": "http://127.0.0.1:9", "key_env": "STORE_KEY"}
        with self.assertRaises(ConfigError) as ctx:
            self.build(cfg, env=dict(ENV, UNRELATED=SECRET_KEY))
        self.assertIn("stores.channel.key_env", str(ctx.exception))
        self.assertNotIn(SECRET_KEY, str(ctx.exception))

    def test_env_options_inside_a_stage_table(self):
        cfg = base_config(recall={"stages": [{"type": __name__ + ":Tagging", "tag_env": "TAG"}, "redact"]})
        gw = self.build(cfg, env=dict(ENV, TAG="from-env"))
        self.assertEqual(gw.stages[0].tag, "from-env")

    def test_default_environment_is_os_environ(self):
        os.environ["FEIYU_TEST_TOKEN_OWNER"] = TOKEN_OWNER
        self.addCleanup(os.environ.pop, "FEIYU_TEST_TOKEN_OWNER", None)
        cfg = base_config()
        cfg["clients"] = {"owner": {"token_env": "FEIYU_TEST_TOKEN_OWNER", "recall": ["private"]}}
        logs = []
        gw = config.build_gateway(cfg, log=logs.append)
        self.addCleanup(gw.close)
        self.assertEqual(gw.authenticate(TOKEN_OWNER), "owner")


class PolicyValidationTests(ConfigTestCase):
    def assert_rejected(self, cfg, *words):
        with self.assertRaises(ConfigError) as ctx:
            self.build(cfg)
        for word in words:
            self.assertIn(word, str(ctx.exception))

    def test_unknown_store_in_recall(self):
        cfg = base_config()
        cfg["clients"]["owner"]["recall"] = ["private", "nope"]
        self.assert_rejected(cfg, "clients.owner.recall", "nope")

    def test_unknown_store_in_write(self):
        cfg = base_config()
        cfg["clients"]["owner"]["write"] = "nope"
        self.assert_rejected(cfg, "clients.owner.write", "nope")

    def test_write_to_a_read_only_store(self):
        cfg = base_config()
        cfg["clients"]["owner"]["write"] = "shared"
        self.assert_rejected(cfg, "clients.owner.write", "read-only", "shared")

    def test_tenant_store_without_tenants(self):
        cfg = base_config()
        cfg["clients"]["owner"]["write"] = "@tenant"
        self.assert_rejected(cfg, "clients.owner.write", "@tenant", "no tenants")

    def test_unknown_tenants_provider(self):
        cfg = base_config()
        cfg["clients"]["owner"]["tenants"] = "group"
        self.assert_rejected(cfg, "clients.owner.tenants", "group")

    def test_gateway_itself_validates_policies(self):
        stores = {"a": InMemoryBackend(writable=False)}
        for policy in (ClientPolicy("c", recall=("zzz",)), ClientPolicy("c", write="zzz"),
                       ClientPolicy("c", write="a"), ClientPolicy("c", write="@tenant"),
                       ClientPolicy("c", tenants="zzz")):
            with self.assertRaises(ConfigError, msg=policy):
                Gateway(stores, {"c": policy}, {"c": TOKEN_OWNER}, log=lambda line: None)

    def test_a_valid_policy_passes(self):
        gw = Gateway({"a": InMemoryBackend()}, {"c": ClientPolicy("c", recall=("a",), write="a")},
                     {"c": TOKEN_OWNER}, log=lambda line: None)
        gw.close()


class UnusableTokenTests(ConfigTestCase):
    def test_placeholder_and_short_tokens_are_dropped_and_logged_by_client_name_only(self):
        short = "short-secret-xyz"
        gw = self.build(base_config(), env={"TOKEN_OWNER": "change-me", "TOKEN_LIVE": short})
        self.assertFalse(gw.has_token("owner"))
        self.assertFalse(gw.has_token("live"))
        self.assertIsNone(gw.authenticate("change-me"))
        self.assertIsNone(gw.authenticate(short))
        text = "\n".join(self.logs)
        self.assertIn("owner", text)
        self.assertIn("live", text)
        self.assertNotIn(short, text)
        self.assertIn("32", text)

    def test_a_token_just_under_the_minimum_is_dropped_and_one_at_the_minimum_is_kept(self):
        gw = self.build(base_config(), env={"TOKEN_OWNER": "a" * 31, "TOKEN_LIVE": "b" * 32})
        self.assertFalse(gw.has_token("owner"))
        self.assertTrue(gw.has_token("live"))
        self.assertEqual(gw.authenticate("b" * 32), "live")
        self.assertNotIn("a" * 31, "\n".join(self.logs))

    def test_a_missing_variable_is_logged_with_its_name_and_the_client_cannot_authenticate(self):
        gw = self.build(base_config(), env={"TOKEN_LIVE": TOKEN_LIVE})
        self.assertFalse(gw.has_token("owner"))
        self.assertTrue(any("owner" in line and "TOKEN_OWNER" in line for line in self.logs))
        self.assertEqual(gw.authenticate(TOKEN_LIVE), "live")

    def test_usable_token_helper(self):
        from feiyu_memgate.gateway.app import usable_token
        self.assertEqual(usable_token(TOKEN_OWNER), TOKEN_OWNER)
        for bad in ("", "change-me", "x" * 31, None, 5):
            self.assertEqual(usable_token(bad), "")


class LoadTests(unittest.TestCase):
    def setUp(self):
        saved = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), saved))  # load() adds the file's folder
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write_file(self, text, name="gateway.toml"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_a_stage_next_to_the_configuration_is_found_by_module_name(self):
        self.write_file(STAGE_MODULE, "nearby_stage_for_tests.py")
        self.addCleanup(sys.modules.pop, "nearby_stage_for_tests", None)
        path = self.write_file('''
[stores.private]
type = "inmemory"

[clients.owner]
token_env = "TOKEN_OWNER"
recall = ["private"]

[recall]
stages = [{ type = "nearby_stage_for_tests:Nearby", word = "hi" }]
''')
        cfg = config.load(path)
        self.assertEqual(sys.path[0], os.path.dirname(os.path.abspath(path)))
        gw = config.build_gateway(cfg, env=ENV, log=lambda line: None)
        self.addCleanup(gw.close)
        self.assertEqual(gw.stages[0].word, "hi")

    def test_load_a_toml_file_and_build_from_it(self):
        path = self.write_file('''
[server]
host = "127.0.0.1"
port = 9191
max_connections = 7

[stores.private]
type = "inmemory"
memories = ["likes green tea"]

[stores.channel]
type = "openviking"
url = "http://127.0.0.1:9"
key_env = "STORE_KEY"
writable = true

[clients.owner]
token_env = "TOKEN_OWNER"
recall = ["private", "channel"]
write = "channel"
daily_writes = 50

[recall]
stages = [
  { type = "min_score", value = 0.3 },
  "dedupe",
  { type = "budget", max_chars = 200 },
]
''')
        cfg = config.load(path)
        self.assertEqual(cfg["clients"]["owner"]["daily_writes"], 50)
        logs = []
        gw = config.build_gateway(cfg, env={"TOKEN_OWNER": TOKEN_OWNER, "STORE_KEY": SECRET_KEY}, log=logs.append)
        self.addCleanup(gw.close)
        self.assertEqual(set(gw.stores), {"private", "channel"})
        self.assertEqual(gw.clients["owner"].write, "channel")
        self.assertEqual([type(s) for s in gw.stages], [stages.MinScore, stages.Dedupe, stages.Budget])
        self.assertEqual(gw.stages[0].value, 0.3)
        self.assertEqual(config.server_options(cfg), ("127.0.0.1", 9191, {"max_connections": 7}))
        self.assertNotIn(SECRET_KEY, "\n".join(logs))

    def test_load_errors_are_config_errors(self):
        with self.assertRaises(ConfigError):
            config.load(os.path.join(tempfile.gettempdir(), "feiyu-no-such-config.toml"))
        with self.assertRaises(ConfigError) as ctx:
            config.load(self.write_file("[stores\nbroken"))
        self.assertIn("TOML", str(ctx.exception))


class ServerOptionsTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(config.server_options({}), ("127.0.0.1", 8080, {}))

    def test_values_and_extra_options(self):
        cfg = {"server": {"host": "0.0.0.0", "port": 9000, "max_connections": 4, "read_deadline": 2.5,
                          "pool_size": 3}}
        self.assertEqual(config.server_options(cfg), ("0.0.0.0", 9000, {"max_connections": 4, "read_deadline": 2.5}))

    def test_partial_server_table_keeps_the_other_defaults(self):
        self.assertEqual(config.server_options({"server": {"port": 1234}}), ("127.0.0.1", 1234, {}))

    def test_server_table_must_be_a_table(self):
        with self.assertRaises(ConfigError):
            config.server_options({"server": 5})

    def test_pool_settings_reach_the_gateway(self):
        gw = config.build_gateway(base_config(server={"pool_size": 3, "tenant_pool_total": 2,
                                                      "tenant_pool_per_tenant": 1}),
                                  env=ENV, log=lambda line: None)
        self.addCleanup(gw.close)
        self.assertEqual(gw.pool.size, 3)


if __name__ == "__main__":
    unittest.main()
