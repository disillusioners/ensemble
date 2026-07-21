from __future__ import annotations

from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import (
    BaseChatOpenAI,
    _convert_delta_to_message_chunk as _base_convert_delta_to_message_chunk,
)
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.messages import BaseMessageChunk
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.config import RunnableConfig
from langchain_core.messages.ai import AIMessageChunk, UsageMetadata
from typing import Any, ClassVar, Mapping, cast
from dataclasses import dataclass, field
import asyncio
import json
import logging
import re
import uuid
import openai
from tenacity import Retrying, stop_after_attempt, wait_exponential_jitter

logger = logging.getLogger(__name__)


# ============================================================================
# get_instance_info throttling (escalating backoff)
# ============================================================================
# Counter resets on any non-gii message — see ToolThrottleSlot.bump/reset.
# Delay table maps the consecutive-call count (after the bump) to seconds
# spent sleeping in agent_node before the next LLM call.
# Scope: detects CONSECUTIVE gii calls only. When the agent emits gii in
# parallel with other tools in one AIMessage, ToolNode produces interleaved
# ToolMessages so messages[-1] may not be gii and the counter resets. This is
# intentional — the throttle targets consecutive single-tool polling loops.
GII_TOOL_NAME = "get_instance_info"
GII_DELAY_MAP: dict[int, int] = {
    3: 180,   # 3rd consecutive call: 3 min
    4: 300,   # 4th: 5 min
    5: 600,   # 5th: 10 min
}
GII_MAX_DELAY = 900  # 6+ consecutive: 15 min (cap)


# ============================================================================
# General Hallucination Loop Breaker (Phase 1 — detection only)
# ============================================================================
# Detects CONSECUTIVE identical tool-call patterns regardless of which tool.
# Unlike the GII throttle (which is a counter that resets on any non-gii
# message), the loop breaker scans the message tail and groups parallel tool
# calls by canonical (name, args) signature. This catches parallel-call loops
# that the GII counter misses and survives compaction/reactive re-reads.
# State lives on InstanceManager._loop_breaker_state; access goes through
# LoopBreakerSlot (duck-typed getattr delegation).
LOOP_BREAKER_DEFAULT_THRESHOLD = 3
LOOP_BREAKER_SUMMARIZATION_TIMEOUT_SECONDS = 30
LOOP_BREAKER_REPAIR_PREFIX = "repair-"

# Phase 2 / Repair engine prompt — sent to the LLM when summarizing a detected
# loop. The repair flow calls ``LoopRepairer._summarize_loop`` which builds
# the prompt via ``str.format`` and falls back to a static truncation summary
# on timeout / error. Keep parameter names stable: ``tool_name``, ``tool_args``,
# ``count``, ``conversation_excerpt``.
REPAIR_SUMMARIZATION_PROMPT = """You are analyzing an AI agent's conversation history that has entered a repetitive loop.

The agent repeatedly called the tool "{tool_name}" with these arguments:
{tool_args}

This happened {count} times consecutively without making progress.

Recent conversation context:
{conversation_excerpt}

Please provide a concise summary (2-3 sentences) of:
1. What the agent was trying to accomplish
2. Why it appears to be stuck in a loop
3. What alternative approach it should try

Be specific and actionable."""

from .llm_error_classifier import (
    classify_llm_errors,
    ContextLengthExceededError,
    TIMEOUT_EXCEPTIONS,
    TRANSIENT_EXCEPTIONS,
    TransientAPIError,
    _truncate_error,
)
from .response_validation import LLMResponseValidationError
from .language_detection import detect_wrong_language
from .utils import serialize_message
from .config import LoopBreakerConfig
# Lazy import below — module-level ``from .services.language_utils`` would
# trigger daemon.services.__init__ → instance_lifecycle → compaction →
# graph (cycle) before this module finishes loading.


# ============================================================================
# Phase 1 / User Message Injection: lightweight handle (C1)
# ============================================================================
# The agent_node pulls a pending user-injection from this handle on every
# invocation and clears it immediately before invoking the LLM (C2). The
# handle intentionally wraps only the two methods that :func:`create_agent_node`
# needs (``get`` / ``clear``) — it does NOT pass the full ``InstanceManager``
# to the agent_node closure, so the graph can be tested with a plain mock
# (see ``tests/test_injection_graph.py``) without spinning up the daemon.
#
# Phase 2 will extend this same handle with ``set()`` for the API path; for
# now the ``set`` side lives on ``InstanceManager`` because no agent-node
# code path needs to write.

class InjectionSlot:
    """Lightweight, mock-friendly handle around InstanceManager injection slot.

    Threaded into :func:`build_instance_graph` and :func:`create_agent_node`
    via factory closure (C1), mirroring the existing ``compactor`` /
    ``graph_ref`` closure parameters. Backed by ``InstanceManager`` so the
    underlying dict is the single source of truth across all paths.

    Args:
        manager: The owning :class:`InstanceManager`. Tests may pass any
            object exposing ``get_injection`` and ``clear_injection``
            methods; the type is intentionally broad.

    """

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def get(self, instance_id: str) -> dict | None:
        """Peek the pending injection without clearing it.

        Returns ``None`` when no injection exists for this instance.
        """
        getter = getattr(self._manager, "get_injection", None)
        if getter is None:
            return None
        return getter(instance_id)

    def clear(self, instance_id: str) -> dict | None:
        """Pop and return the pending injection (or ``None``).

        Idempotent: calling when no injection exists is a no-op.
        """
        clearer = getattr(self._manager, "clear_injection", None)
        if clearer is None:
            return None
        return clearer(instance_id)


class ToolThrottleSlot:
    """Lightweight, mock-friendly handle around InstanceManager tool-throttle counters.

    Mirrors :class:`InjectionSlot`'s pattern: only the methods agent_node needs
    are exposed, the manager reference is duck-typed via ``getattr`` so the
    agent_node can be tested without a real ``InstanceManager``.

    Args:
        manager: The owning :class:`InstanceManager` (or any object exposing
            ``bump_gii_throttle``, ``reset_gii_throttle``, and
            ``get_gii_throttle_count`` methods).
    """

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def bump(self, instance_id: str) -> int:
        """Increment and return the consecutive ``get_instance_info`` count."""
        bumper = getattr(self._manager, "bump_gii_throttle", None)
        if bumper is None:
            return 0
        return bumper(instance_id)

    def reset(self, instance_id: str) -> None:
        """Reset the consecutive-call counter (no-op when unset)."""
        resetter = getattr(self._manager, "reset_gii_throttle", None)
        if resetter is not None:
            resetter(instance_id)

    def get_count(self, instance_id: str) -> int:
        """Return the current consecutive-call count (0 if unset)."""
        getter = getattr(self._manager, "get_gii_throttle_count", None)
        if getter is None:
            return 0
        return getter(instance_id)


class LoopBreakerSlot:
    """Lightweight, mock-friendly handle around InstanceManager loop-breaker state.

    Mirrors :class:`ToolThrottleSlot`'s duck-typed ``getattr`` pattern: only the
    methods ``agent_node`` needs (Phase 3 wiring) are exposed, the manager
    reference is duck-typed so tests can pass any object exposing the matching
    surface without instantiating a real ``InstanceManager``.

    Args:
        manager: The owning :class:`InstanceManager` (or any object exposing
            ``record_loop_repair``, ``reset_loop_breaker``, and
            ``get_loop_repair_count`` methods).
    """

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def record_repair(self, instance_id: str, summary: str) -> int:
        """Record a repair event. Returns the new repair count for the instance.

        Returns ``0`` when the manager does not expose ``record_loop_repair`` —
        matches the no-op default of the ``ToolThrottleSlot.bump`` family so
        tests can stub the manager without the loop-breaker surface.
        """
        recorder = getattr(self._manager, "record_loop_repair", None)
        if recorder is None:
            return 0
        return recorder(instance_id, summary)

    def clear(self, instance_id: str) -> None:
        """Clear loop-breaker state for instance (no-op when unset)."""
        clearer = getattr(self._manager, "reset_loop_breaker", None)
        if clearer is not None:
            clearer(instance_id)

    def get_repair_count(self, instance_id: str) -> int:
        """Return the current repair count (0 if unset)."""
        getter = getattr(self._manager, "get_loop_repair_count", None)
        if getter is None:
            return 0
        return getter(instance_id)


@dataclass
class LoopDetectionResult:
    """Result of a :class:`LoopDetector` scan.

    IMPORTANT: ``loop_messages`` excludes the evidence unit. The evidence unit
    (oldest matching call+result pair) is preserved so the agent has context
    about what it was doing before the loop. Only its IDs appear in
    ``evidence_message_ids`` and those messages are excluded from removal.

    Attributes:
        tool_name: Primary tool in the detected loop (first call's name).
        tool_args: Canonical args of the loop (first call's args).
        repetition_count: Number of consecutive identical units detected.
        loop_messages: Repetitive messages to REMOVE (excludes the evidence unit).
        evidence_message_ids: IDs to KEEP — the 1 oldest call+result pair.
    """

    tool_name: str
    tool_args: dict
    repetition_count: int
    loop_messages: list[BaseMessage] = field(default_factory=list)
    evidence_message_ids: list[str] = field(default_factory=list)


