"""The record fields (model.FIELDS): the stores keep them, hand them back, and nothing loses them on the way.

Nothing fills them yet and no request format changes: a store that never receives fields behaves as before
(the last test class compares the gateway's answers with literal ones), and old store files are read, and when
writable upgraded, without losing a memory.
"""
import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from feiyu_memgate import registry
from feiyu_memgate.backends.base import MemoryBackend, parse_item, parse_record
from feiyu_memgate.backends.embedding import EmbeddingBackend, _unit
from feiyu_memgate.backends.hybrid import HybridBackend
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.backends.sqlite import SQLiteBackend
from feiyu_memgate.core import stages
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.recall import RecallContext, recall
from feiyu_memgate.core.tidy import index_grams, normalize
from feiyu_memgate.forgetting.archive import Archive
from feiyu_memgate.gateway.app import Gateway
from feiyu_memgate.model import FIELDS, Candidate, ClientPolicy, Hit, clean_fields

from .test_backend_embedding import DIMS, FakeEmbeddingServer, fake_vector
from .test_forgetting_engine import DAY, T0, EngineCase, FakeStore

ALL = {"speaker": "mia", "about": "tom", "channel": "kitchen", "audience": "group", "origin": "said",
       "event_date": "2026-03-01", "kind": "event"}
SOME = {"speaker": "mia", "channel": "kitchen"}
TEA = "I like green tea a lot"
TEA2 = "I like green tea very much"

