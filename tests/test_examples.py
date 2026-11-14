"""The example pieces and example configurations must keep working."""
import datetime
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from feiyu_memgate import config, registry
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.recall import RecallContext, recall

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
