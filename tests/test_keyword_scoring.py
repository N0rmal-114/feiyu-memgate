"""The keyword backends' scoring: IDF-weighted containment of character grams.

No network, no model. The maths (weights, range, monotonicity), the index versions of the sqlite
backend, and the agreement of the sqlite and in-memory backends.
"""
import math
import os
import sqlite3
import tempfile
import unittest

from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.backends.sqlite import SQLiteBackend
from feiyu_memgate.core.tidy import (PreparedQuery, bigram_similarity, gram_weights, idf_weight, index_grams, is_han,
                                     normalize)

CORPUS = [
    "许明川喝咖啡只喝燕麦拿铁，不加糖。",
    "许明川写稿时喜欢听没有歌词的钢琴曲。",
    "何知夏是许明川的朋友，她喜欢热的桂花乌龙，不喜欢冰饮。",
    "方雨桐养了一只叫汤圆的仓鼠，喜欢吃瓜子。",
    "咖啡机坏了，他把它送去修了，下周才能取回来。",
    "他最近常循环一张叫《雨落青瓦》的专辑。",
    "Green tea with honey is the favourite drink.",
    "The deploy happens on Friday afternoon.",
]


def counts_of(texts):
    counts = {}
    for text in texts:
        for gram in index_grams(normalize(text)):
            counts[gram] = counts.get(gram, 0) + 1
    return counts


class IdfWeightTest(unittest.TestCase):
    def test_always_positive_and_finite(self):
        for n in (0, 1, 2, 10, 1000, 10 ** 6):
            for df in (-3, 0, 1, 2, n, n + 5, 10 ** 7):
                w = idf_weight(df, n)
                self.assertTrue(w > 0 and math.isfinite(w), (df, n))

    def test_rarer_grams_weigh_more(self):
        n = 1000
        weights = [idf_weight(df, n) for df in (1, 2, 5, 50, 500, 1000)]
        self.assertEqual(weights, sorted(weights, reverse=True))
        self.assertEqual(len(set(weights)), len(weights))

    def test_a_gram_nobody_holds_counts_as_held_by_one(self):
        self.assertEqual(idf_weight(0, 100), idf_weight(1, 100))

    def test_the_bm25_formula(self):
        self.assertAlmostEqual(idf_weight(3, 20), math.log(1 + (20 - 3 + 0.5) / (3 + 0.5)))

    def test_more_memories_make_the_same_count_rarer(self):
        self.assertGreater(idf_weight(5, 1000), idf_weight(5, 50))


class GramTest(unittest.TestCase):
    def test_han_characters_only_for_unigrams(self):
        self.assertTrue(is_han("茶"))
        self.assertTrue(is_han("㐀"))
        for char in ("a", "Z", "7", "é", "あ", "。", " "):
            self.assertFalse(is_han(char), char)

    def test_index_grams(self):
        self.assertEqual(index_grams("ab茶"), {"ab", "b茶", "茶"})
        self.assertEqual(index_grams("ab茶", unigrams=False), {"ab", "b茶"})
        self.assertEqual(index_grams("a"), set())
        self.assertEqual(index_grams("茶"), {"茶"})

    def test_gram_weights_cover_the_query_and_respect_limit(self):
        norm = normalize("许明川喝咖啡")
        counts = counts_of(CORPUS)
        weights = gram_weights(norm, counts.get, len(CORPUS))
        self.assertEqual(set(weights), index_grams(norm))
        self.assertTrue(all(w > 0 for w in weights.values()))
        limited = gram_weights(norm, counts.get, len(CORPUS), limit=3)
        self.assertEqual(list(limited), sorted(index_grams(norm))[:3])
        self.assertEqual(set(gram_weights(norm, counts.get, len(CORPUS), unigrams=False)), bigrams_of(norm))

    def test_a_missing_count_is_zero(self):
        weights = gram_weights("茶叶", lambda g: None, 10)
        self.assertEqual(set(weights.values()), {idf_weight(1, 10)})


