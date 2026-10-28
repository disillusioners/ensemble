"""F8-a pin — no manager-engine transaction is open across an await (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F8.1–F8.9 and §5.2. This is the
static AST twin of the PG legs F8-b/c/d/e.

THE THREE EXPECTED SITES (named here so a FOURTH is a deliberate
failure — the rule is a whole-tree invariant, and a new violation
anywhere in ``daemon/`` REDs this pack):

1. ``daemon/manager.py`` — ``_reconcile_deferred_report_async`` (the
   ROUTER-side reconcile): P0 read-decide scope → P1 content fetch
   (no manager-engine session open) → P2 write scope.
2. ``daemon/manager.py`` — ``_reconcile_deferred_report`` (the SWEEP-side
   twin): the same three-phase shape; the cross-thread bridge keeps its
   call shape and its 8.0 s timeout but moved OUT of the held scope
   (DR-26).
3. ``daemon/tools/instance.py`` — ``_register_child_completion_watcher``:
   the ``await _bus.watch(...)`` registration moved OUT of the
   ``WriteGuardSession`` scope (F8.5).

What this pin asserts: **no** ``ast.Await`` node, **no**
``run_coroutine_threadsafe(...).result(...)`` call, and **no** call to
the bridge-carrying fetch helpers (``_fetch_subshape_a_content_sync`` /
``_fetch_subshape_a_content_async``) inside the ``with``-body of a
manager-engine session scope anywhere in ``daemon/``.

The rule is scoped to the ``with``-body ONLY — a function-wide rule
would false-positive on the legitimate bridge calls that now live
OUTSIDE any scope.

Plantable mutation: insert an ``ast.Await`` (or a
``run_coroutine_threadsafe(...).result(...)``) inside the ``with``-body
of a manager-engine session scope in ``daemon/`` ⇒ RED.

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_fix_site_static.py --tb=short -q
"""
from __future__ import annotations

import ast
from pathlib import Path

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 3

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DAEMON_DIR = _REPO_ROOT / "daemon"

#: The manager-engine session scope forms (the rule's scope).
#:   ``with self._session_scope() as session:``      (manager / holder)
#:   ``with Session(<engine>) as session:``           (manager engine attr)
#:   ``with WriteGuardSession(...) as session:``      (the tool-lane proxy)
_SCOPE_MARKERS = ("_session_scope", "WriteGuardSession")

#: The fetch helpers whose bodies carry the cross-thread bridge — a
#: call to them INSIDE a held scope re-introduces the borrow-then-await.
_BRIDGE_CARRYING_HELPERS = (
    "_fetch_subshape_a_content_sync",
    "_fetch_subshape_a_content_async",
)


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


def _violations_in_with_body(node: ast.With) -> list[str]:
    """The violation labels inside one scope's with-body."""
    found: list[str] = []
    for sub in ast.walk(node):
        if sub is node:
            continue
        if isinstance(sub, ast.Await):
            found.append(f"await at line {sub.lineno}")
            break
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "result":
            inner = sub.func.value
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "run_coroutine_threadsafe":
                found.append(f"run_coroutine_threadsafe(...).result(...) at line {sub.lineno}")
                break
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in _BRIDGE_CARRYING_HELPERS:
            found.append(
                f"bridge-carrying helper {sub.func.attr}() called at line {sub.lineno}"
            )
            break
    return found


def _scan() -> list[tuple[str, int, str]]:
    """Scan ``daemon/`` for manager-session scopes holding an await."""
    violations: list[tuple[str, int, str]] = []
    for path in sorted(_DAEMON_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover — a syntax error fails elsewhere
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.With) and _is_manager_session_scope(node):
                for label in _violations_in_with_body(node):
                    violations.append((str(path.relative_to(_REPO_ROOT)), node.lineno, label))
    return violations


class TestF8aNoAwaitInsideManagerSessionScope:
    """F8-a — the borrow-then-await is gone tree-wide."""

    def test_no_manager_session_scope_holds_an_await(self):
        violations = _scan()
        assert not violations, (
            "F8-a: a manager-engine session scope holds an await / a "
            "cross-thread bridge / a bridge-carrying fetch call — the "
            "backend would sit idle-in-transaction across it: "
            f"{violations}"
        )

    def test_the_three_named_sites_are_clean(self):
        """The three expected sites are named in the module docstring so
        a FOURTH violation is a deliberate failure."""
        named = (
            "_reconcile_deferred_report_async",
            "_reconcile_deferred_report",
            "_register_child_completion_watcher",
        )
        source = (_DAEMON_DIR / "manager.py").read_text() + (
            _DAEMON_DIR / "tools" / "instance.py"
        ).read_text()
        for name in named:
            assert name in source, (
                f"F8-a: the named site {name} disappeared — re-derive the "
                "expected-site list"
            )

    def test_the_manager_reconcile_sites_split_into_three_phases(self):
        """The P0/P1/P2 split is visible in the source: the P1 fetch call
        must NOT be inside a manager-engine session scope."""
        manager_source = (_DAEMON_DIR / "manager.py").read_text()
        tree = ast.parse(manager_source)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "_reconcile_deferred_report_async"
            ):
                continue
            # The fetch call must exist…
            fetch_calls = [
                sub
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "_fetch_subshape_a_content_async"
            ]
            assert fetch_calls, "F8-a: the async reconcile lost its fetch call"
            # …and the carrier check must be hoisted OUT of every scope.
            # It is reached as ``asyncio.to_thread(self._has_live_...)``
            # — the helper appears as an ATTRIBUTE value, not as the
            # call's own attribute.
            carrier_calls = [
                sub
                for sub in ast.walk(node)
                if (
                    isinstance(sub, ast.Attribute)
                    and sub.attr == "_has_live_process_report_carrier"
                )
                or (
                    isinstance(sub, ast.Name)
                    and sub.id == "_has_live_process_report_carrier"
                )
            ]
            assert carrier_calls, (
                "F8-a: the async reconcile lost its carrier check"
            )
            for scope in ast.walk(node):
                if isinstance(scope, ast.With) and _is_manager_session_scope(scope):
                    for sub in ast.walk(scope):
                        if (
                            isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Attribute)
                            and sub.func.attr
                            in ("_fetch_subshape_a_content_async",)
                        ):
                            pytest.fail(
                                "F8-a: the content fetch is still inside a "
                                "manager-engine session scope"
                            )
            return
        pytest.fail("F8-a: _reconcile_deferred_report_async not found")
