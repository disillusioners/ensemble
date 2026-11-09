"""The four Memoir Store tables — layer 1 (episodes), 3 (persona), 4 (syntheses).

Phase 1 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase1-plan.md``).

Design contract refs: ``design.md`` §3 (the three storage kinds — the
relational table is the TRUTH, the ``tsv`` column is the LEXICAL half,
the JSONB embedding is the SEMANTIC half), §4.1 (episodes), §4.2
(keepsakes — a STATUS on the episode row, not a table), §4.3 (the
persona layer), §4.4 (syntheses — SCHEMA ONLY in v1, EMPTY); D1.2 (every
string column is TEXT), D1.8 (``StarsListType``), D1.9 (the embedding
identity columns), D1.14 (``source_episode_ids`` is ``JSONBType``),
D1.15 (the timestamp families).

Storage posture (the ``app_settings`` / ``resource_monitor`` / ``board``
precedent):

* **PostgreSQL** — the schema comes from
  ``SQLModel.metadata.create_all`` with THESE models as the canonical
  source. The models register into the metadata via the late-import
  block in ``daemon/manager.py`` BEFORE the ``create_all`` call, so the
  tables arrive on existing PG databases on the next boot with zero
  operator action.
* **SQLite** — the companion migration
  ``daemon/migrations/versions/20261105_102821_create_memoir_tables.sql``
  is the canonical persistent record of the schema change (the runner is
  SQLite-only; the PG side of the runner is a NO-OP).

THE ``tsv`` COLUMN IS IN NO MODEL (D1.4). SQLite has no ``to_tsvector``,
so a generated column in the model would break every SQLite boot. It is
added by the ``_ensure_postgres_columns`` ensure as an idempotent
``ALTER TABLE ... ADD COLUMN IF NOT EXISTS ... GENERATED ALWAYS AS
(to_tsvector('english', coalesce(title,'') || ' ' || body)) STORED`` plus
a GIN index. The **two-argument** ``to_tsvector`` form is mandatory: the
one-argument form is STABLE and PostgreSQL rejects it (*"generation
expression is not immutable"*) — proven on PG 16.15 during phase 1.

MODEL-BOUND CONSTRAINTS (W2): the CHECK constraints below are bound to
the MODELS, not only to the ``.sql`` — on PG the tables are built by
``create_all`` FROM THE MODELS (the runner is SQLite-only, so the
``.sql`` never runs there). SQLModel's ``Field()`` does not emit CHECK:
without ``__table_args__`` the PG tables would carry no importance /
pinned_by / status / source / verb constraint at all.

Column families (D1.15): ``created_at`` / ``event_at`` / ``pinned_at`` /
``last_accessed_at`` / ``updated_at`` are timezone-aware ``DATETIME``;
``embedded_at`` / ``submitted_at`` / ``approved_at`` / ``valid_from`` /
``valid_to`` are ISO-8601 TEXT (the PSL / critical-notes string
convention for provenance stamps).

MUTABILITY (DD-8, decision §4 row 5): the record columns (``id``,
``agent_id``, ``title``, ``body``, ``created_at``, ``event_at``,
``importance``, ``stars``) are written ONCE at INSERT. Every operational
family has exactly ONE named repository writer; there is no
general-purpose update method (phase 2 owns those writers).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    REAL,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


# ── StarsListType (D1.8 / AM-7) ────────────────────────────────────────────


class StarsListType(TypeDecorator):
    """A flat list of entity mentions — ``TEXT[]`` on PG, JSON on SQLite.

    Contract (D1.8, architect-verified round-trip on both drivers for
    ``["Emperor", "the Emperor"]``, ``None`` and ``[]``):

    * ``impl = JSON`` and ``cache_ok = True``.
    * ``load_dialect_impl`` → ``dialect.type_descriptor(ARRAY(Text()))``
      on PostgreSQL (so PG renders ``TEXT[]`` — the house "never VARCHAR"
      rule) and ``dialect.type_descriptor(JSON())`` elsewhere.
    * ``process_bind_param`` / ``process_result_value`` are IDENTITY: they
      return a **Python list on BOTH drivers**. A pre-serialized JSON
      string would be re-encoded on PG (double-encoding).
    * The column stays NULLABLE — ``None`` is not ``[]`` — and there is
      never a mutable default.

    The stars are stored FLAT and UNUSED by retrieval in v1 (DD-19): the
    mentions are captured exactly as the writer phrased them; the v2
    alias merge is a star-table identity problem, not a string
    transform.
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        """Return the dialect-specific column type."""
        if dialect.name == "postgresql":
            return dialect.type_descriptor(ARRAY(Text()))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        """Coerce to a plain list of strings (identity, never a JSON string)."""
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        """Return a plain list of strings (identity, on BOTH drivers)."""
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        return value


