"""The ``notify_audit`` SQLModel table — the ``notify_user`` audit ledger.

One row per ``notify_user`` attempt (ari-voice commission, 2026-10-25).
The table plays TWO roles at once (decisions.md D8 + C3):

* **The audit trail** — who notified which resolved destination, when,
  with what outcome. The body is stored as SHA-256 + length, NEVER
  cleartext: rate counting + tamper-evidence without a second copy of
  message content (the mcp_gateway_audit ``args_hash`` reasoning,
  applied to a message body).
* **The rate-limit budget source** — the rolling-hour M cap and the
  per-instance ≥60s interval are COUNTED FROM THIS TABLE (insert-then-
count as corrected by C3: the accepted INSERT commits in its own
transaction, then the COUNT runs in a fresh statement). Because the
ledger is the budget, a ledger-write failure on the send path is
FAIL-CLOSED (N2): no row, no send, typed refusal
``notify_audit_unavailable``.

LIFECYCLE (Tier-2 write-ahead, instance_ops precedent)
------------------------------------------------------
``accepted`` (the pre-dispatch INSERT — also the budget row) →
``dispatched`` / ``delivery_failed`` / ``rate_limited`` (UPDATE of the
own row) / ``delivery_unknown`` (timeout path + the N4 crash-orphan
sweep). Refusals ARE ledgered (the refusal is the security signal —
a deliberate divergence from instance_ops, whose input-validation
refusals write no row): ``outcome="refused"`` + ``refusal_reason``.
Refusal rows NEVER consume budget (N3) — only rows that currently stand
as ``accepted`` do (crash-orphaned accepted rows count conservatively
until the N4 sweep marks them ``delivery_unknown`` — still consuming;
under-send biased, never over-send).

NULLABILITY (W3, pinned choice)
-------------------------------
``source_id`` and ``target_hash`` are NULLABLE because PRE-resolution
refusals (``notify_size_exceeded``, ``notify_no_bound_chat_source``) have
no resolved source to name. W3 offered "nullable OR post-resolution-only
ledgering"; the nullable variant is pinned because fold-2 N3 (RULED,
supersedes) requires EVERY refusal class to be ledgered as the security
signal — and N3's list includes the pre-resolution classes. The budget
COUNT excludes NULL ``target_hash`` rows explicitly, so nullable columns
cannot poison the cap.

TIMESTAMPS
----------
``created_at`` is an ISO-8601 TEXT stamp — the lineage of the
``secrets_audit`` / ``instance_ops_audit`` / ``mcp_gateway_audit``
tables, which deliberately carry ISO-8601 TEXT rather than a naive
timestamp column. Nothing here binds a naive timestamp, so the PG session
timezone cannot shift a digit. String comparison is the window predicate
(ISO-8601 UTC sorts lexicographically) — same as the instance_ops budget
count.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Index, Integer, Text
from sqlmodel import Field, SQLModel


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


#: The pre-dispatch INSERT outcome — the LEADING member of the
#: budget-consuming accepted CLASS (N3), NOT the only one. A row stands
#: as ``accepted`` between the write-ahead INSERT and its terminal
#: UPDATE; a crash orphan keeps standing as ``accepted`` until the N4
#: sweep re-marks it. The authority is ``ACCEPTED_CLASS_OUTCOMES``
#: (``accepted`` / ``dispatched`` / ``delivery_failed`` /
#: ``delivery_unknown`` consume; only refusal outcomes never do) — the
#: tuple the repository's budget predicate counts.
OUTCOME_ACCEPTED = "accepted"

#: Terminal outcomes.
OUTCOME_DISPATCHED = "dispatched"
OUTCOME_DELIVERY_FAILED = "delivery_failed"
OUTCOME_RATE_LIMITED = "rate_limited"
OUTCOME_DELIVERY_UNKNOWN = "delivery_unknown"
OUTCOME_REFUSED = "refused"

#: Rows whose CURRENT outcome is one of these consumed a budget slot
#: (the per-instance ≥60s interval baseline — the pinned "last-accepted
#: class" reading of fold-able 4: a refused attempt must not extend the
#: cooldown, a dispatched/failed/unknown/accepted one must).
ACCEPTED_CLASS_OUTCOMES: tuple[str, ...] = (
    OUTCOME_ACCEPTED,
    OUTCOME_DISPATCHED,
    OUTCOME_DELIVERY_FAILED,
    OUTCOME_DELIVERY_UNKNOWN,
)


class NotifyAudit(SQLModel, table=True):
    """One audited ``notify_user`` attempt (write-ahead; refusals too).

    Attributes:
        id: Primary key (UUID4).
        created_at: ISO-8601 timestamp (UTC) — the window predicate for
            both rate gates.
        agent_id: WHO asked — always ``ari`` on an admitted call (the
            tool's layer-3 gate refuses everyone else before any ledger
            access).
        instance_id: The calling instance (the ≥60s interval key).
        action: The tool name — constant ``notify_user``.
        source_id: The RESOLVED registered chat source. NULLABLE on
            purpose (W3): pre-resolution refusals have no source to
            name.
        target_hash: ``sha256(source_id + ":" + external_user_id)``
            (W1 — source-qualified so two sources cannot pool a cap).
            The raw destination is NEVER stored. NULL on pre-resolution
            refusals; the budget COUNT excludes NULLs explicitly.
        outcome: ``accepted`` (write-ahead) → ``dispatched`` /
            ``delivery_failed`` / ``rate_limited`` / ``delivery_unknown``;
            or ``refused``.
        refusal_reason: The typed code when ``outcome="refused"``
            (``notify_size_exceeded``, ``notify_no_bound_chat_source``,
            ...). NULL otherwise.
        error_code: The typed code for non-refusal failures
            (``notify_dispatch_failed`` on ``delivery_failed``).
        body_length: The message body's character length.
        body_sha256: SHA-256 of the message body (lower-case hex) —
            NEVER the body itself.
        detail: Advisory human text (e.g. when the next send is legal
            after a rate-limit refusal). Never the message content.
    """

    __tablename__ = "notify_audit"
    __table_args__ = (
        Index(
            "idx_notify_audit_agent_created",
            "agent_id",
            "created_at",
        ),
        Index(
            "idx_notify_audit_instance_created",
            "instance_id",
            "created_at",
        ),
        Index(
            "idx_notify_audit_target_created",
            "target_hash",
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
    action: str = Field(
        default="notify_user",
        sa_column=Column("action", Text, nullable=False, default="notify_user"),
    )
    source_id: str | None = Field(
        default=None,
        sa_column=Column("source_id", Text, nullable=True),
    )
    target_hash: str | None = Field(
        default=None,
        sa_column=Column("target_hash", Text, nullable=True),
    )
    outcome: str = Field(
        default=OUTCOME_ACCEPTED,
        sa_column=Column(
            "outcome", Text, nullable=False, default=OUTCOME_ACCEPTED
        ),
    )
    refusal_reason: str | None = Field(
        default=None,
        sa_column=Column("refusal_reason", Text, nullable=True),
    )
    error_code: str | None = Field(
        default=None,
        sa_column=Column("error_code", Text, nullable=True),
    )
    body_length: int = Field(
        default=0,
        sa_column=Column("body_length", Integer, nullable=False),
    )
    body_sha256: str = Field(
        sa_column=Column("body_sha256", Text, nullable=False)
    )
    detail: str | None = Field(
        default=None,
        sa_column=Column("detail", Text, nullable=True),
    )
