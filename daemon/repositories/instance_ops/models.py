"""The ``instance_ops_audit`` SQLModel table (nest-ops D-2 / D-15).

The vocabulary is copied from ``repair_log``
(``daemon/repositories/ens_db/models.py``) minus its six SQL-repair
columns (``sql_text`` / ``sql_class`` / ``sql_hash`` / ``target_table``
/ ``nonce`` / ``ttl_expires_at``), plus the instance-ops-specific
columns.

THE AUDIT BOUNDARY IS A TWO-TIER PROPERTY (D-15) — stated here, in the
DDL comment of the ``.sql`` migration, and in the writer's docstring:

* **Tier 1 — true same-transaction.** ``instance_terminate`` (via the
  additive ``audit_sink`` hook in the lifecycle service) and
  ``instance_mark(protect|suspect)`` (via the optional ``session=`` on
  ``repo.set_metadata``) write the audit row INSIDE the act's own
  transaction. The act and the row commit together or NEITHER commits
  — a destructive act that cannot be audited never happens.
* **Tier 2 — write-ahead.** ``instance_nudge`` / ``instance_answer`` /
  ``instance_dismiss`` / ``instance_reclaim`` / ``instance_mark(park)``
  have no single transaction to join (they are daemon service calls
  that own their own transactions, and the question pack lives in
  memory). They INSERT the row FIRST with ``outcome="pending"`` and the
  full intent, run the act, then UPDATE the row to ``"committed"`` /
  ``"error"``. A crash mid-act leaves a durable ``pending`` row.

The two tiers are DECLARED, not silently de-coupled (R11's rule,
generalized and recorded rather than used to cut three real tools).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Column, Index, Integer, Text, text
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InstanceOpsAudit(SQLModel, table=True):
    """Append-only audit log for the ``instance_ops`` act tools.

    Every preview, every write-ahead intent and every committed act
    writes a row here. Refusals from the protected-target gate, the
    budget, and the act itself are ledgered; input-validation and
    early-gate refusals are not.

    Attributes:
        id: Primary key (UUID4).
        created_at: ISO-8601 timestamp (UTC).
        created_by_instance_id: Instance id of the ACTOR (the calling
            instance).
        created_by_agent_id: Agent id of the actor.
        action: The tool name (``instance_terminate``,
            ``instance_nudge``, ...).
        target: The target instance id.
        reason: The required act reason, verbatim. Empty when the act
            was REFUSED before a reason could be supplied (the
            ``reason-required`` refusal).
        dry_run: True when the row records a dry-run preview.
        before_snapshot: JSONB pre-act state (status, marker, children,
            the protected determination, ...).
        after_snapshot: JSONB post-act state.
        outcome: One of ``"previewed"``, ``"pending"`` (Tier-2
            write-ahead), ``"committed"``, ``"refused"``, ``"error"``.
        confirmed_target_id: The named-target confirmation (D-17) —
            the target the caller asserted the user consented to.
        provenance_class: ``"direct"`` | ``"inherited"`` | ``None``
            (D-18).
        provenance_depth: The verdict's ``depth``.
        root_source: The window's ``root_source``.
        root_message_id: The user's ``root_message_id`` — what a
            reviewer compares ``confirmed_target_id`` against (D-17).
        error_message: The refusal text when ``outcome != "committed"``.
        preview_run_id: Soft link from a preview row to the later
            applied row (the ``maintenance_runs.dry_run_run_id`` pattern).
    """

    __tablename__ = "instance_ops_audit"
    __table_args__ = (
        Index(
            "idx_instance_ops_audit_action_created",
            "action",
            "created_at",
        ),
        Index(
            "idx_instance_ops_audit_target",
            "target",
        ),
        Index(
            "idx_instance_ops_audit_outcome_created",
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
    created_by_instance_id: str | None = Field(
        default=None,
        sa_column=Column("created_by_instance_id", Text, nullable=True),
    )
    created_by_agent_id: str | None = Field(
        default=None,
        sa_column=Column("created_by_agent_id", Text, nullable=True),
    )

    action: str = Field(sa_column=Column("action", Text, nullable=False))
    target: str = Field(sa_column=Column("target", Text, nullable=False))
    reason: str = Field(
        default="",
        sa_column=Column("reason", Text, nullable=False, default=""),
    )
    # ``dry_run`` is Boolean on PG (BOOLEAN) and INTEGER on SQLite (type
    # affinity) — the ``resource_limit_policy.enabled`` precedent. The
    # companion .sql migration declares the SQLite side as
    # ``INTEGER NOT NULL DEFAULT 0``.
    dry_run: bool = Field(
        default=False,
        sa_column=Column("dry_run", Boolean, nullable=False),
    )

    before_snapshot: Any | None = Field(
        default=None,
        sa_column=Column("before_snapshot", JSONBType, nullable=True),
    )
    after_snapshot: Any | None = Field(
        default=None,
        sa_column=Column("after_snapshot", JSONBType, nullable=True),
    )

    outcome: str = Field(sa_column=Column("outcome", Text, nullable=False))

    confirmed_target_id: str | None = Field(
        default=None,
        sa_column=Column("confirmed_target_id", Text, nullable=True),
    )
    provenance_class: str | None = Field(
        default=None,
        sa_column=Column("provenance_class", Text, nullable=True),
    )
    provenance_depth: int | None = Field(
        default=None,
        sa_column=Column("provenance_depth", Integer, nullable=True),
    )
    root_source: str | None = Field(
        default=None,
        sa_column=Column("root_source", Text, nullable=True),
    )
    root_message_id: str | None = Field(
        default=None,
        sa_column=Column("root_message_id", Text, nullable=True),
    )

    error_message: str | None = Field(
        default=None,
        sa_column=Column("error_message", Text, nullable=True),
    )
    preview_run_id: str | None = Field(
        default=None,
        sa_column=Column("preview_run_id", Text, nullable=True),
    )
