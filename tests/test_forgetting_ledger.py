import os
import tempfile
import threading
import unittest

from feiyu_memgate.forgetting.ledger import Entry, Ledger


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(":memory:", wall=lambda: 5000.0)
        self.addCleanup(self.ledger.close)

    def test_touch_inserts_missing_and_never_overwrites(self):
        self.ledger.touch("s", [("a", 100.0), ("b", None)], 900.0)
        self.ledger.touch("s", [("a", 999.0)], 950.0)
        got = self.ledger.get_many("s", ["a", "b", "c"])
        self.assertEqual(set(got), {"a", "b"})
        self.assertEqual(got["a"], Entry("s", "a", 100.0, None, 0, False, 900.0))
        self.assertEqual(got["b"].first_seen, 900.0)

    def test_use_creates_and_increments(self):
        self.ledger.use("s", [("a", 100.0)], 1000.0)
        self.ledger.use("s", [("a", 100.0)], 2000.0)
        entry = self.ledger.get_many("s", ["a"])["a"]
        self.assertEqual((entry.first_seen, entry.last_used, entry.uses), (100.0, 2000.0, 2))

    def test_use_keeps_first_seen_of_an_existing_row(self):
        self.ledger.touch("s", [("a", 10.0)], 20.0)
        self.ledger.use("s", [("a", 999.0)], 30.0)
        self.assertEqual(self.ledger.get_many("s", ["a"])["a"].first_seen, 10.0)

    def test_stores_are_separate(self):
        self.ledger.use("one", [("a", None)], 1.0)
        self.ledger.use("two", [("a", None)], 2.0)
        self.assertEqual(self.ledger.stores(), ["one", "two"])
        self.assertEqual(self.ledger.get_many("one", ["a"])["a"].last_used, 1.0)
        self.assertEqual([e.ref for e in self.ledger.entries("two")], ["a"])

    def test_pin_unpin(self):
        self.ledger.touch("s", [("a", 1.0)], 1.0)
        self.ledger.pin("s", ["a", "new"])
        got = self.ledger.get_many("s", ["a", "new"])
        self.assertTrue(got["a"].pinned)
        self.assertTrue(got["new"].pinned)
        self.assertEqual(got["new"].first_seen, 5000.0)
        self.ledger.unpin("s", ["a"])
        self.assertFalse(self.ledger.get_many("s", ["a"])["a"].pinned)

    def test_pin_keeps_the_use_history(self):
        self.ledger.use("s", [("a", 1.0)], 2.0)
        self.ledger.pin("s", ["a"])
        entry = self.ledger.get_many("s", ["a"])["a"]
        self.assertEqual((entry.uses, entry.last_used, entry.pinned), (1, 2.0, True))

    def test_remove(self):
        self.ledger.touch("s", [("a", 1.0), ("b", 1.0)], 1.0)
        self.ledger.remove("s", ["a", "nothing"])
        self.assertEqual([e.ref for e in self.ledger.entries("s")], ["b"])

    def test_revive_inserts_or_updates_last_used(self):
        self.ledger.revive("s", "a", 700.0)
        entry = self.ledger.get_many("s", ["a"])["a"]
        self.assertEqual((entry.first_seen, entry.last_used, entry.uses), (700.0, 700.0, 0))
        self.ledger.use("s", [("a", None)], 800.0)
        self.ledger.revive("s", "a", 900.0)
        entry = self.ledger.get_many("s", ["a"])["a"]
        self.assertEqual((entry.last_used, entry.uses), (900.0, 1))

    def test_counts(self):
        self.ledger.touch("s", [("a", 1.0), ("b", 1.0), ("c", 1.0)], 1.0)
        self.ledger.pin("s", ["a"])
        self.ledger.use("s", [("b", None)], 2.0)
        self.assertEqual(self.ledger.counts("s"), {"total": 3, "pinned": 1, "used": 1})
        self.assertEqual(self.ledger.counts("nope"), {"total": 0, "pinned": 0, "used": 0})

    def test_many_refs_in_one_call(self):
        refs = ["r%d" % i for i in range(1500)]
        self.ledger.touch("s", [(r, 1.0) for r in refs], 1.0)
        self.assertEqual(len(self.ledger.get_many("s", refs)), 1500)
        self.assertEqual(len(self.ledger.entries("s")), 1500)

    def test_empty_inputs_are_fine(self):
        self.ledger.touch("s", [], 1.0)
        self.ledger.use("s", [], 1.0)
        self.assertEqual(self.ledger.get_many("s", []), {})

    def test_concurrent_uses_are_all_counted(self):
        def worker():
            for _ in range(50):
                self.ledger.use("s", [("a", 1.0)], 2.0)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.ledger.get_many("s", ["a"])["a"].uses, 400)

    def test_a_file_database_survives_reopening(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.db")
            first = Ledger(path)
            first.use("s", [("a", 1.0)], 2.0)
            first.close()
            second = Ledger(path)
            try:
                self.assertEqual(second.get_many("s", ["a"])["a"].uses, 1)
            finally:
                second.close()


class ManagedSinceTests(unittest.TestCase):
    """The deletion clock: set when the ledger first learns of a memory, kept afterwards (revive excepted)."""

    def setUp(self):
        self.ledger = Ledger(":memory:", wall=lambda: 5000.0)
        self.addCleanup(self.ledger.close)

    def entry(self, ref="a"):
        return self.ledger.get_many("s", [ref])[ref]

    def test_touch_use_and_pin_set_it_when_they_create_the_row(self):
        self.ledger.touch("s", [("t", 10.0)], 900.0)
        self.ledger.use("s", [("u", 20.0)], 910.0)
        self.ledger.pin("s", ["p"])
        self.assertEqual(self.entry("t").managed_since, 900.0)
        self.assertEqual(self.entry("u").managed_since, 910.0)
        self.assertEqual(self.entry("p").managed_since, 5000.0)  # the ledger's own wall clock
        # first_seen stays the memory's own date
        self.assertEqual((self.entry("t").first_seen, self.entry("u").first_seen), (10.0, 20.0))

    def test_later_calls_never_change_it(self):
        self.ledger.touch("s", [("a", 10.0)], 900.0)
        self.ledger.touch("s", [("a", 10.0)], 1000.0)
        self.ledger.use("s", [("a", 10.0)], 1100.0)
        self.ledger.pin("s", ["a"])
        self.ledger.unpin("s", ["a"])
        entry = self.entry()
        self.assertEqual((entry.managed_since, entry.last_used), (900.0, 1100.0))

    def test_revive_resets_it_to_now(self):
        self.ledger.touch("s", [("a", 10.0)], 900.0)
        self.ledger.revive("s", "a", 2000.0)
        entry = self.entry()
        self.assertEqual((entry.managed_since, entry.last_used, entry.first_seen), (2000.0, 2000.0, 10.0))
        self.ledger.revive("s", "new", 2100.0)
        self.assertEqual(self.entry("new").managed_since, 2100.0)


class MigrationTests(unittest.TestCase):
    OLD_SCHEMA = (
        "CREATE TABLE ledger (store TEXT NOT NULL, ref TEXT NOT NULL, first_seen REAL NOT NULL, "
        "last_used REAL, uses INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (store, ref))")

    def make_old_file(self, folder):
        import sqlite3
        path = os.path.join(folder, "ledger.db")
        db = sqlite3.connect(path)
        db.execute(self.OLD_SCHEMA)
        db.execute("INSERT INTO ledger VALUES ('s', 'old', 100.0, 200.0, 3, 1)")
        db.execute("INSERT INTO ledger VALUES ('s', 'older', 50.0, NULL, 0, 0)")
        db.commit()
        db.close()
        return path

    def test_an_old_file_gets_the_column_and_its_rows_start_at_the_migration_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_old_file(tmp)
            ledger = Ledger(path, wall=lambda: 7000.0)
            try:
                got = ledger.get_many("s", ["old", "older"])
                # nothing else about the rows changes; first_seen is still the memory's own date
                self.assertEqual(got["old"], Entry("s", "old", 100.0, 200.0, 3, True, 7000.0))
                self.assertEqual(got["older"], Entry("s", "older", 50.0, None, 0, False, 7000.0))
                ledger.touch("s", [("new", 1.0)], 7100.0)
                self.assertEqual(ledger.get_many("s", ["new"])["new"].managed_since, 7100.0)
            finally:
                ledger.close()

    def test_migrating_twice_keeps_the_first_migration_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_old_file(tmp)
            Ledger(path, wall=lambda: 7000.0).close()
            again = Ledger(path, wall=lambda: 9000.0)
            try:
                self.assertEqual(again.get_many("s", ["old"])["old"].managed_since, 7000.0)
            finally:
                again.close()


class NoContentTests(unittest.TestCase):
    def test_the_table_has_no_text_column(self):
        ledger = Ledger(":memory:")
        self.addCleanup(ledger.close)
        columns = [r[1] for r in ledger._db.execute("PRAGMA table_info(ledger)")]
        self.assertEqual(columns, ["store", "ref", "first_seen", "last_used", "uses", "pinned", "managed_since"])
