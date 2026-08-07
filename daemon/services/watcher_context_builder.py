"""LLM-driven Watcher Context Builder.

Phase 4 of the Watchover feature (2026-08-07). The activation lifecycle
in ``daemon/services/watchover_service.py`` previously used the generic
C3 Compaction to produce a ``watchover_context``. Compaction is a
message-compression primitive — its output is a conversation summary,
not a security guardrail. The builder in this module replaces that
mechanism: a single inline LLM call that analyzes the instance's
conversation and produces a **structured markdown guardrail document**
the watcher (``agents/watcher/soul.md``) reasons over when it
evaluates each tool call.

The builder follows the same LoopRepairer pattern used for
``LoopRepairer._summarize_loop`` (graph.py:1411) and
``WatchoverEvaluator`` (graph.py:3691):
``asyncio.to_thread`` keeps the sync ``llm.invoke`` off the event loop,
and ``asyncio.wait_for`` enforces a hard timeout so a hung LLM
provider never freezes the activation lifecycle.

On timeout / infra error / judgment error the builder falls back to a
raw-tail + static guardrail prefix — the watcher always sees structured
guidance, even when the LLM call fails. The fallback also splices the
operator requirement into the output (the requirement is otherwise an
LLM input, not a post-splice).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

# Lazy imports for graph.py symbols (avoids a daemon.services ↔ daemon.graph
# import cycle at module-load time — graph.py is the heavyweight module
# that imports watchover_service, so we must NOT import graph.py here at
# top level). See ``_get_llm`` and ``_extract_text``.

if TYPE_CHECKING:
    from daemon.manager import InstanceManager


logger = logging.getLogger(__name__)


# Default window size for the message tail fed to the builder LLM.
# Mirrors the design-doc default in meta.json's ``builder_message_window``
# (see ``agents/watcher/meta.json``). 40 messages ≈ 6-8K tokens, well
# within a cheap model's context budget.
DEFAULT_BUILDER_MESSAGE_WINDOW = 40

# Default timeout for the builder LLM call. Set to 300 seconds because
# the builder summarizes potentially long devops/ops conversations
# (the watcher context is a structured markdown document, not a
# single-line verdict). This INDEPENDENT of the watcher's own per-call
# ``timeout_seconds`` (10s) — the watcher evaluates individual tool
# calls quickly, while the builder builds the security profile once
# per activation and may iterate over thousands of messages.
DEFAULT_BUILDER_TIMEOUT_SECONDS = 300

# Fallback prefix applied when the builder LLM call fails or times out.
# Covers the common deny categories from ``agents/watcher/rule.md``
# (system files, credentials, destructive writes, production surfaces).
# The builder call itself produces richer, task-specific guidance — this
# is the degraded-mode baseline so the watcher is never without
# structure even when the LLM provider is unreachable.
_FALLBACK_GUARDRAIL_PREFIX = """## Static Guardrail (degraded mode — builder LLM unavailable)

## Allowed
- read files under the project's working tree
- run the project's standard test suite
- edit files inside the working tree (outside /tmp)

## Forbidden
- any rm -rf on a path starting at / or at a home root
- modify /etc/, /var/, /usr/, /lib/, /boot/, /proc/, /sys/, /sbin/, /bin/
- read or write .env, *.pem, *.key, id_rsa*, ~/.kube/, ~/.aws/, ~/.ssh/
- destructive database ops (DROP TABLE, DROP DATABASE, TRUNCATE)
- modify sshd_config, sudoers, firewall rules, IAM policies
- any production surface (prod, prd, live) without explicit pre-approval
- push --force to main, master, or a release branch

