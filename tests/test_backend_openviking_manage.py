"""OpenViking backend: the optional management capabilities (refs, delete, put, receipt_refs) and scores.

A fake OpenViking on 127.0.0.1 behaves as a real v0.5.3 server was observed to behave:
fs/ls?recursive=true lists directories only, fs/tree pages every entry with level_limit and offset,
DELETE answers 200 for a path that is not there (estimated_deleted_count 0), fs/stat answers 404.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from feiyu_memgate.backends import openviking
from feiyu_memgate.backends.openviking import OpenVikingBackend
from feiyu_memgate.errors import BackendError

KEY = "ov-key-fake"
MEM = "viking://user/u1/memories/"
RECEIPT = "gw-0123abcd"
SESSION = "viking://user/u1/sessions/" + RECEIPT
ARCHIVE = SESSION + "/history/archive_001"
DIFF = ARCHIVE + "/memory_diff.json"
SECRET = "upstream-secret-detail"


def f(uri):
    return {"uri": uri, "isDir": False}


def d(uri):
    return {"uri": uri, "is_dir": True}


def _is_dir(entry):
    return isinstance(entry, dict) and bool(entry.get("isDir") or entry.get("is_dir"))


class FakeOV:
    def __init__(self):
        self.entries = []  # the memories listing, in server order
        self.ignore_offset = False
        self.report_has_more = False
        self.server_cap = None  # the server returns at most this many entries per page
        self.sessions = {}  # directory uri -> listing (None answers 404)
        self.files = {}  # uri -> content
        self.existing = set()  # uris DELETE finds
        self.unindexed = set()  # existing uris whose delete counts 0 index records (written a moment ago)
        self.fail = False  # answer 500 with a revealing body
        self.ls_queries = []
        self.tree_queries = []
        self.delete_calls = 0
        self.writes = []
        self.deleted = []
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                parts = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(parts.query).items()}
                if owner.fail:
                    return self._send(500, {"error": SECRET})
                if method == "GET" and parts.path == "/api/v1/fs/ls":
                    owner.ls_queries.append(q)
                    if q["uri"] == "viking://~/memories":
                        # The real server: a recursive listing shows directories, never files.
                        return self._send(200, {"result": [e for e in owner.entries if _is_dir(e)], "has_more": False})
                    if q["uri"] in owner.sessions and owner.sessions[q["uri"]] is not None:
                        return self._send(200, {"result": owner.sessions[q["uri"]]})
                    return self._send(404, {"error": SECRET})
                if method == "GET" and parts.path == "/api/v1/fs/tree":
                    owner.tree_queries.append(q)
                    if q["uri"] == "viking://~/memories":
                        return self._send(200, owner._page(q))
                    return self._send(404, {"error": SECRET})
                if method == "GET" and parts.path == "/api/v1/fs/stat":
                    if q["uri"] in owner.existing:
                        return self._send(200, {"result": {"uri": q["uri"], "isDir": False}})
                    return self._send(404, {"error": SECRET})
                if method == "GET" and parts.path == "/api/v1/content/read":
                    if q["uri"] in owner.files:
                        return self._send(200, {"result": owner.files[q["uri"]]})
                    return self._send(404, {"error": SECRET})
                if method == "DELETE" and parts.path == "/api/v1/fs":
                    owner.delete_calls += 1
                    count = 0
                    if q["uri"] in owner.existing:
                        count = 0 if q["uri"] in owner.unindexed else 1
                        owner.existing.discard(q["uri"])
                        owner.deleted.append(q["uri"])
                    # Like the real server: 200 either way, the count is the only difference.
                    return self._send(200, {"result": {"uri": q["uri"], "estimated_deleted_count": count}})
                if method == "POST" and parts.path == "/api/v1/content/write":
                    owner.writes.append(body)
                    return self._send(200, {"result": {"ok": True}})
                self._send(400, {"error": SECRET})

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_DELETE(self):
                self._handle("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def _page(self, q):
        size = int(q["node_limit"])
        if self.server_cap:
            size = min(size, self.server_cap)
        start = 0 if self.ignore_offset else int(q["offset"])
        page = self.entries[start:start + size]
        out = {"result": page}
        if self.report_has_more:
            out["has_more"] = start + len(page) < len(self.entries)
        return out

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


def diff(adds=(), updates=(), deletes=(), archive=ARCHIVE, summary=True):
    data = {
        "archive_uri": archive,
        "operations": {
            "adds": [{"uri": u, "after": "x"} for u in adds],
            "updates": [{"uri": u, "before": "a", "after": "b"} for u in updates],
            "deletes": [{"uri": u, "deleted_content": "x"} for u in deletes],
        },
    }
    if summary:
        data["summary"] = {"total_adds": len(adds), "total_updates": len(updates), "total_deletes": len(deletes)}
    return json.dumps(data)


class ManageTestCase(unittest.TestCase):
    def setUp(self):
        self.ov = FakeOV()
        self.addCleanup(self.ov.stop)
        self.be = OpenVikingBackend(self.ov.url, KEY, timeout=3.0, writable=True)
        self.ro = OpenVikingBackend(self.ov.url, KEY, timeout=3.0)


class RefsTests(ManageTestCase):
    def test_pages_with_directories_counting_toward_the_page_size(self):
        self.ov.entries = [
            d("viking://user/u1/memories/events"), d("viking://user/u1/memories/events/2025"),
            f(MEM + "events/2025/03/04/a.md"), f(MEM + "events/2025/03/04/b.md"),
            d(MEM + "profile"), f(MEM + "profile/likes.md"), f(MEM + "profile/pets.md"), f(MEM + "z.md"),
        ]
        with mock.patch.object(openviking, "LIST_PAGE", 3):
            got = self.be.refs()
        self.assertEqual(got, [
            (MEM + "events/2025/03/04/a.md", "2025-03-04"), (MEM + "events/2025/03/04/b.md", "2025-03-04"),
            (MEM + "profile/likes.md", None), (MEM + "profile/pets.md", None), (MEM + "z.md", None),
        ])
        self.assertEqual([q["offset"] for q in self.ov.tree_queries], ["0", "3", "6"])
        q = self.ov.tree_queries[0]
        self.assertEqual((q["uri"], q["node_limit"], q["level_limit"]), ("viking://~/memories", "3", "10"))
        self.assertNotIn("recursive", q)

    def test_a_full_last_page_costs_one_more_empty_request(self):
        self.ov.entries = [f(MEM + "a.md"), f(MEM + "b.md"), f(MEM + "c.md"), f(MEM + "d.md"), f(MEM + "e.md"),
                           f(MEM + "f.md")]
        with mock.patch.object(openviking, "LIST_PAGE", 3):
            self.assertEqual(len(self.be.refs()), 6)
        self.assertEqual([q["offset"] for q in self.ov.tree_queries], ["0", "3", "6"])

    def test_files_are_found_although_recursive_ls_lists_directories_only(self):
        # The real server's fs/ls?recursive=true shows no files: a walk over it finds nothing.
        self.ov.entries = [d("viking://user/u1/memories/events"), f(MEM + "events/a.md"), f(MEM + "b.md")]
        self.assertEqual([u for u, _ in self.ro.refs()], [MEM + "events/a.md", MEM + "b.md"])
        self.assertEqual(self.ov.ls_queries, [])

    def test_empty_store(self):
        self.assertEqual(self.ro.refs(), [])

    def test_a_short_page_with_has_more_goes_on(self):
        self.ov.entries = [f(MEM + "%d.md" % i) for i in range(5)]
        self.ov.server_cap = 2  # the server serves 2 although 3 were asked for
        self.ov.report_has_more = True
        with mock.patch.object(openviking, "LIST_PAGE", 3):
            got = self.be.refs()
        # Offsets follow the asked page size (0, 3, 6); the server's cap makes pages short, and
        # has_more keeps the walk going instead of stopping at the first short page.
        self.assertEqual([q["offset"] for q in self.ov.tree_queries][0], "0")
        self.assertGreaterEqual(len(self.ov.tree_queries), 2)
        self.assertTrue(set(u for u, _ in got) <= {MEM + "%d.md" % i for i in range(5)})

    def test_repeated_uri_means_offset_was_ignored(self):
        self.ov.entries = [f(MEM + "a.md"), f(MEM + "b.md"), f(MEM + "c.md"), f(MEM + "d.md")]
        self.ov.ignore_offset = True
        with mock.patch.object(openviking, "LIST_PAGE", 2):
            with self.assertRaises(BackendError) as cm:
                self.be.refs()
        self.assertNotIn("a.md", str(cm.exception))

    def test_page_cap(self):
        self.ov.entries = [f(MEM + "%03d.md" % i) for i in range(20)]
        with mock.patch.object(openviking, "LIST_PAGE", 2), mock.patch.object(openviking, "MAX_LIST_PAGES", 3):
            with self.assertRaises(BackendError):
                self.be.refs()
        self.assertEqual(len(self.ov.tree_queries), 3)

    def test_filters(self):
        self.ov.entries = [
            f(MEM + "keep.md"), f(MEM + ".abstract.md"), f(MEM + ".overview.md"), f(MEM + ".hidden.md"),
            f(MEM + "soul.md"), f(MEM + "Identity.md"), f(MEM + "note.txt"), f(MEM + "readme"),
            f(MEM + "sub/"), d(MEM + "dir.md"), {"uri": MEM + "isdir.md", "isDir": True}, "junk", 7,
            {"isDir": False}, f(MEM + "sub/.overview.md"), f(MEM + "sub/deep.md"),
        ]
        self.assertEqual([u for u, _ in self.ro.refs()], [MEM + "keep.md", MEM + "sub/deep.md"])

    def test_custom_skip_names(self):
        self.ov.entries = [f(MEM + "keep.md"), f(MEM + "soul.md")]
        be = OpenVikingBackend(self.ov.url, KEY, skip_names=("keep.md",))
        self.assertEqual([u for u, _ in be.refs()], [MEM + "soul.md"])

    def test_bad_listing_shape_and_failure_do_not_leak_upstream_text(self):
        self.ov.fail = True
        with self.assertRaises(BackendError) as cm:
            self.ro.refs()
        self.assertNotIn(SECRET, str(cm.exception))


class DeleteTests(ManageTestCase):
    def test_present_then_already_gone(self):
        ref = MEM + "a.md"
        self.ov.existing.add(ref)
        self.assertTrue(self.be.delete(ref))
        self.assertEqual(self.ov.deleted, [ref])
        self.assertFalse(self.be.delete(ref))

    def test_a_file_that_never_existed_is_false_although_the_server_says_200(self):
        self.assertFalse(self.be.delete(MEM + "nothing.md"))
        self.assertEqual(self.ov.deleted, [])

    def test_a_file_not_indexed_yet_still_counts_as_deleted(self):
        # estimated_deleted_count is 0 for it, but it existed and is gone now.
        ref = MEM + "fresh.md"
        self.ov.existing.add(ref)
        self.ov.unindexed.add(ref)
        self.assertTrue(self.be.delete(ref))
        self.assertEqual(self.ov.deleted, [ref])

    def test_read_only_backend_refuses(self):
        self.ov.existing.add(MEM + "a.md")
        with self.assertRaises(PermissionError):
            self.ro.delete(MEM + "a.md")
        self.assertEqual(self.ov.deleted, [])
        self.assertEqual(self.ov.delete_calls, 0)

    def test_refs_outside_the_memories_are_refused(self):
        for ref in ("viking://user/u1/sessions/x", MEM + "../soul.md", "memories/a.md", None, MEM + "a\nb.md"):
            with self.assertRaises(ValueError):
                self.be.delete(ref)

    def test_other_failures_raise_without_upstream_text(self):
        self.ov.fail = True
        with self.assertRaises(BackendError) as cm:
            self.be.delete(MEM + "a.md")
        self.assertNotIn(SECRET, str(cm.exception))

    def test_a_404_elsewhere_stays_an_error(self):
        # _call only treats 404 as "missing" when asked to: a read of a missing file still fails.
        with self.assertRaises(BackendError):
            self.be.read(MEM + "nothing.md")


class PutTests(ManageTestCase):
    def test_put_writes_with_mode_create_and_returns_the_ref(self):
        ref = MEM + "events/2025/03/04/a.md"
        self.assertEqual(self.be.put("hello", ref=ref, date="2025-03-04"), ref)
        self.assertEqual(self.ov.writes, [{"uri": ref, "content": "hello", "mode": "create"}])

    def test_put_needs_a_ref(self):
        with self.assertRaises(ValueError):
            self.be.put("hello")
        self.assertEqual(self.ov.writes, [])

    def test_put_refuses_other_places(self):
        with self.assertRaises(ValueError):
            self.be.put("x", ref="viking://user/u1/sessions/s/a.md")
        with self.assertRaises(ValueError):
            self.be.put("x", ref=MEM + "../a.md")

    def test_read_only_backend_refuses(self):
        with self.assertRaises(PermissionError):
            self.ro.put("x", ref=MEM + "a.md")
        self.assertEqual(self.ov.writes, [])

    def test_failure_has_no_upstream_text(self):
        self.ov.fail = True
        with self.assertRaises(BackendError) as cm:
            self.be.put("x", ref=MEM + "a.md")
        self.assertNotIn(SECRET, str(cm.exception))


class ReceiptRefsTests(ManageTestCase):
    def listing(self, *uris):
        self.ov.sessions["viking://~/sessions/" + RECEIPT] = [f(u) for u in uris]

    def test_no_session_or_no_diff_yet(self):
        self.assertIsNone(self.be.receipt_refs(RECEIPT))  # 404
        self.listing(SESSION + "/messages.jsonl", SESSION + "/history/archive_001/messages.jsonl")
        self.assertIsNone(self.be.receipt_refs(RECEIPT))

    def test_one_good_diff(self):
        self.listing(SESSION + "/messages.jsonl", DIFF)
        self.ov.files[DIFF] = diff(
            adds=[MEM + "events/2025/03/04/a.md", MEM + "profile/.overview.md"],
            updates=[MEM + "profile/likes.md", MEM + "profile/soul.md", MEM + "events/2025/03/04/a.md"],
            deletes=[MEM + "old.md"],
        )
        self.assertEqual(self.be.receipt_refs(RECEIPT), [MEM + "events/2025/03/04/a.md", MEM + "profile/likes.md"])
        q = self.ov.ls_queries[-1]
        self.assertEqual((q["uri"], q["recursive"]), ("viking://~/sessions/" + RECEIPT, "true"))

    def test_a_diff_that_changed_nothing_is_an_empty_list(self):
        self.listing(DIFF)
        self.ov.files[DIFF] = diff()
        self.assertEqual(self.be.receipt_refs(RECEIPT), [])

    def test_a_file_deleted_by_the_same_session_is_left_out(self):
        self.listing(DIFF)
        self.ov.files[DIFF] = diff(adds=[MEM + "a.md", MEM + "b.md"], deletes=[MEM + "a.md"])
        self.assertEqual(self.be.receipt_refs(RECEIPT), [MEM + "b.md"])

    def test_two_diffs(self):
        other = SESSION + "/history/archive_002/memory_diff.json"
        self.listing(DIFF, other)
        self.ov.files[DIFF] = diff(adds=[MEM + "a.md"])
        self.ov.files[other] = diff(adds=[MEM + "b.md"], archive=SESSION + "/history/archive_002")
        self.assertIsNone(self.be.receipt_refs(RECEIPT))

    def test_a_diff_of_another_session_does_not_count(self):
        elsewhere = "viking://user/u1/sessions/gw-other/history/archive_001/memory_diff.json"
        self.listing(elsewhere)
        self.ov.files[elsewhere] = diff(adds=[MEM + "a.md"])
        self.assertIsNone(self.be.receipt_refs(RECEIPT))

    def test_bad_shapes(self):
        self.listing(DIFF)
        good = MEM + "a.md"
        bad = [
            "not json",
            json.dumps([1, 2]),
            json.dumps({"archive_uri": ARCHIVE}),
            json.dumps({"archive_uri": ARCHIVE, "operations": {"adds": "x", "updates": [], "deletes": []}}),
            json.dumps({"archive_uri": ARCHIVE, "operations": {"adds": [], "updates": []}}),
            json.dumps({"archive_uri": ARCHIVE, "operations": {"adds": [5], "updates": [], "deletes": []}}),
            json.dumps({"archive_uri": ARCHIVE, "operations": {"adds": [{"uri": 5}], "updates": [], "deletes": []}}),
            diff(adds=[good], archive=SESSION + "/history/archive_009"),
            json.dumps({"archive_uri": None, "operations": {"adds": [], "updates": [], "deletes": []}}),
            json.dumps({"archive_uri": ARCHIVE, "summary": {"total_adds": 3},
                        "operations": {"adds": [{"uri": good}], "updates": [], "deletes": []}}),
        ]
        for raw in bad:
            self.ov.files[DIFF] = raw
            self.assertIsNone(self.be.receipt_refs(RECEIPT), raw)

    def test_operations_outside_the_memories_root(self):
        self.listing(DIFF)
        outside = [
            "viking://user/u1/sessions/x/a.md",
            "viking://user/other/memories/a.md",  # another account's memories
            "viking://user/u1/memories/../sessions/a.md",
            "viking://agent/u1/memories/a.md",
        ]
        for uri in outside:
            for kind in ("adds", "updates", "deletes"):
                self.ov.files[DIFF] = diff(**{kind: [MEM + "ok.md", uri]})
                self.assertIsNone(self.be.receipt_refs(RECEIPT), (kind, uri))

    def test_unsafe_receipts_do_not_reach_the_server(self):
        for receipt in ("", "a/b", "../x", "a b", None, 5, "x" * 200):
            self.assertIsNone(self.be.receipt_refs(receipt))
        self.assertEqual(self.ov.ls_queries, [])

    def test_failure_raises_without_upstream_text(self):
        self.ov.fail = True
        with self.assertRaises(BackendError) as cm:
            self.be.receipt_refs(RECEIPT)
        self.assertNotIn(SECRET, str(cm.exception))


class FindScoreTests(unittest.TestCase):
    def backend(self, **options):
        be = OpenVikingBackend("http://127.0.0.1:1", KEY, **options)
        raw = [{"uri": MEM + "events/2025/03/04/a.md", "score": x, "abstract": "x"} for x in (0.95, 0.75, 0.6, 1.2)]
        be.find_raw = lambda query, limit, timeout=None, ctl=None: raw
        return be

    def test_defaults_map_scores_from_the_range(self):
        be = self.backend()
        hits = be.find("q", 10)
        self.assertEqual([round(h.score, 6) for h in hits], [0.9, 0.5, 0.2, 1.0])
        self.assertEqual([h.meta["raw_score"] for h in hits], [0.95, 0.75, 0.6, 1.2])
        self.assertEqual(hits[0].date, "2025-03-04")
        self.assertEqual((be.floor, be.ceil), (0.5, 1.0))
        self.assertEqual(OpenVikingBackend.default_min_score, 0.55)
        self.assertEqual(OpenVikingBackend.default_relative_ratio, 0.95)

    def test_custom_range_and_clamping(self):
        hits = self.backend(floor=0.7, ceil=0.9).find("q", 10)
        self.assertEqual([round(h.score, 6) for h in hits], [1.0, 0.25, 0.0, 1.0])

    def test_identity_range_keeps_the_raw_score(self):
        hits = self.backend(floor=0, ceil=1).find("q", 10)
        self.assertEqual([h.score for h in hits], [0.95, 0.75, 0.6, 1.0])

    def test_calibration_never_reorders_hits(self):
        """Every raw score, also those at or below the floor, keeps its place in the ranking:
        a flat 0 below the floor once turned weak hits into ties and cost Hit@5 on real data."""
        backend = OpenVikingBackend("http://127.0.0.1:1", KEY, floor=0.5, ceil=1.0)
        raws = [0.0, 0.05, 0.2, 0.3, 0.49, 0.5, 0.51, 0.6, 0.75, 0.9, 1.0]
        mapped = [backend._calibrate(raw) for raw in raws]
        self.assertEqual(mapped, sorted(mapped))
        self.assertEqual(len(set(mapped[:-1])), len(mapped) - 1)  # no ties below the ceiling
        self.assertLess(mapped[5], 1e-6)       # still far under any usable threshold
        self.assertAlmostEqual(mapped[8], 0.5)  # 0.75 raw is the default min_score

    def test_bad_range_is_refused(self):
        for floor, ceil in ((0.5, 0.5), (0.9, 0.1), (0, float("inf")), (float("nan"), 1)):
            with self.assertRaises(ValueError):
                OpenVikingBackend("http://127.0.0.1:1", KEY, floor=floor, ceil=ceil)


if __name__ == "__main__":
    unittest.main()
