"""Makefile INSTALL_DIR live-install guard tests (incident 2026-11-09,
commission wi-52666630 leg 1; operator directive #2 addenda included).

Pins the root-cause guard for the ``INSTALL_DIR ?= $(HOME)/agents-ensemble``
hazard: a stray destructive ``make`` invocation from any cwd used to hit the
LIVE install because the live dir was the DEFAULT target of every mutating
Makefile target (``uninstall`` = ungated ``rm -rf $(INSTALL_DIR)``;
``install`` = kills the live daemon via scripts/stop-ensemble.sh first).

The guard (scripts/install-dir-guard.sh + Makefile wiring) gives:
  1. INERT DEFAULT — bare ``make install`` / ``make uninstall`` refuse with
     guidance; there is no default install dir anymore.
  2. LIVE-CONFIRM GATE — explicit INSTALL_DIR canonicalizing to a protected
     live dir ($HOME/agents-ensemble both Layer-1 shapes, /opt/ensemble,
     plus ENSEMBLE_LIVE_INSTALL_DIRS entries) or the demo dir refuses
     without ENSEMBLE_INSTALL_LIVE=1 / ENSEMBLE_INSTALL_DEMO=1.

SAFETY CARVE-OUT (mission rules, honored here verbatim):
  * EVERY real make invocation runs with HOME = a fresh tmp fake home and
    every path any recipe can touch lives under tmp.
  * scripts/stop-ensemble.sh is NEVER executed — not even under the fake
    home. Targets whose recipes call it (install, stop) prove their
    proceed-arm at GUARD level (direct guard invocation with the confirm
    var → exit 0) plus ``make -n`` sanity; the make-level proceed proof
    uses the stop-free ``uninstall`` recipe (echo / rm -rf / echo only —
    recon-confirmed, no script calls).
  * The /opt/ensemble built-in is exercised at GUARD level ONLY (the guard
    never writes, so a refusal probe cannot side-effect) and at make level
    via a tmp fake dir reached through ENSEMBLE_LIVE_INSTALL_DIRS.
  * The sudo arm is gated behind ``sudo -n`` (non-interactive): skipped
    with a reason when sudo would prompt — a test must never hang on a
    password prompt.

REPO ROOT DISCOVERY: this file lives at tests/unit/, so
``Path(__file__).resolve().parents[2]`` is the repo root — the same
convention as tests/unit/tools/test_upgrade_journal.py (parents[3] one
level deeper) and tests/unit/test_memoir_tools_unit.py (parents[2]).
No daemon imports: pure subprocess + pathlib; the tests/conftest.py
langgraph mocks are irrelevant to this file.

ENV SCRUBBING: every subprocess env is derived from os.environ minus the
guard-relevant vars (INSTALL_DIR, ENSEMBLE_INSTALL_*, ENSEMBLE_LIVE_INSTALL_DIRS,
ENSEMBLE_DEPLOY_LIVE, SUDO_USER, MAKEFLAGS/MFLAGS/MAKELEVEL) so an ambient
shell export can never silently weaken the gate under test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
GUARD = REPO_ROOT / "scripts" / "install-dir-guard.sh"
STOP_SCRIPT = REPO_ROOT / "scripts" / "stop-ensemble.sh"

#: Guard-relevant vars scrubbed from every subprocess environment.
SCRUBBED_VARS = (
    "INSTALL_DIR",
    "ENSEMBLE_INSTALL_LIVE",
    "ENSEMBLE_INSTALL_DEMO",
    "ENSEMBLE_LIVE_INSTALL_DIRS",
    "ENSEMBLE_DEPLOY_LIVE",
    "ENSEMBLE_DEPLOY_DEMO",
    "SUDO_USER",
    "MAKEFLAGS",
    "MFLAGS",
    "MAKELEVEL",
)

#: Bare ``make`` exit code is make's own (2) when a recipe/prereq fails;
#: the guard's config-refuse code 78 surfaces in the OUTPUT (``Error 78``).
MAKE_REFUSED_RC = 2
GUARD_REFUSE_RC = 78


def _base_env(
    home: Path,
    extra: dict[str, str] | None = None,
    drop_home: bool = False,
) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_VARS}
    env["HOME"] = str(home)
    if drop_home:
        # F-02 arm probe: HOME must be genuinely ABSENT from the child env —
        # popping it is the only honest way to test the "unset" spelling
        # (os.environ always carries the operator's real HOME).
        env.pop("HOME", None)
    env.update(extra or {})
    return env


def run_make(
    args: list[str],
    home: Path,
    extra_env: dict[str, str] | None = None,
    timeout: int = 60,
    cwd: Path | None = None,
    drop_home: bool = False,
) -> subprocess.CompletedProcess:
    """Run make from the repo root with a fake HOME (carve-out rule).

    ``cwd`` overrides the working directory for the relative-spelling rows
    (the guard resolves a relative INSTALL_DIR against ITS OWN cwd); the
    default stays the repo root so every existing call is unchanged.
    ``drop_home`` removes HOME from the child env entirely (F-02).
    """
    return subprocess.run(
        ["make", *args],
        cwd=str(cwd or REPO_ROOT),
        env=_base_env(home, extra_env, drop_home=drop_home),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_guard(
    target: str,
    home: Path,
    install_dir: str | None = None,
    extra_env: dict[str, str] | None = None,
    timeout: int = 30,
    drop_home: bool = False,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the guard script directly (guard-level proof; never writes).

    ``install_dir`` reaches the guard VERBATIM through the environment — make
    never touches this spelling, so the raw-value arm sees exactly the bytes
    the operator typed (that is why the metacharacter rows are guard-level).
    ``drop_home`` removes HOME from the child env entirely (F-02). ``cwd``
    moves the guard's working directory (the relative-spelling row resolves
    a relative INSTALL_DIR against it).
    """
    env = _base_env(home, extra_env, drop_home=drop_home)
    if install_dir is not None:
        env["INSTALL_DIR"] = install_dir
    return subprocess.run(
        ["bash", str(GUARD), "--target", target],
        cwd=str(cwd or REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def make_live_shaped(home: Path) -> Path:
    """Create the fake-home-shaped LIVE dir with a sentinel file."""
    live = home / "agents-ensemble"
    live.mkdir(parents=True, exist_ok=True)
    (live / "SENTINEL").write_text("do-not-delete")
    return live


def sentinel_of(path: Path) -> Path:
    return path / "SENTINEL"


@pytest.fixture()
def fake_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


# ─────────────────────────── make-level: the 8-case matrix ──────────────────


class TestInertDefault:
    def test_bare_make_install_refuses_before_build_chain(self, fake_home):
        """Case 1: bare `make install` (no env) → inert refusal, and the
        refusal fires at the FIRST PREREQ — the pyinstaller → build →
        ensure-latest chain (git checkout latest!) must never start."""
        r = run_make(["install"], fake_home)
        assert r.returncode != 0
        assert "INSTALL_DIR is not set" in r.stderr
        assert "Error 78" in r.stderr  # the guard's refusal code, make-wrapped
        # ordering proof: no build-chain side effect even STARTED
        assert "PyInstaller" not in r.stdout
        assert "Switching to latest branch" not in r.stdout
        # inert: nothing was created under the fake home
        assert not (fake_home / "agents-ensemble").exists()

    def test_bare_make_uninstall_refuses(self, fake_home):
        """Case 2: bare `make uninstall` → inert refusal, no rm anywhere."""
        r = run_make(["uninstall"], fake_home)
        assert r.returncode != 0
        assert "INSTALL_DIR is not set" in r.stderr
        assert not (fake_home / "agents-ensemble").exists()


class TestSafeSandboxProceeds:
    def test_uninstall_with_safe_sandbox_proceeds(self, fake_home):
        """Case 3: explicit NON-live-shaped INSTALL_DIR → guard opens; the
        make-level proceed proof runs on the stop-free uninstall recipe."""
        sandbox = fake_home / "safe-sbx"
        sandbox.mkdir()
        (sandbox / "SENTINEL").write_text("ok-to-delete")
        # command-line make-variable form (exercises `export INSTALL_DIR`)
        r = run_make(["uninstall", f"INSTALL_DIR={sandbox}"], fake_home)
        assert r.returncode == 0, r.stderr
        assert "Uninstall complete" in r.stdout
        assert not sandbox.exists()  # rm -rf actually ran in the sandbox

    def test_uninstall_with_env_install_dir_proceeds(self, fake_home):
        """Same as above via the ENV form — both invocation styles must
        reach the guard through the Makefile's `export INSTALL_DIR`."""
        sandbox = fake_home / "safe-env"
        sandbox.mkdir()
        r = run_make(["uninstall"], fake_home, extra_env={"INSTALL_DIR": str(sandbox)})
        assert r.returncode == 0, r.stderr
        assert not sandbox.exists()


class TestLiveGate:
    def test_live_shaped_without_confirm_refuses_before_rm(self, fake_home):
        """Case 4 + 7: the ungated `rm -rf` specifically refused — the
        fake-home "live" dir still EXISTS after the refused attempt, and
        the refusal names the exact confirm var."""
        live = make_live_shaped(fake_home)
        r = run_make(["uninstall", f"INSTALL_DIR={live}"], fake_home)
        assert r.returncode != 0
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr
        assert sentinel_of(live).exists(), "ungated rm -rf ran — GUARD ORDERING BROKEN"

    def test_live_shaped_with_confirm_proceeds(self, fake_home):
        """Case 5: live path WITH ENSEMBLE_INSTALL_LIVE=1 → proceeds.
        Make-level proceed proof via the stop-free uninstall recipe
        (install/stop proceed-arms are guard-level only — carve-out)."""
        live = make_live_shaped(fake_home)
        r = run_make(
            ["uninstall", f"INSTALL_DIR={live}"],
            fake_home,
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r.returncode == 0, r.stderr
        assert not live.exists()

    def test_vm_rollback_shape_refuse_then_proceed(self, fake_home):
        """Addendum 5c: the VM scenario shape — HOME=<fakehome> with
        <fakehome>/agents-ensemble as live. `make uninstall` refuses
        without the var, proceeds with it (data-center-scripts rollback
        block becomes: set INSTALL_DIR + ENSEMBLE_INSTALL_LIVE=1)."""
        live = make_live_shaped(fake_home)
        refused = run_make(["uninstall", f"INSTALL_DIR={live}"], fake_home)
        assert refused.returncode != 0
        assert sentinel_of(live).exists()
        proceeded = run_make(
            ["uninstall", f"INSTALL_DIR={live}"],
            fake_home,
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert proceeded.returncode == 0, proceeded.stderr
        assert not live.exists()


class TestDemoGate:
    def test_demo_without_confirm_refuses(self, fake_home):
        """Case 6 (arm 1): demo path without ENSEMBLE_INSTALL_DEMO=1."""
        demo = fake_home / "agents-ensemble-demo"
        demo.mkdir()
        (demo / "SENTINEL").write_text("keep")
        r = run_make(["uninstall", f"INSTALL_DIR={demo}"], fake_home)
        assert r.returncode != 0
        assert "ENSEMBLE_INSTALL_DEMO" in r.stderr
        assert sentinel_of(demo).exists()

    def test_demo_with_confirm_proceeds(self, fake_home):
        """Case 6 (arm 2): demo path with ENSEMBLE_INSTALL_DEMO=1."""
        demo = fake_home / "agents-ensemble-demo"
        demo.mkdir()
        (demo / "SENTINEL").write_text("ok-to-delete")
        r = run_make(
            ["uninstall", f"INSTALL_DIR={demo}"],
            fake_home,
            extra_env={"ENSEMBLE_INSTALL_DEMO": "1"},
        )
        assert r.returncode == 0, r.stderr
        assert not demo.exists()


class TestTildeSpellingGate:
    """Tilde spellings gate (incident verification follow-up):
    - `~user/…` forms and bare `~` cannot be reliably canonicalized prior to
      shell expansion; the guard must refuse them with exit 78 even if a
      confirm var is present.
    - `~/…` forms normalize cleanly to `$HOME/…` and flow through candidate
      matching (refused without confirm var, allowed with it).
    """

    def test_tilde_user_spelling_refuses_make_uninstall(self, fake_home):
        """~fakeuser/agents-ensemble must refuse before recipe execution,
        leaving any fake-home sentinel untouched."""
        live = make_live_shaped(fake_home)
        r = run_make(["uninstall", "INSTALL_DIR=~fakeuser/agents-ensemble"], fake_home)
        assert r.returncode != 0
        assert "cannot be verified safely" in r.stderr
        assert "Error 78" in r.stderr
        assert sentinel_of(live).exists(), "ungated rm -rf ran"

    def test_bare_tilde_refuses_make_uninstall(self, fake_home):
        """Bare INSTALL_DIR=~ (whole-HOME target hazard) must refuse before rm."""
        live = make_live_shaped(fake_home)
        r = run_make(["uninstall", "INSTALL_DIR=~"], fake_home)
        assert r.returncode != 0
        assert "cannot be verified safely" in r.stderr
        assert "Error 78" in r.stderr
        assert sentinel_of(live).exists(), "ungated rm -rf ran"

    def test_tilde_user_with_confirm_var_still_refuses(self, fake_home):
        """Confirm var (ENSEMBLE_INSTALL_LIVE=1) does NOT open unverifiable
        spellings; the guard refuses regardless."""
        live = make_live_shaped(fake_home)
        r = run_make(
            ["uninstall", "INSTALL_DIR=~fakeuser/agents-ensemble"],
            fake_home,
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r.returncode != 0
        assert "cannot be verified safely" in r.stderr
        assert "Error 78" in r.stderr
        assert sentinel_of(live).exists(), "ungated rm -rf ran"

    def test_tilde_slash_live_refused_without_confirm(self, fake_home):
        """~/agents-ensemble resolves to $HOME/agents-ensemble and is refused
        without ENSEMBLE_INSTALL_LIVE=1."""
        live = make_live_shaped(fake_home)
        r = run_make(["uninstall", "INSTALL_DIR=~/agents-ensemble"], fake_home)
        assert r.returncode != 0
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr
        assert sentinel_of(live).exists()

    def test_tilde_slash_live_proceeds_with_confirm(self, fake_home):
        """~/agents-ensemble proceeds when ENSEMBLE_INSTALL_LIVE=1 is set,
        and rm -rf cleans the fake home live dir."""
        live = make_live_shaped(fake_home)
        r = run_make(
            ["uninstall", "INSTALL_DIR=~/agents-ensemble"],
            fake_home,
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r.returncode == 0, r.stderr
        assert not live.exists()

    def test_unverifiable_tilde_guard_level(self, fake_home):
        """Guard-level direct invocations exit 78 for unverifiable tilde forms."""
        for spelling in ("~", "~fakeuser", "~fakeuser/agents-ensemble"):
            r = run_guard("uninstall", fake_home, install_dir=spelling)
            assert r.returncode == GUARD_REFUSE_RC, spelling
            assert "cannot be verified safely" in r.stderr, spelling
            # Confirm var does not open it
            r_conf = run_guard(
                "uninstall",
                fake_home,
                install_dir=spelling,
                extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
            )
            assert r_conf.returncode == GUARD_REFUSE_RC, spelling
            assert "cannot be verified safely" in r_conf.stderr, spelling


class TestMakeDryRun:
    def test_make_n_mutating_targets_do_not_explode(self, fake_home):
        """Case 8: `make -n` on mutating targets is sane (rc 0, no
        traceback) — the guard fires at RECIPE EXECUTION, not parse time."""
        for args in (
            ["-n", "uninstall"],
            ["-n", "install", f"INSTALL_DIR={fake_home / 'sbx'}"],
            ["-n", "stop", f"INSTALL_DIR={fake_home / 'agents-ensemble'}"],
        ):
            r = run_make(args, fake_home)
            assert r.returncode == 0, (args, r.stdout, r.stderr)
            assert "Traceback" not in r.stderr


class TestStopTargetGate:
    def test_stop_live_shaped_refuses_before_stop_script(self, fake_home):
        """stop (and its dependents start/dev) is INSTALL_DIR-mutating:
        the guard must refuse BEFORE scripts/stop-ensemble.sh executes.
        No make-level confirm arm here — that would run the stop script,
        which the carve-out forbids; the proceed-arm is guard-level
        (TestGuardLevel)."""
        live = make_live_shaped(fake_home)
        r = run_make(["stop", f"INSTALL_DIR={live}"], fake_home)
        assert r.returncode != 0
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr
        # the stop script's ownership-scoped banner never printed → it
        # never ran (guard ordering proof)
        assert "Stopping processes owned by" not in r.stdout


# ─────────────────────── addendum #2: extensible live set ───────────────────


class TestExtensibleLiveSet:
    def test_builtin_opt_ensemble_guard_level(self, fake_home):
        """/opt/ensemble (Stage-1 relocation target) is a BUILT-IN live dir.
        Guard-level probe only — the guard never writes, so this cannot
        side-effect the real /opt/ensemble."""
        r = run_guard("uninstall", fake_home, install_dir="/opt/ensemble")
        assert r.returncode == GUARD_REFUSE_RC
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr
        assert "/opt/ensemble" in r.stderr
        r2 = run_guard(
            "uninstall", fake_home, install_dir="/opt/ensemble",
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r2.returncode == 0

    def test_extension_var_gates_tmp_fake_live(self, fake_home, tmp_path):
        """Addendum 5b: a /tmp fake "/opt/ensemble" sandbox reached via
        ENSEMBLE_LIVE_INSTALL_DIRS — refuse without confirm (sentinel
        survives), proceed with confirm (make-level, stop-free recipe)."""
        fake_live = tmp_path / "fake-opt-ensemble"
        fake_live.mkdir()
        (fake_live / "SENTINEL").write_text("keep")
        env = {"ENSEMBLE_LIVE_INSTALL_DIRS": str(fake_live)}
        refused = run_make(
            ["uninstall", f"INSTALL_DIR={fake_live}"], fake_home, extra_env=env
        )
        assert refused.returncode != 0
        assert "ENSEMBLE_INSTALL_LIVE" in refused.stderr
        assert sentinel_of(fake_live).exists()
        proceeded = run_make(
            ["uninstall", f"INSTALL_DIR={fake_live}"],
            fake_home,
            extra_env={**env, "ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert proceeded.returncode == 0, proceeded.stderr
        assert not fake_live.exists()

    def test_extension_var_is_extend_not_replace(self, fake_home):
        """The override EXTENDS the built-ins: with ENSEMBLE_LIVE_INSTALL_DIRS
        set, the $HOME candidates stay protected (zero-config behavior and
        the HOME arm cannot be dropped by a bad override)."""
        live = make_live_shaped(fake_home)
        r = run_make(
            ["uninstall", f"INSTALL_DIR={live}"],
            fake_home,
            extra_env={"ENSEMBLE_LIVE_INSTALL_DIRS": "/tmp/somewhere-else"},
        )
        assert r.returncode != 0
        assert sentinel_of(live).exists()

    def test_extension_var_no_prefix_false_positive(self, fake_home, tmp_path):
        """A dir that merely shares a prefix with an extension entry is NOT
        gated (exact-path matching)."""
        ext = tmp_path / "ext-live"
        ext.mkdir()
        sibling = tmp_path / "ext-live-other"
        sibling.mkdir()
        r = run_make(
            ["uninstall", f"INSTALL_DIR={sibling}"],
            fake_home,
            extra_env={"ENSEMBLE_LIVE_INSTALL_DIRS": str(ext)},
        )
        assert r.returncode == 0, r.stderr
        assert not sibling.exists()


# ─────────────────── refusal-message content (addendum 5a) ──────────────────


class TestRefusalContent:
    def test_live_refusal_names_path_var_sudo_form_and_lanes(self, fake_home):
        """Addendum 1a/5a: the live refusal must be actionable — resolved
        path, exact confirm var, the sudo-correct command form, and the
        sanctioned deploy/upgrade lanes as signposts."""
        live = make_live_shaped(fake_home)
        r = run_make(["uninstall", f"INSTALL_DIR={live}"], fake_home)
        assert "REFUSING" in r.stderr
        assert str(live) in r.stderr or str(live.resolve()) in r.stderr
        assert "ENSEMBLE_INSTALL_LIVE=1" in r.stderr
        # sudo-correct form: `sudo env VAR=… make` (works without sudoers
        # env_keep; `sudo -E` would need it — the env form is the pinned one)
        assert f"sudo env ENSEMBLE_INSTALL_LIVE=1 make uninstall INSTALL_DIR='{live}'" in r.stderr
        # sanctioned-lane signposts
        assert "ENSEMBLE_DEPLOY_LIVE=1 scripts/deploy.sh live" in r.stderr
        assert "ENSEMBLE_UPGRADE_LIVE=1 scripts/upgrade/promote.sh live --f2-verified-closed" in r.stderr

    def test_demo_refusal_names_path_var_and_demo_lane(self, fake_home):
        demo = fake_home / "agents-ensemble-demo"
        demo.mkdir()
        r = run_make(["uninstall", f"INSTALL_DIR={demo}"], fake_home)
        assert str(demo) in r.stderr or str(demo.resolve()) in r.stderr
        assert "sudo env ENSEMBLE_INSTALL_DEMO=1 make uninstall" in r.stderr
        assert "scripts/deploy.sh demo" in r.stderr


# ──────────────────── canonicalization hard-arms (guard level) ──────────────


class TestGuardCanonicalization:
    def test_trailing_slash_live_refused(self, fake_home):
        make_live_shaped(fake_home)
        r = run_guard(
            "uninstall", fake_home,
            install_dir=f"{fake_home}/agents-ensemble///",
        )
        assert r.returncode == GUARD_REFUSE_RC

    def test_symlink_alias_of_live_refused(self, fake_home):
        make_live_shaped(fake_home)
        alias = fake_home / "alias-to-live"
        alias.symlink_to(fake_home / "agents-ensemble")
        r = run_guard("uninstall", fake_home, install_dir=str(alias))
        assert r.returncode == GUARD_REFUSE_RC

    def test_dotslash_spelling_refused(self, fake_home):
        make_live_shaped(fake_home)
        r = run_guard(
            "uninstall", fake_home,
            install_dir=f"{fake_home}/./agents-ensemble",
        )
        assert r.returncode == GUARD_REFUSE_RC

    def test_layer1_home_shape_refused(self, tmp_path):
        """Layer-1 shape (ensemble-promote runs with HOME=<install dir>):
        HOME itself ENDS WITH agents-ensemble → HOME IS the live dir, and
        INSTALL_DIR=HOME must be gated."""
        live_home = tmp_path / "layer1-home" / "agents-ensemble"
        live_home.mkdir(parents=True)
        r = run_guard("uninstall", live_home, install_dir=str(live_home))
        assert r.returncode == GUARD_REFUSE_RC
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr

    def test_layer1_demo_home_shape_refused(self, tmp_path):
        """Layer-1 demo shape: HOME itself ends with agents-ensemble-demo."""
        demo_home = tmp_path / "layer1-demo-home" / "agents-ensemble-demo"
        demo_home.mkdir(parents=True)
        r = run_guard("uninstall", demo_home, install_dir=str(demo_home))
        assert r.returncode == GUARD_REFUSE_RC
        assert "ENSEMBLE_INSTALL_DEMO" in r.stderr

    def test_wrong_confirm_var_does_not_open_live(self, fake_home):
        """Per-path vars are NOT interchangeable: confirming demo must never
        implicitly confirm live."""
        live = make_live_shaped(fake_home)
        r = run_guard(
            "uninstall", fake_home, install_dir=str(live),
            extra_env={"ENSEMBLE_INSTALL_DEMO": "1"},
        )
        assert r.returncode == GUARD_REFUSE_RC
        assert sentinel_of(live).exists()

    def test_sudo_user_home_candidates_stay_gated(self, fake_home):
        """SUDO_USER arm (operator directive 1b hardening): under sudo HOME
        resets to the target user's home — SUDO_USER's home candidates must
        stay gated. Here SUDO_USER resolves to /home/<user> (getent/dscl
        absent in the scrubbed env) — the fake-home live is STILL gated via
        $HOME, and an explicit /home-shaped live spelling is gated too."""
        live = make_live_shaped(fake_home)
        r = run_guard(
            "uninstall", fake_home, install_dir=str(live),
            extra_env={"SUDO_USER": "someop"},
        )
        assert r.returncode == GUARD_REFUSE_RC

    def test_guard_pass_silently_on_safe_dir(self, fake_home, tmp_path):
        sandbox = tmp_path / "plain"
        sandbox.mkdir()
        r = run_guard("install", fake_home, install_dir=str(sandbox))
        assert r.returncode == 0
        assert r.stdout == "" and r.stderr == ""


class TestGuardLevelProceedArms:
    """Guard-level proceed proofs for targets whose recipes may run the
    stop script (install, stop) or a toolchain (install-deps) — the
    carve-out forbids executing those recipes even under a fake home."""

    def test_install_proceeds_with_confirm_at_guard_level(self, fake_home):
        make_live_shaped(fake_home)
        r = run_guard(
            "install", fake_home, install_dir=str(fake_home / "agents-ensemble"),
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r.returncode == 0
        assert sentinel_of(fake_home / "agents-ensemble").exists()  # guard never writes

    def test_stop_proceeds_with_confirm_at_guard_level(self, fake_home):
        make_live_shaped(fake_home)
        r = run_guard(
            "stop", fake_home, install_dir=str(fake_home / "agents-ensemble"),
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r.returncode == 0

    def test_install_deps_proceeds_with_confirm_at_guard_level(self, fake_home):
        demo = fake_home / "agents-ensemble-demo"
        demo.mkdir()
        r = run_guard(
            "install-deps", fake_home, install_dir=str(demo),
            extra_env={"ENSEMBLE_INSTALL_DEMO": "1"},
        )
        assert r.returncode == 0


# ─────────────────────────────── sudo arm (addendum 1a) ─────────────────────


def _sudo_non_interactive_available() -> bool:
    """Probe `sudo -n true` — never let a test hang on a password prompt."""
    try:
        probe = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


class TestSudoArm:
    """Operator directive 1a: the sudo-correct form must ACTUALLY work with
    the implementation. `sudo env VAR=… HOME=… make …` — vars set via env
    survive sudo's env_reset without any sudoers env_keep entry. Gated
    behind `sudo -n` (skip-with-reason when sudo would prompt)."""

    @pytest.mark.skipif(
        not _sudo_non_interactive_available(),
        reason="sudo -n unavailable (would prompt for a password) — skipping "
        "the real-sudo arm; the sudo-correct FORM is still asserted in "
        "TestRefusalContent and the SUDO_USER candidate arm in "
        "TestGuardCanonicalization",
    )
    def test_sudo_form_refuses_without_confirm_and_proceeds_with_it(self, fake_home):
        live = make_live_shaped(fake_home)
        env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_VARS}
        # refusal arm: HOME reset into the sandbox, no confirm var
        refused = subprocess.run(
            ["sudo", "-n", "env",
             f"HOME={fake_home}", f"INSTALL_DIR={live}",
             "make", "-s", "uninstall"],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=60,
        )
        assert refused.returncode != 0
        assert "ENSEMBLE_INSTALL_LIVE" in refused.stderr
        assert sentinel_of(live).exists(), "sudo'd ungated rm -rf ran — SUDO ARM BROKEN"
        # proceed arm: the exact sudo-correct form the refusal prints
        try:
            proceeded = subprocess.run(
                ["sudo", "-n", "env",
                 f"HOME={fake_home}", f"INSTALL_DIR={live}",
                 "ENSEMBLE_INSTALL_LIVE=1",
                 "make", "-s", "uninstall"],
                cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=60,
            )
            assert proceeded.returncode == 0, proceeded.stderr
            assert not live.exists()
        finally:
            if live.exists():
                subprocess.run(
                    ["sudo", "-n", "rm", "-rf", str(live)],
                    timeout=30, check=False,
                )


# ─────────────────────────── remaining mutating targets ─────────────────────


class TestRemainingMutatingTargets:
    """Every mutating target from the recon inventory has at least one
    make-level refusal arm + guard-level pass (proceed) arm."""

    @pytest.mark.parametrize("target", ["install-deps", "plist-install", "watchdog-install"])
    def test_inert_refusal(self, fake_home, target):
        r = run_make([target], fake_home)
        assert r.returncode != 0
        assert "INSTALL_DIR is not set" in r.stderr

    @pytest.mark.parametrize("target", ["install-deps", "plist-install", "watchdog-install"])
    def test_live_without_confirm_refused(self, fake_home, target):
        live = make_live_shaped(fake_home)
        r = run_make([target, f"INSTALL_DIR={live}"], fake_home)
        assert r.returncode != 0
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr
        assert sentinel_of(live).exists()

    @pytest.mark.parametrize("target", ["install-deps", "plist-install", "watchdog-install"])
    def test_guard_pass_with_confirm(self, fake_home, target):
        live = make_live_shaped(fake_home)
        r = run_guard(
            target, fake_home, install_dir=str(live),
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r.returncode == 0

    def test_stop_by_port_refuses_without_confirm(self, fake_home):
        live = make_live_shaped(fake_home)
        r = run_make(["stop-by-port", f"INSTALL_DIR={live}"], fake_home)
        assert r.returncode != 0
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr

    def test_dev_and_start_inherit_stop_gate(self, fake_home):
        """start (stop prereq) and dev (stop in its chain) inherit the gate:
        bare invocation refuses via stop's guard line before start.sh runs."""
        for target in ("start", "dev"):
            r = run_make([target], fake_home)
            assert r.returncode != 0, target
            assert "INSTALL_DIR is not set" in r.stderr, target


# ─────────────────────────────── static wiring pins ─────────────────────────


class TestMakefileWiring:
    """Structural pins: the guard line precedes the first side-effecting
    line in every mutating recipe, and install has it as first prereq too."""

    def test_guard_script_exists_and_is_bash32_safe_enough(self):
        assert GUARD.exists()
        head = GUARD.read_text().splitlines()[0]
        assert head == "#!/bin/bash"
        r = subprocess.run(["bash", "-n", str(GUARD)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    def test_every_mutating_recipe_starts_with_guard_line(self):
        text = MAKEFILE.read_text()
        for target in (
            "stop:", "stop-by-port:", "install-deps:", "plist-install:",
            "watchdog-install:", "uninstall:",
        ):
            block = text.split(f"\n{target}\n", 1)[1]
            first_line = block.split("\n", 1)[0]
            assert "$(GUARD_SCRIPT) --target" in first_line, target

    def test_install_has_guard_as_first_prereq_and_first_line(self):
        text = MAKEFILE.read_text()
        pin = (
            "install: ENS_GUARD_TARGET = install\n"
            "install: install-dir-guard pyinstaller\n"
            "\t@bash $(GUARD_SCRIPT) --target install\n"
        )
        assert pin in text, "install: guard prereq must precede pyinstaller, " \
            "and the recipe's first line must be the belt guard invocation"

    def test_inert_default_and_export_present(self):
        text = MAKEFILE.read_text()
        assignment_lines = [ln for ln in text.splitlines() if ln.startswith("INSTALL_DIR")]
        assert "INSTALL_DIR ?=" in assignment_lines, "the default must be EMPTY (inert)"
        assert "export INSTALL_DIR" in text

    def test_stop_script_never_executed_by_this_suite(self):
        """Documentation-pin: this suite never executes the stop script;
        the only make-level proceed target is the stop-free uninstall."""
        text = STOP_SCRIPT.read_text()  # read-only reference
        assert text.startswith("#!/bin/bash")

    def test_port_sink_quoted_in_stop_recipes(self, fake_home):
        """D1 pin (PROD_PORT_FALLBACK sink): the port must reach the stop
        script as a QUOTED argument.

        ``$(PROD_PORT_FALLBACK)`` is the raw text after ``PORT=`` in the
        repo's .env.prod (Makefile:75) — a repo-controlled file. Unquoted at
        the sink it is a word-splitting / command-separator surface: a
        ``PORT=9797; echo x > f`` line would run as a command. The guard
        refusal aborts the recipe BEFORE the sink, so this is
        defence-in-depth (NOT a guard bypass); the threat model is an
        operator-controlled repo file.

        Behavioral pin: ``make -n`` PRINTS the recipe without executing it,
        so the printed stop-script invocation must carry the port QUOTED,
        with the argv order unchanged (install dir, then port — two
        arguments). The port value is pinned deterministically by
        overriding ENV_PROD_FILE (the file Makefile:75 reads), so the
        assertion does not depend on whatever .env.prod exists."""
        sandbox = fake_home / "sbx"
        sandbox.mkdir()
        port_file = fake_home / "port.env"
        port_file.write_text("PORT=9797\n")
        for target in ("stop", "stop-by-port", "install"):
            r = run_make(
                ["-n", target, f"INSTALL_DIR={sandbox}", f"ENV_PROD_FILE={port_file}"],
                fake_home,
            )
            assert r.returncode == 0, (target, r.stdout, r.stderr)
            calls = [
                ln for ln in r.stdout.splitlines()
                if "stop-ensemble.sh" in ln and "bash" in ln
            ]
            assert calls, f"{target}: no stop-ensemble.sh invocation printed"
            for ln in calls:
                assert f'"{sandbox}"' in ln, (target, ln)
                assert '"9797"' in ln, f"{target}: port sink is NOT quoted: {ln}"
                assert ln.index(f'"{sandbox}"') < ln.index('"9797"'), (target, ln)


# ═══════════════════════════════════════════════════════════════════════════
# P0 SECURITY HOTFIX MATRIX — F-01 … F-05 refusal rows
#
# The guard script (scripts/install-dir-guard.sh) grew from 327 to 719 lines
# of refusal arms. Every row below is a case the ARM must refuse BEFORE any
# side effect. The five findings pinned here:
#
#   F-01  RAW-VALUE / UNVERIFIABLE-SPELLING ARM — `make` substitutes
#         $(INSTALL_DIR) TEXTUALLY into the recipe and the shell re-parses it,
#         so a spelling the guard cannot verify (whitespace, quotes, shell
#         metacharacters, glob characters, or a non-normalized path) must
#         never reach the recipe. STRUCTURAL: no confirm var opens it.
#   F-02  HOME ARM — the protected set is HOME-derived; an unset/empty HOME
#         SILENTLY SHRINKS it (the live dir stops being classified and the
#         guard would exit 0). Fail CLOSED instead.
#   F-03  MULTI-ENTRY LIVE SET — ENSEMBLE_LIVE_INSTALL_DIRS is PATH-syntax
#         (colon-separated); the old quoted command substitution collapsed
#         the whole list into ONE word, so no extension entry ever matched.
#   F-04  CONTAINMENT ARM — `rm -rf` of an ANCESTOR of a protected install dir
#         destroys the protected tree; `rm -rf` of a DESCENDANT is the same
#         incident one level down. STRUCTURAL: no confirm var opens it.
#   F-05  CASE / PATH RESOLUTION — case-folded comparison on Darwin and
#         longest-existing-prefix PHYSICAL resolution, so an uppercase
#         spelling, a non-existent intermediate, or a relative spelling of a
#         live dir still classifies as live.
#
# REGRESSION NAMES pinned by this section: TestTildeSpellingGate (the
# unverifiable-spelling first-line shape is SHARED with the tilde arm — both
# keep the same wording), TestInertDefault / TestLiveGate / TestDemoGate
# (the LIVE / DEMO / inert-default refusals are UNCHANGED and keep passing),
# TestExtensibleLiveSet (single-entry behavior preserved), and
# TestGuardCanonicalization (the symlink alias is still refused).
#
# SAFETY (mission rules, honored here verbatim): every path here lives under
# the pytest tmp sandbox (the fake HOME is <tmp>/home); the LIVE install, the
# demo install and /opt/ensemble are NEVER written; scripts/stop-ensemble.sh
# is NEVER executed — the only make-level target used is the stop-free
# `uninstall` recipe (echo / rm -rf / echo).
# ═══════════════════════════════════════════════════════════════════════════

#: First-line anchor of the unverifiable-spelling refusal (F-01). The tilde
#: arm (TestTildeSpellingGate) shares this exact shape.
RAW_VALUE_FIRST_LINE = "REFUSING — INSTALL_DIR spelling"
RAW_VALUE_TAIL = "cannot be verified safely."

#: The two detail-line anchors of the F-01 arm — WHICH reason fired.
DETAIL_METACHAR = "The path contains whitespace, quotes, shell metacharacters"
DETAIL_NOT_NORMALIZED = "The path is not in normalized form"

#: HOME arm (F-02) first line — fail CLOSED, never a silent shrink.
HOME_ARM_FIRST_LINE = (
    "REFUSING — HOME is unset or empty, so the protected install set "
    "cannot be determined."
)

#: Containment arm (F-04) — the relation word and the STRUCTURAL marker.
CONTAINS_ARM = "which CONTAINS the protected install dir"
INSIDE_ARM = "which is INSIDE the protected install dir"
STRUCTURAL_LINE = "This refusal is STRUCTURAL: no confirm var opens it"

#: Live / demo classification wording (UNCHANGED by this hotfix).
LIVE_ARM = "REFUSING — INSTALL_DIR resolves to a LIVE install dir"
DEMO_ARM = "REFUSING — INSTALL_DIR resolves to the DEMO install"

#: Absolute-path arm (F6) — a RELATIVE INSTALL_DIR is refused outright.
#: The first line is "REFUSING — INSTALL_DIR '<spelling>' is not an absolute
#: path."; the tail anchor below is the wording that distinguishes this arm
#: from the raw-value arm ("cannot be verified safely.") and from the live /
#: demo classification wording.
ABSOLUTE_ARM_TAIL = "is not an absolute path."


def absolute_first_line(spelling: str) -> str:
    """The exact first line of the absolute-path refusal for a spelling."""
    return f"REFUSING — INSTALL_DIR '{spelling}' is not an absolute path."

#: F-01 case table: (case-id, value template, detail anchor, make template).
#:
#: The value templates are formatted with `sbx` (the SAFE sandbox dir inside
#: the fake home) and `home` (the fake home itself). Every value points at a
#: SAFE dir — never at a protected one — so the ONLY reason for a refusal is
#: the raw-value arm itself.
#:
#: The make template is None when MAKE rewrites the value before the guard
#: ever sees it (make's `override INSTALL_DIR := $(patsubst …)` collapses
#: whitespace, and `$(INSTALL_DIR)` expands `$H` in a `$HOME` spelling), so
#: those spellings are asserted at GUARD level only. The `$` case reaches
#: make verbatim as `$$` (make's escape for a literal `$`) and IS asserted at
#: make level.
RAW_VALUE_CASES = (
    ("trailing-space", "{sbx} ", DETAIL_METACHAR, None),
    ("leading-space", " {sbx}", DETAIL_METACHAR, None),
    ("double-quote", '{sbx}"q', DETAIL_METACHAR, '{sbx}"q'),
    ("single-quote", "{sbx}'q", DETAIL_METACHAR, "{sbx}'q"),
    ("semicolon", "{sbx};x", DETAIL_METACHAR, "{sbx};x"),
    ("pipe", "{sbx}|x", DETAIL_METACHAR, "{sbx}|x"),
    ("ampersand", "{sbx}&x", DETAIL_METACHAR, "{sbx}&x"),
    ("dollar", "{sbx}$HOME", DETAIL_METACHAR, "{sbx}$$HOME"),
    ("backtick", "{sbx}`x`", DETAIL_METACHAR, "{sbx}`x`"),
    ("less-than", "{sbx}<x", DETAIL_METACHAR, "{sbx}<x"),
    ("greater-than", "{sbx}>x", DETAIL_METACHAR, "{sbx}>x"),
    ("open-paren", "{sbx}(x", DETAIL_METACHAR, "{sbx}(x"),
    ("close-paren", "{sbx})x", DETAIL_METACHAR, "{sbx})x"),
    ("newline", "{sbx}\nx", DETAIL_METACHAR, "{sbx}\nx"),
    ("tab", "{sbx}\tx", DETAIL_METACHAR, "{sbx}\tx"),
    ("glob-star", "{sbx}*", DETAIL_METACHAR, "{sbx}*"),
    ("double-slash", "{home}//sandbox", DETAIL_NOT_NORMALIZED, "{home}//sandbox"),
    ("dot-slash-segment", "{home}/./sandbox", DETAIL_NOT_NORMALIZED, "{home}/./sandbox"),
    ("trailing-slash", "{sbx}/", DETAIL_NOT_NORMALIZED, "{sbx}/"),
)

#: The subset make passes through VERBATIM (asserted at make level too).
MAKE_VERBATIM_RAW_CASES = tuple(c for c in RAW_VALUE_CASES if c[3] is not None)

RAW_VALUE_IDS = [c[0] for c in RAW_VALUE_CASES]
MAKE_VERBATIM_RAW_IDS = [c[0] for c in MAKE_VERBATIM_RAW_CASES]


def _safe_sandbox(home: Path, name: str = "sandbox") -> Path:
    """A SAFE (never protected) dir inside the fake home, with a sentinel."""
    sbx = home / name
    sbx.mkdir(parents=True, exist_ok=True)
    (sbx / "SENTINEL").write_text("do-not-delete")
    return sbx


def _ext_live_dir(root: Path, name: str) -> Path:
    """A fake EXTENSION live dir (a sibling of the fake home) with a sentinel."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SENTINEL").write_text("do-not-delete")
    return d


def _assert_make_refused(result: subprocess.CompletedProcess, context: str) -> None:
    """The make-level refusal shape: non-zero rc and the guard's code 78."""
    assert result.returncode != 0, context
    assert result.returncode == MAKE_REFUSED_RC, (context, result.returncode)
    assert "Error 78" in result.stderr, (context, result.stderr)


class TestRawValueArm:
    """F-01 — UNVERIFIABLE SPELLING (the raw-value arm).

    `make` substitutes $(INSTALL_DIR) TEXTUALLY into the recipe and the shell
    then RE-PARSES it: a trailing space is stripped by word splitting (the
    recipe then deletes the tree the guard verified as a different path) and
    ';' / '|' are true command separators (arbitrary commands run inside a
    make recipe). The guard compares the NORMALIZED path while the recipe
    consumes the RAW one — so a spelling that differs from its normalized
    form, or that carries characters a shell would re-parse, is REFUSED
    STRUCTURALLY (no confirm var opens it).

    The arm is one shared refusal (`_refuse_unverifiable`) with the tilde
    arm, so the first-line shape asserted here is the SAME one
    TestTildeSpellingGate pins — the two must not drift apart.
    """

    @pytest.mark.parametrize("case", RAW_VALUE_CASES, ids=RAW_VALUE_IDS)
    def test_raw_value_spelling_refused_at_guard_level(self, fake_home, case):
        """Every unverifyable spelling refuses at rc 78 with the shared
        wording — and stays refused WITH the confirm var set (structural)."""
        case_id, template, detail, _make_template = case
        live = make_live_shaped(fake_home)
        sbx = _safe_sandbox(fake_home)
        value = template.format(sbx=str(sbx), home=str(fake_home))

        r = run_guard("uninstall", fake_home, install_dir=value)
        assert r.returncode == GUARD_REFUSE_RC, (case_id, r.stderr)
        assert RAW_VALUE_FIRST_LINE in r.stderr, (case_id, r.stderr)
        assert RAW_VALUE_TAIL in r.stderr, (case_id, r.stderr)
        assert detail in r.stderr, (case_id, r.stderr)
        # the guard never writes — but the SENTINELS prove nothing ran
        assert sentinel_of(live).exists(), case_id
        assert sentinel_of(sbx).exists(), case_id

        # STRUCTURAL: ENSEMBLE_INSTALL_LIVE=1 does NOT open the raw-value arm
        r_conf = run_guard(
            "uninstall", fake_home, install_dir=value,
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r_conf.returncode == GUARD_REFUSE_RC, (case_id, r_conf.stderr)
        assert RAW_VALUE_TAIL in r_conf.stderr, (case_id, r_conf.stderr)
        assert sentinel_of(live).exists(), case_id
        assert sentinel_of(sbx).exists(), case_id

    @pytest.mark.parametrize("case", MAKE_VERBATIM_RAW_CASES, ids=MAKE_VERBATIM_RAW_IDS)
    def test_raw_value_spelling_refused_at_make_level(self, fake_home, case):
        """The make-level refusal for every spelling make passes through
        VERBATIM: non-zero rc, the guard's "Error 78", and the sandbox
        sentinel still there (the rm -rf recipe never ran)."""
        case_id, _template, detail, make_template = case
        live = make_live_shaped(fake_home)
        sbx = _safe_sandbox(fake_home)
        value = make_template.format(sbx=str(sbx), home=str(fake_home))

        r = run_make(["uninstall", f"INSTALL_DIR={value}"], fake_home)
        _assert_make_refused(r, case_id)
        assert RAW_VALUE_FIRST_LINE in r.stderr, (case_id, r.stderr)
        assert RAW_VALUE_TAIL in r.stderr, (case_id, r.stderr)
        assert detail in r.stderr, (case_id, r.stderr)
        assert sentinel_of(live).exists(), case_id
        assert sentinel_of(sbx).exists(), case_id

    def test_make_level_metachar_payload_never_executes(self, fake_home):
        """The command-injection shape: a `;`-separated payload in
        INSTALL_DIR must not run. The guard refuses BEFORE the recipe, so the
        injected command never executes and the sentinel survives."""
        live = make_live_shaped(fake_home)
        sbx = _safe_sandbox(fake_home)
        marker = fake_home / "INJECTED"
        payload = f"{sbx}; echo INJECTED > {marker}"

        r = run_make(["uninstall", f"INSTALL_DIR={payload}"], fake_home)
        _assert_make_refused(r, "semicolon-payload")
        assert RAW_VALUE_TAIL in r.stderr, r.stderr
        assert not marker.exists(), "the injected command RAN — guard ordering broken"
        assert sentinel_of(live).exists()
        assert sentinel_of(sbx).exists()


class TestHomeArm:
    """F-02 — HOME unset or empty must fail CLOSED.

    The protected live/demo set is derived from $HOME (and SUDO_USER's home).
    With HOME unset the $HOME candidates would be SILENTLY DROPPED, the
    protected set would shrink, the live install would stop being recognised
    — and the guard would exit 0 and let the recipe run. The arm refuses
    instead, with no unbound-variable crash (the script captures every env
    value up front with ${var:-}).
    """

    def test_home_empty_refused_at_guard_level(self, fake_home):
        """HOME="" → the protected set cannot be determined → refuse (78)."""
        live = make_live_shaped(fake_home)
        sbx = _safe_sandbox(fake_home)
        r = run_guard(
            "uninstall", fake_home, install_dir=str(sbx), extra_env={"HOME": ""},
        )
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert HOME_ARM_FIRST_LINE in r.stderr, r.stderr
        assert sentinel_of(live).exists()
        assert sentinel_of(sbx).exists()

    def test_home_unset_refused_at_guard_level(self, fake_home):
        """HOME absent from the child env → refuse (78), NOT 0 — and with no
        unbound-variable crash (set -u safety)."""
        live = make_live_shaped(fake_home)
        sbx = _safe_sandbox(fake_home)
        r = run_guard(
            "uninstall", fake_home, install_dir=str(sbx), drop_home=True,
        )
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert HOME_ARM_FIRST_LINE in r.stderr, r.stderr
        assert "unbound variable" not in r.stderr, r.stderr
        assert sentinel_of(live).exists()
        assert sentinel_of(sbx).exists()

    def test_home_empty_refused_at_make_level(self, fake_home):
        """make-level: HOME="" → the guard refuses before the recipe."""
        live = make_live_shaped(fake_home)
        sbx = _safe_sandbox(fake_home)
        r = run_make(
            ["uninstall", f"INSTALL_DIR={sbx}"], fake_home, extra_env={"HOME": ""},
        )
        _assert_make_refused(r, "home-empty")
        assert HOME_ARM_FIRST_LINE in r.stderr, r.stderr
        assert sentinel_of(live).exists()
        assert sentinel_of(sbx).exists()

    def test_home_unset_refused_at_make_level(self, fake_home):
        """make-level: HOME absent → the guard refuses before the recipe."""
        live = make_live_shaped(fake_home)
        sbx = _safe_sandbox(fake_home)
        r = run_make(
            ["uninstall", f"INSTALL_DIR={sbx}"], fake_home, drop_home=True,
        )
        _assert_make_refused(r, "home-unset")
        assert HOME_ARM_FIRST_LINE in r.stderr, r.stderr
        assert "unbound variable" not in r.stderr, r.stderr
        assert sentinel_of(live).exists()
        assert sentinel_of(sbx).exists()


class TestMultiEntryLiveSet:
    """F-03 — ENSEMBLE_LIVE_INSTALL_DIRS is PATH-syntax (colon-separated).

    The old implementation used a QUOTED command substitution
    (`for ext in /opt/ensemble "$(printf '%s' "$VAR" | tr ':' '\n')"`), which
    collapsed the whole list into ONE word — so no extension entry ever
    matched and the documented knob protected NOTHING. The list is now split
    per entry, trimmed of surrounding whitespace, empty entries skipped and
    globbing disabled. Regression guard: a SINGLE entry behaves exactly as
    before (TestExtensibleLiveSet).
    """

    @pytest.mark.parametrize("entry", ["extA", "extB"])
    def test_two_entry_live_set_protects_both(self, fake_home, tmp_path, entry):
        """Two colon-separated entries → BOTH are protected (rc 78)."""
        ext_a = _ext_live_dir(tmp_path, "extA")
        ext_b = _ext_live_dir(tmp_path, "extB")
        target = {"extA": ext_a, "extB": ext_b}[entry]
        env = {"ENSEMBLE_LIVE_INSTALL_DIRS": f"{ext_a}:{ext_b}"}

        r = run_guard("uninstall", fake_home, install_dir=str(target), extra_env=env)
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert LIVE_ARM in r.stderr, r.stderr
        assert sentinel_of(target).exists()

        m = run_make(["uninstall", f"INSTALL_DIR={target}"], fake_home, extra_env=env)
        _assert_make_refused(m, entry)
        assert LIVE_ARM in m.stderr, m.stderr
        assert sentinel_of(target).exists()

    @pytest.mark.parametrize("entry", ["extA", "extB", "extC"])
    def test_three_entry_live_set_protects_all(self, fake_home, tmp_path, entry):
        """Three colon-separated entries → all three are protected (rc 78)."""
        dirs = {n: _ext_live_dir(tmp_path, n) for n in ("extA", "extB", "extC")}
        env = {"ENSEMBLE_LIVE_INSTALL_DIRS": ":".join(str(dirs[n]) for n in ("extA", "extB", "extC"))}
        target = dirs[entry]

        r = run_guard("uninstall", fake_home, install_dir=str(target), extra_env=env)
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert LIVE_ARM in r.stderr, r.stderr
        assert sentinel_of(target).exists()

        m = run_make(["uninstall", f"INSTALL_DIR={target}"], fake_home, extra_env=env)
        _assert_make_refused(m, entry)
        assert LIVE_ARM in m.stderr, m.stderr
        assert sentinel_of(target).exists()

    @pytest.mark.parametrize("entry", ["extA", "extB"])
    def test_whitespace_padded_entries_behave_identically(self, fake_home, tmp_path, entry):
        """Whitespace-padded entries (" A : B ") are TRIMMED → identical to
        the unpadded spelling (both entries stay protected)."""
        ext_a = _ext_live_dir(tmp_path, "extA")
        ext_b = _ext_live_dir(tmp_path, "extB")
        env = {"ENSEMBLE_LIVE_INSTALL_DIRS": f" {ext_a} : {ext_b} "}
        target = {"extA": ext_a, "extB": ext_b}[entry]

        r = run_guard("uninstall", fake_home, install_dir=str(target), extra_env=env)
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert LIVE_ARM in r.stderr, r.stderr
        assert sentinel_of(target).exists()

        m = run_make(["uninstall", f"INSTALL_DIR={target}"], fake_home, extra_env=env)
        _assert_make_refused(m, entry)
        assert LIVE_ARM in m.stderr, m.stderr
        assert sentinel_of(target).exists()

    def test_single_entry_behavior_unchanged(self, fake_home, tmp_path):
        """REGRESSION: a single entry behaves exactly as before — refused
        without the confirm var, opened with it."""
        ext = _ext_live_dir(tmp_path, "extA")
        env = {"ENSEMBLE_LIVE_INSTALL_DIRS": str(ext)}

        r = run_guard("uninstall", fake_home, install_dir=str(ext), extra_env=env)
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert LIVE_ARM in r.stderr, r.stderr
        assert sentinel_of(ext).exists()

        m = run_make(["uninstall", f"INSTALL_DIR={ext}"], fake_home, extra_env=env)
        _assert_make_refused(m, "single-entry")
        assert LIVE_ARM in m.stderr, m.stderr
        assert sentinel_of(ext).exists()

        r_conf = run_guard(
            "uninstall", fake_home, install_dir=str(ext),
            extra_env={**env, "ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r_conf.returncode == 0, r_conf.stderr
        assert sentinel_of(ext).exists()  # the guard never writes

    def test_glob_entry_matched_literally_never_expanded(self, fake_home, tmp_path):
        """REGRESSION: an entry containing '*' is matched LITERALLY — the
        guard must never glob-expand it, so a decoy path that the glob would
        have matched is NOT refused."""
        ext_a = _ext_live_dir(tmp_path, "extA")
        decoy = _ext_live_dir(tmp_path, "extB")  # would match the glob "ext*"
        env = {"ENSEMBLE_LIVE_INSTALL_DIRS": f"{tmp_path}/ext*"}

        r = run_guard("uninstall", fake_home, install_dir=str(decoy), extra_env=env)
        assert r.returncode == 0, r.stderr
        assert "REFUSING" not in r.stderr, r.stderr
        assert sentinel_of(decoy).exists()

        # the LITERAL entry spelling is itself unverifyable (it carries a glob
        # character) — it is refused as a spelling, never as a live dir
        r_literal = run_guard(
            "uninstall", fake_home, install_dir=f"{tmp_path}/ext*", extra_env=env,
        )
        assert r_literal.returncode == GUARD_REFUSE_RC, r_literal.stderr
        assert RAW_VALUE_TAIL in r_literal.stderr, r_literal.stderr
        assert sentinel_of(ext_a).exists()
        assert sentinel_of(decoy).exists()

        # make level: the decoy is NOT refused either — the recipe runs and
        # removes it (proof the path was genuinely ALLOWED, not refused)
        m = run_make(["uninstall", f"INSTALL_DIR={decoy}"], fake_home, extra_env=env)
        assert m.returncode == 0, m.stderr
        assert "REFUSING" not in m.stderr, m.stderr
        assert not decoy.exists()
        assert sentinel_of(ext_a).exists()


class TestContainmentArm:
    """F-04 — ANCESTOR / DESCENDANT containment (STRUCTURAL).

    A recursive delete of an ANCESTOR of a protected install dir destroys the
    protected tree ($HOME itself, /opt, …); a recursive delete of a
    DESCENDANT is the same incident one level down (<live>/data). Both are
    refused regardless of any confirm var — the confirm vars open a path that
    IS the protected install dir, not one that swallows it. The false-positive
    guard matters just as much: a plain subdir of $HOME is a NORMAL install
    location and must stay allowed.
    """

    def test_home_itself_refused(self, fake_home):
        """INSTALL_DIR=$HOME → CONTAINS the protected live dir."""
        live = make_live_shaped(fake_home)
        r = run_guard("uninstall", fake_home, install_dir=str(fake_home))
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert CONTAINS_ARM in r.stderr, r.stderr
        assert STRUCTURAL_LINE in r.stderr, r.stderr
        assert sentinel_of(live).exists()

        # STRUCTURAL: the confirm var does not open it
        r_conf = run_guard(
            "uninstall", fake_home, install_dir=str(fake_home),
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r_conf.returncode == GUARD_REFUSE_RC, r_conf.stderr
        assert CONTAINS_ARM in r_conf.stderr, r_conf.stderr
        assert sentinel_of(live).exists()

        m = run_make(["uninstall", f"INSTALL_DIR={fake_home}"], fake_home)
        _assert_make_refused(m, "home-itself")
        assert CONTAINS_ARM in m.stderr, m.stderr
        assert sentinel_of(live).exists()

    def test_ancestor_of_home_refused(self, fake_home, tmp_path):
        """An ancestor of $HOME/agents-ensemble (here: the tmp root that
        holds the fake home) → CONTAINS the protected live dir."""
        live = make_live_shaped(fake_home)
        r = run_guard("uninstall", fake_home, install_dir=str(tmp_path))
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert CONTAINS_ARM in r.stderr, r.stderr
        assert STRUCTURAL_LINE in r.stderr, r.stderr
        assert sentinel_of(live).exists()

        m = run_make(["uninstall", f"INSTALL_DIR={tmp_path}"], fake_home)
        _assert_make_refused(m, "ancestor-of-home")
        assert CONTAINS_ARM in m.stderr, m.stderr
        assert sentinel_of(live).exists()

    def test_descendant_of_live_refused(self, fake_home):
        """A STRICT descendant of the live dir (<live>/data) → INSIDE."""
        live = make_live_shaped(fake_home)
        data = live / "data"
        data.mkdir()
        (data / "SENTINEL").write_text("do-not-delete")
        r = run_guard("uninstall", fake_home, install_dir=str(data))
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert INSIDE_ARM in r.stderr, r.stderr
        assert STRUCTURAL_LINE in r.stderr, r.stderr
        assert sentinel_of(live).exists()
        assert (data / "SENTINEL").exists()

        r_conf = run_guard(
            "uninstall", fake_home, install_dir=str(data),
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r_conf.returncode == GUARD_REFUSE_RC, r_conf.stderr
        assert INSIDE_ARM in r_conf.stderr, r_conf.stderr

        m = run_make(["uninstall", f"INSTALL_DIR={data}"], fake_home)
        _assert_make_refused(m, "descendant-of-live")
        assert INSIDE_ARM in m.stderr, m.stderr
        assert sentinel_of(live).exists()

    def test_descendant_of_demo_refused(self, fake_home):
        """A STRICT descendant of the DEMO dir → INSIDE (demo is protected
        too — confirming live must not open a demo descendant)."""
        demo = fake_home / "agents-ensemble-demo"
        demo.mkdir()
        (demo / "SENTINEL").write_text("do-not-delete")
        data = demo / "data"
        data.mkdir()
        r = run_guard("uninstall", fake_home, install_dir=str(data))
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert INSIDE_ARM in r.stderr, r.stderr
        assert STRUCTURAL_LINE in r.stderr, r.stderr
        assert sentinel_of(demo).exists()

        m = run_make(["uninstall", f"INSTALL_DIR={data}"], fake_home)
        _assert_make_refused(m, "descendant-of-demo")
        assert INSIDE_ARM in m.stderr, m.stderr
        assert sentinel_of(demo).exists()

    def test_plain_subdir_of_home_still_allowed(self, fake_home):
        """FALSE-POSITIVE GUARD: a plain subdir of $HOME that is NOT a
        protected dir ($HOME/sandbox) is a NORMAL install location and must
        stay ALLOWED (rc 0) — the containment arm must not swallow it."""
        live = make_live_shaped(fake_home)
        sbx = _safe_sandbox(fake_home)

        r = run_guard("uninstall", fake_home, install_dir=str(sbx))
        assert r.returncode == 0, r.stderr
        assert "REFUSING" not in r.stderr, r.stderr
        assert sentinel_of(sbx).exists()

        m = run_make(["uninstall", f"INSTALL_DIR={sbx}"], fake_home)
        assert m.returncode == 0, m.stderr
        assert "REFUSING" not in m.stderr, m.stderr
        assert "Uninstall complete" in m.stdout
        assert not sbx.exists()  # the recipe really ran on the sandbox
        assert sentinel_of(live).exists()


class TestCaseAndPathResolutionArm:
    """F-05 — case-folded comparison + longest-existing-prefix PHYSICAL
    resolution.

    An uppercase spelling of a live dir (Darwin/APFS is case-insensitive), a
    spelling whose INTERMEDIATE does not exist (<home>/ghost/../live) and a
    symlink alias must all still classify as live. The physical resolution
    uses /bin/pwd -P (BSD) — bash's builtin `pwd -P` echoes the TYPED
    casing, which compares unequal to the real tree.

    A RELATIVE spelling is no longer part of this matrix: the absolute-path
    arm (F6, TestAbsolutePathArm) refuses every relative spelling before
    the classification can run, so the relative-live row below is now
    refused one arm earlier.
    """

    @pytest.mark.skipif(
        sys.platform != "darwin",
        reason="case-folded comparison is Darwin-only (APFS/HFS+ are "
        "case-insensitive by default); on a case-sensitive filesystem the "
        "uppercase spelling is a DIFFERENT path and must not be refused",
    )
    def test_darwin_case_variant_of_live_refused(self, fake_home):
        """The UPPERCASE spelling of the live dir still classifies as live."""
        live = make_live_shaped(fake_home)
        upper = str(live).upper()
        assert upper != str(live)

        r = run_guard("uninstall", fake_home, install_dir=upper)
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert LIVE_ARM in r.stderr, r.stderr
        assert sentinel_of(live).exists()

        m = run_make(["uninstall", f"INSTALL_DIR={upper}"], fake_home)
        _assert_make_refused(m, "case-variant")
        assert LIVE_ARM in m.stderr, m.stderr
        assert sentinel_of(live).exists()

    def test_nonexistent_intermediate_spelling_refused(self, fake_home):
        """<home>/ghost/../agents-ensemble — the intermediate does not exist,
        so the guard resolves through the deepest EXISTING ancestor and
        compares the PHYSICAL path: still live."""
        live = make_live_shaped(fake_home)
        assert not (fake_home / "ghost").exists()
        spelling = f"{fake_home}/ghost/../agents-ensemble"

        r = run_guard("uninstall", fake_home, install_dir=spelling)
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert LIVE_ARM in r.stderr, r.stderr
        assert sentinel_of(live).exists()

        m = run_make(["uninstall", f"INSTALL_DIR={spelling}"], fake_home)
        _assert_make_refused(m, "ghost-intermediate")
        assert LIVE_ARM in m.stderr, m.stderr
        assert sentinel_of(live).exists()

    def test_relative_live_spelling_refused(self, fake_home):
        """A RELATIVE live spelling ("agents-ensemble") is refused EARLIER —
        as 'not an absolute path' — because the absolute-path arm (F6) runs
        BEFORE the live/demo classification.

        BEHAVIOUR CHANGE (F6, deliberate and contract-mandated): this row
        used to assert the LIVE wording, because the guard resolved the
        relative spelling against its cwd and matched the live dir. The
        absolute-path arm is UNCONDITIONAL by design — no confirm var may
        open a relative path — so the refusal now happens one arm earlier.
        The live dir is still protected either way (it is never deleted), and
        the '~/agents-ensemble' spelling still reaches the LIVE classification
        (pinned by TestAbsolutePathArm.test_tilde_form_is_judged_on_its_expanded_form).

        Guard-level: the guard resolves a relative INSTALL_DIR against its
        own cwd, so the probe runs with cwd=$HOME. Make-level: make resolves
        the recipe's rm -rf against ITS cwd, so the probe runs with
        cwd=$HOME too — and GUARD_SCRIPT is overridden with an absolute path
        because the Makefile spells it relative to the repo root.
        """
        live = make_live_shaped(fake_home)
        r = run_guard(
            "uninstall", fake_home, install_dir="agents-ensemble", cwd=fake_home,
        )
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert absolute_first_line("agents-ensemble") in r.stderr, r.stderr
        assert ABSOLUTE_ARM_TAIL in r.stderr, r.stderr
        assert LIVE_ARM not in r.stderr, r.stderr
        assert sentinel_of(live).exists()

        m = run_make(
            ["-f", str(MAKEFILE), f"GUARD_SCRIPT={GUARD}",
             "uninstall", "INSTALL_DIR=agents-ensemble"],
            fake_home, cwd=fake_home,
        )
        _assert_make_refused(m, "relative-live")
        assert absolute_first_line("agents-ensemble") in m.stderr, m.stderr
        assert LIVE_ARM not in m.stderr, m.stderr
        assert sentinel_of(live).exists()

    def test_symlink_alias_of_live_refused(self, fake_home):
        """REGRESSION: a symlink alias of the live dir is still refused (the
        physical resolution catches the alias)."""
        live = make_live_shaped(fake_home)
        alias = fake_home / "alias-to-live"
        alias.symlink_to(live)

        r = run_guard("uninstall", fake_home, install_dir=str(alias))
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert LIVE_ARM in r.stderr, r.stderr
        assert sentinel_of(live).exists()

        m = run_make(["uninstall", f"INSTALL_DIR={alias}"], fake_home)
        _assert_make_refused(m, "symlink-alias")
        assert LIVE_ARM in m.stderr, m.stderr
        assert sentinel_of(live).exists()
        assert alias.exists()  # the alias itself is not a protected dir


# ═══════════════════════════════════════════════════════════════════════════
# F6 — ABSOLUTE-PATH ARM (the guard requires an absolute INSTALL_DIR)
# ═══════════════════════════════════════════════════════════════════════════


class TestAbsolutePathArm:
    """F6 — INSTALL_DIR MUST BE AN ABSOLUTE PATH.

    A relative path resolves against whatever directory the recipe happens
    to run in, so the path the guard checks and the path a recipe would
    delete can be two DIFFERENT paths: '..' run from a repo root is the
    repo's parent, 'agents' run from a repo root is that repo's own
    agents/ tree. The guard therefore refuses every relative spelling.

    ORDERING (pinned by the rows below):
      * the unverifiable-spelling arm runs FIRST — './x' keeps the
        raw-value wording ("The path is not in normalized form") and '~' /
        '~user/...' keep the tilde wording ("cannot be verified safely");
      * the absolute-path arm runs NEXT, BEFORE the live/demo classification
        and BEFORE the containment arm, so NO confirm var can ever open a
        relative path;
      * the arm judges the TILDE-EXPANDED form: '~/x' expands to $HOME/x
        before the guard sees it, so it stays absolute and reaches the LIVE
        classification exactly as before.

    SAFETY: every make-level relative probe runs with the make cwd inside a
    NESTED sandbox (tmp_path/"a"/"b"), so even a hypothetical
    `rm -rf ".."` would stay inside the pytest tmp tree. The make-level
    probes NEVER run with the cwd at the repo root.
    """

    #: Relative spellings refused at guard level. 'agents' is the repo's own
    #: agents/ tree — the shape the tester reported.
    RELATIVE_SPELLINGS = ("agents", "..", "foo/bar")

    @pytest.mark.parametrize("spelling", RELATIVE_SPELLINGS)
    def test_relative_path_refused_at_guard_level(self, fake_home, spelling):
        """Every relative spelling refuses at rc 78 with the absolute-path
        wording — and stays refused WITH the confirm var set (structural)."""
        live = make_live_shaped(fake_home)
        sbx = _safe_sandbox(fake_home)
        # cwd = the fake home: a relative spelling COULD resolve to the live
        # dir from here — the worst case the arm has to refuse.
        r = run_guard(
            "uninstall", fake_home, install_dir=spelling, cwd=fake_home,
        )
        assert r.returncode == GUARD_REFUSE_RC, (spelling, r.stderr)
        assert absolute_first_line(spelling) in r.stderr, (spelling, r.stderr)
        assert ABSOLUTE_ARM_TAIL in r.stderr, (spelling, r.stderr)
        assert sentinel_of(live).exists(), spelling
        assert sentinel_of(sbx).exists(), spelling

        # STRUCTURAL: ENSEMBLE_INSTALL_LIVE=1 does NOT open a relative path
        r_conf = run_guard(
            "uninstall", fake_home, install_dir=spelling, cwd=fake_home,
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r_conf.returncode == GUARD_REFUSE_RC, (spelling, r_conf.stderr)
        assert absolute_first_line(spelling) in r_conf.stderr, (spelling, r_conf.stderr)
        assert sentinel_of(live).exists(), spelling
        assert sentinel_of(sbx).exists(), spelling

    def test_relative_path_refusal_precedes_the_confirm_gate(self, fake_home):
        """The arm runs BEFORE the live/demo classification: a relative
        spelling that WOULD have been live is refused as 'not an absolute
        path', never as a live dir — so no confirm var can reach it."""
        live = make_live_shaped(fake_home)
        r = run_guard(
            "uninstall", fake_home, install_dir="agents-ensemble", cwd=fake_home,
        )
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert ABSOLUTE_ARM_TAIL in r.stderr, r.stderr
        assert LIVE_ARM not in r.stderr, r.stderr
        assert sentinel_of(live).exists()

    def test_dot_slash_spelling_refused_by_the_raw_value_arm(self, fake_home):
        """'./x' is caught EARLIER, by the raw-value arm — its wording must
        NOT change (the absolute-path arm never sees it)."""
        sbx = _safe_sandbox(fake_home)
        r = run_guard("uninstall", fake_home, install_dir="./x", cwd=fake_home)
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert RAW_VALUE_FIRST_LINE in r.stderr, r.stderr
        assert DETAIL_NOT_NORMALIZED in r.stderr, r.stderr
        assert ABSOLUTE_ARM_TAIL not in r.stderr, r.stderr
        assert sentinel_of(sbx).exists()

    def test_empty_install_dir_keeps_the_inert_default_message(self, fake_home):
        """The EMPTY spelling keeps arm 0's message — the inert-default
        refusal must not silently become the absolute-path refusal."""
        live = make_live_shaped(fake_home)
        r = run_guard("uninstall", fake_home, install_dir="")
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert "INSTALL_DIR is not set" in r.stderr, r.stderr
        assert ABSOLUTE_ARM_TAIL not in r.stderr, r.stderr
        assert sentinel_of(live).exists()

    @pytest.mark.parametrize("spelling", ("..", "agents"))
    def test_relative_path_refused_at_make_level_in_a_nested_sandbox(
        self, fake_home, tmp_path, spelling,
    ):
        """MAKE-LEVEL: the recipe must NOT expand to a destructive rm -rf.

        The make cwd is a NESTED sandbox (tmp_path/"a"/"b"), so a
        hypothetical `rm -rf ".."` would delete tmp_path/"a" — contained
        inside the pytest tmp tree. The cwd is NEVER the repo root.
        """
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        (tmp_path / "a" / "SENTINEL_A").write_text("do-not-delete")
        (nested / "SENTINEL_B").write_text("do-not-delete")
        live = make_live_shaped(fake_home)

        r = run_make(
            ["-f", str(MAKEFILE), f"GUARD_SCRIPT={GUARD}",
             "uninstall", f"INSTALL_DIR={spelling}"],
            fake_home, cwd=nested,
        )
        _assert_make_refused(r, spelling)
        assert "Error 78" in r.stderr, (spelling, r.stderr)
        assert absolute_first_line(spelling) in r.stderr, (spelling, r.stderr)
        # the recipe never ran: the "Removing <dir>" banner is the recipe's
        # first echo — it must NOT be there
        assert "Removing" not in r.stdout, (spelling, r.stdout)
        # and the nested sandbox tree is still intact
        assert (tmp_path / "a" / "SENTINEL_A").exists(), spelling
        assert (nested / "SENTINEL_B").exists(), spelling
        assert sentinel_of(live).exists(), spelling

    def test_absolute_path_is_allowed(self, fake_home):
        """POSITIVE CONTROL: an absolute sandbox path is NOT refused."""
        sbx = _safe_sandbox(fake_home)
        r = run_guard("uninstall", fake_home, install_dir=str(sbx))
        assert r.returncode == 0, r.stderr
        assert r.stdout == "" and r.stderr == "", (r.stdout, r.stderr)
        assert sentinel_of(sbx).exists()

    def test_tilde_form_is_judged_on_its_expanded_form(self, fake_home):
        """POSITIVE CONTROL: '~/…' is judged on the EXPANDED form, so it is
        never refused as relative — '~/agents-ensemble' still reaches the
        LIVE classification, and a safe '~/…' is allowed."""
        live = make_live_shaped(fake_home)
        safe_tilde = _safe_sandbox(fake_home, "sandbox-tilde")

        r = run_guard("uninstall", fake_home, install_dir="~/agents-ensemble")
        assert r.returncode == GUARD_REFUSE_RC, r.stderr
        assert LIVE_ARM in r.stderr, r.stderr
        assert ABSOLUTE_ARM_TAIL not in r.stderr, r.stderr
        assert sentinel_of(live).exists()

        r_safe = run_guard("uninstall", fake_home, install_dir=f"~/{safe_tilde.name}")
        assert r_safe.returncode == 0, r_safe.stderr
        assert r_safe.stdout == "" and r_safe.stderr == "", (
            r_safe.stdout, r_safe.stderr,
        )
        assert sentinel_of(safe_tilde).exists()

    def test_tilde_form_reaches_the_live_classification_at_make_level(self, fake_home):
        """MAKE-LEVEL: the Makefile expands '~/' to $HOME/ before the guard
        sees it, so the value is absolute and reaches the LIVE
        classification — the absolute-path arm must not fire."""
        live = make_live_shaped(fake_home)
        r = run_make(["uninstall", "INSTALL_DIR=~/agents-ensemble"], fake_home)
        _assert_make_refused(r, "tilde-live")
        assert LIVE_ARM in r.stderr, r.stderr
        assert ABSOLUTE_ARM_TAIL not in r.stderr, r.stderr
        assert sentinel_of(live).exists()

