"""SQLModel table definitions for the Skill Evolution System.

Phase 1 of the Skill Evolution System. Six tables support the
full skill lifecycle, from import through A/B testing:

* :class:`Skill` — the skill document itself (content, category,
  generation, usage counters, A/B test grouping). Counter columns
  (``total_selections``, ``total_applied``, ``total_completions``,
  ``total_fallbacks``, ``consecutive_failures``) are bumped
  atomically by raw SQL via
  :meth:`SkillRepository.increment_counter`.

* :class:`SkillLineage` — append-only parent/child graph.
  Composite PK ``(skill_id, parent_skill_id)`` with cascading
  delete from the ``skills`` row. A skill's lineage forms a DAG
  where roots are ``lineage_origin='imported'`` /
  ``generation=0`` and descendants are created by mutation.

* :class:`SkillUsageRecord` — one row per skill application to a
  task. Captures selection / application / completion / fallback
  booleans plus timing data. ``feedback_applied`` is NULL until
  the user (or post-mortem service) marks feedback; True means
  the feedback was applied, False means recorded-but-not-applied.

* :class:`SkillTrigger` — declarative condition → action rules
  that match against incoming task messages. ``condition_json``
  is the rule body (type-specific). ``project_id IS NULL`` is a
  global trigger; otherwise it's project-scoped.

* :class:`SkillEmbedding` — one or more per-skill embeddings of
  common trigger queries. The embedding column is stored as a
  plain JSON array of floats — NOT BYTEA, NOT pickle — so the
  same schema works on both SQLite and PostgreSQL via
  :class:`~daemon.repositories.infra.types.JSONBType`.

* :class:`SkillABTest` — A/B test bucket grouping. The
  ``ab_test_group`` is the shared UUID across old + new variants;
  ``comparisons`` and ``extension_count`` are bumped atomically
  as feedback rolls in; ``winner_skill_id`` is set on
  :meth:`SkillABTestRepository.resolve`.

Notes
-----
* ``UniqueConstraint("project_id", "name", "generation")`` on
  :class:`Skill` lets multiple ``NULL`` ``project_id`` rows share
  a ``name`` — both PostgreSQL and SQLite treat NULLs as distinct
  in UNIQUE columns by default. If you need
  "exactly one global skill named X" you have to enforce that
  at the tool layer (or replace NULL with a sentinel string).
* All timestamps are ISO-8601 strings (``_now_iso()`` style) for
  cross-driver consistency.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlmodel import Field, PrimaryKeyConstraint, SQLModel

from daemon.repositories.infra.types import JSONBType


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string.

    Module-level helper so model ``default_factory`` lambdas stay
    short. Mirrors the project-wide pattern used by
    :class:`InfraAsset` and friends.
    """
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Models
# ============================================================


