"""Engine-agnostic scenario builders for the Phase 2 Bug B tests.

These builders accept any SQLAlchemy ``Engine`` (SQLite in-memory
or PostgreSQL via ``pg_engine``) and seed the full set of tables
needed to exercise the cascade reconciliation and completion-guard
hardening paths:

  * ``instances``  (PAUSED tree)
  * ``task``        (the ``process_report`` Task that was cancelled)
  * ``message_queue`` (the orphaned ``completion_report`` row)
  * ``job_queue_items`` (message-type mirror)
  * ``dependency_watchers`` (any PENDING watchers that may have leaked)
  * ``report_injections`` (consumption check for Phase 2.5)

The builders return a small dataclass-style mapping (Plain
``dict``) of IDs and ``work_id``s so the calling test can assert
specific return values without re-querying the DB.

Reference: ``.agents/shared/planning/fix-pause-report-turn-orphan/phase2-plan.md``
Task 14 + 18.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

# Register tables before metadata.create_all() so the schema is built.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus.models import (
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.instance.models import (
    Instance,
    InstanceHierarchy,
    InstanceStatus,
)
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
)
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


# Status-to-admission mapping local to the test helper. Mirrors
# the same mapping used in test_cascade_pause_resume.py. Defined
# here so the helper is self-contained.
def _status_to_admission(status: str) -> str:
    return {
        "pending": "queued",
        "processing": "active",
        "paused": "active",
        "completed": "done",
        "failed": "done",
        "cancelled": "done",
        "dead_letter": "dead",
        "queued": "queued",
        "active": "active",
        "done": "done",
        "dead": "dead",
    }.get(status, "queued")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class OrphanScenario:
    """Seeded IDs and work_ids for a single test scenario.

    The test can use ``scenario.instance_id`` /
    ``scenario.cancelled_task_work_id`` / etc. to assert against
    the cascade result without re-querying the DB.
    """

    instance_id: str
    child_instance_id: str | None
    cancelled_task_id: int
    cancelled_task_work_id: str
    orphaned_message_id: str
    other_message_ids: list[str] = field(default_factory=list)
    other_task_work_ids: list[str] = field(default_factory=list)
    report_injection_id: str | None = None
    report_injection_state: str | None = None


def ensure_schema(engine: Engine) -> None:
    """Build the SQLModel schema on the given engine.

    Idempotent: ``create_all`` is a no-op on an existing schema.
    """
    SQLModel.metadata.create_all(engine)


def seed_paused_tree(
    engine: Engine,
    *,
    instance_id: str | None = None,
    parent_id: str | None = None,
    status: str = InstanceStatus.PAUSED.value,
) -> str:
    """Insert a single Instance row. Returns the instance_id."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = _now_iso()
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            agent_name="developer",
            parent_id=parent_id,
            project_id="test-project",
            status=status,
            created_at=now_iso,
            updated_at=now_iso,
        )
        s.add(inst)
        s.commit()
    return iid


def seed_hierarchy(
    engine: Engine, *, parent_id: str, child_id: str
) -> None:
    """Insert an InstanceHierarchy row."""
    now_iso = _now_iso()
    with Session(engine) as s:
        s.add(
            InstanceHierarchy(
                parent_id=parent_id, child_id=child_id, created_at=now_iso
            )
        )
        s.commit()


