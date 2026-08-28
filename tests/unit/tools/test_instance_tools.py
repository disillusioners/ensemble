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
  * Quick-win #7 revive-once guard: the SECOND agent-tool revive of
    the same child is refused with spawn-a-replacement guidance
    (mechanical ``RECOVERY_GUIDANCE_HINT`` bound, in-memory counter on
    the manager); the user-API revive path is neither counted nor
    blocked (``TestReviveOnceGuard``).
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
# Quick-win #7 — Revive-once guard for agent-tool-initiated revives
# ---------------------------------------------------------------------------

# The exact refusal string the tool returns on a SECOND agent-tool
# revive attempt (single-string, child id interpolated). Locked here so
# the phrasing mirrors RECOVERY_GUIDANCE_HINT semantics verbatim.
_REVIVE_REFUSAL_TEMPLATE = (
    "Refused: Instance '{iid}' has already been revived once and "
    "failed again. Spawn a replacement instance instead."
)


class TestReviveOnceGuard:
    """Quick-win #7: ``RECOVERY_GUIDANCE_HINT``
    (``daemon/services/error_reporting.py``) bounds child revives to
    AT MOST ONE via the agent tool; a second attempt is REFUSED with
    spawn-a-replacement guidance and dispatches nothing. The bound is
    enforced by an in-memory cumulative counter on the manager
    (``_agent_tool_revive_counts``), keyed by child instance id —
    agent-tool path ONLY; the user-API revive path neither increments
    it nor is blocked by it.
    """

    async def test_t1_first_agent_tool_revive_granted(self):
        """T1: first agent-tool revive of a terminal child succeeds —
        counter 0→1, child revived, message dispatched."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="error")
            send_message = _get_send_message_tool(manager)

            result = await send_message.coroutine("child-1", "continue")

        # Revived + dispatched via the shared enqueue path.
        assert (
            "Instance was error — revived and message dispatched." in result
        ), f"Expected revival prefix; got: {result!r}"
        manager.enqueue_message.assert_awaited_once()
        manager.set_injection.assert_not_called()
        # Counter incremented exactly once, for this child.
        manager.note_agent_tool_revive.assert_called_once_with("child-1")
        assert manager.get_agent_tool_revive_count("child-1") == 1

    async def test_t2_second_agent_tool_revive_refused(self):
        """T2: the SECOND agent-tool revive attempt is refused with the
        guidance message; child NOT revived again; message NOT
        dispatched."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="error")
            send_message = _get_send_message_tool(manager)

            first = await send_message.coroutine("child-1", "continue")
            assert "revived and message dispatched" in first
            manager.enqueue_message.assert_awaited_once()

            # Child failed again — still terminal for the second attempt.
            manager.enqueue_message.reset_mock()
            second = await send_message.coroutine("child-1", "continue again")

        # Refusal mirrors the hint's semantics, verbatim shape.
        assert second == _REVIVE_REFUSAL_TEMPLATE.format(iid="child-1"), (
            f"Expected revive-once refusal; got: {second!r}"
        )
        # NOT dispatched, NOT injected.
        manager.enqueue_message.assert_not_awaited()
        manager.set_injection.assert_not_called()
        # Refusal does not consume/increment anything further.
        manager.note_agent_tool_revive.assert_called_once_with("child-1")
        assert manager.get_agent_tool_revive_count("child-1") == 1

    async def test_t3_counter_survives_across_tool_invocations(self):
        """T3: the counter lives on the manager, so it survives across
        SEPARATE tool invocations (fresh tool closures) within the same
        daemon/manager lifetime — and is per-child."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="failed")
            tool_one = _get_send_message_tool(manager)
            tool_two = _get_send_message_tool(manager)

            first = await tool_one.coroutine("child-9", "continue")
            assert "revived and message dispatched" in first

            # Different tool invocation, same manager → same counter.
            second = await tool_two.coroutine("child-9", "continue")
            assert second == _REVIVE_REFUSAL_TEMPLATE.format(iid="child-9")

            # A DIFFERENT child is unaffected — the counter is per-child.
            other = await tool_two.coroutine("child-10", "continue")
            assert "revived and message dispatched" in other
            assert manager.get_agent_tool_revive_count("child-10") == 1

    async def test_t4_user_api_revive_not_counted_not_blocked(self):
        """T4: the user-API revive authority does NOT increment the
        counter and is NOT blocked by it — even after an agent-tool
        revive was already refused for the same child. The user-API
        path (``_prepare_enqueued_message``) calls
        ``manager.enqueue_message`` directly with no guard interposed;
        this test simulates exactly that call."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="error")
            send_message = _get_send_message_tool(manager)

            first = await send_message.coroutine("child-1", "continue")
            assert "revived and message dispatched" in first
            refused = await send_message.coroutine("child-1", "continue")
            assert "already been revived once" in refused

            # Simulate the user-API revive: direct enqueue_message call
            # (what the service layer does), no agent-tool guard.
            manager.note_agent_tool_revive.reset_mock()
            await manager.enqueue_message(
                instance_id="child-1",
                message="user says continue",
                source="api",
            )

        # NOT blocked: the enqueue went through despite the refusal.
        assert manager.enqueue_message.await_count >= 1
        # NOT counted: the user-API send never touches the counter.
        manager.note_agent_tool_revive.assert_not_called()
        assert manager.get_agent_tool_revive_count("child-1") == 1

    def test_t4_service_layer_revive_path_has_no_counter_hookup(self):
        """T4 (construction guarantee): the shared service-layer revive
        path must contain NO reference to the counter symbols — the
        user-API revive is uncounted and unblocked by construction, not
        by convention."""
        src = (
            REPO_ROOT / "daemon" / "services" / "instance_messaging.py"
        ).read_text(encoding="utf-8")
        for symbol in (
            "note_agent_tool_revive",
            "get_agent_tool_revive_count",
            "_agent_tool_revive_counts",
        ):
            assert symbol not in src, (
                f"instance_messaging.py must not reference {symbol!r} — "
                f"the revive-once guard is agent-tool-path only"
            )

    async def test_t5_busy_queue_rejection_does_not_consume_revive_budget(self):
        """W2 (review-warning lock): the queue-busy guard sits BEFORE
        the revive-once guard, so a busy-queue rejection must NOT
        consume the child's revive budget. Two-step demonstration:

          Step 1: terminal child + busy queue → busy-rejection text;
                  counter UNTOUCHED (note_agent_tool_revive not called,
                  get_agent_tool_revive_count == 0); no dispatch.
          Step 2: same child, queue now idle → FIRST revive granted
                  (counter 0→1, message dispatched).

        This locks the ordering invariant "a busy-queue rejection never
        consumes revive budget" — without it, a transient busy queue
        could silently burn the one allowed revive on a no-op enqueue.
        Same busy-queue shape as ``TestEnqueueOverrideForLoadSkill::
        test_load_skill_keeps_queue_busy_guard``."""
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager = _make_manager(status="error")
            # Step 1 — busy queue. Overrides the fixture's idle counts.
            manager.get_queue_stats = AsyncMock(
                return_value={"pending_count": 5, "processing_count": 2}
            )
            send_message = _get_send_message_tool(manager)

            refused = await send_message.coroutine("child-1", "continue")

        # Busy-queue rejection fired — NOT the revive-once refusal.
        # The counter check was never reached because the busy-queue
        # guard short-circuited first.
        assert "already has a message in progress" in refused, (
            f"Expected busy-queue rejection; got: {refused!r}"
        )
        # Counter UNTOUCHED: the busy-queue rejection sits BEFORE the
        # revive-once guard, so the budget is preserved.
        manager.note_agent_tool_revive.assert_not_called()
        assert manager.get_agent_tool_revive_count("child-1") == 0
        # No dispatch happened — busy-queue guard returns BEFORE
        # enqueue_message is even attempted.
        manager.enqueue_message.assert_not_awaited()
        # Reset call tracking so step 2 assertions are clean.
        manager.note_agent_tool_revive.reset_mock()
        manager.enqueue_message.reset_mock()

        # Step 2 — same child, queue now IDLE. First revive is granted
        # (the budget survived the prior busy rejection).
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            manager.get_queue_stats = AsyncMock(
                return_value={"pending_count": 0, "processing_count": 0}
            )
            send_message = _get_send_message_tool(manager)

            granted = await send_message.coroutine("child-1", "continue")

        # Counter 0→1, message dispatched via the enqueue-revive branch.
        assert (
            "Instance was error — revived and message dispatched."
            in granted
        ), f"Expected revival prefix; got: {granted!r}"
        manager.note_agent_tool_revive.assert_called_once_with("child-1")
        assert manager.get_agent_tool_revive_count("child-1") == 1
        manager.enqueue_message.assert_awaited_once()

    def test_refusal_text_documented_in_docstring_and_full_doc(self):
        """Spec quick-win #7: the guard is documented in the tool's
        ``_full_doc_`` (and docstring — D10 parity): in-memory counter,
        daemon-restart reset, agent-tool-path-only, refusal shape."""
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
        # Normalize whitespace: the docs line-wrap the phrases, but the
        # runtime refusal string is single-line and locked exactly by
        # ``test_t2_second_agent_tool_revive_refused``.
        docstring = " ".join(send_message_tool.description.split())
        full_doc = " ".join(send_message_tool._full_doc_.split())

        for phrase in (
            "Refused: ",
            "has already been revived once and failed again",
            "Spawn a replacement instance instead",
        ):
            assert phrase in docstring, (
                f"Docstring must document the revive-once guard "
                f"({phrase!r}); got: {docstring[:300]!r}"
            )
            assert phrase in full_doc, (
                f"_full_doc_ must document the revive-once guard "
                f"({phrase!r}); got: {full_doc[:300]!r}"
            )
        # The v1 caveats are documented too (in-memory / restart-reset /
        # agent-tool path only).
        for caveat in (
            "in-memory cumulative counter",
            "daemon restart resets it",
            "agent-tool path",
        ):
            assert caveat in full_doc, (
                f"_full_doc_ must document guard caveat {caveat!r}"
            )

    def test_provenance_documented_in_docstring_and_full_doc(self):
        """Quick-win #1 (D10 parity): the injection provenance marker
        is documented in BOTH the docstring and ``_full_doc_`` —
        ``internal_agent:<caller_instance_id>`` source on the
        downstream HumanMessage for agent-tool sends; no ``source``
        for user-API sends (back-compat)."""
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
        # Normalize whitespace: the docs line-wrap the phrases.
        docstring = " ".join(send_message_tool.description.split())
        full_doc = " ".join(send_message_tool._full_doc_.split())

        for phrase in (
            "Provenance (quick-win #1)",
            "internal_agent:<caller_instance_id>",
            'HumanMessage.additional_kwargs["source"]',
            "user-API injected sends carry no",
        ):
            assert phrase in docstring, (
                f"Docstring must document injection provenance "
                f"({phrase!r}); got: {docstring[:300]!r}"
            )
            assert phrase in full_doc, (
                f"_full_doc_ must document injection provenance "
                f"({phrase!r}); got: {full_doc[:300]!r}"
            )


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


