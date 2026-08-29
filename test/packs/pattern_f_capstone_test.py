#!/usr/bin/env python3
"""Pattern (f) Capstone Probe — 092c5ed3-class E2E on real engine composition.

Spec: .agents/tester/MOCK_TESTS.md → "E2E Capstone — 092c5ed3-class Zombie
Active JobItems".

Branch: feature/orphan-active-job-recovery @ ba39a40e.
Composed E2E story on REAL engine components (no mocks below
repository/service seam; no daemon process; file-backed SQLite under /tmp;
no ports). The component-level kill-path probe
(test/packs/pattern_f_killpath_matrix_test.py, PASS 5/5) proved the
sweep's guards per-scenario — this capstone proves the COMPOSED story:

  1. SEED one world:
     - proj-capstone:
       * queue-fifo (FIFO, c=2) — holds zombie shape-1 + zombie shape-2
       * queue-defer (DEFER, c=1) — holds defer job C (the wedge target)
     - proj-other (separate project so the healthy control doesn't pollute
       proj-capstone's idle-gate count):
       * queue-other (FIFO, c=1) — holds healthy control

     * Zombie shape-1: ACTIVE JobItem job-z1 on queue-fifo (slot 0),
       created_at 1800s ago, NO Task rows, instance inst-z1 created
       3600s ago (mid-mint conjunct satisfied), lock row for z1.
     * Zombie shape-2: ACTIVE JobItem job-z2 on queue-fifo (slot 1),
       created_at 1800s ago, COMPLETED Task task-z2 (work_id=job-z2,
       completed_at 120s ago — past 60s age floor), instance inst-z2
       created 3600s ago, lock row for z2, JobWatcher row
       watcher-z2 (job_id=job-z2, instance_id=inst-watcher) with
       watch_events including 'completed' so notify fires.
     * Healthy control: ACTIVE JobItem job-h on queue-other, PENDING
       Task task-h (work_id=job-h), instance inst-h created 30s ago
       (young — would hit healthy_shape guard regardless).
     * Defer job C: QUEUED JobItem job-c on queue-defer (instance
       inst-c created 3600s ago). The defer queue has NO lock held
       (job-c is QUEUED, not ACTIVE) — the wedge is the IDLE GATE
       (defer queue admission is gated on
       ``count_active_jobs_in_non_defer_queues(project_id) > 0``).
       Pre-sweep: count_active_jobs_in_non_defer_queues("proj-capstone")
       returns 2 (job-z1, job-z2 both ACTIVE on non-defer queue).
       Post-sweep (goal): returns 0 — the wedge is released.

  2. RUN ONE REAL ``reconcile_drift_states`` (the production periodic
     drift entry at ``daemon/services/job_recovery_service.py:1402``
     which delegates into ``_pattern_f_orphan_active_job_recovery:1731``).
     Patches: ``get_dependency_bus`` → empty-bus stub (so the
     bus-pending leg of f2 passes). Real JobQueueService wired with
     watcher_repo + JobRepository + LockRepository +
     MagicMock(InstanceManager) that records enqueue_message calls
     (the notify path needs an instance_manager; the mock is above
     the service seam and only records calls — it doesn't drive
     behavior).

  3. ASSERT (real DB rows at each step):
     - shape-1 → DEAD + lock-z1 GONE + orphan_active_no_task_dead
       detail recorded
     - shape-2 → DONE + lock-z2 GONE + orphan_active_completed_task_done
       detail recorded + watcher-z2 row GONE (fired+canceled) +
       instance_manager.enqueue_message was called once (notify path
       exercised end-to-end)
     - healthy control → job-h still ACTIVE + task-h still PENDING
       (orphan_active_skipped_healthy_shape detail recorded)
     - defer job C → job-c now ADMITTABLE:
         * count_active_jobs_in_non_defer_queues("proj-capstone") drops
           from 2 → 0 (idle gate released)
         * real try_acquire_slot for job-c on queue-defer slot 0
           succeeds (lock was always free, but proves the real claim
           path resolves cleanly after the wedge)

  4. NEGATIVE control within the same world (the healthy control is
     the negative — see above).

Output contract: stepwise PASS/FAIL story lines + ``RESULT:
PASS|FAIL|TIMEOUT`` + exit 0/1/124. Runtime <4 min.

Constraints honored:
- READ-ONLY on production code — defects → evidence + REPORT.
- No commits. Doesn't touch .agents/tester/.
- Honest reporting: the wedge for C is the IDLE GATE (not the lock),
  explicitly reported below.

Self-contained. Internal 270s timeout via ``signal.alarm``; wrapped
with `timeout 300` by the .sh wrapper (dual-layer guard per the
test-pack skill).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure repo root on PYTHONPATH so daemon/ resolves when run from anywhere
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from daemon.repositories.instance.repository import (  # noqa: E402
    SQLModelInstanceRepository,
)
from daemon.repositories.job_queue.lock_repository import (  # noqa: E402
    LockRepository,
)
from daemon.repositories.job_queue.models import (  # noqa: E402
    AdmissionState,
)
from daemon.repositories.job_queue.queue_repository import (  # noqa: E402
    JobQueueRepository,
)
from daemon.repositories.job_queue.repository import JobRepository  # noqa: E402
from daemon.repositories.job_queue.watcher_models import (  # noqa: E402
    ALL_TERMINAL_STATES,
)
from daemon.repositories.job_queue.watcher_repository import (  # noqa: E402
    JobWatcherRepository,
)
from daemon.repositories.task.repository import TaskRepository  # noqa: E402
from daemon.services.job_queue_service import JobQueueService  # noqa: E402
from daemon.services.job_recovery_service import JobRecoveryService  # noqa: E402


# ════════════════════════════════════════════════════════════════════════════
# Result collection
# ════════════════════════════════════════════════════════════════════════════

_RESULTS: list[tuple[str, str, str]] = []  # (step, status, evidence)
_OVERALL_PASS = True
_TIMED_OUT = False


def _record(step: str, passed: bool, evidence: str) -> None:
    global _OVERALL_PASS
    status = "PASS" if passed else "FAIL"
    if not passed:
        _OVERALL_PASS = False
    _RESULTS.append((step, status, evidence))
    print(f"--- {step}: {status} ---")
    print(evidence)
    print()


def _alarm_handler(signum, frame):  # noqa: ARG001
    global _TIMED_OUT
    _TIMED_OUT = True
    raise TimeoutError("internal 270s alarm tripped")


# ════════════════════════════════════════════════════════════════════════════
# Shared bus stub (only stub for the production seam; everything else is real)
# ════════════════════════════════════════════════════════════════════════════


class _EmptyBus:
    """Bus stub returning no pending watchers (passes f2 Gate 1)."""

    async def pending_watchers(self, source_task_id):  # noqa: ARG002
        return []


# ════════════════════════════════════════════════════════════════════════════
# Row inserters (inlined — independent of pytest fixtures)
# ════════════════════════════════════════════════════════════════════════════


def _insert_instance(
    engine, instance_id: str, *, project_id: str = "proj-capstone",
    status: str = "running", agent_id: str = "developer",
    created_at: datetime | None = None,
) -> None:
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status, :project_id,
                     :created_at, :updated_at, 1)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def _insert_job_item(
    engine, *, job_id: str, instance_id: str, project_id: str,
    queue_id: str, admission_state: str,
    created_at: datetime | None = None,
    job_type: str = "task",
) -> None:
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source,
                     project_id, queue_id, priority, admission_state,
                     created_at, instance_id, job_type, retry_count,
                     metadata)
                VALUES
                    (:job_id, :agent_id, :agent_dir, :message, :source,
                     :project_id, :queue_id, :priority, :admission_state,
                     :created_at, :instance_id, :job_type, :retry_count,
                     :metadata)
                """
            ),
            {
                "job_id": job_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "message": "capstone probe",
                "source": "api",
                "project_id": project_id,
                "queue_id": queue_id,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
                "job_type": job_type,
                "retry_count": 0,
                "metadata": json.dumps({}),
            },
        )


