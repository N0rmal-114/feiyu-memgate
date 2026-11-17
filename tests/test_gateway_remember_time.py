import http.client
import json
import threading
import unittest

from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.gateway import server as http_server
from feiyu_memgate.gateway.app import Gateway
from feiyu_memgate.model import ClientPolicy

TOKEN = "tok-owner-fake-" + "0" * 20


class RememberTimeHttpTests(unittest.TestCase):
    """POST /v1/remember with a `time`, then POST /v1/recall returns that date."""

    def setUp(self):
        store = InMemoryBackend(writable=True)
        client = ClientPolicy("owner", recall=("private",), write="private")
        self.gw = Gateway({"private": store}, {"owner": client}, {"owner": TOKEN}, log=lambda line: None,
                          sweep_every_hours=0)
        self.addCleanup(self.gw.close)
        self.server = http_server.make_server(self.gw, "127.0.0.1", 0)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join(5)

        self.addCleanup(stop)
        self.port = self.server.server_address[1]

    def post(self, path, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("POST", path, body=json.dumps(body).encode(), headers={"Authorization": "Bearer " + TOKEN})
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read())
        finally:
            conn.close()

    def remember_then_recall(self, query, **message):
        status, _ = self.post("/v1/remember", {"messages": [dict({"role": "user", "content": query}, **message)]})
        self.assertEqual(status, 200)
        status, body = self.post("/v1/recall", {"query": query})
        self.assertEqual(status, 200)
        self.assertEqual(len(body["results"]), 1, body)
        return body["results"][0]

    def test_recall_returns_the_date_of_the_message(self):
        item = self.remember_then_recall("the mint on the balcony was cut twice", time="2026-10-27T08:30:00+08:00")
        self.assertEqual(item["date"], "2026-10-27")

    def test_a_space_separated_date_time_works_too(self):
        item = self.remember_then_recall("learning to bake tiramisu next month", time="2026-11-16 21:05")
        self.assertEqual(item["date"], "2026-11-16")

    def test_without_a_usable_time_there_is_no_invented_date(self):
        for time in ("last week", "2026-02-30"):
            item = self.remember_then_recall("a plain note about the garden hose " + time, time=time)
            self.assertIsNone(item.get("date"), time)
        item = self.remember_then_recall("another plain note about the shed")
        self.assertIsNone(item.get("date"))


if __name__ == "__main__":
    unittest.main()
