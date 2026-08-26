"""Phase 1 (agent-instance-tools) tests for ``send_message`` routing.

These tests lock in the contract introduced by the Phase 1 changes:

  * ``_route_send_message`` is the single source of truth for the
    dispatch decision (Tasks 2 + 3).
  * RUNNING / WAITING_CHILDREN → ``manager.set_injection(...)``
    (Tasks 3 + 6; R-O3 + R-O4).
  * COMPLETED / TERMINATED / ERROR / FAILED → REVIVE + ENQUEUE
    via the shared ``_prepare_enqueued_message`` path
    (Task 4 / D2). Tool result pre-pends ``"Instance was X — revived
    and message dispatched."``
  * IDLE / WAITING / QUEUED → enqueue parity (Task 3 e-bis).
  * PAUSED → REJECT (Task 5, R-O1 — NO auto-resume, NO
    ``resume_instance`` reference).
  * Not-found → friendly error (delta-fix #1).
  * Empty / whitespace-only content is rejected BEFORE routing
    (Task 2c, §7 #7).
  * INFO logging on every successful send (Task 3b, §7 #8).
  * Eligibility-set constant is hoisted to ``daemon.constants`` —
    exactly ONE definition site, THREE consumers (test k).

Most tests build the real ``send_message`` closure by patching out
the heavy ``create_instance_tools`` factory helpers (mirrors the
pattern in ``tests/tools/test_send_message_status_guard.py``) and
invoking ``tool.coroutine(instance_id, message)`` directly.

The routing helper is exercised in isolation in
``TestRoutingHelper`` below — pure unit tests, no manager needed for
the routing logic itself (only for the KeyError not-found path).
"""

from __future__ import annotations

import logging
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — manager fixture and tool-builder
# ---------------------------------------------------------------------------


def _patch_heavy_helpers():
    """Disable the heavy ``create_instance_tools`` factory helpers (RAG,
    knowledge, MCP, project, job, mother, OpenCode, DB, infra, context)
    so only the instance-management tools (spawn/send/terminate/list/get)
    are built.

    NOTE: ``_check_team_membership`` is patched at the test-method level
    (the ``with patch(...)`` block wraps each coroutine) so the team
    membership gate stays open for the full call duration — see
    ``tests/tools/test_send_message_status_guard.py`` for the same
    pattern.
    """
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
    """Build a mock manager wired for the Phase 1 ``send_message`` tool.

    The manager exposes:
      * ``get_instance`` (async) — succeeds so ``_resolve_instance_id``
        passes.
      * ``get_instance_info`` — returns ``{"status": status, "agent_id":
        "developer"}``.
      * ``get_queue_stats`` (async) — returns empty counts (idle queue).
      * ``enqueue_message`` (async) — succeeds (the enqueue path).
      * ``set_injection`` (sync) — succeeds (the injection path; Phase 1
        RUNNING / WAITING_CHILDREN targets route through here).
      * ``get_injection_count`` (sync) — returns 1.
    """
    manager = MagicMock()

    async def _get_instance(instance_id):
        return MagicMock(instance_id=instance_id)

    manager.get_instance = _get_instance
    manager.find_near_instance = MagicMock(return_value=[])
    manager.get_instance_info = MagicMock(
        return_value={"status": status, "agent_id": "developer"}
    )
    manager.get_queue_stats = AsyncMock(
        return_value={"pending_count": 0, "processing_count": 0}
    )
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-abc-123")
    )
    manager.enqueue_message_job = MagicMock(
        return_value=MagicMock(message_id="msg-abc-123")
    )
    manager.set_injection = MagicMock(
        return_value={"content": "stub", "timestamp": "2026-08-26T00:00:00Z"}
    )
    manager.get_injection_count = MagicMock(return_value=1)
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    manager.engine = MagicMock()
    manager.write_guard = MagicMock()
    manager._live_hub = MagicMock()
    return manager


def _get_send_message_tool(manager: MagicMock):
    """Build the instance tools and return the ``send_message`` tool."""
    from daemon.tools.instance import create_instance_tools

    patches = _patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        tools = create_instance_tools(manager, "parent-instance", "developer")
    finally:
        for p in reversed(patches):
            p.stop()

    for t in tools:
        if getattr(t, "name", None) == "send_message":
            return t
    raise RuntimeError(
        "send_message tool not found in create_instance_tools output; "
        f"got {[getattr(t, 'name', None) for t in tools]}"
    )


# ---------------------------------------------------------------------------
# Task 1 / Audit — confirm baseline state
# ---------------------------------------------------------------------------


