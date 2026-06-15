"""Infra asset repository module.

Implements the JSONB document model for infrastructure assets
(Phase 1 of the infra info storage design).

Three tables:

* ``infra_asset_types`` — global registry of type schemas (no
  ``project_id``; shared across all projects for consistent type
  definitions).
* ``infra_assets`` — main storage with
  ``UNIQUE(project_id, type, name)`` and JSONB columns for
  ``attributes`` and ``relationships``.
* ``infra_asset_history`` — built-in versioning. Every create /
  update / delete writes a row here with a full snapshot and
  diff metadata.

The ``JSONBType`` TypeDecorator in :mod:`.types` maps to
``JSONB`` on PostgreSQL and ``JSON`` on SQLite so the same
schema works on both drivers.
"""

from .models import InfraAsset, InfraAssetType, InfraAssetHistory, InfraChangeType
from .repository import SQLModelInfraRepository
from .types import (
    JSONBType,
    InfraTypeDefinition,
    INFRA_TYPE_DEFINITIONS,
)

__all__ = [
    # Models
    "InfraAsset",
    "InfraAssetType",
    "InfraAssetHistory",
    "InfraChangeType",
    # Repository
    "SQLModelInfraRepository",
    # Types
    "JSONBType",
    "InfraTypeDefinition",
    "INFRA_TYPE_DEFINITIONS",
]
