#!/usr/bin/env python3
"""Pattern (f) Fail-Safe Flips Probe — real-DB verification that lookup
errors now SKIP (not finalize).

Branch under test: feature/security-boundary-hygiene @ a77647bf/ac2c3091.

The batch flipped the Pattern (f) lineage-helper fail-safe direction
``False → True`` in ``daemon/services/job_recovery_service.py``:

* ``_pattern_f_instance_has_pending_tasks``  :3263 (no instance_id),
  :3265 (repo unwired), :3280 (exception path)
* ``_pattern_f_instance_has_inflight_task``  :3356 (no instance_id),
  :3358 (repo unwired) — the exception path (:3383) was already True

Semantics: ``True`` = "pending/live exists" → the sweep SKIPS the
candidate (JobItem stays ACTIVE, re-checked next drift cycle).
Previously ``False`` meant "nothing pending" → finalize (the
over-finalize risk adjudicated at the prior gate).

This probe drives the REAL
``JobRecoveryService._pattern_f_orphan_active_job_recovery`` sweep on
file-backed SQLite, asserting REAL DB rows (never mock return values).
Fixture patterns are reused from
``test/packs/pattern_f_killpath_matrix_test.py`` (the committed
prior-gate pack). Only the dependency-bus singleton is stubbed.

Scenarios:

  FS1  exception→skip: FAILED-task parent shape (the killpath scenario
       (b) pre-completion shape, NO retry child so a REAL lookup would
       return False → finalize); monkeypatch
       ``TaskRepository.has_instance_busy`` to RAISE
       RuntimeError('transient DB error') → sweep → JobItem STAYS
       ACTIVE + skip detail recorded + WARNING logged + NO finalize
       (no terminal_reason, no failed_at, lock intact).
  FS2  unwired-repo→skip: helper-level probe with
       ``task_repository=None`` (both helpers → True = skip direction,
       the :3265/:3358 flips) + sweep-level ACTIVE preservation with
       the repo unwired + real-sweep unmutated pending-helper leg
       (f2 Gate 2: genuine PENDING sibling → skip label intact).
  FS3  no-instance_id→skip: helper-level probe with ``None``
       (both helpers → True, the :3263/:3356 flips) + sweep-level
       ACTIVE preservation for a JobItem row with ``instance_id=NULL``
       (the :1828 sweep-level guard, upstream of the helpers).
  FS4  re-check-next-cycle: continuing FS1's rows — un-monkeypatch
       (restore the real lookup; the shape is now finalizable per the
       killpath scenario (b) completion leg) → sweep again → boundary
       finalization NOW proceeds (admission_state='done',
       terminal_reason='failed', failed_at stamped, lock released).
       Proves the FS1 skip was a deferral, not a wedge.
  FS5  healthy-shape negative: healthy ACTIVE JobItem + young PENDING
       Task is still skipped via the NORMAL path
       (orphan_active_skipped_healthy_shape) — the guard is unaffected
       by the flips.

Output contract (per scenario): PASS/FAIL line + key evidence rows.
Final line: ``RESULT: PASS|FAIL|TIMEOUT``; exit 0/1/124.

Self-contained. Internal 150s timeout via ``signal.alarm``; designed
to be wrapped with `timeout 300` by the .sh wrapper (dual-layer guard
per the test-pack skill).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

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

_RECOVERY_LOGGER_NAME = "daemon.services.job_recovery_service"


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
    raise TimeoutError("internal 150s alarm tripped")


class _LogCapture(logging.Handler):
    """Capture (level, message) records from the recovery-service logger."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append((record.levelno, record.getMessage()))
        except Exception:  # noqa: BLE001 — never break the sweep on logging
            pass


def _attach_log_capture() -> tuple[_LogCapture, int]:
    logger = logging.getLogger(_RECOVERY_LOGGER_NAME)
    old_level = logger.level
    cap = _LogCapture()
    logger.addHandler(cap)
    logger.setLevel(logging.DEBUG)
    return cap, old_level


def _detach_log_capture(cap: _LogCapture, old_level: int) -> None:
    logger = logging.getLogger(_RECOVERY_LOGGER_NAME)
    logger.removeHandler(cap)
    logger.setLevel(old_level)