class LoopDetector:
    """Static utility that scans a message tail for consecutive identical tool-call patterns.

    The detector is intentionally stateless — it inspects the message list
    directly (the source of truth) instead of a counter, so it survives
    compaction, pause/resume, and crash recovery.

    Detection rules:
        * Walk backwards from ``messages[-1]``.
        * A "unit" is one ``AIMessage`` with ``tool_calls`` plus its matching
          ``ToolMessage`` results (matched via ``tool_call_id``).
        * A unit's signature is the sorted set of ``(name, args)`` pairs with
          args canonicalised via ``json.dumps(args, sort_keys=True, separators=(",",":"))``.
        * Parallel tool calls (multiple ``tool_calls`` in one ``AIMessage``)
          produce a single signature per unit.
        * Count consecutive units with the same signature.
        * If the count reaches ``threshold``, the oldest unit is kept as
          evidence and all newer duplicate units are flagged for removal.
        * Walking stops at any non-tool message (HumanMessage, plain AIMessage
          without ``tool_calls``, plain SystemMessage, etc.).
        * If every tool in a unit is in ``excluded_tools``, that unit breaks
          the chain (we don't penalise legitimately polled resources).
    """

    @staticmethod
    def _compute_tool_signature(ai_message: AIMessage) -> str:
        """Compute a canonical signature for an ``AIMessage``'s tool calls.

        Groups all ``tool_calls`` in the message into a sorted set of
        ``(name, args)`` pairs. Handles parallel tool calls: multiple calls
        in one ``AIMessage`` yield a single signature covering all of them.

        Returns an empty string when the message has no tool calls (caller
        should treat that as "not a tool unit").
        """
        tool_calls = getattr(ai_message, "tool_calls", None) or []
        if not tool_calls:
            return ""
        pairs: list[str] = []
        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("args", {})
            args_str = json.dumps(args, sort_keys=True, separators=(",", ":"))
            pairs.append(f"{name}:{args_str}")
        pairs.sort()
        return "|".join(pairs)

    @staticmethod
    def scan(
        messages: list[BaseMessage],
        threshold: int = LOOP_BREAKER_DEFAULT_THRESHOLD,
        excluded_tools: list[str] | None = None,
    ) -> LoopDetectionResult | None:
        """Scan message tail for consecutive identical tool-call patterns.

        Args:
            messages: The full conversation history (most recent message last).
            threshold: Number of consecutive identical units required to
                trigger detection. Defaults to :data:`LOOP_BREAKER_DEFAULT_THRESHOLD`.
            excluded_tools: Tool names to skip — a unit whose tool_calls are
                entirely excluded breaks the chain. ``None`` means no
                exclusions.

        Returns:
            :class:`LoopDetectionResult` if a loop is detected, ``None``
            otherwise. The returned ``loop_messages`` exclude the oldest
            evidence unit so callers can build ``RemoveMessage`` sentinels
            without losing context.
        """
        if not messages or threshold < 1:
            return None
        excluded = set(excluded_tools or [])

        # Build unit records walking backwards. Each record captures the
        # AIMessage index and its signature so we can later resolve the
        # matching ToolMessages by tool_call_id.
        units: list[tuple[str, int]] = []  # (signature, ai_message_index)
        i = len(messages) - 1
        while i >= 0:
            msg = messages[i]
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                sig = LoopDetector._compute_tool_signature(msg)
                if not sig:
                    break
                tool_names = {tc.get("name", "") for tc in (msg.tool_calls or [])}
                if tool_names and tool_names.issubset(excluded):
                    # Excluded tools break the chain — the agent is polling
                    # something legitimately.
                    break
                units.append((sig, i))
            elif isinstance(msg, ToolMessage):
                # ToolMessages are folded into the unit when we walk back to
                # their issuing AIMessage (matched via tool_call_id in the
                # evidence/loop_message collection below). For counting
                # purposes we ignore them and keep walking.
                pass
            else:
                # HumanMessage, SystemMessage, plain AIMessage without
                # tool_calls — non-tool message breaks the consecutive chain.
                break
            i -= 1

        if not units:
            return None

        # Count consecutive identical signatures from the tail (most recent).
        # ``units`` is newest-first because we walked backwards.
        first_sig = units[0][0]
        consecutive = 0
        loop_indices: list[int] = []
        for sig, idx in units:
            if sig == first_sig:
                consecutive += 1
                loop_indices.append(idx)
            else:
                break

        if consecutive < threshold:
            return None

        # ``loop_indices`` is newest-first; the LAST entry is the oldest
        # matching AIMessage — that's the evidence unit we KEEP.
        evidence_unit_idx = loop_indices[-1]
        evidence_ai_msg = messages[evidence_unit_idx]

        # Collect message IDs in the evidence unit (AIMessage + its ToolMessages).
        evidence_message_ids: set[str] = set()
        evidence_ai_id = getattr(evidence_ai_msg, "id", None)
        if evidence_ai_id:
            evidence_message_ids.add(evidence_ai_id)
        evidence_tool_call_ids = {
            tc.get("id", "")
            for tc in (getattr(evidence_ai_msg, "tool_calls", None) or [])
            if tc.get("id", "")
        }
        for msg in messages:
            if isinstance(msg, ToolMessage) and getattr(msg, "tool_call_id", "") in evidence_tool_call_ids:
                tool_msg_id = getattr(msg, "id", None)
                if tool_msg_id:
                    evidence_message_ids.add(tool_msg_id)

        # Collect loop_messages: every unit's AIMessage + matching ToolMessages,
        # EXCEPT the evidence unit.
        loop_messages: list[BaseMessage] = []
        for idx in loop_indices[:-1]:  # skip evidence (last = oldest)
            ai_msg = messages[idx]
            loop_messages.append(ai_msg)
            tool_call_ids = {
                tc.get("id", "")
                for tc in (getattr(ai_msg, "tool_calls", None) or [])
                if tc.get("id", "")
            }
            for msg in messages:
                if isinstance(msg, ToolMessage) and getattr(msg, "tool_call_id", "") in tool_call_ids:
                    loop_messages.append(msg)

        # Primary tool/args for the summary come from the first (newest) unit.
        first_ai_msg = messages[loop_indices[0]]
        first_tc = (getattr(first_ai_msg, "tool_calls", None) or [{}])[0]

        return LoopDetectionResult(
            tool_name=first_tc.get("name", "unknown"),
            tool_args=first_tc.get("args", {}),
            repetition_count=consecutive,
            loop_messages=loop_messages,
            evidence_message_ids=list(evidence_message_ids),
        )


# ============================================================================
# Phase 2 / Message Repair Engine
# ============================================================================
# When the :class:`LoopDetector` (Phase 1) flags a repetition, the
# :class:`LoopRepairer` below performs the actual repair: build
# ``RemoveMessage`` sentinels for the duplicate units, summarize the loop via
# the LLM (with timeout fallback to a static summary), construct a repair
# ``SystemMessage`` with a FRESH UUID, and apply the state update via
# ``graph.aupdate_state``. The reactive compaction pattern at
# ``daemon.graph.create_agent_node`` (the C3 fix at lines 1204-1217) is the
# reference for the re-read + injected_msg re-append sequence — keeping the
# two paths aligned means the same injection guarantee holds whether the
# recovery is triggered by context overflow or by a hallucination loop.
#
# Design contract (matches ``phase2-plan.md`` + ``notes.md``):
#   * RemoveMessage sentinels MUST come BEFORE the repair message in the
#     replacement list (LangGraph ``add_messages`` reducer processes
#     left-to-right).
#   * Repair message ID MUST be a fresh UUID — reusing an ID replaces the
#     existing message instead of appending (LangGraph ``add_messages``
#     reducer behaviour).
#   * ``clean_llm_config`` MUST run before constructing ``ThinkingChatOpenAI``
#     so the ``model_vision`` key never leaks into the text summarization
#     call (matches the 5+ existing call sites).
#   * Summarization MUST be wrapped in ``asyncio.wait_for(timeout=...)`` —
#     a hung LLM call would otherwise freeze ``agent_node`` indefinitely.
#   * On any failure (timeout, exception, or state-update error), the repair
#     returns the ORIGINAL message list so the graph can fall through to
#     ``recursion_limit`` rather than wedging the agent.

# ``_extract_text_from_content`` is imported lazily inside
# ``LoopRepairer._build_excerpt`` to avoid a circular import: ``compaction.py``
# already imports ``clean_llm_config`` from this module at module-load time,
# so a top-level ``from .compaction import _extract_text_from_content`` here
# would deadlock at import time. The lazy import mirrors the
# ``from .compaction import CompactionContext`` pattern already used in
# :func:`create_agent_node` for the same reason.


@dataclass
class RepairContext:
    """Inputs for a single :class:`LoopRepairer` invocation.

    Carries everything :meth:`LoopRepairer.repair` needs without holding a
    reference to ``InstanceManager`` — the repairer is intentionally a
    stateless helper so the agent-node closure can construct it lazily and
    tests can pass arbitrary objects exposing the matching surface.

    Attributes:
        detection: Loop detection result from :class:`LoopDetector` (Phase 1).
            ``loop_messages`` already excludes the evidence unit; the repairer
            builds ``RemoveMessage`` sentinels only for these.
        messages: Full conversation history at detection time (oldest-first).
            Used for the LLM summarization excerpt — last N messages only.
        thread_config: LangGraph thread config (``{"configurable": {"thread_id": ...}}``).
            Passed to ``graph.aupdate_state`` and ``graph.aget_state``.
        graph: Compiled LangGraph graph (or any object exposing
            ``aupdate_state`` / ``aget_state``). Held by reference, not by
            closure, so tests can substitute an ``AsyncMock``.
        llm_config: Session LLM config used for the summarization call.
            Passed through ``clean_llm_config`` to strip ``model_vision``
            before constructing ``ThinkingChatOpenAI``.
        system_prompt: System prompt for the agent session. Carried for
            parity with reactive compaction; not consumed by the current
            repair logic but kept so future call sites can match the same
            shape as ``create_agent_node``.
        injected_msg: Optional ``HumanMessage`` that was pending in the
            injection slot when the loop was detected. Re-appended to
            ``repaired_messages`` after the state re-read (C3 pattern, see
            ``daemon.graph.create_agent_node`` lines 1204-1217) so the LLM
            retry sees the user's injection exactly as the first attempt
            did. ``None`` when no injection was consumed.
        summarization_timeout_seconds: Override for the LLM summarization
            ``asyncio.wait_for`` timeout. ``0`` / unset defers to the
            repairer's own ``self._timeout_seconds`` and finally 30s.
    """

    detection: LoopDetectionResult
    messages: list[BaseMessage]
    thread_config: dict
    graph: Any  # compiled LangGraph graph (or AsyncMock stand-in)
    llm_config: dict
    system_prompt: str
    injected_msg: BaseMessage | None = None
    summarization_timeout_seconds: int = 30


@dataclass
class RepairResult:
    """Outcome of a :class:`LoopRepairer.repair` invocation.

    The caller (Phase 3 ``agent_node`` wiring, not in this phase) reads
    ``success`` to decide whether to re-invoke the LLM with
    ``repaired_messages`` or fall back to the original message list. Even on
    failure ``repaired_messages`` is populated with the ORIGINAL input — never
    ``None`` — so the call site can substitute it directly into
    ``agent_node``'s LLM call without further checks.
    """

    success: bool
    repaired_messages: list[BaseMessage]
    summary: str
    repair_message_id: str
    error: str | None = None


