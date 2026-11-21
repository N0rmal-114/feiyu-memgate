"""Hybrid backend: several backends behind one, their results fused. Standard library only.

Typical use: a keyword store (sqlite) next to a vector store (embedding). The keyword side
is exact on shared wording and cheap; the vector side finds paraphrases. A memory is written
to every child under one ref chosen here, and a search asks every child and fuses by ref.

Fusion: the gateway sorts by `score`, so the fused score carries the ranking itself and stays
a 0..1 relevance. It is the probabilistic OR of the children's scores,
`1 - prod(1 - w_i * s_i)` (weights clamped to 0..1): a memory both children like outranks one
only a child likes, and one strong signal alone still scores high. It assumes the children's
scores mean roughly the same thing (relevance), which the sqlite and embedding backends aim at.
It was compared with the larger score, a calibrated OR, a calibrated max with an agreement bonus,
reciprocal-rank fusion for the order and OR with a smaller keyword weight, each at its own best
threshold on bench/dev (bge-m3; bench/experiments/fusion): none beat the OR by one question, so it
stays. What changes with fusion is the scale: two weak signals add up, so the OR needs a higher
threshold than either child (`default_min_score` 0.70, against 0.30 for sqlite and 0.50 for embedding).
Known limit: on paraphrases the keyword score of the right memory (mean 0.15) is lower than that
of the best memory of a no-answer question (mean 0.23), so with a strong embedding model the
keyword child adds little there and the line it forces up costs delivered hits.
`Hit.meta` (when Hit has it) carries each child's own score under "child_scores" (None where
a child did not return the memory) and the vector child's raw cosine.

Failure handling: a child that raises during a search is skipped for that search; only when
every child fails is it an error. A write is all or nothing: if any child fails, the memories
are deleted again from every child (best effort; replacing an existing ref and failing
removes it, so re-put it). Children must be able to `put(text, ref=..., date=...)` and
`delete` when the hybrid is writable.

Secrets: the `children` list is not walked by the configuration's `<name>_env` rule, so
a child option ending in `_env` (for example `key_env`) is resolved here from the process
environment, the same way.
"""
import inspect
import os
import time

from .. import registry
from ..errors import BackendError, ConfigError
from ..registry import register
from .base import (DEFAULT_BUDGET, MemoryBackend, as_roles, clamp01, kept_messages, make_hit, new_receipt,
                   parse_item, receipt_ref, require_writable)
from .base import today as _today

OVERFETCH = 3  # each child is asked for limit * 3 hits so that fusion has material to rank


def _resolve_env(spec, where):
    """A copy of a child spec with `<name>_env` options replaced by `<name>` from os.environ."""
    out = {}
    for key, value in spec.items():
        if isinstance(value, dict):
            out[key] = _resolve_env(value, where + "." + key)
        elif key.endswith("_env") and key != "token_env":
            if not isinstance(value, str) or not os.environ.get(value):
                raise ConfigError("%s.%s: environment variable %s is not set" % (where, key, value))
            out[key[:-4]] = os.environ[value]
        else:
            out[key] = value
    return out


def _accepts(fn, name):
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _extras(fn, timeout, ctl):
    """The timeout/ctl keyword arguments `fn` understands."""
    extra = {}
    if timeout is not None and _accepts(fn, "timeout"):
        extra["timeout"] = timeout
    if ctl is not None and _accepts(fn, "ctl"):
        extra["ctl"] = ctl
    return extra


