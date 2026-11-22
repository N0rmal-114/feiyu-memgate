"""The recall engine: search every store in parallel, then pass the candidates through the stages.

A recall stage is one piece of the puzzle. It can act at two moments:

- prepare(candidates, ctx): once, on the whole list, before it is sorted by score.
  Cheap work only (no backend calls): drop by score, change scores, re-weight by source.
- admit(candidate, ctx): on one candidate at a time, best score first, until the
  limit is reached. It answers KEEP, DROP (not usable) or DEFER (usable, but it does
  not fit: counted in "more"). Slow work goes here, so it is only done for candidates
  that are about to be returned.

Stages are shared between requests and must keep no state of their own; per-request
state goes in ctx.state.

One piece failing never takes the request down silently:
- a store whose find() raises, times out or answers with something that is not a list
  of hits fails alone (counted in "failed");
- a stage whose admit() raises drops that candidate (fail closed) and the request gets
  the note "stage-error:<stage>"; a verdict that is not KEEP/DROP/DEFER (a forgotten
  `return`) is a DROP with the note "bad-verdict:<class>";
- a stage whose prepare() raises fails the whole recall (StageError): there is no safe
  way to go on without it.

An explained recall (ctx.trace, see Trace) runs the same steps and also records what became of
every candidate and which stage decided; it does not run the stages' finish(), so it counts as
no use. Without it nothing is recorded and nothing extra is done.
Each such note is also one "piece-error" log line, with names and exception types only.

Standard library only.
"""
import math
import time
from typing import Any

from ..model import Candidate, Hit

KEEP, DROP, DEFER = "keep", "drop", "defer"
VERDICTS = (KEEP, DROP, DEFER)

RECALL_DEADLINE = 3.0
MAX_FETCH = 30
MAX_WAIT = 0.25          # the longest a call waits for a free worker
STAGE_SHARE = 0.2        # part of the deadline the finds leave to the stages...
MAX_STAGE_RESERVE = 0.5  # ...at most this many seconds
EXPLAIN_TEXT = 300       # characters of a candidate's text in an explained recall
EXPLAIN_MAX = 100        # candidates listed in an explained recall

# Outcomes of one pool call.
OK, BUSY, FAILED = "ok", "busy", "failed"


class StageError(Exception):
    """A stage's prepare() failed: the recall cannot be answered."""

    def __init__(self, name):
        super().__init__(name)
        self.name = name


def piece_name(piece):
    """The registered name of a stage or guard when known, else its class name."""
    name = getattr(piece, "name", None)
    return name if isinstance(name, str) and name else type(piece).__name__


class RecallStage:
    """Base class of a recall stage. Override prepare, admit or both.

    name      the registered name; registry.create fills it in (None: the class name is used)
    cheap     True when admit() does no I/O and no heavy work: it then also runs on the
              candidates past the limit, so an obvious drop is not counted in "more"
    enforces  what the stage guarantees, for the configuration check: ("redaction",)
    """

    name = None
    cheap = False
    enforces: tuple[str, ...] = ()

    def prepare(self, candidates, ctx):
        return candidates

    def admit(self, candidate, ctx):
        return KEEP

    def finish(self, ctx):
        """Called once per request, after every candidate has been looked at; ctx.accepted
        holds what is returned. For bookkeeping (such as counting uses); must not change
        the answer. A failure here is noted ("finish-failed") and never fails the recall."""


