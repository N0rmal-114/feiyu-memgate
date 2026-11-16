"""The feiyu-memgate benchmark adapters: the sqlite one end to end on a tiny made-up dataset (no network),
and the pieces of the vector ones that need none."""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest

_BENCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.join(_BENCH, "adapters"))  # the vector adapters import each other by name
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


score = _load(os.path.join(_BENCH, "score.py"), "bench_score_for_adapters")
sqlite_adapter = _load(os.path.join(_BENCH, "adapters", "feiyu_memgate_sqlite.py"), "adapter_sqlite")
full_adapter = _load(os.path.join(_BENCH, "adapters", "feiyu_memgate_full.py"), "adapter_full")
embedding_adapter = _load(os.path.join(_BENCH, "adapters", "feiyu_memgate_embedding.py"), "adapter_embedding")

MEMORIES = {
    "m001": {"text": "小明喝咖啡只喝燕麦拿铁，不加糖。", "date": "2025-01-02", "source": "private", "kind": "fact"},
    "m002": {"text": "小明写稿时喜欢听没有歌词的钢琴曲。", "date": "2025-01-03", "source": "private", "kind": "fact"},
    "m003": {"text": "小红养了一只叫汤圆的仓鼠。", "date": "2025-02-01", "source": "private", "kind": "fact"},
}
QUERIES = {
    "q1": {"query": "小明喝什么咖啡", "expect": ["m001"], "type": "lexical"},
    "q2": {"query": "汤圆是什么宠物", "expect": ["m003"], "type": "lexical"},
    "q3": {"query": "火星表面的平均气温是多少", "expect": [], "type": "quiet"},
}


