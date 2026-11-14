"""Client side of feiyu-memgate: talk to a gateway, shape the query, format the block.

Standard library only; every call is blocking and never raises.
"""
from .block import BlockFormatter, format_block
from .http import MemoryClient
from .local import LocalClient
from .query import QueryShaper, build_query, meaningful, previous_user_text

__all__ = ["MemoryClient", "LocalClient", "BlockFormatter", "format_block", "QueryShaper", "build_query",
           "previous_user_text", "meaningful"]
