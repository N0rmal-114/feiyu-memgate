"""Everything a piece author needs, from one module.

    from feiyu_memgate.pieces import RecallStage, KEEP, DROP, register

These names are the public contract for pieces (docs/pieces.md). Everything under
feiyu_memgate.core is internal and may change between releases; import from here.

Standard library only.
"""
from .backends.base import MemoryBackend
from .core.pool import NULL_CTL
from .core.recall import DEFER, DROP, KEEP, RecallContext, RecallStage
from .core.write import WriteContext, WriteGuard
from .errors import BackendError, ConfigError, WriteRejected
from .forgetting.curve import ForgettingCurve
from .model import Candidate, ClientPolicy, Hit
from .registry import register
from .tenants.base import TenantProvider

__all__ = [
    "MemoryBackend", "RecallStage", "WriteGuard", "TenantProvider", "ForgettingCurve",
    "KEEP", "DROP", "DEFER", "RecallContext", "WriteContext", "Hit", "Candidate", "ClientPolicy",
    "BackendError", "ConfigError", "WriteRejected", "register", "NULL_CTL",
]
