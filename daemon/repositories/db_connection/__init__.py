"""Database connection registry module.

Exports the ``DbConnectionConfig`` SQLModel and the
``DbConnectionRepository`` for the Database Tool Category's
Phase 1 Connection Registry Layer.
"""

from .models import DbConnectionConfig
from .repository import DbConnectionRepository

__all__ = [
    "DbConnectionConfig",
    "DbConnectionRepository",
]