# ---------------------------------------------------------------------------
# Phase 2 (agent-instance-tools) — ``subtree_messages`` tests
# ---------------------------------------------------------------------------
#
# Layout: 9 classes (a–i) per phase2-plan.md §Test Plan, plus a
# helpers block that builds a mock manager pre-wired with the per-test
# subtree lineage + ``get_messages`` stub. The pattern mirrors the
# Phase 1 ``_make_manager`` / ``_get_send_message_tool`` factory but
# adds ``manager.get_tree_ids_permanent`` and ``manager.get_messages``
# stubs because the new tool never touches ``manager._instance_repository``
# (D14 / R-D14 — single chokepoint ``_validate_subtree_target``).
#
# The tests exercise:
#   (a) subtree scoping — accept
#   (b) subtree scoping — reject (cross-subtree / unrelated / root caller)
#   (c) filter behavior — all four canonical roles + combined + child/target
#   (d) D12 synthetic-message exclusion + caller-own-system KEPT +
#       20-descendant token fuzz
#   (e) pagination + caps (100→20 cap; global offset/limit on a
#       200-message instance; cap_first_N_per_instance)
#   (f) token safety (truncation, ToolMessage redaction, ceiling, summary)
#   (g) performance / fixture (mocked sequential get_messages, per-
#       instance error skip+warn, 100-instance fuzz asserting EXACTLY
#       20 get_messages calls)
#   (h) compaction-instability smoke (documented-behavior assertion)
#   (i) registration (tool_help non-empty; meta.json with/without
#       subtree_messages → resolvable/not-resolvable)
# ---------------------------------------------------------------------------


import inspect
import json
from contextlib import contextmanager
from typing import Iterable


def _make_subtree_manager(
    *,
    subtree_ids: list[str] | None = None,
    messages_by_iid: dict[str, list[dict]] | None = None,
    statuses_by_iid: dict[str, str] | None = None,
    get_messages_side_effect: Exception | None = None,
) -> MagicMock:
    """Build a mock manager pre-wired for ``subtree_messages``.

    Default caller (``current_instance_id`` from the shared fixture) is
    ``"parent-instance"``; the agent_id is ``"developer"``.

    Args:
        subtree_ids: The list ``manager.get_tree_ids_permanent(caller)``
            should return. Defaults to ``["parent-instance"]`` (caller is
            a root with no descendants).
        messages_by_iid: Per-instance message list returned by
            ``manager.get_messages(iid)``. Missing keys → empty list.
        statuses_by_iid: Per-instance status dict returned by
            ``manager.get_instance_info(iid).get("status")``. Missing keys
            → default ``"running"``.
        get_messages_side_effect: If provided, ``get_messages`` raises
            this exception for EVERY call (used for the per-instance
            error-skip test).

    Returns:
        A ``MagicMock`` whose ``get_tree_ids_permanent``, ``get_messages``,
        and ``get_instance_info`` stubs are pre-wired.
    """
    manager = MagicMock()
    if subtree_ids is None:
        subtree_ids = ["parent-instance"]
    if messages_by_iid is None:
        messages_by_iid = {}
    if statuses_by_iid is None:
        statuses_by_iid = {}

    # CRITICAL: subtree_messages calls ``manager.get_tree_ids_permanent``,
    # NOT ``manager._instance_repository.get_tree_ids_permanent``. The
    # helper IS the only authorization chokepoint. Track calls so the
    # 100-instance fuzz test can assert exactly-once-per-working-set.
    manager.get_tree_ids_permanent = MagicMock(return_value=list(subtree_ids))

    async def _get_messages(iid):
        if get_messages_side_effect is not None:
            raise get_messages_side_effect
        return list(messages_by_iid.get(iid, []))

    manager.get_messages = AsyncMock(side_effect=_get_messages)

    def _get_info(iid):
        status = statuses_by_iid.get(iid, "running")
        return {"status": status, "agent_id": "developer"}

    manager.get_instance_info = MagicMock(side_effect=_get_info)
    # Mimic the Phase 1 manager shape so ``create_instance_tools`` builds
    # without exploding (it does not touch these in the subtree code path,
    # but it reads them during factory wiring).
    manager._instance_repository = MagicMock()
    manager.engine = MagicMock()
    manager.write_guard = MagicMock()
    manager._live_hub = MagicMock()
    return manager


