import calendar
import sqlite3
import threading
import time
import unittest

from feiyu_memgate.backends.base import MemoryBackend
from feiyu_memgate.core import stages
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.recall import RecallContext, RecallStage, recall
from feiyu_memgate.errors import BackendError
from feiyu_memgate.forgetting.archive import Archive
from feiyu_memgate.forgetting.curve import Ebbinghaus
from feiyu_memgate.forgetting.engine import Forgetting, date_to_epoch
from feiyu_memgate.forgetting.ledger import Entry, Ledger
from feiyu_memgate.forgetting.stage import Freshness
from feiyu_memgate.model import Hit

DAY = 86400.0
T0 = float(calendar.timegm((2026, 6, 1, 0, 0, 0)))


def date_of(epoch):
    return time.strftime("%Y-%m-%d", time.gmtime(epoch))


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, days):
        self.t += days * DAY


class FakeStore(MemoryBackend):
    """A backend with the optional forgetting capabilities, all in a dict."""

    writable = True

    def __init__(self, items=None, tracks=False):
        self.items = dict(items or {})  # ref -> (text, date)
        self.receipts = {}  # receipt -> refs, or None while "not known yet"
        self.tracks_writes = tracks
        self.state = {}  # receipt -> write_state answer
        self.scores = {}
        self.fail_refs = False
        self.fail_delete = False
        self.late = False  # True: receipt_refs says "not known yet" until later[receipt] is moved over
        self.later = {}
        self.deleted = []
        self._count = 0

    def find(self, query, limit, timeout=None, ctl=None):
        hits = [Hit(ref=ref, score=self.scores.get(ref, 0.8), text=text, date=date)
                for ref, (text, date) in self.items.items() if query in text]
        return hits[:limit]

    def read(self, ref, timeout=None, ctl=None):
        return self.items[ref][0]

    def remember(self, messages, timeout=None, ctl=None):
        self._count += 1
        receipt = "w%d" % self._count
        refs = []
        for index, message in enumerate(messages):
            if message["role"] == "user":
                ref = "%s-%d" % (receipt, index)
                self.items[ref] = (message["content"], None)
                refs.append(ref)
        if self.late:
            self.receipts[receipt] = None
            self.later[receipt] = refs
        else:
            self.receipts[receipt] = refs
        return receipt

    def write_state(self, receipt, timeout=None):
        return self.state.get(receipt)

    def refs(self):
        if self.fail_refs:
            raise BackendError("down")
        return [(ref, date) for ref, (text, date) in self.items.items()]

    def delete(self, ref):
        if self.fail_delete:
            raise BackendError("down")
        self.deleted.append(ref)
        return self.items.pop(ref, None) is not None

    def put(self, text, ref=None, date=None):
        ref = ref or "put-%d" % len(self.items)
        self.items[ref] = (text, date)
        return ref

    def receipt_refs(self, receipt):
        return self.receipts.get(receipt)


class PlainStore(MemoryBackend):
    """A backend without the forgetting capabilities."""

    def find(self, query, limit, timeout=None, ctl=None):
        return []


class BrokenLedger(Ledger):
    def __init__(self, fail_get=True, fail_use=False):
        super().__init__(":memory:")
        self.fail_get = fail_get
        self.fail_use = fail_use

    def get_many(self, store, refs):
        if self.fail_get:
            raise sqlite3.OperationalError("disk I/O error")
        return super().get_many(store, refs)

    def use(self, store, items, now):
        if self.fail_use:
            raise sqlite3.OperationalError("disk I/O error")
        super().use(store, items, now)


class FailingArchive(Archive):
    def add(self, *args, **kwargs):
        raise sqlite3.OperationalError("disk full")


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.make_engine()

    def make_engine(self, ledger=None, archive=None, **options):
        self.ledger = ledger or Ledger(":memory:")
        self.archive = archive or Archive(":memory:")
        options.setdefault("stores", ("private", "group"))
        options.setdefault("max_forget_fraction", 1.0)  # the brake has its own tests below
        self.engine = Forgetting(self.ledger, self.archive, Ebbinghaus(half_life_days=60), wall=self.clock, **options)
        self.addCleanup(self.engine.close)

    def entry(self, first_days_ago=0, used_days_ago=None, uses=0, pinned=False, ref="r", managed_days_ago=None):
        """A ledger row. Unless said otherwise the memory has been under management since its own date."""
        last = None if used_days_ago is None else T0 - used_days_ago * DAY
        managed = first_days_ago if managed_days_ago is None else managed_days_ago
        return Entry("private", ref, T0 - first_days_ago * DAY, last, uses, pinned, T0 - managed * DAY)

    def manage(self, key, store, days_ago=None):
        """Put every memory of `store` in the ledger as if it had been managed for `days_ago` days
        (default: since its own date), so the old sweep scenarios still describe a managed store."""
        for ref, date in store.refs():
            epoch = date_to_epoch(date)
            since = T0 - days_ago * DAY if days_ago is not None else (T0 if epoch is None else epoch)
            self.ledger.touch(key, [(ref, epoch)], since)