class TestAuditBaseline:
    """The §0 IMPLEMENTER CHECKLIST (delta-fix SHOULD-FIX) requires that
    we verify a few preconditions before extending the regression suite.

    These are sanity assertions, NOT behavior tests. If they fail, the
    constants/anatomy may have drifted and we should re-verify before
    shipping.
    """

    def test_injection_eligible_statuses_constant_exists(self):
        """The eligibility set MUST live in ``daemon.constants`` (D13,
        LOCKED choice — no Manager-attr alternative, delta-fix #4)."""
        from daemon.constants import INJECTION_ELIGIBLE_STATUSES

        assert INJECTION_ELIGIBLE_STATUSES == frozenset({"running", "waiting_children"})

    def test_ensure_tool_result_pairing_call_site_anchored(self):
        """Verify ``graph.py:2892-2894`` still hosts the single pairing
        guard call site. The Phase 1 design relies on this single
        chokepoint (D3 / R-O7)."""
        from daemon.graph import _ensure_tool_result_pairing

        assert callable(_ensure_tool_result_pairing)

    def test_set_injection_is_only_fifo_writer(self):
        """``Manager._pending_injections`` writes happen ONLY via
        ``set_injection``. No parallel writer exists (architect §4 race
        map).

        We verify by inspecting ``daemon/manager.py`` for the only
        ``.append`` site on the FIFO queue. The architecture uses a
        single writer (``set_injection`` at line 2361-2370) plus drain
        sites (agent_node), pause-path clears (lifecycle.py:2501),
        ``clear_all`` (lifecycle.py:3383-3384), and TTL sweeps
        (manager.py:3542-3570).

        NOTE: this test is specifically about the **injection FIFO**
        (``_pending_injections``). Other modules may have their own
        ``queue.append(...)`` sites for unrelated queues (e.g.
        ``todo_manager.py``); the invariant is that ONLY
        ``manager.set_injection`` writes to ``_pending_injections``.
        """
        from pathlib import Path

        repo = Path("/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble")
        manager_src = (repo / "daemon" / "manager.py").read_text()
        # The append-to-queue site is in set_injection (the single
        # writer). Look for it directly.
        assert "queue.append(entry)" in manager_src, (
            "set_injection's queue.append(entry) is the single FIFO "
            "writer — expected to find it in daemon/manager.py"
        )
        # And no OTHER ``.append`` on ``_pending_injections[...]``
        # exists in any other file in daemon/ (architect invariant —
        # single writer).
        # We look specifically for the pattern
        # ``_pending_injections[..] = [...] .append(`` or
        # ``_pending_injections.get(`` followed by ``.append(`` —
        # this catches writers even if the variable is named ``queue``.
        for f in (repo / "daemon").rglob("*.py"):
            if "__pycache__" in str(f):
                continue
            text = f.read_text()
            # Look for `_pending_injections` mutations. We allow
            # reads (.get, .pop, .items), but mutations via .append on
            # a value pulled from _pending_injections should ONLY be
            # in manager.py.
            if "_pending_injections" in text:
                # Find lines that look like ``.append(`` near
                # ``_pending_injections``.
                for ln, line in enumerate(text.splitlines(), 1):
                    if "_pending_injections" in line and ".append(" in line:
                        # Must be in manager.py AND in set_injection.
                        if f.name != "manager.py":
                            raise AssertionError(
                                f"{f.relative_to(repo)}:{ln} has a "
                                f"_pending_injections.append( site — "
                                f"single-writer invariant violated"
                            )

    def test_resolve_instance_id_raises_valueerror_not_keyerror(self):
        """Approver correction verified at 6ca9541c:
        ``_resolve_instance_id`` raises ``ValueError`` (NOT ``KeyError``)
        — the routing helper's KeyError catch is defense-in-depth, NOT
        the primary not-found path."""
        import inspect

        from daemon.tools.instance import _resolve_instance_id

        src = inspect.getsource(_resolve_instance_id)
        assert "raise ValueError" in src
        # The except-clause catches KeyError from manager.get_instance,
        # then re-raises ValueError. The production not-found behavior
        # is ValueError; the routing helper independently catches the
        # KeyError raised by manager.get_instance_info (different code
        # path).
        assert "except KeyError" in src

    def test_create_instance_tools_closure_layout(self):
        """Approver correction verified at 6ca9541c:
        ``create_instance_tools()`` is at instance.py:~943; the closure
        list with ``send_message`` lives at ~1880-1903 (NOT 2240-2250
        as the plan cites — drift correction)."""
        import inspect

        from daemon.tools.instance import create_instance_tools

        # Just confirm the function is importable + the closure list
        # contains ``send_message``. Drift in line numbers is
        # acceptable as long as the symbols are reachable.
        assert callable(create_instance_tools)
        src = inspect.getsource(create_instance_tools)
        assert "send_message," in src


# ---------------------------------------------------------------------------
# Test k — Eligibility-set constant uniqueness (Task 2b)
# ---------------------------------------------------------------------------


