"""Watchover activation / deactivation service.

Phase 3 of the Watchover feature (2026-08-05). Encapsulates the
business logic that backs the ``POST /instances/{id}/watchover``
endpoint:

  * **T3.4 — ``_build_watchover_context``** — reads the current
    LangGraph state via ``compiled_graph.aget_state(config)``,
    attempts a :class:`ContextCompactor`-based summary, and falls back
    to a raw-tail snapshot when the conversation is too short to
    compact (TD-6 / AC-EC.7).
  * **T3.5 — ``activate_watchover``** — orchestrates the full
    pause → quiescence barrier → compaction → atomic flag-write →
    resume sequence with try/except + rollback (W-8) so a failure
    cannot leave the instance stuck paused with a partial watchover
    config.
  * **T3.6 — ``deactivate_watchover``** — symmetric pause → clear flag
    → resume for FR-14.

Design notes:

  * **Service holds no I/O state.** It accepts the manager at
    construction time and reaches per-instance resources through the
    manager facade (``manager._instance_repository``,
    ``manager._compactor``, ``manager._config``, etc.) — the same
    dependency-passing convention used by ``InstanceLifecycleService``
    and ``InstanceMessagingService``.
  * **Atomicity over simplicity.** Activation writes 3-4 metadata keys
    in a single ``set_metadata_many`` call so a crash mid-write cannot
    produce torn state. The rollback path clears the half-written flag
    and re-raises.
  * **Graceful degradation on best-effort paths.** The quiescence
    barrier (T3.5b) and SSE emit are both best-effort — a failure logs
    but does not propagate, so a transient SSE hiccup cannot block the
    activation lifecycle.

In-flight limitation (W-9, CR-3, LD-4 ACCEPTED):
    Watchover activation does NOT guarantee interception of tool
    calls that began executing BEFORE activation was requested.
    ``pause_instance_cascade`` cancels the graph task but cannot stop
    a tool already running in a worker thread. For maximum safety,
    activate watchover before starting autonomous work, or pause
    manually first.

Phase 5 (H3 — DONE):
    Activation pauses now persist
    ``SuspensionReason.WATCHOVER_SETUP`` on the suspended task turn via
    the manager/lifecycle cascade. Deactivation passes ``None`` because it
    is a transition window, not a watchover setup suspension.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from daemon.repositories.task.models import SuspensionReason
from daemon.repositories.message_queue.models import (
    MessageStatus,
    MessageType,
)

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)


# Default number of trailing messages to keep in the raw-tail fallback
# context (TD-6). Keeps the watcher prompt bounded even on very long
# histories where compaction skipped (e.g. below the min-messages
# threshold).
DEFAULT_RAW_TAIL_MESSAGES = 10


def _format_raw_tail(messages: list[Any], limit: int) -> str:
    """Build a compact string from the last ``limit`` messages.

    Best-effort formatter: tolerates both ``BaseMessage`` objects (with
    ``.content`` attribute) and plain dicts (with a ``"content"`` key).
    Tool-call blocks are stripped to the tool name + a short
    argument preview so the watcher prompt stays readable.

    Args:
        messages: Conversation messages in chronological order.
        limit: Maximum number of trailing messages to include.

    Returns:
        A non-empty newline-joined string suitable for use as the
        watchover_context. Returns an explicit empty-string marker if
        no usable content was found (so the caller can decide whether
        to treat that as a real context or fall back further).
    """
    tail = messages[-limit:] if len(messages) > limit else messages
    parts: list[str] = []
    for idx, msg in enumerate(tail, start=1):
        # Tolerate both LangChain BaseMessage and plain dict shapes
        # (state.values["messages"] is normally a list of BaseMessage
        # but tests may pass mocks).
        content: Any
        msg_type: str | None = None
        tool_calls: Any = None
        if isinstance(msg, dict):
            content = msg.get("content", "")
            msg_type = msg.get("type") or msg.get("role")
            tool_calls = msg.get("tool_calls")
        else:
            content = getattr(msg, "content", "")
            msg_type = getattr(msg, "type", None) or getattr(msg, "role", None)
            tool_calls = getattr(msg, "tool_calls", None)

        prefix = f"[{idx}"
        if msg_type:
            prefix += f"/{msg_type}"
        prefix += "]"
        if tool_calls:
            names = []
            for tc in tool_calls:
                if isinstance(tc, dict):
                    name = tc.get("name") or tc.get("function", {}).get("name")
                    args = tc.get("args") or tc.get("function", {}).get("arguments")
                else:
                    name = getattr(tc, "name", None) or getattr(tc, "function", None)
                    args = getattr(tc, "args", None)
                if name:
                    names.append(f"{name}({_short_arg(args)})")
            if names:
                content = " ".join(names) if not content else f"{content}\n  tools: {', '.join(names)}"

        text = _stringify_content(content)
        if text:
            parts.append(f"{prefix} {text}")

    return "\n".join(parts)


def _short_arg(arg: Any) -> str:
    """Render a tool-call argument value as a short preview string."""
    if arg is None:
        return ""
    if isinstance(arg, str):
        return arg[:60]
    try:
        rendered = str(arg)
    except Exception:
        rendered = "<unrepr>"
    return rendered[:60]


def _stringify_content(content: Any) -> str:
    """Convert a message content (str | list | other) to a flat string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Multimodal content array — pick the text blocks only.
        chunks: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if text:
                    chunks.append(str(text))
            else:
                text = getattr(block, "text", None)
                if text:
                    chunks.append(str(text))
        return "\n".join(chunks)
    return str(content)


def _summary_from_compaction_result(
    replacement_messages: list[Any],
    original_messages: list[Any],
) -> str:
    """Extract a human-readable summary from a :class:`CompactionResult`.

    NOTE: retained for backwards compatibility — the builder path no
    longer calls this function, but the helper remains importable for
    tests and any external callers. The Phase 4 builder replaces
    compaction in the activation path; this helper is dormant unless
    a non-builder strategy is configured.

    Args:
        replacement_messages: ``result.replacement_messages`` from the
            compactor.
        original_messages: The original conversation messages (used
            only when the compactor produced no summary).

    Returns:
        A non-empty string suitable for use as watchover_context.
    """
    summary_parts: list[str] = []
    for msg in replacement_messages:
        msg_type = getattr(msg, "type", None) or type(msg).__name__
        if "system" in str(msg_type).lower():
            content = _stringify_content(getattr(msg, "content", ""))
            if content:
                summary_parts.append(content)

    if summary_parts:
        return "\n".join(summary_parts)

    # No summary message found — fall back to a short raw tail of the
    # original messages.
    return _format_raw_tail(original_messages, limit=DEFAULT_RAW_TAIL_MESSAGES)


def _llm_config_from_manager(manager: "InstanceManager") -> dict[str, Any]:
    """Build an ``llm_config`` dict compatible with :class:`ContextCompactor`.

    Mirrors the dict the manager builds in its own ``__init__`` (see
    :class:`daemon.manager.InstanceManager`) — same keys, same shape.
    Per-instance model overrides stored on the instance metadata are
    NOT consulted here: the compactor only needs the LLM credentials
    to talk to the proxy, not the model name (the model name goes into
    the ``CompactionContext.model_name`` field separately). Keeping
    this sync with the manager's llm_config makes future migrations
    easier.
    """
    return {
        "base_url": manager.config.llm.base_url,
        "api_key": manager.config.llm.api_key,
        "model": manager.config.llm.model,
        "model_vision": manager.config.llm.model_vision,
        "temperature": manager.config.llm.temperature,
        "request_timeout": manager.config.llm.request_timeout,
    }


