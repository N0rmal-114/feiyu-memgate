"""Requests carry record fields, and every record has an audience of its own that recall checks.

A message of POST /v1/remember may name a channel, an origin, an event_date, a kind and an audience. The
gateway turns them into the fields a store keeps, stamps every record with an audience, and the recall stage
`record_audience` drops a record narrower than the client. What that must do is written by hand below (the
record level x client audience table, the validation rules), not computed by the code under test; the leak
scenarios use real SQLite files.
"""
import contextlib
import io
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from feiyu_memgate import cli, config, registry
from feiyu_memgate.backends.embedding import EmbeddingBackend
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.backends.sqlite import SQLiteBackend
from feiyu_memgate.core import stages
from feiyu_memgate.core.recall import recall
from feiyu_memgate.core.redact import MARK
from feiyu_memgate.core.tidy import index_grams, normalize
from feiyu_memgate.core.write import DailyCapGuard, RedactGuard, WriteGuard
from feiyu_memgate.errors import ConfigError
from feiyu_memgate.gateway.app import Gateway, _valid_messages
from feiyu_memgate.model import ClientPolicy, Hit
from feiyu_memgate.tenants.directory import DirectoryTenants
from tests import test_forgetting_engine as fe
from tests.test_audience import GROUP_ID, MemTenants
from tests.test_backend_embedding import FakeEmbeddingServer
from tests.test_record_fields import OLD_SQLITE, columns
from tests.test_stages import FakeStore, StageTestCase

MSG = {"role": "user", "content": "zebra quartz marker about oolong tea"}
SECRET = "zebra quartz marker about oolong tea"
OTHER = "violet harbor lantern near the river"
THIRD = "copper meadow orbit under the bridge"


class NoFields(InMemoryBackend):
    """A backend that keeps no record fields, as one written before they existed."""

    stores_fields = False
    checks_audience = False
    stamp_audience = None  # such a store has none of the methods that go with the fields
    set_audience = None


registry.register("backend", "rf_nofields")(NoFields)


class Spy(InMemoryBackend):
    """An in-memory store that records the message dicts remember() receives."""

    def __init__(self, keeps=True, **options):
        super().__init__(**options)
        self.stores_fields = keeps
        self.received = []

    def remember(self, messages, timeout=None, ctl=None):
        self.received.append([dict(m) for m in messages])
        return super().remember(messages, timeout, ctl)


class Look(WriteGuard):
    """A guard that records the messages it is given."""

    def __init__(self):
        self.seen = []

    def before(self, messages, ctx):
        self.seen.append([dict(m) for m in messages])
        return messages


class Case(unittest.TestCase):
    def tmp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return Path(folder.name)

    def gateway(self, stores, clients, audiences=None, tenants=None, **options):
        tokens = {name: ("tok-rf-%s-" % name).ljust(40, "0") for name in clients}
        gw = Gateway(stores, clients, tokens, audiences=audiences, tenants=tenants, log=lambda line: None, **options)
        self.addCleanup(gw.close)
        return gw

    def build(self, cfg):
        gw = config.build_gateway(cfg, env={}, log=lambda line: None)
        self.addCleanup(gw.close)
        return gw

    @staticmethod
    def say(gw, client, *messages, **body):
        return gw.remember(client, dict(body, messages=[dict(MSG, **m) for m in messages]))

    @staticmethod
    def found(gw, client, query, **body):
        """The texts a recall returns, best first."""
        reply = gw.recall(client, dict(body, query=query))
        assert reply.status == 200, reply
        return [item["text"] for item in reply.body["results"]]


# ---------------------------------------------------------------- what a request may carry

class ValidationTests(Case):
    VALID = {"channel": ("a", "kitchen", "tg:123", "a.b_c-d", "0abc", "x" * 64),
             "origin": ("said", "tool", "curated", "derived"),
             "event_date": ("2026-03-01", "2024-02-29", "1999-12-31"),
             "kind": ("identity", "preference", "event", "plan", "entity"),
             "audience": ("owner", "group", "public")}
    INVALID = {"channel": ("", "Kitchen", "-a", "_a", ".a", "a b", "x" * 65, "a\n", "ké", 5, True, ["a"]),
               "origin": ("told", "SAID", "", " said", 5, ["said"]),
               "event_date": ("2026-02-30", "2025-02-29", "2026-3-1", "2026-03-01T10:00", "2026-03-01 ", "20260301",
                              "", "2026-13-01", 20260301, "yesterday"),
               "kind": ("fact", "Event", "", 5),
               "audience": ("everyone", "Owner", "", 5)}

    def test_each_valid_value_is_kept_as_it_is(self):
        for key, values in self.VALID.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    out = _valid_messages([{"role": "user", "content": "x", key: value}])
                    self.assertEqual(out, [{"role": "user", "content": "x", key: value}])

    def test_each_invalid_value_makes_the_request_invalid(self):
        for key, values in self.INVALID.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertIsNone(_valid_messages([{"role": "user", "content": "x", key: value}]))
                    self.assertIsNone(_valid_messages([{"role": "user", "content": "fine"},
                                                       {"role": "user", "content": "x", key: value}]))

    def test_null_is_not_given(self):
        message = {"role": "user", "content": "x", "time": None, "speaker": None}
        message.update({key: None for key in self.VALID})
        self.assertEqual(_valid_messages([message]), [{"role": "user", "content": "x"}])

    def test_a_message_without_the_keys_is_cleaned_as_before(self):
        given = [{"role": "user", "content": "x", "time": "2026-03-01", "speaker": "mia", "mood": "dropped"}]
        self.assertEqual(_valid_messages(given), [{"role": "user", "content": "x", "time": "2026-03-01",
                                                   "speaker": "mia"}])

    def test_the_gateway_answers_bad_messages_for_the_whole_request(self):
        store = InMemoryBackend()
        gw = self.gateway({"s": store}, {"o": ClientPolicy("o", write="s")})
        for key, values in self.INVALID.items():
            reply = self.say(gw, "o", {"content": "a fine one"}, {key: values[0]})
            with self.subTest(key=key):
                self.assertEqual((reply.status, reply.body["code"]), (400, "bad_messages"))
        self.assertEqual(store.refs(), [])  # nothing of a refused request was written


# ---------------------------------------------------------------- the message dicts guards and stores get

