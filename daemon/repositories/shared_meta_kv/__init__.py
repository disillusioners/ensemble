"""Shared Meta KV module.

Exports the :class:`SharedMetaKV` SQLModel table and the
:class:`SharedMetaKVRepository` for the Shared Meta KV system. The
underlying DB table remains ``shared_context_metadata`` for backwards
compatibility with any existing rows, but the Python symbols have been
renamed to disambiguate the metadata key-value (KV) toolset from the
``context`` document-reading toolset.
"""

from .models import SharedMetaKV
from .repository import SharedMetaKVRepository

__all__ = [
    "SharedMetaKV",
    "SharedMetaKVRepository",
]
