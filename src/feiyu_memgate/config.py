"""Turn a configuration file into a running gateway.

The file is TOML. Every table names pieces by `type` (see registry.py) and gives
them options. Secrets never go in the file: an option whose name ends in `_env`
names an environment variable, and the piece receives its value under the name
without the suffix (`key_env = "OV_KEY"` becomes `key = <value of OV_KEY>`).

    [server]
    host = "127.0.0.1"
    port = 8080

    [stores.private]                 # a shared store; the table name is its source name
    type = "sqlite"
    path = "memories.db"

    [tenants.group]                  # optional: one store per tenant
    type = "directory"
    path = "groups"

    [clients.owner]                  # one table per token
    token_env = "GATEWAY_TOKEN_OWNER"
    recall = ["private"]
    write = "private"
    daily_writes = 50

    [clients.group]                  # a tenant client whose tokens are each bound to one tenant
    token_env = "GATEWAY_MINT_GROUP" # a minting secret: `feiyu-memgate tenants token` mints from it
    tenants = "group"
    tenant_binding = "token"
    recall = ["@tenant"]
    write = "@tenant"

    [recall]
    stages = ["min_score", "redact", "dedupe", "budget"]

    [forgetting]                     # optional: see docs/design/2026-11-14-forgetting-curve.md
    ledger = "ledger.db"
    archive = "forgotten.db"
    stores = ["private"]

Every setting is checked when the gateway is built: an unknown key, a wrong type or a
value out of range is a ConfigError naming the setting, never a failure per request. A
client setting that no configured piece enforces (daily_writes without a daily cap
guard, public without redaction) is refused too. docs/configuration.md lists every
setting. Standard library only.
"""
import os
import sys
import time
import tomllib

from . import registry
from .core import stages as stages_module, write as write_module
from .errors import ConfigError
from .forgetting.archive import Archive
from .forgetting.engine import REQUIRED as FORGETTING_CAPABILITIES, Forgetting
from .forgetting.ledger import Ledger
from .gateway import policies
from .gateway.app import Gateway, stderr_log, usable_token, MIN_TOKEN_CHARS
from .model import (AUDIENCES, DEFAULT_ORIGINS, FIELDS, ORIGINS, TENANT_BINDINGS, ClientPolicy, valid_person,
                    valid_platform)

SERVER_DEFAULTS = {"host": "127.0.0.1", "port": 8080}
_POLICY_KEYS = {"token_env", "recall", "write", "tenants", "public", "daily_writes", "daily_writes_total",
                "rate_limit", "rate_limit_total", "pin_writes", "tenant_binding", "tenant_token_generation",
                "audience", "explain", "origins", "fields", "platforms", "only_for"}
_TOP_KEYS = {"server", "stores", "tenants", "clients", "recall", "write", "redaction", "forgetting", "identity"}
_IDENTITY_KEYS = {"type", "hash_key_env", "people"}

# What each fixed section may hold: key -> checker(where, value). "stages"/"guards" are piece lists.
_POSITIVE_INT, _POSITIVE_NUMBER, _NUMBER_AT_LEAST_0 = "positive integer", "positive number", "number >= 0"
_SECTIONS = {
    "server": {"host": "host", "port": "port", "max_connections": _POSITIVE_INT, "read_deadline": _POSITIVE_NUMBER,
               "pool_size": _POSITIVE_INT, "tenant_pool_total": _POSITIVE_INT,
               "tenant_pool_per_tenant": _POSITIVE_INT, "store_max_inflight": _POSITIVE_INT},
    "recall": {"deadline": _POSITIVE_NUMBER, "cold_deadline": _POSITIVE_NUMBER, "cold_after": _NUMBER_AT_LEAST_0,
               "stages": "pieces", "profile": "profile"},
    "write": {"deadline": _POSITIVE_NUMBER, "guards": "pieces"},
    "redaction": {"private": "piece", "public": "piece"},
}


def load(path):
    """The configuration file as a dict.

    The file's directory goes first on sys.path, so `type = "my_stage:Recency"` finds
    my_stage.py next to the configuration (keep no other .py files there)."""
    try:
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
    except OSError as error:
        raise ConfigError("cannot read %s: %s" % (path, type(error).__name__)) from None
    except tomllib.TOMLDecodeError as error:
        raise ConfigError("%s is not valid TOML: %s" % (path, error)) from None
    folder = os.path.dirname(os.path.abspath(path))
    if folder not in sys.path:
        sys.path.insert(0, folder)
    return cfg


