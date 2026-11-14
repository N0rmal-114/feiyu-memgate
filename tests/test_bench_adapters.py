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


class BroadProfileAdapterTest(unittest.TestCase):
    def test_sqlite_broad_writes_its_own_file_and_names_the_profile(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            for name, content in (("memories.json", MEMORIES), ("queries.json", QUERIES)):
                with open(os.path.join(data, name), "w", encoding="utf-8") as f:
                    json.dump(content, f, ensure_ascii=False)
            with contextlib.redirect_stdout(io.StringIO()):
                sqlite_adapter.main(["--data", data, "--out", out, "--profile", "broad"])
            self.assertEqual(sorted(os.listdir(out)), ["feiyu-memgate-sqlite-broad.json"])
            with open(os.path.join(out, "feiyu-memgate-sqlite-broad.json"), encoding="utf-8") as f:
                result = json.load(f)
        self.assertEqual(score.validate_result(result, MEMORIES, QUERIES), [])
        self.assertEqual(result["system"], "feiyu-memgate-sqlite-broad")
        self.assertIn("broad profile", result["mode"])
        self.assertIn("0.25", " ".join(result["notes"]))

    def test_the_gateway_is_built_with_the_profile(self):
        gw = sqlite_adapter.build("broad")
        try:
            self.assertEqual([s.profile for s in gw.stages if hasattr(s, "profile")], ["broad", "broad"])
        finally:
            gw.close()
        gw = sqlite_adapter.build()
        try:
            self.assertEqual([s.profile for s in gw.stages if hasattr(s, "profile")], ["precise", "precise"])
        finally:
            gw.close()

    def test_the_vector_adapters_need_a_broad_suffix_for_a_broad_run(self):
        with self.assertRaises(SystemExit):
            embedding_adapter.parse_args("doc", ["--profile", "broad"])
        args = embedding_adapter.parse_args("doc", ["--profile", "broad", "--system-suffix", "broad"])
        self.assertEqual(embedding_adapter.system_name(embedding_adapter.SYSTEM, args.system_suffix),
                         "feiyu-memgate-embedding-broad")
        self.assertEqual(embedding_adapter.parse_args("doc", []).profile, "precise")

    def test_the_embedding_adapter_builds_a_gateway_with_the_profile(self):
        spec = embedding_adapter.store_spec("http://127.0.0.1:1/v1", "bge-m3")
        gw = embedding_adapter.build(spec, profile="broad")
        try:
            self.assertEqual([s.profile for s in gw.stages if hasattr(s, "profile")], ["broad", "broad"])
        finally:
            gw.close()
        notes = []
        args = embedding_adapter.parse_args("doc", ["--profile", "broad", "--system-suffix", "broad"])
        suffix = embedding_adapter.profile_notes(args, spec, notes)
        self.assertEqual(suffix, ", broad recall profile")
        self.assertIn("min_score 0.30, ratio 0.60 instead of 0.50 and 0.85", notes[0])


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
