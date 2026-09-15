"""Unit tests for ``ServiceReconciliationService`` (Phase 1.C skeleton + Phase 2 sweep body).

Phase 1.C (1.C.12) covers the lifecycle shell — ``start`` /
``stop`` / ``sweep_once`` stub — and pins the constructor contract
(A9 repo injected directly, A8 — no ``max_concurrent`` param).

Phase 2 (2.A.2 / 2.A.6) adds the D6 sweep body tests:

* full sweep_onceround-trip — alive / dead / pid-recycled /
  starting-within-grace rows → expected counters;
* A3 eternal-``starting`` reaper — over-grace ``pid IS NULL`` row
  → ``starting_reaped`` + WARNING log line;
* 2.A.6 kill-switch gate — ``enabled_check=False`` returns the
  disabled-shape dict, NO DB queries;
* A13 race — concurrent ``mark_exited`` and external transition
  both leave the table consistent (the row-count contract);
* F16 (Risk-17 wording) — a sweep that misreads ``/proc`` (returns
  ``None``) classifies the row as ``reason=dead``; a future
  misreport class would carry the same shape.

Pattern: file-backed SQLite engine (NullPool + WAL + busy_timeout)
per the canonical ``tests/unit/repositories/test_service_tool_repository.py``
fixture. Repo writes go through ``ServiceRepo`` (the F3 frozen
interface) — direct SQLAlchemy UPDATEs are forbidden by the test
suite (``tests/unit/repositories/test_repo_contract.py``).
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from pathlib import Path
from typing import Iterator, List
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session, select

# Register the service_tool model so create_all emits the table.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.service_tool.models  # noqa: F401

from daemon.repositories.service_tool.models import (
    ServiceStatus,
    ServiceTracking,
)
from daemon.repositories.service_tool.repository import ServiceRepo
from daemon.services.service_reconciliation import (
    DEFAULT_STARTING_GRACE_SECONDS,
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    ServiceReconciliationService,
)
from daemon.tools import service_spawner


# ── fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def repo() -> MagicMock:
    """A ServiceRepo stand-in — the Phase-1 stub never calls it, but
    the constructor contract (A9: repo injected directly) is pinned
    by passing it here."""
    return MagicMock(name="ServiceRepo")


# ── importability + constants ───────────────────────────────────────


class TestModuleContract:
    def test_module_constants_pinned(self) -> None:
        """Defaults share the house sweep cadence (90s) and the A3
        starting-grace window (30s) — module-level so the config
        layer can reference them."""
        assert DEFAULT_SWEEP_INTERVAL_SECONDS == 90
        assert DEFAULT_STARTING_GRACE_SECONDS == 30

    def test_constructor_accepts_repo_positionally(self, repo) -> None:
        """A9: the narrowest collaborator (ServiceRepo) is the FIRST
        constructor arg — injected directly, NOT via ServiceManager."""
        svc = ServiceReconciliationService(repo, 120, 45)
        assert svc.interval_seconds == 120
        assert svc.starting_grace_seconds == 45

    def test_constructor_defaults(self, repo) -> None:
        svc = ServiceReconciliationService(repo)
        assert svc.interval_seconds == DEFAULT_SWEEP_INTERVAL_SECONDS
        assert svc.starting_grace_seconds == DEFAULT_STARTING_GRACE_SECONDS

    def test_interval_floor_clamped_in_ctor(self, repo) -> None:
        """Direct construction with a sub-1 interval clamps to 1 —
        the loop can never spin (the authoritative ge=1 floor is the
        pydantic Field at config level; this is the second line of
        defence for legacy fixtures)."""
        svc = ServiceReconciliationService(repo, interval_seconds=0)
        assert svc.interval_seconds == 1

    def test_no_max_concurrent_param(self, repo) -> None:
        """A8: the dead ``max_concurrent`` param is DROPPED — the cap
        lives on ServiceToolManager; the reconcile service only marks
        rows EXITED and never enforces capacity."""
        with pytest.raises(TypeError):
            ServiceReconciliationService(repo, max_concurrent=5)  # type: ignore[call-arg]


# ── lifecycle ───────────────────────────────────────────────────────


class TestLifecycle:
    def test_start_is_idempotent(self, repo) -> None:
        """Double start() spawns ONE task — the second call is a
        silent no-op (template pattern)."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo)
            await svc.start()
            first_task = svc._task
            assert first_task is not None and not first_task.done()
            await svc.start()  # silent no-op
            assert svc._task is first_task, (
                "double start() must not respawn the sweep task"
            )
            await svc.stop()

        asyncio.run(scenario())

    def test_stop_when_never_started_is_safe(self, repo) -> None:
        """stop() on a never-started service is a silent no-op — the
        shutdown path must survive partial startup failures."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo)
            await svc.stop()  # must not raise
            assert svc._task is None

        asyncio.run(scenario())

    def test_stop_cancels_and_awaits_the_task(self, repo) -> None:
        """A8 template semantics: stop() sets the stop event, cancels
        the task, awaits it (CancelledError swallowed) and clears the
        handle — no un-awaited cancelled task leak."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo, interval_seconds=1)
            await svc.start()
            task = svc._task
            assert task is not None
            await svc.stop()
            assert task.done(), "stop() must leave the task done"
            assert svc._task is None

        asyncio.run(scenario())

    def test_restart_after_stop(self, repo) -> None:
        """start() after a clean stop() respawns the task (restart
        symmetry — the lifespan may stop then re-start in tests)."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo)
            await svc.start()
            first = svc._task
            await svc.stop()
            await svc.start()
            second = svc._task
            assert second is not None and second is not first
            await svc.stop()

        asyncio.run(scenario())


# ── sweep stub shape ────────────────────────────────────────────────


class TestSweepOnceStub:
    def test_sweep_once_returns_zeroed_counters(self, repo) -> None:
        """The Phase-1 stub returns the EXACT counters dict shape the
        Phase-2 body must preserve (the A6 boot pass and the
        ``[ServiceTool] reconcile_boot_sweep`` lifespan log index into
        these keys)."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo)
            counters = await svc.sweep_once()
            assert counters == {
                "alive": 0,
                "reaped": 0,
                "errors": 0,
                "starting_reaped": 0,
            }

        asyncio.run(scenario())

    def test_sweep_once_ticks_the_counter(self, repo) -> None:
        """Each call bumps the public tick counter (observability)."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo)
            await svc.sweep_once()
            await svc.sweep_once()
            assert svc.ticks_total == 2
            # The shape of ``counters()`` may grow with Phase 2
            # additions (e.g. ``disabled_ticks``); pin the canonical
            # lifecycle scalars instead of exact-dict equality.
            all_counters = svc.counters()
            assert all_counters["ticks_total"] == 2
            assert all_counters["sweep_errors"] == 0

        asyncio.run(scenario())

    def test_loop_survives_a_raising_sweep(self, repo) -> None:
        """Defense-in-depth: a sweep body that raises is swallowed +
        counted and the loop keeps ticking (the Phase-2 body inherits
        this guarantee — a bad row must never kill the sweep)."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo, interval_seconds=1)
            calls = {"n": 0}

            async def boom() -> dict[str, int]:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("synthetic tick failure")
                return {"alive": 0, "reaped": 0, "errors": 0, "starting_reaped": 0}

            svc.sweep_once = boom  # type: ignore[method-assign]
            await svc.start()
            # Poll until the second tick lands (bounded wait — never
            # rely on real sleep timing in CI).
            for _ in range(200):
                if calls["n"] >= 2:
                    break
                await asyncio.sleep(0.01)
            await svc.stop()
            assert calls["n"] >= 2, "loop must tick past a raising body"
            assert svc.sweep_errors >= 1, "the failed tick must be counted"

        asyncio.run(scenario())


