"""Option D (Stage-2.2a) ruling pin pack — the upgrade executor as a
run_id-named systemd TRANSIENT SERVICE.

Source of truth: .agents/shared/planning/upgrade-executor-resilience/
architecture-recommendation.md §5.3 (:113-116). Each test names the
ruling it pins:

  R1  Restart=no load-bearing — the unit spec carries it; any Restart
      override (even alongside RestartPreventExitStatus=78) is rejected.
  R2  Unique unit name per run_id (+ unit-name charset guard).
  R3  --setenv threads the FULL executor_env — verified-arm
      ENSEMBLE_UPGRADE_LIVE + F2_VERIFIED_NOTE forwarded post-gate;
      unverified arm forwards nothing (F2 fence holds end-to-end).
  R4  reset-failed hygiene — ``systemctl [--user] reset-failed <unit>``
      runs BEFORE the transient start; a reset-failed failure does not
      block the spawn.
  R5  --wait client ⇒ reaper observes the UNIT exit; the JOURNAL stays
      authoritative (reconcile closes the op on journal truth — the
      loud-refusal event included, end-to-end).
  R6  systemd-run failure ⇒ LOUD refusal (journal
      ``refusal`` + ``reason=executor-systemd-unavailable`` + raise) —
      NEVER a silent legacy-setsid fallback.

Hermetic: no real systemd-run/systemctl is ever invoked — the detector
seam is stubbed and subprocess.Popen / subprocess.run are monkeypatched
at the module level (the same convention
test_promote_cgroup_survivorship_python.py uses for its probes).

Run: pytest tests/unit/tools/test_upgrade_executor_systemd_service.py
(or ``python3 tests/unit/tools/test_upgrade_executor_systemd_service.py``
for the standalone runner).
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from daemon.tools import upgrade_journal as uj
from daemon.tools.upgrade_journal import (
    EXECUTOR_SYSTEMD_UNAVAILABLE_TOKEN,
    ExecutorSystemdUnavailable,
    PendingOp,
    journal_history_append,
    journal_init,
    journal_read,
    read_pending_op,
    reconcile_pending_op,
    write_pending_op,
)


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """A fresh staged-install fixture: journal initialized, extensions on."""
    inst = tmp_path / "install"
    (inst / "releases").mkdir(parents=True)
    journal_init(inst)
    uj.ensure_extensions(inst)
    return inst


class FakeProc:
    """Recording stand-in for subprocess.Popen results."""

    def __init__(
        self,
        pid: int = 424242,
        *,
        rc: int | None = None,
        stderr: bytes = b"",
        hang: bool = False,
    ) -> None:
        self.pid = pid
        self._rc = rc
        self._stderr = io.BytesIO(stderr)
        self._hang = hang

    def wait(self, timeout: float | None = None) -> int:
        if self._hang:
            raise subprocess.TimeoutExpired(cmd="systemd-run", timeout=timeout)
        return int(self._rc if self._rc is not None else 0)

    @property
    def stderr(self) -> io.BytesIO:  # type: ignore[override]
        return self._stderr


class Recorder:
    """Records subprocess.Popen / subprocess.run invocations in order."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.next_proc: FakeProc = FakeProc(hang=True)

    def popen(self, argv, **kwargs) -> FakeProc:
        self.calls.append({"kind": "Popen", "argv": list(argv), **kwargs})
        return self.next_proc

    def run(self, argv, **kwargs):
        self.calls.append({"kind": "run", "argv": list(argv), **kwargs})
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    @property
    def popen_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["kind"] == "Popen"]

    @property
    def run_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["kind"] == "run"]


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(uj.subprocess, "Popen", rec.popen)
    monkeypatch.setattr(uj.subprocess, "run", rec.run)
    return rec


def _stub_detect(
    monkeypatch: pytest.MonkeyPatch, branch: str, bus: str = "", reason: str = ""
) -> None:
    monkeypatch.setattr(
        uj, "_service_detect_fn", lambda env=None: (branch, bus, reason)
    )


# ── R1: Restart=no is load-bearing ──────────────────────────────────────────