class StatusTests(EngineCase):
    def test_manages_and_key(self):
        self.assertTrue(self.engine.manages("private"))
        self.assertFalse(self.engine.manages("other"))
        self.assertEqual(self.engine.key("group", "10001"), "group:10001")

    def test_thresholds(self):
        cases = ((1, "active"), (100, "active"), (110, "demoted"), (250, "demoted"), (265, "due"))
        for age, expected in cases:
            self.assertEqual(self.engine.status("private", "r", self.entry(age)), expected, age)

    def test_last_used_is_the_reference_when_set(self):
        old = self.entry(first_days_ago=400, used_days_ago=2, uses=1)
        self.assertEqual(self.engine.status("private", "r", old), "active")

    def test_uses_slow_the_fading(self):
        self.assertEqual(self.engine.status("private", "r", self.entry(110, uses=0)), "demoted")
        self.assertEqual(self.engine.status("private", "r", self.entry(110, uses=3)), "active")

    def test_pinned_entry_and_pin_refs(self):
        self.make_engine(pin_refs=("*/profile.md", "id-*"))
        ancient = self.entry(5000, pinned=True)
        self.assertEqual(self.engine.status("private", "r", ancient), "pinned")
        self.assertEqual(self.engine.status("private", "me/profile.md", self.entry(5000)), "pinned")
        self.assertEqual(self.engine.status("private", "id-7", None, date="2000-01-01"), "pinned")
        self.assertEqual(self.engine.status("private", "me/notes.md", self.entry(5000)), "due")

    def test_unknown_memory_ages_from_its_date_or_not_at_all(self):
        # ranking looks at the memory's own date; deletion has no clock for a memory the ledger has not met
        old = date_of(T0 - 300 * DAY)
        self.assertEqual(self.engine.status("private", "r", None, date=old), "demoted")
        self.assertEqual(self.engine.status("private", "r", None), "active")
        self.assertEqual(self.engine.status("private", "r", None, date="not-a-date"), "active")

    def test_two_clocks_for_one_old_memory(self):
        # dated 700 days ago, never used, but the ledger met it only 10 days ago
        e = self.entry(first_days_ago=700, managed_days_ago=10)
        self.assertLess(self.engine.ranking_retention("private", e), 0.001)
        self.assertGreater(self.engine.deletion_retention("private", e), 0.85)
        self.assertEqual(self.engine.status("private", "r", e), "demoted")  # ranks low ...
        # ... but only down to the floor: the multiplier is bounded
        self.assertAlmostEqual(self.engine.freshness("private", "r", e, 0.7), 0.7, places=3)

    def test_deletion_counts_from_the_later_of_last_use_and_managed_since(self):
        used_late = self.entry(first_days_ago=700, managed_days_ago=500, used_days_ago=3, uses=1)
        self.assertEqual(self.engine.deletion_start(used_late, T0), T0 - 3 * DAY)
        used_before = self.entry(first_days_ago=700, managed_days_ago=10, used_days_ago=300, uses=1)
        self.assertEqual(self.engine.deletion_start(used_before, T0), T0 - 10 * DAY)
        self.assertEqual(self.engine.ranking_start(used_before), T0 - 300 * DAY)
        unknown = Entry("private", "r", T0 - 900 * DAY, None, 0, False, None)
        self.assertEqual(self.engine.deletion_start(unknown, T0), T0)
        self.assertEqual(self.engine.deletion_start(None, T0), T0)

    def test_a_memory_is_due_only_after_the_whole_lifetime_under_management(self):
        # half-life 60: deletion line (R < 0.05) after 259.3 days
        for managed, expected in ((100, "demoted"), (259, "demoted"), (260, "due")):
            e = self.entry(first_days_ago=900, managed_days_ago=managed)
            self.assertEqual(self.engine.status("private", "r", e), expected, managed)

    def test_use_resets_both_clocks(self):
        self.ledger.touch("private", [("r", T0 - 900 * DAY)], T0 - 400 * DAY)  # old memory, managed 400 days
        before = self.ledger.get_many("private", ["r"])["r"]
        self.assertEqual(self.engine.status("private", "r", before), "due")
        self.engine.record_use("private", [("r", None)])
        after = self.ledger.get_many("private", ["r"])["r"]
        self.assertEqual((after.last_used, after.managed_since), (T0, T0 - 400 * DAY))
        self.assertEqual(self.engine.ranking_retention("private", after), 1.0)
        self.assertEqual(self.engine.deletion_retention("private", after), 1.0)
        self.assertEqual(self.engine.status("private", "r", after), "active")
        self.clock.advance(100)  # one use: half-life 96 days, both ages count from the use
        expected = 0.5 ** (100 / 96)
        self.assertAlmostEqual(self.engine.ranking_retention("private", after), expected)
        self.assertAlmostEqual(self.engine.deletion_retention("private", after), expected)

    def test_retention_of_an_unknown_memory_without_date_is_one(self):
        self.assertEqual(self.engine.retention("private", None), 1.0)

    def test_freshness_multipliers(self):
        # one bounded formula for every state: floor + (1 - floor) * ranking retention; pinned is 1
        floor = 0.7
        pinned = self.entry(5000, pinned=True)
        self.assertEqual(self.engine.freshness("private", "r", pinned, floor), 1.0)
        for age, state in ((0, "active"), (60, "active"), (120, "demoted"), (300, "due")):
            entry = self.entry(age)
            retention = 0.5 ** (age / 60)
            self.assertEqual(self.engine.status("private", "r", entry), state)
            self.assertAlmostEqual(self.engine.freshness("private", "r", entry, floor), floor + 0.3 * retention)

    def test_freshness_never_goes_below_the_floor(self):
        for floor in (0.0, 0.5, 0.7, 1.0):
            value = self.engine.freshness("private", "r", self.entry(5000), floor)
            self.assertGreaterEqual(value, floor)
            self.assertLessEqual(value, 1.0)
        self.assertEqual(self.engine.freshness("private", "r", self.entry(5000), 1.0), 1.0)