class MessageShapeTests(Case):
    def setUp(self):
        self.look = Look()
        self.guards = [RedactGuard(), DailyCapGuard(), self.look]

    def setup_store(self, keeps, level="owner"):
        store = Spy(keeps=keeps)
        gw = self.gateway({"s": store}, {"o": ClientPolicy("o", write="s", recall=("s",), daily_writes=9)},
                          audiences={"s": level}, guards=self.guards)
        return store, gw

    def test_a_store_that_keeps_no_fields_gets_exactly_todays_messages(self):
        store, gw = self.setup_store(False)
        message = {"role": "user", "content": "I like tea", "time": "2026-11-14T12:00:00+08:00", "speaker": "guest",
                   "mood": "dropped", "channel": "kitchen", "origin": "said", "event_date": "2026-03-01",
                   "kind": "event", "audience": "owner"}
        self.assertEqual(gw.remember("o", {"messages": [message]}).status, 200)
        expected = [{"role": "user", "content": "I like tea", "time": "2026-11-14T12:00:00+08:00",
                     "speaker": "guest"}]
        self.assertEqual(self.look.seen, [expected])
        self.assertEqual(store.received, [expected])
        self.assertEqual(store.record(store.refs()[0][0])["fields"], {})

    def test_a_request_without_the_keys_is_unchanged_for_such_a_store(self):
        store, gw = self.setup_store(False)
        self.say(gw, "o", {})
        self.assertEqual(store.received, [[dict(MSG)]])

    def test_a_store_that_keeps_fields_gets_them_and_so_do_the_guards(self):
        store, gw = self.setup_store(True, "public")
        message = {"role": "user", "content": "I like tea", "speaker": "guest", "channel": "kitchen",
                   "origin": "tool", "event_date": "2026-03-01", "kind": "event", "audience": "group"}
        self.assertEqual(gw.remember("o", {"messages": [message]}).status, 200)
        fields = {"audience": "group", "channel": "kitchen", "origin": "tool", "event_date": "2026-03-01",
                  "kind": "event"}
        expected = [{"role": "user", "content": "I like tea", "speaker": "guest", "fields": fields}]
        self.assertEqual(self.look.seen, [expected])  # after redact and daily_cap: nothing lost on the way
        self.assertEqual(store.received, [expected])
        self.assertEqual(store.record(store.refs()[0][0])["fields"], fields)

    def test_without_the_keys_such_a_store_still_gets_the_audience(self):
        store, gw = self.setup_store(True)
        self.say(gw, "o", {})
        self.assertEqual(store.received, [[dict(MSG, fields={"audience": "owner"})]])

    def test_the_guards_keep_fields_when_they_rebuild_a_message(self):
        ctx = mock.Mock(redact=lambda text: text.upper())
        message = {"role": "user", "content": "tea", "speaker": "mia", "fields": {"audience": "public"}}
        out = RedactGuard().before([message], ctx)
        self.assertEqual(out, [{"role": "user", "content": "TEA", "speaker": "MIA", "fields": {"audience": "public"}}])

    def test_the_answer_of_a_request_without_the_keys_is_what_it_was(self):
        for keeps in (True, False):
            with self.subTest(keeps=keeps):
                store = Spy(keeps=keeps)
                gw = self.gateway({"s": store}, {"o": ClientPolicy("o", write="s", recall=("s",))})
                said = self.say(gw, "o", {"time": "2026-03-01"})
                self.assertEqual(said.status, 200)
                self.assertEqual(set(said.body), {"status", "receipt"})
                self.assertEqual(said.body["status"], "ok")
                reply = gw.recall("o", {"query": SECRET})
                item = reply.body["results"][0]
                self.assertEqual({k: v for k, v in item.items() if k != "score"},
                                 {"id": said.body["receipt"] + "-0", "source": "s", "text": SECRET,
                                  "date": "2026-03-01"})
                self.assertEqual(set(reply.body), {"results", "more", "failed", "busy"})
                self.assertEqual(len(reply.body["results"]), 1)


# ---------------------------------------------------------------- the stamp at write

class StampAtWriteTests(Case):
    def one(self, gw, store):
        records = list(store.records())
        self.assertEqual(len(records), 1)
        return records[0]["fields"]

    def test_a_shared_store_stamps_its_own_level(self):
        for declared, expected in ((None, "owner"), ("owner", "owner"), ("group", "group"), ("public", "public")):
            with self.subTest(declared=declared):
                store = InMemoryBackend()
                gw = self.gateway({"s": store}, {"o": ClientPolicy("o", write="s")},
                                  audiences={} if declared is None else {"s": declared})
                self.assertEqual(self.say(gw, "o", {}).status, 200)
                self.assertEqual(self.one(gw, store), {"audience": expected})

    def test_a_tenant_store_stamps_the_providers_level(self):
        for declared, expected in ((None, "group"), ("public", "public")):
            with self.subTest(declared=declared):
                gw = self.gateway({}, {"t": ClientPolicy("t", tenants="g", write="@tenant", public=True,
                                                         audience="group")},
                                  audiences={} if declared is None else {"g": declared}, tenants={"g": MemTenants()})
                self.assertEqual(self.say(gw, "t", {}, tenant_id=GROUP_ID).status, 200)
                self.assertEqual(self.one(gw, gw.tenants["g"].store(GROUP_ID)), {"audience": expected})

    def test_a_message_may_ask_for_a_narrower_audience_never_a_wider_one(self):
        # asked -> what is kept, for a store whose level is "public"; the order is owner < group < public
        for level, asked, kept in (("public", "public", "public"), ("public", "group", "group"),
                                   ("public", "owner", "owner"), ("group", "group", "group"),
                                   ("group", "owner", "owner"), ("owner", "owner", "owner")):
            with self.subTest(level=level, asked=asked):
                store = InMemoryBackend()
                gw = self.gateway({"s": store}, {"o": ClientPolicy("o", write="s")}, audiences={"s": level})
                self.assertEqual(self.say(gw, "o", {"audience": asked}).status, 200)
                self.assertEqual(self.one(gw, store), {"audience": kept})

    def test_asking_for_a_wider_audience_is_refused_and_writes_nothing(self):
        for level, asked in (("group", "public"), ("owner", "group"), ("owner", "public")):
            with self.subTest(level=level, asked=asked):
                store = InMemoryBackend()
                gw = self.gateway({"s": store}, {"o": ClientPolicy("o", write="s")}, audiences={"s": level})
                reply = self.say(gw, "o", {"content": "fine"}, {"audience": asked})
                self.assertEqual((reply.status, reply.body["code"]), (400, "bad_messages"))
                self.assertEqual(store.refs(), [])

    def test_the_channel_comes_from_the_message_then_the_context_else_nowhere(self):
        cases = (({"channel": "kitchen"}, {}, "kitchen"),
                 ({}, {"channel": "tg:42"}, "tg:42"),
                 ({"channel": "kitchen"}, {"channel": "tg:42"}, "kitchen"),
                 ({}, {}, None),
                 ({}, {"channel": "Not A Channel"}, None),
                 ({}, {"channel": 7}, None),
                 ({}, {"channel": "x" * 65}, None))
        for message, context, channel in cases:
            with self.subTest(message=message, context=context):
                store = InMemoryBackend()
                gw = self.gateway({"s": store}, {"o": ClientPolicy("o", write="s")})
                self.assertEqual(self.say(gw, "o", message, context=context).status, 200)
                fields = self.one(gw, store)
                self.assertEqual(fields.get("channel"), channel)
                self.assertEqual(set(fields), {"audience"} | ({"channel"} if channel else set()))

    def test_event_date_and_kind_are_kept_as_given(self):
        store = InMemoryBackend()
        gw = self.gateway({"s": store}, {"o": ClientPolicy("o", write="s")})
        self.say(gw, "o", {"event_date": "2026-03-01", "kind": "plan"})
        self.assertEqual(self.one(gw, store), {"audience": "owner", "event_date": "2026-03-01", "kind": "plan"})

    def test_origins_a_client_may_write(self):
        self.assertEqual(ClientPolicy("o").origins, ("said", "tool"))
        for allowed, origin, status in ((("said", "tool"), "said", 200), (("said", "tool"), "tool", 200),
                                        (("said", "tool"), "curated", 403), (("said", "tool"), "derived", 403),
                                        (("said", "tool", "curated"), "curated", 200),
                                        (("derived",), "said", 403), ((), "said", 403)):
            with self.subTest(allowed=allowed, origin=origin):
                store = InMemoryBackend()
                gw = self.gateway({"s": store}, {"o": ClientPolicy("o", write="s", origins=allowed)})
                reply = self.say(gw, "o", {"origin": origin})
                self.assertEqual(reply.status, status)
                if status == 403:
                    self.assertEqual(reply.body, {"error": "forbidden", "code": "forbidden"})
                    self.assertEqual(store.refs(), [])
                else:
                    self.assertEqual(self.one(gw, store)["origin"], origin)

    def test_no_origin_given_means_none_is_kept(self):
        store = InMemoryBackend()
        gw = self.gateway({"s": store}, {"o": ClientPolicy("o", write="s")})
        self.say(gw, "o", {})
        self.assertNotIn("origin", self.one(gw, store))

    def test_the_origin_rule_applies_to_a_store_that_keeps_no_fields_too(self):
        gw = self.gateway({"s": NoFields()}, {"o": ClientPolicy("o", write="s")})
        self.assertEqual(self.say(gw, "o", {"origin": "curated"}).status, 403)
        self.assertEqual(self.say(gw, "o", {"origin": "said"}).status, 200)

    def test_an_origin_grants_nothing(self):
        # a record that says it is "curated" is as private as the store it was written to
        pub_store, owner_store = InMemoryBackend(), InMemoryBackend()
        gw = self.gateway({"p": pub_store, "s": owner_store},
                          {"o": ClientPolicy("o", write="s", recall=("s",), origins=("curated",)),
                           "pub": ClientPolicy("pub", recall=("p",), public=True)}, audiences={"p": "public"})
        self.assertEqual(self.say(gw, "o", {"origin": "curated"}).status, 200)
        self.assertEqual(self.one(gw, owner_store)["audience"], "owner")
        self.assertEqual(self.found(gw, "o", SECRET), [SECRET])
        self.assertEqual(self.found(gw, "pub", SECRET), [])


