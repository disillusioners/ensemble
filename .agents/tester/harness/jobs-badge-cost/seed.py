"""Seed a file-backed SQLite fixture for the jobs-badge-cost harness.

Recipe: NullPool + WAL + busy_timeout (Testing & QC conventions).
Population: >=400 terminal JobItems (completed/failed/cancelled/dead_letter mix,
dead_letter>=5), >=8 queued + >=4 active, ~30 linked Instances, a few ORPHAN
instance_ids, some Task timing rows, >=50 JobWatcher rows (mixed active).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.task.models import Task, TaskStatus, TaskType


def make_engine(db_path: str) -> Engine:
    eng = create_engine(
        f"sqlite:///{db_path}",
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    with eng.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA busy_timeout=10000")
        conn.commit()
    SQLModel.metadata.create_all(eng)
    return eng


def seed(engine: Engine, *, project_id: str = "badge-cost-project") -> dict:
    now = datetime.now(timezone.utc)
    stats = {"instances": 0, "jobs_terminal": 0, "jobs_queued": 0,
             "jobs_active": 0, "orphans": 0, "tasks": 0, "watches": 0}

    # ~30 linked instances
    instance_ids = []
    for i in range(30):
        iid = f"inst-{i}"
        instance_ids.append(iid)
        with Session(engine) as s:
            s.add(Instance(
                instance_id=iid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                project_id=project_id,
                status="running" if i % 3 != 0 else "completed",
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
                last_activity_at=now,
                paused_at=None,
            ))
            s.commit()
        stats["instances"] += 1

    # a few orphan instance_ids (referenced by jobs but no instance row)
    orphan_ids = [f"orphan-{i}" for i in range(5)]
    stats["orphans"] = len(orphan_ids)

    all_instance_refs = instance_ids + orphan_ids

    # queued + active jobs (>=8 queued, >=4 active)
    queued_ids = []
    for i in range(10):
        iid = all_instance_refs[i % len(all_instance_refs)]
        jid = f"job-queued-{i}"
        queued_ids.append(jid)
        with Session(engine) as s:
            s.add(JobItem(
                job_id=jid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="queued badge job",
                source="api",
                project_id=project_id,
                priority=5,
                queue_id="budget-queue",
                instance_id=iid,
                admission_state=AdmissionState.QUEUED.value,
                terminal_reason=None,
                job_metadata={},
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
                job_type="message",
            ))
            s.commit()
        stats["jobs_queued"] += 1

    for i in range(5):
        iid = all_instance_refs[i % len(all_instance_refs)]
        jid = f"job-active-{i}"
        with Session(engine) as s:
            s.add(JobItem(
                job_id=jid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="active badge job",
                source="api",
                project_id=project_id,
                priority=5,
                queue_id="budget-queue",
                instance_id=iid,
                admission_state=AdmissionState.ACTIVE.value,
                terminal_reason=None,
                job_metadata={},
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
                job_type="message",
            ))
            s.commit()
        stats["jobs_active"] += 1

    # terminal jobs: 400+ mix
    terminal_states = [
        (AdmissionState.DONE.value, "completed"),
        (AdmissionState.DEAD.value, "dead_letter"),
        (AdmissionState.DONE.value, "failed"),
        (AdmissionState.DONE.value, "cancelled"),
    ]
    terminal_count = 0
    # dead_letter >=5
    for i in range(8):
        iid = all_instance_refs[i % len(all_instance_refs)]
        jid = f"job-dl-{i}"
        with Session(engine) as s:
            s.add(JobItem(
                job_id=jid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="dead letter job",
                source="api",
                project_id=project_id,
                priority=5,
                queue_id="budget-queue",
                instance_id=iid,
                admission_state=AdmissionState.DEAD.value,
                terminal_reason="dead_letter",
                job_metadata={},
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
                job_type="message",
            ))
            s.commit()
        terminal_count += 1
    stats["jobs_terminal"] += 8

    i = 0
    while terminal_count < 420:
        state, reason = terminal_states[i % len(terminal_states)]
        # avoid double counting dead_letter already done; use completed/failed/cancelled mostly
        if reason == "dead_letter":
            i += 1
            continue
        iid = all_instance_refs[i % len(all_instance_refs)]
        jid = f"job-term-{terminal_count}"
        admission = AdmissionState.DONE.value if reason in ("completed", "failed", "cancelled") else AdmissionState.DEAD.value
        with Session(engine) as s:
            s.add(JobItem(
                job_id=jid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message=f"terminal {reason} job",
                source="api",
                project_id=project_id,
                priority=5,
                queue_id="budget-queue",
                instance_id=iid,
                admission_state=admission,
                terminal_reason=reason,
                job_metadata={},
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
                job_type="message",
            ))
            s.commit()
        terminal_count += 1
        stats["jobs_terminal"] += 1
        i += 1

    # Task timing rows: some tasks with started/completed
    for i in range(20):
        iid = instance_ids[i % len(instance_ids)]
        with Session(engine) as s:
            t = Task(
                work_id=f"work-task-{i}",
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=iid,
                status=TaskStatus.COMPLETED.value,
                started_at=now - timedelta(seconds=10),
                completed_at=now,
                created_at=now - timedelta(seconds=20),
            )
            s.add(t)
            s.commit()
        stats["tasks"] += 1

    # JobWatcher rows >=50 mixed active
    watch_count = 0
    for i in range(60):
        jid = f"watched-job-{i}"
        iid = instance_ids[i % len(instance_ids)]
        # seed a job for some watched
        with Session(engine) as s:
            s.add(JobWatcher(
                watch_id=str(uuid.uuid4()),
                job_id=jid,
                instance_id=iid,
                watch_events=["completed", "failed"],
                created_at=now,
            ))
            s.commit()
        watch_count += 1
    stats["watches"] = watch_count

    return stats


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "/tmp/badge_cost.db"
    if os.path.exists(db):
        os.remove(db)
    eng = make_engine(db)
    st = seed(eng)
    print(st)
