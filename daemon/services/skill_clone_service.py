"""Clone-on-miss service: bridges Skill Bank → Skills Evolution.

Phase 4 of the Skill Evolution System (see
``.agents/shared/planning/tester-skill-evolution/phase4-plan.md``).

When a skill is requested but doesn't exist in the project-scoped
``skills`` table, this service clones the template from
``skill_bank`` into ``skills`` with ``lineage_origin='bank_clone'``
and ``source_skill_bank_id`` set to the template's ID.

The ``auto_load`` flag propagates from the bank template to the
cloned skill — NOT hardcoded. This is the C2 fix called out in
the Phase 4 plan: the source-of-truth is the template, which in
turn was seeded from ``skill-set.yaml`` (legacy ``.md``) by the Phase 3 seeding
pipeline.

Embedding computation (W3) — design note
----------------------------------------

The Phase 4 plan referenced an ``embedding_service.refresh_embeddings_sync()``
method that does NOT exist on :class:`SkillEmbeddingService`.
That service only exposes an async ``update_skill_embeddings()``,
which is LLM-bound (it asks the chat model to generate 3-10
trigger queries, then embeds each).

The clone service therefore **does not** compute embeddings
synchronously. Cloned skills remain BM25-searchable immediately
after clone — Stage 1 of the search pipeline (:class:`SkillSearchService`)
runs on the raw ``name + description + content`` text and does
NOT require cached embeddings. Stage 2 (cosine re-rank) and
Stage 3 (LLM re-rank) gracefully degrade to "BM25-only" when
``skill_embeddings`` rows are missing — the search pipeline's
own contract.

The ``embedding_service`` constructor parameter is retained so
the architecture stays forward-compatible: a future commit can
add a sync ``refresh_embeddings_sync()`` and wire it here
without touching call sites. For now it is unused by design.

Async / sync surface
--------------------

* **Sync methods** — used by the synchronous prompt loader
  (Phase 5's ``append_auto_load_skills`` hook) and by ad-hoc
  CLI / migration scripts. Operate directly on the
  (synchronous) repositories.
* **Async methods** — thin ``asyncio.to_thread`` wrappers around
  their ``_sync`` counterparts. Used by the async injection
  pipeline (``instance_messaging.py``) so a blocking DB round
  trip doesn't stall the event loop.

Repositories are synchronous; that's the existing Phase 1
contract. Wrapping them in ``to_thread`` is the project's
standard pattern.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from ..repositories.skill.models import Skill, SkillBankItem
    from ..repositories.skill.repository import SkillRepository
    from ..repositories.skill.skill_bank_repository import SkillBankRepository

logger = logging.getLogger(__name__)


# Lineage origin tag written onto every skill cloned from the
# bank. Distinct from ``'imported'`` (raw import) and
# ``'evolved'`` (mutation pipeline descendants) so analytics can
# distinguish bank-clones from manual imports from evolutions.
_LINEAGE_ORIGIN_BANK_CLONE: str = "bank_clone"

# Default status stamped onto freshly-cloned skills. Mirrors the
# repository default but written explicitly here so the clone
# path doesn't depend on repository-level default changes.
_DEFAULT_CLONE_STATUS: str = "active"


class SkillCloneService:
    """Bridges ``skill_bank`` templates to project-scoped skills.

    Provides both sync and async methods.

    * Sync methods — for the prompt loader / instance lifecycle
      context (the synchronous call chain that builds system
      prompts before the agent runs).
    * Async methods — for the async injection pipeline; wrap the
      sync methods in ``asyncio.to_thread`` so a blocking DB
      round trip doesn't stall the event loop.

    Repositories are synchronous (Phase 1 contract); the async
    wrappers bridge the gap.

    Attributes:
        _skill_repo: :class:`SkillRepository` — read / write
            the project-scoped ``skills`` table.
        _skill_bank_repo: :class:`SkillBankRepository` — read
            the ``skill_bank`` table for templates.
        _embedding_service: Reserved for future use. NOT
            invoked by the current sync clone path — see the
            module-level docstring's "Embedding computation"
            section for why.
    """

    def __init__(
        self,
        skill_repo: SkillRepository,
        skill_bank_repo: SkillBankRepository,
        embedding_service: Optional[Any] = None,  # SkillEmbeddingService (reserved)
    ) -> None:
        """Store the repositories and the reserved embedding service.

        Args:
            skill_repo: :class:`SkillRepository` instance bound
                to the project's SQLAlchemy engine.
            skill_bank_repo: :class:`SkillBankRepository`
                instance bound to the same engine.
            embedding_service: Reserved for future use. The
                Phase 4 plan called for sync embedding
                generation via this object, but the existing
                :class:`SkillEmbeddingService` only exposes an
                async, LLM-bound ``update_skill_embeddings()``.
                Embeddings are therefore NOT computed during
                sync clone — see the module-level docstring's
                "Embedding computation" section for the design
                rationale and the BM25-only fallback contract.
        """
        self._skill_repo = skill_repo
        self._skill_bank_repo = skill_bank_repo
        self._embedding_service = embedding_service

    # ================================================================
    # SYNC METHODS (prompt loader / instance lifecycle)
    # ================================================================

    # Reserved for future single-skill lookups
    def clone_on_miss_sync(
        self,
        name: str,
        agent_id: str,
        project_id: str,
    ) -> Optional[Skill]:
        """Sync clone-on-miss: existing → refresh+return; miss → clone.

        Lookup order:

        1. ``skills`` table at ``(project_id, name, generation=0)``
           — if present, refresh its content from the bank template
           (when one exists and content differs) and return it.
           The refresh propagates include-merged bank updates,
           template-version bumps, and any other bank-side edits
           into already-cloned project skills without requiring a
           daemon restart + new instance spawn cycle.
        2. ``skill_bank`` table at ``(name, agent_id)`` (with
           cross-agent fallback) — if present, clone into the
           project scope.
        3. Otherwise return ``None`` — the template isn't in the
           bank either, so the caller (prompt loader / search)
           simply has nothing to inject for this name.

        Args:
            name: Skill name (the lookup key in both tables).
            agent_id: Agent ID (used to disambiguate the
                template lookup — multiple agents can own
                templates with the same name).
            project_id: Owning project for the cloned skill.
                Must be a non-None, non-empty string.

        Returns:
            The existing (refreshed) or newly-cloned :class:`Skill`
            row, or ``None`` when no template exists in the bank.
        """
        # Step 1: check if skill already exists in project scope.
        existing = self._skill_repo.get_by_name(
            project_id=project_id,
            name=name,
            generation=0,
        )

        # Step 2: find template in skill_bank. First an exact
        # ``(name, agent_id)`` lookup — the agent_id is the OWNING
        # agent of the template. When this misses it usually means
        # the skill was requested by a dispatcher that is NOT the
        # template owner (e.g. tester dispatches load_skill="unit-test"
        # to a worker; the worker's agent_id is "worker" but
        # "unit-test" is owned by "tester"). Skill names are
        # conventionally unique across the bank, so a name-only
        # fallback resolves the template the dispatcher intended.
        template = self._skill_bank_repo.get_by_name_and_agent(
            name, agent_id
        )
        if template is None:
            template = self._skill_bank_repo.get_by_name_any_agent(name)

        if existing is not None:
            # Refresh path — propagate bank updates (include
            # resolution, version bumps, manual edits) into the
            # already-cloned project skill. No-op when content is
            # already in sync (the common case). Skipped for
            # non-bank-clone lineages (evolved / imported skills
            # have their own content lifecycle).
            if template is not None:
                existing = self._refresh_from_template_sync(
                    existing, template
                )
            return existing

        if template is None:
            logger.debug(
                f"No skill template for clone: name={name}, "
                f"agent={agent_id}"
            )
            return None

        # Step 3: clone — auto_load comes from the template, NOT
        # from a hardcoded value (C2 fix).
        return self._clone_template_sync(template, project_id)

    def ensure_auto_load_skills_sync(
        self,
        agent_id: str,
        project_id: str,
    ) -> list[Skill]:
        """Sync: ensure all ``auto_load=True`` skills exist for the agent.

        Queries ``skill_bank`` for ``auto_load=True`` templates
        belonging to ``agent_id``, then for each one either
        returns the existing project-scoped row (idempotent
        contract) or clones it via :meth:`_clone_template_sync`.

        This is the Phase 5 hook point for the prompt loader:
        before every system-prompt build, the loader calls this
        to materialize the foundational skill set into project
        scope. On-demand (``auto_load=False``) templates are
        intentionally excluded — they land in a project only
        when explicitly requested.

        Args:
            agent_id: Agent ID whose auto-load templates should
                be materialized.
            project_id: Owning project for the cloned skills.

        Returns:
            List of :class:`Skill` instances — either existing
            or freshly-cloned. Order matches the bank-repo
            query order. May be empty when no ``auto_load=True``
            templates exist for the agent.
        """
        templates = self._skill_bank_repo.get_auto_load_by_agent(agent_id)
        return self._clone_missing_templates_sync(templates, project_id)

    def ensure_all_skills_sync(
        self,
        agent_id: str,
        project_id: str,
    ) -> list[Skill]:
        """Sync: ensure ALL skills (auto_load + on-demand) exist for the agent.

        Used by the injection pipeline (see ``instance_messaging.py``)
        to guarantee every agent skill is materialized in project
        scope BEFORE the BM25 search runs. Cloning is idempotent
        — existing rows are returned, not re-cloned.

        Trade-off vs :meth:`ensure_auto_load_skills_sync`: this
        may clone a larger batch on the first call (including
        on-demand skills that may never be selected in the
        current task). The injection path prefers correctness
        over sparseness — better to have every skill visible to
        BM25 than to discover a missing template only after a
        search miss.

        Args:
            agent_id: Agent ID whose templates should be
                materialized.
            project_id: Owning project for the cloned skills.

        Returns:
            List of :class:`Skill` instances — either existing
            or freshly-cloned. Order matches the bank-repo
            query order.
        """
        templates = self._skill_bank_repo.list_by_agent(agent_id)
        return self._clone_missing_templates_sync(templates, project_id)

    def _clone_missing_templates_sync(
        self,
        templates: list[SkillBankItem],
        project_id: str,
    ) -> list[Skill]:
        """Clone or refresh templates into project-scoped skills.

        For each template:

        * Existing project skill → :meth:`_refresh_from_template_sync`
          propagates bank-side updates (include resolution, version
          bumps) into the already-cloned row. No-op when content
          already matches.
        * Missing project skill → clone via
          :meth:`_clone_template_sync`.

        Order of the returned list matches the input template order.

        Args:
            templates: Bank templates to materialize into project
                scope (already filtered by the caller — e.g.
                auto-load only, or all).
            project_id: Owning project for the cloned skills.

        Returns:
            List of :class:`Skill` instances — either existing
            (possibly refreshed) or freshly-cloned.
        """
        results: list[Skill] = []

        for template in templates:
            # Look up existing first.
            existing = self._skill_repo.get_by_name(
                project_id=project_id,
                name=template.name,
                generation=0,
            )
            if existing is not None:
                # Refresh path — propagate bank updates into the
                # already-cloned project skill. Returns the
                # (possibly updated) row; no-op when content is
                # already in sync.
                refreshed = self._refresh_from_template_sync(
                    existing, template
                )
                results.append(refreshed)
                continue

            cloned = self._clone_template_sync(template, project_id)
            if cloned is not None:
                results.append(cloned)

        return results

    def _refresh_from_template_sync(
        self,
        skill: Skill,
        template: SkillBankItem,
    ) -> Skill:
        """Refresh a project skill's content from its bank template.

        Propagates bank-side updates (include resolution, version
        bumps, manual edits) into already-cloned project skills.
        This is the propagation path for the seed-time include
        feature: when ``skill-set.yaml`` is re-seeded with new
        ``include:`` directives, the bank's ``content`` is
        re-rendered, and this method carries the new content into
        every project that already cloned the skill — without
        requiring a daemon restart + fresh instance spawn.

        Refresh scope: only template-derived fields are touched
        (``content``, ``description``, ``category``, ``auto_load``).
        Metrics counters (``total_selections``, ``total_applied``,
        ``total_completions``, ``total_fallbacks``,
        ``consecutive_failures``), status, lineage, generation,
        and A/B test group are preserved — they belong to the
        project-scoped skill's own lifecycle, not the template.

        Lineage guard: only ``bank_clone`` skills are refreshable.
        ``evolved`` and ``imported`` lineages have their own content
        lifecycle (the skill-keeper agent may have evolved them, or
        a user may have imported a custom body) — overwriting them
        would silently destroy that work.

        The refresh is a no-op when content + description + category
        + auto_load all already match the template (the common case
        on every lookup after the first refresh). This keeps the
        per-lookup cost at one DB read (the bank fetch the caller
        already did) plus one DB write only when content actually
        changed.

        Args:
            skill: The existing project-scoped skill row.
            template: The bank template to refresh from.

        Returns:
            The refreshed :class:`Skill` (re-fetched from the DB
            after the update so the caller sees the new state).
            When the lineage guard rejects the refresh, the
            original ``skill`` is returned unchanged.
        """
        # Lineage guard — only bank-cloned skills are refreshable.
        # Evolved/imported skills have their own content lifecycle.
        if getattr(skill, "lineage_origin", "") != _LINEAGE_ORIGIN_BANK_CLONE:
            return skill

        # Common-case short-circuit — skip the write when nothing
        # changed. ``content`` is the dominant signal; the others
        # are cheap to also check inline.
        if (
            getattr(skill, "content", None) == template.content
            and getattr(skill, "description", None) == template.description
            and getattr(skill, "category", None) == template.category
            and bool(getattr(skill, "auto_load", False))
            == bool(template.auto_load)
        ):
            return skill

        logger.info(
            f"Refreshing project skill from bank: "
            f"name={template.name}, "
            f"project={skill.project_id[:8] if skill.project_id else '?'}, "
            f"reason=content_changed"
        )
        updated = self._skill_repo.update(
            skill.id,
            content=template.content,
            description=template.description,
            category=template.category,
            auto_load=bool(template.auto_load),
        )
        # Fall back to the in-memory ``skill`` if the update lost
        # the row race (extremely rare — concurrent delete between
        # our get_by_name and update). The caller still gets a
        # usable object even if it's the pre-refresh state.
        return updated if updated is not None else skill

    def _clone_template_sync(
        self,
        template: SkillBankItem,
        project_id: str,
    ) -> Skill:
        """Clone a ``SkillBankItem`` into a project-scoped ``Skill``.

        ``auto_load`` is read from ``template.auto_load`` — NOT
        hardcoded (C2 fix; the source-of-truth is the bank
        template, which was seeded from ``skill-set.yaml`` (legacy ``.md``) by the
        Phase 3 pipeline).

        ``source_skill_bank_id`` is set to the template's ID
        so the cloned row links back to its origin. The
        ``source_skill_bank_id`` column is declared as a *soft*
        FK (no DB-level constraint) so this works regardless of
        whether the template row is later mutated or deleted.

        Embeddings are NOT computed here — sync clones stay
        BM25-searchable. See the module docstring's "Embedding
        computation" section for the design rationale.
        """
        try:
            cloned = self._skill_repo.create(
                name=template.name,
                description=template.description,
                content=template.content,
                project_id=project_id,
                category=template.category,
                lineage_origin=_LINEAGE_ORIGIN_BANK_CLONE,
                generation=0,
                status=_DEFAULT_CLONE_STATUS,
                is_active=True,
                # C2 fix: auto_load is propagated from the template,
                # NOT hardcoded. Phase 3 seeded this from
                # ``skill-set.yaml`` (legacy ``.md``) ``auto_load:`` field.
                auto_load=template.auto_load,
                source_skill_bank_id=template.id,
            )
            logger.info(
                f"Cloned skill from bank: name={template.name}, "
                f"project={project_id[:8]}..., auto_load={template.auto_load}, "
                f"source_skill_bank_id={template.id}"
            )
        except IntegrityError:
            # Race loser — another instance already created this skill.
            # Re-query for the winning row and return it; this is expected
            # behavior under concurrent spawns, not a DB outage.
            logger.debug(
                f"Clone race lost for {template.name}, re-querying existing skill"
            )
            existing = self._skill_repo.get_by_name(
                project_id=project_id,
                name=template.name,
                generation=0,
            )
            if existing is None:
                # Not a race — re-raise so the caller sees the real error.
                raise
            return existing

        # Embeddings computed lazily by async path; sync clones
        # are BM25-searchable. SkillSearchService gracefully
        # degrades to BM25-only when embeddings are missing —
        # Stage 1 (BM25) does not require embeddings.
        return cloned

    # ================================================================
    # ASYNC METHODS (injection pipeline + lifecycle)
    # ================================================================

    async def ensure_all_skills_async(
        self,
        agent_id: str,
        project_id: str,
    ) -> list[Skill]:
        """Async wrapper for :meth:`ensure_all_skills_sync`.

        Used by the injection pipeline (``instance_messaging.py``)
        which runs in the async event loop. The underlying repo
        calls are synchronous so we hop to the worker pool via
        ``asyncio.to_thread``.

        Args:
            agent_id: Agent ID.
            project_id: Owning project.

        Returns:
            List of project-scoped :class:`Skill` rows
            (existing or freshly-cloned).
        """
        return await asyncio.to_thread(
            self.ensure_all_skills_sync, agent_id, project_id
        )

    async def clone_on_miss_async(
        self,
        name: str,
        agent_id: str,
        project_id: str,
    ) -> Optional[Skill]:
        """Async wrapper for :meth:`clone_on_miss_sync`.

        Args:
            name: Skill name.
            agent_id: Agent ID for the bank-template lookup.
            project_id: Owning project for the cloned row.

        Returns:
            The existing or newly-cloned :class:`Skill`, or
            ``None`` when no template exists.
        """
        return await asyncio.to_thread(
            self.clone_on_miss_sync, name, agent_id, project_id
        )

    async def ensure_auto_load_skills_async(
        self,
        agent_id: str,
        project_id: str,
    ) -> list[Skill]:
        """Async wrapper for :meth:`ensure_auto_load_skills_sync`.

        Provided so the Phase 5 prompt-loader hook can be called
        from an async context (e.g. a hook point inside an async
        lifecycle method) without forcing that hook to live in
        the sync prompt-build chain.

        Args:
            agent_id: Agent ID.
            project_id: Owning project.

        Returns:
            List of auto-load :class:`Skill` rows (existing or
            freshly-cloned).
        """
        return await asyncio.to_thread(
            self.ensure_auto_load_skills_sync, agent_id, project_id
        )
