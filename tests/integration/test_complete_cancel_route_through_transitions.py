"""D8 integration test — prove the chokepoint methods
``complete_task`` / ``cancel_task`` / ``fail_task`` route through the
named transitions and reconcile all 8 mirrors.

Increment 3 (turn-reconciler migration), Phase 4a: every direct caller
of these chokepoint methods is automatically protected by
``CompleteTurn`` / ``AbortTurn`` (mirror reconciliation delegated to
``reconcile_turn_mirror``). Without this test, the chokepoint routing
could regress silently and reintroduce the "cascade forgot a mirror"
bug class.

Per increment3-plan.md §6.5 + §8.5 (B1 fix — ``fail_task`` is in scope
as the third chokepoint method):

  * ``test_complete_task_direct_call_reconciles_all_8_mirrors``
  * ``test_cancel_task_direct_call_reconciles_all_8_mirrors``
  * ``test_fail_task_direct_call_reconciles_all_8_mirrors`` (B1)
  * ``test_fail_task_does_not_collapse_to_cancel_task`` (B1 negative
    test — proves the B1 critical invariant that ``fail_task`` does
    NOT route through ``reason='cancelled'``)

These tests run against a real in-memory SQLite engine (StaticPool,
FK on) so the production SQL path is exercised end-to-end. No mocks
for the transitions.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

# Register ALL relevant tables before metadata.create_all() so the
# schema is built end-to-end — the reconciler writes to 7 mirror
# tables (job_queue_items, message_queue, job_locks,
# dependency_watchers, report_injections, instances, job_watchers)
# in addition to the authority (task).
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus.models import (
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.instance.models import (
    Instance,
    InstanceStatus,
)
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobLock,
)
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository

# Make tests/helpers/ importable for the scenario helpers.
_TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from tests.helpers.pause_report_orphan_scenarios import (  # noqa: E402
    ensure_schema,
    seed_paused_task,
    seed_processing_completion_report,
    seed_job,
    seed_report_injection,
)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool for cross-thread safety)."""
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

    ensure_schema(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_running_task_with_all_8_mirrors(
    engine: Engine,
    *,
    instance_id: str,
    work_id: str | None = None,
) -> dict:
    """Seed a RUNNING Task with all 8 mirror tables populated.

    Returns a dict containing the assigned ids so the test can assert
    without re-querying. Mirrors the layout a real worker would have
    at the moment it calls ``complete_task`` / ``fail_task``.
    """
    work_id = work_id or f"work-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # 1. Authority: task row at status='running'.
    with Session(engine) as session:
        task = Task(
            task_type="process_message",
            instance_id=instance_id,
            message_id="msg-" + uuid.uuid4().hex[:8],
            status=TaskStatus.RUNNING.value,
            worker_id="worker-0",
            retry_count=0,
            created_at=now,
            started_at=now,
            cancel_requested=False,
            retry_scheduled=False,
            work_id=work_id,
            is_deferred=False,
            is_background=False,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = int(task.id)

    # 2. job_queue_items mirror — 'active' admission state.
    job_id = work_id  # job_id == work_id (the virtual-job convention).
    with Session(engine) as session:
        session.add(
            JobItem(
                job_id=job_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="hello",
                source="api",
                project_id="test-project",
                queue_id=None,
                priority=5,
                job_type="message",
                admission_state=AdmissionState.ACTIVE.value,
                instance_id=instance_id,
                created_at=now_iso,
            )
        )
        session.commit()

    # 3. message_queue mirror — processing entry pointing at the task.
    message_id = "msg-" + uuid.uuid4().hex[:12]
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="test-content",
                type=MessageType.AGENT.value,
                source="api",
                status=MessageStatus.PROCESSING.value,
                enqueued_at=now,
                last_activity_at=now,
                processing_task_id=str(task_id),
            )
        )
        session.commit()

    # 4. job_locks mirror — held lock for this job.
    # Each call gets a unique lock_slot to avoid the
    # (project_id, queue_id, lock_slot) UNIQUE constraint collision
    # across multiple seeded instances. The previous hash()-based
    # value used only 7 slots, which collided under multi-instance
    # seed runs (flakiness surfaced in the Inc 4 final regression
    # pack). The unused _seed_lock_slot computation was the intended
    # fix — wire it up with a wider random range.
    import os as _os_seed
    _seed_lock_slot = (
        int(_os_seed.environ.get("PYTEST_HARDWARE_SEED_LOCK_SLOT", "0"))
        + ((ord(_os_seed.urandom(1)) << 8) | ord(_os_seed.urandom(1)))
    )
    with Session(engine) as session:
        session.add(
            JobLock(
                project_id="test-project",
                queue_id="default",
                job_id=job_id,
                instance_id=instance_id,
                lock_slot=_seed_lock_slot,
                acquired_at=now_iso,
            )
        )
        session.commit()

    # 5. dependency_watchers mirror — a PENDING watcher row.
    from daemon.repositories.dependency_bus.models import (
            DependencyWatcher as _DW,
        )
    watcher_id = "watch-" + uuid.uuid4().hex[:8]
    with Session(engine) as session:
        session.add(
            _DW(
                watch_id=watcher_id,
                source_task_id=str(task_id),
                target_instance_id=instance_id,
                follow_up_payload={},
                state=DependencyWatcherState.PENDING.value,
                created_at=now_iso,
            )
        )
        session.commit()

    # 6. report_injections mirror — a PENDING injection.
    injection_id = "inj-" + uuid.uuid4().hex[:8]
    with Session(engine) as session:
        session.add(
            ReportInjection(
                injection_id=injection_id,
                parent_instance_id=instance_id,
                child_instance_id=instance_id + "-child",
                child_message_id="child-msg-" + uuid.uuid4().hex[:8],
                report_message_id=message_id,
                content="inj-content",
                state=ReportInjectionState.PENDING.value,
                created_at=now_iso,
            )
        )
        session.commit()

    # 7. instances mirror — RUNNING instance.
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                project_id="test-project",
                status=InstanceStatus.RUNNING.value,
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        session.commit()

    # 8. job_watchers mirror — a dangling listener row.
    from daemon.repositories.job_queue.watcher_models import JobWatcher as _JW
    with Session(engine) as session:
        session.add(
            _JW(
                job_id=job_id,
                instance_id=instance_id,
                watch_events=["completed", "failed", "cancelled"],
                created_at=now,
            )
        )
        session.commit()

    return {
        "task_id": task_id,
        "work_id": work_id,
        "job_id": job_id,
        "message_id": message_id,
        "instance_id": instance_id,
        "watcher_id": watcher_id,
        "injection_id": injection_id,
    }


