"""F7-e-static pin — the occupancy twin (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §5.2 (F7-e-static row) and §1 F8.6 /
F8.7 / F8.8; decisions D-24/D-48. Default-collected static twin of
the PG pin F7-e (``tests/integration/test_dbpool_churn_census.py``)
— deleting or un-marking that PG pack leaves the DEFAULT run RED
through this twin.

What this pin asserts:

1. **No borrow-then-await** — no ``ast.Await`` node and no
   ``run_coroutine_threadsafe(...).result(...)`` call inside the
   ``with``-body of a manager-engine session scope anywhere in
   ``daemon/`` (F8-a's shape). The THREE expected sites are named in
   the F8-a pack's docstring
   (``tests/unit/tools/test_dbpool_fix_site_static.py``); a FOURTH
   violation is a deliberate failure of this pack too.
2. **The guards are re-asserted inside the P2 write scope** (F8.6) —
   the terminal guard (``INJECTED`` / ``TASK_DELIVERED``) AND the
   dead-parent guard (parent ``TERMINATED``) are re-evaluated in P2
   against the RE-READ row, so a concurrent escalation between P0
   and P2 cannot double-deliver.
3. **``inj`` is re-read in P2** (F8.7) — the P0 row is detached after
   its scope closes; the write path re-reads the injection row.
4. **The writes commit in ONE transaction** (F8.8) — the message
   INSERT + task INSERT + injection UPDATE of
   ``_create_subshape_a_artifacts`` are followed by a SINGLE
   ``session.commit()`` with no intermediate commit between them.

Plantable mutations: re-introduce an ``await`` inside the
``with``-body of a manager-engine session scope; drop a P2 guard
re-assertion; drop the P2 re-read; split the single-transaction
commit ⇒ RED.

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_occupancy_static.py --tb=short -q
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 4

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DAEMON_DIR = _REPO_ROOT / "daemon"
_MANAGER_PATH = _DAEMON_DIR / "manager.py"

#: The manager-engine session scope forms (F8-a's scope rule).
_SCOPE_MARKERS = ("_session_scope", "WriteGuardSession")


def _is_manager_session_scope(node: ast.With) -> bool:
    for item in node.items:
        expr = item.context_expr
        if isinstance(expr, ast.Call):
            func = expr.func
            if isinstance(func, ast.Attribute) and func.attr in _SCOPE_MARKERS:
                return True
            if isinstance(func, ast.Name) and func.id in _SCOPE_MARKERS:
                return True
            if isinstance(func, ast.Name) and func.id == "Session":
                for arg in expr.args:
                    if isinstance(arg, ast.Attribute) and arg.attr == "engine":
                        return True
    return False


def _await_violations() -> list[str]:
    """await / run_coroutine_threadsafe(...).result(...) inside a
    manager-engine session scope's with-body, tree-wide."""
    violations: list[str] = []
    for path in sorted(_DAEMON_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover — fails elsewhere loudly
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.With) and _is_manager_session_scope(node):
                for sub in ast.walk(node):
                    if sub is node:
                        continue
                    if isinstance(sub, ast.Await):
                        violations.append(
                            f"{path.relative_to(_REPO_ROOT)}:{sub.lineno} "
                            "(await inside a manager-engine session scope)"
                        )
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "result"
                        and isinstance(sub.func.value, ast.Call)
                        and isinstance(sub.func.value.func, ast.Name)
                        and sub.func.value.func.id == "run_coroutine_threadsafe"
                    ):
                        violations.append(
                            f"{path.relative_to(_REPO_ROOT)}:{sub.lineno} "
                            "(run_coroutine_threadsafe(...).result(...) "
                            "inside a manager-engine session scope)"
                        )
    return violations


def _manager_function_source(name: str) -> str:
    """The source of one manager method (async or sync)."""
    src = _MANAGER_PATH.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return ast.get_source_segment(src, node) or ""
    return ""


