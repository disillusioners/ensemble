"""Unit tests for the Virtual Job Management Surface — Phase 1 (Batch 4).

Tests cover two cooperating modules:

* ``daemon.services.work_status`` — ``canonicalize_status`` and
  ``is_terminal`` helpers that map the per-table Task / JobItem status
  strings onto a single canonical virtual-job vocabulary.
* ``daemon.services.work_resolver`` — ``WorkResolverService.resolve_work``
  and ``WorkResolverService.list_work``, the unified read API that
  collapses the worker pool's ``task`` table and the dependency bus's
  ``job_queue_items`` table onto a :class:`WorkRecord` view-model.

Pattern follows the existing in-memory SQLite repository tests
(``tests/unit/test_instance_tree_loading.py``,
``tests/unit/test_pause_resume_root.py``) — a fresh engine per test
backed by ``StaticPool`` and ``PRAGMA foreign_keys=ON``, with
``SQLModel.metadata.create_all`` to materialise the schema and direct
``Session`` inserts for fixture data so the tests can pin known
``work_id`` values.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import JobItem, JobStatus
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.work_resolver import WorkRecord, WorkResolverService
from daemon.services.work_status import canonicalize_status, is_terminal


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool for cross-thread safety).

    Same shape as ``tests/unit/test_pause_resume_root.py::engine`` —
    StaticPool keeps one connection alive for the whole test so
    asyncio.to_thread workers (if any) share the in-memory store, and
    PRAGMA foreign_keys=ON matches the production daemon's SQLite
    posture so any future FK-guarded reads behave the same here as in
    production.
    """
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
    """Real ``TaskRepository`` bound to the in-memory engine."""
    return TaskRepository(engine)


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    """Real ``JobRepository`` bound to the in-memory engine."""
    return JobRepository(engine)