class LedgerBookkeepingTests(EngineCase):
    def test_record_use_uses_the_clock_and_the_memory_date(self):
        self.engine.record_use("private", [("a", "2026-05-01"), ("b", None), ("a", "2026-05-01")])
        got = self.ledger.get_many("private", ["a", "b"])
        self.assertEqual(got["a"].uses, 1)  # one memory returned once, even when listed twice
        self.assertEqual(got["a"].last_used, T0)
        self.assertEqual(got["a"].first_seen, float(calendar.timegm((2026, 5, 1, 0, 0, 0))))
        self.assertEqual(got["b"].first_seen, T0)

    def test_sync_adds_unknown_and_removes_gone(self):
        store = FakeStore({"a": ("x", "2026-01-01"), "b": ("y", None)})
        self.ledger.touch("private", [("ghost", 1.0), ("a", 5.0)], T0)
        report = self.engine.sync("private", store)
        self.assertEqual(report, {"added": 1, "removed": 1})
        got = {e.ref: e for e in self.ledger.entries("private")}
        self.assertEqual(set(got), {"a", "b"})
        self.assertEqual(got["a"].first_seen, 5.0)  # an existing row is never overwritten
        self.assertEqual(got["b"].first_seen, T0)

    def test_sync_takes_first_seen_from_the_date(self):
        store = FakeStore({"a": ("x", "2026-01-01")})
        self.engine.sync("private", store)
        self.assertEqual(self.ledger.entries("private")[0].first_seen, float(calendar.timegm((2026, 1, 1, 0, 0, 0))))

    def test_pin_and_unpin(self):
        self.engine.pin("private", ["a"])
        self.assertTrue(self.ledger.get_many("private", ["a"])["a"].pinned)
        self.engine.unpin("private", ["a"])
        self.assertFalse(self.ledger.get_many("private", ["a"])["a"].pinned)

    def test_pin_receipt(self):
        store = FakeStore()
        receipt = store.remember([{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}])
        self.assertEqual(self.engine.pin_receipt("private", store, receipt), 1)
        self.assertEqual([e.pinned for e in self.ledger.entries("private")], [True])

    def test_pin_receipt_is_none_when_not_known_or_not_supported(self):
        store = FakeStore()
        store.receipts["later"] = None
        self.assertIsNone(self.engine.pin_receipt("private", store, "later"))
        self.assertIsNone(self.engine.pin_receipt("private", store, "never-heard-of"))
        self.assertIsNone(self.engine.pin_receipt("private", PlainStore(), "w"))
        self.assertEqual(self.ledger.entries("private"), [])


