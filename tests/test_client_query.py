import unittest

from feiyu_memgate.client import QueryShaper, build_query, meaningful, previous_user_text


class BuildQueryTests(unittest.TestCase):
    def test_skips(self):
        for text in ("", "   ", "😀😀", "[图片]", "[表情][表情]", "嗯", "哈哈", "OK", "ok！", "/help", "／help",
                     "嗯~", "好～", "哈哈~~", "1️⃣"):
            self.assertIsNone(build_query(text, "上一句"), text)

    def test_non_string_is_skipped(self):
        for value in (None, 5, ["a"]):
            self.assertIsNone(build_query(value, "x"))

    def test_placeholder_only_messages_skip_recall(self):
        self.assertIsNone(build_query("[图片]", "我上周去了杭州"))

    def test_short_message_takes_the_previous_one(self):
        self.assertEqual(build_query("那你还记得吗", "我上周去了杭州"), "我上周去了杭州\n那你还记得吗")

    def test_short_message_without_previous_stands_alone(self):
        self.assertEqual(build_query("那你还记得吗", None), "那你还记得吗")
        self.assertEqual(build_query("那你还记得吗", "  "), "那你还记得吗")

    def test_long_message_stands_alone(self):
        self.assertEqual(build_query("我明天要去医院复查一下牙齿", "上一句"), "我明天要去医院复查一下牙齿")

    def test_length_cap_keeps_the_current_message_whole(self):
        q = build_query("那你还记得吗", "长" * 600)
        self.assertLessEqual(len(q), 500)
        self.assertTrue(q.endswith("那你还记得吗"))

    def test_max_chars_argument(self):
        self.assertEqual(len(build_query("x" * 900, None, max_chars=100)), 100)

    def test_meaningful_drops_noise(self):
        self.assertEqual(meaningful("[图片] 你好！😀 ~"), "你好")
        self.assertEqual(meaningful(None), "")

    def test_previous_user_text(self):
        ctx = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
               {"role": "user", "content": [{"type": "text", "text": "c"}, {"type": "image_url"}]}]
        self.assertEqual(previous_user_text(ctx), "c")
        self.assertIsNone(previous_user_text([{"role": "assistant", "content": "b"}]))
        self.assertIsNone(previous_user_text("not a list"))
        self.assertIsNone(previous_user_text([{"role": "user", "content": "  "}]))
        self.assertIsNone(previous_user_text([{"role": "user", "content": 5}]))


class QueryShaperTests(unittest.TestCase):
    def test_defaults_match_the_module_function(self):
        shaper = QueryShaper()
        for current, previous in (("那你还记得吗", "我上周去了杭州"), ("嗯", "x"), ("/help", None),
                                  ("我明天要去医院复查一下牙齿", "上一句")):
            self.assertEqual(shaper.build(current, previous), build_query(current, previous))

    def test_custom_filler_chars(self):
        shaper = QueryShaper(filler_chars="ab")
        self.assertIsNone(shaper.build("ab", "prev"))
        self.assertIsNotNone(QueryShaper().build("ab", "prev"))

    def test_short_chars(self):
        self.assertEqual(QueryShaper(short_chars=3).build("hello there", "before"), "hello there")
        self.assertEqual(QueryShaper(short_chars=30).build("hello there", "before"), "before\nhello there")

    def test_max_chars(self):
        q = QueryShaper(max_chars=20).build("short", "p" * 100)
        self.assertEqual(len(q), 20)
        self.assertTrue(q.endswith("short"))
        self.assertEqual(QueryShaper(max_chars=5).build("abcdefghij", None), "abcde")

    def test_skip_prefixes(self):
        shaper = QueryShaper(skip_prefixes="!")
        self.assertIsNone(shaper.build("!roll something long enough", None))
        self.assertEqual(shaper.build("/not a command here", None), "/not a command here")

    def test_skip_words(self):
        shaper = QueryShaper(skip_words=("ok", "thanks", "Lol"))
        for word in ("OK", "Thanks!", "lol"):
            self.assertIsNone(shaper.build(word, "prev"), word)
        self.assertIsNotNone(QueryShaper(skip_words=()).build("ok", "prev"))


if __name__ == "__main__":
    unittest.main()