class RecallContext:
    """What a stage may know about the request it is working on.

    query, limit      the request
    client, tenant_id who asks (names, never tokens)
    tenant_source     the name of the tenants provider whose store is part of this request
                      (None when there is none): a candidate from that source belongs to tenant_id
    public            True when the answer is shown to other people
    audience          who sees the answer: the client's reach, "owner", "group" or "public"
    show_fields       the record fields (model.FIELDS) the answer adds to each item (the client's `fields`)
    redact            the text cleaner chosen for this client (str -> str)
    context           the request's optional "context" object (small, already checked)
    accepted          the candidates kept so far, in order
    notes             words for the access log (reasons only, never content)
    state             a dict for the stages' own per-request state
    trace             a Trace when the request is an explained recall (trace=True), else None

    The gateway also gives it the log, the per-store call slots and how many workers a
    tenant request must leave free (keep); a stage only uses them through run().
    """

    def __init__(self, query, limit, *, pool, clock=time.monotonic, deadline=None, public=False,
                 redact=None, client=None, tenant_id=None, notes=None, tenant_source=None, context=None,
                 log=None, slots=None, keep=0, audience="owner", trace=False, show_fields=()):
        self.query = query
        self.limit = limit
        self.pool = pool
        self.clock = clock
        now = clock()
        self.deadline = now + RECALL_DEADLINE if deadline is None else deadline
        # The finds stop a little before the deadline, so the stages still have time.
        self.find_deadline = self.deadline - min(MAX_STAGE_RESERVE, STAGE_SHARE * max(self.deadline - now, 0.0))
        self.public = public
        self.audience = audience
        self.show_fields = tuple(show_fields)
        self.redact = redact or (lambda text: text)
        self.client = client
        self.tenant_id = tenant_id
        self.tenant_source = tenant_source
        self.context = dict(context or {})
        self.notes = notes if notes is not None else []
        self.accepted = []
        self.state = {}
        self.log = log
        self.slots = slots
        self.keep = keep
        self.trace = Trace() if trace else None

    def time_left(self):
        return self.deadline - self.clock()

    def expired(self):
        return self.clock() >= self.deadline

    def wait_budget(self):
        """How long a call may wait for a free worker: briefly, never most of the time left."""
        return min(MAX_WAIT, max(self.time_left(), 0.0) / 4)

    def run(self, fn, store=None, until=None):
        """Run fn(ctl) on the pool and wait until the deadline. Returns (ok, result).

        When the time runs out the call is cancelled (its socket is shut down, its cancel
        hooks run) and we return at once. Name the `store` the call is for, so the
        per-store cap applies. No free worker counts as not ok. `until` (a time on the
        context's clock) ends the wait earlier than the deadline, for a step that must
        leave time to the stages after it; it never extends the deadline.
        """
        end = self.deadline if until is None else min(until, self.deadline)
        outcome, result = call(self.pool, fn, self.clock, end, wait=self.wait_budget(),
                               keep=self.keep, slots=self.slots, store=store)
        return outcome == OK, result

    def note(self, word):
        """Add a word for the access log, once per request."""
        if word not in self.notes:
            self.notes.append(word)

    def fault(self, note, detail):
        """A piece failed: note it once per request and write one log line.
        Names and exception types only, never content."""
        if note is not None:
            if note in self.notes:
                return
            self.notes.append(note)
        if self.log is not None:
            line = "piece-error client=%s %s" % (self.client or "-", " ".join(w for w in (note, detail) if w))
            try:
                self.log(line)
            except Exception:  # noqa: BLE001 - logging must not fail the recall
                pass