# ---------------------------------------------------------------- the backends

class StampCases:
    """What every backend that can be stamped must do. Subclasses say how to make one."""

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
        self.addCleanup(self.be.close)

    def path(self, name="m.db"):
        return os.path.join(self._tmp.name, name)

    def level(self, ref):
        return self.be.record(ref)["fields"].get("audience")

    def seed(self):
        self.be.put("alpha tea", ref="a")  # no fields
        self.be.put("bravo coffee", ref="b", fields={"channel": "kitchen"})  # fields, no audience
        self.be.put("charlie cocoa", ref="c", fields={"audience": "group"})

    def test_a_new_store_checks_nothing_until_it_is_stamped(self):
        self.assertIs(self.be.checks_audience, False)

    def test_the_first_stamp_gives_records_without_an_audience_the_level(self):
        self.seed()
        self.assertEqual(self.be.stamp_audience("public"), 2)
        self.assertEqual([self.level(ref) for ref in "abc"], ["public", "public", "group"])
        self.assertEqual(self.be.record("b")["fields"], {"channel": "kitchen", "audience": "public"})
        self.assertIs(self.be.checks_audience, True)

    def test_later_stamps_do_nothing(self):
        self.seed()
        self.be.stamp_audience("public")
        self.be.put("delta tea", ref="d")
        self.assertEqual(self.be.stamp_audience("owner"), 0)
        self.assertEqual([self.level(ref) for ref in "abcd"], ["public", "public", "group", None])

    def test_an_empty_store_is_stamped_too(self):
        self.assertEqual(self.be.stamp_audience("group"), 0)
        self.assertIs(self.be.checks_audience, True)
        self.be.put("late tea", ref="late")
        self.assertIsNone(self.level("late"))  # a record arriving later has none: recall treats it as owner

    def test_a_record_put_after_the_stamp_stays_without_an_audience(self):
        self.be.stamp_audience("public")
        self.be.put("later tea", ref="x")
        self.be.remember([{"role": "user", "content": "later coffee"}])
        self.assertEqual([r["fields"].get("audience") for r in self.be.records()], [None, None])

    def test_set_audience_for_the_missing_ones(self):
        self.seed()
        self.be.stamp_audience("owner")
        self.be.put("delta tea", ref="d")
        self.be.put("echo tea", ref="e", fields={"audience": "public"})
        self.assertEqual(self.be.set_audience("group", only_missing=True), 1)
        self.assertEqual([self.level(ref) for ref in "abcde"], ["owner", "owner", "group", "group", "public"])
        self.assertEqual(self.be.set_audience("group", only_missing=True), 0)

    def test_set_audience_for_all_counts_only_what_changed(self):
        self.seed()
        self.be.stamp_audience("owner")  # a, b: owner; c: group
        self.assertEqual(self.be.set_audience("group", only_missing=False), 2)
        self.assertEqual([self.level(ref) for ref in "abc"], ["group", "group", "group"])
        self.assertEqual(self.be.set_audience("public", only_missing=False), 3)
        self.assertEqual(self.be.record("b")["fields"], {"channel": "kitchen", "audience": "public"})

    def test_set_audience_marks_the_store_stamped(self):
        self.seed()
        self.assertEqual(self.be.set_audience("public", only_missing=False), 3)
        self.assertIs(self.be.checks_audience, True)
        self.assertEqual(self.be.stamp_audience("owner"), 0)  # already stamped: this one does nothing
        self.assertEqual([self.level(ref) for ref in "abc"], ["public"] * 3)

    def test_a_level_must_be_one_of_the_three(self):
        for bad in ("everyone", "", None, 3):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.be.stamp_audience(bad)
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.be.set_audience(bad)
        self.assertIs(self.be.checks_audience, False)

    def test_stamping_changes_neither_text_nor_date_nor_other_fields(self):
        self.be.put("alpha tea", ref="a", date="2026-03-01", fields={"speaker": "mia", "kind": "event"})
        self.be.stamp_audience("group")
        self.assertEqual(self.be.record("a"), {"ref": "a", "text": "alpha tea", "date": "2026-03-01",
                                               "fields": {"speaker": "mia", "kind": "event", "audience": "group"}})
        self.assertEqual([h.fields["audience"] for h in self.be.find("alpha tea", 5)], ["group"])


