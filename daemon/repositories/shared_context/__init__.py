"""Shared Context Metadata module (Phase 1).

Exports the :class:`SharedContextMetadata` SQLModel table and the
:class:`SharedContextMetadataRepository` for the Shared Context
Metadata KV system. The table is a generic, partition-agnostic
``(context_key, meta_key) → meta_value`` store that any caller can
use for cross-cutting per-context KV scratch space.
"""

from .models import SharedContextMetadata
from .repository import SharedContextMetadataRepository

__all__ = [
    "SharedContextMetadata",
    "SharedContextMetadataRepository",
]