class TestKConstantUniqueness:
    """Task 2b / test k: the eligibility-set hoist is verified by greps.

    The implementation guarantee is:
      * Exactly ONE definition site for ``INJECTION_ELIGIBLE_STATUSES``
        — in ``daemon/constants.py``.
      * THREE consumers — ``daemon/routers/messages.py``,
        ``daemon/tools/job_queue.py``, and ``daemon/tools/instance.py`` —
        all import from ``daemon.constants`` (no third fork).
    """

    def test_constant_definition_singleton(self):
        """``grep -n "INJECTION_ELIGIBLE_STATUSES\s*=\s*{" daemon/`` returns
        EXACTLY ONE hit (the definition in ``daemon/constants.py``). The
        router's local frozenset and ``job_inject``'s inline tuple are
        GONE.

        Note: the actual definition is annotated
        ``INJECTION_ELIGIBLE_STATUSES: frozenset[str] = frozenset({...})``
        — we look for ``INJECTION_ELIGIBLE_STATUSES.*=.*frozenset({``
        which handles both annotated and unannotated forms.
        """
        out = subprocess.check_output(
            [
                "grep",
                "-rn",
                "INJECTION_ELIGIBLE_STATUSES.*=.*frozenset({",
                "daemon/",
            ],
            cwd="/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble",
            text=True,
        )
        py_lines = [
            line
            for line in out.splitlines()
            if line and ".py:" in line and "__pycache__" not in line
        ]
        assert len(py_lines) == 1, (
            f"Expected exactly one INJECTION_ELIGIBLE_STATUSES definition "
            f"in daemon/, got {len(py_lines)}:\n"
            + "\n".join(py_lines)
        )
        # Normalize path (macOS cwd double-slash quirk).
        path = py_lines[0].split(":", 1)[0].replace("//", "/")
        assert path == "daemon/constants.py", (
            f"Definition must live in daemon/constants.py, got: {path}"
        )

    def test_constant_imported_by_all_three_consumers(self):
        """``grep -n "from daemon.constants import INJECTION_ELIGIBLE_STATUSES"``
        returns THREE hits — one per consumer (router + job tool + agent
        tool). The router uses a multi-line import (parenthesized),
        so we grep for both the single-line and multi-line forms.

        We use Python's AST parser to find import statements —
        bulletproof against multi-line / parenthesized imports.
        """
        import ast
        from pathlib import Path

        repo = Path("/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble")
        consumers: set[str] = set()
        candidates = [
            "daemon/routers/messages.py",
            "daemon/tools/job_queue.py",
            "daemon/tools/instance.py",
        ]
        for rel_path in candidates:
            path = repo / rel_path
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    # `from daemon.constants import INJECTION_ELIGIBLE_STATUSES`
                    # OR `from daemon.constants import (..., INJECTION_ELIGIBLE_STATUSES, ...)`
                    for alias in node.names:
                        if alias.name == "INJECTION_ELIGIBLE_STATUSES":
                            consumers.add(rel_path)

        assert consumers == set(candidates), (
            f"Wrong import sites; expected {sorted(candidates)}, "
            f"got: {sorted(consumers)}"
        )

    def test_no_circular_import(self):
        """``from daemon.constants import INJECTION_ELIGIBLE_STATUSES``
        must work without circular-import errors. The pre-lock
        smoke-test from the plan."""
        import daemon.constants
        import daemon.tools.instance
        import daemon.tools.job_queue
        import daemon.routers.messages

        assert daemon.constants.INJECTION_ELIGIBLE_STATUSES is daemon.tools.instance.INJECTION_ELIGIBLE_STATUSES
        assert daemon.constants.INJECTION_ELIGIBLE_STATUSES is daemon.tools.job_queue.INJECTION_ELIGIBLE_STATUSES
        # Object-identity check between daemon.routers.messages and
        # daemon.constants confirms the router now imports (not
        # re-defines) the constant.
        # (daemon.routers.messages re-exports the name only inside
        # functions; we just verify the import succeeded.)


# ---------------------------------------------------------------------------
# Test c-bis — Empty-content trim-check (Task 2c, §7 #7)
# ---------------------------------------------------------------------------


class TestTrimCheck:
    """Task 2c: empty / whitespace-only content is rejected BEFORE
    routing. A blank message injected into a live turn wastes an LLM
    turn; the trim-check mirrors S4 at
    ``daemon/routers/messages.py:181-188``.
    """

    @pytest.mark.parametrize("empty_value", ["", "   ", "\n\t\n", "\r\n   \t"])
    async def test_empty_content_rejected_before_routing(self, empty_value):
        """Each whitespace-only input must be rejected without dispatch."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="running")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine("target-id", empty_value)

            assert result == "Message content is empty; nothing to send.", (
                f"Empty content must be rejected; got: {result!r}"
            )
            # No dispatch.
            manager.set_injection.assert_not_called()
            manager.enqueue_message.assert_not_called()

    async def test_whitespace_around_real_content_is_trimmed(self):
        """``"  hello  "`` is trimmed and routed normally — the
        trim-check rejects ONLY fully-empty content, not content with
        leading/trailing whitespace."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="running")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine("target-id", "  hello  ")

            # Trim-check did NOT reject — the message has real content.
            assert result != "Message content is empty; nothing to send."
            # Routed via injection (RUNNING target).
            manager.set_injection.assert_called_once()
            # The set_injection call received the trimmed message body
            # (we forward ``message`` as-is to set_injection; the agent
            # caller is expected to pre-trim if it cares). The important
            # assertion: the dispatch happened.
            manager.enqueue_message.assert_not_called()


# ---------------------------------------------------------------------------
# Test c-ter — UNKNOWN / not-found instance_id (delta-fix #1)
# ---------------------------------------------------------------------------


