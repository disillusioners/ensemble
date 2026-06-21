"""A11 — Invariant test pack: completion authority enforcement.

This test pack enforces the completion authority invariant as a CI gate:

    When USE_LEGACY_WAITING_FOR_CASCADE=OFF, CM is the sole completion authority.
    Every waiting_for mutation site has a matching CM call OR is documented
    cache-only. Every waiting_for control-flow read is gated by the flag OR
    documented cache-only.

Test categories
==============
  1. Mutation site coverage: every SQL UPDATES with waiting_for is gated behind
     use_legacy_cascade. If a developer adds a new ungated increment/decrement,
     CI fails here.
  2. Control-flow read coverage: every waiting_for == 0 / > 0 cascade decision
     is gated behind use_legacy_cascade or raises a hard error when CM is None
     and the flag is OFF.
  3. CM consistency: rebuild_from_db() is NOT gated (cache-only per ADR-011),
     pending_count matches is_complete(), and DEBUG_COMPLETION_INVARIANT fires.

Run with::

    python -m pytest tests/test_completion_authority_invariant.py -v --tb=short

NOTE: This is a UNIT test file (in-memory or source inspection, no real DB
required for most tests). The behavioral CM consistency tests use mocks.
"""

from __future__ import annotations

import ast
import logging
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Reuse CM test helpers (no daemon start required).
from tests.test_correlation_manager import (
    make_callback,
    make_cm,
    make_instance,
    make_instance_repo,
)

from daemon.services.correlation_manager import (
    CorrelationManager,
    set_correlation_manager,
)


# =============================================================================
# Root of the daemon source tree
# =============================================================================

DAEMON_ROOT = Path(__file__).parent.parent / "daemon"

# Names used in the codebase to read the legacy cascade flag.
_LEGACY_FLAG_NAMES = (
    "use_legacy_cascade",
    "use_legacy_waiting_for_cascade",
)


# =============================================================================
# Documented exceptions — cache-only writes NOT requiring the flag gate
# =============================================================================
# These sites intentionally write `waiting_for` outside `if use_legacy_cascade:`
# because they are cache cleanup (terminate) or status flips (terminate) —
# they are NOT completion-control-flow. ADR-011 documents the column as the
# rebuild cache; terminating an instance legitimately wipes its cache.
# Each entry: (file relative path, line number of the SQL string constant).
_DOCUMENTED_CACHE_ONLY_SITES: set[tuple[str, int]] = {
    # terminate_instance: status='terminated', waiting_for=0 (atomic cleanup)
    # The line of the SQL string constant — must be updated if the
    # terminate_instance cascade in instance_lifecycle.py is refactored
    # and the SQL is moved.
    ("services/instance_lifecycle.py", 1563),
}


# =============================================================================
# AST helpers
# =============================================================================


def _flag_name_referenced(node: ast.AST) -> bool:
    """Return True if the AST node references the legacy cascade flag by name.

    Handles bare Name, Attribute access (``self._config.use_legacy_...``),
    and dotted names recursively.
    """
    if isinstance(node, ast.Name):
        return node.id in _LEGACY_FLAG_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in _LEGACY_FLAG_NAMES
    if isinstance(node, ast.UnaryOp):
        return _flag_name_referenced(node.operand)
    if isinstance(node, ast.BoolOp):
        return any(_flag_name_referenced(v) for v in node.values)
    if isinstance(node, ast.Compare):
        return _flag_name_referenced(node.left) or any(
            _flag_name_referenced(c) for c in node.comparators
        )
    if isinstance(node, ast.Call):
        return any(_flag_name_referenced(a) for a in node.args) or any(
            _flag_name_referenced(kw.value) for kw in node.keywords
        )
    return False


def _has_use_legacy_cascade_test(node: ast.If) -> bool:
    """Check whether an If node tests the legacy cascade flag.

    Accepts:
      - bare: ``if use_legacy_cascade:``
      - negated: ``if not use_legacy_cascade:``
      - boolean: ``if X and use_legacy_cascade and Y:``
      - getattr: ``if getattr(self._config, "use_legacy_waiting_for_cascade", ...):``
    """
    return _flag_name_referenced(node.test)


