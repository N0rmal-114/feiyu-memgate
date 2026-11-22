"""bench/audience: every scenario passes, every file is well formed, and the set would notice a missing protection.

The scenarios (bench/audience/scenarios/*.json, run by bench/audience/run.py) are data. The last class here
switches each protection off, one at a time, with a patch on the smallest piece of code that implements it, and
asks that every scenario which claims the protection in `checks` then fails with a named probe. A set that
passes with a protection missing would prove nothing. One more scenario (the `linked` stage) needs the fake
embeddings server of the tests, so it is a plain test here and not a file of the set.
"""
import contextlib
import dataclasses
import importlib.util
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from feiyu_memgate import config
from feiyu_memgate.backends.base import MemoryBackend
from feiyu_memgate.core import stages
from feiyu_memgate.core.recall import KEEP
from feiyu_memgate.gateway import app, fields, policies
from feiyu_memgate.identity import TableIdentity
from tests.test_backend_embedding import FakeEmbeddingServer

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench", "audience", "run.py")
_spec = importlib.util.spec_from_file_location("bench_audience_run", _PATH)
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)

SCENARIOS = bench.load_all()
EXPECTED_IDS = {
    "owner-private-vs-group", "owner-private-vs-public", "group-vs-other-group", "foreign-platform-account",
    "only-the-owner", "one-person-two-hosts", "copied-store-file", "two-names-one-file", "record-without-audience",
    "older-version-row", "forgotten-and-restored", "explain-for-the-owner", "fields-in-answers", "stand-in-codes",
    "narrower-audience-by-message",
}


def describe(result):
    return "; ".join("step %d: %s [%s]" % (f["step"], f["why"], f["detail"]) for f in result["failed"])


# ---------------------------------------------------------------- the protections, each switched off

def _store_rule():
    return mock.patch.object(policies, "check_audience", lambda *args, **kwargs: None)


def _record_rule():
    return mock.patch.object(stages.RecordAudience, "admit", lambda self, candidate, ctx: KEEP)


def _unstamped_is_owner():
    """A record without an audience counts as public: the stage's own code runs on a candidate that was given one."""
    original = stages.RecordAudience.admit

    def admit(self, candidate, ctx):
        if isinstance(candidate.fields, dict) and "audience" not in candidate.fields:
            candidate.fields = dict(candidate.fields, audience="public")
        return original(self, candidate, ctx)

    return mock.patch.object(stages.RecordAudience, "admit", admit)


def _stamp_at_write():
    """The fields of a message leave the gateway without the audience: the record is stored unstamped."""
    original = fields.request_fields

    def without_audience(*args, **kwargs):
        messages = original(*args, **kwargs)
        for message in messages:
            if isinstance(message.get("fields"), dict):
                message["fields"].pop("audience", None)
        return messages

    return mock.patch.object(app, "request_fields", without_audience)


def _platform_rule():
    return mock.patch.object(fields, "check_platform", lambda policy, account: None)


def _only_for():
    """Every client policy is built without `only_for` (the rule sits inline in two places of the gateway)."""
    original = config.client_policy

    def client_policy(name, spec):
        return dataclasses.replace(original(name, spec), only_for=())

    return mock.patch.object(config, "client_policy", client_policy)


def _code_per_store():
    """One stand-in code for an account in every store: the scope is left out of the code."""
    original = TableIdentity.code
    return mock.patch.object(TableIdentity, "code", lambda self, account, scope: original(self, account, ""))


def _raw_account_gone():
    """The resolved speaker of an account is the account itself."""
    return mock.patch.object(fields, "stand_in", lambda identity, account, scope: account)


def _explain_owner_only():
    return mock.patch.object(policies, "check_explain", lambda where, policy: None)


def _names_owner_only():
    return mock.patch.object(policies, "check_fields", lambda where, policy: None)


SWITCHES = {
    "store_rule": _store_rule, "record_rule": _record_rule, "unstamped_is_owner": _unstamped_is_owner,
    "stamp_at_write": _stamp_at_write, "platform_rule": _platform_rule, "only_for": _only_for,
    "code_per_store": _code_per_store, "raw_account_gone": _raw_account_gone,
    "explain_owner_only": _explain_owner_only, "names_owner_only": _names_owner_only,
}


# ---------------------------------------------------------------- the set

class SetTests(unittest.TestCase):
    def test_the_set_is_the_one_in_the_specification(self):
        self.assertEqual({scenario["id"] for _, scenario in SCENARIOS}, EXPECTED_IDS)

    def test_every_file_is_well_formed(self):
        for path, scenario in SCENARIOS:
            with self.subTest(file=os.path.basename(path)):
                self.assertEqual(bench.validate(scenario, os.path.splitext(os.path.basename(path))[0]), [])

    def test_every_protection_is_claimed_by_a_scenario(self):
        claimed = {name for _, scenario in SCENARIOS for name in scenario["checks"]}
        self.assertEqual(claimed, set(bench.PROTECTIONS))
        self.assertEqual(set(SWITCHES), set(bench.PROTECTIONS))

    def test_every_scenario_passes(self):
        for _, scenario in SCENARIOS:
            with self.subTest(scenario=scenario["id"]):
                result = bench.run_scenario(scenario)
                self.assertEqual(result["failed"], [], describe(result))
                self.assertGreater(result["probes"], 0)

    def test_a_run_is_the_same_every_time(self):
        scenario = next(s for _, s in SCENARIOS if s["id"] == "stand-in-codes")
        self.assertEqual(bench.run_scenario(scenario), bench.run_scenario(scenario))