class Skill(SQLModel, table=True):
    """A skill document — the atomic unit of the skill system.

    Lifecycle:

    * Inserted as ``lineage_origin='imported'``, ``generation=0``
      by the import pipeline.
    * Mutated into a new generation row by the evolution pipeline
      (parent/child link via :class:`SkillLineage`).
    * Soft-deactivated via :meth:`SkillRepository.deactivate`
      (``is_active=False``, ``status='inactive'``) rather than
      hard-deleted, so usage history remains queryable.
    * Grouped with sibling variants via ``ab_test_group`` (shared
      UUID across the old + new rows). The active variant for a
      given ``(project_id, name)`` pair is the row with
      ``is_active=True`` — see
      :meth:`SkillRepository.get_active_variant`.

    Counters (``total_selections``, ``total_applied``,
    ``total_completions``, ``total_fallbacks``,
    ``consecutive_failures``) are bumped atomically via raw SQL in
    :meth:`SkillRepository.increment_counter` to avoid the
    read-modify-write race under concurrent workers.

    Attributes:
        id: UUID4 primary key. TEXT (not UUID type) for portability.
        project_id: Owning project. ``NULL`` is allowed for
            global skills.
        name: Human-readable name. Unique within
            ``(project_id, name, generation)``.
        description: One-line summary.
        content: The skill body — markdown / instructions consumed
            by agents at runtime.
        category: Free-form category string (default
            ``'workflow'``).
        is_active: Whether the skill is currently in the active
            set. Stored as INTEGER 0/1 — works on both SQLite
            and PostgreSQL.
        status: Lifecycle status (``'active'``, ``'inactive'``,
            ``'archived'``, …). Application-defined; the
            repository does not constrain it.
        lineage_origin: ``'imported'`` (root skills), or
            ``'evolved'`` / ``'feedback'`` for descendants.
        generation: ``0`` for imported skills; bumped by the
            evolution pipeline on each mutation.
        ab_test_group: Shared UUID across old + new variants
            during an A/B test. NULL means "not in an A/B test".
        total_selections: Times this skill was selected for use
            by the trigger resolver.
        total_applied: Times this skill was actually applied to
            a task (post-selection).
        total_completions: Times a task that applied this skill
            completed successfully.
        total_fallbacks: Times the skill execution fell back to
            a different path (skill was applied but didn't help
            the task progress).
        consecutive_failures: Rolling counter of consecutive
            task failures that touched this skill. Reset to 0 by
            the evolution pipeline on a successful application.
        created_at: ISO-8601 timestamp, immutable.
        updated_at: ISO-8601 timestamp, bumped on every
            :meth:`SkillRepository.update`.
        last_used_at: ISO-8601 timestamp of the most recent
            usage. ``NULL`` until first use.
    """

    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "name",
            "generation",
            name="uq_skills_project_name_gen",
        ),
        Index("ix_skills_project_id", "project_id"),
        Index("ix_skills_is_active", "is_active"),
        Index("ix_skills_ab_test_group", "ab_test_group"),
        Index("ix_skills_auto_load", "auto_load"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    project_id: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(sa_column=Column(String, nullable=False), max_length=256)
    description: str = Field(default="")
    content: str = Field(sa_column=Column(String, nullable=False))
    category: str = Field(default="workflow", max_length=64)
    is_active: bool = Field(default=True)
    status: str = Field(default="active", max_length=32)
    lineage_origin: str = Field(default="imported", max_length=32)
    generation: int = Field(default=0)
    ab_test_group: Optional[str] = Field(default=None, max_length=64)
    # Phase 2 (skill evolution): auto_load + source_skill_bank_id.
    # ``auto_load`` is the clone-side counterpart of the skill_bank
    # template flag: True means the skill is loaded into the system
    # prompt before every task (vs on-demand only). ``source_skill_bank_id``
    # records the skill_bank template ID this row was cloned from
    # (NULL for manually-created or evolved skills — soft FK only,
    # not enforced).
    auto_load: bool = Field(
        default=False,
        description=(
            "Whether this skill is auto-loaded into the system "
            "prompt before every task. False = on-demand only."
        ),
    )
    source_skill_bank_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "If cloned from a skill_bank template, the bank item ID. "
            "NULL for manually created or evolved skills."
        ),
    )

    # Counter columns. Stored as INTEGER (no Python type coercion
    # needed — ``int`` round-trips on both SQLite and PostgreSQL).
    # Bumped via raw-SQL UPDATE in
    # :meth:`SkillRepository.increment_counter` to avoid the
    # read-modify-write race.
    total_selections: int = Field(default=0)
    total_applied: int = Field(default=0)
    total_completions: int = Field(default=0)
    total_fallbacks: int = Field(default=0)
    consecutive_failures: int = Field(default=0)

    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    last_used_at: Optional[str] = Field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description,
            "content": self.content,
            "category": self.category,
            "is_active": self.is_active,
            "status": self.status,
            "lineage_origin": self.lineage_origin,
            "generation": self.generation,
            "ab_test_group": self.ab_test_group,
            "auto_load": self.auto_load,
            "source_skill_bank_id": self.source_skill_bank_id,
            "total_selections": self.total_selections,
            "total_applied": self.total_applied,
            "total_completions": self.total_completions,
            "total_fallbacks": self.total_fallbacks,
            "consecutive_failures": self.consecutive_failures,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
        }