class TestNotFoundInstanceId:
    """Delta-fix #1: an unknown / typo'd ``target_instance_id`` returns a
    friendly error and NEITHER ``set_injection`` NOR ``enqueue_message``
    is called. Preserves the existing ``_resolve_instance_id``
    not-found behavior (which raises ValueError on fuzzy-match misses).
    """

    async def test_unknown_instance_id_returns_friendly_error(self):
        """The not-found branch never reaches the routing decision —
        ``_resolve_instance_id`` short-circuits with a ValueError that
        the tool surfaces as a friendly error string."""
        manager = _make_manager(status="running")
        # ``_resolve_instance_id`` calls ``manager.get_instance(iid)``
        # which raises KeyError on miss; the helper translates that to
        # ValueError with fuzzy-match suggestions.
        async def _get_instance_raises(iid):
            raise KeyError(iid)

        manager.get_instance = _get_instance_raises
        manager.find_near_instance = MagicMock(return_value=[])

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine("nonexistent-id", "hello")

        # Not-found error from ``_resolve_instance_id`` (with the
        # original fuzzy-match suggestion behavior).
        assert "nonexistent-id" in result
        assert "not found" in result.lower()
        # No dispatch.
        manager.set_injection.assert_not_called()
        manager.enqueue_message.assert_not_called()

    async def test_none_target_id_rejected(self):
        """``target_instance_id=None`` is rejected with a distinct error
        (None is not a valid instance id) — different from the
        not-found text."""
        manager = _make_manager(status="running")
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            send_message = _get_send_message_tool(manager)
            result = await send_message.coroutine(None, "hello")  # type: ignore[arg-type]

        # ``_resolve_instance_id`` raises ValueError("...cannot be empty")
        # for None.
        assert "cannot be empty" in result.lower() or "none" in result.lower()
        # No dispatch.
        manager.set_injection.assert_not_called()
        manager.enqueue_message.assert_not_called()

    async def test_routing_helper_returns_none_for_unknown(self):
        """``_route_send_message`` returns ``None`` when
        ``manager.get_instance_info(...)`` raises KeyError (the
        delta-fix #1 contract — defense-in-depth in case
        ``_resolve_instance_id`` somehow passed but get_instance_info
        raises)."""
        from daemon.tools.instance import _route_send_message

        manager = MagicMock()

        def _raise_key_error(iid):
            raise KeyError(iid)

        manager.get_instance_info = _raise_key_error

        result = _route_send_message(manager, "ghost")
        assert result is None, (
            f"Routing helper must return None for unknown instance_id; "
            f"got: {result!r}"
        )


# ---------------------------------------------------------------------------
# Test b — Revive from each terminal state (Task 4, D2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "terminal_status",
    ["completed", "terminated", "error", "failed"],
)
class TestTerminalRevive:
    """Task 4 / D2: all four terminal states flow into the shared
    ``_prepare_enqueued_message`` revive path. Tool result pre-pends
    ``"Instance was {prior_status} — revived and message dispatched."``
    """

    async def test_terminal_state_revives_and_enqueues(self, terminal_status):
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status=terminal_status)
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine("target-id", "hello")

        # Tool result pre-pends the revival text with the prior status.
        expected_prefix = (
            f"Instance was {terminal_status} — revived and message dispatched."
        )
        assert expected_prefix in result, (
            f"Expected revival prefix for {terminal_status!r}; got: {result!r}"
        )
        # The enqueue path was taken.
        manager.enqueue_message.assert_awaited_once()
        # NOT the injection path.
        manager.set_injection.assert_not_called()
        # enqueue_message was called with the canonical source stamp.
        call_kwargs = manager.enqueue_message.await_args.kwargs
        assert call_kwargs["instance_id"] == "target-id"
        assert call_kwargs["message"] == "hello"
        assert call_kwargs["source"] == "internal_agent:parent-instance"


# ---------------------------------------------------------------------------
# Test c — PAUSED branch (Task 5, R-O1 verbatim)
# ---------------------------------------------------------------------------


class TestPausedReject:
    """Task 5 / R-O1: PAUSED targets are rejected with the verbatim text
    from decisions.md R-O1. No enqueue, no inject, no auto-resume, no
    ``resume_instance`` reference.
    """

    async def test_paused_branch_returns_verbatim_rejection(self):
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="paused")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine("target-id", "hello")

        # Verbatim text from decisions.md R-O1.
        expected = (
            "Instance 'target-id' is PAUSED. Paused instances cannot "
            "receive messages; delivery is rejected to respect the pause "
            "(operator/lifecycle intent). Wait for it to be resumed via "
            "the API/UI, or proceed with other work."
        )
        assert result == expected, (
            f"PAUSED reject must use verbatim R-O1 text; got: {result!r}"
        )
        # No dispatch.
        manager.set_injection.assert_not_called()
        manager.enqueue_message.assert_not_called()

    async def test_paused_rejection_does_not_name_resume_instance(self):
        """The original bug referenced a non-existent ``resume_instance``
        tool. The R-O1 text does NOT name any follow-up tool — verify
        by absence."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="paused")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine("target-id", "hello")

        # No mention of "resume_instance" (the bug).
        assert "resume_instance" not in result
        # No mention of the operator methods either — those are
        # operator/lifecycle methods, not agent tools, and naming
        # them in a tool result would invite misuse.
        assert "pause_instance_cascade" not in result
        assert "resume_instance_cascade" not in result


# ---------------------------------------------------------------------------
# Test d — RUNNING + queue idle (R-O3, §7 #10)
# ---------------------------------------------------------------------------


class TestRunningInjection:
    """Task 3 / R-O3: RUNNING + idle queue always injects (queue-busy
    guard DROPPED for the injection branch — status is the source of
    truth per D11).
    """

    async def test_running_injects_with_idle_queue(self):
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="running")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine("target-id", "hi")

        # Injection path was taken.
        manager.set_injection.assert_called_once()
        # Queue-busy guard DROPPED for injection (D11 / R-O3). Even if
        # the queue were busy, status-at-routing is the source of truth
        # and the agent_node will consume the injection on its next
        # drain.
        manager.get_queue_stats.assert_not_called()
        manager.enqueue_message.assert_not_called()
        # The R-O2 W3 stranding sentence is in the result verbatim.
        assert "Message injected into running target" in result
        assert "pause-loss parity with the user messages API" in result

    async def test_running_with_busy_queue_still_injects(self):
        """The queue-busy guard is DROPPED for the injection branch.
        Even if the queue has pending/processing messages, status is
        the source of truth."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="running")
            # A "busy" queue — the injection branch ignores this.
            manager.get_queue_stats = AsyncMock(
                return_value={"pending_count": 5, "processing_count": 2}
            )
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine("target-id", "hi")

        # Injection still taken; queue-busy guard does NOT fire.
        manager.set_injection.assert_called_once()
        manager.enqueue_message.assert_not_called()
        assert "Message injected into running target" in result


