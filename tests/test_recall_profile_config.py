"""`[recall] profile` ("broad" when absent, "precise" gives the old lines): the shortcut that sets the profile of min_score and relative_score
wherever the stage list does not set it itself, and the line `feiyu-memgate check` prints."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from feiyu_memgate import cli, config
from feiyu_memgate.core import stages
from feiyu_memgate.errors import ConfigError

TOKEN = "tok-owner-fake-".ljust(40, "0")


def base(recall=None):
    cfg = {"stores": {"private": {"type": "inmemory"}},
           "clients": {"owner": {"token_env": "TOKEN_OWNER", "recall": ["private"], "write": "private"}}}
    if recall is not None:
        cfg["recall"] = recall
    return cfg


def build(recall=None):
    return config.build_gateway(base(recall), env={"TOKEN_OWNER": TOKEN}, log=lambda line: None)


def profiles(gateway):
    return {s.name: s.profile for s in gateway.stages if hasattr(s, "profile")}


class ProfileShortcutTest(unittest.TestCase):
    def test_default_is_broad_and_the_stage_list_is_untouched(self):
        for recall in (None, {}, {"profile": "broad"}):
            gateway = build(recall)
            self.addCleanup(gateway.close)
            self.assertEqual(profiles(gateway), {"min_score": "broad", "relative_score": "broad"})
            self.assertEqual([s.name for s in gateway.stages], list(stages.DEFAULT_STAGES))

    def test_no_profile_key_gives_broad_also_with_a_written_out_list(self):
        gateway = build({"stages": ["min_score", {"type": "relative_score", "gap": 0.2}, "redact"]})
        self.addCleanup(gateway.close)
        self.assertEqual(profiles(gateway), {"min_score": "broad", "relative_score": "broad"})

    def test_an_explicit_precise_reaches_the_stages(self):
        gateway = build({"profile": "precise"})
        self.addCleanup(gateway.close)
        self.assertEqual(profiles(gateway), {"min_score": "precise", "relative_score": "precise"})
        self.assertEqual([s.name for s in gateway.stages], list(stages.DEFAULT_STAGES))
        gateway = build({"profile": "precise",
                         "stages": ["min_score", {"type": "relative_score", "gap": 0.2}, "redact"]})
        self.addCleanup(gateway.close)
        self.assertEqual(profiles(gateway), {"min_score": "precise", "relative_score": "precise"})
        self.assertEqual(gateway.stages[1].gap, 0.2)

    def test_a_stage_profile_overrides_the_section_profile_both_ways(self):
        gateway = build({"profile": "precise", "stages": [
            {"type": "min_score", "profile": "broad"}, "relative_score", "redact"]})
        self.addCleanup(gateway.close)
        self.assertEqual(profiles(gateway), {"min_score": "broad", "relative_score": "precise"})
        gateway = build({"stages": [{"type": "min_score", "profile": "precise"}, "relative_score", "redact"]})
        self.addCleanup(gateway.close)
        self.assertEqual(profiles(gateway), {"min_score": "precise", "relative_score": "broad"})

    def test_broad_applies_to_the_default_stage_list(self):
        gateway = build({"profile": "broad"})
        self.addCleanup(gateway.close)
        self.assertEqual(profiles(gateway), {"min_score": "broad", "relative_score": "broad"})
        self.assertEqual([s.name for s in gateway.stages], list(stages.DEFAULT_STAGES))  # nothing added or lost

    def test_broad_applies_to_a_written_out_list_by_name_and_by_table(self):
        gateway = build({"profile": "broad", "stages": [
            "min_score", {"type": "relative_score", "gap": 0.2}, "dedupe", "redact"]})
        self.addCleanup(gateway.close)
        self.assertEqual(profiles(gateway), {"min_score": "broad", "relative_score": "broad"})
        self.assertEqual(gateway.stages[1].gap, 0.2)  # the other options of the entry stay

    def test_a_stage_that_sets_its_own_profile_keeps_it(self):
        gateway = build({"profile": "broad", "stages": [
            {"type": "min_score", "profile": "precise"}, "relative_score", "redact"]})
        self.addCleanup(gateway.close)
        self.assertEqual(profiles(gateway), {"min_score": "precise", "relative_score": "broad"})

    def test_explicit_numbers_still_win(self):
        gateway = build({"profile": "broad", "stages": [
            {"type": "min_score", "value": 0.9}, {"type": "relative_score", "ratio": 0.99}, "redact"]})
        self.addCleanup(gateway.close)
        self.assertEqual((gateway.stages[0].value, gateway.stages[1].ratio), (0.9, 0.99))

    def test_a_list_without_the_two_stages_is_not_changed(self):
        gateway = build({"profile": "broad", "stages": ["redact", "dedupe"]})
        self.addCleanup(gateway.close)
        self.assertEqual(profiles(gateway), {})
        self.assertEqual([s.name for s in gateway.stages], ["redact", "dedupe"])

    def test_the_config_dict_is_not_modified(self):
        recall = {"profile": "broad", "stages": ["min_score", {"type": "relative_score"}, "redact"]}
        gateway = build(recall)
        self.addCleanup(gateway.close)
        self.assertEqual(recall["stages"], ["min_score", {"type": "relative_score"}, "redact"])

    def test_an_unknown_profile_is_a_configuration_error(self):
        for bad in ("wide", "", None, 1, True, "Broad"):
            with self.assertRaises(ConfigError, msg=repr(bad)) as ctx:
                build({"profile": bad})
            self.assertIn("recall.profile", str(ctx.exception))
        with self.assertRaises(ConfigError):  # a stage's own wrong profile is refused too
            build({"stages": [{"type": "min_score", "profile": "wide"}, "redact"]})

    def test_the_shortcut_changes_what_is_delivered(self):
        # a store whose default line (0.5) drops a 0.46 hit and whose broad line (0.3) keeps it
        from feiyu_memgate.backends.base import MemoryBackend
        from feiyu_memgate.model import Hit

        class Store(MemoryBackend):
            default_min_score, broad_min_score = 0.5, 0.3
            default_relative_ratio, broad_relative_ratio = 0.9, 0.5
            writable = False

            def find(self, query, limit, timeout=None, ctl=None):
                return [Hit(ref="a", score=0.9, text="alpha"), Hit(ref="b", score=0.46, text="bravo")]

        for recall, expected in (({"profile": "precise"}, ["alpha"]), ({"profile": "broad"}, ["alpha", "bravo"]),
                                 (None, ["alpha", "bravo"])):
            profile = recall and recall["profile"]
            gateway = build(recall)
            self.addCleanup(gateway.close)
            gateway.stores["private"] = Store()
            reply = gateway.recall("owner", {"query": "q"})
            self.assertEqual(reply.status, 200)
            self.assertEqual([r["text"] for r in reply.body["results"]], expected, profile)


class CheckLineTest(unittest.TestCase):
    def run_check(self, extra):
        text = ('[stores.private]\ntype = "inmemory"\n\n[clients.owner]\ntoken_env = "PROFILE_TEST_TOKEN"\n'
                'recall = ["private"]\nwrite = "private"\n' + extra)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gateway.toml"
            path.write_text(text, encoding="utf-8")
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"PROFILE_TEST_TOKEN": TOKEN}), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(["check", "--config", str(path)])
        return code, out.getvalue()

    def test_check_prints_the_active_profile(self):
        code, text = self.run_check("")
        self.assertEqual(code, 0)
        self.assertIn("分数线档位：min_score broad、relative_score broad", text)
        code, text = self.run_check('\n[recall]\nprofile = "precise"\n')
        self.assertEqual(code, 0)
        self.assertIn("分数线档位：min_score precise、relative_score precise", text)

    def test_check_reports_a_bad_profile(self):
        code, text = self.run_check('\n[recall]\nprofile = "wide"\n')
        self.assertEqual(code, 1)
        self.assertIn("recall.profile", text)


if __name__ == "__main__":
    unittest.main()
