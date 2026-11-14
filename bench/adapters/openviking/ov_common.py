"""Shared helpers for the two OpenViking adapters: HTTP calls, account setup, direct ingest, index wait.

Standard library only. Runs inside the benchmark container on the internal network; never prints keys.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

BASE = os.environ.get("OV_URL", "http://fmg-bench-openviking:1933")
ACCOUNT = "bench"
TARGET = "viking://~/memories"
_ID_FROM_URI = re.compile(r"/([a-z]\d{3,})\.md$")


def call(method, path, key, body=None, timeout=60):
    """(status, parsed JSON or text). HTTP errors are returned, not raised."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Authorization": "Bearer " + key}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8", "replace")[:300]
        try:
            return error.code, json.loads(text)
        except ValueError:
            return error.code, text


def wait_health(timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    raise RuntimeError("OpenViking did not become healthy")


def setup_accounts(root_key, users):
    """Create the account and one user per name with the root key; returns {user: key}.

    Routes (from /openapi.json): POST /api/v1/admin/accounts {account_id, admin_user_id},
    POST /api/v1/admin/accounts/{account}/users {user_id, role}; the key is result.user_key.
    """
    status, reply = call("POST", "/api/v1/admin/accounts", root_key,
                         {"account_id": ACCOUNT, "admin_user_id": "admin"})
    if status != 200:
        raise RuntimeError("create account failed: HTTP %s" % status)
    keys = {}
    for user in users:
        status, reply = call("POST", "/api/v1/admin/accounts/%s/users" % ACCOUNT, root_key,
                             {"user_id": user, "role": "user"})
        if status != 200:
            raise RuntimeError("register user failed: HTTP %s" % status)
        keys[user] = reply["result"]["user_key"]
    return keys


def memory_id(uri):
    """'m012' (or 'd012' in the development set) from a memory file URI, or None for anything else (directory summaries, other files)."""
    m = _ID_FROM_URI.search(uri or "")
    return m.group(1) if m else None


def event_uri(user_part, mid, date):
    y, mo, d = date.split("-")
    return "viking://%s/memories/events/%s/%s/%s/%s.md" % (user_part, y, mo, d, mid)


def ingest_direct(key, user, memories):
    """POST /api/v1/content/write for every memory, oldest first. Returns the number of failures."""
    failed = 0
    for mid, m in sorted(memories.items(), key=lambda kv: (kv[1]["date"], kv[0])):
        status, _ = call("POST", "/api/v1/content/write", key,
                         {"uri": event_uri("user/" + user, mid, m["date"]), "content": m["text"], "mode": "create"})
        failed += status != 200
    return failed


def wait_indexed(key, memories, sample=20, timeout=900):
    """Poll search/find with a sample of memories' own texts until each finds itself (top 1 among memory files).

    Returns (seconds waited, number of sample memories confirmed). Raises on timeout.
    """
    ids = sorted(memories)
    step = max(1, len(ids) // sample)
    pending = set(ids[::step][:sample]) | {ids[-1]}  # the last one written: the queue is worked in order
    total = len(pending)
    start = time.time()
    while pending and time.time() - start < timeout:
        for mid in sorted(pending):
            status, reply = call("POST", "/api/v1/search/find", key,
                                 {"query": memories[mid]["text"], "limit": 3, "target_uri": TARGET})
            if status == 200:
                found = [memory_id(x["uri"]) for x in reply["result"]["memories"]]
                if mid in found:
                    pending.discard(mid)
        if pending:
            time.sleep(2)
    if pending:
        raise RuntimeError("%d of %d sample memories not indexed within %ds" % (len(pending), total, timeout))
    return time.time() - start, total


def count_files(key):
    """Number of memory files indexed for this user: a search for each memory is too slow, so use the listing."""
    status, reply = call("GET", "/api/v1/fs/ls?uri=viking%3A%2F%2F~%2Fmemories&recursive=true&node_limit=1000", key)
    if status != 200:
        return None
    return sum(1 for e in reply["result"] if not e.get("isDir") and memory_id(e["uri"]))
