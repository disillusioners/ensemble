"""Wiring pin for the wedge-capture watchdog BOOT seam (not the class).

WHY THIS PACK EXISTS
--------------------
The 2026-10-23 ship-blocker: ``daemon/api.py`` constructed the watchdog
with ``release_version=getattr(daemon, "__version__", "")`` — but the
name ``daemon`` is NEVER bound in ``api.py`` module scope (the import at
the top is ``from daemon import __version__``). The NameError fired at
constructor-kwarg evaluation, was swallowed by the surrounding
``except Exception``, and left ``app.state.wedge_capture_watchdog =
None`` on EVERY enabled production boot — while every existing pack
stayed green, because they drive the watchdog CLASS directly with their
own correct kwargs (a mock-replay of the seam is exactly the shape that
was blind).

So this pin exercises the ACTUAL call site — the real source text of
``daemon/api.py`` — three ways, none of which replays the call:

* **(1) static resolution** — every name loaded inside every constructor
  kwarg must resolve to a module-scope binding, the enclosing
  function's locals, or a builtin. An unbound name is the blocker's
  exact failure class (``daemon`` was none of the three).
* **(2) dynamic value** — the ``release_version`` kwarg expression is
  ``eval``-uated in the REAL ``daemon.api`` module namespace and must
  yield the live ``daemon.__version__`` string.
* **(3) wiring shape** — the call must wire ``app_state=app.state`` and
  the same enclosing function must assign
  ``app.state.wedge_capture_watchdog`` (the seam Lane B's boot-alive
  gate reads).

SAFETY FENCE: repo-relative source reads only — no HOME-derived or
live-shaped path is ever resolved.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_PATH = REPO_ROOT / "daemon" / "api.py"

#: The constructor the boot seam builds.
WATCHDOG_CLASS = "WedgeCaptureWatchdog"
#: The app.state attribute the whole wiring exists to populate.
STATE_ATTR = "wedge_capture_watchdog"


# ── source helpers ──────────────────────────────────────────────────────


def _api_source() -> str:
    return API_PATH.read_text(encoding="utf-8")


def _bound_names(node: ast.AST) -> set[str]:
    """Names BOUND in ``node``'s own scope (never descending into a
    nested function/class/lambda, which carries its own scope).

    ``node`` itself IS the scope being collected (its body statements
    are direct children); every scope-typed CHILD binds only its name.
    Deliberately conservative in the GREEN direction only: a name this
    misses makes the pin RED (a false alarm), never silently green.
    """
    bound: set[str] = set()
    scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

    def visit(current: ast.AST) -> None:
        for child in ast.iter_child_nodes(current):
            if isinstance(child, scopes):
                # a nested def/class/lambda BINDS its name in this
                # scope, but its body is its own scope — do not descend
                bound.add(getattr(child, "name", "") or "")
                continue
            if isinstance(child, ast.Name) and isinstance(
                child.ctx, (ast.Store, ast.Del)
            ):
                bound.add(child.id)
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    bound.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(child, ast.ImportFrom):
                for alias in child.names:
                    bound.add(alias.asname or alias.name)
            elif isinstance(child, ast.arguments):
                for arg in (
                    *child.posonlyargs,
                    *child.args,
                    child.vararg,
                    *child.kwonlyargs,
                    child.kwarg,
                ):
                    if arg is not None:
                        bound.add(arg.arg)
            visit(child)

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # the root function's own parameters bind in its scope
        for arg in (
            *node.args.posonlyargs,
            *node.args.args,
            node.args.vararg,
            *node.args.kwonlyargs,
            node.args.kwarg,
        ):
            if arg is not None:
                bound.add(arg.arg)
    visit(node)
    return {name for name in bound if name}


def _module_scope_bindings(tree: ast.Module) -> set[str]:
    """Every name the ``daemon/api.py`` MODULE scope binds."""
    return _bound_names(tree)


def _watchdog_call(tree: ast.Module) -> ast.Call:
    """The ``WedgeCaptureWatchdog(...)`` call node in ``daemon/api.py``."""
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == WATCHDOG_CLASS
    ]
    assert calls, (
        f"no {WATCHDOG_CLASS}(...) construction found in daemon/api.py — "
        "the boot seam was removed or renamed; re-anchor this pin"
    )
    return calls[0]


def _enclosing_function(tree: ast.Module, call: ast.Call) -> ast.AST:
    """The function node that lexically encloses the watchdog call."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            candidate is call for candidate in ast.walk(node)
        ):
            # the DEEPEST enclosing function wins
            for inner in ast.walk(node):
                if (
                    isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and inner is not node
                    and any(candidate is call for candidate in ast.walk(inner))
                ):
                    node = inner
            return node
    pytest.fail("the watchdog construction is not inside any function")