class Trace:
    """What an explained recall records: one entry per candidate, keyed by (source, ref) since a
    stage may return a new object for the same candidate. `by` is the stage that gave the last
    non-KEEP verdict (set by _verdict, so the verdicts cost nothing extra when there is no trace)."""

    def __init__(self):
        self.entries = {}
        self.by = None
        self.delivered = []

    def _entry(self, candidate, added_by=None):
        key = (candidate.source, candidate.ref)
        entry = self.entries.get(key)
        if entry is None:
            entry = self.entries[key] = {"found": candidate.score, "added_by": added_by}
        entry.update(candidate=candidate, outcome=None, stage=None)
        return entry

    def found(self, candidates):
        for candidate in candidates:
            self._entry(candidate)

    def prepared(self, stage, before, after):
        """`stage`'s prepare() turned `before` into `after`: the missing ones it dropped, the new ones it added."""
        name = piece_name(stage)
        kept = {(c.source, c.ref) for c in after}
        for candidate in before:
            if (candidate.source, candidate.ref) not in kept:
                self._entry(candidate).update(outcome="dropped", stage=name)
        for candidate in after:
            key = (candidate.source, candidate.ref)
            if key in self.entries:
                self.entries[key].update(candidate=candidate, outcome=None, stage=None)
            else:
                self._entry(candidate, added_by=name)

    def decided(self, candidate, outcome, stage=None):
        entry = self._entry(candidate)
        entry.update(outcome=outcome, stage=None if stage is None else piece_name(stage))
        if outcome == "delivered":
            self.delivered.append(entry)

    def not_reached(self, candidates):
        for candidate in candidates:
            self.decided(candidate, "not_reached")

    def report(self, ctx):
        """The candidates as the answer lists them: delivered first, then by score. Returns (list, cut)."""
        first = {id(e) for e in self.delivered}
        rest = sorted((e for e in self.entries.values() if id(e) not in first), key=lambda e: -e["candidate"].score)
        out: list[dict[str, Any]] = []
        cut = False
        for entry in self.delivered + rest:
            if len(out) >= EXPLAIN_MAX:
                cut = True
                break
            out.append(self._row(entry, ctx))
        return out, cut

    @staticmethod
    def _row(entry, ctx):
        candidate = entry["candidate"]
        row = {"source": candidate.source, "id": candidate.ref, "found": entry["found"], "score": candidate.score,
               "outcome": entry["outcome"] or "dropped", "stage": entry["stage"], "added_by": entry["added_by"]}
        text = candidate.text
        if isinstance(text, str) and text:
            try:
                text = ctx.redact(text)  # redact first, then cut: a mask must not be split by the cut
            except Exception:  # noqa: BLE001 - a redactor that fails shows nothing (fail closed)
                text = ""
            if isinstance(text, str) and text:
                if len(text) > EXPLAIN_TEXT:
                    text, row["cut"] = text[:EXPLAIN_TEXT], True
                row["text"] = text
        if candidate.date:
            row["date"] = candidate.date
        if candidate.fields:
            row["fields"] = dict(candidate.fields)
        return row


def _item(candidate, ctx):
    """The answer's item for an accepted candidate: as_item() and, when the client lists record fields
    (ctx.show_fields), "fields" with the listed ones the memory has (left out when it has none of them).
    For a public answer each value goes through the redactor too; one that comes out empty is left out."""
    item = candidate.as_item()
    shown = {}
    for name in getattr(ctx, "show_fields", ()):
        value = candidate.fields.get(name)
        if isinstance(value, str) and value:
            if getattr(ctx, "public", False):
                try:
                    value = ctx.redact(value)
                except Exception:  # noqa: BLE001 - a redactor that fails shows nothing (fail closed)
                    value = ""
            if isinstance(value, str) and value:
                shown[name] = value
    if shown:
        item["fields"] = shown
    return item


def submit(pool, fn, *, wait=0.0, keep=0, slots=None, store=None):
    """Start fn(ctl) on the pool. Returns the task, or None when the call never started:
    no worker became free within `wait` seconds, or `store` already has as many calls in
    flight as `slots` allows."""
    end = time.monotonic() + max(wait, 0.0)
    if slots is not None and store is not None:
        if not slots.acquire(store, wait):
            return None
        inner = fn

        def fn(ctl):
            try:
                return inner(ctl)
            finally:
                slots.release(store)

        task = pool.submit(fn, wait=max(end - time.monotonic(), 0.0), keep=keep)
        if task is None:
            slots.release(store)
        return task
    return pool.submit(fn, wait=max(end - time.monotonic(), 0.0), keep=keep)


def call(pool, fn, clock, deadline, *, wait=0.0, keep=0, slots=None, store=None):
    """Run fn(ctl) on the pool until `deadline` (on `clock`). Returns (outcome, result):
    OK; BUSY when it never started (nothing reached the store); FAILED when it raised or
    ran out of time (then it is cancelled; it may have reached the store)."""
    task = submit(pool, fn, wait=wait, keep=keep, slots=slots, store=store)
    if task is None:
        return BUSY, None
    if not task.wait(deadline - clock()):
        task.cancel()
        return FAILED, None
    if not task.ok():
        return FAILED, task.error
    return OK, task.result


