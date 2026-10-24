"""Isolated boot smoke — the 3-leg wedge matrix (T36 / AC50 / AC51 / AC52).

TWO LANES, TWO GATES
--------------------
This file carries the whole smoke, in two lanes:

* **Lane A — the injection matrix** (legs i / ii / iii, the cooldown and
  the kill-switch): the REAL watchdog against a REAL event loop, in this
  process, against a ``tmp_path`` capture root. It needs no daemon, no
  database and no systemd — and it is still a genuine end-to-end proof
  (trigger → capture → artifacts → attribution).
* **Lane B — the daemon boot in a ``systemd-run`` transient unit**: the
  full daemon on an ephemeral port against a scratch PG, with the cgroup
  assertion, the live-fence snapshot and a SIGTERM-only teardown.

Lane B checklist (the boot-alive gate, added 2026-10-23 after the wiring
blocker): an ENABLED real boot must leave a LIVE watchdog on
``app.state`` — ``wedge_capture_watchdog is not None``, the thread
``running``, and the ``started_at_monotonic`` signal set
(``smoke.assert_watchdog_boot_alive``). The historical blocker
(api.py's ``getattr(daemon, ...)`` NameError, swallowed by the
never-wedge boot guard) passed every direct-drive pack and died exactly
at this gate.

Both lanes are **opt-in**:

    ENSEMBLE_WEDGE_SMOKE=1            # lane A (the module gate)
    ENSEMBLE_WEDGE_SMOKE_UNIT=1       # + lane B (an ADDITIONAL gate —
                                      #   it does NOT imply lane A)

BOTH env vars are required for lane B: the module-level ``pytestmark``
skips everything unless ``ENSEMBLE_WEDGE_SMOKE=1``, and the lane-B
classes skip again unless ``ENSEMBLE_WEDGE_SMOKE_UNIT=1``. The
``TestBootWiring`` boot-alive leg additionally needs a scratch PG
provisioned via ``PG_TEST_*`` (translated to ``POSTGRES_*`` for the
child by ``smoke.scratch_env`` — the F1 fix).

Rationale: lane A spins a CPU core for a few seconds and lane B starts a
daemon — neither belongs in a default suite run. **The tester lane owns
executing them**; this file is the prepared harness.

F-13 / F-14
-----------
Leg (iii) does not trust "a concentration fired": it asserts the injected
spin's FRAME is in ``stacks.txt`` and that the attributed ``native_id`` is
the loop thread's. Legs (ii)/(iii)'s negative pairs are only meaningful on
a multi-core host — on one core they are **SKIPPED, never failed**, and
``nproc`` is recorded in every observation.
"""

from __future__ import annotations

import pytest

from tests.support import wedge_smoke as smoke

pytestmark = pytest.mark.skipif(
    not smoke.smoke_enabled(),
    reason=(
        "the wedge boot smoke is OPT-IN (set ENSEMBLE_WEDGE_SMOKE=1 for "
        "lane A; lane B additionally needs ENSEMBLE_WEDGE_SMOKE_UNIT=1 — "
        "it does NOT imply lane A); the tester lane runs it — it spins a "
        "CPU core and starts a daemon"
    ),
)


# ── Lane A: the injection matrix ──────────────────────────────────────────


@pytest.fixture()
def leg_runner(tmp_path, monkeypatch):
    """Set one leg's knobs, run it, then restore the environment."""

    def _run(leg: smoke.LegEnv, inject, hold_s: float = 3.0):
        for key, value in leg.as_env().items():
            monkeypatch.setenv(key, value)
        observation = smoke.run_injected_leg(
            root=tmp_path,
            leg=leg,
            inject=inject,
            hold_s=hold_s,
        )
        return observation

    return _run


class TestLegI_CpuBurn:
    def test_a_dedicated_burner_fires_the_cpu_trigger_only(
        self, tmp_path, leg_runner
    ) -> None:
        """Leg (i): a CPU peg with a SERVICED loop — the CPU trigger fires
        and the lag trigger must stay silent."""
        from tests.support.wedge_smoke import Burner

        burner = Burner()
        with burner:
            observation = leg_runner(smoke.LEG_CPU, lambda loop: None, hold_s=3.0)

        assert observation.capture_count >= 1, observation.as_dict()
        assert smoke.REASON_CPU in observation.fired, observation.as_dict()
        for silent in smoke.LEG_CPU.expect_silent:
            assert silent not in observation.fired, (
                f"leg (i) fired {silent} but the loop was serviced — the "
                "leg's negative is invalid: " + str(observation.as_dict())
            )


