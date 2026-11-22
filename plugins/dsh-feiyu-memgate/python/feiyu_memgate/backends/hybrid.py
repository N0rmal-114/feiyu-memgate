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

Concurrency: `find` and `neighbours` ask every child at the same time, each with the whole budget
(one thread per child per call, owned here; the call waits for them until the budget is spent. A
child that has not answered by then is cancelled through its own control object and winds down alone;
`close()` cancels and joins such threads). The answers are ordered by child index before fusing, so the
result does not depend on which child was faster. With a single child the call runs inline with the
caller's own `ctl`.

Failure handling: a child that raises or does not answer in time during a search is skipped for that
search and recorded: `child_failures` (a list of ints, one per child, never reset) and the word
`child-failed` through the caller's `ctl.note` (the access log). Only when every child fails is it an error.
A write is all or nothing: if any child fails, the memories
are deleted again from every child (best effort; replacing an existing ref and failing
removes it, so re-put it). Children must be able to `put(text, ref=..., date=...)` and
`delete` when the hybrid is writable.

Record fields (model.FIELDS): `stores_fields` is True when any child's is. A write hands the fields only to
the children that store them (the others are called exactly as before, so a child written for the old
`put(text, ref=None, date=None)` keeps working). Fused hits and merged neighbours carry the fields of the
first child, in child order, that returned any for that memory; `records()` and `record(ref)` come from the
first child that stores fields (else from the first child that has the method).