class InMemoryStampTest(StampCases, unittest.TestCase):
    def make(self):
        return InMemoryBackend()

    def test_a_read_only_store_is_not_stamped(self):
        be = InMemoryBackend(writable=False, memories=["seed tea"])
        self.assertEqual(be.stamp_audience("public"), 0)
        self.assertIs(be.checks_audience, False)
        self.assertEqual(be.set_audience("public", only_missing=False), 0)
        self.assertEqual(next(be.records())["fields"], {})

    def test_the_seeded_memories_are_stamped_too(self):
        be = InMemoryBackend(memories=["seed tea"])
        be.stamp_audience("group")
        self.assertEqual(next(be.records())["fields"], {"audience": "group"})


class SQLiteStampTest(StampCases, unittest.TestCase):
    def make(self):
        return SQLiteBackend(self.path())

    def reopened(self, **options):
        self.be.close()
        self.be = SQLiteBackend(self.path(), **options)
        self.addCleanup(self.be.close)
        return self.be

    def info(self):
        db = sqlite3.connect(self.path())
        try:
            return dict(db.execute("SELECT key, value FROM info").fetchall())
        finally:
            db.close()

    def test_the_stamp_is_one_row_of_the_info_table_and_survives_a_reopen(self):
        self.seed()
        self.assertNotIn("audience_stamp", self.info())
        self.be.stamp_audience("public")
        self.assertEqual(self.info()["audience_stamp"], "public")
        self.reopened()
        self.assertIs(self.be.checks_audience, True)
        self.assertEqual(self.be.stamp_audience("owner"), 0)
        self.assertEqual([self.level(ref) for ref in "abc"], ["public", "public", "group"])

    def test_a_read_only_open_of_a_stamped_store_checks(self):
        self.be.stamp_audience("public")
        self.assertIs(self.reopened(writable=False).checks_audience, True)

    def test_a_read_only_open_of_an_unstamped_store_does_not_stamp_it(self):
        self.seed()
        be = self.reopened(writable=False)
        self.assertEqual(be.stamp_audience("public"), 0)
        self.assertEqual(be.set_audience("public", only_missing=False), 0)
        self.assertIs(be.checks_audience, False)
        self.assertEqual([self.level(ref) for ref in "abc"], [None, None, "group"])
        self.assertNotIn("audience_stamp", self.info())

    def test_an_old_file_opened_read_only_is_left_alone(self):
        self.be.close()
        path = self.path("old.db")
        db = sqlite3.connect(path)
        db.executescript(OLD_SQLITE)
        db.execute("INSERT INTO memories (ref, text, created_at, date) VALUES ('o', 'old tea', 1000, NULL)")
        db.commit()
        db.close()
        be = SQLiteBackend(path, writable=False)
        self.addCleanup(be.close)
        self.assertFalse(be.stores_fields)
        self.assertEqual((be.stamp_audience("public"), be.set_audience("public", False)), (0, 0))
        self.assertIs(be.checks_audience, False)
        self.assertNotIn("audience", columns(path))

    def test_a_row_inserted_the_old_way_has_no_audience(self):
        self.be.stamp_audience("public")
        db = sqlite3.connect(self.path())
        db.execute("INSERT OR REPLACE INTO memories (ref, text, created_at, date) VALUES ('raw', 'raw tea', 5, NULL)")
        db.commit()
        db.close()
        self.assertIsNone(self.level("raw"))


class EmbeddingStampTest(StampCases, unittest.TestCase):
    needs_server = True

    def make(self):
        return EmbeddingBackend(self.path(), url=self.server.url, model="fake-model")

    def test_the_stamp_is_kept_in_the_file_and_the_vectors_are_not_touched(self):
        self.seed()
        before = self.be._db.execute("SELECT ref, vector FROM memories ORDER BY ref").fetchall()
        gen = self.be._db.execute("SELECT value FROM info WHERE key = 'gen'").fetchone()
        self.be.stamp_audience("public")
        self.assertEqual(self.be._db.execute("SELECT ref, vector FROM memories ORDER BY ref").fetchall(), before)
        self.assertEqual(self.be._db.execute("SELECT value FROM info WHERE key = 'gen'").fetchone(), gen)
        self.be.close()
        self.be = EmbeddingBackend(self.path(), url=self.server.url, model="fake-model")
        self.addCleanup(self.be.close)
        self.assertIs(self.be.checks_audience, True)
        self.assertEqual(self.be.stamp_audience("owner"), 0)


class HybridStampTest(StampCases, unittest.TestCase):
    needs_server = True

    def make(self):
        return registry.create("backend", {"type": "hybrid", "children": [
            {"type": "sqlite", "path": ":memory:"},
            {"type": "embedding", "path": ":memory:", "url": self.server.url, "model": "fake-model"},
        ]})

    def test_the_stamp_goes_to_every_child(self):
        self.seed()
        self.be.stamp_audience("public")
        for child in self.be.children:
            self.assertIs(child.checks_audience, True)
            self.assertEqual(child.record("a")["fields"], {"audience": "public"})

    def test_a_hybrid_checks_when_a_child_does(self):
        self.assertIs(self.be.checks_audience, False)
        self.be.children[0].stamp_audience("owner")
        self.assertIs(self.be.checks_audience, True)


# ---------------------------------------------------------------- the check at recall

class Levels(StageTestCase):
    """The check on its own: candidates with a record level, a client audience, a store."""

    # (record level, client audience) -> kept? Written by hand from the rule: a record is shown to a client
    # when it is at least as wide as the client's audience (owner < group < public); none counts as owner.
    TABLE = {
        ("owner", "owner"): True, ("owner", "group"): False, ("owner", "public"): False,
        ("group", "owner"): True, ("group", "group"): True, ("group", "public"): False,
        ("public", "owner"): True, ("public", "group"): True, ("public", "public"): True,
        (None, "owner"): True, (None, "group"): False, (None, "public"): False,
    }

    def checking(self):
        store = InMemoryBackend()
        store.stamp_audience("owner")
        return store

    def verdict(self, store, level, client):
        fields = {} if level is None else {"audience": level}
        stage = stages.RecordAudience()
        return stage.admit(self.cand(store=store, fields=fields), self.ctx(audience=client))

    def test_the_table(self):
        store = self.checking()
        for (level, client), kept in self.TABLE.items():
            with self.subTest(record=level, client=client):
                self.assertEqual(self.verdict(store, level, client), "keep" if kept else "drop")

    def test_the_table_is_complete(self):
        self.assertEqual(len(self.TABLE), 4 * 3)

    def test_a_store_that_does_not_check_keeps_everything(self):
        unstamped = InMemoryBackend()
        for store in (unstamped, NoFields(), FakeStore(), object(), None):
            for level in ("owner", "group", "public", None):
                with self.subTest(store=type(store).__name__, level=level):
                    self.assertEqual(self.verdict(store, level, "public"), "keep")

    def test_a_level_nobody_knows_counts_as_owner(self):
        store = self.checking()
        self.assertEqual(self.verdict(store, "friends", "public"), "drop")
        self.assertEqual(self.verdict(store, "friends", "owner"), "keep")

    def test_the_stage_is_cheap_and_enforces_the_check(self):
        stage = registry.create("recall_stage", "record_audience")
        self.assertIs(stage.cheap, True)
        self.assertEqual(stage.enforces, ("record_audience",))
        self.assertEqual(stage.name, "record_audience")

    def test_it_stands_right_after_linked_in_the_default_stages(self):
        names = list(stages.DEFAULT_STAGES)
        self.assertEqual(names[names.index("linked") + 1], "record_audience")
        self.assertEqual(names[names.index("record_audience") + 1], "fill_text")
        self.assertEqual(len(names), 10)

    def test_an_explained_recall_names_the_stage_that_dropped_a_record(self):
        store = self.checking()
        store.put("private tea note", ref="p", fields={"audience": "owner"})
        store.put("public tea note", ref="q", fields={"audience": "public"})
        ctx = self.ctx(query="tea note", audience="public", trace=True)
        out = recall({"a": store}, [registry.create("recall_stage", "record_audience")], ctx)
        rows = {row["id"]: row for row in out["candidates"]}
        self.assertEqual((rows["p"]["outcome"], rows["p"]["stage"]), ("dropped", "record_audience"))
        self.assertEqual((rows["q"]["outcome"], rows["q"]["stage"]), ("delivered", None))
        self.assertEqual(out["stages"], ["record_audience"])
        self.assertEqual([r["id"] for r in out["results"]], ["q"])


