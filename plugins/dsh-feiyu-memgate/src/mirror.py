"""Bring the keyword index of a hybrid store in line with its vector store file.

dsh-feiyu-memgate runs this with the interpreter that runs its gateway, right before it starts the
gateway (the gateway is not running then, so nothing else writes to either file):

    python -I -B mirror.py

Why it exists: the vector file holds every memory of the store. While the hybrid switch is off,
saving, forgetting, deleting and the copy from the keyword store touch only that file, so the keyword
index next to it falls behind: a memory forgotten in the meantime would come back through the index,
and a memory saved in the meantime would have no keyword signal. This script makes the index hold
exactly the refs of the vector file: it removes the refs the vector file no longer has and adds the
ones it has not indexed yet, oldest first. It is idempotent: a second run right after a finished one
changes nothing.

The job arrives as one line of JSON on stdin: { package_dir, vector, keyword, deadline_seconds }, where
`vector` is the path of the vector store file and `keyword` is the keyword index as a gateway store
table ({ "type": "sqlite", "path": ... }). stdin then stays open, and the script exits as soon as it
closes: the plugin stopped the run, or the host process went away however it went away. Progress goes
to stdout as JSON lines. Nothing printed names a memory text, a ref, a key or a path: counts, and the
kind of error when a run fails. Exit code 0 for done and partial, 2 for failed.

The seven record fields (speaker, about, channel, audience, origin, event_date, kind) go along when the
vector file has them as columns and the keyword index keeps fields; a memory without an audience gets
"owner": these stores are the owner's.

The vector file is only read, through SQLite directly and read-only (it needs no embeddings endpoint).
The keyword index is written through the gateway's own backend, so its layout is always the one the
gateway expects. A run that reaches its time limit reports `partial` and the next start continues.
"""
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

BATCH = 200
FIELD_NAMES = ("speaker", "about", "channel", "audience", "origin", "event_date", "kind")  # model.FIELDS
AUDIENCES = ("owner", "group", "public")
MAX_FIELD = 128
DEFAULT_DEADLINE = 60.0


def _watch_stdin():
    try:
        while sys.stdin.buffer.read(4096):
            pass
    except BaseException:  # noqa: BLE001 - any end of stdin ends the run
        pass
    os._exit(3)


def emit(event, **fields):
    sys.stdout.write(json.dumps(dict(event=event, **fields)) + "\n")
    sys.stdout.flush()


def open_vector(path):
    """A read-only connection to the vector store file, or None when there is none yet."""
    if not os.path.exists(path):
        return None
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10.0)


def vector_refs(db):
    """Every ref of the vector store, oldest first. A file without the table holds nothing."""
    if db is None:
        return []
    try:
        return [row[0] for row in db.execute("SELECT ref FROM memories ORDER BY created_at, ref")]
    except sqlite3.OperationalError:
        return []


def vector_columns(db):
    """The record field columns the vector file has (a subset of FIELD_NAMES, in that order)."""
    if db is None:
        return ()
    try:
        names = {row[1] for row in db.execute("PRAGMA table_info(memories)")}
    except sqlite3.OperationalError:
        return ()
    return tuple(name for name in FIELD_NAMES if name in names)


def clean_record_fields(values):
    """The fields of one memory: only usable values, and an audience ("owner" when there is none)."""
    fields = {}
    for name, value in values.items():
        if isinstance(value, str) and value and len(value) <= MAX_FIELD:
            fields[name] = value
    if fields.get("audience") not in AUDIENCES:
        fields["audience"] = "owner"
    return fields


def vector_items(db, refs, columns=()):
    """(text, ref, date, fields) of the given refs, in that order. Empty texts are left out. `fields`
    is the memory's record fields (an audience of "owner" when the file has none)."""
    marks = ",".join("?" * len(refs))
    names = ("ref", "text", "date") + tuple(columns)
    rows = db.execute("SELECT %s FROM memories WHERE ref IN (%s)" % (", ".join(names), marks), refs).fetchall()
    found = {row[0]: row[1:] for row in rows}
    return [(found[ref][0], ref, found[ref][1], clean_record_fields(dict(zip(columns, found[ref][2:]))))
            for ref in refs
            if ref in found and isinstance(found[ref][0], str) and found[ref][0].strip()]


def main():
    job = json.loads(sys.stdin.buffer.readline().decode("utf-8"))
    threading.Thread(target=_watch_stdin, name="parent-watch", daemon=True).start()
    sys.path.insert(0, job["package_dir"])
    from feiyu_memgate import registry  # noqa: E402 - the package path is part of the job

    deadline = time.monotonic() + float(job.get("deadline_seconds", DEFAULT_DEADLINE))
    keyword = None
    vector = None
    try:
        vector = open_vector(job["vector"])
        wanted = vector_refs(vector)
        keyword = registry.create("backend", job["keyword"])
        columns = vector_columns(vector)
        keeps_fields = getattr(keyword, "stores_fields", False) is True
        have = [ref for ref, _ in keyword.refs()]
        wanted_set = set(wanted)
        stale = [ref for ref in have if ref not in wanted_set]
        have_set = set(have)
        missing = [ref for ref in wanted if ref not in have_set]
        emit("start", vector=len(wanted), keyword=len(have))

        removed = 0
        for ref in stale:
            if time.monotonic() >= deadline:
                break
            keyword.delete(ref)
            removed += 1

        added = 0
        handled = 0  # refs looked at, whether added or skipped as empty
        while handled < len(missing) and time.monotonic() < deadline and removed == len(stale):
            chunk = missing[handled:handled + BATCH]
            for text, ref, date, fields in vector_items(vector, chunk, columns):
                if keeps_fields:
                    keyword.put(text, ref=ref, date=date, fields=fields)
                else:
                    keyword.put(text, ref=ref, date=date)
                added += 1
            handled += len(chunk)
            emit("progress", added=added, removed=removed, remaining=len(missing) - handled + len(stale) - removed)

        remaining = len(missing) - handled + len(stale) - removed
        emit("done" if remaining == 0 else "partial", added=added, removed=removed, remaining=remaining)
        return 0
    except Exception as error:  # noqa: BLE001 - reported by kind only
        emit("failed", kind=type(error).__name__)
        return 2
    finally:
        for resource in (keyword, vector):
            try:
                if resource is not None:
                    resource.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    sys.exit(main())
