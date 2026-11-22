"""Copy memories from the keyword store into a vector store, one bounded step at a time.

dsh-feiyu-memgate runs this with the interpreter that runs its gateway, while the gateway serves the
vector store (both open it through SQLite; the gateway notices writes of other connections):

    python -I -B migrate.py

The job arrives as one line of JSON on stdin. stdin then stays open, and the script exits as soon as it
closes: the plugin stopped the copy, or the host process went away however it went away. Progress goes
to stdout as JSON lines. Nothing printed names a memory text, a key or a token: failures are reported
by the kind of error only.

The seven record fields (speaker, about, channel, audience, origin, event_date, kind) are copied with
the text when the keyword store file has them as columns and the target keeps fields; a copied memory
without an audience gets "owner": these stores are the owner's. Only values the gateway would take are
copied (a short non-empty string; an audience of the three known ones).

A state file next to the stores lists every ref already copied. A memory copied once and later removed
from the vector store (forgotten, deleted) is therefore not copied again, and a run that stopped half
way, at its item limit, its time limit or a failure of the embeddings endpoint, resumes where it
stopped. The keyword store is only read.

An endpoint may refuse single texts while serving the others: an embeddings service may answer 413 for
input longer than its model takes (the local embedding add-on does so when its overflow setting is
`reject`; by default it truncates), and the gateway accepts memories of up to 4000 characters. When a
batch fails, its texts are therefore tried one by one; the
ones refused while others of the same batch are accepted are recorded as refused and not offered to
this store again (another model has another store and state file). A batch of which no text is
accepted means the endpoint itself fails: the run stops and resumes later.
"""
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

STATE_VERSION = 1
FIELD_NAMES = ("speaker", "about", "channel", "audience", "origin", "event_date", "kind")  # model.FIELDS
AUDIENCES = ("owner", "group", "public")
MAX_FIELD = 128


def _watch_stdin():
    try:
        while sys.stdin.buffer.read(4096):
            pass
    except BaseException:  # noqa: BLE001 - any end of stdin ends the copy
        pass
    os._exit(3)


def emit(event, **fields):
    sys.stdout.write(json.dumps(dict(event=event, **fields)) + "\n")
    sys.stdout.flush()