class SweepTests(EngineCase):
    def store(self, profile=False, key="private"):
        items = {
            "fresh": ("fresh text", date_of(T0 - 1 * DAY)),
            "fading": ("fading text", date_of(T0 - 110 * DAY)),
            "gone": ("gone text", date_of(T0 - 300 * DAY)),
        }
        if profile:
            items["me/profile.md"] = ("profile text", date_of(T0 - 300 * DAY))
        store = FakeStore(items)
        self.manage(key, store)  # managed since each memory's date: the lifetime has been lived
        return store

    def test_report_numbers_and_what_is_forgotten(self):
        self.make_engine(pin_refs=("*/profile.md",))
        store = self.store(profile=True)
        report = self.engine.sweep({"private": store})
        self.assertEqual(report, {
            "private": {"total": 4, "pinned": 1, "active": 1, "demoted": 1, "forgotten": 1, "held": 0, "failed": 0},
            "purged": 0, "unsupported": []})
        self.assertEqual(set(store.items), {"fresh", "fading", "me/profile.md"})
        self.assertEqual(self.archive.get("private", "gone").text, "gone text")
        self.assertNotIn("gone", {e.ref for e in self.ledger.entries("private")})

    def test_the_archive_holds_the_memory_before_it_is_deleted(self):
        store = self.store()
        seen = []
        original = store.delete

        def delete(ref):
            seen.append(self.archive.get("private", ref) is not None)
            return original(ref)

        store.delete = delete
        self.engine.sweep({"private": store})
        self.assertEqual(seen, [True])

    def test_archive_record_carries_dates_and_expiry(self):
        self.make_engine(archive_days=30)
        self.ledger.use("private", [("gone", None)], T0 - 500 * DAY)
        store = FakeStore({"gone": ("t", "2025-01-01")})
        self.engine.sweep({"private": store})
        record = self.archive.get("private", "gone")
        self.assertEqual((record.date, record.uses, record.forgotten_at, record.expires_at),
                         ("2025-01-01", 1, T0, T0 + 30 * DAY))

    def test_a_failed_archive_write_means_no_delete(self):
        self.make_engine(archive=FailingArchive(":memory:"))
        store = self.store()
        report = self.engine.sweep({"private": store})
        self.assertEqual(report["private"]["failed"], 1)
        self.assertEqual(report["private"]["forgotten"], 0)
        self.assertIn("gone", store.items)
        self.assertEqual(store.deleted, [])
        self.assertIn("gone", {e.ref for e in self.ledger.entries("private")})

    def test_a_failed_read_or_delete_leaves_the_memory(self):
        store = self.store()
        store.fail_delete = True
        report = self.engine.sweep({"private": store})
        self.assertEqual(report["private"]["failed"], 1)
        self.assertIn("gone", store.items)
        self.assertIsNone(self.archive.get("private", "gone"))  # no archive copy of a memory that is still there
        store.fail_delete = False
        report = self.engine.sweep({"private": store})
        self.assertEqual(report["private"]["forgotten"], 1)
        self.assertNotIn("gone", store.items)

    def test_a_store_that_cannot_list_is_failed_not_fatal(self):
        broken, fine = self.store(), self.store(key="group")
        broken.fail_refs = True
        report = self.engine.sweep({"private": broken, "group": fine})
        self.assertEqual(report["private"]["failed"], 1)
        self.assertEqual(report["group"]["forgotten"], 1)

    def test_dry_run_changes_nothing_anywhere(self):
        store = self.store()
        before = dict(store.items)
        rows = self.ledger.entries("private")
        self.archive.add("private", "stale", "old", None, 0, T0 - 200 * DAY, T0 - 100 * DAY)
        report = self.engine.sweep({"private": store}, dry_run=True)
        self.assertEqual(report["private"], {"total": 3, "pinned": 0, "active": 1, "demoted": 1, "due": 1, "held": 0, "failed": 0})
        self.assertEqual(report["purged"], 0)
        self.assertEqual(store.items, before)
        self.assertEqual(store.deleted, [])
        self.assertEqual(self.ledger.entries("private"), rows)  # a dry run does not even sync the ledger
        self.assertEqual(self.archive.count(), 1)

    def test_unsupported_stores_are_reported_and_skipped(self):
        report = self.engine.sweep({"plain": PlainStore(), "private": self.store()})
        self.assertEqual(report["unsupported"], ["plain"])
        self.assertNotIn("plain", report)
        self.assertIn("private", report)
        self.assertEqual(self.ledger.entries("plain"), [])

    def test_the_archive_is_purged_after_the_retention(self):
        self.make_engine(archive_days=90)
        self.engine.sweep({"private": self.store()})
        self.assertEqual(self.archive.count(), 1)
        self.clock.advance(89)
        self.assertEqual(self.engine.sweep({"private": FakeStore()})["purged"], 0)
        self.clock.advance(2)
        self.assertEqual(self.engine.sweep({"private": FakeStore()})["purged"], 1)
        self.assertEqual(self.archive.count(), 0)

    def test_pinned_entries_are_never_forgotten(self):
        store = self.store()
        self.engine.pin("private", ["gone"])
        report = self.engine.sweep({"private": store})
        self.assertEqual(report["private"]["pinned"], 1)
        self.assertIn("gone", store.items)

    def test_tenant_keys_are_swept_one_by_one(self):
        a = FakeStore({"x": ("t", date_of(T0 - 300 * DAY))})
        b = FakeStore({"x": ("t", date_of(T0 - 1 * DAY))})
        self.manage("group:1", a)
        self.manage("group:2", b)
        report = self.engine.sweep({"group:1": a, "group:2": b})
        self.assertEqual(report["group:1"]["forgotten"], 1)
        self.assertEqual(report["group:2"]["active"], 1)
        self.assertEqual(self.archive.get("group:1", "x").text, "t")

    def test_the_report_holds_no_memory_text(self):
        report = self.engine.sweep({"private": self.store()})
        self.assertNotIn("text", repr(report))

    def test_restore_puts_the_memory_back_and_it_survives_the_next_sweep(self):
        store = self.store()
        self.engine.sweep({"private": store})
        self.assertNotIn("gone", store.items)
        self.assertTrue(self.engine.restore("private", "gone", store))
        self.assertEqual(store.items["gone"], ("gone text", date_of(T0 - 300 * DAY)))
        self.assertIsNone(self.archive.get("private", "gone"))
        self.assertEqual(self.ledger.get_many("private", ["gone"])["gone"].last_used, T0)
        report = self.engine.sweep({"private": store})
        self.assertEqual(report["private"]["forgotten"], 0)
        self.assertIn("gone", store.items)

    def test_restore_of_something_not_archived_is_false(self):
        self.assertFalse(self.engine.restore("private", "nothing", FakeStore()))