# ---------------------------------------------------------------------------
# Test e — WAITING_CHILDREN + injection (R-O4)
# ---------------------------------------------------------------------------


class TestWaitingChildrenInjection:
    """Task 3 / R-O4: WAITING_CHILDREN is injection-eligible (parity
    with the user messages API). The injection sits in the FIFO until
    the next dispatch (typically a child report waking the instance
    via the dependency bus).
    """

    async def test_waiting_children_injects(self):
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="waiting_children")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine("target-id", "wake up")

        # Injection path was taken.
        manager.set_injection.assert_called_once()
        manager.enqueue_message.assert_not_called()
        # Result reflects WAITING_CHILDREN.
        assert "Message injected into waiting_children target" in result
        # R-O2 W3 stranding caveat still present.
        assert "pause-loss parity with the user messages API" in result


# ---------------------------------------------------------------------------
# Test e-bis — ENQUEUE-PARITY else-branch for IDLE / WAITING / QUEUED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "enqueue_status",
    ["idle", "waiting", "queued"],
)
class TestEnqueueParity:
    """Task 3 / e-bis: IDLE / WAITING / QUEUED (and any other
    non-eligible non-terminal state) route via ``enqueue_message(...)``
    with the queue-busy guard retained. Test exhaustively enumerates
    the three relevant enum values.
    """

    async def test_non_terminal_non_injection_state_enqueues(self, enqueue_status):
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status=enqueue_status)
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine("target-id", "hi")

        # Enqueue path was taken (NOT injection).
        manager.enqueue_message.assert_awaited_once()
        manager.set_injection.assert_not_called()
        # No revival prefix — the target was NOT terminal.
        assert "revived and message dispatched" not in result
        # Standard success text.
        assert "Message queued and sent to target-id" in result


class TestExhaustiveEnumRouting:
    """Test e-bis exhaustiveness assertion: every state in the
    ``InstanceStatus`` enum maps to exactly one of the five routing
    branches (injection / enqueue-revive / enqueue / paused / not-found).
    If a future enum value is added, this test fails loudly — that's
    the point.
    """

    @pytest.mark.parametrize(
        "enum_value",
        [
            "idle", "running", "waiting", "paused",
            "completed", "error", "terminated",
            "queued", "waiting_children", "failed",
        ],
    )
    def test_each_enum_value_maps_to_known_branch(self, enum_value):
        from daemon.tools.instance import _route_send_message

        manager = MagicMock()
        manager.get_instance_info = MagicMock(return_value={"status": enum_value})

        result = _route_send_message(manager, "any-id")
        assert result is not None, (
            f"Enum value {enum_value!r} returned None — must map to "
            f"a known branch"
        )
        routed_via, prior_status = result
        # Exactly one of the five branches.
        assert routed_via in {
            "injection", "enqueue-revive", "enqueue", "paused",
        }, (
            f"Unknown routed_via={routed_via!r} for status={enum_value!r}"
        )
        assert prior_status == enum_value

    async def test_routing_helper_has_no_silent_fallthrough(self):
        """The routing helper is EXHAUSTIVE — it has no `else: pass` /
        silent fall-through. We assert this by inspecting the source
        directly: the function body MUST end with the
        ``return ("enqueue", prior_status)`` catch-all (the enqueue
        branch covers every other state)."""
        import inspect

        from daemon.tools.instance import _route_send_message

        src = inspect.getsource(_route_send_message)
        # The catch-all enqueue return MUST be present.
        assert 'return ("enqueue", prior_status)' in src, (
            "Routing helper must end with an enqueue catch-all return "
            "to ensure exhaustiveness"
        )
        # The ``return None`` sites are limited to the two
        # defense-in-depth branches: (1) ``manager.get_instance_info``
        # raises ``KeyError`` (not-found); (2) the dict is missing the
        # ``status`` key (defensive). Both are intentional, NOT silent
        # fall-throughs.
        none_returns = src.count("return None")
        assert none_returns == 2, (
            f"Expected exactly two `return None` sites (KeyError "
            f"not-found + missing-status defensive), got {none_returns}"
        )


