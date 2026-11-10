"""scripts/stop-ensemble.sh — guard-02: the BARE default must not stop the
live/demo install (incident 2026-11-09, live-install-guard mission, leg 4).

THE RESIDUAL THIS PINS. ``scripts/stop-ensemble.sh`` used to resolve a
bare invocation (no positional INSTALL_DIR) to ``_default_install_dir()`` —
and that default can ONLY ever be a live or a demo install, never a
sandbox:

  * normal HOME                            → $HOME/agents-ensemble   (LIVE)
  * Layer-1 HOME (basename agents-ensemble)      → $HOME   (LIVE)
  * Layer-1 HOME (basename agents-ensemble-demo)  → $HOME   (DEMO)

So the ONE shape that always aims at a running install aimed at it
silently: a bare ``bash scripts/stop-ensemble.sh`` walked the ownership
scan of the LIVE install and TERMed whatever it owned — with no
confirmation at all. The guard-02 gate (scripts/stop-ensemble.sh, right
after the positional parse) now refuses that shape with exit 78 and prints
both intentional forms. The EXPLICIT form (<dir> ...) is untouched — no
double-gating (the Makefile's install-dir-guard fronts the live dir).

SAFETY (read before changing anything in this file):
  * EVERY execution of scripts/stop-ensemble.sh here runs with HOME = a
    fresh /tmp fake home, and every path the script can touch lives under
    tmp. Nothing here ever points at a real install dir, a real home, a
    real daemon or a real port.
  * The REFUSAL arm exits BEFORE any lookup or signal. The gate sits
    right after the positional parse — ahead of the lib.sh source, the
    Layer (i) settle-check, the Layer (ii) pipeline-lock acquire (which
    WRITES <dir>/releases/rollback.lock.d) and every lsof/pid lookup that
    could lead to a signal. The tests assert that evidence directly: the
    refusal prints nothing on stdout, no ownership-scan line appears, no
    port report appears, and NO releases/ dir is created.
  * The PROCEED arm (the confirm var set) really does execute the
    script's ownership path — and it CANNOT deliver a signal: the owned
    set is built from processes whose launcher path
    (<INSTALL_DIR>/launcher.sh, <INSTALL_DIR>/ensemble-prod,
    <INSTALL_DIR>/current/ensemble-prod) or whose cwd IS the install dir.
    A fresh /tmp fake dir owns nothing, so the script logs
    "no processes owned by <dir> — nothing to stop" and exits 0 BEFORE its
    signal sites (the launcher TERM pass and the straggler TERM pass). The
    tests assert that exact shape and that no TERM/"stopping" line was
    printed.
  * Every subprocess call carries a timeout cap.

WHY A SIBLING FILE: tests/unit/test_makefile_install_guard.py carries the
carve-out "scripts/stop-ensemble.sh is NEVER executed — not even under the
fake home" (its module docstring). That statement stays TRUE for that
file — the executions guard-02 needs to pin live here instead.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STOP = REPO_ROOT / "scripts" / "stop-ensemble.sh"

#: Vars scrubbed from every subprocess environment — an ambient export must
#: never open (or mis-open) the gate under test. STOP_BY_PORT is the legacy
#: port-kill opt-in (never set it here: it would kill by PORT, not by
#: ownership); PIPELINE_LOCK_HELD_BY_CALLER and ENSEMBLE_SUPERVISION_RESULT
#: would divert the script into the in-pipeline / unit-managed lanes.
SCRUBBED_VARS = (
    "INSTALL_DIR",
    "ENSEMBLE_INSTALL_LIVE",
    "ENSEMBLE_INSTALL_DEMO",
    "ENSEMBLE_LIVE_INSTALL_DIRS",
    "SUDO_USER",
    "PIPELINE_LOCK_HELD_BY_CALLER",
    "ENSEMBLE_SUPERVISION_RESULT",
    "STOP_BY_PORT",
    "STOP_FORCE",
    "WAIT_S",
    "DRY_RUN",
)

#: The script's config-refuse code (same repo convention as the guard's).
REFUSE_RC = 78


def _base_env(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_VARS}
    env["HOME"] = str(home)
    env.update(extra or {})
    return env


def run_stop(
    args: list[str],
    home: Path,
    extra_env: dict[str, str] | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run scripts/stop-ensemble.sh with a fake HOME (timeout-capped)."""
    return subprocess.run(
        ["bash", str(STOP), *args],
        cwd=str(REPO_ROOT),
        env=_base_env(home, extra_env),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture()
def fake_home(tmp_path: Path) -> Path:
    """A fake operator HOME with a LIVE-shaped install dir inside it."""
    home = tmp_path / "home"
    (home / "agents-ensemble").mkdir(parents=True)
    return home


@pytest.fixture()
def fake_demo_home(tmp_path: Path) -> Path:
    """A fake Layer-1 DEMO HOME (the basename IS the demo install dir)."""
    home = tmp_path / "demo-home" / "agents-ensemble-demo"
    home.mkdir(parents=True)
    return home


@pytest.fixture()
def fake_sandbox(tmp_path: Path) -> Path:
    """A fake NON-live install dir (nothing inside is owned by anyone)."""
    sbx = tmp_path / "sandbox"
    sbx.mkdir()
    return sbx


class TestBareDefaultRefuses:
    """(a)/(c)/(e): the bare shape refuses unless the per-path var is set."""

    def test_bare_normal_home_refuses_and_names_the_live_dir(self, fake_home):
        """A bare invocation under a normal HOME resolves to the LIVE
        install → exit 78, the refusal names that dir and prints BOTH
        intentional forms."""
        live = fake_home / "agents-ensemble"
        r = run_stop([], fake_home)
        assert r.returncode == REFUSE_RC
        assert "REFUSING" in r.stderr
        assert "LIVE install dir" in r.stderr
        assert str(live) in r.stderr
        # both intentional forms
        assert f"bash scripts/stop-ensemble.sh '{live}' --force" in r.stderr
        assert "ENSEMBLE_INSTALL_LIVE=1 bash scripts/stop-ensemble.sh" in r.stderr
        # --force is documented as an execution-mode flag, not a confirm
        assert "it is NOT an intent confirm" in r.stderr

    def test_bare_layer1_demo_home_refuses_with_the_demo_var(self, fake_demo_home):
        """A Layer-1 DEMO HOME resolves to the DEMO install → exit 78 naming
        ENSEMBLE_INSTALL_DEMO=1 and the explicit demo-dir command."""
        r = run_stop([], fake_demo_home)
        assert r.returncode == REFUSE_RC
        assert "REFUSING" in r.stderr
        assert "DEMO install dir" in r.stderr
        assert str(fake_demo_home) in r.stderr
        assert f"bash scripts/stop-ensemble.sh '{fake_demo_home}' --force" in r.stderr
        assert "ENSEMBLE_INSTALL_DEMO=1 bash scripts/stop-ensemble.sh" in r.stderr
        # the live var must NOT open a demo default
        assert "ENSEMBLE_INSTALL_LIVE=1" not in r.stderr

    def test_bare_with_force_still_refuses(self, fake_home):
        """--force is an execution-mode flag (it bypasses the settle-check
        and the pipeline lock), NOT an intent confirm: a bare invocation
        with --force still refuses."""
        r = run_stop(["--force"], fake_home)
        assert r.returncode == REFUSE_RC
        assert "REFUSING" in r.stderr
        assert "LIVE install dir" in r.stderr
        assert "ENSEMBLE_INSTALL_LIVE=1 bash scripts/stop-ensemble.sh" in r.stderr

    def test_wrong_confirm_var_does_not_open_the_demo_default(self, fake_demo_home):
        """The per-path vars are NOT interchangeable: the live var must not
        open a demo default (same discipline as the Makefile guard)."""
        r = run_stop([], fake_demo_home, extra_env={"ENSEMBLE_INSTALL_LIVE": "1"})
        assert r.returncode == REFUSE_RC
        assert "DEMO install dir" in r.stderr

    def test_refusal_is_non_vacuous_no_side_effects(self, fake_home):
        """NON-VACUITY: the refusal exits before the ownership scan, the
        port report and the pipeline-lock acquire (the earliest WRITE).
        Nothing is printed on stdout and no releases/ dir is created."""
        live = fake_home / "agents-ensemble"
        r = run_stop([], fake_home)
        assert r.returncode == REFUSE_RC
        assert r.stdout == "", "the script ran past the gate"
        assert "scoping to INSTALL_DIR=" not in r.stdout
        assert "no processes owned by" not in r.stdout
        assert "WAIT_S resolved to" not in r.stdout
        assert "port " not in r.stdout
        assert not (live / "releases").exists(), "the pipeline lock was acquired"
        assert live.exists()


class TestBareDefaultProceeds:
    """(b): the confirm var opens the bare shape; nothing is owned, so the
    script's ownership path runs to its empty-set exit — no signal."""

    def test_bare_with_live_confirm_proceeds_to_the_nothing_owned_exit(self, fake_home):
        """PROCEED arm: with ENSEMBLE_INSTALL_LIVE=1 the bare invocation
        proceeds into the ownership scan, finds nothing and exits 0 with
        today's shape — no TERM, no stop action."""
        live = fake_home / "agents-ensemble"
        r = run_stop([], fake_home, extra_env={"ENSEMBLE_INSTALL_LIVE": "1"})
        assert r.returncode == 0, r.stderr
        assert f"scoping to INSTALL_DIR={live}" in r.stdout
        assert f"no processes owned by {live} — nothing to stop" in r.stdout
        # non-vacuity of the proceed arm: no signal action was taken
        assert "TERMing" not in r.stdout
        assert "stopping" not in r.stdout.lower()
        assert r.stderr == ""

    def test_bare_with_demo_confirm_proceeds_under_a_demo_home(self, fake_demo_home):
        """PROCEED arm (demo): ENSEMBLE_INSTALL_DEMO=1 opens the demo
        default; the same empty-owned-set exit follows."""
        r = run_stop(
            [], fake_demo_home, extra_env={"ENSEMBLE_INSTALL_DEMO": "1"}
        )
        assert r.returncode == 0, r.stderr
        assert f"scoping to INSTALL_DIR={fake_demo_home}" in r.stdout
        assert "no processes owned by" in r.stdout
        assert "TERMing" not in r.stdout


class TestExplicitDirUnchanged:
    """(d) + scope discipline: the explicit form behaves EXACTLY as before
    the gate (baseline captured from the pre-change commit 3d89ea66b)."""

    def test_explicit_sandbox_dir_matches_the_pre_change_baseline(self, fake_sandbox):
        """An explicit NON-live dir is untouched by the gate: byte-identical
        output and exit code to the pre-change baseline."""
        r = run_stop([str(fake_sandbox)], home=fake_sandbox.parent)
        assert r.returncode == 0, r.stderr
        expected = (
            f"stop-ensemble: scoping to INSTALL_DIR={fake_sandbox}\n"
            "stop-ensemble: WAIT_S resolved to 70s — default "
            "(70 = 60s graceful + 10s margin)\n"
            f"stop-ensemble: no processes owned by {fake_sandbox} — nothing to stop\n"
        )
        assert r.stdout == expected

    def test_explicit_sandbox_dir_with_port_matches_the_baseline(self, fake_sandbox):
        """The <dir> <port> form is unchanged too (the port is a reporting
        hint only)."""
        r = run_stop([str(fake_sandbox), "7979"], home=fake_sandbox.parent)
        assert r.returncode == 0, r.stderr
        assert f"scoping to INSTALL_DIR={fake_sandbox}" in r.stdout
        assert "port 7979 is free" in r.stdout
        assert "no processes owned by" in r.stdout

    def test_explicit_live_dir_without_confirm_var_is_not_double_gated(self, fake_home):
        """SCOPE DISCIPLINE: today an explicit live dir is accepted with NO
        confirm var (the Makefile's guard fronts it) — that stays exactly
        as it is; this script does not double-gate the explicit path."""
        live = fake_home / "agents-ensemble"
        r = run_stop([str(live)], home=fake_home)
        assert r.returncode == 0, r.stderr
        assert f"scoping to INSTALL_DIR={live}" in r.stdout
        assert "no processes owned by" in r.stdout
        assert "REFUSING" not in r.stderr


class TestUnresolvableInstallDir:
    """GUARD-1: a resolution failure must refuse CLEANLY (exit 78, named),
    never die through a bash `unbound variable` error.

    The bare+confirm-var shape resolves to `$HOME/agents-ensemble` (or
    `$HOME` itself); when that dir does not exist the physical resolution
    (`cd + pwd`) fails. The old code died through `_die … '$1'` — under
    `set -u` a BARE invocation has no `$1`, so the script aborted with a
    bash error (rc 1) instead of a clean, named refusal.
    """

    def test_bare_with_confirm_and_absent_default_dir_refuses_cleanly(
        self, tmp_path
    ):
        """Bare + confirm var + absent default dir → exit 78, named."""
        home = tmp_path / "home-no-install"
        home.mkdir()
        r = run_stop([], home, extra_env={"ENSEMBLE_INSTALL_LIVE": "1"})
        assert r.returncode == REFUSE_RC, (r.returncode, r.stdout, r.stderr)
        assert "REFUSING" in r.stderr
        assert "cannot be resolved" in r.stderr
        assert "bash scripts/stop-ensemble.sh '<install-dir>'" in r.stderr
        assert "unbound variable" not in r.stderr

    def test_explicit_missing_install_dir_refuses_cleanly(self, tmp_path):
        """The explicit shape with a missing dir refuses cleanly too (same
        named refusal — the resolution is shared)."""
        missing = tmp_path / "not-there"
        home = tmp_path / "home"
        home.mkdir()
        r = run_stop([str(missing)], home)
        assert r.returncode == REFUSE_RC, (r.returncode, r.stdout, r.stderr)
        assert "REFUSING" in r.stderr
        assert str(missing) in r.stderr
        assert "unbound variable" not in r.stderr
