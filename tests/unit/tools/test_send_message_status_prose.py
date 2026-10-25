"""D4(iv) — surface the enqueue outcome in the agent-tool prose.

``AsyncMessageResult.status`` was SWALLOWED: the ``send_message`` tool
read only ``result.message_id`` and returned a prose string that said
"Message queued and sent to <id>" without ever telling the caller what
the enqueue actually reported. The convene paths had the same gap.

Phase 0 adds ONE shared formatter (``_format_enqueue_result_status``)
and applies it to every prose-returning enqueue path:

* ``send_message``            → appended to the return prose.
* ``convene_council``         → appended to the ``hint``.
* ``convene_council_with_skill`` → appended to the ``hint``.

The formatter renders ``status={result.status}`` and, when
``result.queued is False``, a reason-scoped sentence.

W1 (2026-10-24) SPLIT THE TWO NOT-QUEUED REASONS: there are TWO
distinct reasons a message can be ``queued=False``, and the pre-split
prose asserted "the target was NOT woken" for BOTH — a false marker in
the refresh-fail case (the dispatch Task row EXISTS there, so the
target IS woken):

* ``deferred_pause_marker`` — the marker guard skipped the dispatch
  Task → the message row survives as an audit record only and the
  target was NOT woken.
* ``task_id_refresh_failed`` — the dispatch Task row was created but
  its id could not be read back → the message WAS committed and the
  target WILL be woken.

An absent/unknown reason degrades to a SOFTENED sentence that claims
nothing about whether the target was woken.

The status vocabulary is unchanged: ``"queued"`` on every build site.
No new status value is invented.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.services.messaging_types import AsyncMessageResult
from daemon.tools.instance import _format_enqueue_result_status
from tests.helpers.send_message_fixtures import (
    get_send_message_tool,
    make_send_message_manager,
)


def _enqueue_result(
    *,
    status: str = "queued",
    queued: bool = True,
    not_queued_reason: str | None = None,
) -> AsyncMessageResult:
    """A REAL ``AsyncMessageResult`` (the class the service builds)."""
    return AsyncMessageResult(
        message_id="msg-abc-123",
        instance_id="target-id",
        status=status,
        job_id="job-abc-123",
        queued=queued,
        not_queued_reason=not_queued_reason,
    )


class TestStatusFormatter:
    """The ONE shared formatter."""

    def test_renders_the_status(self):
        assert _format_enqueue_result_status(_enqueue_result()) == "status=queued"

    def test_renders_the_marker_guard_sentence_when_not_queued(self):
        text = _format_enqueue_result_status(
            _enqueue_result(
                queued=False, not_queued_reason="deferred_pause_marker"
            )
        )
        assert text.startswith("status=queued")
        assert "queued=False" in text
        assert "marker_guard" in text
        assert "NOT woken" in text

    def test_the_refresh_fail_reason_does_not_claim_the_target_was_not_woken(
        self,
    ):
        """W1 — the false-marker corner. The dispatch Task row EXISTS
        in the refresh-fail case, so the prose must say the target WILL
        be woken."""
        text = _format_enqueue_result_status(
            _enqueue_result(
                queued=False, not_queued_reason="task_id_refresh_failed"
            )
        )
        assert "queued=False" in text
        assert "NOT woken" not in text
        assert "WILL be woken" in text
        assert "marker_guard" not in text

    def test_an_unknown_reason_degrades_to_the_softened_sentence(self):
        """No reason → claim NOTHING about whether the target was
        woken."""
        text = _format_enqueue_result_status(_enqueue_result(queued=False))
        assert "queued=False" in text
        assert "NOT woken" not in text
        assert "WILL be woken" not in text
        assert "marker_guard" not in text

    def test_omits_the_marker_guard_sentence_when_queued(self):
        text = _format_enqueue_result_status(_enqueue_result(queued=True))
        assert text == "status=queued"
        assert "marker_guard" not in text

    def test_tolerates_a_missing_result(self):
        """The formatter must never raise on a tool-result path."""
        assert _format_enqueue_result_status(None) == "status=None"

    def test_reports_the_status_verbatim(self):
        """No new vocabulary is invented — the service's value is
        reported as-is."""
        assert _format_enqueue_result_status(_enqueue_result(status="queued")) == (
            "status=queued"
        )


class TestSendMessageProse:
    """``send_message`` surfaces the enqueue outcome."""

    async def test_prose_carries_the_status(self):
        manager = make_send_message_manager(status="waiting_children")
        manager.enqueue_message = AsyncMock(return_value=_enqueue_result())
        send_message = get_send_message_tool(manager)

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            result = await send_message.coroutine("target-id", "wake up")

        assert "Message queued and sent to target-id" in result
        assert "status=queued" in result
        # The happy path must NOT carry the marker-guard sentence.
        assert "marker_guard" not in result

    async def test_prose_carries_the_marker_guard_sentence(self):
        manager = make_send_message_manager(status="waiting_children")
        manager.enqueue_message = AsyncMock(
            return_value=_enqueue_result(
                queued=False, not_queued_reason="deferred_pause_marker"
            )
        )
        send_message = get_send_message_tool(manager)

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            result = await send_message.coroutine("target-id", "wake up")

        assert "Message queued and sent to target-id" in result
        assert "status=queued" in result
        assert "queued=False" in result
        assert "marker_guard" in result

    async def test_the_terminal_revive_prefix_is_preserved(self):
        """The status fragment is APPENDED — the revive prefix and the
        original prose are unchanged."""
        manager = make_send_message_manager(status="completed")
        manager.enqueue_message = AsyncMock(return_value=_enqueue_result())
        send_message = get_send_message_tool(manager)

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            result = await send_message.coroutine("target-id", "wake up")

        assert result.startswith("Instance was completed — revived and message dispatched.")
        assert "Message queued and sent to target-id" in result
        assert result.endswith("status=queued")


class TestConvenePaths:
    """The convene paths use the SAME shared formatter."""

    @staticmethod
    def _make_convene_manager() -> MagicMock:
        manager = MagicMock()
        manager.ensure_mcp_preloaded = AsyncMock(return_value=None)
        manager.config = MagicMock()
        manager.config.llm = MagicMock()
        manager.config.llm.allowed_models = ["agentic", "coding", "vision"]
        manager.spawn_instance = MagicMock(return_value=("governor-instance-id", None))
        manager.enqueue_message = AsyncMock(return_value=_enqueue_result())
        manager._instance_repository = MagicMock()
        manager._instance_repository.get = MagicMock(return_value=None)
        return manager

    @staticmethod
    def _get_tool(manager: MagicMock, tool_name: str):
        from daemon.tools.instance import create_instance_tools

        from tests.helpers.send_message_fixtures import patch_heavy_helpers

        patches = patch_heavy_helpers()
        for p in patches:
            p.start()
        try:
            tools = create_instance_tools(manager, "parent-instance-id", "leader")
        finally:
            for p in reversed(patches):
                p.stop()
        for t in tools:
            if getattr(t, "name", None) == tool_name:
                return t
        raise RuntimeError(f"{tool_name} tool not found")

    @pytest.mark.parametrize(
        "tool_name",
        ["convene_council", "convene_council_with_skill"],
    )
    async def test_convene_hint_carries_the_status(self, tool_name):
        manager = self._make_convene_manager()
        tool = self._get_tool(manager, tool_name)

        kwargs = {"councilor_agent_id": "developer", "request": "Refactor X"}
        if tool_name == "convene_council_with_skill":
            kwargs["councilor_skill"] = "python-pro"

        with (
            patch(
                "daemon.registry.AgentRegistry.resolve_to_id",
                return_value="developer",
            ),
            patch(
                "daemon.tools.instance._check_team_membership",
                return_value=None,
            ),
            patch(
                "daemon.tools.instance._register_child_completion_watcher",
                return_value=None,
            ),
        ):
            result = await tool.coroutine(**kwargs)

        assert result["status"] == "convened"
        assert "status=queued" in result["hint"]
        assert "marker_guard" not in result["hint"]

    async def test_convene_hint_carries_the_marker_guard_sentence(self):
        manager = self._make_convene_manager()
        manager.enqueue_message = AsyncMock(
            return_value=_enqueue_result(
                queued=False, not_queued_reason="deferred_pause_marker"
            )
        )
        tool = self._get_tool(manager, "convene_council")

        with (
            patch(
                "daemon.registry.AgentRegistry.resolve_to_id",
                return_value="developer",
            ),
            patch(
                "daemon.tools.instance._check_team_membership",
                return_value=None,
            ),
            patch(
                "daemon.tools.instance._register_child_completion_watcher",
                return_value=None,
            ),
        ):
            result = await tool.coroutine(
                councilor_agent_id="developer", request="Refactor X"
            )

        assert "status=queued" in result["hint"]
        assert "queued=False" in result["hint"]
        assert "marker_guard" in result["hint"]
