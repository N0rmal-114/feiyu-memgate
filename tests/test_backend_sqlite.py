import os
import random
import sqlite3
import tempfile
import threading
import time
import unicodedata
import unittest
from unittest import mock

from feiyu_memgate import config, registry
from feiyu_memgate.backends.sqlite import SQLiteBackend
from feiyu_memgate.core.tidy import PreparedQuery, bigram_similarity, bigrams, gram_weights, index_grams, normalize
from feiyu_memgate.errors import BackendError


class SQLiteBackendTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "sub", "m.db")
        self.be = self.open()

    def open(self, **options):
        be = SQLiteBackend(self.path, **options)
        self.addCleanup(be.close)  # runs before the directory is removed (LIFO)
        return be

    def test_put_read_count_delete(self):
        ref = self.be.put("I like green tea", date="2025-01-02")
        self.assertEqual(self.be.count(), 1)
        self.assertEqual(self.be.read(ref), "I like green tea")
        self.assertEqual(list(self.be.items()), [(ref, "I like green tea", "2025-01-02")])
        self.assertTrue(self.be.delete(ref))
        self.assertFalse(self.be.delete(ref))
        self.assertEqual(self.be.count(), 0)
        self.assertEqual(self.be.find("green tea", 5), [])
        with self.assertRaises(KeyError):
            self.be.read(ref)

    def test_parent_directories_created(self):
        self.assertTrue(os.path.isfile(self.path))

    def test_english_and_chinese_queries(self):
        self.be.put("My favourite drink is oolong tea", ref="en")
        self.be.put("我最喜欢的饮料是乌龙茶", ref="zh", date="2025-03-04")
        self.be.put("The deploy happens on Friday", ref="other")
        en = self.be.find("favourite drink", 5)
        self.assertEqual(en[0].ref, "en")
        zh = self.be.find("喜欢的饮料", 5)
        self.assertEqual([h.ref for h in zh], ["zh"])
        self.assertEqual(zh[0].date, "2025-03-04")
        self.assertEqual(zh[0].text, "我最喜欢的饮料是乌龙茶")

    def test_ranking_and_score_range(self):
        self.be.put("alpha beta gamma", ref="full")
        self.be.put("alpha only here", ref="part")
        self.be.put("nothing related xyzzy", ref="none")
        hits = self.be.find("alpha beta gamma", 10)
        self.assertEqual(hits[0].ref, "full")
        self.assertNotIn("none", [h.ref for h in hits])
        self.assertTrue(all(0 < h.score <= 1 for h in hits))
        self.assertEqual([h.score for h in hits], sorted((h.score for h in hits), reverse=True))
        self.assertAlmostEqual(hits[0].score, 1.0)

    def test_limit(self):
        for i in range(10):
            self.be.put("common phrase number %d" % i)
        self.assertEqual(len(self.be.find("common phrase", 3)), 3)
        self.assertEqual(len(self.be.find("common phrase", 100)), 10)

    def test_short_query_falls_back_to_substring(self):
        self.be.put("茶是好东西", ref="a")
        self.be.put("coffee only", ref="b")
        self.assertEqual([h.ref for h in self.be.find("茶", 5)], ["a"])
        self.assertEqual([h.ref for h in self.be.find("c", 5)], ["b"])
        self.assertEqual(self.be.find("?", 5), [])  # normalises to nothing

    def test_replace_by_ref_updates_index(self):
        self.be.put("old wording about bananas", ref="r")
        self.be.put("new wording about cherries", ref="r")
        self.assertEqual(self.be.count(), 1)
        self.assertEqual(self.be.find("bananas", 5), [])
        self.assertEqual([h.ref for h in self.be.find("cherries", 5)], ["r"])

    def test_persistence_across_reopen(self):
        self.be.put("remember this sentence", ref="p")
        self.be.close()
        again = self.open()
        self.assertEqual(again.read("p"), "remember this sentence")
        self.assertEqual([h.ref for h in again.find("remember this", 3)], ["p"])

    def test_read_only_refuses_remember(self):
        self.be.put("kept", ref="k")
        self.be.close()
        ro = self.open(writable=False)
        with self.assertRaises(PermissionError):
            ro.remember([{"role": "user", "content": "x"}])
        self.assertEqual(ro.count(), 1)

    def test_roles(self):
        msgs = [{"role": "user", "content": "I live in Lisbon"}, {"role": "assistant", "content": "Nice city"}]
        receipt = self.be.remember(msgs)
        self.assertIsInstance(receipt, str)
        self.assertEqual([t for _, t, _ in self.be.items()], ["I live in Lisbon"])
        both = SQLiteBackend(":memory:", roles=["user", "assistant"])
        self.addCleanup(both.close)
        both.remember(msgs)
        self.assertEqual(both.count(), 2)
        date = next(iter(both.items()))[2]
        self.assertRegex(date, r"^\d{4}-\d{2}-\d{2}$")

    def test_health_and_close(self):
        self.assertTrue(self.be.health())
        self.be.close()
        self.assertIs(self.be.health(), False)
        self.be.close()  # twice is fine
        self.assertFalse(self.be.tracks_writes)

    def test_max_candidates_bounds_scoring(self):
        be = SQLiteBackend(":memory:", max_candidates=3)
        self.addCleanup(be.close)
        for i in range(10):
            be.put("shared words %d" % i)
        self.assertLessEqual(len(be.find("shared words", 50)), 3)

    def test_zero_timeout_returns_at_once(self):
        self.be.put("some text here")
        started = time.monotonic()
        try:
            self.assertIsInstance(self.be.find("some text", 5, timeout=0), list)
        except BackendError:
            pass  # nothing could be scored in no time at all: documented
        self.assertLess(time.monotonic() - started, 1.0)

    def test_threads(self):
        errors = []

        def work(n):
            try:
                for i in range(25):
                    self.be.put("thread %d note %d about topic%d" % (n, i, n))
                    self.be.find("note about topic%d" % n, 5)
            except Exception as err:  # noqa: BLE001
                errors.append(err)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.be.count(), 150)

    def test_registry(self):
        be = registry.create("backend", {"type": "sqlite", "path": os.path.join(self._tmp.name, "r.db")})
        self.addCleanup(be.close)
        self.assertIsInstance(be, SQLiteBackend)
        self.assertIn("sqlite", registry.available("backend"))

    def test_end_to_end_through_gateway(self):
        cfg = {
            "stores": {"mine": {"type": "sqlite", "path": os.path.join(self._tmp.name, "gw.db")}},
            "clients": {"owner": {"token_env": "T", "recall": ["mine"], "write": "mine"}},
            "recall": {"stages": [{"type": "min_score", "value": 0.4}, "dedupe", "budget"]},
        }
        gw = config.build_gateway(cfg, env={}, log=lambda line: None)
        self.addCleanup(lambda: [s.close() for s in gw.stores.values()])
        reply = gw.remember("owner", {"messages": [{"role": "user", "content": "My cat is called Mochi"},
                                                   {"role": "assistant", "content": "Cute"}]})
        self.assertEqual(reply.status, 200, reply.body)
        found = gw.recall("owner", {"query": "what is my cat called"})
        self.assertEqual(found.status, 200, found.body)
        texts = [item["text"] for item in found.body["results"]]
        self.assertEqual(texts, ["My cat is called Mochi"])
        miss = gw.recall("owner", {"query": "quantum chromodynamics"})
        self.assertEqual(miss.body["results"], [])


    def test_refs_streams_every_memory_in_batches(self):
        from feiyu_memgate.backends import sqlite as sqlite_mod

        with mock.patch.object(sqlite_mod, "SCAN_BATCH", 3):
            for i in range(8):
                self.be.put("memory number %d" % i, ref="r%d" % i, date="2025-01-0%d" % (i + 1) if i < 5 else None)
            got = list(self.be.refs())
            self.assertEqual([r for r, _d in got], ["r%d" % i for i in range(8)])
            self.assertEqual(got[0], ("r0", "2025-01-01"))
            self.assertEqual(got[7], ("r7", None))
            for ref, _date in self.be.refs():  # deleting while iterating loses nothing
                self.be.delete(ref)
            self.assertEqual(self.be.count(), 0)
        self.assertEqual(list(self.be.refs()), [])

    def test_receipt_refs_match_what_remember_stored(self):
        msgs = [{"role": "user", "content": "first fact"}, {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "second fact"}]
        r1 = self.be.remember(msgs)
        r2 = self.be.remember([{"role": "user", "content": "other receipt"}])
        self.assertEqual(self.be.receipt_refs(r1), [r1 + "-0", r1 + "-2"])
        self.assertEqual(self.be.receipt_refs(r2), [r2 + "-0"])
        self.assertEqual(self.be.receipt_refs("w-nothing"), [])
        self.assertIsNone(self.be.receipt_refs(None))
        self.be.delete(r1 + "-0")
        self.assertEqual(self.be.receipt_refs(r1), [r1 + "-2"])

    def test_read_only_refuses_put_and_delete(self):
        self.be.put("kept", ref="k")
        self.be.close()
        ro = self.open(writable=False)
        with self.assertRaises(PermissionError):
            ro.delete("k")
        with self.assertRaises(PermissionError):
            ro.put("y", ref="y")
        self.assertEqual(list(ro.refs()), [("k", None)])