# The table definitions as the code before the fields wrote them (copied from the previous version).
OLD_SQLITE = """
CREATE TABLE IF NOT EXISTS memories (
    ref TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    date TEXT
);
CREATE TABLE IF NOT EXISTS grams (
    gram TEXT NOT NULL,
    ref TEXT NOT NULL,
    PRIMARY KEY (gram, ref)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS grams_by_ref ON grams (ref);
CREATE INDEX IF NOT EXISTS memories_by_created ON memories (created_at);
CREATE TABLE IF NOT EXISTS info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
OLD_EMBEDDING = """
CREATE TABLE IF NOT EXISTS memories (
    ref TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    date TEXT,
    vector BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
OLD_ARCHIVE = """
CREATE TABLE IF NOT EXISTS archive (
    store        TEXT NOT NULL,
    ref          TEXT NOT NULL,
    text         TEXT NOT NULL,
    date         TEXT,
    uses         INTEGER NOT NULL DEFAULT 0,
    forgotten_at REAL NOT NULL,
    expires_at   REAL NOT NULL,
    PRIMARY KEY (store, ref)
)
"""


def columns(path, table="memories"):
    db = sqlite3.connect(path)
    try:
        return [row[1] for row in db.execute("PRAGMA table_info(%s)" % table).fetchall()]
    finally:
        db.close()


def info(path):
    db = sqlite3.connect(path)
    try:
        return dict(db.execute("SELECT key, value FROM info").fetchall())
    finally:
        db.close()


class CleanFieldsTest(unittest.TestCase):
    def test_none_and_empty_give_an_empty_dict(self):
        self.assertEqual(clean_fields(None), {})
        self.assertEqual(clean_fields({}), {})

    def test_a_new_dict_with_the_entries_that_are_not_none(self):
        given = {"speaker": "mia", "about": None, "kind": "event"}
        out = clean_fields(given)
        self.assertEqual(out, {"speaker": "mia", "kind": "event"})
        self.assertIsNot(out, given)
        self.assertEqual(given["about"], None)  # the argument is left alone

    def test_every_field_is_accepted(self):
        self.assertEqual(clean_fields(ALL), ALL)
        self.assertEqual(set(ALL), set(FIELDS))

    def test_an_unknown_key_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            clean_fields({"speaker": "mia", "mood": "x"})
        self.assertIn("mood", str(caught.exception))

    def test_a_value_must_be_a_non_empty_string_of_at_most_128_characters(self):
        for bad in (1, True, 1.5, ["a"], b"x", "", {"a": 1}):
            with self.assertRaises(ValueError, msg=repr(bad)) as caught:
                clean_fields({"channel": bad})
            self.assertIn("channel", str(caught.exception))
        self.assertEqual(clean_fields({"channel": "c" * 128}), {"channel": "c" * 128})
        with self.assertRaises(ValueError):
            clean_fields({"channel": "c" * 129})

    def test_something_that_is_not_a_mapping_is_refused(self):
        with self.assertRaises(ValueError):
            clean_fields([("speaker", "mia")])

    def test_parse_item_still_gives_three_and_parse_record_four(self):
        self.assertEqual(parse_item(("t", "r", "2026-01-01")), ("t", "r", "2026-01-01"))
        self.assertEqual(parse_item({"text": "t", "ref": "r", "fields": SOME}), ("t", "r", None))
        self.assertEqual(parse_record({"text": "t", "ref": "r", "fields": SOME}), ("t", "r", None, SOME))
        self.assertEqual(parse_record(("t", "r"))[3], {})
        self.assertEqual(parse_record("t")[3], {})
        with self.assertRaises(ValueError):
            parse_record({"text": "t", "fields": {"nope": "x"}})


class ModelTest(unittest.TestCase):
    def test_fields_is_the_last_attribute_and_positional_construction_still_works(self):
        hit = Hit("r", 0.5, "t", "2026-01-01", {"m": 1})
        self.assertEqual((hit.meta, hit.fields), ({"m": 1}, {}))
        candidate = Candidate(0.5, "s", None, "r", "t", "2026-01-01", {"m": 1})
        self.assertEqual((candidate.meta, candidate.fields), ({"m": 1}, {}))
        self.assertEqual(Hit.__dataclass_fields__.__len__(), 6)
        self.assertEqual(list(Hit.__dataclass_fields__)[-1], "fields")
        self.assertEqual(list(Candidate.__dataclass_fields__)[-1], "fields")

    def test_as_item_does_not_show_the_fields(self):
        candidate = Candidate(0.5, "s", None, "r", "t", "2026-01-01", fields=dict(ALL))
        self.assertEqual(candidate.as_item(),
                         {"id": "r", "source": "s", "text": "t", "score": 0.5, "date": "2026-01-01"})

    def test_defaults_are_not_shared(self):
        a, b = Hit("a", 0.1), Hit("b", 0.1)
        a.fields["speaker"] = "x"
        self.assertEqual(b.fields, {})


class RecordCases:
    """What every backend that keeps fields must do. Subclasses say how to make one."""

    has_put_many = True
    needs_server = False

    def make(self):
        raise NotImplementedError

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        if self.needs_server:
            self.server = FakeEmbeddingServer()
            self.addCleanup(self.server.stop)
        self.be = self.make()
        self.addCleanup(self.be.close if hasattr(self.be, "close") else (lambda: None))

    def path(self, name="m.db"):
        return os.path.join(self._tmp.name, name)

    def count(self):
        return len(list(self.be.refs()))

    def stored_items(self):
        """What items() yields; a backend without it (inmemory) gives it from its own mapping."""
        if hasattr(self.be, "items"):
            return list(self.be.items())
        return [(ref, *self.be._items[ref]) for ref, _ in self.be.refs()]

    def test_it_says_it_stores_fields(self):
        self.assertIs(self.be.stores_fields, True)
        self.assertIs(MemoryBackend.stores_fields, False)

    def test_put_fields_come_back_in_find_records_and_record(self):
        self.be.put(TEA, ref="a", date="2026-03-01", fields=ALL)
        self.be.put("The deploy happens on Friday", ref="b")
        hit = next(h for h in self.be.find("green tea", 5) if h.ref == "a")
        self.assertEqual(hit.fields, ALL)
        self.assertEqual(hit.date, "2026-03-01")
        self.assertEqual(self.be.record("a"), {"ref": "a", "text": TEA, "date": "2026-03-01", "fields": ALL})
        self.assertEqual(self.be.record("b")["fields"], {})
        by_ref = {r["ref"]: r for r in self.be.records()}
        self.assertEqual(set(by_ref), {"a", "b"})
        self.assertEqual(by_ref["a"]["fields"], ALL)
        self.assertEqual(by_ref["b"], {"ref": "b", "text": "The deploy happens on Friday", "date": None, "fields": {}})

    def test_only_the_fields_that_are_set_come_back(self):
        self.be.put(TEA, ref="a", fields={"speaker": "mia", "about": None})
        self.assertEqual(self.be.record("a")["fields"], {"speaker": "mia"})
        self.assertEqual(self.be.find("green tea", 5)[0].fields, {"speaker": "mia"})

    def test_records_come_in_the_order_of_items(self):
        for i in range(5):
            self.be.put("memory number %d about tea" % i, ref="r%d" % i, fields={"kind": "k%d" % i})
        self.assertEqual([r["ref"] for r in self.be.records()], [row[0] for row in self.stored_items()])

    def test_a_put_without_fields_clears_the_old_ones(self):
        self.be.put(TEA, ref="a", fields=ALL)
        self.be.put(TEA, ref="a")
        self.assertEqual(self.be.record("a")["fields"], {})
        self.assertEqual(self.be.find("green tea", 5)[0].fields, {})
        self.be.put(TEA, ref="a", fields=SOME)
        self.be.put(TEA, ref="a", fields={"kind": "event"})
        self.assertEqual(self.be.record("a")["fields"], {"kind": "event"})
        self.assertEqual(self.count(), 1)

    def test_items_still_give_three_tuples(self):
        self.be.put(TEA, ref="a", date="2026-03-01", fields=ALL)
        self.assertEqual(self.stored_items(), [("a", TEA, "2026-03-01")])
        self.assertEqual(list(self.be.refs()), [("a", "2026-03-01")])
        self.assertEqual(self.be.read("a"), TEA)

    def test_record_of_a_missing_ref_is_a_key_error(self):
        with self.assertRaises(KeyError):
            self.be.record("nothing")
        self.be.put(TEA, ref="a")
        self.be.delete("a")
        with self.assertRaises(KeyError):
            self.be.record("a")

    def test_delete_takes_the_fields_with_the_memory(self):
        self.be.put(TEA, ref="a", fields=ALL)
        self.be.delete("a")
        self.be.put(TEA, ref="a")
        self.assertEqual(self.be.record("a")["fields"], {})

    def test_put_many_takes_fields_in_dict_items_only(self):
        if not self.has_put_many:
            self.skipTest("this backend has no put_many")
        refs = self.be.put_many([
            {"text": TEA, "ref": "d", "date": "2026-03-01", "fields": ALL},
            (TEA2, "t", "2026-03-02"),
            "a plain text about tea",
            {"text": "no fields about tea", "ref": "n"},
        ])
        self.assertEqual(refs[:2], ["d", "t"])
        self.assertEqual(self.be.record("d")["fields"], ALL)
        self.assertEqual(self.be.record("t")["fields"], {})
        self.assertEqual(self.be.record(refs[2])["fields"], {})
        self.assertEqual(self.be.record("n")["fields"], {})

    def test_remember_takes_a_fields_key_of_a_message(self):
        receipt = self.be.remember([
            {"role": "user", "content": TEA, "time": "2026-03-01", "fields": ALL},
            {"role": "assistant", "content": "noted", "fields": SOME},
            {"role": "user", "content": TEA2},
        ])
        refs = sorted(self.be.receipt_refs(receipt))
        self.assertEqual(refs, ["%s-0" % receipt, "%s-2" % receipt])
        self.assertEqual(self.be.record("%s-0" % receipt)["fields"], ALL)
        self.assertEqual(self.be.record("%s-2" % receipt)["fields"], {})
        hit = next(h for h in self.be.find("green tea", 5) if h.ref == "%s-0" % receipt)
        self.assertEqual(hit.fields, ALL)

    def test_neighbours_carry_fields_where_the_backend_has_them(self):
        neighbours = getattr(self.be, "neighbours", None)
        if not callable(neighbours) or isinstance(self.be, (SQLiteBackend, InMemoryBackend)):
            self.skipTest("this backend has no neighbours")
        self.be.put(TEA, ref="a", fields=ALL)
        self.be.put(TEA2, ref="b", fields=SOME)
        self.be.put("completely different words here 12345", ref="c")
        near = neighbours("a", 5)
        self.assertTrue(near)
        self.assertEqual(near[0].ref, "b")
        self.assertEqual(near[0].fields, SOME)
        self.assertEqual([h.fields for h in near if h.ref == "c"] or [{}], [{}])

    def test_an_unknown_key_raises_and_stores_nothing(self):
        with self.assertRaises(ValueError):
            self.be.put(TEA, ref="a", fields={"mood": "x"})
        with self.assertRaises(ValueError):
            self.be.put(TEA, ref="a", fields={"speaker": ""})
        with self.assertRaises(ValueError):
            self.be.remember([{"role": "user", "content": TEA, "fields": SOME},
                              {"role": "user", "content": TEA2, "fields": {"mood": "x"}}])
        if self.has_put_many:
            with self.assertRaises(ValueError):
                self.be.put_many([{"text": TEA, "ref": "x", "fields": SOME},
                                  {"text": TEA2, "ref": "y", "fields": {"mood": "x"}}])
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.be.find("green tea", 5), [])

    def test_a_bad_put_leaves_the_old_memory_and_its_fields(self):
        self.be.put(TEA, ref="a", fields=ALL)
        with self.assertRaises(ValueError):
            self.be.put(TEA2, ref="a", fields={"mood": "x"})
        self.assertEqual(self.be.record("a")["fields"], ALL)
        self.assertEqual(self.be.read("a"), TEA)

    def test_a_message_without_fields_is_stored_as_before(self):
        receipt = self.be.remember([{"role": "user", "content": TEA, "time": "2026-03-01"}])
        row = self.be.record("%s-0" % receipt)
        self.assertEqual((row["text"], row["date"], row["fields"]), (TEA, "2026-03-01", {}))


