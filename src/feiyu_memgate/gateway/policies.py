"""The start-up checks of a gateway's configuration, as plain functions.

Each takes what it needs (stores, tenants providers, clients, audiences) as arguments and raises
ConfigError with the text the operator reads; none touches a Gateway. They are their own file
so the rules about who may read which store can be read, and tested, apart from the request
handling. The Gateway keeps thin methods of the same names that call them.

Standard library only.
"""
from ..errors import ConfigError
from ..model import AUDIENCES, FIELDS, ORIGINS, TENANT_BINDINGS, TENANT_STORE, valid_person, valid_platform
from .tokens import MIN_TOKEN_CHARS


def _audience_choices():
    return ", ".join('"%s"' % level for level in AUDIENCES)


def load_audiences(stores, tenants, audiences):
    """(levels, declared): name -> level for every store and tenants provider (what was not declared
    gets its default), and the set of names that were declared."""
    levels = {name: "owner" for name in stores}
    levels.update({name: "group" for name in tenants})
    declared = set()
    for name, level in dict(audiences or {}).items():
        if name not in levels:
            raise ConfigError("audiences names %s, which is neither a store nor a tenants provider" % name)
        kind = "stores" if name in stores else "tenants"
        if level not in AUDIENCES:
            raise ConfigError("%s.%s.audience must be one of %s" % (kind, name, _audience_choices()))
        levels[name] = level
        declared.add(name)
    return levels, declared


def check_audience(where, policy, audiences, declared):
    """The two audience rules: a client reads only stores at least as wide as its own audience,
    and a client serving many groups reads no store that belongs to one group."""
    if policy.audience is not None and policy.audience not in AUDIENCES:
        raise ConfigError("%s.audience must be one of %s" % (where, _audience_choices()))
    if policy.audience == "owner" and policy.public:
        raise ConfigError("%s.audience is \"owner\" but public is true: an owner-level client is not public"
                          % where)
    if policy.audience in ("group", "public") and not policy.public:
        raise ConfigError("%s.audience is \"%s\" but public is false: a client whose audience is \"group\" or "
                          "\"public\" shows what it recalls to other people" % (where, policy.audience))
    reach = AUDIENCES.index(policy.reach)
    for entry in policy.recall or ():
        shared = entry != TENANT_STORE
        source = entry if shared else policy.tenants
        level, note = audiences[source], "" if source in declared else " (the default: it declares none)"
        if AUDIENCES.index(level) < reach:
            if shared:
                head = "%s.recall names the store %r, whose audience is \"%s\"%s" % (where, entry, level, note)
            else:
                head = "%s.recall names \"%s\", and the audience of tenants.%s is \"%s\"%s" % (
                    where, TENANT_STORE, source, level, note)
            raise ConfigError(
                "%s, but the client's audience is \"%s\": what it recalls would be shown to people the store "
                "is not for. take the store out of recall, or, if everything in it may be shown to them, set "
                "audience on [%s.%s]" % (head, policy.reach, "stores" if shared else "tenants", source))
        if shared and policy.tenants is not None and policy.reach == "group" and level == "group":
            raise ConfigError(
                "%s.recall names the store %r, whose audience is \"group\" (one group), but the client "
                "serves every tenant of %r: a store that all of them read must have audience \"public\""
                % (where, entry, policy.tenants))


def check_explain(where, policy):
    """An explained recall shows what a recall left out, so only a client that recalls and
    shows its answer to no one but the owner may have it."""
    if not policy.explain:
        return
    if not policy.can_recall:
        raise ConfigError("%s.explain needs recall: there is nothing to explain for a client that may not recall"
                          % where)
    if policy.reach != "owner":
        raise ConfigError("%s.explain is for owner-level clients: it shows the candidates a recall left out, "
                          "with their scores (the client's audience is \"%s\")" % (where, policy.reach))


def _names(values):
    return ", ".join('"%s"' % value for value in values)


