import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False

from feiyu_memgate.tools import crypto


def put(folder, files):
    for name, data in files.items():
        path = Path(folder) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def make_bundle(key, members):
    """Encrypt a hand-built zip the way encrypt_dirs does."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            info = zipfile.ZipInfo("placeholder")
            info.filename = name  # set after __init__, which would turn a backslash into a slash on Windows
            archive.writestr(info, data)
    nonce = os.urandom(12)
    return crypto.MAGIC + nonce + AESGCM(key).encrypt(nonce, buffer.getvalue(), crypto.MAGIC)


@unittest.skipUnless(HAVE_CRYPTO, "cryptography is not installed")
class CryptoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.key = os.urandom(32)
        self.files_a = {"memories/preferences/tea.md": b"likes tea", "memories.ovpack": b"\x00\x01pack"}
        self.files_b = {"memories/events/e.md": "喜欢猫".encode("utf-8")}
        self.a, self.b = self.base / "desktop", self.base / "chat"
        put(self.a, self.files_a)
        put(self.b, self.files_b)
        self.dirs = {"desktop": self.a, "chat": self.b}

    def tree(self, folder):
        folder = Path(folder)
        return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}

    def test_round_trip(self):
        blob = crypto.encrypt_dirs(self.dirs, self.key)
        self.assertTrue(blob.startswith(crypto.MAGIC))
        out = self.base / "out"
        count = crypto.decrypt_to(blob, self.key, out)
        self.assertEqual(count, 3)
        expected = {"desktop/" + k: v for k, v in self.files_a.items()}
        expected.update({"chat/" + k: v for k, v in self.files_b.items()})
        self.assertEqual(self.tree(out), expected)

    def test_missing_dir_is_skipped(self):
        blob = crypto.encrypt_dirs({"desktop": self.a, "chat": self.base / "nope"}, self.key)
        out = self.base / "out"
        self.assertEqual(crypto.decrypt_to(blob, self.key, out), 2)

    def test_each_encryption_uses_a_new_nonce(self):
        one = crypto.encrypt_dirs(self.dirs, self.key)
        two = crypto.encrypt_dirs(self.dirs, self.key)
        self.assertNotEqual(one, two)
        n = len(crypto.MAGIC)
        self.assertNotEqual(one[n:n + 12], two[n:n + 12])

    def test_wrong_key_leaves_no_output(self):
        blob = crypto.encrypt_dirs(self.dirs, self.key)
        out = self.base / "out"
        with self.assertRaises(crypto.DecryptError):
            crypto.decrypt_to(blob, os.urandom(32), out)
        self.assertFalse(out.exists() and any(out.iterdir()))
        self.assertEqual([p.name for p in self.base.iterdir() if p.name.startswith(".fym-decrypt")], [])

    def test_tampered_blob_is_refused(self):
        blob = bytearray(crypto.encrypt_dirs(self.dirs, self.key))
        blob[-20] ^= 1
        out = self.base / "out"
        with self.assertRaises(crypto.DecryptError):
            crypto.decrypt_to(bytes(blob), self.key, out)
        self.assertFalse(out.exists())

    def test_wrong_magic_is_refused(self):
        with self.assertRaises(crypto.DecryptError):
            crypto.decrypt_to(b"NOPE1" + b"x" * 60, self.key, self.base / "out")

    def test_key_is_created_once(self):
        path = self.base / "keys" / "backup.key"
        key, created = crypto.load_or_create_key(path)
        self.assertTrue(created)
        self.assertEqual(len(key), 32)
        again, created = crypto.load_or_create_key(path)
        self.assertFalse(created)
        self.assertEqual(again, key)

    def test_bad_key_file_raises(self):
        path = self.base / "bad.key"
        path.write_bytes(b"x" * 31)
        with self.assertRaises(crypto.KeyFileError):
            crypto.load_or_create_key(path)
        self.assertEqual(path.read_bytes(), b"x" * 31)

    def test_non_empty_output_dir_is_refused(self):
        blob = crypto.encrypt_dirs(self.dirs, self.key)
        out = self.base / "out"
        put(out, {"keep.txt": b"mine"})
        with self.assertRaises(crypto.DecryptError):
            crypto.decrypt_to(blob, self.key, out)
        self.assertEqual(self.tree(out), {"keep.txt": b"mine"})

    def test_empty_output_dir_is_allowed(self):
        blob = crypto.encrypt_dirs(self.dirs, self.key)
        out = self.base / "out"
        out.mkdir()
        self.assertEqual(crypto.decrypt_to(blob, self.key, out), 3)

    def test_unsafe_member_is_refused(self):
        names = ["../x", "a/../../x", "/abs.txt", "C:/x.txt", "C:x.txt", "./a.txt", "a//b.txt"]
        if os.sep == "/":
            names.append("a\\b.txt")  # zipfile itself reads a backslash as a separator on Windows
        for number, name in enumerate(names):
            with self.subTest(name):
                out = self.base / f"out{number}"
                with self.assertRaises(crypto.DecryptError):
                    crypto.decrypt_to(make_bundle(self.key, {"ok.md": b"fine", name: b"x"}), self.key, out)
                self.assertFalse(out.exists())
                self.assertFalse((self.base / "x").exists())
                self.assertEqual([p.name for p in self.base.iterdir() if p.name.startswith(".fym-decrypt")], [])

    def test_windows_unsafe_names_are_refused(self):
        names = ["notes.", "notes ", "dir./a.txt", "dir /a.txt", "CON", "con.txt", "a/NUL", "a/nul.tar.gz",
                 "COM1", "lpt9.md", "Aux.", "conin$", "x/COM3 .txt"]
        for number, name in enumerate(names):
            with self.subTest(name):
                out = self.base / f"win{number}"
                with self.assertRaises(crypto.DecryptError) as caught:
                    crypto.decrypt_to(make_bundle(self.key, {"ok.md": b"fine", name: b"x"}), self.key, out)
                self.assertNotIn(name.strip(), str(caught.exception))  # the error names no member
                self.assertFalse(out.exists())
                self.assertEqual([p.name for p in self.base.iterdir() if p.name.startswith(".fym-decrypt")], [])

    def test_ordinary_names_with_dots_and_device_lookalikes_are_kept(self):
        files = {"a.b.md": b"1", "console.txt": b"2", "com10.txt": b"3", "nulls/x.md": b"4", ".hidden": b"5",
                 "memories/e v e n t.md": b"6"}
        out = self.base / "fine"
        self.assertEqual(crypto.decrypt_to(make_bundle(self.key, files), self.key, out), len(files))

    def test_names_equal_ignoring_case_are_refused_before_anything_is_written(self):
        cases = [{"memories/Tea.md": b"A", "memories/tea.md": b"B"},
                 {"Notes.md": b"A", "notes.MD": b"B"},
                 {"a/x.md": b"A", "A/y.md": b"B", "a/X.md": b"C"}]
        for number, members in enumerate(cases):
            with self.subTest(members=sorted(members)):
                out = self.base / f"case{number}"
                with self.assertRaises(crypto.DecryptError) as caught:
                    crypto.decrypt_to(make_bundle(self.key, members), self.key, out)
                self.assertNotIn("Tea", str(caught.exception))
                self.assertFalse(out.exists())
                self.assertEqual([p.name for p in self.base.iterdir() if p.name.startswith(".fym-decrypt")], [])

    def test_the_reported_bundle_no_longer_restores_silently_short(self):
        members = {"memories/Tea.md": b"one", "memories/tea.md": b"two", "notes.": b"three", "notes": b"four", "x.md": b"5"}
        out = self.base / "reported"
        with self.assertRaises(crypto.DecryptError):
            crypto.decrypt_to(make_bundle(self.key, members), self.key, out)
        self.assertFalse(out.exists())

    def test_digest_changes_with_content(self):
        first = crypto.digest_dirs(self.dirs)
        self.assertEqual(first, crypto.digest_dirs(self.dirs))
        put(self.a, {"memories/preferences/tea.md": b"likes coffee"})
        second = crypto.digest_dirs(self.dirs)
        self.assertNotEqual(first, second)
        put(self.b, {"new.md": b"x"})
        self.assertNotEqual(second, crypto.digest_dirs(self.dirs))

    def test_digest_can_leave_out_named_files(self):
        first = crypto.digest_dirs(self.dirs, skip=("memories.ovpack",))
        put(self.a, {"memories.ovpack": b"\x00\x02same members, new zip times"})
        self.assertEqual(first, crypto.digest_dirs(self.dirs, skip=("memories.ovpack",)))
        self.assertNotEqual(crypto.digest_dirs(self.dirs), first)  # without skip the pack still counts
        put(self.a, {"memories/preferences/tea.md": b"likes coffee"})
        self.assertNotEqual(first, crypto.digest_dirs(self.dirs, skip=("memories.ovpack",)))

    def test_truncated_blob_is_refused(self):
        blob = crypto.encrypt_dirs(self.dirs, self.key)
        for number, cut in enumerate((blob[:-10], blob[:len(crypto.MAGIC) + 5], blob[:len(crypto.MAGIC) + 12 + 8],
                                      crypto.MAGIC)):
            out = self.base / f"cut{number}"
            with self.subTest(length=len(cut)), self.assertRaises(crypto.DecryptError):
                crypto.decrypt_to(cut, self.key, out)
            self.assertFalse(out.exists())

    def test_key_of_33_bytes_is_refused(self):
        path = self.base / "long.key"
        path.write_bytes(b"k" * 33)
        with self.assertRaises(crypto.KeyFileError):
            crypto.load_or_create_key(path)
        with self.assertRaises(crypto.KeyFileError):
            crypto.read_key(path)
        blob = crypto.encrypt_dirs(self.dirs, self.key)
        with self.assertRaises(crypto.DecryptError):
            crypto.decrypt_to(blob, b"k" * 33, self.base / "out")
        with self.assertRaises(crypto.KeyFileError):
            crypto.encrypt_dirs(self.dirs, b"k" * 33)

    def test_zip_errors_in_an_authenticated_bundle_become_decrypt_errors(self):
        import zlib
        blob = crypto.encrypt_dirs(self.dirs, self.key)
        for error in (zlib.error("bad"), NotImplementedError("compression"), EOFError("short"), ValueError("v"),
                      zipfile.BadZipFile("bad"), RuntimeError("encrypted")):
            out = self.base / ("err-" + type(error).__name__)
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(zipfile.ZipFile, "read", side_effect=error), \
                        self.assertRaises(crypto.DecryptError):
                    crypto.decrypt_to(blob, self.key, out)
                self.assertFalse(out.exists())
                self.assertEqual([p.name for p in self.base.iterdir() if p.name.startswith(".fym-decrypt")], [])

    def test_real_zip_corruption_is_a_decrypt_error(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("a.txt", b"hello world " * 200)
        data = bytearray(buffer.getvalue())
        data[40] ^= 0xFF  # inside the compressed stream
        nonce = os.urandom(12)
        blob = crypto.MAGIC + nonce + AESGCM(self.key).encrypt(nonce, bytes(data), crypto.MAGIC)
        out = self.base / "out"
        with self.assertRaises(crypto.DecryptError):
            crypto.decrypt_to(blob, self.key, out)
        self.assertFalse(out.exists())

    def test_module_has_no_default_key_path(self):
        self.assertFalse(hasattr(crypto, "KEY_FILE"))

    def test_missing_cryptography_is_reported(self):
        import builtins
        real_import = builtins.__import__

        def fake(name, *a, **kw):
            if name.startswith("cryptography"):
                raise ImportError(name)
            return real_import(name, *a, **kw)

        builtins.__import__ = fake
        try:
            with self.assertRaises(crypto.CryptoMissing):
                crypto.encrypt_dirs(self.dirs, self.key)
        finally:
            builtins.__import__ = real_import


if __name__ == "__main__":
    unittest.main()