class SqliteAdapterTest(unittest.TestCase):
    def test_writes_a_valid_result_with_ids_not_text_mapping(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            for name, content in (("memories.json", MEMORIES), ("queries.json", QUERIES)):
                with open(os.path.join(data, name), "w", encoding="utf-8") as f:
                    json.dump(content, f, ensure_ascii=False)
            with contextlib.redirect_stdout(io.StringIO()):
                sqlite_adapter.main(["--data", data, "--out", out])
            with open(os.path.join(out, "feiyu-memgate-sqlite.json"), encoding="utf-8") as f:
                result = json.load(f)
        self.assertEqual(score.validate_result(result, MEMORIES, QUERIES), [])
        self.assertEqual(result["system"], "feiyu-memgate-sqlite")
        self.assertEqual([i["id"] for i in result["answers"]["q1"]["deployed"]], ["m001"])
        self.assertEqual(result["answers"]["q1"]["ranked"][0]["id"], "m001")
        self.assertEqual(result["answers"]["q3"]["deployed"], [])
        self.assertIn("0 item(s) named no dataset memory", " ".join(result["notes"]))


class DefaultProfileAdapterTest(unittest.TestCase):
    """The plain ids run the gateway's defaults (the broad profile) and pass no profile; --profile precise pins the
    precise lines and writes the -precise id."""

    @staticmethod
    def _dataset(data):
        for name, content in (("memories.json", MEMORIES), ("queries.json", QUERIES)):
            with open(os.path.join(data, name), "w", encoding="utf-8") as f:
                json.dump(content, f, ensure_ascii=False)

    def test_sqlite_without_a_profile_writes_the_plain_file_and_names_the_broad_lines(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            self._dataset(data)
            with contextlib.redirect_stdout(io.StringIO()):
                sqlite_adapter.main(["--data", data, "--out", out])
            self.assertEqual(sorted(os.listdir(out)), ["feiyu-memgate-sqlite.json"])
            with open(os.path.join(out, "feiyu-memgate-sqlite.json"), encoding="utf-8") as f:
                result = json.load(f)
        self.assertEqual(result["system"], "feiyu-memgate-sqlite")
        self.assertIn("broad profile", result["mode"])
        self.assertNotIn("precise", result["mode"])
        self.assertIn("broad line 0.25", " ".join(result["notes"]))

    def test_sqlite_precise_writes_its_own_file_and_names_the_profile(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            self._dataset(data)
            with contextlib.redirect_stdout(io.StringIO()):
                sqlite_adapter.main(["--data", data, "--out", out, "--profile", "precise"])
            self.assertEqual(sorted(os.listdir(out)), ["feiyu-memgate-sqlite-precise.json"])
            with open(os.path.join(out, "feiyu-memgate-sqlite-precise.json"), encoding="utf-8") as f:
                result = json.load(f)
        self.assertEqual(score.validate_result(result, MEMORIES, QUERIES), [])
        self.assertEqual(result["system"], "feiyu-memgate-sqlite-precise")
        self.assertIn("precise recall profile", result["mode"])
        self.assertIn("precise line 0.30", " ".join(result["notes"]))

    def test_the_gateway_is_built_with_the_profile(self):
        stage_profiles = lambda gw: [s.profile for s in gw.stages if hasattr(s, "profile")]  # noqa: E731
        for profile, expected in ((None, ["broad", "broad"]), ("precise", ["precise", "precise"]),
                                  ("broad", ["broad", "broad"])):
            gw = sqlite_adapter.build(profile)
            try:
                self.assertEqual(stage_profiles(gw), expected)
            finally:
                gw.close()

    def test_an_explicit_precise_is_passed_on_to_the_gateway_config(self):
        built = []
        original = sqlite_adapter.config.build_gateway
        sqlite_adapter.config.build_gateway = lambda cfg, **kw: built.append(cfg) or original(cfg, **kw)
        try:
            sqlite_adapter.build().close()
            sqlite_adapter.build("precise").close()
        finally:
            sqlite_adapter.config.build_gateway = original
        self.assertNotIn("recall", built[0])                      # plain: no profile passed at all
        self.assertEqual(built[1]["recall"], {"profile": "precise"})

    def test_the_vector_adapters_name_the_precise_twin(self):
        args = embedding_adapter.parse_args("doc", ["--profile", "precise"])
        self.assertEqual(embedding_adapter.system_name(embedding_adapter.SYSTEM, args.system_suffix, args.profile),
                         "feiyu-memgate-embedding-precise")
        args = embedding_adapter.parse_args("doc", ["--system-suffix", "bge-small-zh", "--profile", "precise"])
        self.assertEqual(embedding_adapter.system_name(embedding_adapter.SYSTEM, args.system_suffix, args.profile),
                         "feiyu-memgate-embedding-bge-small-zh-precise")
        plain = embedding_adapter.parse_args("doc", ["--system-suffix", "bge-small-zh"])
        self.assertIsNone(plain.profile)
        self.assertEqual(embedding_adapter.system_name(embedding_adapter.SYSTEM, plain.system_suffix, plain.profile),
                         "feiyu-memgate-embedding-bge-small-zh")
        with self.assertRaises(SystemExit):                       # the -broad ids are gone
            embedding_adapter.parse_args("doc", ["--profile", "broad", "--system-suffix", "broad"])

    def test_the_embedding_adapter_builds_a_gateway_with_the_profile(self):
        spec = embedding_adapter.store_spec("http://127.0.0.1:1/v1", "bge-m3")
        stage_profiles = lambda gw: [s.profile for s in gw.stages if hasattr(s, "profile")]  # noqa: E731
        for profile, expected in ((None, ["broad", "broad"]), ("precise", ["precise", "precise"])):
            gw = embedding_adapter.build(spec, profile=profile)
            try:
                self.assertEqual(stage_profiles(gw), expected)
            finally:
                gw.close()
        notes = []
        args = embedding_adapter.parse_args("doc", ["--profile", "precise"])
        suffix = embedding_adapter.profile_notes(args, spec, notes)
        self.assertEqual(suffix, ", precise recall profile")
        self.assertIn("min_score 0.50, ratio 0.85 instead of the broad 0.30 and 0.60", notes[0])
        self.assertEqual(embedding_adapter.profile_notes(embedding_adapter.parse_args("doc", []), spec, []), "")

    def test_the_stage_note_names_the_lines_that_applied(self):
        lines = embedding_adapter.embedding_lines(embedding_adapter.store_spec("http://127.0.0.1:1/v1", "bge-m3"))
        self.assertEqual(lines, (0.50, 0.85, 0.30, 0.60))
        plain = embedding_adapter.stage_note(None, lines)
        self.assertIn("default profile is broad", plain)
        self.assertIn("broad line 0.30", plain)
        precise = embedding_adapter.stage_note("precise", lines)
        self.assertIn("'precise' pinned", precise)
        self.assertIn("precise line 0.50", precise)


class FullAdapterTest(unittest.TestCase):
    def test_stage_list_is_the_default_one_plus_freshness_after_min_score(self):
        from feiyu_memgate.core import stages
        names = full_adapter.stage_names()
        self.assertEqual(names[:3], ["min_score", "freshness", "relative_score"])
        self.assertEqual([n for n in names if n != "freshness"], list(stages.DEFAULT_STAGES))

    def test_the_clock_stands_on_the_day_after_the_newest_memory(self):
        from feiyu_memgate.forgetting.engine import DAY, date_to_epoch
        now = full_adapter.pinned_clock(MEMORIES)()
        self.assertEqual(now, date_to_epoch("2025-02-01") + DAY)

    def test_system_name_and_modes(self):
        self.assertEqual(full_adapter.SYSTEM, "feiyu-memgate-full")
        self.assertIn("freshness", full_adapter.MODE)


if __name__ == "__main__":
    unittest.main()
