"""Write guards: what a write must pass before it reaches a backend.

A write guard is one piece of the puzzle. Guards run in the configured order:

- before(messages, ctx) sees the messages and may change them, refuse the write by
  raising WriteRejected, or return an empty list ("nothing worth keeping": the write
  is skipped, nothing reaches the backend);
- rollback(ctx) (optional) undoes what before() did, for every guard whose before()
  completed, in reverse order, when the write does not happen after all: a later guard
  refused or failed, the backend failed or was busy, or the write was skipped;
- after(receipt, ctx) runs once the backend has kept something.

A guard that raises anything else than WriteRejected fails the write (500, code
guard_error): nothing is written. Guards are shared between requests and may keep state
of their own (counters), as long as it is safe to use from several threads; per-request
state goes in ctx.state.

Standard library only.
"""
import time

from ..errors import WriteRejected
from ..registry import register
from .limits import DailyCaps


class WriteGuard:
    """Base class of a write guard. Override before, after, rollback as needed.

    name      the registered name; registry.create fills it in (None: the class name is used)
    enforces  what the guard guarantees, for the configuration check: ("redaction",),
              ("daily_writes",)

    A message is a dict with "role", "content" and optionally "time" and "speaker", and, when the store it
    goes to keeps record fields, "fields": a dict of model.FIELDS (the audience of the record among them).
    A guard that builds new message dicts must carry "fields" over; a guard that drops it drops what the
    store would keep, and the record then has no audience.
    """

    name = None
    enforces: tuple[str, ...] = ()

    def before(self, messages, ctx):
        return messages

    def rollback(self, ctx):
        pass

    def after(self, receipt, ctx):
        pass


class WriteContext:
    """What a guard may know about the write.

    client, tenant_id  who writes (names, never tokens)
    policy             the client's ClientPolicy (policy.reach is who sees what it shows)
    public             True when the client is a public one
    redact             the text cleaner chosen for this client (str -> str)
    key                client name, or "client:tenant" for a tenant client
    context            the request's optional "context" object (small, already checked)
    state              a dict for the guards' own per-request state
    """

    def __init__(self, policy, tenant_id=None, redact=None, context=None):
        self.policy = policy
        self.client = policy.name
        self.tenant_id = tenant_id
        self.public = policy.public
        self.redact = redact or (lambda text: text)
        self.key = policy.name if tenant_id is None else "%s:%s" % (policy.name, tenant_id)
        self.context = dict(context or {})
        self.state = {}


# The message keys that hold free text: the redact guard cleans all of them. "fields" holds no free text (each
# value was checked against a fixed set or pattern by the gateway) and is carried over as it is.
TEXT_FIELDS = ("content", "speaker", "time")


@register("write_guard", "redact")
class RedactGuard(WriteGuard):
    """Clean every message with the redactor chosen for this client before it is stored:
    its content, and its optional speaker and time."""

    enforces = ("redaction",)

    def before(self, messages, ctx):
        out = []
        for message in messages:
            clean = dict(message)
            for key in TEXT_FIELDS:
                if isinstance(clean.get(key), str):
                    clean[key] = ctx.redact(clean[key])
            out.append(clean)
        return out


@register("write_guard", "daily_cap")
class DailyCapGuard(WriteGuard):
    """Refuse writes over the client's daily_writes (per tenant for a tenant client) and
    daily_writes_total (the whole client, all tenants together).

    A write is counted when it passes this guard and given back (rollback) when it does
    not happen after all, so concurrent writes cannot all slip under the cap, and a failed
    or skipped write costs nothing. Options: utc_offset_hours (where the day starts,
    default 0), wall (a clock, for tests).
    """

    enforces = ("daily_writes",)

    def __init__(self, utc_offset_hours=0, wall=time.time):
        self.caps = DailyCaps(wall, utc_offset_hours)

    @staticmethod
    def _limits(ctx):
        policy = ctx.policy
        limits = []
        if policy.daily_writes is not None:
            limits.append((ctx.key, policy.daily_writes))
        total = getattr(policy, "daily_writes_total", None)
        if total is not None:
            limits.append((("total", policy.name), total))  # a tuple: never equal to a client or tenant key
        return limits

    def before(self, messages, ctx):
        held = ctx.state.setdefault(self, [])
        for key, limit in self._limits(ctx):
            day = self.caps.reserve_day(key, limit)
            if day is None:
                self.rollback(ctx)
                raise WriteRejected(429, "daily limit", "daily-limit", code="daily_limit")
            held.append((key, day))
        return messages

    def rollback(self, ctx):
        for key, day in ctx.state.pop(self, []):
            self.caps.release(key, day)


DEFAULT_GUARDS = ("redact", "daily_cap")