def run_bounded(pool, fn, clock, deadline):
    """(ok, result) of one call without waiting for a worker; see call()."""
    outcome, result = call(pool, fn, clock, deadline)
    return outcome == OK, result if outcome == OK else None


def _hits(result):
    """`result` as a list of Hit, or None when it is not a list of Hit-like objects."""
    if not isinstance(result, (list, tuple)):
        return None
    out = []
    for hit in result:
        ref, score = getattr(hit, "ref", None), getattr(hit, "score", None)
        text, date, meta = getattr(hit, "text", ""), getattr(hit, "date", None), getattr(hit, "meta", None)
        fields = getattr(hit, "fields", None)
        if text is None:
            text = ""
        if (not isinstance(ref, str) or isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(score) or not isinstance(text, str)
                or (date is not None and not isinstance(date, str))):
            return None
        out.append(Hit(ref=ref, score=float(score), text=text, date=date,
                       meta=dict(meta) if isinstance(meta, dict) else {},
                       fields=dict(fields) if isinstance(fields, dict) else {}))
    return out


def find_all(stores, query, fetch, ctx):
    """Submit all finds first (they run in parallel), then collect them.

    Returns (hits_by_source, failed, busy). A store that raised, timed out or answered
    with something that is not a list of hits is failed; one whose find could not start
    (no free worker, or its own calls cap reached) is busy. An empty answer is neither.
    """
    wait_until = time.monotonic() + ctx.wait_budget()
    tasks = {}
    for source, store in stores.items():
        tasks[source] = submit(
            ctx.pool, lambda t, s=store: s.find(query, fetch, max(ctx.find_deadline - ctx.clock(), 0.05), t),
            wait=max(wait_until - time.monotonic(), 0.0), keep=ctx.keep, slots=ctx.slots, store=store)
    out: dict[Any, list[Any]] = {}
    failed = busy = 0
    for source, task in tasks.items():
        out[source] = []
        if task is None:
            busy += 1
            continue
        finished = task.wait(ctx.find_deadline - ctx.clock())
        if not finished:
            task.cancel()
        for word in task.notes:  # what the store itself reported (for example a failed child)
            ctx.note(word)
        if not finished:
            failed += 1
        elif not task.ok():
            ctx.fault(None, "store=%s where=find error=%s" % (source, type(task.error).__name__))
            failed += 1
        else:
            hits = _hits(task.result)
            if hits is None:
                ctx.fault(None, "store=%s where=find error=bad-result" % source)
                failed += 1
            else:
                out[source] = hits
    return out, failed, busy


def _prepared(stage, candidates, ctx):
    """The stage's prepare() output, checked. Raises StageError when it failed."""
    prepare = getattr(stage, "prepare", None)
    if prepare is None:
        return candidates
    name = piece_name(stage)
    try:
        out = list(prepare(candidates, ctx))
        ok = all(isinstance(c, Candidate) and not isinstance(c.score, bool) and isinstance(c.score, (int, float))
                 and math.isfinite(c.score) for c in out)
        detail = "where=prepare error=bad-result"
    except Exception as error:  # noqa: BLE001 - any failure of a stage is its own
        ok, detail = False, "where=prepare error=%s" % type(error).__name__
    if not ok:
        ctx.fault("stage-error:%s" % name, detail)
        raise StageError(name)
    return out


def _verdict(candidate, stages, ctx, cheap_only=False):
    """KEEP, DROP or DEFER for one candidate: the first stage that does not keep it decides.
    In an explained recall that stage is left in ctx.trace.by."""
    trace = getattr(ctx, "trace", None)  # a stand-in context (bench) may lack it
    for stage in stages:
        if cheap_only and not getattr(stage, "cheap", False):
            continue
        admit = getattr(stage, "admit", None)
        if admit is None:
            continue
        try:
            verdict = admit(candidate, ctx)
        except Exception as error:  # noqa: BLE001 - fail closed: the candidate is not returned
            ctx.fault("stage-error:%s" % piece_name(stage), "where=admit error=%s" % type(error).__name__)
            if trace is not None:
                trace.by = stage
            return DROP
        if verdict not in VERDICTS:
            ctx.fault("bad-verdict:%s" % type(stage).__name__, "stage=%s" % piece_name(stage))
            if trace is not None:
                trace.by = stage
            return DROP
        if verdict != KEEP:
            if trace is not None:
                trace.by = stage
            return verdict
    return KEEP


