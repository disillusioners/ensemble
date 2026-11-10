"""The ``prompt_assent_tokens`` SQLModel table — the conversational-assent token store.

One row per minted assent token (the ``prompt-assent`` commission — the
conversational approval lane, "The Emperor's Word / Assent"). The ruling of
record is ``.agents/shared/planning/prompt-assent/architecture-recommendation.md``
@ ``df8da01a6`` (§1 D-1 amendments, §2.1 schema); this model is the canonical
source for the PostgreSQL ``create_all`` path (``daemon/api.py`` N1 REQUIRED
seam — brand-new table, therefore **NO** ``_ensure_postgres_columns`` entry:
the ensure list is ONE transaction for the whole statement list and any
entry's failure aborts boot).

HASH-ONLY STORAGE (D-1 amendment 1 / S-3)
-----------------------------------------
The raw token exists EXACTLY ONCE in the universe — in the mint response
shown to the Emperor. Durable state carries only:

* ``token_hash`` — ``sha256(token)`` (lower-case hex). There is deliberately
  NO column for the raw token (the ``body_sha256`` reasoning: there is no
  column to fill in by accident).
* ``render_sha256`` — ``sha256`` of the **named canonical render** produced
  by the ONE shared helper (``daemon/services/prompt_version_diff.py``,
  task 1.10 / J-3 / K-7). **NEVER** a copy of ``prompt_versions.content_hash``
  (``models.py:369`` — sha256 of raw content): that column-copy would satisfy
  SP-1's letter while silently no-oping J-3 (an effective-side mutation or a
  rendered-view divergence would be invisible and the shown-bytes guarantee
  would disappear). The ``content_hash`` / ``content_sha256`` naming is
  RETIRED for this table and its absence is pinned by test.
* **NO ``token_display`` column** — the grouped render minus dashes *is* the
  raw secret; the grouped form is rendered from the raw token at mint time
  only, in the mint response, and never persisted.

NO HARD FK (O-B)
----------------
``version_id`` is an INDEXED SOFT REFERENCE to ``prompt_versions`` — no
``ForeignKey``. This mirrors the audit-ledger precedent
(``daemon/repositories/prompt_version/models.py``, ``PromptAuditEvent``:
*"no FK — audit must survive even when the version row is archived /
cleaned up"*): token forensics must outlive version archival the same way.

MINT-CONTEXT COLUMNS (AR-1 — channel AND user binding)
------------------------------------------------------
The token binds to WHERE and WHO minted it: ``mint_source_id`` /
``mint_channel_id`` / ``mint_is_dm`` / ``mint_user_id`` (daemon-derived —
K-8) / ``minted_external_user_id`` (the destination identity; NEVER the
approving-user key — the guild form is a CHANNEL). Every value is derived
from the factor-2 window's message row — never from tool arguments (the
anti-forgery analogue of the repo's R3). ``mint_is_dm`` is an int 0/1
flag (house boolean convention, ``is_selected`` precedent).

LIFECYCLE
---------
``live`` → ``consumed`` (the D-4 co-transactional consume-CAS) /
``superseded`` (a re-mint flips the prior live row — deliberately distinct
from ``consumed`` so a replay of a superseded token reads
``token-superseded``, not ``token-already-used`` — S-2) / ``revoked``
(the attempt-counter lockout at the configured threshold). ``attempts`` is
the per-token mismatch counter; the counter and the lockout revoke commit
in their OWN transaction on the refusal path (D-4 blocking rule — a
rollback inside the guarded transition must never reset the counter).

TIMESTAMPS
----------
ISO-8601 UTC TEXT stamps (``%Y-%m-%dT%H:%M:%SZ`` — the
``upgrade_journal.now_iso`` lineage), so string comparison is the window
predicate and the PG session timezone cannot shift a digit. ``ttl_expires_at``
= ``issued_at + NONCE_TTL_S`` with ``NONCE_TTL_S`` IMPORTED from
``daemon/tools/upgrade_journal.py`` (the SSOT — 60 min; never re-declared).

INDEXES (names byte-identical to the .sql migration)
----------------------------------------------------
* ``uq_prompt_assent_tokens_token_hash`` — UNIQUE (token_hash).
* ``uq_prompt_assent_tokens_live_version`` — UNIQUE (version_id)
  ``WHERE status = 'live'`` — the PARTIAL unique index: single-active-token
  is STRUCTURAL against double-mint races (D-1 amendment 2).
* ``idx_prompt_assent_tokens_version_status`` — (version_id, status).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, Index, Integer, Text, text
from sqlmodel import Field, SQLModel


def _new_token_id() -> str:
    """uuid4 hex (32 chars) — the token_id PK mint rule (D-1)."""
    return uuid.uuid4().hex


def _now_iso() -> str:
    """UTC ISO-8601 stamp in the upgrade_journal format (second precision).

    Same shape as ``upgrade_journal.now_iso`` (the lineage the token
    table's clocks follow); the SSOT CONSTANTS (``NONCE_TTL_S``) are
    still IMPORTED from ``daemon.tools.upgrade_journal`` — deferred to
    call time, because a module-level import from ``daemon.tools``
    would re-enter ``daemon.tools.__init__`` → ``instance.py`` →
    ``repositories.factory`` and dead-lock the boot import order.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


#: The table name — ONE constant so the migration parity test, the
#: create_all seam and the no-ensure-entry pin all cite the same string.
PROMPT_ASSENT_TOKENS_TABLE = "prompt_assent_tokens"

#: Lifecycle statuses. ``live`` is the only consumable state; ``superseded``
#: is deliberately distinct from ``consumed`` (S-2 — a replay of a
#: superseded token must read ``token-superseded``, not
#: ``token-already-used``).
STATUS_LIVE = "live"
STATUS_CONSUMED = "consumed"
STATUS_SUPERSEDED = "superseded"
STATUS_REVOKED = "revoked"

#: All lifecycle statuses (the ``status`` column vocabulary).
TOKEN_STATUSES: frozenset[str] = frozenset({
    STATUS_LIVE,
    STATUS_CONSUMED,
    STATUS_SUPERSEDED,
    STATUS_REVOKED,
})


def token_ttl_expires_at(issued_at: str | None = None) -> str:
    """``issued_at + NONCE_TTL_S`` as an ISO-8601 UTC stamp.

    The TTL comes from the IMPORTED ``NONCE_TTL_S`` SSOT (60 min —
    ``daemon/tools/upgrade_journal``; deferred import: a module-level
    ``daemon.tools`` import from a ``daemon.repositories`` module
    dead-locks the boot import order) — the same constant the promote
    lane uses, so a future TTL widening moves both lanes together.
    Called with no argument (or with the mint-time ``issued_at`` stamp)
    by ``PromptAssentTokenRepository.mint``.
    """
    from daemon.tools.upgrade_journal import NONCE_TTL_S

    base = datetime.now(timezone.utc)
    if issued_at:
        parsed = datetime.strptime(issued_at, "%Y-%m-%dT%H:%M:%SZ")
        base = parsed.replace(tzinfo=timezone.utc)
    return (base + timedelta(seconds=NONCE_TTL_S)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class PromptAssentToken(SQLModel, table=True):
    """One minted assent token (hash-only; item-bound; mint-context-bound).

    Attributes:
        token_id: Primary key — uuid4 hex (32 chars).
        version_id: Indexed SOFT reference to ``prompt_versions`` — NO hard
            FK (O-B: forensic rows must outlive version archival, the
            audit-ledger precedent).
        agent_id: Item key — the row's agent (denormalized for audit
            legibility).
        version_tag: Item key — version tag (``''`` for base).
        artifact: Item key — artifact name.
        version: Item key — the pending row's monotonic integer version.
        token_hash: ``sha256(token)`` — lower-case hex. The raw token has NO
            column anywhere in this schema (S-3). UNIQUE.
        render_sha256: ``sha256`` of the named canonical render (J-3/K-7) —
            NEVER ``prompt_versions.content_hash`` (retired naming, pinned).
        mint_source_id: AR-1 mint context — the window's chat source id
            (daemon-derived).
        mint_channel_id: AR-1 mint context — the window's channel id.
        mint_is_dm: AR-1 mint context — 1/0 flag, the window's DM-ness.
        mint_user_id: AR-1 mint context — the daemon-derived user id (K-8;
            the approve-time equality key).
        minted_external_user_id: AR-1 mint context — the destination
            identity (AR-1/D-5: NEVER the approving-user key).
        issued_at: Mint time (ISO-8601 UTC).
        ttl_expires_at: ``issued_at + NONCE_TTL_S`` (imported SSOT).
        superseded_at: When a re-mint flipped this row (``superseded``).
        consumed_at: When the D-4 co-transactional consume-CAS flipped this
            row (``consumed``).
        consumed_by_message_id: The daemon message id of the consuming
            (approving) turn.
        attempts: Per-token mismatch counter (D-4 blocking rule: increments
            + the lockout revoke commit in their OWN transaction on the
            refusal path).
        status: ``live | consumed | superseded | revoked``.
    """

    __tablename__ = PROMPT_ASSENT_TOKENS_TABLE
    __table_args__ = (
        # Anti-confusion fence: the same token value can never mint twice.
        Index(
            "uq_prompt_assent_tokens_token_hash",
            "token_hash",
            unique=True,
        ),
        # D-1 amendment 2 — the PARTIAL unique index: at most ONE live
        # token per item (version_id), structurally, on both drivers.
        Index(
            "uq_prompt_assent_tokens_live_version",
            "version_id",
            unique=True,
            sqlite_where=text("status = 'live'"),
            postgresql_where=text("status = 'live'"),
        ),
        # The lifecycle lookup (find_live / sweeps by (version_id, status)).
        Index(
            "idx_prompt_assent_tokens_version_status",
            "version_id",
            "status",
        ),
    )

    token_id: str = Field(
        default_factory=_new_token_id,
        sa_column=Column("token_id", Text, primary_key=True),
    )
    version_id: str = Field(
        sa_column=Column("version_id", Text, nullable=False),
    )
    agent_id: str = Field(
        sa_column=Column("agent_id", Text, nullable=False),
    )
    version_tag: str = Field(
        default="",
        sa_column=Column("version_tag", Text, nullable=False, default=""),
    )
    artifact: str = Field(
        sa_column=Column("artifact", Text, nullable=False),
    )
    version: int = Field(
        sa_column=Column("version", Integer, nullable=False),
    )
    token_hash: str = Field(
        sa_column=Column("token_hash", Text, nullable=False),
    )
    render_sha256: str = Field(
        sa_column=Column("render_sha256", Text, nullable=False),
    )
    # ── AR-1 mint context (daemon-derived; never tool args) ──────────────
    mint_source_id: str | None = Field(
        default=None,
        sa_column=Column("mint_source_id", Text, nullable=True),
    )
    mint_channel_id: str | None = Field(
        default=None,
        sa_column=Column("mint_channel_id", Text, nullable=True),
    )
    mint_is_dm: int = Field(
        default=0,
        sa_column=Column("mint_is_dm", Integer, nullable=False, default=0),
    )
    mint_user_id: str | None = Field(
        default=None,
        sa_column=Column("mint_user_id", Text, nullable=True),
    )
    minted_external_user_id: str | None = Field(
        default=None,
        sa_column=Column("minted_external_user_id", Text, nullable=True),
    )
    # ── clocks + lifecycle ───────────────────────────────────────────────
    issued_at: str = Field(
        default_factory=_now_iso,
        sa_column=Column("issued_at", Text, nullable=False),
    )
    ttl_expires_at: str = Field(
        default_factory=token_ttl_expires_at,
        sa_column=Column("ttl_expires_at", Text, nullable=False),
    )
    superseded_at: str | None = Field(
        default=None,
        sa_column=Column("superseded_at", Text, nullable=True),
    )
    consumed_at: str | None = Field(
        default=None,
        sa_column=Column("consumed_at", Text, nullable=True),
    )
    consumed_by_message_id: str | None = Field(
        default=None,
        sa_column=Column("consumed_by_message_id", Text, nullable=True),
    )
    attempts: int = Field(
        default=0,
        sa_column=Column("attempts", Integer, nullable=False, default=0),
    )
    status: str = Field(
        default=STATUS_LIVE,
        sa_column=Column("status", Text, nullable=False, default=STATUS_LIVE),
    )