class TestR1RestartNoLoadBearing:
    def test_unit_spec_carries_restart_no(self) -> None:
        argv = uj.build_service_argv(
            ["bash", "promote.sh", "live"], "r-1", "user", {}, Path("/i/data/upgrade.log")
        )
        assert "--property=Restart=no" in argv, argv

    def test_restart_always_override_rejected(self) -> None:
        with pytest.raises(ValueError, match="Restart=no is load-bearing"):
            uj.build_service_argv(
                ["bash", "x"], "r-1", "user", {}, Path("/i/data/upgrade.log"),
                ["Restart=always"],
            )

    def test_restart_on_failure_override_rejected(self) -> None:
        with pytest.raises(ValueError, match="load-bearing"):
            uj.build_service_argv(
                ["bash", "x"], "r-1", "user", {}, Path("/i/data/upgrade.log"),
                ["Restart=on-failure"],
            )

    def test_prevent_exit_status_alone_is_not_a_substitute(self) -> None:
        """Ruling R1 verbatim: RestartPreventExitStatus=78 alone fences only
        the refusal exit class — a Restart=always unit carrying it would
        STILL re-enter a partially-completed ceremony on any non-78 crash.
        The builder must reject the combination."""
        with pytest.raises(ValueError, match="RestartPreventExitStatus=78"):
            uj.build_service_argv(
                ["bash", "x"], "r-1", "user", {}, Path("/i/data/upgrade.log"),
                ["RestartPreventExitStatus=78", "Restart=always"],
            )

    def test_explicit_restart_no_override_is_accepted(self) -> None:
        argv = uj.build_service_argv(
            ["bash", "x"], "r-1", "user", {}, Path("/i/data/upgrade.log"),
            ["Restart=no"],
        )
        assert argv.count("--property=Restart=no") >= 1


# ── R2: unique unit name per run_id ─────────────────────────────────────────


class TestR2UniqueUnitName:
    def test_distinct_run_ids_yield_distinct_units(self) -> None:
        a = uj.build_service_argv(
            ["bash", "x"], "r-run-a", "user", {}, Path("/i/data/upgrade.log")
        )
        b = uj.build_service_argv(
            ["bash", "x"], "r-run-b", "user", {}, Path("/i/data/upgrade.log")
        )
        ua = next(t for t in a if t.startswith("--unit="))
        ub = next(t for t in b if t.startswith("--unit="))
        assert ua == "--unit=ensemble-upgrade-r-run-a"
        assert ub == "--unit=ensemble-upgrade-r-run-b"
        assert ua != ub

    def test_concurrent_spawns_never_collide(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        """Two simultaneous arms must carry two DIFFERENT unit names in
        their systemd-run argv (systemd refuses a second transient start
        against an existing name)."""
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        pids = []
        for rid in ("r-concurrent-1", "r-concurrent-2"):
            pid, note = uj.spawn_executor(
                ["bash", "promote.sh", "demo", "--version", "1.0"],
                install, {}, run_id=rid,
            )
            pids.append((pid, note))
        units = [
            next(c for c in c["argv"] if str(c).startswith("--unit="))
            for c in recorder.popen_calls
        ]
        assert len(units) == 2
        assert units[0] != units[1]
        assert units[0] == "--unit=ensemble-upgrade-r-concurrent-1"
        assert units[1] == "--unit=ensemble-upgrade-r-concurrent-2"

    @pytest.mark.parametrize("bad", ["", "bad;name", "has space", "a/b", "../../etc"])
    def test_unsafe_run_id_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError, match="unsafe systemd unit run_id"):
            uj.build_service_argv(
                ["bash", "x"], bad, "user", {}, Path("/i/data/upgrade.log")
            )


# ── R3: --setenv threads the full env ───────────────────────────────────────