class LoopRepairer:
    """Repairs the message history when a hallucination loop is detected.

    Mirrors the reactive compaction pattern at
    :func:`daemon.graph.create_agent_node` (the C3 fix): remove the
    repetitive messages via ``RemoveMessage`` sentinels, summarize what the
    agent was doing, inject a fresh ``SystemMessage`` that nudges the LLM
    toward a different approach, and apply the change via
    ``graph.aupdate_state(as_node='agent')``. The injected user message (if
    any) is re-appended after the state re-read so the LLM retry does not
    silently lose it.

    The repairer is intentionally simple: no I/O of its own (the LLM call is
    synchronous via ``asyncio.to_thread``), no DB writes (the
    ``graph.aupdate_state`` call handles persistence), no callback wiring.
    Phase 3 will thread it into ``agent_node`` and ``LoopBreakerSlot`` will
    record the repair via ``record_repair``.
    """

    def __init__(self, timeout_seconds: int = LOOP_BREAKER_SUMMARIZATION_TIMEOUT_SECONDS):
        # The LLM config is supplied per-call via ``RepairContext.llm_config``
        # (mirrors how ``ContextCompactor`` works). Keeping the config out of
        # the constructor avoids threading it through every ``LoopRepairer``
        # instantiation site while matching the reactive-compaction shape.
        self._timeout_seconds = timeout_seconds or LOOP_BREAKER_SUMMARIZATION_TIMEOUT_SECONDS

    async def repair(self, context: RepairContext) -> RepairResult:
        """Execute the full repair flow for a detected loop.

        Steps (each wrapped in the outer ``try`` so any failure returns the
        ORIGINAL ``context.messages`` and a populated ``error``):

            1. Build ``RemoveMessage`` sentinels from ``detection.loop_messages``
               (evidence IDs are excluded — see :meth:`_build_removal_list`).
            2. Call LLM summarization with timeout fallback (see
               :meth:`_summarize_loop`). A hung LLM call never freezes
               ``agent_node`` because of the ``asyncio.wait_for`` guard.
            3. Build the repair ``SystemMessage`` with a FRESH UUID
               (``f"{LOOP_BREAKER_REPAIR_PREFIX}{uuid4()}"``) so the
               ``add_messages`` reducer appends rather than replaces.
            4. ``graph.aupdate_state(thread_config, {'messages': replacement},
               as_node='agent')`` — sentinels first, repair message last.
            5. Re-read state via ``graph.aget_state`` and extract the
               updated ``messages`` list.
            6. Re-append ``context.injected_msg`` if present (C3 pattern —
               the injection lives only in the local closure until the LLM
               call returns, so a checkpoint re-read would lose it).

        Args:
            context: Fully populated :class:`RepairContext`.

        Returns:
            :class:`RepairResult` with ``success=True`` and the post-repair
            message list on success; ``success=False`` and the ORIGINAL
            ``context.messages`` on any exception. ``error`` carries the
            exception string when ``success`` is False.
        """
        try:
            # Step 1: Build removal list.
            removals = self._build_removal_list(context.detection)
            logger.info(
                f"[LoopRepairer] Removing {len(removals)} repetitive messages "
                f"for tool '{context.detection.tool_name}' "
                f"(repetition_count={context.detection.repetition_count})"
            )

            # Step 2: LLM summarization (timeout fallback to static summary).
            # REVIEW FIX: prefer the per-context timeout (set by Phase 3
            # from ``LoopBreakerConfig.summarization_timeout_seconds``)
            # over the constructor default, so config flows through
            # without requiring a fresh ``LoopRepairer`` per call.
            timeout = (
                context.summarization_timeout_seconds
                or self._timeout_seconds
                or LOOP_BREAKER_SUMMARIZATION_TIMEOUT_SECONDS
            )
            summary = await self._summarize_loop(
                context.detection,
                context.messages,
                context.llm_config,
                timeout_seconds=timeout,
            )

            # Step 3: Build repair message with fresh UUID.
            repair_msg = self._build_repair_message(context.detection, summary)

            # Step 4: Apply state update. Order matters — RemoveMessage
            # sentinels must come BEFORE the repair message so the
            # ``add_messages`` reducer processes removals before appending
            # the summary (LangGraph processes the list left-to-right).
            replacement = list(removals) + [repair_msg]
            await context.graph.aupdate_state(
                context.thread_config,
                {'messages': replacement},
                as_node='agent',
            )
            logger.info(
                f"[LoopRepairer] State updated, repair message "
                f"{repair_msg.id[:16] if repair_msg.id else '<no-id>'}... injected"
            )

            # Step 5: Re-read state via the checkpoint (matches the
            # reactive compaction pattern at lines 1201-1202).
            updated_state = await context.graph.aget_state(context.thread_config)
            repaired_messages = list(updated_state.values.get('messages', []))

            # Step 6: C3 re-append — the injected user message lives only
            # in the closure, NOT in the checkpoint, so the re-read above
            # loses it. Re-append to ``repaired_messages`` so the LLM
            # retry sees the user's intent.
            if context.injected_msg is not None:
                repaired_messages = list(repaired_messages) + [context.injected_msg]

            return RepairResult(
                success=True,
                repaired_messages=repaired_messages,
                summary=summary,
                repair_message_id=repair_msg.id or "",
            )

        except Exception as e:
            # Any failure (LLM, state update, re-read) — fall back to the
            # ORIGINAL message list so the graph continues rather than
            # wedging. ``recursion_limit`` still protects against runaway
            # loops if the LLM keeps hallucinating after the failed repair.
            logger.error(
                f"[LoopRepairer] Repair failed: {type(e).__name__}: {e}",
                exc_info=True,
            )
            return RepairResult(
                success=False,
                repaired_messages=list(context.messages),
                summary="",
                repair_message_id="",
                error=str(e),
            )

    @staticmethod
    def _build_removal_list(detection: LoopDetectionResult) -> list[RemoveMessage]:
        """Build ``RemoveMessage`` sentinels for the duplicate units.

        The ``LoopDetector.scan`` algorithm already excluded the evidence
        unit (oldest matching call+result pair) from ``loop_messages``, so
        every message here is safe to remove. The ``evidence_message_ids``
        check is defensive: if the detector ever returns a result that
        includes an evidence ID in ``loop_messages``, we still preserve it
        (the reducer would otherwise remove the only context the agent
        has about what it was doing).

        Args:
            detection: Result from :class:`LoopDetector`.

        Returns:
            List of :class:`RemoveMessage` sentinels, one per removable
            message. Empty when nothing qualifies (no IDs, all evidence).
        """
        evidence_ids = set(detection.evidence_message_ids or [])
        removals: list[RemoveMessage] = []
        for msg in detection.loop_messages:
            msg_id = getattr(msg, "id", None)
            if msg_id and msg_id not in evidence_ids:
                removals.append(RemoveMessage(id=msg_id))
        return removals

    @staticmethod
    async def _summarize_loop(
        detection: LoopDetectionResult,
        messages: list[BaseMessage],
        llm_config: dict,
        timeout_seconds: int = LOOP_BREAKER_SUMMARIZATION_TIMEOUT_SECONDS,
    ) -> str:
        """Call LLM to summarize the loop, with strict timeout + fallback.

        Builds a focused prompt from :data:`REPAIR_SUMMARIZATION_PROMPT`,
        calls the session LLM (with ``clean_llm_config`` to strip
        ``model_vision``) and returns the response text. The call is wrapped
        in ``asyncio.wait_for(asyncio.to_thread(llm.invoke, [...]),
        timeout=timeout_seconds)`` so a hung LLM provider can never freeze
        ``agent_node``.

        On ``asyncio.TimeoutError`` OR any other Exception: log a warning
        and return the static fallback summary (``f"The agent called
        {tool_name} {count} times with the same arguments without
        progress."``). The fallback is sufficient to break the loop — it
        just lacks the contextual nuance of an LLM-generated summary.

        Args:
            detection: Loop detection result — supplies ``tool_name``,
                ``tool_args``, and ``repetition_count`` for the prompt.
            messages: Full conversation history — last 10 messages are
                included in the prompt as context.
            llm_config: Session LLM config; cleaned before constructing
                ``ThinkingChatOpenAI``.
            timeout_seconds: Hard timeout for the summarization call.

        Returns:
            Either the LLM-generated summary string or the static fallback
            on timeout / error. Never raises.
        """
        # Lazy import to avoid circular dependency with daemon.compaction
        # (see module-level comment above the ``RepairContext`` dataclass).
        from .compaction import _extract_text_from_content

        excerpt = LoopRepairer._build_excerpt(messages, max_messages=10)
        prompt = REPAIR_SUMMARIZATION_PROMPT.format(
            tool_name=detection.tool_name,
            tool_args=json.dumps(detection.tool_args, indent=2)[:500],
            count=detection.repetition_count,
            conversation_excerpt=excerpt,
        )

        fallback = (
            f"The agent called {detection.tool_name} "
            f"{detection.repetition_count} times with the same arguments "
            f"without progress."
        )

        try:
            # clean_llm_config strips model_vision — see note above
            # about the 5+ existing call sites. Same module as
            # ``_call_summarization_llm`` in ``daemon/compaction.py``.
            config = clean_llm_config(llm_config)
            llm = ThinkingChatOpenAI(**config)

            # CRITICAL: asyncio.to_thread keeps the LLM call off the
            # event loop (matches compaction.py:999) and asyncio.wait_for
            # enforces the hard timeout. If either guard fails we fall
            # back to the static summary rather than freezing the agent.
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    llm.invoke,
                    [
                        SystemMessage(
                            content=(
                                "You are a helpful assistant that analyzes "
                                "conversation patterns."
                            )
                        ),
                        HumanMessage(content=prompt),
                    ],
                ),
                timeout=timeout_seconds,
            )
            return _extract_text_from_content(response.content)

        except asyncio.TimeoutError:
            logger.warning(
                f"[LoopRepairer] Summarization timed out after {timeout_seconds}s, "
                f"using truncation fallback for tool '{detection.tool_name}'"
            )
            return fallback
        except Exception as e:
            logger.warning(
                f"[LoopRepairer] Summarization failed: {type(e).__name__}: {e}, "
                f"using fallback for tool '{detection.tool_name}'"
            )
            return fallback

    @staticmethod
    def _build_excerpt(messages: list[BaseMessage], max_messages: int = 10) -> str:
        """Build a text-only excerpt of the last ``max_messages`` entries.

        Used as the ``conversation_excerpt`` field of the summarization
        prompt. Multimodal content (list-of-dicts with image_url blocks) is
        flattened to text via ``_extract_text_from_content`` so the LLM
        only ever sees a plain string — never ``str(list_of_dicts)``
        garbage (see compaction.py:706 fix in the experience notes).

        Args:
            messages: Full conversation history (oldest-first). Only the
                last ``max_messages`` entries are included.
            max_messages: Maximum number of recent messages to include.
                Defaults to 10 — large enough for context, small enough to
                keep the prompt bounded.

        Returns:
            Newline-joined text excerpt. Empty string when ``messages``
            is empty.
        """
        # Lazy import to avoid circular dependency with daemon.compaction
        # (see module-level comment above the ``RepairContext`` dataclass).
        from .compaction import _extract_text_from_content

        if not messages:
            return ""
        tail = messages[-max_messages:]
        lines: list[str] = []
        for msg in tail:
            content = getattr(msg, "content", "") or ""
            text = _extract_text_from_content(content)
            msg_type = type(msg).__name__
            if text:
                lines.append(f"[{msg_type}] {text}")
            else:
                lines.append(f"[{msg_type}] <empty>")
        return "\n".join(lines)

    @staticmethod
    def _build_repair_message(detection: LoopDetectionResult, summary: str) -> SystemMessage:
        """Construct the repair ``SystemMessage`` with a fresh UUID.

        The UUID is fresh on every call (``uuid.uuid4()``) so the
        ``add_messages`` reducer appends the new message rather than
        replacing an existing one with the same ID (LangGraph reducer
        behaviour — see ``phase2-plan.md`` gotcha #1 and the resume
        message replacement bug in the experience notes).

        Args:
            detection: Loop detection result — supplies ``tool_name`` and
                ``repetition_count`` for the body text.
            summary: Either the LLM-generated summary or the static
                fallback (after a timeout / error).

        Returns:
            :class:`SystemMessage` with ID prefixed by
            :data:`LOOP_BREAKER_REPAIR_PREFIX` and the formatted repair
            content. The SystemMessage type matches the compaction summary
            pattern (``compaction.py:962``) and ``decisions.md`` D9 — a
            system-level directive, NOT a user message.
        """
        content = (
            f"[LOOP BREAKER — Repetitive tool call detected]\n\n"
            f"You have called the tool '{detection.tool_name}' "
            f"{detection.repetition_count} times consecutively with the "
            f"same arguments. This indicates you may be stuck in a loop.\n\n"
            f"Summary of what happened:\n{summary}\n\n"
            f"Please try a DIFFERENT approach. Consider:\n"
            f"- Using a different tool\n"
            f"- Changing the arguments\n"
            f"- Reviewing the available information before acting\n"
            f"- If the task is complete, provide your final response\n"
        )
        return SystemMessage(
            content=content,
            id=f"{LOOP_BREAKER_REPAIR_PREFIX}{uuid.uuid4()}",
        )