def _p2_region(reconcile_source: str) -> str:
    """The P2 write-scope region of the async reconcile's source."""
    marker = "── P2: write scope ──"
    at = reconcile_source.find(marker)
    assert at != -1, (
        "F7-e-static: the P2 write-scope marker was not found in "
        "_reconcile_deferred_report_async — the F8 scope-split shape "
        "has drifted"
    )
    return reconcile_source[at:]


class TestF7eStaticOccupancyTwin:
    """F7-e-static — the occupancy invariant's static facts."""

    def test_no_borrow_then_await_anywhere_in_daemon(self):
        violations = _await_violations()
        assert not violations, (
            "F7-e-static/F8-a shape: manager-engine session scopes "
            "holding an await or a cross-thread bridge call (the "
            "borrow-then-await is back): "
            f"{violations}"
        )

    def test_the_guards_are_re_asserted_inside_p2(self):
        source = _manager_function_source("_reconcile_deferred_report_async")
        assert source, (
            "F7-e-static: _reconcile_deferred_report_async not found"
        )
        p2 = _p2_region(source)
        # F8.6 — terminal guard re-asserted in P2 against the re-read
        # row (a concurrent escalation P0→P2 must not double-deliver).
        assert re.search(
            r"inj\.state\s+in\s*\(\s*ReportInjectionState\.INJECTED", p2
        ), (
            "F7-e-static/F8.6: the terminal guard (INJECTED / "
            "TASK_DELIVERED) is NOT re-asserted inside the P2 write "
            "scope — a concurrent escalation slips through ⇒ double "
            "delivery"
        )
        # F8.6 — dead-parent guard re-asserted in P2.
        assert "TERMINATED" in p2 and "parent_row" in p2, (
            "F7-e-static/F8.6: the dead-parent guard (parent is None "
            "or TERMINATED) is NOT re-asserted inside the P2 write "
            "scope"
        )

    def test_inj_is_reread_in_p2(self):
        source = _manager_function_source("_reconcile_deferred_report_async")
        assert source, (
            "F7-e-static: _reconcile_deferred_report_async not found"
        )
        p2 = _p2_region(source)
        assert re.search(
            r"inj\s*=\s*session\.get\(\s*ReportInjection\s*,", p2
        ), (
            "F7-e-static/F8.7: the injection row is NOT re-read inside "
            "the P2 write scope — the P0 row is detached after its "
            "scope closed; the write path must use a fresh row"
        )

    def test_the_three_artefacts_commit_in_one_transaction(self):
        source = _manager_function_source("_create_subshape_a_artifacts")
        assert source, (
            "F7-e-static: _create_subshape_a_artifacts not found"
        )
        # F8.8 — message INSERT + task INSERT + injection UPDATE, then
        # a SINGLE commit. No intermediate commit between the writes
        # (the change is decision↔write, never write↔write).
        commits = re.findall(r"^\s*session\.commit\(\)\s*$", source, re.M)
        assert len(commits) == 1, (
            "F7-e-static/F8.8: _create_subshape_a_artifacts carries "
            f"{len(commits)} session.commit() calls — the message + "
            "task + injection writes must commit in ONE transaction"
        )
        commit_at = source.find("session.commit()")
        message_add = re.search(r"session\.add\(\s*MessageQueue\(", source)
        task_add = re.search(r"session\.add\(\s*Task\(", source)
        injection_update = re.search(
            r"sa_update\(\s*ReportInjection\s*\)", source
        )
        assert message_add and task_add and injection_update, (
            "F7-e-static/F8.8: the message INSERT / task INSERT / "
            "injection UPDATE trio is incomplete in "
            "_create_subshape_a_artifacts"
        )
        assert (
            message_add.start() < commit_at
            and task_add.start() < commit_at
            and injection_update.start() < commit_at
        ), (
            "F7-e-static/F8.8: a write lands AFTER the single commit — "
            "the three artefacts must all precede it (one transaction)"
        )
