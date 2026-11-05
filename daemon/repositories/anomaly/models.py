"""The ``anomaly_reports`` + ``anomaly_clusters`` SQLModel tables.

The nest-level anomaly store (the ``nest-anomaly-report`` lane,
board ``wi-2d691ed2``). Two tables, ONE job each:

* :class:`AnomalyReport` — one agent-filed anomaly. The row carries the
  reporter identity, the CLOSED canonical category (plus the raw input
  it folded from), the severity tier, the scrubbed authored text, and
  the bounded evidence harvest (ancestor chain, job/mission linkage,
  a bounded transcript excerpt, a registry snapshot REFERENCE, wedge
  capture references).
* :class:`AnomalyCluster` — the incident identity anchor. The key is
  ``(tree_root_instance_id, canonical category)``; the identity is
  assigned ONCE at write time and never re-derived.

THE TWO TABLES ARE SEPARATE BY RULING (decisions.md R5): a cluster row
is what the metric family aggregates over and what the board item
points at. A query-time fold would re-implement the sliding window in
SQL AND make cluster identity retroactively unstable.

TIMESTAMPS (AM-4)
-----------------
Every stamp is an ISO-8601 TEXT value written by the LOCAL
:func:`_utc_now_iso` in the FIXED-WIDTH form
``datetime.now(timezone.utc).isoformat(timespec="microseconds")`` —
always 32 characters, always ``+00:00``. This is a DOCUMENTED
divergence from the ``notify_audit`` sibling, whose
``isoformat()``-without-timespec shape IS the hazard. Lexicographic
ISO comparison equals chronological comparison for this single
producer; ALL cutoffs are computed in PYTHON and bound as parameters
(never SQL ``now() - interval``).

JSON COLUMNS
------------
The house dual-face rule: SQLite ``JSON`` / PostgreSQL ``JSONB`` via
:class:`~daemon.repositories.infra.types.JSONBType` — one model, both
drivers, no per-dialect fork.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, Index, Integer, Text
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utc_now_iso() -> str:
    """The FIXED-WIDTH UTC stamp (AM-4).

    ``datetime.now(timezone.utc).isoformat(timespec="microseconds")``
    renders as ``YYYY-MM-DDTHH:MM:SS.ffffff+00:00`` — always 32
    characters, always ``+00:00``, always the same width. The width
    matters because the sliding-window comparisons are LEXICOGRAPHIC
    on the stored TEXT.
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


#: The outcome vocabulary (AM-5). ``accepted`` MEANS clustered — a row
#: that stands as ``accepted`` with a NULL ``cluster_id`` is an ORPHAN
#: and is terminalized to ``unclustered`` by the phase-2 sweep (never
#: reassigned: post-hoc reassignment is retroactive identity
#: instability, the very thing R5 rejects).
OUTCOME_ACCEPTED = "accepted"
OUTCOME_RATE_LIMITED = "rate_limited"
OUTCOME_PERSIST_FAILED = "persist-failed"
OUTCOME_UNCLUSTERED = "unclustered"

#: Every outcome the column may hold — the closed vocabulary.
OUTCOME_VALUES: tuple[str, ...] = (
    OUTCOME_ACCEPTED,
    OUTCOME_RATE_LIMITED,
    OUTCOME_PERSIST_FAILED,
    OUTCOME_UNCLUSTERED,
)

#: The severity tiers (decisions.md R3). A closed 3-tier set — an
#: out-of-set severity is a typed ``invalid-input`` REFUSAL, never a
#: silent fold (``critical`` → ``major`` would hide a nest-level event).
SEVERITY_MINOR = "minor"
SEVERITY_MAJOR = "major"
SEVERITY_CRITICAL = "critical"
SEVERITY_VALUES: tuple[str, ...] = (SEVERITY_MINOR, SEVERITY_MAJOR, SEVERITY_CRITICAL)