def _insert_task(
    engine, *, work_id: str, instance_id: str,
    status: str, created_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> int:
    created_iso = (
        created_at or datetime.now(timezone.utc)
    ).isoformat()
    completed_iso = (
        completed_at.isoformat() if completed_at is not None else None
    )
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred, is_background,
                     completed_at)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :cancel_requested,
                     :retry_scheduled, :work_id, :is_deferred, :is_background,
                     :completed_at)
                """
            ),
            {
                "task_type": "process_message",
                "instance_id": instance_id,
                "message_id": None,
                "status": status,
                "retry_count": 0,
                "created_at": created_iso,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": work_id,
                "is_deferred": False,
                "is_background": False,
                "completed_at": completed_iso,
            },
        )
        return result.lastrowid


def _insert_lock(
    engine, *, project_id: str, queue_id: str, job_id: str,
    instance_id: str, lock_slot: int = 0,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_locks
                    (lock_id, project_id, queue_id, job_id,
                     instance_id, lock_slot, acquired_at)
                VALUES
                    (:lock_id, :project_id, :queue_id, :job_id,
                     :instance_id, :lock_slot, :acquired_at)
                """
            ),
            {
                "lock_id": f"lock-{job_id}",
                "project_id": project_id,
                "queue_id": queue_id,
                "job_id": job_id,
                "instance_id": instance_id,
                "lock_slot": lock_slot,
                "acquired_at": now,
            },
        )