async def _maybe_repair_loop(
    messages: list[BaseMessage],
    full_messages: list[BaseMessage],
    instance_id: str,
    instance_short: str,
    config: dict | None,
    graph_ref: list | None,
    injected_msg: BaseMessage | None,
    system_prompt: str,
    llm_config: dict | None,
    loop_breaker_slot: LoopBreakerSlot | None,
    loop_repairer: LoopRepairer | None,
    loop_breaker_config: LoopBreakerConfig,
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """Run the general hallucination loop detection+repair block.

    Extracted from :func:`create_agent_node` to keep the LLM-call site readable.
    The helper owns the entire ``loop_breaker_slot`` / ``loop_repairer``
    pipeline: scan -> repair-cap check -> graph_ref guard -> repair call ->
    C3 re-append. The agent node consumes only the returned
    ``(messages, full_messages)`` pair.

    The function is intentionally pure-except-for-side-effects: it returns the
    ORIGINAL ``messages`` / ``full_messages`` pair whenever the loop breaker
    is disabled, no loop is detected, the repair is skipped (max repairs or
    missing ``graph_ref``), or the repair itself fails. ``full_messages`` is
    ONLY rebuilt when a repair succeeds — mirroring the original inline
    behavior. ``graph_ref=None`` disables the repair path (Fix 2).

    ``full_messages`` MUST be the same list the caller intends to send to
    the LLM at the call site — typically already including
    ``injected_msg`` (C2) — so that the no-repair paths preserve it
    unchanged. The helper only knows how to rebuild ``full_messages`` from
    a clean ``[SystemMessage(system_prompt), *messages]`` skeleton; it does
    NOT know whether the caller appended extra items, so the caller must
    pass the post-append list and trust that the helper returns it
    verbatim on the no-repair paths.

    Args:
        messages: Current conversation messages (oldest-first).
        full_messages: The ``messages`` list the caller intends to feed to
            the LLM (already includes ``injected_msg`` if any). Returned
            unchanged on no-repair paths; rebuilt on success.
        instance_id: Graph thread id — used for slot lookups.
        instance_short: Short id for log readability.
        config: LangGraph thread config (used to build ``RepairContext``).
        graph_ref: Late-bound list ``[compiled_graph_or_None]``. ``None`` or
            ``[None]`` disables the repair path.
        injected_msg: Optional ``HumanMessage`` that was consumed from the
            injection slot this turn; re-appended after a successful repair
            (C3) when the repairer forgot it.
        system_prompt: Session system prompt — prepended to ``full_messages``.
        llm_config: Session LLM config — passed to ``RepairContext``.
        loop_breaker_slot: Slot handle; ``None`` disables the block.
        loop_repairer: Repair helper; ``None`` disables the block.
        loop_breaker_config: ``LoopBreakerConfig`` — supplies ``enabled``,
            ``threshold``, ``max_repairs``, ``excluded_tools``, and
            ``summarization_timeout_seconds``.

    Returns:
        Tuple of ``(messages, full_messages)``. When the loop breaker is
        no-op or the repair fails, the ORIGINAL pair is returned unchanged.
        When the repair succeeds, ``messages`` is the post-repair list
        (with ``injected_msg`` re-appended when missing) and ``full_messages``
        is the freshly-rebuilt ``[SystemMessage(system_prompt), *messages]``.
    """
    if not (
        loop_breaker_slot is not None
        and loop_repairer is not None
        and loop_breaker_config.enabled
    ):
        return messages, full_messages

    try:
        detection = LoopDetector.scan(
            messages=messages,
            threshold=loop_breaker_config.threshold,
            excluded_tools=loop_breaker_config.excluded_tools,
        )
    except Exception as det_err:  # noqa: BLE001
        # Defensive: a detector crash MUST NOT freeze the agent.
        # Log and continue with the original messages.
        logger.warning(
            f"[LOOP BREAKER] scan failed for {instance_short}: "
            f"{type(det_err).__name__}: {det_err}"
        )
        detection = None

    if detection is None:
        # No loop detected this turn. If a prior repair had been recorded,
        # the agent has made progress since, so reset the counter so the
        # next genuine loop starts from ``count=1`` instead of inheriting
        # stale state.
        if loop_breaker_slot.get_repair_count(instance_id) > 0:
            loop_breaker_slot.clear(instance_id)
        return messages, full_messages

    repair_count = loop_breaker_slot.get_repair_count(instance_id)
    if repair_count >= loop_breaker_config.max_repairs:
        logger.warning(
            f"[LOOP BREAKER] Instance {instance_short}: max "
            f"repairs ({loop_breaker_config.max_repairs}) reached, "
            f"forcing continuation with original messages"
        )
        return messages, full_messages

    logger.warning(
        f"[LOOP BREAKER] Instance {instance_short}: detected "
        f"{detection.repetition_count}x repeated "
        f"'{detection.tool_name}' calls. Triggering repair "
        f"(attempt {repair_count + 1}/{loop_breaker_config.max_repairs})"
    )

    # Recoverable guard (Fix 2): if the graph reference has not been bound
    # yet (e.g. early-turn graph compilation is still pending, or the agent
    # is running outside ``build_instance_graph``), ``repair()`` cannot
    # run and would land in the ``success=False`` ERROR path below. That
    # outcome is recoverable — the agent continues with the original
    # messages either way and the next turn will retry — so skip the
    # repair call at WARNING and let the agent proceed unblocked.
    if graph_ref is None or graph_ref[0] is None:
        logger.warning(
            f"[LOOP BREAKER] Skipping repair for "
            f"{instance_short}: graph_ref is empty, "
            f"continuing with original messages"
        )
        return messages, full_messages

    try:
        repair_context = RepairContext(
            detection=detection,
            messages=messages,
            thread_config=config or {},
            graph=graph_ref[0],
            llm_config=llm_config or {},
            system_prompt=system_prompt,
            injected_msg=injected_msg,
            summarization_timeout_seconds=(
                loop_breaker_config.summarization_timeout_seconds
            ),
        )
        result = await loop_repairer.repair(repair_context)
    except Exception as rep_err:  # noqa: BLE001
        # Repair's own ``repair`` is already wrapped in try/except — a
        # raise here means something escaped that guard (e.g. graph_ref
        # is a bad type). Log + fall through to original messages.
        logger.error(
            f"[LOOP BREAKER] repair raised unexpectedly for "
            f"{instance_short}: {type(rep_err).__name__}: "
            f"{rep_err}"
        )
        result = RepairResult(
            success=False,
            repaired_messages=list(messages),
            summary="",
            repair_message_id="",
            error=str(rep_err),
        )

    if not result.success:
        logger.error(
            f"[LOOP BREAKER] Repair failed: {result.error}, "
            f"continuing with original messages"
        )
        return messages, full_messages

    loop_breaker_slot.record_repair(instance_id, result.summary)
    messages = result.repaired_messages
    # C3 defensive re-append: the injection lives only in the local
    # closure (the real ``LoopRepairer`` already re-appends it on its own,
    # but a mock repairer — or a future repairer that forgets — could
    # drop it). If the repaired tail does NOT end with the injected
    # message, re-append it so the LLM retry still receives the user's
    # intent. The id-match guard prevents double-appending when a
    # well-behaved repairer already preserved the injection.
    if injected_msg is not None and (
        not messages or messages[-1].id != injected_msg.id
    ):
        messages = list(messages) + [injected_msg]
    full_messages = [SystemMessage(content=system_prompt)] + list(messages)
    logger.info(
        f"[LOOP BREAKER] Repair complete, re-invoking "
        f"LLM with {len(full_messages)} messages "
        f"(repair msg: "
        f"{result.repair_message_id[:16] if result.repair_message_id else '<no-id>'}...)"
    )
    return messages, full_messages


class ThinkingChatOpenAI(ChatOpenAI):
    """Custom ChatOpenAI that captures reasoning_content from OpenAI-compatible APIs.

    Note: This class does NOT make duplicate requests. The thinking extraction
    is done from the response metadata if available, without additional API calls.
    """

    # Class-level config: model name patterns (case-insensitive substring
    # match) for which reasoning_content MUST be echoed back in multi-turn
    # assistant messages.
    #
    # Why this is configurable:
    #   - DeepSeek thinking mode requires reasoning_content in the assistant
    #     history whenever the prior turn included a tool call, or the model
    #     loses its chain-of-thought context. See:
    #     https://api-docs.deepseek.com/guides/thinking_mode
    #   - Other providers (e.g. raw OpenAI) reject unknown fields like
    #     reasoning_content, so we must NOT echo for those.
    #
    # The daemon sets this from LLMConfig.reasoning_echo_models at startup
    # (see daemon/__main__.py and daemon/manager.py). Default keeps DeepSeek
    # behavior working out of the box.
    reasoning_echo_models: ClassVar[list[str]] = ["deepseek"]

    def _should_echo_reasoning(self) -> bool:
        """Return True if the current model requires reasoning_content echo.

        Substring match (case-insensitive) against ``reasoning_echo_models``.
        """
        model = (self.model_name or "").lower()
        if not model:
            return False
        return any(pattern.lower() in model for pattern in self.reasoning_echo_models)

    def _create_chat_result(
        self,
        response: Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        """Override to extract reasoning_content from the raw OpenAI response.

        LangChain's _convert_dict_to_message() does NOT extract the
        ``reasoning_content`` (or ``reasoning``) field that GLM/DeepSeek-style
        extended-thinking responses include at the top level of the assistant
        message dict. Without this override, the non-streaming path silently
        drops the model's thinking, and the web UI cannot render it.
        """
        result = super()._create_chat_result(response, generation_info)

        try:
            response_dict = (
                response if isinstance(response, dict) else response.model_dump()
            )
            choices = response_dict.get("choices") or []
            for i, res in enumerate(choices):
                if i >= len(result.generations):
                    break
                msg_dict = res.get("message") or {}
                reasoning = msg_dict.get("reasoning_content")
                if reasoning is None:
                    reasoning = msg_dict.get("reasoning")
                if reasoning is None:
                    continue
                gen_message = result.generations[i].message
                if not hasattr(gen_message, "additional_kwargs"):
                    continue
                # Store guard: only set if not already present (avoid clobbering
                # streaming path that may have already populated it).
                if gen_message.additional_kwargs.get("reasoning_content") is None:
                    gen_message.additional_kwargs["reasoning_content"] = reasoning
                    logger.debug(
                        f"[LLM] Extracted reasoning_content from raw response: "
                        f"{str(reasoning)[:100]}..."
                    )
        except Exception as e:
            logger.debug(f"[LLM] Could not extract reasoning_content in _create_chat_result: {e}")

        return result

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Override to capture reasoning_content from response metadata.

        This is a secondary safety net for the non-streaming path. The primary
        extraction now happens in _create_chat_result() which has access to the
        raw response message dict (where reasoning_content lives for GLM/DeepSeek
        responses). This method keeps the legacy fallback chain for any case
        where reasoning_content was already promoted to additional_kwargs or
        response_metadata by an upstream parser.
        """
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

        try:
            if result.generations:
                gen_message = result.generations[0].message
                if hasattr(gen_message, 'additional_kwargs') and gen_message.additional_kwargs.get('reasoning_content') is not None:
                    # Already populated by _create_chat_result override.
                    return result
                if hasattr(gen_message, 'additional_kwargs'):
                    reasoning = gen_message.additional_kwargs.get('reasoning')
                    if reasoning is not None:
                        gen_message.additional_kwargs['reasoning_content'] = reasoning
                if hasattr(gen_message, 'response_metadata'):
                    meta = gen_message.response_metadata or {}
                    reasoning = meta.get('reasoning_content') or meta.get('reasoning')
                    if reasoning is not None and hasattr(gen_message, 'additional_kwargs') \
                            and gen_message.additional_kwargs.get('reasoning_content') is None:
                        gen_message.additional_kwargs['reasoning_content'] = reasoning
                        logger.debug(f"[LLM] Extracted reasoning from metadata: {str(reasoning)[:100]}...")

        except Exception as e:
            logger.debug(f"[LLM] Could not extract reasoning_content: {e}")

        return result

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Override to preserve reasoning_content in assistant message dicts.

        Only injects ``reasoning_content`` for models listed in
        ``reasoning_echo_models`` (default: ``["deepseek"]``).

        Why this is gated by model name:
          - DeepSeek thinking mode requires reasoning_content in the assistant
            history whenever the prior turn included a tool call, or the model
            loses its chain-of-thought context. See:
            https://api-docs.deepseek.com/guides/thinking_mode
          - Other providers (e.g. raw OpenAI) reject unknown fields like
            reasoning_content with a 400 error, so we must skip echo for them.
          - Some proxies ignore unknown fields silently, in which case echo is
            harmless but wastes a few hundred bytes of payload per turn.

        The parent class's ``_convert_message_to_dict()`` strips
        ``reasoning_content`` from additional_kwargs, so we re-inject it after
        the parent has built the payload.
        """
        # Fast path: skip the entire message-matching machinery for models that
        # don't require reasoning echo. This keeps the hot path identical to
        # stock ChatOpenAI for GPT-4o, GLM, Claude, etc.
        if not self._should_echo_reasoning():
            return super()._get_request_payload(input_, stop=stop, **kwargs)

        # Extract original messages once BEFORE calling super() to avoid double conversion.
        # super()._get_request_payload() internally calls _convert_input().to_messages(),
        # so we extract messages here first and use them for matching.
        try:
            original_messages = self._convert_input(input_).to_messages()
        except Exception as e:
            logger.debug(f"[LLM] Could not get original messages for reasoning_content injection: {e}")
            return super()._get_request_payload(input_, stop=stop, **kwargs)

        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        payload_messages = payload.get("messages", [])

        # Build a mapping of assistant message indices to original AIMessages.
        # Index-based pairing invariant:
        # - The N-th assistant payload dict corresponds to the N-th original AIMessage.
        # - This relies on _convert_message_to_dict preserving message order (it does).
        # - We filter to assistant-only messages for matching since that's all we need to patch.
        assistant_idx = 0
        original_assistants = [m for m in original_messages if isinstance(m, AIMessage)]

        for msg in payload_messages:
            if msg.get("role") == "assistant":
                if assistant_idx < len(original_assistants):
                    original = original_assistants[assistant_idx]
                    reasoning = original.additional_kwargs.get('reasoning_content')
                    if reasoning is not None:
                        msg["reasoning_content"] = reasoning
                        logger.debug(f"[LLM] Injected reasoning_content for assistant message {assistant_idx}")
                    assistant_idx += 1

        return payload

    def _convert_delta_to_message_chunk(
        self, _dict: Mapping[str, Any], default_class: type[BaseMessageChunk]
    ) -> BaseMessageChunk:
        """Override to extract reasoning_content from delta chunks (e.g., GLM extended thinking).

        This is called during streaming via _stream()/_astream() when we override
        _convert_chunk_to_generation_chunk to call self._convert_delta_to_message_chunk
        instead of the module-level function.
        """
        # Extract reasoning_content before parent processes the delta
        reasoning_content = _dict.get("reasoning_content")
        if reasoning_content is None:
            reasoning_content = _dict.get("reasoning")

        # Call module-level function (ChatOpenAI doesn't override it)
        result = _base_convert_delta_to_message_chunk(_dict, default_class)

        # If we found reasoning_content and the result is an AIMessageChunk, store it
        if reasoning_content is not None and isinstance(result, AIMessageChunk):
            result.additional_kwargs["reasoning_content"] = reasoning_content
            logger.debug(f"[LLM] Stream extracted reasoning_content: {str(reasoning_content)[:50]}...")

        return result

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        """Override to route _convert_delta_to_message_chunk through self.

        The parent implementation calls _convert_delta_to_message_chunk as a plain
        module-level function (bypassing our override). We fix that by calling it
        as self._convert_delta_to_message_chunk so our thinking extraction runs.
        """
        # --- Begin identical copy of BaseChatOpenAI._convert_chunk_to_generation_chunk ---
        # (only changed: _convert_delta_to_message_chunk(...) -> self._convert_delta_to_message_chunk(...))
        from langchain_openai.chat_models.base import (
            _create_usage_metadata,
        )

        if chunk.get("type") == "content.delta":  # From beta.chat.completions.stream
            return None
        token_usage = chunk.get("usage")
        choices = (
            chunk.get("choices", [])
            or chunk.get("chunk", {}).get("choices", [])
        )

        usage_metadata: UsageMetadata | None = (
            _create_usage_metadata(token_usage, chunk.get("service_tier"))
            if token_usage
            else None
        )
        if len(choices) == 0:
            generation_chunk = ChatGenerationChunk(
                message=default_chunk_class(content="", usage_metadata=usage_metadata),
                generation_info=base_generation_info,
            )
            if self.output_version == "v1":
                generation_chunk.message.content = []
                generation_chunk.message.response_metadata["output_version"] = "v1"
            return generation_chunk

        choice = choices[0]
        if choice["delta"] is None:
            return None

        # KEY FIX: call through self so our _convert_delta_to_message_chunk override is used
        message_chunk = self._convert_delta_to_message_chunk(
            choice["delta"], default_chunk_class
        )
        # --- End identical copy ---

        generation_info = {**base_generation_info} if base_generation_info else {}

        if finish_reason := choice.get("finish_reason"):
            generation_info["finish_reason"] = finish_reason
            if model_name := chunk.get("model"):
                generation_info["model_name"] = model_name
            if system_fingerprint := chunk.get("system_fingerprint"):
                generation_info["system_fingerprint"] = system_fingerprint
            if service_tier := chunk.get("service_tier"):
                generation_info["service_tier"] = service_tier

        logprobs = choice.get("logprobs")
        if logprobs:
            generation_info["logprobs"] = logprobs

        if usage_metadata and isinstance(message_chunk, AIMessageChunk):
            message_chunk.usage_metadata = usage_metadata

        message_chunk.response_metadata["model_provider"] = "openai"
        return ChatGenerationChunk(
            message=message_chunk, generation_info=generation_info or None
        )


def clean_llm_config(cfg: dict) -> dict:
    """Strip non-kwarg keys before passing to ThinkingChatOpenAI(**cfg).

    model_vision is used for vision routing decisions but is not a valid
    LangChain/ChatOpenAI parameter and must be removed before LLM construction.
    """
    return {k: v for k, v in cfg.items() if k != "model_vision"}


class SessionState(MessagesState):
    """Extended state schema for agent sessions.
    
    Inherits all message handling from MessagesState (add_messages reducer).
    Adds compaction metadata fields that persist in checkpoints.
    Also tracks user language preference check state.
    """
    # Compaction dedup: ISO timestamp of last successful compaction
    # Stored/retrieved via graph.aupdate_state() and state.values["compacted_at"]
    compacted_at: str | None = None
    # Language preference check state. Persisted in checkpoints so retries
    # survive across resumed graph executions.
    language_check_retry: bool = False
    language_check_count: int = 0


def should_continue(state: MessagesState) -> str:
    """Determine if we should continue or end.
    
    Routes:
    - "tools": LLM returned tool_calls (normal flow)
    - "agent": Ghost promise — LLM text ends with ':' but no tool_call
    - "nudge": Empty response after tool execution — inject prompt to continue
    - END: LLM returned actual content with no tool_calls (done speaking)
    """
    messages = state["messages"]
    last_message = messages[-1]
    
    # Normal case: LLM made tool calls
    if getattr(last_message, 'tool_calls', None):
        return "tools"
    
    # Check if the model produced a "thinking-only" response.
    # Some models (e.g. Claude with extended thinking) emit an AIMessage that
    # carries reasoning_content but no content and no tool_calls — meaning the
    # model intends the next LLM call to produce the final answer. In that
    # case we re-route to "agent" to invoke the LLM again.
    #
    # However, streaming models like GLM/DeepSeek return BOTH reasoning_content
    # AND content in a single response. Re-invoking the LLM in that case would
    # either loop indefinitely or overwrite the correct response with a fresh
    # one that lacks reasoning_content, breaking the web UI's "show thinking"
    # feature. So we only re-invoke when the response is genuinely
    # thinking-only.
    if hasattr(last_message, 'additional_kwargs'):
        reasoning = last_message.additional_kwargs.get('reasoning_content')
        content = getattr(last_message, 'content', '') or ''
        has_tool_calls = bool(getattr(last_message, 'tool_calls', None))
        if reasoning and not content and not has_tool_calls:
            logger.debug(f"[Graph] Thinking-only response, continuing...")
            return "agent"
    
    # Ghost promise detection: LLM promised action but didn't emit tool_call
    # Common pattern: "Now let me write the document:" (ends with ':')
    content = getattr(last_message, 'content', '') or ''
    if isinstance(content, str) and content.rstrip().endswith(':'):
        logger.warning(f"[Graph] Ghost promise detected, LLM text ends with ':': {content[:100]}...")
        return "agent"  # Re-invoke agent to produce actual tool_call
    
    # Empty response after tool execution: model ACK'd but didn't continue
    # Inject a nudge so the model either continues working or finishes properly
    if _is_empty_content(content) and _has_recent_tool_result(messages):
        logger.info("[Graph] Empty response after tool execution, nudging agent to continue")
        return "nudge"
    
    return END


def _is_empty_content(content) -> bool:
    """Check if content is empty or whitespace-only."""
    if content is None:
        return True
    if isinstance(content, str):
        return content.strip() == ""
    return False


def _has_recent_tool_result(messages: list) -> bool:
    """Check if there's a ToolMessage in the recent message history.
    
    Looks back through messages (skipping the last empty AIMessage) to find
    a ToolMessage. Stops at the first HumanMessage to avoid false positives
    from tool results in earlier turns.
    """
    # Skip the last message (the empty AI response we're deciding on)
    for msg in reversed(messages[:-1]):
        msg_type = getattr(msg, 'type', None)
        if msg_type == 'tool':
            return True
        # Stop searching at human message boundary
        if msg_type == 'human':
            break
    return False


# Message injected when LLM returns empty after tool execution
NUDGE_MESSAGE = "Continue with your task, or provide your final response if you are finished."


def nudge_node(state):
    """Inject a nudge message to prompt the agent to continue or finish."""
    return {'messages': [HumanMessage(content=NUDGE_MESSAGE)]}


# ---------------------------------------------------------------------------
# User language preference: language check node + routing helpers
# ---------------------------------------------------------------------------
#
# These functions implement Phase 2 of the user language preference feature.
# They intercept the would-be END decision in should_continue() and route the
# final AI response through a detection step that may re-inject a reminder
# message if the response is in the wrong language.
#
# The original should_continue() is NOT modified. Instead, create_should_continue()
# returns a wrapper that translates END -> "end_candidate" so the graph routes
# to the language_check node when language_check_enabled=True.
LANGUAGE_REMINDER_TEMPLATE = (
    "You are responding in the wrong language. "
    "The user's preferred language is {language}. "
    "Please respond again in {language}."
)

LANGUAGE_CHECK_MAX_RETRIES = 2


def create_language_check_node(user_language: str):
    """Create the language check node function.

    The returned node examines the last AI message, runs language detection
    against the user's preferred language, and either:
    - Returns a HumanMessage reminder injected into the conversation, OR
    - Allows the conversation to END.

    Counter logic (S5 fix): language_check_count resets whenever a new
    HumanMessage without the language_check_reminder marker is observed,
    so each user turn starts with a fresh retry budget.

    Skip logic (C4 fix): if a `language_skip_check` tool was invoked since
    the last user message, detection is bypassed entirely for this turn.
    """

    async def language_check_node(state):
        messages = state["messages"]
        last_message = messages[-1]

        # Only check AIMessage content (not tool calls). If the last message
        # has tool_calls, it's a tool execution in progress — nothing to
        # validate yet.
        if not hasattr(last_message, 'content') or getattr(last_message, 'tool_calls', None):
            return {"language_check_retry": False, "language_check_count": 0}

        count = state.get("language_check_count", 0)

        # Combined scan: counter reset (S5) + skip detection (C4).
        # Both original loops scan the same range and break on the first
        # HumanMessage, so they can be safely merged into a single pass.
        # On hitting a skip tool we DO NOT break — we keep scanning so the
        # HumanMessage boundary still resets `count` consistently.
        skip = False
        for msg in reversed(messages[:-1]):
            msg_type = getattr(msg, 'type', None)
            if msg_type == 'human':
                # A reminder-injected HumanMessage is marked via
                # additional_kwargs so we don't reset on our own re-injections.
                if not getattr(msg, 'additional_kwargs', {}).get('language_check_reminder', False):
                    count = 0  # New user message, reset counter
                break  # Stop scanning past the last HumanMessage
            if msg_type == 'tool':
                tool_name = getattr(msg, 'name', None)
                if tool_name == 'language_skip_check':
                    skip = True
                    # Don't break — continue scanning in case there's a
                    # HumanMessage before this we still need to account for

        # Max retries — prevent infinite loop.
        if count >= LANGUAGE_CHECK_MAX_RETRIES:
            logger.warning(
                f"[LanguageCheck] Max retries ({LANGUAGE_CHECK_MAX_RETRIES}) reached, allowing response"
            )
            return {"language_check_retry": False, "language_check_count": 0}

        # Skip if language_skip_check tool was called.
        if skip:
            return {"language_check_retry": False, "language_check_count": 0}

        # Get content.
        content = getattr(last_message, 'content', '') or ''

        # W4 FIX: Wrap detection in try/except — never crash the graph.
        try:
            if detect_wrong_language(content, user_language):
                reminder = HumanMessage(
                    content=LANGUAGE_REMINDER_TEMPLATE.format(language=user_language),
                    additional_kwargs={"language_check_reminder": True},
                )
                logger.info(
                    f"[LanguageCheck] Wrong language detected "
                    f"(attempt {count + 1}/{LANGUAGE_CHECK_MAX_RETRIES}), injecting reminder"
                )
                return {
                    "messages": [reminder],
                    "language_check_retry": True,
                    "language_check_count": count + 1,
                }
        except (ValueError, TypeError, AttributeError, re.error) as e:
            logger.warning(f"[LanguageCheck] Detection error, allowing response: {e}")
            return {"language_check_retry": False, "language_check_count": 0}

        # Correct language — reset counter, no retry.
        return {"language_check_retry": False, "language_check_count": 0}

    return language_check_node


def should_end_language_check(state) -> str:
    """Determine if language check should retry or end.

    Returns "retry" if the language_check_node flagged a retry (wrong
    language detected); otherwise returns END so the conversation finishes.
    """
    if state.get("language_check_retry", False):
        return "retry"
    return END


def create_should_continue(language_check_enabled: bool):
    """Create a should_continue wrapper that routes to language_check when enabled.

    When language_check_enabled=True:
        - Routes final responses (would-be END) to "end_candidate" -> language_check
        - All other branches (tools, agent, nudge) unchanged.

    When language_check_enabled=False:
        - Returns the original should_continue() unchanged (END -> END).
        - No language_check node exists in the graph in this case.
    """
    if not language_check_enabled:
        return should_continue  # Use original function directly

    def should_continue_with_language_check(state: MessagesState) -> str:
        result = should_continue(state)
        if result == END:
            return "end_candidate"
        return result

    return should_continue_with_language_check


def create_agent_node(
    llm_with_tools,
    system_prompt: str,
    compactor=None,
    graph_ref=None,
    config=None,
    llm_config=None,
    retry_config=None,
    llm_standard=None,
    injection_slot: InjectionSlot | None = None,
    live_hub: Any = None,
    throttle_slot: ToolThrottleSlot | None = None,
    loop_breaker_slot: LoopBreakerSlot | None = None,
    loop_repairer: LoopRepairer | None = None,
    loop_breaker_config: "LoopBreakerConfig | None" = None,
):
    """Create the agent node function with optional reactive compaction.

    Args:
        llm_with_tools: LLM already bound with tools (vision model if configured).
        system_prompt: System prompt to prepend to messages.
        compactor: Optional ContextCompactor for reactive compaction.
        graph_ref: Optional list for late-bound graph reference.
        config: Optional config for compaction.
        llm_config: Optional LLM config for compaction context.
        retry_config: Optional retry configuration for logging.
        llm_standard: Optional standard LLM bound with tools (for non-vision calls).
            When provided, vision model is used when images are present.
        injection_slot: Optional :class:`InjectionSlot` handle (C1) that
            exposes ``get(instance_id) → dict|None`` and
            ``clear(instance_id) → dict|None``. When supplied, the agent
            node peeks + clears a pending user message on every LLM
            invocation and threads the resulting ``HumanMessage`` into
            the conversation. ``None`` disables injection entirely
            (backward compatible).
        live_hub: Optional ``LiveEventHub`` reference threaded for the
            Phase 2 SSE emission path (``stream_message(... event_type=
            "injection_consumed" ...)``). In Phase 1 the handle is wired
            but only a log-only stub runs so the structural call site is
            exercised; ``None`` skips the stub entirely.
        throttle_slot: Optional :class:`ToolThrottleSlot` handle that
            throttles consecutive ``get_instance_info`` tool calls by
            injecting escalating ``asyncio.sleep`` delays before the
            LLM call. The slot's ``bump``/``reset``/``get_count`` are
            invoked on the last message in the state — non-gii
            messages reset the consecutive-call counter. ``None``
            disables throttling (backward compatible).
        loop_breaker_slot: Optional :class:`LoopBreakerSlot` handle that
            exposes per-instance loop-breaker state. ``None`` disables
            loop detection / repair (backward compatible). Phase 3
            wiring — runs AFTER the GII throttle block and BEFORE the
            LLM call. When detection finds a repeating tool pattern
            AND repair_count < max_repairs, the repairer is invoked
            and the LLM is re-invoked with the repaired messages.
        loop_repairer: Optional :class:`LoopRepairer` (Phase 2) used
            to summarize and rewrite messages when detection fires.
            ``None`` disables the repair path even if a slot is
            provided. Backward compatible.
        loop_breaker_config: Optional :class:`LoopBreakerConfig` —
            supplies ``enabled``, ``threshold``, ``max_repairs``,
            ``excluded_tools``, ``summarization_timeout_seconds``.
            ``None`` (or omitted) implies a default-enabled config
            (``LoopBreakerConfig()``).
    """

    # Resolve once at factory time so the closure does not rebuild a
    # default config on every LLM call (and so tests can pass their
    # own frozen config object). ``LoopBreakerConfig`` is imported at
    # module top-level (no cycle — ``daemon.config`` does not import
    # ``daemon.graph``).
    if loop_breaker_config is None:
        _lb_config = LoopBreakerConfig()
    else:
        _lb_config = loop_breaker_config

    async def agent_node(state, config=None):
        messages = state['messages']
        full_messages = [SystemMessage(content=system_prompt)] + list(messages)
        transient = retry_config.get('transient_attempts', 8) if retry_config else 8
        timeout = retry_config.get('timeout_attempts', 3) if retry_config else 3
        instance_id = (config or {}).get('configurable', {}).get('thread_id', 'unknown')
        instance_short = instance_id.split('-')[0] if '-' in instance_id else instance_id

        # ── Phase 1 / C2: pull + clear the pending user-injection ─────────
        # Pull happens BEFORE the LLM call so the injected HumanMessage is
        # part of the request. Clear happens BEFORE the LLM call too —
        # not after — so a transient LLM failure cannot leave the slot
        # stale: either the LLM sees the injection, or the slot survives
        # to be retried on the next agent turn.
        #
        # Reference is captured in ``injected_msg`` so the reactive
        # compaction handler (C3) can re-append it after a checkpoint
        # re-read, and so the return value (C2) persists BOTH messages.
        injected_msg: HumanMessage | None = None
        if injection_slot is not None:
            pending = injection_slot.get(instance_id)
            if pending is not None:
                content = pending.get("content", "")
                injected_msg = HumanMessage(
                    content=content,
                    additional_kwargs={"injected_message": True},
                )
                full_messages.append(injected_msg)
                cleared = injection_slot.clear(instance_id)
                # Defensive: if the slot was empty on clear (extremely
                # unlikely race — another consumer popped it between our
                # get and clear), log and continue. ``injected_msg`` is
                # already in full_messages and will be returned.
                if cleared is None:
                    logger.warning(
                        f"[Injection] Slot disappeared between get+clear "
                        f"for instance {instance_short} — continuing"
                    )
                logger.info(
                    f"[Injection] Pulled pending message for "
                    f"{instance_short} (len={len(content)})"
                )

                # Phase 2 / Task 7 (W5): finalize the SSE emission at the
                # consumption point. The Phase 1 placeholder exercised the
                # call site so the structural wiring is already proven; this
                # is the real ``stream_message(..., event_type=...)`` call.
                # W5 contract: NO new method on ``LiveEventHub`` — we reuse
                # the existing ``stream_message`` with a custom ``event_type``
                # so the frontend (Phase 3) sees ``event_type="injection_consumed"``
                # under the same payload shape the API uses.
                #
                # The clear returned the entry that was just consumed; we
                # re-emit content + timestamp so the SSE listener sees the
                # exact text the LLM was about to see.
                #
                # BUG FIX (injection-sse-echo-fix): the normal ``send_message``
                # path in ``instance_messaging.py`` pre-emits a ``user_message``
                # SSE event before the LLM runs so the frontend can echo the
                # user's text. The injection path only emitted
                # ``injection_consumed`` and was missing the ``user_message``
                # echo, so injected messages rendered without a user-bubble
                # update on the UI. We mirror the normal-path shape here:
                # serialize a HumanMessage carrying the injected ``content``,
                # stamp ``instance_id``, and emit ``user_message`` with
                # ``checkpoint_id="user"`` so the frontend treats it the same
                # way as a regular user turn.
                if live_hub is not None:
                    try:
                        injected_user_msg = HumanMessage(content=content)
                        user_serialized = serialize_message(injected_user_msg)
                        user_serialized["instance_id"] = instance_id
                        await live_hub.stream_message(
                            instance_id=instance_id,
                            message=user_serialized,
                            event_type="user_message",
                            checkpoint_id="user",
                        )
                    except Exception as e:  # pragma: no cover - defensive
                        # LLM call must not be blocked by an SSE outage —
                        # log and continue. The injection is already
                        # consumed locally (checkpoint persist + injected_msg
                        # in full_messages); the SSE event is best-effort.
                        logger.warning(
                            f"[Injection] user_message SSE emit failed for "
                            f"{instance_short}: {type(e).__name__}: {e}"
                        )

                if live_hub is not None:
                    try:
                        await live_hub.stream_message(
                            instance_id,
                            message={
                                "instance_id": instance_id,
                                "event_type": "injection_consumed",
                                "content": cleared.get("content") if cleared else content,
                                "timestamp": cleared.get("timestamp") if cleared else None,
                            },
                            event_type="injection_consumed",
                        )
                    except Exception as e:  # pragma: no cover - defensive
                        # LLM call must not be blocked by an SSE outage —
                        # log and continue. The injection is already
                        # consumed locally (checkpoint persist + injected_msg
                        # in full_messages); the SSE event is best-effort.
                        logger.warning(
                            f"[Injection] injection_consumed SSE emit "
                            f"failed for {instance_short}: "
                            f"{type(e).__name__}: {e}"
                        )

        # Check if vision model is being used (images present in user message)
        model_vision = llm_config.get("model_vision") if llm_config else None
        has_images = False
        for msg in messages:
            content = getattr(msg, 'content', None)
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "image_url":
                        has_images = True
                        break
            if has_images:
                break

        # Select the appropriate LLM:
        # - Images present: use vision model (llm_with_tools which has vision model)
        # - No images: use standard model if available
        use_vision_model = has_images and model_vision and llm_standard is not None
        current_llm = llm_with_tools if use_vision_model else (llm_standard or llm_with_tools)

        model_name = model_vision if use_vision_model else llm_config.get("model", "unknown") if llm_config else "unknown"
        vision_log = f", vision={model_vision}" if model_vision and has_images else ""
        call_type = "VISION" if use_vision_model else "STANDARD"
        logger.info(f'[LLM][{instance_short}] Invoking LLM ({call_type}) with {len(full_messages)} messages (model={model_name}, transient_attempts={transient}, timeout_attempts={timeout}{vision_log})')

        # ── get_instance_info throttling ─────────────────────────────────
        # Counts consecutive gii tool messages so we can inject escalating
        # delays and break the hallucination polling loop. The counter
        # resets on any non-gii message — this branch covers every other
        # message type (HumanMessage, AIMessage without tool_calls, etc.).
        if throttle_slot is not None:
            last_msg = messages[-1] if messages else None
            if isinstance(last_msg, ToolMessage) and last_msg.name == GII_TOOL_NAME:
                count = throttle_slot.bump(instance_id)
                if count >= 3:
                    delay = GII_DELAY_MAP.get(count, GII_MAX_DELAY)
                    logger.info(
                        f"[THROTTLE] Instance {instance_short}: "
                        f"get_instance_info consecutive call #{count}, "
                        f"sleeping {delay}s before next LLM call"
                    )
                    await asyncio.sleep(delay)
            else:
                throttle_slot.reset(instance_id)

        # ── General hallucination loop detection + repair ───────────────
        # Runs AFTER the GII throttle block and BEFORE the LLM call.
        # The two mechanisms are complementary — GII gives the LLM provider
        # a breather via ``asyncio.sleep``; the loop breaker removes the
        # repetitive messages and re-injects a fresh summary so the LLM
        # sees different context on the retry. Both are no-ops when their
        # respective slots are ``None`` (backward-compatible default).
        #
        # The full detection+repair pipeline lives in ``_maybe_repair_loop``
        # so the LLM-call site here stays readable. ``_lb_config.enabled`` is
        # the kill switch — a config with ``enabled=False`` disables
        # detection+repair entirely without callers having to thread
        # ``None`` slots.
        messages, full_messages = await _maybe_repair_loop(
            messages,
            full_messages,
            instance_id,
            instance_short,
            config,
            graph_ref,
            injected_msg,
            system_prompt,
            llm_config,
            loop_breaker_slot,
            loop_repairer,
            _lb_config,
        )

        try:
            # Use run_in_executor to avoid blocking the event loop.
            # This allows SSE streaming to continue while LLM processes.
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: current_llm.invoke(full_messages)
            )
        except ContextLengthExceededError:
            if compactor is None or graph_ref is None or graph_ref[0] is None:
                logger.warning('[LLM] Context length exceeded (no compactor available)')
                raise

            logger.info(f'[LLM] Context length exceeded, attempting reactive compaction for {len(messages)} messages')

            graph = graph_ref[0]
            thread_config = config or {}

            current_state = await graph.aget_state(thread_config)
            current_messages = current_state.values.get('messages', [])
            compacted_at_val = current_state.values.get('compacted_at')

            from .compaction import CompactionContext
            ctx = CompactionContext(
                messages=current_messages,
                system_prompt_tokens=0,
                model_name=llm_config.get('model', '') if llm_config else '',
                config=compactor.config,
                llm_config=compactor.llm_config,
                last_compacted_at=compacted_at_val,
            )

            result = await compactor.compact_state(ctx)
            if result is None or result.replacement_messages is None:
                logger.warning('Reactive compaction returned no result, re-raising')
                raise

            await graph.aupdate_state(thread_config, {'messages': result.replacement_messages}, as_node='agent')
            if result.compacted_at:
                await graph.aupdate_state(thread_config, {'compacted_at': result.compacted_at}, as_node='agent')

            logger.info(f'[LLM] Reactive compaction complete: {result.messages_before} -> {result.messages_after} messages, {result.tokens_saved} tokens saved ({result.compaction_type})')

            updated_state = await graph.aget_state(thread_config)
            compact_messages = [SystemMessage(content=system_prompt)] + updated_state.values.get('messages', [])

            # C3: Reactive compaction re-append — the injected message
            # lives only in the local ``full_messages`` list above (it
            # has NOT been persisted to the checkpoint via
            # ``add_messages`` yet). ``graph.aget_state`` reads from
            # checkpoint, so without this re-append the LLM retry would
            # lose the user's injected message. We re-append in-place
            # so the retry sees it exactly as the first attempt did.
            if injected_msg is not None:
                compact_messages.append(injected_msg)
                logger.debug(
                    f'[LLM] Reactive compaction: re-appended injected '
                    f'message for {instance_short} '
                    f'(len={len(injected_msg.content or "")})'
                )

            # Use run_in_executor to avoid blocking the event loop after compaction
            # Continue with the same LLM that was being used (may be vision or standard)
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: current_llm.invoke(compact_messages)
            )
        except (openai.APITimeoutError, openai.APIConnectionError, ConnectionResetError,
                BrokenPipeError, ConnectionAbortedError, TransientAPIError, LLMResponseValidationError) as e:
            transient = retry_config.get('transient_attempts', 'N/A') if retry_config else 'N/A'
            timeout = retry_config.get('timeout_attempts', 'N/A') if retry_config else 'N/A'
            category = 'timeout' if isinstance(e, TIMEOUT_EXCEPTIONS) else 'transient' if isinstance(e, TRANSIENT_EXCEPTIONS) else 'non-retryable'
            logger.error(f"[LLM] All retries exhausted ({category}, transient_attempts={transient}, timeout_attempts={timeout}): {type(e).__name__}: {_truncate_error(e)}")
            raise
        except Exception as e:
            logger.error(f"[LLM] Unexpected error after retries: {type(e).__name__}: {_truncate_error(e)}")
            raise

        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_names = [tc.get('name', getattr(tc, 'name', '?')) for tc in response.tool_calls]
            # Get first tool's arguments for display
            first_tc = response.tool_calls[0]
            tc_args = first_tc.get('args', getattr(first_tc, 'args', {}))
            tc_args_str = str(tc_args)[:80] if tc_args else ''
            logger.info(f'[LLM] Tool call: {tool_names[0]} — {tc_args_str}..., tools: {tool_names}')
        elif response.content:
            logger.info(f'[LLM] Response: {response.content[:80]}...')
        else:
            logger.info('[LLM] Response: empty')

        # C2: Persist BOTH the injected HumanMessage and the LLM response
        # so the ``add_messages`` reducer writes them to the checkpoint
        # together. When no injection was consumed, fall back to the
        # existing single-message return so the surface is identical to
        # the pre-Phase-1 behavior.
        if injected_msg is not None:
            return {'messages': [injected_msg, response]}
        return {'messages': [response]}

    return agent_node


