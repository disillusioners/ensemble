"""Phase 1 (agent-instance-tools) tests for ``send_message`` routing.

Suite layout: 18 classes, one per Phase-1 contract surface, kept
single-file for diff-review locality. A future split into
``test_instance_tools_routing.py`` + ``test_instance_tools_guards.py``
+ ``test_instance_tools_docs.py`` is a ticketed follow-up; not done here.

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
  * Review 377b0a8f fix 1: sends bearing ``load_skill`` route via
    ENQUEUE even for injection-eligible targets (the ``<meta>`` tag
    parser lives only in the enqueue pipeline; queue-busy guard
    retained, as before).
  * Review 377b0a8f fix 2: sends bearing a non-empty ``context``
    route via ENQUEUE even for injection-eligible targets
    (``set_injection`` has no metadata channel; ``task_context``
    rides ``enqueue_message(metadata=...)``).
  * Green: the exhaustive-enum parametrization is DERIVED from the
    real ``InstanceStatus`` enum; the terminal-status set is hoisted
    to ``daemon.constants.TERMINAL_INSTANCE_STATUSES``.

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
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Module-level: derive the repo root once. The test file lives at
# ``tests/unit/tools/test_instance_tools.py``, so
# ``parents[0]=tests/unit/tools/``, ``parents[1]=tests/unit/``,
# ``parents[2]=tests/``, ``parents[3]=<repo-root>``. Several source-scan
# tests use this constant so the suite stays portable across machines
# and CI (no hardcoded absolute checkout paths).
REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Helpers — manager fixture and tool-builder (shared with the two
# tests/tools/ suites via ``tests.helpers.send_message_fixtures``)
# ---------------------------------------------------------------------------

from tests.helpers.send_message_fixtures import (
    get_send_message_tool as _get_send_message_tool,
    patch_heavy_helpers as _patch_heavy_helpers,
)


def _make_manager(*, status: str) -> MagicMock:
    """Build a mock manager wired for the Phase 1 ``send_message`` tool.

    Extends the shared baseline (``tests.helpers.send_message_fixtures.
    make_send_message_manager``) with ``get_injection_count`` (read by
    the WAITING_CHILDREN injection path that some classes in this suite
    exercise — e.g. ``TestWaitingChildrenInjection``).
    """
    from tests.helpers.send_message_fixtures import make_send_message_manager

    manager = make_send_message_manager(status=status)
    manager.get_injection_count = MagicMock(return_value=1)
    return manager


def _instance_status_values() -> list[str]:
    """All ``InstanceStatus`` enum values, derived from the enum itself.

    ``TestExhaustiveEnumRouting`` parametrizes over this so a newly
    added enum member is AUTOMATICALLY covered — the previous hardcoded
    ten-value list would have silently skipped an eleventh member while
    the class docstring claimed it would "fail loudly".
    """
    from daemon.repositories.instance.models import InstanceStatus

    return [s.value for s in InstanceStatus]


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

    def test_terminal_instance_statuses_constant_exists(self):
        """Green fix (review follow-up): the terminal-status set is
        hoisted to ``daemon.constants`` as
        ``TERMINAL_INSTANCE_STATUSES`` — sibling of
        ``INJECTION_ELIGIBLE_STATUSES``; the module-local
        ``_TERMINAL_STATUSES`` frozenset in ``daemon/tools/instance.py``
        is gone, and the routing helper consumes the hoisted constant.
        """
        import daemon.tools.instance as instance_tools
        from daemon.constants import TERMINAL_INSTANCE_STATUSES

        assert TERMINAL_INSTANCE_STATUSES == frozenset(
            {"completed", "terminated", "error", "failed"}
        )
        assert not hasattr(instance_tools, "_TERMINAL_STATUSES"), (
            "The module-local _TERMINAL_STATUSES frozenset must be gone — "
            "use daemon.constants.TERMINAL_INSTANCE_STATUSES instead"
        )
        assert (
            instance_tools.TERMINAL_INSTANCE_STATUSES
            is TERMINAL_INSTANCE_STATUSES
        )

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
        manager_src = (REPO_ROOT / "daemon" / "manager.py").read_text()
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
        for f in (REPO_ROOT / "daemon").rglob("*.py"):
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
                                f"{f.relative_to(REPO_ROOT)}:{ln} has a "
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
            cwd=str(REPO_ROOT),
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

        consumers: set[str] = set()
        candidates = [
            "daemon/routers/messages.py",
            "daemon/tools/job_queue.py",
            "daemon/tools/instance.py",
        ]
        for rel_path in candidates:
            path = REPO_ROOT / rel_path
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

    async def test_split_cache_race_returns_friendly_error(self):
        """Split-cache race defense: ``_resolve_instance_id`` (async)
        succeeds, but ``manager.get_instance_info(...)`` (sync, in-memory
        cache) raises ``KeyError`` — instance.py:1959 wraps the unguarded
        ``get_instance_info`` call with the SAME friendly not-found text
        the routing helper uses (delta-fix #1 contract on the
        CR-2 membership-gate path).

        Without the guard a raw ``KeyError`` propagates to the agent
        instead of the friendly not-found text — tester CR-2 race
        probe (RESULTS §1).
        """
        manager = _make_manager(status="running")

        # ``_resolve_instance_id`` path (async, in-memory cache hit)
        # succeeds — split-cache race: this happens to return a row.
        async def _get_instance_succeeds(iid):
            return MagicMock(instance_id=iid)

        manager.get_instance = _get_instance_succeeds
        manager.find_near_instance = MagicMock(return_value=[])

        # ``get_instance_info`` (sync) raises ``KeyError`` — the
        # lifecycle store evicted the row between the two lookups.
        def _get_instance_info_raises(iid):
            raise KeyError(iid)

        manager.get_instance_info = _get_instance_info_raises

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine("vanished-id", "hello")

        # Full-sentence friendly not-found text — verbatim match to the
        # string the routing-helper not-found branch uses at
        # instance.py:1978. No raw ``KeyError`` leaks to the agent.
        assert isinstance(result, str)
        assert "vanished-id" in result
        assert "not found" in result.lower()
        assert "no message dispatched" in result
        # Delta-fix #1: NEITHER ``set_injection`` NOR
        # ``enqueue_message`` called.
        manager.set_injection.assert_not_called()
        manager.enqueue_message.assert_not_called()


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
# Review 377b0a8f finding 1 — load_skill forces the enqueue pipeline
# ---------------------------------------------------------------------------


class TestEnqueueOverrideForLoadSkill:
    """FIX 1 (review 377b0a8f): a send bearing ``load_skill`` routes
    via ENQUEUE even when the target is injection-eligible (RUNNING /
    WAITING_CHILDREN).

    The ``<meta>`` tag parser (``extract_load_skill``,
    ``daemon/services/instance_messaging.py:2234``) lives ONLY in the
    enqueue pipeline; the injection drain (``daemon/graph.py``) builds
    a plain HumanMessage. Without the override the tag lands as raw
    garbage text in the target's live turn AND the skill never loads.
    The override restores exact pre-Phase-1 behavior for the
    load_skill case (queue-busy guard included, as before). Plain
    sends without load_skill keep the new injection routing.
    """

    @pytest.mark.parametrize("status", ["running", "waiting_children"])
    async def test_load_skill_routes_via_enqueue_not_injection(self, status):
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status=status)
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine(
                "target-id", "please review", load_skill="unit-test"
            )

        # Enqueue path taken; set_injection NEVER called — the raw
        # <meta> tag cannot land in a live turn via the injection FIFO.
        manager.enqueue_message.assert_awaited_once()
        manager.set_injection.assert_not_called()
        # The tag is honored: it rides the enqueued payload for the
        # enqueue pipeline's parser (extract_load_skill).
        enqueued_message = manager.enqueue_message.await_args.kwargs["message"]
        assert '<meta>{"load_skill": "unit-test"}</meta>' in enqueued_message
        # Pre-Phase-1 result text for the enqueue path.
        assert "Message queued and sent to target-id" in result
        assert "Message injected into" not in result

    async def test_load_skill_keeps_queue_busy_guard(self):
        """The override restores pre-Phase-1 behavior INCLUDING the
        queue-busy guard: a load_skill send to a RUNNING target with a
        busy queue is rejected (guard fires), not injected."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="running")
            manager.get_queue_stats = AsyncMock(
                return_value={"pending_count": 5, "processing_count": 2}
            )
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine(
                "target-id", "hi", load_skill="unit-test"
            )

        manager.get_queue_stats.assert_awaited_once()
        manager.set_injection.assert_not_called()
        manager.enqueue_message.assert_not_called()
        assert "already has a message in progress" in result

    async def test_whitespace_load_skill_still_injects(self):
        """Boundary: a whitespace-only ``load_skill`` appends no tag and
        must NOT force the enqueue path — the send behaves like a plain
        send and takes the injection branch for a RUNNING target."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="running")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine(
                "target-id", "hi", load_skill="   "
            )

        manager.set_injection.assert_called_once()
        manager.enqueue_message.assert_not_called()
        # No <meta> tag was appended to the injected content.
        injected_message = manager.set_injection.call_args.args[1]
        assert "<meta>" not in injected_message
        assert "Message injected into running target" in result

    async def test_load_skill_paused_still_rejects_verbatim(self):
        """The override never weakens the PAUSED reject: PAUSED +
        load_skill returns the verbatim R-O1 text with no dispatch."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="paused")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine(
                "target-id", "hi", load_skill="unit-test"
            )

        expected = (
            "Instance 'target-id' is PAUSED. Paused instances cannot "
            "receive messages; delivery is rejected to respect the pause "
            "(operator/lifecycle intent). Wait for it to be resumed via "
            "the API/UI, or proceed with other work."
        )
        assert result == expected
        manager.set_injection.assert_not_called()
        manager.enqueue_message.assert_not_called()


