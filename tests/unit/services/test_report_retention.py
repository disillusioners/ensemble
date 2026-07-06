"""Phase 4 partial collapse — report-task retention test (AD-6 / RF2).

This file is the regression guard for Task 10 of the phase4 plan
(``docs/plans/job-as-front-primitive/phase4-plan.md``). RF2 deep
review found 6 backend code paths that branch on
``kind != "job"``. AD-6 retains ``kind="report"`` Task rows so all
6 paths continue to work with **zero code changes** — the precise
``(report)`` error messages, the cooperative cancel via
``request_cancel``, the JobItem-side ``kind != "job"`` defensive
filter all stay.

The collapse must:

* retain ``kind="report"`` Task rows in ``list_work`` and
  ``resolve_work``;
* keep the ``_kind_from_task_type`` report discrimination alive;
* keep ``REPORT_TASK_TYPES`` and ``task_repo`` wired into the
  WorkResolverService constructor;
* keep the Task SELECT broadened to include ``process_report`` /
  ``send_report`` task types only.

These tests fail loudly if any future change accidentally removes
report support from the resolver.

Pattern matches ``tests/unit/services/test_work_resolver.py`` — a
fresh in-memory SQLite engine per test (StaticPool,
``PRAGMA foreign_keys=ON``), ``SQLModel.metadata.create_all`` for
the schema, direct ``Session`` inserts via local seed helpers.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_queue_service import JobQueueService
from daemon.services.work_resolver import (
    REPORT_TASK_TYPES,
    WorkRecord,
    WorkResolverService,
    _kind_from_task_type,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine per test (StaticPool, FK on)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def task_repo(engine: Engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def resolver(
    task_repo: TaskRepository,
    job_repo: JobRepository,
    instance_repo: SQLModelInstanceRepository,
) -> WorkResolverService:
    return WorkResolverService(task_repo, job_repo, instance_repo)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _seed_instance(engine: Engine, *, instance_id: str | None = None,
                   project_id: str = "test-project",
                   agent_id: str = "developer",
                   status: str = "running",
                   parent_id: str | None = None) -> str:
    """Insert an Instance row."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            agent_name=agent_id,
            project_id=project_id,
            status=status,
            created_at=now_iso,
            updated_at=now_iso,
            paused_at=None,
            parent_id=parent_id,
        ))
        s.commit()
    return iid


def _seed_report_task(
    engine: Engine,
    *,
    work_id: str | None = None,
    instance_id: str,
    task_type: str = "process_report",
    status: str = TaskStatus.RUNNING.value,
    message_id: str | None = None,
) -> str:
    """Insert a report Task row.

    Defaults ``task_type="process_report"`` — pass
    ``task_type="send_report"`` to exercise the second value in
    ``REPORT_TASK_TYPES``.
    """
    assert task_type in {"process_report", "send_report"}, (
        f"Test helper enforces report-only task_types; got {task_type!r}"
    )
    wid = work_id or str(uuid.uuid4())
    with Session(engine) as s:
        s.add(Task(
            work_id=wid,
            task_type=task_type,
            instance_id=instance_id,
            message_id=message_id,
            status=status,
            created_at=datetime.now(timezone.utc),
            is_deferred=False,
        ))
        s.commit()
    return wid


def _seed_job(
    engine: Engine,
    *,
    job_id: str | None = None,
    instance_id: str | None = None,
    project_id: str = "test-project",
    admission: str = AdmissionState.QUEUED.value,
) -> str:
    """Insert a JobItem row."""
    jid = job_id or str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="test",
            source="api",
            project_id=project_id,
            priority=5,
            admission_state=admission,
            instance_id=instance_id,
            created_at=now_iso,
            deleted_at=None,
            job_metadata={},
            terminal_reason=None,
        ))
        s.commit()
    return jid


# ─── Tests ──────────────────────────────────────────────────────────────────


