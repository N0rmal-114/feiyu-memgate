"""Importing this module registers every built-in piece. The registry does it on first use."""
from .backends import embedding, hybrid, inmemory, openviking, sqlite  # noqa: F401
from .core import redact, stages, write  # noqa: F401
from .forgetting import curve, stage  # noqa: F401
from . import identity  # noqa: F401
from .tenants import directory, keyfile  # noqa: F401
