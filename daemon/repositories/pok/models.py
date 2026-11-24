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

AUDIENCE DIMENSION (user ruling ER-1, 2026-11-07): ``audience``
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

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    Index,
    Integer,
    Text,
    false,
    text,
)
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType

__all__ = [
    "POK_AUDIENCES",
    "POK_ARCHIVE_BASES",
    "POK_DEFAULT_AUDIENCE",
    "POK_DEFAULT_DOMAIN",
    "POK_DEFAULT_PROVENANCE",
    "POK_DEFAULT_SOURCE_TIER",
    "POK_DEFAULT_STATUS",
    "POK_DOMAINS",
    "POK_FAMILY_SLUG_PATTERN",
    "POK_FAMILY_RE",
    "pok_family_key",
    "POK_LEDGER_MERGE_KINDS",
    "POK_LEDGER_PHASES",
    "POK_LEDGER_SOURCES",
    "POK_LEDGER_VERDICTS",
    "POK_PROVENANCE_VALUES",
    "POK_SOURCE_TIERS",
    "POK_STATUSES",
    "ProjectOperatingKnowledge",
    "ProjectOperatingKnowledgeEmbedding",
    "POKWriteGateLedger",
]


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
#: user ruling ER-1: the classification stated at capture,
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
#: lowercase family head (possibly multi-token, e.g. ``prompt-assent`` or
#: ``snapshot-l3``), two-digit ordinal, free tail. Single source for
#: tools, injection, and tests.
POK_FAMILY_SLUG_PATTERN = r"^([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)-(\d{2})-(.+)$"

#: Compiled form of the canonical pattern — the SINGLE source every
#: family-membership decision derives from.
POK_FAMILY_RE = re.compile(POK_FAMILY_SLUG_PATTERN)


def pok_family_key(slug: str) -> str | None:
    """The canonical family key of a slug, else ``None``.

    THE single derivation: the maximal run of letter-led tokens before
    the leftmost ``-NN-`` ordinal (``prompt-assent-01-x`` -> ``prompt-assent``).
    Family MEMBERSHIP is decided by comparing this key for EQUALITY —
    never by a prefix match.
    """
    m = POK_FAMILY_RE.match(slug)
    return m.group(1) if m else None


# ── WRITE-GATE LEDGER VOCABULARIES (pok-write-gate Phase 1, T1.1) ──────
#
# The ledger is the DECISION SUBSTRATE for the write gate (plan
# ``.agents/shared/planning/pok-write-gate/`` — design-amendments-v2
# §13.5 "Binding schema consequences (no-pick-foreclosed rule)").
# These vocabularies land UP FRONT so the table can represent a
# proposal, a verdict, and an ASK round-trip without a later migration.

#: The gate's four outcomes (design-amendments-v2 §8). The set is
#: CLOSED — the verdict token set is unchanged by the R-RET/R-PROV
#: rider (gate-rules-r6-r7-amendment.md §2.4).
POK_LEDGER_VERDICTS: tuple[str, ...] = ("ACCEPT", "MERGE", "ASK", "REJECT")

#: The ledger's CHAIN-STEP discriminator.
#:
#: A gate event is recorded as a chain of rows keyed by ``gate_id`` +
#: ``phase`` (design-amendments-v2 §13.2 fork G):
#:   * a PLAIN chain (ACCEPT / MERGE / REJECT) keeps TWO rows —
#:     ``ADMISSION`` (the gate's verdict) → ``EXECUTION`` (the tool's
#:     write);
#:   * an ASK chain keeps THREE rows — ``ASK_MINTED`` (the verdict
#:     row) → ``ASK_ANSWERED`` (post-answer adjudication) →
#:     ``ASK_EXECUTED`` (the tool's write);
#:   * ``ASK_EXPIRED`` records a gate that expired unanswered
#:     (fail-closed — NO write);
#:   * ``ASK_SHADOW`` records the advisory-only shadow-ASK verdict
#:     (NO pending mint, NO execution row);
#:   * ``RETRIEVAL`` records a retrieval event (a stored row was
#:     retrieved/injected) — these counts SUPERSEDE ``access_count``
#:     for the librarian (design-amendments-v2 §13.2 fork J,
#:     amendment 1: they disambiguate query-vs-injection).
POK_LEDGER_PHASES: tuple[str, ...] = (
    "ADMISSION",
    "RETRIEVAL",
    "EXECUTION",
    "ASK_MINTED",
    "ASK_ANSWERED",
    "ASK_EXECUTED",
    "ASK_EXPIRED",
    "ASK_SHADOW",
)