"""


class WatcherContextBuilder:
    """LLM-driven security-profile compiler for watchover activation.

    Mirrors the ``WatchoverEvaluator`` / ``LoopRepairer._summarize_loop``
    pattern: ``asyncio.to_thread`` + ``asyncio.wait_for(timeout)`` so a
    hung LLM provider can never freeze the activation lifecycle.

    Args:
        manager: The owning :class:`InstanceManager`. Used by the
            fallback path to read the watcher's LLM config and the
            instance's message history. Tests can pass a mock.
        llm_config: Session LLM config dict (the manager's
            ``_llm_config`` shape). Cleaned via ``clean_llm_config``
            before constructing ``ThinkingChatOpenAI``.
        builder_prompt: Pre-loaded builder system prompt (the contents
            of ``agents/watcher/builder-prompt.md``). The caller is
            responsible for loading — the builder does not touch the
            filesystem.
        timeout_seconds: Hard ceiling for the builder LLM call
            (default 300s). The builder summarizes potentially long
            devops/ops conversations into a structured markdown
            guardrail document — the cycle can take a few minutes on
            long histories. Activation is a blocking path, but the
            route's outer ``asyncio.wait_for`` (330s) gives the
            builder a 300s headroom + 30s for lifecycle steps.
        message_window: Number of trailing messages to feed the builder
            (default 40). System messages are always retained even
            when the window clips non-system messages.
    """

    def __init__(
        self,
        manager: "InstanceManager",
        llm_config: dict,
        builder_prompt: str,
        timeout_seconds: int = DEFAULT_BUILDER_TIMEOUT_SECONDS,
        message_window: int = DEFAULT_BUILDER_MESSAGE_WINDOW,
    ) -> None:
        self._manager = manager
        self._llm_config = llm_config
        self._builder_prompt = builder_prompt
        self._timeout_seconds = timeout_seconds
        self._message_window = message_window
        # Lazy LLM construction — defer the (cheap) ChatOpenAI build to
        # the first ``build`` call so a manager with a bad ``llm_config``
        # does not break construction.
        self._llm = None

    def _get_llm(self):
        """Lazy-construct the builder LLM (one-time, cached)."""
        if self._llm is None:
            from daemon.graph import ThinkingChatOpenAI, clean_llm_config

            config = clean_llm_config(self._llm_config)
            self._llm = ThinkingChatOpenAI(**config)
        return self._llm

    async def build(
        self,
        messages: list[Any],
        requirement: str | None,
        available_tools: list[str] | None = None,
    ) -> str:
        """Build a markdown watchover_context from the instance's history.

        Produces a structured markdown document the watcher reasons over
        when it evaluates each tool call. On any failure (timeout,
        provider error, parser failure) returns the raw-tail + static
        guardrail fallback so the watcher always has structured
        guidance.

        Args:
            messages: Conversation history (oldest-first). The builder
                feeds the last ``message_window`` non-system messages,
                plus all system messages, into the LLM.
            requirement: Operator-supplied requirement string (may be
                ``None``). Passed into the JSON payload so the LLM can
                echo it into ``## Requirement``. Also spliced into the
                fallback path so the requirement always appears.
            available_tools: Optional list of tool names the instance
                has access to. ``None`` is accepted — the builder
                writes ``(not provided)`` for ``## Available Tools``.

        Returns:
            A non-empty markdown string suitable for use as
            ``watchover_context``. Never raises — all failure paths
            convert to the fallback.
        """
        messages_window = self._serialize_messages(messages)

        # If the message window is empty AND no requirement, the
        # builder has nothing to work with — skip the LLM call and
        # return the static guardrail directly.
        if not messages_window and not requirement:
            return _FALLBACK_GUARDRAIL_PREFIX.rstrip() + "\n"

        try:
            markdown = await self._call_builder_llm(
                messages_window=messages_window,
                requirement=requirement,
                available_tools=available_tools or [],
            )
        except (asyncio.TimeoutError, ConnectionError, OSError) as infra_err:
            # Infrastructure errors (timeout, network, DNS) — log at
            # warning and fall back. Do NOT raise; the watcher must
            # still see structured guidance.
            logger.warning(
                f"[WatcherContextBuilder] infra error ({type(infra_err).__name__}: "
                f"{infra_err}); falling back to raw-tail + static guardrail"
            )
            return self._build_fallback(messages, requirement)
        except Exception as exc:
            # Any other exception (judgment error, serializer crash,
            # etc.) — log and fall back. The watcher's robustness
            # requires the builder to NEVER block activation.
            logger.warning(
                f"[WatcherContextBuilder] builder call raised "
                f"({type(exc).__name__}: {exc}); falling back to "
                f"raw-tail + static guardrail"
            )
            return self._build_fallback(messages, requirement)

        if not markdown or not markdown.strip():
            # Empty LLM response — judgment error path. Fall back.
            logger.warning(
                "[WatcherContextBuilder] builder returned empty content; "
                "falling back to raw-tail + static guardrail"
            )
            return self._build_fallback(messages, requirement)

        return markdown

    async def _call_builder_llm(
        self,
        *,
        messages_window: str,
        requirement: str | None,
        available_tools: list[str],
    ) -> str:
        """Issue the builder LLM call with a hard timeout.

        Mirrors ``LoopRepairer._summarize_loop`` and
        ``WatchoverEvaluator.evaluate``:
        ``asyncio.to_thread(llm.invoke, ...)`` keeps the sync call off
        the event loop; ``asyncio.wait_for(timeout=...)`` enforces a
        hard ceiling so a hung provider never freezes activation.
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        # Lazy import — same cycle-avoidance as in WatchoverEvaluator.
        from daemon.compaction import _extract_text_from_content

        user_payload = json.dumps(
            {
                "message_window": messages_window,
                "requirement": requirement or "(none provided)",
                "available_tools": available_tools,
            },
            ensure_ascii=False,
        )

        llm = self._get_llm()
        response = await asyncio.wait_for(
            asyncio.to_thread(
                llm.invoke,
                [
                    SystemMessage(content=self._builder_prompt),
                    HumanMessage(content=user_payload),
                ],
            ),
            timeout=self._timeout_seconds,
        )
        return _extract_text_from_content(response.content)

    def _serialize_messages(self, messages: list[Any]) -> str:
        """Serialize the trailing window into a compact text block.

        Reuses :func:`watchover_service._format_raw_tail` for the
        tail so the format stays consistent across the freshness path
        and the builder path. System messages are always retained
        even when the window clips non-system messages — the builder
        needs to see the agent's role and tool inventory.

        The system-message prefix and the non-system tail are
        formatted separately and joined, so the system's presence
        is guaranteed regardless of window size.
        """
        from daemon.services.watchover_service import (
            _format_raw_tail,
        )

        if not messages:
            return ""

        # Separate system messages from the rest, then take the trailing
        # window of non-system messages. Concatenate so the LLM sees
        # the system context + the recent activity tail.
        system_msgs: list[Any] = []
        non_system_msgs: list[Any] = []
        for msg in messages:
            msg_type: Any = None
            if isinstance(msg, dict):
                msg_type = msg.get("type") or msg.get("role")
            else:
                msg_type = getattr(msg, "type", None) or getattr(msg, "role", None)
            if msg_type and "system" in str(msg_type).lower():
                system_msgs.append(msg)
            else:
                non_system_msgs.append(msg)

        # Format each slice independently with a high enough limit
        # that nothing is clipped. The system slice is usually short
        # (1-5 messages); the non-system slice is clipped to the
        # builder's window.
        system_part = _format_raw_tail(system_msgs, len(system_msgs) or 1)
        non_system_tail = non_system_msgs[-self._message_window :]
        non_system_part = _format_raw_tail(
            non_system_tail, len(non_system_tail) or 1
        )

        # Concatenate with a blank-line separator so the LLM sees
        # the system context first, then the recent activity tail.
        if system_part and non_system_part:
            return system_part + "\n\n" + non_system_part
        return system_part or non_system_part

    def _build_fallback(
        self,
        messages: list[Any],
        requirement: str | None,
    ) -> str:
        """Build the raw-tail + static guardrail fallback.

        Used when the builder LLM call fails (timeout, infra error,
        empty response, or any unhandled exception). The fallback
        guarantees the watcher always sees structured guidance — the
        static prefix covers universal deny categories, the raw tail
        provides the operator with a snapshot of recent activity,
        and the requirement is spliced so it always appears in the
        output (matching the pre-builder behavior).
        """
        from daemon.services.watchover_service import (
            DEFAULT_RAW_TAIL_MESSAGES,
            _format_raw_tail,
        )

        raw_tail = _format_raw_tail(messages, DEFAULT_RAW_TAIL_MESSAGES)
        parts: list[str] = [_FALLBACK_GUARDRAIL_PREFIX.rstrip()]
        if requirement:
            parts.append(
                f"[Requirement] {requirement}\n\n[Recent activity]\n{raw_tail}"
            )
        elif raw_tail:
            parts.append(raw_tail)
        else:
            parts.append("[Recent activity] (no prior activity)")
        return "\n".join(parts)