class FreshnessStageTests(EngineCase):
    def setUp(self):
        super().setUp()
        self.pool = Pool()
        self.addCleanup(self.pool.close)
        self.stage = Freshness(self.engine, floor=0.3, credit="all")

    def run_recall(self, stores, stage_list, limit=5, **ctx_kw):
        notes = []
        ctx = RecallContext("tea", limit, pool=self.pool, notes=notes, **ctx_kw)
        out = recall(stores, stage_list, ctx)
        return out, ctx, notes

    def texts(self, out):
        return [r["text"] for r in out["results"]]

    def test_a_newer_memory_outranks_an_older_equally_similar_one(self):
        store = FakeStore({"old": ("tea old", date_of(T0 - 50 * DAY)), "new": ("tea new", date_of(T0 - 2 * DAY))})
        out, ctx, _ = self.run_recall({"private": store}, [self.stage])
        self.assertEqual(self.texts(out), ["tea new", "tea old"])
        self.assertGreater(out["results"][0]["score"], out["results"][1]["score"])

    def test_the_original_similarity_is_kept_in_meta(self):
        store = FakeStore({"a": ("tea a", date_of(T0 - 50 * DAY))})
        out, ctx, _ = self.run_recall({"private": store}, [self.stage])
        candidate = ctx.accepted[0]
        self.assertEqual(candidate.meta["similarity"], 0.8)
        self.assertAlmostEqual(candidate.meta["retention"], 0.5 ** (50 / 60))
        self.assertLess(candidate.score, 0.8)

    def test_a_pinned_old_memory_keeps_its_score(self):
        store = FakeStore({"a": ("tea a", date_of(T0 - 900 * DAY))})
        self.ledger.pin("private", ["a"])
        out, _, _ = self.run_recall({"private": store}, [self.stage])
        self.assertAlmostEqual(out["results"][0]["score"], 0.8)

    def test_every_state_uses_the_same_bounded_multiplier(self):
        demoted = FakeStore({"a": ("tea a", date_of(T0 - 120 * DAY))})  # retention 0.25, "demoted"
        out, _, _ = self.run_recall({"private": demoted}, [self.stage])
        self.assertAlmostEqual(out["results"][0]["score"], 0.8 * (0.3 + 0.7 * 0.25))
        active = FakeStore({"b": ("tea b", date_of(T0 - 60 * DAY))})  # retention 0.5
        out, _, _ = self.run_recall({"private": active}, [self.stage])
        self.assertAlmostEqual(out["results"][0]["score"], 0.8 * (0.3 + 0.7 * 0.5))

    def test_the_default_floor_is_seven_tenths(self):
        self.assertEqual(Freshness(self.engine).floor, 0.7)

    def test_a_strong_stale_match_outranks_a_weak_fresh_one_at_the_default_floor(self):
        stage = Freshness(self.engine)
        store = FakeStore({"stale": ("tea stale", date_of(T0 - 2000 * DAY)), "fresh": ("tea fresh", date_of(T0))})
        store.scores.update({"stale": 0.9, "fresh": 0.5})
        out, ctx, _ = self.run_recall({"private": store}, [stage])
        self.assertEqual(self.texts(out), ["tea stale", "tea fresh"])
        self.assertAlmostEqual(out["results"][0]["score"], 0.9 * 0.7, places=3)
        # the old formula (no floor once demoted) would have put the stale one at 0.9 * ~0 and buried it
        self.assertGreater(out["results"][0]["score"], out["results"][1]["score"])

    def test_freshness_settles_near_ties(self):
        stage = Freshness(self.engine)
        store = FakeStore({"old": ("tea old", date_of(T0 - 2000 * DAY)), "new": ("tea new", date_of(T0))})
        store.scores.update({"old": 0.80, "new": 0.78})
        out, _, _ = self.run_recall({"private": store}, [stage])
        self.assertEqual(self.texts(out), ["tea new", "tea old"])

    def test_min_score_before_freshness_looks_at_similarity_only(self):
        store = FakeStore({"old": ("tea old", date_of(T0 - 200 * DAY)), "weak": ("tea weak", date_of(T0))})
        store.scores["weak"] = 0.4
        out, _, _ = self.run_recall({"private": store}, [stages.MinScore(0.5), self.stage])
        self.assertEqual(self.texts(out), ["tea old"])  # kept although its final score is far below 0.5

    def test_stores_forgetting_does_not_manage_are_untouched(self):
        store = FakeStore({"a": ("tea a", date_of(T0 - 900 * DAY))})
        out, ctx, _ = self.run_recall({"other": store}, [self.stage])
        self.assertAlmostEqual(out["results"][0]["score"], 0.8)
        self.assertEqual(self.ledger.stores(), [])

    def test_finish_records_uses_only_for_returned_memories(self):
        store = FakeStore({"best": ("tea best", date_of(T0 - 1 * DAY)), "next": ("tea next", date_of(T0 - 90 * DAY))})
        out, _, notes = self.run_recall({"private": store}, [self.stage], limit=1)
        self.assertEqual(self.texts(out), ["tea best"])
        got = self.ledger.get_many("private", ["best", "next"])
        self.assertEqual(set(got), {"best"})
        self.assertEqual((got["best"].uses, got["best"].last_used), (1, T0))
        self.assertEqual(notes, [])

    def credit_store(self):
        store = FakeStore({k: ("tea " + k, None) for k in ("best", "close", "far")})
        store.scores.update({"best": 0.90, "close": 0.82, "far": 0.60})
        return store

    def used(self, *refs):
        return {r for r, e in self.ledger.get_many("private", list(refs)).items() if e.uses}

    def test_credit_top_records_the_best_and_the_near_ties_only(self):
        stage = Freshness(self.engine)  # default: credit "top", ratio 0.9
        out, _, _ = self.run_recall({"private": self.credit_store()}, [stage])
        self.assertEqual(len(out["results"]), 3)  # all three are returned ...
        self.assertEqual(self.used("best", "close", "far"), {"best", "close"})  # ... 0.82 >= 0.9 * 0.9; 0.60 is noise

    def test_credit_top_with_a_stricter_ratio(self):
        stage = Freshness(self.engine, credit_ratio=0.95)
        self.run_recall({"private": self.credit_store()}, [stage])
        self.assertEqual(self.used("best", "close", "far"), {"best"})

    def test_credit_top_uses_similarity_not_the_freshness_adjusted_score(self):
        stage = Freshness(self.engine)
        store = FakeStore({"stale": ("tea stale", date_of(T0 - 2000 * DAY)), "fresh": ("tea fresh", date_of(T0))})
        store.scores.update({"stale": 0.9, "fresh": 0.5})
        self.run_recall({"private": store}, [stage])
        self.assertEqual(self.used("stale", "fresh"), {"stale"})

    def test_credit_all_records_every_returned_memory(self):
        self.run_recall({"private": self.credit_store()}, [Freshness(self.engine, credit="all")])
        self.assertEqual(self.used("best", "close", "far"), {"best", "close", "far"})

    def test_credit_none_records_nothing(self):
        out, _, notes = self.run_recall({"private": self.credit_store()}, [Freshness(self.engine, credit="none")])
        self.assertEqual(len(out["results"]), 3)
        self.assertEqual(self.ledger.stores(), [])
        self.assertEqual(notes, [])

    def test_a_single_returned_memory_is_credited(self):
        store = FakeStore({"only": ("tea only", None)})
        store.scores["only"] = 0.31
        self.run_recall({"private": store}, [Freshness(self.engine)])
        self.assertEqual(self.used("only"), {"only"})

    def test_bad_credit_options_are_config_errors(self):
        from feiyu_memgate.errors import ConfigError
        for options in ({"credit": "some"}, {"credit_ratio": 0}, {"credit_ratio": 1.5}, {"floor": True},
                        {"credit_ratio": "0.9"}):
            with self.assertRaises(ConfigError, msg=options):
                Freshness(self.engine, **options)

    def test_a_dropped_candidate_is_not_used(self):
        class DropAll(RecallStage):
            def admit(self, candidate, ctx):
                return "drop"

        store = FakeStore({"a": ("tea a", None)})
        out, _, _ = self.run_recall({"private": store}, [self.stage, DropAll()])
        self.assertEqual(out["results"], [])
        self.assertEqual(self.ledger.stores(), [])

    def test_a_tenant_candidate_is_keyed_with_the_tenant(self):
        store = FakeStore({"a": ("tea a", None)})
        out, _, _ = self.run_recall({"group": store}, [self.stage], tenant_id="10001", tenant_source="group")
        self.assertEqual(self.ledger.stores(), ["group:10001"])
        self.run_recall({"group": store}, [self.stage])  # a shared store of that name has no tenant
        self.assertEqual(self.ledger.stores(), ["group", "group:10001"])

    def test_a_ledger_read_failure_does_not_fail_the_recall(self):
        self.make_engine(ledger=BrokenLedger())
        stage = Freshness(self.engine)
        store = FakeStore({"a": ("tea a", date_of(T0 - 900 * DAY))})
        out, _, notes = self.run_recall({"private": store}, [stage])
        self.assertEqual(self.texts(out), ["tea a"])
        self.assertAlmostEqual(out["results"][0]["score"], 0.8)
        self.assertEqual(notes, ["ledger-failed"])

    def test_a_ledger_write_failure_is_noted_once(self):
        self.make_engine(ledger=BrokenLedger(fail_get=False, fail_use=True))
        stage = Freshness(self.engine)
        store = FakeStore({"a": ("tea a", None), "b": ("tea b", None)})
        out, _, notes = self.run_recall({"private": store}, [stage])
        self.assertEqual(len(out["results"]), 2)
        self.assertEqual(notes, ["ledger-failed"])

    def test_the_stage_state_is_per_request(self):
        store = FakeStore({"a": ("tea a", None)})
        self.run_recall({"private": store}, [self.stage])
        self.run_recall({"private": store}, [self.stage])
        self.assertEqual(self.ledger.get_many("private", ["a"])["a"].uses, 2)

    def test_a_bad_floor_is_a_config_error(self):
        from feiyu_memgate.errors import ConfigError
        with self.assertRaises(ConfigError):
            Freshness(self.engine, floor=1.5)


