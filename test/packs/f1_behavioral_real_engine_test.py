"""f1 behavioral proof on REAL machinery — the core of the f1-misfire gate.

This pack proves the misfire class is DEAD and the zombie mission SURVIVED,
on real machinery. It uses the actual JobRecoveryService (with real repos
wired: instance repo + task repo + job queue repo + lock) driven via its
REAL entry ``reconcile_drift_states`` — the sweep itself is the engine.

It does NOT mock the production logic. It uses file-backed SQLite in tmp
dirs (deliberately NOT StaticPool — see ``tests/job_queue/test_orphan_active_job_recovery.py:3024-3038``
for the rationale: StaticPool corrupts writes when dependency-bus repo
sessions share one open transaction). NO daemon boot needed.

Three scenarios (gate scopes 2+3+8, scope-5 tie-in):

* S1 — incident replay, SKIP branch (scopes 2 + 8 capstone). Seed the
  EXACT incident shape (leader WAITING_CHILDREN → child → grandchild
  RUNNING with a FRESH work_id that does NOT match the JobItem's job_id,
  JobItem created_at older than grace, grandchild created_at fresh to
  clear the instance-grace conjunct) → reconcile must SKIP f1, the
  JobItem STAYS active, the WARN line fires with the full 5-substring
  text class. Then let the subtree complete (mark grandchild TERMINATED
  + Task COMPLETED) and re-run reconcile — assert JobItem is NOT
  dead-with-f1-reason (truth-claim: f1 didn't kill the live subtree).
* S2 — zombie preservation, FIRE branch (scope 3). The 802095d8 class:
  instance RUNNING + STALE (last_activity_at = now - 2h, past the 900s
  tree-activity window), no live tasks in tree (all Task rows terminal
  or absent), tree otherwise dead, JobItem active + old, NO Task linked
  via work_id → reconcile must DEAD-finalize, release the per-job lock,
  persist ``terminal_reason='pattern_f1_orphan'`` (re-read from a FRESH
  engine connection for durability proof).
* S3 — tz read-back spot (scope 5 tie-in). Repeat S2 with the tree's
  ``last_activity_at`` stored NAIVE (PG read-back shape documented at
  ``instance/repository.py:2180-2184``) → zombie must STILL fire.
  Belt-and-braces spot on top of the dedicated matrix pack.

Production anchors (DO NOT EDIT — these are the contracts under proof):

* Sweep entry: ``JobRecoveryService.reconcile_drift_states`` at
  ``daemon/services/job_recovery_service.py:658``.
* f1 sub-method: ``_pattern_f_orphan_active_job_recovery`` at
  ``daemon/services/job_recovery_service.py:1846``.
* Subtree-alive guard: lines 2615-2767. Leg-1 =
  ``count_live_tasks_in_instances`` (PENDING/RUNNING tasks over
  lineage tree); leg-2 = ``get_max_last_activity_in_instances`` vs
  ``f1_tree_activity_max_age_seconds`` (default 900s).
* SKIP WARN text: lines 2735-2749 — contains all 5 substrings the
  Scenario-1 assertion targets.
* DEAD finalize: ``_pattern_f_finalize_dead`` at lines 2943-2975
  (lock.release_by_job BEFORE transition).
* Durable ``terminal_reason='pattern_f1_orphan'``: ``atomic_transition``
  call at lines 2989-3003.
* Kill-switch: ``ENSEMBLE_ORPHAN_F1_ENABLED`` default ON — see
  ``_resolve_orphan_f1_enabled`` at lines 117-155.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel

from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import (
    AdmissionState,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services import job_recovery_service as _jrs
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.stale_task_recovery import StaleTaskRecovery


# ─────────────────────────────────────────────────────────────────────────────
# File-backed engine fixture (mirrors the f1-batch convention at
# tests/job_queue/test_orphan_active_job_recovery.py:3024-3038).
# Deliberately NOT StaticPool — file-backed SQLite prevents the
# shared-open-transaction write corruption the misfire batch
# identified.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def f1_engine(tmp_path):
    """File-backed SQLite engine for the f1 behavioral proof."""
    db_path = tmp_path / "f1_behavioral.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# Raw-SQL seeders — minimal subset of the ones in test_orphan_active_job_recovery.py
# to keep the pack self-contained.
# ─────────────────────────────────────────────────────────────────────────────


def _insert_instance(
    engine,
    instance_id: str,
    *,
    project_id: str = "test-project",
    status: str = "running",
    parent_id: str | None = None,
    created_at: datetime | None = None,
    last_activity_at: datetime | None = None,
) -> None:
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    if isinstance(last_activity_at, datetime):
        activity_iso = last_activity_at.isoformat()
    else:
        activity_iso = None
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version, parent_id,
                     last_activity_at)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status, :project_id,
                     :created_at, :updated_at, 1, :parent_id,
                     :last_activity_at)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
                "parent_id": parent_id,
                "last_activity_at": activity_iso,
            },
        )


def _insert_job_item(
    engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str | None = None,
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


def _insert_task(
    engine,
    *,
    work_id: str,
    instance_id: str,
    status: str = TaskStatus.RUNNING.value,
    completed_at: datetime | None = None,
) -> None:
    completed_iso = (
        completed_at.isoformat() if completed_at is not None else None
    )
    with engine.begin() as conn:
        conn.execute(
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
                "task_type": TaskType.PROCESS_MESSAGE.value,
                "instance_id": instance_id,
                "message_id": None,
                "status": status,
                "retry_count": 0,
                "created_at": datetime.now(timezone.utc),
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": work_id,
                "is_deferred": False,
                "is_background": False,
                "completed_at": completed_iso,
            },
        )


def _insert_lock(
    engine,
    *,
    project_id: str,
    queue_id: str,
    job_id: str,
    instance_id: str,
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
                "lock_slot": 0,
                "acquired_at": now,
            },
        )


def _lock_exists(engine, *, project_id: str, queue_id: str, job_id: str) -> bool:
    """Fresh-engine read to prove lock-state durability."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM job_locks WHERE project_id = :p "
                "AND queue_id = :q AND job_id = :j"
            ),
            {"p": project_id, "q": queue_id, "j": job_id},
        ).first()
        return row is not None


