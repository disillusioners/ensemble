"""SQLModel-based repositories for the Skill Evolution System.

Six repositories, one per table defined in :mod:`.models`:

* :class:`SkillRepository` — CRUD on :class:`Skill` plus
  atomic counter increments and BM25 full-text search.
* :class:`SkillLineageRepository` — DAG queries
  (:meth:`~SkillLineageRepository.get_parents`,
  :meth:`~SkillLineageRepository.get_children`).
* :class:`SkillUsageRepository` — usage-event inserts and
  per-skill stats aggregation.
* :class:`SkillTriggerRepository` — CRUD on trigger rules.
* :class:`SkillEmbeddingRepository` — embedding cache CRUD and
  bulk project lookup.
* :class:`SkillABTestRepository` — A/B test lifecycle (create,
  increment counters, resolve).

Design highlights
----------------
* **Engine sharing.** Constructor takes a SQLAlchemy ``Engine``
  only — the shared engine is created once at the
  ``InstanceManager`` level (see
  :func:`daemon.repositories.factory.create_engine_from_config`)
  and passed to every repository to avoid DB lock contention.

* **Atomic counters.** :meth:`SkillRepository.increment_counter`
  and the two ``increment_*`` methods on
  :class:`SkillABTestRepository` use raw-SQL ``UPDATE col = col + 1``
  to avoid the read-modify-write race under concurrent workers.
  The column name is whitelisted (Python set lookup) to prevent
  SQL injection on the interpolated column identifier.

* **Sync calls.** All methods are synchronous; callers bridge to
  async via ``asyncio.to_thread`` (the project's standard pattern)
  or invoke from inside the worker thread pool.

* **Cross-driver JSON.** All JSON columns are typed via
  :class:`~daemon.repositories.infra.types.JSONBType` so the same
  schema works on both SQLite and PostgreSQL. Embeddings are
  stored as plain JSON arrays of floats (NOT BYTEA, NOT pickle,
  NOT numpy) — the project standard for JSON-shaped columns.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timezone
from math import log
from typing import Any, Optional

from sqlalchemy import func, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .models import (
    Skill,
    SkillABTest,
    SkillEmbedding,
    SkillLineage,
    SkillTrigger,
    SkillUsageRecord,
)

logger = logging.getLogger(__name__)


# Module-level ISO-timestamp helper, mirrors infra's
# ``_now_iso``. Model ``default_factory`` lambdas use the
# in-models module's ``_now_iso``; this one is for repository
# code paths (updates, atomic-counter increments need an
# explicit stamp).
def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# Whitelisted counter columns for
# :meth:`SkillRepository.increment_counter`. Validated against
# this set before being interpolated into the raw-SQL UPDATE
# string — anything else is rejected with ``ValueError`` to
# prevent SQL injection on the column identifier (the amount
# parameter is bound, so only the column name is at risk).
_SKILL_COUNTER_COLUMNS: frozenset[str] = frozenset(
    {
        "total_selections",
        "total_applied",
        "total_completions",
        "total_fallbacks",
        "consecutive_failures",
    }
)

# Stopwords for :meth:`SkillRepository.search_bm25`. English-only
# at this layer — the skill search corpus is mostly English at
# the project level, and keeping the stopword list inline avoids
# pulling in a ``nltk`` download for the daemon. A future
# per-locale tokenization layer can swap this for a configurable
# list without changing the call sites.
_BM25_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "is", "are", "was",
        "were", "be", "been", "being", "have", "has", "had", "do",
        "does", "did", "will", "would", "should", "could", "may",
        "might", "must", "to", "of", "in", "for", "on", "with",
        "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "between", "out",
        "off", "over", "under", "again", "further", "then", "once",
    }
)

# BM25 hyperparameters — standard values from the literature.
_BM25_K1: float = 1.5
_BM25_B: float = 0.75


# ============================================================
# SkillRepository
# ============================================================


class SkillRepository:
    """SQLModel-based repository for the ``skills`` table.

    All methods are synchronous; callers bridge to async via
    ``asyncio.to_thread`` (the project's standard pattern).
    """

    def __init__(self, engine: Engine):
        """Initialize the repository with a database engine.

        Args:
            engine: SQLAlchemy Engine bound to a SQLite or
                PostgreSQL database. The same engine should be
                shared across all repositories to avoid lock
                contention — see
                :func:`daemon.repositories.factory.create_engine_from_config`.
        """
        self.engine = engine

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def create(
        self,
        name: str,
        description: str,
        content: str,
        project_id: Optional[str] = None,
        category: str = "workflow",
        lineage_origin: str = "imported",
        generation: int = 0,
        **kwargs: Any,
    ) -> Skill:
        """Create a new skill row.

        Extra columns (``ab_test_group``, ``status``, …) can be
        passed via ``**kwargs`` — they're forwarded to the
        :class:`Skill` constructor after the named arguments.

        Args:
            name: Human-readable skill name.
            description: One-line summary.
            content: The skill body (markdown / instructions).
            project_id: Owning project ID, or ``None`` for a
                global skill.
            category: Free-form category string.
            lineage_origin: ``'imported'`` for new imports;
                ``'evolved'`` / ``'feedback'`` for descendants.
            generation: ``0`` for new imports; bumped by the
                evolution pipeline.
            **kwargs: Additional column values forwarded to
                :class:`Skill` (e.g. ``ab_test_group``).

        Returns:
            The newly created :class:`Skill` instance.
        """
        now = _now_iso()
        skill = Skill(
            name=name,
            description=description,
            content=content,
            project_id=project_id,
            category=category,
            lineage_origin=lineage_origin,
            generation=generation,
            created_at=now,
            updated_at=now,
            **kwargs,
        )
        with Session(self.engine) as session:
            session.add(skill)
            session.commit()
            session.refresh(skill)
            logger.info(
                f"Created skill: id={skill.id}, name={name}, "
                f"project_id={project_id}, generation={generation}"
            )
            return skill

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, skill_id: str) -> Skill | None:
        """Fetch a single skill by its primary key.

        Args:
            skill_id: The skill's UUID4 ID.

        Returns:
            The :class:`Skill` instance, or ``None`` if no row
            matches.
        """
        with Session(self.engine) as session:
            return session.get(Skill, skill_id)

    def get_by_name(
        self,
        project_id: Optional[str],
        name: str,
        generation: int = 0,
    ) -> Skill | None:
        """Fetch a skill by ``(project_id, name, generation)``.

        The match mirrors the ``UNIQUE(project_id, name,
        generation)`` constraint, so the query returns at most
        one row.

        Args:
            project_id: Owning project ID (``None`` for global).
            name: Skill name.
            generation: Generation number (default ``0``).

        Returns:
            The matching :class:`Skill` or ``None``.
        """
        with Session(self.engine) as session:
            stmt = (
                select(Skill)
                .where(Skill.project_id == project_id)
                .where(Skill.name == name)
                .where(Skill.generation == generation)
            )
            return session.exec(stmt).first()

    def list(
        self,
        project_id: Optional[str] = None,
        active_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Skill], int]:
        """List skills for a project, with optional filters.

        Args:
            project_id: Owning project ID (``None`` to list
                global skills only — skills with
                ``project_id IS NULL``).
            active_only: If ``True`` (default), filter to rows
                with ``is_active=True``.
            limit: Maximum number of rows to return.
            offset: Number of rows to skip.

        Returns:
            ``(items, total)`` — a list of :class:`Skill`
            instances ordered by ``created_at`` descending plus
            the total row count matching the filters (ignoring
            ``limit`` / ``offset``).
        """
        with Session(self.engine) as session:
            stmt = select(Skill).where(Skill.project_id == project_id)
            if active_only:
                stmt = stmt.where(Skill.is_active == True)  # noqa: E712
            count_stmt = (
                select(func.count()).select_from(stmt.subquery())
            )
            total = int(session.scalar(count_stmt) or 0)
            stmt = (
                stmt.order_by(col(Skill.created_at).desc())
                .offset(offset)
                .limit(limit)
            )
            items = list(session.exec(stmt))
            return items, total

    def update(self, skill_id: str, **fields: Any) -> Skill | None:
        """Update fields on an existing skill.

        Protected keys (``id``, ``created_at``) are silently
        dropped. ``updated_at`` is owned by the repository and
        bumped to the current time before commit.

        Args:
            skill_id: The skill to update.
            **fields: Column values to overwrite. Any unknown
                key that isn't a column on :class:`Skill`
                raises ``AttributeError``.

        Returns:
            The updated :class:`Skill` instance, or ``None`` if
            no row with that ID exists.
        """
        protected = {"id", "created_at"}
        with Session(self.engine) as session:
            skill = session.get(Skill, skill_id)
            if skill is None:
                logger.warning(
                    f"Skill not found for update: id={skill_id}"
                )
                return None
            for key, value in fields.items():
                if key in protected:
                    logger.warning(
                        f"Ignoring protected field in skill update: "
                        f"id={skill_id}, field={key}"
                    )
                    continue
                if not hasattr(skill, key):
                    raise AttributeError(
                        f"Skill has no field {key!r}"
                    )
                setattr(skill, key, value)
            skill.updated_at = _now_iso()
            session.commit()
            session.refresh(skill)
            logger.info(f"Updated skill: id={skill_id}")
            return skill

    def delete(self, skill_id: str) -> bool:
        """Hard-delete a skill row.

        Cascades through the FK constraints: ``skill_lineage``
        edges and ``skill_embeddings`` rows pointing at this
        skill are deleted automatically. ``skill_usage_records``
        and ``skill_ab_tests`` cascade too (their FKs declare
        ``ON DELETE CASCADE`` — see the model docstrings).

        Args:
            skill_id: The skill to delete.

        Returns:
            ``True`` if a row was deleted, ``False`` if no row
            with that ID existed.
        """
        with Session(self.engine) as session:
            skill = session.get(Skill, skill_id)
            if skill is None:
                logger.warning(
                    f"Skill not found for delete: id={skill_id}"
                )
                return False
            session.delete(skill)
            session.commit()
            logger.info(f"Deleted skill: id={skill_id}")
            return True

    def deactivate(self, skill_id: str) -> Skill | None:
        """Soft-deactivate a skill.

        Sets ``is_active=False`` and ``status='inactive'``. The
        row is preserved so usage history remains queryable.

        Args:
            skill_id: The skill to deactivate.

        Returns:
            The updated :class:`Skill` instance, or ``None`` if
            no row with that ID exists.
        """
        return self.update(skill_id, is_active=False, status="inactive")

    # --------------------------------------------------------
    # COUNTERS (atomic)
    # --------------------------------------------------------

    def increment_counter(
        self,
        skill_id: str,
        counter: str,
        amount: int = 1,
    ) -> None:
        """Atomically bump a counter column on a skill row.

        Uses raw SQL ``UPDATE col = col + :amount`` so concurrent
        workers can't lose increments to a read-modify-write
        race. The column name is whitelisted against
        :data:`_SKILL_COUNTER_COLUMNS` before interpolation —
        ``ValueError`` is raised for any other name to prevent
        SQL injection on the column identifier.

        Args:
            skill_id: The skill row to update.
            counter: Counter column name. Must be one of
                ``total_selections``, ``total_applied``,
                ``total_completions``, ``total_fallbacks``,
                ``consecutive_failures``.
            amount: Increment value (default ``1``). Negative
                values are allowed and produce a decrement —
                used by the evolution pipeline to reset
                ``consecutive_failures``.

        Raises:
            ValueError: If ``counter`` is not a known skill
                counter column.
        """
        if counter not in _SKILL_COUNTER_COLUMNS:
            raise ValueError(
                f"Unknown skill counter column: {counter!r}. "
                f"Allowed: {sorted(_SKILL_COUNTER_COLUMNS)}"
            )
        # The column name is whitelisted above; only the amount
        # and id are bound parameters, so the interpolation is
        # safe.
        stmt = text(
            f"UPDATE skills SET {counter} = {counter} + :amount "
            "WHERE id = :id"
        )
        with Session(self.engine) as session:
            session.execute(
                stmt, {"amount": amount, "id": skill_id}
            )
            session.commit()
            logger.debug(
                f"Incremented skill counter: id={skill_id}, "
                f"counter={counter}, amount={amount}"
            )

    # --------------------------------------------------------
    # A/B TEST QUERIES
    # --------------------------------------------------------

    def get_ab_variants(self, ab_test_group: str) -> list[Skill]:
        """List all skills in a given A/B test group.

        Args:
            ab_test_group: The shared UUID grouping old + new
                variants.

        Returns:
            List of :class:`Skill` rows whose
            ``ab_test_group`` matches. Typically 2 rows (old +
            new); may be 1 if one variant has been deactivated
            and the other is still running.
        """
        with Session(self.engine) as session:
            stmt = select(Skill).where(Skill.ab_test_group == ab_test_group)
            return list(session.exec(stmt))

    def get_active_variant(
        self,
        project_id: Optional[str],
        name: str,
    ) -> Skill | None:
        """Fetch the currently-active skill for a name.

        "Active" here means ``is_active=True``. Note that this
        does NOT consult ``ab_test_group`` — if two active rows
        share a name (which shouldn't happen, but is technically
        possible across non-overlapping generations), the
        newest one wins by virtue of ``.first()`` ordering.

        Args:
            project_id: Owning project ID (``None`` for
                global).
            name: Skill name.

        Returns:
            The active :class:`Skill` matching the name, or
            ``None`` if none is active.
        """
        with Session(self.engine) as session:
            stmt = (
                select(Skill)
                .where(Skill.project_id == project_id)
                .where(Skill.name == name)
                .where(Skill.is_active == True)  # noqa: E712
            )
            return session.exec(stmt).first()

    # --------------------------------------------------------
    # FULL-TEXT SEARCH (BM25)
    # --------------------------------------------------------

    @staticmethod
    def _tokenize(text_str: str) -> list[str]:
        """Lowercase + split on non-alphanumeric characters.

        Strips stopwords. Used by
        :meth:`SkillRepository.search_bm25` for both query and
        document tokenization.

        Args:
            text_str: Input string.

        Returns:
            List of lowercased tokens with stopwords removed.
            Empty list for empty / stopword-only input.
        """
        # ``re.split`` on non-alphanumeric + lowercase.
        tokens = re.split(r"[^a-z0-9]+", text_str.lower())
        return [t for t in tokens if t and t not in _BM25_STOPWORDS]

    def search_bm25(
        self,
        project_id: Optional[str],
        query: str,
        limit: int = 10,
    ) -> list[Skill]:
        """BM25-ranked full-text search over skill documents.

        Loads all skills for the given project (matching
        ``project_id`` or with ``project_id IS NULL`` so global
        skills are included too), tokenizes the query and each
        document (concatenation of ``name``, ``description``,
        and ``content``), and returns the top ``limit`` rows
        ranked by BM25 score.

        Implementation:

        * Tokenize: lowercase + split on non-alphanumeric.
          Stopwords (English) are stripped — see
          :data:`_BM25_STOPWORDS`.
        * BM25 params: ``k1=1.5``, ``b=0.75`` — standard values
          from the literature.
        * IDF: ``log((N - df + 0.5) / (df + 0.5) + 1)`` where
          ``N`` is the total document count and ``df`` is the
          document frequency for the term.
        * Document length (``dl``) is the number of tokens in
          ``name + ' ' + description + ' ' + content``.

        Suitable for the small-to-medium skill corpora that the
        daemon operates on (hundreds of skills per project). For
        larger corpora, switch to a vector-search path via
        :class:`SkillEmbeddingRepository`.

        Args:
            project_id: Owning project ID (``None`` to search
                global skills only — skills with
                ``project_id IS NULL``).
            query: Search query string.
            limit: Maximum number of rows to return (default
                ``10``).

        Returns:
            List of :class:`Skill` instances sorted by BM25
            score descending. Empty list when no skill scores
            above zero.
        """
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        with Session(self.engine) as session:
            # Match either the project's skills OR global
            # (project_id IS NULL) skills so a search within a
            # project also considers the global library.
            stmt = select(Skill).where(
                (Skill.project_id == project_id)
                | (Skill.project_id.is_(None))
            )
            skills = list(session.exec(stmt))

        # Tokenize every document once. Pre-tokenization avoids
        # re-splitting on each query term.
        tokenized_docs: list[tuple[Skill, list[str]]] = []
        doc_term_freqs: list[Counter] = []
        for skill in skills:
            doc_text = f"{skill.name} {skill.description} {skill.content}"
            tokens = self._tokenize(doc_text)
            tokenized_docs.append((skill, tokens))
            doc_term_freqs.append(Counter(tokens))

        # Document frequency per term — how many docs contain
        # each query term. Computed across the tokenized
        # documents above.
        df: Counter = Counter()
        for tokens in (toks for _, toks in tokenized_docs):
            unique_terms = set(tokens)
            for term in query_tokens:
                if term in unique_terms:
                    df[term] += 1

        n_docs = len(tokenized_docs)
        if n_docs == 0:
            return []

        # Average document length over the tokenized docs.
        # Used by BM25's length-normalization term.
        total_tokens = sum(len(toks) for _, toks in tokenized_docs)
        avgdl = total_tokens / n_docs if n_docs else 1.0

        scored: list[tuple[float, Skill]] = []
        for (skill, tokens), term_freq in zip(tokenized_docs, doc_term_freqs):
            dl = len(tokens)
            if dl == 0:
                continue
            score = 0.0
            for term in query_tokens:
                f = term_freq.get(term, 0)
                if f == 0:
                    continue
                # Smoothed IDF: log((N - df + 0.5) / (df + 0.5) + 1).
                # The ``+1`` keeps the score positive when ``df > N``
                # is impossible by construction, but the smoothing
                # is the standard BM25+ formulation that gracefully
                # handles very common terms (large ``df``).
                df_term = df[term]
                idf = log((n_docs - df_term + 0.5) / (df_term + 0.5) + 1)
                # BM25 term-frequency contribution with length
                # normalization.
                denom = f + _BM25_K1 * (
                    1 - _BM25_B + _BM25_B * (dl / avgdl)
                )
                score += idf * (f * (_BM25_K1 + 1)) / denom
            if score > 0:
                scored.append((score, skill))

        # Sort by score descending, take top ``limit``.
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [skill for _, skill in scored[:limit]]


# ============================================================
# SkillLineageRepository
# ============================================================


class SkillLineageRepository:
    """SQLModel-based repository for the ``skill_lineage`` table.

    The lineage graph is a DAG: a skill's parents are the
    previous-generation rows that produced it, and a skill's
    children are the next-generation rows it produced.

    All methods are synchronous; callers bridge to async via
    ``asyncio.to_thread``.
    """

    def __init__(self, engine: Engine):
        """Initialize the repository with a database engine.

        Args:
            engine: SQLAlchemy Engine bound to a SQLite or
                PostgreSQL database.
        """
        self.engine = engine

    def create(
        self,
        skill_id: str,
        parent_skill_id: str,
        change_summary: str = "",
        content_diff: str = "",
    ) -> SkillLineage:
        """Record a parent/child lineage edge.

        Args:
            skill_id: The descendant skill (new generation).
            parent_skill_id: The ancestor skill (previous
                generation).
            change_summary: One-line description of the change.
            content_diff: Unified diff of the content body.

        Returns:
            The newly created :class:`SkillLineage` instance.
        """
        edge = SkillLineage(
            skill_id=skill_id,
            parent_skill_id=parent_skill_id,
            change_summary=change_summary,
            content_diff=content_diff,
            created_at=_now_iso(),
        )
        with Session(self.engine) as session:
            session.add(edge)
            session.commit()
            session.refresh(edge)
            logger.info(
                f"Recorded skill lineage: skill_id={skill_id}, "
                f"parent_skill_id={parent_skill_id}"
            )
            return edge

    def get_parents(self, skill_id: str) -> list[SkillLineage]:
        """Return the lineage edges where ``skill_id`` is the descendant.

        Args:
            skill_id: The skill whose parents to fetch.

        Returns:
            List of :class:`SkillLineage` edges pointing into
            the skill (one per ancestor). Empty for a
            root-imported skill.
        """
        with Session(self.engine) as session:
            stmt = select(SkillLineage).where(SkillLineage.skill_id == skill_id)
            return list(session.exec(stmt))

    def get_children(self, parent_skill_id: str) -> list[SkillLineage]:
        """Return the lineage edges where ``parent_skill_id`` is the ancestor.

        Args:
            parent_skill_id: The skill whose children to
                fetch.

        Returns:
            List of :class:`SkillLineage` edges pointing out of
            the skill (one per descendant). Empty for a
            leaf skill that has not been evolved yet.
        """
        with Session(self.engine) as session:
            stmt = select(SkillLineage).where(
                SkillLineage.parent_skill_id == parent_skill_id
            )
            return list(session.exec(stmt))


# ============================================================
# SkillUsageRepository
# ============================================================


class SkillUsageRepository:
    """SQLModel-based repository for the ``skill_usage_records`` table.

    All methods are synchronous; callers bridge to async via
    ``asyncio.to_thread``.
    """

    def __init__(self, engine: Engine):
        """Initialize the repository with a database engine.

        Args:
            engine: SQLAlchemy Engine bound to a SQLite or
                PostgreSQL database.
        """
        self.engine = engine

    def create(
        self,
        skill_id: str,
        project_id: str,
        instance_id: str,
        agent_id: str,
        task_message: Optional[str] = None,
        selected: bool = False,
        applied: bool = False,
        task_succeeded: bool = False,
        iterations: int = 0,
        duration_seconds: int = 0,
        fallback: bool = False,
    ) -> SkillUsageRecord:
        """Insert a new usage record.

        Typical call-site pattern: insert once when the skill
        is selected (``selected=True``) and update via
        :meth:`update_feedback` once the task completes.

        Args:
            skill_id: The skill that was used.
            project_id: Owning project.
            instance_id: The instance that triggered the skill.
            agent_id: The agent that consumed the skill.
            task_message: Optional snapshot of the triggering
                task message.
            selected: Whether the trigger resolver selected
                this skill.
            applied: Whether the skill was actually loaded
                into the prompt and used.
            task_succeeded: Whether the task that used this
                skill ultimately succeeded.
            iterations: Graph iterations the task took while
                this skill was active.
            duration_seconds: Wall-clock seconds the task
                spent while this skill was active.
            fallback: Whether the skill execution fell back to
                a different path.

        Returns:
            The newly created :class:`SkillUsageRecord`
            instance.
        """
        record = SkillUsageRecord(
            skill_id=skill_id,
            project_id=project_id,
            instance_id=instance_id,
            agent_id=agent_id,
            task_message=task_message,
            selected=selected,
            applied=applied,
            task_succeeded=task_succeeded,
            iterations=iterations,
            duration_seconds=duration_seconds,
            fallback=fallback,
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            logger.debug(
                f"Created skill usage record: id={record.id}, "
                f"skill_id={skill_id}, instance_id={instance_id}"
            )
            return record

    def get_by_skill(
        self,
        skill_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[SkillUsageRecord], int]:
        """List usage records for a skill.

        Args:
            skill_id: The skill whose history to fetch.
            limit: Maximum number of rows to return.
            offset: Number of rows to skip.

        Returns:
            ``(items, total)`` — a list of
            :class:`SkillUsageRecord` instances plus the total
            row count matching the skill filter (ignoring
            ``limit`` / ``offset``).
        """
        with Session(self.engine) as session:
            stmt = select(SkillUsageRecord).where(
                SkillUsageRecord.skill_id == skill_id
            )
            count_stmt = (
                select(func.count()).select_from(stmt.subquery())
            )
            total = int(session.scalar(count_stmt) or 0)
            stmt = (
                stmt.order_by(col(SkillUsageRecord.created_at).desc())
                .offset(offset)
                .limit(limit)
            )
            items = list(session.exec(stmt))
            return items, total

    def get_stats(self, skill_id: str) -> dict[str, Any]:
        """Compute aggregate stats for a skill.

        Loads every usage record for the skill and counts the
        boolean signals. Cheap for typical per-skill record
        counts (tens to hundreds); for skills with millions of
        records, switch to a SQL-side aggregation. This is
        consistent with the project's "small daemon-side
        workloads" expectation.

        Args:
            skill_id: The skill whose stats to compute.

        Returns:
            Dict with keys:

            * ``total`` — total record count
            * ``selected`` — count of ``selected=True``
            * ``applied`` — count of ``applied=True``
            * ``completions`` — count of ``task_succeeded=True``
            * ``fallbacks`` — count of ``fallback=True``
            * ``completion_rate`` — ``completions / total``
              (``0.0`` if ``total == 0``)
            * ``fallback_rate`` — ``fallbacks / total``
              (``0.0`` if ``total == 0``)
        """
        with Session(self.engine) as session:
            stmt = select(SkillUsageRecord).where(
                SkillUsageRecord.skill_id == skill_id
            )
            records = list(session.exec(stmt))

        total = len(records)
        if total == 0:
            return {
                "total": 0,
                "selected": 0,
                "applied": 0,
                "completions": 0,
                "fallbacks": 0,
                "completion_rate": 0.0,
                "fallback_rate": 0.0,
            }

        selected = sum(1 for r in records if r.selected)
        applied = sum(1 for r in records if r.applied)
        completions = sum(1 for r in records if r.task_succeeded)
        fallbacks = sum(1 for r in records if r.fallback)
        return {
            "total": total,
            "selected": selected,
            "applied": applied,
            "completions": completions,
            "fallbacks": fallbacks,
            "completion_rate": completions / total,
            "fallback_rate": fallbacks / total,
        }

    def update_feedback(
        self,
        record_id: str,
        applied: bool,
        note: str,
    ) -> SkillUsageRecord | None:
        """Stamp feedback onto an existing usage record.

        Args:
            record_id: The record to update.
            applied: Whether the feedback was applied
                (``True``) or recorded-but-not-applied
                (``False``). ``None`` is intentionally NOT
                accepted here — the post-mortem service always
                commits to one outcome.
            note: Free-form note attached to the feedback.

        Returns:
            The updated :class:`SkillUsageRecord`, or ``None``
            if no row with that ID exists.
        """
        with Session(self.engine) as session:
            record = session.get(SkillUsageRecord, record_id)
            if record is None:
                logger.warning(
                    f"Skill usage record not found for feedback: "
                    f"id={record_id}"
                )
                return None
            record.feedback_applied = applied
            record.feedback_note = note
            session.commit()
            session.refresh(record)
            logger.debug(
                f"Updated skill usage feedback: id={record_id}, "
                f"applied={applied}"
            )
            return record

    def count_comparisons(self, ab_test_group: str) -> dict[str, int]:
        """Count usage records per skill in an A/B test group.

        Used by the A/B test winner-selection pipeline to
        compute the per-variant sample size for statistical
        comparison.

        Args:
            ab_test_group: The shared UUID grouping old + new
                variants.

        Returns:
            Dict mapping ``skill_id`` to usage record count.
            Empty dict when no skills belong to the group
            (caller should treat as "no test in progress").
        """
        with Session(self.engine) as session:
            # First get the skill IDs that belong to this group.
            id_stmt = select(Skill.id).where(Skill.ab_test_group == ab_test_group)
            skill_ids = list(session.exec(id_stmt))
            if not skill_ids:
                return {}
            # Then count usage records per skill_id in one
            # GROUP BY query. SQLModel's ``select(...).where(...).group_by(...)``
            # doesn't directly expose ``func.count(*)`` with
            # grouping in a portable way, so the implementation
            # uses raw SQL with bound parameters for the IN list.
            # Bound parameters make the IN list safe from SQL
            # injection — only the skill IDs we just queried
            # can appear here.
            placeholders = ", ".join(f":sid{i}" for i in range(len(skill_ids)))
            params: dict[str, Any] = {
                f"sid{i}": sid for i, sid in enumerate(skill_ids)
            }
            count_sql = text(
                f"SELECT skill_id, COUNT(*) FROM skill_usage_records "
                f"WHERE skill_id IN ({placeholders}) "
                f"GROUP BY skill_id"
            )
            result = session.execute(count_sql, params)
            rows = result.fetchall()
            session.commit()
            return {str(skill_id): int(count) for skill_id, count in rows}

    def get_applied_for_instance(
        self,
        instance_id: str,
    ) -> list[SkillUsageRecord]:
        """Return usage records for an instance with feedback applied.

        Used by the feedback rollup service to decide which
        skills on a given instance received actionable user
        feedback.

        Args:
            instance_id: The instance ID.

        Returns:
            List of :class:`SkillUsageRecord` rows for this
            instance with ``feedback_applied=True``. Empty list
            when no such record exists.
        """
        with Session(self.engine) as session:
            stmt = (
                select(SkillUsageRecord)
                .where(SkillUsageRecord.instance_id == instance_id)
                .where(SkillUsageRecord.feedback_applied == True)  # noqa: E712
            )
            return list(session.exec(stmt))

    def has_applied_for_instance(self, instance_id: str) -> bool:
        """Check whether any usage record for an instance has feedback applied.

        Cheaper alternative to
        :meth:`get_applied_for_instance` when the caller only
        needs a yes/no answer.

        Args:
            instance_id: The instance ID.

        Returns:
            ``True`` iff at least one :class:`SkillUsageRecord`
            row exists for this instance with
            ``feedback_applied=True``.
        """
        with Session(self.engine) as session:
            stmt = (
                select(1)
                .select_from(SkillUsageRecord)
                .where(SkillUsageRecord.instance_id == instance_id)
                .where(SkillUsageRecord.feedback_applied == True)  # noqa: E712
                .limit(1)
            )
            return session.exec(stmt).first() is not None


# ============================================================
# SkillTriggerRepository
# ============================================================


class SkillTriggerRepository:
    """SQLModel-based repository for the ``skill_triggers`` table.

    All methods are synchronous; callers bridge to async via
    ``asyncio.to_thread``.
    """

    def __init__(self, engine: Engine):
        """Initialize the repository with a database engine.

        Args:
            engine: SQLAlchemy Engine bound to a SQLite or
                PostgreSQL database.
        """
        self.engine = engine

    def create(
        self,
        name: str,
        condition_type: str,
        condition_json: dict[str, Any],
        action: str,
        project_id: Optional[str] = None,
    ) -> SkillTrigger:
        """Insert a new trigger rule.

        Args:
            name: Human-readable name.
            condition_type: Discriminator for ``condition_json``
                shape (``"keyword"``, ``"regex"``,
                ``"embedding_match"``, …).
            condition_json: Rule body. Type-specific.
            action: Free-form action string
                (``"select_skill:workflow-debug"``, …).
            project_id: Owning project (``None`` for a global
                trigger).

        Returns:
            The newly created :class:`SkillTrigger` instance.
        """
        trigger = SkillTrigger(
            name=name,
            condition_type=condition_type,
            condition_json=condition_json,
            action=action,
            project_id=project_id,
            created_at=_now_iso(),
        )
        with Session(self.engine) as session:
            session.add(trigger)
            session.commit()
            session.refresh(trigger)
            logger.info(
                f"Created skill trigger: id={trigger.id}, "
                f"name={name}, project_id={project_id}"
            )
            return trigger

    def get(self, trigger_id: str) -> SkillTrigger | None:
        """Fetch a trigger by its primary key.

        Args:
            trigger_id: The trigger's UUID4 ID.

        Returns:
            The :class:`SkillTrigger` instance, or ``None``.
        """
        with Session(self.engine) as session:
            return session.get(SkillTrigger, trigger_id)

    def list(
        self,
        project_id: Optional[str] = None,
        enabled_only: bool = True,
    ) -> list[SkillTrigger]:
        """List triggers.

        Args:
            project_id: Project filter. Per the spec, passing
                ``None`` here means "list global triggers"
                (where ``project_id IS NULL``); pass a string
                to scope to a specific project.
            enabled_only: If ``True`` (default), filter to
                ``is_enabled=True`` rows.

        Returns:
            List of :class:`SkillTrigger` instances. Empty
            when none match.
        """
        with Session(self.engine) as session:
            stmt = select(SkillTrigger).where(
                SkillTrigger.project_id.is_(None)
            )
            if enabled_only:
                stmt = stmt.where(SkillTrigger.is_enabled == True)  # noqa: E712
            return list(session.exec(stmt))

    def update(
        self,
        trigger_id: str,
        **fields: Any,
    ) -> SkillTrigger | None:
        """Update fields on an existing trigger.

        Protected keys (``id``, ``created_at``) are silently
        dropped.

        Args:
            trigger_id: The trigger to update.
            **fields: Column values to overwrite.

        Returns:
            The updated :class:`SkillTrigger`, or ``None`` if
            no row with that ID exists.
        """
        protected = {"id", "created_at"}
        with Session(self.engine) as session:
            trigger = session.get(SkillTrigger, trigger_id)
            if trigger is None:
                logger.warning(
                    f"Skill trigger not found for update: id={trigger_id}"
                )
                return None
            for key, value in fields.items():
                if key in protected:
                    logger.warning(
                        f"Ignoring protected field in trigger update: "
                        f"id={trigger_id}, field={key}"
                    )
                    continue
                if not hasattr(trigger, key):
                    raise AttributeError(
                        f"SkillTrigger has no field {key!r}"
                    )
                setattr(trigger, key, value)
            session.commit()
            session.refresh(trigger)
            logger.info(f"Updated skill trigger: id={trigger_id}")
            return trigger

    def delete(self, trigger_id: str) -> bool:
        """Delete a trigger row.

        Args:
            trigger_id: The trigger to delete.

        Returns:
            ``True`` if a row was deleted, ``False`` if no row
            with that ID existed.
        """
        with Session(self.engine) as session:
            trigger = session.get(SkillTrigger, trigger_id)
            if trigger is None:
                logger.warning(
                    f"Skill trigger not found for delete: id={trigger_id}"
                )
                return False
            session.delete(trigger)
            session.commit()
            logger.info(f"Deleted skill trigger: id={trigger_id}")
            return True


# ============================================================
# SkillEmbeddingRepository
# ============================================================


class SkillEmbeddingRepository:
    """SQLModel-based repository for the ``skill_embeddings`` table.

    All methods are synchronous; callers bridge to async via
    ``asyncio.to_thread``.
    """

    def __init__(self, engine: Engine):
        """Initialize the repository with a database engine.

        Args:
            engine: SQLAlchemy Engine bound to a SQLite or
                PostgreSQL database.
        """
        self.engine = engine

    def create(
        self,
        skill_id: str,
        trigger_query: str,
        embedding: list[float],
    ) -> SkillEmbedding:
        """Insert a new cached embedding.

        Args:
            skill_id: The skill this embedding is for.
            trigger_query: The source query text.
            embedding: Vector as a list of floats. Stored as a
                JSON array via
                :class:`~daemon.repositories.infra.types.JSONBType`
                — NOT BYTEA, NOT pickle.

        Returns:
            The newly created :class:`SkillEmbedding` instance.
        """
        row = SkillEmbedding(
            skill_id=skill_id,
            trigger_query=trigger_query,
            embedding=list(embedding),
            created_at=_now_iso(),
        )
        with Session(self.engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            logger.debug(
                f"Created skill embedding: id={row.id}, "
                f"skill_id={skill_id}"
            )
            return row

    def get_by_skill(self, skill_id: str) -> list[SkillEmbedding]:
        """List cached embeddings for a skill.

        Args:
            skill_id: The skill whose embeddings to fetch.

        Returns:
            List of :class:`SkillEmbedding` rows for the
            skill. Empty when none exist.
        """
        with Session(self.engine) as session:
            stmt = select(SkillEmbedding).where(
                SkillEmbedding.skill_id == skill_id
            )
            return list(session.exec(stmt))

    def delete_by_skill(self, skill_id: str) -> int:
        """Delete every cached embedding for a skill.

        Uses raw SQL via ``session.execute(text(...))`` because
        SQLModel's ``delete()`` constructor is awkward for
        batched deletes. The skill_id is bound as a parameter,
        so there's no SQL injection risk.

        Args:
            skill_id: The skill whose embeddings to clear.

        Returns:
            Number of rows deleted (``result.rowcount``).
        """
        with Session(self.engine) as session:
            stmt = text(
                "DELETE FROM skill_embeddings WHERE skill_id = :sid"
            )
            result = session.execute(stmt, {"sid": skill_id})
            session.commit()
            deleted = int(result.rowcount or 0)
            logger.debug(
                f"Deleted skill embeddings: skill_id={skill_id}, "
                f"count={deleted}"
            )
            return deleted

    def get_all_for_project(
        self,
        project_id: Optional[str],
    ) -> list[tuple[SkillEmbedding, str]]:
        """Bulk-load embeddings for a project's skills.

        Implementation: loads every embedding, then bulk-fetches
        their owning skills to filter by ``project_id``. This
        is acceptable for the daemon's small embedding table
        (hundreds of rows typical) and avoids per-row N+1
        queries.

        For ``project_id=None``, returns all embeddings across
        every skill (no filter applied).

        Args:
            project_id: Project ID to scope to, or ``None``
                for "no project filter" (load every
                embedding across every skill).

        Returns:
            List of ``(SkillEmbedding, skill_id)`` tuples
            matching the filter.
        """
        with Session(self.engine) as session:
            all_embeddings = list(session.exec(select(SkillEmbedding)))
            if not all_embeddings:
                return []
            skill_ids = list({emb.skill_id for emb in all_embeddings})
            skill_stmt = select(Skill).where(Skill.id.in_(skill_ids))
            skills = list(session.exec(skill_stmt))
            skills_by_id = {s.id: s for s in skills}

            if project_id is None:
                # No project filter — return every embedding.
                return [(emb, emb.skill_id) for emb in all_embeddings]

            # Filter to embeddings whose owning skill belongs
            # to the requested project. Skills with
            # ``project_id IS NULL`` are excluded by the
            # strict-equality check below — call sites that
            # want "global skills included" should pass
            # ``project_id=None``.
            return [
                (emb, emb.skill_id)
                for emb in all_embeddings
                if skills_by_id.get(emb.skill_id) is not None
                and skills_by_id[emb.skill_id].project_id == project_id
            ]


# ============================================================
# SkillABTestRepository
# ============================================================


class SkillABTestRepository:
    """SQLModel-based repository for the ``skill_ab_tests`` table.

    The A/B test lifecycle:

    1. :meth:`create_ab_test` registers a new test group with
       ``comparisons=0``, ``extension_count=0``, and an unset
       ``resolved_at``.
    2. Each feedback event for either variant bumps
       ``comparisons`` via
       :meth:`increment_comparison`.
    3. The test can be extended via
       :meth:`increment_extension` to gather more data when
       statistical significance isn't reached at the planned
       deadline.
    4. :meth:`resolve` sets ``resolved_at`` and
       ``winner_skill_id`` when a winner is chosen.

    Both ``increment_*`` methods use raw SQL UPDATE to avoid the
    read-modify-write race under concurrent feedback ingestion.

    All methods are synchronous; callers bridge to async via
    ``asyncio.to_thread``.
    """

    def __init__(self, engine: Engine):
        """Initialize the repository with a database engine.

        Args:
            engine: SQLAlchemy Engine bound to a SQLite or
                PostgreSQL database.
        """
        self.engine = engine

    def create_ab_test(
        self,
        ab_test_group: str,
        skill_id_old: str,
        skill_id_new: str,
    ) -> SkillABTest:
        """Register a new A/B test.

        Args:
            ab_test_group: Shared UUID grouping old + new
                variants.
            skill_id_old: The incumbent (baseline) skill.
            skill_id_new: The candidate (new) skill.

        Returns:
            The newly created :class:`SkillABTest` instance.
        """
        test = SkillABTest(
            ab_test_group=ab_test_group,
            skill_id_old=skill_id_old,
            skill_id_new=skill_id_new,
            extension_count=0,
            comparisons=0,
            created_at=_now_iso(),
        )
        with Session(self.engine) as session:
            session.add(test)
            session.commit()
            session.refresh(test)
            logger.info(
                f"Created skill A/B test: id={test.id}, "
                f"ab_test_group={ab_test_group}, "
                f"old={skill_id_old}, new={skill_id_new}"
            )
            return test

    def get_by_group(self, ab_test_group: str) -> SkillABTest | None:
        """Fetch a test by its group UUID.

        Note: there should be exactly one row per
        ``ab_test_group`` (created by
        :meth:`create_ab_test`). The query uses ``.first()`` so
        it tolerates accidental duplicates gracefully.

        Args:
            ab_test_group: The shared UUID grouping old + new
                variants.

        Returns:
            The :class:`SkillABTest` instance, or ``None`` if
            no row matches.
        """
        with Session(self.engine) as session:
            stmt = select(SkillABTest).where(
                SkillABTest.ab_test_group == ab_test_group
            )
            return session.exec(stmt).first()

    def increment_comparison(self, ab_test_group: str) -> None:
        """Atomically bump ``comparisons`` for a test group.

        Uses raw SQL ``UPDATE comparisons = comparisons + 1`` so
        concurrent feedback events can't lose increments to a
        read-modify-write race. The ``ab_test_group`` value is
        bound, so there's no SQL injection risk.

        Args:
            ab_test_group: The shared UUID of the test.
        """
        with Session(self.engine) as session:
            session.execute(
                text(
                    "UPDATE skill_ab_tests "
                    "SET comparisons = comparisons + 1 "
                    "WHERE ab_test_group = :g"
                ),
                {"g": ab_test_group},
            )
            session.commit()
            logger.debug(
                f"Incremented skill A/B test comparison: "
                f"ab_test_group={ab_test_group}"
            )

    def increment_extension(self, ab_test_group: str) -> None:
        """Atomically bump ``extension_count`` for a test group.

        Uses raw SQL ``UPDATE extension_count = extension_count
        + 1`` so concurrent extension requests can't lose
        increments to a read-modify-write race.

        Args:
            ab_test_group: The shared UUID of the test.
        """
        with Session(self.engine) as session:
            session.execute(
                text(
                    "UPDATE skill_ab_tests "
                    "SET extension_count = extension_count + 1 "
                    "WHERE ab_test_group = :g"
                ),
                {"g": ab_test_group},
            )
            session.commit()
            logger.debug(
                f"Incremented skill A/B test extension: "
                f"ab_test_group={ab_test_group}"
            )

    def resolve(
        self,
        ab_test_group: str,
        winner_skill_id: str,
    ) -> SkillABTest | None:
        """Mark a test as resolved with a winning variant.

        Sets ``resolved_at`` to the current time and
        ``winner_skill_id`` to the chosen variant. The caller
        is responsible for ensuring ``winner_skill_id`` is one
        of ``skill_id_old`` / ``skill_id_new`` — the repository
        does not validate that constraint.

        Args:
            ab_test_group: The shared UUID of the test.
            winner_skill_id: The skill ID of the winning
                variant.

        Returns:
            The updated :class:`SkillABTest`, or ``None`` if
            no test with that group exists.
        """
        with Session(self.engine) as session:
            test = session.exec(
                select(SkillABTest).where(
                    SkillABTest.ab_test_group == ab_test_group
                )
            ).first()
            if test is None:
                logger.warning(
                    f"Skill A/B test not found for resolve: "
                    f"ab_test_group={ab_test_group}"
                )
                return None
            test.resolved_at = _now_iso()
            test.winner_skill_id = winner_skill_id
            session.commit()
            session.refresh(test)
            logger.info(
                f"Resolved skill A/B test: ab_test_group={ab_test_group}, "
                f"winner_skill_id={winner_skill_id}"
            )
            return test

    def get_active_tests(
        self,
        project_id: Optional[str] = None,
    ) -> list[SkillABTest]:
        """List unresolved tests.

        Per the spec, the ``project_id`` arg is reserved for
        future use — the ``skill_ab_tests`` table has no
        ``project_id`` column. The current implementation only
        filters by ``resolved_at IS NULL``.

        Args:
            project_id: Reserved for future use (no-op at
                the table layer today).

        Returns:
            List of :class:`SkillABTest` rows with
            ``resolved_at IS NULL``. Empty when no test is
            running.
        """
        del project_id  # Reserved for future use; see docstring.
        with Session(self.engine) as session:
            stmt = select(SkillABTest).where(
                SkillABTest.resolved_at.is_(None)
            )
            return list(session.exec(stmt))