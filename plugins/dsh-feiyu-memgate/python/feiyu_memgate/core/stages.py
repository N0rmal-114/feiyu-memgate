"""The built-in recall stages. Each one is small on purpose: take the ones you want,
order them in the configuration, and write your own next to them (docs/pieces.md).

Standard library only.
"""
import datetime
import math
from typing import Any

from ..errors import ConfigError
from ..model import Candidate
from ..registry import register
from . import tidy
from .recall import DEFER, DROP, KEEP, RecallStage, _hits

PROBE_MARKER = "FEIYU-PROBE-"
MAX_ITEM_CHARS = 64 * 1024
MAX_OUTPUT_CHARS = 1500
MAX_READS = 3
MIN_SCORE = 0.5
RELATIVE_RATIO = 0.6


# "broad" is the default of the stages that take a profile; "precise" is the profile a user switches to.
PROFILES = ("precise", "broad")
DEFAULT_PROFILE = "broad"


def _fraction(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1


def check_profile(stage, profile):
    """The profile name, or a ConfigError naming `stage` when it is not one of PROFILES."""
    if profile not in PROFILES:
        raise ConfigError("%s: profile must be one of %s" % (stage, ", ".join(PROFILES)))
    return profile


def _declared(store, profile, default_attr, broad_attr, valid, fallback):
    """The score line a store declares for `profile`: its broad value when the profile is "broad" and the
    store has one (a valid, non-None attribute), else its default value, else `fallback`."""
    if profile == "broad":
        broad = getattr(store, broad_attr, None)
        if valid(broad):
            return broad
    declared = getattr(store, default_attr, fallback)
    return declared if valid(declared) else fallback


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@register("recall_stage", "min_score")
class MinScore(RecallStage):
    """Drop candidates under a similarity threshold.

    The threshold of a candidate is per_store[its source], else value when set, else the
    store's own default_min_score (a keyword backend declares a lower one than a vector
    backend, whose scores live on another scale), else 0.5.
    Options: value (default None: each store's own default), per_store ({store name: threshold}),
    profile ("broad", the default: the store's `broad_min_score`, a lower line that answers more questions
    and lets more noise through; or "precise": the store's `default_min_score`, about one memory per question).
    A store that declares no broad line keeps its `default_` line under either profile.
    An explicit value or per_store entry wins over the profile.
    """

    def __init__(self, value=None, per_store=None, profile="broad"):
        self.value = value
        self.per_store = dict(per_store or {})
        self.profile = check_profile("min_score", profile)

    def threshold(self, candidate):
        if candidate.source in self.per_store:
            return self.per_store[candidate.source]
        if self.value is not None:
            return self.value
        return _declared(candidate.store, self.profile, "default_min_score", "broad_min_score", _number, MIN_SCORE)

    def prepare(self, candidates, ctx):
        return [c for c in candidates if c.score >= self.threshold(c)]


@register("recall_stage", "relative_score")
class RelativeScore(RecallStage):
    """Drop candidates that score far below the best one of this request.

    The best candidate is the top-scoring one that reached this stage (so, after `min_score`, one
    that passed the threshold). A candidate stays when its score is at least `ratio` x the best
    score and, when `gap` is set, at least best - gap. Where one memory stands clear of the rest
    (the usual case in vector search) this removes the noise behind it without a global threshold;
    where several memories score alike they all stay. It only compares candidates with each other:
    a request whose best score is poor still keeps that best one, so `min_score` stays the stage
    that decides whether anything is worth saying.

    Scores of different backends spread differently (keyword scores of the right memory are often
    close to those of a wrong one, vector scores usually are not), so like `min_score` the ratio
    defaults to the store's own `default_relative_ratio`, else 0.6.

    Put it after every stage that changes scores (`min_score`, `source_weight`, `freshness`): it
    then compares the scores that decide the order. Options: ratio (0..1; default None: each
    store's own; 0 keeps everything), gap (a score difference, default None: no gap rule), profile
    ("broad", the default: the store's `broad_relative_ratio`, which keeps more candidates; or "precise": the
    store's `default_relative_ratio`; a store that declares no broad line keeps its `default_` line under
    either profile). An explicit ratio wins over the profile.
    """

    cheap = True

    def __init__(self, ratio=None, gap=None, profile="broad"):
        self.profile = check_profile("relative_score", profile)
        if ratio is not None and not _fraction(ratio):
            raise ConfigError("relative_score: ratio must be a number between 0 and 1")
        if gap is not None and (isinstance(gap, bool) or not isinstance(gap, (int, float))
                                or not math.isfinite(gap) or gap < 0):
            raise ConfigError("relative_score: gap must be a number >= 0 or absent")
        self.ratio = None if ratio is None else float(ratio)
        self.gap = None if gap is None else float(gap)

    def ratio_of(self, candidate):
        if self.ratio is not None:
            return self.ratio
        return float(_declared(candidate.store, self.profile, "default_relative_ratio", "broad_relative_ratio",
                               _fraction, RELATIVE_RATIO))

    def cutoff(self, candidate, best):
        """The lowest score that stays for `candidate` when the best score is `best`."""
        line = self.ratio_of(candidate) * best
        if self.gap is not None:
            line = max(line, best - self.gap)
        return line

    def prepare(self, candidates, ctx):
        if not candidates:
            return candidates
        best = max(c.score for c in candidates)
        return [c for c in candidates if c.score >= self.cutoff(c, best)]


@register("recall_stage", "source_weight")
class SourceWeight(RecallStage):
    """Multiply the score by a weight per store, so one source ranks before another.

    Put it after min_score: the threshold then looks at similarity only.
    Options: weights ({store name: weight}), default (weight of unnamed stores, 1.0).
    """

    def __init__(self, weights=None, default=1.0):
        self.weights = dict(weights or {})
        self.default = default

    def prepare(self, candidates, ctx):
        for c in candidates:
            c.meta.setdefault("similarity", c.score)
            c.score = c.score * self.weights.get(c.source, self.default)
        return candidates


@register("recall_stage", "drop_marker")
class DropMarker(RecallStage):
    """Leave out memories that hold a marker string, such as the one a write test leaves behind.

    Options: markers (default ["FEIYU-PROBE-"]). Not in the default stages: nothing in this
    repository writes such markers.
    """

    cheap = True

    def __init__(self, markers=(PROBE_MARKER,)):
        self.markers = tuple(markers)

    def _marked(self, text):
        return any(marker in text for marker in self.markers)

    def prepare(self, candidates, ctx):
        return [c for c in candidates if not self._marked(c.text)]

    def admit(self, candidate, ctx):
        return DROP if self._marked(candidate.text) else KEEP


@register("recall_stage", "fill_text")
class FillText(RecallStage):
    """Read the full text of a candidate whose search result came without one.

    Reads are the only slow step after the search: at most max_reads per request, and
    none once the deadline has passed. Options: max_reads (default 3).
    """

    def __init__(self, max_reads=MAX_READS):
        self.max_reads = max_reads

    def admit(self, candidate, ctx):
        if candidate.text.strip():
            return KEEP
        reads = ctx.state.get(self, 0)
        if reads >= self.max_reads or ctx.expired():
            return DROP
        ctx.state[self] = reads + 1
        ok, text = ctx.run(lambda t: candidate.store.read(candidate.ref, max(ctx.time_left(), 0.05), t),
                           store=candidate.store)
        if not ok or not isinstance(text, str):
            return DROP
        candidate.text = text
        return KEEP


@register("recall_stage", "drop_oversize")
class DropOversize(RecallStage):
    """Drop a memory that is too long, whole: there is never partial output.
    Only the reason is noted. Options: max_chars (default 65536)."""

    cheap = True

    def __init__(self, max_chars=MAX_ITEM_CHARS):
        self.max_chars = max_chars

    def admit(self, candidate, ctx):
        if len(candidate.text) > self.max_chars:
            ctx.notes.append("oversize-dropped")
            return DROP
        return KEEP


@register("recall_stage", "redact")
class Redact(RecallStage):
    """Clean the whole text with the redactor chosen for this client; drop what ends up empty."""

    enforces = ("redaction",)

    def admit(self, candidate, ctx):
        candidate.text = ctx.redact(candidate.text).strip()
        return KEEP if candidate.text else DROP


@register("recall_stage", "dedupe")
class Dedupe(RecallStage):
    """Keep only the best of several near-identical memories.

    Two texts are near-identical when one contains the other, or when enough of their
    character pairs are the same. Options: overlap (default 0.8).
    """

    cheap = True

    def __init__(self, overlap=0.8):
        self.overlap = overlap

    def admit(self, candidate, ctx):
        if any(tidy.near_duplicate(candidate.text, kept.text, self.overlap) for kept in ctx.accepted):
            return DROP
        return KEEP


@register("recall_stage", "budget")
class Budget(RecallStage):
    """Cap the total characters returned. A memory that does not fit is left out whole
    and counted in "more"; a shorter one further down may still fit.
    Options: max_chars (default 1500)."""

    def __init__(self, max_chars=MAX_OUTPUT_CHARS):
        self.max_chars = max_chars

    def admit(self, candidate, ctx):
        used = sum(len(kept.text) for kept in ctx.accepted)
        return DEFER if len(candidate.text) > self.max_chars - used else KEEP


NEWER_FIRST_BAND = 0.85


def _day(date):
    """An ISO date (or date-time) string as a datetime.date; None when it is not one."""
    if not isinstance(date, str):
        return None
    try:
        return datetime.date.fromisoformat(date)
    except ValueError:
        pass
    try:
        return datetime.datetime.fromisoformat(date).date()
    except ValueError:
        return None


@register("recall_stage", "newer_first")
class NewerFirst(RecallStage):
    """Rank the newer of two competing memories first, among candidates that score alike.

    Two memories about the same thing ("lives in A", later "moved to B") often score almost the same
    for one question, so which one comes first is a coin toss; `freshness` cannot help when both are old
    (both sit at its floor). This stage compares the candidates of ONE request with each other. The
    candidates whose score is at least `band` x the best score form the band. Among the band's candidates
    with a valid ISO date, scores are exchanged so that they appear newest first. The multiset of scores
    is unchanged (thresholds after this stage, the budget and the scores the caller sees stay in the
    same range); undated candidates and candidates outside the band keep their score. The similarity
    is kept in meta["similarity"].

    It never drops anything. Which candidates are delivered changes only where the limit or the budget
    cuts the list. Cost: a newer but less relevant memory inside the band can now outrank the right
    older one.

    Put it after `min_score` and `relative_score`, so thresholds only ever see similarity
    (misplaced() reports the opposite order).

    Options:
    band      (default 0.85) in (0, 1]; 1 means only candidates tied with the best one are exchanged.
    related   (default 0 = off) in [0, 1]; when > 0 two candidates are only exchanged if the character-pair
              overlap (Jaccard of bigrams, as `dedupe` measures it) of their texts is at least this.
    min_days  (default 1) dates closer than this many days count as the same age and keep their order.
    """

    after = ("min_score", "relative_score")

    def __init__(self, band=NEWER_FIRST_BAND, related=0, min_days=1):
        if isinstance(band, bool) or not isinstance(band, (int, float)) or not 0 < band <= 1:
            raise ConfigError("newer_first: band must be above 0 and at most 1")
        if not _fraction(related):
            raise ConfigError("newer_first: related must be a number between 0 and 1")
        if (isinstance(min_days, bool) or not isinstance(min_days, (int, float))
                or not math.isfinite(min_days) or min_days < 0):
            raise ConfigError("newer_first: min_days must be a number >= 0")
        self.band = float(band)
        self.related = float(related)
        self.min_days = float(min_days)

    def _overlap(self, a, b):
        a, b = tidy.normalize(a), tidy.normalize(b)
        if len(a) < 2 or len(b) < 2:
            return 1.0 if a == b else 0.0
        ga, gb = tidy.bigrams(a), tidy.bigrams(b)
        return len(ga & gb) / len(ga | gb)

    def _newer(self, later, earlier):
        """True when `later` (ranked lower) should take the place of `earlier` (ranked higher)."""
        gap = (later[1] - earlier[1]).days
        if gap <= 0 or gap < self.min_days:
            return False
        return self.related <= 0 or self._overlap(later[0].text, earlier[0].text) >= self.related

    def prepare(self, candidates, ctx):
        if len(candidates) < 2:
            return candidates
        best = max(c.score for c in candidates)
        if best <= 0:
            return candidates
        line = self.band * best
        dated = []
        for c in sorted((c for c in candidates if c.score >= line), key=lambda c: -c.score):
            day = _day(c.date)
            if day is not None:
                dated.append((c, day))
        if len(dated) < 2:
            return candidates
        slots = [c.score for c, _ in dated]
        # Bubble the newer ones up past older, higher-scored ones. Each swap removes one inversion of
        # the real dates, so this ends; with `related` a pair that is not related simply stays.
        order = list(dated)
        moved = True
        while moved:
            moved = False
            for i in range(len(order) - 1):
                if self._newer(order[i + 1], order[i]):
                    order[i], order[i + 1] = order[i + 1], order[i]
                    moved = True
        # Equal scores stay equal after an exchange, and the recall sort is stable: the list order then
        # decides, so the band's dated candidates also take each other's places in the list, in the
        # same order (identical scores: newer first; the candidates' other places are not touched).
        places = sorted(i for i, c in enumerate(candidates) if any(c is d for d, _ in dated))
        out = list(candidates)
        for place, (c, _), score in zip(places, order, slots):
            c.meta.setdefault("similarity", c.score)
            c.score = score
            out[place] = c
        return out


LINKED_TOP = 2
LINKED_MAX_ADDED = 2
LINKED_MINIMUM = 0.60
LINKED_WAIT = 1.0  # seconds all the lookups of one request may take together at most


def _whole(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


@register("recall_stage", "linked")
class Linked(RecallStage):
    """Add the memories linked to the best ones: one step from what the lines let through.

    A casual remark ("最近天气有点热") is close to one memory ("主人喜欢吃冰淇淋"), and a second one
    ("上次冰淇淋掉在了地上") is close to that memory but not to the remark. After the score lines have
    decided what is delivered, this stage takes the first `top` candidates (in the order the recall will
    deliver them), asks their store for the memories most similar to each (the backend method
    `neighbours`, docs/pieces.md) and appends at most `max_added` of those whose similarity is at least
    `minimum`. It acts after the lines, so it never changes whether the gateway stays quiet: with no
    candidate it does nothing.

    Rules: only while the number of candidates is below the request's limit (the limit keeps meaning "at
    most this many memories"); a memory that is a candidate already (same store and ref) is not added; a
    memory found from two candidates counts once with its higher similarity; the best similarity first.
    An appended candidate keeps the source and store of the memory it was found from, scores
    `parent score x similarity` (never above the lowest first-hop score, so it stays behind every first-hop
    candidate in the recall's score sort and keeps its own order), and has `meta["linked_from"]` = that
    memory's ref. It then goes through the later stages like any other: texts are read by `fill_text`,
    cleaned by `redact`, and counted by `dedupe` and `budget`. The HTTP answer does not change (no field is
    added). A store without `neighbours`, a timeout or an error in it means nothing is added and one note
    "linked-failed" for the access log (no note for a store that simply has no such method). The lookups go
    through ctx.run like the reads of `fill_text`; together they wait at most 1 second and at most half of the
    time left, so a store that hangs costs the request that much and not the answer it already has.

    Put it after `min_score`, `relative_score` and `newer_first` (misplaced() reports the opposite order)
    and before `fill_text`. Options: top (default 2, 1..5), max_added (default 2, 0..5; 0 switches the
    stage off), minimum (default 0.60, 0..1; on the store's own 0..1 score scale: 0.60 was measured with
    bge-m3 on the embedding backend, other models use it unmeasured).
    """

    after = ("min_score", "relative_score", "newer_first")

    def __init__(self, top=LINKED_TOP, max_added=LINKED_MAX_ADDED, minimum=LINKED_MINIMUM):
        if not _whole(top, 1, 5):
            raise ConfigError("linked: top must be a whole number from 1 to 5")
        if not _whole(max_added, 0, 5):
            raise ConfigError("linked: max_added must be a whole number from 0 to 5")
        if not _fraction(minimum):
            raise ConfigError("linked: minimum must be a number between 0 and 1")
        self.top = top
        self.max_added = max_added
        self.minimum = float(minimum)

    def prepare(self, candidates, ctx):
        room = min(self.max_added, ctx.limit - len(candidates))
        if room <= 0 or not candidates:
            return candidates
        try:
            added = self._added(candidates, room, ctx)
        except Exception as error:  # noqa: BLE001 - this stage never fails a recall
            ctx.fault("linked-failed", "stage=linked where=prepare error=%s" % type(error).__name__)
            return candidates
        return list(candidates) + added

    def _added(self, candidates, room, ctx):
        # the order the recall delivers in: best score first, a tie keeps the list order (a stable sort)
        first = sorted(candidates, key=lambda c: -c.score)[:self.top]
        taken = {(id(c.store), c.ref) for c in candidates}
        ask = room + len(candidates)  # enough that the memories already taken cannot crowd the others out
        # All the lookups together wait at most this long, and never more than half of what is left: a store
        # that hangs must not take the time the stages after this one (and the first-hop answer) need.
        until = ctx.clock() + min(LINKED_WAIT, max(ctx.time_left(), 0.0) / 2)
        found: dict[Any, list[Any]] = {}  # (id of store, ref) -> [similarity, order met, parent, hit]
        for parent in first:
            if ctx.clock() >= until:
                break
            if not callable(getattr(parent.store, "neighbours", None)):
                continue
            store = parent.store
            ok, result = ctx.run(
                lambda ctl, s=store, p=parent: s.neighbours(p.ref, ask, max(until - ctx.clock(), 0.05), ctl),
                store=store, until=until)
            hits = _hits(result) if ok else None
            if hits is None:
                ctx.fault("linked-failed", "stage=linked where=neighbours error=%s" % (
                    type(result).__name__ if isinstance(result, BaseException) else "no-result"))
                continue
            for hit in hits:
                key = (id(store), hit.ref)
                if key in taken or hit.ref == parent.ref or hit.score < self.minimum:
                    continue
                if key not in found:
                    found[key] = [hit.score, len(found), parent, hit]
                elif hit.score > found[key][0]:
                    found[key][0], found[key][2], found[key][3] = hit.score, parent, hit
        ranked = sorted(found.values(), key=lambda item: (-item[0], item[1]))[:room]
        # Behind every first-hop candidate, and in the order chosen (a later one never scores above an earlier one).
        ceiling = math.nextafter(min(c.score for c in candidates), 0.0)
        out = []
        for similarity, _, parent, hit in ranked:
            ceiling = min(ceiling, parent.score * similarity)
            meta = dict(hit.meta)
            meta["similarity"] = ceiling
            meta["linked_from"] = parent.ref
            out.append(Candidate(score=ceiling, source=parent.source, store=parent.store, ref=hit.ref,
                                 text=hit.text, date=hit.date, meta=meta, fields=dict(hit.fields)))
        return out


def misplaced(stages):
    """Names of the stages in `stages` that stand before a stage they must come after. Works on
    stage objects (the order of a built gateway); newer_first and linked declare such a rule, in `after`."""
    problems = []
    for index, stage in enumerate(stages):
        rule = getattr(stage, "after", None)
        if not isinstance(rule, tuple):
            continue
        later = {getattr(s, "name", None) for s in stages[index + 1:]}
        if any(name in later for name in rule):
            problems.append(getattr(stage, "name", None) or type(stage).__name__)
    return problems


# redact comes before dedupe and budget: they then look at the text that is really returned.
# relative_score comes right after min_score (the best candidate is then one that passed the threshold);
# a stage that changes scores (source_weight, freshness) goes between the two.
# newer_first only reorders the candidates that passed both thresholds, so it comes after them; it does
# nothing for memories without dates.
# linked acts after the lines (it never changes whether the gateway stays quiet) and before fill_text, so what it
# appends is read, redacted and counted like any other candidate; it does nothing for stores without `neighbours`.
# (drop_marker is not a default: add it after fill_text when something writes such markers.)
DEFAULT_STAGES = ("min_score", "relative_score", "newer_first", "linked", "fill_text", "drop_oversize", "redact",
                  "dedupe", "budget")
