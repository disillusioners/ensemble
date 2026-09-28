"""``MaintenanceRun`` SQLModel — Section 1 Maintenance Console audit trail.

AM-15 — the FINAL schema, conventions-validated, replaces every
prior sketch and the research-findings §4.1 draft. Verbatim from
``architecture-recommendation.md`` Focus Area 5:

    column                 type           notes
    ─────────────────────  ─────────────  ───────────────────────────────────
    run_id                 TEXT PK        ckpt-<YYYYMMDD_HHMMSSffffff>-<hex8> [AM-8]
    section                TEXT NOT NULL  default 'checkpoint-cleanup' (v1)
    kind                   TEXT NOT NULL  'auto' | 'manual_dry_run' | 'manual_execute'
    started_at             TEXT NOT NULL  now_utc_iso() [INV-7/AM-15]
    completed_at           TEXT NULL      now_utc_iso() on terminal write
    status                 TEXT NOT NULL  'running' | 'succeeded' | 'failed' | 'interrupted'
    triggered_by           TEXT NOT NULL  'system' | 'user' (no session ids exist)
    requester_json         JSONBType NULL {peer_ip, user_agent, origin}; NULL for auto
    dry_run_run_id         TEXT NULL      soft ref, no FK [AM-15]
    expected_bytes         BIGINT  NULL   echoed promise [AM-15]; BigInteger (PG int8) — >2GiB cleanups
    dry_run_summary_json   JSONBType NULL full dry-run snapshot incl. skipped[] [AM-15]
    confirm                BOOLEAN NULL   [AM-15]
    advisory               TEXT NULL     'system_busy' | NULL [AM-12/AM-15]
    env_flags_json         JSONBType NULL {blob_prune_dry_run, blob_prune_destructive, destructive_override}
    summary_json           JSONBType NULL outcome: BlobPruneSummary + Op D counts + duration_ms
    error_json             JSONBType NULL {code, message}

JSON columns use :class:`JSONBType` (``daemon/repositories/infra/types.py``):
JSONB on PG / JSON on SQLite. Raw PG ``JSONB`` breaks SQLite
``create_all`` [AM-15]; the decorator keeps one SQLModel portable.

Text timestamps via ``now_utc_iso()`` — zero ``sa.DateTime`` columns in
the repo (sidesteps naive/tz trap entirely, INV-7).

``__table_args__`` carries the partial unique index on
``(section) WHERE status='running'`` — the DB single-flight claim
(AM-5). ``postgresql_where`` + ``sqlite_where`` so ``create_all`` builds
it on BOTH drivers. Phase-1 verification gate is integration case 64
(the partial-index render on both drivers).
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, Boolean, Column, Index, Text, text
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


class MaintenanceRun(SQLModel, table=True):
    """One row per Section 1 maintenance run (Section-1: 'checkpoint-cleanup').

    Lifecycle (AM-6): ``running`` → ``succeeded`` / ``failed`` /
    ``interrupted``. ``overlap_refused`` is DELETED from the schema
    and the audit story (architect ruling over the prior draft's
    rows-for-refusals detail: 404-noise churn from double-clicks
    outweighs attempt-audit value; the refused acquire produces ONE
    INFO log line with requester forensics instead).

    Boot sweep (AM-7 / T8): any ``running`` row observed at lifespan
    start is flipped to ``interrupted`` with ``error_json.code =
    'run_interrupted'`` — unconditional, no age gate (a boot-time
    ``running`` row is an orphan by definition under the single-daemon
    assumption; an age gate would orphan young rows).
    """

    __tablename__ = "maintenance_runs"

    __table_args__ = (
        # AM-5 / AM-15 — partial unique index, dual-dialect. The DB
        # single-flight claim: at most ONE ``running`` row per
        # ``section``. Postgres and SQLite both render the where-clause
        # (PG: ``postgresql_where``; SQLite: ``sqlite_where``); case 64
        # verifies the render on both drivers.
        Index(
            "uq_maintenance_runs_running_section",
            "section",
            unique=True,
            postgresql_where=text("status = 'running'"),
            sqlite_where=text("status = 'running'"),
        ),
        # Composite index for ``latest_completed_for_section`` queries
        # (kind filter + ORDER BY completed_at DESC). TEXT ISO
        # timestamps sort lexicographically (chronological), so a
        # composite (section, completed_at) index suffices.
        Index(
            "ix_maintenance_runs_section_completed",
            "section",
            "completed_at",
        ),
    )

    run_id: str = Field(
        max_length=64,
        sa_column=Column("run_id", Text, primary_key=True),
        description="ckpt-<YYYYMMDD_HHMMSSffffff>-<hex8> — externally-facing id + polling key",
    )
    section: str = Field(
        default="checkpoint-cleanup",
        sa_column=Column(
            "section", Text, nullable=False, default="checkpoint-cleanup"
        ),
        description="Section v1 constant; future sections reuse the table",
    )
    kind: str = Field(
        sa_column=Column("kind", Text, nullable=False),
        description="'auto' | 'manual_dry_run' | 'manual_execute'",
    )
    started_at: str = Field(
        sa_column=Column("started_at", Text, nullable=False),
        description="now_utc_iso() — TEXT ISO; NULL never for any row",
    )
    completed_at: Optional[str] = Field(
        default=None,
        sa_column=Column("completed_at", Text, nullable=True, default=None),
        description="now_utc_iso() on terminal write; NULL while running",
    )
    status: str = Field(
        default="running",
        sa_column=Column("status", Text, nullable=False, default="running"),
        description="'running' | 'succeeded' | 'failed' | 'interrupted'",
    )
    triggered_by: str = Field(
        sa_column=Column("triggered_by", Text, nullable=False),
        description="'system' | 'user' — no session ids exist",
    )
    requester_json: Optional[dict] = Field(
        default=None,
        sa_column=Column("requester_json", JSONBType, nullable=True, default=None),
        description="{peer_ip, user_agent, origin} — forensics, never attribution; NULL for auto",
    )
    dry_run_run_id: Optional[str] = Field(
        default=None,
        sa_column=Column("dry_run_run_id", Text, nullable=True, default=None),
        description="Soft ref to the manual dry-run row (no FK — dialect-divergent cascade trap)",
    )
    expected_bytes: Optional[int] = Field(
        default=None,
        sa_column=Column(
            "expected_bytes", BigInteger, nullable=True, default=None
        ),
        description=(
            "Echoed promise from the execute payload (AM-15). BigInteger "
            "to hold legitimate >2GiB dry-run byte totals — PostgreSQL "
            "INTEGER (int4) overflows at 2^31-1 (incident 2026-09-28, "
            "27.2 GiB echo). The PG-side widening DO block lives in "
            "EnsembleManager._ensure_postgres_columns "
            "(migration: daemon/migrations/versions/"
            "20260928_000001_widen_maintenance_runs_expected_bytes.sql, "
            "MANUAL: TRUE)."
        ),
    )
    dry_run_summary_json: Optional[dict] = Field(
        default=None,
        sa_column=Column(
            "dry_run_summary_json", JSONBType, nullable=True, default=None
        ),
        description="Full dry-run snapshot incl. skipped[] — self-contained audit (AM-15)",
    )
    confirm: Optional[bool] = Field(
        default=None,
        sa_column=Column("confirm", Boolean, nullable=True, default=None),
        description="Confirm flag from the execute payload (AM-15)",
    )
    advisory: Optional[str] = Field(
        default=None,
        sa_column=Column("advisory", Text, nullable=True, default=None),
        description="'system_busy' | NULL — advisory, never a refusal (AM-12)",
    )
    env_flags_json: Optional[dict] = Field(
        default=None,
        sa_column=Column("env_flags_json", JSONBType, nullable=True, default=None),
        description=(
            "{blob_prune_dry_run, blob_prune_destructive, destructive_override} — "
            "proves INV-2 (override kwarg armed the DELETE, not env) [AM-15]"
        ),
    )
    summary_json: Optional[dict] = Field(
        default=None,
        sa_column=Column("summary_json", JSONBType, nullable=True, default=None),
        description="Outcome: BlobPruneSummary + Op D counts + duration_ms",
    )
    error_json: Optional[dict] = Field(
        default=None,
        sa_column=Column("error_json", JSONBType, nullable=True, default=None),
        description="{code, message} — terminal failure (boot-sweep / shutdown / infra)",
    )


__all__ = ["MaintenanceRun"]
