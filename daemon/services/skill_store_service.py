"""Skill store service for the Skill Evolution System.

Phase 2 of the Skill Evolution System. Provides a thin async
service layer over the Phase 1 repositories (:class:`SkillRepository`
and :class:`SkillLineageRepository`) plus the Phase 2
:class:`SkillEmbeddingService`.

Why a service layer
-------------------

The repositories stay the source of truth for SQL behavior — every
CRUD primitive is a synchronous method that opens its own
``Session`` and commits. The service layer adds:

* **Async facade.** Repo methods are sync; callers in the daemon's
  async event loop hop through ``asyncio.to_thread`` so the DB
  never blocks the loop. The service exposes ``async def`` methods
  that callers can ``await`` directly.

* **Embedding-cache lifecycle.** Creating or updating a skill
  triggers :meth:`SkillEmbeddingService.update_skill_embeddings` so
  the resolver's per-skill vector cache stays fresh. The embedding
  call is **best-effort** — failures are logged but do not abort
  the underlying CRUD operation. Skills remain usable via BM25
  full-text search even without cached embeddings, so a transient
  OpenAI outage degrades the resolver rather than breaking the
  store.

* **Project-scope filtering.** :meth:`list_skills` returns both
  project-scoped skills (``project_id == X``) AND global skills
  (``project_id IS NULL``) for a given project — the standard
  "project overlay on top of global" semantics the resolver and
  tool layer expect.

* **View-side projection.** :meth:`list_skills` strips the
  (potentially large) ``content`` column from the row — callers
  see a metadata-only shape suitable for listing UIs.
  :meth:`view_skill` bundles the skill row with its lineage graph
  in one round-trip.

Design notes
------------

* **No numpy / no DB engine.** The service holds no engine — all
  DB work goes through the injected repos. The embedding service
  uses pure-Python vector math (no numpy), per the project-wide
  spec.

* **Defensive repo call wrapping.** Each ``async`` method awaits
  a single ``asyncio.to_thread`` invocation that calls into the
  repo. Multiple repo calls (e.g. the project + global two-step in
  :meth:`list_skills`) live inside the same thread to keep the
  DB session boundary clean.

* **Embedding failure isolation.** :meth:`_refresh_embeddings`
  catches every exception from the embedding pipeline. The skill
  is still returned to the caller; the embedding failure is
  recorded in the log at ``WARNING`` so an operator can spot a
  misconfigured provider without paging the daemon.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Fields projected by :meth:`SkillStoreService.list_skills`.
#
# Phase 6 polish: the list endpoint used to strip the counter columns
# (``total_selections``, ``total_applied``, ``total_completions``,
# ``total_fallbacks``, ``consecutive_failures``) and the lifecycle
# fields (``status``, ``is_active``, ``ab_test_group``,
# ``lineage_origin``, ``generation``, ``last_used_at``) on the
# assumption that the list page only cared about the card metadata.
# That assumption broke the Skills page — :class:`SkillCardComponent`
# computes the success-rate chip from ``total_selections`` /
# ``total_completions`` and the A/B-test badge from
# ``ab_test_group``, so a list response with those columns missing
# rendered ``NaN%`` and a silent no-op on A/B tests.
#
# The full column projection keeps the list payload compact (the
# only column NOT in the list is the large ``content`` body) while
# removing every "strip me here, fill me in detail" mismatch that
# the detail page was carrying. The detail endpoint still adds
# ``content`` + ``lineage`` + ``metrics`` on top.
_LIST_SKILL_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "description",
    "category",
    "status",
    "is_active",
    "lineage_origin",
    "generation",
    "ab_test_group",
    "total_selections",
    "total_applied",
    "total_completions",
    "total_fallbacks",
    "consecutive_failures",
    "last_used_at",
    "created_at",
    "updated_at",
)


# ============================================================
# SkillStoreService
# ============================================================


class SkillStoreService:
    """Service layer for skill CRUD operations.

    Wraps :class:`SkillRepository` and
    :class:`SkillLineageRepository` with the embedding-service
    integration that Phase 2 adds. Constructor injection only —
    no engine, no config, no global state — so the service is
    trivially testable with mock repos and a mock embedding
    service.

    Attributes:
        _skill_repo: :class:`SkillRepository` (Phase 1) for
            CRUD on the ``skills`` table.
        _lineage_repo: :class:`SkillLineageRepository` (Phase 1)
            for the parent/child DAG.
        _embedding_service: :class:`SkillEmbeddingService` (Phase
            2) for refreshing the per-skill embedding cache.
            Best-effort: failures here must not abort CRUD.
    """

    def __init__(
        self,
        skill_repo: Any,
        lineage_repo: Any,
        embedding_service: Any,
    ) -> None:
        """Store the dependencies.

        Args:
            skill_repo: :class:`SkillRepository` bound to the
                project's SQLAlchemy engine.
            lineage_repo: :class:`SkillLineageRepository` bound to
                the same engine.
            embedding_service: :class:`SkillEmbeddingService`
                configured for the project.
        """
        self._skill_repo = skill_repo
        self._lineage_repo = lineage_repo
        self._embedding_service = embedding_service

    # --------------------------------------------------------
    # Create
    # --------------------------------------------------------

    async def create_skill(
        self,
        name: str,
        description: str,
        content: str,
        project_id: Optional[str] = None,
        category: str = "workflow",
        lineage_origin: str = "imported",
    ) -> Any:
        """Create a new skill and refresh its embedding cache.

        Wraps :meth:`SkillRepository.create` (sync) in
        ``asyncio.to_thread`` so the call site can ``await`` from
        an async event loop. After the row is committed, the
        embedding service is invoked to refresh the per-skill
        vector cache. The embedding call is best-effort — see
        :meth:`_refresh_embeddings`.

        The new row is created with ``status='active'`` so the
        resolver's default ``active_only=True`` filter surfaces it
        immediately. The repository's default counters
        (``total_selections=0``, ``total_applied=0``, …) apply.

        Args:
            name: Human-readable skill name. Must be unique
                within ``(project_id, name, generation)`` per the
                underlying UNIQUE constraint.
            description: One-line summary.
            content: The skill body (markdown / instructions).
            project_id: Owning project ID, or ``None`` for a
                global skill.
            category: Free-form category string. Default
                ``'workflow'``.
            lineage_origin: ``'imported'`` for new imports;
                ``'evolved'`` / ``'feedback'`` for descendants.
                Default ``'imported'``.

        Returns:
            The newly created :class:`~daemon.repositories.skill.models.Skill`
            instance. Embeddings may or may not be cached — a
            warning is logged if the embedding refresh fails.
        """
        def _create() -> Any:
            return self._skill_repo.create(
                name=name,
                description=description,
                content=content,
                project_id=project_id,
                category=category,
                lineage_origin=lineage_origin,
                status="active",
            )

        skill = await asyncio.to_thread(_create)
        # Best-effort refresh — never abort the create if the
        # embedding pipeline fails.
        await self._refresh_embeddings(skill)
        return skill

    # --------------------------------------------------------
    # Read
    # --------------------------------------------------------

    async def get_skill(self, skill_id: str) -> Any:
        """Fetch a single skill by its primary key.

        Args:
            skill_id: The skill's UUID4 ID.

        Returns:
            The :class:`~daemon.repositories.skill.models.Skill`
            instance, or ``None`` if no row matches.
        """
        return await asyncio.to_thread(self._skill_repo.get, skill_id)

    async def list_skills(
        self,
        project_id: Optional[str] = None,
        active_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """List skills with project-scope filtering.

        Returns a metadata-only projection (id, name, description,
        category, status, created_at, updated_at) — no content
        body. Callers that need the full document should use
        :meth:`view_skill`.

        Project-scope semantics:

        * ``project_id=None`` → returns ONLY global skills
          (``project_id IS NULL``).
        * ``project_id='abc'`` → returns BOTH project-scoped
          skills (``project_id == 'abc'``) AND global skills
          (``project_id IS NULL``). This matches the resolver's
          "project overlay on top of global" expectation: a search
          inside project ``abc`` should still consider the global
          library.

        Implementation: two repo calls in the same thread (one
        for project-scoped, one for global) and a Python-side
        merge. The repo's :meth:`SkillRepository.list` does not
        natively support the OR condition, and Phase 2's scope is
        the service layer — extending the repo is intentionally
        deferred.

        Args:
            project_id: Project scope filter, or ``None`` for
                global-only.
            active_only: If ``True`` (default), filter to
                ``status='active'`` rows.
            limit: Maximum rows to return (applied to each
                underlying repo call before merging, so the merged
                result may exceed ``limit`` slightly when both
                buckets are non-empty — see Note).
            offset: Number of rows to skip (applied to each
                underlying repo call).

        Returns:
            ``(items, total)`` — a list of metadata-only dicts
            and the total row count across both buckets. The
            combined ``items`` list is unordered across the two
            buckets (no global sort key is applied); callers that
            need a stable order should sort client-side.

        Note:
            When ``project_id`` is set, ``limit`` and ``offset``
            are applied independently to each bucket, so the
            returned ``items`` count can be up to
            ``2 * limit``. The ``total`` reflects the actual
            combined count. Callers wanting strict pagination
            should slice client-side or refactor to a single
            repo call.
        """
        def _list() -> tuple[list[dict], int]:
            if project_id is None:
                items, total = self._skill_repo.list(
                    project_id=None,
                    active_only=active_only,
                    limit=limit,
                    offset=offset,
                )
                return [_project_skill(s) for s in items], total

            proj_items, proj_total = self._skill_repo.list(
                project_id=project_id,
                active_only=active_only,
                limit=limit,
                offset=offset,
            )
            glob_items, glob_total = self._skill_repo.list(
                project_id=None,
                active_only=active_only,
                limit=limit,
                offset=offset,
            )
            merged = list(proj_items) + list(glob_items)
            projected = [_project_skill(s) for s in merged]
            return projected, proj_total + glob_total

        return await asyncio.to_thread(_list)

    # --------------------------------------------------------
    # Update
    # --------------------------------------------------------

    async def update_skill(self, skill_id: str, **fields: Any) -> Any:
        """Update an existing skill, refreshing embeddings on content change.

        Wraps :meth:`SkillRepository.update` (sync) in
        ``asyncio.to_thread``. Detects a ``content`` field in
        ``fields`` and, if present, refreshes the embedding cache
        after the row is committed.

        Args:
            skill_id: The skill to update.
            **fields: Column values to overwrite. Any unknown
                key raises ``AttributeError`` (delegated to the
                repository).

        Returns:
            The updated :class:`~daemon.repositories.skill.models.Skill`,
            or ``None`` if no row with that ID exists.
        """
        content_changed = "content" in fields

        def _update() -> Any:
            return self._skill_repo.update(skill_id, **fields)

        skill = await asyncio.to_thread(_update)
        if skill is None:
            return None

        # Only refresh embeddings when the content body changed.
        # Metadata-only edits (description, category, …) don't
        # affect the resolver's vector cache.
        if content_changed:
            await self._refresh_embeddings(skill)

        return skill

    # --------------------------------------------------------
    # Delete / deactivate
    # --------------------------------------------------------

    async def delete_skill(self, skill_id: str) -> bool:
        """Hard-delete a skill row.

        Cascades through FK constraints: ``skill_lineage`` edges,
        ``skill_embeddings`` rows, ``skill_usage_records``, and
        ``skill_ab_tests`` are removed automatically. Prefer
        :meth:`deactivate_skill` when usage history must be
        preserved.

        Args:
            skill_id: The skill to delete.

        Returns:
            ``True`` if a row was deleted, ``False`` if no row
            with that ID existed.
        """
        return await asyncio.to_thread(
            self._skill_repo.delete, skill_id
        )

    async def deactivate_skill(self, skill_id: str) -> Any:
        """Soft-deactivate a skill (sets ``status='inactive'``).

        Thin wrapper over :meth:`SkillRepository.deactivate` so
        callers can ``await`` from an async event loop. The row
        is preserved so usage history remains queryable.

        Args:
            skill_id: The skill to deactivate.

        Returns:
            The updated :class:`~daemon.repositories.skill.models.Skill`,
            or ``None`` if no row with that ID exists.
        """
        return await asyncio.to_thread(
            self._skill_repo.deactivate, skill_id
        )

    # --------------------------------------------------------
    # View (skill + lineage bundle)
    # --------------------------------------------------------

    async def view_skill(self, skill_id: str) -> Optional[dict]:
        """Return the full skill document plus its lineage graph.

        Bundles two repo reads into one service call so the tool
        layer (Phase 4 ``skill_view``) can ship a complete
        snapshot in one round-trip.

        The returned shape::

            {
                "skill": <Skill.to_dict()>,         # full body
                "lineage": {
                    "parents":  [<SkillLineage.to_dict()>, ...],
                    "children": [<SkillLineage.to_dict()>, ...],
                },
            }

        Returns ``None`` when no skill matches ``skill_id``.

        Args:
            skill_id: The skill whose view to fetch.

        Returns:
            Dict with ``skill`` (full document) and ``lineage``
            (``parents`` + ``children`` lists), or ``None`` if
            the skill does not exist.
        """
        def _view() -> Optional[dict]:
            skill = self._skill_repo.get(skill_id)
            if skill is None:
                return None
            parents = self._lineage_repo.get_parents(skill_id)
            children = self._lineage_repo.get_children(skill_id)
            return {
                "skill": skill.to_dict(),
                "lineage": {
                    "parents": [p.to_dict() for p in parents],
                    "children": [c.to_dict() for c in children],
                },
            }

        return await asyncio.to_thread(_view)

    # --------------------------------------------------------
    # Internal helpers
    # --------------------------------------------------------

    async def _refresh_embeddings(self, skill: Any) -> None:
        """Best-effort refresh of the per-skill embedding cache.

        Calls :meth:`SkillEmbeddingService.update_skill_embeddings`
        and CATCHES every exception — embedding failures must not
        abort the CRUD operation that triggered the refresh.
        Skills remain usable via BM25 full-text search even
        without cached embeddings.

        A failure here typically indicates a misconfigured
        OpenAI-compatible endpoint (missing API key, wrong base
        URL) or a transient API outage. Either case should be
        visible to operators via the warning log, but the skill
        row is still usable.

        Args:
            skill: The just-created or just-updated
                :class:`~daemon.repositories.skill.models.Skill`.
                Must have an ``id``; the embedding service logs
                and returns ``0`` for missing ids.
        """
        try:
            await self._embedding_service.update_skill_embeddings(skill)
        except Exception as e:
            skill_id = getattr(skill, "id", "?")
            logger.warning(
                "[SkillStore] Embedding refresh failed for skill "
                f"id={skill_id}; continuing without embeddings "
                f"(skill will be BM25-only): {e!s}"
            )


# ============================================================
# Module-level helpers
# ============================================================


def _project_skill(skill: Any) -> dict:
    """Project a :class:`Skill` row down to the list-view dict shape.

    Strips only the (potentially large) ``content`` column from the
    full :class:`Skill` row. Everything else (lifecycle counters,
    lineage origin, A/B-test group, timestamps) is forwarded so the
    Skills list page can render success-rate chips, A/B-test
    badges, and the deactivate / share actions without a per-row
    detail fetch.

    Used only by :meth:`SkillStoreService.list_skills` — callers that
    need the full body should call :meth:`SkillStoreService.view_skill`.

    Args:
        skill: A :class:`~daemon.repositories.skill.models.Skill`
            instance (or any object exposing the same attribute
            names — the projection uses ``getattr`` so test
            mocks work cleanly).

    Returns:
        Dict with the full set of list-view columns (everything
        except ``content``). See :data:`_LIST_SKILL_FIELDS` for the
        canonical column list.
    """
    return {field: getattr(skill, field) for field in _LIST_SKILL_FIELDS}