def _terminal_reason_from_fresh_read(engine, job_id: str):
    """Re-read the JobItem ``terminal_reason`` from a fresh SQLModel
    session — proves the value was persisted, not just held in some
    in-memory cache that the same connection could see.
    """
    from sqlmodel import Session as SQLModelSession
    from daemon.repositories.job_queue.models import JobItem

    with SQLModelSession(engine) as session:
        row = session.get(JobItem, job_id)
        return getattr(row, "terminal_reason", None) if row is not None else None


def _build_service(f1_engine) -> JobRecoveryService:
    """Build the real JobRecoveryService with real repos wired. The
    ``job_queue_service`` is a MagicMock only because the unit tests
    stub ``notify_watchers``; the f1 path does NOT call it on either
    SKIP or FIRE branches (f1's only repo touchpoints are
    atomic_transition + lock_release).
    """
    job_repo = JobRepository(f1_engine)
    task_repo = TaskRepository(f1_engine)
    lock_repo = LockRepository(f1_engine)
    instance_repo = SQLModelInstanceRepository(engine=f1_engine)
    stale_recovery = StaleTaskRecovery(
        task_repository=task_repo,
        message_repository=None,
        event_repository=None,
    )
    jq_mock = MagicMock()
    jq_mock.notify_watchers = AsyncMock(return_value=None)
    return JobRecoveryService(
        job_repository=job_repo,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
        job_queue_service=jq_mock,
        task_repository=task_repo,
        stale_task_recovery=stale_recovery,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1 — incident replay, SKIP branch (scopes 2 + 8 capstone)
# ─────────────────────────────────────────────────────────────────────────────


class TestF1BehavioralRealEngineScenario1IncidentReplaySkip:
    """The 69a34b35 misfire: live subtree must NEVER be DEAD-finalized.

    The 11:38:18 WARN-class line that KILLED the live subtree now reads
    as a SKIP. The historical line was the kill; today it's this skip.
    """

    @pytest.mark.asyncio
    async def test_live_subtree_skip_persists_and_warn_text_fires(
        self, f1_engine, caplog,
    ):
        """Seed the EXACT incident shape, run reconcile, assert:

        1. JobItem STAYS active (NOT DEAD).
        2. All 5 WARN text-class substrings fired (scope 8 — the
           historical kill line now reads as SKIP with this exact
           text class).
        3. terminal_reason NOT set to 'pattern_f1_orphan'.
        4. The seeded lock row is INTACT (no lock release on SKIP).
        """
        service = _build_service(f1_engine)
        job_repo = service._job_repository
        now = datetime.now(timezone.utc)

        # Leader: WAITING_CHILDREN, activity FROZEN 1000s ago
        # (per-row trap; outside the 900s window).
        _insert_instance(
            f1_engine,
            "inst-leader-s1",
            status="waiting_children",
            created_at=now - timedelta(seconds=1800),
            last_activity_at=now - timedelta(seconds=1000),
        )
        # Child: bridge between leader and grandchild in the
        # permanent lineage (parent_id chain). Status irrelevant
        # for the subtree-alive guard — the guard enumerates the
        # WHOLE tree, not just this row's status.
        _insert_instance(
            f1_engine,
            "inst-child-s1",
            parent_id="inst-leader-s1",
            created_at=now - timedelta(seconds=900),
        )
        # Grandchild: RUNNING, FRESH activity (5s ago). Created
        # fresh so any future 'instance must be old' guard does
        # not exclude this branch via the wrong conjunct (the W1
        # guard checks the JobItem's instance — the leader — not
        # the descendant).
        _insert_instance(
            f1_engine,
            "inst-grandchild-s1",
            status="running",
            parent_id="inst-child-s1",
            created_at=now - timedelta(seconds=600),
            last_activity_at=now - timedelta(seconds=5),
        )
        # JobItem pinned to the LEADER, past the grace (1800s).
        _insert_job_item(
            f1_engine,
            job_id="job-f1-s1",
            instance_id="inst-leader-s1",
            project_id="test-project",
            queue_id="queue-f1-s1",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=now - timedelta(seconds=1800),
        )
        # Seed a lock row — must be INTACT after the SKIP.
        _insert_lock(
            f1_engine,
            project_id="test-project",
            queue_id="queue-f1-s1",
            job_id="job-f1-s1",
            instance_id="inst-leader-s1",
        )
        # The live grandchild Task carries a FRESH work_id (the
        # 11:38:18 mint-site defect) — NOT the JobItem's job_id.
        # This is the heart of the misfire class: get_by_work_id
        # (job_id) returns None, but the subtree is ALIVE.
        _insert_task(
            f1_engine,
            work_id="fresh-uuid-NOT-the-job-id-s1",
            instance_id="inst-grandchild-s1",
            status=TaskStatus.RUNNING.value,
        )

        # Activate caplog at WARNING so we can assert the WARN
        # text-class substrings.
        with caplog.at_level(logging.WARNING):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )

        # Truth claim 1: JobItem STAYS active — not DEAD-finalized.
        job_after = job_repo.get("job-f1-s1")
        assert job_after is not None
        assert (
            job_after.admission_state == AdmissionState.ACTIVE.value
        ), (
            f"f1 misfire: live subtree (leader waiting_children + "
            f"grandchild RUNNING task, work_id mismatch) was "
            f"DEAD-finalized. admission_state="
            f"{job_after.admission_state!r}, details: "
            f"{stats.get('details') if stats else None}"
        )

        # Truth claim 2: detail record family names the SKIP
        # correctly (canonical pattern key for log scrapers).
        skip_records = [
            d for d in (stats or {}).get("details", [])
            if d.get("pattern") == "orphan_active_skipped_tree_alive"
            and d.get("job_id") == "job-f1-s1"
        ]
        assert skip_records, (
            f"f1 must record an orphan_active_skipped_tree_alive "
            f"detail for the live subtree. Got details: "
            f"{stats.get('details') if stats else None}"
        )

        # Truth claim 3: all 5 WARN text-class substrings present
        # in caplog. These are the EXACT substrings the scope-8
        # acceptance contract binds.
        expected_substrings = [
            "Pattern (f1) skip",
            "no Task linked via work_id",
            "lineage tree is ALIVE",
            "f1-misfire class (incident 2026-08-31)",
            "Verify the dispatch path carried work_id=job_id",
        ]
        log_blob = "\n".join(rec.getMessage() for rec in caplog.records)
        for needle in expected_substrings:
            assert needle in log_blob, (
                f"WARN class substring missing: {needle!r}. "
                f"Got log: {log_blob}"
            )

        # Truth claim 4: terminal_reason NOT set (no f1 kill).
        assert job_after.terminal_reason != "pattern_f1_orphan", (
            f"f1 must NOT set terminal_reason on SKIP. Got "
            f"{job_after.terminal_reason!r}"
        )

        # Truth claim 5: seeded lock row is INTACT (no lock
        # release on SKIP — the lock release is gated inside
        # _pattern_f_finalize_dead, which only runs on FIRE).
        assert _lock_exists(
            f1_engine,
            project_id="test-project",
            queue_id="queue-f1-s1",
            job_id="job-f1-s1",
        ), (
            "f1 SKIP must not release the per-job lock — the lock "
            "release runs inside _pattern_f_finalize_dead which is "
            "only invoked on the FIRE branch."
        )

    @pytest.mark.asyncio
    async def test_subtree_completion_after_skip_does_not_set_f1_terminal_reason(
        self, f1_engine,
    ):
        """Let the subtree complete, re-run reconcile, assert the
        JobItem is NOT dead-with-f1-reason. If the JobItem lands in
        a non-DEAD terminal state via the f2 path (COMPLETED Task
        with bus gates satisfied), that is fine. If the bus-pending
        FAIL-SAFE keeps it ACTIVE (mock JobQueueService doesn't
        wire the bus singleton), that's also fine — the gate claim
        is "f1 didn't kill the subtree"; the exact post-completion
        surface is best-effort evidence.
        """
        service = _build_service(f1_engine)
        job_repo = service._job_repository
        now = datetime.now(timezone.utc)

        # Same lineage as step 1.
        _insert_instance(
            f1_engine,
            "inst-leader-s1b",
            status="waiting_children",
            created_at=now - timedelta(seconds=1800),
            last_activity_at=now - timedelta(seconds=1000),
        )
        _insert_instance(
            f1_engine,
            "inst-child-s1b",
            parent_id="inst-leader-s1b",
            created_at=now - timedelta(seconds=900),
        )
        _insert_instance(
            f1_engine,
            "inst-grandchild-s1b",
            status="running",
            parent_id="inst-child-s1b",
            created_at=now - timedelta(seconds=600),
            last_activity_at=now - timedelta(seconds=5),
        )
        _insert_job_item(
            f1_engine,
            job_id="job-f1-s1b",
            instance_id="inst-leader-s1b",
            project_id="test-project",
            queue_id="queue-f1-s1b",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=now - timedelta(seconds=1800),
        )
        _insert_task(
            f1_engine,
            work_id="fresh-uuid-NOT-the-job-id-s1b",
            instance_id="inst-grandchild-s1b",
            status=TaskStatus.RUNNING.value,
        )

        # First pass: the misfire SKIP — JobItem stays ACTIVE.
        stats1 = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )
        assert job_repo.get("job-f1-s1b").admission_state == (
            AdmissionState.ACTIVE.value
        ), (
            f"Pre-completion reconcile must SKIP f1. Got "
            f"details: {stats1.get('details') if stats1 else None}"
        )

        # Let the subtree COMPLETE — flip the grandchild to
        # TERMINATED and the Task to COMPLETED with completed_at
        # backdated past the f2 60s age floor.
        with f1_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET status = 'terminated' "
                    "WHERE instance_id = :iid"
                ),
                {"iid": "inst-grandchild-s1b"},
            )
            conn.execute(
                text(
                    "UPDATE task SET status = 'completed', "
                    "completed_at = :completed_at "
                    "WHERE work_id = :wid"
                ),
                {
                    "completed_at": (
                        now - timedelta(seconds=120)
                    ).isoformat(),
                    "wid": "fresh-uuid-NOT-the-job-id-s1b",
                },
            )

        # Second pass: reconcile. Either f2 fires (DONE with
        # terminal_reason='completed' if bus singleton is wired
        # + bus_pending == 0 + age floor satisfied) OR the f2
        # bus-unavailable FAIL-SAFE keeps it ACTIVE (mock
        # JobQueueService has no bus singleton). EITHER WAY,
        # the truth claim is: terminal_reason != 'pattern_f1_orphan'.
        await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        final = job_repo.get("job-f1-s1b")
        assert final is not None
        assert (
            final.terminal_reason != "pattern_f1_orphan"
        ), (
            f"Post-completion reconcile must NOT land the JobItem "
            f"in the f1-kill surface. Got admission_state="
            f"{final.admission_state!r}, terminal_reason="
            f"{final.terminal_reason!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 2 — zombie preservation, FIRE branch (scope 3)
# ─────────────────────────────────────────────────────────────────────────────


class TestF1BehavioralRealEngineScenario2ZombieFire:
    """The 802095d8-class zombie must STILL fire after the misfire
    shield was added — the guard shields LIVE trees only; genuine
    restart-orphan zombies keep their recovery path.
    """

    @pytest.mark.asyncio
    async def test_zombie_fires_dead_finalizes_releases_lock_persists_reason(
        self, f1_engine, caplog,
    ):
        """Seed: single stale RUNNING instance (last_activity_at =
        now - 2h, well past the 900s tree-activity window), NO Task
        rows, tree otherwise dead, JobItem active + old.

        Reconcile must:
        1. DEAD-finalize the JobItem.
        2. Release the per-job lock scoped via
           ``(project_id, queue_id, job_id)``.
        3. Persist ``terminal_reason='pattern_f1_orphan'`` —
           verified via a FRESH session/engine connection (not the
           session used by the sweep — proves durability, not in-
           memory state).
        4. Emit the WARN/line class (finalized orphan ACTIVE JobItem).
        """
        service = _build_service(f1_engine)
        job_repo = service._job_repository
        now = datetime.now(timezone.utc)

        _insert_instance(
            f1_engine,
            "inst-zombie-s2",
            status="running",
            created_at=now - timedelta(seconds=1800),
            last_activity_at=now - timedelta(seconds=7200),
        )
        _insert_job_item(
            f1_engine,
            job_id="job-f1-zombie-s2",
            instance_id="inst-zombie-s2",
            project_id="test-project",
            queue_id="queue-f1-zombie-s2",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=now - timedelta(seconds=1800),
        )
        _insert_lock(
            f1_engine,
            project_id="test-project",
            queue_id="queue-f1-zombie-s2",
            job_id="job-f1-zombie-s2",
            instance_id="inst-zombie-s2",
        )
        # NO Task rows — the genuine zombie shape.

        with caplog.at_level(logging.WARNING):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )

        # Truth claim 1: DEAD-finalized.
        job_after = job_repo.get("job-f1-zombie-s2")
        assert job_after is not None
        assert (
            job_after.admission_state == AdmissionState.DEAD.value
        ), (
            f"f1 must DEAD-finalize the zombie. Got admission_state="
            f"{job_after.admission_state!r}, details: "
            f"{stats.get('details') if stats else None}"
        )

        # Truth claim 2: lock released (fresh read).
        assert not _lock_exists(
            f1_engine,
            project_id="test-project",
            queue_id="queue-f1-zombie-s2",
            job_id="job-f1-zombie-s2",
        ), (
            "f1 must release the per-job lock scoped via "
            "(project_id, queue_id, job_id) on FIRE."
        )

        # Truth claim 3: durable terminal_reason — read from a
        # FRESH session (proves persistence, not in-memory).
        dur = _terminal_reason_from_fresh_read(
            f1_engine, "job-f1-zombie-s2",
        )
        assert dur == "pattern_f1_orphan", (
            f"f1 finalize MUST persist terminal_reason="
            f"'pattern_f1_orphan'. Got {dur!r} (read from a "
            f"fresh SQLModelSession on the same engine — proves "
            f"persistence, not in-memory cache state)"
        )

        # Truth claim 4: kill WARN line class present.
        f1_records = [
            d for d in (stats or {}).get("details", [])
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-f1-zombie-s2"
        ]
        assert f1_records, (
            f"f1 must record orphan_active_no_task_dead for the "
            f"zombie. Got details: "
            f"{stats.get('details') if stats else None}"
        )
        # And the corresponding logger.warning() line fired.
        assert any(
            "finalized orphan ACTIVE JobItem" in rec.getMessage()
            and "pattern (f1)" in rec.getMessage().lower()
            for rec in caplog.records
        ), (
            "f1 FIRE branch must emit the WARN line class naming "
            "the orphan ACTIVE JobItem and the finalize."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 3 — tz naive read-back spot (scope 5 tie-in)
# ─────────────────────────────────────────────────────────────────────────────


class TestF1BehavioralRealEngineScenario3TzReadbackSpot:
    """Belt-and-braces spot on top of the dedicated tz matrix pack.

    The PG naive read-back shape (documented at
    ``instance/repository.py:2180-2184``) must NOT crash the leg-2
    comparison AND the zombie must STILL fire — the zombie-silently-
    never-fires class stays dead.
    """

    @pytest.mark.asyncio
    async def test_zombie_fires_with_naive_last_activity(self, f1_engine):
        """Tree's max ``last_activity_at`` stored NAIVE — zombie fires."""
        service = _build_service(f1_engine)
        job_repo = service._job_repository
        now = datetime.now(timezone.utc)

        _insert_instance(
            f1_engine,
            "inst-zombie-naive-s3",
            status="running",
            created_at=now - timedelta(seconds=1800),
            # NAIVE stored value — no tz offset, stale (7200s,
            # far outside the 900s leg-2 window).
            last_activity_at=(
                now - timedelta(seconds=7200)
            ).replace(tzinfo=None),
        )
        _insert_job_item(
            f1_engine,
            job_id="job-f1-naive-s3",
            instance_id="inst-zombie-naive-s3",
            project_id="test-project",
            queue_id="queue-f1-naive-s3",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=now - timedelta(seconds=1800),
        )
        _insert_lock(
            f1_engine,
            project_id="test-project",
            queue_id="queue-f1-naive-s3",
            job_id="job-f1-naive-s3",
            instance_id="inst-zombie-naive-s3",
        )
        # NO Task rows — leg 1 silent.

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # Naive MAX must be tz-normalized to UTC, then the leg-2
        # comparison sees a stale value and the zombie fires.
        job_after = job_repo.get("job-f1-naive-s3")
        assert job_after is not None
        assert (
            job_after.admission_state == AdmissionState.DEAD.value
        ), (
            f"f1 must still fire on the tz-naive stale zombie. Got "
            f"admission_state={job_after.admission_state!r}, "
            f"details: {stats.get('details') if stats else None}"
        )
        # No 'orphan_active_skipped_tree_alive' detail — the
        # naive-stale tree activity must NOT read as alive.
        tree_alive_records = [
            d for d in (stats or {}).get("details", [])
            if d.get("pattern") == "orphan_active_skipped_tree_alive"
            and d.get("job_id") == "job-f1-naive-s3"
        ]
        assert not tree_alive_records, (
            f"stale naive tree activity must NOT read as alive. "
            f"Got: {tree_alive_records}"
        )
        # Lock released.
        assert not _lock_exists(
            f1_engine,
            project_id="test-project",
            queue_id="queue-f1-naive-s3",
            job_id="job-f1-naive-s3",
        )
        # terminal_reason persisted.
        assert (
            _terminal_reason_from_fresh_read(
                f1_engine, "job-f1-naive-s3",
            )
            == "pattern_f1_orphan"
        )

    @pytest.mark.asyncio
    async def test_zombie_fires_with_aware_last_activity(self, f1_engine):
        """AWARE stored value — zombie fires. Symmetry with naive."""
        service = _build_service(f1_engine)
        job_repo = service._job_repository
        now = datetime.now(timezone.utc)

        _insert_instance(
            f1_engine,
            "inst-zombie-aware-s3",
            status="running",
            created_at=now - timedelta(seconds=1800),
            last_activity_at=now - timedelta(seconds=7200),
        )
        _insert_job_item(
            f1_engine,
            job_id="job-f1-aware-s3",
            instance_id="inst-zombie-aware-s3",
            project_id="test-project",
            queue_id="queue-f1-aware-s3",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=now - timedelta(seconds=1800),
        )
        _insert_lock(
            f1_engine,
            project_id="test-project",
            queue_id="queue-f1-aware-s3",
            job_id="job-f1-aware-s3",
            instance_id="inst-zombie-aware-s3",
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        job_after = job_repo.get("job-f1-aware-s3")
        assert job_after is not None
        assert (
            job_after.admission_state == AdmissionState.DEAD.value
        ), (
            f"f1 must fire on the AWARE stale zombie. Got "
            f"admission_state={job_after.admission_state!r}, "
            f"details: {stats.get('details') if stats else None}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Module-level setup: ensure the ENSEMBLE_ORPHAN_F1_ENABLED kill-switch
# cache is reset before AND after each test, so a previous test flipping
# the switch (or a stale cache from another module) cannot change the
# truth claim here. Default ON.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_orphan_f1_killswitch():
    _jrs._reset_orphan_f1_for_tests()
    yield
    _jrs._reset_orphan_f1_for_tests()