def _find_enclosing_gate(tree: ast.AST, target_line: int) -> ast.If | None:
    """Find the innermost `if use_legacy_cascade:` block that contains target_line.

    If no such block exists, returns None — indicating the site is ungated.
    """
    candidates: list[tuple[ast.If, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _has_use_legacy_cascade_test(node):
            end = getattr(node, "end_lineno", None) or node.lineno
            if node.lineno <= target_line <= end:
                # Score by block depth (longer block = innermost)
                candidates.append((node, end - node.lineno))

    if not candidates:
        return None

    return max(candidates, key=lambda x: x[1])[0]


def _find_docstring_line_numbers(tree: ast.AST) -> set[int]:
    """Return the line numbers of all docstring string constants.

    A docstring is the first statement of a function/class/module body
    and is an `ast.Expr` whose value is a string `ast.Constant`.
    """
    docstring_lines: set[int] = set()

    def _scan_body(body: list[ast.stmt]) -> None:
        if not body:
            return
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstring_lines.add(first.value.lineno)

    # Module docstring
    if isinstance(tree, ast.Module):
        _scan_body(tree.body)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _scan_body(node.body)

    return docstring_lines


def _find_waiting_for_sql_strings(
    source: str,
) -> list[tuple[int, str]]:
    """Find every SQL string literal touching waiting_for.

    Filters out docstrings. Returns a list of (lineno, sql_string).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    docstring_lines = _find_docstring_line_numbers(tree)
    results: list[tuple[int, str]] = []
    sql_indicators = ("UPDATE", "SELECT", "SET ", "RETURNING", "FROM")

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        val = node.value
        if "waiting_for" not in val:
            continue
        if node.lineno in docstring_lines:
            continue
        if any(ind in val for ind in sql_indicators):
            results.append((node.lineno, val))

    return results


def _check_sql_gated(file_path: Path, file_rel: str) -> list[tuple[int, str, str]]:
    """Check a source file for any ungated waiting_for SQL.

    Filters out documented cache-only sites (e.g. terminate_instance cleanup).
    Returns [(line, sql_snippet, reason)] — empty list = all gated.
    """
    source = file_path.read_text()
    tree = ast.parse(source)
    sql_sites = _find_waiting_for_sql_strings(source)
    ungated: list[tuple[int, str, str]] = []

    for line, sql in sql_sites:
        # Skip documented cache-only sites.
        if (file_rel, line) in _DOCUMENTED_CACHE_ONLY_SITES:
            continue
        gate = _find_enclosing_gate(tree, line)
        if gate is None:
            snippet = sql[:80].replace("\n", " ")
            ungated.append((line, snippet, "no enclosing `if use_legacy_cascade:` block"))

    return ungated


# =============================================================================
# Category 1 tests — Mutation site coverage
# =============================================================================


class TestMutationSiteCoverage:
    """Every waiting_for SQL mutation is gated behind use_legacy_cascade.

    Documented cache-only sites (terminate_instance cleanup) are allowed.
    If a developer adds a new ungated increment/decrement, CI fails here.
    """

    def test_instance_send_message_increment_gated(self):
        """daemon/tools/instance.py — waiting_for SQL UPDATE is gated."""
        result = _check_sql_gated(
            DAEMON_ROOT / "tools" / "instance.py",
            "tools/instance.py",
        )
        assert result == [], (
            "UNGTED waiting_for SQL mutation in tools/instance.py:\n"
            + "\n".join(f"  line {l}: {sql!r} [{reason}]" for l, sql, reason in result)
        )

    def test_child_reports_decrement_gated(self):
        """daemon/services/child_reports.py — waiting_for SQL decrements gated."""
        result = _check_sql_gated(
            DAEMON_ROOT / "services" / "child_reports.py",
            "services/child_reports.py",
        )
        assert result == [], (
            "UNGTED waiting_for SQL mutation in services/child_reports.py:\n"
            + "\n".join(f"  line {l}: {sql!r} [{reason}]" for l, sql, reason in result)
        )

    def test_error_reporting_decrement_gated(self):
        """daemon/services/error_reporting.py — waiting_for SQL decrement gated."""
        result = _check_sql_gated(
            DAEMON_ROOT / "services" / "error_reporting.py",
            "services/error_reporting.py",
        )
        assert result == [], (
            "UNGTED waiting_for SQL mutation in services/error_reporting.py:\n"
            + "\n".join(f"  line {l}: {sql!r} [{reason}]" for l, sql, reason in result)
        )

    def test_instance_lifecycle_pause_resume_reset_gated(self):
        """daemon/services/instance_lifecycle.py — pause/resume resets gated.

        terminate_instance's ``waiting_for = 0`` write is a documented
        cache-only cleanup (status flip to terminal) and is allowed.
        """
        result = _check_sql_gated(
            DAEMON_ROOT / "services" / "instance_lifecycle.py",
            "services/instance_lifecycle.py",
        )
        assert result == [], (
            "UNGTED waiting_for SQL mutation in services/instance_lifecycle.py:\n"
            + "\n".join(f"  line {l}: {sql!r} [{reason}]" for l, sql, reason in result)
        )


# =============================================================================
# Category 2 tests — Control-flow read coverage
# =============================================================================


class TestControlFlowReadCoverage:
    """Every waiting_for control-flow read is gated or has a hard-error fallback.

    If a developer adds an ungated cascade decision, CI fails.
    """

    def test_child_reports_cascade_read_gated(self):
        """services/child_reports.py — waiting_for cascade SELECTs are gated."""
        result = _check_sql_gated(
            DAEMON_ROOT / "services" / "child_reports.py",
            "services/child_reports.py",
        )
        # We reuse _check_sql_gated because the SELECTs are inside text() calls
        # and the same gating pattern applies.
        assert result == [], (
            "UNGTED waiting_for SELECT in services/child_reports.py:\n"
            + "\n".join(f"  line {l}: {sql!r} [{reason}]" for l, sql, reason in result)
        )

    def test_job_feedback_observer_deferral_gated(self):
        """services/job_feedback_observer.py — FOR UPDATE gate is gated."""
        result = _check_sql_gated(
            DAEMON_ROOT / "services" / "job_feedback_observer.py",
            "services/job_feedback_observer.py",
        )
        assert result == [], (
            "UNGTED waiting_for SQL in services/job_feedback_observer.py:\n"
            + "\n".join(f"  line {l}: {sql!r} [{reason}]" for l, sql, reason in result)
        )

    @pytest.mark.asyncio
    async def test_hard_error_when_flag_off_and_cm_none(self):
        """When USE_LEGACY_WAITING_FOR_CASCADE=OFF and CM is None, hard error raises.

        This is the A8/A9 guard. The SELECT COUNT(*) fallback path must not
        be reachable — it contains the Race #3 TOCTOU bug being fixed.
        """
        from daemon.services.child_reports import ChildReportsService
        from daemon.write_pause_guard import WritePauseGuard

        # Ensure CM is None for this test.
        set_correlation_manager(None)

        config = MagicMock()
        config.job_system = MagicMock()
        config.job_system.use_legacy_waiting_for_cascade = False  # flag OFF

        manager = MagicMock(name="InstanceManager")
        manager.write_guard = WritePauseGuard()
        # ``_config`` is a property that delegates to ``manager.config`` —
        # set the config on the manager (not on the service directly).
        manager.config = config

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = manager
        service._events_service = MagicMock()
        service._trigger_title_generation = MagicMock()

        # Build a child instance whose parent is still running.
        mock_parent = MagicMock()
        mock_parent.instance_id = "parent-1"
        mock_parent.status = "running"
        mock_parent.version = 1
        mock_parent.waiting_for = 1
        mock_parent.parent_id = "grandparent-1"
        mock_parent.last_activity_at = None

        mock_instance = MagicMock()
        mock_instance.instance_id = "child-1"
        mock_instance.parent_id = "parent-1"
        mock_instance.status = "completed"

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            side_effect=lambda cls, iid: mock_parent if iid == "parent-1" else None
        )
        mock_session.execute = MagicMock(
            return_value=MagicMock(first=MagicMock(return_value=(0,)))
        )
        mock_session.expire = MagicMock()
        mock_session.exec = MagicMock()

        # CM is None AND flag is OFF → RuntimeError at the cascade decision.
        # ``_update_parent_on_child_complete`` is async and runs the cascade
        # check via the synchronous ``get_correlation_manager()`` import.
        with pytest.raises(RuntimeError, match="USE_LEGACY_WAITING_FOR_CASCADE=OFF"):
            await service._update_parent_on_child_complete(
                session=mock_session,
                instance=mock_instance,
                completed_message_id="msg-1",
            )


# =============================================================================
# Category 3 tests — CM consistency & cache exemption
# =============================================================================


def _check_rebuild_not_gated(source: str) -> list[tuple[int, str, str]]:
    """Verify rebuild_from_db() reads waiting_for are NOT gated.

    rebuild_from_db() is the sole crash-recovery mechanism (ADR-011).
    Its waiting_for reads are cache-only and must NOT be gated.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    rebuild_method: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "rebuild_from_db":
            rebuild_method = node
            break

    if rebuild_method is None:
        return []

    results: list[tuple[int, str, str]] = []
    for node in ast.walk(rebuild_method):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if "waiting_for" not in val:
                continue
            sql_indicators = ("UPDATE", "SELECT", "SET ", "RETURNING", "FROM")
            if not any(ind in val for ind in sql_indicators):
                continue
            gate = _find_enclosing_gate(tree, node.lineno)
            if gate is not None:
                snippet = val[:80].replace("\n", " ")
                results.append((node.lineno, snippet, "gated inside rebuild_from_db"))

    return results


class TestCMConsistency:
    """CM is consistent, rebuild is cache-only, and DEBUG flag fires correctly."""

    def test_rebuild_from_db_is_not_gated_cache_only(self):
        """correlation_manager.py — rebuild_from_db() waiting_for reads NOT gated.

        rebuild_from_db() reads ``waiting_for > 0`` to reconstruct CM state
        after restart. These reads are the crash-recovery contract (ADR-011)
        and must NOT be gated — gating them would silently skip parents.
        """
        result = _check_rebuild_not_gated(
            (DAEMON_ROOT / "services" / "correlation_manager.py").read_text()
        )
        assert result == [], (
            "rebuild_from_db() has GATED waiting_for reads — breaks crash recovery:\n"
            + "\n".join(f"  line {l}: {sql!r} [{reason}]" for l, sql, reason in result)
        )

    @pytest.mark.asyncio
    async def test_pending_count_matches_is_complete(self):
        """CM: get_pending_count() == 0 iff is_complete() == True."""
        cm = make_cm()
        parent = "parent-consistency-1"

        # No registrations → pending_count=0 and is_complete=True
        assert cm.get_pending_count(parent) == 0
        assert cm.is_complete(parent) is True

        # Register 3 correlations for the same parent.
        msgs = [str(uuid.uuid4()) for _ in range(3)]
        for msg in msgs:
            await cm.register_message_send(parent, "child-1", msg)

        assert cm.get_pending_count(parent) == 3
        assert cm.is_complete(parent) is False

        # Resolve 2
        for msg in msgs[:2]:
            await cm.resolve_response(parent, "child-1", msg)

        assert cm.get_pending_count(parent) == 1
        assert cm.is_complete(parent) is False

        # Resolve last
        await cm.resolve_response(parent, "child-1", msgs[2])
        assert cm.get_pending_count(parent) == 0
        assert cm.is_complete(parent) is True

    @pytest.mark.asyncio
    async def test_debug_completion_invariant_fires_on_divergence(self, caplog: pytest.LogCaptureFixture):
        """DEBUG_COMPLETION_INVARIANT emits CM_WAITING_FOR_DIVERGENCE warning on mismatch.

        When the DEBUG flag is ON and CM's pending count disagrees with the DB
        snapshot, a structured WARNING is logged with event=CM_WAITING_FOR_DIVERGENCE.
        """
        parent_id = "parent-debug-invariant-1"
        child_id = "child-1"
        msg_id = str(uuid.uuid4())

        # DB says waiting_for=99; CM will have 1 after register → mismatch
        instance_repo = make_instance_repo(
            instance_by_id={
                parent_id: make_instance(parent_id, waiting_for=99)
            }
        )
        cm = make_cm(instance_repo=instance_repo)
        cm._debug_invariant_enabled = True

        caplog.set_level(logging.WARNING)
        await cm.register_message_send(parent_id, child_id, msg_id)

        mismatch_logs = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "CM_WAITING_FOR_DIVERGENCE" in r.message
        ]
        assert len(mismatch_logs) >= 1, (
            "Expected at least 1 CM_WAITING_FOR_DIVERGENCE warning, got "
            f"{len(mismatch_logs)}. DEBUG_COMPLETION_INVARIANT not firing."
        )
        log = mismatch_logs[0]
        assert "cm_pending_count=1" in log.message, f"Missing cm_pending_count: {log.message}"
        assert "db_waiting_for=99" in log.message, f"Missing db_waiting_for: {log.message}"

    @pytest.mark.asyncio
    async def test_debug_completion_invariant_silent_on_match(self, caplog: pytest.LogCaptureFixture):
        """DEBUG_COMPLETION_INVARIANT produces no warning when CM and DB agree."""
        parent_id = "parent-debug-silent-1"
        child_id = "child-1"
        msg_id = str(uuid.uuid4())

        instance_repo = make_instance_repo(
            instance_by_id={
                parent_id: make_instance(parent_id, waiting_for=1)
            }
        )
        cm = make_cm(instance_repo=instance_repo)
        cm._debug_invariant_enabled = True

        caplog.set_level(logging.WARNING)
        await cm.register_message_send(parent_id, child_id, msg_id)

        mismatch_logs = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "CM_WAITING_FOR_DIVERGENCE" in r.message
        ]
        assert mismatch_logs == [], (
            "Expected no divergence warning on match, got: "
            f"{[r.message for r in mismatch_logs]}"
        )

    @pytest.mark.asyncio
    async def test_debug_completion_invariant_off_is_noop(self, caplog: pytest.LogCaptureFixture):
        """When DEBUG_COMPLETION_INVARIANT is OFF, _check_invariant is a no-op."""
        parent_id = "parent-debug-off-1"

        instance_repo = MagicMock(name="should_not_be_called")
        instance_repo.get = MagicMock(
            side_effect=AssertionError(
                "instance_repo.get must NOT be called when invariant is OFF"
            )
        )
        cm = make_cm(instance_repo=instance_repo)
        cm._debug_invariant_enabled = False

        caplog.set_level(logging.WARNING)
        await cm._check_invariant(parent_id)

        mismatch_logs = [
            r for r in caplog.records
            if "CM_WAITING_FOR_DIVERGENCE" in r.message
        ]
        assert mismatch_logs == []
        instance_repo.get.assert_not_called()
