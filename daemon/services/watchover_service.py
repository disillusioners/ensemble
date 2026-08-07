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

    async def _build_watchover_context(
        self, instance_id: str, *, requirement: str | None
    ) -> str:
        """Construct the ``watchover_context`` string for ``instance_id``.

        Phase 4 builder path: reads the conversation from the
        checkpoint state and delegates to
        :class:`WatcherContextBuilder`, which issues a single LLM call
        that produces a structured markdown guardrail document. The
        ``requirement`` is passed as an INPUT to the builder so the
        LLM can weave it into ``## Requirement`` rather than being
        appended as a hybrid post-splice.

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
                    watcher_meta.get("builder_timeout_seconds", 15)
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

    async def activate_watchover(
        self,
        instance_id: str,
        *,
        requirement: str | None,
        user_context: str | None = None,
    ) -> dict[str, Any]:
        """Run the activation lifecycle: quiesce → pause → context → flag → resume.

        Phase 3 (T3.5) orchestration. The sequence is:

          1. ``wait_for_instance_quiescent`` (T3.5b) — best-effort
             barrier against in-flight graph tasks so the context
             snapshot is consistent. **Runs BEFORE pause** (see
             "Quiesce-first rationale" below).
          2. ``pause_instance_cascade`` — soft-pause the instance +
             children so the config flip is atomic from the agent's
             perspective.
          3. ``_build_watchover_context`` (T3.4) — summarise the
             recent conversation (compaction + raw-tail fallback).
          4. ``enable_watchover`` — atomic ``set_metadata_many``
             writes the full flag set.
          5. ``resume_instance_cascade`` — resume the instance.
          6. SSE emit ``status_change: watchover_active`` via
             ``LiveEventHub.stream_status_change`` so the frontend
             sees the transition.

        Quiesce-first rationale (H4 deviation from T3.5 plan wording):
            The quiescence barrier observes ``manager._graph_tasks`` —
            it awaits in-flight tool threads so the subsequent
            ``aget_state`` snapshot is consistent. ``pause_instance_cascade``
            *cancels* the graph task that the barrier awaits. If pause
            ran first, the task would be cancelled/removed BEFORE the
            barrier could observe and await it, making the barrier a
            no-op (always returns ``True`` because there is no task
            to wait for). Keeping the quiescence-first order lets the
            barrier await real in-flight tool threads; pause then
            cancels any straggler. The TOCTOU window between the
            quiescence barrier returning and pause actually landing
            is the accepted LD-4 limitation (a tool call that
            starts AFTER the barrier but BEFORE pause may still race;
            watchover docs advise operators to pause manually before
            activating on a busy instance).

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

        Returns:
            Dict with the activation outcome:

              * ``instance_id`` — echoes the input.
              * ``watchover_enabled`` — always ``True`` on success.
              * ``context_length`` — length of the context string.
              * ``quiescent`` — whether the barrier succeeded
                (``True``/``False``).

        Raises:
            KeyError: When the instance is not found.
            Exception: When the activation sequence fails (after
                rollback has cleared any partial state).
        """
        manager = self._manager

        # Step 1 — quiescence barrier (best-effort; never raises).
        #
        # Quiesce-FIRST (H4, deviation from the original T3.5 plan
        # wording "pause → quiesce → context → flag → resume"):
        #   ``pause_instance_cascade`` cancels the graph task that
        #   the barrier observes (via ``manager._graph_tasks``). If
        #   pause ran first, the task would be removed BEFORE the
        #   barrier could observe and await it — the barrier would
        #   be a silent no-op. Quiesce-first lets the barrier await
        #   in-flight tool threads; pause then cancels any straggler.
        #   The TOCTOU window between quiescence returning and pause
        #   landing is the accepted LD-4 limitation (W-9 / CR-3).
        quiescent = await manager.wait_for_instance_quiescent(instance_id)

        # M3 — promote the timeout to a service-level warning so the
        # operator sees the LD-4 limitation in the logs even though
        # the barrier itself lives in manager.py (out of scope for
        # Phase 3 to modify).
        if not quiescent:
            logger.warning(
                "watchover_service.activate_watchover(%s): quiescence barrier "
                "timed out; context snapshot may be inconsistent (LD-4)",
                instance_id,
            )

        # Step 2 — pause and persist the watchover setup reason on any
        # in-flight task turn suspended by the cascade (Phase 5 / H3).
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

            # Step 5 — resume.
            await manager.resume_instance_cascade(instance_id)

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
            except Exception as rollback_exc:
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
            try:
                await manager.resume_instance_cascade(instance_id)
            except Exception as resume_exc:
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
            try:
                await manager._live_hub.stream_status_change(
                    instance_id, "watchover_failed"
                )
            except Exception as sse_exc:
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
            await manager.resume_instance_cascade(instance_id)
        except Exception as exc:
            # Rollback (H2): the disable or resume step failed. We must
            # still attempt to unpause the instance so it is never left
            # PAUSED after a deactivation attempt. This mirrors the
            # activation rollback (H1) symmetrically. The resume is
            # best-effort — if it ALSO fails, log but do NOT raise; the
            # original disable/resume error must propagate.
            logger.error(
                "watchover_service.deactivate_watchover(%s): clear/resume "
                "failed: %s; attempting best-effort rollback resume",
                instance_id,
                exc,
            )
            try:
                await manager.resume_instance_cascade(instance_id)
            except Exception as resume_exc:
                logger.error(
                    "watchover_service.deactivate_watchover(%s): rollback "
                    "resume_instance_cascade also failed (%s); instance may "
                    "be left PAUSED — operator must inspect manually",
                    instance_id,
                    resume_exc,
                )
            raise

        try:
            await manager._live_hub.stream_status_change(
                instance_id, "watchover_inactive"
            )
        except Exception as exc:
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