class SkillLineage(SQLModel, table=True):
    """Append-only parent/child graph for skill evolution.

    A new row is inserted every time the evolution pipeline
    mutates an existing skill into a new generation. The
    ``(skill_id, parent_skill_id)`` composite PK enforces no
    duplicate edges in the lineage DAG.

    ON DELETE CASCADE on both FKs means deleting a skill row
    also deletes its lineage rows — lineage is a derived
    projection of the skills graph, not a primary source.

    Attributes:
        skill_id: The descendant skill (the new generation).
        parent_skill_id: The ancestor skill (the previous
            generation). Equal to ``skill_id`` is rejected at the
            table level (composite PK still permits it — the
            evolution pipeline is responsible for refusing
            self-edges).
        change_summary: One-line description of what changed
            (``"tightened fallback logic"``, …).
        content_diff: Unified diff of the content body. Stored
            as a plain string (TEXT) rather than JSON — diffs are
            always strings.
        created_at: ISO-8601 timestamp, immutable.
    """

    __tablename__ = "skill_lineage"
    __table_args__ = (
        PrimaryKeyConstraint("skill_id", "parent_skill_id", name="pk_skill_lineage"),
        Index("ix_skill_lineage_skill_id", "skill_id"),
        Index("ix_skill_lineage_parent_skill_id", "parent_skill_id"),
    )

    skill_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("skills.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    parent_skill_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("skills.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    change_summary: str = Field(default="")
    content_diff: str = Field(default="")
    created_at: str = Field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "skill_id": self.skill_id,
            "parent_skill_id": self.parent_skill_id,
            "change_summary": self.change_summary,
            "content_diff": self.content_diff,
            "created_at": self.created_at,
        }


