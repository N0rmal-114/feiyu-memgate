import json
import os
import tempfile
import unittest
from pathlib import Path

from feiyu_memgate import registry
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.tenants.keyfile import KeyFileTenants, MAX_KEYS_BYTES
from feiyu_memgate.tools import tenants


@registry.register("backend", "keyed-test-store")
class KeyedStore(InMemoryBackend):
    def __init__(self, key, writable=True):
        super().__init__(writable=writable)
        self.key = key


class TenantToolTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)
        self.path = self.dir / "keys.json"

    def test_missing_file_is_empty(self):
        self.assertEqual(tenants.load_keys(self.path), {})
        self.assertEqual(tenants.list_tenants(self.path), [])

    def test_add_list_remove(self):
        tenants.add_tenant(self.path, "100001", "key-one-fake")
        tenants.add_tenant(self.path, "100002", "key-two-fake")
        self.assertEqual(tenants.list_tenants(self.path), ["100001", "100002"])
        self.assertTrue(tenants.remove_tenant(self.path, "100001"))
        self.assertFalse(tenants.remove_tenant(self.path, "100001"))
        self.assertEqual(tenants.load_keys(self.path), {"100002": "key-two-fake"})

    def test_list_never_returns_keys(self):
        tenants.add_tenant(self.path, "100001", "secret-fake-key")
        self.assertNotIn("secret-fake-key", repr(tenants.list_tenants(self.path)))

    def test_duplicate_add_is_refused_unless_replacing(self):
        tenants.add_tenant(self.path, "100001", "old-fake")
        with self.assertRaises(ValueError) as caught:
            tenants.add_tenant(self.path, "100001", "new-fake")
        self.assertNotIn("new-fake", str(caught.exception))
        self.assertEqual(tenants.load_keys(self.path)["100001"], "old-fake")
        tenants.add_tenant(self.path, "100001", "new-fake", replace=True)
        self.assertEqual(tenants.load_keys(self.path)["100001"], "new-fake")

    def test_bad_ids_and_empty_keys_are_refused(self):
        for bad in ("12", "abc", "100001\n", "", " 100001", None):
            with self.assertRaises(ValueError):
                tenants.add_tenant(self.path, bad, "key-fake")
        with self.assertRaises(ValueError):
            tenants.add_tenant(self.path, "100001", "")
        self.assertFalse(self.path.exists())

    def test_custom_id_pattern(self):
        tenants.add_tenant(self.path, "guild-a", "key-fake", id_pattern=r"guild-[a-z]+")
        self.assertEqual(tenants.load_keys(self.path, r"guild-[a-z]+"), {"guild-a": "key-fake"})
        with self.assertRaises(ValueError):
            tenants.load_keys(self.path)  # default pattern is digits only

    def test_content_the_gateway_would_refuse_is_refused(self):
        for text in ("not json", "[]", json.dumps({"100001": ""}), json.dumps({"x": "k"}), json.dumps({"100001": 5})):
            self.path.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                tenants.load_keys(self.path)

    def test_oversize_file_and_write(self):
        self.path.write_bytes(b" " * (MAX_KEYS_BYTES + 1))
        with self.assertRaises(ValueError):
            tenants.load_keys(self.path)
        other = self.dir / "other.json"
        with self.assertRaises(ValueError):
            tenants.write_keys(other, {"100001": "k" * (MAX_KEYS_BYTES + 1)})
        self.assertFalse(other.exists())
        self.assertEqual(os.listdir(self.dir), ["keys.json"])

    def test_write_leaves_no_temp_files_and_keeps_old_file_on_failure(self):
        tenants.write_keys(self.path, {"100001": "k1-fake"})
        self.assertEqual(os.listdir(self.dir), ["keys.json"])
        with self.assertRaises(ValueError):
            tenants.write_keys(self.path, {"bad id": "k2-fake"})
        self.assertEqual(tenants.load_keys(self.path), {"100001": "k1-fake"})
        self.assertEqual(os.listdir(self.dir), ["keys.json"])

    def test_stale_temp_files_are_cleaned_by_the_next_write(self):
        stale = self.dir / "keys.json.tmp-deadbeef"
        stale.write_text("leftover", encoding="utf-8")
        self.assertEqual(tenants.stale_temp_files(self.path), ["keys.json.tmp-deadbeef"])
        tenants.write_keys(self.path, {"100001": "k-fake"})
        self.assertFalse(stale.exists())

    @unittest.skipIf(os.name == "nt", "file modes are not enforced on Windows")
    def test_file_mode_is_private(self):
        tenants.write_keys(self.path, {"100001": "k-fake"})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_keyfile_provider_reads_what_was_written(self):
        tenants.add_tenant(self.path, "100001", "key-fake-a")
        provider = KeyFileTenants(self.path, {"type": "keyed-test-store"}, log=lambda line: None)
        self.assertTrue(provider.valid_id("100001"))
        self.assertEqual(provider.store("100001").key, "key-fake-a")
        self.assertIsNone(provider.store("100002"))


if __name__ == "__main__":
    unittest.main()
