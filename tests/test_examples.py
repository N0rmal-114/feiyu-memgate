"""The example pieces and example configurations must keep working."""
import datetime
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from feiyu_memgate import config, registry
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.client import LocalClient
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.recall import RecallContext, recall
from feiyu_memgate.errors import BackendError

ROOT = Path(__file__).resolve().parent.parent
CUSTOM = ROOT / "examples" / "custom-stage"


def keep_sys_path(case):
    """config.load puts the configuration's folder on sys.path: undo it after the test."""
    saved = list(sys.path)

    def restore():
        sys.path[:] = saved
        sys.modules.pop("recency_stage", None)

    case.addCleanup(restore)


class RecencyExample(unittest.TestCase):
    def setUp(self):
        self.pool = Pool(2)
        self.addCleanup(self.pool.close)
        keep_sys_path(self)
        config.load(CUSTOM / "gateway.toml")  # the configuration's folder is where recency_stage.py lives

    def test_newer_memory_ranks_first(self):
        stage = registry.create(
            "recall_stage", {"type": "recency_stage:Recency", "half_life_days": 30, "today": datetime.date(2026, 6, 1)})
        store = InMemoryBackend()
        store.put("周末去了动物园", ref="old", date="2025-06-01")
        store.put("周末去了动物园看熊猫", ref="new", date="2026-05-30")
        ctx = RecallContext("周末去了动物园", 5, pool=self.pool)
        out = recall({"notes": store}, [stage], ctx)
        self.assertEqual([item["date"] for item in out["results"]], ["2026-05-30", "2025-06-01"])
        self.assertLess(out["results"][1]["score"], out["results"][0]["score"])

    def test_memory_without_date_keeps_its_score(self):
        stage = registry.create("recall_stage", "recency_stage:Recency")
        store = InMemoryBackend()
        store.put("喜欢草莓蛋糕")
        ctx = RecallContext("喜欢草莓蛋糕", 5, pool=self.pool)
        out = recall({"notes": store}, [stage], ctx)
        self.assertEqual(out["results"][0]["score"], 1.0)

    def test_the_example_imports_only_from_the_public_pieces_module(self):
        text = (CUSTOM / "recency_stage.py").read_text(encoding="utf-8")
        self.assertIn("from feiyu_memgate.pieces import", text)
        self.assertNotIn("feiyu_memgate.core", text)


BACKEND = ROOT / "examples" / "custom-backend"
CHAT_LOOP = ROOT / "examples" / "chat-loop"


def forget_module(case, name):
    """Drop an example module from sys.modules after the test, and keep byte-code files out of
    the example's folder while it is imported (a run without -B would leave a __pycache__ there)."""
    saved = sys.dont_write_bytecode
    sys.dont_write_bytecode = True

    def restore():
        sys.dont_write_bytecode = saved
        sys.modules.pop(name, None)

    case.addCleanup(restore)


