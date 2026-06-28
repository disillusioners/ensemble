"""Functional + edge-case tests for Job-as-Queue-Proxy Phase 1.

Phase 1 (commit 04f36724) of ``feature/job-as-queue-proxy`` routes
every job execution-state read through the Instance / WorkRecord
view-model instead of reading the JobItem mirror columns directly.
These tests pin the Phase 1 read-path contract:

* ``WorkResolverService._job_to_record`` sources ``status`` from the
  joined ``Instance`` row when ``job.instance_id`` is set.
* ``started_at`` / ``completed_at`` are sourced from the Instance
  timing columns (``last_activity_at`` / ``updated_at``) instead of
  the JobItem mirror.
* The legacy fallback (``instance_id is None`` or Instance row
  deleted) canonicalises the JobItem mirror status instead of
  hardcoding ``"pending"``.
* ``dead_letter`` is a JobItem-only state and is special-cased in
  ``_job_to_record`` so the Instance cannot override it.
* ``WorkResolverService.list_work`` fetches Instance rows in a single
  batched SELECT (``_batch_instances``) rather than one query per
  JobItem — eliminating the previous N+1 pattern.

All tests use the same in-memory SQLite + StaticPool fixture style as
``tests/unit/services/test_work_resolver.py`` (real repos against a
fresh schema, no mocks) so the read paths are exercised end-to-end
through the SQL stack, not just the Python mapping layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import JobItem, JobStatus
from daemon.repositories.job_queue.repository import JobRepository
from daemon.services.work_resolver import WorkRecord, WorkResolverService



# >>> test-local status_to_admission (Phase 4 cleanup) <<<
# Phase 4 cleanup removed ``status_to_admission`` from
# ``daemon.repositories.job_queue.models``. Redefined here for test
# seeds that derive ``admission_state`` from a ``status`` value.
def status_to_admission(status):  # noqa: ANN001,ANN201
    return {
        "pending": "queued",
        "processing": "active",
        "paused": "active",
        "completed": "done",
        "failed": "done",
        "cancelled": "done",
        "dead_letter": "dead",
    }.get(status, "queued")


class _NoTaskRepo:
    """Minimal stand-in for ``TaskRepository`` that returns ``None``
    from ``get_by_work_id``.

    Phase 1 (Job as Queue Proxy) tests exercise only the
    ``_job_to_record`` / ``list_work(kind='job')`` paths — the Task
    side of the union is never consulted. A ``MagicMock`` would return
    a truthy object from ``get_by_work_id`` and short-circuit
    ``resolve_work`` onto the Task branch (yielding a MagicMock-shaped
    WorkRecord instead of the JobItem-backed record under test). This
    stub keeps the resolver's task-first lookup order intact while
    cleanly falling through to the JobItem branch.

    ``list_work(kind='job')`` calls ``query_tasks_table = kind != 'job'``
    which evaluates ``False`` and skips the Task side entirely, so the
    stub needs no other methods.
    """

    @property
    def engine(self):  # pragma: no cover — never called under kind="job"
        raise AttributeError("TaskRepo engine is not used in JobItem-only tests")

    def get_by_work_id(self, work_id: str):  # noqa: ARG002
        return None


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool, FK pragma on).

    Mirrors the fixture used in ``tests/unit/services/test_work_resolver.py``
    — StaticPool keeps a single connection alive for the whole test so
    asyncio.to_thread workers (if any) share the in-memory store, and
    ``PRAGMA foreign_keys=ON`` matches the production daemon's SQLite
    posture.
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
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def resolver(
    job_repo: JobRepository,
    instance_repo: SQLModelInstanceRepository,
) -> WorkResolverService:
    """WorkResolverService with real Job + Instance repos and a stub TaskRepo.

    Phase 1's Job-as-Queue-Proxy tests don't exercise the Task side of
    the resolver union (JobItems only — the ``_job_to_record`` path
    under test). A MagicMock TaskRepository keeps the constructor
    happy without introducing a real Task table dependency.
    """
    return WorkResolverService(
        task_repo=_NoTaskRepo(),
        job_repo=job_repo,
        instance_repo=instance_repo,
    )


# ─── Helpers ────────────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    agent_id: str = "developer",
    project_id: str | None = "test-project",
    status: str = InstanceStatus.RUNNING.value,
    last_activity_at: datetime | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> str:
    """Insert an ``Instance`` row with full timing columns populated.

    Unlike the helper in ``test_work_resolver.py`` (which only seeds
    the basic identity columns), this fixture populates
    ``last_activity_at`` / ``created_at`` / ``updated_at`` so the
    Phase 1 ``_instance_started_at`` / ``_instance_completed_at``
    helpers have real values to read. Defaults reflect a freshly-
    active instance that started recently and is currently running.
    """
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    iso_now = now.isoformat()
    activity = last_activity_at if last_activity_at is not None else now
    created = created_at if created_at is not None else iso_now
    updated = updated_at if updated_at is not None else iso_now
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            agent_name=agent_id,
            project_id=project_id,
            status=status,
            created_at=created,
            updated_at=updated,
            last_activity_at=activity,
            paused_at=None,
            parent_id=None,
        )
        s.add(inst)
        s.commit()
    return iid


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
    started_at: str | None = None,
    completed_at: str | None = None,
) -> str:
    """Insert a ``JobItem`` row with explicit timing mirror columns.

    The ``started_at`` / ``completed_at`` defaults are intentionally
    ``None`` so tests that want to verify "Instance-sourced timing
    overrode the JobItem mirror" can pass distinctive values here and
    assert they did NOT appear in the response.
    """
    jid = job_id or str(uuid.uuid4())
    created = created_at or datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        job = JobItem(
            job_id=jid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            message="phase1 test job",
            source="api",
            project_id=project_id,
            priority=5,
            status=status,

            admission_state=status_to_admission(status),
            result_summary=result_summary,
            error_message=error_message,
            instance_id=instance_id,
            created_at=created,
            started_at=started_at,
            completed_at=completed_at,
            deleted_at=None,
            job_metadata={},
        )
        s.add(job)
        s.commit()
    return jid


# ─── Functional tests ──────────────────────────────────────────────────────


class TestInstanceDerivedStatus:
    """Phase 1 (Job as Queue Proxy) — execution status is sourced from
    the joined ``Instance`` row, NOT the JobItem ``status`` mirror.

    The JobItem ``status`` column is treated as a vestigial mirror
    that only carries queue-state information for jobs that haven't
    been dispatched to an instance yet (``instance_id IS NULL``).
    Once a job has a backing instance, the Instance's status is the
    source of truth and is canonicalised through
    ``work_status._STATUS_CANONICAL_MAP``.
    """

    def test_running_instance_overrides_pending_job_status(
        self, engine, resolver
    ):
        """JobItem.status='pending' + Instance.status='running'
        → WorkRecord.status='processing' (from Instance), not
        'pending' (from JobItem).

        The whole point of Phase 1: the Instance is the execution
        authority, so even a JobItem that still says 'pending' in its
        mirror column reports the actual in-flight state.
        """
        _seed_instance(engine, instance_id="inst-pending-overrun", status="running")
        jid = _seed_job(
            engine,
            instance_id="inst-pending-overrun",
            status=JobStatus.PENDING.value,
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.kind == "job"
        # Instance "running" canonicalises to "processing" via
        # _STATUS_CANONICAL_MAP. JobItem "pending" must NOT win.
        assert record.status == "processing", (
            f"Expected Instance-derived status='processing', got "
            f"{record.status!r} — the JobItem mirror overrode the "
            f"Instance status (Phase 1 invariant violated)."
        )

    def test_completed_job_mirror_overridden_by_active_instance(
        self, engine, resolver
    ):
        """JobItem.status='completed' + Instance.status='idle'
        → WorkRecord.status='processing' (from Instance, NOT 'completed').

        Drift case: the JobItem mirror says "completed" but the
        Instance is still active (e.g. a re-enqueue / retry path that
        updated the Instance without updating the mirror yet, or a
        mirror that was written by an older code path). Phase 1 says
        the Instance wins — the response should reflect "work in
        flight", not "all done".
        """
        _seed_instance(engine, instance_id="inst-drift", status="idle")
        jid = _seed_job(
            engine,
            instance_id="inst-drift",
            status=JobStatus.COMPLETED.value,
            result_summary="stale mirror says done",
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.status == "processing", (
            "JobItem mirror ('completed') leaked through despite an "
            "active Instance — Phase 1 invariant violated."
        )
        # ``result_summary`` is still sourced from the JobItem mirror
        # (the Instance doesn't model result storage in Phase 1).
        assert record.result_summary == "stale mirror says done"


class TestTimingColumnsFromInstance:
    """Phase 1 timing precedence: Instance columns beat JobItem mirror."""

    def test_started_at_sourced_from_instance_last_activity_at(
        self, engine, resolver
    ):
        """``started_at`` in the WorkRecord comes from
        ``Instance.last_activity_at`` (ISO-formatted), not from
        ``JobItem.started_at``.

        The fixture plants distinct values for both so the test can
        assert exactly which one the resolver picked.
        """
        activity = datetime(2026, 6, 1, 10, 30, 0, tzinfo=timezone.utc)
        _seed_instance(
            engine,
            instance_id="inst-timing",
            status="running",
            last_activity_at=activity,
            created_at="2026-06-01T10:00:00+00:00",
            updated_at="2026-06-01T10:30:00+00:00",
        )
        jid = _seed_job(
            engine,
            instance_id="inst-timing",
            status=JobStatus.PROCESSING.value,
            started_at="1999-01-01T00:00:00+00:00",  # clearly wrong on purpose
            completed_at="1999-01-01T00:00:00+00:00",
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        # The Instance's last_activity_at must win (2026-06-01T10:30
        # round-tripped through ISO format).
        assert record.started_at == activity.isoformat()
        # And the bogus JobItem mirror value must NOT appear.
        assert record.started_at != "1999-01-01T00:00:00+00:00"

    def test_completed_at_sourced_from_instance_updated_at_for_terminal(
        self, engine, resolver
    ):
        """When the Instance is terminal (``completed``),
        ``completed_at`` in the WorkRecord is sourced from
        ``Instance.updated_at``.

        The completed_at precedence rule in
        ``_instance_completed_at`` only surfaces the Instance
        ``updated_at`` when the canonical status is terminal —
        otherwise non-terminal jobs would falsely report a completion
        time.
        """
        updated = "2026-06-01T11:45:00+00:00"
        _seed_instance(
            engine,
            instance_id="inst-terminal",
            status="completed",
            updated_at=updated,
            last_activity_at=datetime(2026, 6, 1, 10, 30, 0, tzinfo=timezone.utc),
        )
        jid = _seed_job(
            engine,
            instance_id="inst-terminal",
            status=JobStatus.COMPLETED.value,
            started_at="2026-06-01T10:30:00+00:00",
            completed_at="1999-12-31T00:00:00+00:00",  # bogus mirror
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.status == "completed"
        # Instance.updated_at wins for the terminal-state completion
        # timestamp.
        assert record.completed_at == updated
        assert record.completed_at != "1999-12-31T00:00:00+00:00"

    def test_completed_at_falls_back_to_jobitem_mirror_for_non_terminal(
        self, engine, resolver
    ):
        """When the Instance is non-terminal, ``completed_at`` falls
        back to ``JobItem.completed_at`` (the JobItem mirror).

        The transitional contract per ``_instance_completed_at``: the
        Instance ``updated_at`` is only used when the Instance is in
        a terminal canonical state (``completed`` / ``failed`` /
        ``cancelled`` / ``dead_letter``). For a non-terminal Instance
        the resolver falls back to the JobItem mirror — the Instance
        has no authoritative completion timestamp to surface, so the
        legacy mirror is the best signal available during Phase 1's
        transition period.

        If a caller has populated ``JobItem.completed_at`` (e.g.
        an older code path that wrote the mirror before the
        Instance-backed resolver arrived), the WorkRecord still
        surfaces that value. The mirror column is being phased out
        in Phase 4; until then it remains the source for non-terminal
        JobItems.
        """
        _seed_instance(
            engine,
            instance_id="inst-nonterm",
            status="running",
            updated_at="2026-06-01T11:00:00+00:00",
            last_activity_at=datetime(2026, 6, 1, 11, 0, 0, tzinfo=timezone.utc),
        )
        # JobItem mirror populated (e.g. by an older writer). The
        # non-terminal Instance falls back to the mirror.
        jid = _seed_job(
            engine,
            instance_id="inst-nonterm",
            status=JobStatus.PROCESSING.value,
            started_at="2026-06-01T10:00:00+00:00",
            completed_at="2026-06-01T11:00:00+00:00",
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.status == "processing"
        # Non-terminal Instance + non-null mirror → the mirror is
        # surfaced (transitional behaviour). The Instance's own
        # ``updated_at`` is NOT used because the Instance hasn't
        # reached a terminal state.
        assert record.completed_at == "2026-06-01T11:00:00+00:00"

    def test_completed_at_none_when_both_instance_and_mirror_missing(
        self, engine, resolver
    ):
        """When both the Instance is non-terminal AND the JobItem
        mirror is ``None``, ``completed_at`` is ``None``.

        The "no completion time available" case — neither the
        Instance (non-terminal, so its ``updated_at`` is excluded
        by the terminal guard) nor the JobItem mirror has a
        completion timestamp to surface.
        """
        _seed_instance(
            engine,
            instance_id="inst-nonterm-clean",
            status="running",
            updated_at="2026-06-01T11:00:00+00:00",
            last_activity_at=datetime(2026, 6, 1, 11, 0, 0, tzinfo=timezone.utc),
        )
        jid = _seed_job(
            engine,
            instance_id="inst-nonterm-clean",
            status=JobStatus.PROCESSING.value,
            started_at="2026-06-01T10:00:00+00:00",
            completed_at=None,  # mirror also empty
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.status == "processing"
        # Nothing to surface → None.
        assert record.completed_at is None


class TestLegacyFallback:
    """Phase 1 legacy fallback: when the Instance is unavailable, the
    canonical status comes from ``canonicalize_status(job.status)``
    (NOT a hardcoded ``"pending"``).

    Two failure modes exercise the fallback:

    1. ``job.instance_id IS NULL`` — job is still queue-stage, hasn't
       been dequeued to an instance yet.
    2. ``job.instance_id`` set but the Instance row was deleted (orphan
       / project purge).

    The third test pins the ``dead_letter`` special-case: it's a
    JobItem-only state and the Instance cannot override it (Phase 1
    exception, lands cleanly in Phase 4 when ``admission_state`` is
    introduced).
    """

    def test_no_instance_canonicalises_job_status(self, engine, resolver):
        """JobItem with ``instance_id=None`` and a non-pending status
        surfaces that status canonicalised — NOT hardcoded 'pending'.

        Regression for the Phase 1 status-hardcoding bug: the previous
        implementation returned ``"pending"`` whenever ``instance_id``
        was ``None``, which discarded the real queue state for jobs
        that had not yet been dispatched (e.g. a JobItem seeded with
        ``status='completed'`` and ``instance_id=None`` would return
        ``'pending'`` and break the SSE terminal-status detection
        loop).
        """
        # Queue-stage JobItem, no instance_id, mirror says 'completed'.
        jid = _seed_job(
            engine,
            instance_id=None,
            status=JobStatus.COMPLETED.value,
            result_summary="orphaned completion",
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.instance_id is None
        # The canonical vocabulary is the same as the JobItem mirror
        # for the non-dead-letter set; 'completed' stays 'completed'.
        assert record.status == "completed", (
            f"Expected canonical 'completed', got {record.status!r}. "
            f"The legacy fallback may have been regressed to hardcode "
            f"'pending' when instance_id is None."
        )

    def test_deleted_instance_falls_back_to_job_mirror(
        self, engine, resolver
    ):
        """JobItem whose ``instance_id`` references a non-existent
        Instance returns the canonicalised JobItem mirror status
        without raising.

        This is the orphan / deleted-instance path. The resolver
        must degrade gracefully — ``_lookup_instance`` returns None
        and ``_job_to_record`` falls back to
        ``canonicalize_status(job.status)``.
        """
        # Insert a JobItem referencing a phantom instance that never
        # existed in the instances table.
        jid = _seed_job(
            engine,
            instance_id="ghost-instance-uuid",
            status=JobStatus.FAILED.value,
            error_message="orphan failure",
        )

        # Must NOT raise — graceful fallback.
        record = resolver.resolve_work(jid)

        assert record is not None
        # ``failed`` canonicalises to ``failed`` (identity mapping).
        assert record.status == "failed"
        assert record.error == "orphan failure"
        # ``instance_id`` stays the orphaned value — the WorkRecord
        # surfaces it as-is so callers can see the dangling reference.
        assert record.instance_id == "ghost-instance-uuid"

    def test_dead_letter_is_jobitem_only(self, engine, resolver):
        """``dead_letter`` is a JobItem-only canonical value: the
        resolver surfaces it regardless of what the Instance says.

        Phase 1 special-case in ``_job_to_record``: if the JobItem
        mirror is ``dead_letter``, return ``dead_letter`` directly —
        do NOT consult the Instance. This is documented as a Phase 1
        exception (Instance has no equivalent; Phase 2 introduces
        ``admission_state='dead'``) and must not regress when the
        Instance is in any other state.
        """
        # Instance in a non-dead-letter state — even so, the JobItem's
        # ``dead_letter`` must win.
        _seed_instance(
            engine,
            instance_id="inst-dlq",
            status="running",
        )
        jid = _seed_job(
            engine,
            instance_id="inst-dlq",
            status=JobStatus.DEAD_LETTER.value,
            error_message="max retries exhausted",
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.status == "dead_letter", (
            "dead_letter was overridden by the Instance lookup — "
            "Phase 1 special-case violated. The JobItem mirror is "
            "the source of truth for this state until Phase 4."
        )

    def test_dead_letter_with_no_instance(self, engine, resolver):
        """``dead_letter`` works on queue-stage rows too (no
        ``instance_id``).

        Belt-and-braces: the dead_letter special-case must work
        independently of the instance_id-presence branch. The
        ``if job.admission_state == 'dead': status = 'dead_letter'``
        guard fires before the instance_id branch, so a queue-stage
        dead-letter row reports ``dead_letter`` without trying to
        look up an Instance.
        """
        jid = _seed_job(
            engine,
            instance_id=None,
            status=JobStatus.DEAD_LETTER.value,
            error_message="queue-stage DLQ",
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.status == "dead_letter"
        assert record.instance_id is None


class TestListWorkBatchEfficiency:
    """Phase 1 N+1 elimination: ``list_work`` fetches all Instance
    rows for a page of JobItems in a SINGLE batched SELECT, not one
    per row.

    The contract under test: doubling the JobItem count should NOT
    double the number of ``SELECT … FROM instances`` queries. The
    batched path issues exactly one Instance query per ``list_work``
    call (plus at most one for the parent_id child-instance batch
    lookup), regardless of how many JobItems are on the page.

    The implementation detail being verified is in
    ``WorkResolverService._batch_instances`` — a single
    ``SELECT … WHERE instance_id IN (...)`` against the ``instances``
    table.
    """

    def _count_instances_selects(self, engine: Engine) -> list[str]:
        """Capture every ``SELECT`` statement against the ``instances``
        table issued during a test block.

        Uses SQLAlchemy's ``before_cursor_execute`` event to record
        statements. Returns the list of statement strings so the test
        can filter to ``SELECT … FROM instances …`` shapes. The
        collection is bound to the engine so other engines in the
        process (which the test runner may have left over from other
        test files) are NOT polluted.
        """
        captured: list[str] = []

        def _capture(_conn, _cursor, statement, _params, _ctx, _executemany):
            captured.append(statement)

        event.listen(engine, "before_cursor_execute", _capture)
        return captured

    @staticmethod
    def _filter_instance_selects(statements: list[str]) -> list[str]:
        """Return only the SELECTs that touch the ``instances`` table.

        Filters the captured statement list to SELECTs whose FROM
        clause mentions ``instances`` (covers both the per-row
        ``_lookup_instance`` path AND the batched
        ``_batch_instances`` path). Excludes INSERT / UPDATE /
        CREATE so test setup noise doesn't dilute the count.
        """
        return [
            s
            for s in statements
            if "SELECT" in s.upper() and "FROM instances" in s.lower()
        ]

    def test_list_work_uses_one_instance_query_per_call(
        self, engine, resolver
    ):
        """``list_work`` issues exactly ONE Instance SELECT,
        regardless of the JobItem count on the page.

        Seeds N JobItems each with its own backing Instance, calls
        ``list_work`` once, and asserts the number of Instance
        SELECTs is bounded by a small constant (1 batched fetch +
        at most 1 child-instance lookup) — not N (the previous N+1).
        """
        # Seed N job/instance pairs. 10 is enough to make N+1
        # obviously wrong (10 instead of 1) without bloating the test.
        n_jobs = 10
        for _ in range(n_jobs):
            inst_id = f"inst-batch-{uuid.uuid4().hex[:8]}"
            _seed_instance(engine, instance_id=inst_id, status="running")
            _seed_job(
                engine,
                instance_id=inst_id,
                status=JobStatus.PROCESSING.value,
            )

        # Capture every query issued during list_work.
        captured: list[str] = []

        def _capture(_c, _cur, stmt, _p, _ctx, _exe):
            captured.append(stmt)

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            # Reset capture (the seed inserts issued SELECTs we don't
            # care about; we want only the list_work ones).
            captured.clear()

            records = resolver.list_work(kind="job")
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        # The page should be all N jobs.
        assert len(records) == n_jobs

        # Instance SELECTs must be a small fixed count (1 batched
        # fetch + at most 1 child-instance batched lookup), NOT N.
        instance_selects = self._filter_instance_selects(captured)
        assert len(instance_selects) <= 2, (
            f"list_work with {n_jobs} jobs issued "
            f"{len(instance_selects)} SELECTs against the instances "
            f"table — expected at most 2 (1 batched fetch + 1 child "
            f"lookup). N+1 regression: the count must be O(1) in the "
            f"page size, not O(N)."
        )

    def test_list_work_query_count_is_invariant_to_page_size(
        self, engine, job_repo, instance_repo
    ):
        """The Instance-SELECT count for ``list_work`` does not grow
        with the JobItem count.

        Seeds 1 / 5 / 10 JobItems and asserts each call issues the
        SAME number of Instance SELECTs. A N+1 implementation would
        show 1, 5, 10; the batched path shows 1, 1, 1.
        """
        sizes_and_counts: list[tuple[int, int]] = []

        for n_jobs in (1, 5, 10):
            # Use a fresh engine per page-size so the listener
            # state (bound to one engine) doesn't leak between
            # measurements.
            sub_engine = create_engine(
                "sqlite:///:memory:",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            SQLModel.metadata.create_all(sub_engine)
            sub_job_repo = JobRepository(sub_engine)
            sub_instance_repo = SQLModelInstanceRepository(sub_engine)
            sub_resolver = WorkResolverService(
                task_repo=_NoTaskRepo(),
                job_repo=sub_job_repo,
                instance_repo=sub_instance_repo,
            )

            captured: list[str] = []

            def _capture(_c, _cur, stmt, _p, _ctx, _exe):
                captured.append(stmt)

            event.listen(sub_engine, "before_cursor_execute", _capture)
            try:
                # Seed N jobs each on its own instance.
                for _ in range(n_jobs):
                    inst_id = f"inst-inv-{uuid.uuid4().hex[:8]}"
                    _seed_instance(sub_engine, instance_id=inst_id, status="running")
                    _seed_job(
                        sub_engine,
                        instance_id=inst_id,
                        status=JobStatus.PROCESSING.value,
                    )

                # Reset capture (the seed inserts issued SELECTs we
                # don't care about; we want only the list_work ones).
                captured.clear()

                records = sub_resolver.list_work(kind="job")
                assert len(records) == n_jobs
            finally:
                event.remove(sub_engine, "before_cursor_execute", _capture)
                sub_engine.dispose()

            # Count SELECTs against the instances table.
            instance_selects = self._filter_instance_selects(captured)
            sizes_and_counts.append((n_jobs, len(instance_selects)))

        # The batched path: one SELECT per list_work call regardless
        # of page size. (The constant may be 1 for the batched
        # instance fetch plus 1 for the parent_id child-instance
        # lookup — we accept any small fixed count that does NOT grow
        # with N.)
        for n_jobs, count in sizes_and_counts:
            assert count <= 2, (
                f"list_work with {n_jobs} jobs issued {count} SELECTs "
                f"against the instances table — expected a small "
                f"fixed number (1 batched fetch + optional child "
                f"lookup). The N+1 regression has resurfaced."
            )

        # And critically: the count does NOT scale with n_jobs.
        counts = [c for _n, c in sizes_and_counts]
        assert max(counts) == min(counts), (
            f"Instance SELECT count varies with page size: "
            f"{sizes_and_counts}. N+1 regression: the count must be "
            f"invariant to the JobItem count."
        )


# ─── Edge cases ────────────────────────────────────────────────────────────


class TestJobWithNoInstance:
    """E1: JobItem with ``instance_id=None`` (freshly queued).

    The ``_job_to_record`` ``instance_id IS NULL`` branch falls back
    to ``canonicalize_status(job.status)`` for the status field. The
    ``started_at`` / ``completed_at`` helpers also fall back to the
    JobItem mirror in this branch (no Instance to consult).
    """

    def test_freshly_queued_job_surfaces_pending_status(
        self, engine, resolver
    ):
        """A freshly-queued ``instance_id=None`` JobItem surfaces
        ``status='pending'`` (the canonical form of the JobItem
        mirror)."""
        jid = _seed_job(
            engine,
            instance_id=None,
            status=JobStatus.PENDING.value,
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.status == "pending"
        assert record.instance_id is None
        # No Instance means no timing source — both fields are None.
        assert record.started_at is None
        assert record.completed_at is None


class TestJobWithDeletedInstance:
    """E2: JobItem referencing a non-existent Instance.

    The resolver must NOT crash. The canonical status falls back to
    ``canonicalize_status(job.status)`` and the timing fields fall
    back to the JobItem mirror.
    """

    def test_orphan_job_does_not_crash(self, engine, resolver):
        """JobItem.instance_id points to a UUID with no Instance row
        → resolve_work returns a record without raising.

        Defensive contract: ``_lookup_instance`` swallows the
        SQLAlchemyError / missing-row case and returns None, then
        ``_job_to_record`` uses the canonicalised JobItem mirror.
        """
        jid = _seed_job(
            engine,
            instance_id="deleted-uuid-12345",
            status=JobStatus.PROCESSING.value,
            started_at="2026-06-01T10:00:00+00:00",
            completed_at=None,
        )

        # Must not raise.
        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.instance_id == "deleted-uuid-12345"
        # No Instance row → canonicalise the JobItem mirror.
        assert record.status == "processing"
        # Timing falls back to the JobItem mirror (no Instance to source from).
        assert record.started_at == "2026-06-01T10:00:00+00:00"
        assert record.completed_at is None


class TestJobWithCompletedInstance:
    """E3: JobItem whose backing Instance is in ``completed`` state.

    Instance ``completed`` canonicalises to ``completed``. The
    WorkRecord surfaces ``completed`` and sources ``completed_at``
    from ``Instance.updated_at``.
    """

    def test_completed_instance_surfaces_completed_status(
        self, engine, resolver
    ):
        """Instance.status='completed' → WorkRecord.status='completed',
        ``completed_at`` from ``Instance.updated_at``."""
        completed_at = "2026-06-01T12:00:00+00:00"
        _seed_instance(
            engine,
            instance_id="inst-completed",
            status="completed",
            updated_at=completed_at,
            last_activity_at=datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
        )
        jid = _seed_job(
            engine,
            instance_id="inst-completed",
            status=JobStatus.PROCESSING.value,
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.status == "completed"
        assert record.completed_at == completed_at


class TestJobWithErrorInstance:
    """E4: JobItem whose backing Instance is in ``error`` state.

    Instance ``error`` canonicalises to ``failed`` (Phase 1 mapping
    in ``_STATUS_CANONICAL_MAP``).
    """

    def test_error_instance_surfaces_failed_status(
        self, engine, resolver
    ):
        """Instance.status='error' → WorkRecord.status='failed'."""
        _seed_instance(
            engine,
            instance_id="inst-err",
            status="error",
        )
        jid = _seed_job(
            engine,
            instance_id="inst-err",
            status=JobStatus.PROCESSING.value,
            error_message="instance-level error",
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        # The Instance-status → canonical mapping: 'error' → 'failed'.
        assert record.status == "failed", (
            f"Expected canonical 'failed' for Instance.status='error', "
            f"got {record.status!r}. The Instance-status canonical map "
            f"is missing the 'error' → 'failed' entry."
        )


class TestJobWithWaitingChildrenInstance:
    """E5: JobItem whose backing Instance is in ``waiting_children``.

    Instance ``waiting_children`` is in the "active cluster" — it
    canonicalises to ``processing`` because from the resolver's POV
    the work unit is still in flight (the parent is waiting for
    child completion reports, but the parent's own execution state
    has not terminalised).
    """

    def test_waiting_children_instance_surfaces_processing(
        self, engine, resolver
    ):
        """Instance.status='waiting_children' → WorkRecord.status='processing'."""
        _seed_instance(
            engine,
            instance_id="inst-wc",
            status="waiting_children",
        )
        jid = _seed_job(
            engine,
            instance_id="inst-wc",
            status=JobStatus.PROCESSING.value,
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.status == "processing", (
            f"Expected canonical 'processing' for "
            f"Instance.status='waiting_children', got {record.status!r}."
        )


# ─── Sanity smoke ───────────────────────────────────────────────────────────


class TestSmoke:
    """One quick smoke test to catch gross regressions in the fixture
    wiring. If this fails, the test file itself is broken (not the
    code under test)."""

    def test_resolver_smoke_returns_none_for_unknown_id(self, resolver):
        """Sanity: ``resolve_work`` returns ``None`` for an id in
        neither table. If this fails, the fixture / imports are
        misconfigured."""
        record = resolver.resolve_work(str(uuid.uuid4()))
        assert record is None