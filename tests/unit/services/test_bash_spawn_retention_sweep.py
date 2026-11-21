"""Retention sweep service pack (census A2 fix, 2026-11-21).

Defect being fixed: the bash spawn reaper's five lanes are
EVENT-PIGGYBACKED (no standalone loop) — closed-row retention ran ONLY
inside the one-shot boot reconcile, and open rows closed only when
their owning instance terminalized. Registry growth was unbounded
between boots (live v0.31.22: 194k rows / 70MB; last reap ≈1h after
boot, then 0 reaps of 3,604 spawns).

``BashSpawnRetentionSweepService`` is the periodic companion lane:

* Leg 1 — the boot reconcile's 30d prune, now PERIODIC (runs every
  tick, not once per boot).
* Leg 2 — the stall-mode fix: OPEN rows whose OWNING INSTANCE IS
  TERMINAL are swept through the existing ``sweep_instance``
  machinery (``reaped_by='retention-sweep'``); rows of non-terminal
  owners are never touched.
* The master switch is honored BEFORE any repository touch; both
  no-op classes (switch OFF / writer unset) are counted and
  rate-limited-logged — never silent again.

Self-contained in-memory SQLite (StaticPool) per the
tests/repositories conftest convention.
"""

from __future__ import annotations

import asyncio
import logging
import os
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from daemon.repositories.bash_spawn import BashSpawnRecord
from daemon.repositories.bash_spawn.repository import BashSpawnRepository
from daemon.services.bash_spawn_reaper import (
    DEFAULT_RETENTION_INTERVAL_SECONDS,
    ENV_KILL_SWITCH,
    ENV_RETENTION_INTERVAL,
    MAX_RETENTION_INTERVAL_SECONDS,
    MIN_RETENTION_INTERVAL_SECONDS,
    BashSpawnRetentionSweepService,
    retention_interval_seconds_from_env,
)

_RETENTION_LOGGER = "daemon.services.bash_spawn_reaper"


def _make_engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def engine():
    eng = _make_engine()
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine):
    return BashSpawnRepository(engine=engine)


@pytest.fixture(autouse=True)
def _fresh_noop_counters():
    """Reset the module-global no-op skip counters per test — they are
    process-lifetime observability state and would otherwise mute the
    rate-limited log for every test after the first."""
    from daemon.services import bash_spawn_reaper as reaper_module

    reaper_module._NOOP_SKIP_COUNTS = {}
    yield
    reaper_module._NOOP_SKIP_COUNTS = {}


class _FakeInstanceRepo:
    """instance_id → status lookup; mirrors SQLModelInstanceRepository.get."""

    def __init__(self, statuses: dict[str, str]):
        self._statuses = statuses

    def get(self, instance_id: str):
        status = self._statuses.get(instance_id)
        if status is None:
            return None
        return SimpleNamespace(instance_id=instance_id, status=status)


def _open_row(instance_id: str, pgid: int, tracking_id: str) -> dict:
    return dict(
        pid=pgid,
        pgid=pgid,
        tracking_id=tracking_id,
        instance_id=instance_id,
        command_sanitized="bash -c sleep",
        daemon_pid=os.getpid(),  # self-owned rows always proceed (MAJOR-1)
    )


# ---------------------------------------------------------------------------
# 1. Leg 1 — the retention prune is PERIODIC (not boot-only)
# ---------------------------------------------------------------------------