def resolve_env(spec, env, where):
    """A copy of `spec` with every `<name>_env` option replaced by `<name>` from the environment.
    Nested tables are resolved too. A variable that is not set is an error naming the setting."""
    out = {}
    for key, value in spec.items():
        if isinstance(value, dict):
            out[key] = resolve_env(value, env, "%s.%s" % (where, key))
        elif key.endswith("_env") and key != "token_env":
            if not isinstance(value, str) or not env.get(value):
                raise ConfigError("%s.%s: environment variable %s is not set" % (where, key, value))
            out[key[:-4]] = env[value]
        else:
            out[key] = value
    return out


def _table(cfg, name):
    value = cfg.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError("[%s] must be a table" % name)
    return value


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_value(where, kind, value):
    if kind == "host":
        if not isinstance(value, str) or not value:
            raise ConfigError("%s must be a host name or address" % where)
    elif kind == "port":
        if not _is_int(value) or not 0 <= value <= 65535:
            raise ConfigError("%s must be a port number (0 to 65535)" % where)
    elif kind == _POSITIVE_INT:
        if not _is_int(value) or value < 1:
            raise ConfigError("%s must be a positive integer" % where)
    elif kind == _POSITIVE_NUMBER:
        if not _is_number(value) or not value > 0:
            raise ConfigError("%s must be a positive number of seconds" % where)
    elif kind == _NUMBER_AT_LEAST_0:
        if not _is_number(value) or value < 0:
            raise ConfigError("%s must be a number of seconds, 0 or more" % where)
    elif kind == "profile":
        if value not in stages_module.PROFILES:
            raise ConfigError("%s must be one of %s" % (where, ", ".join(stages_module.PROFILES)))


def _section(cfg, name):
    """[name] checked: no unknown key, every value of the right type and range."""
    spec = _table(cfg, name)
    allowed = _SECTIONS[name]
    unknown = set(spec) - set(allowed)
    if unknown:
        raise ConfigError("[%s]: unknown setting %s (known: %s)" % (
            name, ", ".join(sorted(unknown)), ", ".join(sorted(allowed))))
    for key, value in spec.items():
        _check_value("%s.%s" % (name, key), allowed[key], value)
    return spec


def _limit(where, value):
    if value is not None and (not _is_int(value) or value < 1):
        raise ConfigError("%s must be a positive integer" % where)
    return value


def _audience(where, value):
    """A declared audience level, or ConfigError naming the setting."""
    if value not in AUDIENCES:
        raise ConfigError("%s.audience must be one of %s" % (where, ", ".join('"%s"' % level for level in AUDIENCES)))
    return value


def _split_audience(kind, name, spec):
    """(spec without its audience key, the audience or None): the key is the loader's, never the backend's."""
    if "audience" not in spec:
        return spec, None
    rest = {key: value for key, value in spec.items() if key != "audience"}
    return rest, _audience("%s.%s" % (kind, name), spec["audience"])


def _names_of(where, key, value, known, default, kind):
    """A list setting whose entries are each one of `known`, as a tuple (`default` when absent)."""
    if value is None:
        return default
    if not isinstance(value, list) or not all(isinstance(item, str) and item in known for item in value):
        raise ConfigError("%s.%s must be a list of %s, each one of %s" % (
            where, key, kind, ", ".join('"%s"' % item for item in known)))
    return tuple(value)


def _checked_names(where, key, value, valid, kind):
    """A list setting whose entries each pass `valid`, as a tuple (empty when absent)."""
    if value is None:
        return ()
    if not isinstance(value, list) or not all(valid(item) for item in value):
        raise ConfigError("%s.%s must be a list of %s" % (where, key, kind))
    return tuple(value)


