"""``/compact`` executor service (Phase 1 / WS-2, WS-4, WS-5, WS-6).

Phase 1 / WS-2 (architect §2, §4, §6): the executor is the
``/compact`` handler registered into the
:class:`CommandDispatcher`. It drives the existing
:class:`daemon.compaction.ContextCompactor` engine — force-bypasses
the THRESHOLD ONLY (dedup + min-messages still apply inside the
engine — WS-2.1 narrowed) — and writes the result back to the
LangGraph checkpoint via the proactive-path recipe
(``instance_messaging.py`` ~:1190-1202, D3 sentinel single-write +
D12 ``compacted_at`` stamp).

Status gating per WS-6 (architect §6):

* ``IDLE``: quiescence probe (``wait_for_instance_quiescent(timeout=0)``)
  + ``has_instance_busy`` ``False`` → take ExecutionGate → re-read
  ``has_instance_busy`` UNDER the gate, retry-once on staleness →
  compact.
* ``WAITING_CHILDREN``: probe ONLY; treat as IDLE on quiescence.
  NEVER ``pause_instance_cascade`` / ``graph_task.cancel()`` (O16 —
  child workers are legitimate work, N1 sub-tokens invariant).
* ``RUNNING``: ``waiting`` SSE FIRST (F3) → ``pause_instance_cascade``
  (cascade_to_root default True — do not flip) → quiescence wait
  (timeout=30s) → gate → compact → ``resume_instance_cascade`` in
  ``finally``. O9 BINDING: the ENTIRE pause→quiesce sequence in ONE
  ``try/except``; any failure (timeout OR raised exception) →
  ``rejected + reason=quiescence_timeout`` with the exception
  CLASS NAME in ``detail``; best-effort ``resume_instance_cascade``
  in ``finally`` BEFORE emitting the rejection; the async task
  MUST NEVER crash.
* ``PAUSED`` (with or without frozen task): treat as quiescent →
  gate → compact; instance STAYS PAUSED (no state change you
  didn't make).
* Terminal (``COMPLETED`` / ``ERROR`` / ``FAILED`` / ``TERMINATED``):
  REJECT ``reason=terminal_instance``, ``guidance="Send a message
  to start a new turn, then /compact."`` (W-1.2 pinned copy — plan
  S-14 / architect §5). ``aupdate_state`` NEVER invoked. C1
  BINDING — the gate is INSTANCE STATUS (the O-B4 canonical
  terminal set), NOT ``state.next == ()``. The shared
  :func:`daemon.services._checkpoint_utils._is_terminal_checkpoint`
  helper still anchors the proactive path (instance_messaging.py)
  byte-equivalently; the executor's gate is the manager status field,
  which is the authoritative terminal signal the O-B4 spec pins.
  A post-turn IDLE instance has ``state.next == ()`` but a
  non-terminal status — that instance compacts fine (the headline
  scenario the C1 amendment re-opens).

Executor pre-checks BEFORE any engine call (architect §2):

* ``compacted_at`` recency <60s → ``success + noop +
  reason=recently_compacted`` (engine NEVER invoked).
* Estimated tokens < ``SLASH_COMMANDS_NOOP_FLOOR_RATIO`` × resolved
  per-instance window → ``success + noop + reason=below_floor``.
  Engine ``None``/``"can't compact"`` ≠ below-floor
  (``would but shouldn't``).

SSE phase machine (WS-5 §7):

* ``waiting`` (F3 — emitted BEFORE any pause mutation)
* ``in_progress`` (gate acquired; engine running; heartbeat re-emits
  every 10s with the registry's authoritative ``phase_seq``,
  fresh timestamp/elapsed_ms)
* terminal one-of: ``success`` | ``timed_out`` → ``fallback_applied``
  | ``failed``. Rejections are answerable at ack time; the
  accepted-then-quiescence-timeout path surfaces as terminal SSE
  + registry state.

Emission discipline: ``LiveEventHub.stream_message`` with
``event_type="command_progress"`` — flat payload, additive
``command_id`` / ``phase_seq``, try/except WARNING-swallow
(best-effort, never fails the API or compaction — D-B10).

Per-instance model resolution (WS-2.6, approver note 3): the
session model is resolved via the SAME seam the engine's
summarization LLM client uses (``daemon.compaction._call_summarization_llm``
~:1289-1318 — the ``llm_config_with_headers`` seam constructed in
:class:`daemon.compaction.ContextCompactor.__init__`). The
executor goes through ``manager._compactor.llm_config_with_headers``
(the SAME accessor) so window math AND noop-floor measurement
target the same model the engine actually calls. ``context_window_overrides``
(config.py:715-749) layer on top via
:func:`daemon.compaction.get_model_context_limit`. Global
``config.llm.model`` is used ONLY as a WARNING-logged fallback
(never silent — the warning carries ``instance_id`` + resolved
window so auditability is preserved).

O-B7 durability seam (architect verdict): this executor is
ephemeral (registered as a ``CommandSpec`` ``handler``). A future
durable variant wraps ``handler`` in a ``JobItem('command')``
enqueue without touching ``CommandSpec``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import BaseMessage, RemoveMessage

from ..compaction import (
    CompactionContext,
    CompactionResult,
    ContextCompactor,
    estimate_messages_tokens,
    get_model_context_limit,
)
from ._checkpoint_utils import _is_terminal_checkpoint

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# WS-5 §7 wire enum surface (mirrors the dispatcher enums)
# ─────────────────────────────────────────────────────────────────────────


# These strings mirror `daemon.services.command_dispatcher.CompactedType`
# and `NoopReason`. We keep a string-based mapping here (instead of
# importing the enums) to keep this file free of a hard dependency on
# the dispatcher — the dispatcher already imports the executor's
# CommandSpec registration helper.
_COMPACTED_TYPE_SUMMARY = "summary"
_COMPACTED_TYPE_PARTIAL_SUMMARY = "partial_summary"
_COMPACTED_TYPE_TRUNCATION = "truncation"
_COMPACTED_TYPE_EMERGENCY_TRUNCATION = "emergency_truncation"
_COMPACTED_TYPE_NOOP = "noop"

_NOOP_REASON_BELOW_FLOOR = "below_floor"
_NOOP_REASON_RECENTLY_COMPACTED = "recently_compacted"

_FAILURE_KIND_TIMEOUT = "timeout"
_FAILURE_KIND_ERROR = "error"

_PHASE_WAITING = "waiting"
_PHASE_IN_PROGRESS = "in_progress"
_PHASE_SUCCESS = "success"
_PHASE_TIMED_OUT = "timed_out"
_PHASE_FALLBACK_APPLIED = "fallback_applied"
_PHASE_FAILED = "failed"


# Heartbeat — re-emit in_progress every 10s (WS-5 §7 heartbeat rule).
_HEARTBEAT_INTERVAL_S = 10.0

# Quiescence wait after pause_instance_cascade (WS-6 RUNNING row).
_QUIESCENCE_TIMEOUT_S = 30.0


# ─────────────────────────────────────────────────────────────────────────
# WS-2.4 — terminal guard (C1 amendment — instance status, NOT helper)
# ─────────────────────────────────────────────────────────────────────────


# C1 BINDING: the executor's terminal-rejection gate is INSTANCE STATUS
# (the O-B4 canonical set: completed / terminated / error / failed),
# NOT ``_is_terminal_checkpoint`` (the shared helper that keys on
# ``state.next``). The shared helper still anchors the proactive
# compaction path at ``instance_messaging._maybe_compact_context``
# (byte-equivalent per the C1 binding) — the anti-drift test
# ``TestTerminalGuardUsedByTwoSites.test_two_import_sites`` still
# expects exactly TWO import sites for ``_checkpoint_utils`` across
# ``daemon/``. The helper is also re-exported below for unit tests
# that exercise it directly (``TestTerminalCheckpointHelper``).


# ─────────────────────────────────────────────────────────────────────────
# WS-2.2 — pre-checks
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class NoopOutcome:
    """Outcome of an executor pre-check that bypasses the engine.

    Attributes:
        compacted_type: Always ``"noop"`` (WS-5 §7 enum).
        noop_reason: ``"recently_compacted"`` (60s dedup) or
            ``"below_floor"`` (under floor ratio).
    """

    compacted_type: str
    noop_reason: str


def _is_recently_compacted(last_compacted_at: str | None) -> bool:
    """True iff a ``last_compacted_at`` ISO stamp is within 60s of now.

    Mirrors the in-engine ``_is_recently_compacted`` logic
    (``daemon/compaction.py`` ~:1500-1517) so the executor pre-check
    matches the engine's. We re-implement here rather than calling the
    engine helper directly — the engine helper is private (``_`` prefix)
    and the executor pre-check needs to make its own decision BEFORE
    calling the engine (architect §2).
    """
    if not last_compacted_at:
        return False
    try:
        last_time = datetime.fromisoformat(last_compacted_at)
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - last_time).total_seconds() < 60
    except (ValueError, TypeError):
        return False


# ─────────────────────────────────────────────────────────────────────────
# WS-2.6 — per-instance model resolution
# ─────────────────────────────────────────────────────────────────────────


def _resolve_per_instance_model(
    compactor: ContextCompactor,
    manager: Any,
    instance_id: str,
) -> str:
    """Resolve the active model name for ``instance_id``.

    The session model is resolved through the SAME seam the engine's
    summarization LLM client uses:
    ``compactor.llm_config_with_headers["model"]`` — the engine reads
    this in :meth:`daemon.compaction.ContextCompactor._call_summarization_llm`
    (the ``else: llm_config = self.llm_config_with_headers`` branch).
    The executor reuses that EXACT field so window math targets the
    same model the engine actually calls.

    Fallback chain (per O11 spec pin + plan §2.6):

    1. ``compactor.llm_config_with_headers["model"]`` — the active
       summarization session model. Empty / ``None`` → step 2.
    2. ``manager._lifecycle_service.get_instance_info(instance_id)`` →
       ``metadata["model_override"]`` (DB-persisted at spawn time for
       load-balanced + override sources; O11 — see
       ``instance_lifecycle.py:1695-1697``). Strip / fall through → 3.
    3. ``manager.config.llm.model`` — global default. WARNING-logged
       fallback (the warning carries ``instance_id`` + the resolved
       window so auditability is preserved; silent fallback would
       be a regression — O11 spec pin).

    Args:
        compactor: The :class:`ContextCompactor` the executor is
            driving. Used for the seam match (step 1).
        manager: The :class:`InstanceManager` facade. Used for the
            ``instance_info`` lookup (step 2) and the global
            fallback (step 3).
        instance_id: The target instance.

    Returns:
        The resolved model name. Never returns empty — the global
        fallback (step 3) always supplies a string.
    """
    # Step 1: the engine's active summarization model — same field
    # the engine reads at compaction time. Empty/None falls through.
    session_model = ""
    llm_cfg = getattr(compactor, "llm_config_with_headers", None)
    if llm_cfg:
        session_model = (llm_cfg.get("model") or "").strip()
    if session_model:
        return session_model

    # Step 2: per-instance persisted ``model_override`` (DB). The
    # spawn-time resolution (override / llm_models / llm_model)
    # persisted here covers council/Governor overrides + the
    # weighted random selection. Empty / whitespace falls through.
    try:
        info = manager._lifecycle_service.get_instance_info(instance_id)
    except Exception:
        info = None
    if info:
        meta = (info.get("metadata") or {}) if isinstance(info, dict) else {}
        override = (meta.get("model_override") or "").strip()
        if override:
            return override

    # Step 3: global default. WARNING-logged fallback — never silent
    # (O11 spec pin). The warning carries instance_id + the resolved
    # window so auditability is preserved.
    global_model = (manager.config.llm.model or "").strip() or "unknown"
    # The window is computed downstream; we log a structured warning
    # here so the operator can grep for the fallback path. The
    # actual window is computed by the caller (`get_model_context_limit`)
    # and the second WARN below carries it once known.
    logger.warning(
        "/compact per-instance model fell back to GLOBAL config.llm.model; "
        "instance_id=%s model=%s reason=session_model_unavailable",
        instance_id[:8],
        global_model,
    )
    return global_model


def _resolved_context_window(model_name: str, compaction_config: Any) -> int:
    """Resolve the context window for ``model_name`` via the engine's helper.

    Uses :func:`daemon.compaction.get_model_context_limit` (the same
    function the engine calls at compaction time, e.g.
    ``compaction.py:788``) so the executor's noop-floor measurement
    targets the EXACT window the engine uses for the threshold check.
    ``context_window_overrides`` layer on top via the same helper.
    """
    return get_model_context_limit(model_name, compaction_config)


# ─────────────────────────────────────────────────────────────────────────
# WS-5 — SSE phase machine + emission helper
# ─────────────────────────────────────────────────────────────────────────


async def _emit_phase_event(
    manager: Any,
    *,
    instance_id: str,
    command_id: str,
    phase: str,
    started_at_monotonic: float,
    detail: dict | None = None,
    eta_ms: int | None = None,
    context: Any = None,
) -> None:
    """Emit a ``command_progress`` SSE event.

    Shape follows WS-5 §7: flat payload with ``command_id``,
    ``phase_seq``, ``timestamp`` (ISO), ``elapsed_ms`` (server clock
    from ``started_at_monotonic``), and the optional ``detail``
    object. ``eta_ms`` is advisory and only set on ``in_progress``.

    Failure mode: best-effort. SSE errors are logged at WARNING and
    swallowed so a transient SSE hiccup never fails the API or
    compaction (D-B10). The event is silently dropped if no
    connections are registered (``LiveEventHub._stream_to_connections``
    is no-op when ``_connections.get(instance_id)`` is empty).

    Args:
        manager: The InstanceManager facade (for ``live_hub`` access).
        instance_id: Target instance.
        command_id: The command's UUIDv4 (correlates all events).
        phase: One of ``"waiting" | "in_progress" | "success" |
            "timed_out" | "fallback_applied" | "failed"``.
        started_at_monotonic: ``time.monotonic()`` at command start —
            used to compute ``elapsed_ms``.
        detail: Optional detail dict (WS-5 §7 detail shape).
        eta_ms: Optional advisory ETA in ms (``in_progress`` only).
        context: Optional :class:`CommandContext` — when supplied,
            ``phase_seq`` is read from the dispatcher registry (the
            W-3.1 / W-3.2 single source of truth). When ``None``
            (legacy callers), the SSE falls back to ``phase_seq=0``
            and the caller MUST set it explicitly. W-3.2 — the
            registry is authoritative so GET endpoint and SSE
            stream agree on the sequence.

    W-3.1 — this helper is the SINGLE counter path for SSE emits.
    Without it, an executor-local counter can collide with a later
    terminal emit on a >10s compaction (FE dedup drops the terminal).
    """
    elapsed_ms = max(0, int((time.monotonic() - started_at_monotonic) * 1000))
    # W-3.1 / W-3.2 — read the registry's authoritative phase_seq
    # (single source of truth). The local counter previously held by
    # the executor is gone (see execute_compact); every emit goes
    # through this helper.
    phase_seq = 0
    if context is not None:
        phase_seq = context.current_phase_seq()
    payload: dict[str, Any] = {
        "instance_id": instance_id,
        "event_type": "command_progress",
        "command_id": command_id,
        "phase": phase,
        "phase_seq": phase_seq,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": elapsed_ms,
    }
    if detail is not None:
        payload["detail"] = detail
    if eta_ms is not None:
        payload["eta_ms"] = eta_ms

    live_hub = getattr(manager, "_live_hub", None)
    if live_hub is None:
        return
    try:
        await live_hub.stream_message(
            instance_id=instance_id,
            message=payload,
            event_type="command_progress",
        )
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(
            "[/compact] SSE emit failed for phase=%s on %s...: %s",
            phase,
            instance_id[:8],
            type(e).__name__,
        )


# ─────────────────────────────────────────────────────────────────────────
# WS-2 — executor entry point
# ─────────────────────────────────────────────────────────────────────────


async def execute_compact(
    manager: Any,
    *,
    instance_id: str,
    command_id: str,
    context: Any,
) -> None:
    """``/compact`` handler — drive the compaction engine on demand.

    Wired into the dispatcher as a :class:`CommandSpec.handler`.
    The dispatcher's bg-task harness guarantees ``context`` is a
    :class:`daemon.services.command_dispatcher.CommandContext`
    exposing ``update_phase`` and ``terminalize``.

    Per WS-6 (architect §6) + O9 (single try/except wrapping the
    pause→quiesce sequence): any exception is swallowed into a
    terminal ``failed`` phase (via the dispatcher's safety net at
    ``CommandDispatcher._run_handler``) so the bg task NEVER
    crashes. The executor itself is exception-tolerant —
    ``compact_state`` failures surface as terminal ``failed`` +
    registry cleanup.

    Lifecycle:

    1. Resolve instance status (``get_instance_info``).
    2. Pre-check 1 — terminal via shared helper → REJECT
       ``reason=terminal_instance`` with guidance detail.
    3. Pre-check 2 — recency (``compacted_at`` <60s) → SUCCESS + noop.
    4. Pre-check 3 — below-floor → SUCCESS + noop.
    5. Quiescence / pause / resume orchestration per WS-6 matrix.
    6. Acquire ExecutionGate (the per-instance ``asyncio.Lock``).
    7. Re-read ``has_instance_busy`` UNDER the gate, retry-once.
    8. Emit ``in_progress`` (heartbeat starts after this).
    9. Build :class:`CompactionContext` (per-instance model +
       overrides) → ``compact_state(force=True)``.
    10. Persist (C1 recipe — two ``aupdate_state`` calls in order,
        NO ``as_node``).
    11. Map engine result → executor outcome via the engine→wire
        mapping function (approver note 1).
    12. ``emit_context_usage_for_instance`` (FE token-drop refresh).
    13. Terminalize via ``context.terminalize`` (and update_phase for
        the timed_out → fallback_applied two-step).

    Args:
        manager: The :class:`InstanceManager` facade.
        instance_id: Target instance.
        command_id: UUIDv4 minted at dispatcher ``record_start``.
        context: :class:`CommandContext` — the dispatcher-side
            handle the handler uses to update / terminalize.

    W-3.1 / W-3.2 — ``phase_seq`` is read from
    ``context.current_phase_seq()`` (dispatcher registry, single
    source of truth). Every SSE emit goes through ``_emit_phase_event``
    which reads the registry at emit time; no executor-local counter
    exists. Heartbeat (>10s compactions) and terminal events emit
    strictly increasing phase_seq.
    """
    # The dispatcher already calls ``record_start`` so the active slot
    # is populated. We do NOT need to re-seed — the handler runs after
    # ``record_start`` succeeded.
    started_at_monotonic = time.monotonic()
    # W-3.1 / W-3.2 — NO executor-local phase_seq counter. The
    # dispatcher's registry is the single source of truth; every SSE
    # emit reads via ``context.current_phase_seq()``. Eliminating the
    # local counter closes the heartbeat-vs-terminal collision
    # window (>10s compaction re-emitting the same seq).

    # ── 1. Resolve instance status ─────────────────────────────────────
    try:
        instance_info = manager._lifecycle_service.get_instance_info(instance_id)
    except KeyError:
        # Instance vanished between dispatch and execution.
        await context.terminalize(
            _PHASE_FAILED,
            detail={
                "failure_kind": _FAILURE_KIND_ERROR,
                "reason": "instance_not_found",
            },
        )
        return
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(
            "[/compact] instance lookup failed for %s...: %s",
            instance_id[:8],
            type(e).__name__,
        )
        await context.terminalize(
            _PHASE_FAILED,
            detail={
                "failure_kind": _FAILURE_KIND_ERROR,
                "reason": type(e).__name__,
            },
        )
        return

    instance_status = (instance_info.get("status") or "").lower()

    # ── 2. Terminal-instance guard (C1 BINDING — instance status, NOT
    # checkpoint shape) ─────────────────────────────────────────────────
    #
    # The C1 critical fix: rejection is gated on INSTANCE STATUS ∈
    # {completed, terminated, error, failed} (O-B4 semantics +
    # revive-brick tests stay green), NOT on ``_is_terminal_checkpoint``
    # (``state.next == ()``).
    #
    # WHY: the previous helper fired on ANY quiescent checkpoint,
    # including a post-turn IDLE instance (real post-turn state has
    # ``state.next == ()`` — proven by
    # ``test_compact_executor_revive_brick_e2e.py`` TestTerminalObservableOnRealRun
    # and the existing ``_checkpoint_utils._is_terminal_checkpoint``
    # helper itself). That made the headline scenario unreachable —
    # every idle instance rejected before engine invocation.
    #
    # The proactive site (``instance_messaging.py`` ~:1146) keeps the
    # helper: that path is BYTE-EQUIVALENT (per the C1 binding) because
    # it must skip compaction on a finished graph, and the
    # checkpointer's behavior is what the helper sees, not the manager
    # facade. The executor's path is engine-/persistence-driven — it
    # cares about INSTANCE STATUS (the O-B4 set), which is the
    # authoritative terminal signal from the manager.
    #
    # The revive-brick collapse test stays in place — it pins WHY
    # TERMINAL-STATUS instances must never be compacted (the brick
    # property of ``aupdate_state`` on a finished graph). For the
    # executor, the gating signal is the status field, not the
    # checkpointer shape.
    terminal_statuses = {"completed", "terminated", "error", "failed"}
    if instance_status in terminal_statuses:
        logger.info(
            "[/compact] rejecting terminal-status instance %s... (status=%s)",
            instance_id[:8],
            instance_status,
        )
        await context.terminalize(
            _PHASE_FAILED,
            detail={
                "failure_kind": _FAILURE_KIND_ERROR,
                "reason": "terminal_instance",
                "guidance": (
                    "Send a message to start a new turn, then /compact."
                ),
            },
        )
        return

    # ── 3. Pre-check: recently compacted (recency <60s) ────────────────
    # Reads ``compacted_at`` off the live checkpoint. We do NOT call
    # the engine for this — the executor's recency pre-check is
    # independent (architect §2: "engine stays single-purpose").
    last_compacted_at = None
    checkpoint_state = None
    try:
        graph_obj = await manager.get_instance(instance_id)
        config = {"configurable": {"thread_id": instance_id}}
        checkpoint_state = await graph_obj.aget_state(config)
    except Exception:
        # Checkpoint read failed — fall through; recency / noop-floor
        # pre-checks below tolerate a None state.
        checkpoint_state = None

    if checkpoint_state is not None:
        last_compacted_at = (checkpoint_state.values or {}).get("compacted_at")

    if _is_recently_compacted(last_compacted_at):
        # Noop — emit success terminal with the noop detail.
        await context.update_phase(
            _PHASE_IN_PROGRESS,
            bump_seq=True,
            detail={"compacted_type": _COMPACTED_TYPE_NOOP, "noop_reason": _NOOP_REASON_RECENTLY_COMPACTED},
        )
        await context.terminalize(
            _PHASE_SUCCESS,
            detail={
                "compacted_type": _COMPACTED_TYPE_NOOP,
                "noop_reason": _NOOP_REASON_RECENTLY_COMPACTED,
            },
        )
        await _emit_phase_event(
            manager,
            instance_id=instance_id,
            command_id=command_id,
            phase=_PHASE_SUCCESS,
            started_at_monotonic=started_at_monotonic,
            context=context,
            detail={
                "compacted_type": _COMPACTED_TYPE_NOOP,
                "noop_reason": _NOOP_REASON_RECENTLY_COMPACTED,
            },
        )
        return

    # ── 4. Per-instance model + context window for noop floor ──────────
    compactor = getattr(manager, "_compactor", None)
    if compactor is None:
        # Engine not available → REJECT compaction_disabled. No
        # engine invocation possible.
        await context.terminalize(
            _PHASE_FAILED,
            detail={
                "failure_kind": _FAILURE_KIND_ERROR,
                "reason": "compaction_disabled",
            },
        )
        return

    resolved_model = _resolve_per_instance_model(compactor, manager, instance_id)
    resolved_window = _resolved_context_window(resolved_model, manager.config.compaction)

    # Pull messages for the noop-floor measurement. We use the same
    # checkpoint_state we already read above (one DB hit total).
    messages = list((checkpoint_state.values or {}).get("messages", []) or [])
    system_prompt_tokens = 0  # The proactive path uses the prompt cache
    # — the executor uses 0 here for symmetry because we are only
    # measuring against the floor (not running the engine). The
    # floor is intentionally conservative — system prompt tokens
    # count toward the budget too.
    estimated_tokens = estimate_messages_tokens(messages) + system_prompt_tokens

    # Floor ratio (config-driven; default 0.05).
    noop_floor_ratio = float(
        getattr(manager.config.slash_commands, "noop_floor_ratio", 0.05)
    )
    floor_tokens = int(resolved_window * noop_floor_ratio)
    if estimated_tokens < floor_tokens:
        # Noop — below floor.
        await context.update_phase(
            _PHASE_IN_PROGRESS,
            bump_seq=True,
            detail={
                "compacted_type": _COMPACTED_TYPE_NOOP,
                "noop_reason": _NOOP_REASON_BELOW_FLOOR,
                "resolved_window": resolved_window,
                "estimated_tokens": estimated_tokens,
            },
        )
        await context.terminalize(
            _PHASE_SUCCESS,
            detail={
                "compacted_type": _COMPACTED_TYPE_NOOP,
                "noop_reason": _NOOP_REASON_BELOW_FLOOR,
                "resolved_window": resolved_window,
                "estimated_tokens": estimated_tokens,
            },
        )
        await _emit_phase_event(
            manager,
            instance_id=instance_id,
            command_id=command_id,
            phase=_PHASE_SUCCESS,
            started_at_monotonic=started_at_monotonic,
            context=context,
            detail={
                "compacted_type": _COMPACTED_TYPE_NOOP,
                "noop_reason": _NOOP_REASON_BELOW_FLOOR,
                "resolved_window": resolved_window,
                "estimated_tokens": estimated_tokens,
            },
        )
        return

    # ── 5. Status gating (WS-6 matrix) ────────────────────────────────
    # Decide whether to:
    #   * run quiescence probe + take gate (IDLE)
    #   * run probe ONLY (WAITING_CHILDREN — children are legitimate)
    #   * pause → quiesce → gate → resume (RUNNING)
    #   * gate only (PAUSED — already quiescent)
    # The matrix lives in the plan; here we orchestrate.
    run_status = instance_status
    needs_pause_resume = run_status == "running"
    paused_state_resume_ok = False  # for the resume-in-finally

    if needs_pause_resume:
        # F3: emit waiting BEFORE any pause mutation.
        await context.update_phase(
            _PHASE_WAITING,
            bump_seq=False,  # waiting is the start state — do not bump seq
            detail={"checkpoint_id": None},
        )
        await _emit_phase_event(
            manager,
            instance_id=instance_id,
            command_id=command_id,
            phase=_PHASE_WAITING,
            started_at_monotonic=started_at_monotonic,
            context=context,
        )

        # W-3.3 — snapshot the pre-pause checkpoint_state for
        # re-reading AFTER quiescence. Building CompactionContext
        # from the pre-pause snapshot produces a chronologically
        # inverted result (the engine would see the messages that
        # were live when the turn started, NOT the messages that
        # quiesced). We re-read aget_state AFTER the pause/quiesce
        # succeeds and use THAT snapshot for the messages /
        # last_compacted_at reads.
        pre_pause_graph_obj: Any = None
        pre_pause_config: dict | None = None
        try:
            pre_pause_graph_obj = await manager.get_instance(instance_id)
            pre_pause_config = {"configurable": {"thread_id": instance_id}}
        except Exception:
            pre_pause_graph_obj = None
            pre_pause_config = None

        try:
            await manager.pause_instance_cascade(instance_id)
            quiesced = await manager.wait_for_instance_quiescent(
                instance_id, timeout=_QUIESCENCE_TIMEOUT_S
            )
            if not quiesced:
                # Quiescence timeout (O9): best-effort resume in finally
                # BEFORE emitting the rejection. The exception class
                # name is "WaitForQuiescenceTimeout" — single enum
                # value carries the failure_kind.
                raise WaitForQuiescenceTimeout(
                    f"quiescence wait timed out after {_QUIESCENCE_TIMEOUT_S}s"
                )
        except WaitForQuiescenceTimeout as e:
            # Best-effort resume in finally BEFORE emitting rejection.
            await _safe_resume(manager, instance_id)
            detail_text = f"{type(e).__name__}: {e}"
            await context.terminalize(
                _PHASE_FAILED,
                detail={
                    "failure_kind": _FAILURE_KIND_TIMEOUT,
                    "reason": "quiescence_timeout",
                    "checkpoint_id": None,
                    "exception": detail_text,
                },
            )
            await _emit_phase_event(
                manager,
                instance_id=instance_id,
                command_id=command_id,
                phase=_PHASE_FAILED,
                started_at_monotonic=started_at_monotonic,
                context=context,
                detail={
                    "failure_kind": _FAILURE_KIND_TIMEOUT,
                    "reason": "quiescence_timeout",
                    "exception": detail_text,
                },
            )
            return
        except Exception as e:
            # O9: any raised exception in pause→quiesce → same
            # rejection. Exception CLASS NAME in detail (single FE
            # rendering, honest diagnosability — do NOT add a second
            # enum value).
            await _safe_resume(manager, instance_id)
            detail_text = f"{type(e).__name__}: {e}"
            await context.terminalize(
                _PHASE_FAILED,
                detail={
                    "failure_kind": _FAILURE_KIND_ERROR,
                    "reason": "quiescence_timeout",
                    "checkpoint_id": None,
                    "exception": detail_text,
                },
            )
            await _emit_phase_event(
                manager,
                instance_id=instance_id,
                command_id=command_id,
                phase=_PHASE_FAILED,
                started_at_monotonic=started_at_monotonic,
                context=context,
                detail={
                    "failure_kind": _FAILURE_KIND_ERROR,
                    "reason": "quiescence_timeout",
                    "exception": detail_text,
                },
            )
            return
        else:
            paused_state_resume_ok = True

        # W-3.3 — re-read the checkpoint AFTER quiescence. The
        # ``messages`` and ``last_compacted_at`` we computed above
        # were from the pre-pause snapshot, which is
        # chronologically inverted (the engine would see the
        # messages that were live when the turn started, NOT the
        # messages that quiesced). Re-read here so the engine
        # invocation uses the post-quiescence snapshot.
        if pre_pause_graph_obj is not None and pre_pause_config is not None:
            try:
                checkpoint_state = await pre_pause_graph_obj.aget_state(
                    pre_pause_config
                )
                if checkpoint_state is not None:
                    last_compacted_at = (
                        (checkpoint_state.values or {}).get("compacted_at")
                        or last_compacted_at
                    )
                    messages = list(
                        (checkpoint_state.values or {}).get("messages", [])
                        or messages
                    )
            except Exception:
                # Re-read failed — keep the pre-pause snapshot (the
                # engine still has usable messages / last_compacted_at
                # data; this branch only protects against the brick
                # scenario where the read fails after the pause).
                pass

    elif run_status == "waiting_children":
        # O16: probe ONLY (timeout=0); drop into IDLE path on
        # quiescence. NEVER pause/cancel children — they're
        # legitimate work.
        quiesced = await manager.wait_for_instance_quiescent(instance_id, timeout=0)
        if not quiesced:
            # Treat as RUNNING-ish: reject quiescence_timeout (children
            # are active).
            await context.terminalize(
                _PHASE_FAILED,
                detail={
                    "failure_kind": _FAILURE_KIND_TIMEOUT,
                    "reason": "quiescence_timeout",
                    "checkpoint_id": None,
                    "exception": "waiting_children_not_quiescent",
                },
            )
            await _emit_phase_event(
                manager,
                instance_id=instance_id,
                command_id=command_id,
                phase=_PHASE_FAILED,
                started_at_monotonic=started_at_monotonic,
                context=context,
                detail={
                    "failure_kind": _FAILURE_KIND_TIMEOUT,
                    "reason": "quiescence_timeout",
                    "exception": "waiting_children_not_quiescent",
                },
            )
            return

    # elif run_status in ("idle", "paused"): quiescent by definition.

    # ── 6. Acquire ExecutionGate (the per-instance asyncio.Lock) ─────
    async def _in_gate() -> None:
        # W-3.1 / W-3.2 — NO ``nonlocal phase_seq`` — the registry is
        # the single source of truth.
        # Re-read has_instance_busy UNDER the gate (architect §6
        # IDLE re-check-under-gate correction). The gate is held
        # across the engine call + the persistence step so a
        # concurrent auto-resume turn cannot start mid-compaction.
        task_repo = getattr(manager, "_task_repo", None)
        if task_repo is not None and run_status == "idle":
            try:
                if task_repo.has_instance_busy(instance_id):
                    # Retry-once — the probe result was stale at
                    # gate acquire time. One more short quiescence
                    # wait; if still busy, bail.
                    await asyncio.sleep(0)
                    if task_repo.has_instance_busy(instance_id):
                        raise StaleBusy(
                            "instance became busy between probe and gate acquire"
                        )
            except StaleBusy as e:
                await context.terminalize(
                    _PHASE_FAILED,
                    detail={
                        "failure_kind": _FAILURE_KIND_ERROR,
                        "reason": "stale_busy",
                        "checkpoint_id": None,
                        "exception": type(e).__name__,
                    },
                )
                await _emit_phase_event(
                    manager,
                    instance_id=instance_id,
                    command_id=command_id,
                    phase=_PHASE_FAILED,
                    started_at_monotonic=started_at_monotonic,
                    context=context,
                    detail={
                        "failure_kind": _FAILURE_KIND_ERROR,
                        "reason": "stale_busy",
                        "exception": type(e).__name__,
                    },
                )
                raise _GateExit() from e

        # 7. Emit in_progress AFTER the gate is held (the SSE
        # message is best-effort; emit failure does NOT abort
        # compaction). W-3.1 / W-3.2 — ``update_phase(bump_seq=True)``
        # bumps the registry; the SSE emit reads the registry's
        # authoritative phase_seq.
        await context.update_phase(
            _PHASE_IN_PROGRESS,
            bump_seq=True,
            detail={
                "compacted_type": None,
                "resolved_window": resolved_window,
            },
        )
        await _emit_phase_event(
            manager,
            instance_id=instance_id,
            command_id=command_id,
            phase=_PHASE_IN_PROGRESS,
            started_at_monotonic=started_at_monotonic,
            context=context,
            eta_ms=int(_HEARTBEAT_INTERVAL_S * 1000),
        )

        # 8. Start the heartbeat coroutine — re-emits in_progress
        # every 10s. W-3.1 / W-3.2 — the heartbeat reads the
        # registry's phase_seq via ``context`` at emit time (NO
        # local counter, NO ``phase_seq_provider``).
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(
                manager,
                instance_id=instance_id,
                command_id=command_id,
                started_at_monotonic=started_at_monotonic,
                context=context,
            )
        )

        try:
            # 9. Build CompactionContext + call compact_state(force=True).
            llm_cfg = compactor.llm_config_with_headers or {}
            # The engine handles dedup + min-messages internally
            # (those checks still apply under force — WS-2.1
            # narrowed). The executor's pre-checks above only bypass
            # the threshold (which the engine also honors — the
            # bypass lives in the engine's force flag).
            ctx = CompactionContext(
                messages=messages,
                system_prompt_tokens=system_prompt_tokens,
                model_name=resolved_model,
                config=manager.config.compaction,
                llm_config=dict(llm_cfg),
                last_compacted_at=last_compacted_at,
            )
            result = await compactor.compact_state(ctx, force=True)
            if result is None:
                # Engine returned None — "can't compact" path. This
                # is NOT a below-floor noop (different semantics —
                # "would but shouldn't" vs "can't"); surface as a
                # failed outcome with noop_reason=below_floor so
                # the FE can still show "nothing to compact".
                await context.terminalize(
                    _PHASE_SUCCESS,
                    detail={
                        "compacted_type": _COMPACTED_TYPE_NOOP,
                        "noop_reason": _NOOP_REASON_BELOW_FLOOR,
                    },
                )
                await _emit_phase_event(
                    manager,
                    instance_id=instance_id,
                    command_id=command_id,
                    phase=_PHASE_SUCCESS,
                    started_at_monotonic=started_at_monotonic,
                    context=context,
                    detail={
                        "compacted_type": _COMPACTED_TYPE_NOOP,
                        "noop_reason": _NOOP_REASON_BELOW_FLOOR,
                    },
                )
                return

            # 10. Persist (D3 recipe — TWO aupdate_state calls in
            # order; nothing between them).
            await _persist_compaction_result(
                manager,
                instance_id=instance_id,
                result=result,
            )

            # 11. Map engine result → executor outcome via the
            # dedicated engine→wire mapping function (approver
            # note 1). The mapping covers every engine
            # ``compaction_type`` value + both ``failure_kind``
            # values + the wire-only ``noop``.
            wire = _map_engine_result_to_wire(result)

            # 12. Emit context_usage_for_instance — FE token-drop refresh.
            try:
                await manager._messaging_service.emit_context_usage_for_instance(
                    instance_id
                )
            except Exception as e:  # pragma: no cover — defensive
                logger.warning(
                    "[/compact] emit_context_usage failed for %s...: %s",
                    instance_id[:8],
                    type(e).__name__,
                )

            # 13. Terminalize — the phase depends on the mapping.
            terminal_phase = wire.terminal_phase
            terminal_detail = dict(wire.detail)

            # Two-step emit: timed_out → fallback_applied (WS-5
            # §7). We emit timed_out first so the FE can show the
            # transition, then the terminal fallback_applied.
            # W-3.1 / W-3.2 — both emits read the registry's
            # authoritative phase_seq via ``context``.
            if terminal_phase == _PHASE_FALLBACK_APPLIED:
                await context.update_phase(
                    _PHASE_TIMED_OUT,
                    bump_seq=True,
                    detail=terminal_detail,
                )
                await _emit_phase_event(
                    manager,
                    instance_id=instance_id,
                    command_id=command_id,
                    phase=_PHASE_TIMED_OUT,
                    started_at_monotonic=started_at_monotonic,
                    context=context,
                    detail=terminal_detail,
                )

            await context.terminalize(terminal_phase, detail=terminal_detail)
            await _emit_phase_event(
                manager,
                instance_id=instance_id,
                command_id=command_id,
                phase=terminal_phase,
                started_at_monotonic=started_at_monotonic,
                context=context,
                detail=terminal_detail,
            )

            if needs_pause_resume and paused_state_resume_ok:
                await _safe_resume(manager, instance_id)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass

    # Run inside the per-instance ExecutionGate.
    try:
        await manager.execution_gate.run(
            instance_id,
            "compact",
            "command",
            _in_gate,
        )
    except _GateExit:
        # Inner gate returned early (stale-busy / terminal guard).
        # The terminalize + emit already happened inside _in_gate.
        if needs_pause_resume and paused_state_resume_ok:
            await _safe_resume(manager, instance_id)
    except Exception as e:
        # Any escape from the gate body surfaces as a terminal
        # ``failed`` event. The bg task MUST NOT crash (O9).
        logger.exception(
            "[/compact] unexpected error for %s...: %s",
            instance_id[:8],
            type(e).__name__,
        )
        await context.terminalize(
            _PHASE_FAILED,
            detail={
                "failure_kind": _FAILURE_KIND_ERROR,
                "reason": type(e).__name__,
            },
        )
        # W-3.1 / W-3.2 — phase_seq is read from the registry via
        # ``context`` inside ``_emit_phase_event`` (single source of
        # truth; no local counter exists).
        await _emit_phase_event(
            manager,
            instance_id=instance_id,
            command_id=command_id,
            phase=_PHASE_FAILED,
            started_at_monotonic=started_at_monotonic,
            context=context,
            detail={
                "failure_kind": _FAILURE_KIND_ERROR,
                "reason": type(e).__name__,
            },
        )
        if needs_pause_resume and paused_state_resume_ok:
            await _safe_resume(manager, instance_id)


# ─────────────────────────────────────────────────────────────────────────
# WS-4.2 — engine → wire mapping (approver note 1)
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class WireOutcome:
    """Executor-side mapping of an engine result.

    Attributes:
        terminal_phase: The SSE terminal phase to emit
            (``"success" | "timed_out" | "fallback_applied" |
            "failed"``).
        detail: The SSE detail dict (WS-5 §7 shape).
    """

    terminal_phase: str
    detail: dict


def _map_engine_result_to_wire(result: CompactionResult) -> WireOutcome:
    """Map engine ``CompactionResult`` → executor wire outcome.

    Three-way mapping (approver note 1, plan §7 amendment, W-4.2):

    * ``summary`` → ``success`` (full summarization succeeded).
    * ``partial_summary`` + ``failure_kind="timeout"`` →
      ``timed_out → fallback_applied`` (the un-summarized span was
      trimmed; partial summaries survived).
    * ``truncation`` + ``failure_kind="timeout"`` →
      ``timed_out → fallback_applied`` (no summaries; truncate
      fallback fired on timeout).
    * ``truncation`` + ``failure_kind="error"`` → ``failed`` + a
      fallback note if the truncate fallback actually applied
      (W-4.2 — engine misclassifies a genuine LLM error that
      applied the truncate fallback as ``failure_kind="error"``;
      mapping now distinguishes so FE does not see "timed out"
      for a real error).
    * ``emergency_truncation`` → ``timed_out → fallback_applied``
      (documented choice — the wire enum only carries
      ``truncation`` / ``partial_summary`` for the timed-out /
      fallback cases; emergency is mapped to ``truncation`` at the
      detail level).

    Returns:
        :class:`WireOutcome` carrying the terminal phase + detail.
    """
    ctype = result.compaction_type
    fk = result.failure_kind

    detail: dict = {
        "compacted_type": _wire_compacted_type(ctype),
        "failure_kind": fk,
        "tokens_before": result.tokens_before,
        "tokens_after": result.tokens_after,
        "tokens_saved": result.tokens_saved,
    }
    if result.summarization_error:
        detail["summarization_error"] = result.summarization_error
    if result.forced:
        detail["forced"] = True

    if ctype == _COMPACTED_TYPE_SUMMARY:
        return WireOutcome(terminal_phase=_PHASE_SUCCESS, detail=detail)

    if ctype in (
        _COMPACTED_TYPE_PARTIAL_SUMMARY,
        _COMPACTED_TYPE_TRUNCATION,
        _COMPACTED_TYPE_EMERGENCY_TRUNCATION,
    ):
        # W-4.2 — branch on failure_kind BEFORE selecting the terminal
        # phase. A genuine LLM error that applied the truncate fallback
        # (failure_kind="error" + compacted_type="truncation") is a
        # failed outcome, NOT timed_out → fallback_applied. The
        # fallback note carries the diagnostic so the FE can still
        # show "we kept your messages, just trimmed" alongside the
        # failure detail.
        if fk == "error":
            # Error case — failed + fallback note. The wire enum
            # has no "failed_with_fallback" so we encode via detail.
            detail.setdefault("fallback_applied", True)
            return WireOutcome(terminal_phase=_PHASE_FAILED, detail=detail)
        # timeout (or None for the emergency / unknown path) →
        # timed_out → fallback_applied (two-step in caller).
        if ctype == _COMPACTED_TYPE_EMERGENCY_TRUNCATION:
            # Documented mapping — emergency → wire truncation.
            detail["compacted_type"] = _COMPACTED_TYPE_TRUNCATION
            detail["engine_compacted_type"] = _COMPACTED_TYPE_EMERGENCY_TRUNCATION
        return WireOutcome(
            terminal_phase=_PHASE_FALLBACK_APPLIED, detail=detail
        )

    # Unknown / unhandled engine enum — surface as failed.
    return WireOutcome(
        terminal_phase=_PHASE_FAILED,
        detail={
            **detail,
            "reason": "unknown_compaction_type",
            "engine_compacted_type": ctype,
        },
    )


def _wire_compacted_type(engine_compacted_type: str) -> str:
    """Translate engine ``compaction_type`` → wire ``compacted_type``.

    The wire enum is ``summary | partial_summary | truncation | noop``
    (WS-5 §7). The engine emits ``summary | partial_summary |
    truncation | emergency_truncation`` (``chunked_summarization``
    is collapsed into ``summary`` by the WS-3.4 amendment). The
    executor-only ``noop`` value comes from pre-checks; this helper
    is NOT used for noop paths.

    Args:
        engine_compacted_type: The ``CompactionResult.compaction_type``
            value.

    Returns:
        The wire-compatible ``compacted_type``. Emergency is mapped
        to ``truncation`` at this layer (the diagnostic key
        ``engine_compacted_type`` preserves the original).
    """
    if engine_compacted_type in (
        _COMPACTED_TYPE_SUMMARY,
        _COMPACTED_TYPE_PARTIAL_SUMMARY,
        _COMPACTED_TYPE_TRUNCATION,
    ):
        return engine_compacted_type
    if engine_compacted_type == _COMPACTED_TYPE_EMERGENCY_TRUNCATION:
        return _COMPACTED_TYPE_TRUNCATION
    # Unknown — return as-is so the wire enum stays intact and the
    # caller can attach the diagnostic key.
    return engine_compacted_type


# ─────────────────────────────────────────────────────────────────────────
# Helpers — heartbeat, safe resume, gate exit sentinel
# ─────────────────────────────────────────────────────────────────────────


async def _heartbeat_loop(
    manager: Any,
    *,
    instance_id: str,
    command_id: str,
    started_at_monotonic: float,
    context: Any,
) -> None:
    """Heartbeat — re-emit ``in_progress`` every 10s.

    The loop sleeps ``_HEARTBEAT_INTERVAL_S`` between emits. The
    executor's outer finally cancels this task; ``CancelledError`` is
    swallowed inside ``wait``.

    W-3.1 / W-3.2 — the heartbeat READS the dispatcher's
    ``current_phase_seq`` (single source of truth) via
    ``context.current_phase_seq()`` at emit time — it does NOT
    advance it. NO local counter, NO ``phase_seq_provider``
    closure — the shared counter is advanced on terminalize via
    ``update_phase(bump_seq=True)``, so a heartbeat re-emit lands
    on the registry's current value. The previous local counter
    could collide with a terminal event on a >10s compaction (FE
    dedup drops the terminal); this design is collision-proof.
    """
    try:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
            await _emit_phase_event(
                manager,
                instance_id=instance_id,
                command_id=command_id,
                phase=_PHASE_IN_PROGRESS,
                started_at_monotonic=started_at_monotonic,
                eta_ms=int(_HEARTBEAT_INTERVAL_S * 1000),
                context=context,
            )
    except asyncio.CancelledError:
        return
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(
            "[/compact] heartbeat loop crashed for %s...: %s",
            instance_id[:8],
            type(e).__name__,
        )


async def _safe_resume(manager: Any, instance_id: str) -> None:
    """Best-effort ``resume_instance_cascade`` — never raises.

    Used in the O9 ``finally`` blocks to ensure a rejected command
    does not leave the instance in a state the user did not ask for.
    Resume failures are logged at WARNING; the executor's caller
    emits the rejection with ``detail`` carrying ``left-paused``
    when the resume itself failed.
    """
    try:
        await manager.resume_instance_cascade(instance_id)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(
            "[/compact] resume_instance_cascade failed for %s...: %s",
            instance_id[:8],
            type(e).__name__,
        )


async def _persist_compaction_result(
    manager: Any,
    *,
    instance_id: str,
    result: CompactionResult,
) -> None:
    """Persist the engine result via the executor-safe recipe (C1).

    D3: TWO ``aupdate_state`` calls in this exact order, nothing
    between them — direct-list CONCATENATES under
    ``add_messages``, so the messages call carries
    ``RemoveMessage`` set + summary TOGETHER (single-write) and the
    second call carries ``compacted_at`` (D12 declared schema
    field at graph.py:2433-2438).

    C1 BINDING — DIFFERENT FROM PROACTIVE PATH:

    * The proactive path (``instance_messaging.py`` ~:1190-1202)
      uses ``as_node="agent"`` because it persists INSIDE the
      graph-task frame, where the next pointer is already wired.
      That path is BYTE-EQUIVALENT per the C1 binding.
    * The executor's path persists OUTSIDE the graph-task frame —
      the instance may be quiescent (next=() on a post-turn IDLE
      instance). Calling ``aupdate_state(as_node="agent")`` on a
      terminal / quiescent pointer can interact badly with
      ``interrupt_before=['agent']`` configurations (the documented
      brick collapse in
      ``test_compact_executor_revive_brick_e2e.py`` TestBrickCollapseOnRealGraph).

      Empirically determined (real-graph exploration, 2026-08-31):
      for a NON-terminal ``as_node="agent"`` persistence, a
      subsequent ``astream(graph_input)`` runs the agent normally.
      But the conservative executor recipe OMITS ``as_node`` — LangGraph
      interprets the call as an external write (the same shape as the
      proactive path's post-brick window) so the next pointer is not
      touched and the brick interaction is impossible.

    The canary test
    ``test_compact_executor_revive_brick_e2e.py``
    ``TestExecutorCompactOnRealQuiescentInstanceSucceeds`` proves the
    invariant end-to-end on a real graph + file-backed SQLite
    (``tmp_path``): (a) ``execute_compact`` succeeds against a
    genuinely quiescent checkpoint; (b) a subsequent
    ``astream(graph_input)`` runs the agent.
    """
    graph = await manager.get_instance(instance_id)
    config = {"configurable": {"thread_id": instance_id}}
    replacement: list[BaseMessage] = list(result.replacement_messages or [])

    # First call: messages (RemoveMessage set + summary together —
    # direct-list assignment concatenates under add_messages).
    # C1: NO ``as_node`` — the executor persists outside the graph-task
    # frame; the brick-interaction window is closed by the missing
    # ``as_node`` argument (LangGraph treats this as an external write).
    await graph.aupdate_state(
        config,
        {"messages": replacement},
    )

    # Second call: compacted_at stamp (D12). Skipped if the engine
    # didn't stamp one (shouldn't happen — the engine always stamps
    # the timestamp).
    if result.compacted_at:
        await graph.aupdate_state(
            config,
            {"compacted_at": result.compacted_at},
        )


# ─────────────────────────────────────────────────────────────────────────
# Internal sentinel exceptions
# ─────────────────────────────────────────────────────────────────────────


class WaitForQuiescenceTimeout(Exception):
    """Raised when the 30s post-pause quiescence wait elapses.

    O9: surfaces as a single ``reason=quiescence_timeout`` rejection
    (with the exception class name in ``detail``). We do NOT add a
    second enum value for this case — the class name carries the
    diagnosability (single FE rendering, honest signal).
    """


class StaleBusy(Exception):
    """Raised when ``has_instance_busy`` flips between probe + gate."""


class _GateExit(Exception):
    """Internal sentinel — inner gate body returned early.

    Raised to unwind the ``gate.run`` stack WITHOUT counting as an
    external failure. The caller checks for this exception type and
    treats it as "terminal already emitted, just clean up".
    """


# ─────────────────────────────────────────────────────────────────────────
# WS-1.5 — CommandSpec registration helper
# ─────────────────────────────────────────────────────────────────────────


def register_compact_command(dispatcher: Any) -> None:
    """Register ``/compact`` into the :class:`CommandDispatcher`.

    Lazy import of the dispatcher module is NOT necessary here — the
    executor coder is downstream of the dispatcher coder (no import
    cycle). This helper exists so the bootstrap call site in
    ``InstanceManager.initialize`` stays readable.

    The handler signature matches
    :class:`daemon.services.command_dispatcher.CommandSpec.handler`:

        async def handler(*, instance_id, args, command_id, context)

    Rate limit inherits the global minimum interval (we do not pin
    a per-spec override — the dispatcher's ``max(self.min_interval_s,
    spec.rate_limit_per_instance)`` handles it).
    Availability is left unpopulated (O-B6 — the per-agent policy
    hook is designed-in but not populated in phase 1).
    """
    spec = _build_compact_spec()
    dispatcher.registry.register(spec)


def _build_compact_spec() -> Any:
    """Build the ``/compact`` :class:`CommandSpec`.

    Kept as a helper so the registration site reads as a single
    line and the spec shape is testable in isolation.
    """
    from .command_dispatcher import CommandSpec

    async def _handler(
        *,
        instance_id: str,
        args: str,
        command_id: str,
        context: Any,
    ) -> None:
        # Resolve the manager off the dispatcher's bound manager
        # ref — the executor never sees the manager directly so
        # the dispatcher can wire it through context if it ever
        # needs to. Today we pull it via the dispatcher's
        # ``_manager`` attribute (the dispatcher is constructed
        # with the manager reference and the WS-1 surface
        # exposes it via ``dispatcher.manager`` — see
        # ``InstanceManager.command_dispatcher``).
        manager = getattr(context.dispatcher, "_manager", None)
        if manager is None:  # pragma: no cover — defensive
            await context.terminalize(
                _PHASE_FAILED,
                detail={"failure_kind": _FAILURE_KIND_ERROR, "reason": "no_manager"},
            )
            return
        await execute_compact(
            manager,
            instance_id=instance_id,
            command_id=command_id,
            context=context,
        )

    return CommandSpec(
        name="compact",
        description="On-demand context compaction",
        availability=None,  # O-B6 — unpopulated
        rate_limit_per_instance=0,  # inherits dispatcher min_interval_s
        handler=_handler,
    )


__all__ = [
    "NoopOutcome",
    "WireOutcome",
    "WaitForQuiescenceTimeout",
    "StaleBusy",
    "execute_compact",
    "register_compact_command",
    "_is_terminal_checkpoint",
    "_map_engine_result_to_wire",
    "_resolve_per_instance_model",
    "_resolved_context_window",
    "_is_recently_compacted",
    "_HEARTBEAT_INTERVAL_S",
    "_QUIESCENCE_TIMEOUT_S",
]
