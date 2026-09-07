"""AST/grep-style caller guard for
:func:`daemon.repositories.job_queue._idle_predicate_sql.defer_busy_witness_statement`.

Unblock-round ITEM 7 (2026-09-06, ``fix/defer-self-witness-and-cleanup``)
— the two carve-out witness body constants
(:data:`JOB_DEFER_BUSY_WITNESS_BODY_PROJECT_WITH_CARVEOUT` and
:data:`JOB_DEFER_BUSY_WITNESS_BODY_SYSTEM_WITH_CARVEOUT`) stay
RESERVED for future per-candidate witness enumeration (WS4
mission-aware cleanup); no production caller of
``defer_busy_witness_statement(requester_instance_id=...)`` exists
today. The ``defer_block_resolver`` always passes
``requester_instance_id=None``.

This test pins that contract via a static AST scan of every
``.py`` file under ``daemon/`` (excluding the predicate module
itself and excluding test directories): a production caller that
passes a non-None ``requester_instance_id`` (positional or keyword)
breaks this pin with the exact offending file:line traceback.

Plain Python ``ast`` only — no DB, no fixture, no AST cache.

Run with::

    timeout 60 .venv/bin/pytest tests/unit/test_defer_busy_witness_caller_pin.py \\
        -v --tb=short -q --override-ini="addopts="
"""

from __future__ import annotations

import ast
from pathlib import Path

# Project-root-relative daemon tree.
DAEMON_ROOT: Path = Path(__file__).resolve().parents[2] / "daemon"

# Files excluded from the scan:
#   * the predicate module itself (it DEFINES the function — scanning
#     it would trip on the signature);
#   * ``__pycache__`` and ``conftest.py`` (test infra).
EXCLUDE_PATHS: frozenset[Path] = frozenset(
    {
        DAEMON_ROOT / "repositories" / "job_queue" / "_idle_predicate_sql.py",
    }
)


def _walk_daemon_py_files() -> list[Path]:
    """Return every ``.py`` file under ``daemon/`` (excluding the
    predicate module)."""
    files: list[Path] = []
    for path in DAEMON_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path in EXCLUDE_PATHS:
            continue
        files.append(path)
    return sorted(files)


def _is_call_to_defer_busy_witness(node: ast.Call) -> bool:
    """Return True if ``node`` is a call whose function is
    ``defer_busy_witness_statement`` (any module reference)."""
    func = node.func
    # defer_busy_witness_statement(...)
    if isinstance(func, ast.Name) and func.id == "defer_busy_witness_statement":
        return True
    # <module>.defer_busy_witness_statement(...) — most production callers
    # reach through ``_idle_predicate_sql.defer_busy_witness_statement``.
    if isinstance(func, ast.Attribute) and func.attr == "defer_busy_witness_statement":
        return True
    return False


def _requester_argument(node: ast.Call) -> tuple[int | None, ast.AST | None]:
    """Return ``(positional_index_for_requester, kwarg_node_or_None)`` for
    a call to ``defer_busy_witness_statement``.

    The signature is
    ``defer_busy_witness_statement(project_id, requester_instance_id=None)``,
    so ``requester_instance_id`` is the SECOND positional arg. The kwarg
    name is matched verbatim.

    Returns ``(None, None)`` when the argument is omitted (default
    ``None``).
    """
    kwarg: ast.AST | None = None
    for kw in node.keywords:
        if kw.arg == "requester_instance_id":
            kwarg = kw.value
            return (1, kwarg)
    # No keyword; fall back to positional. ``project_id`` is the first
    # positional, ``requester_instance_id`` is the second. If only one
    # positional is supplied the second defaults to ``None``.
    if len(node.args) >= 2:
        return (1, node.args[1])
    return (None, None)


def _is_none_literal(node: ast.AST | None) -> bool:
    """Return True when ``node`` is the literal ``None`` (the
    carve-out-DISABLED shape — the only allowed production shape)."""
    if node is None:
        return False
    return isinstance(node, ast.Constant) and node.value is None


def test_production_callers_pass_requester_none() -> None:
    """Every production caller of ``defer_busy_witness_statement``
    MUST pass ``requester_instance_id=None`` (or omit it, defaulting
    to ``None``).

    The carve-out witness body constants stay RESERVED for WS4+
    per-candidate enumeration; today's only shape is the system-wide
    no-carve-out body. A production caller passing a non-None
    ``requester_instance_id`` would activate the reserved body
    silently — the failure mode this pin exists to catch.

    Offenders are reported via ``offending_calls`` with file:line +
    snippet for greppability.
    """
    offending_calls: list[str] = []
    for py_file in _walk_daemon_py_files():
        try:
            source = py_file.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            # Unparseable (e.g. WIP module); don't trip the pin.
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_call_to_defer_busy_witness(node):
                continue
            _, req_arg = _requester_argument(node)
            if _is_none_literal(req_arg):
                continue
            # Offender: either omitted-but-not-None (default None → ok)
            # OR keyword/positional with a non-None literal/value.
            if req_arg is None:
                # No argument passed → uses the default ``None``. This
                # is the canonical shape (the carve-out stays off).
                continue
            # Non-None: this is the bug class.
            snippet = ast.unparse(req_arg)
            offending_calls.append(
                f"{py_file.relative_to(DAEMON_ROOT.parent)}:{node.lineno} — "
                f"defer_busy_witness_statement(requester_instance_id={snippet!r})"
            )

    assert offending_calls == [], (
        "Production callers of defer_busy_witness_statement must pass "
        "requester_instance_id=None (or omit it). Offending call sites:\n"
        + "\n".join(f"  * {line}" for line in offending_calls)
    )
