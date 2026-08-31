"""D1 seam — structural LangGraph-2013 red/green proof (gateway mimic).

wc-wake-report-integrity (T6 / C1-D1; task T10.2 — strengthens the
prior pass's ImportError-only RED into a STRUCTURAL one).

The prior red proof failed on ``ImportError: _heal_poisoned_checkpoint_tail``
— which proves only that the test needs the helper, not that the 2013
exposure existed. This file has ZERO imports of the helper (or anything
else introduced by the arc), so the SAME test file runs at base and at
HEAD:

  * **base (RED)**: the pipeline drives ``graph.astream`` with the new
    turn's ``HumanMessage`` while the checkpoint tail is a poisoned
    ``AIMessage(tool_calls)`` — the fake graph's gateway validation
    mimics LangGraph/OpenAI-compatible request validation (error code
    2013: ``tool_calls`` follower must be a tool message) and RAISES.
    The test fails through the poisoned-tail shape itself.

  * **HEAD (GREEN)**: the D1 entry seam
    (``_heal_poisoned_checkpoint_tail``) reads the checkpoint via
    ``graph.aget_state``, synthesizes placeholder ``ToolMessage``s with
    the R1 deterministic ids (``pairing-synth-{tc_id}``), and prepends
    them to ``graph_input['messages']`` — the mimic's gateway
    validation passes, no raise, and the captured LLM-seen list shows
    ``AIMessage(tc) → ToolMessage(pairing-synth-*) → HumanMessage``.

The gateway mimic lives entirely in the fake graph (test-side), so the
mimic semantics are identical at base and HEAD — the only difference
is whether production heals the seam.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from daemon.services.cancellation import CancellationService
from daemon.services.instance_messaging import InstanceMessagingService


_TC_ID = "call_poisoned_001"

_GATEWAY_2013 = (
    "2013 - Invalid request: an AIMessage with tool_calls must be "
    "followed by tool messages for every tool_call id before any other "
    "message kind"
)


class _GatewayError2013(ValueError):
    """Mimic of the OpenAI-compatible gateway's error-2013 rejection."""


def _gateway_validate(seen: list[BaseMessage]) -> None:
    """Reject any request payload with an unanswered ``tool_calls`` tail.

    Walks the LLM-bound list the way an OpenAI-compatible gateway does:
    every ``AIMessage.tool_calls`` id must be answered by a
    ``ToolMessage`` before any other message kind may follow.
    """
    unanswered: dict[str, str] = {}
    for msg in seen:
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls or []:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id:
                    unanswered[tc_id] = tc.get("name", "")
        elif isinstance(msg, ToolMessage):
            unanswered.pop(msg.tool_call_id, None)
        else:
            # Any non-tool message (Human/System/AI-without-calls) right
            # after unanswered tool_calls is the 2013 poison shape.
            if unanswered:
                raise _GatewayError2013(
                    f"{_GATEWAY_2013}; unanswered={sorted(unanswered)}"
                )
    if unanswered:
        raise _GatewayError2013(f"{_GATEWAY_2013}; unanswered={sorted(unanswered)}")


def _poisoned_checkpoint_state() -> MagicMock:
    """A LangGraph state whose tail is an unanswered ``AIMessage(tc)``."""
    state = MagicMock()
    state.values = {
        "messages": [
            HumanMessage(content="earlier user turn"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": _TC_ID,
                        "name": "bash",
                        "args": {"x": 1},
                        "type": "tool_call",
                    }
                ],
            ),
        ]
    }
    return state


