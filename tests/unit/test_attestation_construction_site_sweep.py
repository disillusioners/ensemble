"""Construction-site sweep — predicate-vs-shape traceability guard
(2026-09-07 review suggestion, ADOPTED).

The report-masquerade hole existed because the scanner's exclusion
matrix was tested against HAND-MODELED kwargs while the production
constructors emitted a different (bare) shape. This file closes that
test-design gap permanently: every injection constructor in the
feature is imported REAL and its produced shape is classified by the
REAL ``is_real_user_message`` predicate.

Constructors under sweep:

1. ``daemon.graph.nudge_node`` — the empty-response nudge
   (stamped ``injected_message=True``).
2. ``daemon.graph.create_language_check_node`` — the language-check
   reminder (stamped ``injected_message=True``; keeps the
   ``language_check_reminder`` routing kwarg). Driven through the
   REAL node constructor with ``enabled`` semantics local to the
   constructor arg — the test does NOT depend on the
   ``LANGUAGE_CHECK_ENABLED`` config flag (default False).
3. ``daemon.services.instance_messaging._build_graph_input`` — the
   enqueue-lane constructor (stamped shape for the four internal
   namespaces; BARE for user / API sources — both directions pinned).
4. ``daemon.graph.create_attestation_gate_node`` deny-path injection —
   the attestation nudge (``attestation_nudge=True`` marker).

If any constructor's shape drifts so its message would (or would not)
be classified as a real user message, a test HERE fails — this is the
tripwire that would have caught the whole bug class.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from daemon.graph import (
    ATTESTATION_NUDGE_TEXT,
    NUDGE_MESSAGE,
    create_attestation_gate_node,
    create_language_check_node,
    nudge_node,
)
from daemon.services.attestation_gate import GateSettings, build_gate_config
from daemon.services.attestation_scanner import is_real_user_message
from daemon.services.instance_messaging import _build_graph_input


class TestEnqueueLaneConstructorSweep:
    """``_build_graph_input`` — stamped shape for internal sources,
    BARE for user / API sources (both directions are the contract)."""

    def test_internal_sources_stamped_and_not_real_user(self) -> None:
        for source in (
            "internal_report:child-iid:m-1",
            "internal_error_report:child-iid:m-1",
            "internal_agent:caller-iid",
            "system:watchdog",
        ):
            built = _build_graph_input(
                "internal delivery", "mid-1", message_source=source
            )
            user_message = built["messages"][-1]
            assert user_message.additional_kwargs == {
                "injected_message": True,
                "source": source,
            }, source
            assert is_real_user_message(user_message) is False, source

    def test_user_and_api_sources_bare_and_real_user(self) -> None:
        """The other half of the stamped-shape contract: user / API
        deliveries stay BARE (no additional_kwargs) — they genuinely
        are real user messages. Pinning the EMPTY kwargs here is what
        stops the stamp from over-reaching onto real users."""
        for source in (None, "api", "web", "telegram:user:1"):
            built = _build_graph_input(
                "please ship it", "mid-2", message_source=source
            )
            user_message = built["messages"][-1]
            assert user_message.additional_kwargs == {}, (source,)
            assert is_real_user_message(user_message) is True, source


class TestNudgeNodeConstructorSweep:
    """``nudge_node`` — the empty-response nudge."""

    def test_produced_shape_not_real_user(self) -> None:
        result = nudge_node({"messages": []})
        msg = result["messages"][0]
        assert isinstance(msg, HumanMessage)
        assert msg.content == NUDGE_MESSAGE
        assert msg.additional_kwargs == {
            "injected_message": True,
            # Empty-response-guard Phase 1: dedicated nudge marker the S1
            # validator's §8.1 nudge allowance keys on (injected_message
            # alone is shared with context blocks and reminders).
            "empty_response_nudge": True,
        }
        assert is_real_user_message(msg) is False


class TestLanguageCheckReminderConstructorSweep:
    """``create_language_check_node`` — the wrong-language reminder,
    driven through the REAL node constructor (no config-flag
    dependency)."""

    def test_produced_shape_not_real_user(self) -> None:
        node = create_language_check_node("English")
        state = {
            "messages": [
                HumanMessage(content="hi"),
                AIMessage(content="こんにちは、これはテストです"),
            ],
            "language_check_count": 0,
        }
        result = asyncio.run(node(state))
        reminder = result["messages"][0]
        assert isinstance(reminder, HumanMessage)
        # The routing kwarg the retry-counter scan keys on is
        # preserved alongside the injection stamp.
        assert reminder.additional_kwargs["language_check_reminder"] is True
        assert reminder.additional_kwargs["injected_message"] is True
        assert is_real_user_message(reminder) is False


class TestAttestationGateNudgeConstructorSweep:
    """``create_attestation_gate_node`` deny path — the attestation
    nudge (the original self-reference-trap injection)."""

    def test_deny_path_produced_shape_not_real_user(self) -> None:
        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 0
        ledger = MagicMock()
        ledger.increment.return_value = 1
        ledger.reset.return_value = True
        node = create_attestation_gate_node(
            build_gate_config("sweep-unit", GateSettings("enforce", 3, 3)),
            GateSettings("enforce", 3, 3),
            manager,
            "sweep-unit",
            denied_count_getter=lambda: 0,
            ledger=ledger,
        )
        result = asyncio.run(
            node(
                {
                    "messages": [
                        HumanMessage(content="please do it"),
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "send_message",
                                    "args": {"target": "child"},
                                    "id": "c1",
                                }
                            ],
                        ),
                        AIMessage(content="done without attesting"),
                    ]
                },
                config={"configurable": {"thread_id": "sweep-unit"}},
            )
        )
        nudge = result["messages"][0]
        assert isinstance(nudge, HumanMessage)
        assert nudge.content == ATTESTATION_NUDGE_TEXT
        assert nudge.additional_kwargs["attestation_nudge"] is True
        assert is_real_user_message(nudge) is False