class TestReportRetention_ListWork:
    """``list_work`` MUST keep surfacing report Tasks (AD-6).

    The pre-collapse test ``TestListWorkDedup.test_dedup_does_not_affect_reports``
    exercised this surface with explicit ``kind="report"`` records;
    this class re-asserts the same contract from a fresh angle and
    pins the additional invariants introduced by Phase 4 (no more
    turn records; reports are the only Task-side kind).
    """

    def test_report_task_appears_in_list_work_default(
        self, engine, resolver
    ):
        """Default ``list_work()`` includes a ``process_report`` Task
        as ``kind="report"``."""
        iid = _seed_instance(engine)
        wid = _seed_report_task(
            engine, instance_id=iid, task_type="process_report"
        )

        records = resolver.list_work()

        kinds_by_id = {r.work_id: r.kind for r in records}
        assert kinds_by_id.get(wid) == "report", (
            f"process_report Task must surface as kind='report' in "
            f"default list_work(). Got: {kinds_by_id}"
        )

    def test_send_report_task_appears_as_report(self, engine, resolver):
        """``send_report`` task_type also maps to ``kind="report"``.

        ``REPORT_TASK_TYPES = frozenset({"process_report", "send_report"})``
        — both values must surface under ``kind="report"`` so the
        discriminator is exhaustive for the 2 known report types.
        """
        iid = _seed_instance(engine)
        wid = _seed_report_task(
            engine, instance_id=iid, task_type="send_report"
        )

        records = resolver.list_work()

        kinds_by_id = {r.work_id: r.kind for r in records}
        assert kinds_by_id.get(wid) == "report"

    def test_report_task_appears_in_list_work_kind_report(
        self, engine, resolver
    ):
        """``list_work(kind="report")`` returns report Tasks."""
        iid = _seed_instance(engine)
        process_report_wid = _seed_report_task(
            engine, instance_id=iid, task_type="process_report"
        )
        send_report_wid = _seed_report_task(
            engine, instance_id=iid, task_type="send_report"
        )
        # A JobItem on the same instance — must NOT be returned.
        jid = _seed_job(engine, instance_id=iid)

        records = resolver.list_work(kind="report")

        kinds = {r.work_id: r.kind for r in records}
        assert kinds == {process_report_wid: "report",
                         send_report_wid: "report"}, (
            f"kind='report' must return only the two report Tasks. "
            f"Got: {kinds}"
        )
        assert jid not in kinds, (
            f"JobItem on the same instance must NOT appear under "
            f"kind='report'. Got kinds: {kinds}"
        )

    def test_report_pair_with_jobitem_both_survive(
        self, engine, resolver
    ):
        """A report Task + a JobItem on the same instance → both
        surface (no dedup, no suppression).

        Pre-collapse the ``TestListWorkDedup`` suite asserted the
        same contract via the F1 dedup gate. Post-collapse the dedup
        gate is gone — the contract becomes a structural property
        of the resolver rather than an explicit suppression
        exception. This test pins the post-collapse property.
        """
        iid = _seed_instance(engine)
        jid = _seed_job(engine, instance_id=iid)
        report_wid = _seed_report_task(
            engine, instance_id=iid, task_type="process_report"
        )

        records = resolver.list_work()

        work_ids = {r.work_id for r in records}
        assert work_ids == {jid, report_wid}, (
            f"JobItem + report Task pair must both surface. "
            f"Got: {[(r.work_id, r.kind) for r in records]}"
        )


