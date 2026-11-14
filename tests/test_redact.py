"""Redaction: total (never raises, never recurses) and covers the common secret forms.

Every secret below is obviously fake.
"""
import time
import unittest
from unittest import mock

from feiyu_memgate.core import redact as redact_module
from feiyu_memgate.core.redact import MARK, redact, redact_public

TIME_LIMIT = 5.0  # seconds for a 64 KiB adversarial input (a sane run takes well under one)


class TotalFunctionTest(unittest.TestCase):
    def test_many_query_parameters_do_not_recurse(self):
        text = "?" + "密码=&" * 843  # 3.4 KB, a legal message
        self.assertEqual(redact(text).count("\n"), 0)
        self.assertEqual(redact_public(text).count("\n"), 0)
        # and a long URL query that really holds secrets is cleaned all the way
        url = "https://x.example/cb?" + "&".join("token=syntheticABC%04d" % i for i in range(3000))
        out = redact(url)
        self.assertNotIn("synthetic", out)

    def test_adversarial_inputs_are_fast_and_safe(self):
        size = 64 * 1024
        cases = {
            "digits": "1" * size,
            "digit groups": ("1234567890123456 " * (size // 17)),
            "quoted": 'password="' + "a b " * (size // 4),
            "unterminated quotes": "token=\"x " * (size // 9),
            "escaped quotes": "secret=\\\"" + "\\\\\\\"" * (size // 5),
            "pairs": "key=value&" * (size // 10),
            "secret pairs": "token=abcdefgh " * (size // 15),
            "cjk pairs": "密码=&" * (size // 4),
            "cjk query": "?" + "密码=abcdefg&" * (size // 12),
            "bare heads": "password:" * (size // 9),
            "pem": "-----BEGIN PRIVATE KEY-----\n" + "A" * 64 + "\n" * (size // 65),
            "begin only": "-----BEGIN PRIVATE KEY----- " * (size // 28),
            "is": "密码是 " * (size // 4),
            "urls": "http://a:b" * (size // 10),
            "curl": "curl -u a:b " * (size // 12),
        }
        for name, text in cases.items():
            for fn in (redact, redact_public):
                started = time.monotonic()
                out = fn(text)
                elapsed = time.monotonic() - started
                self.assertIsInstance(out, str, name)
                self.assertLess(elapsed, TIME_LIMIT, "%s via %s took %.1fs" % (name, fn.__name__, elapsed))

    def test_any_failure_inside_gives_the_mark_never_a_raise(self):
        for error in (RecursionError(), MemoryError(), ValueError("x"), RuntimeError("x")):
            with mock.patch.object(redact_module, "_redact_key_values", side_effect=error):
                self.assertEqual(redact("a token=abcdefgh b"), MARK)
                self.assertEqual(redact_public("a token=abcdefgh b"), MARK)
        with mock.patch.object(redact_module, "_PUBLIC_RULES", (None,)):
            self.assertEqual(redact_public("hello"), MARK)  # a broken public rule still never leaks
        self.assertEqual(redact(None), MARK)  # not even a str: still no exception

    def test_line_count_is_kept(self):
        text = "a\npassword is hunter2hunter2\n-----BEGIN PRIVATE KEY-----\nAAAA\nBBBB\n-----END PRIVATE KEY-----\nz\n"
        for fn in (redact, redact_public):
            self.assertEqual(fn(text).count("\n"), text.count("\n"))


class NewSecretFormsTest(unittest.TestCase):
    def test_secret_word_is_value(self):
        for text in ("我的密码是 hunter2hunter2", "密码是hunter2hunter2", "密码为：hunter2hunter2",
                     "the password is hunter2hunter2", "Password IS hunter2hunter2", "口令是 correcthorse",
                     "my passphrase is correct-horse-battery"):
            out = redact(text)
            self.assertIn(MARK, out, text)
            self.assertNotIn("hunter2", out, text)
            self.assertNotIn("correcthorse", out, text)
            self.assertNotIn("battery", out, text)
        self.assertEqual(redact("我的密码是 hunter2hunter2"), "我的密码是 " + MARK)
        self.assertEqual(redact("password is hunter2hunter2 ok"), "password is " + MARK + " ok")

    def test_secret_word_is_prose_is_kept(self):
        for text in ("密码是什么", "密码是我的", "the password is short", "the password is required",
                     "this is a password manager", "password is", "token is wrong", "口令是对的",
                     "is the password right", "the secret is out"):
            self.assertEqual(redact(text), text, text)

    def test_pem_block(self):
        body = "\n".join(["MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC" + "x" * 13] * 3)
        for kind in ("PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "OPENSSH PRIVATE KEY"):
            block = "-----BEGIN %s-----\n%s\n-----END %s-----" % (kind, body, kind)
            text = "before\n" + block + "\nafter"
            for fn in (redact, redact_public):
                out = fn(text)
                self.assertNotIn("MIIEvQ", out)
                self.assertNotIn("BEGIN", out)
                self.assertTrue(out.startswith("before\n") and out.endswith("\nafter"))
                self.assertEqual(out.count("\n"), text.count("\n"))

    def test_pem_wrapped_in_64_character_lines_and_cut_off(self):
        data = "QUJD" * 100
        lines = [data[i:i + 64] for i in range(0, len(data), 64)]
        text = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(lines) + "\n-----END PRIVATE KEY-----\ntail"
        out = redact(text)
        self.assertNotIn("QUJD", out)
        self.assertEqual(out.count("\n"), text.count("\n"))
        self.assertTrue(out.endswith("tail"))
        cut = "x\n-----BEGIN PRIVATE KEY-----\n" + "\n".join(lines)  # no END line
        out = redact(cut)
        self.assertNotIn("QUJD", out)
        self.assertEqual(out.count("\n"), cut.count("\n"))

    def test_public_key_and_prose_about_keys_are_kept(self):
        for text in ("-----BEGIN PUBLIC KEY-----\nABCDEF\n-----END PUBLIC KEY-----",
                     "a private key is a secret number", "BEGIN and END words"):
            self.assertEqual(redact(text), text, text)

    def test_basic_authorization(self):
        for text in ("Authorization: Basic dXNlcjpzeW50aGV0aWM=", "authorization=basic dXNlcjpzeW50aGV0aWM=",
                     '-H "Authorization: Basic dXNlcjpzeW50aGV0aWM="'):
            out = redact(text)
            self.assertNotIn("dXNlcjp", out, text)
            self.assertIn(MARK, out)
        self.assertEqual(redact("Authorization: Basic dXNlcjpzeW50aGV0aWM=\nnext"),
                         "Authorization: Basic " + MARK + "\nnext")
        self.assertEqual(redact("basic knowledge of tea"), "basic knowledge of tea")
        self.assertEqual(redact("Authorization: Basic"), "Authorization: Basic")

    def test_credentials_in_urls(self):
        self.assertEqual(redact("clone https://alice:syntheticPW1@git.example/x.git now"),
                         "clone https://alice:" + MARK + "@git.example/x.git now")
        self.assertEqual(redact("postgres://app:p@ss:word@db.example:5432/d"), "postgres://app:" + MARK + "@db.example:5432/d")
        for text in ("https://git.example/x.git", "https://alice@git.example/x", "see http://host:8080/path",
                     "mail me at a@b.example", "ftp://user:@host/"):
            self.assertEqual(redact(text), text, text)

    def test_curl_user(self):
        self.assertEqual(redact("curl -u alice:syntheticPW1 https://x.example"),
                         "curl -u alice:" + MARK + " https://x.example")
        self.assertEqual(redact("curl --user alice:syntheticPW1 https://x.example"),
                         "curl --user alice:" + MARK + " https://x.example")
        self.assertEqual(redact("curl --user=alice:syntheticPW1 https://x.example"),
                         "curl --user=alice:" + MARK + " https://x.example")
        self.assertEqual(redact("curl -u alice https://x.example"), "curl -u alice https://x.example")
        self.assertEqual(redact("ls -u 12:30"), "ls -u 12:30")  # not a curl command


class ExistingBehaviourTest(unittest.TestCase):
    def test_query_values_stop_at_ampersand_and_hash(self):
        self.assertEqual(redact("https://x.example/cb?access_token=abcdef123456&state=xyz"),
                         "https://x.example/cb?access_token=" + MARK + "&state=xyz")
        self.assertEqual(redact("/cb?token=abc&password=syntheticABC123#frag"), "/cb?token=abc&password=" + MARK + "#frag")

    def test_plain_and_json_values(self):
        self.assertEqual(redact("token=abcdef123 x"), "token=" + MARK + " x")
        self.assertEqual(redact('{"token":12345678,"count":12}'), '{"token":' + MARK + ',"count":12}')
        self.assertEqual(redact("password: abc;def,ghi"), "password: " + MARK)


if __name__ == "__main__":
    unittest.main()