def client_policy(name, spec):
    """The ClientPolicy of one [clients.<name>] table, every setting checked (ConfigError naming it)."""
    where = "clients.%s" % name
    if not isinstance(spec, dict):
        raise ConfigError("[%s] must be a table" % where)
    unknown = set(spec) - _POLICY_KEYS
    if unknown:
        raise ConfigError("%s: unknown setting %s" % (where, ", ".join(sorted(unknown))))
    recall = spec.get("recall")
    if recall is not None:
        if not isinstance(recall, list) or not all(isinstance(s, str) for s in recall):
            raise ConfigError("%s.recall must be a list of store names" % where)
        recall = tuple(recall)
    for key, kind in (("write", str), ("tenants", str), ("public", bool), ("pin_writes", bool), ("explain", bool)):
        value = spec.get(key)
        if value is not None and not isinstance(value, kind):
            raise ConfigError("%s.%s must be %s" % (where, key, kind.__name__))
    for key in ("daily_writes", "daily_writes_total", "rate_limit", "rate_limit_total", "tenant_token_generation"):
        _limit("%s.%s" % (where, key), spec.get(key))
    binding = spec.get("tenant_binding", "request")
    if binding not in TENANT_BINDINGS:
        raise ConfigError("%s.tenant_binding must be \"request\" or \"token\"" % where)
    if binding == "token" and spec.get("tenants") is None:
        raise ConfigError("%s.tenant_binding = \"token\" needs tenants: only a tenant client has a tenant "
                          "to bind its tokens to" % where)
    # A setting nothing reads would look like protection: refuse it, as an unenforced cap is refused.
    if "tenant_token_generation" in spec and binding != "token":
        raise ConfigError("%s.tenant_token_generation only applies with tenant_binding = \"token\"" % where)
    # token_env may be left out: the client then has no token and is reachable only in process.
    token_env = spec.get("token_env")
    if token_env is not None and (not isinstance(token_env, str) or not token_env):
        raise ConfigError("%s.token_env must name an environment variable" % where)
    if token_env is None and binding == "token":
        raise ConfigError("%s.token_env is required with tenant_binding = \"token\": it names the minting secret"
                          % where)
    origins = _names_of(where, "origins", spec.get("origins"), ORIGINS, DEFAULT_ORIGINS, "an origin")
    fields = _names_of(where, "fields", spec.get("fields"), FIELDS, (), "a record field")
    platforms = _checked_names(where, "platforms", spec.get("platforms"), valid_platform,
                               "platform names (lower case letters, digits, _ . -)")
    only_for = _checked_names(where, "only_for", spec.get("only_for"), valid_person,
                              "names of people in [identity.people]")
    audience = _audience(where, spec["audience"]) if "audience" in spec else None
    public = spec.get("public")
    if public is None:
        public = audience is not None and audience != "owner"  # derived; a stated one is checked by the Gateway
    return ClientPolicy(
        name=name, recall=recall, write=spec.get("write"), tenants=spec.get("tenants"),
        public=public, audience=audience, daily_writes=spec.get("daily_writes"),
        daily_writes_total=spec.get("daily_writes_total"), rate_limit=spec.get("rate_limit", 60),
        rate_limit_total=spec.get("rate_limit_total"), pin_writes=spec.get("pin_writes", False),
        explain=spec.get("explain", False), tenant_binding=binding,
        tenant_token_generation=spec.get("tenant_token_generation", 1), origins=origins, fields=fields,
        platforms=platforms, only_for=only_for,
    )


def _pieces(kind, specs, env, where, defaults, services=None):
    """The list of pieces for an ordered slot (recall stages, write guards).

    A piece whose factory has a `needs` tuple gets those services as keyword arguments
    (`forgetting=<Forgetting>`); a service that is not configured is a ConfigError."""
    services = services or {}
    if specs is None:
        specs = list(defaults)
    if not isinstance(specs, list):
        raise ConfigError("%s must be a list" % where)
    out = []
    for index, spec in enumerate(specs):
        if isinstance(spec, dict):
            spec = resolve_env(spec, env, "%s[%d]" % (where, index))
        name = spec if isinstance(spec, str) else spec.get("type") if isinstance(spec, dict) else None
        extra = {}
        for need in getattr(registry.resolve(kind, name), "needs", ()):
            if services.get(need) is None:
                raise ConfigError("%s[%d] (%s) needs the %s service: add a [%s] section" % (
                    where, index, name, need, need))
            extra[need] = services[need]
        out.append(registry.create(kind, spec, **extra))
    return out


