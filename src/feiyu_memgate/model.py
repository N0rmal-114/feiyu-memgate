"""The small data shapes that pieces hand to each other. Standard library only."""
import collections.abc
import datetime
import re
import unicodedata
from dataclasses import dataclass, field

TENANT_STORE = "@tenant"
# How a tenant client's tenant is known: named in each request body, or carried by the token itself.
TENANT_BINDINGS = ("request", "token")
# Who may see a store's content or a client's output, narrowest first: the order is the width.
AUDIENCES = ("owner", "group", "public")
# The facts a memory may carry beside its text and date; each a short string, all optional.
FIELDS = ("speaker", "about", "channel", "audience", "origin", "event_date", "kind")
MAX_FIELD = 128  # characters in one field value
# Where a memory came from, as the host says it (a record of what was claimed, never a grant of anything),
# and what sort of fact it is. A request may carry these two, and the channel, event_date and audience.
ORIGINS = ("said", "tool", "curated", "derived")
KINDS = ("identity", "preference", "event", "plan", "entity")
DEFAULT_ORIGINS = ("said", "tool")  # the origins a client may write unless its policy says otherwise

# Who is speaking, as a host reports it: an account "<platform>:<id>" (see identity.py). One definition, used by
# the request checks, the configuration and the identity table alike.
MAX_ACCOUNT_CHARS = 64
PLATFORM = re.compile(r"[a-z0-9][a-z0-9_.-]{0,31}")
PERSON = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")  # a name the operator gives someone in the identity table
RESERVED_PEOPLE = ("self", "anon")  # "self" speaks for the assistant, "anon" marks an account nobody listed


def valid_platform(value):
    """True when `value` is a platform name: [a-z0-9][a-z0-9_.-]{0,31}."""
    return isinstance(value, str) and PLATFORM.fullmatch(value) is not None


def valid_person(value):
    """True when `value` can name a person: [a-z0-9][a-z0-9_-]{0,31}, and is not reserved."""
    return isinstance(value, str) and PERSON.fullmatch(value) is not None and value not in RESERVED_PEOPLE


def valid_account(value):
    """True when `value` is an account: a platform, ":", then an id of at least one character without
    whitespace or control characters; at most MAX_ACCOUNT_CHARS characters in all."""
    if not isinstance(value, str) or len(value) > MAX_ACCOUNT_CHARS:
        return False
    platform, colon, ident = value.partition(":")
    if not colon or not ident or PLATFORM.fullmatch(platform) is None:
        return False
    return not any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in ident)


def platform_of(account):
    """The platform part of an account (the text before the first ":")."""
    return account.partition(":")[0]


# The optional keys of a message in a remember request that become record fields of the memory. One rule
# for each, used by the gateway (gateway.validate) and by the clients, so that they cannot disagree.
REQUEST_FIELDS = ("channel", "origin", "event_date", "kind", "audience")
_CHANNEL = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,63}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def valid_channel(value):
    """True when `value` is a channel name: lower-case letters, digits and _ . : -, at most 64 characters."""
    return isinstance(value, str) and _CHANNEL.fullmatch(value) is not None


def valid_event_date(value):
    """True when `value` is a real calendar date written YYYY-MM-DD and nothing else."""
    if not isinstance(value, str) or _DATE.fullmatch(value) is None:
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def valid_request_field(key, value):
    """True when `value` is a valid value of the request key `key` (one of REQUEST_FIELDS)."""
    if key == "channel":
        return valid_channel(value)
    if key == "event_date":
        return valid_event_date(value)
    choices = {"origin": ORIGINS, "kind": KINDS, "audience": AUDIENCES}[key]
    return isinstance(value, str) and value in choices


def clean_fields(fields):
    """The fields of one memory as a new dict, or ValueError.

    None or an empty mapping gives {}. Entries whose value is None are dropped. A key that is not in
    FIELDS, or a value that is not a non-empty string of at most MAX_FIELD characters, raises ValueError
    naming the key. Stores keep what they are given; what a value may be is the gateway's rule.
    """
    if not fields:
        return {}
    if not isinstance(fields, collections.abc.Mapping):
        raise ValueError("fields must be a mapping")
    out = {}
    for key, value in fields.items():
        if key not in FIELDS:
            raise ValueError("unknown field: %r" % (key,))
        if value is None:
            continue
        if not isinstance(value, str) or not value or len(value) > MAX_FIELD:
            raise ValueError("field %s must be a non-empty string of at most %d characters" % (key, MAX_FIELD))
        out[key] = value
    return out