class LinkedStore(InMemoryBackend):
    """A store whose `neighbours` says every other memory is a close neighbour."""

    def neighbours(self, ref, limit, timeout=None, ctl=None):
        return [Hit(r["ref"], 0.9, r["text"], r["date"], fields=dict(r["fields"])) for r in self.records()
                if r["ref"] != ref][:limit]


class RecallThroughTheGatewayTests(Case):
    def test_the_hand_written_matrix_end_to_end(self):
        # records put straight into a stamped "public" store, one per level, and one with none
        store = InMemoryBackend()
        texts = {"owner": "owner level tea memory", "group": "group level coffee memory",
                 "public": "public level cocoa memory", None: "unmarked level juice memory"}
        gw = self.gateway({"s": store}, {
            "owner": ClientPolicy("owner", recall=("s",)),
            "group": ClientPolicy("group", recall=("s",), public=True, audience="group"),
            "public": ClientPolicy("public", recall=("s",), public=True)}, audiences={"s": "public"})
        for level, text in texts.items():
            store.put(text, ref=str(level), fields=None if level is None else {"audience": level})
        everything = list(texts.values())
        visible = {  # client -> the texts it may see, by hand
            "owner": everything,
            "group": [texts["group"], texts["public"]],
            "public": [texts["public"]],
        }
        for client, expected in visible.items():
            seen = []
            for text in everything:
                seen += self.found(gw, client, text)
            with self.subTest(client=client):
                self.assertEqual(sorted(set(seen)), sorted(expected))

    def test_a_memory_added_by_linked_is_checked_too(self):
        store = LinkedStore()
        public_note, private_note = "public lantern harbor note", "private violet ledger entry"
        gw = self.gateway({"s": store}, {
            "owner": ClientPolicy("owner", recall=("s",)),
            "public": ClientPolicy("public", recall=("s",), public=True)}, audiences={"s": "public"},
            stages=[registry.create("recall_stage", {"type": "linked", "minimum": 0.0}),
                    registry.create("recall_stage", "record_audience")])
        store.put(public_note, ref="a", fields={"audience": "public"})
        store.put(private_note, ref="b", fields={"audience": "owner"})
        # the query finds only the public note; "b" can come in only as its neighbour
        self.assertEqual(self.found(gw, "owner", "lantern harbor"), [public_note, private_note])
        self.assertEqual(self.found(gw, "public", "lantern harbor"), [public_note])

    def test_every_client_sees_what_it_saw_before_when_the_records_were_written_through_the_gateway(self):
        store = InMemoryBackend()
        gw = self.gateway({"s": store}, {
            "w": ClientPolicy("w", write="s"),
            "owner": ClientPolicy("owner", recall=("s",)),
            "public": ClientPolicy("public", recall=("s",), public=True)}, audiences={"s": "public"})
        self.assertEqual(self.say(gw, "w", {}).status, 200)
        self.assertEqual(self.found(gw, "owner", SECRET), [SECRET])
        self.assertEqual(self.found(gw, "public", SECRET), [SECRET])


# ---------------------------------------------------------------- an old file, opened by a gateway

OLD_ROWS = (("old-1", "I like green tea a lot", "2025-01-02", 1000),
            ("old-2", "The deploy happens on Friday", None, 1001),
            ("old-3", "我最喜欢的饮料是乌龙茶", "2025-03-04", 1002))


class OldFileTests(Case):
    def old_file(self):
        path = self.tmp() / "old.db"
        db = sqlite3.connect(str(path))
        db.executescript(OLD_SQLITE)
        for ref, text, date, created in OLD_ROWS:
            db.execute("INSERT INTO memories (ref, text, created_at, date) VALUES (?, ?, ?, ?)",
                       (ref, text, created, date))
            db.executemany("INSERT OR IGNORE INTO grams (gram, ref) VALUES (?, ?)",
                           [(g, ref) for g in index_grams(normalize(text), True)])
        db.execute("INSERT OR REPLACE INTO info (key, value) VALUES ('gram_scheme', '2')")
        db.commit()
        db.close()
        return path

    def cfg(self, path, **store):
        return {"stores": {"s": dict({"type": "sqlite", "path": path.as_posix(), "audience": "public"}, **store)},
                "clients": {"owner": {"recall": ["s"]}, "public": {"public": True, "recall": ["s"]}}}

    def test_the_rows_get_the_declared_level_and_every_client_sees_what_it_saw(self):
        path = self.old_file()
        gw = self.build(self.cfg(path))
        self.assertEqual([r["fields"] for r in gw.stores["s"].records()], [{"audience": "public"}] * 3)
        for client in ("owner", "public"):
            for _, text, _, _ in OLD_ROWS:
                with self.subTest(client=client, text=text):
                    self.assertEqual(self.found(gw, client, text), [text])

    def test_a_store_declared_owner_stamps_owner_and_stays_closed_to_the_public(self):
        path = self.old_file()
        gw = self.build({"stores": {"s": {"type": "sqlite", "path": path.as_posix()}},
                         "clients": {"owner": {"recall": ["s"]}}})
        self.assertEqual({r["fields"]["audience"] for r in gw.stores["s"].records()}, {"owner"})

    def test_a_read_only_old_store_is_left_alone_and_only_the_store_level_rule_applies(self):
        path = self.old_file()
        gw = self.build(self.cfg(path, writable=False))
        self.assertNotIn("audience", columns(str(path)))
        self.assertIs(getattr(gw.stores["s"], "checks_audience", False), False)
        self.assertEqual(self.found(gw, "public", OLD_ROWS[0][1]), [OLD_ROWS[0][1]])

    def test_a_second_gateway_does_not_stamp_again(self):
        path = self.old_file()
        gw = self.build(self.cfg(path))
        gw.close()
        store = SQLiteBackend(str(path))
        store.put("added later", ref="late")  # no audience
        store.close()
        gw2 = self.build(dict(self.cfg(path), clients={"owner": {"recall": ["s"]}}))
        self.assertEqual(gw2.stores["s"].record("late")["fields"], {})


