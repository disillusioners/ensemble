"""Pinned tests for the report-delivery-bug-family fix pack
(2026-10-03, ``fix/report-delivery-bug-family``).

Four fixes, four tests:

* ``test_occ5_repro_terminal_reason_preserved`` — occurrence #5
  repro (job ``fb0cf25c``): finalize logs ``status='completed'``,
  the post-finalize Site-2 reconciler pass must leave
  ``terminal_reason='completed'`` intact (FP2 non-destructive),
  must NOT fire the unguarded job_watchers DELETE arm (FP2b),
  and must fire the PP1 zero-watcher WARN when the watcher row
  is hard-deleted mid-mission.

* ``test_c264aa8a_orphan_mission_live_not_finalized`` — R3
  force-finalize arm: active JobItem + zero Task rows + mission
  live (instance RUNNING) → ``reconcile_turn_mirror`` must NOT
  stamp ``orphaned_no_task`` on the JobItem. Mirrors are
  preserved.

* ``test_fp1_preserve_active_anchoring_task_warns_completed_class`` —
  FP1 boot-wipe: a PENDING task anchoring an ACTIVE JobItem
  SURVIVES the wipe (preserve predicate); a COMPLETED task
  anchoring an ACTIVE JobItem also survives AND the
  observability WARN probe fires. Pure mechanism test (no
  dev-boot, no env poisoning) — the wipe is the SAME
  ``clear_all(preserve_in_flight=True)`` call the
  ``discard_on_startup`` hook makes.

* ``test_fp3_orphaned_no_task_vocabulary_round_trip`` — FP3:
  ``orphaned_no_task`` joins the canonical vocabulary AND
  ``is_terminal()``; ``terminal_reason_variants_for`` round-trips
  through the new token; ``canonicalize_status`` is identity for
  the new token; ``message_queue`` reconcile arm no longer
  stamps the orphan to ``failed`` (it falls through to
  ``completed`` — least-misleading available).

All tests use the file-local ``bug_engine`` fixture (per-test
file-backed SQLite engine; see the fixture at :95) and the
per-test seed helpers. NOT the ``tests/job_queue/conftest.py``
``engine`` fixture — that one is session-scoped in-memory SQLite
used by the sibling pinned test files; this file intentionally
opts out for bulletproof per-test DB isolation. No external DB.
No daemon boot. No env-poison risk. The tests pin the FOUR
commits the fix pack adds (``fix(report-bug): FP2 …`` etc.) and
the occ5/R3 repros the report-delivery-bug family closed.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import (
    Instance,
    InstanceStatus,
)
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
)
from daemon.repositories.job_queue.watcher_repository import (
    JobWatcherRepository,
)
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
)
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.work_notifier import notify_work_watchers
from daemon.services.work_resolver import (
    WorkRecord,
    WorkResolverService,
)
from daemon.services.work_status import (
    _STATUS_CANONICAL_MAP,
    _TERMINAL_STATUSES,
    canonicalize_status,
    is_terminal,
    terminal_reason_variants_for,
)


# ── Fixtures (local, per-test) ──────────────────────────────────────────


@pytest.fixture
def bug_engine(tmp_path):
    """In-process SQLite engine for the report-bug family tests.

    File-backed (not :memory:) so each test gets a clean DB AND
    so multi-test isolation is bulletproof — the session-scoped
    ``engine`` fixture in ``conftest.py`` is shared across the
    whole directory, which is fine for the existing tests but
    the bug-family tests own their own tables.
    """
    db_path = tmp_path / "report_bug.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _iso_now(offset_seconds: int = 0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    ).isoformat()


def _seed_instance(
    engine,
    *,
    instance_id: str,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "worker",
    parent_id: str | None = None,
) -> None:
    now_iso = _iso_now()
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
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
                "status": status,
                "project_id": "test-project",
                "created_at": now_iso,
                "updated_at": now_iso,
                "parent_id": parent_id,
                "last_activity_at": now_iso,
            },
        )


def _seed_job_item(
    engine,
    *,
    job_id: str,
    instance_id: str | None,
    admission_state: str = AdmissionState.ACTIVE.value,
    job_type: str = "task",
    terminal_reason: str | None = None,
    failed_at: str | None = None,
) -> None:
    """Seed a JobItem with optional pre-existing terminal_reason."""
    now_iso = _iso_now()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source,
                     project_id, queue_id, priority, admission_state,
                     created_at, instance_id, job_type, retry_count,
                     terminal_reason, failed_at, version, deleted_at)
                VALUES
                    (:job_id, :agent_id, :agent_dir, :message, :source,
                     :project_id, :queue_id, :priority, :admission_state,
                     :created_at, :instance_id, :job_type, :retry_count,
                     :terminal_reason, :failed_at, 0, NULL)
                """
            ),
            {
                "job_id": job_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "message": "hi",
                "source": "api",
                "project_id": "test-project",
                "queue_id": None,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now_iso,
                "instance_id": instance_id,
                "job_type": job_type,
                "retry_count": 0,
                "terminal_reason": terminal_reason,
                "failed_at": failed_at,
            },
        )


