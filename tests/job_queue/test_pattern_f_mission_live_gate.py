"""Fix e8a61da3 (Option E) — Pattern (f) liveness-aware finalize gate
(E1) + durable JOB_COMPLETED witness row (E2).

The 043d64a8 compounding failures from the RCA:

* **(i) Pattern (f) premature finalize** — the drift reconciler's
  f1 lane (active JobItem + FAILED/CANCELLED backing Task) had ONLY
  the task-scoped retry-child-live conjunct. A usage-limit deferral
  cancelled the parent task, the retry task quiesced between
  attempts, and the reconciler finalized the JobItem — firing
  ``notify_watchers`` — while the MISSION was still alive. The
  premature notify spent the mission's watch rows at the wrong
  moment. E1 wires the canonical ``evaluate_mission_live`` guard
  into the f1 lane (read-only; the retry-child conjunct STAYS).

* **(ii) Witness no-publish** — the recovery finalize boundary
  published NOTHING durable; durable ``job_completed`` rows were
  minted only by the observer producer on the natural path, so the
  recovery terminal left zero job-side trace. E2 publishes the
  ``JOB_COMPLETED`` witness row via the EventBus durable lane at the
  Pattern (f) finalize boundaries (no watcher fan-out).

Covers:

* AC1 — mission tree LIVE (the exact 043d64a8 posture: parent task
  CANCELLED by the deferral, retry quiesced, instances non-terminal)
  → the reconciler REFUSES to finalize (skip detail
  ``orphan_active_skipped_mission_live_f1``), the boundary is never
  called, the JobItem stays ACTIVE.
* AC2 — retry-child-live conjunct still load-bearing: a PENDING
  retry child on the instance skips via the W1 detail (BEFORE the
  mission gate).
* AC3 — all-terminal tree → finalize proceeds through the boundary
  AND the E2 witness row is published (kind ``job_completed``,
  payload carries job_id + status; no watcher fan-out from it).
* AC4 — E2 soft-fail: no events service wired → finalize still
  succeeds.

Fixture shape mirrors ``test_w1_retry_child_lineage_conjunct.py``
(raw-SQL seeders; job_queue/conftest engine fixtures) so the two
files read as one family.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.task.models import TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.stale_task_recovery import StaleTaskRecovery


def _insert_instance(
    engine,
    instance_id: str,
    project_id: str = "test-project",
    status: str = InstanceStatus.RUNNING.value,
    created_at: datetime | None = None,
    parent_id: str | None = None,
    last_activity_at: datetime | None = None,
) -> None:
    """Insert an ``instances`` row.

    ``parent_id`` / ``last_activity_at`` are ADDITIVE (default
    ``None`` ⇒ the historical single-instance shape) — the f2
    guard-anchor-neutrality pin needs a MULTI-MEMBER tree with
    explicit activity stamps (the anchor is a tree aggregate).
    """
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    activity_iso = (
        last_activity_at.isoformat()
        if last_activity_at is not None
        else None
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version, parent_id,
                     last_activity_at)
                VALUES
                    (:instance_id, 'developer', 'agents/developer',
                     :status, :project_id, :created_at, :updated_at, 1,
                     :parent_id, :last_activity_at)
                """
            ),
            {
                "instance_id": instance_id,
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
                "parent_id": parent_id,
                "last_activity_at": activity_iso,
            },
        )


def _update_instance(
    engine,
    instance_id: str,
    *,
    status: str | None = None,
    last_activity_at: datetime | None = None,
) -> None:
    """UPDATE an existing ``instances`` row (status / activity stamp).

    The f2 anchor-neutrality pin re-shapes the SEEDED rows — a second
    INSERT would violate the primary key.
    """
    sets: list[str] = []
    params: dict = {"instance_id": instance_id}
    if status is not None:
        sets.append("status = :status")
        params["status"] = status
    if last_activity_at is not None:
        sets.append("last_activity_at = :last_activity_at")
        params["last_activity_at"] = last_activity_at.isoformat()
    if not sets:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                f"UPDATE instances SET {', '.join(sets)} "
                "WHERE instance_id = :instance_id"
            ),
            params,
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
                    (:job_id, 'developer', 'agents/developer', 'hi', 'api',
                     :project_id, :queue_id, 0, :admission_state,
                     :created_at, :instance_id, 'task', 0, '{}')
                """
            ),
            {
                "job_id": job_id,
                "project_id": project_id,
                "queue_id": queue_id,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
            },
        )


def _insert_task(
    engine,
    *,
    work_id: str,
    instance_id: str,
    status: str = TaskStatus.PENDING.value,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
    retry_count: int = 0,
) -> int:
    now = created_at or datetime.now(timezone.utc)
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
                    ('process_message', :instance_id, NULL, :status,
                     :retry_count, :created_at, 0, 0, :work_id, 0, 0,
                     :completed_at)
                """
            ),
            {
                "instance_id": instance_id,
                "status": status,
                "retry_count": retry_count,
                "created_at": now,
                "work_id": work_id,
                "completed_at": (
                    completed_at.isoformat() if completed_at else None
                ),
            },
        )
        return result.lastrowid