class FinishHookTests(unittest.TestCase):
    def test_finish_runs_for_every_stage_and_a_failure_is_noted(self):
        calls = []

        class Broken(RecallStage):
            def finish(self, ctx):
                calls.append("broken")
                raise RuntimeError("boom")

        class Counting(RecallStage):
            def finish(self, ctx):
                calls.append(("counting", len(ctx.accepted)))

        pool = Pool()
        self.addCleanup(pool.close)
        notes = []
        ctx = RecallContext("tea", 5, pool=pool, notes=notes)
        out = recall({"s": FakeStore({"a": ("tea a", None)})}, [Broken(), Counting()], ctx)
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(calls, ["broken", ("counting", 1)])
        self.assertEqual(notes, ["finish-failed"])

    def test_the_default_finish_does_nothing(self):
        self.assertIsNone(RecallStage().finish(None))

    def test_tenant_source_defaults_to_none(self):
        pool = Pool()
        self.addCleanup(pool.close)
        self.assertIsNone(RecallContext("q", 1, pool=pool).tenant_source)


class ConcurrencyTests(EngineCase):
    def test_uses_from_many_threads(self):
        def worker():
            for _ in range(20):
                self.engine.record_use("private", [("a", None)])

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.ledger.get_many("private", ["a"])["a"].uses, 100)


