"""Pins for the jobs badge path batching + watch bridges (jbh-m1b)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401

from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.services.job_queue_service import JobQueueService


# ─── Engine-bound SELECT spy (copied from test_work_resolver_query_budget) ──


class _SelectSpy:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self.counts: dict[str, int] = {"total": 0, "job_queue_items": 0}

    def __enter__(self) -> "_SelectSpy":
        def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
            s = statement.strip().upper()
            if s.startswith("SELECT"):
                self.counts["total"] += 1
                if "JOB_QUEUE_ITEMS" in s:
                    self.counts["job_queue_items"] += 1

        self._hook = _before_cursor_execute
        event.listen(self._engine, "before_cursor_execute", self._hook)
        return self

    def __exit__(self, *exc: Any) -> None:
        event.remove(self._engine, "before_cursor_execute", self._hook)


@pytest.fixture
def engine(tmp_path) -> Engine:
    eng = create_engine(
        f"sqlite:///{tmp_path}/badge_batch.db",
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    with eng.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA busy_timeout=10000")
        conn.commit()
    SQLModel.metadata.create_all(eng)
    return eng


def _seed_queued_jobs(engine: Engine, project_id: str, n: int) -> list[JobItem]:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    jobs: list[JobItem] = []
    with Session(engine) as s:
        for i in range(n):
            jid = f"qjob-{n}-{i}"
            job = JobItem(
                job_id=jid,
                agent_id="a",
                agent_dir="/",
                message="m",
                project_id=project_id,
                queue_id="q-1",  # jobs carry a queue_id on purpose
                admission_state=AdmissionState.QUEUED.value,
                priority=(10 - (i % 5)),
                created_at=now,
            )
            s.add(job)
            jobs.append(job)
        s.commit()
    return jobs


def _make_service(engine: Engine) -> JobQueueService:
    repo = JobRepository(engine)
    return JobQueueService(
        repository=repo,
        lock_manager=__import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(),
        queue_repo=__import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(),
    )


# ─── (a) queue-position batch pin ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_queue_position_select_count_flat(engine: Engine) -> None:
    """SELECT count stays FLAT as the queued-job count doubles 4→8→16.

    Mutation-meaningfulness: the old per-row loop called
    ``_get_queue_position`` once per queued job, and each call issued
    one ``list_pending_by_project`` SELECT — so the count grew
    4 → 8 → 16. The batched helper issues a single SELECT per page,
    so the count is constant.
    """
    service = _make_service(engine)
    counts: dict[int, int] = {}
    for n in (4, 8, 16):
        jobs = _seed_queued_jobs(engine, f"proj-{n}", n)
        fetched = service._repository.list_pending_by_project(f"proj-{n}")
        with _SelectSpy(engine) as spy:
            await service._get_queue_positions(fetched)
        counts[n] = spy.counts["job_queue_items"]
    assert counts[4] == counts[8] == counts[16], counts
    # exactly one pending-list SELECT per page
    assert counts[4] == 1, counts


@pytest.mark.asyncio
async def test_queue_position_value_equality(engine: Engine) -> None:
    """Batch positions == per-row _get_queue_position on project scope,
    even when jobs carry a queue_id (regression guard for the wire
    contract: the badge page is project-global)."""
    service = _make_service(engine)
    jobs = _seed_queued_jobs(engine, "proj-eq", 6)
    fetched = service._repository.list_pending_by_project("proj-eq")
    per_row = {}
    for j in fetched:
        per_row[j.job_id] = await service._get_queue_position(j.job_id, "proj-eq")
    batch = await service._get_queue_positions(fetched)
    assert batch == per_row, (batch, per_row)


# ─── (b) DLQ batch pin ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dlq_batch_select_count_is_flat(engine: Engine) -> None:
    """The DLQ batch read issues a CONSTANT number of SELECTs.

    Mutation-meaningfulness: a per-row implementation
    (``get_dlq_by_job_id`` once per dead_letter row) issues N SELECTs —
    1 for N=1 and 4 for N=4. The batched implementation issues ONE
    ``IN``-clause SELECT regardless of N, so the two counts are EQUAL
    and equal to the measured constant (1).
    """
    from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository
    from daemon.services.dead_letter_service import DeadLetterService

    dlq_repo = DeadLetterRepository(engine)
    job_repo = _make_service(engine)._repository
    dlq_svc = DeadLetterService(job_repo, dlq_repo)

    from daemon.repositories.job_queue.models import DeadLetterItem

    measured: dict[int, int] = {}
    for n in (1, 4):
        ids = [f"dead-{n}-{i}" for i in range(n)]
        for jid in ids:
            dlq_repo.enqueue(
                DeadLetterItem(
                    job_id=jid,
                    agent_id="a",
                    agent_dir="/",
                    message="m",
                    source="api",
                    project_id="p",
                    queue_id="q",
                    error_message="e",
                    failed_at="2020",
                    reason="MAX_RETRIES",
                )
            )
        with _SelectSpy(engine) as spy:
            res = dlq_svc.get_dlq_by_job_ids(ids)
        measured[n] = spy.counts["total"]
        assert len(res) == n, (n, len(res))

    # Flat across row counts — a per-row N+1 would scale with N.
    assert measured[1] == measured[4], measured
    # ... and equal to the true constant (measured: 1 IN-clause SELECT).
    assert measured[1] == 1, measured

    # Value parity with the per-row accessor (unchanged fields).
    for i in range(4):
        single = dlq_svc.get_dlq_by_job_id(f"dead-4-{i}")
        batch = dlq_svc.get_dlq_by_job_ids([f"dead-4-{i}"])
        assert batch[f"dead-4-{i}"].dlq_id == single.dlq_id


# ─── (c) watch-bridge source pins ───────────────────────────────────────────


def test_watch_bridge_source_pins() -> None:
    src = Path("daemon/services/job_queue_service.py").read_text()
    assert src.count("await asyncio.to_thread(self._watcher_repo.get_active_watches)") == 3


def test_active_watches_repo_method_exists() -> None:
    src = Path("daemon/repositories/job_queue/watcher_repository.py").read_text()
    assert "def get_active_watches" in src
    assert "def get_all_active_watches" in src


# ─── (d) active-WHERE parity ────────────────────────────────────────────────


def test_active_watches_parity_empty(engine: Engine) -> None:
    from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository

    repo = JobWatcherRepository(engine)
    assert repo.get_active_watches() == repo.get_all_active_watches() == []


# ─── route source pin ────────────────────────────────────────────────────────


def test_route_uses_batch_helpers() -> None:
    src = Path("daemon/routers/jobs_crud.py").read_text()
    assert "service._get_queue_positions" in src
    assert "dlq_service.get_dlq_by_job_ids" in src
