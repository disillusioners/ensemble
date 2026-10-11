"""Auth-session SQLModel models — oauth2-auth Phase 2 (T2.2).

Four tables ([TA] §Session Model schema; ``auth_bootstrap_consumption``
per phase4-plan.md T4.1 AMEND v1.3 / F10, AUTHORIZED re-scope into P2):

===================  =====================================================
Table                Model
===================  =====================================================
``auth_sessions``    :class:`AuthSession` — one row per browser session
``auth_states``      :class:`AuthState` — one row per OAuth2 ``state``
``auth_bearer_tokens`` :class:`BearerToken` — static bearer tokens
``auth_bootstrap_consumption`` :class:`AuthBootstrapConsumption` —
one-shot bootstrap mint sentinel rows (inert in P2; repository + CLI
arrive in P4)
===================  =====================================================

Schema conventions
------------------
* **TEXT ISO-8601 UTC timestamps** (``now_utc_iso()`` format) — INV-7
  house convention (zero ``sa.DateTime`` columns in the repo; the
  naive/tz trap is sidestepped entirely). Lexicographic ordering is
  chronological for the fixed format, so the sweeper's
  ``expires_at < :cutoff`` comparison works identically on PG and
  SQLite. The plan's ``TIMESTAMP`` spelling is realized as the repo
  convention's portable TEXT-ISO equivalent (per-table canonical DDL:
  ``daemon/migrations/versions/20261011_0300*.sql``).
* **Hash-only secrets** (NFR-13): ``BearerToken.id_hash`` and
  ``AuthBootstrapConsumption.id_hash`` carry ``SHA-256(material)`` hex —
  the plaintext never touches this module.
* **Model registry**: imported by ``daemon/manager.py`` BEFORE
  ``SQLModel.metadata.create_all`` (snapshot precedent) so all four
  tables materialize on BOTH drivers at manager init. The SQLite-side
  canonical DDL additionally auto-applies via the migration runner
  (D17: no ``MANUAL: TRUE`` markers — auth tables are created on BOTH
  backends, inert when auth is unused).
* **Index parity contract**: the two secondary index names
  (``idx_auth_sessions_expires_at``, ``idx_auth_bearer_tokens_expires_at``)
  MUST stay byte-identical across this file's ``__table_args__`` (the
  canonical source for PG ``create_all``) and the ``CREATE INDEX``
  statements in the ``.sql`` files (the SQLite path).
  ``tests/migration/test_auth_tables_g2_2.py`` pins the triple.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Column, Index, Integer, Text
from sqlmodel import Field, SQLModel


class AuthSession(SQLModel, table=True):
    """One authenticated browser session (cookie ``ensemble_sid`` value).

    Lifecycle: inserted at callback (phase 3), ``last_used_at`` touched
    at most once per minute (batched touch), invalidated = row DELETE
    ([TA] §Logout — single-CAS ``DELETE FROM auth_sessions WHERE id=?``;
    the schema deliberately carries NO ``revoked_at`` column — soft
    revocation is the bearer table's mechanism), swept when
    ``expires_at`` passes.
    """

    __tablename__ = "auth_sessions"

    __table_args__ = (
        Index(
            "idx_auth_sessions_expires_at",
            "expires_at",
        ),
    )

    id: str = Field(
        sa_column=Column("id", Text, primary_key=True),
        description="URL-safe random 32 bytes — the ensemble_sid cookie value",
    )
    subject: str = Field(
        sa_column=Column("subject", Text, nullable=False),
        description="OIDC 'sub' claim",
    )
    email: Optional[str] = Field(
        default=None,
        sa_column=Column("email", Text, nullable=True, default=None),
        description="OIDC 'email' claim (optional)",
    )
    scopes: str = Field(
        sa_column=Column("scopes", Text, nullable=False),
        description="CSV of granted scopes",
    )
    auth_method: str = Field(
        sa_column=Column("auth_method", Text, nullable=False),
        description="'session' | 'bearer'",
    )
    user_agent: Optional[str] = Field(
        default=None,
        sa_column=Column("user_agent", Text, nullable=True, default=None),
        description="Client User-Agent at issuance (forensics)",
    )
    ip_subnet: Optional[str] = Field(
        default=None,
        sa_column=Column("ip_subnet", Text, nullable=True, default=None),
        description="/24 IPv4 or /64 IPv6 when fingerprint_by_ip_subnet=true",
    )
    created_at: str = Field(
        sa_column=Column("created_at", Text, nullable=False),
        description="now_utc_iso() at issuance",
    )
    last_used_at: Optional[str] = Field(
        default=None,
        sa_column=Column("last_used_at", Text, nullable=True, default=None),
        description="now_utc_iso(); 1m-batched touch",
    )
    expires_at: str = Field(
        sa_column=Column("expires_at", Text, nullable=False),
        description="now_utc_iso() + session_ttl_seconds (sweep key)",
    )


class AuthState(SQLModel, table=True):
    """One OAuth2 authorization-request ``state`` row (CSRF + PKCE).

    Exactly-once consumption is the repository's single portable
    conditional UPDATE (``SET consumed_at WHERE consumed_at IS NULL``) —
    the arbitration point per D2.3. A dedicated 60 s sweeper loop
    deletes consumed + expired rows.
    """

    __tablename__ = "auth_states"

    id: str = Field(
        sa_column=Column("id", Text, primary_key=True),
        description="The 'state' param — URL-safe random 32 bytes",
    )
    code_verifier: str = Field(
        sa_column=Column("code_verifier", Text, nullable=False),
        description="PKCE verifier (server-held; never leaves the daemon)",
    )
    code_challenge: str = Field(
        sa_column=Column("code_challenge", Text, nullable=False),
        description="PKCE S256 challenge sent to the IdP",
    )
    challenge_method: str = Field(
        sa_column=Column("challenge_method", Text, nullable=False),
        description="'S256' (never 'plain')",
    )
    redirect_to: Optional[str] = Field(
        default=None,
        sa_column=Column("redirect_to", Text, nullable=True, default=None),
        description="SPA path to return to post-login",
    )
    created_at: str = Field(
        sa_column=Column("created_at", Text, nullable=False),
        description="now_utc_iso() at issuance",
    )
    expires_at: str = Field(
        sa_column=Column("expires_at", Text, nullable=False),
        description="Short-lived expiry (minutes)",
    )
    consumed_at: Optional[str] = Field(
        default=None,
        sa_column=Column("consumed_at", Text, nullable=True, default=None),
        description="Single-CAS marker — NULL = pending",
    )


class BearerToken(SQLModel, table=True):
    """One static bearer token (hash-only storage, NFR-13).

    ``id_hash`` is ``SHA-256(token)`` hex — the plaintext exists only at
    mint time (P4 CLI stdout) and in the caller's presented credential;
    it NEVER reaches this table.
    """

    __tablename__ = "auth_bearer_tokens"

    __table_args__ = (
        Index(
            "idx_auth_bearer_tokens_expires_at",
            "expires_at",
        ),
    )

    id_hash: str = Field(
        sa_column=Column("id_hash", Text, primary_key=True),
        description="SHA-256(token) hex — plaintext NEVER stored",
    )
    subject: str = Field(
        sa_column=Column("subject", Text, nullable=False),
        description="Owner identity (e.g. 'auth:admin' at first mint)",
    )
    scopes: str = Field(
        sa_column=Column("scopes", Text, nullable=False),
        description="CSV of granted scopes",
    )
    label: Optional[str] = Field(
        default=None,
        sa_column=Column("label", Text, nullable=True, default=None),
        description="Operator-friendly name",
    )
    created_at: str = Field(
        sa_column=Column("created_at", Text, nullable=False),
        description="now_utc_iso() at mint",
    )
    expires_at: str = Field(
        sa_column=Column("expires_at", Text, nullable=False),
        description="now_utc_iso() + TTL (default 30d; sweep key)",
    )
    last_used_at: Optional[str] = Field(
        default=None,
        sa_column=Column("last_used_at", Text, nullable=True, default=None),
        description="now_utc_iso(); 1m-batched touch",
    )
    revoked_at: Optional[str] = Field(
        default=None,
        sa_column=Column("revoked_at", Text, nullable=True, default=None),
        description="Soft revocation marker — NULL = active",
    )


class AuthBootstrapConsumption(SQLModel, table=True):
    """One-shot bootstrap-mint sentinel row (phase4 T4.1, F10).

    P2 lands the migration + model ONLY — the table is INERT until the
    P4 mint CLI arrives (D17 inert-tables posture; no repository, no CLI
    in P2). One row per consumed ``sha256(env_value)``: the P4 CLI
    checks for the sentinel before honoring ``BOOTSTRAP_TOKEN``; an
    existing row ⇒ REJECT with the ``consumed_at``/``actor_label``
    detail.
    """

    __tablename__ = "auth_bootstrap_consumption"

    id_hash: str = Field(
        sa_column=Column("id_hash", Text, primary_key=True),
        description="SHA-256(env_value) hex — one row per consumed value",
    )
    consumed_at: str = Field(
        sa_column=Column("consumed_at", Text, nullable=False),
        description="now_utc_iso() at first mint",
    )
    actor_label: str = Field(
        sa_column=Column("actor_label", Text, nullable=False),
        description="The label of the first minted token",
    )
    consumed_by_pid: Optional[int] = Field(
        default=None,
        sa_column=Column("consumed_by_pid", Integer, nullable=True, default=None),
        description="Process that performed the consumption (operator debugging)",
    )


__all__ = [
    "AuthSession",
    "AuthState",
    "BearerToken",
    "AuthBootstrapConsumption",
]