def build_instance_llms(
    llm_config_with_headers: dict,
    model_standard: str,
    model_vision: str | None,
    tools: list,
    retry_config: dict | None = None,
):
    """Create LLM instances for agent execution.

    This function handles the logic for creating:
    - llm_with_tools: Primary LLM bound to tools (vision if configured, else standard)
    - llm_standard: Standard LLM (always bound to tools for tool-calling)

    Returns:
        Tuple of (llm_with_tools, llm_standard)
    """
    llm_standard = None
    llm_with_tools = None

    if model_vision:
        logger.info(f"[Graph] Vision model configured: {model_vision}")
        # Filter model_vision from config to avoid passing it to the API
        vision_config = clean_llm_config(llm_config_with_headers)
        vision_config["model"] = model_vision
        llm_with_tools = ThinkingChatOpenAI(**vision_config).bind_tools(tools)
    else:
        logger.info("[Graph] No vision model configured, using standard model for all calls")

    # Create standard LLM (always needed, even if vision is configured)
    # Filter model_vision from config to avoid noisy LangChain warnings
    standard_config = clean_llm_config(llm_config_with_headers)
    standard_config["model"] = model_standard
    llm_standard = ThinkingChatOpenAI(**standard_config)

    # Always bind tools to llm_standard, regardless of vision configuration
    if llm_with_tools is None:
        llm_with_tools = llm_standard.bind_tools(tools)
    llm_standard = llm_standard.bind_tools(tools)

    # Wrap with error classification and retry if config provided
    if retry_config:
        # CRITICAL: classify errors BEFORE retry so they can be caught
        llm_with_tools = classify_llm_errors(llm_with_tools)
        if llm_standard is not llm_with_tools:
            llm_standard = classify_llm_errors(llm_standard)

        from daemon.llm_error_classifier import _make_llm_retry_strategy

        transient_attempts = retry_config.get("transient_attempts", 8)
        timeout_attempts = retry_config.get("timeout_attempts", 3)

        retry_predicate = _make_llm_retry_strategy(
            transient_max=transient_attempts,
            timeout_max=timeout_attempts,
        )

        # Use max() as hard safety ceiling; the predicate controls per-category limits
        max_attempts = max(transient_attempts, timeout_attempts)

        # Use tenacity directly since LangChain's with_retry() no longer supports
        # custom retry predicates (the 'retry=' parameter was removed)
        retrying = Retrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(),
            retry=retry_predicate,
            reraise=True,
        )

        # Capture the classified LLMs for retry wrapper
        classified_llm = llm_with_tools

        def _run_with_retry(input_value):
            return retrying(classified_llm.invoke, input_value)

        llm_with_tools = RunnableLambda(_run_with_retry)

        # Also wrap standard LLM with Retrying if it's different from llm_with_tools.
        # This handles the dual-LLM architecture case where both vision and standard
        # models need their own retry wrappers.
        if llm_standard is not llm_with_tools:
            classified_standard = llm_standard
            def _run_standard_with_retry(input_value):
                return retrying(classified_standard.invoke, input_value)
            llm_standard = RunnableLambda(_run_standard_with_retry)

        logger.debug(
            f"LLM configured with {transient_attempts} transient retries, "
            f"{timeout_attempts} timeout retries"
        )

    return llm_with_tools, llm_standard