# ════════════════════════════════════════════════════════════════════════════
# Shared seeders (mirrored from pattern_f_killpath_matrix_test.py —
# self-contained per that pack's no-fixture-sharing convention)
# ════════════════════════════════════════════════════════════════════════════


def _insert_instance(
    engine, instance_id: str | None, *, project_id: str = "test-project",
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
    engine, *, job_id: str, instance_id: str | None,
    project_id: str = "test-project", queue_id: str | None = None,
    admission_state: str = AdmissionState.ACTIVE.value,
    created_at: datetime | None = None,
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
                "metadata": json.dumps({}),
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
            text("SELECT * FROM job_locks WHERE job_id = :j"),
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
            text("UPDATE task SET status = :s, completed_at = :c WHERE id = :i"),
            {"s": status, "c": completed_iso, "i": task_id},
        )


def _details_for(stats: Any) -> list[dict[str, Any]]:
    return stats.get("details", []) if isinstance(stats, dict) else []


def _wipe_tables(engine) -> None:
    """Wipe all seed tables.

    ``reconcile_drift_states`` sweeps are WHOLE-DB (``find_processing_jobs``
    returns every ACTIVE JobItem), so each scenario/leg must run against a
    clean table set or a later scenario's real-repo sweep would finalize an
    earlier scenario's leftover rows (observed first run: FS2 leg (c)'s
    sweep terminal-routed FS1's job-fs1, breaking FS4's precondition).
    Mirrors the per-variant wipes in pattern_f_killpath_matrix_test.py.
    """
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM task"))
        conn.execute(text("DELETE FROM job_locks"))
        conn.execute(text("DELETE FROM job_queue_items"))
        conn.execute(text("DELETE FROM instances"))


# ════════════════════════════════════════════════════════════════════════════
# Service factory — REAL service; the only stub is the bus singleton
# (returned by get_dependency_bus()).
# ════════════════════════════════════════════════════════════════════════════


class _EmptyBus:
    """Bus stub returning no pending watchers (passes f2 Gate 1)."""

    async def pending_watchers(self, source_task_id):  # noqa: ARG002
        return []


def _make_service(
    engine,
    *,
    task_repository: TaskRepository | None = None,
    job_queue_service: Any | None = None,
) -> JobRecoveryService:
    """Build the real ``JobRecoveryService`` with a stubbed bus.

    ``task_repository=None`` reproduces the unwired-repo configuration
    (the FS2 probe); default builds the REAL ``TaskRepository(engine)``.
    """
    job_repo = JobRepository(engine)
    lock_repo = LockRepository(engine)
    instance_repo = SQLModelInstanceRepository(engine=engine)
    task_repo = (
        TaskRepository(engine) if task_repository is None else task_repository
    )
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


def _make_unwired_task_service(engine) -> JobRecoveryService:
    """Real service with ``task_repository=None`` (FS2 unwired-repo shape)."""
    job_repo = JobRepository(engine)
    lock_repo = LockRepository(engine)
    instance_repo = SQLModelInstanceRepository(engine=engine)
    from daemon.services.stale_task_recovery import StaleTaskRecovery
    stale_recovery = StaleTaskRecovery(
        task_repository=None,
        message_repository=None,
        event_repository=None,
    )
    return JobRecoveryService(
        job_repository=job_repo,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
        job_queue_service=None,
        task_repository=None,
        stale_task_recovery=stale_recovery,
    )


def _raise_transient(self, instance_id):  # noqa: ARG001 — class-level patch
    """Replacement for ``TaskRepository.has_instance_busy`` that simulates
    a transient DB error (the FS1 fail-safe path trigger)."""
    raise RuntimeError("transient DB error (FS1 fail-safe flips probe)")


# ════════════════════════════════════════════════════════════════════════════
# Scenarios
# ════════════════════════════════════════════════════════════════════════════