class TestLegII_LoopSleep:
    def test_a_loop_sleep_fires_the_lag_trigger_without_a_cpu_peg(
        self, tmp_path, leg_runner
    ) -> None:
        """Leg (ii): the H2 shape, at SC-2's PRESCRIBED envelope
        (plan-overview §6 SC-2): ``LAG_S=2.0`` + ``LAG_SUSTAIN_S=2`` and
        a **>= 5 s loop block (6 s in the leg, for margin)** — the lag
        trigger must fire AND sustain the 2 s window. A sleep releases
        the GIL, so neither the CPU nor the concentration trigger may
        fire — the negative pair IS the H1/H2 discriminator (F-14:
        multi-core only)."""
        if not smoke.multi_core():
            pytest.skip(
                f"single-core host (nproc={smoke.nproc()}): the negative pair "
                "is unprovable — legs (ii)/(iii) are SKIPPED, not failed"
            )

        observation = leg_runner(
            smoke.LEG_LAG,
            lambda loop: smoke.sleep_on_loop(loop, smoke.SC2_LOOP_BLOCK_S),
            hold_s=smoke.SC2_LEG_HOLD_S,
        )

        assert observation.capture_count >= 1, observation.as_dict()
        assert smoke.REASON_LAG in observation.fired, observation.as_dict()
        for silent in smoke.LEG_LAG.expect_silent:
            assert silent not in observation.fired, (
                f"leg (ii) fired {silent} on a GIL-releasing sleep — the "
                "discriminator is broken: " + str(observation.as_dict())
            )


class TestLegIII_LoopSpin:
    def test_a_loop_cpu_spin_attributes_the_loop_thread_and_captures(
        self, tmp_path, leg_runner
    ) -> None:
        """Leg (iii): a LIVE wedge under maximum GIL contention.

        The capture must COMPLETE (that is the instrument's whole point),
        the concentration must be attributed to the loop thread, and the
        injected spin's frame must be visible in the stack dump (F-13).
        """
        if not smoke.multi_core():
            pytest.skip(
                f"single-core host (nproc={smoke.nproc()}): skipping the "
                "negative-bearing legs"
            )

        # 6 s (not the minimum 3 s): the concentration trigger fires
        # first (~1.25 s) and its CAPTURE briefly occupies the watchdog,
        # so the lag series needs headroom to reach the 2 s threshold
        # after that capture completes. 6 s is the margin SC-2/B1
        # prescribes for the lag leg anyway.
        observation = leg_runner(
            smoke.LEG_SPIN, lambda loop: smoke.spin_on_loop(loop, 6.0), hold_s=7.0
        )

        assert observation.capture_count >= 1, observation.as_dict()
        assert smoke.REASON_LAG in observation.fired, observation.as_dict()
        assert smoke.REASON_CONCENTRATION in observation.fired, observation.as_dict()
        assert observation.loop_thread_native_id is not None
        assert observation.loop_thread_native_id in observation.attributed_native_ids, (
            "the concentration was not attributed to the LOOP thread: "
            + str(observation.as_dict())
        )
        assert observation.stacks_contains_spin_frame, (
            "the injected spin's frame is NOT in stacks.txt — a capture "
            "satisfied by some other loop-thread burn is not evidence of "
            "the injected one (F-13)"
        )


class TestCooldown:
    def test_a_sustained_wedge_produces_one_capture_per_reason(
        self, tmp_path, leg_runner
    ) -> None:
        """SC-6 / AC51 (per-reason, D43): the cooldown dedups PER REASON —
        a broken cooldown produces one capture per tick for the SAME
        reason; a legally co-firing second reason may add its OWN once."""
        from collections import Counter
        from tests.support.wedge_smoke import Burner

        burner = Burner()
        with burner:
            observation = leg_runner(smoke.LEG_CPU, lambda loop: None, hold_s=5.0)

        assert observation.capture_count >= 1, (
            "a sustained wedge must produce at least one capture: "
            + str(observation.as_dict())
        )
        per_reason = Counter(
            str(item.get("trigger_reason"))
            for item in smoke.manifests(observation.root)
        )
        duplicates = sorted(r for r, n in per_reason.items() if n > 1)
        assert not duplicates, (
            "a sustained wedge re-fired the same reason(s) "
            f"{duplicates} inside one cooldown window — the SC-6 per-reason "
            "dedup is broken: " + str(observation.as_dict())
        )


