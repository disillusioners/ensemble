"""Shared Plane-workspace test fixtures (lane ``plane-pm-issue-tools`` fold C12).

FIVE test files carry the SAME two fixtures:

* an AUTOLSE resolver-cache reset — byte-equivalent in all five
  (``reset_resolution_cache(); yield; reset_resolution_cache()``);
* an ``env`` factory that pins a fully-controlled Plane environment.

The ``env`` bodies drifted (each file drives a different set of env vars
with its OWN default values), so the helper is PARAMETERISED: each file
declares the variables it drives — and the default for each — and the
shared mechanism applies them. No file loses its own defaults.

Semantics preserved exactly:

* an ``env`` value of ``None`` means DELETED (never "the string None") —
  the house ``monkeypatch.delenv(..., raising=False)`` shape;
* a ``(None, VAR, value)`` entry is a FIXED variable: it is always set to
  ``value`` and is NOT an overridable parameter (the original fixtures in
  those files had no such parameter);
* the resolver cache is reset ONCE at the end of the ``_set`` call (the
  memo is keyed on the env fingerprint, so the reset is what makes the
  new env visible);
* ``return_resolution`` selects whether ``_set`` returns the fresh
  ``current_plane_workspace()`` or ``None``;
* an unknown keyword raises ``TypeError`` — the original keyword-only
  signatures did the same.
"""

from __future__ import annotations

from typing import Any

import pytest

from daemon.plane_workspace import current_plane_workspace, reset_resolution_cache

__all__ = [
    "make_plane_env_fixture",
    "reset_plane_workspace_cache",
]


@pytest.fixture(autouse=True)
def reset_plane_workspace_cache():
    """Reset the resolver memo before AND after every test.

    Byte-equivalent to the autouse fixture each of the five files carried
    before the fold.
    """
    reset_resolution_cache()
    yield
    reset_resolution_cache()


def make_plane_env_fixture(
    env_vars: tuple[tuple[str | None, str, str | None], ...],
    *,
    return_resolution: bool = False,
):
    """Build the ``env`` fixture for one test file.

    Args:
        env_vars: the variables the file drives, IN APPLICATION ORDER, as
            ``(parameter_name, env_var_name, default)`` tuples. A
            ``parameter_name`` of ``None`` marks a FIXED variable (always
            set to ``default``, never overridable).
        return_resolution: when True the fixture's ``_set`` returns the
            fresh ``current_plane_workspace()``; when False it returns
            ``None``.

    Returns:
        A pytest fixture function — bind it to the module-level name
        ``env`` so the tests' call sites are unchanged.
    """
    params = {name for name, _var, _default in env_vars if name is not None}

    @pytest.fixture
    def env(monkeypatch):
        def _set(**overrides: Any):
            unknown = set(overrides) - params
            if unknown:
                raise TypeError(
                    "unexpected environment fixture argument(s): "
                    f"{sorted(unknown)}"
                )
            for name, var, default in env_vars:
                value = default if name is None else overrides.get(name, default)
                if value is None:
                    monkeypatch.delenv(var, raising=False)
                else:
                    monkeypatch.setenv(var, value)
            reset_resolution_cache()
            if return_resolution:
                return current_plane_workspace()
            return None

        return _set

    return env