@pytest.fixture
def task_repository(engine) -> TaskRepository:
    return TaskRepository(engine)


class _WitnessBus:
    """Capture ``create_event`` calls (the E2 durable lane)."""

    def __init__(self):
        self.calls: list[dict] = []

    async def create_event(self, *, instance_id, kind, data=None, **kw):
        self.calls.append(
            {"instance_id": instance_id, "kind": kind, "data": data}
        )


def _witness_manager(instance_repo) -> MagicMock:
    """Instance-manager double carrying the real instance repository
    (the mission-live guard walks it) + a capture bus for E2."""
    manager = MagicMock()
    manager._instance_repository = instance_repo
    bus = _WitnessBus()
    events_service = MagicMock()
    events_service._event_bus = bus
    manager._events_service = events_service
    manager._bus = bus
    return manager


def _make_service(
    *,
    engine,
    repository,
    lock_repo,
    instance_repo,
    task_repository,
    job_queue_service_mock,
    witness_manager,
) -> JobRecoveryService:
    stale_recovery = StaleTaskRecovery(
        task_repository=task_repository,
        message_repository=None,
        event_repository=None,
    )
    return JobRecoveryService(
        job_repository=repository,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
        job_queue_service=job_queue_service_mock,
        task_repository=task_repository,
        stale_task_recovery=stale_recovery,
        instance_manager=witness_manager,
    )


def _boundary_mock(repository) -> MagicMock:
    """JobQueueService mock whose ``_finalize_terminal`` performs the
    real atomic_transition (the W1-test convention) so a stray
    finalization actually persists."""
    mock = MagicMock()
    mock.notify_watchers = AsyncMock(return_value=None)

    async def _boundary_side_effect(
        *, instance_id, decision, job_id, error_message, target_status,
    ):
        now = datetime.now(timezone.utc).isoformat()
        repository.atomic_transition(
            job_id,
            from_status="active",
            to_status=target_status,
            completed_at=now,
            error_message=error_message,
        )
        return (job_id, None)

    mock._finalize_terminal = AsyncMock(side_effect=_boundary_side_effect)
    return mock


def _seed_043d64a8_shape(
    engine,
    *,
    mission_live: bool,
    retry_child_live: bool = False,
):
    """Seed the exact incident posture.

    leader (mission root) → worker (deferred instance). The worker's
    parent task is CANCELLED (the usage-limit deferral supersede); a
    retry task existed and either quiesced (terminal — the reconciler
    sees no in-flight work) or is still live (the W1 conjunct case).
    ``mission_live`` controls the instance-tree posture.
    """
    worker_status = (
        InstanceStatus.RUNNING.value if mission_live
        else InstanceStatus.COMPLETED.value
    )
    _insert_instance(engine, "inst-e1-leader")
    _insert_instance(engine, "inst-e1-worker", status=worker_status)
    _insert_job_item(
        engine,
        job_id="job-e1-043d",
        instance_id="inst-e1-worker",
        queue_id="queue-e1",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
    )
    _insert_task(
        engine,
        work_id="job-e1-043d",
        instance_id="inst-e1-worker",
        status=TaskStatus.CANCELLED.value,  # the deferral supersede
        created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        completed_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
    )
    # The retry task: fresh work_id (schedule_retry mints one),
    # same instance. Quiesced = terminal unless retry_child_live.
    _insert_task(
        engine,
        work_id="job-e1-043d-retry",
        instance_id="inst-e1-worker",
        status=(
            TaskStatus.PENDING.value
            if retry_child_live
            else TaskStatus.CANCELLED.value
        ),
        retry_count=1,
    )