class TestKillSwitchLeg:
    def test_the_kill_switch_produces_no_capture_and_reports_disabled(
        self, tmp_path, monkeypatch
    ) -> None:
        """A15(a): ``ENSEMBLE_WEDGE_CAPTURE_ENABLED=0`` ⇒ no capture dir,
        no row, ``status().enabled == False``, and a manual capture is
        refused with ``watchdog_disabled``."""
        from daemon.services.wedge_capture.config import WedgeCaptureConfig
        from daemon.services.wedge_capture.watchdog import WedgeCaptureWatchdog

        monkeypatch.setenv("ENSEMBLE_WEDGE_CAPTURE_ENABLED", "0")
        config = WedgeCaptureConfig.from_env(data_dir=tmp_path)
        watchdog = WedgeCaptureWatchdog(config=config, boot_epoch=None)

        status = watchdog.status()
        assert status["enabled"] is False

        accepted, token, _ = watchdog.enqueue_manual_capture()
        assert accepted is False
        assert token == "watchdog_disabled"

        # nothing was ever started, so nothing can exist
        assert smoke.capture_dirs(config.capture_dir) == []


# ── Lane B: the daemon in a transient unit ────────────────────────────────


@pytest.mark.skipif(
    not smoke.unit_lane_enabled(),
    reason=(
        "lane B needs BOTH gates: ENSEMBLE_WEDGE_SMOKE=1 (the module "
        "gate, without which every test in this file skips) AND "
        "ENSEMBLE_WEDGE_SMOKE_UNIT=1 (this lane) — plus a systemd "
        "transient unit; the boot-alive leg also needs a scratch PG "
        "(PG_TEST_*, translated to POSTGRES_* for the child)"
    ),
)
class TestBootWiring:
    def test_an_enabled_boot_leaves_a_live_watchdog_on_app_state(
        self, tmp_path
    ) -> None:
        """The wiring blocker's end-to-end gate: a REAL ``create_app()`` +
        REAL ``lifespan`` boot with the instrument ENABLED must leave a
        LIVE watchdog on ``app.state`` — not-None, thread running, and
        the started signal set (``smoke.assert_watchdog_boot_alive``).

        The historical blocker (2026-10-23) died exactly here: a
        NameError at constructor-kwarg evaluation was swallowed by the
        never-wedge boot guard, leaving
        ``app.state.wedge_capture_watchdog = None`` on EVERY enabled
        boot while every direct-drive pack stayed green. This leg runs
        the boot the production process runs, inside the isolated unit.
        """
        import os
        import subprocess
        import sys

        pg_pairs = smoke.pg_test_env_pairs()
        if not pg_pairs:
            pytest.skip(
                "PG_TEST_* not provisioned — the tester lane owns the "
                "scratch DB for the real-boot lane (scratch_env translates "
                "them to the POSTGRES_* names the daemon reads)"
            )

        unit = "ensemble-wedge-smoke-boot-alive"
        env = smoke.apply_leg_env(smoke.BOOT_ALIVE_KNOBS, environ={})
        env["ENSEMBLE_WEDGE_CAPTURE_ENABLED"] = "1"
        env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
        env.update(
            smoke.scratch_env(
                tmp_path=tmp_path, port=smoke.free_port(), pg_env=pg_pairs
            )
        )
        argv = smoke.transient_unit_argv(
            unit=unit,
            workdir=str(smoke_repo_root()),
            argv=[sys.executable, "-c", smoke.boot_alive_script()],
            env=env,
        )
        try:
            completed = subprocess.run(
                argv, capture_output=True, text=True, check=False, timeout=300
            )
        except subprocess.TimeoutExpired as exc:
            pytest.fail(f"the boot-alive leg timed out after 300s: {exc}")
        assert completed.returncode == 0, (
            "the boot-alive gate FAILED (the watchdog did not survive a "
            "real enabled boot):\n"
            + completed.stdout[-2000:]
            + "\n--- stderr ---\n"
            + completed.stderr[-2000:]
        )
        assert not smoke.unit_exists(unit), (
            f"the transient unit {unit} survived the run — the unit-gone "
            "check failed (A16(4))"
        )


