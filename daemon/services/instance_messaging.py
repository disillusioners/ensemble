"""Instance messaging service for sending and processing messages."""

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, NamedTuple

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from sqlmodel import Session

from ..cancellation import CancellationToken
from ..compaction import ContextCompactor, CompactionContext, get_model_context_limit
from ._checkpoint_utils import _is_terminal_checkpoint
from ..language_detection import _normalize_content
from ..loader import estimate_messages_tokens
from ..persistence import get_instance_messages
from ..repositories.event.models import Event, EventKind
from ..repositories.instance.models import Instance, InstanceStatus
from ..repositories.message_queue.models import MessageQueue, MessageStatus, MessageType
from ..repositories.task.models import SuspensionReason, Task, TaskStatus, TaskType
from ..utils import parse_think_tags, serialize_message
from ..write_pause_guard import WriteGuardSession
from .cancellation import CancellationService
from .main_loop_bridge import MainLoopBridge
from .messaging_types import AsyncMessageResult, LinkageContractError
from .message_tap import (  # Phase 1 C2 — langgraph-checkpoint-perf
    MessageTapSlot,
    SOURCE_USER_MESSAGE_ENTRY,
    SOURCE_COMPACTION_MESSAGING,
)
from .project_normalizer import normalize_project_id
from .skill_meta_parser import extract_load_skill, parse_meta_tag
from .skill_metrics_service import (
    AUTO_LOAD_BLOCK_ACTIVE_KEY,
    INJECTED_SKILLS_METADATA_KEY,
    REPLACED_SKILLS_METADATA_KEY,
)

if TYPE_CHECKING:
    from ..config import Config
    from ..graph import CompiledStateGraph
    from ..repositories.instance.repository import SQLModelInstanceRepository
    from ..repositories.message_queue.repository import MessageQueueRepository
    from ..repositories.project.repository import SQLModelProjectRepository
    from .child_reports import ChildReportsService
    from .event_publisher import EventPublisherService
    from .error_reporting import ErrorReportingService


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# WC-wake kill-switch (wc-wake-report-integrity, C1-Q2 RESOLVED 2026-08-30)
# ─────────────────────────────────────────────────────────────────────────────
# The P1 routing pivot (T2 / T4 / T7) replaces the legacy "WC → RAM FIFO
# set_injection" path with "WC → enqueue_message (durable wake turn)". That
# change has three call sites — HTTP ``POST /messages``
# (``daemon/routers/messages.py``), agent-tool ``send_message``
# (``daemon/tools/instance.py``), and ``job_inject``
# (``daemon/tools/job_queue.py``) — and they ALL cross the same
# ``INJECTION_ELIGIBLE_STATUSES`` constant in ``daemon/constants.py`` (T2
# shrinks it to ``frozenset({"running"})``).
#
# Per ``decisions.md`` C1-Q2 (RESOLVED 2026-08-30, leader-locked) the
# pivot ships behind an env-driven kill-switch and is **DEFAULT OFF** at
# code-land: the routing pivot only activates when
# ``ENSEMBLE_WC_WAKE_ENQUEUE=1``. The flag mirrors the precedent set by
# ``LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED`` (governor-chain guard,
# 2026-08-30; same shape: env-driven, cached on first access, restart-
# required to flip, one-shot INFO log on boot, valid truthy/falsy values
# spelled out below).
#
# Flag states:
#
#   * **OFF (default)** — the LEGACY behavior is preserved at all three
#     call sites: HTTP returns 202-injected, agent-tool injection route
#     returns the W3-stranding text, ``job_inject`` returns
#     ``{status: "injected"}``. This is the documented revert path; an
#     operator with an incident can flip the env back to the previous
#     behavior in O(restart) without code changes. **The constant
#     ``INJECTION_ELIGIBLE_STATUSES`` stays shrunk to ``{"running"}``
#     regardless** — the flag-off branch reads that shrunk set and adds
#     the legacy ``"waiting_children"`` glock back at the call sites
#     (constant stays single-home, fork lives ONLY at the gating branch).
#
#   * **ON** — the new routing pivot is live everywhere: WC targets get
#     real ``enqueue_message`` durable wake turns, HTTP returns 200 with
#     ``MessageResponse{message_id, job_id, queued}``, the agent-tool
#     enqueue branch handles WC, ``job_inject`` mirrors Option A.
#
# Always-active (no gating, no flag check): the D1 enqueue-seam pairing
# tail-guard (T6), the D2 seam-drain of parked FIFO leftovers (T5), the
# R1 deterministic placeholder ids (T1), and the T6b deletion of the
# legacy ``Manager.send_message`` -> ``InstanceMessagingService.send_message``
# -> ``graph.ainvoke`` bypass. These are correctness fixes that ship
# regardless of which way the routing flag points.
#
# Soak / flip policy (per C2-D2.5-FLIP precedent, leader-locked 2026-08-30):
# ≤ 2-week soak on the OFF default, operator flips ON on first deploy
# thereafter; immediate flip to OFF on any silent-death incident. The
# exact ``--flip-window``, soak duration, and incident criteria are
# recorded in ``docs/setup.md`` next to the env var documentation.
_WC_WAKE_ENQUEUE_ENV = "ENSEMBLE_WC_WAKE_ENQUEUE"
_WC_WAKE_ENQUEUE_ENABLED: bool | None = None
_WC_WAKE_ENQUEUE_BOOT_LOG_EMITTED: bool = False


def _resolve_wc_wake_enqueue_enabled() -> bool:
    """Resolve and cache the WC-wake routing-pivot kill-switch.

    Returns:
        ``True`` when the routing pivot is enabled — i.e. WC targets
        route through ``enqueue_message`` (durable wake) instead of
        ``set_injection`` (RAM FIFO). ``False`` when disabled via
        ``ENSEMBLE_WC_WAKE_ENQUEUE=0`` — the LEGACY behavior is
        preserved at all three call sites (HTTP, agent-tool, ``job_inject``).

    Valid truthy values: ``("1", "true", "yes", "on")``. Valid falsy
    values: ``("0", "false", "no", "off")``. Blank / unset / unknown
    values all resolve ``False`` (the OFF default) — blanking the env
    mid-incident (``ENSEMBLE_WC_WAKE_ENQUEUE=``) is the instant-revert
    path, so it MUST resolve OFF. (Note: ``""`` is NOT in the truthy
    tuple — unlike the governor-guard resolver, whose ``get(..., "1")``
    unset default makes a blank env consistent with its ON direction;
    this resolver defaults OFF via ``get(..., "0")``.) Unknown (non-blank)
    values additionally fall back to ``False`` with a one-shot WARN
    cached on first access.

    Caching and the boot-log emission are independent: this function
    caches ONLY the resolved boolean; the one-shot INFO log naming the
    resolved state is emitted by :func:`emit_wc_wake_enqueue_boot_log`
    itself (gated by its own ``_WC_WAKE_ENQUEUE_BOOT_LOG_EMITTED``
    flag), which is called from ``InstanceManager.__init__``
    (``daemon/manager.py:740`` — manager-init path). Flipping the env
    mid-flight has no effect on either: the boolean is cached for the
    daemon's lifetime, and the log fires exactly once per process.
    """
    global _WC_WAKE_ENQUEUE_ENABLED
    if _WC_WAKE_ENQUEUE_ENABLED is not None:
        return _WC_WAKE_ENQUEUE_ENABLED
    raw = os.environ.get(_WC_WAKE_ENQUEUE_ENV, "0").strip().lower()
    if raw in ("0", "false", "no", "off"):
        _WC_WAKE_ENQUEUE_ENABLED = False
    elif raw in ("1", "true", "yes", "on"):
        _WC_WAKE_ENQUEUE_ENABLED = True
    else:
        logger.warning(
            "%s=%r is not a recognized truthy/falsy value; falling back "
            "to OFF (default — legacy WC injection routing). Valid falsy: "
            "0/false/no/off. Valid truthy: 1/true/yes/on.",
            _WC_WAKE_ENQUEUE_ENV,
            raw,
        )
        _WC_WAKE_ENQUEUE_ENABLED = False
    return _WC_WAKE_ENQUEUE_ENABLED


def emit_wc_wake_enqueue_boot_log() -> None:
    """Emit the one-time boot-time INFO log naming the resolved flag state.

    Called from ``InstanceManager.__init__`` after the messaging service
    is wired (mirrors ``emit_governor_recursion_guard_boot_log``). Restart-
    required semantics — same as the governor-guard wrapper. The actual
    routing logic is gated on ``_resolve_wc_wake_enqueue_enabled()`` at
    every call site, so flipping the env mid-flight has no effect.
    """
    global _WC_WAKE_ENQUEUE_BOOT_LOG_EMITTED
    if _WC_WAKE_ENQUEUE_BOOT_LOG_EMITTED:
        return
    _WC_WAKE_ENQUEUE_BOOT_LOG_EMITTED = True
    enabled = _resolve_wc_wake_enqueue_enabled()
    logger.info(
        "WC-wake enqueue routing resolved: %s (env %s=%s); "
        "WC targets %s. Restart required to flip. "
        "See docs/setup.md (ENSEMBLE_WC_WAKE_ENQUEUE).",
        "enabled" if enabled else "DISABLED (legacy FIFO injection)",
        _WC_WAKE_ENQUEUE_ENV,
        os.environ.get(_WC_WAKE_ENQUEUE_ENV, "<unset>"),
        "route to enqueue_message (durable wake, first-class turn)"
        if enabled
        else "still route to set_injection (RAM FIFO; 202-injected)",
    )


def _reset_wc_wake_enqueue_for_tests() -> None:
    """Clear the cached kill-switch state so tests can re-resolve after
    mutating the env. Test-only — production code never invokes this."""
    global _WC_WAKE_ENQUEUE_ENABLED, _WC_WAKE_ENQUEUE_BOOT_LOG_EMITTED
    _WC_WAKE_ENQUEUE_ENABLED = None
    _WC_WAKE_ENQUEUE_BOOT_LOG_EMITTED = False


def _derive_task_flags_from_queue_type(
    queue_type: str | None,
    is_deferred: bool = False,
    is_background: bool = False,
) -> tuple[bool, bool]:
    """Derive ``is_deferred`` / ``is_background`` from a queue's ``queue_type``.

    The queue's ``queue_type`` is the source of truth for these flags — a
    task's flags MUST match the lane of the queue it is enqueued on:

      * ``"defer"`` queue → ``is_deferred=True`` (a defer-queue task must
        be defer-flagged so the idle-gate predicate
        ``TaskRepository.has_active_non_deferred_work`` does not count it
        as non-deferred work and wedge the defer queue).
      * ``"background"`` queue → ``is_background=True`` (mirror of the
        above for the background lane).
      * ``"fifo"`` / ``"parallel"`` / ``None`` → fall through with the
        caller's flags intact (normal lanes do not require a flag).

    Extracted into a module-level helper so the logic can be unit-tested
    directly without standing up the full ``enqueue_message_job`` stack
    (fix / unit test for the idle-gate deadlock, 2026-08-10).

    Args:
        queue_type: The queue's ``queue_type`` attribute (one of
            ``"fifo"``, ``"parallel"``, ``"defer"``, ``"background"``,
            or ``None`` when the queue could not be resolved). When
            ``None`` the caller's flags are returned unchanged.
        is_deferred: Caller-supplied ``is_deferred`` (default False).
        is_background: Caller-supplied ``is_background`` (default False).

    Returns:
        ``(is_deferred, is_background)`` — the caller's flags overridden
        by the queue type when applicable.

    Raises:
        ValueError: If ``queue_type`` is a non-``None`` value outside the
            recognized queue types.
    """
    if queue_type == "defer":
        return True, is_background
    if queue_type == "background":
        return is_deferred, True
    if queue_type in {"fifo", "parallel", None}:
        return is_deferred, is_background
    raise ValueError(
        f"Unknown queue_type {queue_type!r}; expected one of "
        "{'fifo', 'parallel', 'defer', 'background'} or None"
    )


def _build_message_content(message: str, images: list[str] | None) -> str | list:
    """Build multimodal content array for messages with optional images.

    Args:
        message: The text content of the message.
        images: Optional list of base64 image data URIs.

    Returns:
        String message if no images, otherwise list with text and image_url blocks.
    """
    if images:
        content = [{"type": "text", "text": message}]
        for img in images:
            content.append({"type": "image_url", "image_url": {"url": img}})
        return content
    return message


def _stringify_tool_message_content(m) -> tuple[str, str]:
    """Extract `(tool_call_id, content_str)` from a ToolMessage.

    Centralized so the bake-in path (into the next AIMessage's tool_calls)
    and the real-time tool_result SSE path cannot drift.

    Args:
        m: A LangChain ToolMessage.

    Returns:
        Tuple of `(tool_call_id, content_str)`. Either may be empty.
    """
    tc_id = getattr(m, "tool_call_id", "") or ""
    raw_content = getattr(m, "content", "") or ""
    content_str = raw_content if isinstance(raw_content, str) else str(raw_content)
    return tc_id, content_str


def _dedup_merge_skill_ids(
    instance_repository: Any,
    instance_id: str,
    new_ids: list[str],
) -> None:
    """Read-merge-write ``last_injected_skill_ids`` (DEDUP-MERGE) for one instance.

    Centralizes the read-modify-write of :data:`INJECTED_SKILLS_METADATA_KEY`
    so the BM25-skill persist and the auto-load-skill persist share one
    implementation. Order-preserving union via ``dict.fromkeys``: existing
    (explicit) IDs first, then ``new_ids`` appended, duplicates dropped.

    No filtering is applied here — REPLACE/REPLACED filtering is the
    caller's responsibility (auto-load: :func:`_fetch_auto_load_skills`;
    BM25: ``SkillInjectionService``). Keeping the merge filter-free means
    a future exclusion rule has exactly one site to update per producer.

    Args:
        instance_repository: Repository exposing ``get`` (returning an
            instance row whose ``instance_metadata`` is a dict) and
            ``set_metadata``.
        instance_id: Target instance.
        new_ids: Skill IDs to merge into the existing set.
    """
    inst = instance_repository.get(instance_id)
    existing: list[str] = []
    if inst is not None and inst.instance_metadata:
        raw = inst.instance_metadata.get(INJECTED_SKILLS_METADATA_KEY) or []
        if isinstance(raw, list):
            existing = [str(x) for x in raw if x]
    merged = list(dict.fromkeys(existing + [str(x) for x in new_ids if x]))
    instance_repository.set_metadata(
        instance_id,
        INJECTED_SKILLS_METADATA_KEY,
        merged,
    )


# ─────────────────────────────────────────────────────────────────────────────
# D1 entry-seam pairing tail-guard (wc-wake-report-integrity, T6)
# ─────────────────────────────────────────────────────────────────────────────
# Closes the pre-existing poisoned-tail → LangGraph 2013 exposure at the
# enqueue-seam boundary. The in-graph guard
# (``daemon.graph._ensure_tool_result_pairing`` at graph.py:271-384)
# runs at the ``agent_node`` drain sites AFTER ``astream`` is invoked
# — too late for the LLM call that the gateway is about to reject.
# The seam guard runs at the enqueue seam (after the three
# ``_build_graph_input`` sites converge, before ``graph.astream``),
# reads the checkpoint state via ``graph.aget_state``, and prepends
# synthesized ``ToolMessage`` placeholders (R1 deterministic ids) to
# ``graph_input['messages']`` so the LLM-bound list is structurally
# valid before the gateway sees it. LangGraph's ``add_messages``
# reducer then commits the healed tail to the checkpoint in the same
# superstep as the new turn.
#
# Flag-INDEPENDENT (always active, no gating) per the dispatch directive.
# Same O(1) happy-path tail check as the in-graph helper; cost is one
# ``aget_state`` read per enqueued turn + bounded walk — measured-cheap,
# optimization seam documented in case profiling flags it later.


async def _heal_poisoned_checkpoint_tail(
    graph: "CompiledStateGraph",
    config: dict,
    graph_input: dict | None,
    instance_short: str = "",
) -> list[ToolMessage]:
    """Prepend synthesized ``ToolMessage`` placeholders to
    ``graph_input['messages']`` when the checkpoint tail is poisoned.

    wc-wake-report-integrity (T6, D1): the helper reads the current
    checkpoint state via ``graph.aget_state(config)``, tail-checks it
    the same way ``daemon.graph._ensure_tool_result_pairing`` does
    (O(1) happy path, bounded backward walk), and prepends synthesized
    placeholders to ``graph_input['messages']`` so the LLM-bound list
    is structurally valid. ``add_messages`` then commits the healed
    tail to the checkpoint in the same superstep as the new turn —
    no separate ``aupdate_state`` round-trip.

    The synthesized placeholders carry the SAME R1 deterministic id
    format (``pairing-synth-{tc_id}``) as the in-graph helper, so a
    re-heal across the seam + in-graph dedup chain is idempotent
    (``add_messages`` dedups by id).

    Args:
        graph: The compiled LangGraph for this instance.
        config: LangGraph config dict (carries ``thread_id``).
        graph_input: The LLM-bound dict being prepared for
            ``graph.astream``. **MUTATED IN PLACE** — placeholders
            are prepended to ``graph_input['messages']``. Pass
            ``None`` to short-circuit (silent-resume branch).
        instance_short: Short instance id (``<first-segment-of-uuid>``)
            for the WARNING log. Empty string is accepted.

    Returns:
        The list of placeholder ``ToolMessage``s synthesized and
        prepended (in the order they appear in ``graph_input['messages']``).
        Empty on the happy path (no trailing unanswered ``tool_calls``)
        or on ``graph_input=None`` (silent-resume short-circuit).

    Note:
        The inspection core is intentionally NOT pulled from the
        in-graph helper directly (no cross-module import) — this
        helper is a state-aware checkpoint reader, not a
        list-mutator. The bounded walk's O(1) happy path and the
        deduplication rules mirror ``_ensure_tool_result_pairing``:
        if the tail is not an ``AIMessage`` carrying ``tool_calls``,
        return ``[]``. If it is, walk backward over trailing
        ``AIMessage(tc)`` blocks (bounded by the same 8-message
        window), dedupe against existing ``tool_call_id``s in the
        trailing window, and synthesize one ``ToolMessage`` per
        unanswered ``tool_call_id``.
    """
    if graph_input is None:
        # S5 / architect correction 2: the ``:3407`` silent-resume
        # branch sets ``graph_input = None`` (pure checkpoint resume
        # — silent mode or no content). The seam heal/prepend MUST
        # SKIP a None graph_input: that path injects no new
        # mid-turn HumanMessage at the seam and is already covered
        # by the in-graph pairing guard (graph.py:2971 / :3145).
        return []

    # Read the checkpoint state. Pattern already used in this file
    # at :925 (``_maybe_compact_context``).
    state = await graph.aget_state(config)
    if state is None:
        return []

    # ``state.values`` is dict-like. Defensive fallback for the rare
    # mock / alternative state shape that returns None for ``values``
    # or lacks a ``messages`` key.
    messages: list | None = None
    values = getattr(state, "values", None)
    if isinstance(values, dict):
        messages = values.get("messages")
    if not messages:
        return []

    # O(1) happy path: only proceed when the tail itself is an
    # AIMessage carrying tool_calls. NO full-history scan — that
    # pattern was explicitly rejected in the in-graph helper as too
    # costly (mirrors ``daemon/graph.py:311-316``).
    tail = messages[-1]
    if not (isinstance(tail, AIMessage) and getattr(tail, "tool_calls", None)):
        return []

    # Walk backward over trailing AIMessage(tc) blocks; stop on the
    # first non-AIMessage(tc) message OR when we hit the safety
    # bound (mirrors ``daemon/graph.py:318-329``).
    ai_indices: list[int] = []
    end_bound = max(0, len(messages) - 8)  # _TOOL_PAIRING_MAX_TRAVERSAL=8
    i = len(messages) - 1
    while i >= end_bound:
        msg = messages[i]
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            ai_indices.append(i)
            i -= 1
        else:
            break
    if not ai_indices:
        return []

    ai_indices.reverse()
    leftmost_idx = ai_indices[0]

    # Dedupe against existing ToolMessages in the trailing window.
    existing_tool_call_ids: set[str] = set()
    for m in messages[leftmost_idx:]:
        if isinstance(m, ToolMessage) and m.tool_call_id:
            existing_tool_call_ids.add(m.tool_call_id)

    synthesized: list[ToolMessage] = []
    for orig_idx in ai_indices:
        ai_msg = messages[orig_idx]
        tool_calls = ai_msg.tool_calls or []
        for tc in tool_calls:
            tc_id = tc.get("id") if isinstance(tc, dict) else None
            if not tc_id or tc_id in existing_tool_call_ids:
                continue
            tc_name = tc.get("name", "") if isinstance(tc, dict) else ""
            # R1 deterministic id (mirrors graph.py:364-368) — same
            # id format so a re-heal across the seam + in-graph
            # dedup chain is idempotent.
            tm = ToolMessage(
                content=(
                    "[Tool execution interrupted (daemon restart/crash) — "
                    "result unavailable. Re-issue the tool call if still "
                    "needed.]"
                ),
                tool_call_id=tc_id,
                name=tc_name,
                id=f"pairing-synth-{tc_id}",
            )
            existing_tool_call_ids.add(tc_id)
            synthesized.append(tm)

    if not synthesized:
        return []

    # Prepend to ``graph_input['messages']`` so the LLM-bound list is
    # structurally valid before ``graph.astream``. The D1 seam
    # places placeholders at the HEAD — the poisoned checkpoint
    # tail sits at the head of the persisted state, so the
    # placeholders must immediately follow it for the gateway to
    # accept the LLM call.
    existing_messages = list(graph_input.get("messages") or [])
    graph_input["messages"] = synthesized + existing_messages

    logger.warning(
        f"[ToolPairing] D1 entry-seam: synthesized {len(synthesized)} "
        f"placeholder tool result(s) for instance {instance_short} — "
        f"checkpoint tail had unanswered tool_calls before enqueue "
        f"turn build."
    )

    return synthesized