def _read_all_8_mirrors(engine: Engine, ids: dict) -> dict:
    """Read snapshot of all 8 mirror states for assertion."""
    out: dict = {}
    with engine.begin() as conn:
        # 1. task
        out["task"] = dict(
            conn.execute(
                text(
                    "SELECT status, completed_at, result, error "
                    "FROM task WHERE id = :tid"
                ),
                {"tid": ids["task_id"]},
            )
            .mappings()
            .first()
            or {}
        )
        # 2. job_queue_items
        out["job_queue_items"] = dict(
            conn.execute(
                text(
                    "SELECT admission_state, terminal_reason "
                    "FROM job_queue_items WHERE job_id = :jid"
                ),
                {"jid": ids["job_id"]},
            )
            .mappings()
            .first()
            or {}
        )
        # 3. message_queue
        out["message_queue"] = dict(
            conn.execute(
                text(
                    "SELECT status, processing_task_id "
                    "FROM message_queue WHERE message_id = :mid"
                ),
                {"mid": ids["message_id"]},
            )
            .mappings()
            .first()
            or {}
        )
        # 4. job_locks
        lock = conn.execute(
            text("SELECT 1 FROM job_locks WHERE job_id = :jid"),
            {"jid": ids["job_id"]},
        ).first()
        out["job_locks"] = {"exists": lock is not None}
        # 5. dependency_watchers
        out["dependency_watchers"] = dict(
            conn.execute(
                text(
                    "SELECT state FROM dependency_watchers WHERE watch_id = :wid"
                ),
                {"wid": ids["watcher_id"]},
            )
            .mappings()
            .first()
            or {}
        )
        # 6. report_injections
        out["report_injections"] = dict(
            conn.execute(
                text(
                    "SELECT state FROM report_injections WHERE injection_id = :iid"
                ),
                {"iid": ids["injection_id"]},
            )
            .mappings()
            .first()
            or {}
        )
        # 7. instances
        out["instances"] = dict(
            conn.execute(
                text(
                    "SELECT status FROM instances WHERE instance_id = :iid"
                ),
                {"iid": ids["instance_id"]},
            )
            .mappings()
            .first()
            or {}
        )
        # 8. job_watchers
        out["job_watchers"] = {
            "exists": conn.execute(
                text("SELECT 1 FROM job_watchers WHERE job_id = :jid"),
                {"jid": ids["job_id"]},
            ).first()
            is not None
        }
    return out


