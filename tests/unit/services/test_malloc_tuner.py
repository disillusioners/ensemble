"""Unit tests for the idle-time glibc heap trim (resource-cost-control O1,
optional arm).

Pins the three wiring surfaces of the ``malloc_trim`` maintenance job:

1. ``daemon/services/malloc_tuner.py::trim_malloc_heap`` — the
   never-raises contract: missing libc handle, missing ``malloc_trim``
   symbol (musl/macOS), and a raising call all collapse to ``False``;
   a working glibc returns a bool. This is the property the host loop
   depends on (``MaintenanceService`` ticks must never die on it).
2. ``daemon/manager.py`` registers the job on the EXISTING
   ``MaintenanceService`` loop (AST wiring pin — no daemon boot):
   name ``malloc_trim``, hourly interval, ``self._run_malloc_trim``
   callback, and the callback wraps the call in ``try/except`` (host
   loop must be unkillable even if the helper's own contract is ever
   violated).
3. ``daemon/__main__.py`` carries the module-level
   ``MALLOC_ARENA_MAX`` setdefault — the child-inheritance /
   bypass-paths belt-and-braces surface (frozen run_app.py entry and
   ``python -m daemon``), placed BEFORE ``def main`` so it provably
   runs at import, ahead of uvicorn/threads/any child spawn.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from daemon.services.malloc_tuner import trim_malloc_heap

REPO_ROOT = Path(__file__).resolve().parents[3]
MANAGER = REPO_ROOT / "daemon" / "manager.py"
MAIN_ENTRY = REPO_ROOT / "daemon" / "__main__.py"


# ── 1. trim_malloc_heap: never-raises contract ──────────────────────────────


def test_returns_bool_on_this_platform() -> None:
    # Value is host-dependent (glibc → True/False; musl/macOS → False);
    # the CONTRACT is bool + no exception.
    result = trim_malloc_heap()
    assert isinstance(result, bool)


def test_missing_libc_handle_collapses_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import daemon.services.malloc_tuner as mt

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("no libc handle")

    monkeypatch.setattr(mt.ctypes, "CDLL", _boom)
    assert trim_malloc_heap() is False


def test_missing_malloc_trim_symbol_collapses_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # musl / macOS shape: CDLL loads fine but the symbol does not exist.
    import daemon.services.malloc_tuner as mt

    class _LibcWithoutTrim:
        def __getattr__(self, name: str):
            raise AttributeError(name)

    monkeypatch.setattr(mt.ctypes, "CDLL", lambda *_a, **_k: _LibcWithoutTrim())
    assert trim_malloc_heap() is False


def test_raising_trim_access_collapses_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import daemon.services.malloc_tuner as mt

    class _Libc:
        @property
        def malloc_trim(self):  # noqa: ANN202 — test stub
            raise RuntimeError("trim access blew up")

    monkeypatch.setattr(mt.ctypes, "CDLL", lambda *_a, **_k: _Libc())
    assert trim_malloc_heap() is False


def test_repeated_calls_never_raise() -> None:
    for _ in range(3):
        assert isinstance(trim_malloc_heap(), bool)


# ── 2. manager wiring pin (AST — no daemon boot) ────────────────────────────


def _manager_tree() -> ast.Module:
    return ast.parse(MANAGER.read_text(encoding="utf-8"))


def _malloc_trim_register_calls(tree: ast.Module) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "register"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "_maintenance_service"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "malloc_trim"
        ):
            calls.append(node)
    return calls


def test_manager_registers_malloc_trim_exactly_once() -> None:
    calls = _malloc_trim_register_calls(_manager_tree())
    assert len(calls) == 1, "malloc_trim must be registered exactly once"


def test_manager_registration_is_hourly_on_existing_loop() -> None:
    (call,) = _malloc_trim_register_calls(_manager_tree())
    # EXISTING MaintenanceService loop — never a new thread / new loop.
    assert isinstance(call.func.value, ast.Attribute)
    assert call.func.value.attr == "_maintenance_service"
    # hourly cadence
    kw = {k.arg: k.value for k in call.keywords}
    assert isinstance(kw.get("min_interval_hours"), ast.Constant)
    assert kw["min_interval_hours"].value == 1.0
    # callback is the guarded wrapper method
    assert isinstance(kw.get("execute_fn"), ast.Attribute)
    assert kw["execute_fn"].attr == "_run_malloc_trim"


def test_run_malloc_trim_wrapper_is_guarded() -> None:
    tree = _manager_tree()
    methods = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name == "_run_malloc_trim"
    ]
    assert len(methods) == 1, "_run_malloc_trim must be defined exactly once"
    src = ast.get_source_segment(MANAGER.read_text(encoding="utf-8"), methods[0])
    assert src is not None
    # calls the never-raises helper …
    assert "trim_malloc_heap()" in src
    # … inside a try/except so the host loop can never die on it.
    assert "except Exception" in src


# ── 3. __main__.py module-level setdefault pin ──────────────────────────────


def test_main_entry_sets_default_at_module_level() -> None:
    src = MAIN_ENTRY.read_text(encoding="utf-8")
    needle = 'os.environ.setdefault("MALLOC_ARENA_MAX", "2")'
    assert needle in src, (
        "daemon/__main__.py must carry the MALLOC_ARENA_MAX setdefault"
    )
    # Module-level placement: before the first def → runs at import,
    # before uvicorn/threads/any child spawn on every entry that reaches
    # this module (frozen run_app.py import; `python -m daemon`).
    assert src.index(needle) < src.index("def main(")


def test_main_entry_setdefault_is_default_only() -> None:
    # setdefault semantics (a staged MALLOC_ARENA_MAX wins) — a bare
    # assignment would clobber operator overrides.
    src = MAIN_ENTRY.read_text(encoding="utf-8")
    assert 'os.environ["MALLOC_ARENA_MAX"]' not in src