@register("backend", "hybrid")
class HybridBackend(MemoryBackend):
    """Options: children (a list of backend specs, each a table with a `type`), weights (a
    list with one weight per child, default all 1.0, clamped to 0..1), writable (default True),
    roles (message roles remember() keeps, default ("user",)), timeout (total budget of one
    find() in seconds when the caller gives none, default 10)."""

    tracks_writes = False
    # The probabilistic OR of a keyword and a vector score needs a higher line than either child:
    # on the development set bench/dev (floor 0.40) the balanced rule of bench/dev_eval.py picked 0.70
    # with ratio 0.85; lower lines let weak agreement of the two children through. Re-checked against
    # other fusions in bench/experiments/fusion: unchanged.
    default_min_score = 0.70
    default_relative_ratio = 0.85
    # The "broad" profile (bench/dev_eval.py --search --profile broad, bge-m3 child).
    broad_min_score = 0.40
    broad_relative_ratio = 0.65

    def __init__(self, children, weights=None, writable=True, roles=("user",), timeout=DEFAULT_BUDGET):
        roles = as_roles(roles)
        if not isinstance(children, (list, tuple)) or not children:
            raise ValueError("children must be a non-empty list of backend specs")
        if weights is None:
            weights = [1.0] * len(children)
        if not isinstance(weights, (list, tuple)) or len(weights) != len(children):
            raise ValueError("weights needs one number per child")
        self.weights = [clamp01(float(w)) for w in weights]
        self.writable = bool(writable)
        self.roles = tuple(roles)
        self._timeout = float(timeout)
        self.children = []
        try:
            for i, spec in enumerate(children):
                if isinstance(spec, dict):
                    spec = _resolve_env(spec, "children[%d]" % i)
                self.children.append(registry.create("backend", spec))
            if self.writable:
                for i, child in enumerate(self.children):
                    if not (hasattr(child, "put") and hasattr(child, "delete")):
                        raise ValueError("child %d cannot store a memory under a given ref (needs put and delete)" % i)
        except Exception:
            self.close()
            raise

    # ------------------------------------------------------------ writing

    def _rollback(self, refs):
        """Best effort: take `refs` out of every child again."""
        for child in self.children:
            for ref in refs:
                try:
                    child.delete(ref)
                except Exception:  # noqa: BLE001 - the original failure is what the caller must see
                    pass

    def put(self, text, ref=None, date=None, timeout=None, ctl=None):
        """Store one memory in every child under one ref and return it."""
        return self.put_many([(text, ref, date)], timeout=timeout, ctl=ctl)[0]

    def put_many(self, items, timeout=None, ctl=None):
        """Store several memories in every child (batched where the child can) and return the refs."""
        require_writable(self)
        rows = [parse_item(item) for item in items]
        if not rows:
            return []
        refs = [r[1] for r in rows]
        try:
            for child in self.children:
                self._child_put(child, rows, timeout, ctl)
        except Exception:
            self._rollback(refs)
            raise BackendError("a child failed to store; the write was rolled back") from None
        return refs

    @staticmethod
    def _child_put(child, rows, timeout, ctl):
        if hasattr(child, "put_many"):
            child.put_many(rows, **_extras(child.put_many, timeout, ctl))
            return
        for text, ref, date in rows:
            child.put(text, ref=ref, date=date)

    def delete(self, ref):
        """Remove a memory from every child. True when any child had it; a failing child
        makes the call fail after the others were tried."""
        require_writable(self)
        found, failed = False, False
        for child in self.children:
            try:
                found = bool(child.delete(ref)) or found
            except PermissionError:
                raise
            except Exception:  # noqa: BLE001
                failed = True
        if failed:
            raise BackendError("a child failed to delete")
        return found

    def remember(self, messages, timeout=None, ctl=None):
        """Put every message whose role is in `roles` into every child. Returns a receipt id,
        or None when nothing was kept."""
        require_writable(self)
        today = _today()
        kept = kept_messages(messages, self.roles, today)
        if not kept:
            return None
        receipt = new_receipt()
        self.put_many([(content, receipt_ref(receipt, i), date) for i, content, date in kept],
                      timeout=timeout, ctl=ctl)
        return receipt

    def receipt_refs(self, receipt):
        """The refs a write produced, as the first child that can tell reports them."""
        for child in self.children:
            if hasattr(child, "receipt_refs"):
                return child.receipt_refs(receipt)
        return None

    # ------------------------------------------------------------ reading

    def find(self, query, limit, timeout=None, ctl=None):
        total = self._timeout if timeout is None else max(0.0, float(timeout))
        end = time.monotonic() + total
        ask = max(1, int(limit)) * OVERFETCH
        answers, failures = [], 0
        for index, child in enumerate(self.children):
            left = max(0.0, end - time.monotonic()) / (len(self.children) - index)
            try:
                answers.append((index, child.find(query, ask, timeout=left, ctl=ctl)))
            except Exception:  # noqa: BLE001 - one failing child must not silence the others
                failures += 1
        if failures == len(self.children):
            raise BackendError("every child failed")
        return self._fuse(answers)[:limit]

    def _fuse(self, answers):
        merged = {}  # ref -> {"text", "date", "scores": [per child], "cosine"}
        for index, hits in answers:
            for hit in hits:
                entry = merged.setdefault(hit.ref, {"text": "", "date": None, "scores": [None] * len(self.children),
                                                    "cosine": None})
                entry["scores"][index] = clamp01(float(hit.score))
                entry["text"] = entry["text"] or hit.text
                entry["date"] = entry["date"] or hit.date
                cosine = (getattr(hit, "meta", None) or {}).get("cosine")
                if cosine is not None:
                    entry["cosine"] = cosine
        fused = []
        for ref, entry in merged.items():
            miss = 1.0
            for weight, score in zip(self.weights, entry["scores"]):
                if score is not None:
                    miss *= 1.0 - weight * score
            meta = {"child_scores": entry["scores"]}
            if entry["cosine"] is not None:
                meta["cosine"] = entry["cosine"]
            fused.append(make_hit(ref, 1.0 - miss, entry["text"], entry["date"], meta))
        fused.sort(key=lambda h: (-h.score, h.ref))
        return fused

    def read(self, ref, timeout=None, ctl=None):
        """The text from the first child that has it. KeyError when none does."""
        errored = False
        for child in self.children:
            try:
                return child.read(ref, **_extras(child.read, timeout, ctl))
            except KeyError:
                continue
            except Exception:  # noqa: BLE001
                errored = True
        if errored:
            raise BackendError("cannot read the memory")
        raise KeyError(ref)

    def neighbours(self, ref, limit, timeout=None, ctl=None):
        """The memories most similar to the stored memory `ref`: every child that has `neighbours` is asked,
        the answers are merged by ref (the higher score wins) and cut to `limit`, best first. A child that
        fails is skipped; with no child that has the method (or none that answers) the answer is []."""
        total = self._timeout if timeout is None else max(0.0, float(timeout))
        end = time.monotonic() + total
        capable = [child for child in self.children if callable(getattr(child, "neighbours", None))]
        # ref -> [highest score, its hit, first text found, first date found]
        best = {}
        for index, child in enumerate(capable):
            left = max(0.0, end - time.monotonic()) / (len(capable) - index)
            try:
                hits = child.neighbours(ref, limit, **_extras(child.neighbours, left, ctl))
                rows = [(h.ref, clamp01(float(h.score)), h) for h in hits]
            except Exception:  # noqa: BLE001 - one failing child must not silence the others
                continue
            for hit_ref, score, hit in rows:
                if hit_ref == ref:
                    continue
                entry = best.setdefault(hit_ref, [score, hit, "", None])
                if score > entry[0]:
                    entry[0], entry[1] = score, hit
                entry[2] = entry[2] or hit.text
                entry[3] = entry[3] or hit.date
        out = []
        for hit_ref, (score, hit, text, date) in best.items():
            meta = dict(getattr(hit, "meta", None) or {})
            out.append(make_hit(hit_ref, score, text, date, meta))
        out.sort(key=lambda h: (-h.score, h.ref))
        return out[:max(0, int(limit))]

    def _first(self, name):
        for child in self.children:
            if hasattr(child, name):
                return getattr(child, name)
        raise AttributeError(name)

    def refs(self):
        """(ref, date) of every memory, as the first child lists them."""
        return self._first("refs")()

    def count(self):
        return self._first("count")()

    def items(self):
        return self._first("items")()

    # ------------------------------------------------------------ lifecycle

    def health(self, timeout=2.0):
        """True when every child is healthy. Never raises."""
        try:
            return all(child.health(timeout) for child in self.children)
        except Exception:  # noqa: BLE001
            return False

    def close(self):
        for child in self.children:
            try:
                child.close()
            except Exception:  # noqa: BLE001
                pass