async def scenario_fs1_exception_skip(engine) -> None:
    """FS1 — exception→skip on the inflight-helper path.

    Seed the killpath scenario (b) PRE-completion shape (FAILED parent
    Task, NO retry child — so a REAL lookup would return False and the
    sweep would finalize). Monkeypatch ``has_instance_busy`` to RAISE.
    The flip at :3383 (already True) must turn the exception into a
    SKIP: JobItem STAYS ACTIVE + skip detail + WARNING logged + lock
    intact + no terminal writes.
    """
    scenario = "FS1 exception→skip (has_instance_busy raises)"
    try:
        _wipe_tables(engine)
        job_id = "job-fs1"
        inst_id = "inst-fs1"
        _insert_instance(
            engine, inst_id,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id=job_id,
            instance_id=inst_id,
            queue_id="queue-fs1",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-fs1",
            job_id=job_id,
            instance_id=inst_id,
        )
        # FAILED parent Task — terminal, so has_instance_busy would
        # return False on a REAL lookup (nothing PENDING/RUNNING/PAUSED).
        parent_task_id = _insert_task_with_status(
            engine,
            work_id=job_id,
            instance_id=inst_id,
            status=TaskStatus.FAILED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        service = _make_service(engine)

        ev: list[str] = []
        passed = True

        cap, old_level = _attach_log_capture()
        try:
            with (
                patch.object(
                    TaskRepository, "has_instance_busy", _raise_transient
                ),
                patch(
                    "daemon.services.job_recovery_service.get_dependency_bus",
                    return_value=_EmptyBus(),
                ),
            ):
                stats = await service.reconcile_drift_states(
                    min_pending_age_seconds=0,
                    min_orphan_age_seconds=60,
                )
        finally:
            _detach_log_capture(cap, old_level)

        details = _details_for(stats)
        job_after = _get_job(engine, job_id)
        task_after = _get_task(engine, parent_task_id)
        locks_after = _get_locks_for_job(engine, job_id)

        # 1. JobItem STAYS ACTIVE
        if job_after and job_after.get("admission_state") == "active":
            ev.append("OK: JobItem admission_state='active' (no finalize)")
        else:
            passed = False
            ev.append(
                f"FAIL: JobItem admission_state="
                f"{job_after.get('admission_state') if job_after else None!r}, "
                f"expected 'active'"
            )

        # 2. NO terminal writes
        if job_after and job_after.get("terminal_reason") is None:
            ev.append("OK: terminal_reason is NULL (no finalize)")
        else:
            passed = False
            ev.append(
                f"FAIL: terminal_reason="
                f"{job_after.get('terminal_reason') if job_after else None!r}, "
                f"expected None"
            )
        if job_after and job_after.get("failed_at") is None:
            ev.append("OK: failed_at is NULL (no finalize)")
        else:
            passed = False
            ev.append(
                f"FAIL: failed_at="
                f"{job_after.get('failed_at') if job_after else None!r}, "
                f"expected None"
            )

        # 3. Lock intact
        if len(locks_after) == 1:
            ev.append("OK: lock row STILL held (1 row — no release on skip)")
        else:
            passed = False
            ev.append(
                f"FAIL: lock rows={len(locks_after)}, expected 1 "
                f"(lock must NOT release on fail-safe skip)"
            )

        # 4. Skip detail recorded — record the label VERBATIM
        skip_records = [
            d for d in details
            if d.get("job_id") == job_id
            and str(d.get("pattern", "")).startswith("orphan_active_skipped")
        ]
        if skip_records:
            fired_label = skip_records[0].get("pattern")
            ev.append(
                f"OK: skip detail recorded — label (verbatim): {fired_label!r}"
            )
            if fired_label == "orphan_active_skipped_retry_child_live":
                ev.append(
                    "NOTE: the FAILED/CANCELLED branch has NO dedicated "
                    "fail-safe label — the fail-safe True reuses "
                    "'orphan_active_skipped_retry_child_live'; the "
                    "distinguishing signal is the helper-level WARNING "
                    "(captured below)."
                )
            else:
                passed = False
                ev.append(
                    f"FAIL: unexpected skip label {fired_label!r} "
                    f"(expected orphan_active_skipped_retry_child_live)"
                )
        else:
            passed = False
            ev.append(
                f"FAIL: no orphan_active_skipped* detail for {job_id}. "
                f"Details: {details}"
            )

        # 5. WARNING captured (helper-level fail-safe log)
        fs_warnings = [
            m for lvl, m in cap.records
            if lvl >= logging.WARNING
            and "has_instance_busy raised" in m
            and "FAIL-SAFE" in m
        ]
        if fs_warnings:
            ev.append(
                "OK: helper-level WARNING captured: "
                f"{fs_warnings[0][:160]}..."
            )
        else:
            passed = False
            ev.append(
                "FAIL: expected helper-level WARNING ('has_instance_busy "
                f"raised' + 'FAIL-SAFE') not found. Records: "
                f"{[m for _, m in cap.records]}"
            )

        # 6. Task row untouched
        if task_after and task_after.get("status") == TaskStatus.FAILED.value:
            ev.append("OK: Task still 'failed' (sweep did not mutate it)")
        else:
            passed = False
            ev.append(
                f"FAIL: Task status="
                f"{task_after.get('status') if task_after else None!r}"
            )

        _record(scenario, passed, "\n".join(ev))
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n{traceback.format_exc()}",
        )


