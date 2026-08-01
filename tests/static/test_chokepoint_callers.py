"""Guard the named-transition chokepoints against accidental bypasses.

The Appendix A caller map in ``increment3-plan.md`` is intentionally encoded
as a normalized file/function/method multiset rather than line numbers.  Line
numbers move during ordinary refactors; the owning function and operation are
the stable identity of a caller.  A migrated caller may disappear from this
set, but a new call may not appear without an explicit allowlist review.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
import re
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
DAEMON = ROOT / "daemon"
CHOKEPOINT_METHODS = frozenset({
    "complete_task",
    "cancel_task",
    "fail_task",
    "schedule_retry",
    "force_cancel_and_schedule_retry",
})

# Appendix A.1, normalized to (relative file, enclosing scope, method).  The
# counts are the pre-Phase-4b direct-call budget.  A migration can reduce a
# count, but adding a call requires moving the caller to a named transition or
# deliberately updating this table with the corresponding plan entry.
APPENDIX_A_CALL_COUNTS: Counter[tuple[str, str, str]] = Counter(
    {
        ("daemon/services/worker_pool.py", "Worker._handle_cancellation", "complete_task"): 1,
        ("daemon/services/worker_pool.py", "Worker._handle_cancellation", "cancel_task"): 1,
        ("daemon/services/worker_pool.py", "Worker._handle_cancellation", "fail_task"): 1,
        ("daemon/services/worker_pool.py", "Worker._handle_cancellation", "schedule_retry"): 1,
        ("daemon/services/worker_pool.py", "Worker._handle_task_failure", "fail_task"): 1,
        ("daemon/services/stale_task_recovery.py", "StaleTaskRecovery.force_complete_task", "complete_task"): 1,
        ("daemon/services/stale_task_recovery.py", "StaleTaskRecovery.fail_task", "fail_task"): 1,
        ("daemon/services/stale_task_recovery.py", "StaleTaskRecovery.recover_stale_tasks", "fail_task"): 2,
        ("daemon/services/stale_task_recovery.py", "StaleTaskRecovery.recover_stale_tasks", "force_cancel_and_schedule_retry"): 1,
        ("daemon/services/stale_task_recovery.py", "StaleTaskRecovery.recover_stale_tasks", "schedule_retry"): 1,
        ("daemon/services/stale_task_recovery.py", "StaleTaskRecovery.recover_on_startup", "fail_task"): 2,
        ("daemon/services/stale_task_recovery.py", "StaleTaskRecovery.recover_on_startup", "force_cancel_and_schedule_retry"): 1,
        ("daemon/services/stale_task_recovery.py", "StaleTaskRecovery.recover_on_startup", "schedule_retry"): 1,
        ("daemon/services/task_processor.py", "ProcessMessageProcessor._skip_task_as_completed", "complete_task"): 1,
        ("daemon/services/task_processor.py", "ProcessMessageProcessor._build_callbacks.on_success", "complete_task"): 1,
        ("daemon/services/job_queue_service.py", "JobQueueService.cancel_task_by_work_id", "cancel_task"): 1,
        # NOTE: Inc 4 (cced02cc, 2026-08-01) moved the cancel_task call out
        # of resume_processing_job into the shared helper
        # _schedule_explicit_handle_resume (the antiphantom-race guard
        # is identical — same cancel reason, same task, same logical
        # caller). Direct caller identity is now the helper, not the
        # public resume_processing_job entry point. Same caller
        # semantically; documented in the helper's docstring.
        ("daemon/manager.py", "InstanceManager._schedule_explicit_handle_resume", "cancel_task"): 1,
        ("daemon/manager.py", "InstanceManager._resume_processing_background", "fail_task"): 1,
    }
)

# Publicly named alias matching the plan's Appendix A terminology.  The
# counter above additionally lets the check reject a second call in an already
# allowed function.
ALLOWED_DIRECT_CALLERS = frozenset(APPENDIX_A_CALL_COUNTS)

# During the phased migration, the repository chokepoint bodies and the named
# transition implementations are the sanctioned status-write locations.  The
# repository contains other pre-existing status operations (claim/requeue), so
# it is deliberately exempted here exactly as §8.8 specifies.
STATUS_SQL_ALLOWLIST = frozenset(
    {
        "daemon/services/turn_transitions.py",
        "daemon/repositories/task/repository.py",
    }
)
_DIRECT_STATUS_UPDATE = re.compile(
    r"\bUPDATE\s+task\s+SET\b(?:(?!\bWHERE\b)[\s\S]){0,1000}?\bstatus\s*=",
    re.IGNORECASE,
)


def _scope_map(tree: ast.AST) -> dict[ast.AST, str]:
    """Return stable ``Class.method`` scopes for AST nodes."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    scopes: dict[ast.AST, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Call, ast.Attribute, ast.Name, ast.Constant, ast.JoinedStr, ast.BinOp)):
            continue
        current: ast.AST | None = node
        names: list[str] = []
        while current in parents:
            current = parents[current]
            if isinstance(current, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(current.name)
        scopes[node] = ".".join(reversed(names)) or "<module>"
    return scopes


def _iter_python_files() -> Iterable[Path]:
    yield from sorted(DAEMON.rglob("*.py"))


def _caller_records() -> list[tuple[str, str, str, int]]:
    """Collect direct chokepoint attribute/name references from ``daemon``."""
    records: list[tuple[str, str, str, int]] = []
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scopes = _scope_map(tree)
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            method: str | None = None
            if isinstance(node, ast.Attribute):
                method = node.attr
            elif isinstance(node, ast.Name) and node.id in CHOKEPOINT_METHODS:
                method = node.id
            if method not in CHOKEPOINT_METHODS:
                continue
            # Only executable references count.  A method name appearing in a
            # docstring or comment never becomes an AST Attribute/Name node.
            records.append((relative, scopes.get(node, "<module>"), method, node.lineno))
    return records


def _literal_text(node: ast.AST) -> str | None:
    """Best-effort extraction of SQL from literal/concatenated AST nodes."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return None
            parts.append(value.value)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_text(node.left)
        right = _literal_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _direct_status_updates() -> list[tuple[str, int, str]]:
    violations: list[tuple[str, int, str]] = []
    for path in _iter_python_files():
        relative = path.relative_to(ROOT).as_posix()
        if relative in STATUS_SQL_ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scopes = _scope_map(tree)
        for node in ast.walk(tree):
            text = _literal_text(node)
            if text is None or not _DIRECT_STATUS_UPDATE.search(text):
                continue
            violations.append((relative, node.lineno, scopes.get(node, "<module>")))
    return violations


def test_no_new_direct_chokepoint_callers() -> None:
    """Every direct lifecycle-method reference stays within Appendix A."""
    observed = Counter(
        (path, scope, method)
        for path, scope, method, _line in _caller_records()
    )
    unexpected = []
    over_budget = []
    for key, count in observed.items():
        if key not in APPENDIX_A_CALL_COUNTS:
            unexpected.append((key, count))
        elif count > APPENDIX_A_CALL_COUNTS[key]:
            over_budget.append((key, count, APPENDIX_A_CALL_COUNTS[key]))

    assert not unexpected and not over_budget, (
        "New direct caller(s) of complete_task/cancel_task/fail_task/"
        "schedule_retry/force_cancel_and_schedule_retry detected. "
        "Migrate to a named transition or add a justified Appendix A entry: "
        f"unexpected={unexpected!r}, over_budget={over_budget!r}"
    )


def test_no_direct_sql_on_task_status_outside_transitions() -> None:
    """Hand-written ``UPDATE task SET status=`` bypasses are forbidden."""
    violations = _direct_status_updates()
    assert not violations, (
        "Direct UPDATE task SET status= found outside the transition/repository "
        f"allowlist: {violations!r}"
    )