# ---------------------------------------------------------------- leak scenarios, real files

class LeakTests(Case):
    def owner_gateway(self, path):
        return self.build({"stores": {"private": {"type": "sqlite", "path": path.as_posix()}},
                           "clients": {"owner": {"recall": ["private"], "write": "private"}}})

    def test_a_copy_of_the_owners_file_configured_as_a_public_store(self):
        folder = self.tmp()
        gw = self.owner_gateway(folder / "private.db")
        self.assertEqual(self.say(gw, "owner", {}).status, 200)
        gw.close()
        shutil.copyfile(folder / "private.db", folder / "copy.db")
        gw2 = self.build({"stores": {"copy": {"type": "sqlite", "path": (folder / "copy.db").as_posix(),
                                              "audience": "public"}},
                          "clients": {"owner": {"recall": ["copy"]},
                                      "public": {"public": True, "recall": ["copy"]}}})
        self.assertEqual(self.found(gw2, "public", SECRET), [])
        self.assertEqual(self.found(gw2, "owner", SECRET), [SECRET])

    def test_two_names_for_one_file_one_owner_and_one_public(self):
        folder = self.tmp()
        path = (folder / "one.db").as_posix()
        gw = self.build({"stores": {"a": {"type": "sqlite", "path": path},
                                    "b": {"type": "sqlite", "path": path, "audience": "public"}},
                         "clients": {"w": {"write": "a"},
                                     "owner": {"recall": ["b"]},
                                     "public": {"public": True, "recall": ["b"]}}})
        self.assertEqual(self.say(gw, "w", {}).status, 200)
        self.assertEqual(self.found(gw, "public", SECRET), [])
        self.assertEqual(self.found(gw, "owner", SECRET), [SECRET])

    def test_a_record_put_without_an_audience_into_a_stamped_public_store_until_restamp(self):
        folder = self.tmp()
        path = (folder / "pub.db").as_posix()
        cfg = {"stores": {"pub": {"type": "sqlite", "path": path, "audience": "public"}},
               "clients": {"owner": {"recall": ["pub"]}, "public": {"public": True, "recall": ["pub"]}}}
        file = folder / "gateway.toml"
        file.write_text('[stores.pub]\ntype = "sqlite"\npath = "%s"\naudience = "public"\n\n'
                        '[clients.owner]\nrecall = ["pub"]\n\n'
                        '[clients.public]\npublic = true\nrecall = ["pub"]\n' % path, encoding="utf-8")
        gw = self.build(cfg)  # stamps the (empty) store
        gw.stores["pub"].put(SECRET, ref="direct")  # a direct put without fields
        self.assertEqual(self.found(gw, "public", SECRET), [])
        self.assertEqual(self.found(gw, "owner", SECRET), [SECRET])
        gw.close()
        code, out = run("restamp", "--config", str(file), "--store", "pub", "--missing")
        self.assertEqual(code, 0, out)
        self.assertIn("1 条", out)
        gw2 = self.build(cfg)
        self.assertEqual(self.found(gw2, "public", SECRET), [SECRET])


# ---------------------------------------------------------------- configuration

TOKENLESS = {"recall": ["s"]}


class ConfigTests(Case):
    def refused(self, cfg, *needles):
        with self.assertRaises(ConfigError) as caught:
            config.build_gateway(cfg, env={}, log=lambda line: None)
        for needle in needles:
            self.assertIn(needle, str(caught.exception))

    @staticmethod
    def cfg(client=None, stages_list=None, **extra):
        cfg = {"stores": {"s": {"type": "inmemory", "audience": "public"}},
               "clients": {"c": dict({"public": True, "recall": ["s"]}, **(client or {}))}}
        if stages_list is not None:
            cfg["recall"] = {"stages": stages_list}
        cfg.update(extra)
        return cfg

    def test_a_non_owner_client_needs_a_stage_that_checks_the_record(self):
        message = ('clients.c shows what it recalls to other people, but no recall stage checks each record\'s '
                   'audience: add "record_audience" to [recall] stages')
        for client in ({}, {"audience": "group"}, {"audience": "public"}):
            with self.subTest(client=client):
                with self.assertRaises(ConfigError) as caught:
                    config.build_gateway(self.cfg(client, ["min_score", "redact"]), env={}, log=lambda line: None)
                self.assertEqual(str(caught.exception), message)
                self.build(self.cfg(client, ["min_score", "redact", "record_audience"]))
                self.build(self.cfg(client))  # the default stages have it

    def test_the_other_checks_keep_their_place_and_words(self):
        self.refused(self.cfg(stages_list=[]), "clients.c is public and may recall, but no recall stage enforces "
                                              "redaction")

    def test_an_owner_client_or_one_that_cannot_recall_needs_no_such_stage(self):
        self.build(self.cfg({"public": False}, ["min_score"]))
        self.build(self.cfg({"recall": None, "write": "s"}, ["min_score"], redaction={"public": "public"}))

    def test_origins_and_fields_are_checked(self):
        self.assertEqual(config.client_policy("c", {}).origins, ("said", "tool"))
        self.assertEqual(config.client_policy("c", {}).fields, ())
        policy = config.client_policy("c", {"origins": ["said", "curated"], "fields": ["channel", "kind"]})
        self.assertEqual((policy.origins, policy.fields), (("said", "curated"), ("channel", "kind")))
        for key, bad in (("origins", ["said", "told"]), ("origins", "said"), ("origins", [1]),
                         ("fields", ["channel", "mood"]), ("fields", "channel"), ("fields", [None])):
            with self.subTest(key=key, bad=bad):
                with self.assertRaises(ConfigError) as caught:
                    config.client_policy("c", {key: bad})
                self.assertIn("clients.c.%s must be a list" % key, str(caught.exception))
        self.assertIn("origins", config._POLICY_KEYS)
        self.assertIn("fields", config._POLICY_KEYS)

    def test_origins_may_be_empty(self):
        self.assertEqual(config.client_policy("c", {"origins": []}).origins, ())

    def test_only_an_owner_level_client_may_list_who_a_memory_is_about(self):
        message = ('clients.c.fields may not include "speaker" or "about": only an owner-level client sees who '
                   'a memory is about')
        for name in ("speaker", "about"):
            for client in ({}, {"audience": "group"}, {"audience": "public"}):
                with self.subTest(name=name, client=client):
                    with self.assertRaises(ConfigError) as caught:
                        config.build_gateway(self.cfg(dict(client, fields=["channel", name])), env={},
                                             log=lambda line: None)
                    self.assertEqual(str(caught.exception), message)
            self.build(self.cfg({"public": False, "fields": [name]}))  # an owner-level client may
        with self.assertRaises(ConfigError):  # built in code the rule holds too
            self.gateway({"s": InMemoryBackend()}, {"c": ClientPolicy("c", recall=("s",), public=True,
                                                                      fields=("about",))}, audiences={"s": "public"})
        with self.assertRaises(ConfigError):
            self.gateway({}, {"c": ClientPolicy("c", origins=("said", "told"))})

    def test_a_forgetting_service_is_told_the_declared_levels(self):
        folder = self.tmp()
        cfg = self.cfg(forgetting={"ledger": (folder / "l.db").as_posix(), "archive": (folder / "a.db").as_posix(),
                                   "stores": ["s"]}, tenants={"g": {"type": "rf_tenants"}})
        gw = self.build(cfg)
        self.assertEqual(gw.forgetting.audiences, {"s": "public", "g": "group"})
        self.assertEqual((gw.forgetting.audience_of("s"), gw.forgetting.audience_of("g:10001")),
                         ("public", "group"))