def _build_graph_input(
    content: str | list,
    message_id: str,
    persistent_context_msgs: list[HumanMessage] | None = None,
    prepended_msgs: list[HumanMessage] | None = None,
) -> dict[str, list[HumanMessage]]:
    """Build the LangGraph ``graph_input`` dict, prepending the persistent context block.

    Phase 3 helper used by all three ``graph_input = ...`` construction
    sites in :meth:`InstanceMessagingService._process_message_with_tracking`.
    Centralizing the construction ensures the prepend order is identical
    across the retry-with-checkpoint, retry-without-checkpoint, and
    first-attempt branches — a divergence there would silently double-
    inject (or skip-inject) on retries.

    Hybrid Context Injection (2026-07-29): the
    ``persistent_context_msgs`` arg carries the project + shared-
    context **+ skills** HumanMessages built by the messaging path.
    When provided (non-empty), they are prepended to ``graph_input``
    so LangGraph's ``add_messages`` reducer checkpoints them BEFORE
    the user message — the persistent block then lives in
    ``state['messages']`` for every subsequent turn without any
    per-turn rebuild. The user message keeps its ``message_id`` for
    reducer dedup, and the persistent messages keep their own uuids
    (generated by ``_make_context_message``) so they survive
    checkpoint serialisation as stable, identifiable rows.

    2026-07-29 refactor: skills were moved from the ephemeral
    (``agent_node``-side rebuild every turn) into this persistent
    block. A skill injected on turn 1 is now a checkpointed
    ``HumanMessage`` that survives every subsequent turn via
    ``state['messages']``. A new skill triggered on turn 2 is
    APPENDED to the persistent half (LangGraph ``add_messages``
    reducer semantics) and prepended to ``graph_input`` for that
    turn — no double-injection because ``agent_node`` no longer
    re-injects skills into ``full_messages``.

    wc-wake-report-integrity (T5): ``prepended_msgs`` is the seam
    parameter the D2 FIFO-leftover drain flows through. The drain
    site in ``_process_message_with_tracking`` builds a list of
    ``HumanMessage`` instances from the parked FIFO (preserving the
    ``injected_message: True`` marker + optional ``source`` per
    ``graph.py:2950-2961`` so leftovers ARE injections — the marker
    is kept so C3 compaction preservation and D12 subtree filtering
    continue to recognise them as injected traffic). The drain's
    exact input order: ``[pairing_placeholders?] + persistent_block +
    leftover_fifo_msgs (oldest-first) + [user_message]`` — see
    ``test_instance_messaging_seam_drain`` for the positional pin
    across all four slots. The pairing-placeholders are prepended at
    position 0 by the D1 entry-seam guard AFTER ``_build_graph_input``
    returns — so the helper places ``prepended_msgs`` (the FIFO
    leftovers) BETWEEN the persistent block and the user message,
    not at the head. ``prepended_msgs`` defaults to ``None`` so the
    three existing call sites (``:3402/:3411/:3420``) remain
    byte-identical; only the new seam-drain call site passes
    non-``None``.

    Args:
        content: The user message content (string or multimodal
            content-block list from ``_build_message_content``).
        message_id: The queue message ID; becomes the user
            ``HumanMessage.id`` for ``add_messages`` dedup.
        persistent_context_msgs: Optional list of
            :class:`HumanMessage` carrying the persistent block
            (project + shared-context + skills). Prepended BEFORE
            the user message so the LangGraph ``add_messages``
            reducer checkpoints them with the user message.
            ``None`` (default) and ``[]`` both mean "no persistent
            block this turn" — every turn after the first, or any
            turn when persistent context is empty.
        prepended_msgs: Optional list of :class:`HumanMessage` to
            inject BETWEEN the persistent block and the user
            message. Used by the D2 seam-drain to thread parked
            FIFO leftovers into the LLM-bound list for THIS turn
            (oldest-first, single turn when both leftovers + new
            message exist). The pairing-placeholder ``ToolMessage``s
            synthesized by the D1 entry-seam guard (T6) ride a
            different seam — they are prepended at position 0 by
            :func:`_heal_poisoned_checkpoint_tail` AFTER
            ``_build_graph_input`` returns, so they precede the
            persistent block in the final list. ``None`` (default)
            and ``[]`` both mean "no prepended messages this
            turn" — preserves byte-identical behavior for the
            three existing call sites.

    Returns:
        ``{"messages": [...]}`` dict ready for
        ``graph.astream(graph_input, ...)``. With a non-empty
        ``persistent_context_msgs``, the list is
        ``[persistent_1, ..., persistent_n, prepended_1, ...,
        user_message]`` — the persistent block first, then the
        FIFO leftovers (``prepended_msgs``), then the user
        message. With a non-empty ``prepended_msgs`` and no
        persistent block, it is ``[prepended_1, ..., user_message]``.
        With neither, just ``[user_message]``. The pairing
        placeholders (T6) sit at the head — prepended by the D1
        guard AFTER ``_build_graph_input`` returns, so the final
        end-to-end order is
        ``[pairing_placeholders?] + persistent_block? +
        leftover_fifo_msgs (oldest-first) + [user_message]``
        per the LOCKED C1-D2 spec.
    """
    user_message = HumanMessage(content=content, id=message_id)
    # Hybrid split — prepend the persistent context block BEFORE the
    # user message so LangGraph's ``add_messages`` reducer checkpoints
    # it with the user message. Empty / None ``persistent_context_msgs``
    # produces the steady-state second-turn layout ``[user_message]``.
    # Per the 2026-07-29 refactor this block also carries skills.
    persistent = list(persistent_context_msgs or [])
    # T5 (wc-wake): ``prepended_msgs`` flows FIFO leftovers into the
    # LLM-bound list between the persistent block and the user message
    # — the LOCKED C1-D2 S4 spec order
    # ``[pairing_placeholders?] + persistent + leftovers (oldest-first) +
    # [user_message]``. The pairing-placeholder ``ToolMessage``s
    # synthesized by the D1 entry-seam guard (T6) ride a DIFFERENT seam
    # — they are prepended at position 0 by
    # :func:`_heal_poisoned_checkpoint_tail` AFTER this helper returns,
    # so they precede the persistent block in the final list. Empty /
    # None ``prepended_msgs`` produces byte-identical pre-T5 output for
    # the three call sites that do not pass it.
    prepended = list(prepended_msgs or [])
    return {"messages": persistent + prepended + [user_message]}


def _get_message_event_type(msg: dict) -> str:
    """Determine event type based on message content.

    Args:
        msg: Serialized message dict

    Returns:
        Event type string: "user_message" | "assistant_message" | "thinking"
            | "tool_call" | "tool_result"
    """
    if msg.get("role") == "user":
        return "user_message"
    if msg.get("role") == "tool":
        return "tool_result"
    if msg.get("tool_calls"):
        return "tool_call"
    if msg.get("thinking") or msg.get("thinking_extracted"):
        return "thinking"
    return "assistant_message"


def _compute_message_content_hash(msg: dict) -> str:
    """Compute a hash of the key content fields for deduplication.

    Args:
        msg: Serialized message dict

    Returns:
        A string hash representing the message content
    """
    import hashlib

    # Key fields that matter for content comparison
    content_parts = {
        "content": msg.get("content"),
        "tool_calls": msg.get("tool_calls"),
        "role": msg.get("role"),
    }
    # Normalize: sort keys and remove None values for consistent hashing
    content_str = json.dumps(content_parts, sort_keys=True, default=str)
    return hashlib.md5(content_str.encode()).hexdigest()[:16]


def _ensure_work_id_fail_closed(
    work_id: str | None,
    work_id_required: bool,
) -> str:
    """Fail-closed ``work_id`` guard (Fix A, constitution Phase 0).

    Pure decision extracted from ``_prepare_enqueued_message`` so the
    contract is directly unit-testable without a DB session
    (``tests/unit/services/test_linkage_contract_fail_closed.py``).

    Behaviour (exactly the pre-extraction semantics):

    * ``work_id`` provided → returned unchanged (the caller-supplied
      linkage wins, job-driven or not).
    * ``work_id_required=True`` and ``work_id is None`` → raise
      :class:`LinkageContractError` instead of auto-minting — a fresh
      UUID on the job-driven path would re-key the Task and break
      Pattern-f1 ``get_by_work_id`` recovery lookups (the 2026-08-31
      f1-misfire incident).
    * ``work_id_required=False`` and ``work_id is None`` → return a
      freshly minted UUID (the internal self-mint path: agent-to-agent
      ``send_message``, cascade-resume, child reports — no JobItem).
    """
    if work_id is not None:
        return work_id
    if work_id_required:
        # This fail-closed ``work_id`` guard fires BEFORE any DB write:
        # no row (and no Task) exists yet, so there is nothing to roll
        # back. A result-mismatch raise is necessarily post-enqueue and
        # post-commit.
        raise LinkageContractError(
            source="_prepare_enqueued_message",
            expected_job_id="",
            actual_job_id="",
            omission=True,
        )
    return str(uuid.uuid4())


class _PreparedEnqueueContext(NamedTuple):
    """Result of `_prepare_enqueued_message` shared prelude.

    Carries the values callers need to perform their path-specific dispatch
    (after D13: unified — WorkerPool Task row + notify, no JobQueue branch).
    """
    message_id: str
    msg_type: str
    status_changed_to_running: bool
    is_idle_to_running: bool
    instance_agent_id: str | None
    previous_status: str | None
    # D13: The Task row is always created in the same transaction as the
    # MessageQueue row. ``task_id`` is its primary key (int | None) — None
    # only if the task insert failed for an unrecoverable reason (callers
    # treat None as "no resolvable work_id available"). The HTTP route
    # discards ``job_id``; the ``job_continue`` tool uses it as
    # ``new_job_id`` (the resolution path goes through
    # ``work_resolver.resolve_work`` against ``task`` and
    # ``job_queue_items`` — see ``enqueue_message``).
    task_id: int | None
    # Virtual Job Management Surface (Phase 1, Batch 3,
    # 2026-06-27). The stable cross-system ``work_id`` (UUID4 string)
    # minted at Task row creation. This is the truthful handle for the
    # virtual job resolver — callers pass it back to ``GET /work/{id}``
    # and ``work_resolver.resolve_work`` looks it up uniformly across
    # ``task`` and ``job_queue_items``. Supersedes ``task_id`` as the
    # ``AsyncMessageResult.job_id`` payload (see
    # ``enqueue_message``); ``task_id`` is retained for callers that
    # still want the int PK (currently nobody does, but it stays in the
    # NamedTuple for the existing test surface). ``None`` only when
    # the Task insert itself failed (mirrors ``task_id``).
    work_id: str | None
    # Defer Queue marker (Phase 3 Part B1, 2026-06-27,
    # feature/virtual-job-management-surface). Mirrors
    # ``Task.is_deferred`` at row-creation time. The orchestrator
    # passes ``is_deferred`` into ``enqueue_message`` /
    # ``_prepare_enqueued_message``; the value is stamped onto the new
    # Task row and surfaced here so callers (and the eventual defer
    # queue gate) can read it without re-querying the DB. Always False
    # for the default (non-defer) path — every existing caller that
    # does not pass ``is_deferred`` is unaffected.
    is_deferred: bool


class ActivityCallbackHandler(BaseCallbackHandler):
    """Callback to update message activity during LLM/graph execution.
    
    This ensures long-running tasks are not incorrectly marked as "stuck"
    by the worker pool health checks, as long as there's recent activity.
    """
    
    def __init__(self, queue_repository, message_id: str, update_interval_seconds: float = 5.0):
        """Initialize with message queue repository.
        
        Args:
            queue_repository: The message queue repository for activity updates.
            message_id: The message ID to update activity for.
            update_interval_seconds: Minimum seconds between activity updates.
        """
        self.queue_repository = queue_repository
        self.message_id = message_id
        self.update_interval = update_interval_seconds
        self._last_update = time.monotonic()
    
    def _maybe_update(self) -> None:
        """Throttled activity update to avoid excessive DB writes."""
        now = time.monotonic()
        if now - self._last_update >= self.update_interval:
            try:
                self.queue_repository.update_activity(self.message_id)
            except Exception as e:
                logger.warning(f"Failed to update activity for {self.message_id}: {e}")
            self._last_update = now
    
    def on_llm_start(self, serialized, prompts, **kwargs) -> None:
        self._maybe_update()
    
    def on_llm_new_token(self, token: str, **kwargs) -> None:
        self._maybe_update()
    
    def on_llm_end(self, response: Any, **kwargs) -> None:
        self._maybe_update()
    
    def on_tool_start(self, serialized, input_str, **kwargs) -> None:
        self._maybe_update()
    
    def on_tool_end(self, output: Any, **kwargs) -> None:
        self._maybe_update()
    
    def on_chain_start(self, serialized, inputs: Any, **kwargs) -> None:
        self._maybe_update()
    
    def on_chain_end(self, outputs: Any, **kwargs) -> None:
        self._maybe_update()


class CancellationCallbackHandler(BaseCallbackHandler):
    """Callback that checks for cancellation at key points during LLM execution."""
    
    def __init__(
        self, 
        cancellation_token: CancellationToken,
        check_interval_tokens: int = 10
    ):
        self._token = cancellation_token
        self._check_interval = check_interval_tokens
        self._token_count = 0
    
    def _check_cancellation(self) -> None:
        """Check for cancellation and raise if cancelled."""
        self._token.check()
    
    def on_llm_start(self, serialized, prompts: Any, **kwargs) -> None:
        """Check cancellation before LLM call."""
        self._check_cancellation()
    
    def on_llm_new_token(self, token: str, **kwargs) -> None:
        """Check cancellation periodically during streaming."""
        self._token_count += 1
        if self._token_count % self._check_interval == 0:
            self._check_cancellation()
    
    def on_tool_start(self, serialized, input_str: Any, **kwargs) -> None:
        """Check cancellation before tool execution."""
        self._check_cancellation()
    
    def on_chain_start(self, serialized, inputs: Any, **kwargs) -> None:
        """Check cancellation before chain step."""
        self._check_cancellation()