_PROFILE_STAGES = ("min_score", "relative_score", "linked")


def _with_profile(specs, profile):
    """`specs` (a recall stage list, or None for the default one) with `profile` added to every min_score,
    relative_score and linked entry that does not set its own `profile` (for linked it only decides the default of
    max_added: 0 under "precise"). No profile (None) leaves the entries alone: the
    stages then use their own default, "broad". An explicit "precise" is added like any other. Entries that are not a
    name or a table are left for _pieces to refuse."""
    if profile is None:
        return specs
    if specs is None:
        specs = list(stages_module.DEFAULT_STAGES)
    if not isinstance(specs, list):
        return specs
    out = []
    for spec in specs:
        if spec in _PROFILE_STAGES:
            spec = {"type": spec, "profile": profile}
        elif isinstance(spec, dict) and spec.get("type") in _PROFILE_STAGES and "profile" not in spec:
            spec = dict(spec, profile=profile)
        out.append(spec)
    return out


def _redactor(spec, env, where):
    """A redactor from a name or a table with a type and options (`_env` options resolved)."""
    if isinstance(spec, dict):
        spec = resolve_env(spec, env, where)
    elif not isinstance(spec, str):
        raise ConfigError("%s must be a redactor name or a table with a type" % where)
    redactor = registry.create("redactor", spec)
    if not callable(redactor):
        raise ConfigError("%s did not give a text cleaner (a function str -> str)" % where)
    return redactor


def _enforced(pieces, what):
    return any(what in (getattr(piece, "enforces", ()) or ()) for piece in pieces)


def _check_enforced(clients, stages, guards):
    """A client setting that no configured piece enforces would be a silent hole: refuse it."""
    for name, policy in clients.items():
        where = "clients.%s" % name
        if (policy.daily_writes is not None or policy.daily_writes_total is not None) \
                and not _enforced(guards, "daily_writes"):
            raise ConfigError("%s has a daily write cap, but no write guard enforces daily_writes: "
                              "add \"daily_cap\" to [write] guards" % where)
        if policy.public:
            if policy.can_recall and not _enforced(stages, "redaction"):
                raise ConfigError("%s is public and may recall, but no recall stage enforces redaction: "
                                  "add \"redact\" to [recall] stages" % where)
            if policy.can_write and not _enforced(guards, "redaction"):
                raise ConfigError("%s is public and may write, but no write guard enforces redaction: "
                                  "add \"redact\" to [write] guards" % where)
        if policy.reach != "owner" and policy.can_recall and not _enforced(stages, "record_audience"):
            raise ConfigError("%s shows what it recalls to other people, but no recall stage checks each record's "
                              "audience: add \"record_audience\" to [recall] stages" % where)


_FORGETTING_KEYS = {"ledger", "archive", "stores", "demote_below", "forget_below", "archive_days",
                    "sweep_every_hours", "pin_refs", "curve", "max_forget_fraction", "max_forget_min"}


