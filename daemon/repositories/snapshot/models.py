"""SQLModel table definitions for the Agent Snapshot system.

Agent Snapshot v1 — PR3 (design-exploration §3.2, Rev 5 per-instance
pivot; two tables — the Rev 4 ``snapshot_nodes`` table is DELETED).

* :class:`Snapshot` — one row per captured instance: header + digest
  + filterable search metadata + R8 typed ``dim:value`` tags.
* :class:`SnapshotEmbedding` — mirrors :class:`SkillEmbedding`
  (``daemon/repositories/skill/models.py``) exactly: JSONB float
  arrays, no numpy / BYTEA / pickle.

Status lifecycle (single column, no dead states):

* ``running`` — row-ledger lane (D3): inserted by
  ``SnapshotService.capture_async`` before the asyncio background
  capture starts.
* ``active`` — capture succeeded; SEARCHABLE (design §3.2 "``active``
  on create" writer).
* ``failed`` — capture raised; error detail lives in the digest's
  ``error`` key. The agent sees the failure and can re-invoke.
* ``interrupted`` — boot sweep found a ``running`` row whose daemon
  died mid-capture (same shape as ``JobRecoveryService.recover_on_startup``,
  scoped to ONE table).
* ``superseded`` — R12 create-mints-successor flipped it (atomic with
  the successor row's INSERT).

The design's §3.2 "``active`` | ``superseded`` ONLY" constraint is the
SEARCHABLE-lifecycle constraint — ``fresh | stale | expired`` are
COMPUTED at query time and never stored (no dead ``expired`` column
state); ``running`` / ``failed`` / ``interrupted`` are the D3 row-ledger
transient states that exist only between ``capture_async`` and its
terminal write.

``target_instance_id`` and ``supersedes_snapshot_id`` are SOFT
references (plain TEXT, no DB-level FK), per the ``superseded_by_id``
precedent (``daemon/repositories/project/models.py:215-222``): the
instance terminate/revive lifecycle makes hard FKs wrong, and dialect-
divergent ON DELETE behavior (PG enforces, SQLite defaults OFF) would
turn cascade questions into database-internal side effects.

All timestamps are ISO-8601 strings for cross-driver consistency.

**R16 monitoring counters** (Wave 3 — DB guardrail: NEW-TABLE-ONLY,
no modifications to existing tables):

* :class:`SnapshotUsageCounter` — one row per ``(scope, key)``
  pair, holding an ``int`` count. Two scopes ship in v1:
  - ``"capture:agent:<agent_id>"`` — increments on every
    ``snapshot_create`` invocation REGARDLESS of R9 verdict
    (REUSE + NEW + SUPERSEDE + CREATE-FRESH all increment).
  - ``"spawn:snapshot:<snapshot_id>"`` — increments on the
    ``spawn_hot_instance`` WARM path ONLY. Cold spawns
    (``None`` snapshot consumption / no-hit / expired /
    verify-failed) DO NOT increment (R16 rider j).

  MONITORING ONLY — explicitly NOT a ranking signal (R10 forbids
  usage-ranking in v1; the snapshot_search / snapshot_embedding
  modules never read this table — pinned by the
  ``MonitoringOnlyPinTest`` in
  tests/unit/tools/test_snapshot_v3.py::TestMonitoringOnlyPin).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String, Text

from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# Searchable lifecycle states (design §3.2 — the ONLY states
# snapshot_search / spawn_hot_instance candidate filters may match).
SNAPSHOT_STATUS_ACTIVE = "active"
SNAPSHOT_STATUS_SUPERSEDED = "superseded"

# D3 row-ledger transient states (design §2.4 — the trigger lane).
SNAPSHOT_STATUS_RUNNING = "running"
SNAPSHOT_STATUS_FAILED = "failed"
SNAPSHOT_STATUS_INTERRUPTED = "interrupted"

# Full writable set — fail-loud guard for the one write path that
# takes a status string (``set_status``).
SNAPSHOT_STATUSES = frozenset(
    {
        SNAPSHOT_STATUS_ACTIVE,
        SNAPSHOT_STATUS_SUPERSEDED,
        SNAPSHOT_STATUS_RUNNING,
        SNAPSHOT_STATUS_FAILED,
        SNAPSHOT_STATUS_INTERRUPTED,
    }
)


class Snapshot(SQLModel, table=True):
    """One captured instance: header + digest + search metadata + tags.

    Attributes:
        id: UUID4 primary key.
        project_id: Owning project (FK → ``projects.project_id``).
            Capture stamps it from the TARGET instance's project;
            search is project-scoped by default (design D8 —
            permanent).
        created_by_agent_id: Agent that invoked the capture (cost
            forensics + R16 capture counts).
        target_instance_id: The captured instance. SOFT reference
            (no FK) — the terminate/revive lifecycle makes hard
            FKs wrong (``superseded_by_id`` precedent).
        title: Human-readable name (e.g.
            ``"version-pump-taskpack-after-v0.13.9"``).
        task_summary: BM25 corpus text for hybrid search (PR5).
        domain_tags: R8 typed ``dim:value`` strings (auto-derived +
            judgment), stored as a JSONB string array. Queryable via
            ``tag_mode: all|any`` (PG ``@>`` containment; SQLite
            JSON-string scan at v1 volumes — rider (f)).

            Rider (f) status (Wave 2a): GIN index on ``domain_tags``
            is DEFERRED. No precedent in this repo for PG-side GIN
            index creation alongside ``create_all`` (the existing
            indexes in this module are btree composites); the
            migrations runner is SQLite-only
            (``daemon/migrations/runner.py:719-727``), so a
            PG-only migration is not on the standard path. At v1
            volumes the SQLite JSON scan is acceptable; on PG the
            ``(project_id, status)`` btree composite + the
            ``@>`` containment operator narrow candidates fast
            enough for the pilot scale. Phase-2 backlog: revisit
            with a dedicated PG-side GIN migration once volumes
            warrant it (the SQL would be
            ``CREATE INDEX ix_snapshots_domain_tags_gin ON snapshots
            USING GIN (domain_tags jsonb_path_ops);`` or equivalent).
        status: Lifecycle state — see module docstring. Searchable
            states are ``active`` / ``superseded`` ONLY.
        supersedes_snapshot_id: R12 soft self-ref — set when this row
            is the successor minted by create-mints-successor.
            Cross-root supersession is permitted (no same-root
            enforcement; design R12).
        repo_path / vcs_type / git_sha / git_branch / git_dirty:
            Repo anchor stamped at capture time (staleness
            ``verify=git`` opt-in — design §5.2).
        runtime_version: ``daemon.__version__`` at capture (staleness
            drift warning — design §5.2).
        effective_model: Model the digest LLM call actually resolved
            to (``SNAPSHOT_MODEL > COMPACTION_MODEL > session``) —
            cost forensics.
        digest: Stored knowledge — R11 8-tuple structure with the
            refs/artifacts section. Unbounded by the consume-side
            ~25k-token injection ceiling (design D6: the cap bounds
            INJECTION, not knowledge). NO ``truncated`` column
            (Rev 5 deletion — freshness is computed, never stored).
        created_at: ISO-8601 capture stamp (immutable).
    """

    __tablename__ = "snapshots"
    __table_args__ = (
        # Candidate-filter index for the project+status search path
        # (PR5: every search is project-scoped + status='active').
        Index("ix_snapshots_project_status", "project_id", "status"),
        # Supersession-chain walks + per-target lookups (R9 verdict:
        # "previous snapshot of this target exists").
        Index("ix_snapshots_target_instance", "target_instance_id"),
        Index("ix_snapshots_supersedes", "supersedes_snapshot_id"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    project_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("projects.project_id"),
            nullable=False,
        )
    )
    created_by_agent_id: str = Field(sa_column=Column(String, nullable=False))
    # SOFT reference — NO FK (design §3.2; superseded_by_id precedent).
    target_instance_id: str = Field(sa_column=Column(String, nullable=False))
    title: str = Field(sa_column=Column(String, nullable=False))
    task_summary: str = Field(
        default="", sa_column=Column(Text, nullable=False, default="")
    )
    domain_tags: list[str] = Field(
        default_factory=list,
        sa_column=Column("domain_tags", JSONBType, nullable=False, default=list),
    )
    status: str = Field(
        default=SNAPSHOT_STATUS_ACTIVE,
        sa_column=Column(
            String, nullable=False, default=SNAPSHOT_STATUS_ACTIVE
        ),
    )
    # SOFT self-reference — NO FK (R12 cross-root supersession permitted).
    supersedes_snapshot_id: str | None = Field(
        default=None, sa_column=Column(String, nullable=True, default=None)
    )
    repo_path: str | None = Field(
        default=None, sa_column=Column(String, nullable=True, default=None)
    )
    vcs_type: str | None = Field(
        default=None, sa_column=Column(String, nullable=True, default=None)
    )
    git_sha: str | None = Field(
        default=None, sa_column=Column(String, nullable=True, default=None)
    )
    git_branch: str | None = Field(
        default=None, sa_column=Column(String, nullable=True, default=None)
    )
    git_dirty: bool = Field(default=False, sa_column=Column(Boolean, default=False))
    runtime_version: str = Field(sa_column=Column(String, nullable=False))
    effective_model: str | None = Field(
        default=None, sa_column=Column(String, nullable=True, default=None)
    )
    digest: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("digest", JSONBType, nullable=False, default=dict),
    )
    created_at: str = Field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "created_by_agent_id": self.created_by_agent_id,
            "target_instance_id": self.target_instance_id,
            "title": self.title,
            "task_summary": self.task_summary,
            "domain_tags": list(self.domain_tags) if self.domain_tags else [],
            "status": self.status,
            "supersedes_snapshot_id": self.supersedes_snapshot_id,
            "repo_path": self.repo_path,
            "vcs_type": self.vcs_type,
            "git_sha": self.git_sha,
            "git_branch": self.git_branch,
            "git_dirty": bool(self.git_dirty),
            "runtime_version": self.runtime_version,
            "effective_model": self.effective_model,
            "digest": dict(self.digest) if self.digest else {},
            "created_at": self.created_at,
        }


class SnapshotEmbedding(SQLModel, table=True):
    """Cached per-snapshot embedding of a trigger query.

    Mirrors :class:`SkillEmbedding` (``daemon/repositories/skill/models.py``)
    exactly: ``trigger_query`` ≤512 chars, ``embedding`` a plain JSON
    array of floats via :class:`JSONBType` — the project standard for
    JSON-shaped columns (no numpy / BYTEA / pickle).

    Embeddings are computed AT CREATION ONLY — v1 snapshots are
    immutable (R12 supersession mints a fresh row with fresh
    embeddings; under Q8-A the successor's embedding rows STAY
    ALONGSIDE the superseded header — no cascade-delete on
    supersession). Rows are removed only with their parent
    ``snapshots`` row (``ON DELETE CASCADE``).
    """

    __tablename__ = "snapshot_embeddings"
    __table_args__ = (Index("ix_snapshot_embeddings_snapshot_id", "snapshot_id"),)

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    snapshot_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("snapshots.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    trigger_query: str = Field(
        sa_column=Column(String, nullable=False), max_length=512
    )
    embedding: list[float] = Field(
        default_factory=list,
        sa_column=Column("embedding", JSONBType, nullable=False),
    )
    created_at: str = Field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "id": self.id,
            "snapshot_id": self.snapshot_id,
            "trigger_query": self.trigger_query,
            "embedding": list(self.embedding) if self.embedding else [],
            "created_at": self.created_at,
        }


# R16 — monitoring counters (Wave 3; new-table-only per the DB guardrail).
# Scope/key composition rules:
#
#   scope = "capture:agent:<agent_id>"   — increments on every
#           ``snapshot_create`` invocation regardless of R9 verdict.
#   scope = "spawn:snapshot:<snapshot_id>" — increments on the
#           ``spawn_hot_instance`` WARM path ONLY. Cold / no-hit /
#           expired / verify-failed paths DO NOT increment (R16 rider j).
#
# ``agent_id`` / ``snapshot_id`` are the natural keys the FE / FE
# join surfaces will look up by. The two scopes are deliberately
# linear prefixes so a single ``WHERE scope LIKE 'capture:agent:%'``
# can scope a query without an enumeration pass.
CAPTURE_COUNTER_PREFIX = "capture:agent:"
SPAWN_COUNTER_PREFIX = "spawn:snapshot:"


class SnapshotUsageCounter(SQLModel, table=True):
    """R16 monitoring counter — increments, never decreases.

    Single-row upsert on ``(scope, key)`` collisions (SQLite uses
    ``ON CONFLICT`` upsert; PG uses the same clause via SQLAlchemy
    dialect insert). The default counter value is 0 (a row created
    on first observe, never read until it has at least one increment
    downstream).

    **Guarantees:**

    * **Fail-soft increments** — the metrics service wraps every
      write in try/except and logs on failure; spawn/create paths
      MUST never raise on counter failure (R16 rider j — counters
      must be cheap; no locks held across awaits; increments
      fail-soft — log, never raise, never fail the spawn/create).
    * **No downgrade** — counters only ever increment; a row's
      ``value`` is monotonically non-decreasing.
    * **Project scope is optional** — capture counts are per-agent
      (not per-project) in v1; the surface reads them aggregated
      across projects. Phase-2 may split per-project counts.
    """

    __tablename__ = "snapshot_usage_counters"
    __table_args__ = (
        # ``(scope, key)`` is the natural key — UNIQUE prevents the
        # race where two concurrent increments would both insert a
        # fresh row at zero and one would overwrite the other.
        # Read surfaces rely on the row count being the sum of
        # observable increments, never the count of UPSERT calls.
        Index(
            "ix_snapshot_usage_counters_scope_key",
            "scope",
            "key",
            unique=True,
        ),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    scope: str = Field(sa_column=Column(String, nullable=False))
    key: str = Field(sa_column=Column(String, nullable=False))
    value: int = Field(sa_column=Column("value", Integer, nullable=False, default=0))
    updated_at: str = Field(default_factory=_now_iso)
