"""The ari-lane POK delivery seam (G-2 / §4.5.1 — POK Phase 1 T7).

STRUCTURAL FACT (the design's own framing): today the Related-Project
POK block injects only into PROJECT-LINKED working agents' contexts —
Ari (the chat-facing dispatcher, global-operator tier) is UNSCOPED and
receives NO project injection. The ER-1 ruling requires Ari to hold
the OPERATION slice AT DISPATCH TIME.

THE SEAM SITE (bounded discovery, 2026-11-07): Ari's dispatch surface
is ``job_create`` (``daemon/tools/job_queue.py``) — the tool Ari uses
to send work to a project's team (``job_create(agent_id='leader',
message=…, project_id=X)``). The seam fires there, AFTER the enqueue
succeeds (a dispatch that never landed carries no operating
obligations).

GATES (§4.5.1 verbatim):

* target ``project_id`` is set AND ``!= SYSTEM_DEFAULT_PROJECT_ID``
  (read at CALL time — the constant is ``None`` until startup; AC24
  negative leg: no block on the default project);
* the caller is ``ari`` — the FACTORY-CAPTURED ``caller_agent_id``
  (attribution ≠ authorization; ari ∈ the operation default set by
  the ruling);
* the same budget / fence / family semantics as every other view —
  the block is assembled by the SAME ``admit_view`` pipeline with
  ``agent_id='ari'`` (the operation default set includes ari).

FRESHNESS (per-dispatch): the block is assembled FRESH at every
dispatching call — Ari is long-lived/reused, so per-dispatch assembly
is the freshness mechanism; ``pok_query`` remains the mid-lane
refresh.

STABLE ID (``pok:{project_id}`` within Ari's own instance-context
namespace): the injection lane drains tool-path entries with fresh
drain-time ids (no stable-id channel exists on
``Manager.set_injection``), so the contract is honored SEMANTICALLY —
the seam injects ONCE per (instance, project), tracked in the
instance metadata (``pok_ari_seam_last_project``). Re-dispatches to
the SAME project do not re-inject (the block is already in Ari's
checkpoint; identical content; pok_query refreshes mid-lane); a
dispatch to a DIFFERENT project injects that project's block — one
block per project, never duplicated.

DELIVERY: ``Manager.set_injection`` (the RAM lane — job_create runs
mid-turn, so the block lands on Ari's next LLM call within the same
session). The content carries the canonical ``[SYSTEM CONTEXT:
Project Operating Knowledge]`` wrapper (the builder's block, wrapper
inclusive, ≤2,900 per-view budget).

NEVER-RAISE: every failure mode logs a ``[POK:Degraded]`` WARNING and
returns quietly — the dispatch result is NEVER touched.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from daemon import constants as _constants
from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME

logger = logging.getLogger(__name__)

#: The instance-metadata key recording the LAST project this instance's
#: seam injected (the semantic stable-id namespace: one block per
#: project). A re-dispatch to the same project never re-injects.
POK_ARI_SEAM_LAST_PROJECT = "pok_ari_seam_last_project"

#: The injection-lane provenance marker (the ``source`` stamp —
#: ``internal_agent:<origin>`` is the house shape for agent-tool
#: injections).
POK_ARI_SEAM_SOURCE = "internal_agent:pok_ari_seam"

#: The factory-captured agent id the seam serves (the ruling's
#: audience: ari ∈ the operation default set).
ARI_AGENT_ID = "ari"


def _resolve_project_name(manager: Any, project_id: str) -> str | None:
    project_repo = getattr(manager, "_project_repository", None)
    if project_repo is None:
        return None
    try:
        project = project_repo.get(project_id)
    except Exception:  # noqa: BLE001 — defensive
        return None
    return getattr(project, "name", None) if project is not None else None


async def maybe_inject_ari_pok_block(
    *,
    manager: Any,
    current_instance_id: str,
    caller_agent_id: str,
    project_id: str | None,
) -> None:
    """The ari dispatch seam — best-effort, never raises.

    Called from ``job_create`` AFTER a successful enqueue. Gates,
    fresh per-dispatch read, once-per-(instance, project) delivery,
    ``set_injection`` with the canonical wrapper.
    """
    try:
        if manager is None or not current_instance_id:
            return
        # Gate 1: the factory-captured caller is ari.
        if (caller_agent_id or "") != ARI_AGENT_ID:
            return
        # Gate 2: the target is a real, NON-default project (AC24
        # negative leg — call-time constant read + the name fallback).
        if not project_id:
            return
        project_name = _resolve_project_name(manager, project_id)
        if project_id == _constants.SYSTEM_DEFAULT_PROJECT_ID or (
            project_name == SYSTEM_DEFAULT_PROJECT_NAME
        ):
            return

        engine = getattr(manager, "_engine", None)
        instance_repository = getattr(manager, "_instance_repository", None)
        if engine is None or instance_repository is None:
            return

        # Semantic stable id: once per (instance, project).
        last_project = await asyncio.to_thread(
            _read_last_project, instance_repository, current_instance_id
        )
        if last_project == project_id:
            return

        # Fresh per-dispatch read → the operation-audience view (the
        # SAME admission pipeline: budget/fence/family semantics).
        from .context_messages import (  # noqa: PLC0415
            CONTEXT_PREFIX,
            CONTEXT_SUFFIX,
        )
        from .pok_injection import (  # noqa: PLC0415
            POK_BLOCK_TITLE,
            admit_view,
            render_pok_block,
        )
        from .pok_service import POKService  # noqa: PLC0415

        def _build() -> str | None:
            service = POKService(engine)
            rows = service._repo.list_entries(project_id, statuses=("active",))
            if not rows:
                return None
            result = admit_view(
                rows,
                agent_id=ARI_AGENT_ID,
                query="",  # the dispatch carries no query — the selector's
                # deterministic floor baseline ranks the view
                embeddings_map=service._repo.embeddings_map(project_id),
                rank_fn=service.rank_for_view,
            )
            body = render_pok_block(result)
            if not body:
                return None
            return f"{CONTEXT_PREFIX}{POK_BLOCK_TITLE}{CONTEXT_SUFFIX}{body}"

        content = await asyncio.to_thread(_build)
        if not content:
            return

        # Deliver through the RAM injection lane (job_create runs
        # mid-turn; the block lands on the next LLM call).
        set_injection = getattr(manager, "set_injection", None)
        if set_injection is None:
            return
        set_injection(
            current_instance_id,
            content,
            source=POK_ARI_SEAM_SOURCE,
        )
        logger.info(
            "[POK] ari seam injected the operation view for project "
            "%s into %s...",
            project_id,
            current_instance_id[:8],
        )

        # Record the namespace slot AFTER a confirmed injection (the
        # AM-22 discipline — a failed write retries next dispatch).
        try:
            await asyncio.to_thread(
                instance_repository.set_metadata,
                current_instance_id,
                POK_ARI_SEAM_LAST_PROJECT,
                project_id,
            )
        except Exception as exc:  # noqa: BLE001 — self-heals next dispatch
            logger.warning(
                "[POK] ari seam metadata write failed for %s...: %s — "
                "the block may re-inject on the next dispatch",
                current_instance_id[:8],
                exc,
            )
    except Exception as exc:  # noqa: BLE001 — §4.9 never-raise
        logger.warning(
            "[POK:Degraded] ari seam failed (%s) — dispatch result "
            "untouched",
            exc,
        )


def _read_last_project(instance_repository: Any, instance_id: str) -> str | None:
    row = instance_repository.get(instance_id)
    if row is None:
        return None
    meta = getattr(row, "instance_metadata", None) or {}
    value = meta.get(POK_ARI_SEAM_LAST_PROJECT)
    return str(value) if value else None
