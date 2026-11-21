"""A write of several messages through the gateway is all or nothing (sqlite and in-memory stores):
a failure in the middle answers 502, stores nothing, and a retry stores each message once."""
import time
import unittest
from unittest import mock

from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.backends.sqlite import SQLiteBackend
from feiyu_memgate.core.tidy import index_grams
from feiyu_memgate.gateway.app import Gateway
from feiyu_memgate.model import ClientPolicy

BODY = {"messages": [{"role": "user", "content": "memory number %d about tea" % i} for i in range(5)]}


class AtomicWriteThroughGatewayTest(unittest.TestCase):
    def setUp(self):
        self.logs = []

    def gateway(self, store):
        policy = ClientPolicy("o", recall=("s",), write="s")
        gw = Gateway({"s": store}, {"o": policy}, {"o": None}, guards=[], log=self.logs.append,
                     clock=time.monotonic)
        self.addCleanup(gw.close)
        return gw

    def failing_third_once(self):
        real = index_grams
        calls = []

        def build(*args, **kw):
            calls.append(1)
            if len(calls) == 3:
                raise ValueError("cannot index the third message")
            return real(*args, **kw)

        return build

    def check(self, store, module):
        self.addCleanup(store.close) if hasattr(store, "close") else None
        gw = self.gateway(store)
        with mock.patch("feiyu_memgate.backends.%s.index_grams" % module, self.failing_third_once()):
            reply = gw.remember("o", BODY)
        self.assertEqual(reply.status, 502)
        self.assertEqual(reply.body["code"], "backend_unavailable")
        self.assertEqual(list(store.refs()), [])
        self.assertEqual(store.find("tea", 20), [])
        retry = gw.remember("o", BODY)  # the same messages again
        self.assertEqual(retry.status, 200)
        refs = list(store.refs())
        self.assertEqual(len(refs), 5)
        texts = sorted(store.read(ref) for ref, _date in refs)
        self.assertEqual(texts, sorted(m["content"] for m in BODY["messages"]))

    def test_sqlite_failure_in_the_middle_then_retry(self):
        self.check(SQLiteBackend(":memory:"), "sqlite")

    def test_inmemory_failure_in_the_middle_then_retry(self):
        self.check(InMemoryBackend(), "inmemory")


if __name__ == "__main__":
    unittest.main()