class TestPeriodicPrune:
    @pytest.mark.asyncio
    async def test_prune_deletes_only_window_past_rows(self, repo):
        """Old reaped rows pruned; fresh reaped rows + open rows kept."""
        old = repo.insert_open_row(**_open_row("i1", 42001, "t-old"))
        repo.mark_reaped(old.id, "terminal-tier@1", "gc")
        fresh = repo.insert_open_row(**_open_row("i1", 42002, "t-fresh"))
        repo.mark_reaped(fresh.id, "terminal-tier@1", "gc")
        opener = repo.insert_open_row(**_open_row("i1", 42003, "t-open"))
        with repo.engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text(
                "UPDATE bash_spawn_registry SET reaped_at = :at "
                "WHERE id = :id"
            ), {"at": "2020-01-01T00:00:00+00:00", "id": old.id})

        svc = BashSpawnRetentionSweepService(
            repo,
            instance_repo=_FakeInstanceRepo({}),
            interval_seconds=1,
        )
        result = await svc.sweep_once()

        assert result["pruned"] == 1
        assert result["pruned_total"] == 1
        assert repo.list_open_by_instance("i1") != []
        # The fresh reaped row survives; only the old one was deleted.
        with repo.engine.begin() as conn:
            from sqlalchemy import text
            remaining = conn.execute(
                text("SELECT COUNT(*) FROM bash_spawn_registry")
            ).scalar()
        assert remaining == 2  # fresh reaped + open

    @pytest.mark.asyncio
    async def test_prune_fires_more_than_once_per_boot(self, repo):
        """THE regression pin: a row reaped BETWEEN ticks is pruned by
        the NEXT tick — the lane runs every tick, not once per boot
        (the boot reconcile's prune could never see it)."""
        svc = BashSpawnRetentionSweepService(
            repo,
            instance_repo=_FakeInstanceRepo({}),
            interval_seconds=1,
        )
        # Tick 1: nothing to do.
        assert (await svc.sweep_once())["pruned"] == 0

        # Between ticks: a spawn closes and ages past the window.
        row = repo.insert_open_row(**_open_row("i1", 42010, "t-mid"))
        repo.mark_reaped(row.id, "terminal-tier@1", "gc")
        with repo.engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text(
                "UPDATE bash_spawn_registry SET reaped_at = :at "
                "WHERE id = :id"
            ), {"at": "2020-01-01T00:00:00+00:00", "id": row.id})

        # Tick 2: the mid-boot row IS pruned.
        assert (await svc.sweep_once())["pruned"] == 1


# ---------------------------------------------------------------------------
# 2. Leg 2 — the terminal-instance stall sweep
# ---------------------------------------------------------------------------


