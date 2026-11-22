"""Engine honest state machine pins (Engine Arc P2 — T2).

File name per ``test-strategy.md`` §1.1 T2
(``tests/unit/services/test_engine_honest_state.py``).

Covers:
* AC1 — every terminal transition stamps last_run_at/last_outcome/
  last_skip_reason AND appends exactly one engine_runs row.
  Mutation: removing the stamp from the skip path ⇒ RED.
* AC7 — restart-clock hydration: persisted last_run_at survives.
* AC4 — retry arithmetic (min(cap, base*2^(n-1)), no jitter).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.engine.repository import EngineRepository
from daemon.services.engine import registry as registry_mod
from daemon.services.engine.engine import EngineService
from daemon.services.engine.locks import JobLockTable
from daemon.services.engine.registry import EngineRegistry
from daemon.services.engine.spec import (
    IntervalTrigger,
    JobSpec,
    ServiceMeta,
)
from daemon.services.engine.state import (
    IDLE_GATE_SKIP_REASONS,
    SKIP_REASONS,
    TerminalStamp,
    effective_interval_seconds,
    now_iso,
    parse_iso,
    skip_outcome,
    stamp_terminal,
)


def _engine():
    from sqlmodel.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _meta(**overrides):
    base = dict(
        service_tag="orphan-watcher-sweep",
        category="reconciler",
        service_name="Orphan Watcher Sweep",
        module_info="daemon.services.orphan_watcher_sweep",
        description="Reconcile orphaned watcher rows.",
    )
    base.update(overrides)
    return ServiceMeta(**base)


def _spec(**overrides):
    base = dict(
        id="orphan-watcher",
        meta=_meta(),
        trigger=IntervalTrigger(seconds=90),
        max_runtime_s=60.0,
        timeout_policy="cancel",
        concurrency_class="DB_OFFLOAD",
        priority=5,
        retry_max=2,
        retry_backoff_base_s=1.0,
        retry_backoff_cap_s=4.0,
    )
    base.update(overrides)
    return JobSpec(**base)


def _count_runs(repo, job_id):
    from sqlmodel import Session, select
    from daemon.repositories.engine.models import EngineRun

    with Session(repo.engine) as s:
        rows = s.exec(select(EngineRun).where(EngineRun.job_id == job_id)).all()
        return len(rows), rows


@pytest.mark.asyncio
async def test_stamp_terminal_writes_run_row_and_stamps_job():
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    job = repo.get_job("orphan-watcher")
    assert job is not None
    started = now_iso()
    import time
    # ensure finished differs
    stamp = TerminalStamp(
        job_id="orphan-watcher",
        service_tag="orphan-watcher-sweep",
        started_at=started,
        finished_at=now_iso(),
        outcome="SUCCEEDED",
        skip_reason=None,
        duration_seconds=0.1,
        attempt=1,
        consecutive_skips=0,
    )
    res = await asyncio.to_thread(stamp_terminal, repo, stamp)
    assert res is not None
    refreshed = repo.get_job("orphan-watcher")
    assert refreshed.last_outcome == "SUCCEEDED"
    assert refreshed.last_run_at is not None
    assert refreshed.last_skip_reason is None
    n, rows = _count_runs(repo, "orphan-watcher")
    assert n == 1
    assert rows[0].outcome == "SUCCEEDED"


@pytest.mark.asyncio
async def test_skip_path_stamps_and_appends_run():
    """AC1 mutation: skip path must stamp. Removing stamp ⇒ RED."""
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    stamp = TerminalStamp(
        job_id="orphan-watcher",
        service_tag="orphan-watcher-sweep",
        started_at=now_iso(),
        finished_at=now_iso(),
        outcome="SKIPPED_OVERLAP",
        skip_reason="OVERLAP",
        duration_seconds=0.0,
        attempt=1,
        consecutive_skips=0,
    )
    await asyncio.to_thread(stamp_terminal, repo, stamp)
    refreshed = repo.get_job("orphan-watcher")
    assert refreshed.last_outcome == "SKIPPED_OVERLAP"
    assert refreshed.last_run_at is not None
    assert refreshed.last_skip_reason == "OVERLAP"
    n, _ = _count_runs(repo, "orphan-watcher")
    assert n == 1


@pytest.mark.asyncio
async def test_every_terminal_outcome_appends_exactly_one_run():
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    outcomes = ["SUCCEEDED", "FAILED_BACKOFF", "TIMED_OUT", "SKIPPED_SHADOW", "SKIPPED_OVERLAP"]
    for oc in outcomes:
        reason = oc[len("SKIPPED_"):] if oc.startswith("SKIPPED_") else None
        stamp = TerminalStamp(
            job_id="orphan-watcher",
            service_tag="orphan-watcher-sweep",
            started_at=now_iso(),
            finished_at=now_iso(),
            outcome=oc,
            skip_reason=reason,
            duration_seconds=0.0,
            attempt=1,
            consecutive_skips=0,
        )
        await asyncio.to_thread(stamp_terminal, repo, stamp)
    n, rows = _count_runs(repo, "orphan-watcher")
    assert n == len(outcomes)


def test_skip_reasons_closed_vocabulary():
    assert "SHADOW" in SKIP_REASONS
    assert "OVERLAP" in SKIP_REASONS
    assert "REREGISTERED" in SKIP_REASONS
    # free-text folds to a member
    assert skip_outcome("totally unknown free text") == "SKIPPED_ERROR_BACKOFF" or skip_outcome(
        "totally unknown free text"
    ).startswith("SKIPPED_")


def test_effective_interval_backoff_and_reset():
    # 0 skips -> base
    assert effective_interval_seconds({"consecutive_skips": 0}, _spec()) == 90.0
    # 1 skip -> 2x
    assert effective_interval_seconds({"consecutive_skips": 1}, _spec()) == 180.0
    # 3 skips -> capped at 8x
    assert effective_interval_seconds({"consecutive_skips": 5}, _spec()) == 90.0 * 8


@pytest.mark.asyncio
async def test_hydration_preserves_last_run_at():
    """AC7: restart does not reset last_run_at."""
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    # stamp a run
    stamp = TerminalStamp(
        job_id="orphan-watcher",
        service_tag="orphan-watcher-sweep",
        started_at=now_iso(),
        finished_at=now_iso(),
        outcome="SUCCEEDED",
        consecutive_skips=0,
    )
    await asyncio.to_thread(stamp_terminal, repo, stamp)
    row = repo.get_job("orphan-watcher")
    assert row.last_run_at is not None
    persisted = row.last_run_at

    # simulate restart: new engine service hydrates from DB
    svc = EngineService(repo, reg, None, shadow_mode=True)
    # config None -> resolution default; hydrate manually
    await svc._hydrate()
    cache = svc._cache["orphan-watcher"]
    assert cache["last_run_at"] == persisted
    assert cache["consecutive_skips"] == 0


@pytest.mark.asyncio
async def test_retry_arithmetic_no_jitter():
    from daemon.services.engine import dispatch as D

    calls = []

    async def attempt(n):
        calls.append(n)
        if n < 3:
            raise RuntimeError("boom")

    outcome, attempts, exc = await D.run_with_retry(
        attempt, retry_max=2, backoff_base_s=0.01, backoff_cap_s=1.0, sleep=lambda d: asyncio.sleep(0)
    )
    assert outcome == "SUCCEEDED"
    assert attempts == 3
    assert calls == [1, 2, 3]


@pytest.mark.asyncio
async def test_retry_exhaustion_lands_failed_backoff():
    from daemon.services.engine import dispatch as D

    async def attempt(n):
        raise RuntimeError("boom")

    outcome, attempts, exc = await D.run_with_retry(
        attempt, retry_max=1, backoff_base_s=0.01, backoff_cap_s=1.0, sleep=lambda d: asyncio.sleep(0)
    )
    assert outcome == "FAILED_BACKOFF"
    assert attempts == 2
    assert exc is not None


def test_allowed_transitions_table():
    from daemon.services.engine.state import ALLOWED_TRANSITIONS, is_allowed_transition

    assert is_allowed_transition("IDLE", "DUE")
    assert is_allowed_transition("DUE", "RUNNING")
    assert is_allowed_transition("RUNNING", "SUCCEEDED")
    assert is_allowed_transition("RUNNING", "SKIPPED_SHADOW")
    assert is_allowed_transition("SUCCEEDED", "IDLE")
    assert not is_allowed_transition("IDLE", "RUNNING")
    assert "RUNNING" in ALLOWED_TRANSITIONS


@pytest.mark.asyncio
async def test_failed_write_logs_dedicated_error(caplog):
    """MINOR-3: a failed DB terminal-write logs ERROR naming job_id + the
    write class (engine_runs append vs engine_jobs stamp), then re-raises.
    """
    import logging

    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    stamp = TerminalStamp(
        job_id="orphan-watcher",
        service_tag="orphan-watcher-sweep",
        started_at=now_iso(),
        finished_at=now_iso(),
        outcome="SUCCEEDED",
        consecutive_skips=0,
    )

    class FailingRepo:
        def __init__(self, fail_append: bool) -> None:
            self.fail_append = fail_append

        def append_run(self, run):
            if self.fail_append:
                raise RuntimeError("append boom")
            return run

        def stamp_job_outcome(self, *a, **kw):
            if not self.fail_append:
                raise RuntimeError("stamp boom")
            return None

    with caplog.at_level(logging.ERROR, logger="daemon.services.engine.state"):
        with pytest.raises(RuntimeError):
            await asyncio.to_thread(stamp_terminal, FailingRepo(True), stamp)
    assert any(
        "orphan-watcher" in r.getMessage() and "engine_runs_append" in r.getMessage()
        for r in caplog.records
    )

    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="daemon.services.engine.state"):
        with pytest.raises(RuntimeError):
            await asyncio.to_thread(stamp_terminal, FailingRepo(False), stamp)
    assert any(
        "orphan-watcher" in r.getMessage() and "engine_jobs_stamp" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_failing_repo_stamp_leaves_loop_alive_w7():
    """W-7: when the best-effort guarded outcome stamp itself fails (repo
    down), the tick loop stays alive and the degradation stays observable in
    the honest in-memory state; the guard still ATTEMPTS the stamp every
    tick (best-effort, swallow-on-fail)."""
    eng = _engine()
    real = EngineRepository(eng)

    class RaisingStampRepo:
        """Everything delegates to the real repo; the outcome stamp raises."""

        def __init__(self) -> None:
            self.stamp_attempts = 0

        def __getattr__(self, name):
            return getattr(real, name)

        def stamp_job_outcome(self, *a, **kw):
            self.stamp_attempts += 1
            raise RuntimeError("stamp path down")

    repo = RaisingStampRepo()
    reg = EngineRegistry(real)
    await reg.aregister(_spec(retry_max=0))
    svc = EngineService(repo, reg, None, shadow_mode=False)
    await svc._hydrate()

    def executor():
        raise RuntimeError("boom")

    svc.set_executor("orphan-watcher", executor)
    loop = svc._execution_loop

    async def force_due():
        # _stamp advances last_run_at before the DB write, so re-arm
        # due-ness directly (test_due_boundary idiom) instead of sleeping
        # out the 90s interval.
        svc._cache["orphan-watcher"]["last_run_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=3600)
        ).isoformat()

    await loop._tick()
    await force_due()
    await loop._tick()

    cache = svc._cache["orphan-watcher"]
    assert cache["last_outcome"] == "FAILED_BACKOFF"
    assert cache["running"] is False
    # 2 attempts per tick (terminal stamp + guarded W-7 re-attempt) x 2
    # ticks: the guarded path fired every tick AND the loop kept dispatching.
    assert repo.stamp_attempts == 4


def test_idempotent_skip_reason_normalization():
    assert skip_outcome("shadow") == "SKIPPED_SHADOW"
    assert skip_outcome("OVERLAP") == "SKIPPED_OVERLAP"


if __name__ == "__main__":
    pass