class InMemoryRecordTest(RecordCases, unittest.TestCase):
    has_put_many = False

    def make(self):
        return InMemoryBackend()

    def test_the_items_mapping_keeps_its_shape(self):
        self.be.put(TEA, ref="a", date="2026-03-01", fields=ALL)
        self.assertEqual(self.be._items["a"], (TEA, "2026-03-01"))

    def test_the_fields_are_copied_in_and_out(self):
        given = dict(SOME)
        self.be.put(TEA, ref="a", fields=given)
        given["speaker"] = "changed"
        self.be.record("a")["fields"]["speaker"] = "changed too"
        self.be.find("green tea", 5)[0].fields["speaker"] = "and this"
        self.assertEqual(self.be.record("a")["fields"], SOME)


class SQLiteRecordTest(RecordCases, unittest.TestCase):
    has_put_many = False

    def make(self):
        return SQLiteBackend(self.path())

    def test_a_new_store_has_the_columns(self):
        self.assertEqual(columns(self.path())[:4], ["ref", "text", "created_at", "date"])
        self.assertEqual(columns(self.path())[4:], list(FIELDS))


class EmbeddingRecordTest(RecordCases, unittest.TestCase):
    needs_server = True

    def make(self):
        return EmbeddingBackend(self.path(), url=self.server.url, model="fake-model")

    def test_a_new_store_has_the_columns_and_the_vector_last(self):
        self.assertEqual(columns(self.path())[4:], list(FIELDS) + ["vector"])

    def test_the_fields_are_not_sent_to_the_embeddings_endpoint(self):
        self.be.put(TEA, ref="a", fields=ALL)
        self.be.remember([{"role": "user", "content": TEA2, "fields": ALL}])
        sent = [text for r in self.server.requests for text in r["body"]["input"]]
        self.assertEqual(sent, [TEA, TEA2])

    def test_invalid_fields_cost_no_embeddings_request(self):
        before = len(self.server.requests)
        with self.assertRaises(ValueError):
            self.be.put(TEA, fields={"mood": "x"})
        with self.assertRaises(ValueError):
            self.be.remember([{"role": "user", "content": TEA, "fields": {"mood": "x"}}])
        self.assertEqual(len(self.server.requests), before)

    def test_the_cache_follows_a_replace_with_other_fields(self):
        self.be.put(TEA, ref="a", fields=ALL)
        self.assertEqual(self.be.find("green tea", 5)[0].fields, ALL)
        self.be.put(TEA, ref="a", fields=SOME)
        self.assertEqual(self.be.find("green tea", 5)[0].fields, SOME)