@dataclass
class Hit:
    """One search result from a backend.

    `ref` identifies the memory inside its backend (a URI, a row id); `score` is a
    similarity in 0..1, higher is better; `text` may be empty when the backend only
    returns it through read(); `date` is "YYYY-MM-DD" when the backend knows when the
    memory is from; `meta` is anything the backend wants the recall stages to see
    (where the memory came from, who said it). It is copied into Candidate.meta and
    never returned to clients. `fields` holds the memory's recorded facts (see FIELDS), only the ones
    that are set; {} for a memory without any and for a backend that keeps none.
    """
    ref: str
    score: float
    text: str = ""
    date: str | None = None
    meta: dict = field(default_factory=dict)
    fields: dict = field(default_factory=dict)


@dataclass
class Candidate:
    """A hit on its way through the recall stages. Stages may change score, text and meta.
    `fields` is a copy of the hit's recorded facts (see FIELDS)."""
    score: float
    source: str
    store: object
    ref: str
    text: str = ""
    date: str | None = None
    meta: dict = field(default_factory=dict)
    fields: dict = field(default_factory=dict)

    def as_item(self):
        # "id" is the memory's ref in its store: opaque to clients, stable for one memory.
        item = {"id": self.ref, "source": self.source, "text": self.text, "score": self.score}
        if self.date:
            item["date"] = self.date
        return item


@dataclass(frozen=True)
class ClientPolicy:
    """What one token may do.

    recall      the stores it may search, in this order: names of shared stores, and
                "@tenant" for the tenant's own store. None means it may not recall at all;
                an empty tuple means "may ask, searches nothing" (useful before a store exists).
    write       name of the store its writes go to, "@tenant" for the tenant's own store,
                None when it may not write.
    tenants     name of a tenants provider: every request is then for one tenant. The
                tenant's own store is only searched when "@tenant" is in recall.
    tenant_binding  how a tenant client's tenant is known. "request" (default): every request
                carries tenant_id, asserted by whoever holds the one shared token. "token": the
                configured secret only mints tokens, each bound to one tenant by an HMAC; a request
                is for the tenant its token names (tenant_id may be left out or repeated, never
                changed). Only valid with tenants.
    tenant_token_generation  part of every minted token of a "token" client: raising it
                invalidates all of them at once.
    public      True when what it reads or writes is shown to other people: the public
                redactor is used instead of the private one.
    daily_writes  writes allowed per day (per tenant for a tenant client); None is no cap.
    daily_writes_total  writes allowed per day for the whole client, all tenants together;
                None is no cap.
    rate_limit  requests per minute (per tenant for a tenant client).
    rate_limit_total  requests per minute for the whole client, all tenants together;
                None means 10 x rate_limit. Only used for a tenant client: rotating tenant
                ids cannot go around it.
    pin_writes  True when what this client writes is pinned: the forgetting curve never forgets it.
    audience    who sees what this client shows: "owner", "group" or "public" (see AUDIENCES). None
                means "not declared": see reach. A client may only recall from stores at least as
                wide as its audience.
    explain     True when this client may ask for an explained recall (POST /v1/explain): the
                candidates a recall left out, with their scores and the step that removed them.
                Off by default; only for owner-level clients.
    origins     the origins (see ORIGINS) a message this client writes may name; a message naming
                another one is refused (403). Default ("said", "tool"). An origin is a record of what the
                host says, nothing more: nothing is granted because of it.
    fields      the record fields (see FIELDS) added to every item this client recalls, "fields": {...},
                for the ones the memory has. Empty by default: items carry no fields. A client whose
                audience is not "owner" may not list "speaker" or "about".
    platforms   the platforms (the part of an account before ":") whose accounts this client may name as the
                speaker or subject of a message, or as the speaker of a recall. Empty by default: it can name
                none. Needs an identity table (see identity.py).
    only_for    names of people in the identity table: when set, every request this client makes is on behalf
                of one of them (every user message, every recall names a speaker who is one of them).
    """
    name: str
    recall: tuple | None = None
    write: str | None = None
    tenants: str | None = None
    public: bool = False
    daily_writes: int | None = None
    rate_limit: int = 60
    pin_writes: bool = False
    daily_writes_total: int | None = None
    rate_limit_total: int | None = None
    tenant_binding: str = "request"
    tenant_token_generation: int = 1
    audience: str | None = None
    explain: bool = False
    origins: tuple = DEFAULT_ORIGINS
    fields: tuple = ()
    platforms: tuple = ()
    only_for: tuple = ()

    @property
    def reach(self):
        """The resolved audience: the declared one, else owner for a private client, group for a
        public tenant client and public for any other public client."""
        if self.audience is not None:
            return self.audience
        if not self.public:
            return "owner"
        return "group" if self.tenants is not None else "public"

    @property
    def token_bound(self):
        """True when this client's tokens are minted per tenant and carry their tenant."""
        return self.tenants is not None and self.tenant_binding == "token"

    @property
    def can_recall(self):
        return self.recall is not None

    @property
    def can_write(self):
        return self.write is not None

    @property
    def total_rate_limit(self):
        return self.rate_limit_total if self.rate_limit_total is not None else self.rate_limit * 10
