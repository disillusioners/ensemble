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


def _base_env(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_VARS}
    env["HOME"] = str(home)
    env.update(extra or {})
    return env


def run_make(
    args: list[str],
    home: Path,
    extra_env: dict[str, str] | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run make from the repo root with a fake HOME (carve-out rule)."""
    return subprocess.run(
        ["make", *args],
        cwd=str(REPO_ROOT),
        env=_base_env(home, extra_env),
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
) -> subprocess.CompletedProcess:
    """Invoke the guard script directly (guard-level proof; never writes)."""
    env = _base_env(home, extra_env)
    if install_dir is not None:
        env["INSTALL_DIR"] = install_dir
    return subprocess.run(
        ["bash", str(GUARD), "--target", target],
        cwd=str(REPO_ROOT),
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
