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

Both lanes are **opt-in**:

    ENSEMBLE_WEDGE_SMOKE=1            # lane A
    ENSEMBLE_WEDGE_SMOKE_UNIT=1       # + lane B (implies lane A)

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
        "the wedge boot smoke is OPT-IN (set ENSEMBLE_WEDGE_SMOKE=1); the "
        "tester lane runs it — it spins a CPU core and starts a daemon"
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
        """Leg (ii): the H2 shape. A sleep releases the GIL, so neither the
        CPU nor the concentration trigger may fire — the negative pair IS
        the H1/H2 discriminator (F-14: multi-core only)."""
        if not smoke.multi_core():
            pytest.skip(
                f"single-core host (nproc={smoke.nproc()}): the negative pair "
                "is unprovable — legs (ii)/(iii) are SKIPPED, not failed"
            )

        observation = leg_runner(
            smoke.LEG_LAG, lambda loop: smoke.sleep_on_loop(loop, 3.0), hold_s=3.5
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
    def test_a_sustained_wedge_produces_exactly_one_capture(
        self, tmp_path, leg_runner
    ) -> None:
        """SC-6 / AC51: one capture per cooldown window — a broken
        cooldown would produce one per tick."""
        from tests.support.wedge_smoke import Burner

        burner = Burner()
        with burner:
            observation = leg_runner(smoke.LEG_CPU, lambda loop: None, hold_s=5.0)

        assert observation.capture_count == 1, (
            "a sustained wedge inside one cooldown window must produce "
            "EXACTLY one capture: " + str(observation.as_dict())
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
        "the daemon boot lane needs a systemd transient unit "
        "(ENSEMBLE_WEDGE_SMOKE_UNIT=1) and a scratch PG (PG_TEST_*)"
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
        identity is identical before and after."""
        import os
        import subprocess

        before = smoke.live_daemon_fence()
        version_before = smoke.livez_version()

        unit = "ensemble-wedge-smoke-cgroup"
        argv = smoke.transient_unit_argv(
            unit=unit,
            workdir=tmp_path,
            argv=["python3", "-c", "import os; print(os.getpid())"],
            env={"ENSEMBLE_WEDGE_CAPTURE_ENABLED": "1"},
        )
        completed = subprocess.run(argv, capture_output=True, text=True, check=False)
        assert completed.returncode == 0, completed.stderr[-800:]

        pid_text = [
            line.strip() for line in completed.stdout.splitlines() if line.strip().isdigit()
        ]
        if pid_text:
            smoke.assert_cgroup_isolated(int(pid_text[-1]))
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
