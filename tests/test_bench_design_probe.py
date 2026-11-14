"""bench/design_probe.py: the probe runs and its results have the documented shape.

The outcomes are not asserted here: a property that does not hold must be able to show up as "no" or
"partial" in the self-check (bench/results/design/self-check.md) without breaking the test suite.
"""
import importlib.util
import json
import os
import tempfile
import unittest

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench", "design_probe.py")
_spec = importlib.util.spec_from_file_location("bench_design_probe", _PATH)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

FAKE_SECRETS = (probe.FAKE_KEY, probe.FAKE_PASSWORD, probe.FAKE_BEARER, probe.PLANTED_KEY, probe.PLANTED_PASSWORD)


class DesignProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = probe.run_all()

    def test_ten_results_in_order(self):
        self.assertEqual(len(self.results), 10)
        self.assertEqual(len({row["property"] for row in self.results}), 10)

    def test_schema(self):
        for row in self.results:
            self.assertIsInstance(row["property"], str)
            self.assertTrue(row["property"])
            self.assertIn(row["result"], ("yes", "no", "partial"))
            self.assertIsInstance(row["evidence"], str)
            self.assertTrue(row["evidence"])
            self.assertIsInstance(row["checks"], dict)
            for check in row["checks"].values():
                self.assertEqual(set(check), {"ok", "kind", "detail"})
                self.assertIsInstance(check["ok"], bool)
                self.assertIn(check["kind"], ("check", "limit"))

    def test_a_probe_that_crashes_is_a_result_not_an_exception(self):
        def broken():
            raise RuntimeError("boom")

        row = probe.run_probe(broken)
        self.assertEqual(row["result"], "no")
        self.assertIn("RuntimeError", row["evidence"])

    def test_evidence_never_holds_a_planted_secret(self):
        blob = json.dumps(self.results, ensure_ascii=False)
        for secret in FAKE_SECRETS:
            self.assertNotIn(secret, blob)

    def test_evidence_never_holds_a_token_or_a_minting_secret(self):
        blob = json.dumps(self.results, ensure_ascii=False)
        for name in ("owner", "group", "bound"):
            self.assertNotIn(probe.token_for(name), blob)
        self.assertNotIn("fmg1.", blob)

    def test_the_isolation_probe_measures_both_tenant_modes(self):
        row = next(r for r in self.results if r["property"] == "isolation_enforced_by_credentials")
        checks = row["checks"]
        # The default mode's limit stays reported, whatever it shows.
        self.assertEqual(checks["one_tenant_token_can_address_every_registered_tenant"]["kind"], "limit")
        for name in ("bound_token_reads_its_tenant_without_naming_it", "bound_token_a_does_not_see_tenant_b",
                     "bound_token_cannot_name_another_tenant_by_any_body_value",
                     "bound_token_cannot_write_or_report_for_another_tenant", "minting_secret_alone_is_refused",
                     "tenant_swapped_inside_a_token_is_refused"):
            self.assertEqual(checks[name]["kind"], "check", name)

    def test_main_writes_the_json_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "feiyu-memgate.json")
            written = probe.main(path)
            with open(path, encoding="utf-8") as f:
                document = json.load(f)
        self.assertEqual(document["system"], "feiyu-memgate")
        self.assertEqual(len(document["properties"]), 10)
        self.assertEqual([p["property"] for p in document["properties"]], [r["property"] for r in written])


class VerdictTests(unittest.TestCase):
    def test_verdict_rules(self):
        c = probe.Checks()
        c.add("a", True)
        c.limit("known", True, caps=False)
        self.assertEqual(c.verdict(), "yes")  # a note does not lower the verdict
        c.limit("part of the property", True, caps=True)
        self.assertEqual(c.verdict(), "partial")
        d = probe.Checks()
        d.add("a", True)
        d.add("b", False)
        self.assertEqual(d.verdict(), "partial")
        e = probe.Checks()
        e.add("a", False)
        self.assertEqual(e.verdict(), "no")


if __name__ == "__main__":
    unittest.main()