# ── (1) static resolution — the blocker's exact failure class ──────────


def test_every_watchdog_kwarg_name_resolves_in_api_scope() -> None:
    """The blocker: ``getattr(daemon, "__version__", "")`` loaded the name
    ``daemon``, which is bound NOWHERE in daemon/api.py — a NameError at
    kwarg evaluation, swallowed by the boot guard. Every name loaded in
    every kwarg must be module-bound, function-local, or a builtin."""
    tree = ast.parse(_api_source())
    call = _watchdog_call(tree)
    module_bound = _module_scope_bindings(tree)
    func = _enclosing_function(tree, call)
    local_bound = _bound_names(func)
    builtins_ = set(dir(builtins))

    failures: list[str] = []
    for kwarg in call.keywords:
        for node in ast.walk(kwarg.value):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in module_bound | local_bound | builtins_:
                    failures.append(
                        f"kwarg '{kwarg.arg}' loads name {node.id!r} which is "
                        "bound NOWHERE in daemon/api.py (not module scope, "
                        "not a local of the enclosing function, not a "
                        "builtin) — NameError at kwarg evaluation"
                    )
    assert not failures, (
        "the watchdog boot seam is dead-on-arrival:\n" + "\n".join(failures)
    )


# ── (2) dynamic value — the real namespace, the real token ─────────────


def test_release_version_kwarg_evaluates_to_the_live_version() -> None:
    """``release_version`` must evaluate — in the REAL ``daemon.api``
    module namespace — to the live ``daemon.__version__`` string."""
    tree = ast.parse(_api_source())
    call = _watchdog_call(tree)
    release_kwargs = [
        kwarg for kwarg in call.keywords if kwarg.arg == "release_version"
    ]
    assert release_kwargs, (
        "the watchdog construction carries no release_version kwarg — the "
        "manifest's release attribution seam was removed"
    )

    import daemon
    import daemon.api as api_module  # the namespace the kwarg evaluates in

    namespace = vars(api_module).copy()
    expression = ast.Expression(body=release_kwargs[0].value)
    code = compile(
        expression,
        filename="daemon/api.py:<watchdog release_version kwarg>",
        mode="eval",
    )
    try:
        value = eval(code, namespace)  # noqa: S307 — the pin's whole point
    except NameError as exc:
        pytest.fail(
            "release_version kwarg raises NameError in daemon/api.py module "
            f"scope — the boot seam is dead-on-arrival: {exc}"
        )
    assert isinstance(value, str), (
        f"release_version must be a str, got {type(value)!r}"
    )
    assert value == daemon.__version__, (
        f"release_version ({value!r}) != daemon.__version__ "
        f"({daemon.__version__!r})"
    )
    assert value, "release_version must be non-empty"


# ── (3) wiring shape — app.state is the observable seam ────────────────


def test_the_call_wires_app_state_and_the_state_attribute_is_assigned() -> None:
    """The construction must pass ``app_state=app.state`` and the SAME
    enclosing function must assign ``app.state.<STATE_ATTR>`` — the
    attribute Lane B's boot-alive gate (wedge_smoke.assert_watchdog_
    boot_alive) reads after a real boot."""
    tree = ast.parse(_api_source())
    call = _watchdog_call(tree)

    app_state_kwargs = [
        kwarg for kwarg in call.keywords if kwarg.arg == "app_state"
    ]
    assert app_state_kwargs, "the call must wire app_state=..."
    unparsed = ast.unparse(app_state_kwargs[0].value)
    assert unparsed.endswith("state"), (
        f"app_state must be wired to app.state, got {unparsed!r}"
    )

    func = _enclosing_function(tree, call)
    assignments = [
        ast.unparse(node)
        for node in ast.walk(func)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Attribute)
        and node.targets[0].attr == STATE_ATTR
        and isinstance(node.targets[0].value, ast.Attribute)
        and node.targets[0].value.attr == "state"
    ]
    assert assignments, (
        f"the enclosing function never assigns app.state.{STATE_ATTR} — "
        "the boot-alive seam has no observable target"
    )
    # one TRUE assignment from the construction, plus the swallow-path
    # None reset(s) of the never-wedge guard
    assert any(
        assignment != f"app.state.{STATE_ATTR} = None" for assignment in assignments
    ), f"only the None swallow-path assigns app.state.{STATE_ATTR}: {assignments}"
