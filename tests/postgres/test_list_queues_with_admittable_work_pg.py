"""PostgreSQL coverage for ``JobQueueRepository.list_queues_with_admittable_work``.

This is the PG-side counterpart to the SQLite coverage in
``tests/job_queue/test_job_processor_admission_starvation.py``
(``TestWorkDrivenScanShape``) and
``tests/job_queue/test_queue_repository.py``
(``TestListQueuesWithAdmittableWork``). It exists because the
SQLite-only test path cannot catch regressions that only surface
under the PostgreSQL ``GROUP BY`` / ``ORDER BY`` translation of
``func.min(JobItem.created_at).asc()`` — for example, dialect
differences in how ``MIN()`` aggregates are evaluated against
textual ISO-8601 ``created_at`` columns.

The test exercises the four shapes the SQLite suite also covers:

  1. ``queued`` + non-deleted (``deleted_at IS NULL``) → returned
  2. ``active``  + non-deleted → returned
  3. ``dead``    + non-deleted → excluded (admission filter)
  4. ``queued``  + ``deleted_at IS NOT NULL`` → excluded (soft-delete filter)

Why this matters
----------------
The work-driven scan (``JobProcessor._process_next_job``) calls
``list_queues_with_admittable_work`` once per poll cycle. A regression
that flips the ``GROUP BY`` into ``DISTINCT`` (or vice-versa) would
not break the SQLite suite but could break PG — the two dialects
translate the same SQLModel statement differently when the SELECT
also exposes ``func.min(...)``. Pinning the contract here makes a
regression visible in CI's PG run.

Why no Phase 2 constraint triggers
----------------------------------
``test_jq_proxy_phase2_constraints.py`` installs
``trg_job_queue_items_active_lock_guard`` to enforce
``admission_state='active' ⇔ job_locks row exists`` at COMMIT time.
This module does NOT install those triggers — its focus is the
SELECT-side filter (``WHERE admission_state IN (...)``,
``WHERE deleted_at IS NULL``), not the INSERT/UPDATE invariant. We
insert ``active`` rows WITHOUT a paired ``job_locks`` row, which
would violate the Phase 2 trigger; omitting the install keeps the
test scope tight.

Run with::

    .venv/bin/python -m pytest tests/postgres/test_list_queues_with_admittable_work_pg.py \\
        -v -m postgres --tb=short --override-ini="addopts="

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips
the entire module cleanly when PostgreSQL is not reachable.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text


# Importing ``JobItem``/``JobQueue`` so ``SQLModel.metadata.create_all``
# (run by the session-scoped ``pg_engine`` fixture) registers the
# ``job_queue_items`` and ``job_queues`` tables.
from daemon.repositories.job_queue.models import (  # noqa: F401
    AdmissionState,
    JobItem,
    JobQueue,
    QueueType,
)
from daemon.repositories.job_queue.queue_repository import JobQueueRepository


# Auto-apply the postgres marker so ``pytest -m postgres`` selects
# these tests and the default ``addopts`` skips them unless overridden.
pytestmark = pytest.mark.postgres


# Hard-coded PG identifiers — verbatim from ``JobQueue.__tablename__``
# and ``JobItem.__tablename__``. Decoupling the SQL from the model
# imports keeps this file readable as a raw-SQL contract test.
TABLE_JOB_QUEUES = "job_queues"
TABLE_JOB_QUEUE_ITEMS = "job_queue_items"

# Literal admission-state values from ``AdmissionState``. Pinned here
# so a typo in the enum cannot silently pass this test.
STATE_QUEUED = "queued"
STATE_ACTIVE = "active"
STATE_DONE = "done"
STATE_DEAD = "dead"


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _insert_project(conn, *, project_id: str) -> None:
    """Insert a minimal ``projects`` row to satisfy the queue FK.

    The PG ``job_queues.project_id`` column does NOT have an explicit
    FK in this schema (``daemon/repositories/job_queue/models.py:199``
    declares ``project_id: str`` without ``foreign_key=``), so this
    row exists purely to keep the test self-documenting.
    """
    conn.execute(
        text(
            """
            INSERT INTO projects (
                project_id, name, project_type, status, job_queue_paused,
                created_at, updated_at
            ) VALUES (
                :project_id, :name, :project_type, :status, :job_queue_paused,
                :created_at, :updated_at
            )
            """
        ),
        {
            "project_id": project_id,
            "name": f"pg-admittable-{project_id[:8]}",
            "project_type": "general",
            "status": "active",
            "job_queue_paused": False,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        },
    )


def _insert_queue(conn, *, project_id: str) -> str:
    """Insert a fresh ``job_queues`` row and return its ``queue_id``."""
    queue_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute(
        text(
            f"""
            INSERT INTO {TABLE_JOB_QUEUES} (
                queue_id, project_id, queue_name, queue_name_lower,
                queue_type, concurrency_limit, is_system, is_paused,
                description, created_at, updated_at
            ) VALUES (
                :queue_id, :project_id, :queue_name, :queue_name_lower,
                :queue_type, 1, FALSE, FALSE, NULL, :created_at, :updated_at
            )
            """
        ),
        {
            "queue_id": queue_id,
            "project_id": project_id,
            "queue_name": "admittable_pg_q",
            "queue_name_lower": "admittable_pg_q",
            "queue_type": QueueType.FIFO.value,
            "created_at": now,
            "updated_at": now,
        },
    )
    return queue_id


def _insert_job_item(
    conn,
    *,
    queue_id: str,
    project_id: str,
    admission_state: str,
    deleted_at: str | None = None,
    created_at: str | None = None,
) -> str:
    """Insert a single ``job_queue_items`` row and return its ``job_id``."""
    job_id = str(uuid.uuid4())
    conn.execute(
        text(
            f"""
            INSERT INTO {TABLE_JOB_QUEUE_ITEMS} (
                job_id, agent_id, agent_dir, message, source, project_id,
                queue_id, priority, admission_state, created_at,
                instance_id, job_type, retry_count, metadata, deleted_at
            ) VALUES (
                :job_id, 'pg-test-agent', 'agents/developer', 'msg',
                'api', :project_id, :queue_id, 5, :admission_state,
                :created_at, NULL, 'task', 0, '{{}}', :deleted_at
            )
            """
        ),
        {
            "job_id": job_id,
            "project_id": project_id,
            "queue_id": queue_id,
            "admission_state": admission_state,
            "created_at": created_at or _now_iso(),
            "deleted_at": deleted_at,
        },
    )
    return job_id


def test_list_queues_with_admittable_work_includes_queued_and_active(
    pg_engine, pg_repository_factory
):
    """``queued`` AND ``active`` non-deleted JobItems surface their queue.

    Shape: 1 queue with one ``queued`` JobItem and one ``active``
    JobItem (no ``deleted_at``). The scan MUST return this queue.

    This is the positive counterpart to the negative tests below —
    it proves the IN-clause accepts BOTH ``queued`` and ``active``
    (the canonical "in-flight" admission pair routed through
    ``ACTIVE_ADMISSION_STATES``).
    """
    queue_repo: JobQueueRepository = pg_repository_factory(JobQueueRepository)

    with pg_engine.begin() as conn:
        project_id = str(uuid.uuid4())
        _insert_project(conn, project_id=project_id)
        queue_id = _insert_queue(conn, project_id=project_id)
        _insert_job_item(
            conn,
            queue_id=queue_id,
            project_id=project_id,
            admission_state=STATE_QUEUED,
        )
        _insert_job_item(
            conn,
            queue_id=queue_id,
            project_id=project_id,
            admission_state=STATE_ACTIVE,
        )
        # Save the queue_id for the post-commit assertion below.
        saved_queue_id = queue_id

    result = queue_repo.list_queues_with_admittable_work()
    returned_ids = {q.queue_id for q in result}
    assert saved_queue_id in returned_ids, (
        "queue with queued+active items MUST be in the scan result; "
        f"got {returned_ids}"
    )


def test_list_queues_with_admittable_work_excludes_dead_items(
    pg_engine, pg_repository_factory
):
    """A queue whose only JobItems are ``dead`` MUST be excluded.

    Shape: 1 queue with a single ``dead`` JobItem (non-deleted). The
    scan MUST NOT return this queue — ``dead`` is outside
    ``ACTIVE_ADMISSION_STATES``. This catches a regression where the
    filter is dropped from the IN-clause.
    """
    queue_repo: JobQueueRepository = pg_repository_factory(JobQueueRepository)

    with pg_engine.begin() as conn:
        project_id = str(uuid.uuid4())
        _insert_project(conn, project_id=project_id)
        queue_id = _insert_queue(conn, project_id=project_id)
        _insert_job_item(
            conn,
            queue_id=queue_id,
            project_id=project_id,
            admission_state=STATE_DEAD,
        )
        saved_queue_id = queue_id

    result = queue_repo.list_queues_with_admittable_work()
    returned_ids = {q.queue_id for q in result}
    assert saved_queue_id not in returned_ids, (
        "queue whose only item is 'dead' MUST be excluded from the scan; "
        f"got {returned_ids}"
    )


def test_list_queues_with_admittable_work_excludes_soft_deleted_items(
    pg_engine, pg_repository_factory
):
    """A queue whose only ``queued`` JobItem is soft-deleted MUST be excluded.

    Shape: 1 queue with one ``queued`` JobItem whose ``deleted_at``
    is set. The ``WHERE deleted_at IS NULL`` clause MUST filter this
    row out, so the queue has zero admittable items and is not
    returned.

    This is the soft-delete invariant — without it, a row in
    ``done`` then soft-deleted state (e.g. cancelled) would still
    surface as "admittable" and the work-driven scan would never
    quiesce for that queue.
    """
    queue_repo: JobQueueRepository = pg_repository_factory(JobQueueRepository)

    with pg_engine.begin() as conn:
        project_id = str(uuid.uuid4())
        _insert_project(conn, project_id=project_id)
        queue_id = _insert_queue(conn, project_id=project_id)
        _insert_job_item(
            conn,
            queue_id=queue_id,
            project_id=project_id,
            admission_state=STATE_QUEUED,
            deleted_at=_now_iso(),  # soft-delete the row
        )
        saved_queue_id = queue_id

    result = queue_repo.list_queues_with_admittable_work()
    returned_ids = {q.queue_id for q in result}
    assert saved_queue_id not in returned_ids, (
        "queue whose only queued item is soft-deleted MUST be excluded "
        f"from the scan; got {returned_ids}"
    )


def test_list_queues_with_admittable_work_4_shapes_combined(
    pg_engine, pg_repository_factory
):
    """All 4 shapes in a single scenario.

    Sets up four queues on four distinct projects, one JobItem each,
    exercising every shape:

      - Q1: ``queued``  + alive       → MUST be in result
      - Q2: ``active``  + alive       → MUST be in result
      - Q3: ``dead``    + alive       → MUST be excluded
      - Q4: ``queued``  + soft-deleted → MUST be excluded

    A single combined scenario guards against order-of-evaluation
    surprises in the SQLAlchemy WHERE chain (e.g. a regression that
    accidentally ANDs the admission filter with the deleted_at
    filter such that one side shadows the other in isolation but
    fails in combination).
    """
    queue_repo: JobQueueRepository = pg_repository_factory(JobQueueRepository)

    with pg_engine.begin() as conn:
        # Q1: queued + alive
        p1 = str(uuid.uuid4())
        _insert_project(conn, project_id=p1)
        q1 = _insert_queue(conn, project_id=p1)
        _insert_job_item(
            conn,
            queue_id=q1,
            project_id=p1,
            admission_state=STATE_QUEUED,
        )

        # Q2: active + alive
        p2 = str(uuid.uuid4())
        _insert_project(conn, project_id=p2)
        q2 = _insert_queue(conn, project_id=p2)
        _insert_job_item(
            conn,
            queue_id=q2,
            project_id=p2,
            admission_state=STATE_ACTIVE,
        )

        # Q3: dead + alive
        p3 = str(uuid.uuid4())
        _insert_project(conn, project_id=p3)
        q3 = _insert_queue(conn, project_id=p3)
        _insert_job_item(
            conn,
            queue_id=q3,
            project_id=p3,
            admission_state=STATE_DEAD,
        )

        # Q4: queued + soft-deleted
        p4 = str(uuid.uuid4())
        _insert_project(conn, project_id=p4)
        q4 = _insert_queue(conn, project_id=p4)
        _insert_job_item(
            conn,
            queue_id=q4,
            project_id=p4,
            admission_state=STATE_QUEUED,
            deleted_at=_now_iso(),
        )

        # Q5: done + alive — also outside the active set; pins
        # ``STATE_DONE`` exclusion symmetrically with ``STATE_DEAD``.
        p5 = str(uuid.uuid4())
        _insert_project(conn, project_id=p5)
        q5 = _insert_queue(conn, project_id=p5)
        _insert_job_item(
            conn,
            queue_id=q5,
            project_id=p5,
            admission_state=STATE_DONE,
        )

    result = queue_repo.list_queues_with_admittable_work()
    returned_ids = {q.queue_id for q in result}

    assert q1 in returned_ids, (
        f"queued+alive queue {q1} MUST be in result; got {returned_ids}"
    )
    assert q2 in returned_ids, (
        f"active+alive queue {q2} MUST be in result; got {returned_ids}"
    )
    assert q3 not in returned_ids, (
        f"dead+alive queue {q3} MUST be excluded; got {returned_ids}"
    )
    assert q4 not in returned_ids, (
        f"queued+soft-deleted queue {q4} MUST be excluded; got {returned_ids}"
    )
    assert q5 not in returned_ids, (
        f"done+alive queue {q5} MUST be excluded (DONE is not in "
        f"ACTIVE_ADMISSION_STATES); got {returned_ids}"
    )


def test_list_queues_with_admittable_work_default_routes_through_active_constant(
    pg_engine, pg_repository_factory
):
    """Default (``admission_states=None``) is equivalent to
    ``ACTIVE_ADMISSION_STATES`` explicitly under PG.

    Same single-source-of-truth contract as the SQLite test
    ``tests/job_queue/test_queue_repository.py::
    TestListQueuesWithAdmittableWork::
    test_default_admission_states_routes_through_active_constant``,
    but exercises the PG-side SQL compilation path. If the default
    ever diverges from the constant, the SQLite suite AND this PG
    test both catch it.
    """
    from daemon.repositories.job_queue.models import ACTIVE_ADMISSION_STATES

    queue_repo: JobQueueRepository = pg_repository_factory(JobQueueRepository)

    with pg_engine.begin() as conn:
        project_id = str(uuid.uuid4())
        _insert_project(conn, project_id=project_id)
        queue_id = _insert_queue(conn, project_id=project_id)
        # Seed: one queued + one dead. The default must INCLUDE the
        # queue (queued), the explicit-ACTIVE constant must INCLUDE
        # the queue (queued). Pass DEAD explicitly and the queue
        # must still appear (dead JobItem surfaces the same queue).
        _insert_job_item(
            conn,
            queue_id=queue_id,
            project_id=project_id,
            admission_state=STATE_QUEUED,
        )
        _insert_job_item(
            conn,
            queue_id=queue_id,
            project_id=project_id,
            admission_state=STATE_DEAD,
        )
        saved_queue_id = queue_id

    default_ids = {
        q.queue_id for q in queue_repo.list_queues_with_admittable_work()
    }
    explicit_ids = {
        q.queue_id
        for q in queue_repo.list_queues_with_admittable_work(
            admission_states=list(ACTIVE_ADMISSION_STATES),
        )
    }
    active_plus_dead_ids = {
        q.queue_id
        for q in queue_repo.list_queues_with_admittable_work(
            admission_states=list(ACTIVE_ADMISSION_STATES) + [STATE_DEAD],
        )
    }

    assert saved_queue_id in default_ids, (
        f"queued-alive queue {saved_queue_id} must surface under the "
        f"default scan; got {default_ids}"
    )
    assert default_ids == explicit_ids, (
        f"default (None) must equal ACTIVE_ADMISSION_STATES explicit; "
        f"got default={default_ids}, explicit={explicit_ids}"
    )
    # Sanity: extending with DEAD does NOT drop the queue (the dead
    # JobItem also surfaces it).
    assert saved_queue_id in active_plus_dead_ids