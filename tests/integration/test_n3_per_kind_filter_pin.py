"""N3 (mission-class, 2026-09-03, ``feature/mission-class``) —
execution-level pin for the per-kind filter SQL.

Pins the M3 per-kind dispatch at the SQL boundary: when a caller passes
canonical status tokens through ``WorkResolverService.list_work``, the
backing JobItem SELECT must apply the per-kind predicate (job_type
discriminator) on top of the canonical-token → ``JobStatusFilter``
translation.

Test shape:

* Real DB writes (``file-backed SQLite at ``tmp_path`` with
  ``NullPool`` + ``PRAGMA journal_mode=WAL`` +
  ``PRAGMA busy_timeout=10000``) — the BLUEPRINT §3 recipe that
  sidesteps the QUARANTINE.md write-corruption pattern
  (StaticPool + WriteGuardSession is FORBIDDEN).
* Seed a Task row + a mirror JobItem row in the same project, both
  with ``admission_state='done'`` + ``terminal_reason='completed'``
  (the per-kind split happens at the ``job_type`` discriminator).
* Drive ``list_work(status=...)`` with each canonical token and the
  raw ``done`` admission_state token; assert the returned row-set
  matches the contract.

Failure mode (the pre-fix ``_canonical_to_job_filters`` before M3):
``completed`` returned BOTH kinds (the legacy ``admission_state='done'``
+ ``terminal_reason='completed'`` predicate with no ``job_type``); a
single ``settled`` token did not exist. The M3 split adds the
``job_type`` discriminator so ``completed`` → task only and
``settled`` → mirror only.

The existing ``tests/integration/test_m3_per_kind_dispatch_pin.py``
covers the unit-level mapping (asserting the
:class:`JobStatusFilter` dataclass that ``_canonical_to_job_filters``
returns). N3 exercises the SQL boundary end-to-end — the recipe is
"execution-level = real DB writes" per the N3 task description.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

# Register every model so ``SQLModel.metadata.create_all`` builds the
# full schema (matches the m3 pin's recipe).
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobQueue,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.task.repository import TaskRepository
from daemon.services.work_resolver import WorkResolverService


# ─── Fixtures (file-backed SQLite per BLUEPRINT recipe) ──────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite at ``tmp_path`` (NullPool + FK on + WAL).

    Blueprint §3 recipe — ``NullPool`` + file-backed SQLite at
    ``tmp_path`` + ``PRAGMA journal_mode=WAL`` +
    ``PRAGMA busy_timeout=10000`` + foreign-keys ON is the
    FORBIDDEN-PATTERN antidote for the QUARANTINE.md StaticPool +
    WriteGuardSession dependency_bus row (which trips write-
    corruption in dependency_bus + repository sessions).
    """
    db_path = tmp_path / "n3_pin.db"
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


def _seed_instance(s, instance_id: str, status: str) -> None:
    inst = Instance(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="/tmp/agents/developer",
        agent_name=instance_id,
        project_id="test-project",
        status=status,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        paused_at=None,
    )
    s.add(inst)
    s.commit()