class FirstSweepTests(EngineCase):
    """Enabling forgetting on a store full of old memories: nothing is deleted at first."""

    def old_store(self, count=20):
        return FakeStore({"m%02d" % i: ("old text %d" % i, date_of(T0 - (300 + 10 * i) * DAY)) for i in range(count)})

    def test_the_first_sweep_deletes_nothing_but_the_old_ones_rank_low(self):
        self.make_engine(max_forget_fraction=0.2)  # the real brake: it must not even be needed
        store = self.old_store()
        report = self.engine.sweep({"private": store})
        self.assertEqual(report["private"], {"total": 20, "pinned": 0, "active": 0, "demoted": 20,
                                             "forgotten": 0, "held": 0, "failed": 0})
        self.assertEqual(len(store.items), 20)
        self.assertEqual(store.deleted, [])
        self.assertEqual(self.archive.count(), 0)
        # all of them are in the ledger now, managed since the sweep, and ranked by their own date
        rows = self.ledger.entries("private")
        self.assertEqual({e.managed_since for e in rows}, {T0})
        self.assertTrue(0.7 <= self.engine.freshness("private", "m00", rows[0], 0.7) < 0.72)  # close to the floor

    def test_old_memories_are_still_found_and_demoted_in_recall(self):
        store = FakeStore({"old": ("tea old", date_of(T0 - 700 * DAY)), "new": ("tea new", date_of(T0 - 2 * DAY))})
        self.engine.sweep({"private": store})
        pool = Pool()
        self.addCleanup(pool.close)
        ctx = RecallContext("tea", 5, pool=pool, notes=[])
        out = recall({"private": store}, [Freshness(self.engine)], ctx)
        self.assertEqual([r["text"] for r in out["results"]], ["tea new", "tea old"])

    def test_deletable_only_after_the_full_lifetime_under_management(self):
        store = self.old_store(1)
        self.engine.sweep({"private": store})  # day 0: the ledger meets the memory
        self.clock.advance(259)
        self.assertEqual(self.engine.sweep({"private": store})["private"]["forgotten"], 0)
        self.clock.advance(2)
        self.assertEqual(self.engine.sweep({"private": store})["private"]["forgotten"], 1)
        self.assertEqual(store.items, {})

    def test_a_use_in_between_postpones_the_deletion_by_a_whole_lifetime(self):
        store = self.old_store(1)
        self.engine.sweep({"private": store})
        self.clock.advance(200)
        self.engine.record_use("private", [("m00", date_of(T0 - 300 * DAY))])
        # one use: half-life 96 days, lifetime 96 * log2(20) = 415 days counted from the use
        self.clock.advance(400)
        self.assertEqual(self.engine.sweep({"private": store})["private"]["forgotten"], 0)
        self.clock.advance(20)
        self.assertEqual(self.engine.sweep({"private": store})["private"]["forgotten"], 1)

    def test_restore_gives_both_clocks_a_fresh_start(self):
        store = self.old_store(1)
        self.engine.sweep({"private": store})
        self.clock.advance(300)
        self.engine.sweep({"private": store})
        self.assertEqual(store.items, {})
        self.clock.advance(5)
        self.assertTrue(self.engine.restore("private", "m00", store))
        row = self.ledger.get_many("private", ["m00"])["m00"]
        self.assertEqual((row.last_used, row.managed_since), (self.clock.t, self.clock.t))
        self.clock.advance(200)
        self.assertEqual(self.engine.sweep({"private": store})["private"]["forgotten"], 0)