class InstanceMessagingService:
    """Service for sending and processing messages to/from instances.
    
    Handles:
    - Direct message sending (send_message)
    - Message queuing (enqueue_message)
    - Message processing with tracking (_process_message_with_tracking)
    - Message history retrieval (get_messages)
    - Queue statistics (get_queue_stats)
    """

    def __init__(
        self,
        manager: "InstanceManager",
        cancellation_service: "CancellationService",
        child_reports_service: "ChildReportsService | None" = None,
        events_service: "EventPublisherService | None" = None,
    ):
        """Initialize the messaging service.
        
        Args:
            manager: The InstanceManager facade.
            cancellation_service: Service for cancellation handling.
            child_reports_service: Service for child completion reports.
            events_service: Service for lifecycle event publishing.
        """
        self._manager = manager
        self._cancellation_service = cancellation_service
        self._child_reports_service = child_reports_service
        self._events_service = events_service

    @property
    def _config(self) -> "Config":
        """Access config through manager for test mockability."""
        return self._manager.config

    @property
    def _queue_repository(self) -> "MessageQueueRepository":
        """Access queue repository through manager for test mockability."""
        return self._manager._queue_repository

    @property
    def _project_repository(self) -> "SQLModelProjectRepository":
        """Access project repository through manager for test mockability."""
        return self._manager._project_repository

    @property
    def _prompt_cache(self) -> Any:
        """Access prompt cache through manager for test mockability."""
        return self._manager.prompt_cache

    @property
    def _llm_semaphore(self) -> asyncio.Semaphore:
        """Access LLM semaphore through manager for test mockability."""
        return self._manager._llm_semaphore

    @property
    def _compactor(self) -> "ContextCompactor | None":
        """Access compactor through manager for test mockability."""
        return self._manager._compactor

    @property
    def _checkpointer(self) -> "Any | None":
        """Access the underlying LangGraph checkpointer (saver) through manager.

        Phase 2 migration: the manager now stores a ``CheckpointerAdapter``;
        services that need the raw saver (``aget`` / ``alist``) reach it via
        ``raw_saver``. ``maintenance.py`` uses the adapter interface directly.

        Returns ``None`` if the checkpointer has not been initialized yet.
        """
        adapter = self._manager._checkpointer
        return adapter.raw_saver if adapter is not None else None

    def _resolve_agent_meta_from_row(self, instance_row: Any) -> Any | None:
        """Resolve the agent metadata for an instance row (best-effort).

        The canonical versioned-resolution path (``get_version`` →
        ``get_resolved`` fallback) shared by every messaging site that
        needs the agent's metadata — both the per-instance recursion
        limit (:meth:`_effective_recursion_limit`) and the streaming
        path's ``_messaging_agent_meta`` (context injection) resolve
        through here so the fallback logic lives in one place. Returns
        ``None`` on any failure (missing row, unknown agent_id,
        registry error) so callers fall back to safe defaults.

        ``get_registry`` is imported locally so tests that patch
        ``daemon.registry.get_registry`` (or the module-level binding)
        remain effective.

        Args:
            instance_row: An instance ORM row exposing ``agent_id`` and
                (optionally) ``agent_tag``. ``None`` → ``None``.

        Returns:
            The :class:`~daemon.registry.AgentMetadata`, or ``None``.
        """
        if instance_row is None:
            return None
        agent_id = getattr(instance_row, "agent_id", None)
        if not agent_id:
            return None
        try:
            from ..registry import get_registry
            registry = get_registry()
            return (
                registry.get_version(
                    agent_id, getattr(instance_row, "agent_tag", None)
                )
                or registry.get_resolved(agent_id)
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                f"[Messaging] Failed to resolve agent_meta for "
                f"agent_id={agent_id!r}: {exc}"
            )
            return None

    def _resolve_recursion_limit_for_meta(self, agent_meta: Any | None) -> int:
        """Compute the effective LangGraph ``recursion_limit`` from agent metadata.

        Thin wrapper over :func:`daemon.registry.resolve_recursion_limit`
        so the ``resolve_recursion_limit`` import lives in exactly one
        messaging site (kept local for test-mockability parity with
        :meth:`_resolve_agent_meta_from_row`). Applies the agent's
        ``recursion_limit`` / ``recursion_limit_multiplier`` (declared in
        ``meta.json``) on top of the global
        ``limits.graph_recursion_limit`` so long-running working agents
        (e.g. worker, coder) get a larger step quota.

        Args:
            agent_meta: Pre-resolved agent metadata (may be ``None``).

        Returns:
            The effective recursion limit as a positive ``int``.
        """
        from ..registry import resolve_recursion_limit
        return resolve_recursion_limit(
            self._config.limits.graph_recursion_limit, agent_meta
        )

    def _effective_recursion_limit(self, instance_row: Any) -> int:
        """Compute the per-instance LangGraph ``recursion_limit``.

        Convenience composition: resolve the agent metadata from the
        instance row, then apply the per-agent override / multiplier.
        Use :meth:`_resolve_recursion_limit_for_meta` directly when the
        metadata is already resolved (e.g. the streaming path's
        ``_messaging_agent_meta``) to avoid re-resolution.

        Args:
            instance_row: The instance ORM row used to resolve the
                agent metadata (may be ``None``).

        Returns:
            The effective recursion limit as a positive ``int``.
        """
        return self._resolve_recursion_limit_for_meta(
            self._resolve_agent_meta_from_row(instance_row)
        )

    async def _get_system_prompt_tokens(self, instance_id: str) -> int:
        """Get the cached system prompt token count for an instance's agent.

        Async because the underlying ``_instance_repository.get`` is a sync
        SQLAlchemy call that, under SQLite WAL write contention, can block
        the event loop. We offload it to a worker thread via
        ``asyncio.to_thread`` (see deadlock analysis in experience docs).
        """
        try:
            meta = await asyncio.to_thread(
                self._manager._instance_repository.get, instance_id
            )
            if not meta:
                return 0
            # Get cached token count from prompt cache using agent_id + mcp_tool_names
            mcp_tool_names = meta.instance_metadata.get("mcp_tool_names")
            cached = self._prompt_cache.get(meta.agent_id, mcp_tool_names)
            if cached is not None:
                _, token_count = cached
                return token_count
            return 0
        except Exception:
            return 0

    async def _compute_context_usage(
        self,
        instance_id: str,
        messages: list,
    ) -> tuple[int, int, str] | None:
        """Compute the current context usage snapshot for an instance.

        Returns (tokens, context_window, model_name) or None if the model
        cannot be resolved (e.g. instance missing). The token count is
        history tokens + cached system prompt tokens so it matches what
        ``_maybe_compact_context`` measures internally.

        Async because it calls the async ``_get_system_prompt_tokens`` which
        offloads the sync SQLAlchemy ``_instance_repository.get`` to a worker
        thread (see deadlock analysis in experience docs).

        Args:
            instance_id: The instance to compute usage for.
            messages: The current message list (LangChain BaseMessage objects
                or dicts). Empty/None is fine — we still return a snapshot.
        """
        try:
            model_name = self._config.llm.model or ""
            context_window = get_model_context_limit(model_name, self._config.compaction)
            history_tokens = estimate_messages_tokens(messages or [])
            system_prompt_tokens = await self._get_system_prompt_tokens(instance_id)
            return history_tokens + system_prompt_tokens, context_window, model_name
        except Exception as e:
            logger.debug(f"Failed to compute context usage for {instance_id[:8]}...: {e}")
            return None

    async def _emit_context_usage(
        self,
        instance_id: str,
        messages: list,
        force: bool = False,
    ) -> None:
        """Compute and broadcast a context_usage event, suppressing duplicates.

        Compares against the last snapshot broadcast for this instance; if
        the token count is unchanged, the call is a no-op so the SSE
        stream isn't polluted with redundant updates. The check is per-
        process; an instance with N active SSE connections pays the cost
        once per call regardless of N.

        Pass ``force=True`` to skip the dedup check — used by the SSE
        connect handler so the first event for a freshly connected
        client always gets through, even if the instance was recently
        snapshotted for another client.

        Args:
            instance_id: The instance to snapshot.
            messages: The current message list.
            force: If True, skip the dedup check and always broadcast.
        """
        snapshot = await self._compute_context_usage(instance_id, messages)
        if snapshot is None:
            return
        tokens, context_window, model_name = snapshot

        if not force:
            last = self._manager._last_context_usage.get(instance_id)
            # Suppress if the token count hasn't moved (typical while a long
            # assistant response is streaming one token at a time — only the
            # final value changes). 1-token jitter is ignored.
            if last is not None and abs(last - tokens) < 1 and tokens > 0:
                return
        self._manager._last_context_usage[instance_id] = tokens

        try:
            await self._manager._live_hub.stream_context_usage(
                instance_id=instance_id,
                tokens=tokens,
                context_window=context_window,
                model_name=model_name,
            )
        except Exception as e:
            logger.debug(f"Failed to broadcast context usage for {instance_id[:8]}...: {e}")

    async def emit_context_usage_for_instance(self, instance_id: str) -> None:
        """Public wrapper: load current state messages and emit context usage.

        Used by the SSE connect handler to populate the FE indicator
        immediately on connect, before any user interaction. Any failure
        is logged at debug level and swallowed so a transient checkpointer
        hiccup never breaks the SSE connection.

        Reads raw LangGraph state messages directly from the checkpoint,
        matching the SSE path (``all_state_messages`` in the astream loop).
        Going through ``get_messages`` would route messages through
        ``serialize_message`` and ``get_instance_messages``, which (a) skip
        ``ToolMessage`` entries entirely, (b) strip thinking content, and
        (c) rewrite tool-call arg keys from ``args`` to ``arguments``,
        producing an inflated-by-omission or otherwise incorrect token
        count on initial page load. Passing raw ``BaseMessage`` objects
        straight to ``estimate_messages_tokens`` keeps the snapshot in sync
        with what the SSE update path computes.
        """
        # Verify instance exists first so a missing instance returns
        # silently without poking the checkpointer.
        try:
            await self._manager.get_instance(instance_id)
        except Exception as e:
            logger.debug(
                f"emit_context_usage_for_instance: instance lookup failed for "
                f"{instance_id[:8]}...: {e}"
            )
            return

        # The service-level ``_checkpointer`` property already unwraps a
        # ``CheckpointerAdapter`` to its raw saver (see the property below)
        # so SQLite and PostgreSQL backends are both supported without any
        # extra plumbing here.
        saver = self._checkpointer
        if saver is None:
            return

        try:
            config = {"configurable": {"thread_id": instance_id}}
            state = await saver.aget(config)
            if state is None:
                await self._emit_context_usage(instance_id, [], force=True)
                return
            channel_values = state.get("channel_values", {}) or {}
            messages = channel_values.get("messages", []) or []
        except Exception as e:
            logger.debug(
                f"emit_context_usage_for_instance: raw checkpoint read failed for "
                f"{instance_id[:8]}...: {e}"
            )
            return

        await self._emit_context_usage(instance_id, messages, force=True)

    async def _maybe_compact_context(
        self,
        instance_id: str,
        graph: "CompiledStateGraph",
        config: dict[str, Any],
    ) -> None:
        """Conditionally compact instance context if threshold is exceeded."""
        if self._compactor is None:
            return
        
        try:
            # Get current state
            state = await graph.aget_state(config)
            if not state:
                return

            # ── Terminal-checkpoint guard ───────────────────────────────
            # Skip compaction entirely when the checkpoint is terminal.
            # On a finished graph, calling ``aupdate_state(as_node="agent")``
            # below would clear the checkpoint's ``next=()``, causing the
            # subsequent ``astream(graph_input)`` to return instantly
            # without running the agent. On reuse of a completed instance
            # this collapses the COMPLETED→RUNNING→COMPLETED cycle to
            # <100ms so the frontend never observes RUNNING.
            #
            # Compaction is an optimization — skipping it here is safe:
            # the new message is passed as ``graph_input`` to ``astream``
            # and the agent runs against the full (uncompacted) history
            # for this turn. Active (non-terminal) turns compact normally.
            #
            # Phase 1 / WS-2.4 (architect §5): the helper lives in the
            # shared ``_checkpoint_utils`` module so this site AND the
            # ``/compact`` executor (compact_executor.py) agree on what
            # "terminal" means — anti-drift (see source-level grep test
            # ``test_terminal_helper_used_by_two_sites``).
            if _is_terminal_checkpoint(state):
                logger.debug(
                    f"[Compaction] Skipping on terminal checkpoint for {instance_id[:8]}..."
                )
                return

            messages = state.values.get('messages', [])
            system_prompt_tokens = await self._get_system_prompt_tokens(instance_id)
            last_compacted_at = state.values.get('compacted_at')
            
            # Build compaction context
            # F1 fix (2026-09-01) — pre-stamp the first-appearance
            # ``{msg_id: iso_ts}`` map so the SECTION DETAIL
            # conversation-time clause renders in the doc (architect
            # §4). F2 fix (2026-09-01) — pass ``instance_id`` so the
            # doc id is ``compaction-global-{iid}-{seq}`` (not
            # ``compaction-global--{seq}``) and seq is per-instance.
            from ..compaction import _extract_msg_timestamps
            context = CompactionContext(
                messages=messages,
                system_prompt_tokens=system_prompt_tokens,
                model_name=self._config.llm.model,
                config=self._config.compaction,
                llm_config={
                    "base_url": self._config.llm.base_url,
                    "api_key": self._config.llm.api_key,
                    "model": self._config.llm.model,
                    "model_vision": self._config.llm.model_vision,
                    "temperature": self._config.llm.temperature,
                    "request_timeout": self._config.llm.request_timeout,
                    # Proxy-buffering header opt-out — consumed by the
                    # compactor's ``default_headers`` site and stripped
                    # again by ``clean_llm_config`` (same pattern as
                    # ``base_url_backup``).
                    "buffer_response_header": self._config.llm.buffer_response_header,
                },
                last_compacted_at=last_compacted_at,
                instance_id=instance_id,
                msg_timestamps=_extract_msg_timestamps(messages),
            )
            
            # Compact state
            result = await self._compactor.compact_state(context)
            
            if result is None or result.replacement_messages is None:
                return
            
            messages_before = len(messages)
            messages_after = len(result.replacement_messages)
            tokens_before = result.tokens_before
            tokens_saved = result.tokens_saved
            
            # Architect §5 — W1 fix: read the pre-compaction
            # snapshot, then run the seam helper that emits the
            # ``REMOVE_ALL_MESSAGES`` sentinel recipe. The sentinel
            # MUST be element 0; anything before it is discarded.
            # NO per-id RemoveMessages are sent (eliminates the
            # ValueError-on-absent-id class entirely).
            pre_state = await graph.aget_state(config)
            pre_messages: list[BaseMessage] = list(
                (pre_state.values or {}).get("messages", []) or []
            )
            from daemon.compaction import (
                build_sentinel_replacement,
                CompactionAborted,
            )
            # B1 + B2 fix (2026-09-01) — engine's compacted_ids
            # is authoritative; site derives from
            # ``pre_ids − new_replacement_ids`` (non-RemoveMessage
            # keep set; RemoveMessage targets are NOT "kept").
            # See compact_executor.py:1597 for the full rationale.
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
                assert set(engine_compacted_ids) <= site_compacted_ids, (
                    "engine populated compacted_ids that are NOT a "
                    "subset of the site-derived set — engine and "
                    "site disagree on the removed span"
                )
                compacted_ids: set[str] = set(engine_compacted_ids)
            else:
                compacted_ids = site_compacted_ids
            try:
                replacement_messages = build_sentinel_replacement(
                    result, pre_messages, compacted_ids=compacted_ids
                )
            except CompactionAborted as abort_exc:
                # W1 mitigation: pre-write guard refused the write.
                # The checkpoint is untouched, the executor surfaces
                # a non-fatal warning, and the next attempt retries
                # from a clean state.
                import logging
                logging.getLogger(__name__).warning(
                    "compaction pre-write guard refused the write "
                    "for instance=%s: %s — failing open, no "
                    "checkpoint write",
                    instance_id, abort_exc,
                )
                return

            # Update graph state with compacted messages
            await graph.aupdate_state(
                config,
                {'messages': replacement_messages},
                as_node='agent'
            )

            # Update compaction timestamp if available
            if result.compacted_at:
                await graph.aupdate_state(
                    config,
                    {'compacted_at': result.compacted_at},
                    as_node='agent'
                )

            # C2 (Phase 1 — langgraph-checkpoint-perf): fire the
            # ``compaction_aupdate_messaging`` message_metadata tap
            # on the compaction's ``replacement_messages`` after the
            # ``aupdate_state`` writes resolve. Idempotent RE-TAP
            # under ``ON CONFLICT DO NOTHING`` (decisions.md D3) —
            # any message whose id was already recorded from a
            # previous turn / tap fires a constraint-level no-op,
            # preserving first-appearance semantics (decisions.md D17,
            # ``test_message_metadata_revive_stability``). The slot's
            # ``try/except`` makes a failed upsert non-load-bearing
            # (Critical 4). Sibling to
            # ``compaction_aupdate_reactive`` at ``daemon/graph.py``
            # (which fires from the in-graph reactive-compaction
            # path); this one fires from the messaging-side
            # pre-flight ``_maybe_compact_context`` path so the tap
            # coverage is complete across BOTH compaction entry
            # points.
            if self._manager.message_metadata_repo is not None:
                _compaction_tap = MessageTapSlot(
                    self._manager.message_metadata_repo,
                    SOURCE_COMPACTION_MESSAGING,
                )
                await _compaction_tap.tap_node_return(
                    result.replacement_messages,
                    instance_id,
                )

            # Log compaction result
            log_parts = [
                f"[Compaction] instance={instance_id[:8]}...",
                f"compaction_type={result.compaction_type}",
                f"messages_before={messages_before}",
                f"messages_after={messages_after}",
                f"tokens_before={tokens_before}",
                f"tokens_after={result.tokens_after}",
                f"tokens_saved={tokens_saved}",
            ]
            if result.summarization_error:
                log_parts.append(f"WARNING: summarization_error={result.summarization_error}")
            
            logger.info(" ".join(log_parts))
            
        except Exception as e:
            logger.warning(f"[Compaction] Failed to compact context for {instance_id[:8]}...: {e}")

    async def _has_checkpoint(self, instance_id: str) -> bool:
        """Check if a checkpoint exists for this instance."""
        try:
            config = {"configurable": {"thread_id": instance_id}}
            state = await self._checkpointer.aget(config)
            result = state is not None
            channel_values = state.get("channel_values", {}) if state else {}
            msg_count = len(channel_values.get("messages", []))
            logger.info(f"[RESUME] instance={instance_id[:8]} has_checkpoint={result}, msg_count={msg_count}")
            return result
        except Exception as e:
            logger.info(f"[RESUME] instance={instance_id[:8]} has_checkpoint=False, exception={type(e).__name__}")
            return False

    async def _get_message_count(self, instance_id: str) -> int:
        """Get the number of messages in the instance's checkpoint/state."""
        try:
            config = {"configurable": {"thread_id": instance_id}}
            state = await self._checkpointer.aget(config)
            if state:
                channel_values = state.get("channel_values", {})
                messages = channel_values.get("messages", [])
                return len(messages) if messages else 0
            return 0
        except Exception:
            return 0

    def _maybe_trigger_title_generation(self, instance_id: str, message: str, should_trigger: bool) -> None:
        """Fire-and-forget title generation and initiative-message capture if conditions are met."""
        if should_trigger:
            MainLoopBridge.run_async_no_wait(
                self._manager._generate_and_broadcast_title(instance_id, message)
            )
            MainLoopBridge.run_async_no_wait(
                self._maybe_store_initiative_message(instance_id, message)
            )
            logger.debug(f"Title generation triggered for first message to instance {instance_id[:8]}...")

    async def _maybe_store_initiative_message(self, instance_id: str, message: str) -> None:
        """Persist the first real user message as ``initiative_message``.

        Captured on the IDLE -> RUNNING transition (the same hook used for
        title generation). First message wins: subsequent transitions are
        no-ops because ``initiative_message`` is already present. Stores a
        truncated (1000-char) copy via the atomic
        :meth:`SQLModelInstanceRepository.set_metadata` so concurrent writes
        against different metadata keys compose correctly.
        """
        # Read the instance off-loop to avoid sync DB writes on the event loop.
        instance = await asyncio.to_thread(
            self._manager._instance_repository.get, instance_id
        )
        if instance is None:
            return
        # Idempotent guard: first message wins.
        if instance.instance_metadata and "initiative_message" in instance.instance_metadata:
            logger.debug(
                f"Initiative message already set for instance {instance_id[:8]}..., skipping"
            )
            return
        if not message or not message.strip():
            return
        truncated_message = message[:1000]
        await asyncio.to_thread(
            self._manager._instance_repository.set_metadata,
            instance_id,
            "initiative_message",
            truncated_message,
        )
        logger.debug(f"Initiative message stored for instance {instance_id[:8]}...")

    async def _drain_deferred_watchover_terminate(self, instance_id: str) -> None:
        """Drain the deferred watchover termination marker set by the graph task.

        Consolidates the C2-safe deferred-termination cascade that used to be
        duplicated at the ``send_message`` and ``_process_message_with_tracking``
        ``finally`` blocks. The ``watchover_terminate_node`` graph node ran
        INSIDE the graph task and set a marker via
        ``slot.set_deferred_terminate(instance_id)`` rather than calling
        ``terminate_instance`` directly (to avoid the self-cancel / torn-state
        bug). The cascade runs from this post-graph completion path AFTER
        ``_graph_tasks`` is popped — at that point there is no graph task to
        self-cancel, so the DB write proceeds cleanly.

        ``terminal_reason="watchover_terminated"`` threads through
        ``terminate_instance`` to the JobItem ``terminal_reason`` column
        (TD-3/TD-4) so the work API surfaces the watchover reason via
        ``canonicalize_status`` rather than the generic ``"aborted"``.

        SHIELDED against double-cancel: a second ``task.cancel()`` arriving
        during the ``await`` would raise ``CancelledError`` (a
        ``BaseException`` in 3.8+, NOT caught by ``except Exception``).
        ``asyncio.shield`` protects the DB write so a transient cancel during
        the termination cascade does not corrupt instance state.

        H2 retry-on-failure: the marker is ONLY cleared on a successful
        termination. If ``terminate_instance`` raises, the marker is
        preserved so the next post-graph completion (next message) will
        retry the cascade. The persistent DB marker
        ``instance_metadata.watchover_pending_termination`` also remains
        set for crash recovery. Re-terminating an already-TERMINATED
        instance is a no-op (the re-entrancy guard at the top of
        ``terminate_instance`` filters terminal rows), so a residual RAM
        marker on top of an external termination is harmless.

        Args:
            instance_id: Owning instance identifier.
        """
        if not self._manager.is_watchover_terminate_requested(instance_id):
            return
        _term_ok = False
        try:
            await asyncio.shield(
                self._manager.terminate_instance(
                    instance_id,
                    terminal_reason="watchover_terminated",
                )
            )
            _term_ok = True
        except Exception as term_err:
            logger.warning(
                f"[watchover_drain] deferred watchover termination "
                f"failed for {instance_id[:8]}...: "
                f"{type(term_err).__name__}: {term_err} — "
                f"marker preserved for retry on next post-graph completion"
            )
            # Marker is NOT cleared (H2): the next post-graph completion
            # will retry the termination. The persistent DB marker
            # (instance_metadata.watchover_pending_termination) also
            # remains set for crash-recovery.
        finally:
            if _term_ok:
                self._manager.clear_watchover_terminate_requested(instance_id)

    async def _drain_pending_system_executions(self, instance_id: str) -> None:
        """Drain the deferred system-execution marker (P2.2 Dispatch B, D-FA1.4).

        Mirrors the C2-safe deferred pattern of
        ``_drain_deferred_watchover_terminate`` above: the actor tools
        (``system_restart`` / ``system_upgrade``) set an in-memory marker at
        arm time (the DURABLE state is the journal ``pending_op``); this
        post-graph completion path — running OUTSIDE the task identity
        guard, AFTER ``_graph_tasks`` was popped — pops the marker and fires
        the daemonized executor at EXACT turn-end. ``asyncio.shield``'d so a
        transient cancel during the spawn cannot corrupt anything; never
        raises (a failed drain leaves the journal pending_op as the
        durable fallback — bounded waiter / boot sweep).
        """
        marker = getattr(self._manager, "_pending_system_executions", None)
        drain = getattr(self._manager, "drain_pending_system_execution", None)
        if not isinstance(marker, dict) or instance_id not in marker:
            return
        try:
            await asyncio.shield(drain(instance_id))
        except Exception as exc:
            logger.warning(
                f"[send_message] deferred system-execution drain "
                f"failed for {instance_id[:8]}...: {type(exc).__name__}: {exc}"
            )


    # wc-wake-report-integrity (T6b, D7 LOCKED 2026-08-30): the legacy
    # ``InstanceMessagingService.send_message`` method — the
    # ``graph.ainvoke`` bypass — was DELETED (it never shipped in
    # production); keeping it would have re-opened the poisoned-tail →
    # LangGraph 2013 exposure that the D1 enqueue-seam guard (T6)
    # closes. There is NO replacement bypass: production callers must
    # use ``enqueue_message`` (durable wake) or the FIFO
    # ``set_injection`` API (mid-turn injections on RUNNING targets).

    def _prepare_enqueued_message(
        self,
        instance_id: str,
        message: str,
        source: str,
        priority: int,
        images: list[str] | None,
        metadata: dict[str, Any] | None,
        *,
        path_label: str = "",
        is_deferred: bool = False,
        is_background: bool = False,
        work_id: str | None = None,
        work_id_required: bool = False,
    ) -> _PreparedEnqueueContext:
        """Shared prelude for ``enqueue_message``.

        Writes the atomic MessageQueue + Task + Event trio that every
        message enqueue needs:

        - Reject messages during shutdown.
        - Resolve ``msg_type`` from the ``source`` prefix and mint a UUID.
        - Insert the ``MessageQueue`` row **unconditionally** (always
          preserved as a durable audit / record).
        - Insert the ``Task`` row **conditionally** — gated by the
          deferred-pause marker guard. When the marker is set (the
          instance is mid-deferred-pause and the cascade's DB commit is
          still in flight), the ``Task`` row is **skipped** to prevent
          ``WorkerPool.claim_pending_task`` from claiming a spurious
          graph turn during the cascade window. The ``MessageQueue``
          row is intentionally preserved as a durable audit record in
          that case (the marker branch always commits the message but
          no Task — see Phase 2 below).
        - Auto-resume ``IDLE`` / ``WAITING_CHILDREN`` / ``COMPLETED`` instances
          to ``RUNNING`` and bump ``last_activity_at`` / ``version``.
        - Append a ``MESSAGE_RECEIVED`` event for event-sourced features.
        - Commit the session.

        **Phase 2 asymmetry** (C2 torn-state / deferred-pause race
        guard, 2026-07): the ``MessageQueue`` row is always created;
        the ``Task`` row is gated by the deferred-pause marker guard
        (skipped when the marker is set). This is intentionally
        asymmetric with the Phase 1 ``child_reports`` guard, which
        checks marker OR ``DB=PAUSED`` — the Phase 2 guard is
        marker-only because ``MessageQueue`` rows have no resume drain
        (``cleanup`` excludes READY rows, so a DB=PAUSED skip would
        orphan an otherwise deliverable message). When the marker
        branch fires:

          * ``ctx.task_id`` is set to ``None`` (callers detect this and
            skip the downstream JobItem creation in
            ``enqueue_message_job`` — see W7 fix).
          * The message in the narrow race window may be lost
            (a known limitation; the durable follow-up is to
            materialize the marker in the DB as
            ``instances.pause_pending`` so Task creation and SQL
            claiming can coexist).

        Args:
            path_label: Optional identifier appended to the "Reactivating
                completed instance" log message. Empty string omits the
                suffix.
            is_deferred: Phase 3 Part B1 (2026-06-27) defer-queue marker.
                When True, the created Task row is stamped
                ``is_deferred=True`` and the worker pool's idle gate
                will hold the task until every non-defer queue is
                empty. Default False preserves the prior behaviour for
                every caller that does not explicitly opt in.
            is_background: Background-queue lane marker. When True, the
                created Task row is stamped ``is_background=True`` so the
                dispatcher routes the work onto the background queue
                instead of the foreground message lane. Default False
                preserves the prior behaviour for every caller that does
                not explicitly opt in (HTTP route, telegram, scheduler,
                internal reports). Independent of ``is_deferred`` — a
                task may be either, both, or neither.

        Returns:
            ``_PreparedEnqueueContext`` carrying the values callers need to
            proceed with dispatch (SSE emit, title generation, WorkerPool
            notify). ``ctx.task_id`` is ``None`` when the marker branch
            fired; ``ctx.message_id`` is always populated.
        """
        # Reject new messages during shutdown
        if self._cancellation_service.is_shutting_down:
            raise RuntimeError("Manager is shutting down, cannot accept new messages")

        # Determine message type based on source
        if source.startswith("internal_report:"):
            msg_type = MessageType.COMPLETION_REPORT.value
            # System-generated reports use random IDs (not user messages)
            message_id = str(uuid.uuid4())
        elif source.startswith("internal_error_report:"):
            msg_type = MessageType.ERROR_REPORT.value
            # System-generated errors use random IDs (not user messages)
            message_id = str(uuid.uuid4())
        elif source.startswith("internal_agent:"):
            msg_type = MessageType.AGENT.value
            # Agent-to-agent messages use random IDs
            message_id = str(uuid.uuid4())
        else:
            msg_type = MessageType.HUMAN.value
            # User messages use UUID IDs
            message_id = str(uuid.uuid4())

        # Log image count if images are provided
        if images:
            logger.info(f"Processing message with {len(images)} image(s)")

        status_changed_to_running = False
        is_idle_to_running = False
        instance_agent_id: str | None = None
        previous_status: str | None = None
        # A deferred-pause race guard may intentionally omit the Task while
        # preserving the MessageQueue audit row. ``task_id`` is otherwise the
        # Task primary key (int | None); callers already treat None as "no
        # resolvable task id available".
        task_id: int | None = None
        # Virtual Job Management Surface (Phase 1, Batch 3,
        # 2026-06-27): capture ``Task.work_id`` alongside ``task.id``.
        # The Task model's ``work_id`` column has a ``default_factory``
        # that mints a UUID4 at construction, so the value is available
        # immediately after ``session.add(task)`` — no DB round-trip
        # needed to read it (unlike ``task.id``, which requires the
        # post-commit ``refresh()``).
        #
        # Linkage contract (POC ``enqueue_message_job`` path):
        # ``JobItem.job_id`` MUST equal ``Task.work_id``. The caller
        # (``enqueue_message_job``) mints a single UUID and passes it
        # here as ``work_id``; we forward it to the Task row so the
        # two rows share one handle. Legacy callers (``enqueue_message``)
        # pass ``work_id=None``; we mint a UUID here so the Task row
        # has a non-null ``work_id`` regardless of path. Place the
        # auto-generation ONCE, early, and do NOT re-bind ``work_id``
        # later — a bare ``work_id: str | None = None`` re-declaration
        # elsewhere in the method would shadow the parameter, dropping
        # the caller's value on the floor and breaking the linkage.
        #
        # Fix A (constitution Phase 0, approach-comparison.md row A):
        # when ``work_id_required=True`` (job-driven path), a ``None``
        # work_id is no longer allowed — auto-minting a fresh UUID on
        # the job-driven path would re-key the Task and break Pattern-f1
        # ``get_by_work_id`` recovery lookups (the 2026-08-31 incident).
        # Raise loudly instead. Internal paths (agent-to-agent
        # send_message, cascade-resume, child reports — no JobItem)
        # legitimately self-mint and call with the default
        # ``work_id_required=False``. The decision lives in the
        # module-level pure helper ``_ensure_work_id_fail_closed`` —
        # directly unit-testable without a DB session.
        work_id = _ensure_work_id_fail_closed(work_id, work_id_required)

        with WriteGuardSession(Session(self._manager.engine), self._manager.write_guard) as session:
            # 1. Insert the message
            db_message = MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content=message,
                source=source,
                type=msg_type,
                status=MessageStatus.READY.value,
                priority=priority,
                images=images,
                message_metadata=metadata or {},
                enqueued_at=datetime.now(timezone.utc),
            )
            session.add(db_message)

            # C2 TORN-STATE / DEFERRED-PAUSE RACE GUARD (Phase 2).
            # Pausing inside the active graph task can self-cancel the cascade
            # mid-transaction, so set_deferred_question_pause first records the
            # in-memory marker and lets question_pause_node reach graph END.
            # _pause_cascade_db_sync commits status=PAUSED only afterward, leaving
            # a narrow window where the marker is set while the DB still says RUNNING.
            # Creating PROCESS_MESSAGE in that window lets WorkerPool claim a
            # spurious graph turn before the pause commit, reproducing the C2 race.
            # This guard is intentionally MARKER-ONLY, not marker-or-DB-status.
            # READY MessageQueue rows have no resume drain: cleanup excludes READY,
            # so skipping on DB=PAUSED would orphan an otherwise deliverable message.
            # With marker empty and DB=PAUSED we still create the PENDING Task and
            # rely on claim_pending_task's SQL pause gate to hold it until resume.
            # The verified root-resume path bypasses enqueue_message and passes the
            # answer as a fresh message parameter at manager.py:5155; child resume
            # calls enqueue_message with a fresh UUID. Neither consumes this READY row.
            # Thus an in-window skipped row is retained only as a stale audit record;
            # the narrow-window message may be lost, which is a known limitation.
            # The durable follow-up is to materialize this marker in the DB as
            # instances.pause_pending so Task creation and SQL claiming can coexist.
            # Compare Phase 1 in child_reports.py:_process_child_completion_db_sync:
            # that guard deliberately checks marker OR DB=PAUSED because its
            # ReportInjection fallback is drained on every LLM call. Phase 2 is
            # explicitly asymmetric because MessageQueue has no equivalent drain.
            #
            # **Marker lifetime (C1 fix, 2026-07)**: the marker is set in
            # ``question_pause_node``, **peeked** in the post-graph completion
            # path via ``has_deferred_question_pause`` BEFORE awaiting
            # ``pause_instance_cascade``, and **popped** in the inner
            # ``finally`` block AFTER the cascade's DB commit completes. This
            # guard depends on that ordering: if the marker were popped
            # BEFORE the cascade, the guard would see ``marker=False,
            # db=RUNNING`` during the cascade's DB-commit window and CREATE a
            # spurious PROCESS_MESSAGE Task.
            instance_for_pause_guard = session.get(Instance, instance_id)
            deferred_pause_marker_set = (
                instance_id in self._manager._deferred_question_pause
            )
            task: Task | None = None

            # 2. Insert the Task row in the same transaction as the
            #    MessageQueue row unless the in-window marker guard fires.
            #    The structural D13 fix makes Task the dispatch primitive;
            #    preserving MessageQueue while skipping Task prevents the
            #    about-to-be-paused instance from being claimed.
            #
            #    ``is_deferred`` (Phase 3 Part B1, 2026-06-27) is
            #    stamped at creation time so the defer-queue idle gate
            #    can recognise the row without a follow-up UPDATE.
            #    Default False matches every pre-existing caller; the
            #    orchestrator opts in via ``enqueue_message``.
            if deferred_pause_marker_set:
                logger.warning(
                    f"instance_messaging: SKIPPING PROCESS_MESSAGE Task creation "
                    f"for instance {instance_id[:8]}... — reason=marker "
                    f"(in-window race); MessageQueue row preserved as audit/record; "
                    f"KNOWN LIMITATION: message in narrow race window may not be "
                    f"delivered on resume. Follow-up: materialize "
                    f"_deferred_question_pause to DB (instances.pause_pending)."
                )
            else:
                task = Task(
                    task_type=TaskType.PROCESS_MESSAGE.value,
                    instance_id=instance_id,
                    message_id=message_id,
                    status=TaskStatus.PENDING.value,
                    created_at=datetime.now(timezone.utc),
                    is_deferred=is_deferred,
                    is_background=is_background,
                    # ``work_id`` is the linkage handle for the
                    # JobItem/Task pair (POC path) or a fresh UUID minted
                    # earlier in this method (legacy path). Always non-None
                    # at this point — see the early auto-generation
                    # immediately above this block. Passing it explicitly
                    # ensures ``task.work_id`` matches the value the caller
                    # intended (``enqueue_message_job``'s shared UUID); if
                    # we relied on ``default_factory`` alone the Task row
                    # would mint an unrelated UUID and the linkage contract
                    # (JobItem.job_id == Task.work_id) would be silently
                    # broken.
                    work_id=work_id,
                )
                session.add(task)
                if (
                    instance_for_pause_guard is not None
                    and instance_for_pause_guard.status
                    == InstanceStatus.PAUSED.value
                ):
                    logger.info(
                        f"instance_messaging: PROCESS_MESSAGE Task created for "
                        f"instance {instance_id[:8]}... with DB=PAUSED; relying "
                        f"on claim_pending_task SQL gate to defer until resume"
                    )
            # ``task.work_id`` was either inherited from the caller
            # (``enqueue_message_job``'s shared UUID, satisfying the
            # linkage contract with JobItem.job_id) or minted above.
            # No re-capture is needed; the local ``work_id`` already holds
            # the correct value even when the marker intentionally skips Task.

            # 3. Update instance status to RUNNING for any state that is
            #    NOT already RUNNING and NOT PAUSED. A terminal instance
            #    (COMPLETED / TERMINATED / ERROR / FAILED) is reactivated on
            #    a new message — "terminal" only records WHY the last run
            #    stopped; the checkpoint, message history, and LangGraph
            #    thread all persist in the DB and reload on the next
            #    graph.astream, so reviving a terminated instance is the
            #    same machinery as reviving a completed one (revive-fix,
            #    2026-07-01). PAUSED is intentionally excluded here — the
            #    messages endpoint routes pause through the explicit resume
            #    path; enqueue itself must not flip PAUSED so the
            #    cooperative pause gate (claim_pending_task excludes paused
            #    instances) and the resume cascade stay in control.
            instance = session.get(Instance, instance_id)
            if instance:
                instance_agent_id = instance.agent_id
                previous_status = instance.status
                is_terminal_revival = previous_status in (
                    InstanceStatus.COMPLETED.value,
                    InstanceStatus.TERMINATED.value,
                    InstanceStatus.ERROR.value,
                    InstanceStatus.FAILED.value,
                )
                if instance.status in (
                    InstanceStatus.IDLE.value,
                    InstanceStatus.WAITING_CHILDREN.value,
                ) or is_terminal_revival:
                    instance.status = InstanceStatus.RUNNING.value
                    status_changed_to_running = True
                    is_idle_to_running = previous_status == InstanceStatus.IDLE.value
                    if is_terminal_revival:
                        suffix = f" ({path_label})" if path_label else ""
                        logger.info(
                            f"Reactivating terminal instance {instance_id[:8]}... "
                            f"(was {previous_status}) for new message{suffix}"
                        )
                instance.last_activity_at = datetime.now(timezone.utc)
                instance.version = (instance.version or 1) + 1
            else:
                logger.warning(
                    f"Instance {instance_id} not found in database during message "
                    f"enqueue. This may indicate the instance was not properly persisted."
                )

            # 4. Create MESSAGE_RECEIVED event for event-sourced features
            role = "system" if msg_type == MessageType.SYSTEM.value else "user"
            message_data = {
                "message_id": message_id,
                "role": role,
                "content": message,
                "source": source,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            event = Event(
                instance_id=instance_id,
                message_id=message_id,
                kind=EventKind.MESSAGE_RECEIVED.value,
                data=json.dumps(message_data),
                created_at=datetime.now(timezone.utc),
            )
            session.add(event)

            session.commit()
            # Capture the Task PK after commit + refresh so the caller
            # can surface it as ``AsyncMessageResult.job_id``. The marker
            # branch intentionally has no Task and leaves task_id=None.
            if task is not None:
                # ``task.id`` is populated by the autoincrement; refresh()
                # re-reads the row from the DB to pick it up.
                try:
                    session.refresh(task)
                    task_id = task.id
                except Exception as e:
                    # Should not happen — the insert succeeded (we're past
                    # commit). Log and continue with None so callers degrade
                    # gracefully (HTTP route doesn't read task_id).
                    logger.warning(
                        f"Failed to refresh Task row for message {message_id}: {e}"
                    )
                    task_id = None

        return _PreparedEnqueueContext(
            message_id=message_id,
            msg_type=msg_type,
            status_changed_to_running=status_changed_to_running,
            is_idle_to_running=is_idle_to_running,
            instance_agent_id=instance_agent_id,
            previous_status=previous_status,
            task_id=task_id,
            work_id=work_id,
            is_deferred=is_deferred,
        )

    async def enqueue_message(
        self,
        instance_id: str,
        message: str,
        source: str = "api",
        priority: int = 1,
        images: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        is_deferred: bool = False,
        is_background: bool = False,
        work_id: str | None = None,
        work_id_required: bool = False,
    ) -> "AsyncMessageResult":
        """Enqueue a message via the unified dispatcher.

        All messages flow through the same single dispatcher:

          1. ``_prepare_enqueued_message`` writes ``MessageQueue`` + ``Task``
             rows in a single transaction.
          2. ``worker_pool.notify_work()`` wakes a worker to claim the Task.

        No ``JobItem`` (job_queue_items) row is ever created for a message.
        This eliminates the dual-record coupling that caused the
        06f500af-class bugs.

        ``AsyncMessageResult.job_id`` is set to ``task.work_id`` (the
        stable cross-system UUID4 handle introduced in Phase 1, Batch
        3 of ``feature/virtual-job-management-surface``). The HTTP
        route discards ``job_id``; the ``job_continue`` tool returns it
        as ``new_job_id``. This supersedes the prior ``str(task_id)``
        adapter — the int PK was a stop-gap until ``work_id`` was
        added; the resolver now resolves ``work_id`` uniformly across
        ``task`` and ``job_queue_items``.

        ``is_deferred`` (Phase 3 Part B1, 2026-06-27): keyword-only
        marker that stamps the created Task row with
        ``Task.is_deferred=True``. The worker pool's idle gate holds
        the task until every non-defer queue is empty. Default False
        preserves the prior behaviour for every caller that does not
        opt in (HTTP route, telegram, scheduler, internal reports).
        Keyword-only on purpose — it is a forward-looking orchestrator
        affordance and threading it positionally would silently
        re-route existing traffic if a caller miscounted args.

        New-message-during-pause behaviour:

            When this method is called for a PAUSED instance, the
            ``_prepare_enqueued_message`` helper writes a fresh ``Task``
            row in PENDING status. The pause-gate in
            ``TaskRepository.claim_pending_task`` excludes PAUSED instances
            from worker claim. INTENDED BEHAVIOUR: messages queue in
            PENDING and are claimed the moment the instance resumes.
        """
        # Wrap the sync DB prelude in asyncio.to_thread so the session.commit()
        # inside `_prepare_enqueued_message` cannot block the event loop. Under
        # SQLite WAL write contention (busy_timeout=30s) a sync commit on the
        # event loop thread would wedge the loop completely — Ctrl+C ignored,
        # all APIs frozen. See the deadlock analysis in the experience docs.
        ctx = await asyncio.to_thread(
            self._prepare_enqueued_message,
            instance_id=instance_id,
            message=message,
            source=source,
            priority=priority,
            images=images,
            metadata=metadata,
            is_deferred=is_deferred,
            is_background=is_background,
            work_id=work_id,
            work_id_required=work_id_required,
        )

        # Phase 5 (Option B): when this message is being delivered via
        # the JobProcessor's message branch, ``ctx.work_id`` is the
        # shared UUID linking the Task ↔ JobItem. Stamp the
        # ``message_id`` onto the JobItem mirror so the cross-system
        # guard in ``claim_pending_task`` can correlate active MESSAGE
        # JobItems with their ``message_queue`` row. Failure is
        # non-fatal (same pattern as JobProcessor L1059-1069).
        if ctx.work_id:
            try:
                await asyncio.to_thread(
                    self._manager._job_queue_service._repository.stamp_message_id,
                    ctx.work_id,
                    ctx.message_id,
                )
            except Exception:
                logger.debug(
                    f"enqueue_message: stamp_message_id failed for "
                    f"work_id={ctx.work_id[:8]}...",
                    exc_info=True,
                )

        # Emit status_change event if status was changed to running
        if ctx.status_changed_to_running:
            await self._manager._live_hub.stream_status_change(
                instance_id, InstanceStatus.RUNNING.value, agent_id=ctx.instance_agent_id
            )

        # Trigger title generation for first message (fire-and-forget)
        # This fires when instance transitions from IDLE -> RUNNING with any message type
        self._maybe_trigger_title_generation(
            instance_id, message, ctx.is_idle_to_running
        )

        # Unified dispatch: notify the WorkerPool (Task row was already
        # written in the prelude, in the same transaction as the
        # MessageQueue row). No path-specific branch — the legacy
        # ``_job_queue_service.enqueue()`` call was eliminated in D13.
        if self._manager._worker_pool is not None:
            self._manager._worker_pool.notify_work()

        # ``job_id`` payload: ``task.work_id`` (UUID4) is the stable
        # cross-system handle minted by the Task model's
        # ``default_factory``. The HTTP ``send_message`` route discards
        # ``job_id`` entirely; the ``job_continue`` tool surfaces it as
        # ``new_job_id`` to the calling agent — both work because the
        # UUID4 is universally unique and the resolver
        # (``daemon.services.work_resolver``) accepts it on both the
        # ``task`` and ``job_queue_items`` sides of the union.
        # ``work_id`` is always populated by the Task model's
        # ``default_factory`` (NOT NULL on the column), so no fallback
        # is needed.
        job_id = ctx.work_id

        logger.debug(
            f"Enqueued message {ctx.message_id} for instance {instance_id} "
            f"task_id={job_id}"
        )

        return AsyncMessageResult(
            message_id=ctx.message_id,
            instance_id=instance_id,
            status="queued",
            job_id=job_id,
        )

    @property
    def _job_repository(self) -> Any:
        """Access JobRepository through the manager's JobQueueService.

        Resolves to ``manager._job_queue_service._repository``. Returns
        ``None`` when the JobQueueService has not been wired yet (test
        fixtures that build ``InstanceManager`` directly without
        ``api.py`` lifespan). Callers MUST handle ``None`` gracefully —
        the POC ``enqueue_message_job`` skips JobItem creation when the
        repo is unavailable so the legacy ``enqueue_message`` path
        remains the fallback.
        """
        try:
            service = self._manager._job_queue_service
        except AttributeError:
            return None
        if service is None:
            return None
        return getattr(service, "_repository", None)

    async def enqueue_message_job(
        self,
        instance_id: str,
        message: str,
        source: str = "api",
        priority: int = 1,
        images: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        is_deferred: bool = False,
        is_background: bool = False,
        queue_id: str | None = None,
    ) -> "AsyncMessageResult":
        """Submit a message to the queue as a JobItem (Option B).

        Option B (synchronous Task contract): the ``MessageQueue`` and
        ``Task`` rows are created synchronously (via
        :meth:`_prepare_enqueued_message`) BEFORE the JobItem is
        enqueued, so the HTTP response can carry a real ``message_id``
        immediately. The ``JobProcessor._process_next_job`` message
        branch is then reduced to a wake-only step — it just calls
        ``worker_pool.notify_work()`` to surface the already-existing
        Task to a worker thread (the Task is created in PENDING by
        ``_prepare_enqueued_message`` and the worker pool's claim path
        picks it up).

        Architecture:
            1. Resolve the target instance's project_id + queue_id
               (cross-project guard, default ``system_parallel_queue``).
            2. Mint one UUID and call
               ``_prepare_enqueued_message(work_id=job_id, ...)`` to write
               the ``MessageQueue`` + ``Task`` rows in one transaction.
            3. Restore the synchronous RUNNING SSE and first-message title
               side effects after the transaction commits.
            4. Call ``JobQueueService.enqueue(job_type='message',
               instance_id=instance_id, job_id=job_id, ...)``. This creates
               the JobItem with the exact shared UUID and only then emits
               ``dispatch_bus.notify_new_job()`` to wake the
               ``JobProcessor``.
            5. Stamp the authoritative ``message_id`` onto the JobItem and
               return ``AsyncMessageResult`` with the real message ID,
               ``status='queued'``, and ``job_id == Task.work_id``.

        Concurrency: ``concurrency_limit`` on the resolved queue is
        NOW ENFORCED for messages. With a FIFO queue at
        ``concurrency_limit=1``, two messages to the same/different
        instances run strictly serially.

        Failure handling:
            * Any exception while resolving the queue or creating the Task
              propagates before a JobItem is visible to the dispatch bus.
            * If JobItem creation fails after the Task transaction commits,
              the Task remains the authoritative work item and the caller
              receives the enqueue error for normal recovery handling.

        Args:
            instance_id: Target instance ID (the existing instance that
                will receive the message).
            message: User content.
            source: Source tag (e.g. ``"api"``, ``"telegram:user:1"``).
            priority: 0=system, 1=user.
            images: Optional base64 images for vision messages.
            metadata: Optional metadata dict.
            is_deferred: Stamps ``Task.is_deferred=True`` on the
                created Task row so the worker pool's idle gate holds
                the task until every non-defer queue is empty.
            is_background: Stamps ``Task.is_background=True`` on the
                created Task row for background-queue routing.
            queue_id: Optional queue override. Validated against the
                target project; falls back to ``system_parallel_queue``
                on mismatch.

        Returns:
            ``AsyncMessageResult`` with the real ``message_id`` (Task
            row's column), ``instance_id=instance_id``,
            ``status='queued'`` (waiting for slot), and ``job_id``
            populated as the JobItem's UUID4 (== ``Task.work_id``).
        """
        # --- Step 1: Resolve queue_id (reuse existing logic) ---
        # We need the instance's project_id (authoritative
        # ``instances.project_id`` column — NOT the LLM-controllable
        # ``instance_metadata.project_id``) and the resolved queue_id
        # (cross-project guard, default ``system_parallel_queue``).

        # 1a. Resolve project_id from the instance row.
        project_id_for_job: str | None = None
        instance_meta = None
        raw_project_was_none = True
        try:
            instance_meta = await asyncio.to_thread(
                self._manager._instance_repository.get, instance_id
            )
            if instance_meta is not None:
                raw_project_id = instance_meta.project_id
                if raw_project_id is not None:
                    raw_project_was_none = False
                project_id_for_job = normalize_project_id(raw_project_id)
        except Exception as project_err:
            logger.debug(
                f"enqueue_message_job: failed to resolve project_id "
                f"for instance {instance_id[:8]}...: "
                f"{type(project_err).__name__}: {project_err}"
            )

        if project_id_for_job is None:
            logger.warning(
                "Instance %s has no project_id; queue routing will use default",
                instance_id,
            )

        # 1b. Resolve agent_id from the instance (for JobItem row).
        agent_id_for_job = (
            instance_meta.agent_id if instance_meta is not None else None
        ) or "default"

        # 1b'. Resolve agent_tag from the instance when available.
        # Older Instance rows may not have ``agent_tag`` set — use
        # ``getattr`` with a default of None so the registry falls
        # back to the base metadata in that case.
        agent_tag_for_job = (
            getattr(instance_meta, "agent_tag", None)
            if instance_meta is not None
            else None
        )

        # 1c. Resolve queue_id (cross-project guard).
        queue_id_for_job: str | None = None
        # FIX 1 (idle-gate deadlock, 2026-08-10): track the resolved
        # queue's ``queue_type`` so we can derive ``is_deferred`` /
        # ``is_background`` from the queue's lane AFTER resolution.
        # Without this, the flags flow straight from the caller into
        # the Task row, and a defer/background queue's task carries
        # the wrong flag — the idle-gate predicate then counts it as
        # non-deferred/non-background work and the queue deadlocks.
        resolved_queue_type: str | None = None
        queue_repo = getattr(
            getattr(self._manager, "_job_queue_service", None),
            "_queue_repo",
            None,
        )
        queue_id_supplied = bool(queue_id and queue_id.strip())
        if project_id_for_job is not None:
            if queue_repo is not None:
                try:
                    if queue_id_supplied:
                        try:
                            requested = await asyncio.to_thread(
                                queue_repo.get, queue_id
                            )
                        except Exception as get_err:
                            logger.warning(
                                "enqueue_message_job: queue_repo.get "
                                f"failed for queue_id={queue_id!r} "
                                f"on project {project_id_for_job}: "
                                f"{type(get_err).__name__}: {get_err}; "
                                "falling back to default "
                                "system_parallel_queue"
                            )
                            requested = None
                        if (
                            requested is not None
                            and getattr(requested, "project_id", None)
                            == project_id_for_job
                        ):
                            queue_id_for_job = requested.queue_id
                            # Capture the queue_type for the flag override
                            # below — see FIX 1 comment above.
                            resolved_queue_type = getattr(
                                requested, "queue_type", None
                            )
                        else:
                            mismatch_reason = (
                                "not_found_or_repo_error"
                                if requested is None
                                else "wrong_project"
                            )
                            logger.warning(
                                "enqueue_message_job: caller-supplied "
                                f"queue_id={queue_id!r} is invalid "
                                f"({mismatch_reason}) for project "
                                f"{project_id_for_job}; falling back "
                                "to default system_parallel_queue"
                            )
                    if queue_id_for_job is None:
                        try:
                            queue = await asyncio.to_thread(
                                queue_repo.get_by_name,
                                project_id_for_job,
                                "system_parallel_queue",
                            )
                        except Exception as by_name_err:
                            logger.warning(
                                "enqueue_message_job: queue_repo."
                                "get_by_name failed for project "
                                f"{project_id_for_job}: "
                                f"{type(by_name_err).__name__}: "
                                f"{by_name_err}; leaving queue_id "
                                "unset on the JobItem"
                            )
                            queue = None
                        if queue is not None:
                            queue_id_for_job = queue.queue_id
                            # Capture the queue_type for the flag override
                            # below — see FIX 1 comment above.
                            resolved_queue_type = getattr(
                                queue, "queue_type", None
                            )
                except Exception as queue_lookup_err:
                    logger.debug(
                        f"enqueue_message_job: unexpected error "
                        f"resolving queue_id for project "
                        f"{project_id_for_job}: "
                        f"{type(queue_lookup_err).__name__}: "
                        f"{queue_lookup_err}"
                    )

        # --- Step 2: Mint the shared linkage ID and create the Task +
        # MessageQueue rows FIRST. The JobItem must not be visible to the
        # dispatch bus until its authoritative Task already exists.
        job_id = str(uuid.uuid4())

        # FIX 1 (idle-gate deadlock, 2026-08-10): override
        # ``is_deferred`` / ``is_background`` from the resolved queue's
        # ``queue_type``. Queue type is the source of truth for the
        # task flags — without this override, a defer/background queue's
        # task carries the caller-supplied (often False) flag,
        # ``TaskRepository.has_active_non_deferred_work`` /
        # ``has_active_non_background_work`` counts it as conflicting
        # work, and the defer/background idle-gate in
        # ``JobProcessor._process_next_job`` skips the queue — the
        # queue's JobItems never get activated and the Task stays
        # PENDING forever (deadlock). Helper kept module-level so the
        # rule is unit-testable without a full manager stack.
        is_deferred, is_background = _derive_task_flags_from_queue_type(
            resolved_queue_type,
            is_deferred=is_deferred,
            is_background=is_background,
        )

        # ── Linkage contract (structurally-safe site) ──
        # ``enqueue_message_job`` mints the shared linkage UUID itself
        # (``job_id`` above) and binds it as ``work_id`` here, so the
        # Task is born linked to its JobItem; the
        # ``AsyncMessageResult.job_id`` handed back below is built from
        # the SAME local. There is no re-mint seam to trip over, so —
        # unlike the observer / JobProcessor dispatch sites (which
        # delegate the mint to ``_prepare_enqueued_message`` and carry
        # the shared ``_assert_linkage_contract`` tripwire) — no
        # post-hoc tripwire is possible or needed on this path.
        #
        # Fix A (constitution Phase 0, approach-comparison.md row A):
        # set ``work_id_required=True`` to formally close the auto-mint
        # fail-open handle (D4) on the job-driven path. The
        # ``work_id=job_id`` argument above is unconditionally
        # populated from the local UUID minted at the top of this
        # method, so the flag is a structural guarantee rather than a
        # behavioural change — it ensures a future maintainer cannot
        # accidentally remove the ``work_id=job_id`` binding without
        # tripping the fail-closed ``work_id`` guard.
        ctx = await asyncio.to_thread(
            self._prepare_enqueued_message,
            instance_id=instance_id,
            message=message,
            source=source,
            priority=priority,
            images=images,
            metadata=metadata,
            is_deferred=is_deferred,
            is_background=is_background,
            work_id=job_id,
            work_id_required=True,
        )

        # Preserve the historical synchronous side effects from the
        # enqueue path: publish the RUNNING transition and start title
        # generation only after the message transaction has committed.
        if ctx.status_changed_to_running:
            await self._manager._live_hub.stream_status_change(
                instance_id, InstanceStatus.RUNNING.value, agent_id=ctx.instance_agent_id
            )
        self._maybe_trigger_title_generation(
            instance_id, message, ctx.is_idle_to_running
        )

        # --- W7 FIX (orphaned JobItem guard, 2026-07) ---
        # When the Phase 2 marker guard in ``_prepare_enqueued_message``
        # fires, the ``MessageQueue`` row is created (durable audit
        # record) but the ``Task`` row is SKIPPED to prevent
        # ``WorkerPool.claim_pending_task`` from claiming a spurious graph
        # turn during the cascade's DB-commit window. ``ctx.task_id`` is
        # ``None`` in that branch. Without this guard, the JobItem
        # creation below would enqueue an item that has NO Task to
        # claim — the JobProcessor would wake the dispatch bus, try to
        # surface a Task that doesn't exist, and the work would be
        # silently lost.
        #
        # We log a WARNING (the same level used elsewhere in this path
        # for skip events) and skip both the JobItem creation and the
        # downstream ``queued`` snapshot / message_id stamp. The
        # ``MessageQueue`` row remains in READY state for later
        # inspection; the narrow-window message may be lost (a known
        # limitation tracked under the C2 follow-up).
        if ctx.task_id is None:
            logger.warning(
                f"enqueue_message_job: SKIPPING JobItem creation for "
                f"instance {instance_id[:8]}... — reason=marker_guard "
                f"(Phase 2 deferred-pause race guard skipped the Task "
                f"row in _prepare_enqueued_message; MessageQueue "
                f"{ctx.message_id[:8]}... preserved as audit record)"
            )
            return AsyncMessageResult(
                message_id=ctx.message_id,
                instance_id=instance_id,
                status="queued",
                job_id=job_id,
                queued=False,
            )

        # --- Step 3: Enqueue the JobItem using the exact same UUID. ---
        # JobQueueService.enqueue emits the dispatch-bus notification only
        # after this call returns, so the JobProcessor can never observe a
        # message JobItem before its Task + MessageQueue rows exist.
        await self._manager._job_queue_service.enqueue(
            agent_id=agent_id_for_job,
            message=message,
            source=source,
            project_id=project_id_for_job,
            priority=priority,
            metadata={
                **(metadata or {}),
                "images": images or [],
                "is_deferred": is_deferred,
                "is_background": is_background,
            },
            queue_id=queue_id_for_job,
            job_type="message",
            instance_id=instance_id,
            agent_tag=agent_tag_for_job,
            job_id=job_id,
        )

        # Snapshot queue capacity synchronously after the JobItem exists. The
        # newly-created item is still in the ``queued`` admission bucket, so it
        # is deliberately excluded from ``active_count``. This avoids relying
        # on the JobProcessor's later queued -> active claim timing.
        queued = False
        if queue_repo is None or queue_id_for_job is None:
            logger.warning(
                "enqueue_message_job: unable to snapshot queue capacity for "
                "job %s (queue repository or queue_id unavailable); "
                "defaulting queued=False",
                job_id[:8],
            )
        else:
            try:
                queue = await asyncio.to_thread(queue_repo.get, queue_id_for_job)
                concurrency_limit = (
                    getattr(queue, "concurrency_limit", None)
                    if queue is not None
                    else None
                )
                if concurrency_limit is None:
                    logger.warning(
                        "enqueue_message_job: queue %s missing or has no "
                        "concurrency_limit; defaulting queued=False for job %s",
                        queue_id_for_job,
                        job_id[:8],
                    )
                else:
                    admission_counts = await asyncio.to_thread(
                        queue_repo.count_jobs_by_admission,
                        queue_id_for_job,
                    )
                    active_count = int(admission_counts.get("active", 0))
                    queued = active_count >= int(concurrency_limit)
            except Exception as capacity_err:
                logger.warning(
                    "enqueue_message_job: failed to snapshot queue capacity "
                    "for queue %s and job %s: %s: %s; defaulting queued=False",
                    queue_id_for_job,
                    job_id[:8],
                    type(capacity_err).__name__,
                    capacity_err,
                )
                queued = False

        # Stamp the message_id onto the JobItem for cross-system correlation.
        # This remains best-effort for compatibility with the historical path:
        # Task + MessageQueue creation and queue admission have already succeeded.
        try:
            await asyncio.to_thread(
                self._manager._job_queue_service._repository.stamp_message_id,
                job_id,
                ctx.message_id,
            )
        except Exception:
            logger.debug(
                f"enqueue_message_job: stamp_message_id failed for job "
                f"{job_id[:8]}...",
                exc_info=True,
            )

        # Do not notify the WorkerPool here. The JobProcessor's message
        # branch is the wake-only handoff after queue slot admission;
        # waking here would bypass that gate.
        # W2 fix — project-less fallback. When the authoritative instance project
        # was None, JobQueueService.enqueue already called notify_new_job with
        # the normalized system project (which works today), but this belt-and-suspenders
        # fallback calls notify_all() to guarantee the wakeup reaches every known
        # project + the global event. Safe getattr keeps it inert when the bus
        # isn't wired (e.g. unit tests with a bare MagicMock manager).
        if raw_project_was_none:
            bus = getattr(
                getattr(self._manager, "_job_queue_service", None),
                "_dispatch_bus",
                None,
            )
            if bus is not None and hasattr(bus, "notify_all"):
                try:
                    bus.notify_all()
                except Exception as bus_err:
                    logger.debug(
                        f"enqueue_message_job: notify_all fallback failed "
                        f"for project-less instance {instance_id[:8]}...: "
                        f"{type(bus_err).__name__}: {bus_err}"
                    )
        return AsyncMessageResult(
            message_id=ctx.message_id,
            instance_id=instance_id,
            status="queued",
            job_id=job_id,
            queued=queued,
        )

    async def _process_message_with_tracking(
        self,
        instance_id: str,
        message: str,
        message_id: str,
        cancellation_token: CancellationToken | None = None,
        is_retry: bool = False,
        retry_count: int = 0,
        message_source: str | None = None,
        images: list[str] | None = None,
        silent: bool = False,
        task_context: str | None = None,
    ) -> "MessageResult":
        """Process message with activity tracking and cancellation support.

        On retry, resumes from checkpoint instead of re-sending message
        to prevent duplicate execution.

        Args:
            instance_id: The instance ID.
            message: The message content.
            message_id: The queue message ID.
            cancellation_token: Optional token to check for cancellation.
            is_retry: If True, attempt to resume from checkpoint.
            message_source: Source of the message (e.g., "agent:xxx", "api", "telegram:xxx").
            images: Optional list of base64-encoded images for multimodal content.
            silent: If True, resume from checkpoint without injecting any message.
            task_context: Optional pre-formatted ``[SYSTEM CONTEXT: Task Context]``
                markdown block passed by the parent's ``send_message(context=...)``
                tool call. Threaded through ``message_metadata`` →
                ``ProcessingContext.task_context`` → this kwarg. Injected as a
                persistent HumanMessage BEFORE the task message on first attempt
                (skipped on retry because the message is checkpointed on turn 1).

        Returns:
            MessageResult with response data.

        Raises:
            OperationCancelledError: If cancellation is requested.
        """
        from ..manager import MessageResult

        # ── <meta> tag parsing (parent-dispatch only) ────────────
        # Strip ``<meta>...</meta>`` control blocks ONLY when the
        # message came from a parent agent dispatching to this child
        # worker — i.e. ``message_source`` starts with
        # ``internal_agent:`` and is NOT a job-event ping
        # (``internal_agent:job_event:``). User / API / telegram /
        # ``internal_report:`` / ``internal_error_report:`` / None
        # all pass through untouched: stripping their tags would
        # leak control-plane syntax into the user-visible message
        # and create a hijack surface where a child LLM's stray
        # ``<meta>`` could mutate the parent's skill set.
        #
        # The carve-out mirrors the inverse of the C3
        # ``is_completion_report`` carve-out below — same prefixes,
        # opposite selection (parent dispatch vs. internal pings).
        # ``_meta_skill`` stays ``None`` for non-parent sources and
        # is consumed by the C3 block further down.
        _meta_skill: str | None = None
        _is_parent_dispatch = (
            message_source is not None
            and message_source.startswith("internal_agent:")
            and not message_source.startswith("internal_agent:job_event:")
        )
        if _is_parent_dispatch and message and isinstance(message, str):
            message, _meta = parse_meta_tag(message)
            _meta_skill = extract_load_skill(_meta)
            if _meta_skill is not None:
                logger.info(
                    f"[MetaTag] Extracted load_skill='{_meta_skill}' "
                    f"for instance {instance_id[:8]}..."
                )

        # Get instance graph (will lazy-load from DB if needed)
        # Note: get_instance() now handles MCP preload internally
        graph = await self._manager.get_instance(instance_id)
        
        # Create activity callback for this message - use repository for activity updates
        activity_callback = ActivityCallbackHandler(
            self._queue_repository, 
            message_id,
            update_interval_seconds=5.0
        )
        
        # Build callbacks list
        callbacks: list[BaseCallbackHandler] = [activity_callback]
        
        # Add cancellation callback if token provided
        if cancellation_token:
            # Check cancellation before starting
            cancellation_token.check()
            cancellation_callback = CancellationCallbackHandler(
                cancellation_token=cancellation_token
            )
            callbacks.append(cancellation_callback)
        
        config = {
            "configurable": {"thread_id": instance_id},
            "callbacks": callbacks,
            # Base recursion limit; overridden below once the agent
            # metadata is resolved so per-agent multipliers apply.
            "recursion_limit": self._config.limits.graph_recursion_limit,
        }
        
        # Variables for checkpoint-based streaming
        final_content = ""
        last_ai_message = None
        
        # Determine the effective source for progressive dispatch
        dispatch_source: str | None = None
        if message_source:
            # C1 fix: Only treat internal_report:* and internal_error_report:* as completion reports.
            # internal_agent:* is agent-to-agent communication, NOT a completion report,
            # so it must NOT trigger original_source lookup/replacement.
            is_internal_report = (
                message_source.startswith("internal_report:") or
                message_source.startswith("internal_error_report:")
            )
            # System-origin infrastructure messages (``system:*``,
            # e.g. the waiting-children watchdog's
            # ``system:watchdog`` hang notice) resolve their dispatch
            # source the SAME way internal reports do — look up the
            # instance's original external source — instead of
            # stamping themselves into ``original_source``. Without
            # this guard a watchdog notice to a parent that never had
            # an external message would (a) pollute
            # ``instances.instance_metadata.original_source`` with
            # ``system:watchdog`` and (b) send every progressive AI
            # chunk of the woken turn to the source dispatcher under
            # a bogus ``system:watchdog`` target (harmless no-op, but
            # log noise + wrong semantics). With it, a
            # telegram-originated parent still dispatches to telegram
            # after a watchdog wake, and an api-originated parent
            # simply has no external dispatch (``source`` without a
            # real adapter).
            is_system_origin = message_source.startswith("system:")
            if is_internal_report or is_system_origin:
                # This is an internal message (completion report, error report, etc.)
                # Retrieve the original external source from instance metadata.
                # Wrap the sync DB read in ``asyncio.to_thread`` to keep the
                # event loop responsive (see deadlock analysis in experience docs).
                instance_meta = await asyncio.to_thread(
                    self._manager._instance_repository.get, instance_id
                )
                if instance_meta is not None and instance_meta.instance_metadata is not None:
                    dispatch_source = instance_meta.instance_metadata.get("original_source")
                if not dispatch_source and is_internal_report:
                    # Missing original_source is only noteworthy for a
                    # genuine internal report (something external
                    # SHOULD have preceded it). A system-origin notice
                    # (``system:*``) to an instance with no external
                    # history simply has no external dispatch target —
                    # expected, not a warning.
                    logger.warning(
                        f"No original_source found for instance {instance_id[:8]}... "
                        f"(message_source={message_source})"
                    )
            else:
                # This is an external message - store as original source for future internal reports
                dispatch_source = message_source
                # Wrap the sync DB read in ``asyncio.to_thread`` for the same
                # event-loop responsiveness reason as above.
                instance_meta = await asyncio.to_thread(
                    self._manager._instance_repository.get, instance_id
                )
                if instance_meta is not None and instance_meta.instance_metadata is not None:
                    current = instance_meta.instance_metadata.get("original_source")
                    if not current and not message_source.startswith("internal_"):
                        logger.debug(f"[DISPATCH] storing original_source: instance={instance_id}, source={message_source}, current={current}")
                        # Sync DB write — wrap in ``asyncio.to_thread``.
                        await asyncio.to_thread(
                            self._manager._instance_repository.set_metadata,
                            instance_id, "original_source", message_source,
                        )
                    else:
                        logger.debug(f"[DISPATCH] original_source already set: instance={instance_id}, current={current}, skipping source={message_source}")
                else:
                    # Instance metadata doesn't exist yet, set it directly
                    if not message_source.startswith("internal_"):
                        logger.debug(f"[DISPATCH] storing original_source: instance={instance_id}, source={message_source}, current=None")
                        # Sync DB write — wrap in ``asyncio.to_thread``.
                        await asyncio.to_thread(
                            self._manager._instance_repository.set_metadata,
                            instance_id, "original_source", message_source,
                        )

        # Compute ``is_completion_report`` once at the top of the method.
        # It's consumed in two places:
        #   1. The project-context gate below (skip injection when an
        #      internal completion/error/agent report is the source).
        #   2. The meta-tag REPLACE gate further down (Fix 1+2) — must
        #      be in scope on both retry and non-retry paths because
        #      the REPLACE block runs unconditionally.
        is_completion_report = (
            message_source is not None and (
                message_source.startswith("internal_report:") or
                message_source.startswith("internal_error_report:") or
                message_source.startswith("internal_agent:job_event:")
            )
        )

        # Resolve the agent metadata ONCE at the top of the messaging
        # path so the ``assemble_context_messages()`` orchestrator call
        # further down has the versioned (vs base) agent metadata in
        # hand. The orchestrator consults it for the agent's skill
        # injection flags / team membership / tools allowlist — keeping
        # a single resolve here avoids re-resolving inside
        # ``agent_node`` and guarantees the versioned meta is honoured
        # for v2 / tagged agents.
        #
        # Cheap lookup — the registry is in-memory; only the registry
        # cache miss case hits disk. ``None`` is treated as the
        # default for any caller that doesn't have a resolvable
        # agent_id.
        #
        # Resolution reuses :meth:`_resolve_agent_meta_from_row` so the
        # versioned (``get_version`` → ``get_resolved``) fallback lives
        # in one place (S2 fix preserved by the helper).
        _messaging_agent_meta: Any | None = None
        try:
            _instance_row_for_meta = await asyncio.to_thread(
                self._manager._instance_repository.get, instance_id
            )
            _messaging_agent_meta = self._resolve_agent_meta_from_row(
                _instance_row_for_meta
            )
        except Exception as _meta_exc:  # pragma: no cover - defensive
            logger.debug(
                f"[Messaging] Failed to resolve agent_meta for "
                f"{instance_id[:8]}...: {_meta_exc}"
            )
            _messaging_agent_meta = None

        # Apply the per-agent recursion-limit override / multiplier now
        # that the agent metadata is resolved. ``config`` is not
        # consumed until the astream call below, so updating it here is
        # safe and lets long-running working agents (e.g. worker, coder)
        # exceed the global step quota. Reuses the already-resolved
        # ``_messaging_agent_meta`` (no second registry lookup).
        config["recursion_limit"] = self._resolve_recursion_limit_for_meta(
            _messaging_agent_meta
        )

        # ── Hybrid Context Injection (2026-07-29) ─────────────────────────
        # Capture the once-per-instance ``project_injected`` flag from
        # the DB BEFORE any of the project / shared-context injection
        # writes below flip it. The captured value drives
        # :func:`daemon.services.context_messages.assemble_context_messages`
        # later in this function: ``False`` ⇒ build the persistent
        # project + shared-context block and prepend it to
        # ``graph_input``; ``True`` ⇒ skip the persistent builders
        # (the orchestrator emits only ephemeral skills) because the
        # persistent block was already checkpointed on the first
        # turn.
        #
        # Read the DB once at the top — works for first attempt
        # (captures pre-injection state) and retry (captures the
        # post-first-attempt state, which is ``True`` on the happy
        # path). ``try/except`` guards against transient DB errors
        # so a failed lookup falls through to ``False`` — the safe
        # "build persistent" default. This single read is the only
        # extra DB call we add to the messaging path on the
        # steady-state hot path (every turn after the first the
        # orchestrator short-circuits, so the extra read is the cost
        # of correctness).
        project_already_injected = False
        # Whether a ``[SYSTEM CONTEXT: Auto-Load Skills]`` block is
        # currently checkpointed for this instance — gating the
        # ``<meta>`` REPLACE sweep (RemoveMessage) so it only targets an
        # id that actually exists (langgraph raises on an absent-id
        # RemoveMessage). Captured from the SAME instance-row read as
        # ``project_already_injected`` — no extra DB round-trip.
        auto_load_block_active = False
        try:
            _flag_row = await asyncio.to_thread(
                self._manager._instance_repository.get, instance_id
            )
            if _flag_row is not None and _flag_row.instance_metadata:
                project_already_injected = bool(
                    _flag_row.instance_metadata.get("project_injected")
                )
                auto_load_block_active = bool(
                    _flag_row.instance_metadata.get(AUTO_LOAD_BLOCK_ACTIVE_KEY)
                )
        except Exception as _flag_exc:  # pragma: no cover - defensive
            logger.debug(
                f"[Messaging] project_injected capture failed for "
                f"{instance_id[:8]}...: {_flag_exc}"
            )
            project_already_injected = False
            auto_load_block_active = False

        # Project context injection for first message only
        if not is_retry:
            if is_completion_report:
                # Skip project/shared-context injection for completion/error reports
                pass
            else:
                # ── Single read of the instance row (reused for both gates below) ──
                # Wrap the sync DB read in ``asyncio.to_thread`` (see deadlock
                # analysis in experience docs). Both the project-context and
                # shared-context gates read the same row, so one fetch covers
                # both — avoids the double round-trip a previous revision paid.
                instance_meta = await asyncio.to_thread(
                    self._manager._instance_repository.get, instance_id
                )
                # Snapshot for repeated gate checks (``instance_meta`` may be
                # detached after the next ``to_thread`` round-trip that writes
                # back to it via ``set_metadata``).
                instance_metadata = (
                    instance_meta.instance_metadata
                    if instance_meta is not None and instance_meta.instance_metadata
                    else None
                )

                # ── Project context injection (existing logic) ─────────────────
                # Context Injection Restructure: per-turn project + KV +
                # notes + history content is rebuilt inside ``agent_node``
                # by :func:`daemon.services.context_messages.assemble_context_messages`
                # → :func:`build_project_context_message`. The legacy
                # project-context body builder is no longer prepended
                # to the user message here — the per-turn orchestrator
                # owns the only delivery path.

                if not project_already_injected:
                    # First injection → attempt project injection
                    existing_project_id = None
                    if instance_metadata:
                        existing_project_id = instance_metadata.get("project_id")

                    injection_succeeded = False

                    if existing_project_id:
                        # project_id exists (inherited from parent) → record
                        # the linkage so ``assemble_context_messages`` can pick
                        # the project up per-turn. The actual project-context
                        # content is rebuilt inside ``agent_node`` by
                        # :func:`daemon.services.context_messages.assemble_context_messages`
                        # → :func:`build_project_context_message`.
                        matched_project = await asyncio.to_thread(
                            self._project_repository.get, existing_project_id
                        )
                        if matched_project:
                            injection_succeeded = True
                            logger.info(f"Project context injection: using stored project_id '{existing_project_id}' for instance {instance_id[:8]}...")
                    else:
                        # No project_id yet → extract keywords and try to match.
                        # ``extract_project_keywords`` and the
                        # ``match_by_keywords`` repository call are still
                        # required: they may stamp a project_id onto the
                        # instance metadata that the per-turn
                        # ``assemble_context_messages`` builder needs.
                        from ..manager import extract_project_keywords
                        keywords = extract_project_keywords(message)

                        if keywords:
                            # Wrap the sync ``match_by_keywords`` DB read in
                            # ``asyncio.to_thread``.
                            matched_project = await asyncio.to_thread(
                                self._project_repository.match_by_keywords, keywords
                            )

                            if matched_project:
                                # Log the match
                                logger.info(
                                    f"Project context injection: matched '{matched_project.name}' "
                                    f"from keywords: {keywords[:5]}..."
                                )

                                injection_succeeded = True

                                # Update instance metadata with project_id.
                                # Sync DB write — wrap in ``asyncio.to_thread``.
                                # Stamped on the instance metadata so
                                # ``assemble_context_messages`` can pick the
                                # project up per-turn. The project_id is
                                # the same gate the legacy prepend used —
                                # preserving it here keeps the matching
                                # behavior consistent.
                                await asyncio.to_thread(
                                    self._manager._instance_repository.set_metadata,
                                    instance_id, "project_id", matched_project.project_id,
                                )

                                logger.debug(f"Injected project context for instance {instance_id[:8]}...")
                    
                    # Mark as injected to prevent re-injection on subsequent messages.
                    # Sync DB write — wrap in ``asyncio.to_thread``.
                    if injection_succeeded:
                        await asyncio.to_thread(
                            self._manager._instance_repository.set_metadata,
                            instance_id, "project_injected", True,
                        )

                # ── Shared context metadata injection (Option C) ──────
                # The shared-context KV block is now part of the
                # ``[SYSTEM CONTEXT: Related Project]`` HumanMessage
                # built per-turn inside ``agent_node`` by
                # :func:`daemon.services.context_messages.assemble_context_messages`
                # → :func:`build_project_context_message` →
                # :func:`_format_kv_metadata_section`. Prepending the
                # KV block to the user message body here would
                # double-inject; the per-turn builder is the only
                # source of truth.
                #
                # The once-per-instance ``shared_context_injected``
                # flag (set by the legacy path) is now redundant — the
                # orchestrator reads ``project_injected`` instead and
                # the per-turn builder runs every turn. We leave the
                # metadata key in the schema for backward compatibility
                # with pre-existing rows but never write or read it
                # here.

            # ── Skill Injection (Phase 3: dynamic skill evolution) ──
            # Runs only on first attempt (``if not is_retry:`` above).
            # Skipped for completion reports — those are internal pings,
            # not real user messages, and the resolver doesn't need skill
            # context for them. Gated on ``agent_meta.skill_injection``
            # so opt-in agents control the cost of the search.
            #
            # The injection service is looked up via ``getattr(..., None)``
            # so a manager built without ``skill_evolution`` config (or
            # before Phase 3 wired it in) degrades to a no-op rather than
            # raising ``AttributeError``. The whole block is wrapped in
            # ``try/except`` so a transient DB / search error leaves the
            # user message path intact — the graph still runs with just
            # the bare ``content`` field.
            if not is_completion_report:
                try:
                    # Re-fetch instance metadata — by now the project
                    # injection block above may have stamped a new
                    # ``project_id`` onto it.
                    skill_instance_meta = await asyncio.to_thread(
                        self._manager._instance_repository.get, instance_id
                    )
                    if skill_instance_meta is not None:
                        from ..registry import get_registry
                        registry = get_registry()
                        # C1 fix: thread the instance's bound ``agent_tag``
                        # so the versioned (not base) meta's
                        # ``skill_injection`` flag wins for v2/etc.
                        # callers. Same ``get_version() or
                        # get_resolved()`` fallback pattern used by
                        # ``_apply_tool_filter`` and
                        # ``_check_team_membership``. ``getattr`` with
                        # ``None`` default keeps tests using
                        # ``SimpleNamespace``-style instance_meta
                        # compatible.
                        agent_meta = (
                            registry.get_version(
                                skill_instance_meta.agent_id,
                                getattr(
                                    skill_instance_meta, "agent_tag", None
                                ),
                            )
                            or registry.get_resolved(
                                skill_instance_meta.agent_id
                            )
                        )

                        if agent_meta and getattr(
                            agent_meta, "skill_injection", False
                        ):
                            # ── skill_search_interval gate ─────────────────
                            # Skip the expensive 3-stage search (BM25 →
                            # embedding → LLM, ~200-2000ms) when a cached
                            # result from a recent search still applies.
                            # ``interval == 1`` (default) = search every
                            # message (current behavior); ``N > 1`` =
                            # search every Nth message, reuse cached
                            # result in between. The first message ALWAYS
                            # searches (no cache yet → falls to ``else``).
                            #
                            # S1 perf: ``interval > 1`` is checked FIRST
                            # so ``get_context_skill_result`` and
                            # ``was_explicit_skill_loaded`` are skipped on
                            # the hot path (all default agents).
                            interval = int(
                                getattr(
                                    agent_meta, "skill_search_interval", 1
                                )
                                or 1
                            )
                            # Counter MUST be incremented unconditionally
                            # — even when ``interval == 1`` we want a
                            # consistent per-message tick for observability
                            # and for any future per-message hooks.
                            msg_count = (
                                self._manager
                                .get_and_increment_skill_search_count(
                                    instance_id
                                )
                            )
                            # Resolve ``skill_project_id`` ONCE above the
                            # gate — the same value feeds both the
                            # ``interval > 1`` else branch and the
                            # ``interval == 1`` hot path (no re-read of
                            # instance metadata).
                            skill_project_id: str | None = None
                            if skill_instance_meta.instance_metadata:
                                skill_project_id = (
                                    skill_instance_meta.instance_metadata.get(
                                        "project_id"
                                    )
                                )

                            async def _run_search_and_cache() -> None:
                                """Run the skill search and refresh caches.

                                Shared body of both gate branches
                                (``interval > 1`` miss + ``interval == 1``
                                hot path). Performs:

                                1. Clone-on-miss (Phase 4)
                                2. ``inject_skills`` via the
                                   ``_skill_injection_service``
                                3. ``set_context_skill_result`` (B2/B3)
                                4. ``track_injection`` + dedup-persist
                                5. ``reset_skill_search_count``
                                6. ``clear_explicit_skill_loaded`` (W1)

                                Idempotent reset calls are safe on every
                                invocation — the counter goes back to 0
                                and the explicit-load marker is cleared so
                                the next ordinary message can re-evaluate
                                the gate from a clean slate.
                                """
                                # ── Clone-on-miss (Phase 4) ──────────────
                                clone_service = getattr(
                                    self._manager,
                                    "_skill_clone_service",
                                    None,
                                )
                                if (
                                    clone_service is not None
                                    and skill_project_id is not None
                                ):
                                    try:
                                        await clone_service.ensure_all_skills_async(
                                            agent_id=skill_instance_meta.agent_id,
                                            project_id=skill_project_id,
                                        )
                                    except Exception as clone_exc:
                                        logger.warning(
                                            f"Clone-on-miss failed for "
                                            f"{instance_id[:8]}...: {clone_exc}"
                                        )

                                injection_service = getattr(
                                    self._manager,
                                    "_skill_injection_service",
                                    None,
                                )
                                if injection_service is not None:
                                    (
                                        injection_text,
                                        injected_skill_ids,
                                    ) = await injection_service.inject_skills(
                                        message,
                                        skill_project_id,
                                        instance_id,
                                        message_id,
                                    )
                                    if injection_text:
                                        # the metrics service queries this
                                        # to attribute future feedback to
                                        # the skills that were offered.
                                        injection_service.track_injection(
                                            instance_id,
                                            message_id,
                                            injected_skill_ids,
                                        )
                                        # Context Injection Restructure —
                                        # Phase 3 / B2 fix: store the
                                        # skill-search result on the manager
                                        # so ``ContextSlot.assemble()``
                                        # (running inside ``agent_node``)
                                        # can reuse it on retry without
                                        # re-running the search (B3 fix).
                                        # Stored unconditionally — context is
                                        # always built per-turn, so the cost
                                        # is one extra dict entry per message.
                                        setter = getattr(
                                            self._manager,
                                            "set_context_skill_result",
                                            None,
                                        )
                                        if setter is not None:
                                            setter(
                                                instance_id,
                                                (injection_text, injected_skill_ids),
                                            )
                                    else:
                                        # Search ran but yielded nothing.
                                        # Still store the empty result so a
                                        # retry of the same message does NOT
                                        # re-run the search (per B3). ``None``
                                        # here means "no injection text", not
                                        # "not searched" — the latter is the
                                        # absent-key case, which
                                        # ``assemble_context_messages`` treats
                                        # as "search again".
                                        setter = getattr(
                                            self._manager,
                                            "set_context_skill_result",
                                            None,
                                        )
                                        if setter is not None:
                                            setter(
                                                instance_id,
                                                (None, list(injected_skill_ids)),
                                            )
                                        # Persist injected skill IDs to instance
                                        # metadata so SkillMetricsService can
                                        # read them at task-completion time.
                                        if injected_skill_ids:
                                            try:
                                                await asyncio.to_thread(
                                                    _dedup_merge_skill_ids,
                                                    self._manager._instance_repository,
                                                    instance_id,
                                                    injected_skill_ids,
                                                )
                                            except Exception as e:
                                                logger.warning(
                                                    f"Failed to persist "
                                                    f"{INJECTED_SKILLS_METADATA_KEY} "
                                                    f"for {instance_id[:8]}...: {e}"
                                                )
                                # Reset the counter so the next
                                # ``interval`` messages can reuse the
                                # freshly-cached result. Called UNCONDITIONALLY
                                # at the end of the search branch — when the
                                # search throws, the message path's outer
                                # ``try/except`` still catches it, so the next
                                # message's gate will fall through to ``else``
                                # again (cached is None because the search
                                # didn't reach the ``set_context_skill_result``
                                # line).
                                self._manager.reset_skill_search_count(
                                    instance_id
                                )
                                # W1 fix: clear the explicit-load marker
                                # — this was a fresh AUTO-search, so the
                                # interval cache is valid again. A stale
                                # marker would force the next ordinary
                                # message to skip its own cache hit.
                                _clear_marker = getattr(
                                    self._manager,
                                    "clear_explicit_skill_loaded",
                                    None,
                                )
                                if _clear_marker is not None:
                                    _clear_marker(instance_id)

                            if interval > 1:
                                # Cached lookup + explicit-load check are
                                # only worth running when there's an actual
                                # interval to gate against.
                                cached = self._manager.get_context_skill_result(
                                    instance_id
                                )
                                if (
                                    cached is not None
                                    and msg_count < interval - 1
                                    and not self._manager.was_explicit_skill_loaded(
                                        instance_id
                                    )
                                ):
                                    # Reuse cached result — skip search.
                                    # The cached value stays in
                                    # ``_context_skill_results`` and is picked up
                                    # by ``assemble_context_messages`` at the
                                    # cache-read site (so the existing
                                    # ``skill_injection_result is not None``
                                    # reuse branch handles it automatically).
                                    #
                                    # W1 guard: ``was_explicit_skill_loaded``
                                    # forces a fresh search after an explicit
                                    # ``<meta>``-tag load, even when the
                                    # cache has a recent result.
                                    logger.debug(
                                        f"[SkillSearch] Reusing cached "
                                        f"result for {instance_id[:8]}... "
                                        f"(msg {msg_count + 1}, "
                                        f"interval={interval})"
                                    )
                                else:
                                    await _run_search_and_cache()
                            else:
                                # ``interval == 1`` (default for all agents
                                # without an explicit ``skill_search_interval``
                                # key in ``meta.json``). This is the hot path —
                                # always run a fresh search, no cache reuse,
                                # no explicit-load guard needed.
                                await _run_search_and_cache()
                except Exception as e:
                    logger.warning(
                        f"Skill injection failed for {instance_id[:8]}...: {e}"
                    )

        # ── C3 INVARIANT: Explicit <meta> injection runs FIRST (REPLACE
        # ── semantics). Auto_load DEDUP-MERGE runs SECOND (additive onto
        # ── the explicit set). This block is the explicit path — the
        # ── the auto_load side lives in ``instance_lifecycle.py`` and
        # ── honors the REPLACE by skipping any ``explicitly_replaced_ids``.
        #
        # Fix 1+2 gate: the REPLACE logic (skill injection + finalize
        # + metadata persist) is skipped when:
        #   * ``is_completion_report`` is True — a child agent's
        #     completion report that happens to contain a ``<meta>``
        #     tag must NOT hijack the parent instance's skill state.
        #   * ``is_retry`` is True — on retry the original message
        #     is re-processed with the same ``<meta>`` directive,
        #     which would create duplicate SUPERSEDED records.
        # The ``parse_meta_tag`` at the top of the method already
        # stripped ``<meta>...</meta>`` from ``message`` for parent
        # dispatches (``internal_agent:``-prefixed, non-job-event
        # sources). For other sources the message passes through
        # verbatim — including any literal ``<meta>...</meta>`` the
        # user typed — so ``_meta_skill`` stays ``None`` and this
        # entire C3 block is skipped naturally. Only the
        # authoritative REPLACE side-effects are gated.
        #
        # Key difference from the first-attempt block above: REPLACE
        # ``last_injected_skill_ids`` instead of dedup-merge. ``<meta>``
        # is the authoritative skill directive for this message and any
        # previously-injected skills that are NOT in the new set get a
        # ``SUPERSEDED`` usage record via ``finalize_superseded_skills``
        # so they stop skewing the completion-rate aggregation.
        # Declared here (before the meta block) so the REPLACE closure
        # can write via ``nonlocal`` and the persistent-context section
        # below can read it unconditionally — most messages have no
        # ``<meta>`` tag, so the variable must always be bound.
        _auto_load_sweep_agent_id: str | None = None
        if _meta_skill is not None:
            if is_completion_report or is_retry:
                logger.debug(
                    "Skipping <meta> tag REPLACE (completion_report=%s, "
                    "retry=%s) for instance %s",
                    is_completion_report, is_retry, instance_id[:8],
                )
            else:
                try:
                    # Resolve the instance row once for both project_id and
                    # agent_id below. Sync DB read — wrap in ``asyncio.to_thread``
                    # (same deadlock-avoidance pattern used elsewhere in
                    # this method).
                    _meta_instance = await asyncio.to_thread(
                        self._manager._instance_repository.get, instance_id
                    )
                    _meta_project_id: str | None = None
                    _meta_agent_id: str = (
                        getattr(_meta_instance, "agent_id", "") or ""
                        if _meta_instance is not None
                        else ""
                    )
                    if _meta_instance is not None and _meta_instance.instance_metadata:
                        _meta_project_id = (
                            _meta_instance.instance_metadata.get("project_id")
                        )

                    injection_service = getattr(
                        self._manager, "_skill_injection_service", None
                    )
                    # Pre-declare so the persist block below can read
                    # ``_meta_skill_ids`` even when the injection service
                    # isn't wired (older manager / pre-Phase 4 test
                    # fixtures).
                    _meta_skill_ids: list[str] = []
                    _meta_injection_text: str | None = None
                    # ``_auto_load_sweep_agent_id`` is declared above the
                    # meta block; the REPLACE closure sets it (nonlocal)
                    # when dropped skills invalidate the auto-load block.
                    if injection_service is not None:
                        (
                            _meta_injection_text,
                            _meta_skill_ids,
                        ) = await injection_service.inject_explicit_skill(
                            skill_name=_meta_skill,
                            project_id=_meta_project_id,
                            instance_id=instance_id,
                            message_id=message_id,
                            agent_id=_meta_agent_id,
                        )
                        # Context Injection Restructure — Phase 3 / Task 13:
                        # store the <meta>-tag skill result on the manager
                        # so ``ContextSlot.assemble()`` can rebuild the
                        # block with the unified ``[SYSTEM CONTEXT: Skills]``
                        # prefix in ``human_messages`` mode. Same pattern
                        # as the auto-search block above. Also store on
                        # the empty-text path so a retry of the same
                        # message does NOT re-run the explicit-skill
                        # resolver — mirrors the B3 short-circuit.
                        _meta_setter = getattr(
                            self._manager,
                            "set_context_skill_result",
                            None,
                        )
                        if _meta_setter is not None:
                            _meta_setter(
                                instance_id,
                                (
                                    _meta_injection_text,
                                    list(_meta_skill_ids),
                                ),
                            )
                        # W1 fix: mark this cache write as EXPLICIT
                        # (``<meta>``-tag ``load_skill``) so the
                        # ``skill_search_interval`` gate does NOT treat
                        # it as an auto-search result on the next
                        # ordinary message. Without this marker, an
                        # explicit load would feed the interval cache
                        # and the next ordinary message would silently
                        # reuse the explicit result.
                        _marker = getattr(
                            self._manager,
                            "mark_explicit_skill_loaded",
                            None,
                        )
                        if _marker is not None:
                            _marker(instance_id)
                        # Phase 4 metrics attribution. Same API the
                        # first-attempt block uses.
                        injection_service.track_injection(
                            instance_id, message_id, _meta_skill_ids
                        )

                    # C2 FIX — Finalize-on-Replace. If we have new IDs to
                    # stamp, compute the dropped set (anything previously
                    # tracked that isn't in the new set), then REPLACE
                    # ``last_injected_skill_ids`` (not merge). Skipping
                    # metadata persistence when the new set is empty keeps
                    # the existing checkpoint untouched — a ``<meta>`` tag
                    # that failed to resolve (skill not found) shouldn't
                    # erase the previously-injected set.
                    #
                    # Fix 4: the writes inside ``_finalize_and_replace``
                    # are reordered — metadata FIRST (atomic-ish, no
                    # orphan side effects if it fails), SUPERSEDED LAST
                    # (the only step with external side effects; the
                    # orphan-sweep task picks up partials).
                    if _meta_skill_ids:
                        def _finalize_and_replace(
                            _iid: str = instance_id,
                            _new_ids: list[str] = list(_meta_skill_ids),
                            _pid: str | None = _meta_project_id,
                            _aid: str = _meta_agent_id,
                        ) -> None:
                            nonlocal _auto_load_sweep_agent_id
                            inst = self._manager._instance_repository.get(_iid)
                            existing: list[str] = []
                            if inst is not None and inst.instance_metadata:
                                raw = inst.instance_metadata.get(
                                    INJECTED_SKILLS_METADATA_KEY
                                ) or []
                                if isinstance(raw, list):
                                    existing = [str(x) for x in raw if x]
                            new_set = {_new_id for _new_id in _new_ids if _new_id}
                            dropped = [
                                s for s in existing if s not in new_set
                            ]
                            # ── Fix 4: METADATA FIRST ─────────────────────
                            # REPLACE ``last_injected_skill_ids`` before any
                            # SUPERSEDED writes. If this fails, we abort
                            # cleanly — ``existing`` is still the source of
                            # truth and no orphan was created.
                            self._manager._instance_repository.set_metadata(
                                _iid,
                                INJECTED_SKILLS_METADATA_KEY,
                                list(new_set),
                            )
                            # Persist dropped IDs as
                            # ``explicitly_replaced_ids`` so the
                            # auto_load dedup-merge in
                            # ``instance_lifecycle.py`` skips them across
                            # checkpoint restores (Issue 2).
                            if dropped:
                                self._manager._instance_repository.set_metadata(
                                    _iid,
                                    REPLACED_SKILLS_METADATA_KEY,
                                    dropped,
                                )
                                # Flag the auto-load REMOVE sweep: the
                                # turn-1 checkpointed
                                # ``[SYSTEM CONTEXT: Auto-Load Skills]``
                                # block may carry a now-replaced skill. The
                                # once-per-instance gate suppresses a rebuild,
                                # so emit a ``RemoveMessage`` (graph_input)
                                # to drop the stale block this turn. A fresh
                                # filtered block re-materializes on the next
                                # first turn of a new instance.
                                _auto_load_sweep_agent_id = _aid or None
                            # ── Fix 4: SUPERSEDED LAST ─────────────────────
                            # Only after metadata is consistent do we stamp
                            # SUPERSEDED usage rows for the dropped IDs.
                            # If this raises, the orphan-sweep task picks
                            # the row up later — metadata is already
                            # correct so no double-stamp on retry.
                            if dropped:
                                metrics_service = getattr(
                                    self._manager, "_skill_metrics_service", None
                                )
                                if metrics_service is not None:
                                    try:
                                        metrics_service.finalize_superseded_skills(
                                            instance_id=_iid,
                                            agent_id=_aid,
                                            project_id=_pid or "",
                                            dropped_skill_ids=dropped,
                                        )
                                    except Exception as final_exc:
                                        logger.warning(
                                            f"Failed to finalize superseded "
                                            f"skills for {_iid[:8]}...: "
                                            f"{final_exc}"
                                        )
                            logger.info(
                                f"[MetaTag] REPLACE skill set for "
                                f"{_iid[:8]}...: old={len(existing)}, "
                                f"new={len(new_set)}, dropped={len(dropped)}"
                            )

                        await asyncio.to_thread(_finalize_and_replace)
                except Exception as e:
                    # Soft-fail — never block message processing on a
                    # meta-tag parse / lookup / persist error. The cleaned
                    # ``message`` text the top-of-function parse produced
                    # still flows through normally.
                    logger.warning(
                        f"Meta-tag skill loading failed for "
                        f"{instance_id[:8]}...: {e}"
                    )

        # ── Hybrid Context Injection (2026-07-29) — assemble persistent ──
        # Build the persistent context block ONCE per instance (on the
        # first turn) and prepend it to ``graph_input`` so LangGraph's
        # ``add_messages`` reducer checkpoints it with the user
        # message. From the next turn onward the persistent block
        # lives in ``state['messages']`` for free — no per-turn DB /
        # RAG rebuild.
        #
        # Skills (2026-07-29 refactor): moved from ephemeral to
        # PERSISTENT alongside project + shared-context. The skill
        # ``HumanMessage`` is now part of the persistent block too,
        # so it survives every turn via ``state['messages']`` and
        # is visible in the message history for debugging. The
        # pre-refactor comment that framed skills as "ephemeral and
        # continue to flow through the per-turn ContextSlot path" is
        # intentionally no longer accurate — the per-turn ContextSlot
        # path now serves only to BUILD the persistent skill message
        # on turns 2+ when a new skill triggers. ``agent_node`` no
        # longer re-injects skills into ``full_messages`` because
        # they enter via ``list(messages)`` from the checkpoint.
        #
        # ``project_already_injected`` (captured at the top of this
        # method) drives the orchestrator:
        #   * False (first turn) → orchestrator builds the full
        #     project + shared-context + skills triple; we use the
        #     persistent half here. Ephemeral is now always ``[]``
        #     (the orchestrator returns ``([...], [])``), so nothing
        #     needs to be cached on the manager for ``ContextSlot``.
        #   * True  (subsequent turns) → orchestrator skips the
        #     persistent project + shared-context builders (no DB /
        #     RAG I/O) but STILL runs the skills search; the freshly
        #     matched skill message lands in the persistent half and
        #     is prepended to ``graph_input`` for THIS turn so the
        #     reducer appends it to the checkpoint.
        #
        # Soft-fail: any ``assemble_context_messages`` exception is
        # logged + swallowed, falling back to the legacy layout
        # (no persistent block) so a transient DB / RAG error never
        # blocks message delivery.
        persistent_context_msgs: list[HumanMessage] = []
        if not is_retry:
            try:
                from .context_messages import assemble_context_messages

                # Read the cached skill result the skill-search
                # block just stored (B2 / B3 fix — reuse, do not
                # re-search). ``None`` falls through to the
                # orchestrator's internal ``_run_skill_search``
                # fallback for the rare case where the cache
                # was cleared between the search and this call.
                _cached_skill: tuple[str | None, list[str]] | None = None
                _skill_getter = getattr(
                    self._manager, "get_context_skill_result", None
                )
                if _skill_getter is not None:
                    _cached_skill = _skill_getter(instance_id)

                # Resolve ``project_id`` for the orchestrator.
                # ``agent_node`` reads it from instance metadata
                # each turn; mirror the same lookup here so the
                # persistent block on the first turn matches
                # what subsequent turns will see in
                # ``state['messages']``.
                _persistent_project_id: str | None = None
                try:
                    _proj_row = await asyncio.to_thread(
                        self._manager._instance_repository.get, instance_id
                    )
                    if _proj_row is not None and _proj_row.instance_metadata:
                        _persistent_project_id = (
                            _proj_row.instance_metadata.get("project_id")
                        )
                except Exception:  # pragma: no cover - defensive
                    _persistent_project_id = None

                # Resolve ``parent_id`` for tree-root resolution.
                # ``instance_meta`` may not be in scope here (it
                # is only assigned inside the ``if not
                # is_retry:`` block above); fall back to a fresh
                # ``None`` default so a stale reference cannot
                # leak through. The orchestrator treats
                # ``parent_id=None`` as "tree-root instance"
                # which is the correct default for our hybrid
                # path — child instances inherit the same
                # persistent context as their root via the
                # tree-root resolution inside the orchestrator.
                _persistent_parent_id: str | None = None

                _persistent_msgs, _ephemeral_msgs = await assemble_context_messages(
                    instance_id=instance_id,
                    user_query=message,
                    project_id=_persistent_project_id,
                    agent_meta=_messaging_agent_meta,
                    manager=self._manager,
                    instance_repository=self._manager._instance_repository,
                    parent_id=_persistent_parent_id,
                    skill_injection_result=_cached_skill,
                    project_already_injected=project_already_injected,
                    # A ``<meta>`` REPLACE recorded dropped skills →
                    # the checkpointed auto-load block may carry a
                    # now-replaced skill. Force a filtered rebuild
                    # (under the stable id) so only the surviving
                    # auto-load skills remain, instead of dropping
                    # them all (bare RemoveMessage) or leaking the
                    # replaced content.
                    auto_load_invalidated=bool(_auto_load_sweep_agent_id),
                )
                # 2026-07-29 refactor: ``_ephemeral_msgs`` is now
                # always ``[]`` in ``human_messages`` mode (skills
                # moved to the persistent half). The variable is
                # still unpacked for backward-compat with the call
                # signature — the orchestrator may return a populated
                # ephemeral half in some configurations, but only the
                # persistent half flows forward into
                # ``_build_graph_input``.

                if _persistent_msgs:
                    persistent_context_msgs = list(_persistent_msgs)
                    logger.info(
                        f"[Hybrid] Prepended {len(persistent_context_msgs)} "
                        f"persistent context message(s) (incl. skills "
                        f"since 2026-07-29 refactor) to graph_input for "
                        f"{instance_id[:8]}... (project_injected={project_already_injected})"
                    )

                # ── Auto-load REPLACE sweep (C3 leak fix) ────────────────
                # A ``<meta>`` REPLACE that dropped skills may have
                # invalidated the turn-1 checkpointed
                # ``[SYSTEM CONTEXT: Auto-Load Skills]`` block (the
                # once-per-instance gate suppresses a filtered rebuild).
                # Emit a ``RemoveMessage`` sentinel targeting the
                # stable block id so LangGraph's ``add_messages``
                # reducer drops the stale block from
                # ``state['messages']`` this turn — paired with the
                # filtered rebuild (``auto_load_invalidated``) so a
                # surviving set re-materializes under the same id.
                #
                # GATED on ``auto_load_block_active``: langgraph's
                # ``add_messages`` raises ``ValueError`` when a
                # ``RemoveMessage`` targets an id ABSENT from the
                # checkpoint. Agents without auto_load skills / no
                # project / no skill stack never build a block, so a
                # REPLACE of their BM25 skills there must NOT emit
                # the sweep (it would crash the message turn). On the
                # rebuild path a fresh same-id HumanMessage
                # supersedes the stale one regardless.
                _sweep_emitted = bool(
                    _auto_load_sweep_agent_id and auto_load_block_active
                )
                if _sweep_emitted:
                    from .context_messages import auto_load_skills_message_id
                    persistent_context_msgs.insert(
                        0,
                        RemoveMessage(
                            id=auto_load_skills_message_id(
                                instance_id, _auto_load_sweep_agent_id
                            )
                        ),
                    )
                    logger.info(
                        f"[Hybrid] Auto-load REPLACE sweep queued for "
                        f"{instance_id[:8]}... (agent="
                        f"{_auto_load_sweep_agent_id})"
                    )

                # ── Auto-load skills metadata tracking (dedup-merge) ───
                # Extract the auto-load skill IDs carried by the
                # ``[SYSTEM CONTEXT: Auto-Load Skills]`` HumanMessage
                # and dedup-merge them into ``last_injected_skill_ids``
                # via the shared ``_dedup_merge_skill_ids`` helper
                # (same path the BM25 block uses). This keeps the
                # orchestrator itself free of DB writes (read-path
                # safe) while still letting ``SkillMetricsService``
                # attribute usage records at task completion.
                #
                # Only on the first turn — the once-per-instance
                # contract means auto-load is already checkpointed
                # on subsequent turns, so this block is naturally
                # gated by ``not project_already_injected`` via the
                # outer ``if not is_retry`` boundary plus the
                # fact that ``_persistent_msgs`` only carries the
                # auto-load message on the first turn.
                _al_ids: list[str] = []
                _has_auto_load_block = False
                for _pm in persistent_context_msgs:
                    _ak = getattr(_pm, "additional_kwargs", None) or {}
                    if _ak.get("context_kind") != "auto_load_skills":
                        continue
                    _has_auto_load_block = True
                    # ``auto_load_skill_ids`` is always a list by
                    # construction (build_auto_load_skills_message),
                    # so no ``isinstance`` guard needed here.
                    _al_ids.extend(
                        str(x) for x in (_ak.get("auto_load_skill_ids") or [])
                        if x
                    )
                if _al_ids:
                    try:
                        await asyncio.to_thread(
                            _dedup_merge_skill_ids,
                            self._manager._instance_repository,
                            instance_id,
                            _al_ids,
                        )
                    except Exception as _al_exc:
                        logger.warning(
                            f"[Hybrid] Failed to persist auto-load "
                            f"skill IDs for {instance_id[:8]}...: "
                            f"{_al_exc}"
                        )
                # Maintain the ``auto_load_block_active`` flag so the
                # sweep on a future REPLACE turn knows whether a block
                # is checkpointed (gates the safe-to-emit RemoveMessage).
                # The flag mirrors ``state['messages']`` presence, NOT
                # ``persistent_context_msgs`` (which on a steady turn-2+
                # carry nothing because the block already lives in the
                # checkpoint). So only TRANSITION when this turn actually
                # changed block state:
                #   * fresh block built this turn → True (supersedes
                #     any swept stale one via the stable id).
                #   * sweep emitted with NO fresh rebuild (all skills
                #     replaced → empty) →False (stale removed, nothing
                #     replaces it).
                #   * neither → leave the flag untouched.
                if _has_auto_load_block:
                    _new_active = True
                elif _sweep_emitted:
                    _new_active = False
                else:
                    _new_active = auto_load_block_active  # unchanged
                if _new_active != auto_load_block_active:
                    try:
                        await asyncio.to_thread(
                            self._manager._instance_repository.set_metadata,
                            instance_id,
                            AUTO_LOAD_BLOCK_ACTIVE_KEY,
                            _new_active,
                        )
                    except Exception as _flag_set_exc:
                        logger.debug(
                            f"[Hybrid] Failed to update {AUTO_LOAD_BLOCK_ACTIVE_KEY} "
                            f"for {instance_id[:8]}...: {_flag_set_exc}"
                        )
            except Exception as _persist_exc:  # pragma: no cover - defensive
                logger.warning(
                    f"[Hybrid] Persistent context assembly failed for "
                    f"{instance_id[:8]}...: {type(_persist_exc).__name__}: "
                    f"{_persist_exc} — continuing without persistent prepending"
                )
                persistent_context_msgs = []

        # ── Task context injection (send_message `context` param) ──
        # When a parent agent passes structured context via
        # send_message(context={...}), it arrives here as
        # ``task_context`` (already formatted into a
        # ``[SYSTEM CONTEXT: Task Context]`` markdown block by the
        # tool). The stable context blocks (project / shared-context /
        # skills) MUST stay at the top of ``persistent_context_msgs``
        # for prompt-cache efficiency — they are identical across runs,
        # so keeping them at the front maximises cache hit rate. Task
        # context is dynamic (varies per message), so it goes at the
        # END of the persistent block, just before the task message.
        # ``append`` (not ``insert(0, ...)``) keeps the task context
        # after the stable blocks but before the user message.
        # Only on first attempt (not retry) to avoid double-injection —
        # the message is checkpointed on turn 1.
        if task_context and not is_retry:
            _task_ctx_msg = HumanMessage(
                content=task_context,
                id=f"task-context-{message_id}",
                additional_kwargs={
                    "injected_message": True,
                    "context_kind": "task_context",
                },
            )
            persistent_context_msgs.append(_task_ctx_msg)

        # ── D2 seam drain (wc-wake-report-integrity, T5) — pre-build phase ──
        # m1 fix: the FIFO snapshot + leftover HumanMessage list are built
        # BEFORE the three ``_build_graph_input`` call sites below. The
        # leftovers then ride the ``prepended_msgs`` seam parameter
        # (``_build_graph_input`` keeps the existing positional contract
        # for the three legacy call sites — they default ``None`` and
        # stay byte-identical when the FIFO is empty; only the WC-wake
        # wake-turn path threads non-empty leftovers through). The
        # ``clear_injection`` step is hoisted to AFTER the build so the
        # get → build → clear race window stays small. The requeue
        # safeguard (M1 object-identity) closes the get/clear race the
        # same way it did before.
        #
        # This subsumes the in-graph site 1
        # (``daemon/graph.py:2937-3005``) for the wake turn (it
        # finds an empty FIFO on the wake turn → no double-add).
        # Crash-window parity with site 1 is accepted: a crash
        # between the clear and ``graph.astream`` loses the leftovers
        # — same exposure, no new risk.
        #
        # The drain is flag-INDEPENDENT (no gating; the constant
        # ``INJECTION_ELIGIBLE_STATUSES`` shrunk to ``{"running"}``
        # in T2 — but the drain operates on the RAM FIFO which is
        # also the RUNNING-target lane; under flag OFF a WC-wake
        # send still lands here via the legacy FIFO injection route
        # and the drain picks it up the same way).
        pending_snapshot = self._manager.get_injection(instance_id)
        leftover_fifo_msgs: list[HumanMessage] = []
        for entry in pending_snapshot or []:
            # Mirror graph.py:2950-2961 — preserve the
            # ``injected_message: True`` marker + optional
            # ``source`` so leftovers ARE injections (C3
            # compaction preservation + D12 subtree filter keep
            # working). The marker is honestly applied because
            # these are pre-existing injections, not first-class
            # turn messages.
            kwargs: dict[str, Any] = {
                "injected_message": True,
            }
            _src = entry.get("source")
            if _src:
                kwargs["source"] = _src
            leftover_fifo_msgs.append(
                HumanMessage(
                    content=entry.get("content", ""),
                    additional_kwargs=kwargs,
                )
            )

        # Build input - on retry with checkpoint, resume from None
        if not is_retry:
            await self._maybe_compact_context(instance_id, graph, config)

        if is_retry:
            has_ckpt = await self._has_checkpoint(instance_id)
            if has_ckpt:
                # Pass resume message as graph_input instead of aupdate_state.
                # aupdate_state(as_node="agent") clears checkpoint's next=() causing
                # astream(None) to return instantly without running the graph.
                # LangGraph's add_messages reducer appends the HumanMessage to existing
                # checkpoint messages, so the agent sees full history + new message.
                content = _build_message_content(message, images)
                if content and not silent:
                    # Resume on the existing checkpoint — no persistent
                    # prepending (the persistent block already lives
                    # in the checkpoint, and re-prepending would double-
                    # inject on the resume). m1: thread leftover FIFO
                    # via the seam parameter (default-None when FIFO is
                    # empty, byte-identical pre-m1 behavior).
                    graph_input = _build_graph_input(
                        content, message_id,
                        prepended_msgs=leftover_fifo_msgs or None,
                    )
                else:
                    # Pure checkpoint resume (silent mode or no content)
                    graph_input = None
            else:
                logger.warning(f"Retry for instance {instance_id[:8]}... but no checkpoint found, re-adding message")
                content = _build_message_content(message, images)
                graph_input = _build_graph_input(
                    content, message_id,
                    prepended_msgs=leftover_fifo_msgs or None,
                )
        else:
            # First attempt - add message to conversation, with the
            # persistent context block (project + shared-context +
            # skills) prepended so LangGraph's ``add_messages`` reducer
            # checkpoints it once for all subsequent turns. m1: thread
            # leftover FIFO via the seam parameter; the helper places
            # ``prepended_msgs`` (FIFO leftovers) BETWEEN the persistent
            # block and the user message — positional order
            # ``[persistent...] + [leftovers...] + [user]`` per the
            # LOCKED C1-D2 S4 spec. The D1 entry-seam guard then
            # prepends pairing placeholders at position 0 to produce
            # the final end-to-end order
            # ``[placeholders?] + persistent + leftovers + user``.
            content = _build_message_content(message, images)
            graph_input = _build_graph_input(
                content, message_id,
                persistent_context_msgs=persistent_context_msgs or None,
                prepended_msgs=leftover_fifo_msgs or None,
            )

        # ── D2 seam drain — post-build phase ─────────────────────────────
        # ``clear_injection`` AFTER the build so the get → build → clear
        # race window stays small. The requeue safeguard (M1 object-
        # identity) closes the remaining get/clear window: entries
        # present in ``cleared`` but not in ``pending_snapshot`` were
        # appended mid-drain by a concurrent ``set_injection`` call;
        # re-append them at the FRONT so the original FIFO order is
        # preserved.
        #
        # Only clear when we actually built a graph_input (silent-resume
        # path leaves the FIFO intact for the next turn — same gating
        # as the pre-m1 drain).
        #
        # M1 fix: dedupe by OBJECT IDENTITY (``id(e)``), not by content
        # string. A concurrent ``set_injection`` call appends a NEW dict
        # object to the FIFO — same content string or not, it has a
        # distinct id. The previous content-keyed check silently dropped
        # a racy entry whose content string collided with a snapshot
        # entry (silent data-loss race).
        if graph_input is not None:
            cleared = self._manager.clear_injection(instance_id)
            if cleared is not None:
                snapshot_ids = {id(e) for e in pending_snapshot or []}
                raced = [e for e in cleared if id(e) not in snapshot_ids]
                if raced:
                    self._manager.requeue_injections(instance_id, raced)
            if leftover_fifo_msgs:
                logger.info(
                    f"[Injection] D2 seam-drain: {len(leftover_fifo_msgs)} "
                    f"parked FIFO entries flowed into graph_input for "
                    f"instance {instance_id[:8]}... (oldest-first)."
                )
        # ── end D2 seam drain ─────────────────────────────────────────────

        # C2 (Phase 1 — langgraph-checkpoint-perf, F1 fix): fire the
        # entry-path message_metadata tap on the ``graph_input_messages``
        # list the graph START receives. This is the PRIMARY call site
        # for the user's turn-start ``HumanMessage`` — without it,
        # ``astream``-invoked user messages would silently fall to the
        # ``state.ts`` fallback (``persistence.py:414-416``) and never
        # receive a ``message_metadata`` row. Idempotent RE-TAP under
        # ``ON CONFLICT DO NOTHING`` (decisions.md D3): a re-tap on a
        # resume / retry collapses to a no-op at the constraint level,
        # preserving first-appearance semantics (decisions.md D17,
        # ``test_message_metadata_revive_stability``). The slot's
        # ``try/except`` makes a failed upsert non-load-bearing
        # (Critical 4). Tap covers the ``astream`` invocation path
        # (``graph.astream(graph_input, ...)`` at line ~3501). The
        # direct ``ainvoke`` invocation at line ~1055 is
        # accepted-degradation OOS per decisions.md D19 + B1 — it
        # constructs ``{"messages": [message]}`` INLINE and bypasses
        # ``_build_graph_input``; zero production callers; id-less
        # inline dict; ``state.ts`` fallback applies; mirrors the
        # watchover handling (LD-D2). See ``daemon/services/message_tap.py``
        # docstring for the full OOS list.
        if graph_input is not None and self._manager.message_metadata_repo is not None:
            _entry_tap = MessageTapSlot(
                self._manager.message_metadata_repo,
                SOURCE_USER_MESSAGE_ENTRY,
            )
            await _entry_tap.tap_node_return(
                graph_input.get("messages", []),
                instance_id,
            )

        # Persistent context HumanMessages are graph inputs rather than normal
        # user turns, so they are not seen by the streaming loop's HumanMessage
        # skip below. Echo each newly prepended context message explicitly using
        # the same envelope as the regular user-message pre-emit. Stable
        # ids/content hashes make this safe when a message is encountered
        # again during retries or a repeated assembly path.
        context_messages_to_emit = list(persistent_context_msgs)
        if context_messages_to_emit:
            emitted_context_content = getattr(
                self._manager, "_emitted_message_content", None
            )
            if not isinstance(emitted_context_content, dict):
                emitted_context_content = {}
                self._manager._emitted_message_content = emitted_context_content
            for context_msg in context_messages_to_emit:
                context_serialized = serialize_message(context_msg)
                context_serialized["instance_id"] = instance_id
                context_id = context_serialized.get("message_id")
                context_hash = _compute_message_content_hash(context_serialized)
                # Include the content hash in the key as a fallback for legacy
                # skill messages whose generated id can differ on retry.
                context_key = (
                    f"{instance_id}:context:{context_id or context_hash}"
                )
                if (
                    emitted_context_content.get(context_key) == context_hash
                    or any(
                        key.startswith(f"{instance_id}:context:")
                        and value == context_hash
                        for key, value in emitted_context_content.items()
                    )
                ):
                    continue
                try:
                    await self._manager._live_hub.stream_message(
                        instance_id=instance_id,
                        message=context_serialized,
                        event_type="user_message",
                        checkpoint_id="user",
                    )
                except Exception as _context_emit_exc:  # pragma: no cover - defensive
                    logger.warning(
                        f"[Hybrid] Persistent context user_message SSE emit failed for "
                        f"{instance_id[:8]}...: {type(_context_emit_exc).__name__}: "
                        f"{_context_emit_exc}"
                    )
                else:
                    emitted_context_content[context_key] = context_hash

        # Build user message for pre-emit - use multimodal content if images present
        user_msg = HumanMessage(content=_build_message_content(message, images), id=message_id)
        
        user_serialized = serialize_message(user_msg)
        user_serialized["instance_id"] = instance_id
        await self._manager._live_hub.stream_message(
            instance_id=instance_id,
            message=user_serialized,
            event_type="user_message",
            checkpoint_id="user",
        )

        # Reset state for this processing call to prevent unbounded growth
        all_state_messages: list = []
        tool_outputs: dict = {}
        event_index = 0  # Sequence counter for checkpoint_id
        _dispatched_msg_ids: set[str] = set()  # Track dispatched message IDs for dedup
        # Per-invocation dedup of emitted tool_result events. Scoped here so the
        # set is reclaimed when this processing call returns — avoids the
        # per-process _original_timestamps map growing without bound.
        _emitted_tool_result_ids: set[str] = set()

        # C1 FIX (Phase 2 — User Language Preference): When language_check is
        # active, the agent node's final AIMessage would otherwise be dispatched
        # to the source BEFORE language_check runs and rewrites it, causing users
        # to see a wrong-language response followed by a corrected one. Defer
        # the final-message dispatch until the astream loop completes normally.
        # Retries naturally overwrite this buffer, so only the corrected (final)
        # message is ever sent to the external source.
        # W4 FIX: Read the flag from the compiled graph object (captures the
        # build-time config snapshot) rather than live config, which could be
        # mutated between graph build and message processing.
        language_check_active = bool(getattr(graph, 'language_check_active', False))
        _deferred_final_message: Any = None
        # C2 FIX: Track IDs of messages buffered for post-loop SSE re-emission.
        # The SSE emission loop iterates ``all_state_messages`` unconditionally
        # and would otherwise deliver the wrong-language AIMessage to the
        # frontend before language_check has had a chance to rewrite it. Using
        # the set keyed by msg.id lets us skip the *exact* buffered message
        # during the streaming loop and re-emit only the final (corrected)
        # version after ``astream`` completes.
        _deferred_msg_ids: set[str] = set()

        # ── D1 entry-seam pairing tail-guard (T6, S5) ────────────────────
        # Read the checkpoint state via ``graph.aget_state(config)`` and
        # prepend synthesized ``ToolMessage`` placeholders to
        # ``graph_input['messages']`` when the checkpoint tail is
        # poisoned. ``add_messages`` then commits the healed tail to the
        # checkpoint in the same superstep as the new turn — no separate
        # ``aupdate_state`` round-trip. Cost: one ``aget_state`` read per
        # enqueued turn + O(1) tail check.
        #
        # The helper short-circuits on ``graph_input is None`` (the
        # silent-resume branch :3407 injects no new mid-turn HumanMessage
        # at the seam; the in-graph pairing guard already covers it).
        # Flag-INDEPENDENT — always active regardless of the
        # ``ENSEMBLE_WC_WAKE_ENQUEUE`` kill-switch.
        if graph_input is not None:
            await _heal_poisoned_checkpoint_tail(
                graph, config, graph_input, instance_id[:8],
            )

        # Stream through graph execution
        # Register task for cancellation tracking INSIDE try block to prevent leaks
        # if CancelledError is raised during _maybe_compact_context
        current_task = asyncio.current_task()
        task_registered = False
        try:
            # Register current task for cancellation tracking
            if current_task:
                self._manager._graph_tasks[instance_id] = current_task
                task_registered = True
                logger.debug(f"Registered graph task for instance {instance_id[:8]}...")

            async with self._llm_semaphore:
                async for event in graph.astream(graph_input, config, stream_mode=["updates"]):
                    # Unpack tuple: (mode, data)
                    if isinstance(event, tuple):
                        mode, data = event
                    else:
                        mode = "updates"
                        data = event
                    
                    if mode == "updates":
                        # Progressive delivery: dispatch AI messages from "agent" node immediately
                        if dispatch_source and self._manager.source_dispatcher:
                            for node_name, node_data in data.items():
                                if node_name == "agent":
                                    node_messages = node_data.get("messages", [])
                                    for msg in node_messages:
                                        # Check if it's an AI message
                                        if not (hasattr(msg, 'type') and msg.type == 'ai'):
                                            continue

                                        # W3: Deduplicate by message ID
                                        msg_id = getattr(msg, 'id', None)
                                        if msg_id and msg_id in _dispatched_msg_ids:
                                            continue
                                        if msg_id:
                                            _dispatched_msg_ids.add(msg_id)

                                        # W2: Normalize multimodal content to a string via the
                                        # shared helper so list/None/str handling stays in
                                        # lockstep with the language check node
                                        # (daemon.language_detection._normalize_content).
                                        content = _normalize_content(getattr(msg, 'content', None))

                                        if content and content.strip():
                                            # C1 FIX (Phase 2): If language_check is active AND this
                                            # is a final response (no tool_calls → will route through
                                            # language_check next), buffer it instead of dispatching
                                            # immediately. Retries overwrite the buffer so only the
                                            # corrected final message is sent. The msg_id is already
                                            # in _dispatched_msg_ids above, so state accumulation won't
                                            # re-trigger anything; the deferred dispatch after the
                                            # astream loop is the only external send.
                                            has_tool_calls = bool(getattr(msg, 'tool_calls', None))
                                            if language_check_active and not has_tool_calls:
                                                _deferred_final_message = msg
                                                # C2 FIX: Track the buffered message's id so
                                                # the SSE emission loop can skip the same
                                                # message — it will be re-emitted after the
                                                # astream loop completes (post language_check).
                                                if msg_id:
                                                    _deferred_msg_ids.add(msg_id)
                                                continue
                                            try:
                                                await self._manager.source_dispatcher.dispatch_message(
                                                    source=dispatch_source,
                                                    content=content
                                                )
                                            except Exception as e:
                                                logger.warning(
                                                    f"Progressive dispatch failed for message {message_id[:8]}...: {e}"
                                                )
                        
                        # Accumulate messages from ALL nodes
                        any_new = False
                        for node_name, node_data in data.items():
                            node_messages = node_data.get("messages", [])
                            if node_messages:
                                any_new = True
                                # Key by msg.id to handle modifications
                                msg_index = {m.id: i for i, m in enumerate(all_state_messages) if hasattr(m, 'id')}
                                for m in node_messages:
                                    if hasattr(m, 'id') and m.id in msg_index:
                                        all_state_messages[msg_index[m.id]] = m  # Replace existing
                                    else:
                                        all_state_messages.append(m)
                        
                        if not any_new:
                            continue

                        # Broadcast context usage against the latest accumulated
                        # state. _emit_context_usage dedupes so this is cheap
                        # when the token count is unchanged (e.g. during a long
                        # single-response stream).
                        await self._emit_context_usage(instance_id, all_state_messages)

                        # Build tool_outputs from ALL messages (including ToolMessages)
                        tool_outputs = {}
                        for m in all_state_messages:
                            if isinstance(m, ToolMessage):
                                tc_id, content_str = _stringify_tool_message_content(m)
                                if tc_id:
                                    tool_outputs[tc_id] = content_str
                        
                        # Build sequence ID for checkpoint_id
                        sequence_id = f"seq_{event_index}"
                        event_index += 1
                        
                        # Emit individual messages, preserving original created_at
                        for m in all_state_messages:
                            # Emit ToolMessage as a real-time tool_result event
                            # (also still baked into the next AIMessage's tool_calls
                            # for clients that don't yet handle tool_result).
                            if isinstance(m, ToolMessage):
                                tc_id, content_str = _stringify_tool_message_content(m)
                                if not tc_id:
                                    continue
                                # Dedup via a stable per-invocation key. ToolMessages
                                # lacking an `id` fall back to (tool_call_id, content)
                                # so the same tool call is never emitted twice across
                                # updates iterations of cumulative state.
                                original_id = getattr(m, "id", None)
                                if original_id:
                                    dedup_key = f"id:{original_id}"
                                else:
                                    dedup_key = f"tc:{tc_id}:{content_str}"
                                if dedup_key in _emitted_tool_result_ids:
                                    continue
                                _emitted_tool_result_ids.add(dedup_key)
                                await self._manager._live_hub.stream_tool_result(
                                    instance_id=instance_id,
                                    tool_call_id=tc_id,
                                    content=content_str,
                                    message_id=original_id or dedup_key,
                                )
                                continue
                            # Skip HumanMessages — already emitted before graph started
                            if hasattr(m, 'type') and m.type == 'human':
                                continue
                            
                            msg_id = getattr(m, 'id', None)
                            # C2 FIX: Skip messages buffered for deferred SSE emission.
                            # When language_check is active, the buffered AI message
                            # may be rewritten/retried during the astream loop. The
                            # post-loop block re-emits the *final* version via SSE,
                            # so emitting here would deliver a wrong-language message
                            # to the frontend first. Fall back to ``message_id`` for
                            # consistency with the dispatcher's id resolution.
                            msg_id_check = msg_id or getattr(m, 'message_id', None)
                            if msg_id_check and msg_id_check in _deferred_msg_ids:
                                continue
                            msg_serialized = serialize_message(m, tool_outputs)
                            msg_serialized["instance_id"] = instance_id
                            
                            # Preserve original created_at from first emission
                            ts_key = f"{instance_id}:{msg_id}" if msg_id else None
                            if ts_key and ts_key in self._manager._original_timestamps:
                                msg_serialized["created_at"] = self._manager._original_timestamps[ts_key]
                            elif ts_key:
                                self._manager._original_timestamps[ts_key] = msg_serialized["created_at"]
                            
                            # Store content hash for deduplication (skip if content unchanged)
                            if ts_key:
                                content_hash = _compute_message_content_hash(msg_serialized)
                                self._manager._emitted_message_content[ts_key] = content_hash
                            
                            # Emit individually
                            event_type = _get_message_event_type(msg_serialized)
                            await self._manager._live_hub.stream_message(
                                instance_id=instance_id,
                                message=msg_serialized,
                                event_type=event_type,
                                checkpoint_id=sequence_id,
                            )
                        
                        # Track final content and last AI message from streaming
                        for msg in reversed(all_state_messages):
                            if hasattr(msg, 'type') and msg.type == 'ai':
                                if hasattr(msg, 'content'):
                                    # Normalize multimodal content (str | list | None) via
                                    # the shared _normalize_content helper for parity with
                                    # the language check node and the progressive
                                    # dispatch loop above.
                                    final_content = _normalize_content(msg.content)
                                last_ai_message = msg
                                break

        except asyncio.CancelledError:
            # Graph was cancelled by pause_instance_cascade
            # Re-raise so caller (MessageJobHandler/ProcessMessageProcessor) can
            # distinguish pause-cancel from normal completion and leave job PROCESSING
            logger.info(f"Graph execution cancelled for instance {instance_id[:8]}... (message_id={message_id[:8]}...)")
            raise

        except Exception as e:
            logger.error(f"Streaming failed for message {message_id}: {e}")
            await self._manager._live_hub.stream_error(
                instance_id=instance_id,
                error={"error": str(e), "stage": "streaming", "message_id": message_id},
            )
            raise

        finally:
            # C2 fix — deferred question pause (Solution A), second pass.
            #
            # ``question_pause_node`` ran inside this graph task and set a
            # marker rather than calling ``pause_instance_cascade`` directly
            # (to avoid self-cancel of this very task). The graph task is now
            # popped from ``_graph_tasks`` so we are safely OUTSIDE the
            # graph-task context — calling the cascade here will not
            # self-cancel; the DB transaction completes normally.
            #
            # HOISTED out of the ``if existing is current_task`` guard below:
            # if an external ``pause_instance_cascade`` already pre-popped
            # ``_graph_tasks[instance_id]`` (e.g. user-click-stop racing the
            # graph completion), the identity check fails and the marker
            # would otherwise leak — causing a spurious pause on the next
            # message. ``pop_deferred_question_pause`` is idempotent
            # (``set.discard``), so it's safe to call unconditionally.
            #
            # C1 FIX (marker lifetime): the marker is PEEKED with
            # ``has_deferred_question_pause`` BEFORE the cascade and POPPED
            # with ``pop_deferred_question_pause`` in the inner ``finally``
            # block AFTER ``pause_instance_cascade`` completes. The old
            # "pop-before-cascade" ordering left the marker empty during
            # the cascade's DB-commit window (DB still RUNNING) so
            # source-side Task guards saw ``marker=False, db=RUNNING`` and
            # CREATED a spurious Task. Extending the marker lifetime past
            # the cascade's DB commit closes that race. Safe because:
            #   * the marker is in-memory only (no DB write) — moving the
            #     pop cannot introduce a DB torn state;
            #   * the cascade is wrapped in ``asyncio.shield`` so the DB
            #     write completes regardless of outer cancellation;
            #   * ``pause_instance_cascade`` does NOT touch
            #     ``_deferred_question_pause`` (confirmed by grep — no
            #     reference in ``instance_lifecycle.py``);
            #   * ``pop_deferred_question_pause`` is idempotent.
            #
            # SHIELDED against double-cancel: a second ``task.cancel()``
            # arriving during the ``await`` would raise ``CancelledError``
            # (a ``BaseException`` in 3.8+, NOT caught by ``except Exception``).
            # ``asyncio.shield`` protects the DB write so a transient cancel
            # during the pause cascade does not corrupt instance state.
            #
            # This runs on every exit path (normal completion,
            # CancelledError, exception) because the cascade peek/pop is
            # unconditional — a no-op when no marker was set.
            #
            # Wrapped in try/except so a transient cascade failure does not
            # crash the message-processing call. The question pack SSE has
            # already fired from the tool, so the user can still answer; the
            # instance will just remain in whatever status the graph
            # completed in. Re-pausing an already-PAUSED instance is a
            # no-op (``pause_instance_cascade`` filters out PAUSED nodes
            # at line 1966), so a residual marker on top of an external
            # pause is harmless.
            if self._manager.has_deferred_question_pause(instance_id):
                try:
                    await asyncio.shield(
                        self._manager.pause_instance_cascade(
                            instance_id,
                            suspension_reason=SuspensionReason.AWAITING_ANSWER.value,
                        )
                    )
                except Exception as pause_err:
                    logger.warning(
                        f"[process_message] deferred question pause "
                        f"failed for {instance_id[:8]}...: "
                        f"{type(pause_err).__name__}: {pause_err}"
                    )
                finally:
                    # Pop AFTER the cascade completes so the marker
                    # covers the full cascade-execution window (DB
                    # commit to PAUSED). Closes C1.
                    self._manager.pop_deferred_question_pause(instance_id)

            # Watchover deferred termination (T2.9).
            #
            # Consumed here by ``_drain_deferred_watchover_terminate`` — the
            # C2-safe deferred cascade runs from this post-graph completion
            # path AFTER ``_graph_tasks`` is popped (mirrors the
            # question_pause pattern). See the helper docstring for the full
            # contract (C2 torn-state + H2 retry-on-failure semantics).
            await self._drain_deferred_watchover_terminate(instance_id)

            # P2.2 Dispatch B: deferred system-execution drain (D-FA1.4).
            # The marker set by the actor tools fires the daemonized
            # executor at exact turn-end (additive consumer of this
            # post-graph path; shielded inside the helper; never raises).
            await self._drain_pending_system_executions(instance_id)

            # Always unregister the task, but only if we're still the registered task
            # (handles race condition where new execution starts before our finally runs)
            if task_registered and current_task:
                existing = self._manager._graph_tasks.get(instance_id)
                if existing is current_task:
                    self._manager._graph_tasks.pop(instance_id, None)
                    self._manager.release_context_usage_cache(instance_id)
                    logger.debug(f"Unregistered graph task for instance {instance_id[:8]}...")

        # C1 FIX (Phase 2): Dispatch the deferred final message AFTER the astream
        # loop completes normally. This code only runs on successful completion —
        # asyncio.CancelledError is re-raised above and skips this block, so a
        # cancelled response is never sent to the external source.
        if _deferred_final_message is not None:
            # Normalize multimodal content (str | list | None) via the shared
            # _normalize_content helper for parity with the language check
            # node and the progressive dispatch loop above.
            deferred_content = _normalize_content(getattr(_deferred_final_message, 'content', ''))
            if (
                deferred_content
                and deferred_content.strip()
                and dispatch_source
                and self._manager.source_dispatcher
            ):
                try:
                    await self._manager.source_dispatcher.dispatch_message(
                        source=dispatch_source,
                        content=deferred_content,
                    )
                except Exception as e:
                    logger.warning(
                        f"Deferred dispatch failed for message {message_id[:8]}...: {e}"
                    )

            # C2 FIX: Also re-emit the deferred message via SSE so the frontend
            # sees the *final* (post-language_check) version. The in-loop SSE
            # emission skipped this message via ``_deferred_msg_ids``; we now
            # flush it here using the same serialization pattern as the loop.
            try:
                # Reconstruct tool_outputs from the final accumulated state,
                # mirroring the in-loop logic so any inline tool_call output
                # matches what the frontend would have seen inline.
                deferred_tool_outputs: dict[str, str] = {}
                for mm in all_state_messages:
                    if isinstance(mm, ToolMessage):
                        tc_id, content_str = _stringify_tool_message_content(mm)
                        if tc_id:
                            deferred_tool_outputs[tc_id] = content_str

                deferred_serialized = serialize_message(
                    _deferred_final_message,
                    deferred_tool_outputs,
                )
                deferred_serialized["instance_id"] = instance_id

                # Preserve original created_at from first emission, same as loop.
                deferred_msg_id = getattr(_deferred_final_message, 'id', None)
                deferred_ts_key = (
                    f"{instance_id}:{deferred_msg_id}" if deferred_msg_id else None
                )
                if deferred_ts_key and deferred_ts_key in self._manager._original_timestamps:
                    deferred_serialized["created_at"] = (
                        self._manager._original_timestamps[deferred_ts_key]
                    )
                elif deferred_ts_key:
                    self._manager._original_timestamps[deferred_ts_key] = (
                        deferred_serialized["created_at"]
                    )

                deferred_event_type = _get_message_event_type(deferred_serialized)
                await self._manager._live_hub.stream_message(
                    instance_id=instance_id,
                    message=deferred_serialized,
                    event_type=deferred_event_type,
                    checkpoint_id=f"seq_{event_index}",
                )
            except Exception as e:
                logger.warning(
                    f"Deferred SSE dispatch failed for message {message_id[:8]}...: {e}"
                )

        # Parse <think/> tags from final content
        content, thinking_extracted = parse_think_tags(final_content)
        
        # Extract thinking from last AI message
        thinking = None
        if last_ai_message:
            if hasattr(last_ai_message, 'thinking') and last_ai_message.thinking:
                thinking = last_ai_message.thinking
            elif hasattr(last_ai_message, 'additional_kwargs'):
                kwargs = last_ai_message.additional_kwargs or {}
                thinking = kwargs.get("reasoning_content") or kwargs.get("thinking")
        
        # Build tool_calls from final state
        tool_calls = None
        if last_ai_message and hasattr(last_ai_message, 'tool_calls') and last_ai_message.tool_calls:
            tool_calls = []
            outputs_map = tool_outputs
            for tc in last_ai_message.tool_calls:
                tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                tool_calls.append({
                    "id": tc_id,
                    "name": tc_name,
                    "arguments": tc_args,
                    "output": outputs_map.get(tc_id),
                })
        
        return MessageResult(
            content=content,
            thinking=thinking,
            thinking_extracted=thinking_extracted,
            tool_calls=tool_calls,
        )

    async def get_messages(self, instance_id: str) -> list[dict]:
        """Get message history for an instance.

        Args:
            instance_id: The ID of the instance.

        Returns:
            List of message dictionaries from LangGraph checkpoints.

        Raises:
            KeyError: If instance is not found.
        """
        # Verify instance exists
        await self._manager.get_instance(instance_id)  # raises KeyError if not found
        
        if self._checkpointer:
            # Pass the manager so get_instance_messages can inject the
            # synthetic system prompt (which is NOT persisted to the
            # checkpoint but is needed by the frontend's
            # "View system message" toggle).
            return await get_instance_messages(
                self._checkpointer, instance_id, manager=self._manager
            )
        return []

    async def get_queue_stats(self, instance_id: str) -> dict:
        """Get queue statistics for an instance.

        Returns a dict with pending_count, processing_count,
        and oldest_message_age_seconds attributes.

        Quick-Wins #2 — Item 1 (send-gate terminal-instance filter):
        the in-progress gate in ``daemon/tools/instance.py`` consumes
        these counts; a stranded carrier on a TERMINATED instance (the
        2026-08-29 wedged-tester 77ab8ab2 incident) must NOT count
        toward the gate — otherwise ``send_message``/revive blocks
        forever ("Pending: 1, Processing: 0"). When the queried
        instance is in a canonical terminal status, the counts are
        reported as 0 so the gate passes through to the enqueue path.
        Uses ``TERMINAL_INSTANCE_STATUSES`` from ``daemon.constants``
        (the canonical set — completed/terminated/error/failed) so
        the filter cannot drift from the rest of the codebase.
        """
        # Terminal-instance short-circuit: a stranded carrier on a
        # dead instance is not "in progress". Cheap SELECT (PK lookup);
        # the existing ``get_stats`` query is reserved for live queues.
        # The missing-instance branch (row absent) also returns zeros —
        # the gate's first call after a cascade may query an already-
        # deleted instance and must remain non-blocking.
        try:
            from ..constants import TERMINAL_INSTANCE_STATUSES

            _instance_meta = await asyncio.to_thread(
                self._manager._instance_repository.get, instance_id
            )
            if (
                _instance_meta is not None
                and _instance_meta.status in TERMINAL_INSTANCE_STATUSES
            ):
                logger.debug(
                    f"get_queue_stats: instance {instance_id[:8]}... is "
                    f"terminal ({_instance_meta.status}); returning zeros "
                    f"to keep the send-gate non-blocking "
                    f"(stranded-carrier quick-win #2)"
                )
                return {
                    "pending_count": 0,
                    "processing_count": 0,
                    "oldest_message_age_seconds": None,
                }
        except Exception as filter_exc:
            # FAIL-OPEN: a transient lookup failure must not block
            # sends. The unfiltered counts are returned, matching the
            # pre-fix behaviour under lookup error. Elevated from DEBUG
            # to WARNING on council W1 (2026-08-29): a silent miscount
            # here silently re-wedges the carrier with zero prod
            # visibility; the warn level is the only externally-visible
            # signal that fail-open masked a degraded lookup.
            logger.warning(
                "get_queue_stats: terminal-status lookup failed "
                "(non-fatal, returning unfiltered counts) "
                "instance_id=%s error=%s: %s",
                instance_id,
                type(filter_exc).__name__,
                filter_exc,
            )

        stats = await asyncio.to_thread(self._queue_repository.get_stats, instance_id)
        return {
            "pending_count": stats["pending_count"],
            "processing_count": stats["processing_count"],
            "oldest_message_age_seconds": stats["oldest_message_age_seconds"]
        }