def check_fields(where, policy):
    """The record fields a client lists, and the origins it may write, are known ones; only an owner-level
    client sees who a memory is about."""
    for setting, known, kind in (("origins", ORIGINS, "an origin"), ("fields", FIELDS, "a record field")):
        value = getattr(policy, setting)
        if not isinstance(value, (tuple, list)) or not all(isinstance(item, str) and item in known for item in value):
            raise ConfigError("%s.%s must be a list of %s, each one of %s" % (where, setting, kind, _names(known)))
    if policy.reach != "owner" and any(name in ("speaker", "about") for name in policy.fields):
        raise ConfigError("%s.fields may not include \"speaker\" or \"about\": only an owner-level client sees "
                          "who a memory is about" % where)


def check_identity(clients, identity):
    """The identity settings of every client against the identity table (None when there is none).

    `platforms` and `only_for` need a table to enforce them; each platform and person is a well-formed name,
    and every person of `only_for` is in the table. With a table, a client that may write needs a hash key
    that passes the floor of a token: it can meet an account nobody listed, and its stand-in code is only as
    private as the key. Messages name settings, never an account or the key."""
    for name, policy in clients.items():
        where = "clients.%s" % name
        for setting, check in (("platforms", valid_platform), ("only_for", valid_person)):
            value = getattr(policy, setting)
            if not isinstance(value, (tuple, list)) or not all(check(item) for item in value):
                raise ConfigError("%s.%s must be a list of %s" % (
                    where, setting, "platform names (lower case letters, digits, _ . -)" if setting == "platforms"
                    else "names of people in [identity.people]"))
            if value and identity is None:
                raise ConfigError("%s.%s needs an [identity] section: without one nothing would enforce it"
                                  % (where, setting))
        if identity is not None:
            table = identity.people()
            if any(person not in table for person in policy.only_for):
                raise ConfigError("%s.only_for names a person who is not in [identity.people]" % where)
    if identity is not None and not identity.ready and any(policy.can_write for policy in clients.values()):
        raise ConfigError("identity.hash_key_env: a client may write, so the key for the stand-in codes is "
                          "required, at least %d characters and not the placeholder" % MIN_TOKEN_CHARS)


def check_binding(where, policy):
    if policy.tenant_binding not in TENANT_BINDINGS:
        raise ConfigError("%s.tenant_binding must be \"request\" or \"token\"" % where)
    if policy.tenant_binding == "token" and policy.tenants is None:
        raise ConfigError("%s.tenant_binding = \"token\" needs tenants: only a tenant client has a tenant "
                          "to bind its tokens to" % where)
    generation = policy.tenant_token_generation
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise ConfigError("%s.tenant_token_generation must be a positive integer" % where)


def check_policies(stores, tenants, clients, audiences, declared):
    """Every client policy against the stores, tenants providers and audiences it names."""
    for name in tenants:
        if name in stores:
            raise ConfigError("tenants.%s has the same name as a store" % name)
    for name, policy in clients.items():
        where = "clients.%s" % name
        if policy.tenants is not None and policy.tenants not in tenants:
            raise ConfigError("%s.tenants names an unknown tenants provider %r" % (where, policy.tenants))
        check_binding(where, policy)
        for store in policy.recall or ():
            if store == TENANT_STORE:
                if policy.tenants is None:
                    raise ConfigError("%s.recall names %r but the client has no tenants" % (where, TENANT_STORE))
            elif store not in stores:
                raise ConfigError("%s.recall names an unknown store %r" % (where, store))
        if policy.write == TENANT_STORE:
            if policy.tenants is None:
                raise ConfigError("%s.write is %r but the client has no tenants" % (where, TENANT_STORE))
        elif policy.write is not None:
            if policy.write not in stores:
                raise ConfigError("%s.write names an unknown store %r" % (where, policy.write))
            if not getattr(stores[policy.write], "writable", False):
                raise ConfigError("%s.write names the read-only store %r" % (where, policy.write))
        check_audience(where, policy, audiences, declared)
        check_explain(where, policy)
        check_fields(where, policy)
