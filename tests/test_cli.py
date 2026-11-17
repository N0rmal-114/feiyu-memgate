import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from feiyu_memgate import cli
# mock.patch.dict(sys.modules, ...) forgets every module first imported inside it, and importing such
# a module again re-registers its pieces under a new class (the registry refuses that). The CLI imports
# these lazily, so load them here, before any test patches sys.modules.
import feiyu_memgate.builtins  # noqa: E402,F401
import feiyu_memgate.gateway.server  # noqa: E402,F401
import feiyu_memgate.tenants.keyfile  # noqa: E402,F401
import feiyu_memgate.tools.evaluate  # noqa: E402,F401
import feiyu_memgate.tools.tenants  # noqa: E402,F401

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "eval"
TOKEN = "tok-owner-fake-0123456789abcdef"

GOOD = """
[stores.private]
type = "inmemory"

[stores.readonly]
type = "inmemory"
writable = false

[clients.owner]
token_env = "CLI_TEST_TOKEN"
recall = ["private", "readonly"]
write = "private"
daily_writes = 20

[clients.nobody]
token_env = "CLI_TEST_TOKEN_MISSING"
recall = ["readonly"]
"""

BAD = """
[stores.private]
type = "inmemory"

[clients.owner]
token_env = "CLI_TEST_TOKEN"
recall = ["no_such_store"]
"""


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(list(argv))
    return code, out.getvalue()


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return str(path)


class CheckTests(TempDirCase):
    def test_good_config_shows_the_wiring_and_no_token(self):
        path = self.write("good.toml", GOOD)
        with mock.patch.dict(os.environ, {"CLI_TEST_TOKEN": TOKEN}):
            os.environ.pop("CLI_TEST_TOKEN_MISSING", None)
            code, text = run("check", "--config", path)
        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, text)
        self.assertIn("private: inmemory，可写", text)
        self.assertIn("readonly: inmemory，只读", text)
        self.assertIn("owner:", text)
        self.assertIn("有可用令牌", text)
        self.assertIn("没有可用令牌", text)  # the client whose variable is not set
        self.assertIn("min_score", text)
        self.assertIn("daily_cap", text)

    def test_bad_config_exits_1_with_a_message(self):
        path = self.write("bad.toml", BAD)
        with mock.patch.dict(os.environ, {"CLI_TEST_TOKEN": TOKEN}):
            code, text = run("check", "--config", path)
        self.assertEqual(code, 1)
        self.assertIn("配置有问题：", text)
        self.assertIn("no_such_store", text)
        self.assertNotIn(TOKEN, text)

    def test_missing_and_malformed_files(self):
        code, text = run("check", "--config", str(self.dir / "nope.toml"))
        self.assertEqual(code, 1)
        self.assertIn("配置有问题：", text)
        code, text = run("check", "--config", self.write("broken.toml", "[[[ nope"))
        self.assertEqual(code, 1)
        self.assertIn("配置有问题：", text)


class PiecesTests(unittest.TestCase):
    def test_lists_the_built_ins(self):
        code, text = run("pieces")
        self.assertEqual(code, 0)
        for name in ("inmemory", "openviking", "min_score", "daily_cap", "keyfile"):
            self.assertIn(name, text)


class EvalTests(unittest.TestCase):
    def test_eval_on_the_examples(self):
        code, text = run("eval", "--memories", str(EXAMPLES / "memories.json"), "--queries", str(EXAMPLES / "queries.json"))
        self.assertEqual(code, 0)
        self.assertIn("分数线", text)
        self.assertIn("分数分布", text)

    def test_unknown_store_type_and_missing_file(self):
        args = ("--memories", str(EXAMPLES / "memories.json"), "--queries", str(EXAMPLES / "queries.json"))
        code, text = run("eval", "--store-type", "no-such-store", *args)
        self.assertEqual(code, 1)
        self.assertIn("配置有问题：", text)
        code, text = run("eval", "--memories", str(EXAMPLES / "gone.json"), "--queries", str(EXAMPLES / "queries.json"))
        self.assertEqual(code, 1)
        self.assertIn("错误：FileNotFoundError", text)

    def test_thresholds_option(self):
        code, text = run("eval", "--store-type", "sqlite", "--store-option", "path=:memory:",
                         "--thresholds", "0.10,0.30",
                         "--memories", str(EXAMPLES / "memories.json"), "--queries", str(EXAMPLES / "queries.json"))
        self.assertEqual(code, 0)
        self.assertIn("分数线 0.10", text)
        self.assertIn("分数线 0.30", text)
        self.assertNotIn("分数线 0.50", text)
        self.assertEqual(cli._thresholds("0.1, 0.2"), (0.1, 0.2))
        for bad in ("2", "abc", "", "-0.1"):
            with self.assertRaises(Exception):
                cli._thresholds(bad)

    def test_store_option_types(self):
        self.assertEqual(cli._option("writable=false"), ("writable", False))
        self.assertEqual(cli._option("path=a.db"), ("path", "a.db"))
        with self.assertRaises(Exception):
            cli._option("novalue")