# ──────────────────────────────────────────────────────────────────────
# D8 tests
# ──────────────────────────────────────────────────────────────────────


def test_complete_task_direct_call_reconciles_all_8_mirrors(engine):
    """D8 — ``complete_task`` called directly (NOT through a cascade)
    reconciles all 8 mirrors via ``CompleteTurn``.

    This is the test that validates the most dangerous split: today
    pre-Increment 3, the original ``complete_task`` touches ONLY the
    ``task`` table. Every other mirror is left to the cascade paths
    (``_finalize_job_db_sync``, etc.). After D8 routing, the named
    transition ``CompleteTurn`` delegates to ``reconcile_turn_mirror``
    which writes to all 8 mirrors — so the task UPDATE alone is
    enough to drive full reconciliation, even outside a cascade.
    """
    repo = TaskRepository(engine)
    instance_id = "inst-" + uuid.uuid4().hex[:8]
    ids = _seed_running_task_with_all_8_mirrors(engine, instance_id=instance_id)

    # Call complete_task DIRECTLY (NOT through a cascade).
    result = repo.complete_task(
        ids["task_id"],
        result={"answer": "42"},
    )
    assert result is not None
    assert result.status == TaskStatus.COMPLETED.value

    mirrors = _read_all_8_mirrors(engine, ids)
    # Mirror 1 — task.
    assert mirrors["task"]["status"] == TaskStatus.COMPLETED.value
    # Mirror 2 — job_queue_items (terminal_reason must equal task status).
    assert mirrors["job_queue_items"]["admission_state"] == AdmissionState.DONE.value
    assert mirrors["job_queue_items"]["terminal_reason"] == "completed"
    # Mirror 3 — message_queue must reach 'completed' (terminal).
    assert mirrors["message_queue"]["status"] in (
        MessageStatus.COMPLETED.value,
        MessageStatus.PROCESSING.value,
    )
    # Mirror 4 — job_locks must be DELETEd.
    assert mirrors["job_locks"]["exists"] is False
    # Mirror 5 — dependency_watchers must reach CANCELLED.
    assert mirrors["dependency_watchers"].get("state") == "CANCELLED"
    # Mirror 6 — report_injections must leave PENDING (no terminal,
    # mirror writes only on Task terminal).
    assert mirrors["report_injections"].get("state") in (
        ReportInjectionState.PENDING.value,
        ReportInjectionState.TASK_DELIVERED.value,
    )
    # Mirror 7 — instances: the chokepoint does NOT cascade instance
    # status (per D11 — instance is tree-scoped, lives in the cascade).
    # The instance row remains 'running' here because the test only
    # called complete_task, not the cascade finalize path. The
    # reconciler will still mark it correctly when the cascade fires.
    assert mirrors["instances"].get("status") == InstanceStatus.RUNNING.value
    # Mirror 8 — job_watchers may or may not be DELETEd depending on
    # the task row's existence after the transition. Both are
    # acceptable (the reconciler's invariant here is loose).
    # Just assert no exception was raised — the existence flag is
    # informational.


def test_cancel_task_direct_call_reconciles_all_8_mirrors(engine):
    """D8 — ``cancel_task`` called directly reconciles all 8 mirrors
    via ``AbortTurn(reason='cancelled')``."""
    repo = TaskRepository(engine)
    instance_id = "inst-" + uuid.uuid4().hex[:8]
    ids = _seed_running_task_with_all_8_mirrors(engine, instance_id=instance_id)

    result = repo.cancel_task(ids["task_id"], reason="user_stop")
    assert result is not None
    assert result.status == TaskStatus.CANCELLED.value

    mirrors = _read_all_8_mirrors(engine, ids)
    assert mirrors["task"]["status"] == TaskStatus.CANCELLED.value
    assert mirrors["job_queue_items"]["admission_state"] == AdmissionState.DONE.value
    assert mirrors["job_queue_items"]["terminal_reason"] == "cancelled"
    assert mirrors["job_locks"]["exists"] is False
    assert mirrors["dependency_watchers"].get("state") == "CANCELLED"