class JsonLinesBackendExample(unittest.TestCase):
    def setUp(self):
        keep_sys_path(self)
        forget_module(self, "jsonl_backend")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.cfg = config.load(BACKEND / "gateway.toml")  # the folder of jsonl_backend.py goes on sys.path
        self.cfg["stores"]["notes"]["path"] = str(self.tmp / "data" / "notes.jsonl")  # the folder does not exist yet

    def backend(self, **options):
        return registry.create("backend", dict(self.cfg["stores"]["notes"], **options))

    def test_configuration_names_the_backend_by_dotted_path(self):
        self.assertEqual(self.cfg["stores"]["notes"]["type"], "jsonl_backend:JsonLinesBackend")
        store = self.backend()
        self.assertTrue(store.writable)
        self.assertEqual(store.find("anything", 5), [])  # no file yet: an empty store, not an error

    def test_remember_then_find_by_shared_words(self):
        store = self.backend()
        receipt = store.remember([
            {"role": "user", "content": "I like oolong tea", "time": "2026-11-14 20:15"},
            {"role": "assistant", "content": "Noted."},
            {"role": "user", "content": "周末去了动物园看熊猫"},
        ])
        self.assertTrue(receipt)
        hits = store.find("which tea do I like", 5)
        self.assertEqual([h.text for h in hits], ["I like oolong tea"])
        self.assertEqual(hits[0].date, "2026-11-14")
        self.assertTrue(0 < hits[0].score <= 1)
        self.assertEqual([h.text for h in store.find("动物园的熊猫", 5)], ["周末去了动物园看熊猫"])
        self.assertEqual(store.find("quantum chromodynamics", 5), [])
        self.assertEqual(len(store.find("tea 熊猫", 1)), 1)  # limit is honoured

    def test_nothing_to_keep_gives_no_receipt(self):
        self.assertIsNone(self.backend().remember([{"role": "assistant", "content": "only the bot spoke"}]))

    def test_memories_survive_a_restart_and_a_torn_last_line_is_skipped(self):
        first = self.backend()
        first.remember([{"role": "user", "content": "I keep bees"}])
        path = Path(self.cfg["stores"]["notes"]["path"])
        with open(path, "a", encoding="utf-8") as f:
            f.write('{"ref": "m-torn", "te')  # a crash in the middle of a write
        second = self.backend()
        self.assertEqual([h.text for h in second.find("bees", 5)], ["I keep bees"])

    def test_read_only_store_refuses_writes(self):
        with self.assertRaises(PermissionError):
            self.backend(writable=False).remember([{"role": "user", "content": "x y z"}])

    def test_an_unwritable_path_is_a_backend_error_without_the_text(self):
        blocker = self.tmp / "file"
        blocker.write_text("x", encoding="utf-8")
        store = self.backend(path=str(blocker / "notes.jsonl"))  # a file where a folder should be
        with self.assertRaises(BackendError) as caught:
            store.remember([{"role": "user", "content": "secret memory text"}])
        self.assertNotIn("secret memory text", str(caught.exception))

    def test_through_the_gateway_the_backend_stores_and_recalls(self):
        env = {"GATEWAY_TOKEN_OWNER": "example-test-token-" + "x" * 32}
        for round_ in range(2):  # the second round opens the same file again: what was stored is still there
            with self.subTest(round=round_), LocalClient.from_config(
                    self.cfg, client="owner", env=env, log=lambda line: None) as memory:
                if round_ == 0:
                    self.assertEqual(memory.remember_status("I am allergic to peanuts"), "saved")
                    self.assertEqual(memory.remember_messages([{"role": "assistant", "content": "Noted."}]), "skipped")
                results = memory.recall("what am I allergic to")
                self.assertEqual([r["text"] for r in results], ["I am allergic to peanuts"])
                self.assertEqual(results[0]["source"], "notes")
                self.assertEqual(memory.recall("something unrelated entirely"), [])

    def test_the_example_imports_only_from_the_public_pieces_module(self):
        text = (BACKEND / "jsonl_backend.py").read_text(encoding="utf-8")
        self.assertIn("from feiyu_memgate.pieces import", text)
        self.assertNotIn("feiyu_memgate.core", text)