def _left_over(candidates, stages, ctx):
    """How many of the candidates past the limit are worth mentioning in "more": the ones
    no cheap stage drops. After the deadline the rest are counted as they are."""
    count = 0
    trace = getattr(ctx, "trace", None)
    for index, candidate in enumerate(candidates):
        if ctx.expired():
            ctx.note("deadline")
            if trace is not None:
                trace.not_reached(candidates[index:])
            return count + len(candidates) - index
        if _verdict(candidate, stages, ctx, cheap_only=True) != DROP:
            count += 1
            if trace is not None:
                trace.decided(candidate, "over_limit")
        elif trace is not None:
            trace.decided(candidate, "dropped", trace.by)
    return count


def _admit_all(candidates, stages, ctx):
    """Fill ctx.accepted, best score first. Returns "more"."""
    more = 0
    trace = getattr(ctx, "trace", None)
    for index, candidate in enumerate(candidates):
        if ctx.expired():
            # The deadline covers the stages too: stop, the rest are "more".
            ctx.note("deadline")
            if trace is not None:
                trace.not_reached(candidates[index:])
            return more + len(candidates) - index
        if len(ctx.accepted) >= ctx.limit:
            return more + _left_over(candidates[index:], stages, ctx)
        verdict = _verdict(candidate, stages, ctx)
        if verdict == DEFER:
            more += 1
        elif verdict == KEEP:
            ctx.accepted.append(candidate)
        if trace is not None:
            outcome = "delivered" if verdict == KEEP else "deferred" if verdict == DEFER else "dropped"
            trace.decided(candidate, outcome, None if verdict == KEEP else trace.by)
    return more


def _finish(stages, ctx):
    for stage in stages:
        finish = getattr(stage, "finish", None)
        if finish is None:
            continue
        try:
            finish(ctx)
        except Exception as error:  # noqa: BLE001 - bookkeeping must not fail the recall
            ctx.fault("finish-failed", "stage=%s where=finish error=%s" % (piece_name(stage), type(error).__name__))


def recall(stores, stages, ctx):
    """Merge, tidy and cap results from `stores` (source name -> backend), best score first.

    Returns {"results": [...], "more": int, "failed": int, "busy": int}. `failed` counts the
    stores that broke or timed out, `busy` the ones that could not be searched for want of
    a worker; `more` is at most how many good candidates were left out (limit reached, a
    stage deferred them, or the deadline came).

    With ctx.trace the answer also holds "stages" (their names, in order) and "candidates" (see
    Trace.report; "candidates_cut" when the list was cut), and the stages' finish() is not run.

    Bounded by ctx.deadline overall: the finds stop a little before it, and once it passes
    no more candidates are admitted. Raises StageError when a stage's prepare() fails.
    """
    fetch = min(ctx.limit * 3, MAX_FETCH)
    found, failed, busy = find_all(stores, ctx.query, fetch, ctx)
    candidates = []
    for source, hits in found.items():
        for hit in hits:
            candidates.append(Candidate(score=hit.score, source=source, store=stores[source], ref=hit.ref,
                                        text=hit.text, date=hit.date, meta=hit.meta, fields=dict(hit.fields)))
    trace = getattr(ctx, "trace", None)
    if trace is not None:
        trace.found(candidates)
    for stage in stages:
        out = _prepared(stage, candidates, ctx)
        if trace is not None:
            trace.prepared(stage, candidates, out)
        candidates = out
    candidates.sort(key=lambda c: -c.score)
    more = _admit_all(candidates, stages, ctx)
    answer = {"results": [_item(c, ctx) for c in ctx.accepted], "more": more, "failed": failed, "busy": busy}
    if trace is None:
        _finish(stages, ctx)
        return answer
    answer["stages"] = [piece_name(stage) for stage in stages]
    answer["candidates"], cut = trace.report(ctx)
    if cut:
        answer["candidates_cut"] = True
    return answer