@pytest.fixture
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    """Real ``SQLModelInstanceRepository`` bound to the in-memory engine."""
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def resolver(task_repo: TaskRepository, job_repo: JobRepository,
             instance_repo: SQLModelInstanceRepository) -> WorkResolverService:
    """Constructed ``WorkResolverService`` against the three real repos."""
    return WorkResolverService(task_repo, job_repo, instance_repo)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    agent_id: str = "developer",
    project_id: str | None = "test-project",
    status: str = "running",
) -> str:
    """Insert an ``Instance`` row. Returns the ``instance_id``."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            agent_name=agent_id,
            project_id=project_id,
            status=status,
            created_at=now_iso,
            updated_at=now_iso,
            paused_at=None,
        )
        s.add(inst)
        s.commit()
    return iid


def _seed_task(
    engine: Engine,
    *,
    work_id: str | None = None,
    instance_id: str = "inst-test",
    status: str = TaskStatus.PENDING.value,
    result: str | None = None,
    error: str | None = None,
    project_id: str | None = "test-project",
    created_at: datetime | None = None,
    seed_instance: bool = True,
) -> str:
    """Insert a ``Task`` row. Returns the ``work_id`` (auto-generated if None).

    By default this also seeds the matching ``Instance`` row carrying
    ``project_id`` (and the default ``agent_id``) so the resolver's
    ``_lookup_instance`` post-fetch can resolve both ``agent_id`` and
    ``project_id``. Pass ``seed_instance=False`` for tests that
    intentionally exercise the "instance deleted / orphaned work unit"
    path (e.g. ``test_resolve_work_task_without_instance_returns_none_agent_id``).
    """
    if seed_instance:
        # Match the resolver's lookup: ``_lookup_instance(task.instance_id)``
        # must return a row with the right ``project_id`` for the post-fetch
        # filter in ``list_work`` to keep the row. Skip if the test
        # already seeded the same instance_id with the right
        # project_id (some tests seed it explicitly so they can pin
        # agent_id / other fields). The check is a cheap PK lookup;
        # the engine is in-memory and the table is small.
        with Session(engine) as s:
            existing = s.get(Instance, instance_id)
        if existing is None or existing.project_id != project_id:
            _seed_instance(
                engine,
                instance_id=instance_id,
                project_id=project_id,
            )
    wid = work_id or str(uuid.uuid4())
    created = created_at or datetime.now(timezone.utc)
    with Session(engine) as s:
        task = Task(
            work_id=wid,
            task_type="process_message",
            instance_id=instance_id,
            message_id=None,
            status=status,
            result=result,
            error=error,
            created_at=created,
        )
        s.add(task)
        s.commit()
    return wid


def _seed_job(
    engine: Engine,
    *,
    job_id: str | None = None,
    agent_id: str = "developer",
    instance_id: str | None = None,
    status: str = JobStatus.PENDING.value,
    result_summary: str | None = None,
    error_message: str | None = None,
    project_id: str | None = "test-project",
    created_at: str | None = None,
    deleted_at: str | None = None,
) -> str:
    """Insert a ``JobItem`` row. Returns the ``job_id`` (auto-generated if None)."""
    jid = job_id or str(uuid.uuid4())
    created = created_at or datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        job = JobItem(
            job_id=jid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            message="test message",
            source="api",
            project_id=project_id,
            priority=5,
            status=status,
            result_summary=result_summary,
            error_message=error_message,
            instance_id=instance_id,
            created_at=created,
            deleted_at=deleted_at,
            job_metadata={},
        )
        s.add(job)
        s.commit()
    return jid


# ─── work_status canonicalization ───────────────────────────────────────────


class TestCanonicalizeStatus:
    """``work_status.canonicalize_status`` maps both Task and JobItem
    source strings onto the canonical virtual-job vocabulary."""

    def test_task_running_maps_to_processing(self):
        # Task uses "running"; the resolver surface speaks "processing".
        assert canonicalize_status("running") == "processing"

    def test_jobitem_dead_letter_passes_through(self):
        # dead_letter only exists on JobItem side and is already canonical.
        assert canonicalize_status("dead_letter") == "dead_letter"

    def test_paused_passes_through(self):
        # paused is identical in both vocabularies.
        assert canonicalize_status("paused") == "paused"

    def test_unknown_status_passes_through_unchanged(self):
        # Defensive: a future/unknown status is returned verbatim so the
        # resolver does not crash on a status the map has not been
        # taught about.
        assert canonicalize_status("unknown_status") == "unknown_status"


class TestIsTerminal:
    """``work_status.is_terminal`` recognises terminal virtual-job states."""

    def test_completed_is_terminal(self):
        assert is_terminal("completed") is True

    def test_paused_is_not_terminal(self):
        # paused is intentionally non-terminal — a paused work unit can
        # be resumed back to processing.
        assert is_terminal("paused") is False

    def test_dead_letter_is_terminal(self):
        assert is_terminal("dead_letter") is True

    def test_processing_is_not_terminal(self):
        assert is_terminal("processing") is False

    def test_unknown_status_is_not_terminal(self):
        # Conservative default: unknown → "might still move" so callers
        # do not accidentally treat a future status as final.
        assert is_terminal("some_future_status") is False


# ─── WorkResolverService.resolve_work ───────────────────────────────────────


class TestResolveWork:
    """``WorkResolverService.resolve_work`` looks up a work_id across
    both the Task and JobItem tables and returns a populated
    :class:`WorkRecord`."""

    def test_resolve_work_job_returns_job_record(
        self, engine, resolver, job_repo
    ):
        """``resolve_work(job.job_id)`` returns a JobItem-backed record."""
        _seed_instance(engine, instance_id="inst-1")
        jid = _seed_job(
            engine,
            status=JobStatus.PROCESSING.value,
            instance_id="inst-1",
            result_summary="all done",
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.kind == "job"
        assert record.work_id == jid
        # JobItem "processing" stays "processing" on the canonical side.
        assert record.status == "processing"
        assert record.instance_id == "inst-1"
        assert record.project_id == "test-project"
        assert record.agent_id == "developer"
        assert record.result_summary == "all done"
        assert record.error is None
        # Sanity-check the underlying row really is what we asked for.
        assert job_repo.get(jid) is not None

    def test_resolve_work_task_returns_task_record(
        self, engine, resolver, task_repo
    ):
        """``resolve_work(task.work_id)`` returns a Task-backed record,
        including the agent_id lookup from the instance and the
        result_summary parse from ``Task.result`` JSON."""
        _seed_instance(engine, instance_id="inst-task", agent_id="developer")
        # Task.result is a JSON string carrying the assistant payload.
        result_payload = json.dumps({"answer": "42", "sources": ["a", "b"]})
        wid = _seed_task(
            engine,
            instance_id="inst-task",
            status=TaskStatus.RUNNING.value,
            result=result_payload,
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.kind == "task"
        assert record.work_id == wid
        # Task "running" canonicalises to "processing".
        assert record.status == "processing"
        assert record.instance_id == "inst-task"
        assert record.project_id == "test-project"
        # agent_id must be looked up from the Instance row — Task does
        # not carry it directly.
        assert record.agent_id == "developer"
        # ``Task.result`` is parsed; a non-string payload is re-serialised
        # so the frontend always receives a string.
        assert isinstance(record.result_summary, str)
        parsed = json.loads(record.result_summary)
        assert parsed == {"answer": "42", "sources": ["a", "b"]}
        assert record.error is None
        # Sanity-check the underlying row really is what we asked for.
        assert task_repo.get_by_work_id(wid) is not None

    def test_resolve_work_task_result_summary_string_passthrough(
        self, engine, resolver
    ):
        """If ``Task.result`` is a JSON string already, it's returned as-is
        (mirrors the rule in ``daemon/routers/messages.py:251-263``)."""
        _seed_instance(engine, instance_id="inst-2")
        wid = _seed_task(
            engine,
            instance_id="inst-2",
            status=TaskStatus.COMPLETED.value,
            result=json.dumps("a plain string"),
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        # When the parsed payload is itself a string, the resolver
        # hands it back verbatim — no double JSON encoding.
        assert record.result_summary == "a plain string"

    def test_resolve_work_task_result_none(self, engine, resolver):
        """If ``Task.result`` is empty, ``result_summary`` is ``None``."""
        _seed_instance(engine, instance_id="inst-3")
        wid = _seed_task(
            engine,
            instance_id="inst-3",
            status=TaskStatus.COMPLETED.value,
            result=None,
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.result_summary is None

    def test_resolve_work_task_result_invalid_json_falls_back_to_raw(
        self, engine, resolver
    ):
        """If ``Task.result`` fails to parse as JSON, fall back to the raw string."""
        _seed_instance(engine, instance_id="inst-4")
        wid = _seed_task(
            engine,
            instance_id="inst-4",
            status=TaskStatus.FAILED.value,
            result="not valid json {",
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.result_summary == "not valid json {"

    def test_resolve_work_task_paused_status(self, engine, resolver):
        """``paused`` canonicalises identically on both sides."""
        _seed_instance(engine, instance_id="inst-paused")
        wid = _seed_task(
            engine,
            instance_id="inst-paused",
            status=TaskStatus.PAUSED.value,
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.status == "paused"

    def test_resolve_work_job_dead_letter_status(self, engine, resolver):
        """``dead_letter`` is a JobItem-only canonical value."""
        jid = _seed_job(
            engine,
            status=JobStatus.DEAD_LETTER.value,
            error_message="retries exhausted",
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.kind == "job"
        assert record.status == "dead_letter"
        assert record.error == "retries exhausted"

    def test_resolve_work_unknown_returns_none(self, resolver):
        """A random UUID present in neither table returns ``None``."""
        missing_id = str(uuid.uuid4())

        record = resolver.resolve_work(missing_id)

        assert record is None

    def test_resolve_work_task_shadows_job_with_same_id(
        self, engine, resolver
    ):
        """Task-first lookup order means a Task row's ``work_id`` shadows
        any JobItem row that happens to share the value (defence against
        the theoretical UUID collision case)."""
        # Both tables get the same identifier — only one row can win.
        shared_id = str(uuid.uuid4())
        _seed_instance(engine, instance_id="inst-shadow")
        _seed_task(
            engine,
            work_id=shared_id,
            instance_id="inst-shadow",
            status=TaskStatus.RUNNING.value,
        )
        _seed_job(
            engine,
            job_id=shared_id,
            status=JobStatus.PROCESSING.value,
            instance_id="inst-shadow",
        )

        record = resolver.resolve_work(shared_id)

        # The task-first branch wins.
        assert record is not None
        assert record.kind == "task"

    def test_resolve_work_task_without_instance_returns_none_agent_id(
        self, engine, resolver
    ):
        """A task whose instance was deleted returns ``agent_id=None``
        (orphaned work unit) without blowing up."""
        # Insert a task referencing an instance that doesn't exist in
        # the Instance table — the resolver must degrade gracefully.
        # ``seed_instance=False`` opts out of the fixture's auto-seeding
        # so the resolver's ``_lookup_instance`` returns None.
        wid = _seed_task(
            engine,
            instance_id="ghost-instance",
            status=TaskStatus.COMPLETED.value,
            seed_instance=False,
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.kind == "task"
        assert record.instance_id == "ghost-instance"
        assert record.agent_id is None


# ─── WorkResolverService.list_work ──────────────────────────────────────────


class TestListWork:
    """``WorkResolverService.list_work`` returns the union of Task and
    JobItem rows matching the supplied filters, newest-first."""

    def test_list_work_empty(self, resolver):
        """No rows in either table → empty list."""
        assert resolver.list_work() == []

    def test_list_work_returns_both_kinds(self, engine, resolver):
        """With no filters, ``list_work`` returns rows from both tables."""
        _seed_instance(engine, instance_id="inst-a")
        _seed_instance(engine, instance_id="inst-b")
        task_id = _seed_task(
            engine, instance_id="inst-a", status=TaskStatus.PENDING.value
        )
        job_id = _seed_job(
            engine, instance_id="inst-b", status=JobStatus.PENDING.value
        )

        records = resolver.list_work()

        assert len(records) == 2
        work_ids = {r.work_id for r in records}
        assert work_ids == {task_id, job_id}
        assert {r.kind for r in records} == {"task", "job"}

    def test_list_work_filter_by_project_id(self, engine, resolver):
        """``project_id`` filter narrows results to one project."""
        _seed_task(engine, instance_id="i1", project_id="proj-A")
        _seed_task(engine, instance_id="i2", project_id="proj-B")
        _seed_job(engine, project_id="proj-A")
        _seed_job(engine, project_id="proj-B")

        records = resolver.list_work(project_id="proj-A")

        assert len(records) == 2
        assert {r.project_id for r in records} == {"proj-A"}

    def test_list_work_filter_by_instance_id(self, engine, resolver):
        """``instance_id`` filter narrows results to one instance."""
        _seed_task(engine, instance_id="inst-target")
        _seed_task(engine, instance_id="inst-other")
        _seed_job(engine, instance_id="inst-target")
        _seed_job(engine, instance_id="inst-other")

        records = resolver.list_work(instance_id="inst-target")

        assert len(records) == 2
        assert {r.instance_id for r in records} == {"inst-target"}

    def test_list_work_filter_by_status_canonical(self, engine, resolver):
        """A canonical ``status`` filter matches both Task ``running`` and
        JobItem ``processing`` (the two source values that canonicalise
        to ``"processing"``)."""
        _seed_task(engine, instance_id="i1", status=TaskStatus.RUNNING.value)
        _seed_task(engine, instance_id="i2", status=TaskStatus.PENDING.value)
        _seed_job(engine, status=JobStatus.PROCESSING.value)
        _seed_job(engine, status=JobStatus.PENDING.value)

        records = resolver.list_work(status="processing")

        assert len(records) == 2
        assert {r.status for r in records} == {"processing"}
        # One Task (status="running") and one JobItem (status="processing")
        # both canonicalise to "processing".
        assert {r.kind for r in records} == {"task", "job"}

    def test_list_work_filter_by_status_dead_letter_only_matches_jobs(
        self, engine, resolver
    ):
        """``dead_letter`` is JobItem-only — Task rows never match."""
        _seed_task(engine, instance_id="i-dl", status=TaskStatus.FAILED.value)
        _seed_job(engine, status=JobStatus.DEAD_LETTER.value)
        _seed_job(engine, status=JobStatus.PENDING.value)

        records = resolver.list_work(status="dead_letter")

        assert len(records) == 1
        assert records[0].kind == "job"
        assert records[0].status == "dead_letter"

    def test_list_work_filter_by_kind_task(self, engine, resolver):
        """``kind="task"`` excludes JobItem rows."""
        _seed_task(engine, instance_id="i1")
        _seed_job(engine, instance_id="i2")

        records = resolver.list_work(kind="task")

        assert len(records) == 1
        assert records[0].kind == "task"

    def test_list_work_filter_by_kind_job(self, engine, resolver):
        """``kind="job"`` excludes Task rows."""
        _seed_task(engine, instance_id="i1")
        _seed_job(engine, instance_id="i2")

        records = resolver.list_work(kind="job")

        assert len(records) == 1
        assert records[0].kind == "job"

    def test_list_work_sort_newest_first(self, engine, resolver):
        """Records are returned ordered by ``created_at`` DESC."""
        older = _seed_task(
            engine,
            instance_id="i1",
            created_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        )
        newer = _seed_job(
            engine,
            created_at=datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
        )

        records = resolver.list_work()

        assert len(records) == 2
        # Newer first.
        assert records[0].work_id == newer
        assert records[1].work_id == older

    def test_list_work_excludes_soft_deleted_jobs(self, engine, resolver):
        """``deleted_at IS NOT NULL`` JobItems are invisible to the
        virtual job surface (matches ``JobRepository.list`` default)."""
        active_id = _seed_job(
            engine, status=JobStatus.PENDING.value, deleted_at=None
        )
        _seed_job(
            engine, status=JobStatus.PENDING.value,
            deleted_at=datetime.now(timezone.utc).isoformat(),
        )
        _seed_task(engine, instance_id="i-task")

        records = resolver.list_work()

        work_ids = {r.work_id for r in records}
        assert active_id in work_ids
        # The soft-deleted job must NOT appear.
        assert len(records) == 2  # 1 task + 1 active job

    def test_list_work_combines_multiple_filters(self, engine, resolver):
        """Filters compose with AND semantics."""
        _seed_task(
            engine,
            instance_id="i-match",
            project_id="proj-match",
            status=TaskStatus.RUNNING.value,
        )
        _seed_task(
            engine,
            instance_id="i-other",
            project_id="proj-match",
            status=TaskStatus.RUNNING.value,
        )
        _seed_job(
            engine,
            instance_id="i-match",
            project_id="proj-match",
            status=JobStatus.PROCESSING.value,
        )

        records = resolver.list_work(
            project_id="proj-match",
            instance_id="i-match",
            status="processing",
        )

        # 1 task + 1 job, both matching all three filters.
        assert len(records) == 2
        assert {r.kind for r in records} == {"task", "job"}
        for r in records:
            assert r.project_id == "proj-match"
            assert r.instance_id == "i-match"
            assert r.status == "processing"