def bigrams_of(norm):
    return {norm[i:i + 2] for i in range(len(norm) - 1)}


class WeightedScoreTest(unittest.TestCase):
    def prepared(self, query, texts=CORPUS):
        norm = normalize(query)
        return PreparedQuery(query, gram_weights(norm, counts_of(texts).get, len(texts)))

    def test_without_weights_the_score_is_the_old_bigram_similarity(self):
        for query in ("许明川喝咖啡", "green tea", "a", "", "乌龙茶，好喝"):
            for text in CORPUS:
                self.assertEqual(PreparedQuery(query).score(text), bigram_similarity(query, text))

    def test_range_and_exact_containment(self):
        for query in ("许明川喝咖啡", "喜欢的朋友", "green tea honey", "周五发布", "完全无关的内容xyz"):
            p = self.prepared(query)
            for text in CORPUS:
                self.assertTrue(0.0 <= p.score(text) <= 1.0, (query, text))
        p = self.prepared("许明川喝咖啡")
        self.assertEqual(p.score("许明川喝咖啡"), 1.0)
        self.assertEqual(p.score("昨天听说许明川喝咖啡了"), 1.0)  # the text may hold more than the query
        self.assertEqual(p.score("tea tea"), 0.0)
        self.assertEqual(p.score(""), 0.0)

    def test_adding_a_matching_gram_never_lowers_the_score(self):
        query = "许明川喝咖啡不加糖"
        p = self.prepared(query)
        grams = sorted(index_grams(normalize(query)))
        previous = 0.0
        text = ""
        for gram in grams:  # a text that holds one more query gram each time
            text += gram + "|"  # "|" is dropped by normalize, so the grams of `text` are only the ones added
            score = p.score(text)
            self.assertGreaterEqual(score + 1e-12, previous)
            previous = score
        self.assertGreater(previous, 0.9)

    def test_a_rare_gram_counts_for_more_than_a_common_one(self):
        texts = ["猫的饭", "狗的饭", "鱼的饭", "鸟的饭", "龙的饭", "熊猫的故事"]
        p = self.prepared("猫的饭", texts)
        # Plain bigram containment ties them (one of the query's two pairs each: "猫的" and "的饭").
        self.assertEqual(PreparedQuery("猫的饭").score("熊猫的故事"), PreparedQuery("猫的饭").score("狗的饭"))
        # "熊猫的故事" shares the rare "猫的" and "猫"; "狗的饭" only the pair "的饭" that five memories hold.
        self.assertGreater(p.score("熊猫的故事"), p.score("狗的饭") + 0.3)
        self.assertEqual(p.score("猫的饭"), 1.0)

    def test_unweighted_containment_ties_what_weighting_separates(self):
        texts = ["咖啡机坏了", "咖啡很好喝", "机器人跳舞", "天气很好", "今天很好", "好喝的茶"]
        query = "咖啡机"
        plain = PreparedQuery(query)
        weighted = self.prepared(query, texts)
        # plain bigram containment: "咖啡" and "啡机" are 2 of 2 pairs for the first memory and 1 of 2 for "咖啡很好喝"
        self.assertEqual(plain.score("咖啡很好喝"), 0.5)
        self.assertGreater(weighted.score("咖啡机坏了"), weighted.score("咖啡很好喝"))
        self.assertEqual(weighted.score("咖啡机坏了"), 1.0)

    def test_only_weighted_grams_take_part(self):
        p = PreparedQuery("茶叶蛋", {"茶叶": 1.0})
        self.assertEqual(p.score("茶叶"), 1.0)
        self.assertEqual(p.score("叶蛋"), 0.0)

    def test_empty_or_nonpositive_weights_score_zero(self):
        self.assertEqual(PreparedQuery("茶叶蛋", {}).score("茶叶蛋"), 0.0)
        self.assertEqual(PreparedQuery("茶叶蛋", {"茶叶": 0.0}).score("茶叶蛋"), 0.0)

    def test_one_character_query_still_matches_by_containment(self):
        self.assertEqual(PreparedQuery("茶", {}).score("一杯茶"), 1.0)
        self.assertEqual(PreparedQuery("茶", {}).score("一杯水"), 0.0)

    def test_a_unigram_lets_a_paraphrase_count(self):
        # shares "睡" and "觉" but no whole pair with the memory "他晚上睡得早，觉也浅"
        texts = ["他晚上睡得早，觉也浅"] + ["完全不同的第%d条" % i for i in range(5)]
        p = self.prepared("睡觉", texts)
        self.assertGreater(p.score(texts[0]), 0.0)
        self.assertEqual(PreparedQuery("睡觉").score(texts[0]), 0.0)  # bigrams alone find nothing


