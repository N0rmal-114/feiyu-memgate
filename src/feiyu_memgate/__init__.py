"""feiyu-memgate: a pluggable memory gateway for chat agents.

Every part is a piece you can swap: where memories live (backend), how recall
results are tidied (recall stages), what a write must pass (write guards), how
text is cleaned (redactors) and how tenants get their own store (tenants).
See docs/pieces.md.
"""
from .errors import BackendError, ConfigError, WriteRejected
from .registry import available, create, register
from .model import Candidate, Hit

__version__ = "0.1.0"

__all__ = [
    "BackendError", "ConfigError", "WriteRejected", "Candidate", "Hit",
    "available", "create", "register", "__version__",
]