# ── The persona vocabulary (mirrored from the prompt-version lane) ────────

#: The persona version lifecycle (DD-9, D1.11). MIRRORED from
#: ``daemon/repositories/prompt_version/repository.py`` (``VALID_TRANSITIONS``)
#: so DD-9's gate is enforced by the data and not by a convention. The
#: house set is: ``draft -> pending_approval -> approved | rejected |
#: draft`` and ``draft -> archived``. ``draft -> approved`` is NOT a
#: member — the human lane is not optional.
PERSONA_VALID_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("draft", "pending_approval"),
    ("pending_approval", "approved"),
    ("pending_approval", "rejected"),
    ("pending_approval", "draft"),
    ("draft", "archived"),
})

#: The persona version STATUS vocabulary (the CHECK constraint domain).
PERSONA_STATUSES: frozenset[str] = frozenset({
    "draft",
    "pending_approval",
    "approved",
    "rejected",
    "archived",
})

#: The persona version SOURCE vocabulary. ``seed`` is the day-one seed
#: (born ``approved``, bypassing the transition vocabulary by design);
#: ``ari`` and ``operator`` are the v2 authoring lanes.
PERSONA_SOURCES: frozenset[str] = frozenset({"seed", "ari", "operator"})

#: The synthesis VERB vocabulary (design §4.4): how a new belief relates
#: to the prior one.
SYNTHESIS_VERBS: frozenset[str] = frozenset({
    "CREATE",
    "CORROBORATE",
    "REFINE",
    "CORRECT",
})

#: Who may pin a keepsake (R2 — the dual governance: the user via Ari in
#: chat, and Ari via tool).
PIN_ACTORS: frozenset[str] = frozenset({"empress", "emperor"})


# ── Layer 1 + 2 — memoir_episodes ─────────────────────────────────────────


