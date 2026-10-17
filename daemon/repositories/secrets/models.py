"""SQLModel table definitions for the agent-secrets P1 store.

Phase 1 of the agent-secrets commission (see
``.agents/shared/planning/agent-secrets/`` for the full plan + decisions).
Two tables back the durable Fernet-encrypted secret registry + the
append-only audit log:

* :class:`Secret` — one row per stored secret. The row IS the secret:
  the Fernet ciphertext lives in ``wrapped_secret``; the public-side
  (SSH public key) lives in ``public_material``; the UUID4 ``id`` is
  the immutable handle embedded inside the
  ``__ENS_SECRET__<id>__`` marker text that P2 will resolve. Names
  are unique within the system so a retried ``secrets.store`` call
  by the same agent hits the existing row (or errors out at the
  service boundary).
* :class:`SecretAuditEvent` — one row per audited action
  (``create`` / ``read_public`` / ``retire`` in P1; ``use_marker`` /
  ``read_value`` reserved-for-P2 in the enum). Stamped INSIDE the
  same ``engine.begin()`` transaction as the mutation (the
  prompt_versions Phase-A precedent; audit-event stamping contract,
  A3). The audit table is the single source of truth for "who
  read what and when" in the long-mission critical path; the
  trust ruling (§1.2) has no human-in-loop, so the audit row
  IS the only signal a secret was actually consumed.

Encryption coupling
-------------------

The Fernet key is ``SYSTEM_ENCRYPTION_KEY`` — the same env the
existing ``daemon/sources/credentials.py`` +
``daemon/services/kms_lite.py`` paths consume (user Q1 ruling,
2026-10-17). Rotation coupling: a future ``SYSTEM_ENCRYPTION_KEY``
rotation MUST re-encrypt ``secrets.wrapped_secret`` AND the
``db_connections`` Fernet credentials (linearly extends the existing
rotation coupling — plan §10). The coupling is documented in code at
the cipher seam (``daemon/services/secret_service.py:SecretCipher``).

Notes
-----

* ``id`` is a UUID4 string (TEXT) for cross-driver portability
  (precedent: ``StoredWatch``, ``PromptVersion``, ``CustomAgentConfig``,
  ``SecretStore`` pattern in skills/seed). The marker regex in
  ``daemon/services/secret_service.py`` enforces the UUID4 shape at
  mint time, so the column-level integrity invariant is dual-
  enforced (model + marker regex).
* ``wrapped_secret`` is a ``LargeBinary`` (BLOB on SQLite, BYTEA on
  PG). The companion migration
  ``daemon/migrations/versions/20261017_000001_create_secrets_tables.sql``
  declares ``BLOB NOT NULL`` for SQLite; the SQLModel declaration is
  the canonical source for PG (``BYTEA``).
* ``public_material`` is nullable (token / generic kinds have no
  public-side; only ``ssh_keypair`` populates it).
* ``retired_at`` is the soft-retire tombstone (ISO-8601 TEXT —
  same convention as ``CustomAgentConfig.deleted_at``). A retired
  row stays queryable; the partial index on ``(kind) WHERE
  retired_at IS NULL`` lets the active-registry queries hit only
  live rows.
* ``action`` is a forward-compatible enum tag (frozenset-validated
  at the service boundary, NOT at the model — the model just
  stores the string). ``use_marker`` and ``read_value`` are in the
  enum today with NO P1 emitter (P2-only; plan §4.2
  reserved-for-future note).
* Booleans are not used in this table; timestamps are ISO-8601 TEXT
  (the cross-driver convention used by the other additive tables
  in this lineage).
* Index / unique-constraint names stay byte-identical to the
  companion migration
  ``daemon/migrations/versions/20261017_000001_create_secrets_tables.sql``
  (the canonical source for the SQL DDL on SQLite; the model is
  the canonical source for the PG ``create_all`` path).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, Index, LargeBinary, UniqueConstraint, text
from sqlmodel import Field, SQLModel


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string.

    Shared helper — every other additive migration module defines
    its own (the pattern). Pulled out here so the two tables in
    this module stamp the same shape.
    """
    return datetime.now(timezone.utc).isoformat()


# =============================================================================
# Secret — durable Fernet-encrypted secret store.
# =============================================================================