#: Merge-proposal kinds (design-amendments-v2 §13.2 fork F). A
#: ``no_delta`` merge is NEVER a write — it stamps the target only.
POK_LEDGER_MERGE_KINDS: tuple[str, ...] = ("no_delta", "delta")

#: WHERE a retrieval came from. The ledger's retrieval counts
#: SUPERSEDE ``access_count`` for the librarian precisely because they
#: DISAMBIGUATE query-vs-injection, which ``access_count`` alone
#: cannot (design-amendments-v2 §13.2 fork J, amendment 1: "they
#: disambiguate query-vs-injection, which the effectiveness brief
#: :104 explicitly could not").
#:
#: The column lands UP FRONT (no-pick-foreclosed rule, §13.5): a
#: retrieval that cannot say where it came from is exactly the
#: ambiguity the ledger exists to remove. ``query`` is the read-path
#: side (the ``pok_query`` tool); ``injection`` is the context-block
#: side. A row that is NOT a retrieval carries NULL.
#:
#: ── RETRIEVAL EVENTS: DESIGNED, NOT WIRED (Wave 1) ─────────────
#: No production caller records ``source="query"`` in Wave 1: the
#: ``pok_query`` read path does not append a ledger RETRIEVAL row
#: yet. The column lands up front so the read path can populate it
#: in Wave 2 without a migration. Until then, retrieval rows that
#: exist carry ``source="injection"`` only.
POK_LEDGER_SOURCES: tuple[str, ...] = ("injection", "query")

#: The RULED ARCHIVE BASES — the ONLY grounds on which a row may be
#: archived (design-amendments-v2 §9: "Consolidation ONLY under ruled
#: archive bases — covered-by-surviving-row · relocated-to-routed-home
#: · NOW-FALSE — NEVER age, NEVER access_count alone"). The fourth
#: member (``gate-rejection``) is the curator's citation of a gate
#: rejection as archive basis (design-amendments-v2 §1: "``pok_curate``
#: stays curator-authority (leader/PM) and may cite a gate rejection as
#: archive basis").
#:
#: The token spellings are the PLAN's literal tokens (phase10-plan.md
#: row 7 / plan-overview.md task-map row 10 / design-amendments-v2
#: §9 all agree) — ``NOW-FALSE`` is upper-case in the plan and is
#: reproduced verbatim here so the librarian and the curator cite the
#: SAME token.
POK_ARCHIVE_BASES: tuple[str, ...] = (
    "covered-by-surviving-row",
    "relocated-to-routed-home",
    "NOW-FALSE",
    "gate-rejection",
)


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


