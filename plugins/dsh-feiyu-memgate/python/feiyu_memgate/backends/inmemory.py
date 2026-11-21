"""In-memory backend: a list in this process, searched by shared characters.

No model, no files, nothing kept after the process ends. It exists for tests,
demos and as the smallest example of a backend. Standard library only.

Scoring is the same as the sqlite backend's: IDF-weighted containment of the query's grams
(character bigrams and single CJK ideographs), where a gram's weight comes from how many of the
memories held here contain it (the counts are kept up to date by put and delete).
"""
import collections
import threading

from ..core.tidy import PreparedQuery, gram_weights, index_grams, normalize
from ..registry import register
from ..model import Hit
from .base import MemoryBackend, message_date, new_receipt, new_ref, receipt_ref, require_writable


@register("backend", "inmemory")
class InMemoryBackend(MemoryBackend):
    """Options: writable (default True), memories (texts to start with)."""

    # Scores are IDF-weighted keyword overlap: the gateway's min_score stage starts from this
    # (the same scoring as the sqlite backend, chosen on the development set bench/dev).
    default_min_score = 0.30
    default_relative_ratio = 0.75  # see the sqlite backend
    broad_min_score = 0.25  # the "broad" profile, same numbers and rule as the sqlite backend
    broad_relative_ratio = 0.70

    def __init__(self, writable=True, memories=()):
        self.writable = bool(writable)
        self._lock = threading.Lock()
        self._items = {}  # ref -> (text, date or None)
        self._frequency = collections.Counter()  # gram -> how many memories hold it
        for text in memories:
            self._store(text)  # seeding works for a read-only store too

    def put(self, text, ref=None, date=None):
        """Store one memory text as it is and return its ref."""
        require_writable(self)
        return self._store(text, ref, date)

    def _store(self, text, ref=None, date=None):
        ref = ref or new_ref()
        grams = index_grams(normalize(text))
        with self._lock:
            self._forget_grams(ref)  # replacing a ref must not count its old text twice
            self._items[ref] = (text, date)
            self._frequency.update(grams)
        return ref

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
            items = list(self._items.items())
            if len(prepared.norm) >= 2:  # the weights are read under the lock: they match `items`
                prepared = PreparedQuery(query, gram_weights(prepared.norm, self._frequency.get, len(items)))
        hits = [Hit(ref=ref, score=prepared.score(text), text=text, date=date) for ref, (text, date) in items]
        hits = [h for h in hits if h.score > 0]
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

    def read(self, ref, timeout=None, ctl=None):
        with self._lock:
            return self._items[ref][0]

    def remember(self, messages, timeout=None, ctl=None):
        require_writable(self)
        receipt = new_receipt()
        kept = 0
        for index, message in enumerate(messages):
            if message["role"] == "user":
                self._store(message["content"], ref=receipt_ref(receipt, index),
                            date=message_date(message))
                kept += 1
        return receipt if kept else None  # nothing kept: no receipt for nothing