def seed_paused_task(
    engine: Engine,
    *,
    instance_id: str,
    work_id: str | None = None,
    message_id: str | None = None,
) -> int:
    """Insert a Task at ``status='paused'``. Returns the task id."""
    work_id = work_id or f"work-{uuid.uuid4().hex[:12]}"
    now = _now_dt()
    with Session(engine) as s:
        task = Task(
            work_id=work_id,
            task_type="process_report",
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.PAUSED.value,
            worker_id="worker-0",
            cancel_requested=True,
            cancel_requested_at=now.isoformat(),
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return int(task.id)


def seed_processing_completion_report(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str | None = None,
    processing_task_id: str | None = None,
    content: str = "test-orphan-content",
) -> str:
    """Insert a ``processing`` ``completion_report`` row.

    Production reality: ``processing_task_id=NULL`` (no producer in
    ``daemon/`` populates the column today). The ``processing_task_id``
    parameter is provided for the defensive non-NULL test case
    (Task 1 row + Task 2 acceptance).
    """
    mid = message_id or f"msg-{uuid.uuid4().hex[:12]}"
    now = _now_dt()
    with Session(engine) as s:
        s.add(
            MessageQueue(
                message_id=mid,
                instance_id=instance_id,
                content=content,
                type=MessageType.COMPLETION_REPORT.value,
                source="test",
                status=MessageStatus.PROCESSING.value,
                enqueued_at=now,
                last_activity_at=now,
                processing_task_id=processing_task_id,
            )
        )
        s.commit()
    return mid


def seed_job(
    engine: Engine,
    *,
    instance_id: str,
    status: str = "active",
) -> str:
    """Insert a message-type JobItem. Returns the job_id."""
    jid = f"job-{uuid.uuid4().hex[:8]}"
    now_iso = _now_iso()
    with Session(engine) as s:
        s.add(
            JobItem(
                job_id=jid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="hello",
                source="api",
                project_id="test-project",
                job_type="message",
                admission_state=_status_to_admission(status),
                instance_id=instance_id,
                created_at=now_iso,
            )
        )
        s.commit()
    return jid


def seed_report_injection(
    engine: Engine,
    *,
    parent_instance_id: str,
    child_instance_id: str,
    child_message_id: str,
    report_message_id: str,
    state: str = ReportInjectionState.PENDING.value,
    content: str = "test-injection-content",
) -> str:
    """Insert a ReportInjection row. Returns the injection_id."""
    iid = f"inj-{uuid.uuid4().hex[:8]}"
    now_iso = _now_iso()
    with Session(engine) as s:
        s.add(
            ReportInjection(
                injection_id=iid,
                parent_instance_id=parent_instance_id,
                child_instance_id=child_instance_id,
                child_message_id=child_message_id,
                report_message_id=report_message_id,
                content=content,
                state=state,
                created_at=now_iso,
                delivered_at=(
                    now_iso
                    if state
                    in (
                        ReportInjectionState.INJECTED.value,
                        ReportInjectionState.TASK_DELIVERED.value,
                    )
                    else None
                ),
            )
        )
        s.commit()
    return iid


def seed_watcher(
    engine: Engine,
    *,
    target_instance_id: str,
    source_task_id: str,
    state: str = DependencyWatcherState.PENDING.value,
) -> str:
    """Insert a DependencyWatcher row. Returns the watch_id."""
    wid = f"watch-{uuid.uuid4().hex[:8]}"
    now_iso = _now_iso()
    with Session(engine) as s:
        s.add(
            DependencyWatcher(
                watch_id=wid,
                source_task_id=source_task_id,
                target_instance_id=target_instance_id,
                follow_up_payload={"kind": "test"},
                watcher_metadata={"kind": "test"},
                created_at=now_iso,
                state=state,
            )
        )
        s.commit()
    return wid


def seed_orphan_scenario(
    engine: Engine,
    *,
    instance_id: str | None = None,
    with_report_injection: bool = False,
    report_injection_state: str = ReportInjectionState.PENDING.value,
) -> OrphanScenario:
    """Seed the exact production-state scenario for Bug B.

    The returned ``OrphanScenario`` carries the IDs and work_ids
    so the test can assert specific return values.

    Production state (see
    ``docs/bugs/pause-during-report-turn-orphans-message-jobitem.md``):

      * root instance at ``WAITING_CHILDREN`` (we use PAUSED for
        the cascade-triggering variant);
      * one ``process_report`` Task at ``status='paused'`` (will be
        cancelled by the resume cascade);
      * one ``completion_report`` ``message_queue`` row at
        ``status='processing'`` with ``processing_task_id=NULL``
        (orphan — the production reality);
      * (optional) one ``ReportInjection`` row for the
        consumption check.
    """
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    seed_paused_tree(engine, instance_id=iid, status=InstanceStatus.PAUSED.value)
    # Seed the process_report Task at PAUSED so the resume cascade
    # has something to cancel.
    task_id = seed_paused_task(
        engine, instance_id=iid, work_id=None, message_id=None
    )
    # Re-fetch to get the generated work_id.
    with Session(engine) as s:
        task_row = s.get(Task, task_id)
        work_id = str(task_row.work_id)
    # Seed the orphaned ``completion_report`` row. Link its
    # ``message_id`` to the cancelled Task so the NULL-fallback
    # correlation finds it.
    msg_id = seed_processing_completion_report(
        engine,
        instance_id=iid,
        message_id=f"msg-{uuid.uuid4().hex[:12]}",
        processing_task_id=None,  # production reality
    )
    # Patch the Task's message_id so the correlation is real.
    with Session(engine) as s:
        task_row = s.get(Task, task_id)
        task_row.message_id = msg_id
        s.add(task_row)
        s.commit()
    # Optional ReportInjection.
    if with_report_injection:
        rid = seed_report_injection(
            engine,
            parent_instance_id=iid,
            child_instance_id=f"child-{uuid.uuid4().hex[:8]}",
            child_message_id=msg_id,
            report_message_id=msg_id,
            state=report_injection_state,
        )
    else:
        rid = None
    return OrphanScenario(
        instance_id=iid,
        child_instance_id=None,
        cancelled_task_id=task_id,
        cancelled_task_work_id=work_id,
        orphaned_message_id=msg_id,
        report_injection_id=rid,
        report_injection_state=report_injection_state if rid else None,
    )


def read_message(
    engine: Engine, message_id: str
) -> dict[str, Any] | None:
    """Read a single ``message_queue`` row as a dict (or None)."""
    with Session(engine) as s:
        row = s.get(MessageQueue, message_id)
        if row is None:
            return None
        return {
            "message_id": row.message_id,
            "instance_id": row.instance_id,
            "status": row.status,
            "type": row.type,
            "processing_task_id": row.processing_task_id,
            "error_message": row.error_message,
            "completed_at": row.completed_at,
        }


def read_task(
    engine: Engine, task_id: int
) -> dict[str, Any] | None:
    """Read a single ``task`` row as a dict (or None)."""
    with Session(engine) as s:
        row = s.get(Task, task_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "work_id": row.work_id,
            "status": row.status,
            "message_id": row.message_id,
            "instance_id": row.instance_id,
        }


def read_instance(
    engine: Engine, instance_id: str
) -> dict[str, Any] | None:
    """Read a single ``instances`` row as a dict (or None)."""
    with Session(engine) as s:
        row = s.get(Instance, instance_id)
        if row is None:
            return None
        return {
            "instance_id": row.instance_id,
            "status": row.status,
            "parent_id": row.parent_id,
            "paused_at": row.paused_at,
        }


def read_all_pending_for_instance(
    engine: Engine, *, instance_id: str
) -> list[dict[str, Any]]:
    """Read all ``message_queue`` rows for an instance (any status)."""
    with Session(engine) as s:
        rows = list(
            s.exec(
                select(MessageQueue).where(
                    MessageQueue.instance_id == instance_id
                )
            )
        )
    return [
        {
            "message_id": r.message_id,
            "status": r.status,
            "type": r.type,
            "processing_task_id": r.processing_task_id,
        }
        for r in rows
    ]