class ValidatorTests(unittest.TestCase):
    def good(self):
        return json.loads(json.dumps(next(s for _, s in SCENARIOS if s["id"] == "owner-private-vs-public")))

    def test_the_good_one_has_no_problem(self):
        self.assertEqual(bench.validate(self.good(), "owner-private-vs-public"), [])

    def problems(self, change):
        scenario = self.good()
        change(scenario)
        return bench.validate(scenario, "owner-private-vs-public")

    def test_what_is_refused(self):
        cases = {
            "an unknown key": lambda s: s.update(extra=1),
            "a name that is not the file": lambda s: s.update(id="other"),
            "a title that is not Chinese": lambda s: s.update(title="plain"),
            "a protection that does not exist": lambda s: s.update(checks=["nothing"]),
            "no checks": lambda s: s.update(checks=[]),
            "no clients": lambda s: s["setup"].update(clients={}),
            "a token written into a client": lambda s: s["setup"]["clients"]["owner_bot"].update(token_env="X"),
            "a bad audience": lambda s: s["setup"]["stores"]["board"].update(audience="everyone"),
            "an unknown step": lambda s: s["steps"].append({"do": "dance"}),
            "a typo in an expectation": lambda s: s["steps"][2].update(absnet=["x"]),
            "a probe without why": lambda s: s["steps"][2].pop("why"),
            "a why that is not Chinese": lambda s: s["steps"][2].update(why="because"),
            "a recall that states nothing": lambda s: s["steps"].append(
                {"do": "recall", "client": "owner_bot", "query": "q", "why": "没有期望"}),
            "an absent text that nobody expects present": lambda s: s["steps"].append(
                {"do": "recall", "client": "public_bot", "query": "q", "absent": ["nowhere to be found"], "why": "没有对照"}),
            "a bad mode": lambda s: s["steps"][2].update(mode="loud"),
            "a bad restamp": lambda s: s["steps"].append({"do": "restamp", "store": "board", "mode": "some"}),
            "a refuse without why": lambda s: s["steps"].append({"do": "reconfigure", "patch": {}, "refuse": "x"}),
        }
        for what, change in cases.items():
            with self.subTest(what):
                self.assertTrue(self.problems(change), what)


class ToolTests(unittest.TestCase):
    def test_merge_patch(self):
        base = {"a": {"b": 1, "c": [1, 2]}, "d": 4}
        self.assertEqual(bench.merge(base, {"a": {"b": None, "c": [3]}, "e": {"f": 5}}),
                         {"a": {"c": [3]}, "d": 4, "e": {"f": 5}})
        self.assertEqual(base, {"a": {"b": 1, "c": [1, 2]}, "d": 4})

    def test_the_toml_written_for_restamp_reads_back_as_the_configuration(self):
        import tomllib
        cfg, _ = bench.make_config(next(s for _, s in SCENARIOS if s["id"] == "stand-in-codes")["setup"], "C:/x y/data")
        self.assertEqual(tomllib.loads(bench.to_toml(cfg)), cfg)

    def test_the_command_lists_runs_and_writes_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            with contextlib.redirect_stdout(io.StringIO()):
                code = bench.main(["foreign-platform-account", "--json", out])
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual([s["id"] for s in data["scenarios"]], ["foreign-platform-account"])
            self.assertEqual((data["failed"], data["probes"] > 0), (0, True))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bench.main(["no-such-scenario"]), 2)

    def test_a_failing_probe_gives_exit_code_1_and_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            scenario = next(s for _, s in SCENARIOS if s["id"] == "owner-private-vs-public")
            broken = json.loads(json.dumps(scenario))
            broken["steps"][4]["absent"] = ["kite festival"]  # the public client does read that one
            with open(os.path.join(tmp, "owner-private-vs-public.json"), "w", encoding="utf-8") as f:
                json.dump(broken, f, ensure_ascii=False)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(bench.main(["--dir", tmp]), 1)
            self.assertIn("FAIL owner-private-vs-public step 5 (recall)", out.getvalue())


# ---------------------------------------------------------------- the reverse check