async def scenario_fs2_unwired_repo_skip(engine) -> None:
    """FS2 — unwired-repo→skip (task_repository=None).

    Leg (a) HELPER-LEVEL flip probe: with the repo unwired both helpers
    must return True (skip direction) — the :3265/:3358 early returns.
    Leg (b) sweep-level: real sweep with repo unwired on a FAILED-parent
    shape → the sweep cannot fetch the Task (task=None) → routes f1 →
    instance alive + JobItem young → grace skip → ACTIVE preserved
    (no finalize anywhere). Leg (c) real-sweep unmutated pending-helper
    path (f2 Gate 2, genuine PENDING sibling) → skip label intact.
    """
    scenario = "FS2 unwired-repo→skip (task_repository=None)"
    try:
        _wipe_tables(engine)
        ev: list[str] = []
        passed = True

        # ── Leg (a): helper-level flip probe with repo unwired ──
        svc_unwired = _make_unwired_task_service(engine)
        pending_true = await svc_unwired._pattern_f_instance_has_pending_tasks(
            "inst-fs2-nonexistent"
        )
        inflight_true = (
            await svc_unwired._pattern_f_instance_has_inflight_task(
                "inst-fs2-nonexistent"
            )
        )
        if pending_true is True and inflight_true is True:
            ev.append(
                "OK: helper-level (task_repository=None): "
                "_pattern_f_instance_has_pending_tasks → True, "
                "_pattern_f_instance_has_inflight_task → True "
                "(skip direction — the :3265/:3358 flips)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL: helper-level with repo unwired: pending={pending_true!r} "
                f"inflight={inflight_true!r}, expected True/True"
            )

        # ── Leg (b): sweep-level with repo unwired → ACTIVE preserved ──
        job_id = "job-fs2"
        inst_id = "inst-fs2"
        _insert_instance(
            engine, inst_id,  # alive ('running'), YOUNG
        )
        _insert_job_item(
            engine,
            job_id=job_id,
            instance_id=inst_id,
            queue_id="queue-fs2",
            # YOUNG — with the repo unwired the row routes f1 and the
            # grace guard skips it; proves ACTIVE preservation without
            # any finalize on the unwired path.
        )
        _insert_task_with_status(
            engine,
            work_id=job_id,
            instance_id=inst_id,
            status=TaskStatus.FAILED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        svc_unwired2 = _make_unwired_task_service(engine)
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBus(),
        ):
            stats_b = await svc_unwired2.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )
        details_b = _details_for(stats_b)
        job_b = _get_job(engine, job_id)
        locks_b = _get_locks_for_job(engine, job_id)

        if job_b and job_b.get("admission_state") == "active":
            ev.append(
                "OK: sweep (repo unwired) — JobItem admission_state='active'"
            )
        else:
            passed = False
            ev.append(
                f"FAIL: sweep (repo unwired) JobItem state="
                f"{job_b.get('admission_state') if job_b else None!r}"
            )
        if job_b and job_b.get("terminal_reason") is None and job_b.get(
            "failed_at"
        ) is None:
            ev.append(
                "OK: sweep (repo unwired) — no terminal_reason, no failed_at"
            )
        else:
            passed = False
            ev.append(
                f"FAIL: terminal writes present: terminal_reason="
                f"{job_b.get('terminal_reason')!r}, failed_at="
                f"{job_b.get('failed_at')!r}"
            )
        if len(locks_b) == 0:
            ev.append(
                "OK: no lock row seeded/released (no lock churn on skip path)"
            )
        row_details = [d for d in details_b if d.get("job_id") == job_id]
        if row_details:
            ev.append(
                f"OK: sweep detail for {job_id} (verbatim label): "
                f"{row_details[0].get('pattern')!r} — reason: "
                f"{row_details[0].get('reason', '')[:90]}..."
            )
        else:
            ev.append(
                f"NOTE: no detail record for {job_id} (row untouched by "
                f"all patterns) — ACTIVE preservation is the assertion"
            )

        # ── Leg (c): real repo, unmutated pending-helper path (f2 G2) ──
        # Wipe first: leg (c)'s sweep is real-repo + whole-DB — without
        # the wipe it would sweep (and finalize) leg (b)'s leftover
        # job-fs2 row (observed on the first run).
        _wipe_tables(engine)
        job_c = "job-fs2c"
        inst_c = "inst-fs2c"
        _insert_instance(
            engine, inst_c,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id=job_c,
            instance_id=inst_c,
            queue_id="queue-fs2c",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
        )
        _insert_task_with_status(
            engine,
            work_id=job_c,
            instance_id=inst_c,
            status=TaskStatus.COMPLETED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        # Genuine PENDING sibling on the same instance (different
        # work_id) — Gate 2's real trigger.
        _insert_task_with_status(
            engine,
            work_id=f"{job_c}-other",
            instance_id=inst_c,
            status=TaskStatus.PENDING.value,
        )

        svc_real = _make_service(engine)
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBus(),
        ):
            stats_c = await svc_real.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )
        details_c = _details_for(stats_c)
        job_c_after = _get_job(engine, job_c)
        g2_records = [
            d for d in details_c
            if d.get("job_id") == job_c
            and d.get("pattern") == "orphan_active_skipped_pending_instance_tasks"
        ]
        if g2_records:
            ev.append(
                "OK: real-sweep pending-helper path (f2 Gate 2, unmutated) — "
                "label (verbatim): 'orphan_active_skipped_pending_instance_tasks'"
            )
        else:
            passed = False
            ev.append(
                f"FAIL: orphan_active_skipped_pending_instance_tasks NOT "
                f"recorded for {job_c}. Details: {details_c}"
            )
        if job_c_after and job_c_after.get("admission_state") == "active":
            ev.append(
                "OK: real-sweep pending-helper path — JobItem stays 'active'"
            )
        else:
            passed = False
            ev.append(
                f"FAIL: f2-leg JobItem state="
                f"{job_c_after.get('admission_state') if job_c_after else None!r}"
            )

        _record(scenario, passed, "\n".join(ev))
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n{traceback.format_exc()}",
        )