class POKWriteGateLedger(SQLModel, table=True):
    """The WRITE-GATE LEDGER — one APPEND-ONLY row per gate event.

    The decision substrate for the POK write gate (pok-write-gate
    Phase 1, T1.1). It records, for every gate event:

    * the ADMISSION decision (the gate's verdict + the reason excerpt
      + the judge explanation),
    * the RETRIEVAL events (a stored row was retrieved/injected),
    * the TOOL's EXECUTION (what was actually written, by whom),
    * the BASIS on which anything was archived.

    AUTHORITY SPLIT (design-amendments-v2 §8, BINDING): the gate NEVER
    writes — it returns a verdict; the TOOL executes. The audit
    records BOTH: the gate's verdict row and the tool's execution row.
    A plain ACCEPT/MERGE/REJECT chain therefore keeps TWO rows
    (``ADMISSION`` → ``EXECUTION``); an ASK chain keeps THREE
    (``ASK_MINTED`` → ``ASK_ANSWERED`` → ``ASK_EXECUTED``), keyed by
    ``gate_id`` + ``phase``.

    THE LEDGER ALONE ANSWERS "why was this row accepted?" (plan
    success criterion #30): gate_id, verdict, reason excerpt, judge
    explanation, phase, and execution are all here — NO cross-store
    join is required.

    MERGE-PROPOSAL FIELDS land UP FRONT (design-amendments-v2 §13.5
    "no-pick-foreclosed rule"): a schema that cannot represent a
    proposal forces a migration at P1. ``target_slug`` +
    ``merge_kind`` + ``proposed_body`` + the three sha256 columns +
    ``executed_by`` let the librarian measure edit-distance between
    what the judge PROPOSED and what the tool EXECUTED.

    NO FOREIGN KEY anywhere (house posture, matching the sibling POK
    tables): ``project_id`` / ``entry_id`` / ``gate_id`` are plain
    TEXT. Combined with the additive-only .sql file this makes the
    table ROLLBACK-INERT.

    APPEND-ONLY: rows are never updated and never deleted by the
    substrate. The repository exposes an append + read-only query
    surface; there is deliberately NO update/delete method.
    """

    __tablename__ = "pok_write_gate_ledger"
    __table_args__ = (
        CheckConstraint(
            "phase IN ("
            "'ADMISSION','RETRIEVAL','EXECUTION','ASK_MINTED',"
            "'ASK_ANSWERED','ASK_EXECUTED','ASK_EXPIRED','ASK_SHADOW')",
            name="ck_pok_ledger_phase",
        ),
        CheckConstraint(
            "verdict IN ('ACCEPT','MERGE','ASK','REJECT')",
            name="ck_pok_ledger_verdict",
        ),
        CheckConstraint(
            "merge_kind IN ('no_delta','delta')",
            name="ck_pok_ledger_merge_kind",
        ),
        CheckConstraint(
            "basis IN ("
            "'covered-by-surviving-row','relocated-to-routed-home',"
            "'NOW-FALSE','gate-rejection')",
            name="ck_pok_ledger_basis",
        ),
        CheckConstraint(
            "source IN ('injection','query')",
            name="ck_pok_ledger_source",
        ),
        Index("idx_pok_ledger_project_id", "project_id"),
        Index("idx_pok_ledger_project_phase", "project_id", "phase"),
        Index("idx_pok_ledger_project_created", "project_id", "created_at"),
        Index("idx_pok_ledger_gate_id", "gate_id"),
        Index("idx_pok_ledger_entry_id", "entry_id"),
        # DB-LEVEL GATE-CHAIN IDEMPOTENCY (pok-write-gate Phase 8,
        # T8.16): at most ONE row per (project_id, gate_id, phase).
        # The appender used to be app-level read-then-append only, so
        # two concurrent callers could write the SAME chain step
        # twice — the DB uniqueness is the Wave-2 backstop (the
        # single-writer discipline is documentation, not enforcement).
        #
        # PARTIAL on ``gate_id IS NOT NULL``: a RETRIEVAL row (and any
        # other gate-less row) belongs to no chain and must never be
        # constrained.
        #
        # 3-SITE PIN (byte-identical name + predicate):
        #   * this declaration (fresh databases via ``create_all``),
        #   * ``daemon/migrations/versions/20261124_093000_add_pok_
        #     ledger_gate_chain_unique_index.sql`` (SQLite),
        #   * ``daemon/manager.py::_ensure_postgres_columns`` (the
        #     PostgreSQL mirror — the .sql runner is a NO-OP on PG).
        Index(
            "uq_pok_ledger_gate_chain",
            "project_id",
            "gate_id",
            "phase",
            unique=True,
            sqlite_where=text("gate_id IS NOT NULL"),
            postgresql_where=text("gate_id IS NOT NULL"),
        ),
    )

    id: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("id", Text, primary_key=True),
    )
    # NO FK — plain TEXT (house posture; rollback-inert).
    project_id: str = Field(sa_column=Column("project_id", Text, nullable=False))
    #: The chain key. Rows of one gate event share it; a RETRIEVAL
    #: event that belongs to no gate carries NULL.
    gate_id: Optional[str] = Field(
        default=None, sa_column=Column("gate_id", Text, nullable=True)
    )
    #: The chain-step discriminator — see :data:`POK_LEDGER_PHASES`.
    phase: str = Field(sa_column=Column("phase", Text, nullable=False))
    #: The POK row this event concerns (the landed row, the candidate
    #: row, the merge target, or the retrieved row).
    entry_id: Optional[str] = Field(
        default=None, sa_column=Column("entry_id", Text, nullable=True)
    )
    #: The slug under consideration (the candidate's slug).
    candidate_slug: Optional[str] = Field(
        default=None, sa_column=Column("candidate_slug", Text, nullable=True)
    )
    #: The gate's outcome — see :data:`POK_LEDGER_VERDICTS`. NULL on a
    #: RETRIEVAL row (a retrieval is not a decision).
    verdict: Optional[str] = Field(
        default=None, sa_column=Column("verdict", Text, nullable=True)
    )
    #: The caller's OPERATIONAL CLAIM (the reason) — the excerpt an
    #: operator reads to answer "why was this row accepted?".
    reason: Optional[str] = Field(
        default=None, sa_column=Column("reason", Text, nullable=True)
    )
    #: The judge's explanation of the verdict.
    judge_explanation: Optional[str] = Field(
        default=None, sa_column=Column("judge_explanation", Text, nullable=True)
    )
    #: The judge's confidence (0..1). A confidence <= 0.667 (≈ 2/3)
    #: routes to ASK (never auto-reject).
    confidence: Optional[float] = Field(
        default=None, sa_column=Column("confidence", Float, nullable=True)
    )
    #: The RULED BASIS on which anything was archived — see
    #: :data:`POK_ARCHIVE_BASES`. NULL on rows that archived nothing.
    basis: Optional[str] = Field(
        default=None, sa_column=Column("basis", Text, nullable=True)
    )
    #: WHERE a retrieval came from — ``injection`` or ``query`` (see
    #: :data:`POK_LEDGER_SOURCES`). NULL on rows that are not
    #: retrievals. This is the column that makes the ledger's
    #: retrieval counts disambiguate query-vs-injection.
    source: Optional[str] = Field(
        default=None, sa_column=Column("source", Text, nullable=True)
    )
    #: The ASK round-trip: the answer excerpt and who answered.
    answer_excerpt: Optional[str] = Field(
        default=None, sa_column=Column("answer_excerpt", Text, nullable=True)
    )
    answered_by: Optional[str] = Field(
        default=None, sa_column=Column("answered_by", Text, nullable=True)
    )
    #: The tool's execution: who executed, and whether a write
    #: actually happened. A NO-delta merge and an expired ASK are
    #: EXECUTED=FALSE — the audit says so explicitly.
    executed_by: Optional[str] = Field(
        default=None, sa_column=Column("executed_by", Text, nullable=True)
    )
    executed: bool = Field(
        default=False,
        sa_column=Column("executed", Boolean, nullable=False, server_default=false()),
    )
    # ── merge-proposal fields (design-amendments-v2 §13.5) ──────────
    #: The MERGE target slug.
    target_slug: Optional[str] = Field(
        default=None, sa_column=Column("target_slug", Text, nullable=True)
    )
    #: ``no_delta`` (no write — a verification stamp only) or ``delta``
    #: (the only case a merged body is authored).
    merge_kind: Optional[str] = Field(
        default=None, sa_column=Column("merge_kind", Text, nullable=True)
    )
    #: The body the judge/caller PROPOSED.
    proposed_body: Optional[str] = Field(
        default=None, sa_column=Column("proposed_body", Text, nullable=True)
    )
    proposed_body_sha256: Optional[str] = Field(
        default=None, sa_column=Column("proposed_body_sha256", Text, nullable=True)
    )
    target_body_sha256: Optional[str] = Field(
        default=None, sa_column=Column("target_body_sha256", Text, nullable=True)
    )
    #: The body the tool EXECUTED — the edit-distance anchor against
    #: ``proposed_body_sha256`` (rubber-stamp detection).
    executed_body_sha256: Optional[str] = Field(
        default=None, sa_column=Column("executed_body_sha256", Text, nullable=True)
    )
    #: The acting agent (attribution — never authorization).
    agent_id: Optional[str] = Field(
        default=None, sa_column=Column("agent_id", Text, nullable=True)
    )
    created_at: str = Field(
        default_factory=_utc_now_iso,
        sa_column=Column("created_at", Text, nullable=False),
    )
