"""Check the openviking backend's management capabilities against the real server.

    python capabilities.py --out DIR      (after feiyu_memgate_openviking.py: it uses the `gate` user's 240 files)

Checks refs() (paging), put + read, delete (then put again to restore), and what a session listing
viking://~/sessions/<id> does (receipt_refs, uncommitted). No language model is involved: a session is created but
never committed. Writes <out>/capabilities.json and prints it. Contains no memory text and no keys.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ.get("FMG_SRC", "/repo/src"))

import ov_common as ov  # noqa: E402
from feiyu_memgate.backends import openviking as ovb  # noqa: E402

DATA_DIR = os.environ.get("BENCH_DATA", "/repo/bench/data")


def attempt(report, name, fn):
    try:
        report[name] = fn()
    except Exception as error:  # noqa: BLE001
        report[name] = {"error": type(error).__name__ + ": " + str(error)[:120]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    memories = json.load(open(os.path.join(DATA_DIR, "memories.json"), encoding="utf-8"))
    key = json.load(open("/work/keys.json", encoding="utf-8"))["gate"]
    backend = ovb.OpenVikingBackend(ov.BASE, key, timeout=30.0, writable=True)
    report = {}

    def refs_check():
        refs = backend.refs()
        ids = {ov.memory_id(u) for u, _ in refs}
        dated = sum(1 for _, d in refs if d)
        small = []
        for page_size in (50, 100):  # force several pages: the backend pages by LIST_PAGE
            ovb.LIST_PAGE = page_size
            try:
                small.append(len(backend.refs()))
            except Exception as error:  # noqa: BLE001
                small.append(type(error).__name__ + ": " + str(error)[:80])
        ovb.LIST_PAGE = 500
        return {"count": len(refs), "distinct_memory_ids": len(ids - {None}), "with_date": dated,
                "count_with_page_size_50_and_100": small}

    attempt(report, "refs", refs_check)

    def tree_listing():
        """Not a backend call: the server's /fs/tree with a deep level_limit and offset paging, for comparison."""
        seen, offset, pages = set(), 0, 0
        while pages < 50:
            status, reply = ov.call("GET", "/api/v1/fs/tree?uri=viking%3A%2F%2F~%2Fmemories&level_limit=10"
                                    "&node_limit=100&offset=" + str(offset), key)
            if status != 200:
                return {"http": status}
            pages += 1
            seen |= {e["uri"] for e in reply["result"] if not e.get("isDir") and ov.memory_id(e["uri"])}
            if reply.get("has_more") is not True:
                break
            offset += 100
        return {"files": len(seen), "pages": pages}

    attempt(report, "raw_fs_tree_listing_for_comparison", tree_listing)
    attempt(report, "raw_fs_ls_recursive_entries_that_are_files", lambda: sum(
        1 for e in ov.call("GET", "/api/v1/fs/ls?uri=viking%3A%2F%2F~%2Fmemories&recursive=true&node_limit=1000", key)[1]["result"]
        if not e.get("isDir")))

    probe_id, probe_text = "m999", "capability probe memory, safe to delete"
    uri = ov.event_uri("~", probe_id, "2025-01-01")

    def put_read():
        put_ref = backend.put(probe_text, ref=uri)
        back = backend.read(put_ref)
        backend.put(probe_text + " v2", ref=uri)  # mode create over an existing file
        replaced = backend.read(uri) == probe_text + " v2"
        in_refs = any(u.endswith("/m999.md") for u, _ in backend.refs())
        return {"put_returned_ref": put_ref == uri, "read_equals_written": back == probe_text, "listed_in_refs": in_refs,
                "second_put_replaces_text": replaced}

    attempt(report, "put_read_roundtrip", put_read)

    def delete_probe():
        first = backend.delete(uri)
        second = backend.delete(uri)
        return {"first_delete": first, "second_delete_already_gone": second,
                "still_listed": any(u.endswith("/m999.md") for u, _ in backend.refs())}

    attempt(report, "delete", delete_probe)

    def delete_restore_real():
        mid = "m001"
        real_uri = ov.event_uri("~", mid, memories[mid]["date"])
        before = len(backend.refs())
        deleted = backend.delete(real_uri)
        after = len(backend.refs())
        backend.put(memories[mid]["text"], ref=real_uri)
        restored = len(backend.refs())
        text_ok = backend.read(real_uri) == memories[mid]["text"]
        waited, _ = ov.wait_indexed(key, {mid: memories[mid]}, sample=1, timeout=120)
        return {"deleted": deleted, "refs_before_after_restore": [before, after, restored], "text_restored": text_ok,
                "searchable_again_after_s": round(waited, 1)}

    attempt(report, "delete_then_restore_real_memory", delete_restore_real)

    def sessions():
        sid = "gw-captest0001"
        status, _ = ov.call("POST", "/api/v1/sessions", key, {"session_id": sid})
        listing_status, listing = ov.call(
            "GET", "/api/v1/fs/ls?uri=viking%3A%2F%2F~%2Fsessions%2F" + sid + "&recursive=true&simple=true&node_limit=1000", key)
        refs = backend.receipt_refs(sid)
        missing = backend.receipt_refs("gw-doesnotexist0001")
        return {"create_session_http": status, "session_listing_http": listing_status,
                "session_listing_entries": len(listing["result"]) if listing_status == 200 else None,
                "receipt_refs_uncommitted_session": refs, "receipt_refs_unknown_session": missing,
                "note": "no commit was made here (it needs an LLM extraction): see receipt_check.py for the committed layout"}

    attempt(report, "session_listing_and_receipt_refs", sessions)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "capabilities.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