registry.register("tenants", "rf_tenants")(MemTenants)


# ---------------------------------------------------------------- fields in the answers

class AnswerTests(Case):
    def setUp(self):
        self.cfg = {"stores": {"s": {"type": "inmemory", "audience": "public"}},
                    "clients": {"w": {"write": "s", "recall": ["s"], "explain": True},
                                "plain": {"public": True, "recall": ["s"]},
                                "rich": {"public": True, "recall": ["s"], "fields": ["channel", "kind", "audience"]},
                                "none": {"public": True, "recall": ["s"], "fields": ["origin"]},
                                "owner": {"recall": ["s"], "fields": ["speaker", "channel"]}}}
        self.gw = self.build(self.cfg)

    def items(self, client, query):
        reply = self.gw.recall(client, {"query": query})
        self.assertEqual(reply.status, 200)
        return reply.body["results"]

    def test_items_carry_the_listed_fields_the_memory_has(self):
        self.say(self.gw, "w", {"channel": "kitchen", "kind": "event", "origin": "tool", "audience": "group"})
        self.assertEqual(self.items("w", SECRET)[0].get("fields"), None)  # w lists none
        self.gw.stores["s"].put(OTHER, ref="o", fields={"audience": "public", "channel": "hall"})
        self.gw.stores["s"].put(THIRD, ref="t", fields={"audience": "public", "speaker": "mia"})
        self.assertEqual(self.items("rich", OTHER)[0]["fields"], {"channel": "hall", "audience": "public"})
        self.assertEqual(self.items("rich", THIRD)[0]["fields"], {"audience": "public"})

    def test_a_client_without_a_fields_list_gets_items_exactly_as_before(self):
        self.say(self.gw, "w", {"channel": "kitchen", "kind": "event", "audience": "public", "time": "2026-03-01"})
        item = self.items("plain", SECRET)[0]
        self.assertEqual(set(item), {"id", "source", "text", "score", "date"})

    def test_the_key_is_left_out_when_the_memory_has_none_of_the_listed_fields(self):
        self.say(self.gw, "w", {"audience": "public"})
        self.assertEqual(set(self.items("none", SECRET)[0]), {"id", "source", "text", "score"})

    def test_an_owner_level_client_may_list_speaker(self):
        self.gw.stores["s"].put(SECRET, ref="o", fields={"audience": "public", "speaker": "mia", "channel": "hall"})
        self.assertEqual(self.items("owner", SECRET)[0]["fields"], {"speaker": "mia", "channel": "hall"})

    def test_a_public_answer_cleans_the_values_too(self):
        self.gw.stores["s"].put(SECRET, ref="p", fields={"audience": "public", "channel": "mia@example.com"})
        self.assertEqual(self.items("rich", SECRET)[0]["fields"], {"channel": MARK, "audience": "public"})

    def test_explain_rows_carry_all_the_fields_the_record_has(self):
        self.say(self.gw, "w", {"channel": "kitchen", "kind": "event", "event_date": "2026-03-01", "origin": "tool"})
        row = self.gw.explain("w", {"query": SECRET}).body["candidates"][0]
        self.assertEqual(row["fields"], {"audience": "public", "channel": "kitchen", "kind": "event",
                                         "event_date": "2026-03-01", "origin": "tool"})



class ExplainRowTests(StageTestCase):
    def test_a_row_without_fields_has_no_such_key(self):
        out = recall({"a": FakeStore([Hit("m1", 0.9, "likes tea")])}, [], self.ctx(trace=True))
        self.assertEqual(out["candidates"][0],
                         {"source": "a", "id": "m1", "found": 0.9, "score": 0.9, "outcome": "delivered",
                          "stage": None, "added_by": None, "text": "likes tea"})

    def test_a_row_with_fields_has_them_all(self):
        fields = {"audience": "public", "speaker": "mia"}
        out = recall({"a": FakeStore([Hit("m1", 0.9, "likes tea", fields=fields)])}, [], self.ctx(trace=True))
        self.assertEqual(out["candidates"][0]["fields"], fields)


# ---------------------------------------------------------------- check

class DescribeTests(Case):
    def lines(self, cfg):
        return cli.describe(self.build(cfg))

    def test_a_client_line_names_the_fields_it_gets(self):
        cfg = {"stores": {"s": {"type": "inmemory", "audience": "public"}},
               "clients": {"rich": {"public": True, "recall": ["s"], "fields": ["channel", "kind"]},
                           "plain": {"public": True, "recall": ["s"]}}}
        lines = self.lines(cfg)
        rich = next(line for line in lines if line.startswith("  rich:"))
        plain = next(line for line in lines if line.startswith("  plain:"))
        self.assertIn("召回结果带的字段 channel, kind", rich)
        self.assertNotIn("字段", plain)

    def test_a_reminder_for_a_store_that_keeps_no_fields(self):
        cfg = {"stores": {"old": {"type": "rf_nofields", "audience": "public"},
                          "new": {"type": "inmemory", "audience": "public"}},
               "clients": {"public": {"public": True, "recall": ["old", "new"]},
                           "owner": {"recall": ["old"]},
                           "fine": {"public": True, "recall": ["new"]}}}
        reminders = [line for line in self.lines(cfg) if "提醒：" in line]
        self.assertEqual(len(reminders), 1)
        self.assertIn("public 的内容会被别人看到", reminders[0])
        self.assertIn("old", reminders[0])
        self.assertNotIn("new", reminders[0].split("只有")[0].split("召回的存储")[1])

    def test_no_reminder_when_every_store_checks(self):
        cfg = {"stores": {"s": {"type": "inmemory", "audience": "public"}},
               "clients": {"public": {"public": True, "recall": ["s"]}}}
        self.assertEqual([line for line in self.lines(cfg) if "提醒：" in line], [])


# ---------------------------------------------------------------- restamp

def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = cli.main(list(argv))
        except SystemExit as exit_:
            code = exit_.code
    return code, out.getvalue()