class TenantsCommandTests(TempDirCase):
    def test_add_list_remove(self):
        keys = str(self.dir / "keys.json")
        with mock.patch.dict(os.environ, {"CLI_TEST_TENANT_KEY": "secret-fake-key"}):
            code, text = run("tenants", "add", "100001", "--keys", keys, "--key-env", "CLI_TEST_TENANT_KEY")
        self.assertEqual(code, 0)
        self.assertNotIn("secret-fake-key", text)
        code, text = run("tenants", "list", "--keys", keys)
        self.assertEqual(code, 0)
        self.assertIn("100001", text)
        self.assertNotIn("secret-fake-key", text)
        code, _ = run("tenants", "remove", "100001", "--keys", keys)
        self.assertEqual(code, 0)
        code, _ = run("tenants", "remove", "100001", "--keys", keys)
        self.assertEqual(code, 1)

    def test_add_needs_an_environment_variable_and_a_valid_id(self):
        keys = str(self.dir / "keys.json")
        os.environ.pop("CLI_TEST_TENANT_KEY", None)
        code, text = run("tenants", "add", "100001", "--keys", keys, "--key-env", "CLI_TEST_TENANT_KEY")
        self.assertEqual(code, 1)
        self.assertIn("环境变量", text)
        with mock.patch.dict(os.environ, {"CLI_TEST_TENANT_KEY": "secret-fake-key"}):
            code, text = run("tenants", "add", "bad id", "--keys", keys, "--key-env", "CLI_TEST_TENANT_KEY")
            self.assertEqual(code, 1)
            self.assertNotIn("secret-fake-key", text)
            code, _ = run("tenants", "add", "--keys", keys, "--key-env", "CLI_TEST_TENANT_KEY")
            self.assertEqual(code, 1)
        self.assertFalse(Path(keys).exists())

    def test_a_corrupt_key_file_is_an_error_not_a_crash(self):
        keys = self.write("keys.json", "not json")
        code, text = run("tenants", "list", "--keys", keys)
        self.assertEqual(code, 1)
        self.assertIn("错误：", text)


class BackupCommandTests(TempDirCase):
    """The backup module is written elsewhere; its functions are replaced here."""

    def fake_module(self):
        module = types.ModuleType("feiyu_memgate.tools.backup")
        module.seal = mock.Mock(return_value="written")
        module.open_bundle = mock.Mock(return_value=3)
        module.export_openviking = mock.Mock(return_value=7)
        return module

    def run_with(self, module, *argv, env=None):
        with mock.patch.dict(sys.modules, {"feiyu_memgate.tools.backup": module}), \
                mock.patch("feiyu_memgate.tools.backup", module, create=True), \
                mock.patch.dict(os.environ, env or {}):
            return run("backup", *argv)

    def test_seal_names_directories_by_base_name(self):
        module = self.fake_module()
        a, b = self.dir / "one", self.dir / "two"
        code, _ = self.run_with(module, "seal", "--key", "k.key", "--out", "b.bin", str(a), str(b))
        self.assertEqual(code, 0)
        (dirs, bundle, key), _kw = module.seal.call_args
        self.assertEqual(dirs, {"one": a, "two": b})
        self.assertEqual((bundle, key), ("b.bin", "k.key"))

    def test_seal_rejects_duplicate_names(self):
        module = self.fake_module()
        code, text = self.run_with(module, "seal", "--key", "k", "--out", "b", str(self.dir / "x" / "same"),
                                   str(self.dir / "y" / "same"))
        self.assertEqual(code, 1)
        self.assertIn("错误：", text)
        module.seal.assert_not_called()

    def test_open_passes_its_arguments(self):
        module = self.fake_module()
        code, _ = self.run_with(module, "open", "--key", "k.key", "b.bin", "outdir")
        self.assertEqual(code, 0)
        module.open_bundle.assert_called_once_with("b.bin", "outdir", "k.key")

    def test_export_reads_the_key_from_the_environment(self):
        module = self.fake_module()
        code, text = self.run_with(module, "export-openviking", "--url", "http://127.0.0.1:1", "--key-env", "CLI_TEST_OV",
                                   "--dest", "d", "--tmp", "t", env={"CLI_TEST_OV": "ov-key-fake"})
        self.assertEqual(code, 0)
        self.assertNotIn("ov-key-fake", text)
        args, kwargs = module.export_openviking.call_args
        self.assertEqual(args, ("http://127.0.0.1:1", "ov-key-fake", "d"))
        self.assertEqual(kwargs, {"tmp_root": "t"})

    def test_export_without_the_variable_fails_before_calling(self):
        module = self.fake_module()
        os.environ.pop("CLI_TEST_OV", None)
        code, text = self.run_with(module, "export-openviking", "--url", "u", "--key-env", "CLI_TEST_OV",
                                   "--dest", "d", "--tmp", "t")
        self.assertEqual(code, 1)
        module.export_openviking.assert_not_called()

    def test_errors_become_messages(self):
        module = self.fake_module()
        module.open_bundle.side_effect = RuntimeError("wrong key")
        code, text = self.run_with(module, "open", "--key", "k", "b", "o")
        self.assertEqual((code, text.strip()), (1, "错误：wrong key"))
        module.open_bundle.side_effect = PermissionError("C:\\secret\\path")
        code, text = self.run_with(module, "open", "--key", "k", "b", "o")
        self.assertEqual((code, text.strip()), (1, "错误：PermissionError"))