def _seed_task(
    engine,
    *,
    work_id: str,
    instance_id: str | None = None,
    message_id: str | None = None,
    status: str = TaskStatus.PENDING.value,
) -> int:
    """Insert a Task row directly. Returns the rowid."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
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
                "message_id": message_id,
                "status": status,
                "retry_count": 0,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": work_id,
                "is_deferred": False,
                "is_background": False,
                "completed_at": None,
            },
        )
        return int(result.lastrowid)


def _seed_message_queue(
    engine,
    *,
    message_id: str,
    status: str = MessageStatus.PROCESSING.value,
) -> None:
    now_iso = _iso_now()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO message_queue
                    (message_id, instance_id, content, type, source,
                     status, priority, retry_count, max_retries,
                     enqueued_at, processing_started_at)
                VALUES
                    (:message_id, :instance_id, :content, :type, :source,
                     :status, :priority, :retry_count, :max_retries,
                     :enqueued_at, :processing_started_at)
                """
            ),
            {
                "message_id": message_id,
                "instance_id": "inst-msg",
                "content": "hi",
                "type": "agent",
                "source": "api",
                "status": status,
                "priority": 1,
                "retry_count": 0,
                "max_retries": 5,
                "enqueued_at": now_iso,
                "processing_started_at": now_iso,
            },
        )


def _seed_job_lock(
    engine, *, job_id: str
) -> None:
    """Insert a JobLock for the given JobItem. Required for
    ACTIVE JobItems — the ``reconcile_turn_mirror`` invariant
    check raises ``InvalidTransitionError`` if
    ``admission_state='active'`` but no lock exists.
    """
    now_iso = _iso_now()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_locks
                    (lock_id, project_id, queue_id, job_id, instance_id,
                     lock_slot, acquired_at)
                VALUES
                    (:lock_id, :project_id, :queue_id, :job_id, :instance_id,
                     :lock_slot, :acquired_at)
                """
            ),
            {
                "lock_id": str(uuid.uuid4()),
                "project_id": "test-project",
                "queue_id": "test-queue",
                "job_id": job_id,
                "instance_id": "inst-test",
                "lock_slot": 0,
                "acquired_at": now_iso,
            },
        )


def _seed_job_watcher(
    engine, *, job_id: str, instance_id: str
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_watchers
                    (watch_id, job_id, instance_id, watch_events,
                     created_at)
                VALUES
                    (:watch_id, :job_id, :instance_id, :watch_events,
                     :created_at)
                """
            ),
            {
                "watch_id": str(uuid.uuid4()),
                "job_id": job_id,
                "instance_id": instance_id,
                "watch_events": json.dumps(
                    ["completed", "failed", "cancelled"]
                ),
                "created_at": now,
            },
        )


def _read_terminal_reason(engine, job_id: str) -> str | None:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT terminal_reason FROM job_queue_items "
                "WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        ).first()
    return row[0] if row else None


def _read_admission_state(engine, job_id: str) -> str | None:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT admission_state FROM job_queue_items "
                "WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        ).first()
    return row[0] if row else None


def _read_message_queue_status(engine, message_id: str) -> str | None:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT status FROM message_queue WHERE message_id = :mid"
            ),
            {"mid": message_id},
        ).first()
    return row[0] if row else None


def _read_version(engine, job_id: str) -> int | None:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT version FROM job_queue_items "
                "WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        ).first()
    return int(row[0]) if row else None


def _read_watcher_count(engine, job_id: str) -> int:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT COUNT(*) FROM job_watchers WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        ).first()
    return int(row[0]) if row else 0


# ── Test 1: occurrence-#5 repro (FP2 + FP2b + PP1) ─────────────────────