HEAVY_SQL = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 400000000) "
             "SELECT COUNT(*) FROM c")  # runs for many seconds unless it is interrupted


class _Ctl:
    """A cancellation handle with the pool Task's new attributes."""

    def __init__(self):
        self.cancelled = False
        self.callbacks = []

    def on_cancel(self, fn):
        self.callbacks.append(fn)

    def cancel(self):
        self.cancelled = True
        for fn in self.callbacks:
            fn()


def _heavy(db, prepared, run):
    db.execute(HEAVY_SQL).fetchone()
    return []


class SQLiteDeadlineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "m.db")
        self.be = SQLiteBackend(self.path)
        self.addCleanup(self.be.close)
        for i in range(300):
            self.be.put("common words about tea number %d" % i, ref="r%d" % i)

    def test_sql_is_stopped_at_the_deadline(self):
        started = time.monotonic()
        with mock.patch.object(SQLiteBackend, "_candidates", side_effect=lambda *a: _heavy(*a)):
            with self.assertRaises(BackendError):
                self.be.find("common words about tea", 5, timeout=0.2)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_sql_is_stopped_when_the_call_is_cancelled(self):
        ctl = _Ctl()
        timer = threading.Timer(0.1, ctl.cancel)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        with mock.patch.object(SQLiteBackend, "_candidates", side_effect=lambda *a: _heavy(*a)):
            with self.assertRaises(BackendError):
                self.be.find("common words about tea", 5, timeout=30, ctl=ctl)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_already_cancelled_call_does_nothing(self):
        ctl = _Ctl()
        ctl.cancel()
        with self.assertRaises(BackendError):
            self.be.find("common words", 5, ctl=ctl)

    def test_cancel_during_scoring_raises(self):
        ctl = _Ctl()
        real = PreparedQuery.score
        calls = []

        def score(prepared, text):
            calls.append(1)
            if len(calls) == 3:
                ctl.cancel()
            return real(prepared, text)

        with mock.patch.object(PreparedQuery, "score", score):
            with self.assertRaises(BackendError):
                self.be.find("common words about tea", 5, ctl=ctl)
        self.assertLess(len(calls), 20)  # it stopped scoring

    def test_out_of_time_while_scoring_returns_the_best_so_far(self):
        real = PreparedQuery.score

        def slow(prepared, text):
            time.sleep(0.02)
            return real(prepared, text)

        started = time.monotonic()
        with mock.patch.object(PreparedQuery, "score", slow):
            hits = self.be.find("common words about tea", 5, timeout=0.3)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(0 < len(hits) <= 5)  # some were scored; all 300 would have taken 6 s

    def test_ctl_without_the_new_attributes_and_no_ctl_work(self):
        class Bare:
            def attach(self, sock):
                pass

            def detach(self):
                pass

        self.assertTrue(self.be.find("common words", 3, ctl=Bare()))
        self.assertTrue(self.be.find("common words", 3, ctl=None))

    def test_readers_hold_connections_at_the_same_time(self):
        barrier = threading.Barrier(4, timeout=5)
        errors = []

        def reader():
            try:
                with self.be._read():
                    barrier.wait()  # only passes when all four read at once
            except Exception as err:  # noqa: BLE001
                errors.append(err)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_a_write_does_not_wait_for_a_running_search(self):
        entered, release = threading.Event(), threading.Event()

        def slow_candidates(db, prepared, run):
            entered.set()
            release.wait(5)
            return []

        with mock.patch.object(SQLiteBackend, "_candidates", side_effect=lambda *a: slow_candidates(*a)):
            finder = threading.Thread(target=lambda: self.be.find("common words", 3))
            finder.start()
            self.assertTrue(entered.wait(5))
            started = time.monotonic()
            self.be.put("written while a search runs", ref="w")
            self.assertLess(time.monotonic() - started, 1.0)
            release.set()
            finder.join()

    def test_close_releases_every_connection_so_the_file_can_be_deleted(self):
        threads = [threading.Thread(target=lambda: self.be.find("common words", 3)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.be.close()
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.remove(self.path + suffix)  # PermissionError on Windows while a connection is open
        self.assertFalse(os.path.exists(self.path))

    def test_find_after_close_is_an_error_not_a_crash(self):
        self.be.close()
        with self.assertRaises(BackendError):
            self.be.find("common words", 3)
        self.assertFalse(self.be.health())


class SQLiteWorkBoundTest(unittest.TestCase):
    def test_scores_are_the_idf_weighted_containment(self):
        be = SQLiteBackend(":memory:")
        self.addCleanup(be.close)
        texts = ["我最喜欢的饮料是乌龙茶", "Green Tea, with honey!", "tea tea tea", "ＡＢＣ　ｄｅｆ", "a"]
        counts = {}
        for i, text in enumerate(texts):
            be.put(text, ref="r%d" % i)
            for gram in index_grams(normalize(text)):
                counts[gram] = counts.get(gram, 0) + 1
        for query in ("喜欢的饮料", "green tea honey", "tea", "abc def", "x", "a", "乌龙茶 tea"):
            weights = gram_weights(normalize(query), counts.get, len(texts))
            prepared = PreparedQuery(query, weights) if len(normalize(query)) >= 2 else PreparedQuery(query)
            for hit in be.find(query, 10):
                self.assertEqual(hit.score, prepared.score(hit.text), query)

    def test_prepared_query_matches_bigram_similarity(self):
        samples = ["", " ", "a", "ab", "Hello, World", "ＡＢＣ", "乌龙茶，好喝", "tea\ttea", "ǅ ǆ İ", "​x​y"]
        for query in samples:
            prepared = PreparedQuery(query)
            for text in samples:
                self.assertEqual(prepared.score(text), bigram_similarity(query, text), (query, text))

    def test_normalize_keeps_its_old_results(self):
        def reference(text):
            text = unicodedata.normalize("NFKC", text)
            kept = [c for c in text if unicodedata.category(c)[0] not in "PZS" and not c.isspace()]
            return "".join(kept).lower()

        for codepoint in list(range(0, 0x3000)) + list(range(0xFE00, 0x10000)) + [0x1F600, 0x20000]:
            if 0xD800 <= codepoint < 0xE000:
                continue
            text = "a" + chr(codepoint) + "B"
            self.assertEqual(normalize(text), reference(text), hex(codepoint))

    def test_work_does_not_grow_with_query_length(self):
        be = SQLiteBackend(":memory:", max_candidates=10, max_postings=100)
        self.addCleanup(be.close)
        for i in range(400):
            be.put("common words about tea %d" % i, ref="c%d" % i)
        be.put("common words about tea and the zyxqwv", ref="rare")
        query = "common words about tea " * 20 + "zyxqwv"
        scored = []
        real = PreparedQuery.score
        with mock.patch.object(PreparedQuery, "score", lambda p, t: scored.append(1) or real(p, t)):
            hits = be.find(query, 5)
        self.assertLessEqual(len(scored), 10)  # max_candidates, not the whole store
        self.assertEqual(hits[0].ref, "rare")  # found through its rare bigrams

    def test_remember_without_a_matching_role_keeps_nothing_and_returns_none(self):
        be = SQLiteBackend(":memory:")
        self.addCleanup(be.close)
        self.assertIsNone(be.remember([{"role": "assistant", "content": "x"}]))
        self.assertIsNone(be.remember([]))
        self.assertEqual(be.count(), 0)
        self.assertIsNotNone(be.remember([{"role": "user", "content": "x y z"}]))

    def test_defaults(self):
        self.assertEqual(SQLiteBackend.default_min_score, 0.30)
        self.assertEqual(SQLiteBackend.default_relative_ratio, 0.75)


@unittest.skipUnless(os.environ.get("FEIYU_MEMGATE_SLOW_TESTS") == "1", "set FEIYU_MEMGATE_SLOW_TESTS=1")
class SQLiteBenchmarkTest(unittest.TestCase):
    """100k synthetic memories. The limits are generous (a slow machine passes) and each is far below
    what the unbounded code needed (12 chars p95 about 0.15 s, 500 chars about 1.1 s)."""

    SIZE = 100000

    def test_find_latency_at_100k(self):
        rng = random.Random(7)
        letters = "abcdefghijklmnopqrstuvwxyz"
        vocab = ["".join(rng.choice(letters) for _ in range(rng.randint(3, 9))) for _ in range(5000)]
        weights = [1.0 / (i + 1) for i in range(len(vocab))]
        with tempfile.TemporaryDirectory() as tmp:
            be = SQLiteBackend(os.path.join(tmp, "big.db"))
            try:
                with be._db:  # one transaction, bypassing put()'s commit per memory
                    for i in range(self.SIZE):
                        text = " ".join(rng.choices(vocab, weights, k=9))
                        ref = "m%d" % i
                        be._db.execute("INSERT INTO memories (ref, text, created_at, date) VALUES (?, ?, ?, NULL)",
                                       (ref, text, i))
                        be._db.executemany("INSERT OR IGNORE INTO grams (gram, ref) VALUES (?, ?)",
                                           [(g, ref) for g in bigrams(normalize(text))])
                short = self._p95(be, lambda: " ".join(rng.choices(vocab, weights, k=2))[:12])
                long = self._p95(be, lambda: " ".join(rng.choices(vocab, weights, k=90))[:500])
                started = time.perf_counter()
                try:
                    be.find(" ".join(rng.choices(vocab, weights, k=90))[:500], 10, timeout=0.2)
                except BackendError:
                    pass
                limited = time.perf_counter() - started
                print("\n100k memories: p95 12 chars %.3fs, p95 500 chars %.3fs, timeout=0.2 returned in %.3fs"
                      % (short, long, limited))
                self.assertLess(short, 0.15)
                self.assertLess(long, 0.30)
                self.assertLess(limited, 0.3)
            finally:
                be.close()

    @staticmethod
    def _p95(be, make_query, n=30):
        times = []
        for _ in range(n):
            query = make_query()
            started = time.perf_counter()
            be.find(query, 10)
            times.append(time.perf_counter() - started)
        times.sort()
        return times[int(0.95 * (n - 1))]


class AtomicRememberTest(unittest.TestCase):
    """A write of several messages is all or nothing."""

    MESSAGES = [{"role": "user", "content": "memory number %d about tea" % i} for i in range(5)]

    def setUp(self):
        self.be = SQLiteBackend(":memory:")
        self.addCleanup(self.be.close)
        self.be.put("an older memory about tea", ref="old")

    def snapshot(self):
        return list(self.be.refs()), list(self.be.items()), [h.ref for h in self.be.find("tea", 20)]

    @staticmethod
    def failing_third():
        real = index_grams
        calls = []

        def build(*args, **kw):
            calls.append(1)
            if len(calls) == 3:
                raise ValueError("cannot index the third message")
            return real(*args, **kw)

        return build

    def test_a_failure_in_the_middle_stores_nothing(self):
        before = self.snapshot()
        with mock.patch("feiyu_memgate.backends.sqlite.index_grams", self.failing_third()):
            with self.assertRaises(ValueError):
                self.be.remember(self.MESSAGES)
        self.assertEqual(self.snapshot(), before)

    def test_a_failure_while_writing_rolls_the_whole_write_back(self):
        before = self.snapshot()
        real = self.be._db

        class Failing:
            """Passes everything on; the INSERT of the third memory fails."""

            def __init__(self):
                self.inserts = 0

            def __getattr__(self, name):
                return getattr(real, name)

            def __enter__(self):
                return real.__enter__()

            def __exit__(self, *exc):
                return real.__exit__(*exc)

            def execute(self, sql, *args):
                if sql.startswith("INSERT OR REPLACE INTO memories"):
                    self.inserts += 1
                    if self.inserts == 3:
                        raise sqlite3.OperationalError("disk I/O error")
                return real.execute(sql, *args)

        self.be._db = Failing()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.be.remember(self.MESSAGES)
        finally:
            self.be._db = real
        self.assertEqual(self.snapshot(), before)

    def test_a_cancelled_call_stores_nothing(self):
        class Cancelled:
            cancelled = True

        before = self.snapshot()
        with self.assertRaises(BackendError):
            self.be.remember(self.MESSAGES, ctl=Cancelled())
        self.assertEqual(self.snapshot(), before)

    def test_the_cancel_flag_is_read_once_right_before_the_commit(self):
        reads = []

        class Ctl:
            @property
            def cancelled(self):
                reads.append(1)
                return False

        self.assertIsNotNone(self.be.remember(self.MESSAGES, ctl=Ctl()))
        self.assertEqual(len(reads), 1)

    def test_a_normal_call_stores_every_message(self):
        messages = self.MESSAGES[:2] + [{"role": "assistant", "content": "not kept"}] + self.MESSAGES[2:]
        receipt = self.be.remember(messages)
        refs = sorted(self.be.receipt_refs(receipt))
        self.assertEqual(refs, sorted("%s-%d" % (receipt, i) for i in (0, 1, 3, 4, 5)))
        self.assertEqual(self.be.count(), 6)
        self.assertEqual(self.be.read(receipt + "-3"), "memory number 2 about tea")

    def test_nothing_kept_gives_no_receipt(self):
        self.assertIsNone(self.be.remember([{"role": "assistant", "content": "x"}]))
        self.assertEqual(self.be.count(), 1)


if __name__ == "__main__":
    unittest.main()