class HybridRecordTest(RecordCases, unittest.TestCase):
    needs_server = True

    def make(self):
        return registry.create("backend", {"type": "hybrid", "children": [
            {"type": "sqlite", "path": ":memory:"},
            {"type": "embedding", "path": ":memory:", "url": self.server.url, "model": "fake-model"},
        ]})

    def test_the_fields_are_kept_by_every_child_that_stores_them(self):
        self.be.put(TEA, ref="a", fields=ALL)
        for child in self.be.children:
            self.assertEqual(child.record("a")["fields"], ALL)


class OldStyleChild(MemoryBackend):
    """A backend written before the fields: put(text, ref=None, date=None) and nothing else."""

    writable = True

    def __init__(self, hits=()):
        self.store = {}
        self.hits = list(hits)

    def find(self, query, limit, timeout=None, ctl=None):
        return [Hit(ref, 0.8, text, date) for ref, (text, date) in self.store.items()] + list(self.hits)

    def put(self, text, ref=None, date=None):
        self.store[ref] = (text, date)
        return ref

    def delete(self, ref):
        return self.store.pop(ref, None) is not None

    def read(self, ref, timeout=None, ctl=None):
        return self.store[ref][0]


class OldStyleBatchChild(OldStyleChild):
    """The same with a put_many that only knows tuples and texts."""

    def put_many(self, items, timeout=None, ctl=None):
        for item in items:
            if not isinstance(item, tuple):
                raise TypeError("only tuples")
            self.put(*item)
        return [item[1] for item in items]


def hybrid_of(*children):
    """A HybridBackend around ready-made children (the registry is bypassed, as in the hybrid tests)."""
    with mock.patch("feiyu_memgate.backends.hybrid.registry.create", side_effect=lambda kind, spec: spec):
        return HybridBackend(list(children))


class HybridMixedTest(unittest.TestCase):
    def hybrid(self, old, keeper_first=True):
        keeper = registry.create("backend", {"type": "sqlite", "path": ":memory:"})
        children = [keeper, old] if keeper_first else [old, keeper]
        be = hybrid_of(*children)
        self.addCleanup(be.close)
        return be, keeper

    def test_an_old_style_child_is_never_given_fields_and_the_other_keeps_them(self):
        for cls in (OldStyleChild, OldStyleBatchChild):
            for first in (True, False):
                old = cls()
                be, keeper = self.hybrid(old, first)
                self.assertIs(be.stores_fields, True)
                be.put(TEA, ref="a", date="2026-03-01", fields=ALL)
                be.put_many([{"text": TEA2, "ref": "b", "fields": SOME}, (TEA, "c", None)])
                receipt = be.remember([{"role": "user", "content": "tea once more", "fields": SOME}])
                self.assertEqual(old.store["a"], (TEA, "2026-03-01"))
                self.assertEqual(old.store["b"], (TEA2, None))
                self.assertIn("%s-0" % receipt, old.store)
                self.assertEqual(keeper.record("a")["fields"], ALL)
                self.assertEqual(keeper.record("b")["fields"], SOME)
                self.assertEqual(keeper.record("c")["fields"], {})
                self.assertEqual(be.record("a")["fields"], ALL)  # from the child that stores fields
                self.assertEqual({r["ref"] for r in be.records()}, {"a", "b", "c", "%s-0" % receipt})

    def test_fused_hits_carry_the_fields_of_the_child_that_has_them(self):
        for first in (True, False):
            be, _ = self.hybrid(OldStyleChild(), first)
            be.put(TEA, ref="a", fields=ALL)
            be.put("tea without fields", ref="b")
            hits = {h.ref: h for h in be.find("tea", 5)}
            self.assertEqual(hits["a"].fields, ALL)
            self.assertEqual(hits["b"].fields, {})

    def test_stores_fields_is_false_when_no_child_stores_them(self):
        be = hybrid_of(OldStyleChild(), OldStyleChild())
        self.addCleanup(be.close)
        self.assertIs(be.stores_fields, False)
        be.put(TEA, ref="a")
        self.assertEqual(be.find("tea", 3)[0].fields, {})

    def test_the_first_child_that_returned_fields_wins(self):
        one = InMemoryBackend()
        two = InMemoryBackend()
        one.put(TEA, ref="a", fields={"speaker": "from-one"})
        two.put(TEA, ref="a", fields={"speaker": "from-two", "kind": "event"})
        be = hybrid_of(one, two)
        self.addCleanup(be.close)
        self.assertEqual(be.find("green tea", 3)[0].fields, {"speaker": "from-one"})
        self.assertEqual(be.record("a")["fields"], {"speaker": "from-one"})

    def test_neighbours_merge_takes_the_first_child_fields(self):
        class Near(OldStyleChild):
            def __init__(self, fields_by_ref):
                super().__init__()
                self.fields_by_ref = fields_by_ref

            def neighbours(self, ref, limit, timeout=None, ctl=None):
                return [Hit(r, 0.7, "t " + r, None, {}, f) for r, f in self.fields_by_ref.items()]

        class NearKeeper(Near):
            stores_fields = True

        late = NearKeeper({"x": {"speaker": "late"}})
        early = Near({"x": {}, "y": {}})
        first = NearKeeper({"x": {"speaker": "first"}, "y": {"kind": "k"}})
        be = hybrid_of(early, first, late)
        self.addCleanup(be.close)
        out = {h.ref: h.fields for h in be.neighbours("a", 5)}
        self.assertEqual(out, {"x": {"speaker": "first"}, "y": {"kind": "k"}})


class OldStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "old.db")
        self.server = FakeEmbeddingServer()
        self.addCleanup(self.server.stop)

    OLD_ROWS = (("old-1", "I like green tea a lot", "2025-01-02", 1000),
                ("old-2", "The deploy happens on Friday", None, 1001),
                ("old-3", "我最喜欢的饮料是乌龙茶", "2025-03-04", 1002))

    def build_old_sqlite(self):
        db = sqlite3.connect(self.path)
        db.executescript(OLD_SQLITE)
        for ref, text, date, created in self.OLD_ROWS:
            db.execute("INSERT OR REPLACE INTO memories (ref, text, created_at, date) VALUES (?, ?, ?, ?)",
                       (ref, text, created, date))
            db.executemany("INSERT OR IGNORE INTO grams (gram, ref) VALUES (?, ?)",
                           [(g, ref) for g in index_grams(normalize(text), True)])
        db.execute("INSERT OR REPLACE INTO info (key, value) VALUES ('gram_scheme', '2')")
        db.commit()
        db.close()

    def build_old_embedding(self):
        db = sqlite3.connect(self.path)
        db.executescript(OLD_EMBEDDING)
        for ref, text, date, created in self.OLD_ROWS:
            db.execute("INSERT OR REPLACE INTO memories (ref, text, created_at, date, vector) VALUES (?, ?, ?, ?, ?)",
                       (ref, text, created, date, _unit(fake_vector(text, DIMS)).tobytes()))
        for key, value in (("model", "fake-model"), ("document_prefix", ""), ("dim", str(DIMS)), ("gen", "1")):
            db.execute("INSERT OR REPLACE INTO info (key, value) VALUES (?, ?)", (key, value))
        db.commit()
        db.close()

    def open_embedding(self, **options):
        be = EmbeddingBackend(self.path, url=self.server.url, model="fake-model", **options)
        self.addCleanup(be.close)
        return be

    def open_sqlite(self, **options):
        be = SQLiteBackend(self.path, **options)
        self.addCleanup(be.close)
        return be

    def snapshot(self, be, queries):
        return [[(h.ref, round(h.score, 9), h.date, h.text) for h in be.find(q, 5)] for q in queries]

    # -- writable: upgraded

    def test_sqlite_old_store_opened_writable_is_upgraded(self):
        self.build_old_sqlite()
        be = self.open_sqlite()
        self.assertEqual(columns(self.path)[4:], list(FIELDS))
        self.assertEqual(info(self.path)["record_version"], "2")
        self.assertIs(be.stores_fields, True)
        self.assertEqual(be.count(), 3)
        self.assertEqual(be.record("old-1"), {"ref": "old-1", "text": self.OLD_ROWS[0][1], "date": "2025-01-02",
                                              "fields": {}})
        self.assertEqual([r["fields"] for r in be.records()], [{}, {}, {}])
        self.assertEqual(be.find("green tea", 5)[0].ref, "old-1")
        self.assertEqual([h.fields for h in be.find("喜欢的饮料", 5)], [{}])
        be.put("new tea memory", ref="new", fields=ALL)
        self.assertEqual(be.record("new")["fields"], ALL)
        self.assertEqual(be.find("new tea memory", 5)[0].fields, ALL)

    def test_sqlite_search_is_the_same_before_and_after_the_upgrade(self):
        self.build_old_sqlite()
        queries = ["green tea", "deploy Friday", "喜欢的饮料", "nothing matches xyzzy"]
        before = self.snapshot(self.open_sqlite(writable=False), queries)
        after = self.snapshot(self.open_sqlite(), queries)
        self.assertEqual(before, after)
        self.assertTrue(any(before))

    def test_embedding_old_store_opened_writable_is_upgraded(self):
        self.build_old_embedding()
        be = self.open_embedding()
        self.assertEqual(columns(self.path)[4:], ["vector"] + list(FIELDS))
        self.assertEqual(info(self.path)["record_version"], "2")
        self.assertIs(be.stores_fields, True)
        self.assertEqual(be.count(), 3)
        self.assertEqual(be.record("old-2")["fields"], {})
        self.assertEqual(be.record("old-2")["date"], None)
        hits = be.find("I like green tea a lot", 5)
        self.assertEqual(hits[0].ref, "old-1")
        self.assertEqual(hits[0].fields, {})
        be.put("new tea memory", ref="new", fields=SOME)
        self.assertEqual(be.record("new")["fields"], SOME)
        self.assertEqual(be.record("old-1")["fields"], {})
        self.assertEqual(sorted(r[0] for r in be.items()), ["new", "old-1", "old-2", "old-3"])

    def test_embedding_search_is_the_same_before_and_after_the_upgrade(self):
        self.build_old_embedding()
        queries = ["I like green tea a lot", "The deploy happens on Friday", "我最喜欢的饮料是乌龙茶"]
        before = self.snapshot(self.open_embedding(writable=False), queries)
        after = self.snapshot(self.open_embedding(), queries)
        self.assertEqual(before, after)
        self.assertTrue(any(before))

    def test_a_second_open_of_an_upgraded_store_changes_nothing(self):
        self.build_old_sqlite()
        self.open_sqlite().close()
        first = columns(self.path)
        again = self.open_sqlite()
        self.assertEqual(columns(self.path), first)
        self.assertEqual(again.count(), 3)

    # -- read-only: nothing altered

    def test_sqlite_old_store_opened_read_only_is_left_alone(self):
        self.build_old_sqlite()
        before_columns, before_info = columns(self.path), info(self.path)
        be = self.open_sqlite(writable=False)
        self.assertEqual(columns(self.path), before_columns)
        self.assertEqual(info(self.path), before_info)
        self.assertIs(be.stores_fields, False)
        hits = be.find("green tea", 5)
        self.assertEqual(hits[0].ref, "old-1")
        self.assertTrue(all(h.fields == {} for h in hits))
        self.assertEqual(be.record("old-1")["fields"], {})
        self.assertEqual([r["fields"] for r in be.records()], [{}, {}, {}])
        self.assertEqual(list(be.items())[0], ("old-1", self.OLD_ROWS[0][1], "2025-01-02"))
        with self.assertRaises(PermissionError):
            be.put("x", ref="x", fields=SOME)
        self.assertEqual(columns(self.path), before_columns)

    def test_embedding_old_store_opened_read_only_is_left_alone(self):
        self.build_old_embedding()
        before_columns, before_info = columns(self.path), info(self.path)
        be = self.open_embedding(writable=False)
        self.assertEqual(columns(self.path), before_columns)
        self.assertEqual(info(self.path), before_info)
        self.assertIs(be.stores_fields, False)
        hits = be.find("I like green tea a lot", 5)
        self.assertEqual(hits[0].ref, "old-1")
        self.assertEqual(hits[0].fields, {})
        self.assertEqual(be.record("old-3")["fields"], {})
        self.assertEqual(len(list(be.records())), 3)
        near = be.neighbours("old-1", 5)
        self.assertTrue(all(h.fields == {} for h in near))
        self.assertEqual(columns(self.path), before_columns)

    def test_a_read_only_open_of_a_new_store_still_reads_fields(self):
        be = self.open_sqlite()
        be.put(TEA, ref="a", fields=SOME)
        be.close()
        ro = self.open_sqlite(writable=False)
        self.assertIs(ro.stores_fields, True)
        self.assertEqual(ro.find("green tea", 5)[0].fields, SOME)

    # -- the other direction: today's statements on a new store

    def test_todays_statements_work_on_a_new_sqlite_store(self):
        be = self.open_sqlite()
        be.put(TEA, ref="a", date="2026-03-01", fields=ALL)
        db = sqlite3.connect(self.path)
        db.execute("INSERT OR REPLACE INTO memories (ref, text, created_at, date) VALUES (?, ?, ?, ?)",
                   ("a", "rewritten", 5, "2026-04-01"))
        db.execute("INSERT OR REPLACE INTO memories (ref, text, created_at, date) VALUES (?, ?, ?, ?)",
                   ("b", "plain row", 6, None))
        db.commit()
        rows = db.execute("SELECT ref, text, date FROM memories ORDER BY created_at, ref").fetchall()
        db.close()
        self.assertEqual(rows, [("a", "rewritten", "2026-04-01"), ("b", "plain row", None)])
        self.assertEqual(be.record("a")["fields"], {})
        self.assertEqual(be.record("a")["text"], "rewritten")
        self.assertEqual(be.record("b")["fields"], {})

    def test_todays_statements_work_on_a_new_embedding_store(self):
        be = self.open_embedding()
        be.put(TEA, ref="a", date="2026-03-01", fields=ALL)
        blob = _unit(fake_vector("rewritten", DIMS)).tobytes()
        db = sqlite3.connect(self.path)
        db.execute("INSERT OR REPLACE INTO memories (ref, text, created_at, date, vector) VALUES (?, ?, ?, ?, ?)",
                   ("a", "rewritten", 5, "2026-04-01", blob))
        db.commit()
        rows = db.execute("SELECT ref, text, date FROM memories").fetchall()
        db.close()
        self.assertEqual(rows, [("a", "rewritten", "2026-04-01")])
        self.assertEqual(be.record("a")["fields"], {})
        self.assertEqual(be.record("a")["text"], "rewritten")