class Secret(SQLModel, table=True):
    """One row per stored secret (the durable backing row).

    The row is the single source of truth for the secret's
    cryptographic state. The ``id`` is the UUID4 handle embedded
    in the ``__ENS_SECRET__<id>__`` marker text that P2 will mint
    on tool calls (Q3 — marker format FROZEN 2026-10-17). The
    private-side material (Fernet ciphertext) NEVER crosses a
    tool-result boundary: only ``id`` + ``name`` + ``kind`` +
    ``public_material`` are ever returned by any read path
    (plan §5.2 pubkey-only rule).

    Attributes:
        id: UUID4 primary key (TEXT — cross-driver portability,
            precedent: ``StoredWatch`` / ``PromptVersion``).
            Stamped at the service boundary via
            :func:`uuid.uuid4`; the marker regex enforces
            UUID4-shape on the encrypted-and-minted side, so
            the column-level invariant is dual-enforced.
        name: Human-readable label. UNIQUE within the system —
            a re-store on the same name errors at the service
            boundary (the trust ruling has no human-in-loop, so
            silent rename is the wrong default).
        kind: Forward-compatible enum tag
            (``'ssh_keypair' | 'api_token' | 'generic'``). New
            kinds can be added later without a migration; the
            service boundary is the validator.
        public_material: Public-side material when applicable
            (SSH public key for ``ssh_keypair``); null for
            ``api_token`` / ``generic``. Agents get this via
            ``secrets.read_public`` — the only read tool in v1
            that returns material beyond id/name/kind.
        wrapped_secret: Fernet-encrypted private material.
            Stored as ``LargeBinary`` (BLOB on SQLite, BYTEA on
            PG). NEVER returned by any read path. The only
            consumer that decrypts is the in-process
            ``SecretService`` (P2 marker-resolver seam).
        created_by: Agent id (Cardinal #2 source discipline).
        created_at: ISO-8601 stamp of the row birth.
        retired_at: ISO-8601 stamp of the soft-retire event
            (null until retire). Retired rows stay queryable
            for audit; the partial index on
            ``(kind) WHERE retired_at IS NULL`` lets the
            active-registry queries skip them.
    """

    __tablename__ = "secrets"
    __table_args__ = (
        # UNIQUE(name) is declared at the column level (Field
        # constraint) — the constraint name is auto-derived to
        # ``name`` on SQLite, which is the name the migration
        # carries inline. We re-declare it here with an explicit
        # name for parity with the model's __table_args__ pattern
        # (and to ensure PG ``create_all`` produces the same
        # constraint name across the two drivers).
        UniqueConstraint("name", name="uq_secrets_name"),
        # Active-registry partial index — covers the
        # ``list_active(kind=X)`` query path the P1
        # ``secrets.list`` tool follows (only live rows, kind
        # tag matching). The partial ``WHERE`` clause is a
        # direct copy of the migration's
        # ``idx_secrets_kind_active`` predicate. Dual-dialect
        # via SQLAlchemy ``sqlite_where`` / ``postgresql_where``
        # (the service_tool + job_queue precedent —
        # ``daemon/repositories/service_tool/models.py:148-157``,
        # ``daemon/repositories/job_queue/models.py:303-305``).
        Index(
            "idx_secrets_kind_active",
            "kind",
            sqlite_where=text("retired_at IS NULL"),
            postgresql_where=text("retired_at IS NULL"),
        ),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
        description=(
            "UUID4 primary key (TEXT — cross-driver portability, "
            "StoredWatch / PromptVersion / CustomAgentConfig "
            "precedent). The marker text embeds this id."
        ),
    )
    name: str = Field(
        max_length=256,
        unique=True,
        description=(
            "Human-readable label; UNIQUE within the system. "
            "Re-storing on the same name is an error at the "
            "service boundary (the trust ruling has no "
            "human-in-loop, so silent rename is the wrong "
            "default)."
        ),
    )
    kind: str = Field(
        max_length=64,
        description=(
            "Forward-compatible enum tag — ``'ssh_keypair' | "
            "'api_token' | 'generic'``. New kinds can be added "
            "later without a migration; the service boundary is "
            "the validator."
        ),
    )
    public_material: Optional[str] = Field(
        default=None,
        description=(
            "Public-side material when applicable (SSH public "
            "key for ``ssh_keypair``); null for token / generic "
            "kinds. Only ever returned via ``secrets.read_public``."
        ),
    )
    wrapped_secret: bytes = Field(
        sa_column=Column(LargeBinary, nullable=False),
        description=(
            "Fernet-encrypted private material (BLOB on SQLite, "
            "BYTEA on PG). NEVER returned by any read path. The "
            "only consumer that decrypts is the in-process "
            "SecretService (P2 marker-resolver seam)."
        ),
    )
    created_by: str = Field(
        max_length=256,
        description=(
            "Agent id (Cardinal #2 source discipline — the "
            "calling agent, not the HTTP subject)."
        ),
    )
    created_at: str = Field(
        default_factory=_now_iso,
        description=(
            "ISO-8601 stamp of the row birth. Uses the "
            "_now_iso() helper so SQLite and PG produce the "
            "same shape (text)."
        ),
    )
    retired_at: Optional[str] = Field(
        default=None,
        description=(
            "ISO-8601 stamp of the soft-retire event (null "
            "until retire). Retired rows stay queryable; the "
            "partial index on (kind) WHERE retired_at IS NULL "
            "lets the active-registry queries skip them."
        ),
    )