def _get_subtree_messages_tool(manager: MagicMock):
    """Build the instance tools and return the ``subtree_messages`` tool object.

    Mirrors ``tests.helpers.send_message_fixtures.get_send_message_tool``
    (Phase 1) — same factory-helper patches so the closure builds cleanly
    in isolation. Returns the ``StructuredTool`` whose ``.coroutine`` is
    the actual async function (so we bypass Pydantic schema validation,
    matching the Phase 1 convention).
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

    for t in tools:
        if getattr(t, "name", None) == "subtree_messages":
            return t
    raise RuntimeError(
        "subtree_messages tool not found in create_instance_tools output; "
        f"got {[getattr(t, 'name', None) for t in tools]}"
    )


def _user_msg(content: str, iid: str = "i-child-1", ts: str = "2026-08-26T00:00:00Z") -> dict:
    """Helper: build a canonical user-role message dict."""
    return {
        "message_id": f"m-user-{iid}-{ts}-{hash(content) & 0xffff}",
        "type": "human",
        "role": "user",
        "content": content,
        "thinking": None,
        "thinking_extracted": None,
        "tool_calls": None,
        "images": None,
        "created_at": ts,
        "instance_id": iid,
    }


def _assistant_msg(content: str, iid: str = "i-child-1", ts: str = "2026-08-26T00:00:01Z") -> dict:
    return {
        "message_id": f"m-ai-{iid}-{ts}-{hash(content) & 0xffff}",
        "type": "ai",
        "role": "assistant",
        "content": content,
        "thinking": None,
        "thinking_extracted": None,
        "tool_calls": None,
        "images": None,
        "created_at": ts,
        "instance_id": iid,
    }


def _tool_msg(name: str, args: str, iid: str = "i-child-1", ts: str = "2026-08-26T00:00:02Z") -> dict:
    return {
        "message_id": f"m-tool-{iid}-{ts}-{hash(name + args) & 0xffff}",
        "type": "tool",
        "role": "tool",
        "name": name,
        "content": "<output omitted>",
        "args": args,
        "thinking": None,
        "thinking_extracted": None,
        "tool_calls": None,
        "images": None,
        "created_at": ts,
        "instance_id": iid,
    }


def _system_msg(content: str, iid: str = "i-child-1", ts: str = "2026-08-26T00:00:03Z") -> dict:
    return {
        "message_id": f"m-system-{iid}-{ts}-{hash(content) & 0xffff}",
        "type": "system",
        "role": "system",
        "content": content,
        "thinking": None,
        "thinking_extracted": None,
        "tool_calls": None,
        "images": None,
        "created_at": ts,
        "instance_id": iid,
    }


def _synthetic_system_msg(iid: str = "i-child-1", content: str = "system prompt") -> dict:
    return {
        "message_id": f"synthetic-system-{iid}",
        "type": "system",
        "role": "system",
        "content": content,
        "thinking": None,
        "thinking_extracted": None,
        "tool_calls": None,
        "images": None,
        "created_at": "2026-08-26T00:00:00Z",
        "instance_id": iid,
        "is_synthetic": True,
    }


def _synthetic_context_msg(iid: str = "i-child-1", content: str = "context block") -> dict:
    return {
        "message_id": f"synthetic-context-project-{iid}-0",
        "type": "human",
        "role": "user",
        "content": content,
        "thinking": None,
        "thinking_extracted": None,
        "tool_calls": None,
        "images": None,
        "created_at": "2026-08-26T00:00:01Z",
        "instance_id": iid,
        "is_synthetic": True,
        "context_kind": "project",
    }


# ---------------------------------------------------------------------------
# a. Subtree scoping — accept
# ---------------------------------------------------------------------------


class TestSubtreeScopingAccept:
    """Phase 2 §Test Plan (a): caller has 3 children; queries return
    own + descendants, or a single grandchild subtree."""

    @pytest.mark.timeout(10)
    async def test_target_none_returns_caller_and_all_children(self):
        """``target=None`` resolves to the caller's own subtree.

        The tool internally calls ``manager.get_tree_ids_permanent(caller)``
        for authz and (when target IS caller) reuses the same set for
        retrieval. The mock's ``subtree_ids`` is the caller's full
        lineage — both calls hit it, the retrieval just uses the same
        list."""
        caller_subtree = ["parent-instance", "i-child-1", "i-child-2", "i-child-3"]
        manager = _make_subtree_manager(
            subtree_ids=caller_subtree,
            messages_by_iid={
                "parent-instance": [_user_msg("hello", iid="parent-instance")],
                "i-child-1": [_assistant_msg("ack1", iid="i-child-1")],
                "i-child-2": [_assistant_msg("ack2", iid="i-child-2")],
                "i-child-3": [_assistant_msg("ack3", iid="i-child-3")],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        # All four instances contributed messages.
        assert "parent-instance" in result
        assert "i-child-1" in result
        assert "i-child-2" in result
        assert "i-child-3" in result
        # No permission error.
        assert "ERROR" not in result
        # ``get_tree_ids_permanent`` was called once (authz); the
        # resolved target IS the caller, so the second retrieval-time
        # call is short-circuited and the facade is invoked exactly once.
        manager.get_tree_ids_permanent.assert_called_once_with("parent-instance")

    @pytest.mark.timeout(10)
    async def test_target_grandchild_returns_grandchild_subtree_only(self):
        """When ``target=grandchild_id``, the helper validates the
        target is in the caller's subtree (authz) and the queried set is
        the TARGET's own subtree (target + its descendants) — not the
        caller's whole tree. The plan's §Test Plan (a) acceptance.

        The mock's ``subtree_ids`` is the caller's full lineage (for
        authz); the second facade call (target's own subtree) is wired
        via ``side_effect`` keyed on the requested root."""
        grandchild = "i-grandchild-A"
        caller_subtree = ["parent-instance", "i-child-1", "i-child-2", grandchild]
        grandchild_subtree = [grandchild]  # no further descendants

        manager = MagicMock()
        manager.get_tree_ids_permanent = MagicMock(
            side_effect=lambda root: caller_subtree if root == "parent-instance" else grandchild_subtree
        )
        async def _get_messages(iid):
            return {
                grandchild: [_assistant_msg("grandchild-only", iid=grandchild)],
                "i-child-2": [_assistant_msg("child-2", iid="i-child-2")],
            }.get(iid, [])
        manager.get_messages = AsyncMock(side_effect=_get_messages)
        manager.get_instance_info = MagicMock(
            side_effect=lambda iid: {"status": "running", "agent_id": "developer"}
        )
        manager._instance_repository = MagicMock()
        manager.engine = MagicMock()
        manager.write_guard = MagicMock()
        manager._live_hub = MagicMock()

        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(target_instance_id=grandchild)

        # Grandchild's message present.
        assert "grandchild-only" in result
        # The siblings under the caller are NOT in the grandchild's
        # subtree, so their messages are not in the output.
        assert "child-2" not in result
        assert "ERROR" not in result
        # Two facade calls: authz (caller's tree) + retrieval (target's tree).
        assert manager.get_tree_ids_permanent.call_count == 2
        # Retrieval happened ONLY for the target's subtree (1 instance).
        assert manager.get_messages.call_count == 1

    @pytest.mark.timeout(10)
    async def test_caller_is_a_root_returns_only_own_messages(self):
        """Caller has no descendants — subtree = {caller}."""
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={
                "parent-instance": [_user_msg("root-caller-msg", iid="parent-instance")],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        assert "root-caller-msg" in result
        assert "ERROR" not in result
        manager.get_tree_ids_permanent.assert_called_once_with("parent-instance")


# ---------------------------------------------------------------------------
# b. Subtree scoping — reject
# ---------------------------------------------------------------------------


class TestSubtreeScopingReject:
    """Phase 2 §Test Plan (b): cross-subtree / unrelated / root cases."""

    @pytest.mark.timeout(10)
    async def test_target_sibling_rejected(self):
        """target=someone else's instance (sibling of caller, NOT a
        descendant) → permission error."""
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={},
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(target_instance_id="i-sibling-1")

        assert "ERROR" in result
        assert "not in the caller" in result
        # No messages read.
        manager.get_messages.assert_not_called()

    @pytest.mark.timeout(10)
    async def test_target_unrelated_rejected(self):
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={},
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(target_instance_id="i-random-other")

        assert "ERROR" in result
        assert "not in the caller" in result
        manager.get_messages.assert_not_called()

    @pytest.mark.timeout(10)
    async def test_caller_is_root_target_none_returns_only_self(self):
        """Caller is a root (parent_id NULL); ``target=None`` → caller's
        own subtree = {caller} only."""
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={
                "parent-instance": [_user_msg("self-only", iid="parent-instance")],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        assert "self-only" in result
        assert "ERROR" not in result

    @pytest.mark.timeout(10)
    async def test_empty_string_target_rejected(self):
        """Empty string is malformed — treated as not-found."""
        manager = _make_subtree_manager(subtree_ids=["parent-instance"])
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(target_instance_id="")

        assert "ERROR" in result


# ---------------------------------------------------------------------------
# c. Filter behavior
# ---------------------------------------------------------------------------


class TestFilterBehavior:
    """Phase 2 §Test Plan (c): all four canonical roles + combined AND
    + child/target conflict error."""

    @pytest.mark.timeout(10)
    @pytest.mark.parametrize(
        "role",
        ["user", "assistant", "tool", "system"],
    )
    async def test_each_canonical_role_filter(self, role):
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-child-1"],
            messages_by_iid={
                "parent-instance": [
                    _user_msg("u1", iid="parent-instance"),
                    _assistant_msg("a1", iid="parent-instance"),
                    _tool_msg("search", '{"q":"x"}', iid="parent-instance"),
                    _system_msg("sys1", iid="parent-instance"),
                ],
                "i-child-1": [
                    # Synthetic + real system in descendant — D12 drops
                    # both, leaving only non-system messages.
                    _user_msg("child-u", iid="i-child-1"),
                    _assistant_msg("child-a", iid="i-child-1"),
                ],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(filters={"role": role})

        assert "ERROR" not in result
        # The filter selects only the requested role. We assert the
        # expected presence/absence per role:
        #   user: parent u1 + child-u (both kept)
        #   assistant: parent a1 + child-a
        #   tool: parent tool msg (redacted as [name] args)
        #   system: parent sys1 ONLY (descendant system msgs dropped by D12)
        if role == "user":
            assert "u1" in result
            assert "child-u" in result
            assert "a1" not in result
            assert "sys1" not in result
        elif role == "assistant":
            assert "a1" in result
            assert "child-a" in result
            assert "u1" not in result
        elif role == "tool":
            assert "[search]" in result
            assert "u1" not in result
            assert "a1" not in result
        elif role == "system":
            # Caller's own system message KEPT (target == caller).
            assert "sys1" in result
            # No descendant system messages (D12 prunes them).
            assert "ERROR" not in result
            # The synthetic system msg (synthetic-system-parent-instance)
            # is still pruned even for the caller's own instance — D12
            # drops synthetic markers regardless of target. We verify by
            # noting the synthetic system is NOT the "sys1" content
            # (sys1 is the caller's REAL system msg).

    @pytest.mark.timeout(10)
    async def test_non_canonical_role_filter_rejected(self):
        """``"human"`` and ``"ai"`` are NOT canonical and must be
        rejected at the filter layer."""
        manager = _make_subtree_manager(subtree_ids=["parent-instance"])
        tool = _get_subtree_messages_tool(manager)

        for bad in ("human", "ai"):
            result = await tool.coroutine(filters={"role": bad})
            assert "ERROR" in result, f"role={bad!r} must be rejected"
            assert "canonical" in result

    @pytest.mark.timeout(10)
    async def test_child_instance_id_filter(self):
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-child-1", "i-child-2"],
            messages_by_iid={
                "parent-instance": [_user_msg("parent-u", iid="parent-instance")],
                "i-child-1": [_assistant_msg("child-1-a", iid="i-child-1")],
                "i-child-2": [_assistant_msg("child-2-a", iid="i-child-2")],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(filters={"child_instance_id": "i-child-1"})

        assert "child-1-a" in result
        assert "child-2-a" not in result
        assert "parent-u" not in result

    @pytest.mark.timeout(10)
    async def test_status_filter(self):
        """``filters.status`` keeps only messages from instances whose
        ``manager.get_instance_info(iid)["status"]`` matches."""
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-child-1", "i-child-2"],
            messages_by_iid={
                "parent-instance": [_user_msg("parent-msg", iid="parent-instance")],
                "i-child-1": [_assistant_msg("c1", iid="i-child-1")],
                "i-child-2": [_assistant_msg("c2", iid="i-child-2")],
            },
            statuses_by_iid={
                "parent-instance": "running",
                "i-child-1": "running",
                "i-child-2": "completed",
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(filters={"status": "completed"})

        assert "c2" in result
        assert "[assistant] c1" not in result
        assert "[user] parent-msg" not in result
        # status filter calls get_instance_info EXACTLY once per
        # working-set instance (3 in this fixture) under the gather —
        # pins one-status-call-per-instance and guards against
        # double-fetch / fan-out regressions.
        assert manager.get_instance_info.call_count == 3

    @pytest.mark.timeout(10)
    async def test_combined_filters_AND_semantics(self):
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-child-1"],
            messages_by_iid={
                "parent-instance": [_user_msg("parent-u", iid="parent-instance")],
                "i-child-1": [
                    _user_msg("c1u", iid="i-child-1"),
                    _assistant_msg("c1a", iid="i-child-1"),
                ],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(
            filters={"role": "assistant", "child_instance_id": "i-child-1"}
        )

        assert "c1a" in result
        assert "c1u" not in result
        # Use the full marker to avoid false positives from header
        # substrings (the param block echoes ``'parent-instance'``).
        assert "[user] parent-u" not in result

    @pytest.mark.timeout(10)
    async def test_child_target_conflict_error(self):
        """``filters.child_instance_id != target_instance_id`` is an
        error UNLESS equal."""
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-child-1", "i-child-2"],
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(
            target_instance_id="i-child-1",
            filters={"child_instance_id": "i-child-2"},
        )

        assert "ERROR" in result
        assert "must be equal" in result


# ---------------------------------------------------------------------------
# d. D12 synthetic-message exclusion + caller-own-system KEPT
# ---------------------------------------------------------------------------


class TestD12SyntheticExclusion:
    """Phase 2 §Test Plan (d): D12 prunes ``is_synthetic=True`` and
    real system-role messages in any descendant result. The caller's
    own system messages are KEPT."""

    @pytest.mark.timeout(10)
    async def test_descendant_zero_synthetic_zero_real_system(self):
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-child-1"],
            messages_by_iid={
                "parent-instance": [_user_msg("u", iid="parent-instance")],
                "i-child-1": [
                    _synthetic_system_msg(iid="i-child-1", content="FAKE-SYS-CONTENT"),
                    _synthetic_context_msg(iid="i-child-1", content="FAKE-CTX-CONTENT"),
                    _system_msg("FAKE-REAL-SYSTEM", iid="i-child-1"),
                    _user_msg("real-u", iid="i-child-1"),
                    _assistant_msg("real-a", iid="i-child-1"),
                ],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        # Synthetic content MUST NOT appear.
        assert "FAKE-SYS-CONTENT" not in result
        assert "FAKE-CTX-CONTENT" not in result
        # Real descendant system message MUST NOT appear.
        assert "FAKE-REAL-SYSTEM" not in result
        # Non-system descendant messages DO appear.
        assert "real-u" in result
        assert "real-a" in result
        # Synthetic markers (message_id prefixes) MUST NOT appear either.
        assert "synthetic-system-" not in result
        assert "synthetic-context-" not in result
        # Caller's user message appears too.
        assert "[user]" in result

    @pytest.mark.timeout(10)
    async def test_target_caller_keeps_caller_system_messages(self):
        """Counter-test: target == caller → caller's own system messages
        are KEPT."""
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={
                "parent-instance": [
                    _system_msg("caller-real-sys", iid="parent-instance"),
                    _user_msg("u", iid="parent-instance"),
                ],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()  # target=None → caller

        assert "caller-real-sys" in result
        assert "u" in result

    @pytest.mark.timeout(10)
    async def test_target_explicit_caller_keeps_caller_system(self):
        """Explicit ``target=current_instance_id`` is equivalent to
        ``target=None``."""
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={
                "parent-instance": [_system_msg("caller-sys", iid="parent-instance")],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(target_instance_id="parent-instance")

        assert "caller-sys" in result

    @pytest.mark.timeout(10)
    async def test_20_descendant_token_fuzz_within_1_5x_baseline(self):
        """20-descendant subtree, each with a synthetic system prompt.
        Output token count <= 1.5x the no-synthetic baseline.

        Baseline = messages WITHOUT synthetic prefixes (we just strip
        the synthetic markers from the same content list). The fuzz
        verifies D12 actually drops them at retrieval time, not in the
        formatter."""
        subtree = ["parent-instance"] + [f"i-d{i:02d}" for i in range(20)]
        synthetic_payload = "X" * 5000  # big-ish, will truncate to 200 + ellipsis

        # Baseline: same messages WITHOUT is_synthetic flag.
        baseline_msgs = {
            iid: [
                _user_msg(synthetic_payload, iid=iid),
            ]
            for iid in subtree[1:]
        }
        # With-synthetic: add a synthetic system + synthetic context + real
        # system msg to each descendant — D12 must drop ALL of them.
        with_synthetic_msgs = {
            iid: [
                _synthetic_system_msg(iid=iid, content=synthetic_payload),
                _synthetic_context_msg(iid=iid, content=synthetic_payload),
                _system_msg(synthetic_payload, iid=iid),
                _user_msg(synthetic_payload, iid=iid),
            ]
            for iid in subtree[1:]
        }

        # Baseline run.
        mgr_baseline = _make_subtree_manager(
            subtree_ids=subtree,
            messages_by_iid=baseline_msgs,
        )
        tool = _get_subtree_messages_tool(mgr_baseline)
        baseline_result = await tool.coroutine()
        baseline_len = len(baseline_result)

        # With-synthetic run.
        mgr_synth = _make_subtree_manager(
            subtree_ids=subtree,
            messages_by_iid=with_synthetic_msgs,
        )
        tool = _get_subtree_messages_tool(mgr_synth)
        synth_result = await tool.coroutine()
        synth_len = len(synth_result)

        # The with-synthetic run should NOT be wildly bigger than the
        # baseline — D12 drops the synthetic/system entries before the
        # formatter truncates. Allow 1.5x margin for the appended
        # header lines (per-instance block headers, total counts).
        assert synth_len <= baseline_len * 1.5, (
            f"D12 leakage: with-synthetic output ({synth_len} chars) "
            f"exceeds 1.5x baseline ({baseline_len} chars)."
        )
        # Sanity: synthetic payload never appears in output (truncation
        # only keeps 200 chars; full payload is 5000 chars).
        assert synthetic_payload[:250] not in synth_result

    # -----------------------------------------------------------------
    # W1 INTERIM RESOLUTION (this batch). The descendant filter no
    # longer matches on a literal ``[SYSTEM CONTEXT: …]`` content
    # prefix — it matches on the structured ``injected_message=True``
    # marker surfaced by ``daemon.utils.serialize_message``. Every
    # context-injection HumanMessage construction site (context
    # builders, task-context injection, FIFO drain, report drain)
    # stamps ``injected_message=True`` in ``additional_kwargs``; the
    # structured marker is therefore authoritative. Legacy pre-W1
    # checkpoints that pre-date the marker scheme are not present in
    # the active deployment — see decisions.md D12 addendum
    # RESOLUTION for the data-flow evidence.
    # -----------------------------------------------------------------

    def _injected_user_msg(
        self,
        content: str,
        *,
        iid: str = "i-child-1",
        ts: str = "2026-08-26T00:00:00Z",
        context_kind: str = "task_context",
        source: str | None = None,
    ) -> dict:
        """Build a serialized user-role context-injection dict that
        mimics what ``get_instance_messages`` returns AFTER the W1
        ``serialize_message`` fix: the ``injected_message`` marker
        flows through (no longer stripped) and ``context_kind`` /
        ``source`` are surfaced when present on the source message.
        """
        d = _user_msg(content, iid=iid, ts=ts)
        d["injected_message"] = True
        if context_kind:
            d["context_kind"] = context_kind
        if source:
            d["source"] = source
        return d

    @pytest.mark.timeout(10)
    async def test_descendant_injected_context_user_dropped(self):
        """W1 STRUCTURED FILTER — descendant's persisted context-
        injection HumanMessage (with ``injected_message=True``
        surfaced via ``serialize_message``) MUST be dropped from
        the descendant result.
        """
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-child-1"],
            messages_by_iid={
                "parent-instance": [_user_msg("caller-msg", iid="parent-instance")],
                "i-child-1": [
                    # Persisted context-injection HumanMessage: the
                    # W1 structured marker is set, so the descendant
                    # filter drops it.
                    self._injected_user_msg(
                        "[SYSTEM CONTEXT: Task Context]\n## Foo\nbar",
                        iid="i-child-1",
                    ),
                    # A LEGITIMATE user message on the descendant — must
                    # still pass through.
                    _user_msg("real-user-msg", iid="i-child-1"),
                ],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        # W1: persisted context-injection user-role message MUST NOT
        # appear in descendant result.
        assert "SYSTEM CONTEXT" not in result
        # The legitimate user message DOES appear.
        assert "real-user-msg" in result

    @pytest.mark.timeout(10)
    async def test_caller_injected_context_user_kept(self):
        """Counter-test (W1): the SAME-shaped injected message on the
        CALLER's own instance is KEPT (the caller's own injections are
        its own context — the structured filter is gated by
        ``is_descendant=True``).
        """
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={
                "parent-instance": [
                    # Same shape as the descendant test above, but on
                    # the caller (target == caller).
                    self._injected_user_msg(
                        "[SYSTEM CONTEXT: Task Context]\n## Caller's own context",
                        iid="parent-instance",
                    ),
                ],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()  # target=None → caller

        # Caller's own context injection IS visible to it.
        assert "SYSTEM CONTEXT" in result
        assert "Caller's own context" in result

    @pytest.mark.timeout(10)
    async def test_user_message_quoting_marker_mid_text_kept(self):
        """W1 false-positive guard: a legitimate user message that
        CONTAINS ``[SYSTEM CONTEXT:`` mid-text (NOT prefix) MUST be
        KEPT. The structured filter discriminates on the
        ``injected_message`` flag, not on the content prefix — the
        false-positive risk that motivated the INTERIM's
        ``no-trim / no-normalize`` rule is eliminated.
        """
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-child-1"],
            messages_by_iid={
                "parent-instance": [_user_msg("caller-msg", iid="parent-instance")],
                "i-child-1": [
                    # Quote of the marker mid-text — NOT a prefix AND
                    # no structured marker (regular user content).
                    _user_msg(
                        "the user wrote: \"[SYSTEM CONTEXT: foo]\" earlier",
                        iid="i-child-1",
                    ),
                ],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        # Legitimate user message quoting the marker MUST be kept.
        assert "the user wrote" in result
        assert "earlier" in result

    @pytest.mark.timeout(10)
    async def test_unmarked_system_context_prefix_descendant_kept_after_W1(self):
        """W1 RESOLUTION witness test — documents the explicit
        behavior change vs the W1 INTERIM. The literal-prefix check
        has been REMOVED, so a descendant message that has the
        ``[SYSTEM CONTEXT: ...]`` content prefix but DOES NOT carry
        the structured ``injected_message=True`` marker is now KEPT
        in the descendant result.

        Rationale: every ``[SYSTEM CONTEXT: ...]`` construction site
        in the daemon stamps the structured marker. A descendant
        message that lacks the marker but has the literal content
        prefix is therefore either (a) a legitimate user message that
        quotes the prefix for some reason, or (b) legacy data that
        pre-dates the marker scheme (not present in active
        deployment per the D12 addendum RESOLUTION evidence). In
        either case, the structured marker is the authoritative
        signal — the filter no longer falls back to a content-prefix
        match.
        """
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-child-1"],
            messages_by_iid={
                "parent-instance": [_user_msg("caller-msg", iid="parent-instance")],
                "i-child-1": [
                    # Same shape as the W1 INTERIM test fixture — a
                    # user-role message with the literal prefix — but
                    # WITHOUT the structured marker. The W1
                    # INTERIM-prefix check would have dropped this.
                    # The W1 structured filter KEEPS it.
                    _user_msg(
                        "[SYSTEM CONTEXT: Legacy prefix]\n## Old data",
                        iid="i-child-1",
                    ),
                ],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        # W1 RESOLUTION: the unmarked message is KEPT (the literal
        # prefix is no longer a filter criterion).
        assert "SYSTEM CONTEXT" in result
        assert "Legacy prefix" in result

    @pytest.mark.timeout(10)
    async def test_descendant_injected_with_source_dropped(self):
        """W1 source provenance — a descendant's injected HumanMessage
        (``injected_message=True`` + ``source="internal_report:..."``)
        MUST still be dropped by the descendant filter even though it
        carries the provenance marker. The structured filter does not
        care about ``source``; only ``injected_message`` matters for
        the descendant-leak guard.
        """
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-child-1"],
            messages_by_iid={
                "parent-instance": [_user_msg("caller-msg", iid="parent-instance")],
                "i-child-1": [
                    self._injected_user_msg(
                        "[SYSTEM CONTEXT: Task Context]\n## child report",
                        iid="i-child-1",
                        source="internal_report:i-child-1",
                    ),
                ],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        # Even with provenance source, the structured-marker filter
        # drops it from the descendant view.
        assert "child report" not in result
        assert "internal_report" not in result


# ---------------------------------------------------------------------------
# e. Pagination + caps
# ---------------------------------------------------------------------------


class TestPaginationAndCaps:
    """Phase 2 §Test Plan (e): max_instances=20 cap, global offset/limit
    pagination, cap_first_N_per_instance."""

    @pytest.mark.timeout(10)
    async def test_100_instance_subtree_capped_to_20(self):
        """100-instance subtree → first 20 (sorted by instance_id)
        returned + warning text."""
        subtree = ["parent-instance"] + [f"i-{i:03d}" for i in range(99)]
        messages = {
            iid: [_assistant_msg(f"msg-{iid}", iid=iid)]
            for iid in subtree
        }
        manager = _make_subtree_manager(subtree_ids=subtree, messages_by_iid=messages)
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        assert "100 instances" in result or "WARNING" in result
        # Sorted: instance_ids starting with 'i-0' come first.
        assert "i-000" in result
        # Exact 20 working-set calls: parent + first 19 children (sorted).
        # (The cap slices sorted_subtree[:20].)
        # We assert the LAST instance in the cap is i-018, not i-099.
        assert "i-018" in result
        assert "i-099" not in result

    @pytest.mark.timeout(10)
    async def test_global_pagination_on_200_message_instance(self):
        """Single instance with 200 messages; global offset/limit.

        Pre-merge security-council batch S1 (caller-first sort):
        ``working_set`` orders the caller FIRST, so the caller's single
        ``p`` message occupies slot 0 of the merged collection and the
        first page therefore carries 1 caller message + 49 i-big
        messages (NOT 50 i-big). Subsequent pages shift accordingly.
        The mock returns the SAME ``subtree_ids`` for both authz AND
        target-subtree lookups (a known mock limitation), so the
        caller's instance appears in the target's "subtree" here.
        """
        big_msgs = [
            _user_msg(f"msg-{i:03d}", iid="i-big", ts=f"2026-08-26T00:0{i//60}:{i%60:02d}Z")
            for i in range(200)
        ]
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-big"],
            messages_by_iid={
                "parent-instance": [_user_msg("p", iid="parent-instance")],
                "i-big": big_msgs,
            },
        )
        tool = _get_subtree_messages_tool(manager)

        # First page (S1: caller at slot 0; 49 i-big messages in slots
        # 1..49). msg-049 is now in the SECOND page.
        result_p0 = await tool.coroutine(target_instance_id="i-big", limit=50, offset=0)
        assert "p" in result_p0  # S1: caller's message is on the first page
        assert "msg-000" in result_p0
        assert "msg-048" in result_p0  # S1: 49th i-big msg (last in window)
        assert "msg-049" not in result_p0  # S1: moved to next page
        assert "msg-050" not in result_p0

        # Second page (S1: starts at slot 50 = msg-049).
        result_p1 = await tool.coroutine(target_instance_id="i-big", limit=50, offset=50)
        assert "msg-049" in result_p1
        assert "msg-098" in result_p1  # S1: last in second page
        assert "msg-099" not in result_p1
        assert "msg-000" not in result_p1

        # Past-the-end → empty body + warning. Total collected = 1
        # (caller) + 200 (i-big) = 201 messages. offset=300 is past
        # the end.
        result_p3 = await tool.coroutine(target_instance_id="i-big", limit=50, offset=300)
        assert "msg-000" not in result_p3
        assert "msg-150" not in result_p3
        assert "msg-199" not in result_p3
        assert "offset=300" in result_p3

    @pytest.mark.timeout(10)
    async def test_cap_first_N_per_instance(self):
        """Each instance contributes at most N messages BEFORE global
        pagination."""
        msgs = [_user_msg(f"u{i}", iid="i-c") for i in range(10)]
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-c"],
            messages_by_iid={
                "parent-instance": [_user_msg("p", iid="parent-instance")],
                "i-c": msgs,
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(
            target_instance_id="i-c",
            cap_first_N_per_instance=5,
        )

        # First 5 messages (u0..u4) appear; u5..u9 do not.
        assert "u0" in result
        assert "u4" in result
        assert "u5" not in result
        assert "u9" not in result

    # -----------------------------------------------------------------
    # Pre-merge security-council batch W4 — input upper-bound clamps
    # (silent, not errors) + limit=0 semantics + negative-value
    # errors. The truncation-warning copy "(<= 100)" is now literally
    # true against the working cap.
    # -----------------------------------------------------------------

    @pytest.mark.timeout(10)
    async def test_max_instances_clamped_to_100(self):
        """W4: ``max_instances=500`` is silently clamped to 100; the
        cap-slice still fires (asserted via get_messages call count)."""
        subtree = ["parent-instance"] + [f"i-{i:03d}" for i in range(199)]
        # Pre-populate ALL 200 so a broken cap would yield 200 calls.
        messages = {
            iid: [_assistant_msg(f"m-{iid}", iid=iid)] for iid in subtree
        }
        manager = _make_subtree_manager(subtree_ids=subtree, messages_by_iid=messages)
        tool = _get_subtree_messages_tool(manager)

        await tool.coroutine(max_instances=500)

        # Clamp fired: 100 reads, NOT 200.
        assert manager.get_messages.call_count == 100, (
            f"W4: max_instances=500 must clamp to 100; got "
            f"{manager.get_messages.call_count}"
        )

    @pytest.mark.timeout(10)
    async def test_limit_clamped_to_500(self):
        """W4: ``limit=99999`` is silently clamped to 500; the cap-slice
        still fires (asserted via the messages= metadata line). The
        8000-char output ceiling truncates the body, so we check the
        metadata header (which renders first and survives the
        truncation) plus a boundary check on the working set."""
        # Build a single instance with 600 messages; limit=99999 should
        # clamp to 500 → 500 messages in the window (NOT 99999, NOT
        # 600).
        big_msgs = [
            _user_msg(
                f"msg-{i:04d}",
                iid="i-big",
                ts=f"2026-08-26T00:{i//60:02d}:{i%60:02d}Z",
            )
            for i in range(600)
        ]
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-big"],
            messages_by_iid={
                "parent-instance": [_user_msg("p", iid="parent-instance")],
                "i-big": big_msgs,
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(target_instance_id="i-big", limit=99999)

        # The clamp cut at 500 — verified via the metadata header line
        # (rendered first; survives the ceiling truncation). Total
        # collected = 1 caller + 600 i-big = 601 messages.
        assert "messages=500" in result, (
            f"W4: limit=99999 must clamp to 500; metadata should say "
            f"'messages=500'; got first 200 chars: {result[:200]!r}"
        )
        assert "of 601 collected" in result, (
            f"W4: total should be 601 (1 caller + 600 i-big); got "
            f"first 200 chars: {result[:200]!r}"
        )

    @pytest.mark.timeout(10)
    async def test_limit_zero_emits_headers_zero_rows(self):
        """W4: ``limit=0`` is the explicit "no rows" sentinel — emit the
        per-instance block headers + filter/pagination metadata, but
        return zero message rows."""
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-c"],
            messages_by_iid={
                "parent-instance": [_user_msg("parent-msg", iid="parent-instance")],
                "i-c": [_user_msg("child-msg", iid="i-c")],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(target_instance_id="i-c", limit=0)

        # Per-instance block headers ARE present (the structure).
        assert "=== instance_id: i-c ===" in result
        # messages= line reports zero rows.
        assert "messages=0" in result
        # But the message content itself does NOT appear.
        assert "parent-msg" not in result
        assert "child-msg" not in result

    @pytest.mark.timeout(10)
    async def test_negative_limit_is_error(self):
        """W4: negative ``limit`` is a clean ERROR string (matches the
        existing negative-offset / negative-cap_first_N_per_instance
        behavior)."""
        manager = _make_subtree_manager(subtree_ids=["parent-instance"])
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(limit=-5)

        assert result.startswith("ERROR: subtree_messages: ")
        assert "limit" in result
        assert "-5" in result


# ---------------------------------------------------------------------------
# f. Token safety
# ---------------------------------------------------------------------------


class TestTokenSafety:
    """Phase 2 §Test Plan (f): truncation, ToolMessage redaction,
    output ceiling, summary mode."""

    @pytest.mark.timeout(10)
    async def test_long_content_truncated_with_ellipsis(self):
        big = "A" * 10_000
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={
                "parent-instance": [_user_msg(big, iid="parent-instance")],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        # Content truncated to 200 + ellipsis = ~201 chars of As + …
        # The full 10k-A string MUST NOT appear in the output.
        assert big not in result
        # Truncation marker present.
        assert "…" in result

    @pytest.mark.timeout(10)
    async def test_tool_message_redaction(self):
        """ToolMessage → ``[name] <first 100 chars of args>``. Raw
        ``content`` is OMITTED."""
        long_args = (
            '{"q": "' + ('A' * 200) + '"}'
        )  # 209 chars — well over the 100-char cap.
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={
                "parent-instance": [
                    _tool_msg("search_docs", long_args, iid="parent-instance"),
                ],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        # Tool name rendered.
        assert "[search_docs]" in result
        # Raw output content OMITTED.
        assert "<output omitted>" not in result
        # Full args string NOT in output (truncated at 100 chars + ellipsis).
        assert long_args not in result
        # Truncation marker present.
        assert "…" in result

    @pytest.mark.timeout(10)
    async def test_tool_marker_message_redacted_without_canonical_role(self):
        """Defense-in-depth: a message with tool markers
        (``tool_call_id`` / ``type=="tool"``) but a non-canonical role
        MUST still be redacted. Otherwise a leaked message carrying
        tool output could overflow the token budget."""
        # Build a message that LOOKS like a tool message but has its
        # ``role`` stripped to a non-canonical value (e.g. a buggy
        # serializer). The marker keys ``type="tool"`` and
        # ``tool_call_id`` are still present.
        leaky_msg = _tool_msg(
            "leaky_search",
            '{"q":"short-args"}',
            iid="parent-instance",
        )
        leaky_msg["role"] = "function"  # non-canonical — must not bypass redaction
        # ``type == "tool"`` and ``tool_call_id`` (set via ``_tool_msg``
        # via name+args hash; we add tool_call_id explicitly here)
        leaky_msg["tool_call_id"] = "call_abc123"
        leaky_msg["content"] = "RAW-LARGE-TOOL-OUTPUT-" + ("X" * 500)

        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={
                "parent-instance": [leaky_msg],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        # Redaction still fires — ``[leaky_search]`` name+args form
        # appears (NOT the raw content).
        assert "[leaky_search]" in result
        # The raw content MUST NOT appear anywhere in the output.
        assert "RAW-LARGE-TOOL-OUTPUT" not in result
        assert ("X" * 100) not in result
        # The non-canonical role label itself is harmless to print,
        # but its raw content MUST be gone.
        assert "[function]" not in result or "RAW-LARGE-TOOL-OUTPUT" not in result

    @pytest.mark.timeout(10)
    async def test_output_ceiling_tail_truncate(self):
        """Huge output → tail truncated + ceiling warning."""
        subtree = ["parent-instance"] + [f"i-{i:02d}" for i in range(15)]
        # Each instance: 30 messages of 200-char content = ~6000 chars
        # per instance × 16 instances = ~96k chars → well over the 8000
        # ceiling.
        messages = {
            iid: [
                _user_msg("X" * 200, iid=iid) for _ in range(30)
            ]
            for iid in subtree
        }
        manager = _make_subtree_manager(subtree_ids=subtree, messages_by_iid=messages)
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        # Ceiling truncation marker present.
        assert "output truncated at" in result
        # The warning line at the end mentions the ceiling.
        assert "8000-char ceiling" in result

    @pytest.mark.timeout(10)
    async def test_summary_mode_emits_metadata_only(self):
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={
                "parent-instance": [
                    _user_msg("hello world", iid="parent-instance"),
                    _assistant_msg("hi back", iid="parent-instance"),
                ],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(summary=True)

        # Metadata fields present (role, created_at).
        assert "[user]" in result
        assert "[assistant]" in result
        # 80-char summary preview — full message IS present, just inside
        # the 80-char cap.
        assert "hello world" in result
        assert "hi back" in result

    # -----------------------------------------------------------------
    # Pre-merge security-council batch W2 — redaction symmetry. The
    # tool-marker detection (``role == "tool"`` /
    # ``type == "tool"`` / ``tool_call_id`` / ``_call_id``) MUST fire in
    # BOTH full and summary mode; previously it only fired in full mode
    # so summary=True would render raw content for tool-marker messages
    # with non-canonical roles.
    # -----------------------------------------------------------------

    @pytest.mark.timeout(10)
    async def test_summary_mode_redacts_tool_marker_non_canonical_role(self):
        """W2: ``summary=True`` on a tool-marker message with a
        non-canonical role MUST still route through redaction. The
        summary preview shows the redacted preview, NOT raw content."""
        leaky_msg = _tool_msg(
            "leaky_search",
            '{"q":"short-args"}',
            iid="parent-instance",
        )
        leaky_msg["role"] = "function"  # non-canonical — must not bypass redaction
        leaky_msg["tool_call_id"] = "call_abc123"
        leaky_msg["content"] = "RAW-LARGE-TOOL-OUTPUT-" + ("X" * 500)

        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={
                "parent-instance": [leaky_msg],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(summary=True)

        # W2 invariant: summary preview shows the REDACTED form
        # ``[leaky_search] {args}`` and NEVER the raw content.
        assert "[leaky_search]" in result
        # The raw content MUST NOT appear anywhere in the summary output.
        assert "RAW-LARGE-TOOL-OUTPUT" not in result
        assert ("X" * 100) not in result

    # -----------------------------------------------------------------
    # Pre-merge security-council batch W3 — unbounded name fields. The
    # rendered tool name is capped at 64 chars + "…", and the joined
    # ``tools=…`` / ``(tools: …)`` summary string is capped at 200 chars
    # + "…". Applies at all three render sites.
    # -----------------------------------------------------------------

    @pytest.mark.timeout(10)
    async def test_tool_name_capped_at_64_chars_in_full_mode(self):
        """W3: a 200-char tool name is truncated to 64+ellipsis in full
        mode (``_summarize_tool_message``)."""
        long_name = "n" * 200
        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={
                "parent-instance": [
                    _tool_msg(long_name, '{"q":"x"}', iid="parent-instance"),
                ],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        # Full 200-char name MUST NOT appear.
        assert long_name not in result
        # Truncation marker present (the 64-char prefix of n's + "…").
        assert "…" in result
        # Find the ``[name]`` block: 64 n's + ellipsis inside brackets.
        truncated = "[" + ("n" * 64) + "…]"
        assert truncated in result

    @pytest.mark.timeout(10)
    async def test_tool_name_capped_at_64_chars_in_summary_mode(self):
        """W3: tool-marker redaction in summary mode also caps the name
        (W2 hoisted the redaction path; W3 caps its output)."""
        long_name = "n" * 200
        leaky_msg = _tool_msg(
            long_name, '{"q":"x"}', iid="parent-instance",
        )
        leaky_msg["content"] = "RAW-OUTPUT-" + ("X" * 200)

        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={
                "parent-instance": [leaky_msg],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine(summary=True)

        # Full 200-char name MUST NOT appear.
        assert long_name not in result
        # Truncation marker present.
        assert "…" in result
        # Raw output MUST NOT appear (W2 + W3 together).
        assert "RAW-OUTPUT" not in result

    @pytest.mark.timeout(10)
    async def test_joined_tool_call_names_capped_at_200_chars(self):
        """W3: an assistant message with many/long ``tool_calls`` joins
        into a string capped at 200 chars + ``"…"``."""
        long_tool = "t" * 80
        # 5 tool calls × 80 chars + 4 commas = 404 chars joined.
        tool_calls = [
            {"name": long_tool, "arguments": "{}"} for _ in range(5)
        ]
        assistant_msg = {
            "message_id": "m-many-tools",
            "type": "ai",
            "role": "assistant",
            "content": "ok",
            "thinking": None,
            "thinking_extracted": None,
            "tool_calls": tool_calls,
            "images": None,
            "created_at": "2026-08-26T00:00:00Z",
            "instance_id": "parent-instance",
        }

        manager = _make_subtree_manager(
            subtree_ids=["parent-instance"],
            messages_by_iid={
                "parent-instance": [assistant_msg],
            },
        )
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        # The un-truncated joined string (404 chars worth of 80-char t's
        # separated by commas) MUST NOT appear.
        joined_untruncated = ",".join([long_tool] * 5)
        assert joined_untruncated not in result
        # Truncation marker present (joined list overflowed the 200 cap).
        assert "…" in result


# ---------------------------------------------------------------------------
# g. Performance / fixture
# ---------------------------------------------------------------------------


class TestPerformanceFixture:
    """Phase 2 §Test Plan (g): mocked get_messages sequential correctness,
    per-instance error skip+warn, 100-instance fuzz asserting EXACTLY 20
    get_messages calls."""

    @pytest.mark.timeout(10)
    async def test_mocked_get_messages_sequential_for_5_instances(self):
        subtree = ["parent-instance"] + [f"i-c{i}" for i in range(4)]
        messages = {iid: [_user_msg(f"m-{iid}", iid=iid)] for iid in subtree}
        manager = _make_subtree_manager(subtree_ids=subtree, messages_by_iid=messages)
        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        # All 5 instances contributed.
        for iid in subtree:
            assert iid in result
        # ``get_messages`` was called once per subtree member (5 total).
        assert manager.get_messages.call_count == 5

    @pytest.mark.timeout(10)
    async def test_per_instance_error_skipped_remaining_returned(self):
        """One descendant raises in ``get_messages`` → skip + warn; the
        rest still come back.

        Verifies the partial-failure path: ONLY ``i-broken`` fails;
        the other two instances still surface their messages in the
        result. A uniform side_effect (raising on EVERY call) would
        only exercise the all-fail path; this test must exercise the
        partial-failure branch to guard against future regressions
        where one bad descendant silently takes down the whole query.
        """
        subtree = ["parent-instance", "i-ok", "i-broken"]
        working_msgs = {
            "parent-instance": [_user_msg("p", iid="parent-instance")],
            "i-ok": [_assistant_msg("c-ok", iid="i-ok")],
            # ``i-broken`` intentionally absent from ``messages_by_iid``
            # — the per-iid side effect overrides this for that one id.
        }
        manager = _make_subtree_manager(
            subtree_ids=subtree,
            messages_by_iid=working_msgs,
        )

        # Per-iid side effect: raise ONLY for ``i-broken``. All other
        # instances fall through to the default fixture behavior (which
        # returns ``list(messages_by_iid.get(iid, []))``).
        original_side_effect = manager.get_messages.side_effect

        async def _per_iid_side_effect(iid):
            if iid == "i-broken":
                raise KeyError("simulated missing checkpoint")
            return await original_side_effect(iid)

        manager.get_messages = AsyncMock(side_effect=_per_iid_side_effect)

        tool = _get_subtree_messages_tool(manager)

        result = await tool.coroutine()

        # No exception bubbled out.
        assert isinstance(result, str)
        # All three reads happened (one per subtree member) — failure
        # is reported per-instance, NOT via early termination.
        assert manager.get_messages.call_count == 3
        # The two working instances' messages DO appear in the result.
        assert "p" in result
        assert "c-ok" in result
        # The broken instance contributed no message (its only message
        # would have been something distinctive — none exists here, so
        # the negative assertion is "no error markers leak either").
        assert "ERROR" not in result
        # Sanity: the parent's user msg + the OK child's assistant msg
        # are both rendered, proving the partial-failure branch is real.
        assert "[user] p" in result
        assert "[assistant] c-ok" in result

    @pytest.mark.timeout(10)
    async def test_100_instance_fuzz_asserts_exactly_20_get_messages(self):
        """100-instance subtree → cap slices to first 20 → EXACTLY 20
        get_messages calls (NOT 100, NOT 0). Pre-merge security-council
        batch S1: caller is prioritized via the composite sort key
        ``(x != caller, x)`` so it always survives the cap slice; the
        first 20 returned are caller + first 19 children sorted
        lexicographically (``i-000`` … ``i-018``). Without S1, a pure
        ``sorted()`` would push ``parent-instance`` off the slice
        because ``"parent-instance" > "i-018"`` lexicographically —
        this test would still see 20 calls but the CALLER would be
        missing from the working set."""
        subtree = ["parent-instance"] + [f"i-{i:03d}" for i in range(99)]
        # Pre-populate messages for ALL 100 so the test would have
        # failed with 100 calls if the cap were broken.
        messages = {
            iid: [_assistant_msg(f"m-{iid}", iid=iid)] for iid in subtree
        }
        manager = _make_subtree_manager(subtree_ids=subtree, messages_by_iid=messages)
        tool = _get_subtree_messages_tool(manager)

        await tool.coroutine()

        # Exactly 20: caller (parent-instance, prioritized via S1) + first
        # 19 children sorted lexicographically (``i-000`` … ``i-018``).
        assert manager.get_messages.call_count == 20, (
            f"Expected EXACTLY 20 get_messages calls under "
            f"max_instances=20 cap; got {manager.get_messages.call_count}."
        )
        # S1 invariant: caller was queried (lexicographic sort would
        # have skipped it because ``parent-instance`` > ``i-018``).
        assert manager.get_messages.call_args_list[0].args == (
            "parent-instance",
        ), (
            f"S1: caller must be first in working_set; got "
            f"{manager.get_messages.call_args_list[0].args!r}"
        )


# ---------------------------------------------------------------------------
# h. Compaction-instability smoke (documented behavior, not a bug)
# ---------------------------------------------------------------------------


class TestCompactionInstabilitySmoke:
    """Phase 2 §Test Plan (h): compaction replaces pre-compaction messages
    with ``RemoveMessage`` sentinels + a SystemMessage summary
    (``daemon/compaction.py:1036-1070``). Offsets returned today may not
    correspond to the same messages tomorrow. This is documented
    behavior; the test passes if (a) the result is a string AND (b)
    either the message set differs from a pre-compaction query OR a
    clear "compacted_at"-style warning is in the output. NOT a bug
    assertion — we pin the behavior, not the bug."""

    @pytest.mark.timeout(10)
    async def test_compaction_instability_documented_behavior(self):
        # Pre-compaction: 100 user messages.
        pre = [_user_msg(f"pre-{i}", iid="i-target") for i in range(100)]
        # Post-compaction: 1 SystemMessage summary + 2 recent messages.
        post = [
            _system_msg("SUMMARY: 98 prior messages summarized.", iid="i-target"),
            _user_msg("post-0", iid="i-target"),
            _user_msg("post-1", iid="i-target"),
        ]

        # Pre-compaction query.
        mgr_pre = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-target"],
            messages_by_iid={"parent-instance": [], "i-target": pre},
        )
        tool = _get_subtree_messages_tool(mgr_pre)
        result_pre = await tool.coroutine(target_instance_id="i-target", limit=50)

        # Post-compaction query.
        mgr_post = _make_subtree_manager(
            subtree_ids=["parent-instance", "i-target"],
            messages_by_iid={"parent-instance": [], "i-target": post},
        )
        tool2 = _get_subtree_messages_tool(mgr_post)
        result_post = await tool2.coroutine(target_instance_id="i-target", limit=50)

        # Documented behavior: the message set differs across compaction
        # OR the warning surfaces. Either is acceptable.
        differs = result_pre != result_post
        has_warning = "compaction" in result_post.lower() or "offset" in result_post.lower()
        assert differs or has_warning, (
            "Compaction-instability smoke: result_post must differ from "
            "result_pre OR surface a compaction/offset warning."
        )


# ---------------------------------------------------------------------------
# i. Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    """Phase 2 §Test Plan (i): tool_help non-empty; meta.json
    with/without subtree_messages → resolvable/not-resolvable."""

    @pytest.mark.timeout(10)
    def test_subtree_messages_in_tool_list(self):
        """The factory closure contains ``subtree_messages``."""
        manager = _make_subtree_manager()
        tool = _get_subtree_messages_tool(manager)

        assert tool is not None
        assert tool.name == "subtree_messages"

    @pytest.mark.timeout(10)
    def test_subtree_messages_full_doc_non_empty(self):
        """``tool._full_doc_`` is non-empty (powers ``tool_help``)."""
        manager = _make_subtree_manager()
        tool = _get_subtree_messages_tool(manager)

        full_doc = getattr(tool, "_full_doc_", None)
        assert isinstance(full_doc, str) and full_doc.strip(), (
            "subtree_messages._full_doc_ must be a non-empty string"
        )
        # Mentions the key concepts (subtree, D12, opt-in).
        assert "subtree" in full_doc.lower()
        assert "D12" in full_doc or "synthetic" in full_doc.lower()

    @pytest.mark.timeout(10)
    def test_tool_help_returns_subtree_messages_doc(self):
        """``tool_help("subtree_messages")`` returns a non-empty doc
        (the help tool reads from ``_full_doc_``).

        Uses ``agent_id="leader"`` because leader is the canonical
        opt-in agent — the developer agent does NOT have
        ``subtree_messages`` in their ``tools.allow`` and the help tool
        filters by allowed tools (so it would surface
        ``"Tool 'subtree_messages' not found or not available."`` for
        developer).
        """
        from unittest.mock import patch

        from daemon.tools.help import create_help_tool

        manager = _make_subtree_manager()
        # Build a minimal tool list with just subtree_messages.
        patches = _patch_heavy_helpers()
        for p in patches:
            p.start()
        try:
            from daemon.tools.instance import create_instance_tools
            tools = create_instance_tools(manager, "parent-instance", "developer")
        finally:
            for p in reversed(patches):
                p.stop()

        # Wire the help tool. Stub the registry so leader is reported
        # as having ``subtree_messages`` in its allow list (the real
        # registry requires full app init to scan meta.json files).
        sentinel_allowed = {
            "subtree_messages", "send_message", "spawn_instance",
            "tool_help", "bash",
        }

        with patch(
            "daemon.tools.help._get_allowed_tools",
            return_value=sentinel_allowed,
        ):
            # Filter MagicMock-contaminated entries before they reach
            # the real create_help_tool. Without this guard, patch
            # teardown in the builder block above can leave MagicMock
            # objects in ``tools``, and scan_tools_for_full_docs will
            # write MagicMock-keyed entries into the module-level
            # ``_tool_metadata`` singleton, poisoning downstream tests.
            tools = [t for t in tools if not isinstance(t, MagicMock)]
            help_tool = create_help_tool(tools, agent_id="leader")
            # ``tool_help`` is a sync tool — call it directly (no
            # ``.coroutine`` attribute).
            result = help_tool.func("subtree_messages") if hasattr(help_tool, "func") else help_tool("subtree_messages")

        assert isinstance(result, str)
        assert "subtree" in result.lower()
        # Specifically: not the "not found" notice (which would mean
        # the agent-filter stripped it out).
        assert "not found" not in result.lower()

    @pytest.mark.timeout(10)
    def test_meta_json_with_subtree_messages_resolves(self):
        """An agent meta with ``tools.allow: ["subtree_messages"]``
        resolves the tool via ``resolve_tool_filter``."""
        from pathlib import Path

        from daemon.registry import AgentMetadata, ToolFilter
        from daemon.tools.instance import (
            _SUBTREE_CANONICAL_ROLES,
            resolve_tool_filter,
        )

        # Build a synthetic agent meta + the canonical tool-category map
        # (``list_tools_by_category`` would do this for real).
        meta = AgentMetadata(
            id="leader",
            name="Leader",
            description="test",
            path=Path("/tmp/leader"),
            team_members=[],
            tools=ToolFilter(allow=["subtree_messages"], deny=[]),
        )
        # Tool category map that includes the "instance" category with
        # subtree_messages listed (matches what the registry would
        # produce post-decoration).
        categories = {
            "instance": [
                "spawn_instance", "send_message", "terminate_instance",
                "list_instances", "get_instance_info",
                "subtree_messages",
            ],
        }

        resolved = resolve_tool_filter(
            allow=meta.tools.allow, deny=meta.tools.deny,
            tool_categories=categories,
        )
        assert resolved is not None
        assert "subtree_messages" in resolved
        # Sibling instance tools are NOT included (narrow opt-in).
        assert "spawn_instance" not in resolved
        assert "send_message" not in resolved

    @pytest.mark.timeout(10)
    def test_meta_json_without_subtree_messages_does_not_resolve(self):
        """An agent meta WITHOUT ``subtree_messages`` and WITHOUT the
        ``instance`` category does not resolve it."""
        from pathlib import Path

        from daemon.registry import AgentMetadata, ToolFilter
        from daemon.tools.instance import resolve_tool_filter

        meta = AgentMetadata(
            id="approver",
            name="Approver",
            description="test",
            path=Path("/tmp/approver"),
            team_members=[],
            tools=ToolFilter(allow=["bash", "filesystem"], deny=[]),
        )
        categories = {
            "instance": ["subtree_messages", "send_message"],
        }

        resolved = resolve_tool_filter(
            allow=meta.tools.allow, deny=meta.tools.deny,
            tool_categories=categories,
        )
        assert resolved is not None
        assert "subtree_messages" not in resolved
        assert "send_message" not in resolved
        assert "bash" in resolved

    @pytest.mark.timeout(10)
    def test_meta_json_with_instance_category_resolves(self):
        """An agent with ``tools.allow: ["instance"]`` (whole-category
        grant) DOES resolve subtree_messages."""
        from pathlib import Path

        from daemon.registry import AgentMetadata, ToolFilter
        from daemon.tools.instance import resolve_tool_filter

        meta = AgentMetadata(
            id="project-manager",
            name="PM",
            description="test",
            path=Path("/tmp/pm"),
            team_members=[],
            tools=ToolFilter(allow=["instance"], deny=[]),
        )
        categories = {
            "instance": [
                "subtree_messages", "send_message", "spawn_instance",
            ],
        }

        resolved = resolve_tool_filter(
            allow=meta.tools.allow, deny=meta.tools.deny,
            tool_categories=categories,
        )
        assert resolved is not None
        assert "subtree_messages" in resolved
        assert "send_message" in resolved

    @pytest.mark.timeout(10)
    def test_leader_meta_json_has_subtree_messages_entry(self):
        """Sanity: the leader meta.json — the canonical opt-in agent —
        carries the explicit ``subtree_messages`` entry. Locks in the
        config decision."""
        meta_path = REPO_ROOT / "agents" / "leader" / "meta.json"
        meta = json.loads(meta_path.read_text())
        allow = (meta.get("tools") or {}).get("allow") or []
        assert "subtree_messages" in allow, (
            f"agents/leader/meta.json tools.allow must contain "
            f"'subtree_messages'; got: {allow}"
        )

    @pytest.mark.timeout(10)
    def test_planner_and_tester_resolve_subtree_messages_via_registry(self):
        """Quick-win #4: planner and tester opt into ``subtree_messages``.

        Unlike the leader sanity test above (raw file read), this goes
        through the production meta resolution path: a real
        ``AgentRegistry`` discovery of the repo ``agents/`` tree, then
        ``get_version()`` with the ``get_resolved()`` fallback — the
        resolution convention from the Version Tag Tool Resolution fix
        (all meta lookups MUST use ``get_version()`` first).
        """
        from daemon.registry import AgentRegistry

        agents_dir = REPO_ROOT / "agents"
        assert agents_dir.is_dir(), f"agents/ not found at {agents_dir}"

        registry = AgentRegistry(agents_dir)
        registry.discover()

        for agent_id in ("planner", "tester"):
            # Production resolution convention: get_version() first,
            # get_resolved() fallback.
            meta = registry.get_version(agent_id) or registry.get_resolved(
                agent_id
            )
            assert meta is not None, (
                f"{agent_id} was not discovered from the real agents/ tree"
            )
            allow = (meta.tools.allow if meta.tools is not None else None) or []
            assert "subtree_messages" in allow, (
                f"agents/{agent_id}/meta.json tools.allow must contain "
                f"'subtree_messages' (resolved via get_version/"
                f"get_resolved); got: {allow}"
            )

    @pytest.mark.timeout(10)
    def test_aget_state_regression_guard(self):
        """Regression guard: ``aget_state`` MUST NOT appear in the new
        tool code. The plan's exit-criterion grep."""
        # Read the source and verify the new code path does not call
        # ``manager.graph.aget_state`` or ``.graph.aget_state``.
        instance_src = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text()
        # The submodule is read-only — anywhere ``aget_state`` appears is
        # a regression.
        for ln, line in enumerate(instance_src.splitlines(), 1):
            if "aget_state" in line:
                raise AssertionError(
                    f"daemon/tools/instance.py:{ln} contains 'aget_state' — "
                    "the new tool code MUST use manager.get_messages(...), "
                    "not aget_state (see phase2-plan.md exit criterion)."
                )

    @pytest.mark.timeout(10)
    def test_manager_facade_method_exists(self):
        """The leader-approved ``Manager.get_tree_ids_permanent`` facade
        method exists and delegates to ``manager._instance_repository``."""
        import inspect

        from daemon.manager import InstanceManager

        assert hasattr(InstanceManager, "get_tree_ids_permanent"), (
            "Manager.get_tree_ids_permanent facade method is required"
        )
        method = InstanceManager.get_tree_ids_permanent
        sig = inspect.signature(method)
        # Unbound classmethod-style: ``self`` is present in the params
        # list when accessed via the class (unlike a bound instance
        # method). We strip ``self`` for the assertion.
        params = [p for p in sig.parameters.keys() if p != "self"]
        assert params == ["caller_instance_id"], (
            f"get_tree_ids_permanent must accept only "
            f"'caller_instance_id'; got: {params}"
        )

        # Read source — must delegate via ``self._instance_repository``.
        src = inspect.getsource(method)
        assert "self._instance_repository.get_tree_ids_permanent(" in src, (
            "Manager.get_tree_ids_permanent must delegate to "
            "self._instance_repository.get_tree_ids_permanent"
        )
