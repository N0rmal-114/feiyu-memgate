import io
import json
import os
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

try:
    import cryptography  # noqa: F401
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False

from feiyu_memgate.tools import backup, crypto

KEY = "ov-key-fake"


class FakeExportServer:
    """Just enough of OpenViking for the pack export call."""

    def __init__(self, export_zip=None, status=200):
        self.export_zip = export_zip
        self.status = status
        self.calls = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null") if length else None
                outer.calls.append((self.path, self.headers.get("Authorization"), body))
                found = self.path == "/api/v1/pack/export"
                data = outer.export_zip if found and outer.status == 200 else b"{}"
                self.send_response(outer.status if found else 404)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def tree(folder):
    folder = Path(folder)
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / "t"
        self.target.mkdir()

    def test_good_zip(self):
        count = backup._extract(make_zip({"a/b.md": "x", "c.md": "y"}), self.target)
        self.assertEqual(count, 2)
        self.assertEqual(tree(self.target), {"a/b.md": b"x", "c.md": b"y"})

    def test_bad_zip(self):
        with self.assertRaises(backup.ExportError):
            backup._extract(b"not a zip", self.target)

    def test_unsafe_path(self):
        with self.assertRaises(backup.ExportError):
            backup._extract(make_zip({"ok.md": "fine", "../../escape.md": "x"}), self.target)
        self.assertEqual(tree(self.target), {})
        self.assertFalse((Path(self.tmp.name) / "escape.md").exists())

    def test_empty_zip(self):
        with self.assertRaises(backup.ExportError):
            backup._extract(make_zip({}), self.target)

    def test_truncated_zip(self):
        good = make_zip({"a.md": "x" * 5000})
        with self.assertRaises(backup.ExportError):
            backup._extract(good[: len(good) // 2], self.target)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.dest = self.base / "out" / "desktop"
        self.tmp_root = self.base / "scratch"
        self.fake = FakeExportServer(make_zip({"preferences/tea.md": "likes tea"}))
        self.addCleanup(self.fake.close)
        self.messages = []

    def export(self, **kw):
        return backup.export_openviking(self.fake.url, KEY, self.dest, tmp_root=self.tmp_root,
                                        out=self.messages.append, **kw)

    def assert_no_temp_left(self):
        left = list(self.tmp_root.rglob("*")) if self.tmp_root.exists() else []
        self.assertEqual([p for p in left if p.is_file() or p.name.startswith(("new-", "old-", "pack-"))], [])

    def assert_bad_export_keeps_old_one(self, bad):
        self.export()
        before = tree(self.dest)
        self.fake.export_zip = bad
        with self.assertRaises(OSError):
            self.export()
        self.assertEqual(tree(self.dest), before)
        self.assert_no_temp_left()

    def test_export_writes_memories_and_pack(self):
        self.assertEqual(self.export(), 1)
        self.assertEqual(tree(self.dest), {"memories/preferences/tea.md": b"likes tea",
                                           "memories.ovpack": self.fake.export_zip})
        path, auth, body = self.fake.calls[0]
        self.assertEqual((path, auth, body), ("/api/v1/pack/export", "Bearer " + KEY,
                                              {"uri": "viking://~/memories"}))
        self.assertEqual(len(self.messages), 1)
        self.assertNotIn(KEY, self.messages[0])
        self.assert_no_temp_left()

    def test_custom_uri_is_sent(self):
        self.export(uri="viking://~/memories/events")
        self.assertEqual(self.fake.calls[0][2], {"uri": "viking://~/memories/events"})

    def test_unsafe_zip_member_keeps_old_export_and_deletes_nothing(self):
        self.assert_bad_export_keeps_old_one(make_zip({"ok.md": "fine", "../../escape.md": "x"}))
        self.assertFalse((self.base / "escape.md").exists())

    def test_bad_zip_keeps_old_export(self):
        self.assert_bad_export_keeps_old_one(b"not a zip")

    def test_empty_zip_keeps_old_export(self):
        self.assert_bad_export_keeps_old_one(make_zip({}))

    def test_truncated_zip_keeps_old_export(self):
        good = make_zip({"a.md": "x" * 5000})
        self.assert_bad_export_keeps_old_one(good[: len(good) // 2])

    def test_server_error_keeps_old_export_and_hides_the_key(self):
        self.export()
        before = tree(self.dest)
        self.fake.status = 500
        with self.assertRaises(backup.ExportError) as ctx:
            self.export()
        self.assertNotIn(KEY, str(ctx.exception))
        self.assertEqual(tree(self.dest), before)

    def test_unreachable_server_is_an_export_error(self):
        url = self.fake.url
        self.fake.close()
        with self.assertRaises(backup.ExportError):
            backup.export_openviking(url, KEY, self.dest, tmp_root=self.tmp_root, timeout=2)

    def test_good_export_replaces_the_old_one_including_removed_files(self):
        self.export()
        self.fake.export_zip = make_zip({"preferences/coffee.md": "likes coffee"})
        self.export()
        files = tree(self.dest)
        self.assertIn("memories/preferences/coffee.md", files)
        self.assertNotIn("memories/preferences/tea.md", files)
        self.assertEqual(files["memories.ovpack"], self.fake.export_zip)

    def test_swap_failure_restores_the_old_export(self):
        self.export()
        before = tree(self.dest)
        self.fake.export_zip = make_zip({"new.md": "x"})
        real_rename = Path.rename

        def flaky(path, target):
            if path.name.startswith("new-"):
                raise OSError("locked")
            return real_rename(path, target)

        with mock.patch.object(Path, "rename", flaky), self.assertRaises(OSError):
            self.export()
        self.assertEqual(tree(self.dest), before)

    def interrupt_swap(self):
        """Leave the state a crash between the two renames leaves: canonical gone, old copy in temp."""
        canonical = self.dest / "memories"
        old = backup._tmp_dir(self.dest, self.tmp_root) / "old-deadbeef"
        old.parent.mkdir(parents=True, exist_ok=True)
        canonical.rename(old)
        self.assertFalse(canonical.exists())

    def test_interrupted_swap_then_failed_export_restores_the_old_copy(self):
        self.export()
        before = tree(self.dest)
        self.interrupt_swap()
        self.fake.export_zip = b"not a zip"
        with self.assertRaises(backup.ExportError):
            self.export()
        self.assertEqual(tree(self.dest), before)
        self.assert_no_temp_left()

    def test_interrupted_swap_then_successful_export_gives_new_export(self):
        self.export()
        self.interrupt_swap()
        self.fake.export_zip = make_zip({"preferences/coffee.md": "likes coffee"})
        self.export()
        files = tree(self.dest)
        self.assertIn("memories/preferences/coffee.md", files)
        self.assertNotIn("memories/preferences/tea.md", files)
        self.assert_no_temp_left()

    def test_recover_export_alone_restores_the_old_copy(self):
        self.export()
        before = tree(self.dest)
        self.interrupt_swap()
        backup.recover_export(self.dest, self.tmp_root)
        self.assertEqual(tree(self.dest), before)

    def test_recover_without_temp_area_is_a_no_op(self):
        backup.recover_export(self.dest, self.tmp_root)
        self.assertFalse(self.dest.exists())

    def test_tmp_root_inside_dest_is_refused(self):
        with self.assertRaises(backup.ExportError):
            backup.export_openviking(self.fake.url, KEY, self.dest, tmp_root=self.dest / "tmp")
        self.assertEqual(self.fake.calls, [])

    def test_temp_dir_is_named_after_a_hash_of_the_path(self):
        one = backup._tmp_dir(self.base / "a", self.tmp_root)
        two = backup._tmp_dir(self.base / "b", self.tmp_root)
        self.assertNotEqual(one, two)
        self.assertEqual(one, backup._tmp_dir(self.base / "a" / ".." / "a", self.tmp_root))
        self.assertEqual(one.parent, self.tmp_root)


@unittest.skipUnless(HAVE_CRYPTO, "cryptography is not installed")
class SealTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.a, self.b = self.base / "data" / "desktop", self.base / "data" / "chat"
        self.write(self.a, {"memories/preferences/tea.md": b"likes tea", "memories.ovpack": b"\x00pack1"})
        self.write(self.b, {"memories/events/e.md": "喜欢猫".encode("utf-8")})
        self.dirs = {"desktop": self.a, "chat": self.b}
        self.bundle = self.base / "store" / "backup.fymenc"
        self.key_file = self.base / "keys" / "backup.key"
        self.messages = []

    def write(self, folder, files):
        for name, data in files.items():
            path = Path(folder) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def seal(self, **kw):
        kw.setdefault("skip", ("memories.ovpack",))
        return backup.seal(self.dirs, self.bundle, self.key_file, out=self.messages.append, **kw)

    def opens(self):
        return crypto.can_open(self.bundle.read_bytes(), self.key_file.read_bytes())

    def test_nothing_when_no_directory_exists(self):
        dirs = {"desktop": self.base / "nope", "chat": self.base / "nope2"}
        self.assertEqual(backup.seal(dirs, self.bundle, self.key_file, out=self.messages.append), "nothing")
        self.assertFalse(self.bundle.exists())
        self.assertFalse(self.key_file.exists())

    def test_written_then_unchanged(self):
        self.assertEqual(self.seal(), "written")
        before = self.bundle.read_bytes()
        self.assertTrue(self.opens())
        self.assertEqual(self.seal(), "unchanged")
        self.assertEqual(self.bundle.read_bytes(), before)
        self.assertIn("沿用", self.messages[-1])

    def test_creating_the_key_tells_the_user_without_the_key(self):
        self.seal()
        self.assertIn("已生成备份密钥", self.messages[0])
        self.assertNotIn(self.key_file.read_bytes().hex(), "".join(self.messages))
        self.assertEqual(len(self.key_file.read_bytes()), 32)

    def test_state_file_has_two_lines_and_default_sits_beside_the_bundle(self):
        self.seal()
        state = self.bundle.with_name(self.bundle.name + ".state")
        lines = state.read_text(encoding="ascii").split("\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], crypto.digest_dirs(self.dirs, skip=("memories.ovpack",)))
        self.assertEqual(len(lines[1]), 64)
        self.assertEqual(lines[2], "")

    def test_custom_state_file(self):
        state = self.base / "state" / "s.txt"
        self.assertEqual(self.seal(state_file=state), "written")
        self.assertTrue(state.is_file())
        self.assertFalse(self.bundle.with_name(self.bundle.name + ".state").exists())
        self.assertEqual(self.seal(state_file=state), "unchanged")

    def test_no_temp_files_are_left(self):
        self.seal()
        self.assertEqual(sorted(p.name for p in self.bundle.parent.iterdir()),
                         ["backup.fymenc", "backup.fymenc.state"])

    def test_content_change_is_resealed(self):
        self.seal()
        self.write(self.a, {"memories/preferences/tea.md": b"likes coffee"})
        self.assertEqual(self.seal(), "written")
        out = self.base / "opened"
        backup.open_bundle(self.bundle, out, self.key_file)
        self.assertEqual((out / "desktop/memories/preferences/tea.md").read_bytes(), b"likes coffee")

    def test_pack_that_differs_only_in_bytes_is_skipped_when_listed(self):
        self.seal()
        self.write(self.a, {"memories.ovpack": b"\x00pack2 other zip times"})
        self.assertEqual(self.seal(), "unchanged")
        self.assertEqual(self.seal(skip=()), "written")

    def test_new_key_forces_a_new_bundle_even_when_the_content_is_unchanged(self):
        self.seal()
        self.key_file.unlink()
        self.assertEqual(self.seal(), "written")
        self.assertIn("已生成备份密钥", self.messages[-2])
        self.assertTrue(self.opens())

    def test_different_existing_key_is_resealed(self):
        self.seal()
        self.key_file.write_bytes(os.urandom(32))
        self.assertFalse(self.opens())
        self.assertEqual(self.seal(), "written")
        self.assertTrue(self.opens())

    def test_swapped_bundle_is_resealed(self):
        self.seal()
        self.bundle.write_bytes(crypto.MAGIC + b"\x00" * 60)
        self.assertEqual(self.seal(), "written")
        self.assertTrue(self.opens())

    def test_failed_bundle_write_is_a_runtime_error_and_the_old_state_does_not_win(self):
        self.seal()
        self.key_file.unlink()
        real_replace = os.replace
        calls = []

        def flaky(src, dst, *a, **kw):
            if str(dst).endswith("backup.fymenc") and not calls:
                calls.append(dst)
                raise PermissionError("locked")
            return real_replace(src, dst, *a, **kw)

        with mock.patch.object(backup.os, "replace", flaky), self.assertRaises(RuntimeError):
            self.seal()
        self.assertTrue(calls)
        self.assertFalse(self.opens())  # still the old bundle, which the new key cannot open
        self.assertEqual(self.seal(), "written")  # the key exists now: the old state must not win
        self.assertTrue(self.opens())

    def test_key_next_to_the_bundle_or_inside_data_is_refused(self):
        for key in (self.bundle.parent / "backup.key", self.a / "backup.key", self.b / "sub" / "backup.key",
                    self.bundle.parent / "x" / ".." / "backup.key"):
            with self.subTest(key=str(key)):
                with self.assertRaises(RuntimeError):
                    backup.seal(self.dirs, self.bundle, key, out=self.messages.append)
                self.assertFalse(key.exists())
        self.assertFalse(self.bundle.exists())

    def test_bad_key_file_is_a_runtime_error_and_is_not_touched(self):
        self.key_file.parent.mkdir(parents=True)
        self.key_file.write_bytes(b"x" * 31)
        with self.assertRaises(RuntimeError):
            self.seal()
        self.assertEqual(self.key_file.read_bytes(), b"x" * 31)
        self.assertFalse(self.bundle.exists())

    def test_missing_library_stops_before_anything_is_written(self):
        import builtins
        real_import = builtins.__import__

        def fake(name, *a, **kw):
            if name.startswith("cryptography"):
                raise ImportError(name)
            return real_import(name, *a, **kw)

        with mock.patch.object(builtins, "__import__", fake), self.assertRaises(crypto.CryptoMissing):
            self.seal()
        self.assertFalse(self.bundle.exists())

    def test_open_bundle_round_trip(self):
        self.seal()
        out = self.base / "opened"
        count = backup.open_bundle(self.bundle, out, self.key_file)
        self.assertEqual(count, 3)
        self.assertEqual(tree(out), {
            "desktop/memories/preferences/tea.md": b"likes tea", "desktop/memories.ovpack": b"\x00pack1",
            "chat/memories/events/e.md": "喜欢猫".encode("utf-8")})

    def test_open_bundle_wrong_key_and_missing_key(self):
        self.seal()
        other = self.base / "other.key"
        other.write_bytes(os.urandom(32))
        out = self.base / "opened"
        with self.assertRaises(crypto.DecryptError):
            backup.open_bundle(self.bundle, out, other)
        self.assertFalse(out.exists())
        with self.assertRaises(OSError):
            backup.open_bundle(self.bundle, out, self.base / "missing.key")

    def test_open_bundle_missing_bundle_is_oserror(self):
        self.seal()
        with self.assertRaises(OSError):
            backup.open_bundle(self.base / "nope.fymenc", self.base / "o", self.key_file)

    def test_export_then_seal_then_open(self):
        fake = FakeExportServer(make_zip({"preferences/tea.md": "likes tea"}))
        self.addCleanup(fake.close)
        dest = self.base / "exports" / "desktop"
        backup.export_openviking(fake.url, KEY, dest, tmp_root=self.base / "scratch", out=self.messages.append)
        self.assertEqual(backup.seal({"desktop": dest}, self.bundle, self.key_file, skip=("memories.ovpack",),
                                     out=self.messages.append), "written")
        out = self.base / "opened"
        self.assertEqual(backup.open_bundle(self.bundle, out, self.key_file), 2)
        self.assertEqual((out / "desktop/memories/preferences/tea.md").read_bytes(), b"likes tea")


if __name__ == "__main__":
    unittest.main()
