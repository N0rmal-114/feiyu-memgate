import unittest

from feiyu_memgate.backends.openviking import event_date
from feiyu_memgate.core import stages, tidy


class TidyHelperTests(unittest.TestCase):
    def test_contained_text_is_a_duplicate(self):
        self.assertTrue(tidy.near_duplicate("用户喜欢喝乌龙茶。", "用户喜欢喝乌龙茶"))
        self.assertTrue(tidy.near_duplicate("喜欢乌龙茶", "用户说他很喜欢乌龙茶"))

    def test_different_facts_are_not_duplicates(self):
        self.assertFalse(tidy.near_duplicate("用户喜欢喝乌龙茶", "用户不喝咖啡"))

    def test_bigram_overlap_catches_reworded_text(self):
        self.assertTrue(tidy.near_duplicate("用户喜欢喝很烫的乌龙茶啊", "用户喜欢喝很烫的乌龙茶呀"))

    def test_very_short_text_only_matches_when_equal(self):
        self.assertTrue(tidy.near_duplicate("好!", "好"))
        self.assertFalse(tidy.near_duplicate("好", "好的呀"))

    def test_normalize_drops_space_punctuation_symbols_and_folds_width(self):
        self.assertEqual(tidy.normalize("Ａ b，C。 ★+"), "abc")

    def test_overlap_threshold_is_a_parameter(self):
        a, b = "用户喜欢喝很烫的乌龙茶啊", "用户喜欢喝很烫的乌龙茶呀"
        self.assertFalse(tidy.near_duplicate(a, b, overlap=0.99))

    def test_event_date(self):
        self.assertEqual(event_date("viking://~/memories/events/2026/11/03/a.md"), "2026-11-03")
        self.assertIsNone(event_date("viking://~/memories/events/2026/13/40/a.md"))
        self.assertIsNone(event_date("viking://~/memories/preferences/owner/diet.md"))
        self.assertIsNone(event_date(None))

    def test_marker_constant(self):
        self.assertEqual(stages.PROBE_MARKER, "FEIYU-PROBE-")

    def test_bigram_similarity(self):
        self.assertEqual(tidy.bigram_similarity("乌龙茶", "用户喜欢喝乌龙茶"), 1.0)
        self.assertEqual(tidy.bigram_similarity("乌龙茶", "咖啡"), 0.0)
        self.assertEqual(tidy.bigram_similarity("茶", "乌龙茶"), 1.0)
        self.assertEqual(tidy.bigram_similarity("", "x"), 0.0)


if __name__ == "__main__":
    unittest.main()