class RateLimitTests(EngineCase):
    """Per sweep and store: forget at most max(max_forget_min, max_forget_fraction * size)."""

    def aged_store(self, count, due, key="private", spread=False):
        """`count` memories, all in the ledger; the first `due` of them managed long enough to be due.
        With spread the due ones differ in age (m00 the oldest), so their retentions differ."""
        store = FakeStore({"m%02d" % i: ("text %d" % i, date_of(T0 - 400 * DAY)) for i in range(count)})
        for i in range(count):
            if i < due:
                since = T0 - ((600 - 10 * i) if spread else 400) * DAY
            else:
                since = T0 - 5 * DAY
            self.ledger.touch(key, [("m%02d" % i, T0 - 400 * DAY)], since)
        return store

    def test_the_defaults(self):
        engine = Forgetting(self.ledger, self.archive, Ebbinghaus(), wall=self.clock)
        self.assertEqual((engine.max_forget_fraction, engine.max_forget_min), (0.02, 5))

    def test_the_limit_is_the_larger_of_the_minimum_and_the_fraction(self):
        engine = Forgetting(self.ledger, self.archive, Ebbinghaus(), wall=self.clock)
        self.assertEqual([engine.forget_limit(n) for n in (0, 10, 240, 250, 1000, 5000)], [5, 5, 5, 5, 20, 100])
        engine = Forgetting(self.ledger, self.archive, Ebbinghaus(), wall=self.clock,
                            max_forget_fraction=0.5, max_forget_min=0)
        self.assertEqual([engine.forget_limit(n) for n in (0, 1, 2, 9)], [0, 0, 1, 4])

    def test_a_cohort_is_spread_over_sweeps(self):
        self.make_engine(max_forget_fraction=0.1, max_forget_min=2)
        store = self.aged_store(30, due=30)  # everything due on the same day; limit max(2, 10% of size)
        gone = []
        for sweep in range(20):
            before = len(store.items)
            report = self.engine.sweep({"private": store})["private"]
            gone.append(report["forgotten"])
            limit = self.engine.forget_limit(report["total"])
            self.assertEqual(report["held"], max(report["total"] - limit, 0))
            self.assertEqual(report["forgotten"], min(report["total"], limit))
            self.assertEqual(before - len(store.items), report["forgotten"])
        self.assertEqual(gone[0], 3)
        self.assertGreater(len([g for g in gone if g]), 5)  # many sweeps, not one
        self.assertEqual(sum(gone), 30)
        self.assertEqual(store.items, {})
        self.assertEqual(self.archive.count(), 30)

    def test_the_lowest_retention_goes_first_and_the_rest_are_held(self):
        self.make_engine(max_forget_fraction=0.0, max_forget_min=3)
        store = self.aged_store(10, due=6, spread=True)  # m00 is the stalest ... m05 the least stale
        report = self.engine.sweep({"private": store})
        self.assertEqual((report["private"]["forgotten"], report["private"]["held"]), (3, 3))
        self.assertEqual(store.deleted, ["m00", "m01", "m02"])
        self.assertEqual(self.archive.count(), 3)

    def test_ties_go_in_ref_order(self):
        self.make_engine(max_forget_fraction=0.0, max_forget_min=2)
        store = self.aged_store(6, due=6)
        self.engine.sweep({"private": store})
        self.assertEqual(store.deleted, ["m00", "m01"])

    def test_at_or_under_the_limit_forgets_everything_due(self):
        self.make_engine(max_forget_fraction=0.0, max_forget_min=5)
        store = self.aged_store(10, due=5)
        report = self.engine.sweep({"private": store})
        self.assertEqual((report["private"]["forgotten"], report["private"]["held"]), (5, 0))
        self.assertEqual(len(store.items), 5)

    def test_force_removes_the_limit_for_one_run(self):
        self.make_engine(max_forget_fraction=0.0, max_forget_min=1)
        store = self.aged_store(10, due=7)
        report = self.engine.sweep({"private": store}, force=True)
        self.assertEqual((report["private"]["forgotten"], report["private"]["held"]), (7, 0))
        self.assertEqual(self.archive.count(), 7)

    def test_the_limit_is_per_store(self):
        self.make_engine(max_forget_fraction=0.0, max_forget_min=2)
        first = self.aged_store(10, due=5)
        second = self.aged_store(10, due=5, key="group")
        report = self.engine.sweep({"private": first, "group": second})
        self.assertEqual([(report[k]["forgotten"], report[k]["held"]) for k in ("private", "group")], [(2, 3), (2, 3)])

    def test_a_dry_run_shows_what_would_be_held(self):
        self.make_engine(max_forget_fraction=0.0, max_forget_min=2)
        store = self.aged_store(10, due=4)
        report = self.engine.sweep({"private": store}, dry_run=True)
        self.assertEqual((report["private"]["due"], report["private"]["held"]), (4, 2))
        self.assertEqual(len(store.items), 10)
        self.assertEqual(self.archive.count(), 0)

    def test_an_empty_store_has_nothing_to_hold(self):
        report = self.engine.sweep({"private": FakeStore()})
        self.assertEqual((report["private"]["forgotten"], report["private"]["held"]), (0, 0))

    def test_a_wrong_clock_costs_a_small_slice_not_the_store(self):
        # the system clock jumps two years ahead: everything looks long unused
        self.make_engine(max_forget_fraction=0.05, max_forget_min=1)
        store = FakeStore({"m%02d" % i: ("t", date_of(T0 - 5 * DAY)) for i in range(20)})
        self.engine.sweep({"private": store})
        self.clock.advance(800)
        report = self.engine.sweep({"private": store})
        self.assertEqual((report["private"]["forgotten"], report["private"]["held"]), (1, 19))
        self.assertEqual(len(store.items), 19)
        self.assertEqual(self.archive.count(), 1)  # and that one can be restored
