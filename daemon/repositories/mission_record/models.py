"""SQLModel table definitions for the Mission-Record Anchor (Proposal B PR1).

Completion-subsystem Proposal B — PR1 of 3 (dry-mints substrate).

Two new tables, both purely ADDITIVE:

* :class:`MissionRecord` — one row per mission EPOCH (``mission_records``).
  INSERT-per-epoch; closed rows are immutable; only ``status`` /
  attestation fields on OPEN rows mutate — the ``dependency_watchers``
  guarded-transition precedent. The record is the WRITE-SIDE truth for
  "what is this mission": the epoch ``mission_id`` is a fresh UUID4 per
  epoch (a deliberate break from the read-model's
  ``mission_id == instance_id`` lean — the legacy read-model
  ``MissionRecord`` dataclass (``daemon/services/mission_resolver.py:241``)
  later joins it; that read model is NOT touched by PR1, and it is a
  DIFFERENT symbol from the ``mission_records`` table below — no import
  alias is needed, no module imports both).

* :class:`MissionCommission` — one row per commissioned worker / work
  item (``mission_commissions``). The census substrate (D2) and the
  routing key (D1) for the PR2/PR3 read flips.

**PR1 contract — mints write, NOTHING reads.** The mode ladder
(``ENSEMBLE_MISSION_RECORDS=off|dry|enforce``, ship default ``dry``)
lives in :mod:`daemon.services.mission_record_service`. In ``dry`` mode
every commissioning lane records rows here and ALL gate / census /
anchor / routing reads stay on legacy behavior (zero behavior change).
``enforce`` (the read flip) is PR2; the routing + backstop flip is PR3.

Deliberately NOT extended: ``instance_ops_audit`` (an ops-safety ledger;
mission semantics are a different concern — proposal-b §2.1). The
``instance_nudge`` mint path writes the commission row adjacent to its
existing audit rows instead.

Schema mirrors ``daemon/migrations/versions/20261025_122631_*.sql``
(SQLite canonical DDL; the PG path is ``create_all`` for the brand-new
tables + the additive ``_ensure_postgres_columns`` statements for the
three additive columns — the dual-driver convention).
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Column, Index, String, Text, text
from sqlmodel import Field, SQLModel


# ============================================================
# Enums
# ============================================================


class MissionFoundingSource(str, enum.Enum):
    """Which commissioning lane founded the epoch (``founding_source``).

    The exact vocabulary from proposal-b §2.1. ``scheduler`` exists in
    the vocabulary for schema completeness; its MINT SITE is WIRED but
    INERT — flag-gated OFF by default (standing product ruling — lane 9
    stays bare: ``ENSEMBLE_MISSION_RECORDS_SCHEDULER_MINT`` must be set
    explicitly AND the mode ladder must not be ``off``);
    ``backfill`` marks synthetic epoch-0 rows written by the
    migration/backfill story (provenance auditability).
    """

    USER_API = "user_api"
    CHAT = "chat"
    JOB_CONTINUE = "job_continue"
    INSTANCE_NUDGE = "instance_nudge"
    REVIVE = "revive"
    SCHEDULER = "scheduler"
    BOOT = "boot"
    BACKFILL = "backfill"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._value2member_map_


class MissionStatus(str, enum.Enum):
    """Epoch lifecycle (``status``) — guarded transitions only.

    ``open`` → ``settled`` | ``closed`` | ``superseded``. The guarded
    ``UPDATE ... WHERE status='open'`` shape mirrors the
    ``dependency_watchers`` PENDING→FIRED pattern
    (``dependency_bus/models.py``): only the mission-record service is a
    writer, so a CHECK constraint would be belt-and-braces for a
    non-zero CPU cost — the value strings here are mirrored verbatim in
    the raw-SQL migration.
    """

    OPEN = "open"
    SETTLED = "settled"
    CLOSED = "closed"
    SUPERSEDED = "superseded"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._value2member_map_


class MissionProvenance(str, enum.Enum):
    """Row provenance (``provenance``) — migration auditability.

    ``live`` = minted by a real commissioning lane at act time.
    ``backfill`` = synthetic row written by the one-time backfill
    (adoption-delta (b) advisory commission rows; epoch-0 coverage).
    """

    LIVE = "live"
    BACKFILL = "backfill"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._value2member_map_


class MissionAttestMethod(str, enum.Enum):
    """Attestation-block method vocabulary (``attest_method``).

    ``dry`` is the stamp written when the attestation EVENT recording
    seam fires while the read flip has not happened yet (record-only —
    the Term-1 blocking policy stays default-OFF per the ruling).
    """

    LEADER_ATTEST = "leader_attest"
    JUDGE_COMPLETE = "judge_complete"
    META_BYPASS = "meta_bypass"
    DRY = "dry"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._value2member_map_


class CommissionLane(str, enum.Enum):
    """Which lane commissioned the work (``mission_commissions.lane``)."""

    SEND_MESSAGE = "send_message"
    INSTANCE_NUDGE = "instance_nudge"
    JOB_CONTINUE = "job_continue"
    SCHEDULER = "scheduler"
    SPAWN = "spawn"
    REVIVE = "revive"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._value2member_map_


class CommissionState(str, enum.Enum):
    """Commission lifecycle (``mission_commissions.state``).

    ``outstanding`` → ``satisfied`` | ``abandoned`` | ``superseded`` —
    every transition is a guarded ``UPDATE ... WHERE state='outstanding'``
    (the ``dependency_watchers`` PENDING→FIRED exactly-once shape).
    Multiple OUTSTANDING rows for the same worker are ALLOWED
    (adoption-delta (a): the guarded-state lifecycle is the
    discriminator; routing authority STAYED with the A-resolver until
    the PR2/PR3 flip — PR3 flipped it to the OLDEST outstanding row,
    the A-resolver surviving as the declared legacy fallback leg).
    """

    OUTSTANDING = "outstanding"
    SATISFIED = "satisfied"
    ABANDONED = "abandoned"
    SUPERSEDED = "superseded"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._value2member_map_


# ============================================================
# Shared column helpers
# ============================================================


def _new_uuid4() -> str:
    return str(uuid.uuid4())


def _utc_now_iso() -> str:
    """TEXT ISO-8601 UTC stamp — the TEXT-timestamp convention
    (``now_utc_iso()`` family: TEXT columns carry UTC ISO digits).

    Delegates to the sanctioned helper (``daemon/services/timestamps.py``)
    so the stamp is byte-identical to every other TEXT timestamp column.
    """
    from ...services.timestamps import now_utc_iso

    return now_utc_iso()


# The mint-idempotency predicate: the partial unique index covers only
# rows WITH a founding message. NULL-founding rows (boot / backfill)
# dedupe by resolve-if-none instead. Declared once so the model, this
# docstring, and the raw-SQL migration all cite the same predicate
# (``WHERE founding_message_id IS NOT NULL``).
FOUNDING_MSG_NOT_NULL_SQL = "founding_message_id IS NOT NULL"

# Proposal B PR2 — F1 carry: the OPEN-EPOCH partial unique predicate.
# At most ONE ``open`` epoch may exist per anchor at any time — the
# "one mission = one open epoch" invariant. Declared once so the model
# (``__table_args__``), the raw-SQL migration, and the PostgreSQL
# ``_ensure_postgres_columns`` mirror all cite the SAME predicate text
# (byte-identical index names across the three sites).
OPEN_EPOCH_STATUS_SQL = "status = 'open'"


def _string_column(nullable: bool = False) -> Column:
    """Indexed-friendly String column (the dependency_bus precedent:
    String indexes more cheaply than Text on PG; SQLite has no
    TEXT-vs-VARCHAR distinction)."""
    return Column(String, nullable=nullable)


# ============================================================
# Models
# ============================================================


class MissionRecord(SQLModel, table=True):
    """One mission EPOCH — the durable written record of "what is this mission".

    Attributes:
        mission_id: PK. A NEW UUID4 per epoch (the deliberate break from
            ``mission_id == instance_id`` — the record is write-side
            truth).
        anchor_instance_id: The instance that owns this epoch (leader /
            root, or the nudged worker for revive-minted epochs).
            Indexed with ``status`` — the resolution ladder's rung-1
            lookup ("does the actor have an OPEN epoch as anchor?").
        epoch_seq: Monotonic per anchor (1, 2, 3…). The field
            ``daemon/services/mission_resolver.py`` cannot compute today
            (its epoch is "always 1" — the map's NEW(iii)). Computed by
            the service at mint: ``1 + count(existing rows for anchor)``.
        founding_message_id: The SOURCE U anchor, made durable. NULL
            only for message-less foundings (boot sweep, backfill) — an
            HONEST null, not a heuristic miss. The mint is idempotent
            keyed ``(anchor_instance_id, founding_message_id)`` via the
            partial unique index (NULLs excluded — boot/backfill mints
            dedupe by resolve-if-none instead).
        founding_source: One of :class:`MissionFoundingSource`.
        commissioner_instance_id: Who commissioned this epoch
            (NULL = user / system). For ``job_continue`` this stays NULL
            in PR1 — the source token is AGENT-grain
            (``agent:{caller_agent_id}``) and an agent id in an
            instance-id column would poison the PR2/PR3 instance-keyed
            joins (the attribution-grain gap comparison.md §3-B flags);
            the gap is recorded in the lane's log line instead.
        work_id: ``JobItem.job_id`` when job-driven (the ``Task.work_id``
            linkage contract).
        parent_mission_id: Continuation chain (job_continue follow-on,
            revive-of-epoch).
        status: One of :class:`MissionStatus` — guarded transitions.
        attested_at / attest_method / attestation_bypassed: The
            attestation block. Records bypasses explicitly (NEW(ii)):
            the Term-1 BLOCKING policy stays default-OFF — PR1 lands the
            record-only fields + the ``record_attestation`` seam.
        created_at / settled_at: TEXT ISO per-epoch timestamps — the
            M4(ii) "known limitation" cure (the DB stores no
            terminal-transition history today).
        provenance: One of :class:`MissionProvenance`.
    """

    __tablename__ = "mission_records"
    __table_args__ = (
        # Rung-1 resolution: "which OPEN epoch does this anchor own?".
        # The status suffix mirrors the dependency_watchers composite
        # trade-off (the vast majority of rows become non-open over
        # time; every resolve/mint gates on the open prefix).
        Index(
            "ix_mission_records_anchor_status",
            "anchor_instance_id",
            "status",
        ),
        # Mint idempotency keyed (anchor, founding_message_id) — the
        # mint-storm guard (proposal-b §8). PARTIAL (NULLs excluded —
        # boot/backfill mints dedupe by resolve-if-none). SQLite
        # supports partial indexes natively (3.8+); the raw-SQL
        # migration mirrors the WHERE clause verbatim.
        Index(
            "ux_mission_records_anchor_founding_msg",
            "anchor_instance_id",
            "founding_message_id",
            unique=True,
            sqlite_where=text(FOUNDING_MSG_NOT_NULL_SQL),
            postgresql_where=text(FOUNDING_MSG_NOT_NULL_SQL),
        ),
        # F1 (Proposal B PR2 carry) — the OPEN-EPOCH partial unique
        # index: at most ONE ``open`` epoch may exist per anchor. The
        # resolution ladder's rung-1 lookup ("does the actor have an
        # OPEN epoch as anchor?") returns a SINGLE row by construction
        # once this index exists — a second concurrent mint of an open
        # epoch for the same anchor is a constraint violation, not a
        # silent "newest wins" ambiguity.
        #
        # PARTIAL on ``status = 'open'``: closed epochs (settled /
        # closed / superseded) accumulate per anchor and MUST NOT be
        # constrained — the index only constrains the LIVE state. The
        # raw-SQL migration and the PostgreSQL ``_ensure_postgres_columns``
        # mirror cite the byte-identical predicate
        # (:data:`OPEN_EPOCH_STATUS_SQL`) and the byte-identical index
        # name ``ux_mission_records_anchor_open``.
        Index(
            "ux_mission_records_anchor_open",
            "anchor_instance_id",
            unique=True,
            sqlite_where=text(OPEN_EPOCH_STATUS_SQL),
            postgresql_where=text(OPEN_EPOCH_STATUS_SQL),
        ),
    )

    mission_id: str = Field(
        default_factory=_new_uuid4,
        primary_key=True,
        max_length=64,
    )
    anchor_instance_id: str = Field(
        sa_column=_string_column(nullable=False),
        max_length=64,
    )
    epoch_seq: int = Field(default=1, nullable=False)

    founding_message_id: str | None = Field(
        default=None,
        sa_column=_string_column(nullable=True),
        max_length=64,
    )
    founding_source: str = Field(
        sa_column=_string_column(nullable=False),
        max_length=32,
    )
    commissioner_instance_id: str | None = Field(
        default=None,
        sa_column=_string_column(nullable=True),
        max_length=64,
    )
    work_id: str | None = Field(
        default=None,
        sa_column=_string_column(nullable=True),
        max_length=64,
    )
    parent_mission_id: str | None = Field(
        default=None,
        sa_column=_string_column(nullable=True),
        max_length=64,
    )

    status: str = Field(default=MissionStatus.OPEN.value, max_length=16)

    # ── Attestation block (record-only in PR1) ──
    attested_at: str | None = Field(default=None)
    attest_method: str | None = Field(
        default=None,
        sa_column=_string_column(nullable=True),
        max_length=32,
    )
    attestation_bypassed: bool = Field(default=False)

    created_at: str = Field(default_factory=_utc_now_iso)
    settled_at: str | None = Field(default=None)

    provenance: str = Field(
        default=MissionProvenance.LIVE.value,
        max_length=16,
    )


class MissionCommission(SQLModel, table=True):
    """One commissioned worker / work item — census substrate + routing key.

    Attributes:
        commission_id: PK. UUID4 for live mints; DETERMINISTIC UUID5 for
            backfill rows (adoption-delta (b)) so the backfill's
            idempotency PRE-CHECK (``get_commission``) keys on the PK
            without constraining live mints. There is NO ``ON CONFLICT``
            clause on this table — a re-run is a no-op because the
            pre-check finds the row, not because the insert swallows a
            conflict.
        mission_id: The mission this work belongs to (indexed with
            ``state``; no enforced FK — the bus precedent: application
            code is the only writer).
        commissioner_instance_id: The nudger / sender (the report
            DESTINATION once the PR3 routing flip lands).
        worker_instance_id: The commissioned instance. Indexed with
            ``state``.
        work_id: Job linkage when present.
        lane: One of :class:`CommissionLane`.
        state: One of :class:`CommissionState` — every transition is a
            guarded ``UPDATE ... WHERE state='outstanding'``.
        created_at / satisfied_at / abandon_reason: TEXT stamps; the
            abandon reason makes the reconciliation sweep's outcome
            operator-visible (the "eternal mission" failure mode,
            proposal-b §8).
        advisory_backfill: True for adoption-delta (b) backfill rows —
            EXCLUDED from routing authority forever (the
            "if dropped, the ledger is worse than no ledger" rule,
            comparison.md §4c).
    """

    __tablename__ = "mission_commissions"
    __table_args__ = (
        # D1 routing key (PR3): "which commission row routes this
        # child's report?" + mission-scoped commission census (D2).
        Index(
            "ix_mission_commissions_mission_state",
            "mission_id",
            "state",
        ),
        # Census / reconciliation: "which outstanding commissions target
        # this worker?" (the ladder's rung-2 lookup + the eternal-mission
        # sweep).
        Index(
            "ix_mission_commissions_worker_state",
            "worker_instance_id",
            "state",
        ),
    )

    commission_id: str = Field(
        default_factory=_new_uuid4,
        primary_key=True,
        max_length=64,
    )
    mission_id: str = Field(
        sa_column=_string_column(nullable=False),
        max_length=64,
    )
    commissioner_instance_id: str = Field(
        sa_column=_string_column(nullable=False),
        max_length=64,
    )
    worker_instance_id: str = Field(
        sa_column=_string_column(nullable=False),
        max_length=64,
    )
    work_id: str | None = Field(
        default=None,
        sa_column=_string_column(nullable=True),
        max_length=64,
    )
    lane: str = Field(
        sa_column=_string_column(nullable=False),
        max_length=32,
    )
    state: str = Field(
        default=CommissionState.OUTSTANDING.value,
        max_length=16,
    )
    created_at: str = Field(default_factory=_utc_now_iso)
    satisfied_at: str | None = Field(default=None)
    abandon_reason: str | None = Field(
        default=None,
        sa_column=Column("abandon_reason", Text, nullable=True),
    )
    advisory_backfill: bool = Field(default=False)


__all__ = [
    "MissionRecord",
    "MissionCommission",
    "MissionFoundingSource",
    "MissionStatus",
    "MissionProvenance",
    "MissionAttestMethod",
    "CommissionLane",
    "CommissionState",
    "FOUNDING_MSG_NOT_NULL_SQL",
    "OPEN_EPOCH_STATUS_SQL",
]