class BackupOpenRealErrorsTests(TempDirCase):
    """`backup open` with the real error types of tools/crypto.py (DecryptError is a ValueError,
    KeyFileError an OSError, CryptoMissing a RuntimeError): a message and exit 1, never a traceback."""

    def key_file(self, size=32, name="k.key"):
        path = self.dir / name
        path.write_bytes(b"k" * size)
        return str(path)

    def open(self, bundle, key):
        return run("backup", "open", "--key", key, bundle, str(self.dir / "out"))

    def test_a_file_that_is_not_a_bundle(self):
        junk = self.dir / "junk.bin"
        junk.write_bytes(b"this is not a bundle at all, just text")
        code, text = self.open(str(junk), self.key_file())
        self.assertEqual(code, 1)
        self.assertEqual(text.strip(), "错误：这不是加密备份包")
        self.assertFalse((self.dir / "out").exists())

    def test_a_wrong_key(self):
        from feiyu_memgate.tools import crypto
        try:
            crypto.encrypt_dirs({}, b"a" * 32)
        except crypto.CryptoMissing:
            # tools/crypto.py imports the library only when it is used, and reports its absence itself
            self.skipTest("the cryptography library is not installed")
        source = self.dir / "src"
        source.mkdir()
        (source / "a.txt").write_text("x", encoding="utf-8")
        bundle = self.dir / "b.bin"
        bundle.write_bytes(crypto.encrypt_dirs({"d": source}, b"a" * 32))
        code, text = self.open(str(bundle), self.key_file())  # a different, correctly sized key
        self.assertEqual(code, 1)
        self.assertIn("错误：密钥不对", text)

    def test_a_key_file_of_the_wrong_size(self):
        code, text = self.open(str(self.dir / "whatever.bin"), self.key_file(size=5))
        self.assertEqual(code, 1)
        self.assertIn("错误：", text)
        self.assertIn("长度不对", text)

    def test_a_missing_key_file(self):
        code, text = self.open(str(self.dir / "whatever.bin"), str(self.dir / "no-such.key"))
        self.assertEqual(code, 1)
        self.assertIn("错误：", text)

    def test_the_cryptography_library_is_missing(self):
        from feiyu_memgate.tools import crypto
        bundle = self.dir / "b.bin"
        bundle.write_bytes(crypto.MAGIC + b"n" * crypto.NONCE_LEN + b"t" * 32)
        with mock.patch.dict(sys.modules, {"cryptography.hazmat.primitives.ciphers.aead": None}):
            code, text = self.open(str(bundle), self.key_file())
        self.assertEqual(code, 1)
        self.assertIn("错误：主机 Python 缺少 cryptography 库", text)


class CheckReminderTests(TempDirCase):
    STAGES = """
[recall]
stages = [%s]
"""

    def check(self, text):
        path = self.write("gateway.toml", text)
        with mock.patch.dict(os.environ, {"CLI_TEST_TOKEN": TOKEN}):
            return run("check", "--config", path)

    def test_a_stage_before_the_stages_it_must_follow_is_named(self):
        code, text = self.check(GOOD + self.STAGES % '"newer_first", "min_score", "relative_score", "budget"')
        self.assertEqual(code, 0)
        self.assertIn("  提醒：newer_first 要放在 min_score 和 relative_score 后面", text.splitlines())
        code, text = self.check(GOOD + self.STAGES % '"min_score", "newer_first", "relative_score"')
        self.assertIn("  提醒：newer_first 要放在 min_score 和 relative_score 后面", text.splitlines())

    def test_the_default_order_needs_no_reminder(self):
        code, text = self.check(GOOD)
        self.assertEqual(code, 0)
        self.assertIn("newer_first", text)
        self.assertNotIn("要放在", text)
        code, text = self.check(GOOD + self.STAGES % '"newer_first", "budget"')  # nothing to follow: fine
        self.assertNotIn("要放在", text)

    def test_a_client_without_token_env_is_shown_without_a_token(self):
        code, text = self.check(GOOD + "\n[clients.embedded]\nrecall = [\"private\"]\n")
        self.assertEqual(code, 0)
        line = [l for l in text.splitlines() if l.startswith("  embedded:")][0]
        self.assertIn("没有可用令牌", line)
        code, text = self.check(GOOD + "\n[clients.embedded]\ntoken_env = 5\nrecall = [\"private\"]\n")
        self.assertEqual(code, 1)
        self.assertIn("clients.embedded.token_env", text)


class EntryPointTests(unittest.TestCase):
    def test_main_module_exists(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec("feiyu_memgate.__main__"))

    def test_no_command_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            cli.main([])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