def _seed_job(
    s,
    *,
    job_id: str,
    instance_id: str,
    job_type: str,
    terminal_reason: str = "completed",
) -> str:
    """Seed a JobItem with the per-kind params.

    Args:
        job_id: Stable UUID4 PK for the JobItem.
        instance_id: Parent instance PK.
        job_type: ``"task"`` (mission) or ``"message"`` (mirror).
        terminal_reason: ``"completed"`` for the per-kind split test;
            the M3 dispatch flips ``completed`` → ``settled`` ONLY
            when ``job_type == "message"``.
    """
    job = JobItem(
        job_id=job_id,
        agent_id="developer",
        agent_dir="/tmp/agents/developer",
        message="n3 pin",
        source="api",
        project_id="test-project",
        priority=5,
        admission_state=AdmissionState.DONE.value,
        terminal_reason=terminal_reason,
        instance_id=instance_id,
        queue_id="queue-n3",
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

    Returns ``(mirror_jid, task_jid)``. Both share the same
    admission shape (``done`` + ``terminal_reason='completed'``) and
    a parent instance in ``completed`` state — so the only thing
    that distinguishes them is ``job_type``. The M3 per-kind SQL
    split MUST apply: ``status="completed"`` returns task rows
    only; ``status="settled"`` returns mirror rows only;
    ``status="done"`` (raw admission_state) returns the union;
    multi-token ``completed,settled`` returns the union.
    """
    mirror_jid = f"job-n3-mirror-{uuid.uuid4().hex[:8]}"
    task_jid = f"job-n3-task-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        _seed_queue(s, "queue-n3")
        _seed_instance(s, "inst-n3", InstanceStatus.COMPLETED.value)
        _seed_job(
            s,
            job_id=mirror_jid,
            instance_id="inst-n3",
            job_type="message",
        )
        _seed_job(
            s,
            job_id=task_jid,
            instance_id="inst-n3",
            job_type="task",
        )
    return mirror_jid, task_jid


# ─── The execution-level per-kind filter pin ─────────────────────────────


class TestN3PerKindFilterSQL:
    """N3 (mission-class, 2026-09-03, ``feature/mission-class``) —
    execution-level pin: the per-kind SQL filter must apply at the
    DB boundary.

    Each test below seeds the same pair of rows (one mirror, one
    task) and drives ``list_work(status=...)`` end-to-end through
    the SQL boundary. Pre-M3 the ``completed`` token returned BOTH
    kinds (the legacy predicate lacked the ``job_type``
    discriminator); the ``settled`` token did not exist.
    """

    def test_single_token_completed_returns_task_rows_only(
        self, resolver, seeded_mirror_and_task
    ) -> None:
        """``status="completed"`` returns TASK rows only.

        Pre-M3, ``completed`` returned BOTH task and mirror rows
        (the legacy ``admission_state='done'`` + ``terminal_reason='
        completed'`` predicate with no ``job_type`` discriminator).
        The M3 split adds ``job_type='task'`` to the predicate so
        mirror rows now match ``settled`` instead.
        """
        mirror_jid, task_jid = seeded_mirror_and_task

        records = resolver.list_work(status="completed")
        record_ids = {r.work_id for r in records}

        assert task_jid in record_ids, (
            f"N3: 'completed' must include the task row {task_jid[:8]}..."
        )
        assert mirror_jid not in record_ids, (
            f"N3: 'completed' must NOT include the mirror row "
            f"{mirror_jid[:8]}... (per-kind split: mirror → settled); "
            f"got: {sorted(record_ids)}"
        )

    def test_single_token_settled_returns_mirror_rows_only(
        self, resolver, seeded_mirror_and_task
    ) -> None:
        """``status="settled"`` returns MIRROR rows only.

        The M3 rename adds ``settled`` as a kind-specific filter
        (strict; no pre-7c hedge). Mirror rows surface under
        ``settled``; task rows surface under ``completed``.
        """
        mirror_jid, task_jid = seeded_mirror_and_task

        records = resolver.list_work(status="settled")
        record_ids = {r.work_id for r in records}

        assert mirror_jid in record_ids, (
            f"N3: 'settled' must include the mirror row "
            f"{mirror_jid[:8]}... (per-kind split: mirror → settled); "
            f"got: {sorted(record_ids)}"
        )
        assert task_jid not in record_ids, (
            f"N3: 'settled' must NOT include the task row "
            f"{task_jid[:8]}... (per-kind split: task → completed); "
            f"got: {sorted(record_ids)}"
        )

    def test_raw_admission_state_done_returns_both_kinds(
        self, resolver, seeded_mirror_and_task
    ) -> None:
        """``status="done"`` (raw admission_state, no per-kind
        predicate) returns the UNION.

        Backward-compat fallback: callers that pass raw
        ``admission_state`` strings (e.g. ``"done"``) get the
        pre-F3 behaviour — the ``JobStatusFilter`` carries no
        ``terminal_reason`` / ``job_type`` discriminator, so the
        match is on ``admission_state='done'`` only and returns
        BOTH task and mirror rows that have settled.
        """
        mirror_jid, task_jid = seeded_mirror_and_task

        records = resolver.list_work(status="done")
        record_ids = {r.work_id for r in records}

        assert mirror_jid in record_ids, (
            f"N3: raw 'done' (no per-kind predicate) must include "
            f"mirror rows; got: {sorted(record_ids)}"
        )
        assert task_jid in record_ids, (
            f"N3: raw 'done' (no per-kind predicate) must include "
            f"task rows; got: {sorted(record_ids)}"
        )

    def test_multi_token_completed_settled_returns_union_non_empty(
        self, resolver, seeded_mirror_and_task
    ) -> None:
        """Multi-token ``status="completed,settled"`` returns the
        UNION — both task and mirror rows — and the result is
        non-empty.

        Operators can pass a comma-separated list of canonical
        tokens to ``list_work(status=...)``; the
        ``_canonical_to_job_filters`` translates each token into
        a per-kind ``JobStatusFilter`` and OR-combines the
        filters at the SQL boundary.
        """
        mirror_jid, task_jid = seeded_mirror_and_task

        records = resolver.list_work(status="completed,settled")
        record_ids = {r.work_id for r in records}

        assert len(records) >= 2, (
            f"N3: multi-token 'completed,settled' must return the "
            f"union (non-empty); got {len(records)} records: "
            f"{sorted(record_ids)}"
        )
        assert mirror_jid in record_ids, (
            f"N3: multi-token union must include the mirror row; "
            f"got: {sorted(record_ids)}"
        )
        assert task_jid in record_ids, (
            f"N3: multi-token union must include the task row; "
            f"got: {sorted(record_ids)}"
        )