class TestE1MissionLiveFinalizeGate:
    @pytest.mark.asyncio
    async def test_mission_alive_refuses_finalize_043d64a8(
        self,
        engine,
        repository,
        lock_repo,
        task_repository,
    ):
        """AC1 — the exact 043d64a8 scenario. Parent task CANCELLED,
        retry quiesced (W1 conjunct passes), but the mission tree is
        STILL LIVE (worker instance running under the leader). Pre-E1
        the reconciler finalized + notified; post-E1 it holds."""
        instance_repo = SQLModelInstanceRepository(engine=engine)
        _seed_043d64a8_shape(engine, mission_live=True)
        jq = _boundary_mock(repository)
        service = _make_service(
            engine=engine,
            repository=repository,
            lock_repo=lock_repo,
            instance_repo=instance_repo,
            task_repository=task_repository,
            job_queue_service_mock=jq,
            witness_manager=_witness_manager(instance_repo),
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )
        details = stats.get("details", []) if isinstance(stats, dict) else []

        # The skip is observable through the drift details.
        assert any(
            d.get("pattern") == "orphan_active_skipped_mission_live_f1"
            for d in details
        ), details
        # The boundary was NEVER reached — no finalize, no notify.
        jq._finalize_terminal.assert_not_called()
        jq.notify_watchers.assert_not_called()
        # The JobItem stays ACTIVE.
        with engine.begin() as conn:
            state = conn.execute(
                text(
                    "SELECT admission_state FROM job_queue_items "
                    "WHERE job_id = 'job-e1-043d'"
                )
            ).fetchone()[0]
        assert state == AdmissionState.ACTIVE.value

    @pytest.mark.asyncio
    async def test_retry_child_live_conjunct_fires_before_mission_gate(
        self,
        engine,
        repository,
        lock_repo,
        task_repository,
    ):
        """AC2 — the W1 conjunct is STILL load-bearing: a live
        (PENDING) retry child skips via the retry-child detail, not
        the mission gate."""
        instance_repo = SQLModelInstanceRepository(engine=engine)
        _seed_043d64a8_shape(engine, mission_live=True, retry_child_live=True)
        jq = _boundary_mock(repository)
        service = _make_service(
            engine=engine,
            repository=repository,
            lock_repo=lock_repo,
            instance_repo=instance_repo,
            task_repository=task_repository,
            job_queue_service_mock=jq,
            witness_manager=_witness_manager(instance_repo),
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )
        details = stats.get("details", []) if isinstance(stats, dict) else []
        assert any(
            d.get("pattern") == "orphan_active_skipped_retry_child_live"
            for d in details
        ), details
        jq._finalize_terminal.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_terminal_tree_finalizes_and_publishes_witness(
        self,
        engine,
        repository,
        lock_repo,
        task_repository,
    ):
        """AC3 — mission genuinely over (all instances terminal): the
        finalize proceeds through the boundary AND the E2 witness row
        is published on the EventBus durable lane (kind
        ``job_completed``, job_id-addressed, watcher fan-out NEVER
        called from it)."""
        instance_repo = SQLModelInstanceRepository(engine=engine)
        _seed_043d64a8_shape(engine, mission_live=False)
        witness_manager = _witness_manager(instance_repo)
        jq = _boundary_mock(repository)
        service = _make_service(
            engine=engine,
            repository=repository,
            lock_repo=lock_repo,
            instance_repo=instance_repo,
            task_repository=task_repository,
            job_queue_service_mock=jq,
            witness_manager=witness_manager,
        )

        await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # Finalize went through the boundary (cancelled target — the
        # Task's terminal status is authoritative).
        jq._finalize_terminal.assert_awaited_once()
        # E2: the durable witness row WAS published.
        bus = witness_manager._bus
        assert len(bus.calls) == 1
        call = bus.calls[0]
        assert call["kind"] == "job_completed"
        assert call["data"]["job_id"] == "job-e1-043d"
        assert call["data"]["status"] == "cancelled"
        assert call["data"]["source"] == "job_recovery_service.pattern_f"
        # The JobItem left ACTIVE.
        with engine.begin() as conn:
            state = conn.execute(
                text(
                    "SELECT admission_state FROM job_queue_items "
                    "WHERE job_id = 'job-e1-043d'"
                )
            ).fetchone()[0]
        assert state != AdmissionState.ACTIVE.value

    @pytest.mark.asyncio
    async def test_witness_publish_is_soft_fail(
        self,
        engine,
        repository,
        lock_repo,
        task_repository,
    ):
        """AC4 — no events service wired (legacy test double shape):
        the witness publish skips quietly and the finalize STILL
        succeeds."""
        instance_repo = SQLModelInstanceRepository(engine=engine)
        _seed_043d64a8_shape(engine, mission_live=False)
        jq = _boundary_mock(repository)
        bare = MagicMock()  # no usable events service
        bare._instance_repository = instance_repo
        # Explicit None — a bare MagicMock would auto-vivify the
        # attribute and exercise the exception path instead of the
        # intended quiet-skip path.
        bare._events_service = None
        service = _make_service(
            engine=engine,
            repository=repository,
            lock_repo=lock_repo,
            instance_repo=instance_repo,
            task_repository=task_repository,
            job_queue_service_mock=jq,
            witness_manager=bare,
        )

        await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        jq._finalize_terminal.assert_awaited_once()
        with engine.begin() as conn:
            state = conn.execute(
                text(
                    "SELECT admission_state FROM job_queue_items "
                    "WHERE job_id = 'job-e1-043d'"
                )
            ).fetchone()[0]
        assert state != AdmissionState.ACTIVE.value

    @pytest.mark.asyncio
    async def test_e1_guard_error_fails_open_to_finalize_f1(
        self,
        engine,
        repository,
        lock_repo,
        task_repository,
        caplog,
    ):
        """Reviewer direction-pin (rider, post-review) — the E1
        mission-live guard's fail-open contract is FROZEN: when
        ``evaluate_mission_live`` reports an internal error
        (``live=False, error=True`` — any exception inside the guard
        collapses to this verdict by the wrapper at
        ``mission_live_guard.py:210-226``), the f1 finalize
        PROCEEDS with a logged WARN rather than refusing the
        finalize.

        Rationale: a missing terminal report is WORSE than an extra
        premature one — at-least-once terminal delivery is preserved.
        A flip to fail-closed here would wedge instance-less orphans
        ACTIVE forever (the guard's seam-error path is exactly the
        case where the row has no other resolution path — fail-closed
        = stranded row).

        This test pins the direction so it can never silently flip:

        (a) the f1 finalize PROCEEDS anyway (fail-open) — the
            boundary ``_finalize_terminal`` IS awaited, the JobItem
            leaves ACTIVE;
        (b) a WARN is logged at the f1 lane (operator visibility on
            the direction — distinguishes a guard error from a
            clean all-terminal verdict);
        (c) the ``live=True`` skip detail family
            (``orphan_active_skipped_mission_live_f1``) MUST NOT
            fire — that detail is for a guard verdict of
            ``live=True``; on ``error=True`` the f1 lane proceeds
            and the WARN is the only signal.
        """
        import daemon.services.job_recovery_service as jrs_module
        from daemon.services.mission_live_guard import (
            MissionLiveVerdict,
        )

        instance_repo = SQLModelInstanceRepository(engine=engine)
        # All-terminal tree: the natural E1 path would already open
        # the door. We force the ERROR state via monkeypatch so the
        # test exercises the f1 lane's fail-open branch (the
        # ``error=True / timed_out=True`` log + skip-finalize guard
        # at job_recovery_service.py:2700-2711), NOT the clean
        # all-terminal branch. W1 conjunct passes naturally (no live
        # retry child on the seeded instance).
        _seed_043d64a8_shape(engine, mission_live=False)
        jq = _boundary_mock(repository)
        witness_manager = _witness_manager(instance_repo)
        service = _make_service(
            engine=engine,
            repository=repository,
            lock_repo=lock_repo,
            instance_repo=instance_repo,
            task_repository=task_repository,
            job_queue_service_mock=jq,
            witness_manager=witness_manager,
        )

        # Force the E1 guard into the ERROR state — the canonical
        # ``evaluate_mission_live`` wrapper collapses any internal
        # exception (``RuntimeError`` from the unwired-seam check at
        # ``mission_live_guard.py:277-281`` or any other guard
        # internal) to ``live=False, error=True``. The f1 lane
        # MUST proceed (fail-open) and emit a WARN.
        original_eval = jrs_module.evaluate_mission_live
        jrs_module.evaluate_mission_live = AsyncMock(
            return_value=MissionLiveVerdict(
                live=False,
                reason=(
                    "FORCED E1 ERROR — guard returned error=True "
                    "(test injection; mirrors the RuntimeError "
                    "seam-error collapse at "
                    "mission_live_guard.py:277-281)"
                ),
                error=True,
                timed_out=False,
            )
        )
        try:
            with caplog.at_level(
                logging.WARNING,
                logger="daemon.services.job_recovery_service",
            ):
                stats = await service.reconcile_drift_states(
                    min_pending_age_seconds=0,
                    min_orphan_age_seconds=60,
                )
            details = (
                stats.get("details", [])
                if isinstance(stats, dict) else []
            )
        finally:
            jrs_module.evaluate_mission_live = original_eval

        # (a) f1 finalize PROCEEDS — fail-open. The boundary was
        # called exactly once (cancelled target — the Task's
        # terminal status is authoritative).
        jq._finalize_terminal.assert_awaited_once()
        # The JobItem left ACTIVE.
        with engine.begin() as conn:
            state = conn.execute(
                text(
                    "SELECT admission_state FROM job_queue_items "
                    "WHERE job_id = 'job-e1-043d'"
                )
            ).fetchone()[0]
        assert state != AdmissionState.ACTIVE.value

        # (c) the ``live=True`` skip detail family MUST NOT fire —
        # that detail is for ``mission_verdict.live == True`` only.
        # On ``error=True`` the f1 lane proceeds and the WARN is
        # the only signal.
        assert not any(
            d.get("pattern")
            == "orphan_active_skipped_mission_live_f1"
            for d in details
        ), details
        # The terminal-routed detail DID fire (the boundary was
        # called) — sanity check that the fail-open branch hit
        # the expected follow-on path, not some other short-circuit.
        assert any(
            d.get("pattern") == "orphan_active_failed_terminal"
            for d in details
        ), details

        # (b) WARN logged at the f1 lane — operator visibility on
        # the fail-open direction. The dedicated log line at
        # ``job_recovery_service.py:2704-2711`` distinguishes
        # ``error=True`` / ``timed_out=True`` from a clean
        # all-terminal verdict (which emits no log line at the f1
        # lane — the post-boundary DONE log at :2742 fires for
        # BOTH).
        fail_open_warnings = [
            record for record in caplog.records
            if record.levelno == logging.WARNING
            and "mission-live guard opened the finalize door"
            in record.getMessage()
        ]
        assert fail_open_warnings, (
            "FAIL-OPEN contract: the f1 lane MUST emit a WARN when "
            "the E1 guard returns error=True (operator visibility "
            "on the direction). Got none. Pinning this prevents a "
            "silent flip to fail-closed (which would wedge "
            "instance-less orphans ACTIVE forever — the guard's "
            "seam-error path is exactly the case where the row "
            "has no other resolution path)."
        )
        # The WARN MUST carry error=True (not timed_out) — the
        # injection's ``error=True, timed_out=False`` shape.
        assert any(
            "error=True" in record.getMessage()
            and "timed_out=False" in record.getMessage()
            for record in fail_open_warnings
        ), fail_open_warnings


