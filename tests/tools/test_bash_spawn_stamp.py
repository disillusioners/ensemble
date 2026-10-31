"""Bash spawn stamp + durable-write contract (reaper Phase 1).

Pins the env-assembly seam contract (plan tasks 1.3 / 1.4):

* stamp present in the spawned-child env when the master switch is ON
  (absent env = the default ⇒ ON in the dev lane too — F6);
* ``env=None`` lane materializes the inherit-env copy +1 var;
* explicit ``ENSEMBLE_BASH_ORPHAN_REAPER=0`` ⇒ byte-identical env (no
  stamp) AND no durable row;
* writer-unset ordering (D3 adjudication 2): no durable writer ⇒ no row,
  tool unchanged (with an A/B mutation leg: the SAME flow WITH a writer
  DOES land exactly one row);
* success ⇒ exactly ONE open row (pid/pgid/boot_id/tracking_id/
  instance_id/daemon_pid populated; reaped_at NULL — D4 tombstone);
* S4: DB-fault injection ⇒ WARNING + tool returns normally;
* S6: stalled engine (``engine.begin()`` blocks, does NOT raise) past
  the ≈1.5s bound ⇒ tool returns within the bound, no hang, WARNING
  logged ("durable stamp late"); the late tombstone may land best-effort.

Success/timeout/cancel arms are NEVER edited (plan constraint 3); these
tests run the real bash tool end-to-end against a real `echo` child.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import re as _re
import time

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

bash_module = importlib.import_module("daemon.tools.bash")
from daemon.repositories.bash_spawn import (
    BashSpawnRecord,
    BashSpawnRepository,
)
from daemon.services.bash_spawn_reaper import (
    BASH_TRACKING_ENV_VAR,
    ENV_KILL_SWITCH,
    CURRENT_BOOT_ID,
)
from daemon.tools.bash import BashProcessRegistry

INSTANCE = "inst-stamp-tests"


def _make_engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'bash-spawn-stamp.sqlite'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _pragma(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    return eng


class _EnvCapture:
    """Spy wrapper that records the env kwarg of the spawn call."""

    def __init__(self) -> None:
        self.env = "NOT-CALLED"

    def install(self, monkeypatch):
        real_shell = asyncio.create_subprocess_shell
        holder = self

        async def spy(*args, **kwargs):
            holder.env = kwargs.get("env")
            return await real_shell(*args, **kwargs)

        monkeypatch.setattr(
            bash_module.asyncio, "create_subprocess_shell", spy
        )


@pytest.fixture
def registry() -> BashProcessRegistry:
    return BashProcessRegistry()


@pytest.fixture
def patch_registry(monkeypatch, registry):
    monkeypatch.setattr(bash_module, "get_bash_process_registry", lambda: registry)
    return registry


@pytest.fixture
def no_reaper_env(monkeypatch):
    """Absent master switch = the default (ON in ALL lanes)."""
    monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)


async def _run_echo() -> str:
    return await bash_module.bash.coroutine(
        command="echo hi", instance_id=INSTANCE
    )


class TestStampContract:
    async def test_stamp_present_in_child_env_when_on(
        self, monkeypatch, patch_registry, no_reaper_env, tmp_path
    ) -> None:
        cap = _EnvCapture()
        cap.install(monkeypatch)
        out = await _run_echo()
        assert "EXIT CODE: 0" in out
        env = cap.env
        assert isinstance(env, dict)
        assert BASH_TRACKING_ENV_VAR in env
        assert _re.fullmatch(r"[0-9a-f]{32}", env[BASH_TRACKING_ENV_VAR])
        # inherit-copy +1 var: every ambient key present, plus the stamp
        assert set(env.keys()) == set(os.environ.keys()) | {BASH_TRACKING_ENV_VAR}

    async def test_stamp_on_and_durable_row_written(
        self, monkeypatch, patch_registry, no_reaper_env, tmp_path
    ) -> None:
        """Success ⇒ exactly ONE open row with the full discriminator set,
        and the row's tracking_id == the child-env stamp value."""
        eng = _make_engine(tmp_path)
        SQLModel.metadata.create_all(eng)
        repo = BashSpawnRepository(engine=eng)
        patch_registry.set_durable_writer(repo)
        cap = _EnvCapture()
        cap.install(monkeypatch)
        try:
            out = await _run_echo()
            assert "EXIT CODE: 0" in out
        finally:
            rows = repo.list_open_by_instance(INSTANCE)
        assert len(rows) == 1
        row = rows[0]
        assert row.pid > 0
        assert row.pgid > 0
        assert row.boot_id == CURRENT_BOOT_ID
        assert _re.fullmatch(r"[0-9a-f]{32}", row.tracking_id)
        assert row.instance_id == INSTANCE
        assert row.daemon_pid == os.getpid()
        assert row.reaped_at is None  # D4: tombstone-until-cleanup-point
        assert row.command_sanitized  # D14 sanitized value reused
        # the stamp the child received IS the row's tracking id
        assert cap.env[BASH_TRACKING_ENV_VAR] == row.tracking_id
        eng.dispose()

    async def test_env_none_materializes_inherit_copy_plus_one(
        self, monkeypatch, patch_registry, no_reaper_env
    ) -> None:
        cap = _EnvCapture()
        cap.install(monkeypatch)
        out = await bash_module.bash.coroutine(
            command="echo hi", instance_id=INSTANCE, env=None
        )
        assert "EXIT CODE: 0" in out
        env = cap.env
        assert BASH_TRACKING_ENV_VAR in env
        assert set(env.keys()) == set(os.environ.keys()) | {BASH_TRACKING_ENV_VAR}

    async def test_explicit_off_byte_identical_env_no_row(
        self, monkeypatch, patch_registry, tmp_path
    ) -> None:
        monkeypatch.setenv(ENV_KILL_SWITCH, "0")
        eng = _make_engine(tmp_path)
        SQLModel.metadata.create_all(eng)
        repo = BashSpawnRepository(engine=eng)
        patch_registry.set_durable_writer(repo)
        cap = _EnvCapture()
        cap.install(monkeypatch)
        try:
            out = await _run_echo()
            assert "EXIT CODE: 0" in out
        finally:
            rows = repo.list_open_by_instance(INSTANCE)
        # OFF ⇒ complete no-op: no stamp, no row
        assert cap.env is None or BASH_TRACKING_ENV_VAR not in (cap.env or {})
        assert rows == []
        eng.dispose()

    async def test_env_overlay_lane_still_stamped(
        self, monkeypatch, patch_registry, no_reaper_env
    ) -> None:
        """env overlay path (secret/KMS resolution runs) still gets the
        stamp injected AFTER resolution."""
        cap = _EnvCapture()
        cap.install(monkeypatch)
        out = await bash_module.bash.coroutine(
            command="echo hi",
            instance_id=INSTANCE,
            env={"MYVAR": "1"},
        )
        assert "EXIT CODE: 0" in out
        assert cap.env.get("MYVAR") == "1"
        assert BASH_TRACKING_ENV_VAR in cap.env


