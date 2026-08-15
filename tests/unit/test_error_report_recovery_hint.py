"""Unit tests for the recovery-guidance hint in error reports.

Change 1 of the "Child Error Resilience" feature: every error report that
``ErrorReportingService._send_error_report`` enqueues to the parent now
carries a ``[RECOVERY GUIDANCE]`` suffix.

WHY (verified prod incident f10b7694, 2026-08-15): the parent LLM received
the child error report and chose "wait and see" — but ERROR is a TERMINAL
status, so the child never resumes on its own and the completion gate
closed the parent as COMPLETED, silently losing the child work. The hint
tells the parent LLM, in-band, that waiting cannot work and lists the
valid recovery options.

Covered:
    1. The error report content sent to the parent includes the
       ``[RECOVERY GUIDANCE]`` hint.
    2. The hint appears ALONGSIDE the original error content (agent name,
       error type, severity, details) — nothing is replaced.
    3. The hint is APPENDED (original content still leads), separated by a
       blank line, and is the tail of the message.
    4. The ``RECOVERY_GUIDANCE_HINT`` constant carries the key directives:
       revive AT MOST ONCE, escalate upward instead of endless respawn,
       never wait passively.

The service is driven through the real ``_send_error_report`` async path
with a mock manager — mirrors the pattern in
``tests/test_cascade_integration.py`` (Site 2) and
``tests/unit/test_report_repair.py``. The sync DB half is stubbed via
``WriteGuardSession`` / ``Session`` patches and the DependencyBus singleton
is patched to a stub with zero pending children — no real DB or LLM calls
are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.repositories.instance.models import InstanceStatus
from daemon.services.error_reporting import (
    RECOVERY_GUIDANCE_HINT,
    ErrorReportingService,
)

CHILD_ID = "child-hint-0001"
PARENT_ID = "parent-hint-0001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager() -> MagicMock:
    """Build a mock InstanceManager for the error-report happy path.

    Exposes:
      * ``_instance_repository.get`` — returns child metadata with a parent
      * ``_queue_repository.list`` — empty (no dedup hit)
      * ``enqueue_message`` — AsyncMock whose result carries ``message_id``
      * ``_live_hub`` — None (skip SSE side-effects entirely)
    """
    manager = MagicMock(name="InstanceManager")

    child_meta = MagicMock(name="child_meta")
    child_meta.parent_id = PARENT_ID
    child_meta.agent_name = "tester"
    child_meta.agent_dir = "/tmp/agents/tester"
    manager._instance_repository.get = MagicMock(return_value=child_meta)

    manager._queue_repository.list = MagicMock(return_value=[])

    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="report-msg-0001")
    )
    manager._live_hub = None

    return manager


def _make_stub_session() -> MagicMock:
    """Build the mock session yielded by the patched WriteGuardSession.

    ``session.get`` maps the child row to a mock child instance and the
    parent row to a mock parent still RUNNING with one pending child (the
    bus stub reports zero pending, so the bus-active branch skips the
    inline cascade and the flow reaches the enqueue step).
    """
    child = MagicMock(name="child_instance")
    child.instance_id = CHILD_ID
    child.agent_id = "tester"
    child.parent_id = PARENT_ID
    child.status = InstanceStatus.RUNNING.value
    child.instance_metadata = {}

    parent = MagicMock(name="parent_instance")
    parent.instance_id = PARENT_ID
    parent.agent_id = "leader"
    parent.parent_id = None
    parent.status = InstanceStatus.RUNNING.value
    parent.version = 1

    session = MagicMock(name="session")
    session.get = MagicMock(
        side_effect=lambda cls, iid: {
            CHILD_ID: child,
            PARENT_ID: parent,
        }.get(iid)
    )
    session.execute = MagicMock(return_value=MagicMock(name="exec_result"))
    session.expire = MagicMock()
    session.commit = MagicMock()
    session.add = MagicMock()
    return session


async def _send_error_report_and_get_message(
    *,
    error: str = "LLM call failed with status 400",
    error_type: str = "execution_error",
) -> str:
    """Run the real ``_send_error_report`` and return the enqueued content.

    Drives the full async path (dedup → metadata → sync DB half via
    ``asyncio.to_thread`` → bus hook → CompletionRegistry → enqueue) with
    every external dependency stubbed, then asserts exactly one enqueue
    happened and returns the ``message`` kwarg sent to the parent.
    """
    manager = _make_manager()
    service = ErrorReportingService(manager=manager, events_service=None)

    stub_session = _make_stub_session()
    wgs = MagicMock(name="WriteGuardSession")
    wgs.__enter__ = MagicMock(return_value=stub_session)
    wgs.__exit__ = MagicMock(return_value=False)

    stub_bus = MagicMock(name="DependencyBus")
    stub_bus.count_pending_for_target_sync = MagicMock(return_value=0)

    with patch(
        "daemon.services.dependency_bus.get_dependency_bus",
        return_value=stub_bus,
    ):
        with patch(
            "daemon.services.error_reporting.WriteGuardSession",
            return_value=wgs,
        ):
            with patch(
                "daemon.services.error_reporting.Session",
                return_value=MagicMock(name="raw_session"),
            ):
                with patch(
                    "daemon.services.completion_registry"
                    ".get_completion_registry",
                    return_value=MagicMock(name="CompletionRegistry"),
                ):
                    await service._send_error_report(
                        instance_id=CHILD_ID,
                        error=error,
                        error_type=error_type,
                        message_id=None,
                    )

    assert manager.enqueue_message.await_count == 1, (
        "expected exactly one error-report enqueue to the parent"
    )
    return manager.enqueue_message.await_args.kwargs["message"]


# ---------------------------------------------------------------------------
# 1–3. The report path (content sent to the parent)
# ---------------------------------------------------------------------------


class TestErrorReportContainsHint:
    """The enqueued error report carries the recovery-guidance hint."""

    @pytest.mark.asyncio
    async def test_report_includes_recovery_guidance_hint(self):
        """The message enqueued to the parent includes the hint block."""
        message = await _send_error_report_and_get_message()

        assert "[RECOVERY GUIDANCE]" in message
        assert RECOVERY_GUIDANCE_HINT in message

    @pytest.mark.asyncio
    async def test_hint_alongside_original_error_content(self):
        """Original error content is preserved, not replaced by the hint."""
        error = "LLM call failed with status 400"
        message = await _send_error_report_and_get_message(error=error)

        # Every piece of the original report is still present.
        assert "⚠️ tester encountered an error:" in message
        assert "**Error Type:** execution_error" in message
        assert "**Severity:** warning" in message
        assert f"**Details:** {error}" in message
        # And the hint coexists with it.
        assert "[RECOVERY GUIDANCE]" in message

    @pytest.mark.asyncio
    async def test_hint_appended_not_prepended(self):
        """The original content leads the message; the hint is the tail.

        The hint must not be prepended or interleaved into the error text —
        the parent LLM reads the report top-down and the original error
        context has to remain the leading content.
        """
        message = await _send_error_report_and_get_message()

        # Original report still leads.
        assert message.startswith("⚠️ tester encountered an error:")
        # Hint strictly after the original error details.
        assert message.index("**Details:**") < message.index(
            "[RECOVERY GUIDANCE]"
        )
        # Separated by a blank line and forming the tail of the message.
        assert f"\n\n{RECOVERY_GUIDANCE_HINT}" in message
        assert message.endswith(RECOVERY_GUIDANCE_HINT)

    @pytest.mark.asyncio
    async def test_hint_appended_for_long_errors_too(self):
        """The hint is appended regardless of the original error length."""
        long_error = "x" * 3000  # exercises the [:2000] truncation
        message = await _send_error_report_and_get_message(error=long_error)

        # Truncated details (2000 chars) still present and still leading.
        assert "**Details:** " + ("x" * 2000) in message
        assert message.index("**Details:**") < message.index(
            "[RECOVERY GUIDANCE]"
        )
        assert message.endswith(RECOVERY_GUIDANCE_HINT)


# ---------------------------------------------------------------------------
# 4. The hint constant's key directives
# ---------------------------------------------------------------------------


class TestRecoveryGuidanceHintConstant:
    """The RECOVERY_GUIDANCE_HINT constant carries the key directives."""

    def test_hint_is_a_marked_block(self):
        """The hint is a clearly labelled block."""
        assert RECOVERY_GUIDANCE_HINT.startswith("[RECOVERY GUIDANCE]")

    def test_hint_states_waiting_cannot_recover(self):
        """The hint opens by stating waiting cannot recover the instance."""
        assert "cannot be recovered by waiting" in RECOVERY_GUIDANCE_HINT
        assert "will not resume on its own" in RECOVERY_GUIDANCE_HINT
        assert (
            "Do not wait for the failed instance to recover by itself."
            in RECOVERY_GUIDANCE_HINT
        )

    def test_hint_revive_at_most_once(self):
        """Option 1 tells the parent to revive AT MOST ONCE."""
        revive_line = next(
            line
            for line in RECOVERY_GUIDANCE_HINT.splitlines()
            if "Try revive" in line
        )
        assert "continue" in revive_line
        assert "last checkpoint" in revive_line
        assert "AT MOST ONCE" in revive_line

    def test_hint_escalates_instead_of_infinite_respawn(self):
        """Option 3 stops the loop: escalate upward, no more spawning."""
        replacement_line = next(
            line
            for line in RECOVERY_GUIDANCE_HINT.splitlines()
            if "replacement" in line
        )
        assert "stop retrying" in replacement_line
        assert "report the situation upward" in replacement_line
        assert "instead of spawning again" in replacement_line

    def test_hint_recovery_options_ordering(self):
        """Options appear in the fixed order revive → respawn → escalate."""
        revive_pos = RECOVERY_GUIDANCE_HINT.index("1. Try revive")
        respawn_pos = RECOVERY_GUIDANCE_HINT.index("2. If revive fails")
        escalate_pos = RECOVERY_GUIDANCE_HINT.index("3. If a replacement")
        assert revive_pos < respawn_pos < escalate_pos

    def test_hint_mentions_spawning_replacement_child(self):
        """Option 2 tells the parent to spawn a new child to continue."""
        respawn_line = next(
            line
            for line in RECOVERY_GUIDANCE_HINT.splitlines()
            if "errors again" in line
        )
        assert "spawn a new child instance" in respawn_line
        assert "continue the task" in respawn_line