# ─────────────────────────────────────────────────────────────────────────────
# f1/E1 LANE GUARD-ANCHOR NEUTRALITY PIN (running-zombie cycle 2, 2026-10-21)
# ─────────────────────────────────────────────────────────────────────────────


class _FakeVerdict:
    """Minimal stand-in for ``MissionLiveVerdict`` (the call-shape pin
    only asserts HOW the lane calls the guard, not the verdict)."""

    def __init__(self, live: bool, reason: str = "pinned"):
        self.live = live
        self.reason = reason
        self.error = False
        self.timed_out = False


class TestF1LaneGuardAnchorNeutralityPin:
    """The f1/E1 lane is NEUTRAL to the running-zombie anchor fix.

    LANE HONESTY (cycle-2 review, C2 — this class was previously
    mislabeled ``TestF2GuardAnchorNeutralityPin``): the seeded shape
    short-circuits at the **f1/E1 lane**
    (``job_recovery_service.py:2674``, detail pattern
    ``orphan_active_skipped_mission_live_f1``) — f2's own lane
    (``orphan_active_skipped_mission_live``, ``:2975``) is NEVER
    REACHED by these tests. Consequences, stated plainly:

    * f2's own lane is **behaviorally unasserted** here. Neither
      behavioral pin below can fail on an anchor REVERT — by
      construction: in both, the MEMBER's stamp is the tree max on
      both sides of the fix, so the verdict is identical whichever
      way the anchor is scoped. These pins prove f1-lane
      *neutrality*, not anchor protection.
    * The **mutation-grade anchor protection** lives in
      ``tests/job_queue/test_running_zombie_sweep.py::
      test_fresh_root_cannot_hold_a_stale_member_open`` — that test
      is M1-proven RED when the anchor reverts to root-inclusive.
    * f2's CALL SHAPE is covered by the static source pin
      (``test_f2_lane_source_call_shape_is_pinned``, below), which
      anchors on the ``job_recovery_service.py`` source text
      (READ-ONLY on this branch — the file itself is not touched).

    The anchor fix (2026-10-21, ``daemon/services/mission_live_guard.py``)
    moves the zombie-backstop freshness anchor OFF the ROOT's own
    ``last_activity_at`` onto the NON-ROOT members'. The root's own
    stamp is refreshed on every message enqueue
    (``daemon/services/instance_messaging.py:2455``), so it can never
    prove a tree is quiet — that is the defect the fix closes.

    This class is the PIN-FIRST proof (leader instruction, cycle 2):

    * ``test_f2_guard_call_shape_is_pinned`` — the FIRST guard call
      the reconciler makes (the f1/E1 lane) keeps its shape (no
      ``skip_root``: for this lane the root's own status IS a live
      leg; the anchor fix must not change the call).
    * ``test_live_member_keeps_f2_holding`` — a tree whose MEMBER
      carries a RECENT stamp holds (no finalize). The member's
      stamp is the tree max both before and after the fix ⇒ the
      verdict is identical on both sides.
    * ``test_quiet_tree_lets_f2_finalize`` — a tree whose member
      carries a STALE stamp (>6h) fires the backstop and the lane
      finalizes. Identical before and after the fix.

    DELIBERATELY NOT PINNED: the shape where the ROOT's stamp is
    FRESH and the member's is STALE. There the verdict CHANGES
    (hold → finalize) — that IS the defect the fix closes, and
    pinning it would pin the bug.
    """

    @pytest.mark.asyncio
    async def test_f2_guard_call_shape_is_pinned(
        self,
        engine,
        repository,
        lock_repo,
        task_repository,
    ):
        """f2's guard call keeps its shape across the anchor fix."""
        from unittest.mock import patch

        instance_repo = SQLModelInstanceRepository(engine=engine)
        _seed_043d64a8_shape(engine, mission_live=True)
        jq = _boundary_mock(repository)
        service = _make_service(
            engine=engine,
            repository=repository,
            lock_repo=lock_repo,
            instance_repo=instance_repo,
            task_repository=task_repository,
            job_queue_service_mock=jq,
            witness_manager=_witness_manager(instance_repo),
        )

        captured: list[dict] = []

        async def _spy(**kwargs):
            captured.append(kwargs)
            return _FakeVerdict(live=True, reason="pinned-stub")

        with patch(
            "daemon.services.job_recovery_service.evaluate_mission_live",
            _spy,
        ):
            await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )

        assert captured, (
            "f2 MUST consult the mission-live guard — the pin is "
            "meaningless if f2 stopped calling it"
        )
        kwargs = captured[0]
        assert kwargs.get("instance_id") == "inst-e1-worker"
        assert kwargs.get("instance_repository") is instance_repo
        # The f1 lane has no bus seam → the bus leg is UNKNOWN
        # (skipped), never a fabricated 0.
        assert kwargs.get("bus_pending_count") is None
        # f2 does NOT run in the pre-stamp mode: the root's own
        # status IS a live leg for f2 (skip_root is never True).
        assert kwargs.get("skip_root", False) is False, (
            "f2 must not opt into the pre-stamp skip_root mode — the "
            f"anchor fix must not change the CALL shape. Got: {kwargs}"
        )

    def test_f2_lane_source_call_shape_is_pinned(self):
        """STATIC source pin for f2's OWN lane.

        The behavioral tests in this class exercise the f1/E1 lane —
        the seeded shape short-circuits there, so f2 is never reached
        and cannot be asserted behaviorally from this module. f2's
        CALL SHAPE is therefore pinned from the SOURCE TEXT instead
        (the census suite's source-text anchoring style; the file
        itself stays READ-ONLY on this branch):

        * the f2 lane passes the Gate-1 bus pending count
          (``bus_pending_count=bus_pending_count``) — the guard's bus
          leg must AGREE with the gate seam (unlike f1, which has no
          bus seam and passes ``None``);
        * the f2 lane must NOT opt into the pre-stamp ``skip_root``
          mode — for f2 the root's own status IS a live leg.
        """
        import re
        from pathlib import Path

        from daemon.services import job_recovery_service as jrs

        source = Path(jrs.__file__).read_text()
        f2_pattern = '"orphan_active_skipped_mission_live"'
        assert f2_pattern in source, (
            "f2's detail pattern vanished from job_recovery_service.py "
            "— the lane was renamed or removed and this pin must be "
            "re-homed"
        )
        # The f2 guard call sits ~218 chars above its detail pattern;
        # a 2.5k-char look-back window covers the call without
        # reaching the f1 lane's own call (~16.5k chars earlier).
        idx = source.index(f2_pattern)
        window = source[max(0, idx - 2500):idx]
        assert "bus_pending_count=bus_pending_count" in window, (
            "f2's guard call must pass the Gate-1 bus pending count — "
            "the guard's bus leg must agree with the gate seam"
        )
        assert not re.search(r"\bskip_root\s*=", window), (
            "f2 must NOT opt into the pre-stamp skip_root mode: for "
            "f2 the root's own status IS a live leg"
        )

    @pytest.mark.asyncio
    async def test_live_member_keeps_f2_holding(
        self,
        engine,
        repository,
        lock_repo,
        task_repository,
    ):
        """A member with a RECENT stamp keeps the mission live ⇒ f2
        HOLDS the JobItem (no finalize). Identical before and after
        the anchor fix (the member's stamp is the tree max on both
        sides)."""
        instance_repo = SQLModelInstanceRepository(engine=engine)
        _seed_043d64a8_shape(engine, mission_live=True)
        # The job's instance is TERMINAL (so the verdict is decided
        # by the MEMBER, not by the root's own status); the member
        # carries a RECENT stamp.
        _update_instance(
            engine, "inst-e1-worker",
            status=InstanceStatus.COMPLETED.value,
            last_activity_at=datetime.now(timezone.utc) - timedelta(hours=9),
        )
        _insert_instance(
            engine, "inst-e1-member",
            status=InstanceStatus.RUNNING.value,
            parent_id="inst-e1-worker",
            last_activity_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        jq = _boundary_mock(repository)
        service = _make_service(
            engine=engine,
            repository=repository,
            lock_repo=lock_repo,
            instance_repo=instance_repo,
            task_repository=task_repository,
            job_queue_service_mock=jq,
            witness_manager=_witness_manager(instance_repo),
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )
        details = stats.get("details", []) if isinstance(stats, dict) else []
        assert any(
            d.get("pattern") == "orphan_active_skipped_mission_live_f1"
            for d in details
        ), details
        jq._finalize_terminal.assert_not_called()
        with engine.begin() as conn:
            state = conn.execute(
                text(
                    "SELECT admission_state FROM job_queue_items "
                    "WHERE job_id = 'job-e1-043d'"
                )
            ).fetchone()[0]
        assert state == AdmissionState.ACTIVE.value

    @pytest.mark.asyncio
    async def test_quiet_tree_lets_f2_finalize(
        self,
        engine,
        repository,
        lock_repo,
        task_repository,
    ):
        """A member with a STALE stamp (>6h) fires the 6h backstop ⇒
        f2 FINALIZES. Identical before and after the anchor fix (the
        member's stamp is the tree max on both sides)."""
        instance_repo = SQLModelInstanceRepository(engine=engine)
        _seed_043d64a8_shape(engine, mission_live=True)
        stale = datetime.now(timezone.utc) - timedelta(hours=9)
        _update_instance(
            engine, "inst-e1-worker",
            status=InstanceStatus.COMPLETED.value,
            last_activity_at=stale,
        )
        _insert_instance(
            engine, "inst-e1-member",
            status=InstanceStatus.RUNNING.value,
            parent_id="inst-e1-worker",
            last_activity_at=stale,
        )
        jq = _boundary_mock(repository)
        service = _make_service(
            engine=engine,
            repository=repository,
            lock_repo=lock_repo,
            instance_repo=instance_repo,
            task_repository=task_repository,
            job_queue_service_mock=jq,
            witness_manager=_witness_manager(instance_repo),
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )
        details = stats.get("details", []) if isinstance(stats, dict) else []
        assert not any(
            d.get("pattern") == "orphan_active_skipped_mission_live_f1"
            for d in details
        ), details
        jq._finalize_terminal.assert_awaited_once()