async def scenario_fs3_no_instance_id_skip(engine) -> None:
    """FS3 — no-instance_id→skip.

    Leg (a) HELPER-LEVEL flip probe: both helpers with ``None`` must
    return True (the :3263/:3356 flips). Leg (b) sweep-level: a REAL
    JobItem row with ``instance_id=NULL`` (old created_at — NOT a grace
    effect) survives the sweep ACTIVE via the :1828 sweep-level guard
    (debug-logged, upstream of the helpers, no detail record).
    """
    scenario = "FS3 no-instance_id→skip (instance_id=NULL)"
    try:
        _wipe_tables(engine)
        ev: list[str] = []
        passed = True

        # ── Leg (a): helper-level flip probe with None ──
        svc = _make_service(engine)
        pending_true = await svc._pattern_f_instance_has_pending_tasks(None)
        inflight_true = await svc._pattern_f_instance_has_inflight_task(None)
        if pending_true is True and inflight_true is True:
            ev.append(
                "OK: helper-level (instance_id=None): "
                "_pattern_f_instance_has_pending_tasks → True, "
                "_pattern_f_instance_has_inflight_task → True "
                "(skip direction — the :3263/:3356 flips)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL: helper-level with None: pending={pending_true!r} "
                f"inflight={inflight_true!r}, expected True/True"
            )

        # ── Leg (b): sweep-level with instance_id=NULL row ──
        job_id = "job-fs3"
        _insert_job_item(
            engine,
            job_id=job_id,
            instance_id=None,  # the probe target
            queue_id="queue-fs3",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )

        cap, old_level = _attach_log_capture()
        try:
            with patch(
                "daemon.services.job_recovery_service.get_dependency_bus",
                return_value=_EmptyBus(),
            ):
                stats = await svc.reconcile_drift_states(
                    min_pending_age_seconds=0,
                    min_orphan_age_seconds=60,
                )
        finally:
            _detach_log_capture(cap, old_level)

        details = _details_for(stats)
        job_after = _get_job(engine, job_id)

        if job_after and job_after.get("admission_state") == "active":
            ev.append(
                "OK: JobItem (instance_id=NULL, 3600s old) "
                "admission_state='active' after sweep"
            )
        else:
            passed = False
            ev.append(
                f"FAIL: JobItem state="
                f"{job_after.get('admission_state') if job_after else None!r}"
            )
        if (
            job_after
            and job_after.get("terminal_reason") is None
            and job_after.get("failed_at") is None
        ):
            ev.append("OK: no terminal_reason, no failed_at (no finalize)")
        else:
            passed = False
            ev.append(
                f"FAIL: terminal writes: terminal_reason="
                f"{job_after.get('terminal_reason')!r}, "
                f"failed_at={job_after.get('failed_at')!r}"
            )

        # The :1828 guard skips BEFORE task inspection — no detail
        # record for this job, only a DEBUG log line.
        row_details = [d for d in details if d.get("job_id") == job_id]
        if not row_details:
            ev.append(
                "OK: no detail record for the instance-less row "
                "(the :1828 guard skips before task inspection, "
                "debug-log only)"
            )
        else:
            ev.append(
                f"NOTE: unexpected detail(s) for {job_id}: "
                f"{[d.get('pattern') for d in row_details]}"
            )
        debug_hits = [
            m for lvl, m in cap.records
            if "has no instance_id" in m
        ]
        if debug_hits:
            ev.append(
                f"OK: guard DEBUG line captured: {debug_hits[0][:140]}..."
            )
        else:
            passed = False
            ev.append(
                "FAIL: expected DEBUG log 'has no instance_id' not found. "
                f"Records: {[m for _, m in cap.records][:10]}"
            )

        _record(scenario, passed, "\n".join(ev))
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n{traceback.format_exc()}",
        )