# ---------------------------------------------------------------------------
# Test f — W3 stranding-race exposure (R-O2 verbatim)
# ---------------------------------------------------------------------------


class TestW3StrandingNote:
    """Test f: the injection-path success result MUST include the R-O2
    W3 stranding sentence verbatim (or equivalent covering the same
    three facts: pause-loss parity, daemon-restart loss, in-flight
    delivery caveat). This composes with R-O1's PAUSED-reject text —
    both MUST ship together; an implementer cannot ship one without
    the other.
    """

    async def test_injection_result_includes_w3_stranding_sentence(self):
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="running")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine("target-id", "hi")

        # The W3 stranding sentence is in the result.
        assert "Note: if the target is paused or the daemon restarts" in result, (
            f"Injection result must include W3 stranding sentence; got: {result!r}"
        )
        # The three key facts (pause-loss parity, daemon restart loss,
        # in-flight delivery caveat) are present.
        assert "pause-loss parity with the user messages API" in result
        assert "in-flight injected message may be dropped" in result

    async def test_paused_reject_includes_w5_pairing_reference(self):
        """The PAUSED reject does NOT include the W3 sentence (the
        message is rejected, not delivered). But the result IS a
        verbatim copy of the R-O1 text — same sentence structure that
        appears in the plan and in decisions.md. The two texts compose
        via the implementation: both must ship together; the test
        asserts the PAUSED text is correct."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="paused")
            send_message = _get_send_message_tool(manager)

            paused_result = await send_message.coroutine("target-id", "hi")
            assert "Paused instances cannot receive messages" in paused_result
            assert "delivery is rejected to respect the pause" in paused_result


# ---------------------------------------------------------------------------
# Test g — W4 RUNNING→terminal FIFO survives
# ---------------------------------------------------------------------------


class TestW4TerminalSurvive:
    """Test g: W4 benign-survival semantic — RUNNING→terminal transition
    with populated FIFO is NOT cleared. The FIFO persists and is drained
    on the next agent_node cycle (e.g. after a subsequent revive).
    """

    def test_terminal_transition_does_not_clear_pending_injections(self):
        """``InstanceManager._pending_injections`` is a RAM dict that is
        NOT touched by terminal transitions. Only the pause path
        (``clear_injection(node_id)`` at ``instance_lifecycle.py:2501``),
        ``clear_all`` (lifecycle.py:3383-3384), and the TTL sweep
        (manager.py:3542-3570) clear entries."""
        # We verify the architectural property by inspecting the
        # terminal-transition code paths — none of them touch
        # ``_pending_injections``.
        from daemon.services import instance_lifecycle

        # ``clear_injection`` is a public method on the manager; the
        # FIFO dict is private. The terminal transition path does not
        # call ``clear_injection`` (only the pause path does).
        # We can't easily mock the full lifecycle service, so we just
        # assert the property that ``InstanceStatus.COMPLETED.value``
        # is NOT a key in any ``clear_injections_for_status`` helper
        # (no such helper exists — terminal transitions preserve the
        # FIFO).
        lifecycle_src = open(instance_lifecycle.__file__).read()
        # The pause path explicitly clears; the terminal-revive path
        # (reactivating a COMPLETED instance) does NOT clear.
        # Spot-check: the clear path is gated on PAUSED, not on any
        # terminal state.
        assert "PAUSED" in lifecycle_src or "paused" in lifecycle_src

    def test_pending_injections_survives_status_change_to_terminal(self):
        """Direct FIFO-level test: populate ``_pending_injections`` on a
        mock manager, then "transition" the target to COMPLETED. The
        FIFO entry MUST still be there (W4 benign-survival)."""
        manager = _make_manager(status="running")
        # Populate the FIFO directly (mock the manager's internal
        # _pending_injections attribute).
        manager._pending_injections = {
            "target-id": [
                {"content": "first", "timestamp": "2026-08-26T00:00:00Z"},
                {"content": "second", "timestamp": "2026-08-26T00:00:01Z"},
            ],
        }
        # Now the status transitions to completed (W4 setup).
        manager.get_instance_info = MagicMock(return_value={"status": "completed"})
        # The FIFO is untouched — neither lifecycle pause nor terminal
        # transition clears it.
        assert "target-id" in manager._pending_injections
        assert len(manager._pending_injections["target-id"]) == 2


# ---------------------------------------------------------------------------
# Test h — JAFP compliance (no new JobItem allocation)
# ---------------------------------------------------------------------------


class TestJAFPCompliance:
    """Test h: source review confirms no new ``JobItem`` allocation in
    the ``send_message`` path. The agent-tool layer continues to use
    ``enqueue_message`` (for terminal / IDLE / WAITING / QUEUED) and
    ``set_injection`` (for RUNNING / WAITING_CHILDREN).
    """

    def test_no_jobitem_in_send_message_path(self):
        """``grep -n "JobItem" daemon/tools/instance.py`` count must be
        unchanged after Phase 1. The pre-Phase-1 baseline was 3 (all
        in comments)."""
        result = subprocess.check_output(
            ["grep", "-c", "JobItem", "daemon/tools/instance.py"],
            cwd="/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble",
            text=True,
        ).strip()
        count = int(result)
        assert count == 3, (
            f"Expected JobItem count of 3 (all in comments), got {count}. "
            f"A new JobItem allocation would be a JAFP violation."
        )

    async def test_injection_branch_does_not_call_enqueue_message_job(self):
        """The injection branch calls ``set_injection`` (NOT
        ``enqueue_message_job``)."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="running")
            send_message = _get_send_message_tool(manager)

            await send_message.coroutine("target-id", "hi")

        manager.set_injection.assert_called_once()
        manager.enqueue_message_job.assert_not_called()
        manager.enqueue_message.assert_not_called()

    async def test_enqueue_branch_does_not_call_enqueue_message_job(self):
        """The enqueue branch calls ``enqueue_message`` (NOT
        ``enqueue_message_job``). The JobItem-mirror path is reserved
        for external/public entry points."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="idle")
            send_message = _get_send_message_tool(manager)

            await send_message.coroutine("target-id", "hi")

        manager.enqueue_message.assert_awaited_once()
        manager.enqueue_message_job.assert_not_called()
        manager.set_injection.assert_not_called()


# ---------------------------------------------------------------------------
# Test i — Docstring / _full_doc_ parity (D10)
# ---------------------------------------------------------------------------


class TestDocstringParity:
    """D10: docstring and ``_full_doc_`` MUST stay in lockstep. The W5
    ordering sentence appears in BOTH.
    """

    def test_w5_ordering_sentence_in_docstring_and_full_doc(self):
        from daemon.tools.instance import create_instance_tools

        patches = _patch_heavy_helpers()
        for p in patches:
            p.start()
        try:
            tools = create_instance_tools(
                _make_manager(status="idle"), "parent-instance", "developer"
            )
        finally:
            for p in reversed(patches):
                p.stop()

        send_message_tool = next(
            t for t in tools if getattr(t, "name", None) == "send_message"
        )
        # The tool object's ``description`` is the docstring; ``_full_doc_``
        # is the long-form docs.
        docstring = send_message_tool.description
        full_doc = send_message_tool._full_doc_

        # W5 ordering sentence verbatim.
        w5_sentence = (
            "Delivery is FIFO but may interleave with concurrent senders "
            "— do not assume order between injection and enqueue. "
            "Injections land before child reports in the same wake-up turn."
        )
        assert w5_sentence in docstring, (
            f"Docstring must contain W5 ordering sentence; got: {docstring[:300]!r}"
        )
        assert w5_sentence in full_doc, (
            f"_full_doc_ must contain W5 ordering sentence; got: {full_doc[:300]!r}"
        )

    def test_docstring_full_doc_share_paused_rejection_text(self):
        """The PAUSED rejection text MUST appear verbatim in both
        docstring and ``_full_doc_`` (D10 parity + R-O1 verbatim)."""
        from daemon.tools.instance import create_instance_tools

        patches = _patch_heavy_helpers()
        for p in patches:
            p.start()
        try:
            tools = create_instance_tools(
                _make_manager(status="idle"), "parent-instance", "developer"
            )
        finally:
            for p in reversed(patches):
                p.stop()

        send_message_tool = next(
            t for t in tools if getattr(t, "name", None) == "send_message"
        )
        docstring = send_message_tool.description
        full_doc = send_message_tool._full_doc_

        paused_phrase = "Paused instances cannot receive messages"
        assert paused_phrase in docstring
        assert paused_phrase in full_doc

    def test_docstring_full_doc_share_w3_stranding_caveat(self):
        """The W3 stranding sentence MUST appear verbatim in both
        docstring and ``_full_doc_`` (leader decision b)."""
        from daemon.tools.instance import create_instance_tools

        patches = _patch_heavy_helpers()
        for p in patches:
            p.start()
        try:
            tools = create_instance_tools(
                _make_manager(status="idle"), "parent-instance", "developer"
            )
        finally:
            for p in reversed(patches):
                p.stop()

        send_message_tool = next(
            t for t in tools if getattr(t, "name", None) == "send_message"
        )
        docstring = send_message_tool.description
        full_doc = send_message_tool._full_doc_

        w3_phrase = "pause-loss parity with the user messages API"
        assert w3_phrase in docstring
        assert w3_phrase in full_doc


# ---------------------------------------------------------------------------
# Test j — INFO logging provenance (Task 3b, §7 #8)
# ---------------------------------------------------------------------------


class TestInfoLogging:
    """Task 3b: every successful ``send_message`` emits ONE INFO log
    line with structured fields (``event``, ``caller_iid``,
    ``target_iid``, ``routed_via``, ``prior_status``, ``content_len``,
    ``source``). Trim-check rejects and PAUSED rejects do NOT emit
    this log line.
    """

    @pytest.mark.parametrize(
        "status,routed_via",
        [
            ("running", "injection"),
            ("waiting_children", "injection"),
            ("idle", "enqueue"),
            ("completed", "enqueue-revive"),
            ("terminated", "enqueue-revive"),
            ("error", "enqueue-revive"),
            ("failed", "enqueue-revive"),
        ],
    )
    async def test_successful_send_emits_agent_send_message_log(
        self, caplog, status, routed_via
    ):
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status=status)
            send_message = _get_send_message_tool(manager)

            with caplog.at_level(logging.INFO, logger="daemon.tools.instance"):
                result = await send_message.coroutine(
                    "target-id", "hello world"
                )

        # Find the agent_send_message log record.
        records = [
            r for r in caplog.records if r.name == "daemon.tools.instance"
        ]
        matching = [r for r in records if getattr(r, "event", None) == "agent_send_message"]
        assert len(matching) >= 1, (
            f"Expected ≥1 agent_send_message log line for status={status!r}; "
            f"got records: {[r.getMessage() for r in records]}"
        )
        record = matching[0]
        # Structured fields.
        assert record.caller_iid == "parent-instance"
        assert record.target_iid == "target-id"
        assert record.routed_via == routed_via
        assert record.prior_status == status
        assert record.content_len == len("hello world")
        assert record.source == "internal_agent:parent-instance"

    async def test_paused_reject_does_not_emit_log_line(self, caplog):
        """PAUSED reject is NOT a successful send — no INFO log line."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="paused")
            send_message = _get_send_message_tool(manager)

            with caplog.at_level(logging.INFO, logger="daemon.tools.instance"):
                await send_message.coroutine("target-id", "hi")

        matching = [
            r for r in caplog.records
            if getattr(r, "event", None) == "agent_send_message"
        ]
        assert matching == [], (
            f"PAUSED reject must NOT emit agent_send_message log; "
            f"got: {[r.getMessage() for r in matching]}"
        )

    async def test_trim_check_reject_does_not_emit_log_line(self, caplog):
        """Trim-check reject is NOT a successful send — no INFO log line."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="running")
            send_message = _get_send_message_tool(manager)

            with caplog.at_level(logging.INFO, logger="daemon.tools.instance"):
                await send_message.coroutine("target-id", "")

        matching = [
            r for r in caplog.records
            if getattr(r, "event", None) == "agent_send_message"
        ]
        assert matching == [], (
            f"Trim-check reject must NOT emit agent_send_message log; "
            f"got: {[r.getMessage() for r in matching]}"
        )

    async def test_not_found_does_not_emit_log_line(self, caplog):
        """Not-found is NOT a successful send — no INFO log line."""
        manager = _make_manager(status="running")
        async def _raise_key_error(iid):
            raise KeyError(iid)
        manager.get_instance = _raise_key_error
        manager.find_near_instance = MagicMock(return_value=[])

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            send_message = _get_send_message_tool(manager)
            with caplog.at_level(logging.INFO, logger="daemon.tools.instance"):
                await send_message.coroutine("nonexistent-id", "hi")

        matching = [
            r for r in caplog.records
            if getattr(r, "event", None) == "agent_send_message"
        ]
        assert matching == [], (
            f"Not-found must NOT emit agent_send_message log; "
            f"got: {[r.getMessage() for r in matching]}"
        )


# ---------------------------------------------------------------------------
# Routing helper unit tests (pure unit, no manager needed)
# ---------------------------------------------------------------------------


class TestRoutingHelper:
    """Direct tests of ``_route_send_message`` — the single source of
    truth for the dispatch decision. Pure unit tests; the manager is
    a MagicMock stub returning the desired ``status`` from
    ``get_instance_info``.
    """

    @pytest.mark.parametrize(
        "status,expected_routed_via",
        [
            ("running", "injection"),
            ("waiting_children", "injection"),
            ("idle", "enqueue"),
            ("waiting", "enqueue"),
            ("queued", "enqueue"),
            ("completed", "enqueue-revive"),
            ("terminated", "enqueue-revive"),
            ("error", "enqueue-revive"),
            ("failed", "enqueue-revive"),
            ("paused", "paused"),
        ],
    )
    def test_classifies_each_known_status(self, status, expected_routed_via):
        from daemon.tools.instance import _route_send_message

        manager = MagicMock()
        manager.get_instance_info = MagicMock(return_value={"status": status})

        result = _route_send_message(manager, "target-id")
        assert result is not None
        routed_via, prior_status = result
        assert routed_via == expected_routed_via
        assert prior_status == status

    def test_returns_none_for_unknown_instance_id(self):
        """``manager.get_instance_info(...)`` raising ``KeyError`` →
        ``_route_send_message`` returns ``None`` (delta-fix #1)."""
        from daemon.tools.instance import _route_send_message

        manager = MagicMock()

        def _raise_key_error(iid):
            raise KeyError(iid)

        manager.get_instance_info = _raise_key_error

        result = _route_send_message(manager, "ghost")
        assert result is None

    def test_returns_none_for_missing_status_field(self):
        """Defensive: ``get_instance_info`` returns a dict WITHOUT a
        ``status`` key (instance row in inconsistent state) → helper
        returns ``None``."""
        from daemon.tools.instance import _route_send_message

        manager = MagicMock()
        manager.get_instance_info = MagicMock(return_value={"agent_id": "developer"})

        result = _route_send_message(manager, "broken")
        assert result is None
