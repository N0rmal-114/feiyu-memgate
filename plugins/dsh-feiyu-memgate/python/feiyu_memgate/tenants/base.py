"""The tenants slot: give every tenant a store of its own.

A tenant is whoever must not see anyone else's memories: a group chat, a guild, a
user. A client whose policy names a tenants provider must send tenant_id with every
request; the provider says whether the id is well-formed and hands out that tenant's
store. Standard library only.
"""
import abc


class TenantProvider(abc.ABC):
    """Subclass this and register it:

        @register("tenants", "mine")
        class MyTenants(TenantProvider):
            ...
    """

    @abc.abstractmethod
    def valid_id(self, tenant_id):
        """True when tenant_id is a well-formed id. Called before anything else is done
        with it, so it must be strict: the id ends up in log lines and limiter keys."""

    @abc.abstractmethod
    def store(self, tenant_id):
        """The MemoryBackend of this tenant, or None when the tenant is not registered.
        Called for every request, so it should cache."""

    # Optional: list() -> [tenant ids], every tenant the provider knows (never keys), so a
    # sweep such as forgetting can visit each store. A provider without it cannot be swept.

    def close(self):
        """Release what the provider holds."""