class TestTerminalInstanceStallSweep:
    @pytest.mark.asyncio
    async def test_terminal_owner_open_rows_swept(self, repo):
        """Open rows whose owning instance is TERMINAL (completed) are
        closed by the tick via the existing sweep machinery; rows of a
        RUNNING owner are left untouched."""
        repo.insert_open_row(**_open_row("done-1", os.getpid() + 55001, "tk-d1"))
        repo.insert_open_row(**_open_row("done-1", os.getpid() + 55002, "tk-d2"))
        repo.insert_open_row(**_open_row("live-1", os.getpid() + 55003, "tk-l1"))

        svc = BashSpawnRetentionSweepService(
            repo,
            instance_repo=_FakeInstanceRepo({
                "done-1": "completed",
                "live-1": "running",
            }),
            interval_seconds=1,
        )
        result = await svc.sweep_once()

        assert result["terminal_swept"] == 1
        assert result["closed_total"] == 2  # both done-1 rows
        assert repo.list_open_by_instance("done-1") == []
        # The running owner's row is UNTOUCHED.
        remaining = repo.list_open_by_instance("live-1")
        assert len(remaining) == 1
        assert remaining[0].tracking_id == "tk-l1"
        # Audit trail: rows are CLOSED (reaped_at + reaped_by stamped),
        # never deleted — dead groups GC-close under the 'gc' label,
        # killed/refused rows close under the lane label; both are
        # sanctioned (models.py minor-3).
        with repo.engine.begin() as conn:
            from sqlalchemy import text
            rows = conn.execute(text(
                "SELECT reaped_by, reaped_at FROM bash_spawn_registry "
                "WHERE reaped_at IS NOT NULL"
            )).fetchall()
        assert len(rows) == 2, "terminal rows must be closed, not deleted"
        assert all(
            r[0] is not None and r[1] is not None for r in rows
        )

    @pytest.mark.asyncio
    async def test_paused_owner_rows_untouched(self, repo):
        """PAUSED is deliberately excluded (pause/resume semantics) —
        its open rows are never swept by this lane."""
        repo.insert_open_row(**_open_row("paused-1", os.getpid() + 55011, "tk-p1"))
        svc = BashSpawnRetentionSweepService(
            repo,
            instance_repo=_FakeInstanceRepo({"paused-1": "paused"}),
            interval_seconds=1,
        )
        result = await svc.sweep_once()

        assert result["terminal_swept"] == 0
        assert result["closed_total"] == 0
        assert len(repo.list_open_by_instance("paused-1")) == 1

    @pytest.mark.asyncio
    async def test_unknown_instance_rows_untouched(self, repo):
        """An open row whose instance row is GONE is left for the boot
        reconcile (conservative — never blind-close)."""
        repo.insert_open_row(**_open_row("ghost-1", os.getpid() + 55021, "tk-g1"))
        svc = BashSpawnRetentionSweepService(
            repo,
            instance_repo=_FakeInstanceRepo({}),  # get() → None
            interval_seconds=1,
        )
        result = await svc.sweep_once()

        assert result["closed_total"] == 0
        assert len(repo.list_open_by_instance("ghost-1")) == 1

    @pytest.mark.asyncio
    async def test_instance_repo_none_disables_stall_leg_visibly(
        self, repo, caplog
    ):
        """instance_repo=None ⇒ the stall leg is a counted, logged
        no-op; the prune leg still runs."""
        old = repo.insert_open_row(**_open_row("i1", 42001, "t-old"))
        repo.mark_reaped(old.id, "terminal-tier@1", "gc")
        with repo.engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text(
                "UPDATE bash_spawn_registry SET reaped_at = :at "
                "WHERE id = :id"
            ), {"at": "2020-01-01T00:00:00+00:00", "id": old.id})

        svc = BashSpawnRetentionSweepService(
            repo,
            instance_repo=None,
            interval_seconds=1,
        )
        with caplog.at_level(logging.WARNING, logger=_RETENTION_LOGGER):
            result = await svc.sweep_once()

        assert result["pruned"] == 1  # prune leg unaffected
        assert result["closed_total"] == 0
        assert any(
            "stall leg" in r.getMessage() for r in caplog.records
        )


# ---------------------------------------------------------------------------
# 3. No-op visibility — never silent again
# ---------------------------------------------------------------------------


