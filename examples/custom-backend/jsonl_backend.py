"""An example piece: a storage backend that keeps memories in a JSON-lines file.

Put this file next to the configuration and name it by module and class, nothing to
install (the configuration's folder is searched first):

    [stores.notes]
    type = "jsonl_backend:JsonLinesBackend"
    path = "data/notes.jsonl"

One line per memory: {"ref": "...", "text": "...", "date": "YYYY-MM-DD" or null}. The whole file
is read into memory when the store is opened; find() scores every memory against the query by
word overlap. This is a teaching example of the smallest complete backend (find, remember, health,
close), not a store for real use: see the README next to it for what it leaves out.
"""
import datetime
import json
import os
import re
import threading
import uuid

from feiyu_memgate.pieces import BackendError, Hit, MemoryBackend

# A "word" is a run of ASCII letters or digits, or one CJK character. Case does not matter.
_WORD_RE = re.compile(r"[a-z0-9]+|[㐀-鿿]")


def words(text):
    return set(_WORD_RE.findall(text.lower()))


def message_day(message):
    """The "YYYY-MM-DD" a message's optional `time` starts with, else None."""
    value = message.get("time")
    if not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


class JsonLinesBackend(MemoryBackend):
    """Options: path (the file; its folder is created when missing), writable (default True)."""

    # The score is the share of the query's words that the memory contains, so it is in 0..1. That
    # scale is the backend's own: say where a sensible floor is. The gateway's `min_score` stage reads
    # these when the configuration gives it no value. `broad_*` is the default profile, `default_*`
    # the "precise" one (the prefix is historical).
    default_min_score = 0.5
    broad_min_score = 0.4

    def __init__(self, path, writable=True):
        self.path = os.fspath(path)
        self.writable = bool(writable)
        self._lock = threading.Lock()
        self._items = []  # (ref, text, date), in file order
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return
        except OSError as error:
            raise BackendError("cannot read the store file: %s" % type(error).__name__) from None
        for line in lines:
            try:
                row = json.loads(line)
                self._items.append((str(row["ref"]), str(row["text"]), row.get("date")))
            except (ValueError, KeyError, TypeError):
                continue  # a half-written last line after a crash: skip it, never log its content

    def _append(self, text, date):
        ref = "m-" + uuid.uuid4().hex[:12]
        line = json.dumps({"ref": ref, "text": text, "date": date}, ensure_ascii=False)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as error:
            # The gateway turns this into a 502 and counts the store as failed for this request.
            # The message names the kind of failure, never the memory text.
            raise BackendError("cannot write the store file: %s" % type(error).__name__) from None
        with self._lock:
            self._items.append((ref, text, date))
        return ref

    def find(self, query, limit, timeout=None, ctl=None):
        wanted = words(query)
        if not wanted:
            return []
        with self._lock:
            items = list(self._items)
        hits = []
        for ref, text, date in items:
            score = len(wanted & words(text)) / len(wanted)
            if score > 0:
                hits.append(Hit(ref=ref, score=score, text=text, date=date))
        hits.sort(key=lambda hit: -hit.score)
        return hits[:limit]

    def remember(self, messages, timeout=None, ctl=None):
        if not self.writable:
            raise PermissionError("this backend is read-only")
        receipt = "w-" + uuid.uuid4().hex
        kept = 0
        for message in messages:
            if message["role"] == "user":  # keep what the user said, as it is
                self._append(message["content"], message_day(message))
                kept += 1
        return receipt if kept else None  # None: nothing kept, the client sees "skipped"

    def health(self, timeout=2.0):
        return not os.path.exists(self.path) or os.access(self.path, os.R_OK)