class MemoirEpisode(SQLModel, table=True):
    """One append-only, immutable first-person narrative row.

    Layer 1 of the four layers (design §4.1). Layer 2 (keepsakes) is a
    STATUS on this row (``is_keepsake`` + the pin columns), not a table.

    The write path is ONE INSERT and never blocks the turn (DD-20): the
    ``tsv`` lexical half is DB-generated and instant, and the semantic
    half is computed by the background worker (phase 3). An episode is
    therefore lexically searchable the moment it is written and
    semantically searchable when its embedding lands.

    Two-axis time discipline (DD-7): ``created_at`` is the WRITE time and
    ``event_at`` is the EVENT time (defaulting to the write time). The
    past can be written about late, and time-travel queries run on
    ``event_at``.
    """

    __tablename__ = "memoir_episodes"

    __table_args__ = (
        # W2 — the CHECK constraints are bound to the MODEL, because PG
        # builds the table from the model (the .sql never runs there).
        CheckConstraint(
            "importance BETWEEN 1 AND 10",
            name="ck_memoir_episodes_importance",
        ),
        CheckConstraint(
            "pinned_by IS NULL OR pinned_by IN ('empress', 'emperor')",
            name="ck_memoir_episodes_pinned_by",
        ),
        # 3-site index lockstep — the names AND column lists are
        # byte-identical to the companion .sql migration.
        Index("idx_memoir_ep_agent_event", "agent_id", "event_at"),
        Index(
            "idx_memoir_ep_keepsakes",
            "agent_id",
            "created_at",
            sqlite_where=text("is_keepsake"),
            postgresql_where=text("is_keepsake"),
        ),
        Index(
            "idx_memoir_ep_pending_emb",
            "agent_id",
            sqlite_where=text("embedding IS NULL"),
            postgresql_where=text("embedding IS NULL"),
        ),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    agent_id: str = Field(sa_column=Column(Text, nullable=False))
    title: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    body: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    event_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    importance: int = Field(
        default=5, sa_column=Column(Integer, nullable=False, server_default="5")
    )
    stars: list[str] | None = Field(
        default=None, sa_column=Column(StarsListType, nullable=True)
    )
    is_keepsake: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    pinned_by: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    pinned_reason: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    pinned_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    last_accessed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    access_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    embedding: list[float] | None = Field(
        default=None, sa_column=Column(JSONBType, nullable=True)
    )
    embedding_model: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    embedding_dims: int | None = Field(
        default=None, sa_column=Column(Integer, nullable=True)
    )
    embedded_at: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    embedding_attempts: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    embedding_error: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    superseded_by: str | None = Field(
        default=None,
        sa_column=Column(Text, ForeignKey("memoir_episodes.id"), nullable=True),
    )
    # ── Memoir Store v2 evolution (V2-D 1.3 / V2-D 1.4 / V2-10) ──────────
    # Four columns, added by the Phase-1 .sql migration (plain
    # ``ADD COLUMN``, SQLite-safe) and by the PostgreSQL ensure in
    # ``daemon/manager.py`` (``TIMESTAMPTZ`` for ``reflected_at``).
    #
    # ``importance`` is WRITE-FROZEN (the v1 lane sealed it) — the v2
    # poignancy score is a SEPARATE axis that never rewrites the
    # importance the user granted (V2-10 / §3-A2).
    #
    # ``poignancy_status`` is the NULLABLE state marker: NULL means
    # "never scored", ``'legacy'`` means "pre-cutover row stamped by the
    # migration seed", and the scoring worker owns the rest of the
    # vocabulary.
    poignancy_status: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    poignancy: int | None = Field(
        default=None, sa_column=Column(Integer, nullable=True)
    )
    poignancy_attempts: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    reflected_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )


# The dynamic-zone index is DESC-ordered, which cannot be expressed with
# the string column references ``__table_args__`` accepts — so it is
# declared here, after the class, against the live table columns. The
# name and the column list stay in 3-site lockstep with the .sql.
Index(
    "idx_memoir_ep_agent_created",
    MemoirEpisode.__table__.c.agent_id,
    MemoirEpisode.__table__.c.created_at.desc(),
)


# ── Layer 3 — memoir_persona + memoir_persona_versions ────────────────────