async def scenario_fs4_recheck_finalizes(engine) -> None:
    """FS4 — re-check-next-cycle: the FS1 skip was a deferral, not a wedge.

    Self-contained re-run of the FS1 shape on a fresh DB (whole-DB sweeps
    make cross-scenario row reuse unreliable — observed first run): seed
    the FAILED-parent shape → sweep with the lookup patched to RAISE →
    verify SKIP (ACTIVE) → restore the REAL lookup (the shape is now
    finalizable per the killpath scenario (b) completion leg) → sweep
    AGAIN on the SAME row → boundary finalization NOW proceeds
    (admission_state='done', terminal_reason='failed', failed_at stamped,
    lock released).
    """
    scenario = "FS4 re-check-next-cycle → boundary finalizes (deferral)"
    try:
        _wipe_tables(engine)
        job_id = "job-fs4"
        inst_id = "inst-fs4"
        ev: list[str] = []
        passed = True

        _insert_instance(
            engine, inst_id,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id=job_id,
            instance_id=inst_id,
            queue_id="queue-fs4",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-fs4",
            job_id=job_id,
            instance_id=inst_id,
        )
        _insert_task_with_status(
            engine,
            work_id=job_id,
            instance_id=inst_id,
            status=TaskStatus.FAILED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        # Build real JobQueueService so the boundary takes the PREFERRED
        # path (JobQueueService._finalize_terminal) — production wiring
        # always passes job_queue_service (daemon/api.py:476-489). The
        # legacy fallback (job_queue_service=None) drops terminal_reason
        # and failed_at (job_recovery_service.py:3108-3117) — observed
        # on the first probe run.
        from daemon.services.job_lock_manager import JobLockManager
        from daemon.services.job_queue_service import JobQueueService
        queue_repo = JobQueueRepository(engine)
        queue_repo.create(
            project_id="test-project",
            queue_name="queue-fs4",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )
        job_repo = JobRepository(engine)
        lock_repo = LockRepository(engine)
        lock_manager = JobLockManager(lock_repo=lock_repo)
        real_jq_service = JobQueueService(
            job_repo, lock_manager, queue_repo,
            instance_manager=None,
        )

        service = _make_service(engine, job_queue_service=real_jq_service)

        # ── Sweep #1: lookup raising → SKIP (same shape as FS1) ──
        with (
            patch.object(TaskRepository, "has_instance_busy", _raise_transient),
            patch(
                "daemon.services.job_recovery_service.get_dependency_bus",
                return_value=_EmptyBus(),
            ),
        ):
            stats1 = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )
        job_mid = _get_job(engine, job_id)
        locks_mid = _get_locks_for_job(engine, job_id)
        skip1 = [
            d for d in _details_for(stats1)
            if d.get("job_id") == job_id
            and str(d.get("pattern", "")).startswith("orphan_active_skipped")
        ]
        if job_mid and job_mid.get("admission_state") == "active" and skip1:
            ev.append(
                "OK: sweep #1 (lookup raising) — JobItem 'active', skip "
                f"label (verbatim): {skip1[0].get('pattern')!r}"
            )
        else:
            passed = False
            ev.append(
                f"FAIL: sweep #1 did not skip: state="
                f"{job_mid.get('admission_state') if job_mid else None!r}, "
                f"skip_details={skip1}"
            )
        if len(locks_mid) == 1:
            ev.append("OK: sweep #1 — lock still held")
        else:
            passed = False
            ev.append(f"FAIL: sweep #1 lock rows={len(locks_mid)}, expected 1")

        # ── Sweep #2: REAL lookup restored → boundary finalizes ──
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBus(),
        ):
            stats2 = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )
        details2 = _details_for(stats2)

        job_after = _get_job(engine, job_id)
        locks_after = _get_locks_for_job(engine, job_id)

        term_records = [
            d for d in details2
            if d.get("job_id") == job_id
            and d.get("pattern") == "orphan_active_failed_terminal"
        ]
        if term_records:
            ev.append(
                "OK: sweep #2 — orphan_active_failed_terminal detail recorded "
                f"(task_id={term_records[0].get('task_id')})"
            )
        else:
            passed = False
            ev.append(
                f"FAIL: orphan_active_failed_terminal NOT recorded for "
                f"{job_id} on sweep #2. Details: {details2}"
            )
        if job_after and job_after.get("admission_state") == "done":
            ev.append(
                "OK: sweep #2 — JobItem admission_state='done' (NOT 'dead' — "
                "atomic_retry preserved via the boundary)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL: JobItem state="
                f"{job_after.get('admission_state') if job_after else None!r}, "
                f"expected 'done'"
            )
        if job_after and job_after.get("terminal_reason") == "failed":
            ev.append("OK: terminal_reason='failed' preserved")
        else:
            passed = False
            ev.append(
                f"FAIL: terminal_reason="
                f"{job_after.get('terminal_reason') if job_after else None!r}"
            )
        if job_after and job_after.get("failed_at") is not None:
            ev.append(
                f"OK: failed_at stamped: {job_after.get('failed_at')!r}"
            )
        else:
            passed = False
            ev.append(
                "FAIL: failed_at is None — atomic_retry gating marker missing"
            )
        if len(locks_after) == 0:
            ev.append("OK: lock row RELEASED (boundary finally block)")
        else:
            passed = False
            ev.append(
                f"FAIL: lock rows={len(locks_after)}, expected 0 "
                f"(lock MUST release on DONE)"
            )

        _record(scenario, passed, "\n".join(ev))
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n{traceback.format_exc()}",
        )


