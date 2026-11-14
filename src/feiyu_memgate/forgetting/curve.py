"""The forgetting curve slot: how much of a memory is still "kept" after some time.

A curve answers one question, retention(age_days, uses, source) -> 0..1, and keeps no
state. Swap it for your own formula: ``[forgetting.curve] type = "my_pkg.mod:MyCurve"``.

Standard library only.
"""
from ..errors import ConfigError
from ..registry import register


class ForgettingCurve:
    """Base class of a forgetting curve."""

    def retention(self, age_days, uses, source):
        """Retention in 0..1: 1 is fresh, 0 is gone.

        age_days   days of age on the clock the caller asks about: for ranking, since the memory was
                   last used (else since its date); for deletion, since the later of its last use
                   and the time it came under management
        uses       how many times it was used (recalled and returned)
        source     the store name; for a tenant store "provider:tenant" it is the provider
        """
        raise NotImplementedError


@register("forgetting_curve", "ebbinghaus")
class Ebbinghaus(ForgettingCurve):
    """R = 0.5 ** (age / half_life), half_life = base(source) * reinforce ** min(uses, max_uses).

    Options: half_life_days (default 90), per_store ({store: days}), reinforce (default 1.6),
    max_uses (default 8).
    """

    def __init__(self, half_life_days=90, per_store=None, reinforce=1.6, max_uses=8):
        if not half_life_days > 0:
            raise ConfigError("forgetting curve: half_life_days must be positive")
        per_store = dict(per_store or {})
        if any(not v > 0 for v in per_store.values()):
            raise ConfigError("forgetting curve: every per_store half-life must be positive")
        if reinforce < 1:
            raise ConfigError("forgetting curve: reinforce must be at least 1")
        if max_uses < 0:
            raise ConfigError("forgetting curve: max_uses must not be negative")
        self.half_life_days = half_life_days
        self.per_store = per_store
        self.reinforce = reinforce
        self.max_uses = max_uses

    def half_life(self, uses, source):
        base = self.per_store.get(source.partition(":")[0], self.half_life_days)
        return base * self.reinforce ** min(max(uses, 0), self.max_uses)

    def retention(self, age_days, uses, source):
        age = max(age_days, 0.0)
        return 0.5 ** (age / self.half_life(uses, source))