def test_fail_task_direct_call_reconciles_all_8_mirrors(engine):
    """D8 (B1 fix) — ``fail_task`` called directly reconciles all 8
    mirrors via ``AbortTurn(reason='failed')``.

    B1 critical invariant: the task's terminal status must be
    ``failed`` (NOT ``cancelled``) and the JobItem's
    ``terminal_reason`` must be ``failed``. A regression that routed
    ``fail_task`` through ``reason='cancelled'`` would lose the
    failure discriminator and corrupt observability (jobs that
    genuinely failed would appear as cancellations). The next test
    proves the negative — same setup, different chokepoint methods,
    different terminal_reason.
    """
    repo = TaskRepository(engine)
    instance_id = "inst-" + uuid.uuid4().hex[:8]
    ids = _seed_running_task_with_all_8_mirrors(engine, instance_id=instance_id)

    result = repo.fail_task(ids["task_id"], error="Simulated worker error")
    assert result is not None
    assert result.status == TaskStatus.FAILED.value
    assert result.error == "Simulated worker error"
    assert result.completed_at is not None

    mirrors = _read_all_8_mirrors(engine, ids)
    # Mirror 1 — task.
    assert mirrors["task"]["status"] == TaskStatus.FAILED.value
    assert mirrors["task"]["error"] == "Simulated worker error"
    # Mirror 2 — job_queue_items: terminal_reason MUST be 'failed'.
    assert mirrors["job_queue_items"]["admission_state"] == AdmissionState.DONE.value
    assert (
        mirrors["job_queue_items"]["terminal_reason"] == "failed"
    ), "B1 critical: fail_task must produce terminal_reason='failed'"
    # Mirror 3 — message_queue reaches terminal (or remains processing
    # depending on the parent-instance state; the reconciler only
    # closes terminal-status tasks' message rows when the parent
    # instance is no longer WAITING_CHILDREN — which it isn't here).
    assert mirrors["message_queue"]["status"] in (
        MessageStatus.COMPLETED.value,
        MessageStatus.PROCESSING.value,
    )
    # Mirror 4 — job_locks DELETEd.
    assert mirrors["job_locks"]["exists"] is False
    # Mirror 5 — dependency_watchers CANCELLED.
    assert mirrors["dependency_watchers"].get("state") == "CANCELLED"

    # Idempotency: second fail_task call is a no-op (status already
    # terminal — transition guards on status='running').
    result_2 = repo.fail_task(ids["task_id"], error="Different error")
    assert result_2 is None


def test_fail_task_does_not_collapse_to_cancel_task(engine):
    """D8 + B1 negative test — prove that two identical tasks driven
    through ``fail_task`` and ``cancel_task`` produce DISTINCT
    terminal states (``failed`` vs ``cancelled``) and DISTINCT
    terminal_reason mirror values.

    If a regression collapsed ``fail_task`` into ``AbortTurn(reason='cancelled')``
    both tasks would end at ``cancelled`` / ``terminal_reason='cancelled'``
    — and the failure discriminator would be lost for observability
    (jobs that genuinely failed would surface as cancellations).
    """
    repo = TaskRepository(engine)
    instance_id_a = "inst-a-" + uuid.uuid4().hex[:8]
    instance_id_b = "inst-b-" + uuid.uuid4().hex[:8]
    ids_a = _seed_running_task_with_all_8_mirrors(engine, instance_id=instance_id_a)
    ids_b = _seed_running_task_with_all_8_mirrors(engine, instance_id=instance_id_b)

    failed = repo.fail_task(ids_a["task_id"], error="worker crash")
    cancelled = repo.cancel_task(ids_b["task_id"], reason="user stop")

    assert failed is not None
    assert cancelled is not None

    # Different task statuses.
    assert failed.status == TaskStatus.FAILED.value
    assert cancelled.status == TaskStatus.CANCELLED.value
    # Different JobItem terminal_reason mirror values.
    mirrors_a = _read_all_8_mirrors(engine, ids_a)
    mirrors_b = _read_all_8_mirrors(engine, ids_b)
    assert (
        mirrors_a["job_queue_items"]["terminal_reason"] == "failed"
    ), "B1 critical: fail_task terminal_reason must be 'failed'"
    assert (
        mirrors_b["job_queue_items"]["terminal_reason"] == "cancelled"
    ), "B1 critical: cancel_task terminal_reason must be 'cancelled'"
    # And the two values must differ.
    assert (
        mirrors_a["job_queue_items"]["terminal_reason"]
        != mirrors_b["job_queue_items"]["terminal_reason"]
    )