class TestNoopVisibility:
    @pytest.mark.asyncio
    async def test_master_switch_off_is_visible_noop(self, repo, caplog, monkeypatch):
        """Switch OFF ⇒ zero repository access, zero work, and a
        WARNING (the silent no-op is gone)."""
        row = repo.insert_open_row(**_open_row("i1", os.getpid() + 55031, "tk-sw"))
        svc = BashSpawnRetentionSweepService(
            repo,
            instance_repo=_FakeInstanceRepo({"i1": "completed"}),
            interval_seconds=1,
        )
        monkeypatch.setenv(ENV_KILL_SWITCH, "0")
        with caplog.at_level(logging.WARNING, logger=_RETENTION_LOGGER):
            result = await svc.sweep_once()

        assert result["pruned"] == 0
        assert result["closed_total"] == 0
        assert len(repo.list_open_by_instance("i1")) == 1  # untouched
        assert any(
            "NO-OP" in r.getMessage() for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_repo_none_is_visible_noop(self, caplog):
        svc = BashSpawnRetentionSweepService(
            None,
            instance_repo=None,
            interval_seconds=1,
        )
        with caplog.at_level(logging.WARNING, logger=_RETENTION_LOGGER):
            result = await svc.sweep_once()

        assert result["pruned"] == 0
        assert result["closed_total"] == 0
        assert result["ticks"] == 1
        assert any(
            "durable writer" in r.getMessage() for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_enabled_false_test_seam_ticks_idle(self):
        """``enabled=False`` is the internal test seam — the tick is a
        plain idle no-op (production wiring hardcodes True)."""
        svc = BashSpawnRetentionSweepService(
            None,
            instance_repo=None,
            interval_seconds=1,
            enabled=False,
        )
        result = await svc.sweep_once()
        assert result["ticks"] == 1
        assert result["pruned"] == 0
        assert result["closed_total"] == 0


# ---------------------------------------------------------------------------
# 4. Counters + lifecycle + knobs
# ---------------------------------------------------------------------------


class TestCountersAndLifecycle:
    @pytest.mark.asyncio
    async def test_counters_accumulate_across_ticks(self, repo):
        old = repo.insert_open_row(**_open_row("i1", 42001, "t-old"))
        repo.mark_reaped(old.id, "terminal-tier@1", "gc")
        with repo.engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text(
                "UPDATE bash_spawn_registry SET reaped_at = :at "
                "WHERE id = :id"
            ), {"at": "2020-01-01T00:00:00+00:00", "id": old.id})

        svc = BashSpawnRetentionSweepService(
            repo,
            instance_repo=_FakeInstanceRepo({}),
            interval_seconds=1,
        )
        await svc.sweep_once()
        await svc.sweep_once()  # nothing left to do

        counters = svc.counters()
        assert counters["ticks"] == 2
        assert counters["pruned_total"] == 1
        assert counters["errors_total"] == 0

    @pytest.mark.asyncio
    async def test_start_stop_round_trip(self, repo):
        svc = BashSpawnRetentionSweepService(
            repo,
            instance_repo=_FakeInstanceRepo({}),
            interval_seconds=1,
        )
        svc.start()
        await asyncio.sleep(0.05)
        assert svc._task is not None and not svc._task.done()
        await svc.stop()
        assert svc._task is None

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, repo):
        svc = BashSpawnRetentionSweepService(
            repo,
            instance_repo=_FakeInstanceRepo({}),
            interval_seconds=1,
        )
        svc.start()
        try:
            await asyncio.sleep(0.05)
            svc.start()  # second start — silent no-op
            assert svc._task is not None
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_stop_is_silent_when_never_started(self, repo):
        svc = BashSpawnRetentionSweepService(
            repo, instance_repo=None, interval_seconds=1,
        )
        await svc.stop()  # must NOT raise
        assert svc._task is None


class TestIntervalKnob:
    def test_default_900s(self):
        assert DEFAULT_RETENTION_INTERVAL_SECONDS == 900
        assert retention_interval_seconds_from_env({}) == 900

    def test_floor_60s(self):
        assert MIN_RETENTION_INTERVAL_SECONDS == 60
        assert retention_interval_seconds_from_env(
            {ENV_RETENTION_INTERVAL: "1"}
        ) == 60

    def test_ceiling_86400s(self):
        assert MAX_RETENTION_INTERVAL_SECONDS == 86400
        assert retention_interval_seconds_from_env(
            {ENV_RETENTION_INTERVAL: "99999999"}
        ) == 86400

    def test_unparseable_falls_back_to_default(self):
        assert retention_interval_seconds_from_env(
            {ENV_RETENTION_INTERVAL: "abc"}
        ) == 900

    def test_service_resolves_env_when_interval_none(self, monkeypatch):
        monkeypatch.setenv(ENV_RETENTION_INTERVAL, "120")
        svc = BashSpawnRetentionSweepService(
            None, instance_repo=None,
        )
        assert svc._interval_seconds == 120


# ---------------------------------------------------------------------------
# 5. Wiring pin — the service is wired into the lifespan
# ---------------------------------------------------------------------------


class TestWiring:
    def test_service_module_importable(self):
        from daemon.services import bash_spawn_reaper  # noqa: F401

    def test_api_lifespan_wires_retention_sweep(self):
        """``daemon/api.py`` references the service in BOTH its
        startup and shutdown paths (mirrors the C2 wiring pin)."""
        import inspect

        from daemon import api

        lifespan_src = inspect.getsource(api.lifespan)
        assert "BashSpawnRetentionSweepService" in lifespan_src
        assert ".start()" in lifespan_src.split(
            "BashSpawnRetentionSweepService", 1
        )[1]
        assert ".stop()" in lifespan_src.split(
            "bash_spawn_retention", 1
        )[1]
