"""Agent Snapshot repository package (PR3).

Tables (design-exploration §3.2, Rev 5 per-instance pivot; the Rev 4
``snapshot_nodes`` table is DELETED) + R16 monitoring:

* ``snapshots`` — header + digest + filterable search metadata +
  R8 typed tags.
* ``snapshot_embeddings`` — per-snapshot trigger-query vectors
  (mirrors ``skill_embeddings``).
* ``snapshot_usage_counters`` — R16 monitoring (new-table-only per
  the DB guardrail; ``scope`` + ``key`` upserts; ``capture:agent:*``
  / ``spawn:snapshot:*`` namespaces).

See :mod:`.models` for the table definitions and :mod:`.repository`
for :class:`SnapshotRepository` (CRUD + R12 atomic supersession flip
+ D3 boot sweep).
"""

from .models import (
    SNAPSHOT_STATUSES,
    SNAPSHOT_STATUS_ACTIVE,
    SNAPSHOT_STATUS_FAILED,
    SNAPSHOT_STATUS_INTERRUPTED,
    SNAPSHOT_STATUS_RUNNING,
    SNAPSHOT_STATUS_SUPERSEDED,
    CAPTURE_COUNTER_PREFIX,
    SPAWN_COUNTER_PREFIX,
    Snapshot,
    SnapshotEmbedding,
    SnapshotUsageCounter,
)
from .repository import SnapshotRepository

__all__ = [
    # Models
    "Snapshot",
    "SnapshotEmbedding",
    "SnapshotUsageCounter",
    # R16 monitoring prefixes
    "CAPTURE_COUNTER_PREFIX",
    "SPAWN_COUNTER_PREFIX",
    # Status constants
    "SNAPSHOT_STATUSES",
    "SNAPSHOT_STATUS_ACTIVE",
    "SNAPSHOT_STATUS_SUPERSEDED",
    "SNAPSHOT_STATUS_RUNNING",
    "SNAPSHOT_STATUS_FAILED",
    "SNAPSHOT_STATUS_INTERRUPTED",
    # Repository
    "SnapshotRepository",
]