class MemoirPersona(SQLModel, table=True):
    """The pointer row: WHICH persona version is currently in force.

    Layer 3 (design §4.3) is one small evolving markdown document — "who
    we are". The document itself lives in
    :class:`MemoirPersonaVersion`; this row is the pointer to the
    currently-approved version, so the durable self is never overwritten
    in place (the Hy-Memory production scar).
    """

    __tablename__ = "memoir_persona"

    agent_id: str = Field(sa_column=Column(Text, primary_key=True))
    current_version_id: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class MemoirPersonaVersion(SQLModel, table=True):
    """One version of the persona document (the reviewable durable self).

    The transition vocabulary is mirrored from the prompt-version lane
    (DD-9): a persona edit lands ``pending_approval`` and takes effect
    ONLY on approval — the human lane. ``(draft, approved)`` is not a
    member of :data:`PERSONA_VALID_TRANSITIONS`, so the gate is enforced
    by the data.
    """

    __tablename__ = "memoir_persona_versions"

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'rejected', "
            "'archived')",
            name="ck_memoir_persona_versions_status",
        ),
        CheckConstraint(
            "source IN ('seed', 'ari', 'operator')",
            name="ck_memoir_persona_versions_source",
        ),
        UniqueConstraint(
            "agent_id", "version", name="uq_memoir_persona_versions_agent_version"
        ),
        Index("idx_memoir_persona_versions_agent", "agent_id", "version"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    agent_id: str = Field(sa_column=Column(Text, nullable=False))
    version: int = Field(sa_column=Column(Integer, nullable=False))
    body: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(sa_column=Column(Text, nullable=False))
    source: str = Field(sa_column=Column(Text, nullable=False))
    submitted_at: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    approved_at: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


# ── Layer 4 — memoir_syntheses (SCHEMA ONLY, EMPTY in v1) ────────────────


class MemoirSynthesis(SQLModel, table=True):
    """One synthesized belief — the table ships EMPTY in v1 (DD-2).

    Layer 4 exists so that v2 adds a BEHAVIOR, not migration churn.
    Nothing writes to this table in v1: the only references in the tree
    are this model and the manager's registration import.
    """

    __tablename__ = "memoir_syntheses"

    __table_args__ = (
        CheckConstraint(
            "verb IN ('CREATE', 'CORROBORATE', 'REFINE', 'CORRECT')",
            name="ck_memoir_syntheses_verb",
        ),
        Index("idx_memoir_syntheses_agent", "agent_id", "created_at"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    agent_id: str = Field(sa_column=Column(Text, nullable=False))
    claim: str = Field(sa_column=Column(Text, nullable=False))
    # D1.14 / DR-6: JSONBType (JSONB on PG, JSON on SQLite). The design
    # §4.4 sketch shows ``[]``; the typed column keeps the .sql
    # SQLite-valid and has ZERO behavioural effect while the table is
    # empty.
    source_episode_ids: list[str] | None = Field(
        default=None, sa_column=Column(JSONBType, nullable=True)
    )
    valid_from: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    valid_to: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    verb: str = Field(sa_column=Column(Text, nullable=False))
    supersedes: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    superseded_by: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    confidence: float | None = Field(
        default=None, sa_column=Column(REAL, nullable=True)
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


# ── Memoir Store v2 — the Constellation substrate (V2-D 1.2 / 1.5 / 1.6) ──
#
# Six NEW tables, created on PostgreSQL by the model registration in
# ``daemon/manager.py`` (the late-import block BEFORE
# ``SQLModel.metadata.create_all``) and on SQLite by the Phase-1
# migration ``daemon/migrations/versions/20261109_021154_create_memoir_v2_tables.sql``.
#
# V2-D 1.2 — these are BRAND-NEW tables, so they carry NO
# ``_ensure_postgres_columns`` entry: ``create_all`` owns them.
#
# MODEL-BOUND CHECK CONSTRAINTS (V2-D 1.5): the CHECK constraints are
# bound to the MODELS, not only to the ``.sql`` — on PG the tables are
# built by ``create_all`` FROM THE MODELS (the runner is SQLite-only, so
# the ``.sql`` never runs there). SQLModel's ``Field()`` does not emit
# CHECK, so without ``__table_args__`` the PG tables would carry no
# vocabulary constraint at all.
#
# TWO-AXIS TIME (V2-D 1.6): ``created_at`` is the WRITE time; the
# ``first_seen_at`` / ``last_touched_at`` / ``first_mentioned_at`` /
# ``last_mentioned_at`` family is the EVENT time. The past can be
# written about late, and the constellation's temporal queries run on
# the event axis.
#
# 3-SITE INDEX LOCKSTEP: the index names AND column lists are
# byte-identical to the companion ``.sql`` migration and to the schema
# pack's parity assertion.


class MemoirStar(SQLModel, table=True):
    """One canonical entity node of the Constellation.

    A star is the CANONICAL surface form of an entity — "the Emperor",
    not "my dear". Alternative surface forms live in
    :class:`MemoirStarAlias` and resolve back to this row.

    ``heat`` is the accumulated-recall accumulator (never negative —
    the ``heat >= 0.0`` CHECK is the data's guarantee). ``description``
    carries the LightRAG summarization, and ``description_as_of`` is
    its 24h cooldown watermark (V2-D 1.7): ``last_touched_at`` moves on
    every mention and therefore cannot carry the summarization time.
    """

    __tablename__ = "memoir_star"

    __table_args__ = (
        CheckConstraint(
            "star_type IN ('person', 'project', 'place', 'concept', 'domain')",
            name="ck_memoir_star_star_type",
        ),
        CheckConstraint("heat >= 0.0", name="ck_memoir_star_heat"),
        UniqueConstraint(
            "agent_id", "name", name="uq_memoir_star_agent_name"
        ),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    agent_id: str = Field(sa_column=Column(Text, nullable=False))
    name: str = Field(sa_column=Column(Text, nullable=False))
    star_type: str = Field(sa_column=Column(Text, nullable=False))
    description: str = Field(
        default="", sa_column=Column(Text, nullable=False, server_default=text("''"))
    )
    description_as_of: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    first_seen_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    last_touched_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    heat: float = Field(
        default=0.0, sa_column=Column(REAL, nullable=False, server_default="0.0")
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


# The DESC-ordered index cannot be expressed with the string column
# references ``__table_args__`` accepts — so it is declared here, after
# the class, against the live table columns. The name and the column
# list stay in 3-site lockstep with the .sql.
Index(
    "idx_memoir_star_agent_heat",
    MemoirStar.__table__.c.agent_id,
    MemoirStar.__table__.c.heat.desc(),
)


class MemoirStarAlias(SQLModel, table=True):
    """One alternative surface form bound to a canonical star.

    "my dear", "the Emperor", "Your Majesty" — every one of them is an
    ALIAS of exactly one canonical star, per agent. The
    ``uq_memoir_alias_agent_alias`` unique constraint is what makes the
    alias table an IDENTITY map rather than a suggestion list: one alias
    resolves to one star, forever.
    """

    __tablename__ = "memoir_star_alias"

    __table_args__ = (
        UniqueConstraint(
            "agent_id", "alias", name="uq_memoir_alias_agent_alias"
        ),
        Index("idx_memoir_star_alias_star", "star_id"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    agent_id: str = Field(sa_column=Column(Text, nullable=False))
    star_id: str = Field(
        sa_column=Column(Text, ForeignKey("memoir_star.id"), nullable=False)
    )
    alias: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class MemoirLine(SQLModel, table=True):
    """One weighted co-occurrence edge: this star, mentioned in this episode.

    ``mention_count`` counts how many times the star was mentioned in
    the episode. The CRASH-RECOVERY GUARD (SF2 / V2-29) is
    ``last_pass_id``: the id of the extraction pass that last touched
    the line. A pass that dies mid-extraction and re-runs with the SAME
    ``pass_id`` must NOT double-count, so the increment is applied only
    when ``last_pass_id IS DISTINCT FROM`` the incoming pass id.
    """

    __tablename__ = "memoir_line"

    __table_args__ = (
        CheckConstraint("mention_count > 0", name="ck_memoir_line_mention_count"),
        UniqueConstraint(
            "star_id", "episode_id", name="uq_memoir_line_star_episode"
        ),
        Index("idx_memoir_line_episode", "episode_id"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    agent_id: str = Field(sa_column=Column(Text, nullable=False))
    star_id: str = Field(
        sa_column=Column(Text, ForeignKey("memoir_star.id"), nullable=False)
    )
    episode_id: str = Field(
        sa_column=Column(Text, ForeignKey("memoir_episodes.id"), nullable=False)
    )
    mention_count: int = Field(
        default=1, sa_column=Column(Integer, nullable=False, server_default="1")
    )
    last_pass_id: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    first_mentioned_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    last_mentioned_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


# The second DESC-ordered index — declared after the class, against the
# live table columns (the ``__table_args__`` string form cannot express
# DESC). 3-site lockstep with the .sql.
Index(
    "idx_memoir_line_agent",
    MemoirLine.__table__.c.agent_id,
    MemoirLine.__table__.c.last_mentioned_at.desc(),
)


class MemoirReflectionState(SQLModel, table=True):
    """The durable nightly-reflection watermark and its claimant.

    ONE row per agent: ``last_nightly_date`` is the date of the last
    completed nightly reflection, and ``claimed_at`` is the timestamp of
    the claim on the CURRENT run (a crashed run leaves the claim behind
    so the next run can decide whether it is stale).
    """

    __tablename__ = "memoir_reflection_state"

    id: str = Field(sa_column=Column(Text, primary_key=True))
    last_nightly_date: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    claimed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )


class MemoirProposalLedger(SQLModel, table=True):
    """The durable keepsake-proposal and notification ledger (C3 / W2).

    The ledger is what makes "at most ONE notification per day" and
    "at most ONE proposal per episode" RESTART-SAFE: the state lives in
    the database, not in process memory.

    ``notification_status`` carries the CLOSED FOUR-TOKEN vocabulary
    (``pending`` / ``enqueued`` / ``sent`` / ``failed``). ``enqueued`` is
    the stamp that enforces the 1/day rule (the row is claimed BEFORE
    the notification is handed to the transport); ``sent`` is reserved
    for the future delivery confirmation. The vocabulary is bound to the
    MODEL (V2-D 1.5) and is TEST-ENFORCED — a fifth token is rejected
    by the database, not merely absent from the source.
    """

    __tablename__ = "memoir_proposal_ledger"

    __table_args__ = (
        CheckConstraint(
            "notification_status IN ('pending', 'enqueued', 'sent', 'failed')",
            name="ck_memoir_proposal_ledger_notification_status",
        ),
        UniqueConstraint(
            "agent_id", "episode_id", name="uq_memoir_proposal_agent_episode"
        ),
        Index("idx_memoir_proposal_ledger_date", "agent_id", "proposal_date"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    agent_id: str = Field(sa_column=Column(Text, nullable=False))
    episode_id: str = Field(
        sa_column=Column(Text, ForeignKey("memoir_episodes.id"), nullable=False)
    )
    proposal_date: str = Field(sa_column=Column(Text, nullable=False))
    proposed_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    notification_status: str = Field(sa_column=Column(Text, nullable=False))
    notified_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    reason: str = Field(
        default="", sa_column=Column(Text, nullable=False, server_default=text("''"))
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class MemoirJevLedger(SQLModel, table=True):
    """The durable JEV classification and candidate-retry ledger (B2).

    A JEV candidate that cannot be classified (or whose diff cannot be
    built) must NOT be silently dropped: the retry state lives here, so
    a daemon restart resumes the work instead of losing it.

    ``classification`` is NULLABLE — a candidate is parked BEFORE
    classification completes — and ``status`` carries the
    ``pending`` / ``drafted`` / ``suppressed`` / ``terminal`` vocabulary.
    """

    __tablename__ = "memoir_jev_ledger"

    __table_args__ = (
        CheckConstraint(
            "classification IS NULL OR classification IN ('conflict', "
            "'boundary_change', 'new_rule', 'reinforcement', 'none')",
            name="ck_memoir_jev_ledger_classification",
        ),
        CheckConstraint(
            "status IN ('pending', 'drafted', 'suppressed', 'terminal')",
            name="ck_memoir_jev_ledger_status",
        ),
        UniqueConstraint(
            "agent_id", "synthesis_id", name="uq_memoir_jev_synthesis"
        ),
        Index("idx_memoir_jev_status", "agent_id", "status"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    agent_id: str = Field(sa_column=Column(Text, nullable=False))
    synthesis_id: str = Field(
        sa_column=Column(Text, ForeignKey("memoir_syntheses.id"), nullable=False)
    )
    classification: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    attempts: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    last_error: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    status: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


# ── THE KEEPSAKE LINE (D5.7 / AM-24 / W-1) ───────────────────────────────
#: The ONE keepsake-line template. It LIVES IN THE REPOSITORY PACKAGE so
#: D2.12's admission arithmetic measures the SAME bytes the briefing
#: ships (W-1: admission used to measure the content alone and under-
#: counted every pin by the wrapper + ``pinned_by`` + the provenance
#: class + the uncapped ``pinned_reason`` — ≈+18 tokens/pin, so the
#: 4,000-token zone was claimed but not enforced).
#: ``daemon/services/memoir_briefing.py`` imports it FROM HERE — the
#: bytes are pinned by the briefing pack; change them here and BOTH the
#: briefing pack and the admission pack go RED.
KEEPSAKE_LINE_TEMPLATE = (
    "- [keepsake] {title} (pinned_by={pinned_by}; "
    "provenance={pclass}) {pinned_reason}"
)

#: The placeholder title the briefing renders when an episode has none.
MEMOIR_UNTITLED_TITLE = "(untitled)"

#: The provenance class admission MEASURES with. The briefing's
#: ``pclass`` is NOT row state — it is captured ONCE per briefing build
#: from the reader's manager (AM-24), so admission cannot know it. It
#: therefore measures the WORST CASE — the longest class the briefing
#: can render (``direct`` | ``inherited`` | ``""``) — so the 4,000-token
#: zone bound holds for EVERY future read, not just the ones whose
#: reader happens to carry the empty class.
KEEPSAKE_ADMISSION_PCLASS = "inherited"


def _keepsake_one_line(value: str | None) -> str:
    """Collapse ONE field to ONE line (newlines are structure loss).

    The briefing's renderer applies the same normalization to the same
    fields, so admission and briefing measure identical bytes.
    """
    if value is None:
        return ""
    return " ".join(str(value).split())


def render_keepsake_line(
    *,
    title: str | None,
    pinned_by: str | None,
    pclass: str,
    pinned_reason: str | None,
) -> str:
    """Render ONE keepsake line from the byte-pinned template (D5.7).

    The SINGLE SOURCE of the rendered line: the briefing renders with
    this function (so a wrong ``pinned_by`` claim stays detectable at
    read time), and the pin admission MEASURES with it (D2.12/W-1 — the
    candidate's rendered line, not its content alone).
    """
    clean_title = _keepsake_one_line(title) or MEMOIR_UNTITLED_TITLE
    return KEEPSAKE_LINE_TEMPLATE.format(
        title=clean_title,
        pinned_by=(pinned_by or "unknown"),
        pclass=(pclass or ""),
        pinned_reason=_keepsake_one_line(pinned_reason),
    )


__all__ = [
    "KEEPSAKE_ADMISSION_PCLASS",
    "KEEPSAKE_LINE_TEMPLATE",
    "MEMOIR_UNTITLED_TITLE",
    "MemoirEpisode",
    "MemoirJevLedger",
    "MemoirLine",
    "MemoirPersona",
    "MemoirPersonaVersion",
    "MemoirProposalLedger",
    "MemoirReflectionState",
    "MemoirStar",
    "MemoirStarAlias",
    "MemoirSynthesis",
    "PERSONA_SOURCES",
    "PERSONA_STATUSES",
    "PERSONA_VALID_TRANSITIONS",
    "PIN_ACTORS",
    "StarsListType",
    "SYNTHESIS_VERBS",
    "render_keepsake_line",
]