class SkillUsageRecord(SQLModel, table=True):
    """One row per skill usage event.

    Captures the four signal booleans the evolution pipeline
    cares about: ``selected``, ``applied``, ``task_succeeded``,
    ``fallback``. A typical lifecycle is::

        insert(selected=True, applied=False, ...)
        ... task runs ...
        update(applied=True, iterations=N, duration_seconds=D)
        ... post-mortem ...
        update_feedback(applied=True, note="...")

    The ``(instance_id, feedback_applied)`` composite index
    supports the "show me all skills applied to this instance
    that received feedback" query used by the feedback rollup
    service.

    Attributes:
        id: UUID4 primary key.
        skill_id: The skill that was used. ``ON DELETE CASCADE``
            so a skill wipe cleans up its history.
        project_id: Owning project. Denormalized for fast
            per-project usage stats (no join to ``skills``
            required).
        instance_id: The instance that triggered the skill.
        agent_id: The agent that consumed the skill.
        task_message: Optional snapshot of the triggering task
            message (first 1–2 KB usually — kept short).
        selected: True iff the skill was selected by the
            trigger resolver (passed the rule match).
        applied: True iff the skill was actually loaded into the
            agent's prompt and used. A skill can be
            selected-but-not-applied if budget / context-window
            guards rejected it.
        task_succeeded: True iff the task that used this skill
            ultimately succeeded.
        iterations: Number of graph iterations the task took
            while this skill was active.
        duration_seconds: Wall-clock seconds the task spent
            while this skill was active.
        fallback: True iff the skill execution fell back to a
            different path (the skill didn't help the task
            progress).
        feedback_applied: ``NULL`` = not yet recorded,
            ``True`` = recorded-and-applied, ``False`` =
            recorded-but-not-applied. Stored as a nullable
            boolean rather than a separate "feedback_recorded"
            column to keep the row count low.
        feedback_note: Optional free-form note attached to the
            feedback (typically from the user).
        feedback_usefulness: Agent-judged quality score 1–10
            (1 = unusable/harmful, 10 = excellent and directly
            helpful). ``NULL`` when the agent did not provide
            a numeric rating — distinct from ``feedback_note``,
            which is the free-form context observation.
        feedback_improvement: Actionable suggestions for
            improving the skill content itself (e.g. "Should
            mention PACKS.md location", "Add example of timeout
            checklist"). Distinct from ``feedback_note`` which is
            general context. Feeds the skill-keeper evolution loop.
        created_at: ISO-8601 timestamp of the usage event.
    """

    __tablename__ = "skill_usage_records"
    __table_args__ = (
        Index("ix_skill_usage_records_skill_id", "skill_id"),
        Index("ix_skill_usage_records_instance_id", "instance_id"),
        Index("ix_skill_usage_records_instance_feedback", "instance_id", "feedback_applied"),
        Index("ix_skill_usage_records_ab_group", "ab_test_group"),
        Index("ix_skill_usage_records_skill_created", "skill_id", "created_at"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    skill_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("skills.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    project_id: str = Field(sa_column=Column(String, nullable=False), max_length=64)
    instance_id: str = Field(sa_column=Column(String, nullable=False), max_length=64)
    agent_id: str = Field(sa_column=Column(String, nullable=False), max_length=64)

    task_message: Optional[str] = Field(default=None)
    selected: bool = Field(default=False)
    applied: bool = Field(default=False)
    task_succeeded: bool = Field(default=False)
    iterations: int = Field(default=0)
    duration_seconds: int = Field(default=0)
    fallback: bool = Field(default=False)
    feedback_applied: Optional[bool] = Field(default=None)
    feedback_note: Optional[str] = Field(default=None)

    created_at: str = Field(default_factory=_now_iso)

    # Phase: Skill-worker milestone. Both columns follow the dual-driver
    # CREATE pattern: declared here on the model, added to existing SQLite
    # databases via the .sql migration in
    # ``daemon/migrations/versions/20260715_000001_skill_usage_new_columns.sql``,
    # and added to existing PostgreSQL databases via the ALTER statements
    # in ``daemon/manager.py::_ensure_postgres_columns``. Fresh databases
    # of either flavor pick up the columns from SQLModel.metadata.create_all.
    ab_test_group: Optional[str] = Field(
        default=None,
        max_length=64,
        sa_column=Column(Text, nullable=True),
        description=(
            "A/B test period isolation. NULL = 'not under test' "
            "(excluded from A/B-scoped queries). Set to the same UUID "
            "as the skills.ab_test_group for usage rows collected during "
            "an A/B comparison window."
        ),
    )
    superseded: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, default=False),
        description=(
            "Marks usage records as superseded when a worker is reused "
            "with a new skill (e.g. A/B test promotion, hot-swap). "
            "Superseded rows are excluded from the standard "
            "completion-rate aggregation but remain queryable for audit."
        ),
    )

    # Phase: skill_feedback usefulness + improvement scoring (2026-07-21).
    # Both columns follow the dual-driver CREATE pattern: declared here on
    # the model, added to existing SQLite databases via the .sql migration in
    # ``daemon/migrations/versions/20260721_000001_skill_usage_feedback_columns.sql``,
    # and added to existing PostgreSQL databases via the ALTER statements in
    # ``daemon/manager.py::_ensure_postgres_columns``. Fresh databases of
    # either flavor pick up the columns from SQLModel.metadata.create_all.
    #
    # ``feedback_usefulness`` is the agent-judged quality score (1-10) the
    # ``skill_feedback`` tool collects. ``feedback_improvement`` is the
    # actionable suggestion text — distinct from ``feedback_note`` which is
    # the general context observation. Together they feed the skill-keeper
    # evolution loop and the per-skill usefulness rollup.
    feedback_usefulness: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
        description=(
            "Agent-judged quality score 1-10 (1=unusable/harmful, "
            "10=excellent and directly helpful). NULL when the agent did "
            "not provide a numeric rating. Optional but encouraged — feeds "
            "the per-skill usefulness rollup and the skill-keeper evolution "
            "loop."
        ),
    )
    feedback_improvement: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description=(
            "Actionable suggestions for improving the skill content itself "
            "(e.g. 'Should mention PACKS.md location', 'Add example of "
            "timeout checklist'). Distinct from feedback_note which is "
            "general context observation. Feeds the skill-keeper evolution "
            "loop directly."
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "id": self.id,
            "skill_id": self.skill_id,
            "project_id": self.project_id,
            "instance_id": self.instance_id,
            "agent_id": self.agent_id,
            "task_message": self.task_message,
            "selected": self.selected,
            "applied": self.applied,
            "task_succeeded": self.task_succeeded,
            "iterations": self.iterations,
            "duration_seconds": self.duration_seconds,
            "fallback": self.fallback,
            "feedback_applied": self.feedback_applied,
            "feedback_note": self.feedback_note,
            "feedback_usefulness": self.feedback_usefulness,
            "feedback_improvement": self.feedback_improvement,
            "created_at": self.created_at,
            "ab_test_group": self.ab_test_group,
            "superseded": self.superseded,
        }


