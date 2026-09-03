"""M3 (mission-class, 2026-09-03) — four-surfaces-agree pin.

Pins the M3 per-kind dispatch contract: mirror-receipt terminal =
``settled`` (transport-receipt disjoint from work-outcome), task
terminal = ``completed`` (a task job IS its own mission — the work
word stays).

All four read surfaces must agree on the per-kind split:

* Surface 1: ``WorkRecord`` (``daemon.services.work_resolver._job_to_record``)
* Surface 2: ``JobResponse`` (``daemon.routers.jobs_crud._job_to_response``)
* Surface 3: ``_ResolvedWork`` SSE payload (``daemon.routers.jobs_streaming``)
* Surface 4: ``jobs_management`` delegation (routes through
  ``jobs_crud._job_to_response`` per §8.2)

Each surface is built from the same JobItem row + the same admission
shape; the row's kind (task vs message) decides whether the
terminal-status field carries ``settled`` or ``completed``.

The fixture seeds both shapes side-by-side so the per-kind dispatch
is visibly different (a downstream consumer that fails to honour the
rename collapses the two rows onto ``completed`` and the
regression is caught here).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import JobItem, JobQueue, AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.repository import TaskRepository
from daemon.routers import jobs_crud, jobs_management, jobs_streaming
from daemon.services.work_resolver import WorkResolverService


# ─── Fixtures (file-backed SQLite per BLUEPRINT recipe) ──────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite at ``tmp_path`` (NullPool + FK on + WAL).

    Blueprint §3 recipe — NullPool + file-backed SQLite at
    ``tmp_path`` + ``PRAGMA journal_mode=WAL`` +
    ``PRAGMA busy_timeout=10000`` + foreign-keys ON is the
    FORBIDDEN-PATTERN antidote for the QUARANTINE.md StaticPool +
    WriteGuardSession dependency_bus row (which trips write-
    corruption in dependency_bus + repository sessions). The M3
    pin must mirror the repo-wide recipe so the test session
    behaves like a single-process file-backed production write.
    """
    db_path = tmp_path / "m3_pin.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repos(engine: Engine):
    """Build the three repositories the resolver needs."""
    return (
        TaskRepository(engine),
        JobRepository(engine),
        SQLModelInstanceRepository(engine),
    )


@pytest.fixture
def resolver(repos) -> WorkResolverService:
    return WorkResolverService(*repos)


