"""In-memory backend: a list in this process, searched by shared characters.

No model, no files, nothing kept after the process ends. It exists for tests,
demos and as the smallest example of a backend. Standard library only.

Scoring is the same as the sqlite backend's: IDF-weighted containment of the query's grams
(character bigrams and single CJK ideographs), where a gram's weight comes from how many of the
memories held here contain it (the counts are kept up to date by put and delete).

The record fields of a memory (model.FIELDS) are kept in a second mapping beside `_items`, which keeps its
(text, date) shape; `find` returns them in `Hit.fields` and `records()` / `record(ref)` with the memory.

Audiences: `stamp_audience(level)` gives every memory without an audience `level` the first time it is called
and sets a flag; `checks_audience` is True from then on (the `record_audience` recall stage then reads each
memory's audience, one without counting as "owner"). `set_audience(level, only_missing)` changes it later on
purpose. Memories put without an audience after the stamp stay without one.
"""
import collections
import threading

from ..core.tidy import PreparedQuery, gram_weights, index_grams, normalize
from ..errors import BackendError
from ..registry import register
from ..model import AUDIENCES, Hit, clean_fields
from .base import (MemoryBackend, message_date, message_fields, new_receipt, new_ref, receipt_ref,
                   require_writable)


@register("backend", "inmemory")
class InMemoryBackend(MemoryBackend):
    """Options: writable (default True), memories (texts to start with)."""

    # Scores are IDF-weighted keyword overlap: the gateway's min_score stage starts from this
    # (the same scoring as the sqlite backend, chosen on the development set bench/dev).
    default_min_score = 0.30
    default_relative_ratio = 0.75  # see the sqlite backend
    broad_min_score = 0.25  # the "broad" profile, same numbers and rule as the sqlite backend
    broad_relative_ratio = 0.70
    stores_fields = True  # the record fields (model.FIELDS) are kept beside text and date

    def __init__(self, writable=True, memories=()):
        self.writable = bool(writable)
        self._lock = threading.Lock()
        self._items = {}  # ref -> (text, date or None)
        self._fields = {}  # ref -> the record fields of that memory (only memories that have some)
        self._frequency: collections.Counter[str] = collections.Counter()  # gram -> how many memories hold it
        self._stamped = False  # stamp_audience() has run (see checks_audience)
        for text in memories:
            self._store(text)  # seeding works for a read-only store too

    def put(self, text, ref=None, date=None, fields=None):
        """Store one memory text as it is and return its ref (an existing ref is replaced, fields included)."""
        require_writable(self)
        return self._store(text, ref, date, clean_fields(fields))

    def _store(self, text, ref=None, date=None, fields=None):
        ref = ref or new_ref()
        grams = index_grams(normalize(text))
        with self._lock:
            self._forget_grams(ref)  # replacing a ref must not count its old text twice
            self._set(ref, text, date, fields)
            self._frequency.update(grams)
        return ref

    def _set(self, ref, text, date, fields):
        """Keep one memory and its fields (a replaced memory loses the old ones). Under the lock."""
        self._items[ref] = (text, date)
        if fields:
            self._fields[ref] = dict(fields)
        else:
            self._fields.pop(ref, None)

    def _forget_grams(self, ref):
        """Take the grams of the memory stored under `ref` (if any) out of the counts. Under the lock."""
        old = self._items.get(ref)
        if old is None:
            return
        for gram in index_grams(normalize(old[0])):
            if self._frequency[gram] <= 1:
                del self._frequency[gram]
            else:
                self._frequency[gram] -= 1

    def refs(self):
        """[(ref, date or None)] for every memory (a snapshot, safe to delete while walking it)."""
        with self._lock:
            return [(ref, date) for ref, (_text, date) in self._items.items()]

    def delete(self, ref):
        """True when the memory existed and is gone now."""
        require_writable(self)
        with self._lock:
            if ref not in self._items:
                return False
            self._forget_grams(ref)
            del self._items[ref]
            self._fields.pop(ref, None)
            return True

    def receipt_refs(self, receipt):
        """The refs remember() made for `receipt` ("<receipt>-<index>"); a write is final at once.
        Messages that were not kept leave no ref, so a deleted one is simply absent."""
        prefix = receipt + "-" if isinstance(receipt, str) and receipt else None
        if prefix is None:
            return None
        with self._lock:
            return [ref for ref in self._items if ref.startswith(prefix)]

    def find(self, query, limit, timeout=None, ctl=None):
        prepared = PreparedQuery(query)  # normalised once, not once per memory
        with self._lock:
            items = [(ref, text, date, self._fields.get(ref)) for ref, (text, date) in self._items.items()]
            if len(prepared.norm) >= 2:  # the weights are read under the lock: they match `items`
                prepared = PreparedQuery(query, gram_weights(prepared.norm, self._frequency.get, len(items)))
        hits = [Hit(ref=ref, score=prepared.score(text), text=text, date=date, fields=dict(fields or {}))
                for ref, text, date, fields in items]
        hits = [h for h in hits if h.score > 0]
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

    def read(self, ref, timeout=None, ctl=None):
        with self._lock:
            return self._items[ref][0]

    def records(self):
        """[{"ref", "text", "date", "fields"}] of every memory, in the order they were stored (a snapshot)."""
        with self._lock:
            return iter([{"ref": ref, "text": text, "date": date, "fields": dict(self._fields.get(ref, {}))}
                         for ref, (text, date) in self._items.items()])

    @property
    def checks_audience(self):
        """True once the store was stamped (stamp_audience, set_audience)."""
        return self._stamped

    @staticmethod
    def _level(level):
        if level not in AUDIENCES:
            raise ValueError("an audience must be one of %s" % ", ".join(AUDIENCES))
        return level

    def stamp_audience(self, level):
        """The first time it is called: give every memory without an audience `level` and remember that the
        store was stamped. Later calls do nothing, and a read-only store is not stamped. Returns how many
        memories got the level."""
        self._level(level)
        if self._stamped or not self.writable:
            return 0
        with self._lock:
            if self._stamped:
                return 0
            changed = self._set_audience(level, True)
            self._stamped = True
        return changed

    def set_audience(self, level, only_missing=True):
        """Give the memories `level` as their audience: only those without one (`only_missing`), or every
        memory. Also marks the store stamped. Returns how many memories changed (0 on a read-only store)."""
        self._level(level)
        if not self.writable:
            return 0
        with self._lock:
            changed = self._set_audience(level, only_missing)
            self._stamped = True
        return changed

    def _set_audience(self, level, only_missing):
        """Under the lock: set the audience of the memories and count the ones that changed."""
        changed = 0
        for ref in self._items:
            fields = self._fields.setdefault(ref, {})
            current = fields.get("audience")
            if current == level or (only_missing and current is not None):
                continue
            fields["audience"] = level
            changed += 1
        return changed

    def record(self, ref):
        """{"ref", "text", "date", "fields"} of one memory; KeyError when it is not stored."""
        with self._lock:
            text, date = self._items[ref]
            return {"ref": ref, "text": text, "date": date, "fields": dict(self._fields.get(ref, {}))}

    def remember(self, messages, timeout=None, ctl=None):
        """All or nothing: every row is prepared first, then all are stored under one hold of the
        lock. A failure while preparing, or a caller that has given up (`ctl.cancelled`) when the
        write is about to be applied, stores nothing (BackendError for the cancelled call)."""
        require_writable(self)
        receipt = new_receipt()
        rows = []  # (ref, text, date, grams, fields)
        for index, message in enumerate(messages):
            if message["role"] == "user":
                text = message["content"]
                rows.append((receipt_ref(receipt, index), text, message_date(message),
                             index_grams(normalize(text)), message_fields(message)))
        if not rows:
            return None  # nothing kept: no receipt for nothing
        with self._lock:
            if ctl is not None and ctl.cancelled:
                raise BackendError("cancelled")
            for ref, text, date, grams, fields in rows:
                self._forget_grams(ref)  # replacing a ref must not count its old text twice
                self._set(ref, text, date, fields)
                self._frequency.update(grams)
        return receipt
