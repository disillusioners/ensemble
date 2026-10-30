"""Pack H (phase 5) — the DAEMON-SIDE surface of the promote-preflight-hang fix.

Every assertion names the plan row it pins (``phase5-plan.md`` §2 Pack H,
AC9/AC15; tasks T5.9/T5.15):

  D2-8  ``unit_properties`` plumbing exists on the spawn seam and is threaded
        into ``build_service_argv``; ``Restart=no`` remains load-bearing
        (an override is still rejected).
  D2-9  ``RuntimeMaxSec`` is DERIVED at arm time:
        ``max(1200, PROMOTE_TOTAL_DEADLINE_S + SOAK_S + 120)`` — 1320 at the
        house defaults, 1620 at ``SOAK_S=600``, 1920 at the 900 s clamp, 1200
        floor. The stale literal ``1200`` is gone (mutation M19).
        DEVIATION D-9: the plan's worked example says "1800 at SOAK_S=600";
        the FORMULA (fold B2) is the single source of truth and its arithmetic
        gives 1620 — see implementation-decisions.md D-9.
  kind  SCOPING: a promote arm carries the property, a RESTART arm carries
        none (a shared bound would kill a legitimate restart ceremony).
  D3-12/D3-15  the per-run TWO-FILE sink: the unit's append: capture owns the
        PAYLOAD file; the NARRATIVE path is threaded in the executor env; the
        aggregate ``data/upgrade.log`` gets one pointer line naming both.
  D3-6/R7  the CLIENT's stderr is a FILE, never a ``PIPE`` (a 64 KiB pipe is
        only drained on the fast-fail path — the stall class this lane exists
        to kill), and the systemd-unavailable triage still fires from the
        file's tail.
  T4.4/D3-9  the ``executor_still_running`` row is ENRICHED (narrative path +
        last phase marker + elapsed since last journal activity) while the
        660 s threshold is UNCHANGED (mutation M21).
  T2.17/A6  a live-lane arm with ``SOAK_S > 300`` REFUSES (exit 78) while the
        same soak is accepted on a sandbox target — executed against the real
        ``promote.sh`` inside an asserted sandbox fence.

Hermetic: no real systemd-run/systemctl, no daemon, no PG, no network. The
two shell legs run ``promote.sh`` only inside a fixture whose HOME/INSTALL_DIR
are ``tmp_path``-derived and are ASSERTED non-live before the invocation
(the 2026-10-12 live-install-deletion class); the fence fails closed.

Run (worktree has no .venv — use the certified main-venv interpreter):
  cd /home/nea/ensemble-src-wt-preflight-hang && \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
      tests/unit/tools/test_upgrade_preflight_daemon_side.py -q
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from daemon.tools import upgrade_journal as uj

# ── shared fixtures / stubs ─────────────────────────────────────────────────


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """A fresh staged-install fixture: journal initialized, extensions on."""
    inst = tmp_path / "install"
    (inst / "releases").mkdir(parents=True)
    uj.journal_init(inst)
    uj.ensure_extensions(inst)
    return inst


class FakeProc:
    """Stand-in for subprocess.Popen results."""

    def __init__(self, pid: int = 424242, *, rc: int | None = None, hang: bool = False):
        self.pid = pid
        self._rc = rc
        self._hang = hang

    def wait(self, timeout: float | None = None) -> int:
        if self._hang:
            raise subprocess.TimeoutExpired(cmd="systemd-run", timeout=timeout)
        return int(self._rc if self._rc is not None else 0)


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


def _clear_derivation_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROMOTE_TOTAL_DEADLINE_S", raising=False)
    monkeypatch.delenv("ENSEMBLE_PROMOTE_SOAK_S", raising=False)


# ── D2-9: the derived L3 bound ──────────────────────────────────────────────


class TestD2_9DerivedRuntimeMaxSec:
    """AC9/AC15 — ``RuntimeMaxSec`` is DERIVED, never a pinned literal."""

    def test_default_derivation_is_1320(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_derivation_env(monkeypatch)
        assert uj.derived_runtime_max_sec() == 1320

    def test_soak_600_derives_1620(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DEVIATION D-9: the plan's "1800 at SOAK_S=600" example is
        unreachable under the binding formula (1800 needs SOAK_S=780).
        900 + 600 + 120 = 1620."""
        _clear_derivation_env(monkeypatch)
        assert uj.derived_runtime_max_sec(600) == 1620

    def test_soak_900_clamp_derives_1920(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_derivation_env(monkeypatch)
        assert uj.derived_runtime_max_sec(900) == 1920

    def test_soak_above_the_shell_ceiling_is_clamped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """promote.sh clamps ``SOAK_S > 900`` to 900; the derivation mirrors
        the clamp so the two can never disagree."""
        _clear_derivation_env(monkeypatch)
        assert uj.derived_runtime_max_sec(5000) == 1920

    def test_floor_is_1200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_derivation_env(monkeypatch)
        assert uj.derived_runtime_max_sec(0, 0) == 1200

    def test_env_knobs_are_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROMOTE_TOTAL_DEADLINE_S", "1800")
        monkeypatch.setenv("ENSEMBLE_PROMOTE_SOAK_S", "300")
        assert uj.derived_runtime_max_sec() == 2220

    def test_garbage_env_degrades_to_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PROMOTE_TOTAL_DEADLINE_S", "not-a-number")
        monkeypatch.setenv("ENSEMBLE_PROMOTE_SOAK_S", "  ")
        assert uj.derived_runtime_max_sec() == 1320

    def test_derived_bound_is_strictly_above_the_shell_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The backstop must fire only after the shell's own 900 s machinery
        has failed to."""
        _clear_derivation_env(monkeypatch)
        assert uj.derived_runtime_max_sec() > 900
        assert uj.RUNTIME_MAX_SEC_FLOOR == 1200
        assert uj.RUNTIME_MAX_SEC_TAIL_S == 120


# ── D2-8: unit_properties plumbing + the Restart=no guard ────────────────────


class TestD2_8UnitPropertiesPlumbing:
    def test_runtime_maxsec_rides_unit_properties(self) -> None:
        argv = uj.build_service_argv(
            ["bash", "promote.sh", "demo"], "r-prop-1", "user", {},
            Path("/i/data/upgrade.log"),
            unit_properties=["RuntimeMaxSec=1320"],
        )
        assert "--property=RuntimeMaxSec=1320" in argv, argv

    def test_no_property_without_unit_properties(self) -> None:
        argv = uj.build_service_argv(
            ["bash", "promote.sh", "demo"], "r-prop-2", "user", {},
            Path("/i/data/upgrade.log"),
        )
        assert not any("RuntimeMaxSec" in t for t in argv), argv

    def test_restart_override_still_rejected_with_runtime_maxsec(self) -> None:
        """A5: the property extension point must not open a hole in R1."""
        with pytest.raises(ValueError, match="Restart=no is load-bearing"):
            uj.build_service_argv(
                ["bash", "x"], "r-prop-3", "user", {},
                Path("/i/data/upgrade.log"),
                unit_properties=["RuntimeMaxSec=1320", "Restart=always"],
            )

    def test_restart_no_is_always_present(self) -> None:
        argv = uj.build_service_argv(
            ["bash", "x"], "r-prop-4", "user", {},
            Path("/i/data/upgrade.log"),
            unit_properties=["RuntimeMaxSec=1320"],
        )
        assert "--property=Restart=no" in argv


# ── kind scoping at the REAL drain seam ─────────────────────────────────────


class _DrainHarness:
    """Drives ``InstanceManager.drain_pending_system_execution`` with the
    spawn seam stubbed (house precedent: test_upgrade_tools.py
    ``TestManagerDrainPendingExecution``)."""

    EXECUTOR_PID = 424242

    def __init__(self, tmp_path: Path) -> None:
        from daemon.manager import InstanceManager

        self.mgr = object.__new__(InstanceManager)  # skip heavy __init__
        self.mgr._pending_system_executions = {}
        self.install = tmp_path / "drain-install"
        (self.install / "releases").mkdir(parents=True)
        uj.journal_init(self.install)
        uj.ensure_extensions(self.install)
        self.scripts = tmp_path / "drain-scripts"
        self.scripts.mkdir(parents=True, exist_ok=True)
        for name in ("promote.sh", "restart.sh"):
            (self.scripts / name).write_text(
                "#!/usr/bin/env bash\n# fixture stub — never executed\n",
                encoding="utf-8",
            )
        self.calls: list[dict[str, Any]] = []

    def spawn_stub(
        self, argv, install_dir, extra_env=None, *, run_id=None,
        unit_prefix="ensemble-upgrade-", unit_properties=None,
    ) -> tuple[int, str]:
        self.calls.append(
            {
                "argv": list(argv),
                "install_dir": Path(str(install_dir)),
                "unit_prefix": unit_prefix,
                "unit_properties": (
                    list(unit_properties) if unit_properties is not None else None
                ),
            }
        )
        return self.EXECUTOR_PID, "(stubbed)"

    def arm_and_drain(self, kind: str, run_id: str, target: str = "v0.0.1-test") -> bool:
        uj.write_pending_op(
            self.install,
            uj.PendingOp(
                run_id=run_id, kind=kind, env="demo", target=target,
                armed_by_instance="inst-drain-h",
            ),
        )
        iid = "inst-drain-h"
        self.mgr._pending_system_executions[iid] = {
            "instance_id": iid,
            "kind": kind,
            "install_dir": str(self.install),
            "scripts_dir": str(self.scripts),
            "run_id": run_id,
            "env": "demo",
            "target": target,
        }
        return asyncio.run(self.mgr.drain_pending_system_execution(iid))


@pytest.fixture
def drain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _DrainHarness:
    h = _DrainHarness(tmp_path)
    monkeypatch.setattr(uj, "spawn_executor", h.spawn_stub)
    return h


class TestKindScopedRuntimeMaxSec:
    def test_promote_arm_carries_the_derived_bound(
        self, drain: _DrainHarness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_derivation_env(monkeypatch)
        assert drain.arm_and_drain("promote", "r-kind-promote-1") is True
        assert drain.calls, "the spawn seam was never reached"
        assert drain.calls[0]["unit_properties"] == ["RuntimeMaxSec=1320"]

    def test_restart_arm_carries_none(
        self, drain: _DrainHarness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_derivation_env(monkeypatch)
        assert drain.arm_and_drain("restart", "r-kind-restart-1") is True
        assert drain.calls, "the spawn seam was never reached"
        assert drain.calls[0]["unit_properties"] is None

    def test_promote_arm_derives_from_the_env_soak(
        self, drain: _DrainHarness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENSEMBLE_PROMOTE_SOAK_S", "600")
        monkeypatch.delenv("PROMOTE_TOTAL_DEADLINE_S", raising=False)
        assert drain.arm_and_drain("promote", "r-kind-promote-2") is True
        assert drain.calls[0]["unit_properties"] == ["RuntimeMaxSec=1620"]


# ── D3-12/D3-15: the per-run two-file sink ──────────────────────────────────


class TestPerRunLogTargets:
    def test_payload_append_targets_the_per_run_payload_file(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        uj.spawn_executor(
            ["bash", "promote.sh", "live", "--version", "v1"],
            install, {}, run_id="r-perrun-1",
        )
        argv = recorder.popen_calls[0]["argv"]
        payload = install / "data" / "upgrade" / "live" / "r-perrun-1.payload.log"
        assert f"--property=StandardOutput=append:{payload}" in argv, argv
        assert f"--property=StandardError=append:{payload}" in argv, argv

    def test_narrative_path_is_threaded_in_the_executor_env(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        uj.spawn_executor(
            ["bash", "promote.sh", "live", "--version", "v1"],
            install, {}, run_id="r-perrun-2",
        )
        argv = recorder.popen_calls[0]["argv"]
        narrative = install / "data" / "upgrade" / "live" / "r-perrun-2.log"
        assert f"--setenv=ENSEMBLE_UPGRADE_LOG_FILE={narrative}" in argv, argv
        # two files, never one: the payload target is a DIFFERENT path
        payload = install / "data" / "upgrade" / "live" / "r-perrun-2.payload.log"
        assert narrative != payload
        assert f"--property=StandardOutput=append:{payload}" in argv, argv

    def test_pointer_line_names_both_files(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        uj.spawn_executor(
            ["bash", "promote.sh", "sandbox", "--version", "v1"],
            install, {}, run_id="r-perrun-3",
        )
        aggregate = install / "data" / "upgrade.log"
        text = aggregate.read_text(encoding="utf-8")
        assert "run_id=r-perrun-3" in text
        assert "narrative=" in text and "r-perrun-3.log" in text
        assert "payload=" in text and "r-perrun-3.payload.log" in text

    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["bash", "promote.sh", "live"], "live"),
            (["bash", "promote.sh", "sandbox"], "sandbox"),
            (["bash", "promote.sh", "demo"], "demo"),
            (["bash", "promote.sh"], "demo"),  # unresolvable ⇒ shell default
        ],
    )
    def test_env_scope_comes_from_the_argv_token(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder,
        argv: list[str], expected: str,
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        uj.spawn_executor(argv, install, {}, run_id="r-scope-1")
        path = recorder.popen_calls[0]["argv"]
        assert any(
            t == f"--setenv=ENSEMBLE_UPGRADE_LOG_FILE="
                 f"{install / 'data' / 'upgrade' / expected / 'r-scope-1.log'}"
            for t in path
        ), path

    def test_unresolvable_scope_never_degrades_to_live(self) -> None:
        assert uj._ceremony_env_scope(["bash", "x"]) == "demo"
        assert uj._ceremony_env_scope(None) == "demo"
        assert uj._ceremony_env_scope(["bash", "x", "LIVE"]) == "demo"


# ── D3-6/R7: the client stderr is a FILE, never a PIPE ──────────────────────


class TestNoClientPipe:
    def test_client_stderr_is_a_file(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        uj.spawn_executor(
            ["bash", "promote.sh", "demo", "--version", "v1"],
            install, {}, run_id="r-pipe-1",
        )
        kw = recorder.popen_calls[0]
        assert kw["stderr"] is not subprocess.PIPE, kw["stderr"]
        assert kw["stdout"] is subprocess.DEVNULL
        # the client's stderr file lives beside the payload and is the THIRD
        # name of the per-run family — `<run_id>.client.log` (A2, code council
        # 2026-10-30: the old `payload.with_suffix(".client.log")` produced
        # `<run_id>.payload.client.log`, a fourth name the retention fence did
        # not cover as the current run's file)
        expected = install / "data" / "upgrade" / "demo" / "r-pipe-1.client.log"
        assert expected.is_file(), sorted(p.name for p in expected.parent.iterdir())
        assert Path(kw["stderr"].name) == expected
        assert not (install / "data" / "upgrade" / "demo"
                    / "r-pipe-1.payload.client.log").exists()

    def test_systemd_unavailable_triage_reads_the_client_file(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        """R7: redirecting the client's stderr to a file must NOT silence the
        fail-closed systemd-unavailable refusal."""
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        recorder.next_proc = FakeProc(rc=1)

        original = recorder.popen

        def popen_writing_signature(argv, **kwargs):
            proc = original(argv, **kwargs)
            target = kwargs.get("stderr")
            if hasattr(target, "write"):
                target.write(b"Failed to connect to bus: No such file or directory")
                target.flush()
            return proc

        monkeypatch.setattr(uj.subprocess, "Popen", popen_writing_signature)
        with pytest.raises(uj.ExecutorSystemdUnavailable):
            uj.spawn_executor(
                ["bash", "promote.sh", "live"], install, {}, run_id="r-pipe-2",
            )


    def test_unreadable_client_log_refuses_instead_of_guessing(
        self, install: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        """A7 (code council 2026-10-30): the fail-closed systemd-unavailable
        triage had a silent hole — if the client log could not be OPENED for
        append AND the tail read then failed, the empty tail read as "no
        systemd signature" and the code fell through to "payload failed fast —
        journal is truth": a conclusion the triage could not observe (and if
        systemd-run really had failed, NO shell ever runs, so the journal stays
        silent and the operator sees nothing). Both halves must fail, which is
        why the double-failure case is pinned explicitly."""
        _stub_detect(monkeypatch, uj.SERVICE_BRANCH_SERVICE, "user")
        recorder.next_proc = FakeProc(rc=1)

        real_open = Path.open

        def open_without_append(self: Path, mode: str = "r", *a, **k):
            if self.name.endswith(".client.log") and mode == "ab":
                raise OSError("read-only file system (A7 fixture)")
            return real_open(self, mode, *a, **k)

        monkeypatch.setattr(Path, "open", open_without_append)
        with pytest.raises(uj.ExecutorSystemdUnavailable) as exc:
            uj.spawn_executor(
                ["bash", "promote.sh", "live"], install, {}, run_id="r-a7-1",
            )
        assert "client-log-unavailable" in str(exc.value)


# ── T4.4/D3-9: the enriched still-running row + the unchanged 660 s ─────────


class TestStillRunningRowEnriched:
    def _service(self):
        from daemon.services.upgrade_journal_sweep import UpgradeJournalSweepService

        return object.__new__(UpgradeJournalSweepService)

    def test_row_carries_narrative_phase_and_elapsed(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = "r-still-1"
        narr = install / "data" / "upgrade" / "live" / f"{run_id}.log"
        narr.parent.mkdir(parents=True, exist_ok=True)
        narr.write_text(
            "2026-01-01T00:00:00Z phase-marker: promote:preflight\n"
            "2026-01-01T00:00:05Z phase-marker: promote:soak\n",
            encoding="utf-8",
        )
        state = install / "releases" / "state.json"
        state.write_text(
            '{"current":null,"previous":null,"in_flight":null,'
            '"history":[{"ts":"2026-01-01T00:00:05Z","event":"x","detail":"y"}]}',
            encoding="utf-8",
        )
        job = SimpleNamespace(install_dir=install, run_id=run_id, pid=4321)
        asyncio.run(
            self._service()._journal_executor_still_running(job, 660)
        )
        hist = uj.journal_read(install)["history"]
        row = hist[-1]
        assert row["event"] == "executor_still_running", row
        detail = row["detail"]
        assert f"narrative={narr}" in detail, detail
        assert "last=2026-01-01T00:00:05Z phase-marker: promote:soak" in detail, detail
        assert re.search(r"last_journal_activity=\d+s ago", detail), detail
        assert "still running after 660s" in detail, detail

    def test_row_degrades_gracefully_without_a_narrative(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        job = SimpleNamespace(install_dir=install, run_id="r-still-2", pid=4322)
        asyncio.run(self._service()._journal_executor_still_running(job, 660))
        detail = uj.journal_read(install)["history"][-1]["detail"]
        assert "narrative=(no per-run narrative)" in detail, detail
        assert "last=unknown" in detail, detail

    def test_reaper_timeout_default_is_still_660(self) -> None:
        """M21: the enrichment must not move the threshold."""
        from daemon import config as cfg
        from daemon.services.upgrade_journal_sweep import (
            DEFAULT_REAPER_TIMEOUT_SECONDS,
        )

        assert DEFAULT_REAPER_TIMEOUT_SECONDS == 660
        settings_field = cfg.ServicesConfig.model_fields[
            "upgrade_journal_reaper_timeout_seconds"
        ]
        assert settings_field.default == 660
        ge = [getattr(m, "ge", None) for m in (settings_field.metadata or [])]
        assert 60 in ge, f"the sub-minute floor must stay 60s: {settings_field.metadata}"


# ── T2.17/A6: the live-lane soak refusal — executed, fenced ─────────────────

_LIVE_PATH_PATTERNS = ("/home/nea/agents-ensemble", "/home/nea/ensemble-src")
_REPO = Path(__file__).resolve().parents[3]


def _assert_not_live(path: Path) -> None:
    s = str(path)
    for pat in _LIVE_PATH_PATTERNS:
        assert not (s == pat or s.startswith(pat + "/")), f"FENCE: live path {s}"


@pytest.fixture
def shell_fence(tmp_path: Path):
    """A fenced sandbox for the two promote.sh legs.

    FENCE (phase5 §3, canonical): ``HOME`` and the live-lane install root are
    ``tmp_path``-derived and ASSERTED non-live BEFORE any pipeline script runs;
    ``POSTGRES_*`` / ``ENSEMBLE_*`` are scrubbed PER VARIABLE (the
    ``env -u POSTGRES_*`` idiom is a silent NO-OP in bash and is banned);
    the port is explicit and never 9797/7979.
    """
    home = tmp_path / "home"
    live_root = home / "agents-ensemble"  # what `resolve_env live` derives
    (live_root / "releases").mkdir(parents=True)
    (live_root / ".env").write_text("PORT=8079\n", encoding="utf-8")
    uj.journal_init(live_root)  # a real journal, so the halt row is assertable
    assert not (live_root / ".env").read_text().count("9797")

    env: dict[str, str] = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(tmp_path / "tmp"),
        "PORT": "8079",
        "LOG_TAG": "pf-pack-h",
        "ENSEMBLE_PROMOTE_SOAK_S": "600",
    }
    (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)

    # FENCE ASSERTION — resolve the live env root the way the script will and
    # refuse to proceed if it is not inside tmp_path (fails closed).
    probe = subprocess.run(
        ["bash", "-c",
         '. "$1/scripts/upgrade/lib.sh"; resolve_env live; '
         'printf "%s" "$INSTALL_DIR"', "_", str(_REPO)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120,
    )
    resolved = Path(probe.stdout.strip() or "/nonexistent")
    if probe.returncode != 0 or not str(resolved).startswith(str(tmp_path)):
        pytest.fail(
            "FENCE: refusing to invoke promote.sh — the live install root "
            f"resolved OUTSIDE the sandbox (rc={probe.returncode}, "
            f"resolved={resolved!r}, stderr={probe.stderr[-400:]!r})"
        )
    _assert_not_live(resolved)
    return SimpleNamespace(home=home, install=resolved, env=env, tmp=tmp_path)


def _run_promote(fence, target: str, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = dict(fence.env)
    env.update(extra_env)
    _assert_not_live(fence.install)
    return subprocess.run(
        ["timeout", "-k", "5", "120",
         "bash", str(_REPO / "scripts" / "upgrade" / "promote.sh"),
         target, "--version", "v0.0.1-test", "--f2-verified-closed"],
        cwd=str(fence.tmp), env=env, capture_output=True, text=True, timeout=180,
    )


class TestLiveSoakPolicyRefusal:
    def test_live_arm_with_soak_over_300_refuses(self, shell_fence) -> None:
        """T2.17/A6: the long-soak knob is a SANDBOX drill; the live lane
        refuses it (exit 78) before any staged-ness or journal work."""
        proc = _run_promote(
            shell_fence, "live", {"ENSEMBLE_UPGRADE_LIVE": "1"},
        )
        assert proc.returncode == 78, (proc.returncode, proc.stderr[-800:])
        assert "refused on the live lane" in proc.stderr, proc.stderr[-1500:]
        # the refusal is the FIRST policy event, not a downstream accident
        assert "not staged" not in proc.stderr.lower(), proc.stderr[-800:]
        # and it is a JOURNALED halt naming the knob, not a bare exit
        row = uj.journal_read(shell_fence.install)["history"][-1]
        assert row["event"] == "halt", row
        assert "exceeds 300 on the live lane" in row["detail"], row

    def test_same_soak_is_accepted_on_a_sandbox_target(self, shell_fence) -> None:
        """The acceptance side of the same predicate: the sandbox lane does
        NOT emit the live-lane refusal (it proceeds until its own staged-ness
        check, which is the expected next refusal in an empty sandbox)."""
        proc = _run_promote(
            shell_fence, "sandbox",
            {"INSTALL_DIR": str(shell_fence.install), "PORT": "8079"},
        )
        assert "exceeds 300 on the live lane" not in proc.stderr, proc.stderr[-1500:]

    def test_live_refusal_is_pinned_ahead_of_the_deadline_arming(self) -> None:
        """Structural ordering pin: the live soak guard must sit BEFORE the
        preflight deadline machinery (a refusal after the deadline armed would
        leave the ceremony half-armed)."""
        text = (_REPO / "scripts" / "upgrade" / "promote.sh").read_text(
            encoding="utf-8"
        )
        guard = text.find('if [ "$UP_TARGET" = "live" ] && [ "$SOAK_S" -gt 300 ]')
        assert guard > 0, "the live-lane SOAK_S guard is missing from promote.sh"
        deadline = text.find("_enforce_deadline")
        assert deadline > 0
        assert guard < deadline, "the live soak guard must precede the deadline arming"