class RestampTests(Case):
    def setUp(self):
        self.dir = self.tmp()
        self.db = (self.dir / "s.db").as_posix()

    def write(self, audience="public", extra=""):
        path = self.dir / "gateway.toml"
        path.write_text('[stores.s]\ntype = "sqlite"\npath = "%s"\naudience = "%s"\n\n'
                        '[clients.owner]\nrecall = ["s"]\n%s' % (self.db, audience, extra), encoding="utf-8")
        return str(path)

    def levels(self, path=None):
        store = SQLiteBackend(path or self.db)
        try:
            return {r["ref"]: r["fields"].get("audience") for r in store.records()}
        finally:
            store.close()

    def put(self, **records):
        store = SQLiteBackend(self.db)
        try:
            for ref, fields in records.items():
                store.put("text of " + ref, ref=ref, fields=fields)
        finally:
            store.close()

    def test_missing_gives_the_declared_level_to_the_records_that_have_none(self):
        file = self.write("public")
        self.assertEqual(run("check", "--config", file)[0], 0)  # the gateway stamps the empty store
        self.put(a=None, b={"audience": "group"}, c={"channel": "x"})
        self.assertEqual(self.levels(), {"a": None, "b": "group", "c": None})
        code, out = run("restamp", "--config", file, "--store", "s", "--missing")
        self.assertEqual(code, 0, out)
        self.assertIn("2 条", out)
        self.assertEqual(self.levels(), {"a": "public", "b": "group", "c": "public"})
        self.assertIn("0 条", run("restamp", "--config", file, "--store", "s", "--missing")[1])

    def test_all_gives_it_to_every_record(self):
        owner_file = self.write("owner")
        self.assertEqual(run("check", "--config", owner_file)[0], 0)
        self.put(a=None, b={"audience": "group"}, c={"audience": "public"})
        self.assertEqual(self.levels(), {"a": None, "b": "group", "c": "public"})
        code, out = run("restamp", "--config", owner_file, "--store", "s", "--all")
        self.assertEqual((code, "3 条" in out), (0, True), out)
        self.assertEqual(self.levels(), {"a": "owner", "b": "owner", "c": "owner"})
        public_file = self.write("public")  # the declaration changed: widen the whole store on purpose
        code, out = run("restamp", "--config", public_file, "--store", "s", "--all")
        self.assertEqual((code, "3 条" in out), (0, True), out)
        self.assertEqual(self.levels(), {"a": "public", "b": "public", "c": "public"})

    def test_the_output_shows_numbers_and_names_not_memory_text(self):
        file = self.write("public")
        run("check", "--config", file)
        self.put(a=None)
        out = run("restamp", "--config", file, "--store", "s", "--all")[1]
        self.assertNotIn("text of", out)

    def test_a_tenants_own_store(self):
        tenants = self.dir / "tenants"
        provider = DirectoryTenants(str(tenants), create=True)
        store = provider.store("10001")
        store.put("tenant text", ref="t1")
        provider.close()
        path = self.dir / "gateway.toml"
        path.write_text('[tenants.g]\ntype = "directory"\npath = "%s"\naudience = "public"\n\n'
                        '[clients.owner]\ntenants = "g"\nrecall = ["@tenant"]\n' % tenants.as_posix(),
                        encoding="utf-8")
        self.assertEqual(self.levels((tenants / "10001.db").as_posix()), {"t1": None})
        code, out = run("restamp", "--config", str(path), "--store", "g", "--tenant", "10001", "--all")
        self.assertEqual(code, 0, out)
        # the gateway handed the store out and stamped it with the provider's level, so --all saw one change
        self.assertEqual(self.levels((tenants / "10001.db").as_posix()), {"t1": "public"})
        self.assertIn("g:10001", out)

    def test_refusals(self):
        file = self.write("public")
        self.assertEqual(run("restamp", "--config", file, "--store", "nope", "--all")[0], 1)
        self.assertEqual(run("restamp", "--config", file, "--store", "s", "--tenant", "10001", "--all")[0], 1)
        for argv in (["--store", "s"], ["--store", "s", "--missing", "--all"], ["--missing"]):
            self.assertEqual(run("restamp", "--config", file, *argv)[0], 2, argv)

    def test_a_store_that_keeps_no_fields_is_refused(self):
        path = self.dir / "gateway.toml"
        path.write_text('[stores.s]\ntype = "rf_nofields"\n\n[clients.owner]\nrecall = ["s"]\n', encoding="utf-8")
        code, out = run("restamp", "--config", str(path), "--store", "s", "--all")
        self.assertEqual(code, 1)
        self.assertIn("不能重新打标记", out)

    def test_a_read_only_store_is_refused(self):
        self.put(a=None)
        path = self.dir / "gateway.toml"
        path.write_text('[stores.s]\ntype = "sqlite"\npath = "%s"\nwritable = false\n\n'
                        '[clients.owner]\nrecall = ["s"]\n' % self.db, encoding="utf-8")
        code, out = run("restamp", "--config", str(path), "--store", "s", "--all")
        self.assertEqual(code, 1)
        self.assertIn("只读", out)
        self.assertEqual(self.levels(), {"a": None})


# ---------------------------------------------------------------- forgetting

class ForgettingAudienceTests(fe.EngineCase):
    def archived(self, ref="old", fields=None):
        self.archive.add("private", ref, "old text", "2024-05-06", 0, 1.0, fe.T0 + 30 * fe.DAY, fields=fields)

    def test_a_row_archived_without_an_audience_is_restored_with_the_stores_level(self):
        self.make_engine(audiences={"private": "group"})
        self.archived()
        store = InMemoryBackend()
        self.assertTrue(self.engine.restore("private", "old", store))
        self.assertEqual(store.record("old")["fields"], {"audience": "group"})

    def test_the_other_archived_fields_are_kept_beside_it(self):
        self.make_engine(audiences={"private": "public"})
        self.archived(fields={"channel": "kitchen", "kind": "event"})
        store = InMemoryBackend()
        self.engine.restore("private", "old", store)
        self.assertEqual(store.record("old")["fields"], {"channel": "kitchen", "kind": "event", "audience": "public"})

    def test_an_archived_audience_is_not_overwritten(self):
        self.make_engine(audiences={"private": "public"})
        self.archived(fields={"audience": "owner"})
        store = InMemoryBackend()
        self.engine.restore("private", "old", store)
        self.assertEqual(store.record("old")["fields"], {"audience": "owner"})

    def test_a_tenants_store_gets_its_providers_level(self):
        self.make_engine(audiences={"private": "owner", "group": "group"}, stores=("private", "group"))
        self.archive.add("group:10001", "old", "old text", None, 0, 1.0, fe.T0 + 30 * fe.DAY)
        store = InMemoryBackend()
        self.engine.restore("group:10001", "old", store)
        self.assertEqual(store.record("old")["fields"], {"audience": "group"})

    def test_without_levels_a_restore_is_as_it_was(self):
        self.archived()
        store = InMemoryBackend()
        self.engine.restore("private", "old", store)
        self.assertEqual(store.record("old")["fields"], {})

    def test_a_store_that_keeps_no_fields_is_restored_as_before(self):
        class Strict(fe.FakeStore):
            def put(self, text, ref=None, date=None):  # a TypeError on any other argument
                return super().put(text, ref, date)

        self.make_engine(audiences={"private": "group"})
        self.archived()
        store = Strict({})
        self.assertTrue(self.engine.restore("private", "old", store))
        self.assertEqual(store.items["old"], ("old text", "2024-05-06"))


if __name__ == "__main__":
    unittest.main()
