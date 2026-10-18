"""Agent view-passcodes repository — durable Fernet-encrypted passcode store.

Phase 1 of the live-view-passcode commission (see
``.agents/shared/planning/live-view-passcode/`` for the full plan +
ADR of record). Two tables back the durable passcode substrate + the
append-only audit log:

* :class:`LiveViewPasscode` — one row per ``(root_name, rel_path)``
  pair (the ADR-D2 row-gating posture; lockout of one file never
  takes out a root). The row IS the passcode: the Fernet ciphertext
  lives in ``wrapped_passcode``; the plaintext re-display on every
  ``view_link`` mint is a FORCED deviation (plan-overview §6 D6 /
  architecture-recommendation.md V1 — same ratified class as
  agent-secrets plaintext-at-mint). A hash cannot be re-displayed;
  reversible encryption is the weakest at-rest primitive compatible
  with re-display.
* :class:`LiveViewPasscodeAudit` — one row per audited action
  (``mint`` / ``regenerate`` / ``grant`` / ``deny`` / ``lockout``
  per ADR D9:115). Stamped INSIDE the same ``engine.begin()``
  transaction as the mutation (the secrets M1 atomicity contract;
  the prompt_versions Phase-A precedent). The audit table is the
  durable record of "who/what/when" for the live-view passcode
  substrate; the P2 verify POST + grant-issuance paths consult it
  for forensic visibility.

Encryption coupling
-------------------

The Fernet key is ``SYSTEM_ENCRYPTION_KEY`` — the same env the
existing ``daemon/sources/credentials.py`` +
``daemon/services/kms_lite.py`` paths consume (user Q1 ruling
2026-10-17). Rotation coupling: a future ``SYSTEM_ENCRYPTION_KEY``
rotation MUST re-encrypt ``live_view_passcodes.wrapped_passcode``
AND ``secrets.wrapped_secret`` AND the ``db_connections`` Fernet
credentials AND invalidate ALL outstanding grant cookies (the P2
stateless cookie + ADR D3 revocation-via-generation makes the
cookie invalidation automatic on the next mint), or every one of
those registries breaks. The coupling is documented in code at the
cipher seam (``daemon/services/view_passcode_service.py``). The
keyed-HMAC IP domain-separation key
(``SHA256(SYSTEM_ENCRYPTION_KEY || "live-view-audit-ip-v1")``) is
derived once at service construction; rotation changes the derived
key → historical ``ip_hmac`` rows remain but cross-period IP
correlation breaks by design (raw IPs are NEVER stored; re-
computation is impossible).

Notes
-----

* ``id`` is a UUID4 string (TEXT) for cross-driver portability
  (precedent: ``Secret`` / ``PromptVersion`` / ``CustomAgentConfig``).
  The P1 mint path stamps the id via :func:`uuid.uuid4`; the
  migration's companion file declares ``TEXT PRIMARY KEY``.
* ``wrapped_passcode`` is a ``LargeBinary`` (BLOB on SQLite, BYTEA
  on PG). The companion migration
  ``daemon/migrations/versions/20261017_193816_create_live_view_passcode_tables.sql``
  declares ``BLOB NOT NULL`` for SQLite; the SQLModel declaration
  is the canonical source for PG (``BYTEA``).
* ``locked`` is INTEGER 0/1 on both drivers (the
  ``prompt_versions.is_selected`` precedent at
  ``daemon/migrations/versions/20261013_214000_create_prompt_versions.sql:94``
  — cross-driver, native-enum-free). The SQLModel column is
  declared ``bool``; SQLAlchemy emits BOOLEAN on PG (where
  ``locked <> 0`` would fail with "operator does not exist:
  boolean <> integer" at ``create_all`` time) and INTEGER 0/1
  on SQLite. The partial-index ``WHERE`` predicate is therefore
  the portable ``locked IS TRUE`` (PG-native boolean test + SQLite
  3.24+ boolean test on INTEGER 0/1) — the secrets precedent
  uses the ``IS NULL`` portable shape; the bool column gets
  ``IS TRUE`` for the same reason.
* ``generation`` is an int bumped ONLY by ``refresh()``. The
  generation bump silently revokes ALL outstanding grant cookies
  (no session table — ADR D6 stateless cookie + D3 revocation-via-
  generation). ``generation`` starts at 1 (the migration default).
* ``failed_attempts`` + ``locked`` are the durable 10-strike
  lockout state. Lock persists until ``refresh``; no auto-unlock
  (the spec default — architecture-recommendation.md D5:66).
* Partial index ``idx_live_view_passcodes_locked`` keyed on
  ``WHERE locked IS TRUE`` (PG: native BOOLEAN test) / ``WHERE
  locked <> 0`` (SQLite migration: INTEGER 0/1) — the durable
  lookup path the P2 verify short-circuit (locked → 200 notice)
  consults. Dual-dialect via SQLAlchemy ``sqlite_where`` /
  ``postgresql_where`` (the ``secrets`` / ``service_tool`` /
  ``job_queue`` precedent). The model declares ``IS TRUE`` for
  both — valid on PG BOOLEAN AND on SQLite INTEGER 0/1 (the IS
  operator landed in SQLite 3.24).
* ``action`` is a forward-compatible enum tag (frozenset-validated
  at the service boundary, NOT at the model — the model just stores
  the string). The closed set is
  ``{mint, regenerate, grant, deny, lockout}`` (ADR D9:115); the
  P1 path emits ``mint`` / ``regenerate`` / ``lockout`` only. P2
  adds ``grant`` (cookie issuance) + ``deny`` (verify
  short-circuit on wrong code).
* ``ip_hmac`` is TEXT (16 hex chars on both drivers — HMAC-SHA256
  truncated to 16 hex = 64 bits; ADR D9:116). NULL for actions
  where no IP was captured (the ``mint`` / ``regenerate`` paths
  carry the calling agent, not an IP).
* Index / unique-constraint names stay byte-identical to the
  companion migration
  ``daemon/migrations/versions/20261017_193816_create_live_view_passcode_tables.sql``
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
# LiveViewPasscode — durable Fernet-encrypted passcode store.
# =============================================================================


class LiveViewPasscode(SQLModel, table=True):
    """One row per ``(root_name, rel_path)`` pair (the durable backing row).

    The row is the single source of truth for the passcode's
    cryptographic state. The ``id`` is the UUID4 handle (the
    service stamps it at mint time); the private-side material
    (Fernet ciphertext) NEVER crosses a service-result boundary
    un-decrypted — the service decrypts in-memory for verify
    and re-wraps the plaintext on every mint / refresh (D6
    deviation: plaintext re-display on every ``view_link`` mint).

    Attributes:
        id: UUID4 primary key (TEXT — cross-driver portability,
            ``Secret`` / ``PromptVersion`` precedent). Stamped
            at the service boundary via :func:`uuid.uuid4`.
        root_name: Configured root name (lowercase; the
            ``_ROOT_NAME_REGEX`` enforced shape at
            ``daemon/services/live_views.py:126``). One passcode
            per ``(root_name, rel_path)`` pair — the ADR-D2
            row-gating posture.
        rel_path: Path relative to ``root_name``'s resolved
            data directory. The UNIQUE pair
            ``(root_name, rel_path)`` is the first-store-wins
            fence the P1 mint path follows.
        wrapped_passcode: Fernet-encrypted passcode plaintext.
            Stored as ``LargeBinary`` (BLOB on SQLite, BYTEA on
            PG). NEVER returned by any read path. The only
            consumer that decrypts is the in-process
            :class:`ViewPasscodeService` (the verify path; the
            service decrypts, compares in constant time, and
            drops the in-memory plaintext).
        generation: Monotonic int bumped ONLY by
            :meth:`ViewPasscodeService.refresh`. The generation
            bump silently revokes ALL outstanding grant cookies
            (no session table — ADR D6 stateless cookie + D3
            revocation-via-generation). Starts at 1 (the
            migration default).
        failed_attempts: Durable counter of consecutive wrong
            verify attempts. The 10th failure flips ``locked``
            in the SAME ``engine.begin()`` transaction (ADR D5
            atomic contract).
        locked: Durable lockout flag. ``True`` after the 10th
            consecutive wrong verify. Persists until
            :meth:`ViewPasscodeService.refresh`. No time-boxed
            auto-unlock (the spec default — architecture-
            recommendation.md D5:66).
        created_at: ISO-8601 stamp of the row birth.
        last_attempt_at: ISO-8601 stamp of the most recent
            verify attempt (success or failure). NULL until the
            first verify. Forensic / lockout-storm-context aid.
    """

    __tablename__ = "live_view_passcodes"
    __table_args__ = (
        # UNIQUE(root_name, rel_path) is the AUTHORITATIVE
        # constraint — the named UniqueConstraint below carries
        # the same name the SQLite migration declares, so the
        # model's ``create_all`` (PG path) and the migration's
        # inline ``UNIQUE`` (SQLite path) agree on
        # ``uq_live_view_passcodes_root_rel`` (parity-record
        # contract). Defining BOTH this named
        # UniqueConstraint AND ``Field(unique=True)`` on a
        # column would create TWO UNIQUE constraints on PG
        # ``create_all`` (an auto-named inline UNIQUE + this
        # named one) — the F15 precedent at
        # ``daemon/repositories/project/models.py:334-340``
        # removed the same dual emission. Keep this comment
        # in lockstep with the model.
        UniqueConstraint(
            "root_name", "rel_path",
            name="uq_live_view_passcodes_root_rel",
        ),
        # Partial index on ``locked`` — covers the
        # ``locked views for a (root, rel)`` lookup path the
        # P2 verify short-circuit (locked → 200 notice)
        # consults. The partial ``WHERE`` clause is a direct
        # copy of the migration's
        # ``idx_live_view_passcodes_locked`` predicate. Dual-
        # dialect via SQLAlchemy ``sqlite_where`` /
        # ``postgresql_where`` (the secrets / service_tool /
        # job_queue precedent — ``daemon/repositories/
        # secrets/models.py:172-177``). The predicate MUST be
        # portable: ``locked IS TRUE`` works on PG BOOLEAN
        # (the canonical boolean test) AND on SQLite INTEGER
        # 0/1 (the IS operator was added in SQLite 3.24).
        # ``locked <> 0`` is PG-ILLEGAL because PG has no
        # boolean<>integer operator — ``create_all`` on the
        # PG path raises immediately at table-create time.
        Index(
            "idx_live_view_passcodes_locked",
            "root_name",
            "rel_path",
            sqlite_where=text("locked IS TRUE"),
            postgresql_where=text("locked IS TRUE"),
        ),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
        description=(
            "UUID4 primary key (TEXT — cross-driver portability, "
            "Secret / PromptVersion / CustomAgentConfig "
            "precedent). Stamped at the service boundary via "
            "uuid.uuid4."
        ),
    )
    root_name: str = Field(
        max_length=128,
        description=(
            "Configured root name (lowercase; the "
            "_ROOT_NAME_REGEX enforced shape at "
            "daemon/services/live_views.py:126). One passcode "
            "per (root_name, rel_path) pair."
        ),
    )
    rel_path: str = Field(
        max_length=1024,
        description=(
            "Path relative to root_name's resolved data "
            "directory. The UNIQUE pair (root_name, rel_path) "
            "is the first-store-wins fence the P1 mint path "
            "follows."
        ),
    )
    wrapped_passcode: bytes = Field(
        sa_column=Column(LargeBinary, nullable=False),
        description=(
            "Fernet-encrypted passcode plaintext (BLOB on "
            "SQLite, BYTEA on PG). NEVER returned by any read "
            "path. The only consumer that decrypts is the "
            "in-process ViewPasscodeService (the verify path; "
            "the service decrypts, compares in constant time, "
            "and drops the in-memory plaintext)."
        ),
    )
    generation: int = Field(
        default=1,
        description=(
            "Monotonic int bumped ONLY by "
            "ViewPasscodeService.refresh. The generation bump "
            "silently revokes ALL outstanding grant cookies "
            "(no session table — ADR D6 stateless cookie + "
            "D3 revocation-via-generation). Starts at 1."
        ),
    )
    failed_attempts: int = Field(
        default=0,
        description=(
            "Durable counter of consecutive wrong verify "
            "attempts. The 10th failure flips locked in the "
            "SAME engine.begin() transaction (ADR D5 atomic "
            "contract)."
        ),
    )
    locked: bool = Field(
        default=False,
        description=(
            "Durable lockout flag. True after the 10th "
            "consecutive wrong verify. Persists until "
            "ViewPasscodeService.refresh. No time-boxed "
            "auto-unlock."
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
    last_attempt_at: Optional[str] = Field(
        default=None,
        description=(
            "ISO-8601 stamp of the most recent verify attempt "
            "(success or failure). NULL until the first "
            "verify. Forensic / lockout-storm-context aid."
        ),
    )


# =============================================================================
# LiveViewPasscodeAudit — append-only audit log for the closed action set.
# =============================================================================


class LiveViewPasscodeAudit(SQLModel, table=True):
    """One row per audited action on a passcode row (append-only).

    Plan §3 + architecture-recommendation.md D9:115 — the table is
    kept minimal: ``at`` + ``action`` + ``(root_name, rel_path,
    generation)`` + optional ``ip_hmac`` + optional ``actor``. The
    closed action set is ``{mint, regenerate, grant, deny,
    lockout}`` (TEXT column, NOT a DB enum — additive emitters
    never require a migration). P1 emits ``mint`` /
    ``regenerate`` / ``lockout`` only; P2 adds ``grant`` (cookie
    issuance) + ``deny`` (verify short-circuit on wrong code).

    Per the prompt_versions Phase-A precedent (audit-event stamping
    contract, A3), the row is stamped INSIDE the same
    ``engine.begin()`` transaction as the mutation it audits —
    never outside the repository call boundary.

    Attributes:
        id: BIGSERIAL PK on PG (INTEGER PRIMARY KEY
            AUTOINCREMENT on SQLite — SQLAlchemy translates
            ``autoincrement=True`` to the right dialect
            incantation).
        at: ISO-8601 stamp of the audit event.
        action: Closed-set enum tag —
            ``'mint' | 'regenerate' | 'grant' | 'deny' |
            'lockout'``. The set is frozenset-validated at
            the service boundary (ViewPasscodeService), NOT
            at the model (the model is a dumb store). ``grant``
            + ``deny`` are P2 emitters; the closed set is
            documented for forward-compatibility.
        root_name: View root name (denormalized for forensic
            query paths; the audit row outlives the passcode
            row when ``refresh`` regenerates).
        rel_path: View path relative to ``root_name``
            (denormalized for the same forensic reason).
        generation: The passcode row's generation AT audit-
            stamp time. Useful for tying grant/deny audit
            rows to the generation the cookie was issued
            under.
        ip_hmac: Keyed-HMAC SHA-256 of the request IP,
            truncated to 16 hex chars (64 bits; ADR D9:116).
            Key derivation:
            ``SHA256(SYSTEM_ENCRYPTION_KEY ||
            "live-view-audit-ip-v1")`` — domain-separated
            from the Fernet key. Raw IPs NEVER stored.
            Rotation unlinks historical rows by design.
        actor: Agent id (Cardinal #2 source discipline — the
            calling agent, not the HTTP subject). NULL for
            public actions (the verify POST on the
            unauthenticated lane has no agent provenance).
    """

    __tablename__ = "live_view_passcode_audit"
    __table_args__ = (
        # (root_name, at) composite index — covers the
        # ``all audit rows for a given view, time-ordered``
        # query path the future operator UI will hit. P1
        # only writes rows; the index is forward-looking.
        Index(
            "idx_live_view_passcode_audit_root_at",
            "root_name",
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
    at: str = Field(
        default_factory=_now_iso,
        description="ISO-8601 stamp of the audit event.",
    )
    action: str = Field(
        max_length=32,
        description=(
            "Closed-set enum tag — 'mint' | 'regenerate' | "
            "'grant' | 'deny' | 'lockout'. The set is "
            "frozenset-validated at the service boundary. "
            "Forward-compatible; P1 emits mint / regenerate / "
            "lockout; P2 adds grant + deny."
        ),
    )
    root_name: str = Field(
        max_length=128,
        description=(
            "View root name (denormalized for forensic "
            "queries)."
        ),
    )
    rel_path: str = Field(
        max_length=1024,
        description=(
            "View path relative to root_name (denormalized "
            "for forensic queries)."
        ),
    )
    generation: int = Field(
        description=(
            "Passcode generation at audit-stamp time. "
            "Useful for tying grant/deny audit rows to the "
            "generation the cookie was issued under."
        ),
    )
    ip_hmac: Optional[str] = Field(
        default=None,
        max_length=16,
        description=(
            "Keyed-HMAC SHA-256 of the request IP, truncated "
            "to 16 hex chars (64 bits; ADR D9:116). NULL for "
            "actions with no IP capture (mint / regenerate). "
            "Raw IPs NEVER stored; rotation unlinks historical "
            "rows by design."
        ),
    )
    actor: Optional[str] = Field(
        default=None,
        max_length=256,
        description=(
            "Agent id (Cardinal #2 source discipline). NULL "
            "for public actions on the unauthenticated verify "
            "lane."
        ),
    )


# =============================================================================
# Domain constants — passcode alphabet, length, strike ceiling, closed
# audit action set. The values are locked by ADR D4/D5/D9 and must
# stay in lockstep with the migration comment + the service layer.
# =============================================================================


#: Passcode alphabet (ADR D4 — 31-symbol unambiguous set, drops
#: 0/O/1/i/l). Lowercase; the ``normalize_entry`` primitive case-
#: folds customer input so the customer can type upper or lower.
#: 31^6 ≈ 8.9e8 ≈ 29.7 bits of entropy.
LIVE_VIEW_PASSCODE_ALPHABET: str = "23456789abcdefghjkmnpqrstuvwxyz"

#: Passcode length in characters (ADR D4). Fixed at 6 for UX.
LIVE_VIEW_PASSCODE_LENGTH: int = 6

#: Strike ceiling — the Nth wrong verify flips ``locked`` (ADR D5).
#: The 10th failure flips ``locked=true`` AND emits the ``lockout``
#: audit row IN THE SAME ``engine.begin()`` transaction (the M1
#: atomicity contract, governor pre-promote fix 2026-10-17).
LIVE_VIEW_PASSCODE_STRIKE_CEILING: int = 10

#: Closed set of audit action values (ADR D9:115). The service
#: boundary validates incoming actions against this set; the model
#: just stores the string. P1 emits ``mint`` / ``regenerate`` /
#: ``lockout`` only; ``grant`` + ``deny`` are P2 emitters
#: (forward-compatible).
LIVE_VIEW_AUDIT_ACTIONS: frozenset[str] = frozenset({
    "mint",
    "regenerate",
    "grant",
    "deny",
    "lockout",
})

#: IP-HMAC key-derivation info string (ADR D9:116). The actual
#: key is ``SHA256(SYSTEM_ENCRYPTION_KEY || LIVE_VIEW_AUDIT_IP_KDF_INFO)``
#: — domain-separated from the Fernet key. Rotating
#: ``SYSTEM_ENCRYPTION_KEY`` changes the derived key, breaking
#: cross-period IP correlation by design (raw IPs are NEVER
#: stored).
LIVE_VIEW_AUDIT_IP_KDF_INFO: str = "live-view-audit-ip-v1"