# ─────────────────────────────────────────────────────────────
# Phase 2 — D6 sweep body, kill-switch, A13 race (2.A.2/2.A.6/2.C)
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    """File-backed SQLite (NullPool + WAL + busy_timeout) — Phase-1
    canonical pattern (``test_repo_contract.py``)."""
    db_path = tmp_path / "service-reconciliation-sweep.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> ServiceRepo:
    """Real ``ServiceRepo`` on the file-backed engine."""
    return ServiceRepo(engine=engine)


def _seed_row(
    engine: Engine,
    *,
    name: str,
    pid: "int | None",
    start_time: "int | None",
    status: str = ServiceStatus.RUNNING.value,
    age_seconds: float = 0.0,
) -> ServiceTracking:
    """Insert one service_tracking row, optionally back-dating
    ``created_at``. The back-date is the trick that lets the A3
    eternal-``starting`` reaper test bypass the 30s real-time wait:
    a stale ``created_at`` makes the row appear ``age_seconds`` old
    immediately.
    """
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    backdated = (now - _dt.timedelta(seconds=age_seconds)).isoformat()
    with Session(engine) as s:
        row = ServiceTracking(
            name=name,
            command="echo hello",
            pid=pid,
            start_time=start_time,
            cwd=str(Path.cwd()),
            status=status,
            started_by_instance_id="test-instance",
            started_by_agent_id="tester",
            log_path=str(Path.cwd() / f".tmp_svc_{name}.log"),
            created_at=backdated,
            updated_at=backdated,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return row


class TestSweepBodyRoundTrip:
    """2.A.2 — the full D6 sweep round-trip across 4 row shapes."""

    def test_alive_row_is_counted_alive(
        self, engine: Engine, repo: ServiceRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A row with a live PID whose stored start_time matches the
        kernel read is counted as ``alive`` — no row transition."""

        async def scenario() -> None:
            pid = os.getpid()  # use the test process — guaranteed live.
            start_time = 42
            _seed_row(
                engine,
                name="svc_live",
                pid=pid,
                start_time=start_time,
                status=ServiceStatus.RUNNING.value,
            )
            # Make the kernel read return the SAME token ⇒ alive.
            monkeypatch.setattr(
                service_spawner, "get_process_start_time", lambda p: start_time
            )
            svc = ServiceReconciliationService(repo, interval_seconds=1)
            counters = await svc.sweep_once()
            assert counters == {
                "alive": 1,
                "reaped": 0,
                "errors": 0,
                "starting_reaped": 0,
            }

        asyncio.run(scenario())

    def test_dead_pid_is_reaped_with_reason_dead(
        self, engine: Engine, repo: ServiceRepo, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """A row whose PID is dead (kernel read returns ``None``) is
        transitioned to ``EXITED`` and reported as ``reaped``; the
        INFO log line ``reason=dead`` is emitted."""
        import logging as _logging

        caplog.set_level(_logging.INFO, logger="daemon.services.service_reconciliation")

        async def scenario() -> None:
            _seed_row(
                engine,
                name="svc_dead",
                pid=999_999_999,
                start_time=12345,
                status=ServiceStatus.RUNNING.value,
            )
            monkeypatch.setattr(
                service_spawner, "get_process_start_time", lambda p: None
            )
            svc = ServiceReconciliationService(repo, interval_seconds=1)
            counters = await svc.sweep_once()
            assert counters["reaped"] == 1
            assert counters["alive"] == 0
            with Session(engine) as s:
                row = s.exec(
                    select(ServiceTracking).where(
                        ServiceTracking.name == "svc_dead"
                    )
                ).one()
                assert row.status == ServiceStatus.EXITED.value
            assert any(
                "reason=dead" in r.message and "name=svc_dead" in r.message
                for r in caplog.records
            ), (
                "expected reason=dead log line; got: "
                + str([r.message for r in caplog.records])
            )

        asyncio.run(scenario())

    def test_pid_recycled_is_reaped_with_reason_pid_recycled(
        self, engine: Engine, repo: ServiceRepo, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """A row whose PID was recycled onto a different process
        (kernel-read returns a start_time different from the stored
        one) is transitioned to ``EXITED`` with the WARNING
        ``reason=pid_recycled`` log line."""
        import logging as _logging

        caplog.set_level(_logging.INFO, logger="daemon.services.service_reconciliation")

        async def scenario() -> None:
            _seed_row(
                engine,
                name="svc_recycled",
                pid=os.getpid(),
                start_time=42,
                status=ServiceStatus.RUNNING.value,
            )
            monkeypatch.setattr(
                service_spawner, "get_process_start_time", lambda p: 9999
            )
            svc = ServiceReconciliationService(repo, interval_seconds=1)
            counters = await svc.sweep_once()
            assert counters["reaped"] == 1
            with Session(engine) as s:
                row = s.exec(
                    select(ServiceTracking).where(
                        ServiceTracking.name == "svc_recycled"
                    )
                ).one()
                assert row.status == ServiceStatus.EXITED.value
            assert any(
                "reason=pid_recycled" in r.message
                and "name=svc_recycled" in r.message
                for r in caplog.records
            ), (
                "expected reason=pid_recycled log line; got: "
                + str([r.message for r in caplog.records])
            )

        asyncio.run(scenario())

    def test_starting_within_grace_is_left_alone(
        self, engine: Engine, repo: ServiceRepo
    ) -> None:
        """A ``STARTING`` row with ``pid IS NULL`` and a fresh
        ``created_at`` (inside the 30s grace window) is not touched
        by the sweep — the spawn may still be in progress."""

        async def scenario() -> None:
            _seed_row(
                engine,
                name="svc_young_starting",
                pid=None,
                start_time=None,
                status=ServiceStatus.STARTING.value,
                age_seconds=2.0,
            )
            svc = ServiceReconciliationService(repo, interval_seconds=1)
            counters = await svc.sweep_once()
            assert counters == {
                "alive": 0,
                "reaped": 0,
                "errors": 0,
                "starting_reaped": 0,
            }
            with Session(engine) as s:
                row = s.exec(
                    select(ServiceTracking).where(
                        ServiceTracking.name == "svc_young_starting"
                    )
                ).one()
                assert row.status == ServiceStatus.STARTING.value
                assert row.pid is None

        asyncio.run(scenario())


class TestEternalStartingReaper:
    """2.A.2 — A3 defense: a ``pid IS NULL`` row past the grace
    window is reaped with ``reason=spawn_failed_or_interrupted``."""

    def test_past_grace_starting_row_is_reaped(
        self, engine: Engine, repo: ServiceRepo, caplog
    ) -> None:
        import logging as _logging

        caplog.set_level(_logging.INFO, logger="daemon.services.service_reconciliation")

        async def scenario() -> None:
            _seed_row(
                engine,
                name="svc_eternal_starting",
                pid=None,
                start_time=None,
                status=ServiceStatus.STARTING.value,
                age_seconds=DEFAULT_STARTING_GRACE_SECONDS + 5.0,
            )
            svc = ServiceReconciliationService(repo, interval_seconds=1)
            counters = await svc.sweep_once()
            assert counters["starting_reaped"] == 1
            assert counters["reaped"] == 0
            assert counters["errors"] == 0
            with Session(engine) as s:
                row = s.exec(
                    select(ServiceTracking).where(
                        ServiceTracking.name == "svc_eternal_starting"
                    )
                ).one()
                assert row.status == ServiceStatus.EXITED.value
            assert any(
                "reason=spawn_failed_or_interrupted" in r.message
                and "name=svc_eternal_starting" in r.message
                and r.levelname == "WARNING"
                for r in caplog.records
            ), (
                "expected WARNING reason=spawn_failed_or_interrupted line; got: "
                + str([r.message for r in caplog.records])
            )

        asyncio.run(scenario())

    def test_healthysweep_emits_no_summary_log_when_no_reapings(
        self, engine: Engine, repo: ServiceRepo, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """The ``[ServiceTool] reconcile_swept`` summary line fires
        ONLY when a row was reaped (or ``starting_reaped`` is
        non-zero). A no-op sweep stays silent."""

        async def scenario() -> None:
            _seed_row(
                engine,
                name="svc_silent",
                pid=os.getpid(),
                start_time=7,
                status=ServiceStatus.RUNNING.value,
            )
            monkeypatch.setattr(
                service_spawner, "get_process_start_time", lambda p: 7
            )
            svc = ServiceReconciliationService(repo, interval_seconds=1)
            counters = await svc.sweep_once()
            assert counters == {
                "alive": 1,
                "reaped": 0,
                "errors": 0,
                "starting_reaped": 0,
            }
            assert not any(
                "reconcile_swept" in r.message for r in caplog.records
            ), "silent sweep must not emit the summary log line"

        asyncio.run(scenario())


class TestKillSwitchGate:
    """2.A.6 — kill-switch OFF early-return path."""

    def test_disabled_returns_disabled_shape_with_zero_counters(
        self, engine: Engine, repo: ServiceRepo, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """With ``enabled_check`` returning ``False``: returns the
        disabled-shape counters dict, emits DEBUG log, NEVER touches
        the DB. ``repo.list_active`` is monkeypatched to fail if
        called — proves the gate short-circuited before the DB."""
        import logging as _logging

        caplog.set_level(_logging.DEBUG, logger="daemon.services.service_reconciliation")

        async def scenario() -> None:
            def fail_if_called(*a, **kw):  # noqa: ANN001
                raise AssertionError(
                    "list_active must not be called when gate is OFF"
                )

            monkeypatch.setattr(repo, "list_active", fail_if_called)
            svc = ServiceReconciliationService(
                repo,
                interval_seconds=1,
                enabled_check=lambda: False,
            )
            counters = await svc.sweep_once()
            assert counters == {
                "alive": 0,
                "reaped": 0,
                "errors": 0,
                "starting_reaped": 0,
                "disabled": True,
            }
            assert any(
                "service_reconciliation_disabled" in r.message
                for r in caplog.records
            )
            assert svc.disabled_ticks == 1

        asyncio.run(scenario())

    def test_enabled_check_true_proceeds_normally(
        self, engine: Engine, repo: ServiceRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the callable returns ``True``, the sweep runs as
        normal — the gate is transparent when ON."""

        async def scenario() -> None:
            _seed_row(
                engine,
                name="svc_enabled_true",
                pid=os.getpid(),
                start_time=11,
                status=ServiceStatus.RUNNING.value,
            )
            monkeypatch.setattr(
                service_spawner, "get_process_start_time", lambda p: 11
            )
            svc = ServiceReconciliationService(
                repo,
                interval_seconds=1,
                enabled_check=lambda: True,
            )
            counters = await svc.sweep_once()
            assert counters == {
                "alive": 1,
                "reaped": 0,
                "errors": 0,
                "starting_reaped": 0,
            }
            assert "disabled" not in counters

        asyncio.run(scenario())

    def test_no_enabled_check_means_always_on(self, engine: Engine, repo: ServiceRepo) -> None:
        """The constructor default for ``enabled_check`` is ``None``
        — sweep runs unconditionally (the ap1.py mount is the
        primary gate)."""

        async def scenario() -> None:
            svc = ServiceReconciliationService(repo, interval_seconds=1)
            assert svc._enabled_check is None
            counters = await svc.sweep_once()
            assert counters == {
                "alive": 0,
                "reaped": 0,
                "errors": 0,
                "starting_reaped": 0,
            }

        asyncio.run(scenario())


class TestA13AtomicGuard:
    """A13 — concurrent sweep ↔ external transition leaves the table
    consistent (exactly-one-winner). ``mark_exited`` rowcount=0 is
    treated as idempotent success."""

    def test_concurrent_mark_exited_only_one_wins(
        self, engine: Engine, repo: ServiceRepo
    ) -> None:
        """Two ``mark_exited`` calls on the same RUNNING row: the
        A13 guarded UPDATE returns rowcount=1 to the first call and
        rowcount=0 to the second (the second sees
        ``status IN ('starting','running')`` no longer true)."""

        row = _seed_row(
            engine,
            name="svc_a13_race",
            pid=os.getpid(),
            start_time=5,
            status=ServiceStatus.RUNNING.value,
        )

        winner = repo.mark_exited(row.id, exit_code=None)
        loser = repo.mark_exited(row.id, exit_code=None)

        # Exactly one wins (1); the other race-loses (0). The repo's
        # contract: rowcount=0 ⇒ idempotent success, so it is NOT
        # an error.
        assert {winner, loser} == {0, 1}
        with Session(engine) as s:
            row = s.get(ServiceTracking, row.id)
            assert row.status == ServiceStatus.EXITED.value

    def test_race_lost_sweep_increments_no_counter(
        self, engine: Engine, repo: ServiceRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the row is EXITED by the time ``mark_exited`` is called
        from the sweep body, the repo returns 0 and the sweep does
        NOT bump ``reaped`` — the counter stays accurate."""

        async def scenario() -> None:
            row = _seed_row(
                engine,
                name="svc_a13_lostrace",
                pid=os.getpid(),
                start_time=5,
                status=ServiceStatus.RUNNING.value,
            )
            # Pre-transition (simulating a concurrent stop winning
            # the race).
            repo.mark_exited(row.id, exit_code=None)
            # Force the sweep down the dead-PID branch.
            monkeypatch.setattr(
                service_spawner, "get_process_start_time", lambda p: None
            )
            svc = ServiceReconciliationService(repo, interval_seconds=1)
            counters = await svc.sweep_once()
            # ``reaped`` stays 0 because the second mark_exited lost
            # the race; ``alive`` also stays 0 (the kernel returned
            # ``None`` ⇒ the dead-PID branch, which then rowcount=0).
            assert counters["reaped"] == 0
            assert counters["alive"] == 0
            assert counters["errors"] == 0

        asyncio.run(scenario())


class TestRepoFailureIsolation:
    """A repo read failure does NOT kill the loop — the sweep
    surfaces the error and returns the zeroed counters."""

    def test_list_active_failure_does_not_raise(
        self, engine: Engine, repo: ServiceRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def scenario() -> None:
            def boom():
                raise RuntimeError("synthetic repo failure")

            monkeypatch.setattr(repo, "list_active", boom)
            svc = ServiceReconciliationService(repo, interval_seconds=1)
            counters = await svc.sweep_once()
            assert counters == {
                "alive": 0,
                "reaped": 0,
                "errors": 0,
                "starting_reaped": 0,
            }
            assert svc.ticks_total == 1
            assert svc.sweep_errors == 1

        asyncio.run(scenario())

    def test_per_row_exception_is_isolated(
        self, engine: Engine, repo: ServiceRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A row whose reconciliation raises an exception is isolated
        — the sweep bumps ``errors`` and moves to the next row. The
        good row is seeded with the REAL start_time of the test PID
        so the sweep classifies it ``alive`` instead of
        ``pid_recycled`` (the test pins exception isolation, not the
        classification branch).

        The bad row carries a deliberately unstable ``PID`` so the
        flaky read can identify it deterministically (the iteration
        order is ``created_at DESC``, so the second-seeded bad row
        is processed first — the sweep raises once on the bad row,
        then continues to the good row)."""

        async def scenario() -> None:
            pid = os.getpid()
            # Resolve the REAL start_time of the test PID so we can
            # seed the good row with a matching token.
            real_start = service_spawner.get_process_start_time(pid)
            assert real_start is not None, (
                "kernel read for the test process unexpectedly None — "
                "the test cannot seed a matching start_time; aborting"
            )

            # Use distinct PIDs so the flaky reader identifies the
            # bad row by PID (deterministic, order-independent).
            bad_pid = 999_999_991
            good_pid = os.getpid()

            _seed_row(
                engine,
                name="svc_good",
                pid=good_pid,
                start_time=real_start,
                status=ServiceStatus.RUNNING.value,
            )
            _seed_row(
                engine,
                name="svc_bad",
                pid=bad_pid,
                start_time=1,
                status=ServiceStatus.RUNNING.value,
            )

            def flaky(p):
                # Bad PID ⇒ synthetic error; good PID ⇒ real token.
                if p == bad_pid:
                    raise RuntimeError("synthetic row-level failure")
                return real_start

            monkeypatch.setattr(
                service_spawner, "get_process_start_time", flaky
            )
            svc = ServiceReconciliationService(repo, interval_seconds=1)
            counters = await svc.sweep_once()
            assert counters["errors"] == 1
            assert counters["alive"] == 1
            assert counters["reaped"] == 0

        asyncio.run(scenario())


class TestEnabledCheckArgShape:
    """Pin the constructor signature so future callers can rely on
    the Phase-2 ``enabled_check=None`` default."""

    def test_constructor_accepts_enabled_check(self, repo: ServiceRepo) -> None:
        svc = ServiceReconciliationService(
            repo, enabled_check=lambda: True
        )
        assert svc._enabled_check is not None

    def test_constructor_default_is_none(self, repo: ServiceRepo) -> None:
        svc = ServiceReconciliationService(repo)
        assert svc._enabled_check is None