class ReverseCheckTests(unittest.TestCase):
    """With a protection switched off, every scenario that claims it fails, and the failed probe is named."""

    def test_each_protection_is_noticed_when_it_is_missing(self):
        for protection, switch in SWITCHES.items():
            claiming = [scenario for _, scenario in SCENARIOS if protection in scenario["checks"]]
            self.assertTrue(claiming, protection)
            for scenario in claiming:
                with self.subTest(protection=protection, scenario=scenario["id"]):
                    with switch():
                        result = bench.run_scenario(scenario)
                    self.assertTrue(result["failed"], "%s switched off, and %s still passes" % (protection, scenario["id"]))
                    for failure in result["failed"]:
                        self.assertTrue(failure["why"])
                        self.assertGreaterEqual(failure["step"], 1)

    def test_the_set_passes_again_when_the_switch_is_back(self):
        for protection, switch in SWITCHES.items():
            with switch():
                pass
            scenario = next(s for _, s in SCENARIOS if protection in s["checks"])
            with self.subTest(protection=protection):
                self.assertEqual(bench.run_scenario(scenario)["failed"], [])

    def test_the_failing_probes_are_the_ones_about_the_protection(self):
        """Spot checks of which probe catches what, so a protection is not caught by an accident."""
        def failed(scenario_id, protection):
            scenario = next(s for _, s in SCENARIOS if s["id"] == scenario_id)
            with SWITCHES[protection]():
                return {(f["step"], f["do"]) for f in bench.run_scenario(scenario)["failed"]}

        self.assertIn((7, "reconfigure"), failed("owner-private-vs-group", "store_rule"))
        self.assertIn((4, "recall"), failed("record-without-audience", "unstamped_is_owner"))
        self.assertIn((6, "recall"), failed("copied-store-file", "record_rule"))
        self.assertIn((1, "remember"), failed("foreign-platform-account", "platform_rule"))
        self.assertIn((2, "remember"), failed("only-the-owner", "only_for"))
        self.assertIn((7, "codes"), failed("stand-in-codes", "code_per_store"))
        self.assertIn((5, "reconfigure"), failed("explain-for-the-owner", "explain_owner_only"))
        self.assertIn((5, "reconfigure"), failed("fields-in-answers", "names_owner_only"))


# ---------------------------------------------------------------- the `linked` stage

REMARK = "我喜欢吃冰淇淋"
NEAR = "主人喜欢吃冰淇淋，尤其是巧克力味的冰淇淋"
LINKED = "巧克力味的冰淇淋掉在地上了"  # close to NEAR, far from the remark: only the `linked` stage brings it
OTHERS = ["周末去游泳馆游泳", "写代码调试程序", "主人养了一只黄色的狗"]
LINES = [{"type": "min_score", "value": 0.4}, "relative_score", "newer_first"]


class LinkedTests(unittest.TestCase):
    """A memory the `linked` stage adds is checked against the audience like any other (the stage record_audience
    stands after it). The first hop finds only NEAR; LINKED is owner-level and comes only as a neighbour."""

    def setUp(self):
        self.server = FakeEmbeddingServer()
        self.addCleanup(self.server.stop)
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        cfg = {
            "stores": {"shared": {"type": "embedding", "path": os.path.join(self.tmp.name, "v.db"),
                                  "url": self.server.url, "model": "fake-model", "floor": 0.0, "ceil": 1.0,
                                  "audience": "public"}},
            "clients": {"owner_bot": {"recall": ["shared"], "rate_limit": 100000},
                        "public_bot": {"recall": ["shared"], "audience": "public", "rate_limit": 100000}},
            "recall": {"stages": LINES + [{"type": "linked", "minimum": 0.4}, "record_audience", "fill_text",
                                          "redact", "dedupe", "budget"]},
        }
        self.gateway = config.build_gateway(cfg, env={}, log=lambda line: None)
        self.addCleanup(self.gateway.close)
        store = self.gateway.stores["shared"]
        self.assertIsInstance(store, MemoryBackend)
        store.put(NEAR, ref="near", date="2026-01-01", fields={"audience": "public"})
        store.put(LINKED, ref="linked", date="2026-01-01", fields={"audience": "owner"})
        for index, text in enumerate(OTHERS):
            store.put(text, ref="o%d" % index, date="2026-01-01", fields={"audience": "public"})

    def ids(self, client):
        reply = self.gateway.recall(client, {"query": REMARK})
        self.assertEqual(reply.status, 200)
        return [item["id"] for item in reply.body["results"]], reply.body

    def test_the_owner_level_neighbour_comes_to_the_owner_only(self):
        owner_ids, _ = self.ids("owner_bot")
        self.assertEqual(owner_ids, ["near", "linked"])  # the stage works: the neighbour is brought along
        public_ids, body = self.ids("public_bot")
        self.assertEqual(public_ids, ["near"])
        self.assertNotIn(LINKED, json.dumps(body, ensure_ascii=False))

    def test_the_test_notices_when_the_record_check_is_missing(self):
        with _record_rule():
            public_ids, _ = self.ids("public_bot")
        self.assertEqual(public_ids, ["near", "linked"])


if __name__ == "__main__":
    unittest.main()