class SkillTrigger(SQLModel, table=True):
    """A condition → action rule for the skill trigger resolver.

    ``condition_json`` is the rule body, type-specific (e.g.
    ``{"keyword": "deploy"}`` for keyword triggers,
    ``{"regex": "^run\\s+tests?$"}`` for regex triggers,
    ``{"embedding_match": {"threshold": 0.85}}`` for embedding
    triggers). Stored as JSONB via
    :class:`~daemon.repositories.infra.types.JSONBType` so the
    same schema works on both SQLite and PostgreSQL.

    ``project_id IS NULL`` is a GLOBAL trigger — the resolver
    applies it to every project. Non-null is a project-scoped
    trigger. The composite is intentional: the global set
    defines baseline behavior, the per-project set layers
    customizations on top.

    Attributes:
        id: UUID4 primary key.
        project_id: Owning project. ``NULL`` = global.
        name: Human-readable name (not unique; duplicates
            allowed so projects can have variants).
        condition_type: Discriminator for ``condition_json``
            shape (``"keyword"``, ``"regex"``,
            ``"embedding_match"``, …).
        condition_json: Rule body. Type-specific; see class
            docstring.
        action: Free-form action string
            (``"select_skill:workflow-debug"``,
            ``"request_clarification"``, …).
        is_enabled: Soft-disable switch — disabled triggers are
            skipped by the resolver but the row is preserved
            for audit.
        created_at: ISO-8601 timestamp, immutable.
    """

    __tablename__ = "skill_triggers"

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    project_id: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(sa_column=Column(String, nullable=False), max_length=256)
    condition_type: str = Field(sa_column=Column(String, nullable=False), max_length=64)
    condition_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("condition_json", JSONBType, nullable=False),
    )
    action: str = Field(sa_column=Column(String, nullable=False), max_length=512)
    is_enabled: bool = Field(default=True)
    created_at: str = Field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "condition_type": self.condition_type,
            "condition_json": dict(self.condition_json) if self.condition_json else {},
            "action": self.action,
            "is_enabled": self.is_enabled,
            "created_at": self.created_at,
        }


class SkillEmbedding(SQLModel, table=True):
    """Cached per-skill embedding of a common trigger query.

    Pre-computes the vector for high-frequency trigger queries so
    the resolver can do a batched similarity scan instead of an
    LLM embedding call per incoming task.

    The ``embedding`` column is a plain JSON array of floats,
    stored via :class:`~daemon.repositories.infra.types.JSONBType`.
    We deliberately avoid ``BYTEA`` / ``numpy`` / ``pickle`` so the
    same schema works on both SQLite and PostgreSQL without any
    binary serialization layer — the project standard for
    JSON-shaped columns.

    Attributes:
        id: UUID4 primary key.
        skill_id: The skill this embedding is for. ``ON DELETE
            CASCADE`` so removing a skill clears its cached
            embeddings.
        trigger_query: The source query text (a high-frequency
            phrase like ``"deploy to staging"``).
        embedding: Vector as a list of floats. Length depends on
            the embedding model in use (typically 1536 for
            OpenAI ``text-embedding-3-small``).
        created_at: ISO-8601 timestamp, immutable.
    """

    __tablename__ = "skill_embeddings"
    __table_args__ = (Index("ix_skill_embeddings_skill_id", "skill_id"),)

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    skill_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("skills.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    trigger_query: str = Field(sa_column=Column(String, nullable=False), max_length=512)
    embedding: list[float] = Field(
        default_factory=list,
        sa_column=Column("embedding", JSONBType, nullable=False),
    )
    created_at: str = Field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "id": self.id,
            "skill_id": self.skill_id,
            "trigger_query": self.trigger_query,
            "embedding": list(self.embedding) if self.embedding else [],
            "created_at": self.created_at,
        }


