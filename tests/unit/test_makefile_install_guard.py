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
import shlex
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
    spelling whose INTERMEDIATE does not exist (<home>/ghost/../live), a
    RELATIVE spelling resolved against the cwd, and a symlink alias must all
    still classify as live. The physical resolution uses /bin/pwd -P (BSD) —
    bash's builtin `pwd -P` echoes the TYPED casing, which compares unequal
    to the real tree.
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
        """A RELATIVE live spelling ("agents-ensemble") is resolved against
        the CWD: with cwd=$HOME it is the live dir and must be refused.

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
        assert LIVE_ARM in r.stderr, r.stderr
        assert sentinel_of(live).exists()

        m = run_make(
            ["-f", str(MAKEFILE), f"GUARD_SCRIPT={GUARD}",
             "uninstall", "INSTALL_DIR=agents-ensemble"],
            fake_home, cwd=fake_home,
        )
        _assert_make_refused(m, "relative-live")
        assert LIVE_ARM in m.stderr, m.stderr
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


# ══════════════════════════════════════════════════════════════════════════
# ADDED 2026-11-09 (phase 2 of the live-install-guard mission) — APPEND-ONLY
# ══════════════════════════════════════════════════════════════════════════
#
# Two gaps closed on top of the 51-test matrix above (which is UNCHANGED —
# every pre-existing test body, name and order is byte-identical):
#
#   P1-core — stop-by-port: the legacy port kill took its port from the
#     REPO's .env.prod (the live prod port 9797) while the guard gates the
#     DIR axis → a sandbox INSTALL_DIR passed the guard and the kill still
#     landed on the live daemon (inverted protection: the correct live
#     spelling is refused, the sandbox spelling reaches live). The kill port
#     is now read from the TARGET install dir's own staged PORT
#     ($(INSTALL_DIR)/.env) and the target fails closed when that dir has
#     no staged PORT.
#
#   P2 — plist-install / watchdog-install write HOST launchd state
#     ($HOME/Library/LaunchAgents/…) REGARDLESS of INSTALL_DIR: a sandbox
#     dir passed the INSTALL_DIR gate and silently overwrote the running
#     prod unit with one pointing at the sandbox. The guard's new opt-in
#     --require-live mode closes that (the dir must BE the live install; a
#     live dir still needs ENSEMBLE_INSTALL_LIVE=1).
#
# CARVE-OUT (same rules as the file header, honored here verbatim):
#   * every real make invocation runs with HOME = a fresh tmp fake home;
#     every path any recipe can touch lives under tmp.
#   * scripts/stop-ensemble.sh is NEVER executed — the stop-by-port
#     proceed-arm is proven at guard level and via `make -n` (dry run);
#     the make-level refusal arm dies BEFORE the stop script line, and the
#     suite asserts the script's banner never printed.
#   * the make-level plist/watchdog proceed arms write into the FAKE home's
#     Library/LaunchAgents (tmp) — never on the real host; neither recipe
#     invokes launchctl (it only prints the bootstrap hint).
#   * the sudo arm stays behind `sudo -n` (not needed here — no new sudo
#     arm is added; the sudo-correct FORM is asserted in the refusal text).


def run_guard_require_live(
    target: str,
    home: Path,
    install_dir: str | None = None,
    extra_env: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Guard-level probe WITH the opt-in --require-live mode (P2).

    A sibling of run_guard (which is left byte-identical): the host launchd
    gate is a MODE of the same script, so the probe adds the flag instead of
    changing the shared helper. The guard never writes.
    """
    env = _base_env(home, extra_env)
    if install_dir is not None:
        env["INSTALL_DIR"] = install_dir
    return subprocess.run(
        ["bash", str(GUARD), "--target", target, "--require-live"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestStopByPortPortScoping:
    """P1-core: the legacy port kill must be bound to the GATED dir.

    Before: `make stop-by-port INSTALL_DIR=<sandbox>` expanded to
    `STOP_BY_PORT=1 bash scripts/stop-ensemble.sh <sandbox> 9797` — the
    repo's prod port, i.e. the LIVE daemon's port, regardless of the dir
    the guard had just approved. After: the port comes from
    $(INSTALL_DIR)/.env and the target refuses when that dir stages none.
    """

    def test_inert_refusal(self, fake_home):
        """Bare `make stop-by-port` → the guard's inert refusal fires first
        (INSTALL_DIR unset → no dir to scope the kill to)."""
        r = run_make(["stop-by-port"], fake_home)
        assert r.returncode != 0
        assert "INSTALL_DIR is not set" in r.stderr

    def test_missing_staged_port_refuses_and_names_ownership_stop(
        self, fake_home, tmp_path
    ):
        """A dir with NO staged PORT → refuse with exit 78 BEFORE the stop
        script runs, and name the exact ownership-scoped command.

        Non-vacuity: the stop script's ownership banner never printed AND
        the STOP_BY_PORT kill line was never reached (make prints recipe
        lines as it executes them, so its absence proves the refusal came
        first).
        """
        sbx = tmp_path / "sbx-no-env"
        sbx.mkdir()
        r = run_make(["stop-by-port", f"INSTALL_DIR={sbx}"], fake_home)
        assert r.returncode != 0
        assert "Error 78" in r.stderr
        assert f"make stop INSTALL_DIR='{sbx}'" in r.stdout
        assert sbx.exists()
        assert "Stopping processes owned by" not in r.stdout
        assert "STOP_BY_PORT=1" not in r.stdout

    def test_confirmed_live_dir_without_staged_port_still_refuses(self, fake_home):
        """Fail-closed even for a CONFIRMED live dir: without a staged PORT
        the kill cannot be scoped to the dir, so it is refused rather than
        falling back to an unscoped port."""
        live = make_live_shaped(fake_home)
        r = run_make(
            ["stop-by-port", f"INSTALL_DIR={live}"],
            fake_home,
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r.returncode != 0
        assert "Error 78" in r.stderr
        assert f"make stop INSTALL_DIR='{live}'" in r.stdout
        assert sentinel_of(live).exists()

    def test_kill_port_is_read_from_the_target_dir_not_the_repo(
        self, fake_home, tmp_path
    ):
        """`make -n stop-by-port` with a dir whose OWN .env stages
        PORT=12345 → the kill targets 12345, never the repo's prod port."""
        sbx = tmp_path / "sbx-with-port"
        sbx.mkdir()
        (sbx / ".env").write_text("PORT=12345\n")
        r = run_make(["-n", "stop-by-port", f"INSTALL_DIR={sbx}"], fake_home)
        assert r.returncode == 0, r.stderr
        assert "12345" in r.stdout
        assert "9797" not in r.stdout
        assert f'scripts/stop-ensemble.sh "{sbx}" 12345' in r.stdout

    def test_no_repo_prod_port_leaks_into_the_kill(self, fake_home, tmp_path):
        """A dir with no staged PORT → the expanded kill line carries NO
        port token at all (empty), never the repo's prod port (the old
        wiring took it from .env.prod)."""
        sbx = tmp_path / "sbx-no-env"
        sbx.mkdir()
        r = run_make(["-n", "stop-by-port", f"INSTALL_DIR={sbx}"], fake_home)
        assert r.returncode == 0, r.stderr
        kill_lines = [ln for ln in r.stdout.splitlines() if "stop-ensemble.sh" in ln]
        assert kill_lines, r.stdout
        assert "9797" not in r.stdout
        assert str(sbx) in kill_lines[0]
        # STRONG pin (the docstring's property): the kill command carries NO
        # port token at all — the (quoted) install dir is the LAST token and
        # nothing follows it. upstream quotes the dir sink (F-01), so the
        # expansion is: STOP_BY_PORT=1 bash scripts/stop-ensemble.sh "<dir>"
        parts = shlex.split(kill_lines[0])
        assert parts[:3] == [
            "STOP_BY_PORT=1", "bash", "scripts/stop-ensemble.sh",
        ], parts
        assert len(parts) == 4, f"a port token leaked into the kill: {parts}"
        assert parts[3] == str(sbx), parts

    def test_recipe_no_longer_reads_the_repo_prod_port(self):
        """Static wiring pin: the stop-by-port recipe is bound to
        $(STOP_PORT) (the target dir's own staged port), not to
        $(PROD_PORT_FALLBACK) (the repo's .env.prod)."""
        text = MAKEFILE.read_text()
        block = text.split("\nstop-by-port:\n", 1)[1]
        recipe = block.split("\n\n", 1)[0]
        assert "PROD_PORT_FALLBACK" not in recipe, recipe
        assert "STOP_PORT" in recipe, recipe

    def test_guard_level_pass_for_unclassified_dir(self, fake_home, tmp_path):
        """Guard-level proceed arm for the DIR axis (the guard never
        writes; the stop script is never executed by this suite)."""
        sbx = tmp_path / "sbx"
        sbx.mkdir()
        r = run_guard("stop-by-port", fake_home, install_dir=str(sbx))
        assert r.returncode == 0, r.stderr


class TestHostLaunchdInstallRequiresLive:
    """P2: the host launchd write must only ever come from the LIVE install.

    plist-install / watchdog-install copy the staged plist into
    $HOME/Library/LaunchAgents/ REGARDLESS of INSTALL_DIR, so a sandbox dir
    (which the INSTALL_DIR gate happily opens) used to overwrite the running
    prod unit with one pointing at the sandbox. The guard's opt-in
    --require-live mode closes that hole; the canonical live refusal still
    fires FIRST for an unconfirmed live dir.
    """

    @staticmethod
    def _launch_agents(home: Path) -> Path:
        return home / "Library" / "LaunchAgents"

    def test_plist_install_refuses_sandbox_and_writes_no_launchd_unit(
        self, fake_home, tmp_path
    ):
        """REFUSAL arm (sandbox dir): the refusal names the exact confirm
        var and the sudo-correct form, and NO host launchd unit was
        written (HOME is the tmp fake home → the mkdir + cp never ran)."""
        sbx = tmp_path / "sbx"
        sbx.mkdir()
        r = run_make(["plist-install", f"INSTALL_DIR={sbx}"], fake_home)
        assert r.returncode != 0
        assert "Error 78" in r.stderr
        assert "REFUSING" in r.stderr
        assert "ENSEMBLE_INSTALL_LIVE=1" in r.stderr
        assert (
            f"sudo env ENSEMBLE_INSTALL_LIVE=1 make plist-install INSTALL_DIR='{sbx}'"
            in r.stderr
        )
        assert not self._launch_agents(fake_home).exists()
        assert not (
            self._launch_agents(fake_home) / "ensemble-prod.plist"
        ).exists()

    def test_watchdog_install_refuses_sandbox_and_writes_no_launchd_unit(
        self, fake_home, tmp_path
    ):
        """REFUSAL arm (sandbox dir) for the watchdog agent — same
        non-vacuity proof: no LaunchAgents dir, no unit file."""
        sbx = tmp_path / "sbx"
        sbx.mkdir()
        r = run_make(["watchdog-install", f"INSTALL_DIR={sbx}"], fake_home)
        assert r.returncode != 0
        assert "Error 78" in r.stderr
        assert "ENSEMBLE_INSTALL_LIVE=1" in r.stderr
        assert (
            f"sudo env ENSEMBLE_INSTALL_LIVE=1 make watchdog-install INSTALL_DIR='{sbx}'"
            in r.stderr
        )
        assert not self._launch_agents(fake_home).exists()
        assert not (
            self._launch_agents(fake_home) / "ensemble-watchdog-watcher.plist"
        ).exists()

    def test_demo_dir_is_not_opened_by_the_demo_confirm(self, fake_home):
        """ENSEMBLE_INSTALL_DEMO=1 must NOT open a demo dir under
        --require-live: the prod-named launchd unit must point at live."""
        demo = fake_home / "agents-ensemble-demo"
        demo.mkdir()
        r = run_make(
            ["plist-install", f"INSTALL_DIR={demo}"],
            fake_home,
            extra_env={"ENSEMBLE_INSTALL_DEMO": "1"},
        )
        assert r.returncode != 0
        assert "ENSEMBLE_INSTALL_LIVE=1" in r.stderr
        assert not self._launch_agents(fake_home).exists()

    @pytest.mark.parametrize("target", ["plist-install", "watchdog-install"])
    def test_live_dir_without_confirm_gets_the_canonical_live_refusal(
        self, fake_home, target
    ):
        """ORDERING: for a live dir WITHOUT the confirm var the EXISTING
        per-path live refusal fires first (its message shape is unchanged);
        the --require-live refusal must not shadow or pre-empt it."""
        live = make_live_shaped(fake_home)
        r = run_make([target, f"INSTALL_DIR={live}"], fake_home)
        assert r.returncode != 0
        assert "resolves to a LIVE install dir" in r.stderr
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr
        assert not self._launch_agents(fake_home).exists()

    def test_guard_require_live_refuses_unclassified_dir(self, fake_home, tmp_path):
        """Guard level: --require-live refuses a sandbox dir (exit 78)."""
        sbx = tmp_path / "sbx"
        sbx.mkdir()
        r = run_guard_require_live("plist-install", fake_home, install_dir=str(sbx))
        assert r.returncode == GUARD_REFUSE_RC
        assert "ENSEMBLE_INSTALL_LIVE=1" in r.stderr

    def test_guard_require_live_refuses_demo_even_with_demo_confirm(self, fake_home):
        """Guard level: a demo dir stays refused under --require-live even
        with ENSEMBLE_INSTALL_DEMO=1."""
        demo = fake_home / "agents-ensemble-demo"
        demo.mkdir()
        r = run_guard_require_live(
            "watchdog-install",
            fake_home,
            install_dir=str(demo),
            extra_env={"ENSEMBLE_INSTALL_DEMO": "1"},
        )
        assert r.returncode == GUARD_REFUSE_RC
        assert "ENSEMBLE_INSTALL_LIVE=1" in r.stderr

    def test_guard_require_live_passes_for_confirmed_live_dir(self, fake_home):
        """Guard level: a confirmed live dir passes (the mode adds the live
        requirement, it does not replace the confirm var)."""
        live = make_live_shaped(fake_home)
        r = run_guard_require_live(
            "plist-install",
            fake_home,
            install_dir=str(live),
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r.returncode == 0, r.stderr

    def test_plist_install_proceeds_for_confirmed_live_dir(self, fake_home):
        """SUCCESS arm: a confirmed live dir installs the staged plist into
        the FAKE home's LaunchAgents (tmp — never the real host)."""
        live = make_live_shaped(fake_home)
        (live / "data").mkdir()
        (live / "data" / "ensemble-prod.plist").write_text("<plist/>\n")
        r = run_make(
            ["plist-install", f"INSTALL_DIR={live}"],
            fake_home,
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r.returncode == 0, r.stderr
        installed = self._launch_agents(fake_home) / "ensemble-prod.plist"
        assert installed.exists()
        assert installed.read_text() == "<plist/>\n"

    def test_watchdog_install_proceeds_for_confirmed_live_dir(self, fake_home):
        """SUCCESS arm for the watchdog agent (fake home only)."""
        live = make_live_shaped(fake_home)
        (live / "data").mkdir()
        (live / "data" / "ensemble-watchdog-watcher.plist").write_text("<plist/>\n")
        r = run_make(
            ["watchdog-install", f"INSTALL_DIR={live}"],
            fake_home,
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r.returncode == 0, r.stderr
        installed = self._launch_agents(fake_home) / "ensemble-watchdog-watcher.plist"
        assert installed.exists()
        assert installed.read_text() == "<plist/>\n"

    @pytest.mark.parametrize("target", ["plist-install", "watchdog-install"])
    def test_recipes_pass_the_require_live_flag(self, target):
        """Static wiring pin: both host-launchd targets front the guard
        with --require-live."""
        text = MAKEFILE.read_text()
        block = text.split(f"\n{target}:\n", 1)[1]
        first_line = block.split("\n", 1)[0]
        assert "$(GUARD_SCRIPT) --target" in first_line, target
        assert "--require-live" in first_line, target


# ══════════════════════════════════════════════════════════════════════════
# ADDED 2026-11-09 (phase 3 of the live-install-guard mission) — APPEND-ONLY
# ══════════════════════════════════════════════════════════════════════════
#
# Continues the phase-2 block above (same ADDED banner style; the phase-2
# banner text itself is NOT rewritten, and the first 1051 lines of this file
# stay byte-identical).
#
#   E1 — live-install SUBPATH classification (both parts):
#     (1) a protected root AND every path INSIDE it (any depth) share its
#         class — `~/agents-ensemble/data` is LIVE and refuses exactly like
#         the root does (`rm -rf $HOME/agents-ensemble/data` destroys the
#         live install's data). The match is PATH-COMPONENT aware: a
#         NAME-PREFIX SIBLING ("…/agents-ensemble-backup") is a separate
#         tree and stays allowed BY DESIGN.
#     (2) the candidate set now mirrors lib.sh:902-917
#         (_install_dir_candidates): under a Layer-1 HOME the cross-env
#         SIBLING is a candidate too (HOME=…/agents-ensemble-demo ⇒
#         …/agents-ensemble is a LIVE candidate).
#
#   P3a — deploy lane demo confirm: `scripts/deploy.sh demo` now needs
#     ENSEMBLE_INSTALL_DEMO=1 (same incident class as its live gate).
#   P3b — upgrade lane demo confirm: the MUTATING upgrade wrappers
#     (stage/promote/rollback) now need ENSEMBLE_INSTALL_DEMO=1 for the
#     demo target (the default target of all three). status.sh stays
#     ungated (read-only); the sandbox lane is exempt.
#
# CARVE-OUT (same rules as the file header, honored here verbatim):
#   * every real invocation runs with HOME = a fresh tmp fake home; every
#     path any recipe can touch lives under tmp.
#   * the deploy/upgrade scripts are invoked ONLY in their refusal arms
#     (no confirm var → the gate fires before any mutation) or in a
#     dry-run / pre-mutation-gate arm (--dry-run for deploy; the VERSION
#     gate for the upgrade scripts) — never in a mutating lane.
#   * scripts/stop-ensemble.sh is NEVER executed.


def run_script(
    script: str,
    args: list[str],
    home: Path,
    extra_env: dict[str, str] | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run a repo script (deploy/upgrade lane) with a fake HOME.

    The scripts resolve their own REPO_ROOT from BASH_SOURCE, so cwd is the
    repo root. The SCRUBBED_VARS scrub applies (ENSEMBLE_INSTALL_DEMO among
    them) — an ambient export can never silently open the gate under test.
    """
    env = _base_env(home, extra_env)
    return subprocess.run(
        ["bash", str(REPO_ROOT / script), *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestLiveSubpathContainment:
    """E1 part 1: root-or-inside containment (path-component aware)."""

    def test_live_subpath_depth1_refused_without_confirm(self, fake_home):
        """`$HOME/agents-ensemble/data` is LIVE → refused with the canonical
        live refusal. Non-vacuity: the sentinel inside the subdir survives
        (the unguarded rm -rf did NOT run)."""
        live = make_live_shaped(fake_home)
        sub = live / "data"
        sub.mkdir()
        (sub / "SENTINEL").write_text("keep")
        r = run_make(["uninstall", f"INSTALL_DIR={sub}"], fake_home)
        assert r.returncode != 0
        assert "Error 78" in r.stderr
        assert "INSIDE the protected install dir" in r.stderr
        assert "STRUCTURAL: no confirm var opens it" in r.stderr
        assert (sub / "SENTINEL").exists(), "ungated rm -rf ran on a live SUBPATH"

    def test_live_subpath_deep_refused_without_confirm(self, fake_home):
        """Depth is irrelevant: …/agents-ensemble/data/deep/x is LIVE too."""
        live = make_live_shaped(fake_home)
        deep = live / "data" / "deep" / "x"
        deep.mkdir(parents=True)
        (deep / "SENTINEL").write_text("keep")
        r = run_make(["uninstall", f"INSTALL_DIR={deep}"], fake_home)
        assert r.returncode != 0
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr
        assert (deep / "SENTINEL").exists(), "ungated rm -rf ran on a live SUBPATH"

    def test_live_subpath_refused_even_with_confirm(self, fake_home):
        """STRENGTHENED by the union (upstream 0f60f1ab9): containment is
        now STRUCTURAL -- no confirm var opens a path inside the protected
        root, so the subpath is refused EVEN WITH ENSEMBLE_INSTALL_LIVE=1.
        (Adapted from the branch's old
        `test_live_subpath_proceeds_with_confirm`, which asserted the
        opposite under the branch's weaker containment-as-classification.)"""
        live = make_live_shaped(fake_home)
        sub = live / "data"
        sub.mkdir()
        (sub / "SENTINEL").write_text("ok-to-delete")
        r = run_make(
            ["uninstall", f"INSTALL_DIR={sub}"],
            fake_home,
            extra_env={"ENSEMBLE_INSTALL_LIVE": "1"},
        )
        assert r.returncode != 0
        assert "Error 78" in r.stderr
        assert "STRUCTURAL: no confirm var opens it" in r.stderr
        assert (sub / "SENTINEL").exists(), "the structural refusal must not run rm -rf"
    def test_name_prefix_siblings_are_not_live(self, fake_home):
        """DOCUMENTED DECISION (guard header): a NAME-PREFIX SIBLING is a
        separate tree and stays ALLOWED — only the tree inside the
        protected root is gated. Both spellings are pinned."""
        make_live_shaped(fake_home)
        for name in ("agents-ensemble-backup", "agents-ensembleX"):
            sib = fake_home / name
            sib.mkdir()
            r = run_guard("uninstall", fake_home, install_dir=str(sib))
            assert r.returncode == 0, (name, r.stderr)
            assert "LIVE install dir" not in r.stderr, name

    def test_symlink_into_live_root_refused(self, fake_home, tmp_path):
        """A symlink alias pointing AT the live root resolves physically
        (cd + pwd -P) and is caught."""
        live = make_live_shaped(fake_home)
        alias = tmp_path / "alias-to-live-root"
        alias.symlink_to(live)
        r = run_guard("uninstall", fake_home, install_dir=str(alias))
        assert r.returncode == GUARD_REFUSE_RC
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr

    def test_symlink_into_live_subdir_refused(self, fake_home, tmp_path):
        """A symlink alias pointing INTO the live root (a subdir) is LIVE
        too — physical resolution happens BEFORE classification."""
        live = make_live_shaped(fake_home)
        sub = live / "data"
        sub.mkdir()
        alias = tmp_path / "alias-to-live-data"
        alias.symlink_to(sub)
        r = run_guard("uninstall", fake_home, install_dir=str(alias))
        assert r.returncode == GUARD_REFUSE_RC
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr

    def test_not_yet_existing_subpath_refused_textually(self, fake_home):
        """A spelling that does not exist yet is still caught TEXTUALLY
        (the root exists, the leaf does not)."""
        make_live_shaped(fake_home)
        r = run_guard(
            "uninstall", fake_home,
            install_dir=f"{fake_home}/agents-ensemble/newdir",
        )
        assert r.returncode == GUARD_REFUSE_RC
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr

    def test_demo_subpath_refused_even_with_confirm(self, fake_home):
        """STRENGTHENED by the union (upstream 0f60f1ab9): containment is
        now STRUCTURAL for the demo root too -- no confirm var opens a path
        inside it, so the subpath is refused EVEN WITH ENSEMBLE_INSTALL_DEMO=1."""
        demo = fake_home / "agents-ensemble-demo"
        sub = demo / "data"
        sub.mkdir(parents=True)
        (sub / "SENTINEL").write_text("keep")
        refused = run_guard("uninstall", fake_home, install_dir=str(sub))
        assert refused.returncode == GUARD_REFUSE_RC
        assert "STRUCTURAL: no confirm var opens it" in refused.stderr
        opened = run_guard("uninstall", fake_home, install_dir=str(sub),
                           extra_env={"ENSEMBLE_INSTALL_DEMO": "1"})
        assert opened.returncode == GUARD_REFUSE_RC, "structural refusal must not open"
        assert "STRUCTURAL: no confirm var opens it" in opened.stderr
        assert (sub / "SENTINEL").exists()
    def test_opt_ensemble_subpath_refused(self, fake_home):
        """The built-in /opt/ensemble live dir extends to its subpaths."""
        r = run_guard("uninstall", fake_home, install_dir="/opt/ensemble/sub")
        assert r.returncode == GUARD_REFUSE_RC
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr

    def test_extension_entry_subpath_refused(self, fake_home, tmp_path):
        """An ENSEMBLE_LIVE_INSTALL_DIRS entry extends to its subpaths."""
        ext = tmp_path / "ext-entry"
        sub = ext / "sub"
        sub.mkdir(parents=True)
        r = run_guard(
            "uninstall", fake_home, install_dir=str(sub),
            extra_env={"ENSEMBLE_LIVE_INSTALL_DIRS": str(ext)},
        )
        assert r.returncode == GUARD_REFUSE_RC
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr


class TestLayer1SiblingCandidate:
    """E1 part 2: the candidate set mirrors lib.sh:902-917.

    Under a Layer-1 HOME (HOME ends with an install name) the cross-env
    SIBLING is a candidate too — lib.sh:898-899 documents it ("plus the
    sibling <name> next to it (cross-env resolution, e.g. the flipped live
    daemon resolving the demo install)").
    """

    def test_live_sibling_under_demo_home_is_live(self, tmp_path):
        """HOME=…/agents-ensemble-demo ⇒ the sibling …/agents-ensemble is a
        LIVE candidate (phase-1 probe: this was rc=0, i.e. ungated)."""
        home = tmp_path / "layer1" / "agents-ensemble-demo"
        home.mkdir(parents=True)
        sibling = tmp_path / "layer1" / "agents-ensemble"
        sibling.mkdir()
        r = run_guard("uninstall", home, install_dir=str(sibling))
        assert r.returncode == GUARD_REFUSE_RC
        assert "ENSEMBLE_INSTALL_LIVE" in r.stderr
        assert "resolves to a LIVE install dir" in r.stderr

    def test_demo_sibling_under_live_home_is_demo(self, tmp_path):
        """HOME=…/agents-ensemble ⇒ the sibling …/agents-ensemble-demo is a
        DEMO candidate (phase-1 probe: this was rc=0, i.e. ungated)."""
        home = tmp_path / "layer1" / "agents-ensemble"
        home.mkdir(parents=True)
        sibling = tmp_path / "layer1" / "agents-ensemble-demo"
        sibling.mkdir()
        r = run_guard("uninstall", home, install_dir=str(sibling))
        assert r.returncode == GUARD_REFUSE_RC
        assert "ENSEMBLE_INSTALL_DEMO" in r.stderr
        assert "ENSEMBLE_INSTALL_LIVE" not in r.stderr


class TestDemoLaneConfirmGates:
    """P3a (deploy lane) + P3b (upgrade lane): the demo lane now demands
    ENSEMBLE_INSTALL_DEMO=1 — one var per PATH, not one per lane."""

    def test_deploy_demo_refuses_without_confirm(self, fake_home):
        """REFUSAL arm (deploy.sh demo, no var): exit 78, and the refusal
        names BOTH exact commands."""
        r = run_script("scripts/deploy.sh", ["demo"], fake_home)
        assert r.returncode == 78
        assert "REFUSING to deploy to demo" in r.stderr
        assert "ENSEMBLE_INSTALL_DEMO=1 make deploy-demo" in r.stderr
        assert "ENSEMBLE_INSTALL_DEMO=1 make stage-demo" in r.stderr

    def test_deploy_demo_refusal_precedes_any_mutation(self, fake_home):
        """Non-vacuity: the refusal fires in phase 0 — no demo env source
        was generated in the repo and the demo install dir was not created."""
        demo_env = REPO_ROOT / ".env.prod.demo"
        existed_before = demo_env.exists()
        mtime_before = demo_env.stat().st_mtime if existed_before else None
        r = run_script("scripts/deploy.sh", ["demo"], fake_home)
        assert r.returncode == 78
        assert demo_env.exists() == existed_before, "the demo env source was generated"
        if existed_before:
            assert demo_env.stat().st_mtime == mtime_before, "the demo env source was rewritten"
        assert not (fake_home / "agents-ensemble-demo").exists()

    def test_deploy_demo_passes_the_gate_with_confirm(self, fake_home):
        """SUCCESS arm: with the confirm var the demo gate opens and the
        run reaches the staging phase. --dry-run keeps it side-effect free
        (deploy.sh's run() prints instead of executing)."""
        r = run_script(
            "scripts/deploy.sh", ["demo", "--dry-run", "--skip-build"], fake_home,
            extra_env={"ENSEMBLE_INSTALL_DEMO": "1"},
        )
        assert "REFUSING to deploy to demo" not in r.stderr, r.stderr
        assert "phase 1/5 build" in r.stdout
        assert "phase 2/5 stage" in r.stdout
        assert not (fake_home / "agents-ensemble-demo").exists()

    def test_deploy_demo_gate_is_static_before_the_first_mutating_phase(self):
        """Static placement pin: the demo arm sits in phase 0, before the
        build (:219-226), the staging block (:325-338) and the ownership-
        scoped stop (:347)."""
        text = (REPO_ROOT / "scripts" / "deploy.sh").read_text()
        gate = text.index("REFUSING to deploy to demo")
        assert gate < text.index('run "PyInstaller build (bare, branch-safe)"')
        assert gate < text.index('run "stage binary"')
        assert gate < text.index('run "stop owned processes"')

    @pytest.mark.parametrize(
        "script,make_target",
        [
            ("scripts/upgrade/stage.sh", "upgrade-stage"),
            ("scripts/upgrade/promote.sh", "upgrade-promote"),
            ("scripts/upgrade/rollback.sh", "upgrade-rollback"),
        ],
    )
    def test_upgrade_demo_refuses_without_confirm(self, fake_home, script, make_target):
        """REFUSAL arm (upgrade lane, demo target, no var): exit 78 naming
        the exact make command for that wrapper. Non-vacuity: the demo
        install dir was not created."""
        r = run_script(script, ["demo"], fake_home)
        assert r.returncode == 78, r.stderr
        assert "REFUSING to operate on demo" in r.stderr
        assert f"ENSEMBLE_INSTALL_DEMO=1 make {make_target}" in r.stderr
        assert not (fake_home / "agents-ensemble-demo").exists()

    def test_upgrade_stage_demo_passes_the_gate_with_confirm(self, fake_home):
        """SUCCESS arm: with the confirm var the demo gate opens and the
        NEXT gate (the explicit-VERSION rule) fires instead — still before
        any mutation (stage.sh's VERSION check precedes the staging)."""
        r = run_script(
            "scripts/upgrade/stage.sh", ["demo"], fake_home,
            extra_env={"ENSEMBLE_INSTALL_DEMO": "1"},
        )
        assert "REFUSING to operate on demo" not in r.stderr, r.stderr
        assert "explicit VERSION required" in r.stderr
        assert not (fake_home / "agents-ensemble-demo").exists()

    def test_upgrade_sandbox_lane_is_exempt(self, fake_home):
        """The SANDBOX lane must NOT hit the demo gate — it reaches its own
        resolution refusal (a sandbox needs an explicit dir + port)."""
        r = run_script("scripts/upgrade/stage.sh", ["sandbox"], fake_home)
        assert "REFUSING to operate on demo" not in r.stderr, r.stderr
        assert "sandbox target requires" in r.stderr

    def test_upgrade_status_stays_ungated(self, fake_home):
        """status.sh is READ-ONLY (status.sh:18-20) → it keeps NO demo
        gate: it observes, it does not mutate."""
        r = run_script("scripts/upgrade/status.sh", ["demo"], fake_home)
        assert "REFUSING to operate on demo" not in r.stderr, r.stderr