class CandidateFieldsTest(unittest.TestCase):
    def setUp(self):
        self.pool = Pool()
        self.addCleanup(self.pool.close)

    def run_recall(self, stores, stage_list, limit=5, query="q"):
        ctx = RecallContext(query, limit, pool=self.pool, deadline=time.monotonic() + 3.0)
        out = recall(stores, stage_list, ctx)
        return out, ctx

    def test_a_candidate_carries_a_copy_of_the_hits_fields(self):
        original = dict(ALL)
        hit = Hit("a", 0.9, "text of a", "2026-03-01", {"m": 1}, original)

        class One(MemoryBackend):
            def find(self, query, limit, timeout=None, ctl=None):
                return [hit]

        out, ctx = self.run_recall({"s": One()}, [])
        candidate = ctx.accepted[0]
        self.assertEqual(candidate.fields, ALL)
        self.assertIsNot(candidate.fields, hit.fields)
        candidate.fields["speaker"] = "changed"
        self.assertEqual(hit.fields, ALL)
        self.assertEqual(out["results"], [{"id": "a", "source": "s", "text": "text of a", "score": 0.9,
                                           "date": "2026-03-01"}])

    def test_a_hit_from_a_backend_without_fields_gives_an_empty_dict(self):
        class Plain:
            ref, score, text, date, meta = "a", 0.9, "t", None, {}

        class One(MemoryBackend):
            def find(self, query, limit, timeout=None, ctl=None):
                return [Plain()]

        _, ctx = self.run_recall({"s": One()}, [])
        self.assertEqual(ctx.accepted[0].fields, {})

    def test_a_candidate_added_by_linked_carries_its_neighbours_fields(self):
        class Linking(MemoryBackend):
            def find(self, query, limit, timeout=None, ctl=None):
                return [Hit("a", 0.9, "text of a", None, {}, {"speaker": "mia"})]

            def neighbours(self, ref, limit, timeout=None, ctl=None):
                return [Hit("b", 0.8, "text of b", None, {}, dict(ALL)), Hit("c", 0.7, "text of c")]

        out, ctx = self.run_recall({"s": Linking()}, [stages.Linked()])
        by_ref = {c.ref: c for c in ctx.accepted}
        self.assertEqual(set(by_ref), {"a", "b", "c"})
        self.assertEqual(by_ref["a"].fields, {"speaker": "mia"})
        self.assertEqual(by_ref["b"].fields, ALL)
        self.assertEqual(by_ref["b"].meta["linked_from"], "a")
        self.assertEqual(by_ref["c"].fields, {})
        self.assertEqual(set(out["results"][1]), {"id", "source", "text", "score"})  # as_item is unchanged

    def test_a_real_store_gives_its_fields_to_the_candidates(self):
        store = InMemoryBackend()
        store.put("likes green tea", ref="a", fields=SOME)
        _, ctx = self.run_recall({"s": store}, [], query="green tea")
        self.assertEqual(ctx.accepted[0].fields, SOME)