# =============================================================================
# SecretAuditEvent — append-only audit log for the P1 actions.
# =============================================================================


class SecretAuditEvent(SQLModel, table=True):
    """One row per audited action on a secret (append-only).

    Plan §4.2 — the table is kept minimal: action + actor + (optional
    secret_id) + at. The ``action`` enum is forward-compatible; P1
    emits ``create`` / ``read_public`` / ``retire`` only. ``use_marker``
    and ``read_value`` are in the enum today with NO P1 emitter
    (P2-only — plan §4.2 reserved-for-future).

    Per the prompt_versions Phase-A precedent (audit-event stamping
    contract, A3), the row is stamped INSIDE the same
    ``engine.begin()`` transaction as the mutation — never outside
    the repository call boundary.

    Attributes:
        id: BIGSERIAL PK on PG (INTEGER PRIMARY KEY
            AUTOINCREMENT on SQLite — SQLAlchemy translates
            ``autoincrement=True`` to the right dialect
            incantation).
        secret_id: Foreign key to ``secrets.id`` (informational
            only — no CASCADE on the audit side; we never
            DELETE a secret, only retire).
        action: Enum tag — ``'create' | 'read_public' |
            'use_marker' | 'read_value' | 'retire'``. The set
            is frozenset-validated at the service boundary
            (SecretService), NOT at the model (the model just
            stores the string).
        actor: Agent id (Cardinal #2).
        at: ISO-8601 stamp.
    """

    __tablename__ = "secrets_audit"
    __table_args__ = (
        # (secret_id, at) composite index — covers the
        # ``all audit rows for a given secret, time-ordered``
        # query path the P2 audit UI will hit. P1 only writes
        # rows, so the index is forward-looking — but the
        # P1 audit-trail reader (secrets_audit inspect, not
        # a v1 tool) also benefits.
        Index(
            "idx_secrets_audit_secret_id_at",
            "secret_id",
            "at",
        ),
    )

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
        description=(
            "BIGSERIAL PK on PG (INTEGER PRIMARY KEY "
            "AUTOINCREMENT on SQLite). The dialect incantation "
            "is handled by SQLAlchemy."
        ),
    )
    secret_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "Foreign key to ``secrets.id`` (TEXT — cross-driver "
            "portability). Nullable so a future create-time "
            "audit row could link to the just-inserted row, "
            "though the P1 path always writes AFTER the row "
            "commits (so secret_id is always set in P1)."
        ),
    )
    action: str = Field(
        max_length=32,
        description=(
            "Enum tag — ``'create' | 'read_public' | "
            "'use_marker' | 'read_value' | 'retire'``. "
            "Forward-compatible; P1 emits the first, second, "
            "and fifth only."
        ),
    )
    actor: str = Field(
        max_length=256,
        description=(
            "Agent id (Cardinal #2 source discipline — the "
            "calling agent, not the HTTP subject)."
        ),
    )
    at: str = Field(
        default_factory=_now_iso,
        description="ISO-8601 stamp of the audit event.",
    )


# =============================================================================
# Action-enum constants (frozen set) — used by the service layer for
# validation. Plan §4.2 is the canonical source of truth for the
# action values; keep this in lockstep with the migration comment.
# =============================================================================


#: Frozen set of valid ``action`` values. The service boundary
#: validates incoming actions against this set (the model is a
#: dumb store). Plan §4.2 names the set; the implementation is
#: the constant.
SECRET_AUDIT_ACTIONS: frozenset[str] = frozenset({
    "create",
    "read_public",
    # Reserved-for-P2 (plan §4.2): no v1 emitter exists.
    "use_marker",
    # Reserved-for-P2 (plan §4.2): no v1 value-read tool exists
    # (v1 read = public material only). The enum entry stays so
    # the P2 marker-resolver audit row (and any future
    # value-read tool) lands in the same table.
    "read_value",
    "retire",
})

#: P1-emitter subset. The service boundary filters incoming
#: audit actions through this set; the rest are reserved.
SECRET_AUDIT_ACTIONS_P1: frozenset[str] = frozenset({
    "create",
    "read_public",
    "retire",
})
