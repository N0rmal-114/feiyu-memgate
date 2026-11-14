"""The small data shapes that pieces hand to each other. Standard library only."""
from dataclasses import dataclass, field

TENANT_STORE = "@tenant"
# How a tenant client's tenant is known: named in each request body, or carried by the token itself.
TENANT_BINDINGS = ("request", "token")


@dataclass
class Hit:
    """One search result from a backend.

    `ref` identifies the memory inside its backend (a URI, a row id); `score` is a
    similarity in 0..1, higher is better; `text` may be empty when the backend only
    returns it through read(); `date` is "YYYY-MM-DD" when the backend knows when the
    memory is from; `meta` is anything the backend wants the recall stages to see
    (where the memory came from, who said it). It is copied into Candidate.meta and
    never returned to clients.
    """
    ref: str
    score: float
    text: str = ""
    date: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class Candidate:
    """A hit on its way through the recall stages. Stages may change score, text and meta."""
    score: float
    source: str
    store: object
    ref: str
    text: str = ""
    date: str | None = None
    meta: dict = field(default_factory=dict)

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