class TestR3SetenvThreading:
    def test_full_env_threaded_including_verified_arm(self) -> None:
        """Ruling R3 + doc :116: pin the forwarded set. The verified-arm
        extras (ENSEMBLE_UPGRADE_LIVE + F2_VERIFIED_NOTE, post-gate) ride
        the executor_env explicit-extra merge and MUST cross the systemd
        boundary via --setenv; every key of the dict must appear."""
        env = uj.executor_env({
            "ENSEMBLE_UPGRADE_LIVE": "1",
            "F2_VERIFIED_NOTE": "discord:r-arm-9",
        })
        env.setdefault("PATH", "/usr/bin:/bin")
        env.setdefault("INSTALL_DIR", "/home/nea/agents-ensemble")
        argv = uj.build_service_argv(
            ["bash", "promote.sh", "live", "--f2-verified-closed"],
            "r-arm-9", "user", env, Path("/i/data/upgrade.log"),
        )
        setenvs = [t for t in argv if t.startswith("--setenv=")]
        threaded = {t.split("=", 1)[1].split("=", 1)[0] for t in setenvs}
        assert threaded == set(env.keys()), (threaded, set(env.keys()))
        assert "--setenv=ENSEMBLE_UPGRADE_LIVE=1" in argv
        assert "--setenv=F2_VERIFIED_NOTE=discord:r-arm-9" in argv
        assert "--setenv=INSTALL_DIR=/home/nea/agents-ensemble" in argv
        # inner argv byte-identical after the -- separator
        sep = argv.index("--")
        assert argv[sep + 1:] == [
            "bash", "promote.sh", "live", "--f2-verified-closed",
        ]

    def test_unverified_arm_forwards_nothing(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        """F2 fence end-to-end: an UNVERIFIED arm (no verified-arm extras in
        extra_env) must NOT carry ENSEMBLE_UPGRADE_LIVE into the unit."""
        monkeypatch.setenv("ENSEMBLE_UPGRADE_LIVE", "1")  # ambient poison
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")  # ambient poison
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        uj.spawn_executor(
            ["bash", "promote.sh", "demo", "--version", "1.0"],
            install, {}, run_id="r-unverified",
        )
        argv = recorder.popen_calls[0]["argv"]
        assert not any(t.startswith("--setenv=ENSEMBLE_UPGRADE_LIVE") for t in argv)
        assert not any(t.startswith("--setenv=OPENAI_API_KEY") for t in argv)
        # allowlist discipline still holds in the threaded set
        for t in (x for x in argv if x.startswith("--setenv=")):
            key = t.split("=", 1)[1].split("=", 1)[0]
            assert (
                key in uj.EXECUTOR_ENV_ALLOWLIST
                or any(key.startswith(p) for p in uj.EXECUTOR_ENV_PREFIXES)
            ), f"non-allowlisted key threaded: {key}"

    def test_payload_stdio_rides_append_properties(self) -> None:
        log = Path("/i/data/upgrade.log")
        argv = uj.build_service_argv(
            ["bash", "x"], "r-1", "user", {}, log
        )
        assert f"--property=StandardOutput=append:{log}" in argv
        assert f"--property=StandardError=append:{log}" in argv


# ── R4: reset-failed hygiene ────────────────────────────────────────────────


class TestR4ResetFailedHygiene:
    def test_reset_failed_runs_before_start(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        uj.spawn_executor(
            ["bash", "promote.sh", "demo"], install, {}, run_id="r-hyg-1",
        )
        kinds = [c["kind"] for c in recorder.calls]
        assert kinds.index("run") < kinds.index("Popen"), recorder.calls
        rf = recorder.run_calls[0]
        assert rf["argv"] == [
            "systemctl", "--user", "reset-failed", "ensemble-upgrade-r-hyg-1",
        ]

    def test_system_bus_skips_user_flag(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "system")
        uj.spawn_executor(
            ["bash", "promote.sh", "demo"], install, {}, run_id="r-hyg-2",
        )
        rf = recorder.run_calls[0]
        assert rf["argv"] == [
            "systemctl", "reset-failed", "ensemble-upgrade-r-hyg-2",
        ]

    def test_reset_failed_failure_does_not_block_spawn(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")

        def failing_run(argv, **kwargs):
            recorder.calls.append({"kind": "run", "argv": list(argv), **kwargs})
            return subprocess.CompletedProcess(argv, 1, b"", b"unit not loaded")

        monkeypatch.setattr(uj.subprocess, "run", failing_run)
        pid, note = uj.spawn_executor(
            ["bash", "promote.sh", "demo"], install, {}, run_id="r-hyg-3",
        )
        assert recorder.popen_calls, "spawn must proceed past a reset-failed failure"
        assert "service=ensemble-upgrade-r-hyg-3" in note


# ── R5: --wait client; journal stays authoritative ──────────────────────────


class TestR5WaitAndJournalTruth:
    def test_argv_carries_wait_and_collect(self) -> None:
        argv = uj.build_service_argv(
            ["bash", "x"], "r-1", "user", {}, Path("/i/data/upgrade.log")
        )
        assert "--wait" in argv
        assert "--collect" in argv

    def test_in_flight_client_note_names_unit_exit(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        recorder.next_proc = FakeProc(hang=True)
        pid, note = uj.spawn_executor(
            ["bash", "promote.sh", "live"], install, {}, run_id="r-wait-1",
        )
        assert "--wait" in recorder.popen_calls[0]["argv"]
        assert "unit in flight" in note
        assert "reaper observes unit exit via --wait client" in note

    def test_fast_zero_exit_is_journal_truth(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        recorder.next_proc = FakeProc(rc=0)
        pid, note = uj.spawn_executor(
            ["bash", "restart.sh", "demo"], install, {}, run_id="r-wait-2",
        )
        assert "outcome is journal-truth" in note

    def test_loud_refusal_event_closes_pending_op(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        """Ruling R5 (journal-truth) wired to R6: the loud-refusal journal
        event is ``refusal``-class → reconcile_pending_op closes the armed
        op IMMEDIATELY (the executor will never run; no false in-flight
        promote, no expiry wait)."""
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        recorder.next_proc = FakeProc(
            rc=1, stderr=b"Failed to start transient service unit: Access denied"
        )
        op = PendingOp(
            run_id="r-jt-1", kind="promote", env="demo", target="1.2.3",
            owner_pid=os.getpid(),
            expires_at=uj.iso_plus(uj.now_iso(), 600),
        )
        write_pending_op(install, op)
        with pytest.raises(ExecutorSystemdUnavailable):
            uj.spawn_executor(
                ["bash", "promote.sh", "live"], install, {}, run_id="r-jt-1",
            )
        note = reconcile_pending_op(install)
        assert note is not None and "r-jt-1" in note
        assert read_pending_op(install) is None


# ── R6: systemd-run failure ⇒ loud refusal, never silent fallback ───────────


class TestR6LoudRefusal:
    def test_client_start_failure_refuses_loudly(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        recorder.next_proc = FakeProc(
            rc=1, stderr=b"Failed to start transient service unit: Access denied"
        )
        with pytest.raises(ExecutorSystemdUnavailable, match="NO legacy setsid fallback"):
            uj.spawn_executor(
                ["bash", "promote.sh", "live"], install, {}, run_id="r-loud-1",
            )
        # the durable record: a refusal-class history event with the token
        data = journal_read(install)
        refusals = [
            e for e in data.get("history", [])
            if e.get("event") == "refusal"
            and EXECUTOR_SYSTEMD_UNAVAILABLE_TOKEN in str(e.get("detail", ""))
        ]
        assert refusals, data.get("history")

    def test_bus_failure_signature_refuses_loudly(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        recorder.next_proc = FakeProc(
            rc=1, stderr=b"Failed to connect to bus: No such file or directory"
        )
        with pytest.raises(ExecutorSystemdUnavailable):
            uj.spawn_executor(
                ["bash", "promote.sh", "live"], install, {}, run_id="r-loud-2",
            )

    def test_unit_managed_host_never_falls_back_to_setsid(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        """THE discriminator: on a unit-managed host where the transient
        start is unavailable, the OLD behavior silently took the legacy
        cgroup-coupled setsid path (the kill class survived). Option D must
        raise INSTEAD — and never issue a start_new_session=True spawn."""
        _stub_detect(
            monkeypatch, uj.SERVICE_BRANCH_UNAVAILABLE, "",
            "transient-unit probe failed on both buses: polkit denial",
        )
        with pytest.raises(ExecutorSystemdUnavailable, match="polkit denial"):
            uj.spawn_executor(
                ["bash", "promote.sh", "live"], install, {}, run_id="r-loud-3",
            )
        assert recorder.popen_calls == [], "no spawn of any kind may happen"
        data = journal_read(install)
        refusals = [
            e for e in data.get("history", [])
            if e.get("event") == "refusal"
            and EXECUTOR_SYSTEMD_UNAVAILABLE_TOKEN in str(e.get("detail", ""))
        ]
        assert refusals

    def test_fast_payload_failure_is_journal_truth_not_refusal(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        """A fast non-zero client exit WITHOUT a systemd failure signature
        means the PAYLOAD ran and refused fast (e.g. preflight exit-78) —
        that is journal-truth, NOT a systemd-unavailable loud refusal."""
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        recorder.next_proc = FakeProc(
            rc=78, stderr=b"some pipeline noise on the client stderr"
        )
        pid, note = uj.spawn_executor(
            ["bash", "promote.sh", "live"], install, {}, run_id="r-fast-78",
        )
        assert "journal-truth" in note
        assert "rc=78" in note

    def test_service_branch_client_never_uses_setsid(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        uj.spawn_executor(
            ["bash", "promote.sh", "live"], install, {}, run_id="r-sess-1",
        )
        call = recorder.popen_calls[0]
        assert call.get("start_new_session") is False
        assert call["argv"][0] == "systemd-run"


# ── detector contract ───────────────────────────────────────────────────────


class TestDetectorContract:
    def test_real_detector_never_raises_and_returns_shape(self) -> None:
        res = uj._service_detect_real()
        assert isinstance(res, tuple) and len(res) == 3
        branch, bus, reason = res
        assert branch in (
            uj.SERVICE_BRANCH_SERVICE,
            uj.SERVICE_BRANCH_LEGACY,
            uj.SERVICE_BRANCH_UNAVAILABLE,
        )
        if branch == uj.SERVICE_BRANCH_SERVICE:
            assert bus in ("user", "system")
        else:
            assert bus == ""

    def test_probe_forwards_spawn_env_not_ambient(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """r-f82e cycle-1 parity (pin 6a ported): the probe must run with
        the SPAWN env (the allowlist result), never the daemon's ambient —
        otherwise session-env hosts probe 'user bus OK' and then spawn
        without the bus address. Skipped on hosts where the detector
        short-circuits at the platform guards before any probe runs."""
        if sys.platform != "linux" or not Path("/run/systemd/system").exists():
            pytest.skip("detector short-circuits before probes on this host")
        probe_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        captured: list[dict[str, Any] | None] = []
        real_run = uj.subprocess.run

        def recording_run(argv, **kwargs):
            captured.append(kwargs.get("env"))
            return subprocess.CompletedProcess(argv, 1, b"", b"probe-denied")

        monkeypatch.setattr(uj.subprocess, "run", recording_run)
        res = uj._service_detect_real(probe_env)
        assert res[0] == uj.SERVICE_BRANCH_UNAVAILABLE  # probes denied
        assert captured, "detector never probed"
        assert all(e == probe_env for e in captured), captured

    def test_swallowed_exception_classifies_unavailable_not_legacy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ruling R6: a swallowed detector exception on a unit-managed host
        must classify UNAVAILABLE (loud refusal), not legacy (silent
        setsid). The old detector's broad-except returned the legacy
        fallback — reversed deliberately."""
        def boom(path_self=None):
            raise RuntimeError("simulated detector internal error")

        monkeypatch.setattr(uj.Path, "exists", boom)
        branch, bus, reason = uj._service_detect_real({})
        assert branch == uj.SERVICE_BRANCH_UNAVAILABLE
        assert "detector exception" in reason


# ── legacy branch byte-identity (doc §5.1.5 — no host regresses) ────────────


class TestLegacyBranchByteIdentity:
    def test_non_linux_host_keeps_setsid_path(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_LEGACY, "",
                     "non-Linux (launchd semantics)")
        argv_in = ["bash", "promote.sh", "demo", "--version", "1.0"]
        pid, note = uj.spawn_executor(argv_in, install, {}, run_id="r-leg-1")
        assert note == "(daemonized, start_new_session)"
        call = recorder.popen_calls[0]
        assert call["argv"] == argv_in  # unwrapped
        assert call.get("start_new_session") is True

    def test_no_systemd_host_keeps_setsid_path(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_LEGACY, "",
                     "no systemd PID1 (/run/systemd/system absent)")
        pid, note = uj.spawn_executor(
            ["bash", "restart.sh", "demo"], install, {}, run_id="r-leg-2",
        )
        assert note == "(daemonized, start_new_session)"
        assert recorder.run_calls == []  # no reset-failed on the legacy branch


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