class ForgettingFieldsTest(EngineCase):
    def forget(self, store, ref="gone"):
        self.ledger.use("private", [(ref, None)], T0 - 500 * DAY)
        return self.engine.sweep({"private": store})

    def test_a_forgotten_memory_is_archived_with_its_fields_and_restored_with_them(self):
        store = InMemoryBackend()
        store.put("gone text", ref="gone", date="2025-01-01", fields=ALL)
        report = self.forget(store)
        self.assertEqual(report["private"]["forgotten"], 1)
        with self.assertRaises(KeyError):
            store.record("gone")
        record = self.archive.get("private", "gone")
        self.assertEqual((record.text, record.date, record.fields), ("gone text", "2025-01-01", ALL))
        self.assertTrue(self.engine.restore("private", "gone", store))
        self.assertEqual(store.record("gone"), {"ref": "gone", "text": "gone text", "date": "2025-01-01",
                                                "fields": ALL})
        self.assertIsNone(self.archive.get("private", "gone"))

    def test_the_archive_holds_the_fields_as_sorted_json(self):
        archive = Archive(":memory:")
        self.addCleanup(archive.close)
        archive.add("s", "r", "t", None, 0, 1.0, 2.0, fields={"speaker": "mia", "about": "tom 汤"})
        raw = archive._db.execute("SELECT fields FROM archive").fetchone()[0]
        self.assertEqual(raw, '{"about": "tom 汤", "speaker": "mia"}')
        archive.add("s", "n", "t", None, 0, 1.0, 2.0)
        self.assertIsNone(archive._db.execute("SELECT fields FROM archive WHERE ref = 'n'").fetchone()[0])
        self.assertEqual(archive.get("s", "n").fields, {})
        self.assertEqual(archive.get("s", "r").fields, {"speaker": "mia", "about": "tom 汤"})
        self.assertEqual([r.fields for r in archive.list()], [{}, {}])  # a listing has no content

    def test_the_record_class_has_fields_last_and_positional_construction_works(self):
        from feiyu_memgate.forgetting.archive import Record
        self.assertEqual(list(Record.__dataclass_fields__)[-1], "fields")
        self.assertEqual(Record("s", "r", "t", None, 0, 1.0, 2.0).fields, {})

    def test_an_old_archive_file_is_upgraded_and_its_rows_restore_without_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "archive.db")
            db = sqlite3.connect(path)
            db.execute(OLD_ARCHIVE)
            db.execute("INSERT INTO archive (store, ref, text, date, uses, forgotten_at, expires_at) "
                       "VALUES ('private', 'old', 'old text', '2024-05-06', 2, 10.0, %f)" % (T0 + 30 * DAY))
            db.commit()
            db.close()
            archive = Archive(path)
            try:
                self.assertIn("fields", columns(path, "archive"))
                record = archive.get("private", "old")
                self.assertEqual((record.text, record.date, record.uses, record.fields),
                                 ("old text", "2024-05-06", 2, {}))
                second = Archive(path)  # a second open is fine
                self.assertEqual(second.count(), 1)
                second.close()
                self.make_engine(archive=archive)
                store = InMemoryBackend()
                self.assertTrue(self.engine.restore("private", "old", store))
                self.assertEqual(store.record("old"),
                                 {"ref": "old", "text": "old text", "date": "2024-05-06", "fields": {}})
            finally:
                archive.close()

    def test_a_store_without_stores_fields_is_archived_and_restored_as_before(self):
        class Strict(FakeStore):
            def put(self, text, ref=None, date=None):  # a TypeError on any other argument
                return super().put(text, ref, date)

        store = Strict({"gone": ("gone text", "2025-01-01")})
        self.assertFalse(getattr(store, "stores_fields", False))
        self.forget(store)
        record = self.archive.get("private", "gone")
        self.assertEqual((record.text, record.fields), ("gone text", {}))
        self.assertTrue(self.engine.restore("private", "gone", store))
        self.assertEqual(store.items["gone"], ("gone text", "2025-01-01"))

    def test_fields_in_an_archive_row_are_not_given_to_a_store_that_cannot_keep_them(self):
        store = InMemoryBackend()
        store.put("gone text", ref="gone", fields=SOME)
        self.forget(store)

        class Strict(FakeStore):
            def put(self, text, ref=None, date=None):
                return super().put(text, ref, date)

        other = Strict()
        self.assertTrue(self.engine.restore("private", "gone", other))
        self.assertEqual(other.items["gone"], ("gone text", None))

    def test_a_store_with_a_failing_record_is_archived_through_read(self):
        class Odd(InMemoryBackend):
            def record(self, ref):
                raise RuntimeError("boom")

        store = Odd()
        store.put("gone text", ref="gone", fields=SOME)
        self.assertEqual(self.forget(store)["private"]["forgotten"], 1)
        self.assertEqual(self.archive.get("private", "gone").text, "gone text")

    def test_a_hybrid_with_no_child_that_has_record_is_archived_through_read(self):
        child = OldStyleChild()
        child.refs = lambda: [(r, d) for r, (t, d) in child.store.items()]
        be = hybrid_of(child)
        self.addCleanup(be.close)
        be.put("gone text", ref="gone")
        self.assertEqual(self.forget(be)["private"]["forgotten"], 1)
        self.assertEqual(self.archive.get("private", "gone").text, "gone text")

    def test_a_hybrid_forgets_and_restores_fields_through_its_field_keeper(self):
        be = registry.create("backend", {"type": "hybrid", "children": [
            {"type": "inmemory"}, {"type": "inmemory"}]})
        self.addCleanup(be.close)
        be.put("gone text", ref="gone", fields=ALL)
        self.forget(be)
        self.assertEqual(self.archive.get("private", "gone").fields, ALL)
        self.assertTrue(self.engine.restore("private", "gone", be))
        for child in be.children:
            self.assertEqual(child.record("gone")["fields"], ALL)