class AnomalyReport(SQLModel, table=True):
    """One filed anomaly report (the agent-authored row).

    Attributes:
        id: Primary key (UUID4).
        created_at: FIXED-WIDTH ISO-8601 UTC stamp (AM-4).
        agent_id: The reporting agent's TYPE id (AM-10 — the budget
            key is the type, never the instance).
        instance_id: The reporting instance.
        tree_root_instance_id: The ROOT of the reporter's ancestor
            chain (self when the chain is empty). NULLABLE on purpose:
            a refused report harvests NOTHING (R5b), so a
            ``rate_limited`` / ``persist-failed`` row has no tree root.
        parent_chain: The ancestor instance ids, PARENT → ROOT, SELF
            EXCLUDED (the helper's actual behaviour).
        parent_chain_agents: The agent labels ALIGNED with
            ``parent_chain`` (an unknown instance yields an empty
            string — labels never block).
        category: The CANONICAL category (the closed vocabulary).
        category_raw: The raw input, verbatim, capped at 64 chars —
            the folded-from value is preserved so a vocabulary gap
            stays visible.
        severity: ``minor`` | ``major`` | ``critical``.
        description: The scrubbed authored description (≤1000 chars).
        suspected_cause: The scrubbed reporter hypothesis (≤500 chars),
            nullable.
        excerpt: The scrubbed transcript excerpt (≤2000 chars total),
            nullable — NULL for ``minor`` severity (no transcript read
            at all) and NULL when the scrubber is absent (the
            fail-CLOSED leg).
        excerpt_sha256: SHA-256 of the STORED excerpt text.
        excerpt_length: The STORED excerpt's character length.
        job_id: The reporter's active job id (harvest).
        mission_id: The reporter's outstanding mission id (harvest).
        dispatch_chain: The daemon-minted delegation chain from the
            job metadata (harvest), nullable.
        boot_epoch: The daemon's boot epoch, NAIVE-UTC (AM-8d).
        registry_summary_sha256: SHA-256 of the registry families
            summary JSON — a REFERENCE, never a payload copy.
        registry_captured_at: When that summary was hashed.
        wedge_capture_ids: The matched wedge capture ids — REFERENCES
            only, never payloads.
        cluster_id: The cluster identity, assigned ONCE at write time.
            NULL in phase 1 (the cluster assignment lands in phase 2).
        outcome: One of :data:`OUTCOME_VALUES`.
        refusal_reason: The typed refusal token + short detail, when
            the row is a refusal.
    """

    __tablename__ = "anomaly_reports"
    __table_args__ = (
        Index(
            "idx_anomaly_reports_agent_created",
            "agent_id",
            "created_at",
        ),
        Index(
            "idx_anomaly_reports_cluster_id",
            "cluster_id",
        ),
        Index(
            "idx_anomaly_reports_tree_root_created",
            "tree_root_instance_id",
            "created_at",
        ),
        Index(
            "idx_anomaly_reports_category_created",
            "category",
            "created_at",
        ),
        Index(
            "idx_anomaly_reports_outcome_created",
            "outcome",
            "created_at",
        ),
    )

    id: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("id", Text, primary_key=True),
    )
    created_at: str = Field(
        default_factory=_utc_now_iso,
        sa_column=Column("created_at", Text, nullable=False),
    )
    agent_id: str = Field(sa_column=Column("agent_id", Text, nullable=False))
    instance_id: str = Field(
        sa_column=Column("instance_id", Text, nullable=False)
    )
    tree_root_instance_id: str | None = Field(
        default=None,
        sa_column=Column("tree_root_instance_id", Text, nullable=True),
    )
    parent_chain: list[str] = Field(
        default_factory=list,
        sa_column=Column("parent_chain", JSONBType, nullable=False),
    )
    parent_chain_agents: list[str] = Field(
        default_factory=list,
        sa_column=Column("parent_chain_agents", JSONBType, nullable=False),
    )
    category: str = Field(sa_column=Column("category", Text, nullable=False))
    category_raw: str = Field(
        sa_column=Column("category_raw", Text, nullable=False)
    )
    severity: str = Field(sa_column=Column("severity", Text, nullable=False))
    description: str = Field(
        sa_column=Column("description", Text, nullable=False)
    )
    suspected_cause: str | None = Field(
        default=None,
        sa_column=Column("suspected_cause", Text, nullable=True),
    )
    excerpt: str | None = Field(
        default=None,
        sa_column=Column("excerpt", Text, nullable=True),
    )
    excerpt_sha256: str | None = Field(
        default=None,
        sa_column=Column("excerpt_sha256", Text, nullable=True),
    )
    excerpt_length: int | None = Field(
        default=None,
        sa_column=Column("excerpt_length", Integer, nullable=True),
    )
    job_id: str | None = Field(
        default=None,
        sa_column=Column("job_id", Text, nullable=True),
    )
    mission_id: str | None = Field(
        default=None,
        sa_column=Column("mission_id", Text, nullable=True),
    )
    dispatch_chain: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column("dispatch_chain", JSONBType, nullable=True),
    )
    boot_epoch: str | None = Field(
        default=None,
        sa_column=Column("boot_epoch", Text, nullable=True),
    )
    registry_summary_sha256: str | None = Field(
        default=None,
        sa_column=Column("registry_summary_sha256", Text, nullable=True),
    )
    registry_captured_at: str | None = Field(
        default=None,
        sa_column=Column("registry_captured_at", Text, nullable=True),
    )
    wedge_capture_ids: list[str] | None = Field(
        default=None,
        sa_column=Column("wedge_capture_ids", JSONBType, nullable=True),
    )
    cluster_id: str | None = Field(
        default=None,
        sa_column=Column("cluster_id", Text, nullable=True),
    )
    outcome: str = Field(
        default=OUTCOME_ACCEPTED,
        sa_column=Column("outcome", Text, nullable=False, default=OUTCOME_ACCEPTED),
    )
    refusal_reason: str | None = Field(
        default=None,
        sa_column=Column("refusal_reason", Text, nullable=True),
    )