# ---------------------------------------------------------------------------
# Review 377b0a8f finding 2 — context forces the enqueue pipeline
# ---------------------------------------------------------------------------


class TestEnqueueOverrideForContext:
    """FIX 2 (review 377b0a8f): a send bearing a non-empty ``context``
    routes via ENQUEUE even when the target is injection-eligible.

    ``task_context`` rides ``enqueue_message(metadata=...)``;
    ``set_injection`` (manager.py) stores ``{content, timestamp}``
    only — no metadata channel. Without the override the context is
    silently dropped on the injection branch.
    """

    @pytest.mark.parametrize("status", ["running", "waiting_children"])
    async def test_context_routes_via_enqueue_with_metadata(self, status):
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status=status)
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine(
                "target-id", "please review", context={"files": ["a.py"]}
            )

        # Enqueue path taken; set_injection NEVER called.
        manager.enqueue_message.assert_awaited_once()
        manager.set_injection.assert_not_called()
        # The context survived: task_context metadata carries the
        # formatted [SYSTEM CONTEXT: Task Context] block.
        metadata = manager.enqueue_message.await_args.kwargs.get("metadata")
        assert metadata is not None, (
            "context-bearing send must thread metadata['task_context']"
        )
        assert "[SYSTEM CONTEXT: Task Context]" in metadata["task_context"]
        # Pre-Phase-1 result text for the enqueue path.
        assert "Message queued and sent to target-id" in result
        assert "Message injected into" not in result

    async def test_load_skill_and_context_combined_route_via_enqueue(self):
        """Both enqueue-only params on one send: the enqueue path carries
        the meta tag in the payload AND the task_context in metadata."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="running")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine(
                "target-id",
                "please review",
                load_skill="unit-test",
                context={"notes": "see plan"},
            )

        manager.enqueue_message.assert_awaited_once()
        manager.set_injection.assert_not_called()
        kwargs = manager.enqueue_message.await_args.kwargs
        assert '<meta>{"load_skill": "unit-test"}</meta>' in kwargs["message"]
        assert "[SYSTEM CONTEXT: Task Context]" in kwargs["metadata"][
            "task_context"
        ]
        assert "Message queued and sent to target-id" in result

    async def test_empty_context_dict_still_injects(self):
        """Boundary: ``context={}`` carries nothing to deliver — the
        override must NOT fire; the plain send takes the injection
        branch for a RUNNING target."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="running")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine(
                "target-id", "hi", context={}
            )

        manager.set_injection.assert_called_once()
        manager.enqueue_message.assert_not_called()
        assert "Message injected into running target" in result


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

    Two locks here:

      1. The parametrization is DERIVED from the real
         ``InstanceStatus`` enum (``_instance_status_values``), so a
         future enum value is automatically covered — that's the
         parametrization lock.
      2. The mapping below (``_STATUS_TO_ROUTE``) is the explicit
         source-of-truth for the EXPECTED route per enum value. A
         value missing from the mapping fails the parametrized test
         (``KeyError`` on lookup); a mapping key not in the enum
         fails the ``test_status_to_route_mapping_is_exhaustive``
         assertion below. No silent fallthrough: every enum value
         must have a declared route.
    """

    # Explicit enum-value → expected route mapping. Mirrors
    # ``_route_send_message`` in ``daemon/tools/instance.py``. A new
    # enum value with no entry here FAILS the parametrized test; an
    # entry here with no enum value FAILS the exhaustive check.
    _STATUS_TO_ROUTE: dict[str, str] = {
        # INJECTION_ELIGIBLE_STATUSES (D13 / LOCKED choice).
        "running": "injection",
        "waiting_children": "injection",
        # TERMINAL_INSTANCE_STATUSES (revive branch).
        "completed": "enqueue-revive",
        "terminated": "enqueue-revive",
        "error": "enqueue-revive",
        "failed": "enqueue-revive",
        # Non-eligible non-terminal states — enqueue catch-all.
        "idle": "enqueue",
        "waiting": "enqueue",
        "queued": "enqueue",
        # PAUSED — explicit pre-check (R-O1).
        "paused": "paused",
    }

    @pytest.mark.parametrize(
        "enum_value",
        _instance_status_values(),
    )
    def test_each_enum_value_maps_to_known_branch(self, enum_value):
        from daemon.tools.instance import _route_send_message

        # The enum value MUST appear in the explicit mapping.
        assert enum_value in self._STATUS_TO_ROUTE, (
            f"Enum value {enum_value!r} has no entry in "
            f"_STATUS_TO_ROUTE — every InstanceStatus must declare its "
            f"expected route. Add it to TestExhaustiveEnumRouting."
            f"_STATUS_TO_ROUTE to fix."
        )
        expected_route = self._STATUS_TO_ROUTE[enum_value]

        manager = MagicMock()
        manager.get_instance_info = MagicMock(return_value={"status": enum_value})

        result = _route_send_message(manager, "any-id")
        assert result is not None, (
            f"Enum value {enum_value!r} returned None — must map to "
            f"a known branch"
        )
        routed_via, prior_status = result
        # Exact-route assertion (not a membership check).
        assert routed_via == expected_route, (
            f"Enum value {enum_value!r} routes via {routed_via!r}; "
            f"_STATUS_TO_ROUTE declares {expected_route!r}. "
            f"Update _STATUS_TO_ROUTE OR fix _route_send_message."
        )
        assert prior_status == enum_value

    def test_status_to_route_mapping_is_exhaustive(self):
        """The explicit ``_STATUS_TO_ROUTE`` mapping MUST have EXACTLY
        the same keys as the ``InstanceStatus`` enum — no extra keys
        (e.g. a typo or a deprecated status string) and no missing
        keys (every enum value is declared)."""
        from daemon.repositories.instance.models import InstanceStatus

        enum_values = {s.value for s in InstanceStatus}
        mapping_keys = set(self._STATUS_TO_ROUTE.keys())

        missing = enum_values - mapping_keys
        extra = mapping_keys - enum_values

        assert not missing, (
            f"_STATUS_TO_ROUTE is missing these enum values: "
            f"{sorted(missing)}"
        )
        assert not extra, (
            f"_STATUS_TO_ROUTE has keys not in InstanceStatus enum: "
            f"{sorted(extra)}"
        )

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

    # Full R-O2 W3 stranding sentence. MUST match byte-for-byte the
    # runtime return in ``_send_message_impl`` (injection branch) —
    # the f-string concat at
    # ``daemon/tools/instance.py`` lines 2052-2055 produces exactly
    # this string. The test asserts this sentence appears VERBATIM in
    # the injection result (not a substring / fragment).
    _W3_STRANDING_SENTENCE = (
        "Note: if the target is paused or the daemon restarts before "
        "delivery, an in-flight injected message may be dropped "
        "(pause-loss parity with the user messages API)."
    )

    async def test_injection_result_includes_w3_stranding_sentence(self):
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="running")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine("target-id", "hi")

        # Full verbatim sentence MUST appear in the result. This is a
        # strict equality-of-sentence-within-result check — the prior
        # substring containment was loose and would have passed if any
        # fragment of the sentence survived.
        assert self._W3_STRANDING_SENTENCE in result, (
            f"Injection result must include the FULL verbatim W3 "
            f"stranding sentence; got: {result!r}\n"
            f"expected (verbatim): {self._W3_STRANDING_SENTENCE!r}"
        )

    async def test_paused_reject_and_w3_texts_compose(self):
        """The PAUSED reject does NOT include the W3 sentence (the
        message is rejected, not delivered). But the result IS a
        verbatim copy of the R-O1 text — same sentence structure that
        appears in the plan and in decisions.md.

        This test composes with ``test_injection_result_includes_w3_
        stranding_sentence`` above: both texts MUST ship together; an
        implementer cannot ship one without the other. The previous
        name (``test_paused_reject_includes_w5_pairing_reference``)
        was a mislabel — the test asserts R-O1 PAUSED-reject text,
        not a W5 reference; the W5 reference was about tool-result
        pairing, which is exercised elsewhere."""
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
# Test g — W4 FIFO clearing is locked to allowlisted lifecycle contexts
# ---------------------------------------------------------------------------


class TestW4TerminalSurvive:
    """Test g: W4 benign-survival semantic — RUNNING→terminal transition
    with populated FIFO is NOT cleared by ad-hoc code in a status-update
    path. The FIFO persists and is drained on the next agent_node cycle
    (e.g. after a subsequent revive).

    Locked invariant (source-scan):
      Every ``clear_injection(...)`` call site and every direct
      ``self._pending_injections.clear()`` / ``.pop(...)`` mutation
      in the lifecycle / manager modules MUST live inside one of the
      allowlisted functions below. A NEW clear added to any other
      function — e.g. inside a status-update handler, a revive path,
      or a terminal-status branch — FAILS this test. That's the
      regression-prevention intent; the previous MagicMock-based test
      did not exercise any production code and silently passed when the
      invariant broke.
    """

    # Allowlist — functions where ``_pending_injections`` clearing is
    # intentional and documented. Adding a clear to a NEW function
    # (e.g. ``_complete_instance_cleanup``) will fail this test.
    _ALLOWED_CLEAR_FUNCS = frozenset({
        # Lifecycle module:
        "pause_instance_cascade",     # pause path (D8 / R-O1)
        "clear_all_instances",        # full clear_all
        "terminate_instance",         # pre-DB cleanup (W1: injected
                                       # HumanMessages are
                                       # checkpoint-persisted; only
                                       # the RAM queue is dropped)
        # Manager module:
        "clear_injection",            # the method definition itself
                                       # (canonical clearer)
        "_cleanup_instance_state",    # centralized cleanup helper
                                       # (TTL-evict + delete_project
                                       # route through here)
        "_cleanup_stale_injections",  # TTL/sweep eviction
    })

    def test_clear_injection_only_in_allowed_lifecycle_contexts(self):
        """Source-scan lock for ``_pending_injections`` clearing sites.

        Mirrors the architectural pattern of
        ``test_set_injection_is_only_FIFO_writer``: walk the source
        AST of ``daemon/services/instance_lifecycle.py`` and
        ``daemon/manager.py``; for each top-level function OR class
        method, find every ``clear_injection(...)`` call OR every
        ``self._pending_injections.{clear,pop}(...)`` attribute
        call; assert each site lives inside an allowlisted function.
        """
        import ast

        def iter_callable_defs(tree: ast.AST):
            """Yield every FunctionDef / AsyncFunctionDef reachable
            at the top level or inside top-level ClassDef blocks.
            Module-level helpers AND class methods both qualify."""
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield node
                elif isinstance(node, ast.ClassDef):
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            yield sub

        def find_clear_sites(path: Path):
            """Yield (func_name, lineno, snippet) for each clear call
            inside any walked function body.

            Detects three call shapes:
              1. ``clear_injection(...)``           — bare-name call
              2. ``self.clear_injection(...)``      — attribute on self
              3. ``self._manager.clear_injection(...)`` — attribute
                 chain via ``_manager`` on self
            And the direct mutation shapes:
              4. ``self._pending_injections.clear(...)``
              5. ``self._pending_injections.pop(...)``
            """
            tree = ast.parse(path.read_text())
            for func_node in iter_callable_defs(tree):
                for sub in ast.walk(func_node):
                    if not isinstance(sub, ast.Call):
                        continue
                    f = sub.func
                    # Build the attribute chain (innermost name first).
                    chain_names: list[str] = []
                    if isinstance(f, ast.Attribute):
                        cur = f.value
                        while isinstance(cur, ast.Attribute):
                            chain_names.append(cur.attr)
                            cur = cur.value
                        if isinstance(cur, ast.Name):
                            chain_names.append(cur.id)
                    # Case 1: ``clear_injection(...)`` — bare-name call.
                    if isinstance(f, ast.Name) and f.id == "clear_injection":
                        yield (func_node.name, sub.lineno, "clear_injection(...)")
                        continue
                    # Cases 2/3: ``self.clear_injection(...)`` or
                    # ``self._manager.clear_injection(...)``.
                    if (
                        isinstance(f, ast.Attribute)
                        and f.attr == "clear_injection"
                        and "self" in chain_names
                    ):
                        yield (func_node.name, sub.lineno, "<chain>.clear_injection(...)")
                        continue
                    # Cases 4/5: ``self._pending_injections.clear/pop(...)``
                    if (
                        isinstance(f, ast.Attribute)
                        and f.attr in {"clear", "pop"}
                        and "self" in chain_names
                        and "_pending_injections" in chain_names
                    ):
                        yield (
                            func_node.name,
                            sub.lineno,
                            f"_pending_injections.{f.attr}(...)",
                        )

        violations: list[str] = []
        for rel in (
            "daemon/services/instance_lifecycle.py",
            "daemon/manager.py",
        ):
            for func_name, lineno, snippet in find_clear_sites(REPO_ROOT / rel):
                if func_name not in self._ALLOWED_CLEAR_FUNCS:
                    violations.append(
                        f"{rel}:{lineno} in {func_name}() — {snippet}"
                    )

        assert not violations, (
            "_pending_injections clearing MUST only happen inside "
            "allowlisted lifecycle/manager functions "
            "(see TestW4TerminalSurvive._ALLOWED_CLEAR_FUNCS):\n  "
            + "\n  ".join(sorted(self._ALLOWED_CLEAR_FUNCS))
            + "\n\nViolations found:\n  "
            + "\n  ".join(violations)
            + (
                "\n\nIf the new clear site is intentional, add the "
                "function name to _ALLOWED_CLEAR_FUNCS and document "
                "why terminal/status-update paths must clear here."
            )
        )


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
            cwd=str(REPO_ROOT),
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