class GatewayAnswersTest(unittest.TestCase):
    """Today's request bodies give today's answers: these dicts are what the code before the fields returned."""

    def test_remember_and_recall_answers_are_unchanged(self):
        store = InMemoryBackend()
        store.put("likes green tea", ref="seed-1")
        policy = ClientPolicy("o", recall=("s",), write="s")
        gw = Gateway({"s": store}, {"o": policy}, {"o": None}, log=lambda *_: None, clock=time.monotonic)
        self.addCleanup(gw.close)
        body = {"messages": [{"role": "user", "content": "I like green tea", "time": "2026-11-14T12:00:00+08:00"},
                             {"role": "assistant", "content": "noted"},
                             {"role": "user", "content": "oolong is nice too"}]}
        reply = gw.remember("o", body)
        self.assertEqual(reply.status, 200)
        receipt = reply.body["receipt"]
        self.assertEqual(reply.body, {"receipt": receipt, "status": "ok"})
        self.assertTrue(receipt.startswith("w-"))
        reply = gw.recall("o", {"query": "green tea"})
        self.assertEqual((reply.status, reply.body), (200, {
            "busy": 0, "failed": 0, "more": 0,
            "results": [
                {"id": "seed-1", "score": 1.0, "source": "s", "text": "likes green tea"},
                {"date": "2026-11-14", "id": receipt + "-0", "score": 1.0, "source": "s", "text": "I like green tea"},
            ]}))
        reply = gw.recall("o", {"query": "oolong", "limit": 3})
        self.assertEqual((reply.status, reply.body), (200, {
            "busy": 0, "failed": 0, "more": 0,
            "results": [{"id": receipt + "-2", "score": 1.0, "source": "s", "text": "oolong is nice too"}]}))
        self.assertEqual(store.record(receipt + "-0")["fields"], {})  # nothing fills the fields yet


if __name__ == "__main__":
    unittest.main()
