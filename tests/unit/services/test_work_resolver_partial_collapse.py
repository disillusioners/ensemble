"""Phase 4 partial collapse — turn-query regression test for ``WorkResolverService``.

This file is the regression guard for Task 9 of the phase4 plan
(``docs/plans/job-as-front-primitive/phase4-plan.md``). Phase 4
deletes the turn-specific ``_query_tasks`` filter (``process_message``
Task rows) and the entire P-C(i) dedup gate. After collapse:

* ``list_work`` never returns ``kind="turn"`` records.
* ``list_work`` returns the union of JobItems + report Tasks only.
* ``resolve_work`` on a ``process_message`` work_id still resolves
  (the Task branch is retained so legacy rows ingested before the
  collapse stay resolvable).

These tests fail loudly if any future change re-introduces turn
records in ``list_work`` or breaks the report-only Task query.

Pattern mirrors ``tests/unit/services/test_work_resolver.py`` — a
fresh in-memory SQLite engine per test (StaticPool,
``PRAGMA foreign_keys=ON``), ``SQLModel.metadata.create_all`` for
the schema, and direct ``Session`` inserts via local seed helpers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

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
from daemon.services.work_resolver import WorkResolverService


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
    """Insert an Instance row. Returns the ``instance_id``.

    ``status`` defaults to ``"running"`` — tests for the post-collapse
    no-promotion invariant pass ``status="waiting_children"`` to model
    an actively-orchestrating parent.
    """
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


def _seed_task(
    engine: Engine,
    *,
    work_id: str | None = None,
    instance_id: str,
    task_type: str = "process_message",
    status: str = TaskStatus.PENDING.value,
    message_id: str | None = None,
) -> str:
    """Insert a Task row. Returns the ``work_id``."""
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
    """Insert a JobItem row. Returns the ``job_id``."""
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


class TestPartialCollapse_NoTurnsInListWork:
    """Phase 4 partial collapse: ``list_work`` never returns ``kind="turn"``."""

    def test_list_work_excludes_process_message_task(
        self, engine, resolver
    ):
        """A ``process_message`` Task row → NEVER appears in ``list_work``.

        Pre-collapse this would surface as ``kind="turn"``; post-collapse
        the Task SELECT is restricted to ``REPORT_TASK_TYPES`` so the
        row is filtered out at SQL level. The JobItem on the same
        instance still surfaces as ``kind="job"``.
        """
        iid = _seed_instance(engine)
        process_message_wid = _seed_task(
            engine,
            instance_id=iid,
            task_type="process_message",
            status=TaskStatus.PENDING.value,
        )

        records = resolver.list_work()

        kinds = {r.work_id: r.kind for r in records}
        # The seeded process_message work_id must NOT be in the result.
        assert process_message_wid not in kinds, (
            f"Phase 4 partial collapse: list_work must NOT surface "
            f"process_message Task rows. Got kinds: {kinds}"
        )
        # And nothing has kind="turn".
        assert all(r.kind != "turn" for r in records), (
            f"After partial collapse, no record may have kind='turn'. "
            f"Got: {[r.kind for r in records]}"
        )

    def test_list_work_returns_jobs_and_report_tasks_only(
        self, engine, resolver
    ):
        """Mix of JobItems + report Tasks + process_message Tasks → only
        JobItems + report Tasks survive.

        Two JobItem rows (on two instances), one process_report Task,
        one process_message Task, one send_report Task. After
        collapse: 4 records (2 jobs + 2 report Tasks); the
        ``process_message`` Task is silently dropped.
        """
        inst_a = _seed_instance(engine, instance_id="inst-A")
        inst_b = _seed_instance(engine, instance_id="inst-B")
        # Report Task + process_message Task on inst_a.
        report_a = _seed_task(
            engine, instance_id=inst_a, task_type="process_report"
        )
        _seed_task(
            engine, instance_id=inst_a, task_type="process_message"
        )
        # Send-report Task on inst_b.
        send_report_b = _seed_task(
            engine, instance_id=inst_b, task_type="send_report"
        )
        # Two Jobs on their own instances.
        job_x = _seed_job(engine, instance_id=inst_a)
        job_y = _seed_job(engine, instance_id=inst_b)

        records = resolver.list_work()

        # 4 records: 2 jobs + 2 reports.
        assert len(records) == 4, (
            f"Expected 4 records (2 jobs + 2 report Tasks), got "
            f"{len(records)}: {[(r.work_id, r.kind) for r in records]}"
        )
        # Kinds set must be {"job", "report"} only.
        assert {r.kind for r in records} == {"job", "report"}
        # No "turn" anywhere.
        assert "turn" not in {r.kind for r in records}
        # Spot-check the work_ids.
        work_ids = {r.work_id for r in records}
        assert {report_a, send_report_b, job_x, job_y} == work_ids

    def test_list_work_kind_turn_returns_empty(
        self, engine, resolver
    ):
        """``list_work(kind="turn")`` → empty (turns are JobItems now).

        The router rejects ``kind="turn"`` with HTTP 400, but the
        resolver defends in depth: passing ``kind="turn"`` must not
        silently fall back to the union (that would leak JobItems).
        """
        _seed_instance(engine, instance_id="inst-t")
        _seed_task(
            engine, instance_id="inst-t", task_type="process_message"
        )
        _seed_job(engine, instance_id="inst-t")

        records = resolver.list_work(kind="turn")

        # Defensive: kind="turn" is unsupported; the resolver should
        # return empty rather than falling back to the union (which
        # would silently surface JobItems the caller's filter tried
        # to exclude).
        assert records == [], (
            f"kind='turn' should return empty after partial collapse "
            f"(turns are JobItems; the router rejects this value). "
            f"Got: {[(r.work_id, r.kind) for r in records]}"
        )

    def test_list_work_kind_task_returns_empty(
        self, engine, resolver
    ):
        """``list_work(kind="task")`` → empty (the backward-compat alias
        for turn+report no longer maps to any record)."""
        _seed_instance(engine, instance_id="inst-t2")
        _seed_task(
            engine, instance_id="inst-t2", task_type="process_report"
        )

        records = resolver.list_work(kind="task")

        # kind="task" was the backward-compat union of turn+report.
        # Post-collapse there's nothing to union — turn is gone, so
        # the resolver returns empty rather than the report-only slice.
        assert records == [], (
            f"kind='task' (turn+report alias) should return empty "
            f"after partial collapse. Got: "
            f"{[(r.work_id, r.kind) for r in records]}"
        )

    def test_list_work_kind_report_still_returns_reports(
        self, engine, resolver
    ):
        """``list_work(kind="report")`` still works — AD-6 retention."""
        inst = _seed_instance(engine, instance_id="inst-r")
        report_wid = _seed_task(
            engine, instance_id=inst, task_type="process_report"
        )
        # A process_message Task on the same instance — must NOT appear.
        process_message_wid = _seed_task(
            engine, instance_id=inst, task_type="process_message"
        )

        records = resolver.list_work(kind="report")

        kinds = {r.work_id: r.kind for r in records}
        assert kinds == {report_wid: "report"}, (
            f"kind='report' must return only the report Task, not the "
            f"process_message one. Got: {kinds}"
        )
        assert process_message_wid not in kinds

    def test_list_work_kind_job_still_returns_jobs(
        self, engine, resolver
    ):
        """``list_work(kind="job")`` still works and excludes Tasks entirely."""
        _seed_instance(engine, instance_id="inst-j")
        jid = _seed_job(engine, instance_id="inst-j")
        _seed_task(
            engine, instance_id="inst-j", task_type="process_report"
        )

        records = resolver.list_work(kind="job")

        kinds = {r.work_id: r.kind for r in records}
        assert kinds == {jid: "job"}


class TestPartialCollapse_NoDedupNoPromotion:
    """Phase 4 deletion of dedup + promotion — verify the data layer
    produces neither effect on ``list_work``."""

    def test_no_promotion_for_active_orchestration(self, engine, resolver):
        """A ``completed`` report Task on a ``waiting_children`` instance
        stays ``completed`` — no promotion rule fires.

        Pre-collapse, the active-orchestration pass would flip the
        newest completed Task turn on a ``running`` /
        ``waiting_children`` instance to ``processing``. Post-collapse
        the promotion rule is deleted; Task rows keep their own
        ``Task.status`` (``"completed"`` here).
        """
        _seed_instance(
            engine, instance_id="inst-active", status="waiting_children"
        )
        wid = _seed_task(
            engine,
            instance_id="inst-active",
            task_type="process_report",
            status=TaskStatus.COMPLETED.value,
        )

        records = resolver.list_work()

        by_id = {r.work_id: r for r in records}
        # The report Task is present (kind="report") and its status
        # is unchanged — no promotion to "processing".
        assert wid in by_id
        assert by_id[wid].kind == "report"
        assert by_id[wid].status == "completed", (
            f"Post-collapse a completed report Task on a "
            f"waiting_children instance must NOT be promoted to "
            f"'processing'. Got status={by_id[wid].status!r}"
        )

    def test_no_dedup_pair_visible(self, engine, resolver):
        """``job_create`` no longer emits a Task turn paired with a JobItem,
        so there is no dedup pair to drop. Verifying the simpler case:
        a JobItem + a process_report Task on the same instance both
        surface (no dedup)."""
        iid = _seed_instance(engine)
        jid = _seed_job(engine, instance_id=iid)
        report_wid = _seed_task(
            engine, instance_id=iid, task_type="process_report"
        )

        records = resolver.list_work()

        work_ids = {r.work_id for r in records}
        # Both records survive — no dedup gate to fire on (turns are
        # JobItems; reports are never deduped).
        assert work_ids == {jid, report_wid}, (
            f"JobItem + report Task pair must both surface. "
            f"Got: {[(r.work_id, r.kind) for r in records]}"
        )


class TestPartialCollapse_ResolveWorkStillResolvesReports:
    """The Task branch in ``resolve_work`` is retained (AD-6); report
    work_ids must still resolve to ``kind="report"``."""

    def test_resolve_work_on_report_task(self, engine, resolver):
        iid = _seed_instance(engine)
        wid = _seed_task(
            engine,
            instance_id=iid,
            task_type="process_report",
            status=TaskStatus.COMPLETED.value,
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.kind == "report"
        assert record.work_id == wid
        assert record.status == "completed"

    def test_resolve_work_on_process_message_task_still_resolves(
        self, engine, resolver
    ):
        """Legacy ``process_message`` rows ingested before the collapse
        still resolve (the Task branch is retained in
        ``resolve_work``). Kind will be ``"report"`` per the
        defensive fallback in ``_kind_from_task_type`` (no more
        ``"turn"``)."""
        iid = _seed_instance(engine)
        wid = _seed_task(
            engine,
            instance_id=iid,
            task_type="process_message",
            status=TaskStatus.COMPLETED.value,
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        # Phase 4 collapse: unknown task_type (process_message is no
        # longer in REPORT_TASK_TYPES) → "report" as defensive
        # fallback (the kind_from_task_type helper defaults to
        # "report" when the type is unrecognised).
        assert record.kind == "report", (
            f"Legacy process_message Task should resolve as 'report' "
            f"(Phase 4 defensive fallback). Got: {record.kind!r}"
        )

    def test_resolve_work_on_missing_returns_none(self, engine, resolver):
        """An unknown work_id → ``None`` (unchanged behaviour)."""
        assert resolver.resolve_work("not-a-real-work-id") is None


__all__ = [
    "TestPartialCollapse_NoTurnsInListWork",
    "TestPartialCollapse_NoDedupNoPromotion",
    "TestPartialCollapse_ResolveWorkStillResolvesReports",
]