def load_state(path):
    """(copied refs, refused refs) from the state file; empty sets when there is none."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and data.get("version") == STATE_VERSION and isinstance(data.get("copied"), list):
            refused = data.get("refused") if isinstance(data.get("refused"), list) else []
            return ({ref for ref in data["copied"] if isinstance(ref, str)},
                    {ref for ref in refused if isinstance(ref, str)})
    except (OSError, ValueError):
        pass
    return set(), set()


def save_state(path, copied, refused, done):
    # Written whole and renamed into place: a crash leaves the previous state, never half a file.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"version": STATE_VERSION, "done": done, "updated": int(time.time()),
                   "copied": sorted(copied), "refused": sorted(refused - copied)}, handle)
    os.replace(tmp, path)


def store_each(target, items, timeout, deadline):
    """After a failed batch: write its items one at a time. Returns (stored refs, refused refs);
    items not tried before the deadline are in neither list."""
    stored, refused = [], []
    for item in items:
        if time.monotonic() >= deadline:
            break
        ref = item["ref"] if isinstance(item, dict) else item[1]
        try:
            target.put_many([item], timeout=timeout)
            stored.append(ref)
        except Exception:  # noqa: BLE001 - only whether it was accepted matters here
            refused.append(ref)
    return stored, refused


def open_source(path):
    """A read-only connection to the keyword store, or None when there is none yet."""
    if not os.path.exists(path):
        return None
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10.0)


def source_refs(db):
    if db is None:
        return []
    try:
        return [row[0] for row in db.execute("SELECT ref FROM memories ORDER BY created_at, ref")]
    except sqlite3.OperationalError:  # a store file without the table holds nothing
        return []


def source_columns(db):
    """The record field columns the keyword store file has (a subset of FIELD_NAMES, in that order)."""
    if db is None:
        return ()
    try:
        names = {row[1] for row in db.execute("PRAGMA table_info(memories)")}
    except sqlite3.OperationalError:
        return ()
    return tuple(name for name in FIELD_NAMES if name in names)


def clean_record_fields(values):
    """The fields of one copied memory: only usable values, and an audience ("owner" when there is none)."""
    fields = {}
    for name, value in values.items():
        if isinstance(value, str) and value and len(value) <= MAX_FIELD:
            fields[name] = value
    if fields.get("audience") not in AUDIENCES:
        fields["audience"] = "owner"
    return fields


def source_items(db, refs, columns=(), keeps_fields=False):
    """The memories of `refs`, in that order: (text, ref, date) tuples, or, when the target keeps fields,
    dicts with "fields" (the columns the file has, an audience of "owner" when it has none)."""
    marks = ",".join("?" * len(refs))
    names = ("ref", "text", "date") + tuple(columns) if keeps_fields else ("ref", "text", "date")
    rows = db.execute("SELECT %s FROM memories WHERE ref IN (%s)" % (", ".join(names), marks), refs).fetchall()
    found = {row[0]: row[1:] for row in rows}
    items = []
    for ref in refs:
        if ref not in found or not isinstance(found[ref][0], str) or not found[ref][0].strip():
            continue
        text, date = found[ref][0], found[ref][1]
        if keeps_fields:
            fields = clean_record_fields(dict(zip(columns, found[ref][2:])))
            items.append({"text": text, "ref": ref, "date": date, "fields": fields})
        else:
            items.append((text, ref, date))
    return items


def main():
    job = json.loads(sys.stdin.buffer.readline().decode("utf-8"))
    threading.Thread(target=_watch_stdin, name="parent-watch", daemon=True).start()
    sys.path.insert(0, job["package_dir"])
    from feiyu_memgate import registry  # noqa: E402 - the package path is part of the job
    from feiyu_memgate.config import resolve_env  # noqa: E402

    state_file = job["state_file"]
    max_items = max(1, int(job.get("max_items", 500)))
    batch = max(1, int(job.get("batch", 32)))
    timeout = float(job.get("timeout", 30))
    deadline = time.monotonic() + float(job.get("deadline_seconds", 120))
    copied, refused = load_state(state_file)

    try:
        target = registry.create("backend", resolve_env(job["target"], os.environ, "migration"))
        source = open_source(job["source"])
        refs = source_refs(source)
        columns = source_columns(source)
        keeps_fields = getattr(target, "stores_fields", False) is True
        present = {ref for ref, _ in target.refs()}
    except Exception as error:  # noqa: BLE001 - reported by kind only
        emit("failed", kind=type(error).__name__)
        return 2

    # A ref already in the vector store needs no copy (written there under this tier, or copied by a
    # run whose state file was lost).
    copied.update(ref for ref in refs if ref in present)
    todo = [ref for ref in refs if ref not in copied and ref not in refused]
    total = len(refs)

    def counts():
        done_count = sum(1 for ref in refs if ref in copied)
        refused_count = sum(1 for ref in refs if ref in refused and ref not in copied)
        return {"total": total, "copied": done_count, "refused": refused_count,
                "remaining": total - done_count - refused_count}

    emit("start", **counts())
    moved = 0
    position = 0
    while position < len(todo):
        if moved >= max_items or time.monotonic() >= deadline:
            break
        chunk = todo[position:position + min(batch, max_items - moved)]
        items = source_items(source, chunk, columns, keeps_fields)
        offered = {item["ref"] if isinstance(item, dict) else item[1] for item in items}
        # Refs that vanished from the keyword store meanwhile count as handled: there is nothing left to copy.
        copied.update(ref for ref in chunk if ref not in offered)
        try:
            if items:
                target.put_many(items, timeout=timeout)
            copied.update(offered)
            moved += len(items)
        except Exception as error:  # noqa: BLE001 - reported by kind only
            stored, rejected = store_each(target, items, timeout, deadline)
            if not stored:
                # Not one text of the batch was accepted: the endpoint fails. Keep what is done.
                save_state(state_file, copied, refused, False)
                emit("failed", kind=type(error).__name__, **counts())
                return 2
            copied.update(stored)
            refused.update(rejected)
            moved += len(stored)
        position += len(chunk)
        save_state(state_file, copied, refused, False)
        emit("progress", **counts())

    status = "done" if counts()["remaining"] == 0 else "partial"
    save_state(state_file, copied, refused, status == "done")
    emit(status, **counts())
    return 0


if __name__ == "__main__":
    sys.exit(main())