class TestReportRetention_ResolveWork:
    """``resolve_work`` resolves ``process_report`` / ``send_report``
    work_ids as ``kind="report"``."""

    def test_resolve_process_report(self, engine, resolver):
        iid = _seed_instance(engine)
        wid = _seed_report_task(
            engine, instance_id=iid, task_type="process_report",
            status=TaskStatus.COMPLETED.value,
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.work_id == wid
        assert record.kind == "report"
        assert record.status == "completed"

    def test_resolve_send_report(self, engine, resolver):
        iid = _seed_instance(engine)
        wid = _seed_report_task(
            engine, instance_id=iid, task_type="send_report",
            status=TaskStatus.COMPLETED.value,
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.kind == "report"
        assert record.status == "completed"

    def test_resolve_report_without_instance_surfaces_orphaned(
        self, engine, resolver
    ):
        """A report Task whose backing Instance was deleted (rare —
        only on project purge) still resolves with project_id=None
        and agent_id=None (orphaned record).

        Pins the contract that ``_task_to_record`` defensively
        handles a missing Instance row instead of raising. This
        is the same path the pre-collapse tests exercised.
        """
        # Don't seed an instance — the Task references a phantom iid.
        wid = _seed_report_task(
            engine,
            instance_id="phantom-inst-that-does-not-exist",
            task_type="process_report",
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.work_id == wid
        assert record.kind == "report"
        # Orphaned → no project, no agent.
        assert record.project_id is None
        assert record.agent_id is None


class TestReportRetention_FacadeInternals:
    """Pin the internal invariants the facade relies on:

    * ``REPORT_TASK_TYPES`` covers both ``process_report`` and
      ``send_report``.
    * ``_kind_from_task_type`` discriminates both values to
      ``"report"``.
    * ``task_repo`` is retained on ``WorkResolverService``
      (AD-6 / RF2 hard requirement).
    """

    def test_report_task_types_constant_contains_both_values(self):
        """``REPORT_TASK_TYPES`` must include both values or the Task
        SELECT would silently drop one type."""
        assert "process_report" in REPORT_TASK_TYPES
        assert "send_report" in REPORT_TASK_TYPES

    def test_kind_from_task_type_returns_report_for_both(self):
        """``_kind_from_task_type`` discriminates both values to
        ``"report"``."""
        assert _kind_from_task_type("process_report") == "report"
        assert _kind_from_task_type("send_report") == "report"
        # Defensive fallback for unknown / None (legacy rows
        # ingested before the collapse).
        assert _kind_from_task_type(None) == "report"
        assert _kind_from_task_type("unknown_future_type") == "report"

    def test_resolver_retains_task_repo(self, resolver, task_repo):
        """``WorkResolverService.__init__`` retains ``task_repo``
        (RF2 / AD-6 hard requirement — the report query uses it)."""
        assert resolver._task_repo is task_repo


class TestReportRetention_CooperativeCancelPath:
    """Pin that ``record.kind != "job"`` is the discriminator used
    by the 6 backend paths from RF2.

    This file doesn't drive the full router/tool stack (those are
    covered by their own tests; we exercise only the contract
    surface the resolver exposes: ``get_work`` returns
    ``kind="report"`` for report rows, so the consumers' branch
    fires correctly).
    """

    def test_report_record_kind_is_not_job(self, engine, resolver):
        """``record.kind != "job"`` for a report record — the
        single discriminator the 6 backend paths share.

        Concretely: ``daemon/tools/job_queue.py:job_cancel`` and
        ``daemon/tools/job_queue.py:job_retry`` both check
        ``record.kind != "job"`` and route accordingly. If the
        resolver ever returned ``kind="job"`` for a report record
        (e.g. by collapsing report records into JobItem rows), the
        backend paths would silently degrade to the JobItem branch
        and the precise ``(report)`` error messages would be
        lost.

        This pins the contract: report records carry
        ``kind="report"`` (NOT ``"job"``) so the
        ``kind != "job"`` branch correctly routes them to the
        cooperative cancel / precise-error paths.
        """
        iid = _seed_instance(engine)
        wid = _seed_report_task(
            engine, instance_id=iid, task_type="process_report"
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.kind != "job", (
            f"Report record must surface as kind != 'job' so the "
            f"6 backend paths (cooperative cancel + precise "
            f"retry/delete/restore errors) keep working. Got "
            f"kind={record.kind!r}"
        )
        # And explicit ``kind == 'report'`` for the precise-error
        # branch.
        assert record.kind == "report"


__all__ = [
    "TestReportRetention_ListWork",
    "TestReportRetention_ResolveWork",
    "TestReportRetention_FacadeInternals",
    "TestReportRetention_CooperativeCancelPath",
]
