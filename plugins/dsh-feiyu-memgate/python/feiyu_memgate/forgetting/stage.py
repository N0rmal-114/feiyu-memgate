"""The recall stage of the forgetting curve: rank older, unused memories lower.

prepare() multiplies each score by one bounded freshness, floor + (1 - floor) * retention
(relevance dominates, freshness settles near-ties); finish() records a use for the returned
memories the credit rule names. A ledger failure never fails the recall: freshness counts as 1
and the access log gets the note "ledger-failed".

Standard library only.
"""
from typing import Any

from ..core.recall import RecallStage
from ..errors import ConfigError
from ..registry import register

LEDGER_FAILED = "ledger-failed"
CREDITS = ("top", "all", "none")


def _key(forgetting, candidate, ctx):
    tenant = ctx.tenant_id if candidate.source == getattr(ctx, "tenant_source", None) else None
    return forgetting.key(candidate.source, tenant)


@register("recall_stage", "freshness")
class Freshness(RecallStage):
    """Options:

    floor         (default 0.7) the share of the score even a long-unused memory keeps; the multiplier is
                  floor + (1 - floor) * retention for every state, pinned memories get 1.
    credit        which returned memories record a use at recall time: "top" (default) the best one and
                  every other whose similarity is at least credit_ratio of the best one's, "all" every
                  returned one, "none" never (use POST /v1/used instead).
    credit_ratio  (default 0.9) the "top" threshold, in (0, 1].

    Being returned is not being useful: with "all", noise that rides along in a top-5 is kept fresh
    while a relevant memory nobody asked about decays.
    """

    needs = ("forgetting",)

    def __init__(self, forgetting, floor=0.7, credit="top", credit_ratio=0.9):
        if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not 0 <= floor <= 1:
            raise ConfigError("freshness: floor must be between 0 and 1")
        if credit not in CREDITS:
            raise ConfigError("freshness: credit must be one of %s" % ", ".join(repr(c) for c in CREDITS))
        if isinstance(credit_ratio, bool) or not isinstance(credit_ratio, (int, float)) or not 0 < credit_ratio <= 1:
            raise ConfigError("freshness: credit_ratio must be above 0 and at most 1")
        self.forgetting = forgetting
        self.floor = floor
        self.credit = credit
        self.credit_ratio = credit_ratio

    @staticmethod
    def _note(ctx):
        if LEDGER_FAILED not in ctx.notes:
            ctx.notes.append(LEDGER_FAILED)

    def prepare(self, candidates, ctx):
        f = self.forgetting
        managed = [c for c in candidates if f.manages(c.source)]
        state = ctx.state.setdefault(self, {"failed": False})
        if not managed:
            return candidates
        before = [(c, c.score) for c in managed]
        try:
            now = f.now()
            by_key: dict[Any, list[Any]] = {}
            for c in managed:
                by_key.setdefault(_key(f, c, ctx), []).append(c)
            for key, group in by_key.items():
                entries = f.ledger.get_many(key, [c.ref for c in group])
                for c in group:
                    entry = entries.get(c.ref)
                    value = f.freshness(key, c.ref, entry, self.floor, c.date, now)
                    c.meta.setdefault("similarity", c.score)
                    c.meta["retention"] = f.retention(key, entry, c.date, now)
                    c.score = c.score * value
        except Exception:  # noqa: BLE001 - the ledger must never fail a recall
            state["failed"] = True
            self._note(ctx)
            for c, score in before:  # undo a half-applied pass: freshness counts as 1
                c.score = score
        return candidates

    def _credited(self, managed):
        """The returned, managed candidates that record a use under the credit rule."""
        if self.credit == "all" or len(managed) <= 1:
            return managed
        sims = [c.meta.get("similarity", c.score) for c in managed]
        best = max(sims)
        bar = best * self.credit_ratio if best > 0 else best
        return [c for c, sim in zip(managed, sims) if sim >= bar]

    def finish(self, ctx):
        state = ctx.state.get(self)
        if state is None or state["failed"] or self.credit == "none":
            return
        f = self.forgetting
        managed = [c for c in ctx.accepted if f.manages(c.source)]
        by_key: dict[Any, list[Any]] = {}
        for c in self._credited(managed):
            by_key.setdefault(_key(f, c, ctx), []).append((c.ref, c.date))
        if not by_key:
            return
        try:
            now = f.now()
            for key, items in by_key.items():
                f.record_use(key, items, now)
        except Exception:  # noqa: BLE001
            self._note(ctx)