async def scenario_fs5_healthy_shape_negative(engine) -> None:
    """FS5 — healthy-shape negative: ACTIVE JobItem + young PENDING Task
    is skipped via the NORMAL path (orphan_active_skipped_healthy_shape)
    — the strict healthy-shape guard is unaffected by the flips."""
    scenario = "FS5 healthy-shape negative (young ACTIVE+PENDING)"
    try:
        _wipe_tables(engine)
        job_id = "job-fs5"
        inst_id = "inst-fs5"
        ev: list[str] = []
        passed = True

        _insert_instance(engine, inst_id)  # alive, YOUNG
        _insert_job_item(
            engine,
            job_id=job_id,
            instance_id=inst_id,
            queue_id="queue-fs5",
            # YOUNG — the healthy-shape exclusion has no grace
            # precondition, but young keeps the shape unambiguous.
        )
        task_id = _insert_task_with_status(
            engine,
            work_id=job_id,
            instance_id=inst_id,
            status=TaskStatus.PENDING.value,
        )

        service = _make_service(engine)
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBus(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )
        details = _details_for(stats)

        job_after = _get_job(engine, job_id)
        task_after = _get_task(engine, task_id)

        healthy_records = [
            d for d in details
            if d.get("job_id") == job_id
            and d.get("pattern") == "orphan_active_skipped_healthy_shape"
        ]
        if healthy_records:
            ev.append(
                "OK: orphan_active_skipped_healthy_shape detail recorded "
                f"(task_id={healthy_records[0].get('task_id')})"
            )
        else:
            passed = False
            ev.append(
                f"FAIL: orphan_active_skipped_healthy_shape NOT recorded "
                f"for {job_id}. Details: {details}"
            )
        if job_after and job_after.get("admission_state") == "active":
            ev.append("OK: JobItem admission_state='active'")
        else:
            passed = False
            ev.append(
                f"FAIL: JobItem state="
                f"{job_after.get('admission_state') if job_after else None!r}"
            )
        if task_after and task_after.get("status") == TaskStatus.PENDING.value:
            ev.append("OK: Task still 'pending' (untouched — Pattern (a)/(b) surface)")
        else:
            passed = False
            ev.append(
                f"FAIL: Task status="
                f"{task_after.get('status') if task_after else None!r}"
            )
        if (
            job_after
            and job_after.get("terminal_reason") is None
            and job_after.get("failed_at") is None
        ):
            ev.append("OK: no terminal_reason, no failed_at")
        else:
            passed = False
            ev.append(
                f"FAIL: terminal writes: terminal_reason="
                f"{job_after.get('terminal_reason')!r}, "
                f"failed_at={job_after.get('failed_at')!r}"
            )

        _record(scenario, passed, "\n".join(ev))
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n{traceback.format_exc()}",
        )


