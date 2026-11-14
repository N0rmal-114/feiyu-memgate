import json
import os
import tempfile
import unittest
from unittest import mock

from feiyu_memgate.errors import ConfigError
from feiyu_memgate.tenants.keyfile import KeyFileTenants


class KeyFileListTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "keys.json")

    def write(self, data):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def provider(self):
        return KeyFileTenants(self.path, {"type": "inmemory"}, log=lambda line: None)

    def test_list_returns_ids_never_keys(self):
        self.write({"22222": "key-fake-two", "11111": "key-fake-one"})
        ids = self.provider().list()
        self.assertEqual(ids, ["11111", "22222"])
        self.assertNotIn("key-fake", json.dumps(ids))

    def test_list_follows_the_file_and_keeps_last_good_on_bad_content(self):
        self.write({"11111": "key-fake-one"})
        p = self.provider()
        self.assertEqual(p.list(), ["11111"])
        self.write({"11111": "key-fake-one", "33333": "key-fake-three"})
        st = os.stat(self.path)
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        self.assertEqual(p.list(), ["11111", "33333"])
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("not json {{")
        self.assertEqual(p.list(), ["11111", "33333"])

    def test_list_is_empty_without_a_good_file(self):
        self.assertEqual(self.provider().list(), [])

    def test_unknown_or_missing_store_type_fails_at_construction(self):
        self.write({"11111": "key-fake-one"})
        for store in ({"type": "inmemry"}, "nosuchbackend", {"url": "http://x"}, {"type": ""}):
            with self.assertRaises(ConfigError, msg=repr(store)):
                KeyFileTenants(self.path, store, log=lambda line: None)

    def test_known_store_types_are_accepted_without_building_a_backend(self):
        self.write({"11111": "key-fake-one"})
        with mock.patch("feiyu_memgate.tenants.keyfile.create") as create:
            KeyFileTenants(self.path, {"type": "inmemory"}, log=lambda line: None)
            KeyFileTenants(self.path, "sqlite", log=lambda line: None)
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