# ============================================================================
# Question tool: conditional post-tools edge + pause node (Phase 1 + C2 fix)
# ============================================================================
# The ``question`` tool sets ``manager._question_pause_requested[instance_id]
# = True`` before returning. The conditional post-tools edge
# (``create_post_tools_router``) reads this flag on every post-tools
# evaluation. When the flag is set, the graph routes to
# ``question_pause_node`` instead of back to ``agent``.
#
# The pause node does NOT call ``pause_instance_cascade`` directly. That
# would self-cancel the currently-running graph task (via
# ``task.cancel()``), which raises ``CancelledError`` at the next ``await``
# inside the cascade — interrupting the cascade's batched DB write and
# leaving the instance in PROCESSING in the DB while in-memory state says
# PAUSED (C2 torn-state bug). Instead, the node sets a deferred-pause
# marker on the manager; the actual cascade runs from the post-graph
# completion path in ``daemon.services.instance_messaging`` AFTER
# ``_graph_tasks`` has been popped, so there is no graph task left to
# self-cancel. The DB transaction completes cleanly.
#
# CRITICAL invariants (F2 from phase1-plan, plus C2):
#   * The conditional-edge flag MUST be cleared in ``finally`` — the post-
#     tools router reads it on every evaluation. Without ``finally``
#     cleanup the flag would stay set forever and the instance would
#     re-pause on the first post-resume tool call, creating a stuck loop.
#   * The node must NOT call ``pause_instance_cascade`` directly — that
#     causes the C2 self-cancel / torn-state bug.