# ════════════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════════════


def main() -> int:
    print("=== Test Pack: pattern_f_failsafe_flips_probe_test ===")
    print("(Pattern (f) Fail-Safe Flips Probe — lookup errors SKIP, not finalize)")
    print("Branch: feature/security-boundary-hygiene @ a77647bf/ac2c3091")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print()

    start = time.monotonic()

    # ── Layer-2 internal timeout (signal-based, 150s) ──
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(150)

    # ── File-backed SQLite under /tmp (cleaned on exit) ──
    tmp_dir = tempfile.mkdtemp(prefix="pattern_f_failsafe_flips_")
    db_path = os.path.join(tmp_dir, "probe.db")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    # Force-import every model that the recovery service touches so
    # SQLModel.metadata.create_all emits a complete schema.
    from daemon.repositories.job_queue import models as _jq_models  # noqa: F401
    from daemon.repositories.task import models as _task_models  # noqa: F401
    from daemon.repositories.instance import models as _inst_models  # noqa: F401
    SQLModel.metadata.create_all(engine)

    print(f"DB: {db_path}")
    print()

    try:
        asyncio.run(scenario_fs1_exception_skip(engine))
        asyncio.run(scenario_fs2_unwired_repo_skip(engine))
        asyncio.run(scenario_fs3_no_instance_id_skip(engine))
        asyncio.run(scenario_fs4_recheck_finalizes(engine))
        asyncio.run(scenario_fs5_healthy_shape_negative(engine))
    except TimeoutError as te:
        print(f"\nTIMEOUT: internal 150s alarm tripped: {te}")
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
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    sys.exit(main())