def _number(where, value, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("%s must be a number" % where)
    if (low is not None and value < low) or (high is not None and value > high):
        raise ConfigError("%s is out of range" % where)
    return value


def _forgetting(cfg, stores, tenants, wall, audiences=None):
    """The Forgetting service from [forgetting], or None when there is no such section.
    Returns (forgetting, sweep_every_hours)."""
    if "forgetting" not in cfg:
        return None, None
    spec = _table(cfg, "forgetting")
    unknown = set(spec) - _FORGETTING_KEYS
    if unknown:
        raise ConfigError("forgetting: unknown setting %s" % ", ".join(sorted(unknown)))
    for key in ("ledger", "archive"):
        if not isinstance(spec.get(key), str) or not spec[key]:
            raise ConfigError("forgetting.%s must be a file path" % key)
    managed = spec.get("stores")
    if not isinstance(managed, list) or not managed or not all(isinstance(s, str) for s in managed):
        raise ConfigError("forgetting.stores must be a list of store or tenants provider names")
    for name in managed:
        if name in stores:
            for capability in FORGETTING_CAPABILITIES:
                if not callable(getattr(stores[name], capability, None)):
                    raise ConfigError("forgetting.stores: store %r does not support %r" % (name, capability))
        elif name not in tenants:
            raise ConfigError("forgetting.stores names %r, which is neither a store nor a tenants provider" % name)
    demote = _number("forgetting.demote_below", spec.get("demote_below", 0.3), 0, 1)
    forget = _number("forgetting.forget_below", spec.get("forget_below", 0.05), 0, 1)
    if forget >= demote:
        raise ConfigError("forgetting.forget_below must be lower than demote_below")
    days = _number("forgetting.archive_days", spec.get("archive_days", 90), 0)
    max_fraction = _number("forgetting.max_forget_fraction", spec.get("max_forget_fraction", 0.02), 0, 1)
    max_min = _number("forgetting.max_forget_min", spec.get("max_forget_min", 5), 0)
    if int(max_min) != max_min:
        raise ConfigError("forgetting.max_forget_min must be a whole number")
    hours = _number("forgetting.sweep_every_hours", spec.get("sweep_every_hours", 24), 0)
    pin_refs = spec.get("pin_refs", [])
    if not isinstance(pin_refs, list) or not all(isinstance(p, str) for p in pin_refs):
        raise ConfigError("forgetting.pin_refs must be a list of patterns")
    curve_spec = spec.get("curve", {"type": "ebbinghaus"})
    if not isinstance(curve_spec, dict):
        raise ConfigError("[forgetting.curve] must be a table")
    curve = registry.create("forgetting_curve", dict({"type": "ebbinghaus"}, **curve_spec))
    try:
        ledger = Ledger(spec["ledger"], wall=wall or time.time)
        archive = Archive(spec["archive"])
    except Exception as error:  # noqa: BLE001 - sqlite3.Error, OSError
        raise ConfigError("forgetting: cannot open the ledger or archive file: %s" % type(error).__name__) from None
    forgetting = Forgetting(ledger, archive, curve, stores=managed, demote_below=demote, forget_below=forget,
                            archive_days=days, pin_refs=pin_refs, max_forget_fraction=max_fraction,
                            max_forget_min=int(max_min), audiences=audiences,
                            wall=wall or time.time)
    return forgetting, hours


def _identity(cfg, env, clients):
    """The identity provider from [identity], or None when there is no such section.

    A client that may write can meet an account nobody listed, so then the key named by hash_key_env must be
    set; for a gateway whose clients only read it is optional (a variable that is not set is then no key)."""
    if "identity" not in cfg:
        return None
    spec = _table(cfg, "identity")
    if spec.get("type") == "table":
        unknown = set(spec) - _IDENTITY_KEYS
        if unknown:
            raise ConfigError("identity: unknown setting %s (known: %s)" % (
                ", ".join(sorted(unknown)), ", ".join(sorted(_IDENTITY_KEYS))))
    name = spec.get("hash_key_env")
    if name is not None:
        if not isinstance(name, str) or not name:
            raise ConfigError("identity.hash_key_env must name an environment variable")
        if not env.get(name) and not any(policy.can_write for policy in clients.values()):
            spec = {key: value for key, value in spec.items() if key != "hash_key_env"}
    # The table of people is not resolved: nothing in it names the environment, and an error about a stray
    # "<name>_env" key there would quote its value, which may be an account.
    resolved = resolve_env({key: value for key, value in spec.items() if key != "people"}, env, "identity")
    if "people" in spec:
        resolved["people"] = spec["people"]
    return registry.create("identity", resolved)


def _close_all(pieces):
    """Close what a failed build already opened (files, connections). Never raises."""
    for piece in pieces:
        close = getattr(piece, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass


def build_gateway(cfg, env=None, log=stderr_log, wall=None, **overrides):
    """A Gateway wired as the configuration says. `overrides` go to Gateway() as they are
    (tests pass a clock this way); `wall` is the wall clock of the forgetting service."""
    env = os.environ if env is None else env
    if not isinstance(cfg, dict):
        raise ConfigError("the configuration must be a table")
    unknown = set(cfg) - _TOP_KEYS
    if unknown:
        raise ConfigError("unknown section %s" % ", ".join(sorted(unknown)))
    # The fixed sections first: a typo there is found before anything is opened.
    server, recall_cfg, write_cfg, redaction = (
        _section(cfg, name) for name in ("server", "recall", "write", "redaction"))

    opened = []
    try:
        stores, audiences = {}, {}
        for name, spec in _table(cfg, "stores").items():
            if not isinstance(spec, dict):
                raise ConfigError("[stores.%s] must be a table" % name)
            spec, audience = _split_audience("stores", name, spec)
            if audience is not None:
                audiences[name] = audience
            stores[name] = registry.create("backend", resolve_env(spec, env, "stores." + name))
            opened.append(stores[name])

        tenants = {}
        for name, spec in _table(cfg, "tenants").items():
            if not isinstance(spec, dict):
                raise ConfigError("[tenants.%s] must be a table" % name)
            if name in stores:
                raise ConfigError("tenants.%s has the same name as a store" % name)
            spec, audience = _split_audience("tenants", name, spec)
            if audience is not None:
                audiences[name] = audience
            tenants[name] = registry.create("tenants", resolve_env(spec, env, "tenants." + name))
            opened.append(tenants[name])

        clients, tokens = {}, {}
        for name, spec in _table(cfg, "clients").items():
            clients[name] = client_policy(name, spec)  # checks token_env too
            token_env = spec.get("token_env")
            if token_env is None:
                tokens[name] = ""
                log("client %s has no token_env: it cannot authenticate over HTTP (in-process use only)" % name)
                continue
            raw = env.get(token_env, "")
            tokens[name] = usable_token(raw)
            # For a tenant-bound client the variable holds the minting secret, not a token.
            what = "minting secret" if clients[name].token_bound else "token"
            if raw and not tokens[name]:
                log("%s of client %s ignored: placeholder or shorter than %d characters"
                    % (what, name, MIN_TOKEN_CHARS))
            elif not raw:
                log("client %s has no %s (%s is not set): it cannot authenticate" % (name, what, token_env))
        if not clients:
            raise ConfigError("no clients configured: add a [clients.<name>] table")

        levels, _ = policies.load_audiences(stores, tenants, audiences)  # a restored memory gets its store's level
        forgetting, sweep_hours = _forgetting(cfg, stores, tenants, wall, levels)
        if forgetting is not None:
            opened.append(forgetting)
        services = {"forgetting": forgetting}
        stages = _pieces("recall_stage", _with_profile(recall_cfg.get("stages"), recall_cfg.get("profile")), env,
                         "recall.stages",
                         stages_module.DEFAULT_STAGES, services)
        guards = _pieces("write_guard", write_cfg.get("guards"), env, "write.guards",
                         write_module.DEFAULT_GUARDS, services)
        opened.extend(stages + guards)
        _check_enforced(clients, stages, guards)
        identity = _identity(cfg, env, clients)
        options = {
            "tenants": tenants,
            "audiences": audiences,
            "stages": stages,
            "guards": guards,
            "redactors": {
                "private": _redactor(redaction.get("private", "secrets"), env, "redaction.private"),
                "public": _redactor(redaction.get("public", "public"), env, "redaction.public"),
            },
            "log": log,
        }
        if identity is not None:
            options["identity"] = identity
        if forgetting is not None:
            options["forgetting"] = forgetting
            options["sweep_every_hours"] = sweep_hours
        for key, source, setting in (
            ("recall_deadline", recall_cfg, "deadline"), ("cold_recall_deadline", recall_cfg, "cold_deadline"),
            ("cold_after", recall_cfg, "cold_after"), ("remember_deadline", write_cfg, "deadline"),
            ("pool_size", server, "pool_size"), ("tenant_pool_total", server, "tenant_pool_total"),
            ("tenant_pool_per_tenant", server, "tenant_pool_per_tenant"),
            ("store_max_inflight", server, "store_max_inflight"),
        ):
            if setting in source:
                options[key] = source[setting]
        options.update(overrides)
        return Gateway(stores, clients, tokens, **options)
    except Exception:
        _close_all(reversed(opened))  # do not leave files open behind a failed build
        raise


def server_options(cfg):
    """(host, port, keyword options for make_server) from the [server] table."""
    server = dict(SERVER_DEFAULTS, **_section(cfg, "server"))
    extra = {key: server[key] for key in ("max_connections", "read_deadline") if key in server}
    return server["host"], server["port"], extra
