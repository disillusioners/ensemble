"""Boot-reconcile + Phase-3 integration pins (bash-tool orphan reaper).

Task 3.1-3.5 acceptance lives here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

import daemon.services.bash_spawn_reaper as reaper
from daemon.services.bash_spawn_reaper import (
    ENV_BOOT_SWEEP,
    ENV_KILL_SWITCH,
    reconcile_stale_bash_spawns,
    sweep_instance,
    sweep_open_rows_for_shutdown,
)


def _engine():
    from daemon.repositories.bash_spawn import BashSpawnRecord  # noqa: F401

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


def _repo(eng=None):
    from daemon.repositories.bash_spawn import BashSpawnRepository

    if eng is None:
        eng = _engine()
    return BashSpawnRepository(engine=eng), eng


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


def _get_row(repo, row_id):
    from sqlmodel import Session

    from daemon.repositories.bash_spawn import BashSpawnRecord

    with Session(repo.engine) as session:
        return session.get(BashSpawnRecord, row_id)


# ── 3.1 candidate selection ──────────────────────────────────────────────────
class TestCandidateSelection:
    def test_foreign_boot_only(self, monkeypatch):
        monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)
        monkeypatch.delenv(ENV_BOOT_SWEEP, raising=False)
        repo, eng = _repo()
        try:
            from daemon.services.bash_spawn_reaper import CURRENT_BOOT_ID

            now = datetime.now(timezone.utc)
            repo.insert_open_row(
                pid=9001, pgid=9001, start_time=None, tracking_id="a" * 32,
                instance_id="i", command_sanitized="sleep", boot_id="previous-boot",
                daemon_pid=999999, created_at=now.isoformat(),
            )
            repo.insert_open_row(
                pid=9002, pgid=9002, start_time=None, tracking_id="b" * 32,
                instance_id="i", command_sanitized="sleep", boot_id=CURRENT_BOOT_ID,
                daemon_pid=999999, created_at=now.isoformat(),
            )
            rows = repo.list_open_previous_boot(CURRENT_BOOT_ID, os.getpid())
            assert len(rows) == 1
            assert rows[0].boot_id == "previous-boot"
        finally:
            eng.dispose()

    def test_null_and_empty_boot_id_listed(self, monkeypatch):
        monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)
        monkeypatch.delenv(ENV_BOOT_SWEEP, raising=False)
        repo, eng = _repo()
        try:
            from daemon.services.bash_spawn_reaper import CURRENT_BOOT_ID

            now = datetime.now(timezone.utc)
            for i, bid in enumerate((None, "")):
                repo.insert_open_row(
                    pid=9100 + i, pgid=9100 + i, start_time=None,
                    tracking_id=("c" * 31 + str(i + 1)),
                    instance_id="i", command_sanitized="sleep", boot_id=bid,
                    daemon_pid=999999, created_at=now.isoformat(),
                )
            rows = repo.list_open_previous_boot(CURRENT_BOOT_ID, os.getpid())
            assert len(rows) == 2
        finally:
            eng.dispose()

    def test_current_boot_rows_never_listed(self, monkeypatch):
        monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)
        monkeypatch.delenv(ENV_BOOT_SWEEP, raising=False)
        repo, eng = _repo()
        try:
            from daemon.services.bash_spawn_reaper import CURRENT_BOOT_ID

            now = datetime.now(timezone.utc)
            repo.insert_open_row(
                pid=9200, pgid=9200, start_time=None, tracking_id="d" * 32,
                instance_id="i", command_sanitized="sleep", boot_id=CURRENT_BOOT_ID,
                daemon_pid=os.getpid(), created_at=now.isoformat(),
            )
            rows = repo.list_open_previous_boot(CURRENT_BOOT_ID, os.getpid())
            assert rows == []
        finally:
            eng.dispose()

    async def test_sibling_daemon_row_skipped(self, monkeypatch):
        monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)
        monkeypatch.delenv(ENV_BOOT_SWEEP, raising=False)
        repo, eng = _repo()
        try:
            from daemon.services.bash_spawn_reaper import CURRENT_BOOT_ID

            foreign_pid = 777777
            now = datetime.now(timezone.utc)
            row = repo.insert_open_row(
                pid=9300, pgid=9300, start_time=None, tracking_id="e" * 32,
                instance_id="i", command_sanitized="sleep",
                boot_id="previous-boot", daemon_pid=foreign_pid,
                created_at=now.isoformat(),
            )

            def _kill(pid, sig):
                if pid == foreign_pid and sig == 0:
                    return None
                raise ProcessLookupError(pid)

            monkeypatch.setattr(reaper.os, "kill", _kill)
            monkeypatch.setattr(
                reaper, "process_start_wallclock",
                lambda pid: (now - timedelta(seconds=10)) if pid == foreign_pid else None,
            )
            monkeypatch.setattr(reaper, "list_group_members", lambda pgid: [])

            # dead child probe for the row pid
            closed = await reconcile_stale_bash_spawns(
                repo, current_boot_id="current-boot", my_pid=os.getpid(),
                term_grace_seconds=0.5,
            )
            # sibling guard skips -> row stays open, 0 closed
            assert closed == 0
            fresh = _get_row(repo, row.id)
            assert fresh.reaped_at is None
        finally:
            eng.dispose()

    async def test_null_boot_id_rows_listed_by_reconcile(self, monkeypatch):
        monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)
        monkeypatch.delenv(ENV_BOOT_SWEEP, raising=False)
        repo, eng = _repo()
        try:
            # dead group -> gc closes
            monkeypatch.setattr(reaper.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError(pid)))
            monkeypatch.setattr(reaper, "list_group_members", lambda pgid: [])
            now = datetime.now(timezone.utc)
            row = repo.insert_open_row(
                pid=9400, pgid=9400, start_time=None, tracking_id="f" * 32,
                instance_id="i", command_sanitized="sleep", boot_id=None,
                daemon_pid=999999, created_at=now.isoformat(),
            )
            closed = await reconcile_stale_bash_spawns(
                repo, current_boot_id="current-boot", my_pid=os.getpid(),
                term_grace_seconds=0.5,
            )
            assert closed >= 1
            fresh = _get_row(repo, row.id)
            assert fresh.reaped_at is not None
        finally:
            eng.dispose()


# ── 3.2 M5 real-process leg + DB fault + leg knob ────────────────────────────
class TestBootReconcileLeg:
    async def test_m5_previous_boot_burner_killed(self, monkeypatch):
        monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)
        monkeypatch.delenv(ENV_BOOT_SWEEP, raising=False)
        monkeypatch.setattr(reaper.time, "sleep", lambda s: None)

        proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
        repo, eng = _repo()
        try:
            now = datetime.now(timezone.utc)
            row = repo.insert_open_row(
                pid=proc.pid, pgid=proc.pid, start_time=None,
                tracking_id="a" * 32, instance_id="i", command_sanitized="sleep 60",
                boot_id="previous-boot", daemon_pid=999999,
                created_at=now.isoformat(),
            )

            def _kill(pid, sig):
                if pid == proc.pid and sig == 0:
                    return None
                if pid == proc.pid:
                    raise AssertionError("unexpected signal in test")
                raise ProcessLookupError(pid)

            monkeypatch.setattr(reaper.os, "kill", _kill)
            # spy killpg -> real delivery (M5 real-process leg)
            _orig_killpg = os.killpg
            def _spy_killpg(pgid, sig):
                return _orig_killpg(pgid, sig)
            monkeypatch.setattr(reaper.os, "killpg", _spy_killpg)
            os.killpg = _spy_killpg
            monkeypatch.setattr(
                reaper, "process_start_wallclock",
                lambda pid: (now - timedelta(seconds=1)) if pid == proc.pid else None,
            )
            monkeypatch.setattr(
                reaper, "list_group_members",
                lambda pgid: [proc.pid] if pgid == proc.pid else [],
            )

            closed = await reconcile_stale_bash_spawns(
                repo, current_boot_id="current-boot", my_pid=os.getpid(),
                term_grace_seconds=0.5,
            )
            assert closed >= 1
            # burner dead
            await asyncio.sleep(0.5)
            assert proc.poll() is not None
            fresh = _get_row(repo, row.id)
            assert fresh.reaped_at is not None
        finally:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            eng.dispose()

    async def test_current_boot_rows_untouched(self, monkeypatch):
        monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)
        monkeypatch.delenv(ENV_BOOT_SWEEP, raising=False)
        repo, eng = _repo()
        try:
            from daemon.services.bash_spawn_reaper import CURRENT_BOOT_ID

            now = datetime.now(timezone.utc)
            row = repo.insert_open_row(
                pid=9500, pgid=9500, start_time=None, tracking_id="g" * 32,
                instance_id="i", command_sanitized="sleep", boot_id=CURRENT_BOOT_ID,
                daemon_pid=os.getpid(), created_at=now.isoformat(),
            )
            monkeypatch.setattr(reaper.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError(pid)))
            monkeypatch.setattr(reaper, "list_group_members", lambda pgid: [])
            closed = await reconcile_stale_bash_spawns(
                repo, current_boot_id=CURRENT_BOOT_ID, my_pid=os.getpid(),
                term_grace_seconds=0.5,
            )
            assert closed == 0
            fresh = _get_row(repo, row.id)
            assert fresh.reaped_at is None
        finally:
            eng.dispose()

    async def test_db_fault_propagates(self, monkeypatch):
        monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)
        monkeypatch.delenv(ENV_BOOT_SWEEP, raising=False)
        repo = MagicMock()
        repo.list_open_previous_boot.side_effect = RuntimeError("db down")
        with pytest.raises(RuntimeError):
            await reconcile_stale_bash_spawns(
                repo, current_boot_id="current-boot", my_pid=os.getpid(),
            )

    async def test_leg_knob_zero_no_repo_reads(self, monkeypatch):
        monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)
        monkeypatch.setenv(ENV_BOOT_SWEEP, "0")
        repo = MagicMock()
        closed = await reconcile_stale_bash_spawns(
            repo, current_boot_id="current-boot", my_pid=os.getpid(),
        )
        assert closed == 0
        repo.list_open_previous_boot.assert_not_called()

    async def test_master_off_no_repo_reads(self, monkeypatch):
        monkeypatch.setenv(ENV_KILL_SWITCH, "0")
        monkeypatch.delenv(ENV_BOOT_SWEEP, raising=False)
        repo = MagicMock()
        closed = await reconcile_stale_bash_spawns(
            repo, current_boot_id="current-boot", my_pid=os.getpid(),
        )
        assert closed == 0
        repo.list_open_previous_boot.assert_not_called()


# ── 3.4 M6 executor survival ─────────────────────────────────────────────────
async def test_restart_executor_child_survives_all_reaper_sweeps(tmp_path, monkeypatch):
    import importlib

    uj = importlib.import_module("daemon.tools.upgrade_journal")

    install = tmp_path / "install"
    (install / "releases").mkdir(parents=True)
    (install / "data").mkdir(parents=True, exist_ok=True)
    script = tmp_path / "sleep.sh"
    script.write_text("#!/bin/bash\nsleep 30\n", encoding="utf-8")
    script.chmod(0o755)

    monkeypatch.setattr(
        uj, "_service_detect_fn",
        lambda env=None: (uj.SERVICE_BRANCH_LEGACY, "", "forced"),
    )

    pid, mode_note = uj.spawn_executor(
        ["bash", str(script)], install, {"RUN_ID": "r-exec"}, run_id="r-exec",
    )
    assert mode_note == "(daemonized, start_new_session)"

    repo, eng = _repo()
    bash_mod = importlib.import_module("daemon.tools.bash")
    from daemon.tools.bash import BashProcessRegistry

    bash_reg = BashProcessRegistry()
    bash_reg.set_durable_writer(repo)
    monkeypatch.setattr(bash_mod, "get_bash_process_registry", lambda: bash_reg)

    try:
        # purity: no stamp in child env
        completed = subprocess.run(
            ["ps", "eww", "-p", str(pid)], capture_output=True, text=True, timeout=5,
        )
        assert "ENSEMBLE_BASH_TRACKING_ID" not in (completed.stdout or "")

        # no row created
        assert repo.list_open_all() == []

        # (a) cleanup_instance
        await bash_reg.cleanup_instance("exec-instance", reaped_by="terminal-tier")
        assert _pid_alive(pid)

        # (b) shutdown durable sweep
        await sweep_open_rows_for_shutdown(repo, my_pid=os.getpid())
        assert _pid_alive(pid)

        # (c) boot reconcile
        await reconcile_stale_bash_spawns(
            repo, current_boot_id="current-boot", my_pid=os.getpid(),
        )
        assert _pid_alive(pid)

        assert repo.list_open_all() == []
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        eng.dispose()


# ── 3.5 daemon-never-targeted ────────────────────────────────────────────────
class TestDaemonNeverTargeted:
    async def _run_leg(self, monkeypatch, repo, leg):
        daemon_group = os.getpgid(os.getpid())
        self_pid = os.getpid()
        now = datetime.now(timezone.utc)
        row1 = repo.insert_open_row(
            pid=self_pid, pgid=daemon_group, start_time=None,
            tracking_id="p" * 32, instance_id="inst-x",
            command_sanitized="x", boot_id="previous-boot",
            daemon_pid=999999, created_at=now.isoformat(),
        )
        row2 = repo.insert_open_row(
            pid=self_pid, pgid=1, start_time=None,
            tracking_id="q" * 32, instance_id="inst-x",
            command_sanitized="x", boot_id="previous-boot",
            daemon_pid=999999, created_at=now.isoformat(),
        )
        row3 = repo.insert_open_row(
            pid=self_pid, pgid=999700, start_time=None,
            tracking_id="r" * 32, instance_id="inst-x",
            command_sanitized="x", boot_id="previous-boot",
            daemon_pid=999999, created_at=now.isoformat(),
        )

        signals = []

        def _kill(pid, sig):
            if sig == 0:
                # signal-0 liveness probe passes through (not a real
                # signal delivery); only ACTUAL signals are fenced.
                return None
            signals.append(("kill", pid, sig))
            raise AssertionError(f"os.kill invoked: {pid} {sig}")

        def _killpg(pgid, sig):
            signals.append(("killpg", pgid, sig))
            raise AssertionError(f"os.killpg invoked: {pgid} {sig}")

        monkeypatch.setattr(reaper.os, "kill", _kill)
        monkeypatch.setattr(reaper.os, "killpg", _killpg)
        monkeypatch.setattr(reaper, "list_group_members", lambda pgid: [])

        if leg == "instance":
            await sweep_instance(repo, "inst-x", reaped_by="terminal-tier", my_pid=self_pid)
        elif leg == "shutdown":
            await sweep_open_rows_for_shutdown(repo, my_pid=self_pid)
        elif leg == "reconcile":
            await reconcile_stale_bash_spawns(
                repo, current_boot_id="current-boot", my_pid=self_pid,
            )

        assert signals == [], f"signals delivered in {leg}: {signals}"

        for r in (row1, row2, row3):
            fresh = _get_row(repo, r.id)
            assert fresh.reaped_at is not None, f"row {r.id} not closed in {leg}"
            assert (fresh.reap_reason or "").startswith("refused:"), (
                f"row {r.id} reason={fresh.reap_reason!r} in {leg}"
            )

    async def test_instance_leg(self, monkeypatch):
        repo, eng = _repo()
        try:
            await self._run_leg(monkeypatch, repo, "instance")
        finally:
            eng.dispose()

    async def test_shutdown_leg(self, monkeypatch):
        repo, eng = _repo()
        try:
            await self._run_leg(monkeypatch, repo, "shutdown")
        finally:
            eng.dispose()

    async def test_reconcile_leg(self, monkeypatch):
        repo, eng = _repo()
        try:
            await self._run_leg(monkeypatch, repo, "reconcile")
        finally:
            eng.dispose()