class SkillABTest(SQLModel, table=True):
    """A/B test bucket grouping old + new skill variants.

    The ``ab_test_group`` is a shared UUID across the two
    variants (the "old" skill and the "new" candidate skill).
    Every time a feedback signal is recorded for either variant,
    ``comparisons`` is bumped. When the test is extended (more
    data needed), ``extension_count`` is bumped. When the test
    is resolved, ``resolved_at`` is set and ``winner_skill_id``
    points to the chosen variant.

    Both ``comparisons`` and ``extension_count`` are bumped
    atomically via raw SQL in
    :meth:`SkillABTestRepository.increment_comparison` /
    :meth:`SkillABTestRepository.increment_extension` to avoid
    the read-modify-write race under concurrent feedback
    ingestion.

    Attributes:
        id: UUID4 primary key.
        ab_test_group: Shared UUID across the old + new variants.
            Indexed for the "all tests for this group" lookup.
        skill_id_old: The incumbent skill (baseline).
        skill_id_new: The candidate skill being tested.
        extension_count: Number of times the test has been
            extended to gather more data. Bumped when
            statistical-significance thresholds aren't met at
            the planned end-of-test deadline.
        comparisons: Number of side-by-side feedback signals
            recorded across both variants. Used as the
            denominator for the comparison-rate metric.
        created_at: ISO-8601 timestamp, immutable.
        resolved_at: ISO-8601 timestamp of resolution. ``NULL``
            while the test is still running.
        winner_skill_id: The chosen variant after resolution.
            ``NULL`` while the test is running; must be one of
            ``skill_id_old`` / ``skill_id_new`` after
            resolution (enforced at the tool layer, not the DB).
    """

    __tablename__ = "skill_ab_tests"
    __table_args__ = (Index("ix_skill_ab_tests_group", "ab_test_group"),)

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    ab_test_group: str = Field(sa_column=Column(String, nullable=False), max_length=64)
    skill_id_old: str = Field(
        sa_column=Column(
            String,
            ForeignKey("skills.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    skill_id_new: str = Field(
        sa_column=Column(
            String,
            ForeignKey("skills.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    extension_count: int = Field(default=0)
    comparisons: int = Field(default=0)
    created_at: str = Field(default_factory=_now_iso)
    resolved_at: Optional[str] = Field(default=None)
    winner_skill_id: Optional[str] = Field(
        default=None,
        sa_column=Column(
            String,
            ForeignKey("skills.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "id": self.id,
            "ab_test_group": self.ab_test_group,
            "skill_id_old": self.skill_id_old,
            "skill_id_new": self.skill_id_new,
            "extension_count": self.extension_count,
            "comparisons": self.comparisons,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "winner_skill_id": self.winner_skill_id,
        }


class SkillBankItem(SQLModel, table=True):
    """A skill stored in the Skill Bank — a user-managed template.

    Isolated from the skill evolution system: no FK to ``skills``,
    no counters, no lineage, no triggers, no embeddings. Pure
    user-facing CRUD storage.

    Attributes:
        id: UUID4 primary key (TEXT for dual-driver portability).
        project_id: Owning project. ``NULL`` = global/shared.
        name: Human-readable name (NOT unique — duplicates allowed).
        description: One-line summary (default empty string).
        content: The skill body — markdown / instructions.
        category: Free-form category string (default ``'workflow'``).
        created_at: ISO-8601 timestamp, immutable.
        updated_at: ISO-8601 timestamp, bumped on every update.
    """

    __tablename__ = "skill_bank"
    __table_args__ = (
        Index("ix_skill_bank_project_id", "project_id"),
        Index("ix_skill_bank_agent_id", "agent_id"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    project_id: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(sa_column=Column(String, nullable=False), max_length=256)
    description: str = Field(default="")
    content: str = Field(sa_column=Column(String, nullable=False))
    category: str = Field(default="workflow", max_length=64)
    # Phase 2 (skill evolution): template_version + agent_id + auto_load.
    # ``template_version`` is bumped when the skills-template source file
    # changes so startup seeding can detect and refresh stale bank copies.
    # ``agent_id`` scopes the template to one agent (e.g. 'tester');
    # NULL means generic/shared. ``auto_load`` is the source-of-truth
    # flag from the skill-set.yaml (legacy .md) definition: when True, skills cloned
    # from this template inherit auto_load=True (loaded into the system
    # prompt before every task, not on demand).
    template_version: str = Field(
        default="1.0.0",
        max_length=32,
        description=(
            "Semver version of this template. Bumped when the "
            "skills-template source file changes so startup "
            "seeding can detect and refresh stale bank copies."
        ),
    )
    agent_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "Agent this template belongs to (e.g. 'tester'). "
            "NULL means generic/shared template."
        ),
    )
    auto_load: bool = Field(
        default=False,
        description=(
            "Whether skills cloned from this template should have "
            "auto_load=true (loaded into system prompt before every "
            "task). Source of truth from skill-set.yaml (legacy .md) definition."
        ),
    )
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description,
            "content": self.content,
            "category": self.category,
            "template_version": self.template_version,
            "agent_id": self.agent_id,
            "auto_load": self.auto_load,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }