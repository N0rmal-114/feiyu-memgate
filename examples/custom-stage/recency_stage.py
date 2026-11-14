"""An example piece: a recall stage that ranks newer memories before older ones.

Put this file next to the configuration and name it by module and class, nothing to
install (the configuration's folder is searched first):

    [recall]
    stages = [
        "min_score",
        { type = "recency_stage:Recency", half_life_days = 30 },
        "redact", "dedupe", "budget",
    ]

A memory without a date keeps its score. This is a teaching example, not the forgetting
curve (the built-in `freshness` stage): it only looks at the date, not at how often a
memory was used.
"""
import datetime

from feiyu_memgate.pieces import RecallStage


class Recency(RecallStage):
    """Options: half_life_days (after this many days a memory's score is halved),
    floor (the score is never multiplied by less than this), today (for tests)."""

    def __init__(self, half_life_days=30, floor=0.5, today=None):
        self.half_life_days = half_life_days
        self.floor = floor
        self._today = today

    def _factor(self, date):
        try:
            then = datetime.date.fromisoformat(date)
        except (TypeError, ValueError):
            return 1.0
        today = self._today or datetime.date.today()
        age = max((today - then).days, 0)
        return max(0.5 ** (age / self.half_life_days), self.floor)

    def prepare(self, candidates, ctx):
        # prepare() sees every candidate before they are sorted: the place to change scores.
        for candidate in candidates:
            candidate.meta.setdefault("similarity", candidate.score)
            candidate.score *= self._factor(candidate.date)
        return candidates
