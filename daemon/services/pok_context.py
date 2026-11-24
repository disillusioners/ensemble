"""The POK context block — the audience-scoped operating-knowledge seam.

The functional-gate fix round (2026-11-24, the tester's NO-GO → GO
conversion): the POK context block MOVES here from
``daemon/services/context_messages.py`` (which the lane's own growth
had taken to 3015 lines — over the 3000-line H2 ceiling the module-
split pin enforces). The module is a MOVE, not a fork: every moved
name is re-exported from ``context_messages``, so the historical
import surface (``from daemon.services.context_messages import
build_pok_context_message, CONTEXT_KIND_POK,
_maybe_pok_context_message``) keeps working — the same discipline as
the P6 H2 joined-room extraction (``joined_room_context.py`` /
``context_block.py``).

What lives here (all moved byte-intact, zero behavior change):

* ``CONTEXT_KIND_POK`` — the block kind (stable message id
  ``pok:{project_id}``, one block per PROJECT);
* ``build_pok_context_message`` — renders an ALREADY-ADMITTED view
  (the admission pipeline stays in ``pok_injection.py``);
* ``_maybe_pok_context_message`` — the POK seam: gates → fresh
  once-flag read → per-view admission → builder (never-raise);
* ``_read_instance_metadata`` — the off-loop metadata read the seam
  uses (its only caller).

Every DB touch is OFF-LOOP (``asyncio.to_thread``); the seam never
raises (a store failure degrades to NO block, never to a broken
turn).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.messages import HumanMessage

from .context_block import _make_context_message, _stable_id_for
from .pok_injection import POK_BLOCK_TITLE, POK_HEADER_LINE, POK_INJECTION_FLAG

logger = logging.getLogger(__name__)

# POK Phase 1 (project-operating-knowledge, 2026-11-07): the
# audience-scoped operating-knowledge block kind. Stable message id
# ``pok:{project_id}`` — one block per PROJECT (the audience
# dimension is per-view admission state, not per-block identity), so
# ``add_messages`` supersedes in place on a re-delivery. The block is
# BUILT here; the once-per-instance flag (``pok_injected``) is written
# by the CONSUMER (``instance_messaging.py``) after the persistent
# half is confirmed to carry it (the AM-22 discipline — a builder-
# written flag would consume the flag on a graph-discarded half).
CONTEXT_KIND_POK = "pok"


# ── POK context block (project-operating-knowledge Phase 1, T6) ───────────
#
# The AUDIENCE-DIMENSIONED operating-knowledge block (ER-1 /
# §4.5.1): ``build_pok_context_message`` renders an ALREADY-ADMITTED
# view (the admission pipeline lives in
# ``daemon/services/pok_injection.py`` — audience filter FIRST, then
# pinned ∪ family-ordered, F5 rider, per-view 8/2,900 wrapper-
# inclusive). The seam (``_maybe_pok_context_message``) owns the
# gates: default project → no block (AC10 negative leg); once-flag
# READ fresh each assembly (revive-safe) — the flag is WRITTEN by the
# consumer (``instance_messaging.py``), never here. Degradation:
# never-raise — a store failure yields ``None`` + WARNING (no block,
# no flag write; the next turn retries).


def build_pok_context_message(
    entries: list[Any],
    project_id: str,
    *,
    omitted: int = 0,
) -> HumanMessage | None:
    """Render the ``[SYSTEM CONTEXT: Project Operating Knowledge]`` block.

    Args:
        entries: The ADMITTED view — a list of
            :class:`~daemon.services.pok_injection.ViewEntry` (already
            audience-filtered, pinned-ordered, F5-bounded). Each
            entry's ``render()`` applies the fixed list-item template +
            newline flattening + ``escape_for_context_block`` (the
            A-29 render-side fence).
        project_id: The project the block belongs to — the stable id
            suffix (``pok:{project_id}``), so ``add_messages``
            supersedes in place on a re-delivery.
        omitted: The F5 tail remainder (renders the count-up line when
            > 0).

    Returns ``None`` when ``entries`` is empty (no empty-host noise —
    the shared_meta_kv precedent). BUILDS ONLY: never writes the
    once-per-instance flag (AM-22 discipline).
    """
    if not entries:
        return None
    lines: list[str] = [POK_HEADER_LINE]
    for entry in entries:
        rendered = entry.render()
        if rendered:
            lines.append(rendered)
    if omitted > 0:
        n = omitted
        lines.append(
            f"({n} more entr{'y' if n == 1 else 'ies'} not shown — use "
            "pok_query for the full view)"
        )
    return _make_context_message(
        kind=CONTEXT_KIND_POK,
        title=POK_BLOCK_TITLE,
        content="\n".join(lines),
        id_=_stable_id_for("pok", project_id=project_id),
    )


async def _maybe_pok_context_message(
    instance_id: str,
    project_id: str | None,
    agent_meta: Any,
    manager: Any,
    instance_repository: Any,
    user_query: str = "",
) -> HumanMessage | None:
    """The POK seam: gates → fresh read → per-view admission → builder.

    Runs on EVERY assembly (turn-1 AND the refresh branch): the
    once-flag is read fresh, so a turn-1 flag-write failure (or a
    store-down turn) delivers on the next turn — the memoir-seam
    lifecycle (D5.11). The caller (``assemble_context_messages``)
    passes the project only when it is NON-default and resolved; the
    default-project gate is enforced here too (AC10 negative leg,
    belt-and-braces).
    """
    if not project_id:
        return None
    from daemon import constants as _constants

    if (
        project_id == _constants.SYSTEM_DEFAULT_PROJECT_ID
        or getattr(agent_meta, "id", None) is None
    ):
        # The id gate is not a POK gate per se — agent_meta.id is
        # required for the audience mapping (factory-captured
        # agent_id); without it the view cannot be resolved.
        return None

    repo = (
        instance_repository
        if instance_repository is not None
        else getattr(manager, "_instance_repository", None)
    )
    engine = getattr(manager, "_engine", None)
    if repo is None or engine is None:
        return None

    # Once-flag read (off-loop, fresh — revive-safe). UNWRITTEN →
    # deliver; written → the block is already checkpointed.
    try:
        flag = await asyncio.to_thread(
            _read_instance_metadata, repo, instance_id, POK_INJECTION_FLAG
        )
    except Exception as exc:  # noqa: BLE001 — degraded read delivers nothing
        logger.warning(
            "[ContextMessages] POK once-flag read failed for "
            f"{instance_id[:8]}...: {exc} — no block this turn"
        )
        return None
    if flag:
        return None

    try:
        from .pok_injection import (  # noqa: PLC0415 — cycle-neutral
            admit_view,
            make_retrieval_recorder,
        )
        from .pok_service import POKService  # noqa: PLC0415

        def _build_sync() -> HumanMessage | None:
            service = POKService(engine)
            rows = service._repo.list_entries(project_id, statuses=("active",))
            if not rows:
                return None
            result = admit_view(
                rows,
                agent_id=getattr(agent_meta, "id", None),
                query=user_query or "",
                embeddings_map=service._repo.embeddings_map(project_id),
                rank_fn=service.rank_for_view,
                # The RETRIEVAL RECORDER (pok-write-gate Phase 10,
                # T10.13 — the W-1 close): the rows admitted to THIS
                # view are recorded as ledger RETRIEVAL rows, so the
                # librarian's J-metric chain (which SUPERSEDES
                # ``access_count``) reads real data. The recorder is
                # never-raise (the §4.9 double containment: the
                # hook site guards it AND the recorder guards each
                # row), so the injection path stays infallible.
                on_retrieval=make_retrieval_recorder(
                    service._repo,
                    project_id,
                    agent_id=getattr(agent_meta, "id", None),
                    source="injection",
                ),
            )
            return build_pok_context_message(
                result.entries, project_id, omitted=result.omitted
            )

        return await asyncio.to_thread(_build_sync)
    except Exception as exc:  # noqa: BLE001 — §4.9 never-raise
        logger.warning(
            "[ContextMessages] POK block build failed for "
            f"{instance_id[:8]}...: {exc} — no block, no flag write"
        )
        return None


def _read_instance_metadata(repo: Any, instance_id: str, key: str) -> Any:
    """Off-loop instance-metadata read (the memoir flag-read shape)."""
    row = repo.get(instance_id)
    if row is None:
        return None
    meta = getattr(row, "instance_metadata", None) or {}
    return meta.get(key)