def create_post_tools_router(manager: Any):
    """Build the conditional post-tools router that handles question pauses.

    Returns a closure suitable for ``graph.add_conditional_edges``. On
    every post-tools evaluation the closure reads
    ``manager.is_question_pause_requested(instance_id)`` and routes to
    ``"question_pause_node"`` when True, otherwise back to ``"agent"``.

    The ``instance_id`` is taken from the LangGraph config's
    ``configurable.thread_id`` (set when the graph is invoked with
    ``{"configurable": {"thread_id": instance_id}}`` — the same pattern
    used elsewhere in the codebase). Falling back to ``None`` when
    config is missing is safe because ``is_question_pause_requested``
    returns ``False`` for unknown ids.

    Args:
        manager: The ``InstanceManager`` reference threaded from
            ``build_instance_graph``. Must expose
            ``is_question_pause_requested(instance_id) -> bool``.

    Returns:
        A callable ``router(state, config) -> str`` returning the
        next-node name (``"agent"`` or ``"question_pause_node"``).
    """
    def post_tools_router(state: Any, config: RunnableConfig | None = None) -> str:
        instance_id: str | None = None
        try:
            if config is not None:
                configurable = (
                    config.get("configurable")
                    if isinstance(config, dict)
                    else getattr(config, "configurable", None)
                )
                if isinstance(configurable, dict):
                    instance_id = configurable.get("thread_id")
        except Exception:
            instance_id = None
        if instance_id and manager.is_question_pause_requested(instance_id):
            return "question_pause_node"
        return "agent"

    return post_tools_router


