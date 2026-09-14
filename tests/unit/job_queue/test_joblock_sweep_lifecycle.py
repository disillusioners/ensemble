"""Lifecycle + config-knob tests for ``JobLockSweepService``.

Closes two coverage gaps from the F3 joblock-leak fix (branch
feature/fix-joblock-leak, commits 9b562e14 + 6622d85c):

  * GAP g — ``JobLockSweepService.start()`` / ``stop()`` lifecycle is
    never exercised in the existing acceptance suite
    (``tests/unit/job_queue/test_joblock_leak_fixes.py`` calls
    ``sweep_once()`` directly, bypassing the asyncio task). The
    daemon/api.py lifespan wiring (construction reads
    ``config.services.job_lock_sweep_interval_seconds`` at :728,
    boot log "JobLockSweepService started" at :741, shutdown at :1438)
    was untested.

  * GAP k — ``ServicesConfig.job_lock_sweep_interval_seconds``
    (``daemon/config.py:1482``, ``Field(default=90, ge=1)``) had no
    test pinning the default or the pydantic ``ValidationError`` on
    out-of-range values (0, -5).

The first three tests are REAL file-backed SQLite + a real
``JobLockSweepService`` instance (F11 shared-worktree safe — same
engine/fixture style as ``test_joblock_leak_fixes.py``). The asyncio
tests rely on the project's ``asyncio_mode = "auto"`` setting
(``pyproject.toml:81``) so they need no ``@pytest.mark.asyncio``
decorator. The config + doc-truth tests are pure-sync.

No production file is touched. No commit is made.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Iterator

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.config import ServicesConfig
from daemon.repositories.instance.models import (
    Instance,
    InstanceStatus,
)
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobLock,
)
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_lock_sweep import JobLockSweepService


# ─────────────────────────────────────────────────────────────────────
# Fixtures — local file-backed SQLite engine per test (F11 safe)
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path: Path) -> Iterator:
    """Real file-backed SQLite engine under tmp_path with QueuePool.

    Mirrors ``tests/unit/job_queue/test_joblock_leak_fixes.py:engine``
    so the async tick path (``asyncio.to_thread`` writes under
    ``JobLockManager.cleanup_terminal_job_locks``) sees genuine
    cross-thread DB behavior.
    """
    db_path = tmp_path / f"db_{uuid.uuid4().hex[:8]}.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=4,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()
        if db_path.exists():
            db_path.unlink()


@pytest.fixture
def lock_manager(engine) -> JobLockManager:
    return JobLockManager(lock_repo=LockRepository(engine))


# ─────────────────────────────────────────────────────────────────────
# Seeding helpers — same shape as the acceptance suite
# ─────────────────────────────────────────────────────────────────────


def _seed_paused_instance_with_done_job_and_lock(
    engine,
) -> tuple[str, str]:
    """Seed the canonical stale-lock pattern: PAUSED instance + DONE job
    + an orphaned JobLock. Mirrors
    ``test_joblock_leak_fixes.py::test_sweep_releases_paused_instance_terminal_lock``.

    Returns ``(instance_id, job_id)`` so the test can poll the lock
    count after start().
    """
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="test",
                agent_dir="/tmp",
                status=InstanceStatus.PAUSED.value,
                version=1,
                instance_metadata={},
            )
        )
        session.add(
            JobItem(
                job_id=job_id,
                agent_id="test",
                agent_dir="/tmp",
                message="m",
                source="api",
                project_id="p1",
                queue_id="q1",
                priority=1,
                admission_state=AdmissionState.DONE.value,
                created_at="2026-09-14T00:00:00Z",
                instance_id=instance_id,
                job_type="message",
                retry_count=0,
                version=1,
            )
        )
        session.add(
            JobLock(
                lock_id=f"lock-{uuid.uuid4().hex[:8]}",
                project_id="p1",
                queue_id="q1",
                job_id=job_id,
                instance_id=instance_id,
                lock_slot=0,
                acquired_at="2026-09-14T00:00:00Z",
            )
        )
        session.commit()
    return instance_id, job_id


def _lock_count_for_instance(engine, instance_id: str) -> int:
    with engine.begin() as conn:
        return (
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM job_locks "
                    "WHERE instance_id = :instance_id"
                ),
                {"instance_id": instance_id},
            ).scalar()
            or 0
        )


async def _wait_for_lock_release(
    engine, instance_id: str, *, deadline_s: float = 2.0
) -> int:
    """Poll the lock count until it hits zero or the deadline expires.

    Returns the LAST observed count (so callers can assert whether the
    reclaim actually happened within the budget). Uses
    ``time.monotonic()`` so the deadline is robust against event-loop
    clock skew.
    """
    deadline = time.monotonic() + deadline_s
    last = -1
    while time.monotonic() < deadline:
        last = _lock_count_for_instance(engine, instance_id)
        if last == 0:
            return last
        await asyncio.sleep(0.02)
    return last


# ─────────────────────────────────────────────────────────────────────
# GAP g — Lifecycle: start() / stop() actually run the periodic loop
# ─────────────────────────────────────────────────────────────────────


async def test_service_start_first_tick_reclaims_then_stop(
    engine, lock_manager
):
    """``start()`` spawns the asyncio task; the FIRST tick (which fires
    before the first ``asyncio.sleep``) reclaims a stale lock; ``stop()``
    cancels + awaits the task cleanly. This pins the lifespan wiring at
    daemon/api.py:728-744 (boot log + ``app.state.job_lock_sweep``).

    The interval is tiny (0.05s) so the test stays well under the
    5-min pack cap while still proving the loop is live (not just a
    single manual ``sweep_once`` call).
    """
    instance_id, _ = _seed_paused_instance_with_done_job_and_lock(engine)
    assert _lock_count_for_instance(engine, instance_id) == 1, (
        "PRECONDITION: a stale lock must exist before start()"
    )

    sweep = JobLockSweepService(
        job_lock_manager=lock_manager,
        interval_seconds=1,  # floor ≥ 1 per Field(ge=1) contract
    )
    # Override to fast cadence via the underlying sleep indirectly: the
    # first tick fires BEFORE the first sleep, so a 1s interval still
    # proves the first-tick reclaim (deadline 2s is well within budget).
    sweep.start()

    try:
        last = await _wait_for_lock_release(
            engine, instance_id, deadline_s=2.0
        )
        assert last == 0, (
            f"start() should have fired the first-tick reclaim within "
            f"2s, but lock count is {last} — lifecycle is broken"
        )
        assert sweep._task is not None, (
            "start() must leave a live asyncio.Task handle"
        )
        assert not sweep._task.done(), (
            "task must still be running (first-tick reclaim fired but "
            "the loop should be parked on asyncio.sleep, not exited)"
        )
    finally:
        await sweep.stop()

    assert sweep._task is None, (
        "stop() must clear the internal task handle (matches "
        "EligiblePendingSweepService.stop() contract)"
    )


async def test_stop_is_idempotent_and_restartable(engine, lock_manager):
    """``stop()`` on a stopped service is a silent no-op (does not
    raise); a fresh ``start()`` after stop() re-spawns the task and the
    first tick still reclaims a stale lock. Pins the idempotency +
    restartability contract that the daemon/api.py:1443-1451 shutdown
    block relies on (calls stop() unconditionally, may be called
    twice during nested lifespan teardown).
    """
    sweep = JobLockSweepService(
        job_lock_manager=lock_manager,
        interval_seconds=1,
    )

    # Round 1: start → stop → stop again (must NOT raise).
    sweep.start()
    assert sweep._task is not None
    await sweep.stop()
    assert sweep._task is None
    await sweep.stop()  # idempotent — silent no-op when _task is None

    # Round 2: re-seed + restart; the first tick must still reclaim.
    instance_id, _ = _seed_paused_instance_with_done_job_and_lock(engine)
    assert _lock_count_for_instance(engine, instance_id) == 1

    sweep.start()
    try:
        last = await _wait_for_lock_release(
            engine, instance_id, deadline_s=2.0
        )
        assert last == 0, (
            f"restart must fire a fresh first-tick reclaim; "
            f"lock count is {last}"
        )
    finally:
        await sweep.stop()


# ─────────────────────────────────────────────────────────────────────
# GAP k — Config knob bounds: default + pydantic ValidationError
# ─────────────────────────────────────────────────────────────────────


def test_config_default_is_90():
    """Pin the documented default (services/job_lock_sweep_interval_seconds
    description text + ``Field(default=90, ge=1)`` at daemon/config.py:1482).
    A regression here would silently change reclaim latency across the
    fleet (90s → ∞ if someone bumps default to 0 with the ge=1 guard
    removed; or 90s → a much-shorter interval if someone lowers the
    default without telling the operator).
    """
    cfg = ServicesConfig()
    assert cfg.job_lock_sweep_interval_seconds == 90, (
        f"ServicesConfig default must be 90s (shared with A3 cadence), "
        f"got {cfg.job_lock_sweep_interval_seconds}"
    )


@pytest.mark.parametrize("bad_value", [0, -5])
def test_config_rejects_out_of_range(bad_value: int):
    """``Field(ge=1)`` must fail fast at ``ServicesConfig``
    instantiation — ``daemon/api.py:728-744`` comments cite this as the
    boot-time refusal site. A regression here (e.g. someone deletes the
    ``ge=1`` constraint) would silently allow a 0-second interval =
    100% DB scan spin.
    """
    with pytest.raises(ValidationError) as exc_info:
        ServicesConfig(job_lock_sweep_interval_seconds=bad_value)
    # The pydantic error must mention the field name so operators can
    # find it in the boot log.
    assert "job_lock_sweep_interval_seconds" in str(exc_info.value), (
        f"ValidationError for {bad_value} must name the field; got: "
        f"{exc_info.value!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# Doc-truth pin — lifespan wiring exists in daemon/api.py
# ─────────────────────────────────────────────────────────────────────


def test_lifespan_wiring_doc_truth():
    """Pin that BOTH the boot log literal and the config-key literal are
    present in ``daemon/api.py``. This is the same doc-truth style used
    throughout this repo (grep real source for the exact wiring
    surface) — a regression here (e.g. someone renames the service or
    drops the interval read) would leave the lifespan without a sweep
    boot line AND without the F3 knob — a silent production blindspot.

    Resolves the path relative to THIS test file:
      tests/unit/job_queue/test_joblock_sweep_lifecycle.py
              ↑  ↑            ↑             ↑
              4 levels up from this file → daemon/api.py
    """
    api_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "daemon"
        / "api.py"
    )
    assert api_path.is_file(), (
        f"daemon/api.py not found at expected path: {api_path}"
    )
    api_src = api_path.read_text(encoding="utf-8")

    assert "JobLockSweepService started" in api_src, (
        "daemon/api.py must contain the 'JobLockSweepService started' "
        "boot log literal — the operator-facing signal that the F3 "
        "sweep is wired in."
    )
    assert "job_lock_sweep_interval_seconds" in api_src, (
        "daemon/api.py must read the config key "
        "'job_lock_sweep_interval_seconds' — the F3 knob source for "
        "the lifespan."
    )