class BackendsAgreeTest(unittest.TestCase):
    QUERIES = ("许明川喝什么咖啡", "他听什么音乐", "桂花乌龙", "green tea", "deploy friday", "雨落青瓦的专辑", "完全无关xyz")

    def setUp(self):
        self.memory = InMemoryBackend()
        self.sqlite = SQLiteBackend(":memory:")
        self.addCleanup(self.sqlite.close)
        for i, text in enumerate(CORPUS):
            self.memory.put(text, ref="r%d" % i)
            self.sqlite.put(text, ref="r%d" % i)

    def test_same_scores_and_order(self):
        for query in self.QUERIES:
            a = [(h.ref, round(h.score, 12)) for h in self.memory.find(query, 10)]
            b = [(h.ref, round(h.score, 12)) for h in self.sqlite.find(query, 10)]
            self.assertEqual(sorted(a), sorted(b), query)
            self.assertEqual([r for r, _ in a][:1], [r for r, _ in b][:1], query)

    def test_scores_in_range_and_best_first(self):
        for backend in (self.memory, self.sqlite):
            for query in self.QUERIES:
                scores = [h.score for h in backend.find(query, 10)]
                self.assertEqual(scores, sorted(scores, reverse=True))
                self.assertTrue(all(0.0 < s <= 1.0 for s in scores))

    def test_the_right_memory_comes_first(self):
        for backend in (self.memory, self.sqlite):
            self.assertEqual(backend.find("许明川喝什么咖啡", 3)[0].ref, "r0")
            self.assertEqual(backend.find("听什么音乐", 3)[0].ref, "r1")
            self.assertEqual(backend.find("green tea", 3)[0].ref, "r6")


