import hashlib
import http.server
import json
import logging
import math
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from feiyu_memgate import registry
from feiyu_memgate.backends import embedding as embedding_module
from feiyu_memgate.backends.embedding import EmbeddingBackend
from feiyu_memgate.errors import BackendError
from feiyu_memgate.model import Hit

# A model without a preset logs one warning (tested below); the other tests use such a model and should not
# print it, so the logger gets a handler of its own.
logging.getLogger("feiyu_memgate.backends.embedding").addHandler(logging.NullHandler())

DIMS = 64
FAKE_KEY = "test-only-fake-key-0001"


def fake_vector(text, dims=DIMS):
    """Deterministic embedding: character unigrams and bigrams hashed into `dims` signed buckets,
    so texts that share wording really are closer (not unit length on purpose)."""
    vec = [0.0] * dims
    grams = list(text) + [text[i:i + 2] for i in range(len(text) - 1)]
    for gram in grams:
        digest = hashlib.md5(gram.encode("utf-8")).digest()
        vec[digest[0] % dims] += 1.0 if digest[1] % 2 else -1.0
    return vec


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def do_POST(self):
        server = self.server
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with server.lock:
            server.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
        mode = server.mode
        if mode == "slow":
            time.sleep(server.delay)
        if mode == "error":
            return self._send(500, b"{}")
        if mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if mode == "garbage":
            return self._send(200, b"not json")
        texts = body["input"]
        dims = server.dims
        data = [{"object": "embedding", "index": i, "embedding": fake_vector(t, dims)} for i, t in enumerate(texts)]
        if mode == "short":
            data = data[:-1]
        if mode == "reversed":
            data = data[::-1]
        self._send(200, json.dumps({"object": "list", "data": data}).encode("utf-8"))

    def _send(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # a client that gave up (deadline, cancel) is part of the tests, not noise


class FakeEmbeddingServer:
    """A throwaway OpenAI-style embeddings endpoint on 127.0.0.1 (random port)."""

    def __init__(self):
        self._srv = _QuietServer(("127.0.0.1", 0), _Handler)
        self._srv.daemon_threads = True
        self._srv.lock = threading.Lock()
        self._srv.requests = []
        self._srv.mode = "ok"
        self._srv.delay = 0.0
        self._srv.dims = DIMS
        self.url = "http://127.0.0.1:%d/v1" % self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    @property
    def requests(self):
        with self._srv.lock:
            return list(self._srv.requests)

    def set(self, mode="ok", delay=0.0, dims=DIMS):
        self._srv.mode, self._srv.delay, self._srv.dims = mode, delay, dims

    def stop(self):
        self._srv.shutdown()
        self._srv.server_close()


def closed_port_url():
    """A URL nothing listens on."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return "http://127.0.0.1:%d/v1" % port


class FakeCtl:
    def __init__(self):
        self.attached = 0
        self.detached = 0

    def attach(self, sock):
        self.attached += 1

    def detach(self):
        self.detached += 1


class EmbeddingTestBase(unittest.TestCase):
    def setUp(self):
        self.server = FakeEmbeddingServer()
        self.addCleanup(self.server.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "sub", "vec.db")

    def open(self, path=None, **options):
        options.setdefault("url", self.server.url)
        options.setdefault("model", "fake-model")
        be = EmbeddingBackend(path or self.path, **options)
        self.addCleanup(be.close)
        return be


class EmbeddingBackendTest(EmbeddingTestBase):
    def test_put_read_count_delete_items_refs(self):
        be = self.open()
        ref = be.put("I like green tea", date="2025-01-02")
        self.assertEqual(be.count(), 1)
        self.assertEqual(be.read(ref), "I like green tea")
        self.assertEqual(list(be.items()), [(ref, "I like green tea", "2025-01-02")])
        self.assertEqual(list(be.refs()), [(ref, "2025-01-02")])
        self.assertTrue(be.delete(ref))
        self.assertFalse(be.delete(ref))
        self.assertEqual(be.count(), 0)
        self.assertEqual(be.find("green tea", 5), [])
        with self.assertRaises(KeyError):
            be.read(ref)

    def test_parent_directories_created_and_memory_path(self):
        self.open()
        self.assertTrue(os.path.isfile(self.path))
        be = self.open(":memory:")
        be.put("x y z", ref="a")
        self.assertEqual(be.count(), 1)

    def test_put_replaces_an_existing_ref(self):
        be = self.open()
        be.put("old wording about tea", ref="r")
        be.put("new wording about coffee", ref="r")
        self.assertEqual(be.count(), 1)
        self.assertEqual(be.read("r"), "new wording about coffee")
        self.assertEqual(be.find("new wording about coffee", 1)[0].ref, "r")

    def test_find_ranks_related_text_first_and_maps_scores(self):
        be = self.open()
        be.put("我喜欢喝燕麦拿铁咖啡", ref="coffee")
        be.put("周末去游泳馆游泳", ref="swim")
        hits = be.find("我喜欢喝燕麦拿铁咖啡", 5)
        self.assertEqual(hits[0].ref, "coffee")
        self.assertAlmostEqual(hits[0].score, 1.0)  # identical text: cosine 1 is above ceil, clamped
        self.assertTrue(all(0.0 <= h.score <= 1.0 for h in hits))
        self.assertTrue(all(h.text for h in hits))
        near = be.find("喜欢喝燕麦拿铁", 5)
        self.assertEqual(near[0].ref, "coffee")
        self.assertLess(near[0].score, 1.0)

    def test_score_mapping_uses_floor_and_ceil(self):
        be = self.open(floor=0.0, ceil=1.0)
        be.put("tea with milk please", ref="a")
        be.put("tea and honey", ref="b")
        hits = be.find("tea with milk", 5)
        by_ref = {h.ref: h for h in hits}
        if hasattr(Hit, "__dataclass_fields__") and "meta" in Hit.__dataclass_fields__:
            for h in hits:
                self.assertAlmostEqual(h.score, min(1.0, max(0.0, h.meta["cosine"])), places=6)
        self.assertGreater(by_ref["a"].score, by_ref["b"].score)
        # A narrow band: the same cosines map to higher scores, never above 1.
        narrow = self.open(":memory:", floor=0.0, ceil=0.5)
        narrow.put("tea with milk please", ref="a")
        self.assertAlmostEqual(narrow.find("tea with milk", 1)[0].score, 1.0)

    def test_unrelated_memories_below_floor_are_left_out(self):
        be = self.open(floor=0.5, ceil=0.9)
        be.put("aaaaaaaaaaaa", ref="a")
        be.put("zzzzzzzzzzzz", ref="z")
        refs = [h.ref for h in be.find("aaaaaaaaaaaa", 5)]
        self.assertEqual(refs, ["a"])

    def test_default_min_score_and_options_validation(self):
        self.assertEqual(EmbeddingBackend.default_min_score, 0.50)
        self.assertEqual(EmbeddingBackend.default_relative_ratio, 0.85)
        be = self.open()
        self.assertEqual((be.floor, be.ceil), (0.40, 0.80))
        for bad in ({"floor": 0.8, "ceil": 0.3}, {"timeout": 0}, {"model": ""}, {"url": "ftp://x"},
                    {"url": "http:///v1"}, {"key": 5}):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.open(":memory:", **bad)

    def test_vectors_are_normalised_at_write_time(self):
        be = self.open()
        be.put("some text to embed", ref="a")
        be.find("some text", 1)
        vec = be._vectors["a"]
        self.assertAlmostEqual(math.sqrt(sum(x * x for x in vec)), 1.0, places=5)
        self.assertEqual(len(vec), DIMS)

    def test_put_many_batches_requests(self):
        be = self.open(batch=32)
        refs = be.put_many(["memory number %d about topic %d" % (i, i % 7) for i in range(70)])
        self.assertEqual(len(refs), 70)
        self.assertEqual(len(set(refs)), 70)
        self.assertEqual([len(r["body"]["input"]) for r in self.server.requests], [32, 32, 6])
        self.assertEqual(be.count(), 70)

    def test_put_many_item_shapes_and_validation(self):
        be = self.open()
        refs = be.put_many(["plain", ("tuple text", "t1"), {"text": "dict text", "ref": "d1", "date": "2025-02-03"},
                            ("tuple with date", "t2", "2025-04-05")])
        self.assertEqual(refs[1:], ["t1", "d1", "t2"])
        self.assertEqual(dict((r, d) for r, d in be.refs())["d1"], "2025-02-03")
        before = len(self.server.requests)
        with self.assertRaises(ValueError):
            be.put_many(["fine text", "   "])
        self.assertEqual(len(self.server.requests), before)  # rejected before any request
        self.assertEqual(be.put_many([]), [])

    def test_put_many_failure_leaves_store_unchanged(self):
        be = self.open(batch=2)
        self.server.set("error")
        with self.assertRaises(BackendError):
            be.put_many(["one", "two", "three"])
        self.assertEqual(be.count(), 0)

    def test_request_shape_and_key(self):
        be = self.open(key=FAKE_KEY)
        be.put("hello there", ref="a")
        request = self.server.requests[0]
        self.assertEqual(request["path"], "/v1/embeddings")
        self.assertEqual(request["body"], {"model": "fake-model", "input": ["hello there"]})
        self.assertEqual(request["auth"], "Bearer " + FAKE_KEY)
        anon = self.open(":memory:")
        anon.put("hello there", ref="a")
        self.assertIsNone(self.server.requests[-1]["auth"])

    def test_response_in_any_index_order_is_matched_to_inputs(self):
        be = self.open()
        self.server.set("reversed")
        be.put_many(["alpha alpha alpha", "omega omega omega"], )
        self.server.set("ok")
        self.assertEqual(be.find("alpha alpha alpha", 1)[0].text, "alpha alpha alpha")

    def test_query_embedding_is_cached_and_cache_is_bounded(self):
        be = self.open()
        be.put("cached query text", ref="a")
        n = len(self.server.requests)
        be.find("cached query text", 3)
        be.find("cached query text", 3)
        be.find("cached query text", 3)
        self.assertEqual(len(self.server.requests) - n, 1)
        for i in range(300):
            be._query_vector("query %d" % i, 5.0, None)
        self.assertLessEqual(len(be._qcache), 256)

    def test_persistence_across_reopen_does_not_re_embed(self):
        be = self.open()
        be.put("我最喜欢的饮料是乌龙茶", ref="zh", date="2025-03-04")
        be.close()
        n = len(self.server.requests)
        again = self.open()
        hits = again.find("我最喜欢的饮料是乌龙茶", 3)
        self.assertEqual(hits[0].ref, "zh")
        self.assertEqual(hits[0].date, "2025-03-04")
        self.assertEqual(len(self.server.requests) - n, 1)  # only the query was embedded

    def test_store_remembers_its_model_and_size(self):
        be = self.open()
        be.put("text", ref="a")
        be.close()
        with self.assertRaises(ValueError):
            EmbeddingBackend(self.path, url=self.server.url, model="another-model")
        again = self.open()
        self.server.set("ok", dims=32)
        with self.assertRaises(BackendError):
            again.find("text", 1)
        with self.assertRaises(BackendError):
            again.put("more text")

    def test_a_write_by_another_connection_is_seen(self):
        a = self.open()
        b = self.open()
        a.put("first memory here", ref="a")
        self.assertEqual([h.ref for h in a.find("first memory here", 3)], ["a"])
        b.put("second memory there", ref="b")
        self.assertEqual(a.find("second memory there", 3)[0].ref, "b")

    def test_own_writes_and_deletes_keep_the_cache_in_step(self):
        be = self.open()
        be.put("alpha beta gamma", ref="a")
        be.find("alpha beta gamma", 1)  # cache loaded
        be.put("delta epsilon zeta", ref="b")
        self.assertEqual(be.find("delta epsilon zeta", 1)[0].ref, "b")
        be.delete("b")
        self.assertNotIn("b", [h.ref for h in be.find("delta epsilon zeta", 3)])

    def test_remember_roles_and_receipt_refs(self):
        be = self.open()
        receipt = be.remember([
            {"role": "user", "content": "I adopted a cat named Dango"},
            {"role": "assistant", "content": "Nice to hear that"},
            {"role": "user", "content": "She likes tuna"},
        ])
        self.assertIsInstance(receipt, str)
        refs = be.receipt_refs(receipt)
        self.assertEqual(len(refs), 2)
        self.assertEqual({be.read(r) for r in refs}, {"I adopted a cat named Dango", "She likes tuna"})
        self.assertEqual(dict(be.refs()).keys(), set(refs))
        self.assertTrue(all(d for _, d in be.refs()))
        self.assertIsNone(be.remember([{"role": "assistant", "content": "only assistant"}]))
        self.assertEqual(be.count(), 2)
        self.assertEqual(be.receipt_refs("w-unknown"), [])
        self.assertIsNone(be.receipt_refs(""))
        only_assistant = self.open(":memory:", roles=("assistant",))
        self.assertIsNotNone(only_assistant.remember([{"role": "assistant", "content": "kept now"}]))

    def test_read_only_refuses_writes_but_searches(self):
        writer = self.open()
        writer.put("shared memory text", ref="a")
        writer.close()
        ro = self.open(writable=False)
        self.assertEqual(ro.find("shared memory text", 1)[0].ref, "a")
        n = len(self.server.requests)
        with self.assertRaises(PermissionError):
            ro.put("x")
        with self.assertRaises(PermissionError):
            ro.put_many(["x"])
        with self.assertRaises(PermissionError):
            ro.delete("a")
        with self.assertRaises(PermissionError):
            ro.remember([{"role": "user", "content": "x"}])
        self.assertEqual(len(self.server.requests) - n, 0 + 0)

    def test_health_does_not_call_the_endpoint(self):
        be = self.open(url=closed_port_url())
        self.assertTrue(be.health())
        self.assertEqual(self.server.requests, [])
        be.close()
        self.assertFalse(be.health())

    def test_unreachable_endpoint_is_a_backend_error_not_a_hang(self):
        be = self.open(url=closed_port_url(), timeout=1.0)
        start = time.monotonic()
        with self.assertRaises(BackendError) as cm:
            be.find("anything", 3)
        with self.assertRaises(BackendError):
            be.put("anything")
        self.assertLess(time.monotonic() - start, 8.0)  # a refused connect takes about 2 s on Windows
        self.assertNotIn("anything", str(cm.exception))

    def test_total_deadline_cuts_a_slow_endpoint(self):
        be = self.open(timeout=5.0)
        self.server.set("slow", delay=3.0)
        start = time.monotonic()
        with self.assertRaises(BackendError):
            be.find("slow question", 3, timeout=0.4)
        self.assertLess(time.monotonic() - start, 2.0)
        with self.assertRaises(BackendError):
            be.find("slow question", 3, timeout=0)

    def test_bad_answers_are_backend_errors_without_upstream_text(self):
        be = self.open(key=FAKE_KEY)
        for mode in ("error", "redirect", "garbage", "short"):
            self.server.set(mode)
            with self.assertRaises(BackendError) as cm:
                be.find("secret question text", 3)
            self.assertNotIn("secret question text", str(cm.exception))
            self.assertNotIn(FAKE_KEY, str(cm.exception))
        self.assertEqual(sum(1 for r in self.server.requests if r["path"] != "/v1/embeddings"), 0)

    def test_ctl_is_attached_and_detached_and_optional(self):
        be = self.open()
        ctl = FakeCtl()
        be.put_many(["one text", "two text"], ctl=ctl)
        be.find("one text", 2, ctl=ctl)
        self.assertEqual(ctl.attached, 2)
        self.assertEqual(ctl.detached, 2)
        be.find("two text", 2, ctl=object())  # a ctl without the methods is tolerated
        be.find("three text", 2, ctl=None)

    def test_cancelled_ctl_stops_the_call(self):
        class Cancelled:
            def attach(self, sock):
                raise BackendError("cancelled")

            def detach(self):
                pass

        be = self.open()
        with self.assertRaises(BackendError):
            be.find("q", 1, ctl=Cancelled())

    def test_blank_query_and_empty_store(self):
        be = self.open()
        self.assertEqual(be.find("   ", 3), [])
        self.assertEqual(be.find("nothing stored yet", 3), [])

    def test_registry_builds_it(self):
        be = registry.create("backend", {"type": "embedding", "path": ":memory:", "url": self.server.url,
                                         "model": "fake-model"})
        self.addCleanup(be.close)
        self.assertIsInstance(be, EmbeddingBackend)
        with self.assertRaises(Exception):
            registry.create("backend", {"type": "embedding", "path": ":memory:"})  # url and model missing

    def test_thread_safety_smoke(self):
        be = self.open()
        be.put_many(["seed memory %d about things" % i for i in range(20)])
        errors = []

        def worker(n):
            try:
                for i in range(15):
                    be.put("thread %d memory %d" % (n, i), ref="t%d-%d" % (n, i))
                    be.find("seed memory %d about things" % (i % 20), 3)
                    if i % 5 == 0:
                        be.delete("t%d-%d" % (n, i))
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(be.count(), 20 + 6 * 15 - 6 * 3)
        self.assertEqual(len(be._vectors), be.count())


class PresetAndPrefixTest(EmbeddingTestBase):
    def setUp(self):
        super().setUp()
        saved = set(embedding_module._warned)
        embedding_module._warned.clear()
        self.addCleanup(lambda: (embedding_module._warned.clear(), embedding_module._warned.update(saved)))

    def test_every_preset_model_gets_its_own_numbers(self):
        expected = {
            "bge-m3": (0.40, 0.80, 0.50, 0.85),
            "bge-m3:latest": (0.40, 0.80, 0.50, 0.85),
            "hf.co/CompendiumLabs/bge-small-zh-v1.5-gguf:F16": (0.40, 0.80, 0.30, 0.90),
            "hf.co/CompendiumLabs/bge-base-zh-v1.5-gguf:Q8_0": (0.35, 0.80, 0.25, 0.75),
            "embeddinggemma": (0.30, 0.75, 0.55, 0.85),
            "embeddinggemma:300m": (0.30, 0.75, 0.55, 0.85),
            "qwen3-embedding:0.6b": (0.35, 0.80, 0.20, 0.95),
        }
        for model, numbers in expected.items():
            with self.assertNoLogs("feiyu_memgate.backends.embedding", "WARNING"):
                be = self.open(":memory:", model=model)
            self.assertEqual((be.floor, be.ceil, be.default_min_score, be.default_relative_ratio), numbers, model)
        # per instance: opening one model does not change another, nor the class values
        self.assertEqual((EmbeddingBackend.default_min_score, EmbeddingBackend.default_relative_ratio), (0.50, 0.85))

    def test_recommended_prefixes_of_the_presets(self):
        base = self.open(":memory:", model="bge-base-zh-v1.5")
        self.assertTrue(base._query_prefix.startswith("为这个句子生成表示"))
        self.assertEqual(base._doc_prefix, "")
        qwen = self.open(":memory:", model="qwen3-embedding:0.6b")
        self.assertTrue(qwen._query_prefix.startswith("Instruct: ") and qwen._query_prefix.endswith("\nQuery:"))
        for model in ("bge-m3", "bge-small-zh-v1.5", "embeddinggemma"):
            plain = self.open(":memory:", model=model)
            self.assertEqual((plain._query_prefix, plain._doc_prefix), ("", ""), model)

    def test_unknown_model_warns_once_without_text_or_key(self):
        with self.assertLogs("feiyu_memgate.backends.embedding", "WARNING") as logs:
            self.open(":memory:", model="some-new-model", key=FAKE_KEY)
            self.open(":memory:", model="some-new-model")
        self.assertEqual(len(logs.records), 1)
        message = logs.records[0].getMessage()
        self.assertIn("calibrate", message)
        self.assertNotIn(FAKE_KEY, message)
        be = self.open(":memory:", model="some-new-model")
        self.assertEqual((be.floor, be.ceil, be.default_min_score, be.default_relative_ratio), (0.40, 0.80, 0.50, 0.85))

    def test_explicit_floor_and_ceil_mean_the_operator_calibrated(self):
        with self.assertNoLogs("feiyu_memgate.backends.embedding", "WARNING"):
            be = self.open(":memory:", model="another-new-model", floor=0.2, ceil=0.6)
        self.assertEqual((be.floor, be.ceil), (0.2, 0.6))

    def test_explicit_options_override_the_preset(self):
        be = self.open(":memory:", model="bge-base-zh-v1.5", floor=0.5, ceil=0.9, query_prefix="", document_prefix="D:")
        self.assertEqual((be.floor, be.ceil, be._query_prefix, be._doc_prefix), (0.5, 0.9, "", "D:"))
        with self.assertRaises(ValueError):
            self.open(":memory:", model="bge-m3", floor=0.9)  # the preset's ceil 0.80 is not above it

    def test_prefixes_are_sent_but_never_stored_or_returned(self):
        be = self.open(model="m", query_prefix="Q: ", document_prefix="D: ")
        be.put("hello there friend", ref="a")
        be.put_many(["second memory", ("third memory", "c")])
        be.remember([{"role": "user", "content": "remembered words"}])
        inputs = [r["body"]["input"] for r in self.server.requests]
        self.assertEqual(inputs, [["D: hello there friend"], ["D: second memory", "D: third memory"],
                                  ["D: remembered words"]])
        hits = be.find("hello there friend", 1)
        self.assertEqual(self.server.requests[-1]["body"]["input"], ["Q: hello there friend"])
        self.assertEqual((hits[0].ref, hits[0].text), ("a", "hello there friend"))
        self.assertEqual(be.read("a"), "hello there friend")
        self.assertEqual(be.find("hello there friend", 1)[0].ref, "a")  # the cache is keyed by the raw query
        self.assertEqual(len([r for r in self.server.requests if r["body"]["input"] == ["Q: hello there friend"]]), 1)

    def test_prefix_changes_the_vector(self):
        a = self.open(":memory:", model="m")
        b = self.open(":memory:", model="m", document_prefix="passage: ")
        a.put("identical words", ref="x")
        b.put("identical words", ref="x")
        a.find("identical words", 1)
        b.find("identical words", 1)
        self.assertNotEqual(list(a._vectors["x"]), list(b._vectors["x"]))

    def test_store_remembers_its_document_prefix_like_its_model(self):
        be = self.open(model="m", document_prefix="D: ")
        be.put("some memory", ref="a")
        be.close()
        for other in ("", "E: "):
            with self.assertRaises(ValueError) as caught:
                EmbeddingBackend(self.path, url=self.server.url, model="m", document_prefix=other)
            self.assertNotIn("D:", str(caught.exception))
        again = self.open(model="m", document_prefix="D: ")
        self.assertEqual(again.count(), 1)
        # a query prefix is not part of the store: it may change between runs
        self.open(model="m", document_prefix="D: ", query_prefix="Q: ")

    def test_a_store_without_the_record_was_built_without_a_prefix(self):
        be = self.open(model="m")
        be.put("old memory", ref="a")
        be.close()
        import sqlite3
        db = sqlite3.connect(self.path)
        db.execute("DELETE FROM info WHERE key = 'document_prefix'")  # what a store from before the option looks like
        db.commit()
        db.close()
        with self.assertRaises(ValueError):
            EmbeddingBackend(self.path, url=self.server.url, model="m", document_prefix="D: ")
        self.open(model="m")  # no prefix: fine, and read-only too
        self.open(model="m", writable=False)

    def test_an_empty_store_takes_the_first_prefix_it_is_given(self):
        self.open(model="m").close()
        be = self.open(model="m", document_prefix="D: ")
        be.put("memory", ref="a")
        be.close()
        with self.assertRaises(ValueError):
            EmbeddingBackend(self.path, url=self.server.url, model="m")

    def test_prefix_validation(self):
        for bad in ({"query_prefix": 5}, {"document_prefix": None.__class__}, {"query_prefix": "x" * 513}):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.open(":memory:", model="m", **bad)


class NeighboursTest(EmbeddingTestBase):
    """neighbours(ref, limit): the stored memories most similar to a stored memory, from the stored vectors."""

    TEXTS = {
        "tea": "我喜欢喝绿茶和红茶",
        "tea2": "我喜欢喝绿茶还有乌龙茶",
        "swim": "周末去游泳馆游泳",
        "swim2": "周末去游泳馆游泳放松",
        "code": "写代码调试程序",
    }

    def filled(self, **options):
        options.setdefault("floor", -1.0)  # the fake vectors are far apart: map the whole cosine range
        options.setdefault("ceil", 1.0)
        be = self.open(**options)
        for ref, text in self.TEXTS.items():
            be.put(text, ref=ref, date="2025-01-0%d" % (len(ref) % 9 + 1))
        return be

    def test_best_first_without_the_memory_itself_and_with_text_and_date(self):
        be = self.filled()
        hits = be.neighbours("tea", 10)
        self.assertEqual(hits[0].ref, "tea2")
        self.assertEqual({h.ref for h in hits}, set(self.TEXTS) - {"tea"})
        self.assertEqual([h.score for h in hits], sorted((h.score for h in hits), reverse=True))
        self.assertEqual(hits[0].text, self.TEXTS["tea2"])
        self.assertTrue(hits[0].date)
        self.assertTrue(all(isinstance(h, Hit) and 0.0 <= h.score <= 1.0 for h in hits))
        self.assertEqual(be.neighbours("swim", 1)[0].ref, "swim2")

    def test_scores_are_the_scores_find_gives_for_the_memorys_own_text(self):
        be = self.filled(floor=0.0, ceil=1.0)  # a model without prefixes: the stored vector is the query vector
        for ref in ("tea", "swim"):
            found = {h.ref: h.score for h in be.find(self.TEXTS[ref], 10)}
            for hit in be.neighbours(ref, 10):
                self.assertAlmostEqual(hit.score, found[hit.ref], places=6)
        if "meta" in Hit.__dataclass_fields__:
            for hit in be.neighbours("tea", 3):
                self.assertAlmostEqual(hit.score, min(1.0, max(0.0, hit.meta["cosine"])), places=6)

    def test_no_embeddings_request_is_made(self):
        be = self.filled()
        before = len(self.server.requests)
        be.neighbours("tea", 3)
        self.server.set("error")  # the endpoint is down: neighbours does not need it
        self.assertEqual(len(be.neighbours("tea", 3)), 3)
        self.assertEqual(len(self.server.requests), before)

    def test_limit_and_unknown_ref(self):
        be = self.filled()
        self.assertEqual(len(be.neighbours("tea", 2)), 2)
        self.assertEqual(len(be.neighbours("tea", 100)), len(self.TEXTS) - 1)
        self.assertEqual(be.neighbours("tea", 0), [])
        self.assertEqual(be.neighbours("tea", -1), [])
        self.assertEqual(be.neighbours("no-such-ref", 5), [])
        self.assertEqual(self.open(":memory:").neighbours("tea", 5), [])  # an empty store

    def test_a_memory_at_or_below_the_floor_is_left_out_like_find_does(self):
        be = self.filled(floor=0.9, ceil=1.0)
        refs = [h.ref for h in be.neighbours("tea", 10)]
        self.assertNotIn("code", refs)
        self.assertTrue(all(h.score > 0.0 for h in be.neighbours("tea", 10)))

    def test_a_deleted_or_replaced_memory_is_seen(self):
        be = self.filled()
        self.assertTrue(be.delete("tea2"))
        self.assertNotIn("tea2", [h.ref for h in be.neighbours("tea", 10)])
        self.assertEqual(be.neighbours("tea2", 10), [])
        be.put("我喜欢喝绿茶和红茶还有花茶", ref="tea3")
        self.assertEqual(be.neighbours("tea", 1)[0].ref, "tea3")

    def test_a_read_only_store_answers(self):
        self.filled().close()
        be = self.open(writable=False, floor=-1.0, ceil=1.0)
        self.assertFalse(be.writable)
        self.assertEqual(be.neighbours("tea", 1)[0].ref, "tea2")

    def test_what_another_connection_wrote_is_seen(self):
        be = self.filled()
        be.neighbours("tea", 1)  # loads the cache
        other = self.open(floor=-1.0, ceil=1.0)
        other.put("我喜欢喝绿茶和红茶和黑茶", ref="tea9")
        self.assertIn("tea9", [h.ref for h in be.neighbours("tea", 10)])

    def test_the_deadline_stops_the_scan(self):
        be = self.filled()
        with self.assertRaises(BackendError):
            be.neighbours("tea", 3, timeout=0)
        be.neighbours("tea", 3, timeout=5.0)
        with mock.patch.object(embedding_module, "SCORE_CHECK_EVERY", 1),                 mock.patch.object(embedding_module.time, "monotonic", side_effect=self.ticks(3)):
            with self.assertRaises(BackendError):
                be.neighbours("tea", 3, timeout=1.0)

    @staticmethod
    def ticks(first_calls):
        """A clock that moves 10 s forward after `first_calls` readings."""
        state = {"n": 0}

        def clock():
            state["n"] += 1
            return 0.0 if state["n"] <= first_calls else 10.0
        return clock

    def test_a_cancelled_call_stops(self):
        class Cancelled:
            cancelled = True

        be = self.filled()
        with self.assertRaises(BackendError):
            be.neighbours("tea", 3, ctl=Cancelled())
        self.assertEqual(len(be.neighbours("tea", 3, ctl=object())), 3)  # a ctl without the flag is tolerated
        self.assertEqual(len(be.neighbours("tea", 3, ctl=None)), 3)


if __name__ == "__main__":
    unittest.main()