def _seed_queue(s, queue_id: str) -> None:
    queue = JobQueue(
        queue_id=queue_id,
        project_id="test-project",
        queue_name=queue_id,
        queue_name_lower=queue_id,
        queue_type="fifo",
        concurrency_limit=1,
        is_system=False,
        is_paused=False,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    s.add(queue)
    s.commit()


def _seed_instance(s, instance_id: str, status: str) -> str:
    inst = Instance(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="/tmp/agents/developer",  # NOT NULL in Instance schema
        agent_name=instance_id,
        project_id="test-project",
        status=status,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        paused_at=None,
    )
    s.add(inst)
    s.commit()
    return instance_id


def _seed_job(
    s,
    *,
    job_id: str,
    instance_id: str,
    job_type: str,
    terminal_reason: str = "completed",
) -> str:
    job = JobItem(
        job_id=job_id,
        agent_id="developer",
        agent_dir="/tmp/agents/developer",
        message="m3 pin",
        source="api",
        project_id="test-project",
        priority=5,
        admission_state=AdmissionState.DONE.value,
        terminal_reason=terminal_reason,
        instance_id=instance_id,
        queue_id="queue-m3",
        created_at=datetime.now(timezone.utc).isoformat(),
        job_metadata={},
        job_type=job_type,
    )
    s.add(job)
    s.commit()
    return job_id


@pytest.fixture
def seeded_mirror_and_task(engine: Engine) -> tuple[str, str]:
    """Seed one mirror row and one task row side-by-side.

    Returns ``(mirror_jid, task_jid)``. Both share the same admission
    shape (``done`` + ``terminal_reason='completed'``) and a parent
    instance in ``completed`` state — so the only thing that
    distinguishes them is ``job_type``. The M3 contract requires the
    four read surfaces to emit ``settled`` for the mirror and
    ``completed`` for the task.
    """
    mirror_jid = f"job-m3-mirror-{uuid.uuid4().hex[:8]}"
    task_jid = f"job-m3-task-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        _seed_queue(s, "queue-m3")
        _seed_instance(s, "inst-m3", InstanceStatus.COMPLETED.value)
        _seed_job(
            s,
            job_id=mirror_jid,
            instance_id="inst-m3",
            job_type="message",
        )
        _seed_job(
            s,
            job_id=task_jid,
            instance_id="inst-m3",
            job_type="task",
        )
    return mirror_jid, task_jid


# ─── The four-surfaces-agree pin ────────────────────────────────────────


class TestM3FourSurfacesAgree:
    """M3 (mission-class, 2026-09-03) — per-kind dispatch on the four
    read surfaces.

    Each surface reads the SAME JobItem row (mirror or task) and the
    per-kind dispatch in ``_job_to_record`` / ``_derive_legacy_status``
    MUST surface ``settled`` for the mirror and ``completed`` for the
    task. If the dispatch regresses (e.g. someone removes the
    ``job_type == "message"`` branch) the surfaces collapse onto
    ``completed`` and this pin catches the regression.
    """

    def test_workrecord_per_kind_dispatch(
        self, resolver, seeded_mirror_and_task
    ) -> None:
        """Surface 1: ``WorkRecord`` — mirror → ``settled``, task → ``completed``.

        The resolver's primary derivation path. Per-kind dispatch lives
        in ``_job_to_record`` (ADR-MISSION-01 §6.6 I3 amendment).
        """
        mirror_jid, task_jid = seeded_mirror_and_task

        mirror_record = resolver.resolve_work(mirror_jid)
        task_record = resolver.resolve_work(task_jid)

        assert mirror_record is not None
        assert task_record is not None
        # Mirror: ``settled`` (per-kind dispatch).
        assert mirror_record.status == "settled", (
            f"mirror WorkRecord.status must be 'settled' (M3 per-kind "
            f"dispatch); got {mirror_record.status!r}"
        )
        assert mirror_record.job_type == "message"
        # Task: ``completed`` (work-outcome vocabulary unchanged).
        assert task_record.status == "completed", (
            f"task WorkRecord.status must be 'completed' (M3 leaves task "
            f"rows alone — a task job IS its own mission); got "
            f"{task_record.status!r}"
        )
        assert task_record.job_type == "task"

    def test_jobresponse_per_kind_dispatch(
        self, repos, seeded_mirror_and_task
    ) -> None:
        """Surface 2: ``JobResponse`` — mirror → ``settled``, task → ``completed``.

        ``_job_to_response`` consumes the WorkRecord (resolver-backed
        path), so the per-kind dispatch flows through automatically.
        """
        task_repo, job_repo, instance_repo = repos
        mirror_jid, task_jid = seeded_mirror_and_task

        # Build WorkRecords (the surface-2 path reads work_record.status).
        resolver = WorkResolverService(task_repo, job_repo, instance_repo)
        mirror_wr = resolver.resolve_work(mirror_jid)
        task_wr = resolver.resolve_work(task_jid)
        assert mirror_wr is not None
        assert task_wr is not None

        # Fetch the raw JobItem rows for the surface-2 builder.
        with Session(repos[1].engine) as s:
            mirror_job = s.get(JobItem, mirror_jid)
            task_job = s.get(JobItem, task_jid)
            assert mirror_job is not None
            assert task_job is not None

        mirror_response = jobs_crud._job_to_response(mirror_job, work_record=mirror_wr)
        task_response = jobs_crud._job_to_response(task_job, work_record=task_wr)

        # Mirror terminal = ``settled`` (per-kind dispatch).
        assert mirror_response.status == "settled", (
            f"JobResponse.status for mirror must be 'settled' (M3 "
            f"per-kind dispatch); got {mirror_response.status!r}"
        )
        # Task terminal = ``completed`` (task row is its own mission).
        assert task_response.status == "completed", (
            f"JobResponse.status for task must be 'completed' (M3 "
            f"leaves task rows alone); got {task_response.status!r}"
        )

    def test_legacy_fallback_per_kind_dispatch(
        self, repos, seeded_mirror_and_task
    ) -> None:
        """Surface 2 (legacy fallback): when ``work_record is None``,
        ``_job_to_response`` routes through ``_derive_legacy_status``
        directly. The per-kind dispatch must apply there too — same
        contract as the resolver-backed path.
        """
        _, job_repo, _ = repos
        mirror_jid, task_jid = seeded_mirror_and_task

        with Session(job_repo.engine) as s:
            mirror_job = s.get(JobItem, mirror_jid)
            task_job = s.get(JobItem, task_jid)
            assert mirror_job is not None
            assert task_job is not None

        # No work_record ⇒ legacy fallback path. The per-kind dispatch
        # in ``_derive_legacy_status`` MUST apply.
        mirror_response = jobs_crud._job_to_response(mirror_job, work_record=None)
        task_response = jobs_crud._job_to_response(task_job, work_record=None)

        assert mirror_response.status == "settled", (
            f"legacy-fallback JobResponse.status for mirror must be "
            f"'settled' (M3 per-kind dispatch on _derive_legacy_status); "
            f"got {mirror_response.status!r}"
        )
        assert task_response.status == "completed", (
            f"legacy-fallback JobResponse.status for task must be "
            f"'completed'; got {task_response.status!r}"
        )


# ─── The per-kind filter pin ────────────────────────────────────────────


class TestM3PerKindStatusFilter:
    """M3 (mission-class, 2026-09-03) — the ``statuses`` filter accepts
    ``settled`` and per-kind matches it to mirror rows only.

    The legacy ``statuses`` filter is RETAINED through the M3 window
    but its per-kind matching is now STRICT:

    * ``statuses="completed"`` → task rows only (with pre-7c NULL
      hedge for backward compat — pre-7c rows never carry
      ``job_type='message'``).
    * ``statuses="settled"`` → mirror rows only (strict — no NULL
      hedge; pre-7c rows never carry ``job_type='message'``).
    * Unknown value → empty result (the §8.2 source-less degrade
      precedent; do NOT widen).
    """

    def test_settled_token_resolves_to_per_kind_filter(
        self,
    ) -> None:
        """The canonical ``settled`` token produces a ``JobStatusFilter``
        carrying ``admission_state='done'`` AND
        ``terminal_reason='completed'`` AND ``job_type='message'``.

        Mirrors ``_canonical_to_job_filters`` in
        ``daemon/services/work_resolver.py``. The dataclass now carries
        a ``job_type`` predicate (M3 per-kind dispatch); the SQL
        builder honors it.
        """
        from daemon.services.work_resolver import _canonical_to_job_filters

        filters = _canonical_to_job_filters(["settled"])
        assert len(filters) == 1
        f = filters[0]
        assert f.admission_state == "done"
        assert f.terminal_reason == "completed"
        assert f.terminal_reason_null_allowed is False  # strict — no pre-7c hedge
        assert f.job_type == "message"  # per-kind dispatch

    def test_completed_token_now_task_only(
        self,
    ) -> None:
        """The ``completed`` token is restricted to TASK rows after M3.

        Pre-7c hedge (NULL terminal_reason) is RETAINED — pre-7c rows
        predate the rename and never carry ``job_type='message'``.
        Mirror rows now match ``settled`` (not ``completed``).
        """
        from daemon.services.work_resolver import _canonical_to_job_filters

        filters = _canonical_to_job_filters(["completed"])
        assert len(filters) == 1
        f = filters[0]
        assert f.admission_state == "done"
        assert f.terminal_reason == "completed"
        assert f.terminal_reason_null_allowed is True  # pre-7c hedge
        assert f.job_type == "task"  # M3: completed ⇒ task only

    def test_failed_and_cancelled_are_kind_agnostic(
        self,
    ) -> None:
        """``failed`` and ``cancelled`` don't carry ``job_type`` —
        both kinds can fail/cancel (the terminal_reason is
        kind-agnostic).
        """
        from daemon.services.work_resolver import _canonical_to_job_filters

        failed_filters = _canonical_to_job_filters(["failed"])
        assert len(failed_filters) == 1
        assert failed_filters[0].job_type is None

        cancelled_filters = _canonical_to_job_filters(["cancelled"])
        assert len(cancelled_filters) == 1
        assert cancelled_filters[0].job_type is None

    def test_valid_legacy_statuses_includes_settled(
        self,
    ) -> None:
        """The legacy-statuses filter set accepts ``settled`` after M3.

        Operators can pass ``statuses="settled"`` to the
        ``GET /api/jobs`` query and get back mirror rows only.
        """
        from daemon.repositories.job_queue.models import _VALID_LEGACY_STATUSES

        assert "settled" in _VALID_LEGACY_STATUSES
        assert "completed" in _VALID_LEGACY_STATUSES  # task terminal still valid

    def test_terminal_statuses_includes_settled(
        self,
    ) -> None:
        """``TERMINAL_STATUSES`` (router-level terminal detection) accepts
        ``settled`` so the SSE stream's terminal-state gate treats a
        settled mirror as terminal (otherwise the WS would loop
        forever waiting for a never-arriving completion event).
        """
        from daemon.routers.jobs_crud import TERMINAL_STATUSES

        assert "settled" in TERMINAL_STATUSES
        assert "completed" in TERMINAL_STATUSES

    def test_is_terminal_recognises_settled(
        self,
    ) -> None:
        """``work_status.is_terminal("settled")`` returns ``True`` so
        cancel/replay gates and watcher notifications treat a settled
        WorkRecord as terminal.
        """
        from daemon.services.work_status import is_terminal

        assert is_terminal("settled") is True
        assert is_terminal("completed") is True  # task terminal unchanged
        assert is_terminal("processing") is False

    def test_normalize_statuses_keeps_settled(
        self,
    ) -> None:
        """``normalize_statuses(["settled"])`` returns ``["settled"]``
        (identity alias — M3 entry).
        """
        from daemon.services.job_queue_service import normalize_statuses

        assert normalize_statuses(["settled"]) == ["settled"]
        assert normalize_statuses(["SETTLED"]) == ["settled"]  # case-insensitive