class ChatLoopExample(unittest.TestCase):
    def run_script(self, *args, extra_env=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("FEIYU_MEMGATE")}
        env.update({"PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        env.update(extra_env or {})
        with tempfile.TemporaryDirectory() as tmp:  # run from an empty folder: the script needs nothing in it
            return subprocess.run([sys.executable, "-B", str(CHAT_LOOP / "chat_loop.py"), *args],
                                  cwd=tmp, env=env, capture_output=True, timeout=60)

    def test_configuration_uses_a_store_that_needs_no_file(self):
        keep_sys_path(self)
        cfg = config.load(CHAT_LOOP / "gateway.toml")
        self.assertEqual(cfg["stores"]["notes"]["type"], "inmemory")

    def test_the_demo_recalls_an_earlier_message_and_prints_the_block(self):
        done = self.run_script("--today", "2026-11-17")
        out = done.stdout.decode("utf-8")
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertEqual(done.stderr, b"")
        self.assertEqual(out.count("<memory_data>"), 1)  # only the last turn finds something
        block = out.split("<memory_data>", 1)[1].split("</memory_data>", 1)[0]
        self.assertIn("- （记忆，2026-11-17，今天）我对花生过敏，点菜的时候要避开", block)
        self.assertIn("今天是 2026-11-17。", block)
        self.assertIn("no lookup for this message", out)  # "嗯" is not worth a lookup
        self.assertEqual(out.count("remember: saved"), 2)  # the two statements, not the question
        # the block comes after the user's message
        self.assertLess(out.index("[user] 我对什么过敏来着？"), out.index("<memory_data>"))

    def test_your_own_messages_and_a_message_that_finds_nothing(self):
        done = self.run_script("--today", "2026-11-17", "I like oolong tea", "What is the capital of France?")
        out = done.stdout.decode("utf-8")
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertIn("remember: saved", out)
        self.assertNotIn("<memory_data>", out)  # the second turn recalls nothing: no block, the turn goes on
        self.assertIn("机器人：（模型的回复）", out.split("你：What is")[1])

    def test_an_unreachable_gateway_does_not_stop_the_loop(self):
        # A closed port on loopback: recall gives None and remember "failed"; every turn still gets its reply.
        done = self.run_script("--today", "2026-11-17", "I like oolong tea", "which tea do I like?",
                               extra_env={"FEIYU_MEMGATE_URL": "http://127.0.0.1:9", "FEIYU_MEMGATE_TOKEN": "t" * 40})
        out = done.stdout.decode("utf-8")
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertIn("recall failed, answering without memories", out)
        self.assertEqual(out.count("机器人：（模型的回复）"), 2)
        self.assertNotIn("<memory_data>", out)


def _move_paths(spec, folder):
    """A copy of a piece table with every file path option pointed into `folder`
    (nested tables and lists of tables included, such as a hybrid store's children)."""
    if isinstance(spec, list):
        return [_move_paths(item, folder) for item in spec]
    if not isinstance(spec, dict):
        return spec
    out = {}
    for key, value in spec.items():
        if key in ("path", "ledger", "archive") and isinstance(value, str):
            out[key] = os.path.join(folder, os.path.basename(value) or "file")
        else:
            out[key] = _move_paths(value, folder)
    return out


class ExampleConfigs(unittest.TestCase):
    def test_every_example_builds(self):
        """Load each example and build a gateway from it (files go to a temporary folder, tokens
        and keys are fake, nothing is contacted): its policies and pieces pass every check."""
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
                env = {}
                for i, client in enumerate(cfg["clients"].values()):
                    env[client["token_env"]] = "example-test-token-%02d-" % i + "x" * 32
                for var in _env_names(cfg):
                    env.setdefault(var, "fake-key-for-example-tests")
                gw = config.build_gateway(cfg, env=env, log=lambda line: None)
                gw.close()

    def test_tenant_clients_name_their_own_store_in_recall(self):
        for path in sorted((ROOT / "examples").glob("*/gateway.toml")):
            cfg = config.load(path)
            for name, client in cfg["clients"].items():
                if "tenants" in client and "recall" in client:
                    self.assertIn("@tenant", client["recall"], (path.parent.name, name))


def _env_names(cfg):
    """Every environment variable a `*_env` option of the configuration names."""
    names = []

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("_env") and key != "token_env" and isinstance(item, str):
                    names.append(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk({k: v for k, v in cfg.items() if k != "clients"})
    return names


class ReadmeExampleTests(unittest.TestCase):
    def test_the_astrbot_readme_configuration_builds(self):
        text = (ROOT / "integrations" / "astrbot" / "feiyu_memgate_recall" / "README.md").read_text(encoding="utf-8")
        cfg = tomllib.loads(text.split("```toml", 1)[1].split("```", 1)[0])
        with tempfile.TemporaryDirectory() as tmp:
            for section in ("stores", "tenants"):
                for name, spec in cfg.get(section, {}).items():
                    cfg[section][name] = _move_paths(spec, tmp)
            env = {c["token_env"]: "readme-test-token-%d-" % i + "y" * 32 for i, c in enumerate(cfg["clients"].values())}
            gw = config.build_gateway(cfg, env=env, log=lambda line: None)
            self.assertEqual(gw.clients["group"].recall, ("@tenant",))
            gw.close()


if __name__ == "__main__":
    unittest.main()
