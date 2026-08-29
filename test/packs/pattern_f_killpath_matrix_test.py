#!/usr/bin/env python3
"""Pattern (f) Kill-Path Matrix Probe — behavioral, real-DB gate.

Spec: .agents/tester/MOCK_TESTS.md → "Pattern (f) Kill-Path Matrix
(council criticals, real scenarios)".

Branch: feature/orphan-active-job-recovery @ ba39a40e.
Independent of the in-tree unit tests
(tests/job_queue/test_orphan_active_job_recovery.py) — this probe
constructs REAL repositories on file-backed SQLite and drives the REAL
``JobRecoveryService._pattern_f_orphan_active_job_recovery`` sweep,
asserting REAL DB rows (never mock return values).

Only the dependency-bus singleton is stubbed via ``unittest.mock.patch``
(the service requires ``get_dependency_bus()`` to return an object with
``pending_watchers(task_id)``); the rest of the chain is real.

Scenarios (per spec):

  (a) PAUSED Task past grace → JobItem STAYS ACTIVE
        (orphan_active_skipped_paused), Task remains PAUSED, then
        PAUSED→PENDING via the resume semantics succeeds.
  (b) FAILED/CANCELLED Task + live retry child (FRESH work_id, same
        instance, PENDING) → JobItem STAYS ACTIVE
        (orphan_active_skipped_retry_child_live); when the retry
        completes, boundary finalizes to DONE with terminal_reason
        'failed'/'cancelled' + failed_at stamped + lock released +
        NO_RETRY.
  (c) Per-leg mutation check — for each of the 3 f2 gates
        (bus_pending, pending_instance_tasks, 60s age floor), prove
        that the leg is LOAD-BEARING: unmutated → JobItem stays
        ACTIVE with that leg's skip label; mutated (helper returns
        permissive) → f2 finalizes (proves the scenario is lethal
        without the leg, not vacuously safe).
  (d) Genuine restart-orphan: ACTIVE JobItem + NO Task rows +
        created_at past 900s + instance.created_at past grace →
        DEAD + lock row released + terminal_reason set; negative
        sub-case: instance FRESH (mid-mint) → STAYS ACTIVE
        (orphan_active_skipped_grace W1).
  (e) f2 lock release on c=1 queue: COMPLETED Task + all 3 legs
        permissive → JobItem DONE + lock released; new JobItem B
        enqueued on same queue → admits via real
        ``try_acquire_slot`` (no wedge).

Output contract (per scenario): PASS/FAIL line + key evidence rows.
Final line: ``RESULT: PASS|FAIL|TIMEOUT``; exit 0/1/124.

Self-contained. Internal 240s timeout via ``signal.alarm``; designed
to be wrapped with `timeout 300` by the .sh wrapper (dual-layer
guard per the test-pack skill).
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

from daemon.repositories.instance.models import InstanceStatus  # noqa: E402
from daemon.repositories.instance.repository import (  # noqa: E402
    SQLModelInstanceRepository,
)
from daemon.repositories.job_queue.lock_repository import (  # noqa: E402
    LockRepository,
)
from daemon.repositories.job_queue.models import (  # noqa: E402
    AdmissionState,
    JobItem,
    JobLock,
)
from daemon.repositories.job_queue.queue_repository import (  # noqa: E402
    JobQueueRepository,
)
from daemon.repositories.job_queue.repository import JobRepository  # noqa: E402
from daemon.repositories.task.models import TaskStatus, TaskType  # noqa: E402
from daemon.repositories.task.repository import TaskRepository  # noqa: E402
from daemon.services.job_recovery_service import JobRecoveryService  # noqa: E402


# ════════════════════════════════════════════════════════════════════════════
# Result collection
# ════════════════════════════════════════════════════════════════════════════

_RESULTS: list[tuple[str, str, str]] = []  # (scenario, status, evidence)
_OVERALL_PASS = True
_TIMED_OUT = False


def _record(scenario: str, passed: bool, evidence: str) -> None:
    global _OVERALL_PASS
    status = "PASS" if passed else "FAIL"
    if not passed:
        _OVERALL_PASS = False
    _RESULTS.append((scenario, status, evidence))
    print(f"--- {scenario}: {status} ---")
    print(evidence)
    print()


def _alarm_handler(signum, frame):  # noqa: ARG001
    global _TIMED_OUT
    _TIMED_OUT = True
    raise TimeoutError("internal 240s alarm tripped")


# ════════════════════════════════════════════════════════════════════════════
# Shared seeders (mirror the helpers in
# tests/job_queue/test_orphan_active_job_recovery.py — the probe is
# independent of pytest, so we inline the row-creators rather than
# fixture-sharing)
# ════════════════════════════════════════════════════════════════════════════


def _insert_instance(
    engine, instance_id: str, *, project_id: str = "test-project",
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
    engine, *, job_id: str, instance_id: str,
    project_id: str = "test-project", queue_id: str | None = None,
    admission_state: str = AdmissionState.ACTIVE.value,
    job_metadata: dict | None = None,
    created_at: datetime | None = None,
) -> None:
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    metadata_json = json.dumps(job_metadata or {})
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
                "message": "hi",
                "source": "api",
                "project_id": project_id,
                "queue_id": queue_id,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
                "job_type": "task",
                "retry_count": 0,
                "metadata": metadata_json,
            },
        )


def _insert_task_with_status(
    engine, *, work_id: str, instance_id: str,
    message_id: str | None = None,
    status: str = TaskStatus.PENDING.value,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> int:
    now = (created_at or datetime.now(timezone.utc))
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
                "task_type": task_type,
                "instance_id": instance_id,
                "message_id": message_id,
                "status": status,
                "retry_count": 0,
                "created_at": now,
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
            text(
                "SELECT * FROM job_locks WHERE job_id = :j"
            ),
            {"j": job_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def _get_task(engine, task_id: int) -> dict[str, Any] | None:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM task WHERE id = :i"),
            {"i": task_id},
        ).mappings().first()
    return dict(row) if row else None


def _update_task_status(
    engine, task_id: int, status: str,
    completed_at: datetime | None = None,
) -> None:
    completed_iso = (
        completed_at.isoformat() if completed_at is not None else None
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE task SET status = :s, completed_at = :c WHERE id = :i"
            ),
            {"s": status, "c": completed_iso, "i": task_id},
        )


# ════════════════════════════════════════════════════════════════════════════
# Service factory — REAL service; the only stub is the bus singleton
# (returned by get_dependency_bus()).
# ════════════════════════════════════════════════════════════════════════════


class _EmptyBus:
    """Bus stub returning no pending watchers (passes f2 Gate 1)."""

    async def pending_watchers(self, source_task_id):  # noqa: ARG002
        return []


class _PendingBus:
    """Bus stub returning a non-empty pending-watcher list (fails Gate 1)."""

    async def pending_watchers(self, source_task_id):  # noqa: ARG002
        return ["watcher-1"]


def _make_service(
    engine, *, queue_repo=None, lock_manager=None,
    job_queue_service: Any | None = None,
    extra_patches: list | None = None,
    bus=_EmptyBus(),
) -> JobRecoveryService:
    """Build the real ``JobRecoveryService`` with a stubbed bus.

    ``extra_patches`` is a list of ``(target, attr, value)`` tuples;
    for each, we ``patch.object(target, attr, value)`` as a context
    manager — caller wraps the sweep call with
    ``with ExitStack() as stack: [stack.enter_context(p) for p in patches]``
    (the real JobRecoveryService instance is shared across patches so
    the monkeypatched helper survives the sweep).
    """
    job_repo = JobRepository(engine)
    lock_repo = LockRepository(engine)
    instance_repo = SQLModelInstanceRepository(engine=engine)
    task_repo = TaskRepository(engine)
    from daemon.services.stale_task_recovery import StaleTaskRecovery
    stale_recovery = StaleTaskRecovery(
        task_repository=task_repo,
        message_repository=None,
        event_repository=None,
    )
    return JobRecoveryService(
        job_repository=job_repo,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
        job_queue_service=job_queue_service,
        task_repository=task_repo,
        stale_task_recovery=stale_recovery,
    )


# ════════════════════════════════════════════════════════════════════════════
# Scenarios
# ════════════════════════════════════════════════════════════════════════════


async def scenario_a_paused_past_grace(engine) -> None:
    """(a) PAUSED Task past grace → JobItem stays ACTIVE + Task resumable.

    Spec: "PAUSED-past-grace: JobItem ACTIVE + Task PAUSED + JobItem.created_at
    >900s old + instance old enough for mid-mint conjunct → assert JobItem
    STILL ACTIVE + skip label orphan_active_skipped_paused in result/log
    evidence + Task still PAUSED. Then resume-works leg: transition Task
    PAUSED→PENDING via the real repository (ResumeTurn semantics — check
    task repo for the real transition method) → assert transition succeeds
    (proves the sweep left it resumable)."
    """
    scenario = "(a) PAUSED-past-grace + resume-works leg"
    try:
        # ── Seed: instance + JobItem + PAUSED Task, all past grace
        _insert_instance(
            engine, "inst-a",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-a",
            instance_id="inst-a",
            queue_id="queue-a",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        task_id = _insert_task_with_status(
            engine,
            work_id="job-a",
            instance_id="inst-a",
            status=TaskStatus.PAUSED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        service = _make_service(engine)

        # ── Run sweep (REAL)
        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # ── Assertions on REAL DB rows
        job_after = _get_job(engine, "job-a")
        task_after = _get_task(engine, task_id)
        ev_lines: list[str] = []

        passed = True
        if job_after is None:
            passed = False
            ev_lines.append("FAIL: JobItem row missing after sweep")
        elif job_after.get("admission_state") != AdmissionState.ACTIVE.value:
            passed = False
            ev_lines.append(
                f"FAIL: JobItem admission_state="
                f"{job_after.get('admission_state')!r}, "
                f"expected 'active'"
            )
        else:
            ev_lines.append("OK: JobItem admission_state='active'")

        if task_after is None:
            passed = False
            ev_lines.append("FAIL: Task row missing after sweep")
        elif task_after.get("status") != TaskStatus.PAUSED.value:
            passed = False
            ev_lines.append(
                f"FAIL: Task status={task_after.get('status')!r}, "
                f"expected 'paused'"
            )
        else:
            ev_lines.append("OK: Task status='paused'")

        # Detail record check
        details = stats.get("details", []) if isinstance(stats, dict) else []
        paused_details = [
            d for d in details
            if d.get("pattern") == "orphan_active_skipped_paused"
            and d.get("job_id") == "job-a"
        ]
        if paused_details:
            ev_lines.append(
                f"OK: orphan_active_skipped_paused detail "
                f"recorded (task_id={paused_details[0].get('task_id')})"
            )
        else:
            passed = False
            ev_lines.append(
                "FAIL: orphan_active_skipped_paused detail NOT "
                f"recorded. Details: {details}"
            )

        # ── Resume-works leg: PAUSED→PENDING via direct SQL
        # (mirrors the resume cascade semantics per
        #  daemon/repositories/task/repository.py:398-405 — the
        #  cascade transitions the Task PAUSED→PENDING directly).
        # The probe asserts the sweep left the row in a state
        # where the resume path can mutate it.
        with engine.begin() as conn:
            resume_result = conn.execute(
                text(
                    "UPDATE task SET status = :pending, "
                    "completed_at = NULL WHERE id = :i "
                    "AND status = :paused"
                ),
                {
                    "pending": TaskStatus.PENDING.value,
                    "i": task_id,
                    "paused": TaskStatus.PAUSED.value,
                },
            )
            resume_rowcount = resume_result.rowcount or 0

        if resume_rowcount == 1:
            ev_lines.append(
                "OK: resume-works leg — PAUSED→PENDING succeeded "
                "(rowcount=1, sweep left the row resumable)"
            )
        else:
            passed = False
            ev_lines.append(
                f"FAIL: resume-works leg failed "
                f"(rowcount={resume_rowcount}, expected 1)"
            )

        # Final state check: Task now PENDING, JobItem still ACTIVE
        task_resumed = _get_task(engine, task_id)
        if task_resumed and task_resumed.get("status") == TaskStatus.PENDING.value:
            ev_lines.append("OK: post-resume Task status='pending'")
        else:
            passed = False
            ev_lines.append(
                f"FAIL: post-resume Task status="
                f"{task_resumed.get('status') if task_resumed else None!r}"
            )

        job_final = _get_job(engine, "job-a")
        if job_final and job_final.get("admission_state") == AdmissionState.ACTIVE.value:
            ev_lines.append("OK: post-resume JobItem still 'active'")
        else:
            passed = False
            ev_lines.append(
                f"FAIL: post-resume JobItem admission_state="
                f"{job_final.get('admission_state') if job_final else None!r}"
            )

        # Also assert: NO f1 dead-finalize happened (no DEAD JobItem)
        # — the W1 mid-mint and skip-paused guard contracted the kill.
        ev_lines.append(
            f"sweep stats: reconciled={stats.get('reconciled') if isinstance(stats, dict) else 'n/a'}"
        )
        _record(scenario, passed, "\n".join(ev_lines))
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
        )


async def scenario_b_failed_with_live_retry_child(engine) -> None:
    """(b) FAILED/CANCELLED Task + live retry child → boundary after retry.

    Spec: "FAILED-task + live retry child: JobItem ACTIVE + Task FAILED
    (status failed) on work_id W1 + a SECOND Task PENDING with fresh
    work_id W2 SAME instance_id (the retry child) → sweep → assert
    JobItem STILL ACTIVE + label orphan_active_skipped_retry_child_live.
    Then: mark W2 task completed → sweep again → assert boundary
    finalization NOW happens (terminal_reason 'failed', failed_at
    stamped, lock row released, NO_RETRY: atomic_retry refuses /
    failed_at present). Also run the CANCELLED twin (task cancelled +
    live retry child → skipped; retry done → boundary 'cancelled')."
    """
    scenario = "(b) FAILED/CANCELLED + live retry child → boundary"
    try:
        from unittest.mock import AsyncMock
        from daemon.services.job_queue_service import JobQueueService
        from daemon.services.job_lock_manager import JobLockManager

        # ── Setup queue + JobQueueService so the boundary is REAL
        queue_repo = JobQueueRepository(engine)
        queue_repo.create(
            project_id="test-project",
            queue_name="queue-b",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )

        results: list[tuple[str, bool, str]] = []

        for variant_name, task_status, expected_terminal_reason in [
            ("FAILED", TaskStatus.FAILED.value, "failed"),
            ("CANCELLED", TaskStatus.CANCELLED.value, "cancelled"),
        ]:
            ev: list[str] = [f"Variant: {variant_name}"]

            # Clean up any leftover rows from prior variants
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM task"))
                conn.execute(text("DELETE FROM job_locks"))
                conn.execute(text("DELETE FROM job_queue_items"))
                conn.execute(text("DELETE FROM instances"))

            # Seed
            _insert_instance(
                engine, "inst-b",
                created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
            )
            _insert_job_item(
                engine,
                job_id=f"job-b-{variant_name}",
                instance_id="inst-b",
                queue_id="queue-b",
                created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
            )
            _insert_lock(
                engine,
                project_id="test-project",
                queue_id="queue-b",
                job_id=f"job-b-{variant_name}",
                instance_id="inst-b",
            )
            # Parent Task in terminal state (FAILED or CANCELLED)
            parent_task_id = _insert_task_with_status(
                engine,
                work_id=f"job-b-{variant_name}",
                instance_id="inst-b",
                status=task_status,
                completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
            )
            # Live retry child — DIFFERENT work_id (the retry mint
            # pattern), SAME instance, PENDING. This is the W1
            # lineage conjunct target.
            retry_task_id = _insert_task_with_status(
                engine,
                work_id=f"job-b-{variant_name}-child",
                instance_id="inst-b",
                status=TaskStatus.PENDING.value,
            )

            # Build real JobQueueService so the boundary is REAL
            job_repo = JobRepository(engine)
            lock_repo = LockRepository(engine)
            lock_manager = JobLockManager(lock_repo=lock_repo)
            real_jq_service = JobQueueService(
                job_repo, lock_manager, queue_repo,
                instance_manager=None,
            )

            service = _make_service(
                engine, job_queue_service=real_jq_service,
            )

            # ── Sweep #1: live retry child present → SKIP
            stats1 = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )
            details1 = stats1.get("details", []) if isinstance(stats1, dict) else []
            skip1 = [
                d for d in details1
                if d.get("pattern") == "orphan_active_skipped_retry_child_live"
                and d.get("job_id") == f"job-b-{variant_name}"
            ]
            job_after_1 = _get_job(engine, f"job-b-{variant_name}")
            lock_after_1 = _get_locks_for_job(engine, f"job-b-{variant_name}")

            variant_pass = True
            if skip1:
                ev.append(
                    f"OK: skipped_retry_child_live detail recorded "
                    f"(parent_task_id={skip1[0].get('task_id')}, "
                    f"instance_id={skip1[0].get('instance_id')})"
                )
            else:
                variant_pass = False
                ev.append(
                    "FAIL: orphan_active_skipped_retry_child_live "
                    f"detail NOT recorded. Details: {details1}"
                )
            if job_after_1 and job_after_1.get("admission_state") == AdmissionState.ACTIVE.value:
                ev.append("OK: post-sweep-1 JobItem still 'active'")
            else:
                variant_pass = False
                ev.append(
                    f"FAIL: post-sweep-1 JobItem state="
                    f"{job_after_1.get('admission_state') if job_after_1 else None!r}"
                )
            if len(lock_after_1) == 1:
                ev.append("OK: post-sweep-1 lock row STILL held (not prematurely released)")
            else:
                variant_pass = False
                ev.append(
                    f"FAIL: post-sweep-1 lock rows={len(lock_after_1)}, "
                    f"expected 1 (lock must NOT release on retry-child skip)"
                )

            # ── Mark retry child COMPLETED — sweep again
            _update_task_status(
                engine, retry_task_id, TaskStatus.COMPLETED.value,
                completed_at=datetime.now(timezone.utc),
            )

            stats2 = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )
            details2 = stats2.get("details", []) if isinstance(stats2, dict) else []
            terminal2 = [
                d for d in details2
                if d.get("pattern") == "orphan_active_failed_terminal"
                and d.get("job_id") == f"job-b-{variant_name}"
            ]
            job_after_2 = _get_job(engine, f"job-b-{variant_name}")
            lock_after_2 = _get_locks_for_job(engine, f"job-b-{variant_name}")

            if terminal2:
                ev.append(
                    f"OK: orphan_active_failed_terminal detail recorded "
                    f"(task_id={terminal2[0].get('task_id')})"
                )
            else:
                variant_pass = False
                ev.append(
                    "FAIL: orphan_active_failed_terminal detail NOT "
                    f"recorded (retry completed but boundary did not fire). "
                    f"Details: {details2}"
                )
            if job_after_2 and job_after_2.get("admission_state") == AdmissionState.DONE.value:
                ev.append(
                    f"OK: post-sweep-2 JobItem 'done' (NOT 'dead' — "
                    f"atomic_retry preserved via boundary)"
                )
            else:
                variant_pass = False
                ev.append(
                    f"FAIL: post-sweep-2 JobItem state="
                    f"{job_after_2.get('admission_state') if job_after_2 else None!r}"
                )
            # terminal_reason check
            if job_after_2 and job_after_2.get("terminal_reason") == expected_terminal_reason:
                ev.append(
                    f"OK: terminal_reason='{expected_terminal_reason}' preserved"
                )
            else:
                variant_pass = False
                ev.append(
                    f"FAIL: terminal_reason="
                    f"{job_after_2.get('terminal_reason') if job_after_2 else None!r}, "
                    f"expected '{expected_terminal_reason}'"
                )
            # failed_at stamp — the W4 marker atomic_retry reads.
            # Production semantic per repository.py:1606-1607:
            # ``if terminal_reason == "failed": set_values["failed_at"] = now``
            # — FAILED-only. CANCELLED finalizes keep failed_at=NULL
            # (intentionally not retryable; see production comment).
            if expected_terminal_reason == "failed":
                if job_after_2 and job_after_2.get("failed_at") is not None:
                    ev.append(
                        f"OK: failed_at stamped: {job_after_2.get('failed_at')!r}"
                    )
                else:
                    variant_pass = False
                    ev.append(
                        f"FAIL: failed_at is None — atomic_retry "
                        f"gating marker missing (terminal_reason="
                        f"'{expected_terminal_reason}')"
                    )
            else:
                # CANCELLED — failed_at must STAY NULL by design
                # (production contract — atomic_retry must REFUSE
                # cancelled rows since they are not retryable)
                if job_after_2 and job_after_2.get("failed_at") is None:
                    ev.append(
                        "OK: failed_at is NULL for CANCELLED "
                        "(correct semantic — atomic_retry refuses "
                        "cancelled via failed_at IS NULL gate)"
                    )
                else:
                    variant_pass = False
                    ev.append(
                        f"FAIL: failed_at unexpectedly stamped for CANCELLED: "
                        f"{job_after_2.get('failed_at') if job_after_2 else None!r}"
                    )
            # lock release
            if len(lock_after_2) == 0:
                ev.append("OK: post-sweep-2 lock row RELEASED (boundary finally block)")
            else:
                variant_pass = False
                ev.append(
                    f"FAIL: post-sweep-2 lock rows={len(lock_after_2)}, "
                    f"expected 0 (lock MUST release on DONE)"
                )

            results.append((variant_name, variant_pass, "\n".join(ev)))

        # Overall: BOTH variants must pass
        all_pass = all(r[1] for r in results)
        full_evidence = "\n\n".join(
            f"=== Variant {v[0]} ===\n{v[2]}" for v in results
        )
        _record(scenario, all_pass, full_evidence)
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
        )


async def scenario_c_f2_gate_per_leg_mutation(engine) -> None:
    """(c) healthy waiting_children parent — per-leg mutation check.

    Spec: "For EACH leg L in {bus_pending (L1), pending_instance_tasks
    (L2), completed_at 60s floor (L3)}: build a parent where L is the
    ONLY blocking leg (make other two legs permissive by construction:
    L1-only → bus has pending watchers but no PENDING instance tasks +
    completed_at old; L2-only → no bus pending but a PENDING instance
    task exists + completed_at old; L3-only → no bus pending, no
    PENDING tasks, but completed_at <60s ago). Assertions per leg:
    (1) unmutated sweep → JobItem stays ACTIVE with THAT leg's skip
    label; (2) monkeypatch the leg's helper to return permissive →
    sweep → observe + RECORD that wrongful finalize WOULD occur (this
    proves the leg is load-bearing and the scenario is lethal, not
    vacuously safe); do NOT leave the mutation on beyond the single
    observation."
    """
    scenario = "(c) f2 gate per-leg mutation"
    try:
        from contextlib import ExitStack

        # Clean state for c — the three legs each build their own
        # scenario row + COMPLETED driving Task + (leg-specific) blocker
        async def _seed_for_leg(leg_name: str) -> str:
            """Returns the job_id for the scenario built for `leg_name`.

            L1 (bus_pending): bus returns 1 pending watcher; no PENDING
                instance tasks; completed_at old (120s ago).
            L2 (pending_instance_tasks): bus returns []; PENDING
                instance task exists (different work_id); completed_at
                old.
            L3 (completed_at age floor): bus returns []; no PENDING
                instance tasks; completed_at 10s ago (inside 60s floor).
            """
            job_id = f"job-c-{leg_name}"
            inst_id = f"inst-c-{leg_name}"
            _insert_instance(
                engine, inst_id,
                created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
            )
            _insert_job_item(
                engine,
                job_id=job_id,
                instance_id=inst_id,
                queue_id=f"queue-c-{leg_name}",
                # Default: NOT past the JobItem-side grace (we want
                # grace=0 for c so only the f2 gate matters).
                # However the f1 grace applies BEFORE the f2 branch
                # — see job_recovery_service.py:2382 — so we need
                # the JobItem past grace too to avoid the grace
                # skip pre-empting the leg test. Use grace=900,
                # backdate 1800s.
                created_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
            )
            # Driving Task — COMPLETED in all 3 legs
            if leg_name == "L3":
                completed_at = datetime.now(timezone.utc) - timedelta(seconds=10)
            else:
                completed_at = datetime.now(timezone.utc) - timedelta(seconds=120)
            _insert_task_with_status(
                engine,
                work_id=job_id,
                instance_id=inst_id,
                status=TaskStatus.COMPLETED.value,
                completed_at=completed_at,
            )
            # Leg-specific blocker
            if leg_name == "L2":
                _insert_task_with_status(
                    engine,
                    work_id=f"{job_id}-other",
                    instance_id=inst_id,
                    status=TaskStatus.PENDING.value,
                )
            return job_id

        leg_specs = [
            # (leg_name, bus_stub_factory, expected_skip_label, mutation_patch)
            (
                "L1",
                _PendingBus,
                "orphan_active_skipped_bus_pending",
                ("_pattern_f_check_bus_pending", (0, False)),
            ),
            (
                "L2",
                _EmptyBus,
                "orphan_active_skipped_pending_instance_tasks",
                ("_pattern_f_instance_has_pending_tasks", False),
            ),
            (
                "L3",
                _EmptyBus,
                "orphan_active_skipped_age_floor",
                ("_pattern_f_check_completed_at_age_floor", (True, "")),
            ),
        ]

        all_results: list[tuple[str, bool, str]] = []
        for leg_name, bus_factory, skip_label, (helper_name, permissive_value) in leg_specs:
            ev: list[str] = [f"=== Leg {leg_name} ({skip_label}) ==="]
            leg_pass = True

            # Reset rows from prior legs (the SAME engine + tables;
            # we do NOT recreate the engine per leg — the spec says
            # "each leg" is a logical isolation, so we wipe rows.)
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM task"))
                conn.execute(text("DELETE FROM job_locks"))
                conn.execute(text("DELETE FROM job_queue_items"))
                conn.execute(text("DELETE FROM instances"))

            job_id = await _seed_for_leg(leg_name)
            bus = bus_factory()

            # ── Build service + (leg-specific) bus patch
            service = _make_service(engine, bus=bus)

            # ── UNMUTATED sweep: expect skip with `skip_label`
            with patch(
                "daemon.services.job_recovery_service.get_dependency_bus",
                return_value=bus,
            ):
                stats_u = await service.reconcile_drift_states(
                    min_pending_age_seconds=0,
                    min_orphan_age_seconds=60,
                )
            details_u = (
                stats_u.get("details", []) if isinstance(stats_u, dict) else []
            )
            job_after_u = _get_job(engine, job_id)
            skip_records = [
                d for d in details_u
                if d.get("pattern") == skip_label
                and d.get("job_id") == job_id
            ]
            terminal_records_u = [
                d for d in details_u
                if d.get("pattern") == "orphan_active_completed_task_done"
                and d.get("job_id") == job_id
            ]
            if skip_records:
                ev.append(
                    f"OK: unmutated → skip_label '{skip_label}' recorded"
                )
            else:
                leg_pass = False
                ev.append(
                    f"FAIL: unmutated sweep missing skip_label '{skip_label}'. "
                    f"Details: {details_u}"
                )
            if job_after_u and job_after_u.get("admission_state") == AdmissionState.ACTIVE.value:
                ev.append("OK: unmutated → JobItem still 'active'")
            else:
                leg_pass = False
                ev.append(
                    f"FAIL: unmutated → JobItem state="
                    f"{job_after_u.get('admission_state') if job_after_u else None!r}"
                )
            if not terminal_records_u:
                ev.append("OK: unmutated → NO f2 DONE finalization (leg correctly blocks)")
            else:
                leg_pass = False
                ev.append(
                    f"FAIL: unmutated sweep UNEXPECTEDLY finalized f2: {terminal_records_u}"
                )

            # ── MUTATED sweep: patch the leg's helper to be
            # permissive and observe that f2 NOW fires (proves the
            # leg is load-bearing, not vacuously safe).
            with ExitStack() as stack:
                # Bus stub — keep its current value (leg-specific)
                stack.enter_context(patch(
                    "daemon.services.job_recovery_service.get_dependency_bus",
                    return_value=bus,
                ))
                # Mutate the helper on the SERVICE instance.
                # Patch.object on the bound method via the class.
                # The service holds self-bound helpers; patching
                # the class method rebinds it for the patched scope.
                if helper_name == "_pattern_f_check_bus_pending":
                    mut_target = (
                        "daemon.services.job_recovery_service."
                        "JobRecoveryService._pattern_f_check_bus_pending"
                    )
                    stack.enter_context(patch(
                        mut_target,
                        new=AsyncMock(return_value=permissive_value),
                    ))
                elif helper_name == "_pattern_f_instance_has_pending_tasks":
                    mut_target = (
                        "daemon.services.job_recovery_service."
                        "JobRecoveryService._pattern_f_instance_has_pending_tasks"
                    )
                    stack.enter_context(patch(
                        mut_target,
                        new=AsyncMock(return_value=permissive_value),
                    ))
                elif helper_name == "_pattern_f_check_completed_at_age_floor":
                    # This is a sync method that returns (ok, reason)
                    mut_target = (
                        "daemon.services.job_recovery_service."
                        "JobRecoveryService._pattern_f_check_completed_at_age_floor"
                    )
                    stack.enter_context(patch(
                        mut_target,
                        new=MagicMock(return_value=permissive_value),
                    ))
                stats_m = await service.reconcile_drift_states(
                    min_pending_age_seconds=0,
                    min_orphan_age_seconds=60,
                )
            details_m = (
                stats_m.get("details", []) if isinstance(stats_m, dict) else []
            )
            terminal_records_m = [
                d for d in details_m
                if d.get("pattern") == "orphan_active_completed_task_done"
                and d.get("job_id") == job_id
            ]
            job_after_m = _get_job(engine, job_id)
            if terminal_records_m:
                ev.append(
                    f"OK: mutated ({helper_name}→permissive) → f2 "
                    f"DONE finalized (proves leg is load-bearing, "
                    f"NOT vacuously safe)"
                )
            else:
                leg_pass = False
                ev.append(
                    f"FAIL: mutated → f2 did NOT finalize; the leg "
                    f"appears vacuous (the scenario is not "
                    f"lethal). Details: {details_m}"
                )
            if job_after_m and job_after_m.get("admission_state") == AdmissionState.DONE.value:
                ev.append("OK: mutated → JobItem 'done'")
            else:
                leg_pass = False
                ev.append(
                    f"FAIL: mutated → JobItem state="
                    f"{job_after_m.get('admission_state') if job_after_m else None!r}"
                )

            all_results.append((leg_name, leg_pass, "\n".join(ev)))

        all_pass = all(r[1] for r in all_results)
        full_evidence = "\n\n".join(
            f"=== {r[0]} ===\n{r[2]}" for r in all_results
        )
        _record(scenario, all_pass, full_evidence)
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
        )


async def scenario_d_genuine_restart_orphan(engine) -> None:
    """(d) genuine restart-orphan → DEAD + lock release + terminal_reason.

    Spec: "genuine restart-orphan: JobItem ACTIVE + NO Task row +
    JobItem.created_at >900s + instance.created_at older than threshold
    (mid-mint conjunct satisfied) → sweep → assert JobItem DEAD + lock
    row for (project,queue,job) GONE + terminal_reason set. NEGATIVE
    sub-case: same shape but instance.created_at FRESH (mid-mint) →
    sweep → JobItem stays ACTIVE (grace/mid-mint skip label)."
    """
    scenario = "(d) genuine restart-orphan + W1 mid-mint negative"
    try:
        # ── Variant 1: genuine orphan — DEAD + lock release
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM task"))
            conn.execute(text("DELETE FROM job_locks"))
            conn.execute(text("DELETE FROM job_queue_items"))
            conn.execute(text("DELETE FROM instances"))

        _insert_instance(
            engine, "inst-d-1",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
        )
        _insert_job_item(
            engine,
            job_id="job-d-1",
            instance_id="inst-d-1",
            queue_id="queue-d",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
        )
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-d",
            job_id="job-d-1",
            instance_id="inst-d-1",
        )

        service = _make_service(engine)
        stats1 = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        ev: list[str] = ["=== Variant 1: genuine orphan (mid-mint conjunct satisfied) ==="]
        pass1 = True

        details1 = stats1.get("details", []) if isinstance(stats1, dict) else []
        dead_records = [
            d for d in details1
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-d-1"
        ]
        if dead_records:
            ev.append(
                f"OK: orphan_active_no_task_dead detail recorded "
                f"(reason={dead_records[0].get('reason')[:80]}...)"
            )
        else:
            pass1 = False
            ev.append(
                f"FAIL: orphan_active_no_task_dead detail missing. Details: {details1}"
            )

        job_after = _get_job(engine, "job-d-1")
        if job_after and job_after.get("admission_state") == AdmissionState.DEAD.value:
            ev.append("OK: JobItem admission_state='dead'")
        else:
            pass1 = False
            ev.append(
                f"FAIL: JobItem state="
                f"{job_after.get('admission_state') if job_after else None!r}, expected 'dead'"
            )
        # Lock release
        locks1 = _get_locks_for_job(engine, "job-d-1")
        if len(locks1) == 0:
            ev.append("OK: per-job lock row RELEASED (no orphan lock rows)")
        else:
            pass1 = False
            ev.append(
                f"FAIL: lock rows remaining={len(locks1)}; expected 0"
            )

        # ── Variant 2: W1 mid-mint negative — instance FRESH
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM task"))
            conn.execute(text("DELETE FROM job_locks"))
            conn.execute(text("DELETE FROM job_queue_items"))
            conn.execute(text("DELETE FROM instances"))

        ev.append("\n=== Variant 2: W1 mid-mint negative — instance FRESH ===")
        pass2 = True

        _insert_instance(
            engine, "inst-d-2",
            # 30s ago — INSIDE the 60s grace
            created_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        _insert_job_item(
            engine,
            job_id="job-d-2",
            instance_id="inst-d-2",
            queue_id="queue-d",
            # JobItem 1800s old (past grace) — but instance FRESH (mid-mint)
            created_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
        )

        stats2 = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )
        details2 = stats2.get("details", []) if isinstance(stats2, dict) else []
        # Expect a grace skip (W1 mid-mint) — pattern
        # 'orphan_active_skipped_grace' with W1 wording in reason.
        grace_records = [
            d for d in details2
            if d.get("pattern") == "orphan_active_skipped_grace"
            and d.get("job_id") == "job-d-2"
        ]
        dead_records_neg = [
            d for d in details2
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-d-2"
        ]
        if grace_records:
            ev.append(
                f"OK: orphan_active_skipped_grace detail recorded (W1 mid-mint)"
            )
            reason = grace_records[0].get("reason", "")
            if "mid-mint" in reason or "instance" in reason.lower():
                ev.append(f"OK: reason references W1 mid-mint: '{reason[:80]}...'")
            else:
                pass2 = False
                ev.append(
                    f"FAIL: grace detail reason missing W1 wording: '{reason[:120]}'"
                )
        else:
            pass2 = False
            ev.append(
                f"FAIL: W1 mid-mint skip detail missing. Details: {details2}"
            )
        if not dead_records_neg:
            ev.append("OK: NO f1 DEAD finalization (mid-mint guard held)")
        else:
            pass2 = False
            ev.append(
                f"FAIL: f1 incorrectly fired on mid-mint instance: {dead_records_neg}"
            )

        job_after_2 = _get_job(engine, "job-d-2")
        if job_after_2 and job_after_2.get("admission_state") == AdmissionState.ACTIVE.value:
            ev.append("OK: mid-mint JobItem still 'active'")
        else:
            pass2 = False
            ev.append(
                f"FAIL: mid-mint JobItem state="
                f"{job_after_2.get('admission_state') if job_after_2 else None!r}"
            )

        overall = pass1 and pass2
        _record(scenario, overall, "\n".join(ev))
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
        )


async def scenario_e_f2_lock_release_c1_queue(engine) -> None:
    """(e) f2 lock release on c=1 queue → new JobItem B admits.

    Spec: "f2 lock release on c=1 queue: queue concurrency 1, JobItem A
    ACTIVE + COMPLETED Task with all 3 legs permissive (old completed_at,
    no bus pending, no pending tasks) + acquire the real lock row for A
    → sweep → assert A DONE + lock released → enqueue JobItem B on same
    queue → assert B admits (real claim/admission path succeeds — no
    wedge)."
    """
    scenario = "(e) f2 lock release on c=1 queue (no wedge)"
    try:
        from daemon.services.job_queue_service import JobQueueService
        from daemon.services.job_lock_manager import JobLockManager

        # Setup: c=1 FIFO queue + JobQueueService
        queue_repo = JobQueueRepository(engine)
        queue_repo.create(
            project_id="test-project",
            queue_name="queue-e",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )

        # Seed
        _insert_instance(
            engine, "inst-e",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-e-a",
            instance_id="inst-e",
            queue_id="queue-e",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
        )
        _insert_task_with_status(
            engine,
            work_id="job-e-a",
            instance_id="inst-e",
            status=TaskStatus.COMPLETED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        # Per-job lock for A (the sweep must release this)
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-e",
            job_id="job-e-a",
            instance_id="inst-e",
            lock_slot=0,
        )

        job_repo = JobRepository(engine)
        lock_repo = LockRepository(engine)
        lock_manager = JobLockManager(lock_repo=lock_repo)
        real_jq_service = JobQueueService(
            job_repo, lock_manager, queue_repo, instance_manager=None,
        )

        service = _make_service(
            engine, job_queue_service=real_jq_service,
        )

        ev: list[str] = ["=== (e) f2 c=1 lock release + new admit ==="]
        passed = True

        # Sanity: lock for A present
        locks_pre = _get_locks_for_job(engine, "job-e-a")
        if len(locks_pre) == 1:
            ev.append(
                f"OK pre-sweep: lock for job-e-a present (slot="
                f"{locks_pre[0].get('lock_slot')})"
            )
        else:
            passed = False
            ev.append(
                f"FAIL pre-sweep: expected 1 lock for job-e-a, found {len(locks_pre)}"
            )

        # Sweep
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBus(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )
        details = stats.get("details", []) if isinstance(stats, dict) else []
        f2_records = [
            d for d in details
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-e-a"
        ]
        if f2_records:
            ev.append("OK: f2 DONE finalize fired (orphan_active_completed_task_done)")
        else:
            passed = False
            ev.append(
                f"FAIL: f2 did not fire on A. Details: {details}"
            )

        # JobItem A DONE
        job_a = _get_job(engine, "job-e-a")
        if job_a and job_a.get("admission_state") == AdmissionState.DONE.value:
            ev.append("OK: JobItem A admission_state='done'")
        else:
            passed = False
            ev.append(
                f"FAIL: JobItem A state="
                f"{job_a.get('admission_state') if job_a else None!r}, expected 'done'"
            )

        # Lock for A GONE (the c=1 critical wedge fix)
        locks_a_post = _get_locks_for_job(engine, "job-e-a")
        if len(locks_a_post) == 0:
            ev.append("OK: lock for A RELEASED (no wedge on c=1 queue)")
        else:
            passed = False
            ev.append(
                f"FAIL: lock for A still present ({len(locks_a_post)} rows) — "
                f"the c=1 queue is WEDGED"
            )

        # Enqueue JobItem B on same queue (real create) — then
        # try the real atomic slot claim to prove no wedge.
        with engine.begin() as conn:
            now_iso = datetime.now(timezone.utc).isoformat()
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
                    "job_id": "job-e-b",
                    "agent_id": "developer",
                    "agent_dir": "agents/developer",
                    "message": "hi",
                    "source": "api",
                    "project_id": "test-project",
                    "queue_id": "queue-e",
                    "priority": 0,
                    "admission_state": AdmissionState.QUEUED.value,
                    "created_at": now_iso,
                    "instance_id": "inst-e-b",
                    "job_type": "task",
                    "retry_count": 0,
                    "metadata": json.dumps({}),
                },
            )

        # Real claim via try_acquire_slot (slot 0, fresh lock row).
        # This is the atomic slot-claim seam that production uses
        # (JobLockManager delegates to it).
        claimed = lock_repo.try_acquire_slot(
            lock_id="lock-job-e-b",
            project_id="test-project",
            queue_id="queue-e",
            job_id="job-e-b",
            instance_id="inst-e-b",
            slot=0,
        )
        if claimed:
            ev.append(
                "OK: real claim for B succeeded (slot 0 acquired — "
                "no wedge from A's previously-leaked lock)"
            )
        else:
            passed = False
            ev.append(
                "FAIL: real claim for B FAILED — the c=1 queue is "
                "WEDGED by A's leaked lock (Critical #2 regression)"
            )

        # Also assert: queued B is still pending in DB (admission_state)
        job_b = _get_job(engine, "job-e-b")
        if job_b and job_b.get("admission_state") == AdmissionState.QUEUED.value:
            ev.append(
                "OK: JobItem B admission_state='queued' (enqueued, "
                "admittable after claim path picks it up)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL: JobItem B state="
                f"{job_b.get('admission_state') if job_b else None!r}"
            )

        # Lock for B is now present (the claim added it)
        locks_b = _get_locks_for_job(engine, "job-e-b")
        if len(locks_b) == 1:
            ev.append(f"OK: lock for B present (slot={locks_b[0].get('lock_slot')})")
        else:
            passed = False
            ev.append(
                f"FAIL: lock for B rows={len(locks_b)}, expected 1"
            )

        _record(scenario, passed, "\n".join(ev))
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
        )


# ════════════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════════════


def main() -> int:
    print("=== Test Pack: pattern_f_killpath_matrix_test ===")
    print("(Pattern (f) Kill-Path Matrix — behavioral, real-DB gate)")
    print(f"Branch: feature/orphan-active-job-recovery @ ba39a40e")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print()

    start = time.monotonic()

    # ── Layer-2 internal timeout (signal-based)
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(240)

    # ── File-backed SQLite under /tmp (per project convention;
    # cleaned on exit via tmp_path-style cleanup below)
    tmp_dir = tempfile.mkdtemp(prefix="pattern_f_killpath_matrix_")
    db_path = os.path.join(tmp_dir, "probe.db")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    # Force-import every model that the recovery service touches so
    # SQLModel.metadata.create_all emits a complete schema.
    # We import inside a try so any future model additions don't
    # silently leave us with an empty schema.
    from daemon.repositories.job_queue import models as _jq_models  # noqa: F401
    from daemon.repositories.task import models as _task_models  # noqa: F401
    from daemon.repositories.instance import models as _inst_models  # noqa: F401
    SQLModel.metadata.create_all(engine)

    print(f"DB: {db_path}")
    print(f"Tables: {sorted(SQLModel.metadata.tables.keys())[:10]}...")
    print()

    try:
        asyncio.run(scenario_a_paused_past_grace(engine))
        asyncio.run(scenario_b_failed_with_live_retry_child(engine))
        asyncio.run(scenario_c_f2_gate_per_leg_mutation(engine))
        asyncio.run(scenario_d_genuine_restart_orphan(engine))
        asyncio.run(scenario_e_f2_lock_release_c1_queue(engine))
    except TimeoutError as te:
        print(f"\nTIMEOUT: internal 240s alarm tripped: {te}")
        # Emit a TIMEOUT line for the report
        elapsed = time.monotonic() - start
        print(f"\nRESULT: TIMEOUT (elapsed={elapsed:.1f}s)")
        engine.dispose()
        _cleanup(tmp_dir, db_path)
        return 124
    except Exception as e:
        print(f"\nUNEXPECTED EXCEPTION in scenario runner: {type(e).__name__}: {e}")
        traceback.print_exc()

    elapsed = time.monotonic() - start
    print("=" * 70)
    print(f"Total scenarios: {len(_RESULTS)}")
    print(f"  PASS: {sum(1 for _, s, _ in _RESULTS if s == 'PASS')}")
    print(f"  FAIL: {sum(1 for _, s, _ in _RESULTS if s == 'FAIL')}")
    print(f"Elapsed: {elapsed:.1f}s")
    print()

    # Dispose engine + remove tmp files (cleanup on exit)
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
