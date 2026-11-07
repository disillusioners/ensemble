"""SQLModel tables for Project Operating Knowledge (POK Phase 1, T2).

Two tables, ONE package (the lane's repo home; design
architecture-recommendation v3.4 §4.1 — the column set is VERBATIM):

* :class:`ProjectOperatingKnowledge` — ``project_operating_knowledge``
  — the DB-canon store of ordered operating procedures per project.
  One row = one fenced single-paragraph fact. The UNIQUE PARTIAL
  INDEX ``uq_pok_active_slug`` makes a dual-active slug STRUCTURALLY
  impossible: append-with-supersede is the ONLY mutation path.
* :class:`ProjectOperatingKnowledgeEmbedding` —
  ``project_operating_knowledge_embeddings`` — the RANKING-ONLY
  vector companion. A missing row degrades that entry to BM25-only
  scoring with the lag surfaced (AC14); the row is never
  authoritative content.

AUDIENCE DIMENSION (Emperor ruling ER-1, 2026-11-07): ``audience``
∈ {operation, implementation, both} gates ACTIVE injection ONLY.
``pok_query`` NEVER filters by audience (universal read, ER-1.4 —
injection is curated; reading is free). ``audience_agents`` (TEXT
NULL, JSON-encoded list at the boundary) is the curator-set narrow
override — when present it REPLACES the default audience set.

TRUST TIER (Phase 2 — addendum T-1, 2026-11-07): ``source_tier``
∈ {agent, curated} is the WRITE/MUTATION AUTHORITY class (the fifth
independent axis — ⊥ provenance ⊥ pinned ⊥ audience ⊥ status).
``curated`` = the dev-curated immutable reference; writes/flips are
curator-only (leader/PM per A-9). NEVER derived from
``created_by_agent`` (A-5: attribution ≠ authorization). Gates the
ACTIVE-injection curated band + supersede authority ONLY — never
retrieval (addendum T-4: universal read across tiers).

NO FOREIGN KEY anywhere (§4.1): ``project_id`` is plain TEXT;
``superseded_by_id`` is plain TEXT WIRED at the application layer
(the supersede transaction maintains the chain — the column is not
self-referencing). Combined with the additive-only .sql files this
makes the schema ROLLBACK-INERT.

Conventions (house): every string column TEXT on both drivers (never
VARCHAR); ISO-8601 TEXT timestamps (arch's "TIMESTAMPTZ" intent maps
to the house TEXT-ISO convention used by the project_skill_* tables
— SQLite has no TIMESTAMPTZ and the .sql files are the SQLite-side
canonical DDL); JSON-shaped columns via
:class:`~daemon.repositories.infra.types.JSONBType` (embedding
column only — ``audience_agents`` stays plain TEXT per §4.1
verbatim, JSON-encoded at the service boundary). The SQLite-side
canonical DDL lives in one ``.sql`` file per table under
``daemon/migrations/versions/`` (house rule). The pre-create_all
model registration in ``daemon/manager.py`` (B7) is the operative
fresh-PG artifact.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, Column, Index, Integer, Text, false, text
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── CHECK-closed vocabularies (mirrored in BOTH .sql files AND here —
#    readiness constraint 1; keep both sides byte-equivalent) ──────────

POK_DOMAINS: tuple[str, ...] = (
    "artifact",
    "deploy",
    "verify",
    "environment",
    "catalog",
    "other",
)
#: Emperor ruling ER-1: the classification stated at capture,
#: curator-adjustable. Gates ACTIVE injection ONLY — never retrieval.
POK_AUDIENCES: tuple[str, ...] = ("operation", "implementation", "both")
POK_PROVENANCE_VALUES: tuple[str, ...] = ("declared", "observed")
#: ``archived`` is REACHABLE only via pok_curate (forward-only flip —
#: never un-archive).
POK_STATUSES: tuple[str, ...] = ("active", "superseded", "archived")
#: Trust tier (Phase 2 — trust-tier addendum T-1): the write/mutation
#: AUTHORITY class of the row. ``curated`` = the dev-curated immutable
#: reference (the parked guidelines); writes/flips touching it are
#: curator-only (leader/PM per A-9). The FIFTH independent axis on a
#: row (⊥ provenance ⊥ pinned ⊥ audience ⊥ status); NEVER derived from
#: ``created_by_agent`` (A-5: attribution ≠ authorization). Gates the
#: ACTIVE-injection curated band + supersede authority ONLY — never
#: retrieval (addendum T-4: universal read across tiers).
POK_SOURCE_TIERS: tuple[str, ...] = ("agent", "curated")

#: Default audience classification at capture (§4.2 — the channel's
#: genre).
POK_DEFAULT_AUDIENCE = "operation"
POK_DEFAULT_DOMAIN = "other"
POK_DEFAULT_PROVENANCE = "observed"
POK_DEFAULT_STATUS = "active"
#: Default trust tier at capture (addendum T-1 — every Phase-1 call
#: signature and every post-dogfood row stays byte-unchanged; zero
#: behavior change without opt-in).
POK_DEFAULT_SOURCE_TIER = "agent"

#: Slug-family ordering regex (§4.5): ``<family>-<NN>-<slug>`` —
#: lowercase head, two-digit ordinal, free tail. Single source for
#: tools, injection, and tests.
POK_FAMILY_SLUG_PATTERN = r"^([a-z][a-z0-9]*)-(\d{2})-(.+)$"


class ProjectOperatingKnowledge(SQLModel, table=True):
    """One operating-knowledge entry for one project (§4.1 verbatim).

    Lifecycle: written by ``pok_upsert`` (append-with-supersede),
    curated by ``pok_curate`` (pinned/disputed/archived/audience/
    audience_agents — leader/PM only). ``status`` moves
    active→superseded (write path) or active→archived (curator,
    forward-only). ``disputed`` suppresses ACTIVE injection but the
    row stays readable (ER-1.4 universal read).
    """

    __tablename__ = "project_operating_knowledge"
    __table_args__ = (
        CheckConstraint(
            "domain IN ('artifact','deploy','verify','environment','catalog','other')",
            name="ck_pok_domain",
        ),
        CheckConstraint(
            "audience IN ('operation','implementation','both')",
            name="ck_pok_audience",
        ),
        CheckConstraint(
            "provenance IN ('declared','observed')",
            name="ck_pok_provenance",
        ),
        CheckConstraint(
            "status IN ('active','superseded','archived')",
            name="ck_pok_status",
        ),
        CheckConstraint(
            "source_tier IN ('agent','curated')",
            name="ck_pok_source_tier",
        ),
        # Dual-active structurally impossible (§4.1). The partial
        # predicate keeps exactly ONE active row per (project_id, slug);
        # superseded/archived rows free the slug for the successor.
        Index(
            "uq_pok_active_slug",
            "project_id",
            "slug",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
        Index("idx_pok_project_id", "project_id"),
        Index("idx_pok_project_status", "project_id", "status"),
    )

    id: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("id", Text, primary_key=True),
    )
    # NO FK — plain TEXT (§4.1; rollback-inert posture).
    project_id: str = Field(sa_column=Column("project_id", Text, nullable=False))
    slug: str = Field(sa_column=Column("slug", Text, nullable=False))
    domain: str = Field(
        default=POK_DEFAULT_DOMAIN,
        sa_column=Column(
            "domain", Text, nullable=False, server_default=POK_DEFAULT_DOMAIN
        ),
    )
    audience: str = Field(
        default=POK_DEFAULT_AUDIENCE,
        sa_column=Column(
            "audience", Text, nullable=False, server_default=POK_DEFAULT_AUDIENCE
        ),
    )
    # JSON-encoded list[str] of agent ids at the service boundary;
    # NULL = use the audience's default set (§4.5.1).
    audience_agents: Optional[str] = Field(
        default=None, sa_column=Column("audience_agents", Text, nullable=True)
    )
    body: str = Field(sa_column=Column("body", Text, nullable=False))
    provenance: str = Field(
        default=POK_DEFAULT_PROVENANCE,
        sa_column=Column(
            "provenance",
            Text,
            nullable=False,
            server_default=POK_DEFAULT_PROVENANCE,
        ),
    )
    status: str = Field(
        default=POK_DEFAULT_STATUS,
        sa_column=Column(
            "status", Text, nullable=False, server_default=POK_DEFAULT_STATUS
        ),
    )
    disputed: bool = Field(
        default=False,
        sa_column=Column(
            "disputed", Boolean, nullable=False, server_default=false()
        ),
    )
    # WIRED at the application layer (supersede txn) — deliberately
    # NOT a ForeignKey (§4.1).
    superseded_by_id: Optional[str] = Field(
        default=None, sa_column=Column("superseded_by_id", Text, nullable=True)
    )
    content_sha256: str = Field(
        sa_column=Column("content_sha256", Text, nullable=False)
    )
    created_by_agent: str = Field(
        sa_column=Column("created_by_agent", Text, nullable=False)
    )
    created_at: str = Field(
        default_factory=_utc_now_iso,
        sa_column=Column("created_at", Text, nullable=False),
    )
    updated_at: str = Field(
        default_factory=_utc_now_iso,
        sa_column=Column("updated_at", Text, nullable=False),
    )
    last_verified: Optional[str] = Field(
        default=None, sa_column=Column("last_verified", Text, nullable=True)
    )
    verify_count: int = Field(
        default=0,
        sa_column=Column(
            "verify_count", Integer, nullable=False, server_default="0"
        ),
    )
    access_count: int = Field(
        default=0,
        sa_column=Column(
            "access_count", Integer, nullable=False, server_default="0"
        ),
    )
    last_accessed_at: Optional[str] = Field(
        default=None, sa_column=Column("last_accessed_at", Text, nullable=True)
    )
    pinned: bool = Field(
        default=False,
        sa_column=Column("pinned", Boolean, nullable=False, server_default=false()),
    )
    # Trust tier (addendum T-1, Phase 2): write/mutation AUTHORITY class
    # ⊥ provenance ⊥ pinned ⊥ audience ⊥ status. DEFAULT 'agent' ⇒ every
    # Phase-1 call signature + every post-dogfood row stays byte-unchanged.
    # NEVER derived from created_by_agent (A-5: attribution ≠ authorization).
    source_tier: str = Field(
        default=POK_DEFAULT_SOURCE_TIER,
        sa_column=Column(
            "source_tier",
            Text,
            nullable=False,
            server_default=POK_DEFAULT_SOURCE_TIER,
        ),
    )


class ProjectOperatingKnowledgeEmbedding(SQLModel, table=True):
    """Ranking-only vector companion row for one POK entry (§4.1).

    RANKING-ONLY: never authoritative content. A missing/stale row
    degrades that entry to BM25-only scoring in the selector with the
    lag surfaced (AC14) — never an error. Orphan rows (entry
    superseded/archived) are harmless: every read joins on live entry
    ids, and the supersede/archive path removes the companion row in
    the same transaction where practical.
    """

    __tablename__ = "project_operating_knowledge_embeddings"
    __table_args__ = (
        Index("idx_poke_entry_id", "entry_id"),
    )

    id: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("id", Text, primary_key=True),
    )
    # NO FK — plain TEXT, mirrors the main table's posture.
    entry_id: str = Field(sa_column=Column("entry_id", Text, nullable=False))
    # JSON array of floats (JSONBType → JSON on SQLite / JSONB on PG) —
    # the house embeddings shape, never BYTEA/numpy/pickle.
    embedding: list = Field(sa_column=Column("embedding", JSONBType, nullable=False))
    created_at: str = Field(
        default_factory=_utc_now_iso,
        sa_column=Column("created_at", Text, nullable=False),
    )
