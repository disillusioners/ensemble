"""Shape-A boot-wipe receipt pins (fix/post-outage-recovery SCOPE b2, F3).

Adjudicated Shape A (post-outage revive-flip pair): the ARMED boot-wipe pair
deletes a revived instance's durable rows. The revive path
(``enqueue_message``, plain) creates Task PENDING + MessageQueue ready + a
``message_received`` Event — but NO JobItem, so the task wipe's FP1 arm is
inapplicable and the task is deleted; the mq wipe (preserve = task status IN
(running, paused)) does not preserve a PENDING task's row either — deleted.
The Event row is never deleted: the receipt-of-truth.

F3 contract: the wipe's job is "discard doomed orphans". A committed enqueue
with a surviving receipt is NOT doomed:
  (i)  the TASK wipe keeps a PENDING task whose message has a surviving
       ``message_received`` Event;
  (ii) the MQ wipe aligns with the task wipe: a row is preserved when its
       task anchors a non-terminal JobItem (FP1) OR its task is PENDING with
       a surviving Event. Terminal-backlog rows stay doomed (the wipe's
       pruner role is intact — the live host's PG volume discipline).

Pins (P3): row existence AFTER the wipe — never "no error appeared".
Hermetic SQLite (file-backed, NullPool, REAL SQLModel schema).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import Instance
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository as MessageQueueRepository,
)
from daemon.repositories.task.models import Task
from daemon.repositories.task.repository import TaskRepository


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    eng = create_engine(
        f"sqlite:///{tmp_path}/boot_wipe_receipt.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def task_repo(engine: Engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def mq_repo(engine: Engine) -> MessageQueueRepository:
    return MessageQueueRepository(engine)


_NOW = datetime.now(timezone.utc)


def _seed_enqueued_work(
    engine: Engine,
    *,
    iid: str,
    with_event: bool,
    jobitem: str | None = None,  # None | "active" | "queued" | "done"
    task_status: str = "pending",
) -> tuple[int, str]:
    """Seed one enqueued unit of work: Task + MessageQueue (+ Event receipt)
    (+ JobItem anchor). Returns (task_id, message_id)."""
    mid = f"mq-{uuid4().hex[:12]}"
    wid = f"w-{uuid4().hex[:12]}"
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="ari",
                agent_dir="/agents/ari",
                status="running",
            )
        )
        s.add(
            Task(
                work_id=wid,
                task_type="process_message",
                instance_id=iid,
                message_id=mid,
                status=task_status,
                created_at=_NOW,
            )
        )
        s.add(
            MessageQueue(
                message_id=mid,
                instance_id=iid,
                content="revive payload",
                source="api",
                status="ready",
                enqueued_at=_NOW,
            )
        )
        if with_event:
            s.add(
                Event(
                    instance_id=iid,
                    message_id=mid,
                    kind="message_received",
                    data=(
                        '{"message_id": "%s", "role": "user", '
                        '"content": "revive payload", "source": "api"}' % mid
                    ),
                    created_at=_NOW,
                )
            )
        if jobitem is not None:
            s.add(
                JobItem(
                    job_id=wid,
                    agent_id="ari",
                    agent_dir="/agents/ari",
                    message="revive payload",
                    source="api",
                    admission_state=jobitem,
                    job_type="message",
                    instance_id=iid,
                    created_at=_NOW.isoformat(),
                )
            )
        s.commit()
        tid = s.exec(
            select(Task.id).where(Task.message_id == mid)
        ).first()
        assert tid is not None
        return int(tid), mid


def _task_exists(engine: Engine, task_id: int) -> bool:
    with Session(engine) as s:
        return s.get(Task, task_id) is not None


def _mq_exists(engine: Engine, message_id: str) -> bool:
    with Session(engine) as s:
        return (
            s.exec(
                select(MessageQueue).where(
                    MessageQueue.message_id == message_id
                )
            ).first()
            is not None
        )


def _run_boot_wipe(
    engine: Engine, task_repo: TaskRepository, mq_repo: MessageQueueRepository
) -> None:
    """The armed wipe pair, exactly as boot runs it (manager CAPTURE/DELETE
    + task clear_all, preserve_in_flight=True)."""
    doomed = mq_repo.capture_doomed_ids_and_audit(preserve_in_flight=True)
    task_repo.clear_all(preserve_in_flight=True)
    mq_repo.delete_doomed_ids(doomed, preserve_in_flight=True)


# ---------------------------------------------------------------------------
# P3 — the revived-instance shape survives the armed wipe
# ---------------------------------------------------------------------------


def test_p3_revive_shape_task_and_mq_survive_the_wipe(
    engine: Engine, task_repo: TaskRepository, mq_repo: MessageQueueRepository
) -> None:
    """THE adjudicated Shape A victim: PENDING task, NO JobItem, Event row
    present (the plain-revive enqueue). Post-F3 BOTH rows must SURVIVE the
    armed wipe."""
    task_id, mid = _seed_enqueued_work(
        engine, iid="inst-revive", with_event=True, jobitem=None
    )

    _run_boot_wipe(engine, task_repo, mq_repo)

    assert _task_exists(engine, task_id), (
        "PENDING task with a surviving message_received Event must be KEPT — "
        "the Event is the receipt-of-truth; the enqueue is not doomed"
    )
    assert _mq_exists(engine, mid), (
        "the MQ row of the same receipt-backed enqueue must be KEPT (F3 ii "
        "Event arm) — the task/mq asymmetry defect is closed"
    )


def test_p3_genuinely_doomed_orphan_still_wiped(
    engine: Engine, task_repo: TaskRepository, mq_repo: MessageQueueRepository
) -> None:
    """The wipe's real job is intact: a PENDING task + mq row with NO Event
    and NO JobItem anchor (no durable receipt, no live job) is still wiped."""
    task_id, mid = _seed_enqueued_work(
        engine, iid="inst-orphan", with_event=False, jobitem=None
    )

    _run_boot_wipe(engine, task_repo, mq_repo)

    assert not _task_exists(engine, task_id), (
        "receipt-less orphan task must be wiped (the discard contract holds)"
    )
    assert not _mq_exists(engine, mid), (
        "receipt-less orphan mq row must be wiped"
    )


def test_p3_fp1_alignment_mq_row_survives_nonterminal_jobitem(
    engine: Engine, task_repo: TaskRepository, mq_repo: MessageQueueRepository
) -> None:
    """F3(ii) FP1 alignment: a PENDING task anchoring a non-terminal JobItem
    already survives the TASK wipe (FP1) — but its MQ row was DELETED by the
    mq wipe (preserve = running/paused only). The asymmetry defect: the task
    is preserved while its message is deleted. Post-F3 both survive."""
    task_id, mid = _seed_enqueued_work(
        engine, iid="inst-fp1", with_event=True, jobitem="active"
    )

    _run_boot_wipe(engine, task_repo, mq_repo)

    assert _task_exists(engine, task_id), "FP1 keeps the anchored task (pre-existing)"
    assert _mq_exists(engine, mid), (
        "the MQ wipe must align with FP1: a row whose task anchors a "
        "non-terminal JobItem is preserved"
    )


def test_p3_completed_backlog_still_wiped_pruner_intact(
    engine: Engine, task_repo: TaskRepository, mq_repo: MessageQueueRepository
) -> None:
    """The pruner role is intact: terminal backlog (COMPLETED task + its mq
    row) stays doomed even though the enqueue's Event survives — the work is
    consumed, the receipt is fulfilled, and the live host's volume discipline
    requires the backlog clear."""
    task_id, mid = _seed_enqueued_work(
        engine, iid="inst-done", with_event=True, jobitem=None,
        task_status="completed",
    )
    with Session(engine) as s:
        row = s.get(Task, task_id)
        assert row is not None
        row.completed_at = _NOW
        s.add(row)
        s.commit()

    _run_boot_wipe(engine, task_repo, mq_repo)

    assert not _task_exists(engine, task_id), (
        "terminal backlog task must still be wiped"
    )
    assert not _mq_exists(engine, mid), (
        "terminal backlog mq row must still be wiped (pruner role intact)"
    )


def test_p3_capture_and_delete_predicates_agree(
    engine: Engine, task_repo: TaskRepository, mq_repo: MessageQueueRepository
) -> None:
    """The F-1 audit contract: the CAPTURE's doomed set and the DELETE's
    re-check predicate must agree — a receipt-backed id captured before a
    late-arriving Event is re-checked at DELETE time and SPARED."""
    task_id, mid = _seed_enqueued_work(
        engine, iid="inst-agree", with_event=True, jobitem=None
    )

    doomed = mq_repo.capture_doomed_ids_and_audit(preserve_in_flight=True)
    task_repo.clear_all(preserve_in_flight=True)
    deleted = mq_repo.delete_doomed_ids(doomed, preserve_in_flight=True)

    assert mid not in doomed, (
        "the capture must not doom a receipt-backed enqueue (post-F3)"
    )
    assert _task_exists(engine, task_id)
    assert _mq_exists(engine, mid)
    assert deleted == 0, "nothing doomed for this fixture → zero deletions"
