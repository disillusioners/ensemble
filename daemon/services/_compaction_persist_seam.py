"""Shared compaction persist seam.

Phase 1 (proactive-compaction-fix): the executor and the proactive
trigger now share ONE persist seam so the verbatim-duplicated B1+B2
``compacted_ids`` derivation blocks in the messaging and executor
callers — superseded by this seam's Variant-A persist (P1 unification) —
collapse to a single site, and both callers see identical
``aupdate_state`` semantics. The seam carries the verified asymmetries
as parameters:

* ``mid_turn`` — ``False`` (quiescent, out-of-frame; executor + proactive
  post-§3.3 retirement) emits Variant A (TWO ``aupdate_state`` calls
  WITHOUT ``as_node``); ``True`` (mid-superstep, in-frame; the CLE
  handler's in-frame persist block and — since
  P1b — the 95% pre-call hook ``_maybe_precall_compact_95``) emits
  Variant B (``as_node='agent'``).

* ``abort_policy`` — ``"raise"`` re-raises :class:`CompactionAborted`
  for the executor (which surfaces the rejection to the command-state
  path); ``"fail_open"`` swallows the abort and logs a WARNING for
  the proactive site; the former caller-local fail-open abort block is
  superseded by this seam's Variant-A persist (P1 unification).

* Anti-refire stamp-only path: when ``result.replacement_messages`` is
  empty (``engine stamped ``compacted_at`` but had no messages to
  write — anti-refire no-op paths), the seam writes ONLY the
  ``compacted_at`` stamp and does NOT touch messages. This is what
  engages the 60s dedup via ``_is_recently_compacted`` in
  ``ContextCompactor.compact_state`` on skip paths that would otherwise
  re-fire every dispatch.

The compaction message tap (``MessageTapSlot`` /
``SOURCE_COMPACTION_MESSAGING``) exists ONLY on the proactive path — it is
NOT invoked from this seam.
The proactive caller fires the tap after the seam returns so the seam
stays generic across executor + proactive.

The seam lives in ``daemon/services/`` rather than next to the engine
in ``daemon/compaction.py`` because the engine must stay free of
checkpoint-state semantics (per the architecture boundary enforced by
:mod:`daemon.services._checkpoint_utils` — the engine never imports
that helper). Persist is a checkpoint concern, not an engine concern.

Architecture reference:
``daemon/compaction.py::ContextCompactor.compact_state``:
60s dedup via ``_is_recently_compacted``; all-injected skip in the
``if not regular_messages:`` guard; min-messages skip; selection budget
via ``select_compactable_groups``; timestamp stamp.
``daemon/graph.py`` CLE handler's in-frame persist block.
Anchors are semantic — line numbers drift; do not re-pin.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from ..compaction import (
    CompactionAborted,
    CompactionResult,
    build_sentinel_replacement,
)
from langchain_core.messages import BaseMessage, RemoveMessage

__all__ = ["persist_compaction_result"]

logger = logging.getLogger(__name__)


AbortPolicy = Literal["raise", "fail_open"]


async def persist_compaction_result(
    manager: Any,
    *,
    instance_id: str,
    result: CompactionResult,
    mid_turn: bool = False,
    abort_policy: AbortPolicy = "raise",
    graph: Any = None,
) -> bool:
    """Persist a :class:`CompactionResult` to the LangGraph checkpoint.

    Returns:
        ``True`` when a checkpoint write happened (full persist OR the
        stamp-only anti-refire write); ``False`` when the pre-write
        guard refused the write under ``abort_policy="fail_open"`` and
        NOTHING was written. Callers that act on the persisted messages
        (P1b hook: tap + rebuild) MUST check this — a ``False`` means
        the engine's result never reached the checkpoint. The executor
        path never sees ``False`` (it runs ``abort_policy="raise"``).

    Args:
        manager: The :class:`daemon.manager.InstanceManager` facade (the
            seam reaches ``manager.get_instance(instance_id)`` for the
            graph + the per-instance thread config). Both consumers pass
            the manager; the seam never reaches for the LLM seam or any
            other daemon state.
        instance_id: Target instance.
        result: Engine output. ``result.replacement_messages`` may be
            empty for the anti-refire stamp-only path (see module
            docstring); in that case ONLY ``compacted_at`` is written.
        mid_turn: ``False`` → Variant A (two ``aupdate_state`` calls
            WITHOUT ``as_node`` — quiescent/out-of-frame); ``True`` →
            Variant B (TWO ``aupdate_state`` calls WITH
            ``as_node='agent'`` — mid-superstep/in-frame; consumed by
            the CLE handler persist recipe and, since P1b, the 95%
            pre-call hook).
        abort_policy: ``"raise"`` re-raises :class:`CompactionAborted`
            (executor path); ``"fail_open"`` swallows and logs at
            WARNING (proactive path).
        graph: Optional PRE-RESOLVED compiled graph. P1b: the 95%
            pre-call hook (``daemon/graph.py``) already holds the graph
            via its late-bound ``graph_ref`` closure and has NO manager
            reference — it passes ``manager=None`` plus the graph here,
            avoiding a redundant ``manager.get_instance`` round-trip.
            When ``None`` (the default, used by the executor + proactive
            callers) the seam resolves the graph via
            ``manager.get_instance`` exactly as before. When BOTH
            ``manager`` and ``graph`` are ``None`` a ``ValueError``
            raises (caller contract violation).

    Raises:
        CompactionAborted: When the pre-write guard refuses the write
            AND ``abort_policy == "raise"``. The checkpoint is left
            untouched on abort (per ``build_sentinel_replacement``).

    Side effects:
        Calls ``graph.aupdate_state`` either once (stamp-only path) or
        twice (Variant A / Variant B). Order-pinned — the messages
        call ALWAYS precedes the compacted_at call.
    """
    if graph is None:
        if manager is None:
            raise ValueError(
                "persist_compaction_result requires either `manager` "
                "(to resolve the graph) or a pre-resolved `graph`"
            )
        graph = await manager.get_instance(instance_id)
    config = {"configurable": {"thread_id": instance_id}}

    # Anti-refire stamp-only path: the engine has nothing to write into
    # the messages channel but stamped ``compacted_at`` so the 60s
    # dedup (``daemon/compaction.py:1771-1774``) engages. Without the
    # stamp, every dispatch would re-evaluate the gate and re-enter
    # this skip — the per-dispatch refire loop the architecture
    # recommendation §3.5 specifically calls out.
    if not result.replacement_messages:
        if result.compacted_at:
            stamp_update: dict[str, Any] = {"compacted_at": result.compacted_at}
            if mid_turn:
                # P1b fix (latent P1 bug, first mid_turn=True stamp-only
                # consumer): ``as_node`` MUST be the langgraph KEYWORD
                # argument — the P1 code embedded it in the STATE dict
                # (``aupdate_state(config, {..., "as_node": "agent"})``),
                # which on a real graph writes a bogus ``as_node``
                # channel key instead of targeting the agent node.
                await graph.aupdate_state(
                    config, stamp_update, as_node="agent"
                )
            else:
                await graph.aupdate_state(config, stamp_update)
            logger.info(
                "[Compaction seam] stamp-only anti-refire for "
                "instance=%s compacted_at=%s (mid_turn=%s)",
                instance_id[:8],
                result.compacted_at,
                mid_turn,
            )
            return True
        # No replacement AND no stamp → nothing to write.
        return False

    # Standard Variant A / Variant B path.
    pre_state = await graph.aget_state(config)
    pre_messages: list[BaseMessage] = list(
        (pre_state.values or {}).get("messages", []) or []
    )

    # B1 + B2 (2026-09-01) — engine's ``compacted_ids`` is
    # authoritative; site derives from ``pre_ids − new_replacement_ids``
    # (non-RemoveMessage keep set; RemoveMessage targets are NOT
    # "kept"). See ``compact_executor.py:1597`` for the full rationale.
    pre_ids = {
        getattr(m, "id", None)
        for m in pre_messages
    }
    pre_ids.discard(None)
    new_replacement_ids = {
        getattr(m, "id", None)
        for m in result.replacement_messages
        if not isinstance(m, RemoveMessage)
    }
    new_replacement_ids.discard(None)
    site_compacted_ids: set[str] = pre_ids - new_replacement_ids
    engine_compacted_ids = getattr(result, "compacted_ids", None)
    if engine_compacted_ids is not None:
        compacted_ids: set[str] = set(engine_compacted_ids)
    else:
        compacted_ids = site_compacted_ids
    try:
        # Engine-vs-site invariant: engine's ``compacted_ids`` MUST be
        # a subset of the site-derived set, otherwise the engine and
        # site disagree on the removed span. This was previously an
        # ``assert`` — but ``python -O`` strips asserts, so an
        # invariant break would silently proceed (the pre-write guard
        # inside ``build_sentinel_replacement`` checks
        # ``pre_ids − kept``, NOT engine-vs-site agreement). Raising
        # :class:`CompactionAborted` here integrates with the
        # ``abort_policy`` machinery below (raise for executor;
        # fail_open → WARNING + return False for proactive + precall).
        if (
            engine_compacted_ids is not None
            and not set(engine_compacted_ids) <= site_compacted_ids
        ):
            raise CompactionAborted(
                f"engine populated compacted_ids that are NOT a subset "
                f"of the site-derived set — engine and site disagree on "
                f"the removed span; refusing the write "
                f"(engine={sorted(engine_compacted_ids)!r}, "
                f"site={sorted(site_compacted_ids)!r})"
            )
        replacement: list[BaseMessage] = build_sentinel_replacement(
            result, pre_messages, compacted_ids=compacted_ids
        )
    except CompactionAborted as abort_exc:
        # W1 mitigation: pre-write guard refused the write. The
        # checkpoint is untouched. abort_policy determines whether the
        # caller surfaces a hard raise (executor → command-state path)
        # or fails open at WARNING (proactive → caller no-ops).
        if abort_policy == "raise":
            logger.warning(
                "compaction pre-write guard refused the write for "
                "instance=%s: %s — failing loud, caller raises",
                instance_id, abort_exc,
            )
            raise
        logger.warning(
            "compaction pre-write guard refused the write for "
            "instance=%s: %s — failing open, no checkpoint write",
            instance_id, abort_exc,
        )
        return False

    # Variant A (mid_turn=False) — quiescent / out-of-frame; NO
    # ``as_node`` so the next pointer is not touched. Pinned by
    # ``test_compact_executor_revive_brick_e2e.py``. Variant B
    # (mid_turn=True) — mid-superstep / in-frame; ``as_node='agent'``
    # so the in-flight task continues to the next node. The
    # mid-superstep canary lives at
    # ``tests/unit/services/test_compact_executor_revive_brick_e2e.py``
    # (T2-ext).
    if mid_turn:
        await graph.aupdate_state(
            config,
            {"messages": replacement},
            as_node="agent",
        )
        if result.compacted_at:
            await graph.aupdate_state(
                config,
                {"compacted_at": result.compacted_at},
                as_node="agent",
            )
    else:
        await graph.aupdate_state(
            config,
            {"messages": replacement},
        )
        if result.compacted_at:
            await graph.aupdate_state(
                config,
                {"compacted_at": result.compacted_at},
            )
    return True