# ════════════════════════════════════════════════════════════════════════════
# Read helpers
# ════════════════════════════════════════════════════════════════════════════


def _get_job(engine, job_id: str) -> dict[str, Any] | None:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM job_queue_items WHERE job_id = :j"),
            {"j": job_id},
        ).mappings().first()
    return dict(row) if row else None


def _get_locks_for_job(engine, job_id: str) -> list[dict[str, Any]]:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT * FROM job_locks WHERE job_id = :j"),
            {"j": job_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def _get_task_for_work(engine, work_id: str) -> dict[str, Any] | None:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM task WHERE work_id = :w"),
            {"w": work_id},
        ).mappings().first()
    return dict(row) if row else None


def _get_watcher_rows(engine, job_id: str) -> list[dict[str, Any]]:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT * FROM job_watchers WHERE job_id = :j"),
            {"j": job_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def _count_active_non_defer(engine, project_id: str) -> int:
    """Real ``count_active_jobs_in_non_defer_queues`` — the defer
    idle-gate's canonical predicate.
    """
    job_repo = JobRepository(engine)
    return job_repo.count_active_jobs_in_non_defer_queues(project_id)


# ════════════════════════════════════════════════════════════════════════════
# Story steps
# ════════════════════════════════════════════════════════════════════════════


async def step1_seed(engine, queue_repo) -> dict[str, str]:
    """Seed the composed world: two projects, queues, zombies, watcher,
    healthy control, defer job C. Returns the resolved queue_id mapping
    (UUID PKs from queue_repo.create()) so later steps use consistent
    queue_id strings.
    """
    step = "Step 1: SEED composed world"
    try:
        # ── proj-capstone queues (FIFO c=2 + DEFER c=1)
        q_fifo = queue_repo.create(
            project_id="proj-capstone",
            queue_name="queue-fifo",
            queue_type="fifo",
            concurrency_limit=2,
            is_system=True,
        )
        q_defer = queue_repo.create(
            project_id="proj-capstone",
            queue_name="queue-defer",
            queue_type="defer",
            concurrency_limit=1,
            is_system=True,
        )
        # ── proj-other queue (FIFO c=1) for the healthy control
        q_other = queue_repo.create(
            project_id="proj-other",
            queue_name="queue-other",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )

        # ── Zombie shape-1: ACTIVE JobItem + NO Task + mid-mint satisfied
        _insert_instance(
            engine, "inst-z1",
            project_id="proj-capstone",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-z1",
            instance_id="inst-z1",
            project_id="proj-capstone",
            queue_id=q_fifo.queue_id,
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
        )
        _insert_lock(
            engine,
            project_id="proj-capstone",
            queue_id=q_fifo.queue_id,
            job_id="job-z1",
            instance_id="inst-z1",
            lock_slot=0,
        )

        # ── Zombie shape-2: ACTIVE JobItem + COMPLETED Task (old)
        _insert_instance(
            engine, "inst-z2",
            project_id="proj-capstone",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-z2",
            instance_id="inst-z2",
            project_id="proj-capstone",
            queue_id=q_fifo.queue_id,
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
        )
        _insert_lock(
            engine,
            project_id="proj-capstone",
            queue_id=q_fifo.queue_id,
            job_id="job-z2",
            instance_id="inst-z2",
            lock_slot=1,
        )
        _insert_task(
            engine,
            work_id="job-z2",
            instance_id="inst-z2",
            status="completed",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        # ── JobWatcher row for shape-2 (the f2 notify target)
        watcher_repo = JobWatcherRepository(engine)
        watcher_repo.add_watch(
            job_id="job-z2",
            instance_id="inst-watcher",
            watch_events=list(ALL_TERMINAL_STATES),
        )

        # ── Defer job C: QUEUED on queue-defer (lock-free — wedge is gate)
        _insert_instance(
            engine, "inst-c",
            project_id="proj-capstone",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-c",
            instance_id="inst-c",
            project_id="proj-capstone",
            queue_id=q_defer.queue_id,
            admission_state=AdmissionState.QUEUED.value,
        )

        # ── Healthy control on proj-other (separate project so the
        # proj-capstone idle-gate count stays clean)
        _insert_instance(
            engine, "inst-h",
            project_id="proj-other",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        _insert_job_item(
            engine,
            job_id="job-h",
            instance_id="inst-h",
            project_id="proj-other",
            queue_id=q_other.queue_id,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _insert_task(
            engine,
            work_id="job-h",
            instance_id="inst-h",
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        # Also seed a lock for job-h so the negative control can assert
        # the LOCK is also untouched (not just admission_state + task)
        _insert_lock(
            engine,
            project_id="proj-other",
            queue_id=q_other.queue_id,
            job_id="job-h",
            instance_id="inst-h",
            lock_slot=0,
        )

        # ── Sanity: verify the seed via real reads
        ev: list[str] = []
        passed = True
        ev.append(f"  q_fifo.queue_id={q_fifo.queue_id[:8]}... (UUID PK)")
        ev.append(f"  q_defer.queue_id={q_defer.queue_id[:8]}... (UUID PK)")
        ev.append(f"  q_other.queue_id={q_other.queue_id[:8]}... (UUID PK)")
        ev.append(
            f"  job-z1 queue_id prefix="
            f"{_get_job(engine, 'job-z1')['queue_id'][:8]}... (expect matches q_fifo)"
        )
        ev.append(
            f"  job-z1 state={_get_job(engine, 'job-z1')['admission_state']!r} "
            f"(expect 'active')"
        )
        ev.append(
            f"  job-z2 state={_get_job(engine, 'job-z2')['admission_state']!r} "
            f"(expect 'active')"
        )
        ev.append(
            f"  job-c state={_get_job(engine, 'job-c')['admission_state']!r} "
            f"(expect 'queued')"
        )
        ev.append(
            f"  job-h state={_get_job(engine, 'job-h')['admission_state']!r} "
            f"(expect 'active')"
        )
        ev.append(
            f"  task for job-z2 status="
            f"{_get_task_for_work(engine, 'job-z2')['status']!r} "
            f"(expect 'completed')"
        )
        ev.append(
            f"  task for job-h status="
            f"{_get_task_for_work(engine, 'job-h')['status']!r} "
            f"(expect 'pending')"
        )
        ev.append(
            f"  watcher rows for job-z2="
            f"{len(_get_watcher_rows(engine, 'job-z2'))} (expect 1)"
        )
        ev.append(
            f"  locks for job-z1={len(_get_locks_for_job(engine, 'job-z1'))} "
            f"(expect 1)"
        )
        ev.append(
            f"  locks for job-z2={len(_get_locks_for_job(engine, 'job-z2'))} "
            f"(expect 1)"
        )
        # IDLE GATE pre-sweep: count_active_jobs_in_non_defer_queues
        pre_count_capstone = _count_active_non_defer(engine, "proj-capstone")
        pre_count_other = _count_active_non_defer(engine, "proj-other")
        ev.append(
            f"  PRE-sweep count_active_jobs_in_non_defer_queues("
            f"proj-capstone)={pre_count_capstone} (expect 2 — z1, z2)"
        )
        ev.append(
            f"  PRE-sweep count_active_jobs_in_non_defer_queues("
            f"proj-other)={pre_count_other} (expect 1 — job-h)"
        )
        if pre_count_capstone != 2:
            passed = False
            ev.append("  FAIL: pre-sweep count for proj-capstone != 2")
        if pre_count_other != 1:
            passed = False
            ev.append("  FAIL: pre-sweep count for proj-other != 1")
        ev.append(
            "  WEDGE-CITED: defer queue C admission is blocked by the idle "
            "gate at daemon/services/job_processor.py:212 "
            "(_defer_idle_check → count_active_jobs_in_non_defer_queues). "
            "Pre-sweep count for proj-capstone=2 → gate returns 1 (blocked). "
            "Post-sweep (goal): count=0 → gate returns 0 (released). "
            "Note: the defer queue's own c=1 lock is NOT held by C (C is "
            "QUEUED, not ACTIVE) — try_acquire_slot would succeed either "
            "way. The honest wedge is the idle gate, not the lock."
        )
        _record(step, passed, "\n".join(ev))
        return {
            "q_fifo": q_fifo.queue_id,
            "q_defer": q_defer.queue_id,
            "q_other": q_other.queue_id,
        }
    except Exception as e:
        _record(
            step, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
        )
        raise


def _make_service(
    engine, queue_repo, *, instance_manager=None,
) -> tuple[JobRecoveryService, Any]:
    """Build the real ``JobRecoveryService`` + a real ``JobQueueService``
    wired with a MagicMock InstanceManager (above the service seam;
    only records enqueue_message calls). Returns the service + the
    mock for later assertions.
    """
    job_repo = JobRepository(engine)
    lock_repo = LockRepository(engine)
    instance_repo = SQLModelInstanceRepository(engine=engine)
    task_repo = TaskRepository(engine)
    watcher_repo = JobWatcherRepository(engine)

    if instance_manager is None:
        instance_manager = MagicMock(name="InstanceManager")
        instance_manager.enqueue_message = AsyncMock(return_value=None)

    lock_manager_for_jq = MagicMock(name="LockManagerForJQ")
    real_jq_service = JobQueueService(
        job_repo, lock_manager_for_jq, queue_repo,
        instance_manager=instance_manager,
    )
    real_jq_service.set_watcher_repo(watcher_repo)

    from daemon.services.stale_task_recovery import StaleTaskRecovery
    stale_recovery = StaleTaskRecovery(
        task_repository=task_repo,
        message_repository=None,
        event_repository=None,
    )
    service = JobRecoveryService(
        job_repository=job_repo,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
        job_queue_service=real_jq_service,
        task_repository=task_repo,
        stale_task_recovery=stale_recovery,
    )
    return service, instance_manager


async def step2_run_sweep(engine, queue_repo) -> tuple[Any, Any]:
    """Run ONE real ``reconcile_drift_states``. Returns the service
    (for later assertions) and the instance_manager mock (to assert
    notify fired).
    """
    step = "Step 2: RUN one real reconcile_drift_states"
    try:
        # ── ONE real sweep via the production drift entry at
        # job_recovery_service.py:1402. Patches only the bus
        # singleton (above the service seam) so the bus-pending leg
        # of f2 passes (otherwise the singleton is None and the leg
        # returns (0, True) — but a real production run would have a
        # real bus; we stub it to deterministically simulate "no
        # pending watchers" for f2's leg 1).
        service, instance_manager = _make_service(engine, queue_repo)
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBus(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )

        details = stats.get("details", []) if isinstance(stats, dict) else []
        ev: list[str] = []
        passed = True

        # ── Pattern (f) detail records must include both zombies
        f1_records = [
            d for d in details
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-z1"
        ]
        f2_records = [
            d for d in details
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-z2"
        ]
        ev.append(
            f"  orphan_active_no_task_dead records for job-z1: "
            f"{len(f1_records)} (expect 1)"
        )
        ev.append(
            f"  orphan_active_completed_task_done records for job-z2: "
            f"{len(f2_records)} (expect 1)"
        )
        if len(f1_records) != 1:
            passed = False
            ev.append(
                f"  FAIL: orphan_active_no_task_dead for job-z1 missing "
                f"(details={details})"
            )
        if len(f2_records) != 1:
            passed = False
            ev.append(
                f"  FAIL: orphan_active_completed_task_done for job-z2 "
                f"missing (details={details})"
            )

        # ── Healthy control: orphan_active_skipped_healthy_shape
        skip_records = [
            d for d in details
            if d.get("pattern") == "orphan_active_skipped_healthy_shape"
            and d.get("job_id") == "job-h"
        ]
        ev.append(
            f"  orphan_active_skipped_healthy_shape records for job-h: "
            f"{len(skip_records)} (expect 1 — negative control held)"
        )
        if len(skip_records) != 1:
            passed = False
            ev.append(
                f"  FAIL: healthy_shape skip for job-h missing "
                f"(details={details})"
            )

        ev.append(
            f"  sweep stats: reconciled={stats.get('reconciled')}, "
            f"details={len(details)}"
        )

        _record(step, passed, "\n".join(ev))
        return service, instance_manager
    except Exception as e:
        _record(
            step, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
        )
        raise


async def step3_assert_post_sweep(engine, instance_manager, queue_ids) -> None:
    """Assert post-sweep DB state: zombies terminal, locks gone,
    watcher fired + cleared, healthy control untouched, defer job C
    now ADMITTABLE (real claim succeeds + idle gate released).
    """
    step = "Step 3: ASSERT post-sweep (real DB rows + claim path)"
    try:
        ev: list[str] = []
        passed = True

        # ── Shape-1: job-z1 DEAD + lock GONE
        job_z1 = _get_job(engine, "job-z1")
        locks_z1 = _get_locks_for_job(engine, "job-z1")
        ev.append(
            f"  job-z1 admission_state={job_z1['admission_state']!r} "
            f"(expect 'dead')"
        )
        ev.append(
            f"  job-z1 terminal_reason={job_z1.get('terminal_reason')!r}, "
            f"completed_at={'set' if job_z1.get('completed_at') else 'None'}"
        )
        ev.append(
            f"  locks for job-z1: {len(locks_z1)} (expect 0)"
        )
        if job_z1["admission_state"] != AdmissionState.DEAD.value:
            passed = False
            ev.append("  FAIL: job-z1 not DEAD")
        if len(locks_z1) != 0:
            passed = False
            ev.append(f"  FAIL: job-z1 locks remaining={len(locks_z1)}")

        # ── Shape-2: job-z2 DONE + lock GONE + watcher FIRED + cleared
        job_z2 = _get_job(engine, "job-z2")
        locks_z2 = _get_locks_for_job(engine, "job-z2")
        watcher_rows = _get_watcher_rows(engine, "job-z2")
        # task-z2 must still be COMPLETED (the sweep doesn't touch it)
        task_z2 = _get_task_for_work(engine, "job-z2")
        ev.append(
            f"  job-z2 admission_state={job_z2['admission_state']!r} "
            f"(expect 'done')"
        )
        ev.append(
            f"  job-z2 terminal_reason={job_z2.get('terminal_reason')!r}, "
            f"completed_at={'set' if job_z2.get('completed_at') else 'None'}"
        )
        ev.append(
            f"  locks for job-z2: {len(locks_z2)} (expect 0)"
        )
        ev.append(
            f"  task-z2 status={task_z2['status']!r} (expect 'completed' — "
            f"sweep must NOT touch the Task)"
        )
        ev.append(
            f"  watcher rows for job-z2: {len(watcher_rows)} (expect 0 — "
            f"fired + cleared by the legacy notify path)"
        )
        ev.append(
            f"  instance_manager.enqueue_message call_count="
            f"{instance_manager.enqueue_message.call_count} (expect >=1 — "
            f"proves the notify path ran end-to-end)"
        )
        if job_z2["admission_state"] != AdmissionState.DONE.value:
            passed = False
            ev.append("  FAIL: job-z2 not DONE")
        if len(locks_z2) != 0:
            passed = False
            ev.append(f"  FAIL: job-z2 locks remaining={len(locks_z2)}")
        if task_z2["status"] != "completed":
            passed = False
            ev.append(f"  FAIL: task-z2 status mutated to {task_z2['status']!r}")
        if len(watcher_rows) != 0:
            passed = False
            ev.append(
                f"  FAIL: watcher rows not cleared ({len(watcher_rows)} "
                f"still present — notify path didn't run or didn't claim)"
            )
        if instance_manager.enqueue_message.call_count < 1:
            passed = False
            ev.append(
                "  FAIL: instance_manager.enqueue_message never called "
                "— the legacy notify path was not exercised"
            )

        # ── Healthy control: job-h still ACTIVE + task-h still PENDING
        job_h = _get_job(engine, "job-h")
        task_h = _get_task_for_work(engine, "job-h")
        locks_h = _get_locks_for_job(engine, "job-h")
        ev.append(
            f"  job-h admission_state={job_h['admission_state']!r} "
            f"(expect 'active' — negative control untouched)"
        )
        ev.append(
            f"  task-h status={task_h['status']!r} (expect 'pending')"
        )
        ev.append(
            f"  locks for job-h: {len(locks_h)} (expect 1 — its lock "
            f"must remain — the sweep never touches healthy-shape jobs)"
        )
        if job_h["admission_state"] != AdmissionState.ACTIVE.value:
            passed = False
            ev.append(f"  FAIL: job-h state mutated to {job_h['admission_state']!r}")
        if task_h["status"] != "pending":
            passed = False
            ev.append(f"  FAIL: task-h status mutated to {task_h['status']!r}")
        if len(locks_h) != 1:
            passed = False
            ev.append(
                f"  FAIL: job-h lock count={len(locks_h)} (expected 1 — "
                f"negative control's lock must remain untouched)"
            )

        # ── DEFER JOB C: idle gate released + real claim succeeds
        # 1. Idle gate (the honest wedge): post-sweep count for
        #    proj-capstone must be 0 (z1 + z2 terminal).
        post_count_capstone = _count_active_non_defer(engine, "proj-capstone")
        post_count_other = _count_active_non_defer(engine, "proj-other")
        ev.append(
            f"  POST-sweep count_active_jobs_in_non_defer_queues("
            f"proj-capstone)={post_count_capstone} (expect 0 — idle gate "
            f"released)"
        )
        ev.append(
            f"  POST-sweep count_active_jobs_in_non_defer_queues("
            f"proj-other)={post_count_other} (expect 1 — healthy control "
            f"on its own project)"
        )
        if post_count_capstone != 0:
            passed = False
            ev.append(
                f"  FAIL: post-sweep proj-capstone count={post_count_capstone} "
                f"(expected 0) — the idle gate is still wedged"
            )

        # 2. Real claim for job-c on queue-defer slot 0 via the real
        #    try_acquire_slot seam. The defer queue's own lock was
        #    always free (job-c is QUEUED, not ACTIVE), so this claim
        #    succeeds PRE-sweep too — the value here is that the
        #    claim path is real and resolves cleanly post-sweep
        #    (proves the defer queue is operational after the wedge).
        lock_repo = LockRepository(engine)
        claimed = lock_repo.try_acquire_slot(
            lock_id="lock-job-c",
            project_id="proj-capstone",
            queue_id=queue_ids["q_defer"],
            job_id="job-c",
            instance_id="inst-c",
            slot=0,
        )
        ev.append(
            f"  real try_acquire_slot for job-c on queue-defer slot 0: "
            f"{claimed} (expect True — real claim path resolves cleanly "
            f"after the wedge)"
        )
        if not claimed:
            passed = False
            ev.append(
                "  FAIL: real claim for job-c FAILED — defer queue is "
                "still wedged (this should not be possible since job-c "
                "is QUEUED, not ACTIVE — the lock should be free)"
            )

        # 3. Confirm job-c is now ADMITTABLE: it's still QUEUED
        #    (the claim didn't transition it; that's the dispatcher's
        #    job), but the wedge is released — the dispatcher can now
        #    pick it up.
        job_c_after = _get_job(engine, "job-c")
        ev.append(
            f"  job-c admission_state={job_c_after['admission_state']!r} "
            f"(expect 'queued' — claim didn't transition; dispatcher does)"
        )
        if job_c_after["admission_state"] != AdmissionState.QUEUED.value:
            passed = False
            ev.append(
                f"  FAIL: job-c state mutated to {job_c_after['admission_state']!r}"
            )

        ev.append(
            "  WEDGE-RESOLVED: defer queue C is now ADMITTABLE. The wedge "
            "for C was the idle gate at daemon/services/job_processor.py:212 "
            "(_defer_idle_check → count_active_jobs_in_non_defer_queues). "
            "Pre-sweep count=2 → gate returns 1 (blocked). "
            "Post-sweep count=0 → gate returns 0 (released). The defer "
            "queue's own c=1 lock was never the wedge — job-c is QUEUED "
            "(not ACTIVE), so try_acquire_slot succeeds either way. The "
            "real claim above proves the post-sweep defer queue is "
            "operational."
        )

        _record(step, passed, "\n".join(ev))
    except Exception as e:
        _record(
            step, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
        )


async def step4_negative_control(engine) -> None:
    """Negative control within the same world: the healthy ACTIVE
    JobItem + PENDING Task (job-h) MUST remain ACTIVE after the sweep.
    Asserted inline in step 3, but called out as a separate step for
    the report's narrative.
    """
    step = "Step 4: NEGATIVE control (healthy ACTIVE JobItem stays ACTIVE)"
    try:
        job_h = _get_job(engine, "job-h")
        task_h = _get_task_for_work(engine, "job-h")
        ev: list[str] = []
        passed = True
        ev.append(
            f"  job-h admission_state={job_h['admission_state']!r} "
            f"(expect 'active')"
        )
        ev.append(
            f"  task-h status={task_h['status']!r} (expect 'pending')"
        )
        ev.append(
            "  Proven in step 3; called out as a discrete step so the "
            "report's story-line mirrors the spec's four-step contract."
        )
        if job_h["admission_state"] != AdmissionState.ACTIVE.value:
            passed = False
            ev.append(
                f"  FAIL: job-h state mutated to {job_h['admission_state']!r}"
            )
        if task_h["status"] != "pending":
            passed = False
            ev.append(f"  FAIL: task-h status mutated to {task_h['status']!r}")
        _record(step, passed, "\n".join(ev))
    except Exception as e:
        _record(
            step, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
        )


# ════════════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════════════


def main() -> int:
    print("=== Test Pack: pattern_f_capstone_test ===")
    print("(Pattern (f) Capstone — 092c5ed3-class E2E on real engine composition)")
    print(f"Branch: feature/orphan-active-job-recovery @ ba39a40e")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print()

    start = time.monotonic()

    # ── Layer-2 internal timeout (signal-based)
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(270)

    # ── File-backed SQLite under /tmp (per project convention)
    tmp_dir = tempfile.mkdtemp(prefix="pattern_f_capstone_")
    db_path = os.path.join(tmp_dir, "capstone.db")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    # ── Force-import every model so SQLModel.metadata.create_all
    # emits a complete schema (incl. job_watchers, job_locks, etc.)
    from daemon.repositories.job_queue import models as _jq_models  # noqa: F401
    from daemon.repositories.job_queue import watcher_models as _watch_models  # noqa: F401
    from daemon.repositories.task import models as _task_models  # noqa: F401
    from daemon.repositories.instance import models as _inst_models  # noqa: F401
    SQLModel.metadata.create_all(engine)

    print(f"DB: {db_path}")
    print(f"Tables: {sorted(SQLModel.metadata.tables.keys())[:10]}...")
    print()

    queue_repo = JobQueueRepository(engine)

    try:
        # Step 1: seed (returns resolved UUID queue_ids)
        queue_ids = asyncio.run(step1_seed(engine, queue_repo))

        # Step 2: run ONE real sweep
        service, instance_manager = asyncio.run(
            step2_run_sweep(engine, queue_repo)
        )

        # Step 3: assert post-sweep state (passes queue_ids so
        # try_acquire_slot uses the right UUID)
        asyncio.run(step3_assert_post_sweep(engine, instance_manager, queue_ids))

        # Step 4: negative control (called out for narrative)
        asyncio.run(step4_negative_control(engine))

    except TimeoutError as te:
        elapsed = time.monotonic() - start
        print(f"\nTIMEOUT: internal 270s alarm tripped: {te}")
        print(f"\nRESULT: TIMEOUT (elapsed={elapsed:.1f}s)")
        engine.dispose()
        _cleanup(tmp_dir, db_path)
        return 124
    except Exception as e:
        print(f"\nUNEXPECTED EXCEPTION in story runner: "
              f"{type(e).__name__}: {e}")
        traceback.print_exc()

    elapsed = time.monotonic() - start
    print("=" * 70)
    print(f"Story steps: {len(_RESULTS)}")
    print(f"  PASS: {sum(1 for _, s, _ in _RESULTS if s == 'PASS')}")
    print(f"  FAIL: {sum(1 for _, s, _ in _RESULTS if s == 'FAIL')}")
    print(f"Elapsed: {elapsed:.1f}s")
    print()

    # Dispose engine + remove tmp files
    engine.dispose()
    _cleanup(tmp_dir, db_path)

    if _TIMED_OUT:
        print("RESULT: TIMEOUT")
        return 124
    if _OVERALL_PASS:
        print("RESULT: PASS")
        return 0
    print("RESULT: FAIL")
    return 1


def _cleanup(tmp_dir: str, db_path: str) -> None:
    """Best-effort cleanup of /tmp files; do not raise."""
    import shutil
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
        if os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