class IndexVersionTest(unittest.TestCase):
    """Stores written before CJK single characters were indexed hold bigrams only."""

    def make_legacy(self, path, texts):
        """A database file as the previous version wrote it: no info table, bigram rows only."""
        db = sqlite3.connect(path)
        db.executescript("""
            CREATE TABLE memories (ref TEXT PRIMARY KEY, text TEXT NOT NULL, created_at INTEGER NOT NULL, date TEXT);
            CREATE TABLE grams (gram TEXT NOT NULL, ref TEXT NOT NULL, PRIMARY KEY (gram, ref)) WITHOUT ROWID;
            CREATE INDEX grams_by_ref ON grams (ref);
            CREATE INDEX memories_by_created ON memories (created_at);
        """)
        for i, text in enumerate(texts):
            ref = "m%d" % i
            norm = normalize(text)
            db.execute("INSERT INTO memories VALUES (?, ?, ?, NULL)", (ref, text, i))
            db.executemany("INSERT OR IGNORE INTO grams VALUES (?, ?)",
                           [(norm[j:j + 2], ref) for j in range(len(norm) - 1)])
        db.commit()
        db.close()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "legacy.db")

    def open(self, **options):
        be = SQLiteBackend(self.path, **options)
        self.addCleanup(be.close)
        return be

    def test_a_writable_open_reindexes_once(self):
        self.make_legacy(self.path, CORPUS)
        be = self.open()
        grams = {row[0] for row in be._db.execute("SELECT gram FROM grams")}
        self.assertIn("乌", grams)  # unigrams are in the index now
        self.assertEqual(be._db.execute("SELECT value FROM info WHERE key = 'gram_scheme'").fetchone()[0], "2")
        self.assertEqual(be.find("许明川喝咖啡", 3)[0].ref, "m0")
        be.close()
        again = self.open()  # the marker is there: no second rebuild
        before = again._db.total_changes
        self.assertEqual(again.find("许明川喝咖啡", 3)[0].ref, "m0")
        self.assertEqual(again._db.total_changes, before)

    def test_a_read_only_open_keeps_searching_on_bigrams(self):
        self.make_legacy(self.path, CORPUS)
        be = self.open(writable=False)
        self.assertFalse(be._unigrams)
        hits = be.find("许明川喝咖啡", 3)
        self.assertEqual(hits[0].ref, "m0")
        self.assertTrue(0 < hits[0].score <= 1.0)
        self.assertEqual(be._db.execute("SELECT COUNT(*) FROM info").fetchone()[0], 0)  # nothing was written
        with self.assertRaises(PermissionError):
            be.put("x y")

    def test_a_new_store_is_marked_and_indexes_unigrams(self):
        be = self.open()
        be.put("乌龙茶", ref="a")
        grams = {row[0] for row in be._db.execute("SELECT gram FROM grams WHERE ref = 'a'")}
        self.assertEqual(grams, {"乌龙", "龙茶", "乌", "龙", "茶"})
        self.assertTrue(be._unigrams)

    def test_replacing_and_deleting_keep_the_index_clean(self):
        be = self.open()
        be.put("乌龙茶", ref="a")
        be.put("红茶", ref="a")
        grams = {row[0] for row in be._db.execute("SELECT gram FROM grams WHERE ref = 'a'")}
        self.assertEqual(grams, {"红茶", "红", "茶"})
        self.assertTrue(be.delete("a"))
        self.assertEqual(be._db.execute("SELECT COUNT(*) FROM grams").fetchone()[0], 0)


class DevSetDefaultsTest(unittest.TestCase):
    """The keyword defaults must keep doing what they were chosen for on bench/dev (no network: keyword only):
    few wrong items, and quiet on most questions that have no answer."""

    def test_keyword_defaults_on_the_dev_set(self):
        import json
        from feiyu_memgate.core.recall import RecallContext, recall
        from feiyu_memgate.core.pool import Pool
        from feiyu_memgate.core import stages as stages_module
        from feiyu_memgate import registry
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench", "dev")
        with open(os.path.join(base, "memories.json"), encoding="utf-8") as f:
            memories = json.load(f)
        with open(os.path.join(base, "queries.json"), encoding="utf-8") as f:
            queries = json.load(f)
        pool = Pool()
        self.addCleanup(pool.close)
        for make in (lambda: InMemoryBackend(), lambda: SQLiteBackend(":memory:")):
            backend = make()
            self.addCleanup(backend.close) if hasattr(backend, "close") else None
            for ref, m in memories.items():
                backend.put(m["text"], ref=ref)
            chain = [registry.create("recall_stage", name) for name in stages_module.DEFAULT_STAGES]
            hit = answerable = quiet = silent = 0
            precisions = []
            for q in queries.values():
                out = recall({"s": backend}, chain, RecallContext(q["query"], 5, pool=pool))
                ids = [r["id"] for r in out["results"]]
                if q["expect"]:
                    answerable += 1
                    hit += any(i in q["expect"] for i in ids)
                    if ids:
                        precisions.append(sum(1 for i in ids if i in q["expect"]) / len(ids))
                else:
                    silent += 1
                    quiet += not ids
            self.assertGreaterEqual(quiet / silent, 0.8)
            self.assertGreaterEqual(sum(precisions) / len(precisions), 0.8)
            self.assertGreaterEqual(hit / answerable, 0.3)


if __name__ == "__main__":
    unittest.main()
