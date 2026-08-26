"""Targeted tests for ``send_message`` status guard in ``daemon/tools/instance.py``.

Branch: fix/revive-stale-job-lookup — Fix 5.

The old guard ``if instance_info.get("terminated")`` was always false because
the instance_info dict (from ``manager.get_instance_info()``) does not contain
a ``"terminated"`` key — it carries the live ``"status"`` field instead. The
fix replaces that with an explicit status check against TERMINATED and ERROR.

A dead (terminated or errored) instance must be rejected by ``send_message`` so
the caller does not enqueue work that will never be processed. Live instances
(idle, running) must pass through the guard.

These tests invoke the real ``send_message`` closure by:
  1. Calling ``create_instance_tools`` with all heavy factory helpers patched
     out (mirrors the pattern in ``tests/unit/tools/test_knowledge_tools.py``).
  2. Extracting the ``send_message`` tool from the returned list.
  3. Invoking ``tool.coroutine(instance_id, message)`` to call the underlying
     async function.

Note: ``send_message`` does not raise; it RETURNS a tool-response string
starting with ``"ERROR:"`` for rejected instances. The LLM sees this string
in the tool result and can act on it. Verifying the return value (not the
exception) is the correct contract.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


from tests.helpers.send_message_fixtures import (
    get_send_message_tool as _get_send_message_tool,
    make_send_message_manager as _make_manager,
    patch_heavy_helpers as _patch_heavy_helpers,
)


# The ``_make_manager`` alias above is retained for call-site locality
# (every test in this file uses ``_make_manager(status="...")``). See
# ``tests/helpers/send_message_fixtures.py`` for the shared baseline
# (``make_send_message_manager``) — the only attribute this suite adds
# beyond the baseline is the docstring contract above, which documents
# the routing rules ``send_message`` enforces.


# =============================================================================
# Tests
# =============================================================================


class TestSendMessageStatusGuard:
    """Regression tests for the ``send_message`` status guard in
    ``daemon/tools/instance.py``.

    Phase 1 (agent-instance-tools) expanded the guard from a simple
    "reject TERMINATED / ERROR" check into an EXHAUSTIVE status-based
    routing table (delta-fix #2). These tests assert the new contract:

      * COMPLETED / TERMINATED / ERROR / FAILED → REVIVE + ENQUEUE
        (Task 4, D2 — lift terminal-state rejection).
      * RUNNING / WAITING_CHILDREN → INJECTION (Task 3, R-O3 + R-O4).
      * IDLE / WAITING / QUEUED → enqueue parity (Task 3 e-bis).
      * PAUSED → REJECT (Task 5, R-O1 — NO auto-resume, NO
        ``resume_instance`` reference).
      * Not-found → friendly error (delta-fix #1).

    The original "reject TERMINATED/ERROR" semantics is intentionally
    DELETED — see ``tests/unit/tools/test_instance_tools.py`` for the
    new test cases that lock the routing-table contract in.

    Test method structure: each method is wrapped in
    ``with patch("daemon.tools.instance._check_team_membership", ...)``
    so the team-membership gate stays open for the full duration of
    the coroutine (the helper-stack patches are torn down BEFORE
    ``send_message.coroutine`` runs).
    """

    async def test_send_message_revives_terminated_instance(self):
        """A TERMINATED instance is REVIVED via the shared
        ``_prepare_enqueued_message`` path (Phase 1 Task 4, D2). The tool
        result pre-pends ``"Instance was terminated — revived and message
        dispatched."`` so the calling LLM can reason about the
        transition.

        Prior to Phase 1, TERMINATED was rejected with an ERROR string.
        That branch was removed intentionally (D2) — completion reports
        still flow through even when the prior run terminated. The new
        behavior is the explicit requirement of the agent-instance-tools
        plan Exit Criterion.
        """
        from unittest.mock import patch

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="terminated")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine(
                "dead-instance-001", "hello"
            )

            # The function returns (does not raise). Verify the REVIVAL text.
            assert isinstance(result, str), f"Expected str, got {type(result)}"
            assert "Instance was terminated — revived and message dispatched." in result, (
                f"Expected revival prefix, got: {result!r}"
            )
            # The enqueue path WAS taken — TERMINATED now flows through the
            # shared ``_prepare_enqueued_message`` revive path.
            manager.enqueue_message.assert_awaited_once()
            # And NOT through the injection path.
            manager.set_injection.assert_not_called()

    async def test_send_message_revives_errored_instance(self):
        """An ERROR instance is REVIVED via the shared
        ``_prepare_enqueued_message`` path (Phase 1 Task 4, D2).

        Prior to Phase 1, ERROR was rejected with an ERROR string. That
        branch was removed intentionally (D2). The new behavior is the
        explicit requirement of the agent-instance-tools plan Exit
        Criterion.
        """
        from unittest.mock import patch

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="error")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine(
                "errored-instance-002", "hello"
            )

            assert isinstance(result, str)
            assert "Instance was error — revived and message dispatched." in result, (
                f"Expected revival prefix, got: {result!r}"
            )
            manager.enqueue_message.assert_awaited_once()
            manager.set_injection.assert_not_called()

    async def test_send_message_accepts_idle_instance(self):
        """An IDLE instance flows through the enqueue-parity branch.

        IDLE / WAITING / QUEUED are non-eligible non-terminal states —
        they round out the InstanceStatus enum to 10 states and route
        via ``enqueue_message(...)`` (test e-bis exhaustiveness).
        """
        from unittest.mock import patch

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="idle")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine(
                "live-instance-idle", "hello from parent"
            )

            # The function does not return an error string for live instances.
            # The exact success response varies (it includes the message_id);
            # what matters is that the routing helper did not reject it.
            assert not (isinstance(result, str) and result.startswith("ERROR")), (
                f"Idle instance should not be rejected; got: {result!r}"
            )

            # The enqueue path WAS taken: enqueue_message was called.
            manager.enqueue_message.assert_awaited_once()
            # And NOT the injection path — IDLE is not injection-eligible.
            manager.set_injection.assert_not_called()

    async def test_send_message_routes_running_instance_via_injection(self):
        """A RUNNING instance routes through ``set_injection(...)``
        (Phase 1 Task 3, R-O3 + R-O4 — agent-tool injection parity with
        the user messages API).

        Prior to Phase 1, RUNNING was queued via ``enqueue_message(...)``
        just like IDLE. That path no longer exists — RUNNING /
        WAITING_CHILDREN inject into the live turn. Tool-pairing safety
        is preserved by the existing ``_ensure_tool_result_pairing``
        guard at ``daemon/graph.py:2893``.
        """
        from unittest.mock import patch

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="running")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine(
                "live-instance-running", "hi"
            )

            assert not (isinstance(result, str) and result.startswith("ERROR")), (
                f"Running instance should not be rejected; got: {result!r}"
            )
            # The injection path WAS taken.
            manager.set_injection.assert_called_once()
            # The queue-busy guard is DROPPED for the injection branch (D11 /
            # R-O3 — status is the source of truth). The queue stats helper
            # is therefore NOT called for RUNNING targets.
            manager.get_queue_stats.assert_not_called()
            # And NOT the enqueue path.
            manager.enqueue_message.assert_not_called()
            # The injection result includes the R-O2 W3 stranding sentence
            # verbatim (leader decision b — both the PAUSED-reject text and
            # the W3 stranding note MUST ship together; an implementer
            # cannot ship one without the other).
            assert "pause-loss parity with the user messages API" in result, (
                f"Injection result must include the W3 stranding caveat; got: {result!r}"
            )

    async def test_send_message_revives_terminal_when_queue_idle(self):
        """A TERMINATED instance with an IDLE queue flows through the
        revive branch. The queue-busy guard STAYS for the enqueue
        branch (D11 / R-O3 — it serializes terminal-revives against
        in-flight child reports).

        Prior to Phase 1, TERMINATED was rejected BEFORE the queue-busy
        guard could fire. The Phase 1 contract is that TERMINATED
        always revives when the queue is idle; the queue-busy guard
        catches the "in-flight child reports" race in the downstream
        check (which is exercised by test ``e-bis`` in
        ``tests/unit/tools/test_instance_tools.py``).
        """
        from unittest.mock import patch

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="terminated")
            # Empty queue → enqueue proceeds. The queue-busy guard STAYS
            # for the enqueue branch but does not fire here.
            manager.get_queue_stats = AsyncMock(
                return_value={"pending_count": 0, "processing_count": 0}
            )
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine(
                "terminated-idle-queue", "x"
            )

            # The revive path was taken — the revival prefix is in the
            # result.
            assert "Instance was terminated — revived and message dispatched." in result
            manager.enqueue_message.assert_awaited_once()

    async def test_send_message_status_check_does_not_rely_on_deprecated_terminated_key(self):
        """Regression guard: the old code checked
        ``instance_info.get("terminated")`` which was always False (the
        key doesn't exist on the dict — only ``status`` does).

        Phase 1 routes via ``_route_send_message`` which reads the
        ``status`` field. The deprecated-key check is dead code now —
        we verify by inspecting the production routing helper directly.
        """
        from daemon.tools.instance import _route_send_message

        # ``status="terminated"`` with no "terminated" key on the dict.
        info = {"status": "terminated"}
        assert "terminated" not in info, (
            "Test invariant: instance_info dict has no 'terminated' key, "
            "only 'status'"
        )

        # Build a manager that returns this dict.
        manager = _make_manager(status="terminated")
        manager.get_instance_info = MagicMock(return_value=info)

        result = _route_send_message(manager, "any-id")
        # The helper correctly classifies TERMINATED as enqueue-revive
        # (Phase 1 Task 3 + Task 4). It does NOT return None — that
        # would indicate the old key-based check fired.
        assert result is not None, (
            "Routing helper must classify via the 'status' field, not "
            "the deprecated 'terminated' key"
        )
        routed_via, prior_status = result
        assert routed_via == "enqueue-revive"
        assert prior_status == "terminated"
