"""The RAM-guard fence helper (test-plan.md §2 — baked into every spawning test).

The house hazards below have caused real incidents.  They are NOT
optional and are NOT rediscovered per test — a spawning RAM-guard test
runs under :func:`apply_fence` and its application is ASSERTED (not
merely invoked) by :func:`assert_fence_applied`:

* ``ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`` pinned ``0`` EXPLICITLY —
  ``env -u`` on it STRIPS the ambient guard and the boot sweep kills
  live processes (2026-10-31 staged-smoke S2).
* ``env -u POSTGRES_*`` is a SILENT NO-OP — bash does not glob env
  names, so each variable is scrubbed PER-VARIABLE (2026-10-21).
* A ``mktemp`` FAKE_HOME + an escape guard — never a HOME-derived
  install dir (2026-10-12 — a test deleted the live install twice).  A
  sentinel file inside the fake home proves the child inherited it.
* The ``ENSEMBLE_AUTH_*`` family is scrubbed ALL-OR-NONE — a partial
  scrub breaks collection (≈100 phantom reds).

This module is deliberately dependency-free (stdlib only) so it can be
imported by any pack without dragging in fixtures.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The boot-sweep kill-switch, pinned ``0`` (never ``env -u``).
BOOT_SWEEP_VAR = "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"

#: Scrubbed PER-VARIABLE (bash does not glob env names).
POSTGRES_VARS: tuple[str, ...] = (
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "DATABASE_URL",
    "DATABASE_URL_POSTGRES",
)

#: The auth family prefix — scrubbed ALL-OR-NONE.
AUTH_PREFIX = "ENSEMBLE_AUTH_"

#: A sentinel file inside the fake HOME; the child must inherit the dir.
FAKE_HOME_SENTINEL = ".ram-guard-fake-home"


def _auth_vars() -> tuple[str, ...]:
    """Every ambient ``ENSEMBLE_AUTH_*`` name (all-or-none scrub)."""
    return tuple(name for name in os.environ if name.startswith(AUTH_PREFIX))


def apply_fence(monkeypatch, tmp_path: Path) -> tuple[Path, tuple[str, ...]]:
    """Apply the fence; return ``(fake_home, scrubbed_auth_names)``.

    The caller MUST pass the returned pair to
    :func:`assert_fence_applied` at the end of the test — invocation
    alone is not proof that the fence held.
    """
    monkeypatch.setenv(BOOT_SWEEP_VAR, "0")
    for var in POSTGRES_VARS:
        monkeypatch.delenv(var, raising=False)
    auth_names = _auth_vars()
    for name in auth_names:
        monkeypatch.delenv(name, raising=False)
    fake_home = Path(tmp_path) / "fake-home"
    fake_home.mkdir(parents=True, exist_ok=True)
    (fake_home / FAKE_HOME_SENTINEL).write_text("fake\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(fake_home))
    return fake_home, auth_names


def assert_fence_applied(fake_home: Path, auth_names: tuple[str, ...]) -> None:
    """Assert the fence actually HELD (the application, not the intent)."""
    assert os.environ.get(BOOT_SWEEP_VAR) == "0", (
        f"{BOOT_SWEEP_VAR} must be pinned '0' explicitly"
    )
    for var in POSTGRES_VARS:
        assert var not in os.environ, f"{var} leaked past the per-variable scrub"
    remaining_auth = tuple(n for n in os.environ if n.startswith(AUTH_PREFIX))
    assert remaining_auth == (), (
        f"the ENSEMBLE_AUTH_* family is scrubbed ALL-OR-NONE; leaked: {remaining_auth}"
    )
    assert os.environ.get("HOME") == str(fake_home), (
        "HOME was not redirected to the FAKE_HOME"
    )
    assert (Path(fake_home) / FAKE_HOME_SENTINEL).exists(), (
        "the FAKE_HOME sentinel is missing — the fence did not materialize"
    )
    # The live-shaped install dirs must NEVER be the resolved HOME.
    resolved = Path(os.environ["HOME"]).resolve()
    for forbidden in ("agents-ensemble", "ensemble-src"):
        assert forbidden not in resolved.parts, (
            f"HOME resolved to a live-shaped path: {resolved}"
        )


__all__ = [
    "AUTH_PREFIX",
    "BOOT_SWEEP_VAR",
    "FAKE_HOME_SENTINEL",
    "POSTGRES_VARS",
    "apply_fence",
    "assert_fence_applied",
]
