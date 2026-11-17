import datetime
import os
import tempfile
import unittest
from unittest import mock

from feiyu_memgate.backends import embedding as embedding_module
from feiyu_memgate.backends import hybrid as hybrid_module
from feiyu_memgate.backends import sqlite as sqlite_module
from feiyu_memgate.backends.base import message_date
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.backends.sqlite import SQLiteBackend

from tests.test_backend_embedding import EmbeddingTestBase
from tests.test_backend_hybrid import HybridCase

TODAY = "2030-01-02"


def user(text, **extra):
    return dict({"role": "user", "content": text}, **extra)


class MessageDateTest(unittest.TestCase):
    def test_plain_date(self):
        self.assertEqual(message_date({"time": "2026-11-17"}, "x"), "2026-11-17")

    def test_date_time_with_t_and_with_space(self):
        self.assertEqual(message_date({"time": "2026-11-17T08:30:00+08:00"}, "x"), "2026-11-17")
        self.assertEqual(message_date({"time": "2026-11-17 08:30"}, "x"), "2026-11-17")
        self.assertEqual(message_date({"time": "2026-11-17T"}, "x"), "2026-11-17")

    def test_the_date_is_the_written_one_not_converted(self):
        self.assertEqual(message_date({"time": "2026-11-17T00:30:00+08:00"}), "2026-11-17")
        self.assertEqual(message_date({"time": "2026-11-17T23:30:00-05:00"}), "2026-11-17")

    def test_wrong_separators_and_forms(self):
        for value in ("2026/11/17", "2026.11.17", "20261117", "2026-1-17", "2026-11-7", "26-11-17",
                      "2026-11-171", "2026-11-17x", "2026-11-17,08:30", "2026-11-17\n", "2026-11-17\t08:30",
                      " 2026-11-17", "2026-11", "", "yesterday", "11-17-2026",
                      "２０２６-１１-１７"):
            self.assertEqual(message_date({"time": value}, "d"), "d", repr(value))

    def test_impossible_dates(self):
        for value in ("2026-02-30", "2026-13-01", "2026-00-10", "2026-11-00", "2026-11-31", "2025-02-29",
                      "0000-01-01"):
            self.assertEqual(message_date({"time": value}, "d"), "d", repr(value))
        self.assertEqual(message_date({"time": "2028-02-29"}, "d"), "2028-02-29")

    def test_not_a_string(self):
        for value in (None, 20261117, 1.5, True, ["2026-11-17"], {"a": 1}, b"2026-11-17",
                      datetime.date(2026, 11, 17)):
            self.assertEqual(message_date({"time": value}, "d"), "d", repr(value))

    def test_missing_key_and_odd_messages(self):
        self.assertEqual(message_date({"role": "user", "content": "x"}, "d"), "d")
        self.assertEqual(message_date({}, "d"), "d")
        self.assertIsNone(message_date({}))
        for odd in (None, "2026-11-17", ["time"], 5):
            self.assertEqual(message_date(odd, "d"), "d")


class SQLiteMessageDateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.be = SQLiteBackend(os.path.join(self._tmp.name, "m.db"), writable=True)
        self.addCleanup(self.be.close)

    def dates(self):
        return {text: date for _, text, date in self.be.items()}

    def test_stores_the_date_of_the_message(self):
        with mock.patch.object(sqlite_module, "_today", return_value=TODAY):
            self.be.remember([user("alpha one", time="2026-11-17T08:30:00+08:00"),
                              user("beta two", time="2026-10-01 07:00")])
        self.assertEqual(self.dates(), {"alpha one": "2026-11-17", "beta two": "2026-10-01"})

    def test_falls_back_to_today(self):
        with mock.patch.object(sqlite_module, "_today", return_value=TODAY):
            self.be.remember([user("alpha one"), user("beta two", time="soon"), user("gamma three", time="2026-02-30"),
                              user("delta four", time=None)])
        self.assertEqual(set(self.dates().values()), {TODAY})
        self.assertEqual(len(self.dates()), 4)


class InMemoryMessageDateTest(unittest.TestCase):
    def dates(self, be):
        return {text: date for _, (text, date) in be._items.items()}

    def test_stores_the_date_of_the_message(self):
        be = InMemoryBackend(writable=True)
        be.remember([user("alpha one", time="2026-11-17"), user("beta two", time="2026-10-01T07:00:00Z")])
        self.assertEqual(self.dates(be), {"alpha one": "2026-11-17", "beta two": "2026-10-01"})
        self.assertEqual({h.text: h.date for h in be.find("alpha one", 5)}["alpha one"], "2026-11-17")

    def test_without_a_usable_time_the_date_stays_unknown_as_before(self):
        be = InMemoryBackend(writable=True)
        be.remember([user("alpha one"), user("beta two", time="2026-13-45"), user("gamma three", time=7)])
        self.assertEqual(set(self.dates(be).values()), {None})


class EmbeddingMessageDateTest(EmbeddingTestBase):
    def dates(self, be):
        return {text: date for _, text, date in be.items()}

    def test_stores_the_date_of_the_message(self):
        be = self.open(writable=True)
        with mock.patch.object(embedding_module, "_today", return_value=TODAY):
            be.remember([user("alpha one", time="2026-11-17T08:30:00+08:00"), user("beta two", time="2026-10-01")])
        self.assertEqual(self.dates(be), {"alpha one": "2026-11-17", "beta two": "2026-10-01"})

    def test_falls_back_to_today(self):
        be = self.open(writable=True)
        with mock.patch.object(embedding_module, "_today", return_value=TODAY):
            be.remember([user("alpha one"), user("beta two", time="2026/11/17")])
        self.assertEqual(set(self.dates(be).values()), {TODAY})


class HybridMessageDateTest(HybridCase):
    def specs(self, **embedding):
        specs = super().specs(**embedding)
        for spec in specs:
            spec["writable"] = True
        return specs

    def dates(self, child):
        return {text: date for _, text, date in child.items()}

    def test_stores_the_date_of_the_message_in_every_child(self):
        be = self.make(writable=True)
        with mock.patch.object(hybrid_module, "_today", return_value=TODAY):
            be.remember([user("alpha one", time="2026-11-17 08:30"), user("beta two", time="2026-10-01")])
        for child in be.children:
            self.assertEqual(self.dates(child), {"alpha one": "2026-11-17", "beta two": "2026-10-01"})

    def test_falls_back_to_today(self):
        be = self.make(writable=True)
        with mock.patch.object(hybrid_module, "_today", return_value=TODAY):
            be.remember([user("alpha one"), user("beta two", time="2026-02-30")])
        for child in be.children:
            self.assertEqual(set(self.dates(child).values()), {TODAY})


if __name__ == "__main__":
    unittest.main()