Secrets: the `children` list is not walked by the configuration's `<name>_env` rule, so
a child option ending in `_env` (for example `key_env`) is resolved here from the process
environment, the same way.
"""
import inspect
import os
import socket
import threading
import time
from typing import Any

from .. import registry
from ..errors import BackendError, ConfigError
from ..registry import register
from .base import (DEFAULT_BUDGET, MemoryBackend, as_roles, clamp01, kept_messages, make_hit, message_fields,
                   new_receipt, parse_record, receipt_ref, require_writable)
from .base import today as _today

OVERFETCH = 3  # each child is asked for limit * 3 hits so that fusion has material to rank
CLOSE_WAIT = 2.0  # seconds close() waits for cancelled child calls to wind down


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


class _ChildCtl:
    """The control object of ONE child call: what children use of a `Task` (attach, detach, on_cancel,
    cancelled) with a socket slot of its own, so two children never share the parent's single slot.
    It is cancelled when the parent is, or by the hybrid when the child ran out of time.

    A parent that cannot register cancel hooks (no `on_cancel`: a plain object with attach/detach, as
    older callers pass) has no other way to reach the socket, so for it attach and detach are forwarded
    as well; the own slot stays the one that cancels."""

    def __init__(self, parent):
        self._forward = None
        self._forward_detach = None
        self._lock = threading.Lock()
        self._sock = None
        self._cancelled = False
        self._finished = False
        self._hooks = []
        hook = getattr(parent, "on_cancel", None)
        if callable(hook):
            hook(self.cancel)
        else:
            self._forward = getattr(parent, "attach", None)
            self._forward_detach = getattr(parent, "detach", None)

    @property
    def cancelled(self):
        with self._lock:
            return self._cancelled

    def attach(self, sock):
        with self._lock:
            if self._cancelled:
                self._kill(sock)
                raise BackendError("cancelled")
            self._sock = sock
        if callable(self._forward):
            self._forward(sock)

    def detach(self):
        with self._lock:
            self._sock = None
        if callable(self._forward_detach):
            self._forward_detach()

    def on_cancel(self, fn):
        with self._lock:
            if self._finished:
                return
            if not self._cancelled:
                self._hooks.append(fn)
                return
        self._quietly(fn)

    def cancel(self):
        with self._lock:
            self._cancelled = True
            sock = self._sock
            hooks, self._hooks = self._hooks, []
        if sock is not None:
            self._kill(sock)
        for fn in hooks:
            self._quietly(fn)

    def finish(self):
        with self._lock:
            self._finished = True
            self._hooks = []

    @staticmethod
    def _kill(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    @staticmethod
    def _quietly(fn):
        try:
            fn()
        except Exception:  # noqa: BLE001 - a failing hook must not stop the canceller
            pass


class _Run:
    """One child call on its own thread: its answer or its error, and its control object."""

    def __init__(self, index, fn, timeout, parent):
        self.index = index
        self.ctl = _ChildCtl(parent)
        self.done = threading.Event()
        self.thread = None
        self.on_exit = None
        self.result = None
        self.error = None
        self._fn, self._timeout = fn, timeout

    def run(self):
        try:
            self.result = self._fn(self._timeout, self.ctl)
        except BaseException as err:  # noqa: BLE001 - recorded here, the hybrid decides
            self.error = err
        finally:
            self.ctl.finish()
            self.done.set()
            if self.on_exit is not None:
                self.on_exit(self)


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
        self._state = threading.Lock()
        self._live = {}  # thread -> its _Run, for every child call that has not been reaped yet
        self._closed = False
        self._failures = [0] * len(children)
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
            # True when any child keeps the record fields (see the module docstring).
            self.stores_fields = any(getattr(child, "stores_fields", False) is True for child in self.children)
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

    def put(self, text, ref=None, date=None, timeout=None, ctl=None, fields=None):
        """Store one memory in every child under one ref and return it. `fields` go to the children that
        store them."""
        item = {"text": text, "ref": ref, "date": date, "fields": fields}
        return self.put_many([item], timeout=timeout, ctl=ctl)[0]

    def put_many(self, items, timeout=None, ctl=None):
        """Store several memories in every child (batched where the child can) and return the refs."""
        require_writable(self)
        rows = [parse_record(item) for item in items]
        if not rows:
            return []
        refs = [r[1] for r in rows]
        try:
            for child in self.children:
                self._child_put(child, rows, timeout, ctl)
        except Exception:  # noqa: BLE001 - any child failure rolls the write back and surfaces as one BackendError
            self._rollback(refs)
            raise BackendError("a child failed to store; the write was rolled back") from None
        return refs

    @staticmethod
    def _child_put(child, rows, timeout, ctl):
        """Write `rows` ((text, ref, date, fields)) to one child; only a child that stores fields sees them."""
        keeps = getattr(child, "stores_fields", False) is True
        if hasattr(child, "put_many"):
            batch: list[Any]
            if keeps:
                batch = [{"text": text, "ref": ref, "date": date, "fields": fields} for text, ref, date, fields in rows]
            else:
                batch = [(text, ref, date) for text, ref, date, _ in rows]
            child.put_many(batch, **_extras(child.put_many, timeout, ctl))
            return
        for text, ref, date, fields in rows:
            if keeps:
                child.put(text, ref=ref, date=date, fields=fields)
            else:
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
        items: list[Any] = []
        for i, content, date in kept:
            fields = message_fields(messages[i])
            ref = receipt_ref(receipt, i)
            items.append({"text": content, "ref": ref, "date": date, "fields": fields} if fields
                         else (content, ref, date))
        self.put_many(items, timeout=timeout, ctl=ctl)
        return receipt

    def receipt_refs(self, receipt):
        """The refs a write produced, as the first child that can tell reports them."""
        for child in self.children:
            if hasattr(child, "receipt_refs"):
                return child.receipt_refs(receipt)
        return None

    # ------------------------------------------------------------ reading

    @property
    def child_failures(self):
        """How many searches each child failed (raised, or did not answer in time), in child order, never reset."""
        with self._state:
            return list(self._failures)

    def _record_failed(self, indexes, ctl):
        """Count failed children and say so for the access log."""
        if not indexes:
            return
        with self._state:
            for index in indexes:
                self._failures[index] += 1
        if getattr(ctl, "cancelled", False):
            return  # the caller gave up: its doing, not the children's
        note = getattr(ctl, "note", None)
        if callable(note):
            try:
                note("child-failed")
            except Exception:  # noqa: BLE001 - a note must never fail a search
                pass

    def _forget(self, run):
        with self._state:
            self._live.pop(run.thread, None)

    def _start(self, run):
        """Start the thread of one child call; False when none could be started."""
        thread = threading.Thread(target=run.run, name="hybrid-child", daemon=True)
        with self._state:
            if self._closed:
                return False
            self._live[thread] = run
        run.thread = thread
        run.on_exit = self._forget
        try:
            thread.start()
        except Exception:  # noqa: BLE001 - no thread to run it on: the child fails
            with self._state:
                self._live.pop(thread, None)
            return False
        return True

    def _gather(self, jobs, total, ctl):
        """Run `jobs` ((child index, fn(timeout, ctl))) at the same time, each with the whole budget `total`.
        Returns (answers ordered by child index, failed child indexes). A child that has not answered when
        the budget is spent is cancelled and counts as failed."""
        answers, failed = [], []
        if len(jobs) == 1:  # nothing to run beside it: inline, with the caller's own ctl
            index, fn = jobs[0]
            try:
                answers.append((index, fn(total, ctl)))
            except Exception:  # noqa: BLE001 - one failing child must not silence the others
                failed.append(index)
            self._record_failed(failed, ctl)
            return answers, failed
        end = time.monotonic() + total
        runs = [_Run(index, fn, total, ctl) for index, fn in jobs]
        for run in runs:
            if not self._start(run):
                run.error = BackendError("no thread for the child call")
                run.ctl.finish()
                run.done.set()
        for run in runs:
            run.done.wait(max(end - time.monotonic(), 0.0))
        for run in runs:  # runs are in child order
            if not run.done.is_set():
                run.ctl.cancel()
                failed.append(run.index)
            elif run.error is not None:
                if not isinstance(run.error, Exception):
                    raise run.error  # KeyboardInterrupt and the like belong to the caller
                failed.append(run.index)
            else:
                answers.append((run.index, run.result))
            if run.thread is not None and run.done.is_set():
                run.thread.join()  # it only has to leave its function: instant
        self._record_failed(failed, ctl)
        return answers, failed

    def find(self, query, limit, timeout=None, ctl=None):
        total = self._timeout if timeout is None else max(0.0, float(timeout))
        ask = max(1, int(limit)) * OVERFETCH
        jobs = [(index, lambda left, cctl, c=child: c.find(query, ask, timeout=left, ctl=cctl))
                for index, child in enumerate(self.children)]
        answers, failed = self._gather(jobs, total, ctl)
        if len(failed) == len(self.children):
            raise BackendError("every child failed")
        return self._fuse(answers)[:limit]

    def _fuse(self, answers):
        merged: dict[Any, dict[str, Any]] = {}  # ref -> {"text", "date", "scores": [per child], "cosine", "fields"}
        for index, hits in answers:
            for hit in hits:
                entry = merged.setdefault(hit.ref, {"text": "", "date": None, "scores": [None] * len(self.children),
                                                    "cosine": None, "fields": {}})
                entry["scores"][index] = clamp01(float(hit.score))
                entry["text"] = entry["text"] or hit.text
                entry["date"] = entry["date"] or hit.date
                entry["fields"] = entry["fields"] or dict(getattr(hit, "fields", None) or {})  # first child wins
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
            fused.append(make_hit(ref, 1.0 - miss, entry["text"], entry["date"], meta, entry["fields"]))
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
        capable = [(index, child) for index, child in enumerate(self.children)
                   if callable(getattr(child, "neighbours", None))]

        def rows_of(child, left, cctl):
            hits = child.neighbours(ref, limit, **_extras(child.neighbours, left, cctl))
            return [(h.ref, clamp01(float(h.score)), h) for h in hits]

        jobs = [(index, lambda left, cctl, c=child: rows_of(c, left, cctl)) for index, child in capable]
        # ref -> [highest score, its hit, first text found, first date found, first fields found]
        best: dict[Any, list[Any]] = {}
        answers = self._gather(jobs, total, ctl)[0] if jobs else []
        for _, rows in answers:
            for hit_ref, score, hit in rows:
                if hit_ref == ref:
                    continue
                entry = best.setdefault(hit_ref, [score, hit, "", None, {}])
                if score > entry[0]:
                    entry[0], entry[1] = score, hit
                entry[2] = entry[2] or hit.text
                entry[3] = entry[3] or hit.date
                entry[4] = entry[4] or dict(getattr(hit, "fields", None) or {})  # first child wins
        out = []
        for hit_ref, (score, hit, text, date, fields) in best.items():
            meta = dict(getattr(hit, "meta", None) or {})
            out.append(make_hit(hit_ref, score, text, date, meta, fields))
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

    def _keeper(self, name):
        """`name` of the first child that stores fields and has it, else of the first child that has it."""
        for child in self.children:
            if getattr(child, "stores_fields", False) is True and hasattr(child, name):
                return getattr(child, name)
        return self._first(name)

    def records(self):
        """{"ref", "text", "date", "fields"} of every memory, from the first child that stores fields."""
        return self._keeper("records")()

    def record(self, ref):
        """{"ref", "text", "date", "fields"} of one memory (KeyError when not stored), from the first child
        that stores fields."""
        return self._keeper("record")(ref)

    # ------------------------------------------------------------ lifecycle

    def health(self, timeout=2.0):
        """True when every child is healthy. Never raises."""
        try:
            return all(child.health(timeout) for child in self.children)
        except Exception:  # noqa: BLE001
            return False

    def close(self):
        """Cancel and wait for child calls still winding down, then close the children."""
        with self._state:
            self._closed = True
            live = list(self._live.items())
        for _, run in live:
            run.ctl.cancel()
        end = time.monotonic() + CLOSE_WAIT
        for thread, _ in live:
            thread.join(max(end - time.monotonic(), 0.0))
        with self._state:
            self._live.clear()
        for child in self.children:
            try:
                child.close()
            except Exception:  # noqa: BLE001
                pass
