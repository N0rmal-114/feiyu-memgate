"""Who is who: one table of people and their accounts, the same answer for every host that asks.

A host reports who is speaking as an account, `<platform>:<id>` (`aiocqhttp:10001`). An identity provider
turns an account into the name of a person the operator chose, or, for an account nobody listed, into a
stand-in code that is stable inside one store and means nothing outside it. The gateway resolves every
account before any guard or store sees it (gateway/fields.py), so a raw account is never stored or logged.

The built-in provider is `table`, configured by `[identity]` (docs/configuration.md). To write another one,
subclass IdentityProvider, register it as kind "identity" and name it in `type` (docs/extending.md).

Standard library only.
"""
import hashlib
import hmac

from .errors import ConfigError
from .gateway.tokens import usable_token
from .model import PERSON, RESERVED_PEOPLE, valid_account
from .registry import register

CODE_PREFIX = "anon:"
CODE_CHARS = 16  # hex characters of the stand-in code: 64 bits


class IdentityProvider:
    """The interface of an identity provider. Every method must be quick and must never raise for an
    odd account (an unknown one resolves to None).

    resolve(account)   the person's name, or None when the account is in no one's list
    owner              the name of the owner
    code(account, scope)
                       the stand-in for an account nobody listed: "anon:" and 16 hex characters. `scope` names
                       the store the record goes to, so the same account has another code in another store
    people()           {name: number of accounts}, never the accounts themselves (for `check`)
    has_key            True when a key for the codes is configured (never the key)
    ready              True when code() may be used: a key is there and strong enough
    """

    @property
    def owner(self):
        raise NotImplementedError

    def resolve(self, account):
        raise NotImplementedError

    def code(self, account, scope):
        raise NotImplementedError

    def people(self):
        raise NotImplementedError

    @property
    def has_key(self):
        return True

    @property
    def ready(self):
        return True


def _label(name, number):
    """How an error names one person of the table: by name when it is a plain name (never an account), else
    by position."""
    if isinstance(name, str) and PERSON.fullmatch(name) is not None:
        return "identity.people.%s" % name
    return "identity.people (person %d)" % number


@register("identity", "table")
class TableIdentity(IdentityProvider):
    """A fixed table of people and their accounts from the configuration; stand-in codes are
    HMAC-SHA256 of the store's name and the account under a key from the environment."""

    def __init__(self, people=None, hash_key=None, **unknown):
        if unknown:
            raise ConfigError("identity: unknown setting %s (known: type, hash_key_env, people)"
                              % ", ".join(sorted(str(key) for key in unknown)))
        if hash_key is not None and (not isinstance(hash_key, str) or not hash_key):
            raise ConfigError("identity.hash_key_env must name an environment variable that holds the key")
        self._key = hash_key
        self._people = {}   # name -> tuple of accounts
        self._by_account = {}
        self._owner = None
        self._load(people)

    def _load(self, people):
        if not isinstance(people, dict) or not people:
            raise ConfigError("identity.people must list the people: one table [identity.people.<name>] each, "
                              "one of them with owner = true")
        owners = []
        for number, (name, spec) in enumerate(people.items(), 1):
            where = _label(name, number)
            if not isinstance(name, str) or PERSON.fullmatch(name) is None:
                raise ConfigError("identity.people (person %d): a name is lower-case letters, digits, _ and -, "
                                  "starting with a letter or digit, at most 32 characters" % number)
            if name in RESERVED_PEOPLE:
                raise ConfigError("%s: the names %s are reserved" % (where, " and ".join(RESERVED_PEOPLE)))
            if not isinstance(spec, dict):
                raise ConfigError("[%s] must be a table" % where)
            unknown = set(spec) - {"owner", "accounts"}
            if unknown:
                raise ConfigError("%s: unknown setting %s (known: owner, accounts)"
                                  % (where, ", ".join(sorted(str(key) for key in unknown))))
            if not isinstance(spec.get("owner", False), bool):
                raise ConfigError("%s.owner must be true or false" % where)
            accounts = spec.get("accounts", [])
            if not isinstance(accounts, list):
                raise ConfigError("%s.accounts must be a list of accounts, each <platform>:<id>" % where)
            for index, account in enumerate(accounts, 1):
                if not valid_account(account):
                    raise ConfigError("%s.accounts: entry %d is not an account of the form <platform>:<id> "
                                      "(platform in lower case, no spaces, at most 64 characters in all)"
                                      % (where, index))
                if account in self._by_account:
                    other = self._by_account[account]
                    raise ConfigError("identity.people: an account is listed %s" % (
                        "twice for %s" % name if other == name else "for both %s and %s" % (other, name)))
                self._by_account[account] = name
            self._people[name] = tuple(accounts)
            if spec.get("owner", False):
                owners.append(name)
        if len(owners) != 1:
            raise ConfigError("identity.people: exactly one person must have owner = true (found %d)" % len(owners))
        self._owner = owners[0]

    @property
    def owner(self):
        return self._owner

    def resolve(self, account):
        try:
            return self._by_account.get(account)
        except TypeError:  # an unhashable value is no account
            return None

    def people(self):
        return {name: len(accounts) for name, accounts in self._people.items()}

    @property
    def has_key(self):
        return bool(self._key)

    @property
    def ready(self):
        return bool(usable_token(self._key))

    def code(self, account, scope):
        if not self._key:
            raise ValueError("no hash key is configured")
        message = (scope + "\n" + account).encode("utf-8", "surrogateescape")
        mac = hmac.new(self._key.encode("utf-8", "surrogateescape"), message, hashlib.sha256)
        return CODE_PREFIX + mac.hexdigest()[:CODE_CHARS]