@pytest.mark.skipif(
    not smoke.unit_lane_enabled(),
    reason=(
        "lane B needs BOTH gates: ENSEMBLE_WEDGE_SMOKE=1 (the module "
        "gate, without which every test in this file skips) AND "
        "ENSEMBLE_WEDGE_SMOKE_UNIT=1 (this lane) — plus a systemd "
        "transient unit"
    ),
)
class TestDaemonBootLane:
    def test_the_unit_argv_never_targets_the_live_unit(self, tmp_path) -> None:
        argv = smoke.transient_unit_argv(
            unit="ensemble-wedge-smoke-test",
            workdir=tmp_path,
            argv=["true"],
            env=smoke.LEG_CPU.as_env(),
        )
        assert argv[0] == "systemd-run"
        assert "--wait" in argv and "--collect" in argv
        assert all("ensemble-main" not in part for part in argv)

    def test_the_cgroup_and_live_fence_hold(self, tmp_path) -> None:
        """SC-14 / AC52: the run is cgroup-isolated and the LIVE daemon's
        identity is identical before and after.

        F2 fix (2026-10-23): ``/proc/<pid>/cgroup`` is sampled WHILE THE
        PROCESS IS ALIVE. The pre-fix shape read it only after
        ``systemd-run --wait`` had returned — by then the child had
        exited and its /proc entry was gone, so the pin was permanently
        red under its own documented prerequisites.
        """
        import subprocess
        import time

        before = smoke.live_daemon_fence()
        version_before = smoke.livez_version()

        unit = "ensemble-wedge-smoke-cgroup"
        # The child reports its pid to a FILE (never stdout — a pipe read
        # could block), then stays alive a few seconds so the parent can
        # read its /proc entry mid-flight.
        pid_file = tmp_path / "cgroup-child.pid"
        script = (
            "import os, pathlib, time\n"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
            "time.sleep(4)\n"
        )
        argv = smoke.transient_unit_argv(
            unit=unit,
            workdir=tmp_path,
            argv=["python3", "-c", script],
            env={"ENSEMBLE_WEDGE_CAPTURE_ENABLED": "1"},
        )
        child = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        try:
            pid = 0
            deadline = time.monotonic() + 60.0
            while time.monotonic() < deadline:
                if pid_file.exists():
                    reported = pid_file.read_text(encoding="utf-8").strip()
                    if reported.isdigit():
                        pid = int(reported)
                        break
                if child.poll() is not None:
                    break
                time.sleep(0.05)
            assert pid, (
                "the unit child never reported its pid — cannot prove "
                "cgroup isolation while the process is alive"
            )
            # THE isolation proof — read while the child is ALIVE (F2).
            smoke.assert_cgroup_isolated(pid)
        finally:
            stdout, stderr = child.communicate(timeout=120)
        assert child.returncode == 0, (
            "the cgroup probe child failed:\n"
            + stdout[-400:]
            + "\n--- stderr ---\n"
            + stderr[-400:]
        )
        assert not smoke.unit_exists(unit), (
            f"the transient unit {unit} is still registered after --wait "
            "--collect — the unit-gone check failed (A16(4))"
        )

        after = smoke.live_daemon_fence()
        smoke.assert_live_fence_unchanged(before, after)
        assert smoke.livez_version() == version_before

    def test_the_three_legs_run_inside_the_transient_unit(self, tmp_path) -> None:
        """The REAL end-to-end: the 3-leg matrix executed INSIDE an
        isolated ``systemd-run`` unit.

        Lane A needs no database, so this is runnable as soon as a
        transient unit is available — the daemon's own PG-backed boot is
        the tester lane's additional gate (it provisions ``PG_TEST_*``).
        """
        import subprocess
        import sys

        unit = "ensemble-wedge-smoke-legs"
        env = smoke.apply_leg_env(smoke.LEG_CPU, environ={})
        env["ENSEMBLE_WEDGE_SMOKE"] = "1"
        env.update(
            smoke.scratch_env(
                tmp_path=tmp_path, port=smoke.free_port(), pg_env=[]
            )
        )
        argv = smoke.transient_unit_argv(
            unit=unit,
            workdir=str(smoke_repo_root()),
            argv=[
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--timeout=300",
                "-k",
                "TestLegI_CpuBurn or TestLegII_LoopSleep or "
                "TestLegIII_LoopSpin or TestCooldown",
                "tests/integration/test_wedge_capture_boot_smoke.py",
            ],
            env=env,
        )

        completed = subprocess.run(argv, capture_output=True, text=True, check=False)
        assert completed.returncode == 0, (
            "the in-unit leg run failed:\n"
            + completed.stdout[-2000:]
            + "\n--- stderr ---\n"
            + completed.stderr[-2000:]
        )
        assert not smoke.unit_exists(unit), (
            f"the transient unit {unit} survived the run — the unit-gone "
            "check failed (A16(4))"
        )


def smoke_repo_root():
    """The repository root (the transient unit's working directory)."""
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


def test_the_leg_matrix_is_three_legs_and_each_has_a_negative_pair() -> None:
    """D43: three legs, and legs (i)/(ii) carry explicit negatives."""
    assert [leg.name for leg in smoke.ALL_LEGS] == [
        "leg-i-cpu-burn",
        "leg-ii-loop-sleep",
        "leg-iii-loop-spin",
    ]
    assert smoke.LEG_CPU.expect_silent == (smoke.REASON_LAG,)
    assert set(smoke.LEG_LAG.expect_silent) == {
        smoke.REASON_CPU,
        smoke.REASON_CONCENTRATION,
    }
    assert smoke.LEG_SPIN.expect_fire == (smoke.REASON_LAG, smoke.REASON_CONCENTRATION)
