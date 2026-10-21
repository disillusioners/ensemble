"""Pinned tests for the hard-reboot worktree gate (Phase 0 / A5 / R-07).

Feature: hard-reboot recovery. Phase 0.

The gate (``tests/conftest_worktree.py``) must FAIL LOUDLY when
``daemon`` resolves outside the feature worktree — that is the
anti-silent-tests-on-wrong-tree invariant (R-07 / A5).

These pins cover the resolution itself, in BOTH directions:

  * a daemon that resolves OUTSIDE the expected worktree is refused;
  * a daemon that resolves INSIDE the expected worktree is accepted;
  * the resolution NEVER re-executes the ``daemon`` package (the
    regression that turned the whole suite red: SQLAlchemy's
    declarative registry is process-global, so re-importing the model
    modules re-defines the same tables and raises
    ``InvalidRequestError: Table '...' is already defined for this
    MetaData instance``).

The pins are checkout-agnostic: they monkeypatch the resolution, so
they hold on the feature worktree AND on main / remote VMs (where the
gate is inert by default).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from tests import conftest_worktree as gate

_FAKE_ORIGIN = "/tmp/definitely-not-the-worktree/daemon/__init__.py"


def _fake_spec(origin: str) -> SimpleNamespace:
    """A stand-in for the spec object ``find_spec`` returns."""
    return SimpleNamespace(origin=origin, submodule_search_locations=None)


def test_resolution_does_not_mutate_sys_modules():
    """The gate must NOT pop/re-import ``daemon`` (process-global metadata).

    This is the regression pin for the defect that made the ARMED gate
    destroy the suite: the old implementation dropped every ``daemon*``
    entry from ``sys.modules`` and re-imported the package.
    """
    before = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "daemon" or name.startswith("daemon.")
    }

    gate._daemon_resolves_inside_worktree()

    for name, mod in before.items():
        assert sys.modules.get(name) is mod, f"{name} was re-imported by the gate"


def test_real_resolution_points_at_the_daemon_package():
    """The real resolution finds the on-disk package — without executing it."""
    resolved = gate._resolve_daemon_origin()

    assert resolved != "<unknown>"
    assert resolved.endswith("daemon/__init__.py")


def test_fake_path_is_refused(monkeypatch):
    """DIRECTION 1 — a daemon resolving OUTSIDE the worktree is refused."""
    monkeypatch.setattr(
        gate.importlib.util, "find_spec", lambda name, package=None: _fake_spec(_FAKE_ORIGIN)
    )

    inside, resolved = gate._daemon_resolves_inside_worktree()

    assert inside is False
    assert resolved == _FAKE_ORIGIN


def test_real_path_inside_the_worktree_is_accepted(monkeypatch, tmp_path):
    """DIRECTION 2 — a daemon resolving INSIDE the worktree is accepted."""
    expected = str(tmp_path / "wt")
    origin = f"{expected}/daemon/__init__.py"
    monkeypatch.setattr(
        gate.importlib.util, "find_spec", lambda name, package=None: _fake_spec(origin)
    )
    monkeypatch.setattr(gate, "WORKTREE_PATH", expected)

    inside, resolved = gate._daemon_resolves_inside_worktree()

    assert inside is True
    assert resolved == origin


def test_module_import_path_fallback_is_used_when_no_spec(monkeypatch):
    """No spec ⇒ fall back to the already-imported module's file."""
    monkeypatch.setattr(
        gate.importlib.util, "find_spec", lambda name, package=None: None
    )
    monkeypatch.setitem(sys.modules, "daemon", SimpleNamespace(__file__="/x/daemon/__init__.py"))

    assert gate._resolve_daemon_origin() == "/x/daemon/__init__.py"
