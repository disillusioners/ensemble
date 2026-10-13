"""Prompt-version service — thin async facade (agent-prompt-immutability Phase A).

Phase A's service layer for the prompt-version DB foundation. Mirrors
the :class:`SkillStoreService` facade at
:mod:`daemon.services.skill_store_service` (the project's canonical
thin-async-facade docstring, lines 1-60): holds no engine, every
public method = one ``await asyncio.to_thread(...)`` wrapping a
single repo call. Multiple repo calls in one method share the
same thread to keep the session boundary clean.

Why a service layer
-------------------

The repository (:mod:`daemon.repositories.prompt_version.repository`)
stays the source of truth for SQL behavior — every CRUD primitive is
a synchronous method that opens its own ``Session`` and commits.
The service layer adds:

* **Async facade.** Repo methods are sync; callers in the daemon's
  async event loop hop through ``asyncio.to_thread`` so the DB
  never blocks the loop. The service exposes ``async def`` methods
  that callers can ``await`` directly.

* **Routers reach via the manager** (manager.py:1171-1173 next to
  ``_skill_bank_repo``) — :attr:`InstanceManager._prompt_version_service`
  holds the singleton.

* **No LLM / no embedding / no async I/O.** Pure daemon-DB layer;
  Phase A does NOT touch the loader hook or inner_soul — those
  live in the Phase B / Phase C scope.

* **Loader hook takes the SYNC repo directly** (A4/C6) — the
  loader's 4 call sites are sync; routing them through the
  async facade would deadlock the event loop or force a mid-build
  refactor. This service is the consumer-facing facade for routers
  + inner_soul/agent_mother callers only.

Composition
-----------

* All 13 A2 methods pass through (one-to-one with the repo).
* The A5/C4 composite ``create_and_submit`` passes through.
* The A8 ``get_effective_rows`` passes through (loader's
  fingerprint-bust-on-get hook reads this on every
  ``load_and_cache_prompt`` call — but the loader uses the SYNC
  repo, not this facade, per A4).

Design notes
------------

* **No engine held** — service constructor takes the repo only.
* **Defensive repo call wrapping.** Each ``async`` method awaits
  a single ``asyncio.to_thread`` invocation that calls into the
  repo. Multiple repo calls (e.g. the SELECT-MAX + INSERT in
  :meth:`PromptVersionRepository.create_draft`) live inside the
  same thread to keep the DB session boundary clean — the repo
  handles those for us.
* **No global state.** The service is trivially testable with
  mock repos.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional, Union

logger = logging.getLogger(__name__)


# ============================================================
# PromptVersionService
# ============================================================


class PromptVersionService:
    """Async facade over :class:`PromptVersionRepository`.

    Every public method is a thin ``await asyncio.to_thread(...)``
    wrapper around the corresponding sync repo method. Holds no
    engine; the repo reference is the sole dependency.

    Attributes:
        _repo: :class:`PromptVersionRepository` bound to the
            project's SQLAlchemy engine.
    """

    def __init__(self, repo: Any) -> None:
        """Store the repo reference.

        Args:
            repo: :class:`PromptVersionRepository` instance bound to
                the project's SQLAlchemy engine. The service holds
                NO engine — all DB work goes through the repo.
        """
        self._repo = repo

    # --------------------------------------------------------
    # Create
    # --------------------------------------------------------

    async def create_draft(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        content: str,
        actor: Any,
        *,
        state: str = "draft",
    ) -> Any:
        """Create a new draft (A2). Async wrapper."""
        return await asyncio.to_thread(
            self._repo.create_draft,
            agent_id,
            version_tag,
            artifact,
            content,
            actor,
            state=state,
        )

    async def create_and_submit(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        content_or_mutator: Union[str, Callable[[Optional[str]], str]],
        actor: Any,
        *,
        file_fallback: Optional[str] = None,
    ) -> Any:
        """Create draft + submit for approval atomically (A5/C4)."""
        return await asyncio.to_thread(
            self._repo.create_and_submit,
            agent_id,
            version_tag,
            artifact,
            content_or_mutator,
            actor,
            file_fallback=file_fallback,
        )

    # --------------------------------------------------------
    # State transitions
    # --------------------------------------------------------

    async def submit_for_approval(
        self,
        version_id: str,
        actor: Any,
    ) -> Any:
        """Transition ``draft → pending_approval`` (A2)."""
        return await asyncio.to_thread(
            self._repo.submit_for_approval,
            version_id,
            actor,
        )

    async def approve(
        self,
        version_id: str,
        actor: Any,
    ) -> Any:
        """Transition ``pending_approval → approved`` (A2). HTTP actor only."""
        return await asyncio.to_thread(
            self._repo.approve,
            version_id,
            actor,
        )

    async def reject(
        self,
        version_id: str,
        actor: Any,
    ) -> Any:
        """Transition ``pending_approval → rejected`` (A2). HTTP actor only."""
        return await asyncio.to_thread(
            self._repo.reject,
            version_id,
            actor,
        )

    async def atomic_transition(
        self,
        version_id: str,
        from_state: str,
        to_state: str,
        actor: Any,
        *,
        approver_subject: Optional[str] = None,
        approver_auth_method: Optional[str] = None,
    ) -> Any:
        """Generic guarded state transition (A2 / A1)."""
        return await asyncio.to_thread(
            self._repo.atomic_transition,
            version_id,
            from_state,
            to_state,
            actor,
            approver_subject=approver_subject,
            approver_auth_method=approver_auth_method,
        )

    # --------------------------------------------------------
    # Selection pointer
    # --------------------------------------------------------

    async def set_selection(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        version_id: str,
        actor: Any,
    ) -> None:
        """Set the per-key one-selection-pointer (A2 / F5 / R3)."""
        return await asyncio.to_thread(
            self._repo.set_selection,
            agent_id,
            version_tag,
            artifact,
            version_id,
            actor,
        )

    async def clear_selection(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        actor: Any,
    ) -> None:
        """Clear the per-key one-selection-pointer (A2 / F5 / R3)."""
        return await asyncio.to_thread(
            self._repo.clear_selection,
            agent_id,
            version_tag,
            artifact,
            actor,
        )

    # --------------------------------------------------------
    # Reads
    # --------------------------------------------------------

    async def get_selection(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
    ) -> Any:
        """Fetch the currently-selected row for the key (A2)."""
        return await asyncio.to_thread(
            self._repo.get_selection,
            agent_id,
            version_tag,
            artifact,
        )

    async def list_versions(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        """List versions for a key, newest first (A2)."""
        return await asyncio.to_thread(
            self._repo.list_versions,
            agent_id,
            version_tag,
            artifact,
            limit=limit,
            offset=offset,
        )

    async def get_version(self, version_id: str) -> Any:
        """Fetch a single row by PK (A2)."""
        return await asyncio.to_thread(self._repo.get_version, version_id)

    async def get_latest_approved(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
    ) -> Any:
        """Fetch the latest-approved row for the key (A2 / F6)."""
        return await asyncio.to_thread(
            self._repo.get_latest_approved,
            agent_id,
            version_tag,
            artifact,
        )

    async def get_effective(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
    ) -> Optional[str]:
        """Resolve the effective prompt content (A2 / F6 / A8).

        Precedence: ``selected_version.content`` >
        ``latest_approved.content`` > ``None``.
        """
        return await asyncio.to_thread(
            self._repo.get_effective,
            agent_id,
            version_tag,
            artifact,
        )

    async def get_effective_rows(
        self,
        agent_id: str,
        version_tag: str,
    ) -> list:
        """ONE-SELECT effective read for the (agent_id, version_tag) pair (A8).

        Returns the selected + latest-approved row ids + content for
        every artifact that has DB rows, sorted by artifact name.
        The loader hook's fingerprint-bust-on-get check consumes
        this (BUT via the SYNC repo, A4 — this facade is for
        routers + inner_soul/agent_mother callers).
        """
        return await asyncio.to_thread(
            self._repo.get_effective_rows,
            agent_id,
            version_tag,
        )

    # --------------------------------------------------------
    # Audit
    # --------------------------------------------------------

    async def append_audit_event(
        self,
        agent_id: str,
        version_tag: str,
        artifact: str,
        version_id: str,
        event_type: str,
        actor: Any,
        payload: Optional[dict] = None,
    ) -> Any:
        """Append a single audit row (A2).

        Standalone variant — most audit rows are stamped by the
        mutating repo methods inside their ``engine.begin()``
        transaction (A3). Use this only when an audit event must
        be recorded outside a repo-level mutation (rare).
        """
        return await asyncio.to_thread(
            self._repo.append_audit_event,
            agent_id,
            version_tag,
            artifact,
            version_id,
            event_type,
            actor,
            payload,
        )


__all__ = ["PromptVersionService"]