def create_question_pause_node(manager: Any):
    """Build the ``question_pause_node`` async function with ``manager`` captured.

    This factory mirrors the pattern used by ``create_agent_node`` and
    ``create_language_check_node`` — the closure captures ``manager``
    so the returned coroutine function has the manager reference at
    call time without depending on module-level singletons (which would
    break tests and any multi-graph runtime).

    The returned node marks the instance as paused-after-question:

      1. Reads ``instance_id`` from ``config["configurable"]["thread_id"]``.
      2. Sets a deferred-pause marker via
         ``manager.set_deferred_question_pause(instance_id)`` — does NOT
         call ``pause_instance_cascade`` directly (C2 torn-state fix).
      3. The actual cascade runs from the post-graph completion path in
         ``daemon.services.instance_messaging`` AFTER ``_graph_tasks`` is
         popped, so there is no graph task left to self-cancel. The DB
         write proceeds cleanly and the instance transitions to PAUSED.
      4. The ``finally`` block clears the conditional-edge flag,
         preventing stuck-pause loops on the next resume.

    CRITICAL invariants (F2 from phase1-plan, plus C2):
      * The conditional-edge flag MUST be cleared in ``finally`` — the
        post-tools router reads it on every evaluation. Without ``finally``
        cleanup the flag would stay set forever and the instance would
        re-pause on the first post-resume tool call, creating a stuck
        loop.
      * The node must NOT call ``pause_instance_cascade`` directly.
        Self-cancel would interrupt the cascade's DB write with
        ``CancelledError`` (C2 torn-state bug) and leave the instance
        stuck in PROCESSING while in-memory state says PAUSED.

    Args:
        manager: The ``InstanceManager`` reference threaded from
            ``build_instance_graph``. Must expose
            ``set_deferred_question_pause(instance_id) -> None`` and
            ``clear_question_pause_requested(instance_id) -> None``.

    Returns:
        An async callable suitable for ``graph.add_node("name", ...)``.
    """
    async def question_pause_node(state: Any, config: RunnableConfig | None = None) -> dict:
        instance_id: str | None = None
        try:
            if config is not None:
                configurable = (
                    config.get("configurable")
                    if isinstance(config, dict)
                    else getattr(config, "configurable", None)
                )
                if isinstance(configurable, dict):
                    instance_id = configurable.get("thread_id")
        except Exception:
            instance_id = None

        if instance_id is None:
            # Should never happen in production (the router requires
            # config to set the flag); log + bail so LangGraph sees a
            # normal return and can route to END.
            logger.warning(
                "[question_pause_node] missing instance_id from config — "
                "skipping deferred pause marker"
            )
            return {}

        # C2 fix — Solution A (deferred pause).
        #
        # The node does NOT call ``pause_instance_cascade`` from within the
        # graph task. The cascade pops ``_graph_tasks[instance_id]`` and
        # calls ``task.cancel()`` on the current task, which raises
        # ``CancelledError`` at the next ``await`` — including the DB write
        # at the end of the cascade. The DB transaction never commits,
        # leaving the instance in PROCESSING in the DB while in-memory
        # state says PAUSED (torn state).
        #
        # Instead, we set a deferred-pause marker. The actual cascade runs
        # from the post-graph completion path in
        # ``daemon.services.instance_messaging`` AFTER the graph task has
        # been popped from ``_graph_tasks``. At that point there is no
        # graph task to self-cancel, so the DB write proceeds cleanly.
        try:
            manager.set_deferred_question_pause(instance_id)
        finally:
            # ALWAYS clear the conditional-edge flag (F2). The graph
            # routes to END after this node returns ``{}``, so the flag
            # is only needed for the one post-tools edge evaluation that
            # brought us here. Leaving it set would re-pause the instance
            # on the first post-resume tool call.
            try:
                manager.clear_question_pause_requested(instance_id)
            except Exception as clear_err:
                logger.warning(
                    f"[question_pause_node] failed to clear pause flag "
                    f"in finally for {instance_id[:8]}...: "
                    f"{type(clear_err).__name__}: {clear_err}"
                )

        return {}

    return question_pause_node


def build_instance_graph(
    tools: list,
    checkpointer,
    llm_config: dict,
    system_prompt: str,
    retry_config: dict | None = None,
    compactor=None,
    graph_config=None,
    user_language: str = "Auto",
    language_check_enabled: bool = True,
    injection_slot: InjectionSlot | None = None,
    live_hub: Any = None,
    throttle_slot: ToolThrottleSlot | None = None,
    manager: Any = None,
    loop_breaker_slot: LoopBreakerSlot | None = None,
    loop_repairer: LoopRepairer | None = None,
    loop_breaker_config: "LoopBreakerConfig | None" = None,
):
    """Build and return a compiled instance graph with LLM-level retry.

    When model_vision is configured, we create two LLM instances:
    - llm_with_tools (vision): Used when images are present
    - llm_standard: Used for text-only calls

    When language_check_enabled=True, the graph gains an additional
    `language_check` node that intercepts the would-be END decision from
    should_continue() and validates the final AI response against the
    user's preferred language. If the language is wrong, a reminder
    HumanMessage is injected and the agent re-runs (up to
    LANGUAGE_CHECK_MAX_RETRIES times).

    Args:
        tools: Tool list bound to the agent LLM.
        checkpointer: LangGraph checkpointer for state persistence.
        llm_config: LLM configuration dict (provider, model, etc.).
        system_prompt: System prompt prepended to every agent turn.
        retry_config: Optional retry/backoff configuration.
        compactor: Optional ``ContextCompactor`` (C3) threaded to the
            agent_node for reactive compaction on context overflow.
        graph_config: Optional LangGraph config (``thread_id``, etc.).
        user_language: User-preferred language for the language-check node.
        language_check_enabled: Whether to enable the language-check node.
        injection_slot: Optional :class:`InjectionSlot` handle (Phase 1
            / C1) that lets the agent_node pull a pending user message
            into the conversation before each LLM call. ``None``
            disables injection (backward-compatible default).
        live_hub: Optional ``LiveEventHub`` reference (Phase 1 / C1)
            threaded for Phase 2 SSE emission (placeholder only in
            Phase 1).
        throttle_slot: Optional :class:`ToolThrottleSlot` handle that
            throttles consecutive ``get_instance_info`` tool calls by
            injecting escalating ``asyncio.sleep`` delays before the
            LLM call. ``None`` disables throttling (backward-compatible
            default).
        manager: Optional ``InstanceManager`` reference (Phase 1 /
            question-tool) threaded so the conditional post-tools edge
            (``create_post_tools_router``) can read the
            ``_question_pause_requested`` flag and the
            ``question_pause_node`` can set a deferred-pause marker
            (C2 fix — the actual ``pause_instance_cascade`` runs from
            the post-graph completion path, not from inside the graph
            task). ``None`` is backward-compatible (no question-pause
            behavior; the unconditional ``tools → agent`` edge is used
            instead).
    """
    # Add proxy header to all LLM requests
    llm_config_with_headers = {
        **llm_config,
        "default_headers": {"x-proxy-app": "ensemble"},
    }

    # Check if vision model is configured
    model_vision = llm_config.get("model_vision")
    model_standard = llm_config.get("model")

    # "Auto" means "no preference" — disable the language_check node so the
    # LLM is free to reply in whatever language matches the user's input.
    # Must happen BEFORE the conditional graph wiring below.
    # Lazy import — see top-of-file note about the graph ↔ services cycle.
    from .services.language_utils import is_auto_language
    if is_auto_language(user_language):
        language_check_enabled = False

    # Create LLMs using the helper function
    llm_with_tools, llm_standard = build_instance_llms(
        llm_config_with_headers=llm_config_with_headers,
        model_standard=model_standard,
        model_vision=model_vision,
        tools=tools,
        retry_config=retry_config,
    )

    # Late binding for graph reference
    graph_ref = [None]

    graph = StateGraph(SessionState)

    # Add nodes - pass both vision and standard LLM. Phase 1 / C1 also
    # threads ``injection_slot`` and ``live_hub`` into the agent-node
    # closure so the graph can consume pending user injections without
    # importing InstanceManager (preserves test isolation).
    graph.add_node("agent", create_agent_node(
        llm_with_tools,
        system_prompt,
        compactor=compactor,
        graph_ref=graph_ref,
        config=graph_config,
        llm_config=llm_config_with_headers,
        retry_config=retry_config,
        llm_standard=llm_standard,
        injection_slot=injection_slot,
        live_hub=live_hub,
        throttle_slot=throttle_slot,
        loop_breaker_slot=loop_breaker_slot,
        loop_repairer=loop_repairer,
        loop_breaker_config=loop_breaker_config,
    ))
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    graph.add_node("nudge", nudge_node)
    
    # Add edges
    graph.add_edge(START, "agent")

    # Conditionally add language_check node + build routing.
    # When language_check_enabled=True, the wrapper routes the original
    # END decision to "end_candidate" -> language_check, which then either
    # retries (back to agent) or ends the graph.
    # When language_check_enabled=False, we use the original should_continue
    # unchanged and no language_check node is added to the graph.
    if language_check_enabled:
        graph.add_node("language_check", create_language_check_node(user_language))

        # Closure wrapper: routes END -> "end_candidate"
        routing_fn = create_should_continue(language_check_enabled=True)

        graph.add_conditional_edges("agent", routing_fn, {
            "tools": "tools",          # Normal: LLM made tool calls
            "agent": "agent",          # Ghost promise: retry agent
            "nudge": "nudge",          # Empty after tool: inject prompt
            "end_candidate": "language_check",  # Would-be END: validate language
        })

        # Language check -> retry or END
        graph.add_conditional_edges("language_check", should_end_language_check, {
            "retry": "agent",
            END: END,
        })
    else:
        # Language check disabled: use original should_continue, no language_check node
        graph.add_conditional_edges("agent", should_continue, {
            "tools": "tools",          # Normal: LLM made tool calls
            "agent": "agent",          # Ghost promise: LLM promised but no tool_call, retry
            "nudge": "nudge",          # Empty after tool: inject prompt to continue
            END: END,
        })

    # Conditional post-tools edge: route to ``question_pause_node`` when
    # the question tool has requested a pause (F1). The original
    # unconditional ``tools → agent`` edge was the bug — it could not
    # honor a pause request because every post-tools routing went back
    # to the agent unconditionally. The conditional router reads
    # ``manager._question_pause_requested`` (set by the ``question``
    # tool, cleared by ``question_pause_node``'s ``finally`` block).
    # Non-question tool calls still route to ``agent`` normally because
    # the flag defaults to False for instances that haven't called the
    # ``question`` tool.
    if manager is None:
        # Backward-compatible fallback: no manager reference means no
        # question-pause behavior. Keep the original unconditional edge.
        graph.add_edge("tools", "agent")
    else:
        question_pause_node = create_question_pause_node(manager)
        graph.add_node("question_pause_node", question_pause_node)
        graph.add_conditional_edges(
            "tools",
            create_post_tools_router(manager),
            {
                "agent": "agent",
                "question_pause_node": "question_pause_node",
            },
        )
        # ``question_pause_node`` routes to END — the cascade has
        # cancelled the graph task, so resuming will start fresh from
        # the checkpoint.
        graph.add_edge("question_pause_node", END)
    graph.add_edge("nudge", "agent")
    
    compiled = graph.compile(checkpointer=checkpointer)

    # W4 FIX: Store language_check_active flag on compiled graph for streaming code to read
    compiled.language_check_active = language_check_enabled

    # Late bind graph reference
    graph_ref[0] = compiled

    return compiled


# Backward compatibility alias
build_session_graph = build_instance_graph


__all__ = [
    "LoopBreakerConfig",
    "LoopRepairer",
    "LoopBreakerSlot",
    "ToolThrottleSlot",
    "InjectionSlot",
    "build_instance_graph",
    "build_session_graph",
    "create_agent_node",
]