class WatchoverService:
    """Encapsulates watchover activation / deactivation business logic.

    Holds a reference to the :class:`InstanceManager` facade; every
    cross-system operation (pause, resume, metadata write, SSE emit,
    graph state read) is reached through the manager so we get the
    same lifecycle guarantees and rollback semantics as the rest of
    the daemon.

    The service is intended to be used as a per-manager singleton
    (the manager constructs one in ``__init__`` once the dependencies
    are available). Tests can construct it directly with a mock
    manager.
    """

    def __init__(self, manager: "InstanceManager") -> None:
        """Initialize the service.

        Args:
            manager: The :class:`InstanceManager` facade. Must expose
                ``pause_instance_cascade``, ``resume_instance_cascade``,
                ``wait_for_instance_quiescent``, ``enable_watchover``,
                ``disable_watchover``, ``get_instance``,
                ``_compactor``, ``_config``, ``_instance_repository``,
                and ``_live_hub`` (for the SSE emit step).
        """
        self._manager = manager

    # ------------------------------------------------------------------
    # T3.4 — context construction
    # ------------------------------------------------------------------

    async def _recover_inflight_human_message(
        self,
        instance_id: str,
        checkpoint_messages: list[Any],
    ) -> list[Any]:
        """Recover the in-flight HUMAN message that ``pause_instance_cascade``
        dropped from the checkpoint.

        When ``activate_watchover`` is invoked while an instance is
        RUNNING, ``pause_instance_cascade`` cancels the in-flight graph
        task via ``graph_task.cancel()``. LangGraph only commits state
        at **node boundaries**, so the input ``HumanMessage`` for the
        current super-step is a PENDING WRITE that is rolled back when
        the cancel fires mid-``agent_node`` LLM call. The result:
        ``graph.aget_state().values["messages"]`` does NOT contain the
        message that triggered the current turn — exactly the message
        the watcher most needs to see.

        The ``message_queue`` DB table is the reliable source. While a
        graph task is running, its triggering message is in
        ``status == processing`` with ``type == human``; pause only
        cancels the graph task and flips the instance to ``PAUSED``,
        it does NOT delete the queue row. This helper reads those
        processing HUMAN rows and returns them as
        :class:`HumanMessage` objects ready to be appended to the
        conversation the builder sees.

        Dedup is content-equality against the last ``HumanMessage``
        already in ``checkpoint_messages``: if the node committed
        before the cancel fired (rare but possible) the message is
        already represented and we must not double-insert.

        Graceful degradation: any DB failure logs a warning and
        returns an empty list — the caller proceeds with the
        checkpoint-only messages rather than crashing the activation
        lifecycle.

        Args:
            instance_id: Owning instance identifier.
            checkpoint_messages: Messages already read from
                ``graph.aget_state().values["messages"]``.

        Returns:
            A list of :class:`HumanMessage` (or empty) representing
            the in-flight user message(s) not yet visible in the
            checkpoint. Order matches ``enqueued_at desc`` from
            :meth:`MessageQueueRepository.get_by_instance`.
        """
        try:
            from langchain_core.messages import HumanMessage

            repo = getattr(self._manager, "_queue_repository", None)
            if repo is None:
                logger.warning(
                    "watchover_service._recover_inflight_human_message(%s): "
                    "no _queue_repository on manager; skipping recovery",
                    instance_id,
                )
                return []

            rows = repo.get_by_instance(instance_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "watchover_service._recover_inflight_human_message(%s): "
                "queue read failed (%s); skipping recovery "
                "(checkpoint-only messages will be used)",
                instance_id,
                exc,
            )
            return []

        # Compute the last HumanMessage content already in the
        # checkpoint for dedup. Tolerate both BaseMessage and dict.
        last_human_content: str | None = None
        for msg in reversed(checkpoint_messages):
            content: Any
            if isinstance(msg, dict):
                content = msg.get("content")
                msg_type = msg.get("type") or msg.get("role")
            else:
                content = getattr(msg, "content", None)
                msg_type = getattr(msg, "type", None) or getattr(msg, "role", None)
            if msg_type and str(msg_type).lower() == "human":
                last_human_content = _stringify_content(content)
                break

        recovered: list[Any] = []
        # ``get_by_instance`` returns newest-first; iterate in that
        # order so the LAST recovered message is also the most recent
        # in-flight message — we append in original order to the
        # caller list below.
        for row in rows:
            row_status = getattr(row, "status", None)
            row_type = getattr(row, "type", None)
            if row_status != MessageStatus.PROCESSING.value:
                continue
            if row_type != MessageType.HUMAN.value:
                continue
            row_content = getattr(row, "content", None)
            if not row_content:
                continue
            if last_human_content is not None and _stringify_content(
                row_content
            ) == last_human_content:
                # Already represented in the checkpoint — skip.
                continue
            recovered.append(HumanMessage(content=row_content))

        if recovered:
            logger.info(
                "watchover_service._recover_inflight_human_message(%s): "
                "recovered %d in-flight human message(s) from "
                "message_queue (not present in checkpoint state)",
                instance_id,
                len(recovered),
            )
        return recovered

    async def _build_watchover_context(
        self,
        instance_id: str,
        *,
        requirement: str | None,
        extra_messages: list | None = None,
    ) -> str:
        """Construct the ``watchover_context`` string for ``instance_id``.

        Phase 4 builder path: reads the conversation from the
        checkpoint state and delegates to
        :class:`WatcherContextBuilder`, which issues a single LLM call
        that produces a structured markdown guardrail document. The
        ``requirement`` is passed as an INPUT to the builder so the
        LLM can weave it into ``## Requirement`` rather than being
        appended as a hybrid post-splice.

        ``extra_messages`` (terminal-activation fix, 2026-08-08) lets
        the caller inject additional ``HumanMessage`` objects — most
        importantly the ``next_command`` that ``_activate_terminal``
        is about to enqueue. Without this hook the builder runs BEFORE
        the next_command is enqueued and therefore NEVER sees it; the
        watcher would guardrail against an outdated conversation. The
        extra messages are appended AFTER inflight recovery so they
        sit at the very tail of the conversation the builder sees.

        Fallback chain:

        1. Builder LLM call succeeds → return markdown guardrail.
        2. Builder LLM call fails (timeout / infra / judgment) →
           builder's internal fallback (raw-tail + static guardrail +
           requirement splice) runs and returns the degraded context.
        3. Builder is unavailable (import error, manager
           misconfigured) → belt-and-suspenders raw-tail fallback here
           so the activation lifecycle always produces SOMETHING.

        Args:
            instance_id: Owning instance identifier.
            requirement: Operator-supplied requirement string. Passed
                to the builder as a JSON input field so the LLM can
                weave it into ``## Requirement``.
            extra_messages: Optional list of additional
                :class:`HumanMessage` (or compatible) objects to
                append to the conversation after inflight recovery.
                Used by :meth:`_activate_terminal` to pass
                ``next_command`` so the watcher sees what the agent
                is about to do. ``None`` (default) → no extra
                messages; behavior identical to previous versions.

        Returns:
            A non-empty context string. If the conversation is empty
            AND no requirement was supplied, a sentinel string is
            returned so the caller can still store SOMETHING.
        """
        manager = self._manager
        graph = await manager.get_instance(instance_id)
        thread_config = {"configurable": {"thread_id": instance_id}}

        current_state = await graph.aget_state(thread_config)
        state_values = current_state.values if current_state else {}
        messages = state_values.get("messages", []) if state_values else []

        # Watchover-mid-flight fix: ``pause_instance_cascade`` cancels
        # the in-flight graph task at a node boundary, so the
        # input ``HumanMessage`` that triggered the current turn is
        # rolled back from the checkpoint. The ``message_queue`` row
        # in PROCESSING status is the reliable source — recover it
        # here so the builder sees the most recent user intent. See
        # ``_recover_inflight_human_message`` for the dedup contract.
        recovered = await self._recover_inflight_human_message(
            instance_id, messages
        )
        if recovered:
            messages = list(messages) + recovered

        # Terminal-activation fix (2026-08-08): ``_activate_terminal``
        # enqueues ``next_command`` AFTER building the watchover
        # context. Without this seam the builder never sees the
        # ``next_command`` — the single most important input for the
        # watcher ("what is the agent about to do?"). We append the
        # extra messages LAST so they appear at the tail of the
        # conversation the builder consumes (and so the raw-tail
        # fallback at the end of this method picks them up
        # automatically since the fallback reads ``messages``).
        if extra_messages:
            messages = list(messages) + list(extra_messages)

        # Try the LLM-driven builder first. If the builder is not
        # importable or the manager is missing the LLM config the
        # exception path below falls back to the raw-tail.
        try:
            from daemon.services.watcher_context_builder import (
                WatcherContextBuilder,
            )
            from daemon.graph import (
                _load_watcher_builder_prompt,
                _load_watcher_meta_config,
            )

            # W2 fix: read ``builder_timeout_seconds`` and
            # ``builder_message_window`` from the watcher's
            # ``meta.json`` ``watchover`` section so the meta values
            # are honored (previously the builder used its hardcoded
            # defaults and the meta entries were dead config).
            # ``builder_llm_model`` is intentionally NOT read here —
            # the model source is ``_llm_config_from_manager(manager)``
            # which already reads ``manager.config.llm.model``. The
            # ``builder_llm_model`` meta entry remains documentation
            # only.
            watcher_meta = _load_watcher_meta_config()
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config=_llm_config_from_manager(manager),
                builder_prompt=_load_watcher_builder_prompt(),
                timeout_seconds=int(
                    watcher_meta.get("builder_timeout_seconds", 300)
                ),
                message_window=int(
                    watcher_meta.get("builder_message_window", 40)
                ),
            )
            markdown = await builder.build(messages, requirement)
            return markdown
        except Exception as exc:
            # Belt-and-suspenders fallback — the builder has its own
            # internal fallback, but if it could not even be imported
            # (test environment, missing module) we still need to
            # produce SOMETHING so the watcher has context.
            logger.warning(
                f"watchover_service._build_watchover_context({instance_id}): "
                f"builder path unavailable ({type(exc).__name__}: {exc}); "
                f"falling back to raw-tail + static guardrail"
            )
            from daemon.services.watcher_context_builder import (
                _FALLBACK_GUARDRAIL_PREFIX,
            )

            raw_tail = _format_raw_tail(messages, DEFAULT_RAW_TAIL_MESSAGES)
            parts: list[str] = [_FALLBACK_GUARDRAIL_PREFIX.rstrip()]
            if requirement:
                parts.append(
                    f"[Requirement] {requirement}\n\n"
                    f"[Recent activity]\n{raw_tail}"
                )
            elif raw_tail:
                parts.append(raw_tail)
            else:
                parts.append("[Recent activity] (no prior activity)")
            return "\n".join(parts)

    # ------------------------------------------------------------------
    # T3.5 — activation lifecycle
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Resume-doesn't-restart-graph fix helpers (2026-08-07)
    # ------------------------------------------------------------------
    #
    # The previous watchover pause/resume lifecycle ONLY called
    # ``manager.resume_instance_cascade`` — which flips the DB status
    # PAUSED → RUNNING but does NOT restart the LangGraph execution.
    # The graph re-trigger is the SEPARATE
    # ``manager.resume_processing_job`` call, paired with the cascade
    # by every other resume path in the codebase (``/resume``,
    # ``/answer``, ``/question/dismiss``, ``POST /messages``-on-paused).
    # Without ``resume_processing_job`` the instance is left in
    # ``RUNNING`` with no Task being processed — a "stuck" state.
    #
    # These two helpers are the shared seam used by all 4 watchover
    # resume sites (activate success, activate rollback, deactivate
    # success, deactivate rollback). The dedup helper exists so the
    # fallback ``enqueue_message`` path does not enqueue a second
    # "continue" if one is already pending in the message queue.

    async def _resume_with_graph_restart(
        self,
        instance_id: str,
        manager: "InstanceManager",
        *,
        resume_message: str | None = None,
    ) -> None:
        """Resume an instance and re-trigger the graph in a single seam.

        Phase 5 fix (resume-doesn't-restart-graph bug): the watchover
        resume lifecycle previously called only
        ``manager.resume_instance_cascade`` — which only flips DB
        status PAUSED → RUNNING. The graph re-trigger is the
        SEPARATE ``manager.resume_processing_job`` call. This helper
        pairs both calls per the pattern in
        ``daemon.routers.instances.resume_instance``,
        ``/answer``, ``/question/dismiss``, and
        ``daemon.routers.messages.send_message`` (PAUSED branch).

        For each resumed instance (target + cascade children):

          * If ``rid == target_id`` → ``resume_message or "continue"``
            and ``silent=False`` (the message is delivered to the
            watched instance's graph).
          * Else → ``"resume"`` and ``silent=True`` (cascade children
            resume silently from their checkpoint).

        If ``resume_processing_job`` returns ``None`` (no
        suspended turn handle, e.g. WATCHOVER_SETUP did not write a
        ``resume_target_turn_id`` because the task was still PENDING
        when pause fired) the helper falls back to
        ``manager.enqueue_message(source="cascade_resume")`` so the
        user's message is not silently dropped — mirroring the F10
        ``api_resume_fallback`` path in
        ``daemon.routers.messages.send_message``. The fallback is
        deduped against a pending "continue" already in the queue to
        avoid double-enqueue on a fast double-tap.

        Both the primary and the fallback paths are wrapped in
        per-instance try/except so a failure on one resumed child
        does not abort the rest of the fan-out. Per-instance
        ``resume_processing_job`` / ``enqueue_message`` errors are
        logged and swallowed; cascade (``resume_instance_cascade``)
        errors are re-raised so callers' rollback/re-raise contracts
        hold — the watchover resume is always best-effort at the
        per-instance level (the user-facing activation has already
        succeeded at the DB level; the graph restart is a recovery
        aid, not a load-bearing step for the API response).

        Args:
            instance_id: Target instance identifier (the watched
                instance, not a child).
            manager: The :class:`InstanceManager` facade.
            resume_message: Optional custom message for the target
                instance. ``None``/empty → ``"continue"``.
        """
        try:
            result = await manager.resume_instance_cascade(instance_id)
        except (Exception, asyncio.CancelledError):
            # Do NOT swallow the cascade error — let it propagate to
            # the caller's exception handler. The activate / deactivate
            # success paths catch it in their `except` block and run
            # rollback; the rollback paths catch it in their own
            # `except` block and log+swallow so the original error
            # surfaces. Suppressing here would break the original
            # re-raise contract (the cascade error IS the original
            # error in the rollback-on-resume-failure test).
            raise

        if not isinstance(result, dict):
            # Defensive: in case a future change returns a non-dict
            # shape, fall back to the single-instance contract.
            resumed_ids: list[str] = [instance_id]
            target_id = instance_id
        else:
            target_id = result.get("target_id", instance_id)
            resumed_ids = result.get("resumed_ids") or [instance_id]

        for rid in resumed_ids:
            is_target = rid == target_id
            # Target gets the user-supplied message (or default
            # "continue"); cascade children always resume silently
            # with the fixed token "resume" — matches the
            # ``/resume`` / ``/answer`` fan-out in
            # ``daemon.routers.instances``.
            resume_msg = (resume_message or "continue") if is_target else "resume"
            try:
                handle_result = await manager.resume_processing_job(
                    rid,
                    message=resume_msg,
                    silent=not is_target,
                )
                if handle_result is None:
                    if is_target:
                        # Fallback (mirrors ``daemon/routers/messages.py``
                        # ``api_resume_fallback``): if the selector
                        # returned no handle (e.g. the
                        # WATCHOVER_SETUP-suspended turn is not in the
                        # filter set), enqueue the message directly so
                        # the user's intent is not silently dropped.
                        # Deduped against a pending "continue" already
                        # in the queue. ONLY applies to the target —
                        # non-target cascade children are silent
                        # (§9.3 ``internal_child_noop`` contract in
                        # ``daemon/manager.py``); fabricating a Task
                        # for them would violate the silent-resume
                        # contract that mirrors the ``if is_target:``
                        # gate in ``daemon/routers/messages.py``.
                        logger.info(
                            "watchover_service._resume_with_graph_restart(%s): "
                            "resume_processing_job returned None; falling "
                            "through to enqueue_message (cascade_resume) for %s",
                            instance_id,
                            rid,
                        )
                        if not self._has_pending_resume_message(rid, resume_msg):
                            try:
                                await manager.enqueue_message(
                                    instance_id=rid,
                                    message=resume_msg,
                                    source="cascade_resume",
                                )
                            except (Exception, asyncio.CancelledError) as enq_exc:
                                logger.error(
                                    "watchover_service._resume_with_graph_restart"
                                    "(%s): fallback enqueue_message failed for "
                                    "%s (%s); user message NOT delivered",
                                    instance_id,
                                    rid,
                                    enq_exc,
                                )
                    else:
                        # Non-target cascade child: silent resume
                        # contract — do NOT enqueue. The selector
                        # returning None (no suspended turn handle)
                        # for a silent child is the expected outcome;
                        # the child resumes from its checkpoint via
                        # the cascade's status flip alone. Mirrors
                        # the ``else: logger.debug(...)`` branch in
                        # ``daemon/routers/messages.py:296-301``.
                        logger.debug(
                            "watchover_service._resume_with_graph_restart(%s): "
                            "resume_processing_job returned None for non-target "
                            "%s; skipping enqueue (silent resume per §9.3 "
                            "internal_child_noop)",
                            instance_id,
                            rid,
                        )
            except (Exception, asyncio.CancelledError) as exc:
                if is_target:
                    # ``resume_processing_job`` itself raised on the
                    # target — log and try the enqueue fallback so the
                    # user's message is not silently dropped.
                    logger.warning(
                        "watchover_service._resume_with_graph_restart(%s): "
                        "resume_processing_job raised for %s (%s); attempting "
                        "enqueue_message fallback",
                        instance_id,
                        rid,
                        exc,
                    )
                    try:
                        if not self._has_pending_resume_message(rid, resume_msg):
                            await manager.enqueue_message(
                                instance_id=rid,
                                message=resume_msg,
                                source="cascade_resume",
                            )
                    except (Exception, asyncio.CancelledError) as enq_exc:
                        logger.error(
                            "watchover_service._resume_with_graph_restart(%s): "
                            "both resume_processing_job and enqueue_message "
                            "failed for %s (rpj=%s, enq=%s); user message NOT "
                            "delivered",
                            instance_id,
                            rid,
                            exc,
                            enq_exc,
                        )
                else:
                    # Non-target cascade child: per-rid failures are
                    # logged and swallowed WITHOUT enqueue. The silent
                    # resume contract forbids fabricating a Task for a
                    # silent child even on the recovery path — the
                    # cascade's PAUSED → RUNNING flip is the only
                    # signal the child needs to replay its checkpoint.
                    logger.warning(
                        "watchover_service._resume_with_graph_restart(%s): "
                        "resume_processing_job raised for non-target %s (%s); "
                        "skipping enqueue fallback (silent resume per §9.3 "
                        "internal_child_noop)",
                        instance_id,
                        rid,
                        exc,
                    )

    def _has_pending_resume_message(
        self, instance_id: str, candidate_message: str | None = None
    ) -> bool:
        """Check whether ``instance_id`` already has a pending resume message.

        Used to dedupe the watchover ``enqueue_message`` fallback
        path: if a "continue" or "resume" message is already pending
        (status ``READY`` or ``PROCESSING``) for this instance, do
        NOT enqueue a duplicate. The check is case-insensitive
        substring match on the message content.

        Conservative on query failure: if the message queue
        repository is not reachable, or the engine is not initialised,
        or the SELECT itself raises, the method logs a warning and
        returns ``False`` so the resume is not BLOCKED by a transient
        dedup-query failure. False negatives (skipping dedup when a
        pending message exists) are preferable to false positives
        (blocking a real resume because dedup cannot query).

        Args:
            instance_id: The instance to check.
            candidate_message: Optional message text being considered
                for enqueue. When supplied, the check is restricted
                to messages whose content contains it (case-insensitive).
                When ``None`` (default), any message whose content
                contains ``"continue"`` or ``"resume"`` is treated as
                a pending resume.

        Returns:
            ``True`` if a pending resume-shaped message already
            exists for ``instance_id``; ``False`` otherwise (including
            on query failure).
        """
        try:
            repo = getattr(self._manager, "_queue_repository", None)
            if repo is None:
                logger.warning(
                    "watchover_service._has_pending_resume_message(%s): "
                    "no _queue_repository on manager; skipping dedup",
                    instance_id,
                )
                return False
            pending = repo.list_pending(instance_id=instance_id, limit=100)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "watchover_service._has_pending_resume_message(%s): "
                "list_pending failed (%s); skipping dedup (false "
                "negative is preferable to blocking the resume)",
                instance_id,
                exc,
            )
            return False

        needle = (candidate_message or "").strip().lower() or None
        # Default needles — match the messages the watchover resume
        # path enqueues ("continue" for the target, "resume" for
        # cascade children) plus the message text itself for any
        # caller-supplied ``resume_message``.
        needles: tuple[str, ...]
        if needle:
            needles = (needle,)
        else:
            needles = ("continue", "resume")

        for msg in pending:
            content = (getattr(msg, "content", "") or "").strip().lower()
            if not content:
                continue
            if any(n in content for n in needles):
                return True
        return False

    async def activate_watchover(
        self,
        instance_id: str,
        *,
        requirement: str | None,
        user_context: str | None = None,
        resume_message: str | None = None,
        next_command: str | None = None,
    ) -> dict[str, Any]:
        """Run the activation lifecycle: pause → bounded_barrier → context → flag → resume.

        Phase 3 (T3.5) orchestration. Two paths now exist:

          * **Running path (Case 1).** When ``is_instance_running`` is
            ``True`` (status in ``{"running", "active"}``): the full
            sequence runs — ``pause_instance_cascade`` →
            ``wait_for_instance_quiescent(timeout=2.0)`` →
            ``_build_watchover_context`` → ``enable_watchover`` →
            ``_resume_with_graph_restart`` → SSE emit
            ``status_change: watchover_active``. This is the original
            T3.5 pause-first ordering — pause comes FIRST so the user
            sees the instance flip to ``PAUSED`` immediately while
            the context snapshot is built (M-2 in-flight hang fix).
          * **Terminal/idle path (Case 2 — Watchover Dialog
            Redesign).** When ``is_instance_running`` is ``False``
            (any non-running status: idle, paused, completed, error,
            terminated, queued, waiting_children, failed, waiting):
            the pause/resume cycle is skipped — there is nothing
            in-flight to cancel. The service builds the context from
            the existing checkpoint messages, atomically enables the
            watchover flag, optionally enqueues ``next_command`` as a
            new message so the watched instance picks it up on its
            next dispatch, then emits the ``watchover_active`` SSE.
            No ``pause_instance_cascade``, no
            ``resume_instance_cascade`` — the instance was already
            idle so neither is needed.

        Case 1 — running path sequence:

          1. ``pause_instance_cascade`` — cancel the in-flight graph
             task (Checkpoint-safe; resume replays from the last
             committed LangGraph node boundary). This makes the user
             immediately see the instance as ``PAUSED`` while the
             context snapshot is built — the bug fix for the M-2
             "no visible pause" report.
          2. ``wait_for_instance_quiescent(timeout=2.0)`` — a short
             bounded barrier that just confirms the graph task
             cancellation has settled. ``pause_instance_cascade`` above
             already cancelled the task, so this barrier is a quick
             sanity check (not a strategy). It returns ``True`` in
             the common case; a ``False`` is logged but does not
             abort because the task was already cancelled upstream.
          3. ``_build_watchover_context`` (T3.4) — summarise the
             recent conversation via the LLM builder (with a 300s
             timeout because devops/ops conversations can be very
             long). Runs AFTER pause so the instance is visibly
             paused while the snapshot is built.
          4. ``enable_watchover`` — atomic ``set_metadata_many``
             writes the full flag set.
          5. ``resume_instance_cascade`` — resume the instance.
          6. SSE emit ``status_change: watchover_active`` via
             ``LiveEventHub.stream_status_change`` so the frontend
             sees the transition.

        Pause-first rationale (replaces the earlier "Quiesce-first
        rationale" which observed an in-flight hang):
            The original ordering was ``quiesce → pause → context → flag
            → resume``. The quiescence barrier uses a 30s default
            timeout and runs FIRST. When the instance was mid-LLM-call
            (the normal case for an active devops instance), the
            barrier blocked the full 30s, eating the route-level
            ``asyncio.wait_for(timeout=30)`` budget and producing a
            504 — and the user never saw the pause because pause
            came AFTER the barrier. The fix: pause FIRST. ``pause_instance_cascade``
            cancels the in-flight graph task (checkpoint-safe — the
            resume path replays from the last committed LangGraph
            node boundary, verified by the LD-4 stability tests).
            The 2s post-pause barrier is a tiny confirmation that
            cancellation settled; it is NOT a strategy. This resolves
            the LD-4 in-flight limitation in the normal-activation
            path: the user now sees the instance flip to ``PAUSED``
            immediately and the activation completes in well under
            the route ceiling.

        Rollback (W-8): if step 3, 4, or 5 raises, the partial state
        is cleared (``watchover_enabled=false`` + ``watchover_context=None``
        + ``watchover_requirement=None`` + audit marker
        ``watchover_transition=rollback``) and the rollback block ALSO
        attempts a best-effort ``resume_instance_cascade`` so the
        instance cannot be left PAUSED with the flag cleared. The
        resume attempt is itself wrapped in a nested try/except — if
        resume also fails we log but do NOT raise (the rollback must
        always re-raise the ORIGINAL activation error). A
        ``watchover_failed`` SSE event is emitted best-effort after
        the resume attempt (H1 + M4 + M5 fixes). Step 6 (SSE on
        success) is best-effort — its failure does NOT trigger
        rollback because the underlying state is already correct.

        Args:
            instance_id: Owning instance identifier.
            requirement: User-supplied requirement string. May be
                ``None`` when only the watcher-side default is wanted.
            user_context: Pre-built context string (skips the
                ``_build_watchover_context`` step when supplied).
                Used by tests and by callers that already have the
                context — production callers pass ``None``.
            resume_message: Optional custom message to deliver to the
                target instance on the post-activation resume. The
                target (watched) instance receives
                ``resume_message or "continue"``; children of the
                cascade resume silently from checkpoint with the
                fixed string ``"resume"``. Mirrors the resume fan-out
                in :meth:`daemon.routers.instances.resume_instance`
                and ``/answer``. Default ``None`` → target gets
                ``"continue"``. Ignored on the terminal-state path
                (Case 2).
            next_command: Optional command to enqueue as a fresh
                message AFTER enabling watchover on the
                terminal-state path (Case 2). The watched instance
                picks the message up on its next dispatch. Default
                ``None`` → no extra message is enqueued. Ignored on
                the running path (Case 1) — running instances use
                ``resume_message`` instead.

        Returns:
            Dict with the activation outcome:

              * ``instance_id`` — echoes the input.
              * ``watchover_enabled`` — always ``True`` on success.
              * ``context_length`` — length of the context string.
              * ``quiescent`` — whether the post-pause barrier
                succeeded (``True``/``False``) on the running path,
                or ``True`` on the terminal-state path (no barrier
                was needed — the instance was already idle).

        Raises:
            KeyError: When the instance is not found.
            Exception: When the activation sequence fails (after
                rollback has cleared any partial state).
        """
        manager = self._manager

        # Case 2 (Watchover Dialog Redesign) — terminal/idle path.
        #
        # Skip the pause → barrier → resume cycle when the instance
        # is NOT actively running. There is nothing in-flight to
        # cancel, so the running-path machinery (``pause_instance_cascade``
        # + bounded barrier + ``_resume_with_graph_restart``) would
        # only add latency (a round-trip to DB + a Task cancel that
        # finds no Task) for no safety benefit. The terminal path
        # builds context from the existing checkpoint, writes the
        # watchover flag atomically, optionally enqueues a follow-up
        # message via ``next_command``, then emits the SSE. The
        # ``is_instance_running`` lookup is defensive — if status
        # cannot be determined we fall back to the running path,
        # which is the historically-proven safe default (the pause
        # and barrier are idempotent on an already-idle instance).
        if not await self._is_instance_running(instance_id):
            return await self._activate_terminal(
                instance_id,
                requirement=requirement,
                user_context=user_context,
                next_command=next_command,
            )

        # Case 1 — running path (PAUSE FIRST — bug fix for the
        # M-2 in-flight hang).
        #
        # The previous ordering ran ``wait_for_instance_quiescent``
        # (30s default) BEFORE pause. When the instance was mid-LLM-call
        # the barrier blocked the full 30s, eating the route-level
        # ``asyncio.wait_for(timeout=30)`` budget and producing a 504
        # — and the user never saw the pause because pause came after
        # the barrier. The fix: pause FIRST. ``pause_instance_cascade``
        # cancels the in-flight graph task (checkpoint-safe; the
        # resume path replays from the last committed LangGraph node
        # boundary, verified by the LD-4 stability tests). The
        # 2s post-pause barrier is a tiny confirmation that
        # cancellation settled — it is NOT a strategy.
        try:
            await manager.pause_instance_cascade(
                instance_id,
                suspension_reason=SuspensionReason.WATCHOVER_SETUP.value,
            )
        except Exception as exc:
            logger.error(
                "watchover_service.activate_watchover(%s): pause failed: %s; "
                "no rollback needed (no flags set yet)",
                instance_id,
                exc,
            )
            raise

        # Step 2 — bounded barrier (confirmation, not strategy).
        #
        # Pause already cancelled the graph task. The 2s barrier just
        # confirms the cancellation has settled before we read state.
        # ``wait_for_instance_quiescent`` is itself best-effort (never
        # raises) and returns the result of an internal ``wait_for``;
        # ``False`` here means the cancellation has not settled yet
        # but is in progress — we proceed anyway.
        quiescent = await manager.wait_for_instance_quiescent(
            instance_id, timeout=2.0
        )

        # Soft warning so operators see the LD-4-style edge case in the
        # logs even though the activation proceeds regardless.
        if not quiescent:
            logger.warning(
                "watchover_service.activate_watchover(%s): post-pause "
                "barrier settled with timeout (2s); proceeding — the "
                "graph task was already cancelled by pause_instance_cascade",
                instance_id,
            )

        context_text: str | None = None
        try:
            # Step 3 — build context.
            if user_context is not None:
                context_text = user_context
            else:
                # Phase 4: pass ``requirement`` into the builder so the
                # LLM can weave it into ``## Requirement`` of the
                # markdown guardrail. No more post-splice — the
                # builder's fallback path also splices the requirement
                # into the degraded output, so it always appears.
                context_text = await self._build_watchover_context(
                    instance_id, requirement=requirement
                )

            # Empty-context guard: if the builder returned empty AND
            # no requirement was supplied, use a sentinel so the
            # activation lifecycle can still write SOMETHING.
            if not context_text:
                if requirement:
                    context_text = f"[Requirement] {requirement}"
                else:
                    context_text = "[Recent activity] (no prior activity)"

            # Step 4 — atomic flag write.
            manager.enable_watchover(
                instance_id,
                requirement=requirement,
                context=context_text,
            )

            # Step 5 — resume + graph restart.
            #
            # ``resume_instance_cascade`` only flips DB status
            # PAUSED → RUNNING; the actual graph re-trigger is the
            # SEPARATE ``resume_processing_job`` call (see
            # :meth:`InstanceManager.resume_processing_job`). The
            # helper pairs both calls and falls back to
            # ``enqueue_message`` when ``resume_processing_job``
            # returns ``None`` (e.g. WATCHOVER_SETUP-suspended turn
            # not present in the selector's filter set). It also
            # dedupes against a pending "continue" already in the
            # message queue so a double-tap does not enqueue a second
            # copy of the same message.
            await self._resume_with_graph_restart(
                instance_id,
                manager,
                resume_message=resume_message,
            )

        except (Exception, asyncio.CancelledError) as exc:
            # Rollback (W-8 + H1 + M5 + M4): clear any partial flag
            # state (including stale context/requirement), best-effort
            # resume the instance so it is never left PAUSED with the
            # flag cleared, emit a watchover_failed SSE so the
            # frontend is not stuck in a stale state, then re-raise
            # the ORIGINAL activation error. None of the rollback
            # sub-steps may raise — they are nested in their own
            # try/except so the re-raise is always the original.
            #
            # W1 fix: also catch ``asyncio.CancelledError``. Python
            # 3.13+ promotes ``CancelledError`` to a ``BaseException``
            # subclass, so a plain ``except Exception`` does NOT catch
            # it. The route layer (``routers/instances.py``) wraps
            # activation in ``asyncio.wait_for(timeout=30)`` and on
            # timeout cancels the inner task with ``CancelledError``.
            # Without this clause the rollback (clear flags + resume)
            # is skipped and the instance is left PAUSED with no
            # recovery path.
            logger.error(
                "watchover_service.activate_watchover(%s): activation failed "
                "(%s); rolling back partial state and re-raising",
                instance_id,
                exc,
            )
            # (1) Clear flags + stale context/requirement + audit
            # marker (M5).
            # CancelledError-safe: the route's outer ``wait_for`` may
            # cancel the inner task on timeout and we still need
            # rollback to fire so the instance is never left PAUSED.
            try:
                manager.set_metadata_many(
                    instance_id,
                    {
                        "watchover_enabled": False,
                        "watchover_context": None,
                        "watchover_requirement": None,
                        "watchover_transition": "rollback",
                    },
                )
            except (Exception, asyncio.CancelledError) as rollback_exc:
                logger.error(
                    "watchover_service.activate_watchover(%s): rollback "
                    "set_metadata_many also failed (%s); operator must "
                    "inspect manually",
                    instance_id,
                    rollback_exc,
                )
            # (2) Best-effort resume — even if step 5 itself raised,
            # attempt to unpause so the instance is never left PAUSED
            # with the flag cleared (H1). Resume failure is logged
            # but NEVER raised.
            # CancelledError-safe: must run on the route's timeout
            # cancel path so the instance is not left PAUSED.
            # Rollback uses the SAME two-step helper as the success
            # path so the graph also restarts after a partial
            # activation; the user-supplied ``resume_message`` is
            # NOT threaded into the rollback (the rollback's job is
            # recovery, not custom-message delivery) — the target
            # gets the default "continue" and children get "resume".
            try:
                await self._resume_with_graph_restart(instance_id, manager)
            except (Exception, asyncio.CancelledError) as resume_exc:
                logger.error(
                    "watchover_service.activate_watchover(%s): rollback "
                    "resume_instance_cascade also failed (%s); instance "
                    "may be left PAUSED — operator must inspect manually",
                    instance_id,
                    resume_exc,
                )
            # (3) Best-effort SSE emit so the frontend does not see a
            # stuck "activating" state (M4). SSE failure is logged
            # but NEVER raised.
            # CancelledError-safe: must run on the route's timeout
            # cancel path so the frontend sees the rollback state.
            try:
                await manager._live_hub.stream_status_change(
                    instance_id, "watchover_failed"
                )
            except (Exception, asyncio.CancelledError) as sse_exc:
                logger.warning(
                    "watchover_service.activate_watchover(%s): rollback "
                    "SSE emit also failed (%s); frontend may see stale state",
                    instance_id,
                    sse_exc,
                )
            raise

        # Step 6 — best-effort SSE emit (do not let SSE errors block).
        try:
            await manager._live_hub.stream_status_change(
                instance_id, "watchover_active"
            )
        except Exception as exc:
            logger.warning(
                "watchover_service.activate_watchover(%s): SSE emit failed: %s",
                instance_id,
                exc,
            )

        logger.info(
            "watchover_service.activate_watchover(%s): activated "
            "(context_length=%d, quiescent=%s)",
            instance_id,
            len(context_text or ""),
            quiescent,
        )

        return {
            "instance_id": instance_id,
            "watchover_enabled": True,
            "context_length": len(context_text or ""),
            "quiescent": quiescent,
        }

    # ------------------------------------------------------------------
    # Watchover Dialog Redesign — terminal/idle activation path
    # ------------------------------------------------------------------

    async def _is_instance_running(self, instance_id: str) -> bool:
        """Return ``True`` when ``instance_id`` is in an actively-running state.

        Watchover Dialog Redesign (2026-08-08). The activation lifecycle
        now branches on the instance's current status: an actively-running
        instance uses the full pause → context → flag → resume flow (Case
        1), while a terminal/idle instance skips the pause/resume cycle
        entirely (Case 2). This helper is the branch predicate.

        Active states are ``"running"`` and ``"active"`` (defensive — the
        canonical :class:`InstanceStatus` enum currently emits
        ``"running"`` only; ``"active"`` is included for any
        compatibility / caller-side aliases observed in the field).
        Every other status (``idle``, ``paused``, ``completed``,
        ``error``, ``terminated``, ``queued``, ``waiting_children``,
        ``failed``, ``waiting``, missing row, or anything unrecognized)
        is treated as NOT running.

        Defensive fallback: when the instance metadata cannot be read
        (DB hiccup, repo error, malformed payload), this returns
        ``True`` so the existing pause → barrier → resume path runs.
        The running path is the historically-proven safe default —
        pause + barrier are idempotent on an already-idle instance,
        so a wrong-but-safe branch is preferable to a wrong-and-broken
        branch.

        Args:
            instance_id: Owning instance identifier.

        Returns:
            ``True`` when the instance status is ``"running"`` /
            ``"active"``; ``False`` for any other value or on error.
        """
        try:
            info = self._manager.get_instance_info(instance_id)
        except Exception as exc:
            # Defensive: if we cannot read the instance (DB hiccup,
            # missing row, repo error) we fall back to the running
            # path — historically the safe default. Log so operators
            # can spot an unexpected branch if it ever fires.
            logger.warning(
                "watchover_service._is_instance_running(%s): could not "
                "read instance info (%s); defaulting to running path "
                "(pause + barrier are idempotent on an idle instance)",
                instance_id,
                exc,
            )
            return True

        status = ""
        if isinstance(info, dict):
            raw = info.get("status", "")
            status = str(raw).lower() if raw is not None else ""
        return status in ("running", "active")

    async def _activate_terminal(
        self,
        instance_id: str,
        *,
        requirement: str | None,
        user_context: str | None = None,
        next_command: str | None = None,
    ) -> dict[str, Any]:
        """Activate watchover for a terminal/idle instance (no pause/resume).

        Watchover Dialog Redesign (2026-08-08). Companion to
        :meth:`activate_watchover` for Case 2 — the instance is NOT
        actively running, so the pause → barrier → resume machinery is
        unnecessary (nothing is in-flight to cancel or restart).
        Instead:

          1. Build the context string. Same builder as the running
             path (``_build_watchover_context``) with the same
             empty-context guard — if the builder returns empty and
             no requirement was supplied, fall back to the same
             sentinel ``"[Recent activity] (no prior activity)"`` so
             the lifecycle always writes SOMETHING.
          2. Atomic flag write — ``manager.enable_watchover`` writes
             the full watchover config (``watchover_enabled``,
             ``watchover_context``, ``watchover_requirement``,
             ``watchover_context_turn``, etc.) in a SINGLE
             ``set_metadata_many`` call so a crash mid-write cannot
             produce torn state. Same call as the running path.
          3. Optionally enqueue ``next_command`` as a NEW message so
             the watched instance picks it up on its next dispatch.
             This is the post-activation message on the terminal path
             — there is no ``resume_instance_cascade`` to carry a
             ``"continue"`` / ``resume_message``, so an explicit
             ``next_command`` is the only way to feed a follow-up
             message to the instance. Best-effort: an enqueue
             failure logs but does NOT roll back the flag — the
             watchover is still enabled, the operator may issue a
             follow-up message themselves.
          4. SSE emit ``status_change: watchover_active`` so the
             frontend sees the transition. Best-effort — SSE failure
             is logged but does NOT roll back the flag.

        No pause, no resume, no rollback block. The instance was
        already idle so a rollback would have nothing to recover.

        Args:
            instance_id: Owning instance identifier.
            requirement: User-supplied requirement string. May be
                ``None``.
            user_context: Pre-built context string (skips the
                ``_build_watchover_context`` step when supplied).
                Mirrors the running-path behaviour.
            next_command: Optional command to enqueue as a new
                message AFTER enabling watchover. ``None`` (default)
                → no extra message is enqueued; the watched
                instance remains idle until the operator issues a
                follow-up.

        Returns:
            Dict with ``instance_id``, ``watchover_enabled=True``,
            ``context_length``, and ``quiescent=True`` (no barrier
            was needed — the instance was already idle).
        """
        manager = self._manager

        # Step 1 — build context (same builder as the running path).
        # The empty-context guard is the same so we never write an
        # empty ``watchover_context`` to the DB.
        if user_context is not None:
            context_text: str | None = user_context
        else:
            # Terminal-activation fix (2026-08-08): ``next_command`` is
            # the most critical input for the watcher — it tells the
            # LLM guardrail what the agent is about to do. The
            # enqueue (Step 3 below) happens AFTER this builder call,
            # so we must thread ``next_command`` in via
            # ``extra_messages`` so the builder actually sees it.
            # Without this the watcher would guardrail against the
            # PRE-``next_command`` conversation — an outdated snapshot.
            extra_messages: list | None = None
            if next_command:
                from langchain_core.messages import HumanMessage

                extra_messages = [HumanMessage(content=next_command)]
            context_text = await self._build_watchover_context(
                instance_id,
                requirement=requirement,
                extra_messages=extra_messages,
            )

        if not context_text:
            if requirement:
                context_text = f"[Requirement] {requirement}"
            else:
                context_text = "[Recent activity] (no prior activity)"

        # Step 2 — atomic flag write. Same call as the running path;
        # the context may be ``None`` at the metadata level (empty
        # string is normalized by Pydantic + DB JSONB), the requirement
        # is independent.
        manager.enable_watchover(
            instance_id,
            requirement=requirement,
            context=context_text,
        )

        # Step 3 — best-effort enqueue of ``next_command``. This is
        # the terminal-path equivalent of the running path's
        # ``_resume_with_graph_restart(resume_message=...)``: there
        # is no cascade to resume, so the only way to deliver a
        # follow-up message is to enqueue one. Failure here is
        # logged but does NOT roll back the flag — watchover is
        # already enabled and the operator can issue a follow-up
        # message themselves if the enqueue fails.
        if next_command:
            try:
                await manager.enqueue_message(
                    instance_id=instance_id,
                    message=next_command,
                    source="watchover_next_command",
                )
            except (Exception, asyncio.CancelledError) as exc:
                # W1 fix: also catch ``asyncio.CancelledError``
                # (Python 3.13+ promotes it to ``BaseException`` so a
                # plain ``except Exception`` would miss it).
                logger.error(
                    "watchover_service._activate_terminal(%s): enqueue "
                    "next_command failed (%s); watchover is active but "
                    "the next command was not delivered",
                    instance_id,
                    exc,
                )

        # Step 4 — best-effort SSE emit. Same call as the running
        # path; ``stream_status_change`` is the frontend signal that
        # flips the toggle UI to "active".
        try:
            await manager._live_hub.stream_status_change(
                instance_id, "watchover_active"
            )
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning(
                "watchover_service._activate_terminal(%s): SSE emit "
                "failed: %s",
                instance_id,
                exc,
            )

        logger.info(
            "watchover_service._activate_terminal(%s): activated "
            "(context_length=%d, next_command=%s)",
            instance_id,
            len(context_text or ""),
            bool(next_command),
        )

        return {
            "instance_id": instance_id,
            "watchover_enabled": True,
            "context_length": len(context_text or ""),
            "quiescent": True,
        }

    # ------------------------------------------------------------------
    # T3.6 — deactivation lifecycle
    # ------------------------------------------------------------------

    async def deactivate_watchover(self, instance_id: str) -> dict[str, Any]:
        """Run the deactivation lifecycle: pause → clear flag → resume.

        Phase 3 (T3.6) / FR-14. Symmetric with :meth:`activate_watchover`
        but without the compaction step — the watcher context is kept
        on disk for audit so an operator can see what the watcher was
        guarding before the toggle-off.

        Rollback policy (H2): once pause succeeds the instance MUST
        NOT be left PAUSED after a deactivation attempt, regardless
        of whether ``disable_watchover`` or the subsequent resume
        raises. The disable+resume sequence is wrapped in a try, and
        any exception in that block triggers a best-effort
        ``resume_instance_cascade`` in a nested try/except (logged,
        not raised) before the ORIGINAL error is re-raised. This
        mirrors the activation rollback pattern (H1) symmetrically.
        The SSE emit is best-effort and lives outside the rollback
        block (it does not affect pause state).

        Args:
            instance_id: Owning instance identifier.

        Returns:
            Dict with the deactivation outcome:

              * ``instance_id`` — echoes the input.
              * ``watchover_enabled`` — always ``False`` on success.

        Raises:
            KeyError: When the instance is not found.
            Exception: When the deactivation sequence fails (after
                rollback has resumed the instance best-effort).
        """
        manager = self._manager

        # Step 1 — pause. ``Exception`` only — this is NOT a rollback
        # path (no partial state pending). If the route's outer
        # ``wait_for`` cancels on timeout, ``CancelledError`` here
        # should propagate (no state to clean up).
        try:
            await manager.pause_instance_cascade(
                instance_id,
                suspension_reason=None,
            )
        except Exception as exc:
            logger.error(
                "watchover_service.deactivate_watchover(%s): pause failed: %s; "
                "no rollback needed (deactivation has no partial state)",
                instance_id,
                exc,
            )
            raise

        try:
            manager.disable_watchover(instance_id)
            # ``disable_watchover`` is symmetric with activation: the
            # bare ``resume_instance_cascade`` only flips the DB
            # status; the helper pairs it with
            # ``resume_processing_job`` (+ ``enqueue_message``
            # fallback) so the graph actually restarts. Deactivation
            # has no user-supplied resume message, so the target gets
            # the default "continue" and children get "resume".
            await self._resume_with_graph_restart(instance_id, manager)
        except (Exception, asyncio.CancelledError) as exc:
            # Rollback (H2): the disable or resume step failed. We must
            # still attempt to unpause the instance so it is never left
            # PAUSED after a deactivation attempt. This mirrors the
            # activation rollback (H1) symmetrically. The resume is
            # best-effort — if it ALSO fails, log but do NOT raise; the
            # original disable/resume error must propagate.
            #
            # CancelledError-safe: the route's outer ``wait_for`` may
            # cancel the inner task on timeout and we still need
            # rollback to fire so the instance is never left PAUSED.
            logger.error(
                "watchover_service.deactivate_watchover(%s): clear/resume "
                "failed: %s; attempting best-effort rollback resume",
                instance_id,
                exc,
            )
            try:
                # Rollback uses the same two-step helper so the
                # graph also restarts on the best-effort recovery
                # path — the bare ``resume_instance_cascade`` was
                # insufficient to unstick a paused instance because
                # it does not re-trigger the graph.
                await self._resume_with_graph_restart(instance_id, manager)
            except (Exception, asyncio.CancelledError) as resume_exc:
                logger.error(
                    "watchover_service.deactivate_watchover(%s): rollback "
                    "resume also failed (%s); instance may "
                    "be left PAUSED — operator must inspect manually",
                    instance_id,
                    resume_exc,
                )
            raise

        try:
            await manager._live_hub.stream_status_change(
                instance_id, "watchover_inactive"
            )
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning(
                "watchover_service.deactivate_watchover(%s): SSE emit failed: %s",
                instance_id,
                exc,
            )

        logger.info(
            "watchover_service.deactivate_watchover(%s): deactivated",
            instance_id,
        )

        return {
            "instance_id": instance_id,
            "watchover_enabled": False,
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _build_compaction_context(
    *,
    manager: "InstanceManager",
    instance_id: str,
    messages: list[Any],
    last_compacted_at: str | None,
) -> Any:
    """Build a :class:`CompactionContext` for the watchover snapshot.

    Defensive against a missing compactor — the caller already
    short-circuits when ``manager._compactor is None``. Tolerates a
    missing or zero ``system_prompt_tokens`` by passing ``0`` (the
    compactor only needs it for threshold math).
    """
    # Local import to avoid a module-level cycle: compaction imports
    # nothing from manager, but importing it here keeps the service
    # importable even when the compactor module is mocked.
    from daemon.compaction import CompactionContext, _extract_msg_timestamps

    # F1 fix (2026-09-01) — pre-stamp the first-appearance
    # ``{msg_id: iso_ts}`` map so the SECTION DETAIL conversation-time
    # clause renders in the doc (architect §4). F2 fix (2026-09-01) —
    # pass ``instance_id`` so the doc id is
    # ``compaction-global-{iid}-{seq}`` (not
    # ``compaction-global--{seq}``) and seq is per-instance.
    return CompactionContext(
        messages=messages,
        system_prompt_tokens=0,
        model_name=manager.config.llm.model,
        # NOTE: manager.config (PUBLIC attribute) is correct. The earlier
        # ``manager._config`` typo passed CI only because MagicMock
        # auto-creates missing attributes — production crash on first
        # activation with compaction enabled (C1 fix).
        config=manager.config.compaction,
        llm_config=_llm_config_from_manager(manager),
        last_compacted_at=last_compacted_at,
        instance_id=instance_id,
        msg_timestamps=_extract_msg_timestamps(messages),
    )

    from daemon.compaction import CompactionContext

    return CompactionContext(
        messages=messages,
        system_prompt_tokens=0,
        model_name=manager.config.llm.model,
        # NOTE: manager.config (PUBLIC attribute) is correct. The earlier
        # ``manager._config`` typo passed CI only because MagicMock
        # auto-creates missing attributes — production crash on first
        # activation with compaction enabled (C1 fix).
        config=manager.config.compaction,
        llm_config=_llm_config_from_manager(manager),
        last_compacted_at=last_compacted_at,
    )