def _make_service() -> tuple[InstanceMessagingService, MagicMock, list[list]]:
    """Pipeline harness with a gateway-mimicking fake graph.

    The fake ``astream`` reconstructs the payload the gateway would see
    (checkpoint messages + this turn's input messages, i.e.
    ``add_messages`` semantics), records it, and runs the gateway
    validation BEFORE yielding — an unhealed poisoned tail raises the
    2013-equivalent out of the turn.
    """
    manager = MagicMock()
    manager._graph_tasks = {}
    manager._deferred_question_pause = set()

    graph = MagicMock()
    graph.language_check_active = False
    poisoned_state = _poisoned_checkpoint_state()
    graph.aget_state = AsyncMock(return_value=poisoned_state)

    llm_seen: list[list] = []

    async def _astream(graph_input: Any, _config: Any, stream_mode=None):
        # Reconstruct the payload the gateway would see: the persisted
        # checkpoint messages + this turn's input messages (LangGraph
        # ``add_messages`` semantics: the input appends to the
        # checkpoint list). The D1 seam's prepended placeholders ride
        # ``graph_input['messages']`` and therefore land BETWEEN the
        # checkpoint tail and the new HumanMessage.
        seen = list(poisoned_state.values["messages"]) + list(
            graph_input["messages"]
        )
        llm_seen.append(seen)
        _gateway_validate(seen)
        return
        yield  # pragma: no cover - makes this an async generator

    graph.astream = _astream
    manager.get_instance = AsyncMock(return_value=graph)

    service = InstanceMessagingService(
        manager=manager,
        cancellation_service=CancellationService(manager=manager),
    )
    service._maybe_compact_context = AsyncMock()  # type: ignore[method-assign]
    service._maybe_trigger_title_generation = MagicMock()  # type: ignore[method-assign]
    service._has_checkpoint = AsyncMock(return_value=False)  # type: ignore[method-assign]
    service._emit_context_usage = AsyncMock()  # type: ignore[method-assign]

    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._live_hub.stream_tool_result = AsyncMock()
    manager._live_hub.stream_error = AsyncMock()
    manager._llm_semaphore = asyncio.Semaphore()
    manager.config = MagicMock()
    manager.config.limits.graph_recursion_limit = 25
    manager._queue_repository = MagicMock()
    manager._queue_repository.update_activity = MagicMock()
    manager._compactor = None
    manager.source_dispatcher = None
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    manager._instance_repository.set_metadata = MagicMock()
    manager._project_repository = MagicMock()
    manager.shared_meta_kv_repo = MagicMock()
    manager._skill_injection_service = None
    manager._skill_clone_service = None
    manager._skill_metrics_service = None

    return service, manager, llm_seen


@pytest.mark.asyncio
async def test_poisoned_checkpoint_tail_is_healed_at_the_enqueue_seam():
    """Poisoned checkpoint tail + enqueued turn → healed, no 2013 raise.

    GREEN at HEAD: the seam prepends ``pairing-synth-{tc_id}``
    placeholders so the gateway-validated payload
    ``[Human(earlier), AIMessage(tc), ToolMessage(pairing-synth-*),
    Human(new)]`` passes. RED at base (no seam): the payload is
    ``[Human(earlier), AIMessage(tc), Human(new)]`` and the mimic raises
    the 2013-equivalent out of the turn.
    """
    service, manager, llm_seen = _make_service()

    # is_retry=True + _has_checkpoint=False → the "re-add message"
    # branch builds a non-None graph_input (so the seam runs), with the
    # new turn's HumanMessage as the only input message.
    result = await service._process_message_with_tracking(
        instance_id="iid-poison",
        message="fresh wake turn",
        message_id="msg-seam-1",
        is_retry=True,
        silent=False,
    )

    # The turn completed — the gateway accepted the payload.
    assert result is not None

    # The gateway saw exactly one request payload.
    assert len(llm_seen) == 1
    seen = llm_seen[0]

    # The healed order: checkpoint tail, placeholder(s), new turn.
    kinds = [type(m).__name__ for m in seen]
    assert kinds == ["HumanMessage", "AIMessage", "ToolMessage", "HumanMessage"], (
        f"unexpected gateway payload shape: {kinds} ({seen!r})"
    )

    # R1 deterministic placeholder id format, synthesized at the seam.
    placeholder = seen[2]
    assert isinstance(placeholder, ToolMessage)
    assert placeholder.id == f"pairing-synth-{_TC_ID}", (
        f"placeholder id must be pairing-synth-{_TC_ID}, got {placeholder.id!r}"
    )
    assert placeholder.tool_call_id == _TC_ID

    # And the new turn's message is last, unmarked by any pairing fix-up.
    assert isinstance(seen[3], HumanMessage)
    assert seen[3].content == "fresh wake turn"
