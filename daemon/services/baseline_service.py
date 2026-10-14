"""Baseline service — thin async facade for the predefined-agent R-B2
template-drift protocol (User Directive #5B, 2026-10-14,
RESOLUTION-CORE).

The service mirrors :class:`daemon.services.skill_store_service` (the
canonical thin-async-facade pattern, ``skill_store_service.py:1-60``):
holds no engine, every public method = one ``await
asyncio.to_thread(...)`` wrapping a single repo call. The repo at
:class:`daemon.repositories.prompt_version.repository.BaselineRepository`
holds the SQL behavior; this service is the consumer-facing surface
the API lifespan + sibling callers reach.

Operates on TWO responsibilities:

* ``record_initial_baselines`` — R-B1 seed pass: walk every
  predefined agent's CORE-artifact files at boot and stamp a
  baseline row carrying the shipped sha256. Idempotent (re-runs on
  unchanged files = no events, no row churn).
* ``detect_template_drift`` — R-B2 auto-reset pass: walk the
  agent's CORE artifacts, compare live files vs recorded
  baselines, and stamp a ``TEMPLATE_RESET`` audit event per
  drift (with payload ``{old_hash, new_hash,
  selection_preserved}``). Optionally runs the auto-reset
  (the default; ``run_reset=False`` for a dry-run preview).

Both methods short-circuit on ``is_custom_agent=True`` (the
A2/R-A2 lifecycle coder owns the full custom-agent ruling; this
service extends the resolution-side hook per User Directive #5A
— custom agents have no template fallback, so the baseline
table carries no rows for them).

Boot wiring — the API lifespan calls
``record_initial_baselines_for_all_predefined_agents`` once at
daemon startup (next to the existing
``PromptVersion.metadata.create_all(manager.engine)`` /
``PromptAuditEvent.metadata.create_all(...)`` lines, and the
``StoredWatch.metadata.create_all(...)`` precedent — see
``daemon/api.py``-714-744). The one-line boot call lands the
baselines idempotently; the detect service runs on demand (a
sibling service wires it to whatever trigger the lifecycle
chooses; documented deferred hook).

MEMORY-class carve-out: the baseline service rejects MEMORY-class
artifacts (memory / user / growth / ``memories/<slug>``) at the
``_validate_artifact_class_core_only`` gate — those families have
no template fallback path (R-B2 is CORE-only). The
``PromptVersionRepository.create_memory_version`` path handles
MEMORY rows separately (transition-free).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from daemon.repositories.prompt_version.repository import (
    ARTIFACT_CLASS_CORE,
    PROMPT_ARTIFACTS,
    BaselineRepository,
    PromptVersionRepository,
    TemplateDriftResult,
)

logger = logging.getLogger(__name__)


class BaselineService:
    """Async facade over :class:`BaselineRepository`.

    Mirrors the project pattern at
    :mod:`daemon.services.prompt_version_service` (the
    prompt-version async facade): holds no engine, every public
    method is a thin ``await asyncio.to_thread(...)`` wrapping
    the corresponding sync repo call. The repo reference + the
    cross-repo (prompt_version_repo) handle is the only state.

    Attributes:
        _repo: The :class:`BaselineRepository` bound to the
            project's SQLAlchemy engine.
        _prompt_version_repo: The :class:`PromptVersionRepository`
            (cross-repo dependency — needed for the
            ``select_selection`` R-B3 preservation check inside
            ``detect_template_drift``). The two repos share the
            same engine so transactions can compose (the auto-reset
            audit-event stamp lands inside the repo's
            ``engine.begin()`` block).
    """

    def __init__(
        self,
        repo: Any,
        prompt_version_repo: Optional[PromptVersionRepository] = None,
    ) -> None:
        """Store the repo handles (no engine held).

        Args:
            repo: :class:`BaselineRepository` instance.
            prompt_version_repo: Optional
                :class:`PromptVersionRepository` cross-reference
                (R-B3 ``get_selection`` check). When ``None``,
                ``detect_template_drift`` runs without the
                preservation check (R-B3 detection is reported as
                ``selection_preserved=False`` for every drift;
                the audit-event payload still carries the field).
        """
        self._repo = repo
        self._prompt_version_repo = prompt_version_repo

    # ─────────────────────────────────────────────────────────────────
    # R-B1 — initial-baseline seeding
    # ─────────────────────────────────────────────────────────────────

    async def record_initial_baselines(
        self,
        agent_id: str,
        version_tag: str,
        agents_root: Any,
        *,
        actor: Any,
        artifact_filter: Optional[frozenset[str]] = None,
        is_custom_agent: bool = False,
    ) -> list:
        """Seed baselines for a predefined agent (R-B1).

        Thin async wrapper; the sync implementation lives in
        :meth:`BaselineRepository.record_initial_baselines`. Idempotent
        — re-running on unchanged files produces identical rows
        (the UPSERT swallows the no-op).

        Custom agents (``is_custom_agent=True``) short-circuit to
        an empty result; the A2/R-A2 ruling (lifecycle coder owns)
        says custom agents have no template fallback, so the
        baseline table carries no rows for them.

        Returns the freshly-recorded baseline rows (loaded-after-
        insert snapshots; empty for custom agents).
        """
        return await asyncio.to_thread(
            self._repo.record_initial_baselines,
            agent_id,
            version_tag,
            agents_root,
            actor=actor,
            artifact_filter=artifact_filter,
            is_custom_agent=is_custom_agent,
        )

    async def record_initial_baselines_for_all_predefined_agents(
        self,
        predefined_agent_ids: list[tuple[str, str]],
        agents_root: Any,
        *,
        actor: Any,
        artifact_filter: Optional[frozenset[str]] = None,
    ) -> dict[tuple[str, str], list]:
        """Boot seed — record_initial_baselines for every predefined agent.

        A thin-loop wrapper over :meth:`record_initial_baselines`
        that the API lifespan calls once at daemon startup (the
        boot-call site is next to the
        ``PromptVersion.metadata.create_all(...)`` calls at
        ``daemon/api.py:743-744`` and the StoredWatch precedent).

        Args:
            predefined_agent_ids: List of ``(agent_id,
                version_tag)`` tuples (the tagged-sibling copy
                convention per F9 / D15/D16 — ``developer`` /
                ``''`` and ``developer`` / ``'v2'`` are two
                entries).
            agents_root: Path to the canonical ``agents`` root
                directory.
            actor: Audit actor (the boot internal actor for the
                boot-time lifecycle).
            artifact_filter: Optional CORE-artifact subset
                (default = conservative-default
                :data:`PROMPT_ARTIFACTS`).

        Returns:
            Dict mapping ``(agent_id, version_tag)`` → the
            recorded-baseline row list. The dict is empty when
            the registry has no predefined agents (rare, but the
            lifecycle coder's A2/R-A2 ruling may skip the
            registry hook entirely on cold-booted hosts).
        """
        out: dict[tuple[str, str], list] = {}
        for agent_id, version_tag in predefined_agent_ids:
            rows = await self.record_initial_baselines(
                agent_id=agent_id,
                version_tag=version_tag,
                agents_root=agents_root,
                actor=actor,
                artifact_filter=artifact_filter,
            )
            out[(agent_id, version_tag)] = rows
        return out

    # ─────────────────────────────────────────────────────────────────
    # R-B2 — auto-reset on template drift
    # ─────────────────────────────────────────────────────────────────

    async def detect_template_drift(
        self,
        agent_id: str,
        version_tag: str,
        agents_root: Any,
        *,
        actor: Any,
        artifact_filter: Optional[frozenset[str]] = None,
        run_reset: bool = True,
        is_custom_agent: bool = False,
    ) -> list[TemplateDriftResult]:
        """Detect shipped-template drift + (optionally) run R-B2 auto-reset.

        See :meth:`BaselineRepository.detect_template_drift` for
        the full semantics (R-B1 / R-B2 / R-B3 conformance). The
        service wrapper threads the ``is_custom_agent`` flag
        through; custom agents short-circuit to ``[]``.

        Args:
            agent_id: Resolved agent identity.
            version_tag: Version tag (``""`` for base; F9).
            agents_root: Path to the canonical ``agents`` root
                directory.
            actor: Audit actor.
            artifact_filter: Optional CORE-artifact subset.
            run_reset: When ``True`` (default), stamp a
                ``TEMPLATE_RESET`` audit row + re-point the
                baseline per drift. When ``False``, do the
                detection walk WITHOUT acting (useful for
                dry-run / preview surfaces).
            is_custom_agent: When ``True``, short-circuit to
                ``[]`` (User Directive #5A resolution-side hook).

        Returns:
            One :class:`TemplateDriftResult` per inspected
            artifact (skip / no-drift / drift-detected). The
            caller can compute the journal of events from the
            list.
        """
        if is_custom_agent:
            return []
        # The detect path needs the prompt-version repo for the
        # R-B3 selection-preservation check. The lifecycle is
        # allowed to construct the service WITHOUT the
        # cross-reference (the repo's ``None`` branch records
        # ``selection_preserved=False``).
        return await asyncio.to_thread(
            self._repo.detect_template_drift,
            agent_id,
            version_tag,
            agents_root,
            actor=actor,
            prompt_version_repo=self._prompt_version_repo,
            artifact_filter=artifact_filter,
            run_reset=run_reset,
            is_custom_agent=is_custom_agent,
        )

    async def detect_drift_for_all_predefined_agents(
        self,
        predefined_agent_ids: list[tuple[str, str]],
        agents_root: Any,
        *,
        actor: Any,
        artifact_filter: Optional[frozenset[str]] = None,
        run_reset: bool = True,
    ) -> dict[tuple[str, str], list[TemplateDriftResult]]:
        """Boot detect — walk every predefined agent's CORE artifacts.

        The boot orchestrator (API lifespan) calls
        ``record_initial_baselines_for_all_predefined_agents``
        FIRST (idempotent — lazy-seeds any missing baselines) and
        THEN this method (the detect pass lands the audit events
        + baseline re-points for any drifts found).

        Returns a dict mapping ``(agent_id, version_tag)`` →
        per-agent list of :class:`TemplateDriftResult` (empty
        lists when no drifts found — idempotent: re-run on an
        unchanged tree yields ``[]`` for every agent).
        """
        out: dict[tuple[str, str], list[TemplateDriftResult]] = {}
        for agent_id, version_tag in predefined_agent_ids:
            results = await self.detect_template_drift(
                agent_id=agent_id,
                version_tag=version_tag,
                agents_root=agents_root,
                actor=actor,
                artifact_filter=artifact_filter,
                run_reset=run_reset,
                is_custom_agent=False,
            )
            out[(agent_id, version_tag)] = results
        return out


__all__ = [
    "BaselineService",
]