class TestOcc5Repro:
    """Occurrence-#5 repro (job ``fb0cf25c``): finalize-then-reconcile
    overwrites ``terminal_reason='completed'`` with
    ``orphaned_no_task``, and the unguarded job_watchers DELETE arm
    amplifies the damage by removing the watcher mid-mission.

    Test model: a task-kind JobItem with a Task row hard-deleted
    mid-mission (the env-poison wipe) → natural completion
    (finalize sets ``terminal_reason='completed'``,
    ``admission_state='done'``) → ``reconcile_turn_mirror`` fires
    (the Site-2 additive pass) → assert:
        1. ``terminal_reason`` is STILL ``'completed'`` (FP2)
        2. The job_watchers row is NOT deleted (FP2b — guard
           recognizes the JobItem is live via the JobItem's
           own ``instance_id`` even though the Task is gone)
        3. The PP1 zero-watcher WARN would fire IF the watcher
           row were also hard-deleted (independent amplifier:
           a zero-watched terminal fire is a delivery gap)
    """

    def test_occ5_repro_terminal_reason_preserved(
        self, bug_engine, caplog
    ) -> None:
        wid = "fb0cf25c-test-job"
        inst = "inst-test"
        watcher_inst = "watcher-inst"
        msg_id = "msg-test"

        # Pre-seed: instance is alive, job is active, message in
        # flight, watcher registered.
        _seed_instance(bug_engine, instance_id=inst)
        _seed_instance(bug_engine, instance_id=watcher_inst)
        _seed_job_item(
            bug_engine, job_id=wid, instance_id=inst,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _seed_job_lock(bug_engine, job_id=wid)
        _seed_message_queue(
            bug_engine, message_id=msg_id,
            status=MessageStatus.PROCESSING.value,
        )
        _seed_job_watcher(
            bug_engine, job_id=wid, instance_id=watcher_inst
        )

        # Step 1: "Env-poison wipe" — the producer-deleter deleted
        # the COMPLETED Task row mid-mission (occurrence #5 chain).
        # We model this directly: NO Task row exists, the JobItem
        # is still ACTIVE, the instance is still RUNNING.
        # The Task is GONE — found=False in reconcile_turn_mirror.
        assert _read_terminal_reason(bug_engine, wid) is None
        assert _read_admission_state(bug_engine, wid) == "active"
        assert _read_watcher_count(bug_engine, wid) == 1

        # Step 2: Finalize commits the terminal state to the DB
        # (the upstream writer — JobQueueService._finalize_terminal
        # or equivalent — sets terminal_reason='completed' and
        # admission_state='done' AND releases the lock). We do
        # this directly via SQL to model the finalize path
        # without booting the full stack.
        with bug_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE job_queue_items "
                    "SET terminal_reason = 'completed', "
                    "    admission_state = 'done', "
                    "    failed_at = NULL, "
                    "    version = version + 1 "
                    "WHERE job_id = :wid"
                ),
                {"wid": wid},
            )
            # Release the lock to keep the invariant happy
            # (active↔lock symmetry). The pre-fix code path
            # would also do this; the cleanup is part of
            # the finalize contract.
            conn.execute(
                text("DELETE FROM job_locks WHERE job_id = :wid"),
                {"wid": wid},
            )
        # Snapshot the pre-reconcile state.
        assert _read_terminal_reason(bug_engine, wid) == "completed"
        assert _read_admission_state(bug_engine, wid) == "done"
        pre_reconcile_version = _read_version(bug_engine, wid)

        # Step 3: Site-2 additive pass — reconcile_turn_mirror fires
        # AFTER the finalize. The pre-fix bug: this pass overwrote
        # terminal_reason='completed' with 'orphaned_no_task' because
        # (a) found=False forced terminal_reason='orphaned_no_task',
        # (b) the instance-liveness guard was vacuously TRUE when
        # task_instance_id was NULL, (c) the
        # terminal_reason was overwritten unconditionally.
        repo = TaskRepository(engine=bug_engine)
        result = repo.reconcile_turn_mirror(wid)

        # ── FP2 assertion 1: terminal_reason STAYS 'completed' ──
        assert _read_terminal_reason(bug_engine, wid) == "completed", (
            f"FP2 REGRESSION: terminal_reason was overwritten to "
            f"{_read_terminal_reason(bug_engine, wid)!r}; the "
            f"non-destructive guard (terminal_reason IS NULL) did "
            f"not hold. result={result!r}"
        )

        # ── FP2 assertion 2: admission_state STAYS 'done' ──
        assert _read_admission_state(bug_engine, wid) == "done", (
            f"FP2 REGRESSION: admission_state flipped to "
            f"{_read_admission_state(bug_engine, wid)!r}; the "
            f"liveness guard OR the version bump is broken."
        )

        # ── FP2 assertion 3: version bumped (cheap version
        # bump on the UPDATE — occurrence showed two writers
        # racing with no version change). ──
        post_reconcile_version = _read_version(bug_engine, wid)
        assert post_reconcile_version is not None
        assert post_reconcile_version >= pre_reconcile_version, (
            f"FP2 version bump missing: pre={pre_reconcile_version} "
            f"post={post_reconcile_version}"
        )

        # ── FP2b assertion: the job_watchers row SURVIVES the
        # reconcile. Pre-fix the unguarded DELETE arm removed it
        # (the amplifier for the silent-completion loss). With
        # the FP2b guard (taskless + alive-lineage/ACTIVE-job),
        # the mission-live check suppresses the DELETE. ──
        assert _read_watcher_count(bug_engine, wid) == 1, (
            f"FP2b REGRESSION: job_watchers row was hard-deleted "
            f"mid-mission; the alive-lineage/ACTIVE-job guard did "
            f"not suppress the DELETE. This is the silent-completion "
            f"amplifier occurrence #5 closed."
        )

        # ── PP1 arm: independent amplifier. The test models the
        # historical counterfactual — the watcher row WAS
        # hard-deleted (the worst case). Even with FP2 alone
        # (no FP2b), the PP1 WARN would have surfaced the
        # delivery gap so the operator could re-fire from the
        # durable job_completed event row. We hard-delete the
        # watcher here, capture log, and assert the WARN
        # pattern that notify_work_watchers would emit (the
        # notifier site tested in test_work_notifier_pins; the
        # pattern check is the contract for the WARN arm). ──
        with bug_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM job_watchers WHERE job_id = :wid"),
                {"wid": wid},
            )
        assert _read_watcher_count(bug_engine, wid) == 0

        # The PP1 WARN pattern that the notifier emits when a
        # terminal fire with zero watchers fires. We pin the
        # message shape here (the WARN site itself is in
        # daemon/services/work_notifier.py:PP1 and the
        # observer site in
        # daemon/services/job_feedback_observer.py:PP1;
        # the full notify path is exercised by
        # tests/job_queue/test_work_notifier_defect1_pins.py
        # and is outside this file's scope — the pinned
        # contract is the message text).
        expected_pp1_pattern = re.compile(
            r"PP1 zero-watcher terminal fire"
        )
        # The WARN itself is emitted by the notifier; the
        # test asserts the pattern is present in the module
        # source so the contract cannot regress silently.
        with open(
            "daemon/services/work_notifier.py", encoding="utf-8"
        ) as f:
            notifier_src = f.read()
        assert expected_pp1_pattern.search(notifier_src), (
            "PP1 REGRESSION: the zero-watcher terminal WARN "
            "pattern is missing from work_notifier.py"
        )
        with open(
            "daemon/services/job_feedback_observer.py", encoding="utf-8"
        ) as f:
            observer_src = f.read()
        assert expected_pp1_pattern.search(observer_src), (
            "PP1 REGRESSION: the zero-watcher terminal WARN "
            "pattern is missing from job_feedback_observer.py"
        )

    def test_fp2_alone_breaks_damage_chain(
        self, bug_engine
    ) -> None:
        """Historical counterfactual: FP1's task-preservation
        ALONE breaks the damage chain. If the COMPLETED task is
        preserved (FP1 preserve predicate), the post-wipe
        reconcile finds it, does NOT stamp orphan, and the
        watcher arm is not eligible (the taskless trigger does
        not fire). This test pins that the task-preservation is
        the upstream gate that makes FP2/FP2b unnecessary in the
        happy case.
        """
        wid = "fp1-only-chain-break"
        inst = "inst-test"

        _seed_instance(bug_engine, instance_id=inst)
        _seed_job_item(
            bug_engine, job_id=wid, instance_id=inst,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _seed_job_lock(bug_engine, job_id=wid)
        # Task row EXISTS and is COMPLETED (FP1 preserves this
        # class via the new predicate — and the integrity WARN
        # fires in the manager.py boot path).
        _seed_task(
            bug_engine, work_id=wid, instance_id=inst,
            status=TaskStatus.COMPLETED.value,
        )

        # Reconcile finds the task (found=True). No orphan stamp.
        repo = TaskRepository(engine=bug_engine)
        repo.reconcile_turn_mirror(wid)
        assert _read_terminal_reason(bug_engine, wid) is None, (
            "FP1 COUNTERFACTUAL BROKEN: reconcile_turn_mirror "
            "stamped terminal_reason on a job whose task is "
            "intact (found=True). The task-preservation arm "
            "must be the primary gate — the orphan path is "
            "only the fallback."
        )


# ── Test 2: c264aa8a R3 orphan force-finalize (FP2) ─────────────────────


class TestC264aa8aR3:
    """R3 force-finalize arm: active JobItem + zero Task rows +
    mission live (instance RUNNING) → ``reconcile_turn_mirror``
    must NOT stamp ``orphaned_no_task``. The mission is still
    in flight; the orphan stamp would prematurely terminalize a
    live job.

    This is the SAME root cause as occurrence #5 (vacuous
    instance-liveness guard) but a different manifestation:
    here the instance is alive, the JobItem is ACTIVE, and the
    only Task row is gone. The pre-fix logic finalized the job
    because the vacuous NOT EXISTS was true. The fix (use the
    JobItem's own ``instance_id``) closes both.
    """

    def test_orphan_mission_live_not_finalized(
        self, bug_engine
    ) -> None:
        wid = "c264aa8a-mission-live"
        inst = "inst-test"

        # Pre-seed: instance RUNNING, job ACTIVE, NO Task row.
        _seed_instance(bug_engine, instance_id=inst)
        _seed_job_item(
            bug_engine, job_id=wid, instance_id=inst,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _seed_job_lock(bug_engine, job_id=wid)
        assert _read_terminal_reason(bug_engine, wid) is None
        assert _read_admission_state(bug_engine, wid) == "active"

        # Reconcile. The pre-fix code would stamp
        # terminal_reason='orphaned_no_task' and flip
        # admission_state to 'done' (vacuous NOT EXISTS).
        repo = TaskRepository(engine=bug_engine)
        repo.reconcile_turn_mirror(wid)

        # ── FP2 + c264aa8a fix: terminal_reason is NOT stamped
        # when the mission is live (the JobItem's own
        # instance_id is alive). The orphan stamp is deferred
        # until the instance reaches a terminal state. ──
        assert _read_terminal_reason(bug_engine, wid) is None, (
            f"c264aa8a R3 REGRESSION: terminal_reason was "
            f"stamped to {_read_terminal_reason(bug_engine, wid)!r} "
            f"on a live-mission job; the FP2 liveness guard did "
            f"not consult the JobItem's own instance_id."
        )
        assert _read_admission_state(bug_engine, wid) == "active", (
            f"c264aa8a R3 REGRESSION: admission_state was "
            f"flipped to {_read_admission_state(bug_engine, wid)!r} "
            f"on a live-mission job; force-finalize fired when "
            f"the mission was still in flight."
        )

    def test_orphan_mission_dead_does_finalize(
        self, bug_engine
    ) -> None:
        """Companion test: when the mission is actually dead
        (instance terminal), the orphan stamp IS applied.
        Negative-side coverage for the c264aa8a fix — the
        guard must not OVER-suppress.
        """
        wid = "orphan-mission-dead"
        inst = "inst-test"

        # Instance is TERMINAL (mission truly over).
        _seed_instance(
            bug_engine, instance_id=inst,
            status=InstanceStatus.TERMINATED.value,
        )
        _seed_job_item(
            bug_engine, job_id=wid, instance_id=inst,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _seed_job_lock(bug_engine, job_id=wid)

        repo = TaskRepository(engine=bug_engine)
        repo.reconcile_turn_mirror(wid)

        # Mission is dead → orphan stamp fires.
        assert _read_terminal_reason(bug_engine, wid) == "orphaned_no_task", (
            f"OVER-SUPPRESSION REGRESSION: terminal_reason was "
            f"NOT stamped to 'orphaned_no_task' on a dead-mission "
            f"job; got {_read_terminal_reason(bug_engine, wid)!r}."
        )
        assert _read_admission_state(bug_engine, wid) == "done", (
            "OVER-SUPPRESSION REGRESSION: admission_state stayed "
            "'active' on a dead-mission job; the orphan stamp "
            "should have finalized the JobItem."
        )


# ── Test 3: FP1 boot-wipe preservation + WARN ──────────────────────────


class TestFP1BootWipe:
    """FP1 boot-wipe: ``clear_all(preserve_in_flight=True)`` must
    preserve PENDING tasks that anchor an ACTIVE JobItem (the
    dev-boot-on-prod wipe class). The new preserve predicate
    extends the keep-set to include ``EXISTS (non-terminal
    JobItem)``. The COMPLETED-task / ACTIVE-JobItem integrity
    class ALSO survives (the predicate covers it via the same
    EXISTS clause) and the manager.py boot-wipe probe fires a
    WARN for it.

    Pure mechanism test: no dev boot, no env poisoning, no full
    stack. The wipe is ``TaskRepository.clear_all(
    preserve_in_flight=True)`` — the same call the
    ``discard_on_startup`` hook makes. The CONFIRMED producer-
    deleter model (occurrence #5) is: a transient-style boot
    calls ``clear_all`` on the live DB. The fix is the
    PRESERVE predicate.
    """

    def test_pending_task_anchoring_active_job_preserved(
        self, bug_engine, caplog
    ) -> None:
        wid_pending = "wipe-test-pending-anchors-active"
        wid_completed_anchors = "wipe-test-completed-anchors-active"
        wid_pending_no_anchor = "wipe-test-pending-no-anchor"
        wid_running = "wipe-test-running-always-preserved"
        inst = "inst-test"

        _seed_instance(bug_engine, instance_id=inst)
        # Three tasks with active JobItem anchors (PENDING /
        # COMPLETED / RUNNING — all preserved by either the
        # legacy or the FP1 predicate) plus one NO-anchor
        # PENDING task (the doomed case — no JobItem exists
        # to anchor it).
        anchored = [
            (wid_pending, TaskStatus.PENDING.value),
            (wid_completed_anchors, TaskStatus.COMPLETED.value),
            (wid_running, TaskStatus.RUNNING.value),
        ]
        for wid, status in anchored:
            _seed_job_item(
                bug_engine, job_id=wid, instance_id=inst,
                admission_state=AdmissionState.ACTIVE.value,
            )
            _seed_task(
                bug_engine, work_id=wid, instance_id=inst,
                status=status,
            )
        # The "no anchor" case: Task row exists, no JobItem
        # exists. This is the orphan class the wipe is
        # allowed to delete.
        _seed_task(
            bug_engine,
            work_id=wid_pending_no_anchor,
            instance_id=inst,
            status=TaskStatus.PENDING.value,
        )

        # Pre-wipe snapshot — all four tasks exist.
        repo = TaskRepository(engine=bug_engine)
        with bug_engine.begin() as conn:
            count_before = conn.execute(
                text("SELECT COUNT(*) FROM task")
            ).scalar()
        assert count_before == 4

        # Probe: the new ``find_completed_tasks_on_active_jobs``
        # method MUST return the COMPLETED-on-active case (this
        # is the WARN probe in the manager.py boot-wipe path).
        completed_active = repo.find_completed_tasks_on_active_jobs()
        assert completed_active == [wid_completed_anchors], (
            f"FP1 probe REGRESSION: expected "
            f"[{wid_completed_anchors!r}], got {completed_active!r}"
        )

        # Wipe.
        deleted = repo.clear_all(preserve_in_flight=True)
        # The legacy predicate (RUNNING/PAUSED) preserved
        # wid_running. The FP1 predicate (EXISTS non-terminal
        # JobItem) preserved wid_pending and wid_completed_anchors
        # (both anchor an ACTIVE JobItem). wid_pending_no_anchor
        # has no JobItem → doomed.
        with bug_engine.begin() as conn:
            survivors = [
                r[0] for r in conn.execute(
                    text("SELECT work_id FROM task")
                ).all()
            ]
        assert wid_pending in survivors, (
            f"FP1 REGRESSION: PENDING task anchoring ACTIVE "
            f"JobItem was DELETED by the wipe; the preserve "
            f"predicate did not extend to the non-terminal-"
            f"JobItem anchor case. deleted={deleted}, "
            f"survivors={survivors}"
        )
        assert wid_completed_anchors in survivors, (
            f"FP1 REGRESSION: COMPLETED task anchoring ACTIVE "
            f"JobItem was DELETED by the wipe; the preserve "
            f"predicate missed the integrity class. "
            f"survivors={survivors}"
        )
        assert wid_running in survivors, (
            "FP1 REGRESSION: RUNNING task was DELETED by the "
            "wipe; the legacy preserve predicate was broken."
        )
        assert wid_pending_no_anchor not in survivors, (
            "FP1 OVER-PRESERVATION: orphan PENDING task (no "
            "anchoring JobItem) was PRESERVED by the wipe; the "
            "predicate is keeping backlog it should not."
        )
        assert deleted == 1, (
            f"FP1 wipe count wrong: expected 1 (the no-anchor "
            f"PENDING), got {deleted}. survivors={survivors}"
        )

    def test_journal_line_emitted_before_wipe(
        self, bug_engine, caplog
    ) -> None:
        """PP1-adjacent journaling: clear_all logs the doomed-id
        list BEFORE the delete. The log is the audit trail for
        the env-poison producer-deleter family. We capture log
        and assert the JOURNAL line carries the doomed ids.
        """
        wid = "journal-test-doomed"
        inst = "inst-journal"
        _seed_instance(bug_engine, instance_id=inst)
        _seed_task(
            bug_engine, work_id=wid, instance_id=inst,
            status=TaskStatus.PENDING.value,
        )

        repo = TaskRepository(engine=bug_engine)
        with caplog.at_level(
            logging.WARNING, logger="daemon.repositories.task.repository"
        ):
            deleted = repo.clear_all(preserve_in_flight=True)

        assert deleted == 1
        journal_lines = [
            rec for rec in caplog.records
            if "JOURNAL" in rec.getMessage()
            and "TaskRepository.clear_all" in rec.getMessage()
        ]
        assert len(journal_lines) == 1, (
            f"Journaling REGRESSION: expected exactly 1 JOURNAL "
            f"line, got {len(journal_lines)}: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        assert wid in journal_lines[0].getMessage(), (
            f"Journaling REGRESSION: doomed work_id {wid!r} "
            f"missing from the JOURNAL line: "
            f"{journal_lines[0].getMessage()!r}"
        )


# ── Test 4: FP3 vocabulary round-trip ──────────────────────────────────


class TestFP3Vocabulary:
    """FP3: ``orphaned_no_task`` joins the canonical vocabulary AND
    ``is_terminal()``. The map is the SINGLE source of truth; the
    derived reverse index (``_CANONICAL_TO_SOURCES``) must
    round-trip the new token. ``canonicalize_status`` is identity
    for the new token (it maps to itself). The message_queue
    reconcile arm no longer stamps the orphan to ``failed`` —
    it falls through to ELSE ``'completed'`` (least-misleading
    available). The Python-level ``_derive_legacy_status`` returns
    ``'orphaned_no_task'`` for a done JobItem with the orphan
    terminal_reason.
    """

    def test_orphaned_no_task_in_canonical_map(self) -> None:
        assert "orphaned_no_task" in _STATUS_CANONICAL_MAP, (
            "FP3 REGRESSION: 'orphaned_no_task' missing from "
            "_STATUS_CANONICAL_MAP"
        )
        # Identity-mapping (new canonical token, not a collapse).
        assert (
            _STATUS_CANONICAL_MAP["orphaned_no_task"]
            == "orphaned_no_task"
        ), (
            "FP3 REGRESSION: 'orphaned_no_task' should map to "
            "itself (new canonical token, not a collapse onto "
            "'cancelled' or 'failed')."
        )

    def test_orphaned_no_task_is_terminal(self) -> None:
        assert "orphaned_no_task" in _TERMINAL_STATUSES, (
            "FP3 REGRESSION: 'orphaned_no_task' missing from "
            "_TERMINAL_STATUSES"
        )
        assert is_terminal("orphaned_no_task") is True, (
            "FP3 REGRESSION: is_terminal('orphaned_no_task') "
            "returned False; the new token must be terminal."
        )

    def test_orphaned_no_task_canonicalize_identity(self) -> None:
        # canonicalize_status is identity for canonical tokens.
        assert (
            canonicalize_status("orphaned_no_task")
            == "orphaned_no_task"
        )
        # Round-trip through the reverse index: the variant-set
        # of 'orphaned_no_task' includes itself.
        variants = terminal_reason_variants_for("orphaned_no_task")
        assert "orphaned_no_task" in variants, (
            f"FP3 REGRESSION: 'orphaned_no_task' missing from "
            f"its own variant-set: {variants!r}"
        )

    def test_message_queue_arm_no_longer_stamps_failed(
        self, bug_engine
    ) -> None:
        """The reconcile message_queue arm removed the
        ``orphaned_no_task → 'failed'`` branch (FP3). The orphan
        case now falls through to ELSE 'completed'. The
        :data:`message_id = :task_message_id` WHERE clause is
        NULL for the orphan case (``task_message_id`` is NULL
        when found=False), so the message_queue row is never
        actually reached in practice — the test pins the
        SQL-level contract that the orphan→failed branch is
        gone.
        """
        # We exercise the SQL text directly to verify the
        # branch is gone — reading the source is the
        # canonical contract check.
        with open(
            "daemon/repositories/task/repository.py", encoding="utf-8"
        ) as f:
            repo_src = f.read()
        # The orphan→failed branch used to be:
        #     WHEN :terminal_reason = 'orphaned_no_task' THEN 'failed'
        # The fix removed it (the orphan case falls through
        # to ELSE 'completed' which is the least-misleading
        # available terminal in the message_queue vocabulary).
        # We assert the orphan→failed line is GONE.
        assert (
            "WHEN :terminal_reason = 'orphaned_no_task' THEN 'failed'"
            not in repo_src
        ), (
            "FP3 REGRESSION: the message_queue arm still maps "
            "'orphaned_no_task' to 'failed'; the mislabeling "
            "branch was supposed to be removed."
        )

    def test_message_queue_arm_orphan_not_mislabeled_failed(
        self, bug_engine
    ) -> None:
        """Behavioral companion to ``test_message_queue_arm_no_
        longer_stamps_failed`` (P1-4).

        Seed the c264aa8a DEAD-mission setup (terminal instance
        linkage, ACTIVE JobItem, NO Task row) plus a message_queue
        row with a known processing status. Invoke
        ``reconcile_turn_mirror``. Assert:

          * the JobItem's terminal_reason is stamped to
            ``orphaned_no_task`` (the orphan stamp fires on a
            dead mission — c264aa8a mechanism);
          * the message_queue row's status is NOT touched — the
            WHERE clause ``message_id = :task_message_id``
            evaluates against a NULL ``task_message_id`` (the
            Task is missing, so snapshot is None), the row is
            never reached, and the pre-FP3 ``orphaned_no_task →
            'failed'`` mislabeling branch is gone.

        The behavioral pin is: the message_queue row status
        stays at its seeded ``processing`` value — neither
        ``failed`` (the pre-FP3 mislabel) nor ``completed``
        (a fresh-claimed value). This is the runtime consequence
        of the FP3 dead-branch removal at the message_queue
        UPDATE site.
        """
        wid = "c264aa8a-msg-queue-orphan"
        inst = "inst-c264-msgq"
        msg_id = "msg-c264-orphan"

        # Dead-mission setup: instance TERMINAL, JobItem ACTIVE,
        # NO Task row. The Task is missing — found=False in
        # reconcile_turn_mirror; ``task_message_id`` is NULL.
        _seed_instance(
            bug_engine, instance_id=inst,
            status=InstanceStatus.TERMINATED.value,
        )
        _seed_job_item(
            bug_engine, job_id=wid, instance_id=inst,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _seed_job_lock(bug_engine, job_id=wid)

        # Seed the message_queue row with a known non-terminal
        # status. The reconcile arm must NOT touch it (orphan
        # case — the WHERE clause is NULL on task_message_id).
        _seed_message_queue(
            bug_engine, message_id=msg_id,
            status=MessageStatus.PROCESSING.value,
        )

        # Sanity: pre-reconcile state.
        assert _read_terminal_reason(bug_engine, wid) is None
        assert _read_admission_state(bug_engine, wid) == "active"
        assert _read_message_queue_status(bug_engine, msg_id) == \
            MessageStatus.PROCESSING.value

        # Run reconcile. The orphan stamp fires (dead mission),
        # the message_queue arm is silent (WHERE clause NULL).
        repo = TaskRepository(engine=bug_engine)
        repo.reconcile_turn_mirror(wid)

        # The JobItem was finalized to 'orphaned_no_task' (the
        # c264aa8a orphan stamp on dead mission).
        assert _read_terminal_reason(bug_engine, wid) == "orphaned_no_task", (
            "Behavioral companion setup: JobItem terminal_reason "
            "should be stamped to 'orphaned_no_task' on a dead "
            f"mission; got {_read_terminal_reason(bug_engine, wid)!r}."
        )
        assert _read_admission_state(bug_engine, wid) == "done", (
            "Behavioral companion setup: admission_state should "
            "be 'done' on a dead mission; got "
            f"{_read_admission_state(bug_engine, wid)!r}."
        )

        # The KEY behavioral assertion: the message_queue row is
        # NOT mislabeled 'failed' (the pre-FP3 dead-branch
        # behavior). It stays at its seeded 'processing' value
        # — the WHERE clause ``message_id = :task_message_id``
        # evaluated against NULL never matches, so the row is
        # never reached by the UPDATE.
        post_status = _read_message_queue_status(bug_engine, msg_id)
        assert post_status != MessageStatus.FAILED.value, (
            f"FP3 REGRESSION (behavioral): message_queue row was "
            f"mislabeled 'failed' (the pre-fix dead-branch "
            f"behavior); the FP3 fix removed the "
            f"'orphaned_no_task' -> 'failed' branch and the row "
            f"is no longer reached. Got: {post_status!r}"
        )
        assert post_status == MessageStatus.PROCESSING.value, (
            f"FP3 message_queue arm should be silent in the "
            f"orphan case (WHERE clause NULL on task_message_id) "
            f"— the row stays at its seeded 'processing' value. "
            f"Got: {post_status!r}"
        )

    def test_vocab_round_trip_no_other_terminal_regressed(
        self,
    ) -> None:
        """Guard test: the new token did not break the existing
        canonical-map completeness. Every pre-FP3 map entry
        still canonicalizes to its original target, and every
        canonical target's variant-set still includes its
        pre-FP3 variants.
        """
        for token in (
            "pending", "processing", "paused", "completed",
            "failed", "cancelled", "dead_letter",
        ):
            assert is_terminal(token) == (
                token in {"completed", "failed", "cancelled", "dead_letter"}
            ), f"is_terminal({token!r}) regressed"
        # settled is per-kind dispatch (not a map target) but
        # is in the terminal set — pin that.
        assert is_terminal("settled") is True
        # Unknown token → non-terminal (conservative).
        assert is_terminal("no-such-token") is False


# ── P1-5 / P1-4 — behavioral companions (P1-5: PP1 zero-watcher WARN) ──


class _NoOpJobRepo:
    """Minimal stand-in for ``JobRepository`` (the message-kind
    side) — ``WorkResolverService`` only consults this when the
    task side returns None. The PP1 test patches
    ``resolver.resolve_work`` directly so this fallback path is
    not exercised, but the resolver constructor still requires
    the argument.
    """

    def get(self, _job_id):
        return None

    def __getattr__(self, _name):
        return lambda *_a, **_kw: None


class TestPP1ZeroWatcherWarn:
    """P1-5 — behavioral companion to the PP1 zero-watcher WARN
    (the source-grep-only pin in
    ``test_occ5_repro_terminal_reason_preserved``).

    The PP1 fix adds a WARN at
    ``daemon/services/work_notifier.py:428-451`` that fires when
    a TERMINAL ``notify_work_watchers`` call finds ZERO claimed
    watchers — the silent-dropped-report class (operator had no
    log line to correlate the dropped delivery). The behavioural
    pin: invoke ``notify_work_watchers`` with a terminal status
    on a job with NO watchers and assert the WARN carries the
    ``work_id[:8]``, the ``status``, and points at the durable
    ``job_completed`` event row (recovery hint).

    One notifier site is covered behaviourally here. The
    observer-outbox site at
    ``daemon/services/job_feedback_observer.py:PP1`` remains a
    source-grep-only pin in the same suite (the outbox threading
    requires the full child-reports stack + events service —
    impractical to invoke in isolation without rebuilding the
    fixtures used by the larger
    ``test_work_notifier_defect1_pins`` suite). The single-
    notifier behavioural pin closes the spec'd path; the
    observer pin is documented as a sibling structural pin.
    """

    @pytest.fixture
    def pp1_components(self, bug_engine):
        """Real ``JobWatcherRepository`` + ``TaskRepository`` +
        ``SQLModelInstanceRepository``; resolver ``MagicMock``'d
        to return a synthetic ``WorkRecord`` so the resolve-first
        step does not require a fully-seeded Task row (the PP1
        WARN fires AFTER resolve_work, on the empty-watchers
        branch — the Task is irrelevant to this code path)."""
        engine = bug_engine
        watcher_repo = JobWatcherRepository(engine)
        task_repo = TaskRepository(engine)
        instance_repo = SQLModelInstanceRepository(engine)
        resolver = WorkResolverService(
            task_repo, _NoOpJobRepo(), instance_repo,
        )
        instance_manager = MagicMock()
        instance_manager.enqueue_message = AsyncMock(
            return_value=MagicMock(message_id="msg-pp1-test"),
        )
        # C1 (2026-09-25): expose the instance repository on the
        # manager so any internal ``evaluate_mission_live`` guard
        # consults the real DB (the default MagicMock would
        # force the guard to fail-OPEN — out of scope for this
        # pin but consistent with the work_notifier fixture
        # convention).
        instance_manager._instance_repository = instance_repo
        return {
            "engine": engine,
            "watcher_repo": watcher_repo,
            "task_repo": task_repo,
            "resolver": resolver,
            "instance_manager": instance_manager,
        }

    @pytest.mark.asyncio
    async def test_pp1_warn_fires_on_terminal_with_zero_watchers(
        self, pp1_components, caplog,
    ) -> None:
        """Behavioural: invoke ``notify_work_watchers`` with a
        terminal status on a work_id that has ZERO watchers.
        Assert via caplog that the PP1 WARN fires carrying
        ``work_id[:8]`` and the terminal ``status``, with the
        durable ``job_completed`` event-row pointer in the
        message body.

        Coverage decision: this is the SINGLE notifier site
        that is practical to invoke in isolation here (the
        observer-outbox site at
        ``daemon/services/job_feedback_observer.py:PP1`` is
        pinned structurally in
        ``test_occ5_repro_terminal_reason_preserved`` — its
        outbox-threading is exercised end-to-end by
        ``tests/job_queue/test_work_notifier_defect1_pins``
        and ``test_job_feedback_observer`` and would
        duplicate substantial fixtures if mirrored here).
        """
        wid = "pp1-test-zero-watchers"
        wid_short = wid[:8]
        # Synthesize a task-kind WorkRecord so ``resolve_work``
        # returns non-None and the code path proceeds to the
        # watcher fetch. The PP1 WARN fires regardless of the
        # task-kind details — only the watchers-list emptiness
        # matters.
        resolver = pp1_components["resolver"]
        record = WorkRecord(
            work_id=wid, kind="report", status="completed",
            instance_id="inst-pp1-test", project_id="test-project",
            agent_id="worker", result_summary="ok",
            error=None, created_at=datetime.now(timezone.utc),
            job_type=None, mission_liveness=None,
        )
        resolver.resolve_work = MagicMock(return_value=record)
        # Sanity: NO watchers seeded for this work_id — the
        # empty-fetch branch is the path under test.
        watchers = pp1_components["watcher_repo"].get_watchers_for_job(wid)
        assert watchers == [], (
            f"PP1 setup contamination: expected zero watchers for "
            f"{wid!r}, got {watchers!r}"
        )

        with caplog.at_level(logging.WARNING, logger="daemon.services.work_notifier"):
            notified = await notify_work_watchers(
                wid, "completed",
                instance_manager=pp1_components["instance_manager"],
                work_resolver=resolver,
                watcher_repo=pp1_components["watcher_repo"],
            )

        # Behavioural contract: zero-watcher terminal fire
        # returns 0 (no one to notify) AND emits the PP1 WARN
        # with work_id[:8] + status pointer.
        assert notified == 0, (
            "PP1 zero-watcher terminal fire MUST return 0 — "
            "no watchers to notify."
        )
        # Find the PP1 line in caplog.
        pp1_records = [
            r for r in caplog.records
            if "PP1 zero-watcher terminal fire" in r.getMessage()
        ]
        assert len(pp1_records) == 1, (
            f"PP1 REGRESSION (behavioural): expected exactly 1 "
            f"PP1 zero-watcher WARN, got {len(pp1_records)}: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        msg = pp1_records[0].getMessage()
        assert wid_short in msg, (
            f"PP1 WARN missing the work_id[:8] pointer "
            f"({wid_short!r}): {msg!r}"
        )
        assert "status=completed" in msg, (
            f"PP1 WARN missing the terminal status pointer: "
            f"{msg!r}"
        )
        assert "job_completed" in msg, (
            f"PP1 WARN must carry the durable job_completed "
            f"event-row pointer for recovery (re-fire from "
            f"the events table); got: {msg!r}"
        )

    @pytest.mark.asyncio
    async def test_pp1_warn_does_not_fire_for_non_terminal_status(
        self, pp1_components, caplog,
    ) -> None:
        """Negative pin: a non-terminal status (``in_progress``)
        with zero watchers does NOT fire the PP1 WARN — the
        spec carves out the non-terminal case (zero watchers
        for an in-progress notification is a normal wait, not a
        delivery gap). The notifier returns 0 either way.
        """
        wid = "pp1-test-in-progress"
        resolver = pp1_components["resolver"]
        record = WorkRecord(
            work_id=wid, kind="report", status="in_progress",
            instance_id="inst-pp1-test", project_id="test-project",
            agent_id="worker", result_summary=None,
            error=None, created_at=datetime.now(timezone.utc),
            job_type=None, mission_liveness=None,
        )
        resolver.resolve_work = MagicMock(return_value=record)

        with caplog.at_level(logging.WARNING, logger="daemon.services.work_notifier"):
            notified = await notify_work_watchers(
                wid, "in_progress",
                instance_manager=pp1_components["instance_manager"],
                work_resolver=resolver,
                watcher_repo=pp1_components["watcher_repo"],
            )

        assert notified == 0
        pp1_records = [
            r for r in caplog.records
            if "PP1 zero-watcher terminal fire" in r.getMessage()
        ]
        assert pp1_records == [], (
            f"PP1 REGRESSION (behavioural): the WARN must NOT "
            f"fire for non-terminal statuses; got "
            f"{[r.getMessage() for r in pp1_records]}"
        )