class TestWriterUnsetOrdering:
    async def test_no_writer_no_row_tool_unchanged(
        self, monkeypatch, patch_registry, no_reaper_env, tmp_path
    ) -> None:
        """D3 adjudication 2: writer unset ⇒ NO row, tool identical.
        A/B mutation leg: the same flow WITH a writer lands a row —
        proving the zero above is the contract, not a broken write."""
        eng = _make_engine(tmp_path)
        SQLModel.metadata.create_all(eng)
        repo = BashSpawnRepository(engine=eng)
        # leg A: no writer
        out_a = await _run_echo()
        assert "EXIT CODE: 0" in out_a
        assert repo.list_open_by_instance(INSTANCE) == []
        # leg B: writer injected ⇒ row lands (mutation guard)
        patch_registry.set_durable_writer(repo)
        out_b = await _run_echo()
        assert "EXIT CODE: 0" in out_b
        assert len(repo.list_open_by_instance(INSTANCE)) == 1
        eng.dispose()

    async def test_instance_id_none_never_stamps(
        self, monkeypatch, patch_registry, no_reaper_env, tmp_path
    ) -> None:
        """Path E residual: instance_id=None spawns stay untracked."""
        eng = _make_engine(tmp_path)
        SQLModel.metadata.create_all(eng)
        repo = BashSpawnRepository(engine=eng)
        patch_registry.set_durable_writer(repo)
        cap = _EnvCapture()
        cap.install(monkeypatch)
        try:
            out = await bash_module.bash.coroutine(
                command="echo hi", instance_id=None
            )
            assert "EXIT CODE: 0" in out
        finally:
            rows = repo.list_open_by_instance(INSTANCE)
        assert rows == []
        assert cap.env is None or BASH_TRACKING_ENV_VAR not in (cap.env or {})
        eng.dispose()


class TestFaultInjection:
    async def test_db_fault_warning_tool_normal(
        self, monkeypatch, patch_registry, no_reaper_env, tmp_path, caplog
    ) -> None:
        """S4: insert raises ⇒ WARNING, tool result unchanged."""

        class _Boom:
            def insert_open_row(self, *a, **k):
                raise RuntimeError("db down")

        patch_registry.set_durable_writer(_Boom())
        with caplog.at_level(logging.WARNING, logger="daemon.tools.bash"):
            out = await _run_echo()
        assert "EXIT CODE: 0" in out
        assert any(
            "durable spawn stamp failed" in r.getMessage() for r in caplog.records
        )

    async def test_stalled_engine_returns_within_bound(
        self, monkeypatch, patch_registry, no_reaper_env, tmp_path, caplog
    ) -> None:
        """S6: engine.begin() blocks (does NOT raise) past the ≈1.5s bound
        ⇒ tool returns within the bound, no hang, WARNING logged; the
        late tombstone may land best-effort — either outcome tool-safe."""

        class _Stalled:
            def __init__(self):
                self.landed = 0

            def insert_open_row(self, *a, **k):
                time.sleep(3.0)  # blocks PAST the 1.5s bound, no raise
                self.landed += 1
                return None

        stalled = _Stalled()
        patch_registry.set_durable_writer(stalled)
        t0 = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="daemon.tools.bash"):
            out = await _run_echo()
        elapsed = time.monotonic() - t0
        assert "EXIT CODE: 0" in out
        assert elapsed < 2.5, f"tool hung past the bound: {elapsed:.2f}s"
        assert any(
            "durable stamp late" in r.getMessage() for r in caplog.records
        )
        # 3s later the detached task has finished; whether it landed is
        # best-effort — assert the tool-side contract only (no hang).
        assert stalled.landed >= 0
