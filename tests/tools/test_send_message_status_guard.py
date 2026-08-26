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


def _patch_heavy_helpers():
    """Return a stack of ``unittest.mock.patch`` context managers that disable
    the heavy ``create_instance_tools`` factory helpers (RAG, knowledge, MCP,
    project, job, mother, OpenCode, DB, infra, context) so only the
    instance-management tools (spawn/send/terminate/list/get) are built.

    NOTE: ``_check_team_membership`` is patched at the test-method level
    (the `with patch(...)` block wraps the whole test), NOT here. The
    helper-stack patches are torn down BEFORE ``send_message.coroutine``
    runs (in ``_get_send_message_tool``), so anything patched here would
    be inactive at call time. The team-membership patch must remain
    active for the duration of the coroutine — hence the test-method
    level ``with`` block.
    """
    from unittest.mock import patch

    return [
        patch("daemon.tools.instance.is_rag_enabled", return_value=False),
        patch("daemon.tools.instance.create_rag_tools", return_value=[]),
        patch("daemon.tools.instance.create_knowledge_tools", return_value=[]),
        patch("daemon.tools.instance.create_inner_soul_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_access_memory_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_project_tools", return_value=[]),
        patch("daemon.tools.instance.create_job_tools_if_available", return_value=[]),
        patch("daemon.tools.instance.create_help_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_critical_notes_tools", return_value=[]),
        patch("daemon.tools.instance.create_project_history_tools", return_value=[]),
        patch("daemon.tools.instance.create_opencode_tools", return_value=[]),
        patch("daemon.tools.instance.create_db_tools", return_value=[]),
        patch("daemon.tools.instance.create_infra_tools", return_value=[]),
        patch("daemon.tools.instance.create_context_tools", return_value=[]),
        patch("daemon.tools.instance._load_mcp_tools", return_value=[]),
        patch("daemon.tools.instance.scan_tools_for_full_docs"),
        patch("daemon.tools.instance._apply_tool_filter", side_effect=lambda tools, *a, **kw: tools),
    ]


def _make_manager(*, status: str) -> MagicMock:
    """Build a mock manager wired for ``send_message`` with a given status.

    The manager exposes:
      * ``get_instance`` (async) — succeeds so ``_resolve_instance_id`` passes.
      * ``get_instance_info`` — returns ``{"status": status, "agent_id": ...}``
        (the contract the production code reads).
      * ``get_queue_stats`` (async) — returns empty counts.
      * ``enqueue_message`` (async) — succeeds (the enqueue path).
      * ``set_injection`` — succeeds (the injection path; Phase 1 RUNNING /
        WAITING_CHILDREN targets route through here).

    Production ``send_message`` (daemon/tools/instance.py) routes based on
    target status:
      * RUNNING / WAITING_CHILDREN → ``manager.set_injection(...)``.
      * COMPLETED / TERMINATED / ERROR / FAILED → ``manager.enqueue_message``
        (revive path via ``_prepare_enqueued_message``).
      * IDLE / WAITING / QUEUED → ``manager.enqueue_message(...)``.
      * PAUSED → reject (no dispatch).

    Tests must mock ``enqueue_message`` as ``AsyncMock`` (awaiting a
    plain ``MagicMock`` raises ``TypeError: object MagicMock can't be
    used in 'await' expression``).
    """
    manager = MagicMock()

    # _resolve_instance_id calls get_instance (async) and find_near_instance.
    async def _get_instance(instance_id):
        return MagicMock(instance_id=instance_id)

    manager.get_instance = _get_instance
    manager.find_near_instance = MagicMock(return_value=[])  # no fuzzy matches

    # The fix reads status from get_instance_info.
    manager.get_instance_info = MagicMock(
        return_value={"status": status, "agent_id": "developer"}
    )

    # Live-instance path: no in-flight messages, enqueue succeeds.
    manager.get_queue_stats = AsyncMock(
        return_value={"pending_count": 0, "processing_count": 0}
    )
    # ``send_message`` dispatches via ``enqueue_message`` (NOT
    # ``enqueue_message_job``). The legacy ``enqueue_message_job``
    # attribute is intentionally NOT called by the tool — the
    # JobItem-mirror path is reserved for external/public entry points.
    # Keep it on the manager as a MagicMock so any straggling code that
    # reads it doesn't accidentally invoke the real implementation, but
    # do not assert against it.
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-abc-123")
    )
    manager.enqueue_message_job = MagicMock(
        return_value=MagicMock(message_id="msg-abc-123")
    )
    # Phase 1 (agent-instance-tools): RUNNING / WAITING_CHILDREN targets
    # route through ``manager.set_injection(...)`` — a synchronous call
    # that appends to the RAM injection FIFO. We mock it as a regular
    # MagicMock returning the appended entry shape (matches
    # ``manager.set_injection`` contract).
    manager.set_injection = MagicMock(
        return_value={"content": "stub", "timestamp": "2026-08-26T00:00:00Z"}
    )
    manager.get_injection_count = MagicMock(return_value=1)
    # Real code path also touches _instance_repository for the waiting_for
    # increment (only when target.parent_id == current_instance_id). To keep
    # the live test deterministic, return None so the increment is skipped.
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    manager.engine = MagicMock()
    manager.write_guard = MagicMock()
    # Live-hub and correlation-manager hooks the production code touches.
    manager._live_hub = MagicMock()
    return manager


def _get_send_message_tool(manager: MagicMock):
    """Build the instance tools and return the ``send_message`` tool object.

    The tool object exposes a ``.coroutine`` attribute that is the actual
    async function decorated by ``@tool``. Invoking it directly bypasses
    Pydantic schema validation (we already know our inputs are valid).
    """
    from daemon.tools.instance import create_instance_tools

    patches = _patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        tools = create_instance_tools(manager, "parent-instance", "developer")
    finally:
        for p in reversed(patches):
            p.stop()

    # Find the send_message tool by name.
    for t in tools:
        if getattr(t, "name", None) == "send_message":
            return t
    raise RuntimeError(
        "send_message tool not found in create_instance_tools output; "
        f"got {[getattr(t, 'name', None) for t in tools]}"
    )


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