class AnomalyCluster(SQLModel, table=True):
    """One incident cluster — the identity anchor for a run of reports.

    Attributes:
        id: Primary key (UUID4) — assigned ONCE (R5 stability).
        cluster_key_hash: SHA-256 of ``"{tree_root}|{canonical
            category}"`` — the key components, hashed.
        tree_root_instance_id: The key component (the cluster's root).
        category: The key component (the CANONICAL category).
        first_report_at: The first member's stamp.
        last_report_at: The sliding-window anchor (R5) — the cluster
            stays open while ``last_report_at >= now - W``.
        report_count: The incremental member count.
        max_severity: The max-rank rollup (``minor`` < ``major`` <
            ``critical``).
        board_item_id: The board row the critical gate created (phase
            2), nullable.
        created_at: FIXED-WIDTH ISO-8601 UTC stamp (AM-4).
    """

    __tablename__ = "anomaly_clusters"
    __table_args__ = (
        Index(
            "idx_anomaly_clusters_key_window",
            "tree_root_instance_id",
            "category",
            "last_report_at",
        ),
    )

    id: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("id", Text, primary_key=True),
    )
    cluster_key_hash: str = Field(
        sa_column=Column("cluster_key_hash", Text, nullable=False)
    )
    tree_root_instance_id: str = Field(
        sa_column=Column("tree_root_instance_id", Text, nullable=False)
    )
    category: str = Field(sa_column=Column("category", Text, nullable=False))
    first_report_at: str = Field(
        sa_column=Column("first_report_at", Text, nullable=False)
    )
    last_report_at: str = Field(
        sa_column=Column("last_report_at", Text, nullable=False)
    )
    report_count: int = Field(
        default=1,
        sa_column=Column("report_count", Integer, nullable=False, default=1),
    )
    max_severity: str = Field(
        sa_column=Column("max_severity", Text, nullable=False)
    )
    board_item_id: str | None = Field(
        default=None,
        sa_column=Column("board_item_id", Text, nullable=True),
    )
    created_at: str = Field(
        default_factory=_utc_now_iso,
        sa_column=Column("created_at", Text, nullable=False),
    )


__all__ = [
    "OUTCOME_ACCEPTED",
    "OUTCOME_PERSIST_FAILED",
    "OUTCOME_RATE_LIMITED",
    "OUTCOME_UNCLUSTERED",
    "OUTCOME_VALUES",
    "SEVERITY_CRITICAL",
    "SEVERITY_MAJOR",
    "SEVERITY_MINOR",
    "SEVERITY_VALUES",
    "AnomalyCluster",
    "AnomalyReport",
]
