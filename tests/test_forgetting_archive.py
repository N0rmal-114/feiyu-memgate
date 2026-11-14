import os
import tempfile
import unittest

from feiyu_memgate.forgetting.archive import Archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.archive = Archive(":memory:")
        self.addCleanup(self.archive.close)

    def test_round_trip(self):
        self.archive.add("s", "a", "some text", "2026-01-02", 3, 1000.0, 2000.0)
        record = self.archive.get("s", "a")
        self.assertEqual((record.store, record.ref, record.text, record.date, record.uses,
                          record.forgotten_at, record.expires_at),
                         ("s", "a", "some text", "2026-01-02", 3, 1000.0, 2000.0))
        self.assertIsNone(self.archive.get("s", "missing"))
        self.assertIsNone(self.archive.get("other", "a"))

    def test_add_replaces(self):
        self.archive.add("s", "a", "old", None, 0, 1.0, 2.0)
        self.archive.add("s", "a", "new", None, 1, 3.0, 4.0)
        self.assertEqual(self.archive.count(), 1)
        self.assertEqual(self.archive.get("s", "a").text, "new")

    def test_list_has_no_text(self):
        self.archive.add("s", "a", "secret one", "2026-01-01", 0, 10.0, 20.0)
        self.archive.add("t", "b", "secret two", None, 2, 5.0, 20.0)
        everything = self.archive.list()
        self.assertEqual([(r.store, r.ref) for r in everything], [("t", "b"), ("s", "a")])
        self.assertTrue(all(r.text is None for r in everything))
        self.assertEqual([r.ref for r in self.archive.list("s")], ["a"])

    def test_remove(self):
        self.archive.add("s", "a", "x", None, 0, 1.0, 2.0)
        self.archive.remove("s", "a")
        self.archive.remove("s", "a")
        self.assertEqual(self.archive.count(), 0)

    def test_purge_deletes_only_the_expired(self):
        self.archive.add("s", "old", "x", None, 0, 1.0, 100.0)
        self.archive.add("s", "new", "y", None, 0, 1.0, 300.0)
        self.assertEqual(self.archive.purge(99.0), 0)
        self.assertEqual(self.archive.purge(100.0), 1)
        self.assertEqual([r.ref for r in self.archive.list()], ["new"])

    def test_a_file_database_survives_reopening(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "forgotten.db")
            first = Archive(path)
            first.add("s", "a", "kept", None, 0, 1.0, 2.0)
            first.close()
            second = Archive(path)
            try:
                self.assertEqual(second.get("s", "a").text, "kept")
            finally:
                second.close()
