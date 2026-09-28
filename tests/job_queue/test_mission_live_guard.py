"""Mission-live guard tests (2026-09-22 premature ``completed ✓`` fix).

Bug class: a task-kind LEADER job is legitimately
``JobItem ACTIVE + backing Task COMPLETED`` mid-mission (child_reports
defers the JobItem finalize behind still-running children). Two
finalize sites force-fired a terminal on that shape:

* drift Pattern f2 (``job_recovery_service.reconcile_drift_states``)
  → false ``completed ✓`` + F10 notify arm CAS-deleted the mission
  watcher row (live repro: events 79328/79349, lag 183s/232s);
* boot sweep ``job_queue_service.reconcile_terminal_watches`` fired
  terminal for mission-keyed watches on settled work rows with no
  liveness consult.

The fix inserts a shared mission-live guard
(``daemon/services/mission_live_guard.py``) at BOTH sites:

* LIVE legs: (a) bus pending watchers, (b) any non-terminal
  descendant, (c) non-terminal root — non-terminal = NOT IN
  ``TERMINAL_INSTANCE_STATUSES`` (the mission resolver canonicalizes
  IDLE → ``processing``, so idle counts LIVE);
* zombie backstop: ``completed_at`` older than
  ``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS`` (6h) falls through to
  finalize (at-least-once delivery, starvation impossible);
* fail-open: any guard error → finalize proceeds.

HARD CONSTRAINT under test: at-least-once terminal delivery — the
mission-dead shapes MUST still finalize + notify (the original
missing-report bug must not regress).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.instance.models import Instance  # noqa: F401
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_models import JobWatcher  # noqa: F401
from daemon.repositories.job_queue.watcher_repository import (
    JobWatcherRepository,
)
from daemon.repositories.task.models import Task as TaskModel  # noqa: F401
from daemon.repositories.task.models import TaskStatus, TaskType  # noqa: F401
from daemon.repositories.task.repository import TaskRepository
from daemon.services import mission_live_guard as _mlg
from daemon.services.job_queue_service import JobQueueService
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.stale_task_recovery import StaleTaskRecovery
from daemon.services.timestamps import now_utc_naive
from daemon.services.work_resolver import WorkResolverService


# ─────────────────────────────────────────────────────────────────────────────
# Engine + seed helpers (file-local engine so ``job_watchers`` exists;
# mirrors tests/job_queue/test_jober_watch_integration.py).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def task_repository(engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def instance_repo(engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine=engine)


def _insert_instance(
    engine,
    instance_id: str,
    *,
    status: str = "running",
    project_id: str = "test-project",
    parent_id: str | None = None,
    created_at: datetime | None = None,
    last_activity_at: datetime | None = None,
) -> None:
    """Insert an ``instances`` row.

    Args:
        last_activity_at: Override the U7 backstop anchor value for
            the row. ``None`` (default) leaves the column NULL, which
            the guard treats as "no activity signal yet". For zombie
            tests set this to an old naive-UTC datetime; for live-tree
            tests set it to ``now_utc_naive()`` (or any recent value).
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
                     created_at, updated_at, last_activity_at, version,
                     parent_id)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status,
                     :project_id, :created_at, :updated_at,
                     :last_activity_at, 1, :parent_id)
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
                "last_activity_at": activity_iso,
                "parent_id": parent_id,
            },
        )


def _insert_job_item(
    engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str = "queue-mlg-1",
    admission_state: str = AdmissionState.ACTIVE.value,
    job_type: str = "task",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
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
                     :created_at, :instance_id, :job_type, 0, :metadata)
                """
            ),
            {
                "job_id": job_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "message": "mission",
                "source": "api",
                "project_id": project_id,
                "queue_id": queue_id,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
                "job_type": job_type,
                "metadata": json.dumps({}),
            },
        )


def _insert_completed_task(
    engine,
    *,
    work_id: str,
    instance_id: str,
    completed_at: datetime | None = None,
) -> int:
    completed_iso = (
        completed_at or now_utc_naive()
    ).isoformat()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, status, retry_count,
                     created_at, cancel_requested, retry_scheduled,
                     work_id, is_deferred, is_background, completed_at)
                VALUES
                    ('process_message', :instance_id, 'completed', 0,
                     :created_at, 0, 0, :work_id, 0, 0, :completed_at)
                """
            ),
            {
                "instance_id": instance_id,
                "created_at": completed_iso,
                "work_id": work_id,
                "completed_at": completed_iso,
            },
        )
        return result.lastrowid


def _add_watch(
    watcher_repo: JobWatcherRepository,
    job_id: str,
    instance_id: str = "watcher-inst",
    events: list[str] | None = None,
) -> None:
    watcher_repo.add_watch(job_id, instance_id, watch_events=events)


class _EmptyBusStub:
    async def pending_watchers(self, source_task_id):
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Real-delivery-chain helpers (iteration 2 — tester M1/claim-witness bar).
#
# The critical-path tests below NO LONGER mock ``notify_watchers``. The
# whole delivery chain is real except the TRANSPORT boundary:
#
#   real WorkResolverService ── real JobQueueService.notify_watchers
#     ── real work_notifier.notify_work_watchers
#       ── real JobWatcherRepository.claim_watchers_for_job_for_instances
#          (the claim-first CAS DELETE...RETURNING — durable state)
#       ── instance_manager.enqueue_message  ← AsyncMock (transport seam)
#
# Durable outcomes are witnessed in the repository (row presence /
# claim-emptiness), not by mock-not-called; deliveries are witnessed on
# the transport seam including the ``[JOB_EVENT]`` byte-path and the
# ``internal_agent:job_event:`` source contract.
# ─────────────────────────────────────────────────────────────────────────────


def _make_real_queue_service(
    engine, repository, task_repository, instance_repo,
) -> tuple[JobQueueService, MagicMock]:
    """Build a REAL JobQueueService over the test engine.

    Returns ``(service, transport)`` — ``transport`` is the instance
    manager whose ``enqueue_message`` is the mocked transport boundary;
    every production seam between the sweep and the enqueue is real.
    """
    resolver = WorkResolverService(
        task_repo=task_repository,
        job_repo=repository,
        instance_repo=instance_repo,
    )
    transport = MagicMock()
    transport._instance_repository = instance_repo
    transport.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-1")
    )
    service = JobQueueService(
        repository=repository,
        lock_manager=MagicMock(),
        queue_repo=MagicMock(),
        instance_manager=transport,
    )
    service.set_watcher_repo(JobWatcherRepository(engine))
    service.set_work_resolver(resolver)
    return service, transport


def _make_recovery_service(
    repository, task_repository, lock_repo, instance_repo,
    queue_service,
) -> JobRecoveryService:
    """JobRecoveryService wired to the REAL queue service (real notify)."""
    return JobRecoveryService(
        job_repository=repository,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
        job_queue_service=queue_service,
        task_repository=task_repository,
        stale_task_recovery=None,
    )


def _set_instance_status(engine, instance_id: str, status: str) -> None:
    """Test-phase instance transition (raw SQL, seeder convention)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE instances SET status = :s "
                "WHERE instance_id = :i"
            ),
            {"s": status, "i": instance_id},
        )


def _job_event_calls(transport) -> list:
    return list(transport.enqueue_message.await_args_list)


def _assert_completed_event(call, job_id: str, watcher_instance: str) -> None:
    """Pin the emission byte-path + source contract (work_notifier)."""
    assert call.kwargs["instance_id"] == watcher_instance
    message = call.kwargs["message"]
    assert message.startswith(
        f"[JOB_EVENT] Job {job_id[:8]}... completed ✓"
    ), f"unexpected emission bytes: {message!r}"
    assert call.kwargs["source"] == (
        f"internal_agent:job_event:{job_id}:completed"
    )


def _assert_rows_claimed(watcher_repo, job_id: str) -> None:
    """Durable claim witness — the CAS DELETE...RETURNING consumed the
    rows; the REPOSITORY (not a mock) shows them gone."""
    assert watcher_repo.get_watchers_for_job(job_id) == [], (
        f"watcher rows for {job_id[:8]}... must be CAS-claimed"
    )
    assert all(
        w.job_id != job_id
        for w in watcher_repo.get_all_active_watches()
    ), "no active watch row may survive for the delivered job"


def _assert_rows_survive(watcher_repo, job_id: str) -> None:
    """Durable hold witness — the guard held the sweep, so the rows are
    still registered in the REPOSITORY for the future terminal."""
    rows = watcher_repo.get_watchers_for_job(job_id)
    assert rows, (
        f"watcher rows for {job_id[:8]}... must SURVIVE a guard-held "
        f"sweep (they are the at-least-once delivery contract)"
    )
    assert any(
        w.job_id == job_id for w in watcher_repo.get_all_active_watches()
    )


# ─────────────────────────────────────────────────────────────────────────────
# Part A — guard unit semantics (``evaluate_mission_live``)
# ─────────────────────────────────────────────────────────────────────────────


class TestMissionLiveGuardUnit:
    def _repo_with(self, engine, rows: dict[str, str]):
        for iid, status in rows.items():
            parent = None
            _insert_instance(engine, iid, status=status, parent_id=parent)
        return SQLModelInstanceRepository(engine=engine)

    @pytest.mark.asyncio
    async def test_leg_c_root_running_is_live(self, engine):
        # Production-realistic fixture: a running root has recent
        # ``last_activity_at`` (the resolver stamps it on every wake;
        # the guard's no-anchor disarm is preserved but the leg-holds
        # prose is emitted only when the anchor IS present so the
        # reason is unambiguous to operators).
        recent = now_utc_naive()
        _insert_instance(
            engine, "root-1", status="running",
            last_activity_at=recent,
        )
        repo = SQLModelInstanceRepository(engine=engine)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-1",
            task_completed_at=now_utc_naive(),
        )
        assert verdict.live and not verdict.error
        assert "live leg holds" in verdict.reason

    @pytest.mark.asyncio
    async def test_leg_b_descendant_running_is_live(self, engine):
        recent = now_utc_naive()
        _insert_instance(
            engine, "root-2", status="completed",
            last_activity_at=recent,
        )
        _insert_instance(
            engine, "child-2", status="running",
            parent_id="root-2", last_activity_at=recent,
        )
        repo = SQLModelInstanceRepository(engine=engine)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-2",
            task_completed_at=now_utc_naive(),
        )
        assert verdict.live and not verdict.error
        assert "live leg holds" in verdict.reason

    @pytest.mark.asyncio
    async def test_idle_root_is_live_resolver_parity(self, engine):
        """The mission resolver canonicalizes IDLE → ``processing``
        (non-terminal). The guard must match: idle = LIVE."""
        _insert_instance(engine, "root-idle", status="idle")
        repo = SQLModelInstanceRepository(engine=engine)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-idle",
            task_completed_at=now_utc_naive(),
        )
        assert verdict.live, (
            f"idle must be LIVE per the resolver's IDLE→processing "
            f"mapping; got {verdict}"
        )

    @pytest.mark.asyncio
    async def test_all_terminal_bus_quiet_is_not_live(self, engine):
        """ALL-TERMINAL tree + a fresh ``last_activity_at`` anchor → the
        anchor plays NO role: ``live=False`` immediately (the C1 frozen
        contract).

        Production-realistic fixture: every member carries a
        ``last_activity_at`` (a row that never observed activity would
        trip the data-gap disarm, masking the C1 bug). With the C1
        fall-through intact, this test MUST fail because the guard
        returns ``live=True`` ("recent activity suggests the mission is
        still deferring") — exactly the overshoot the fixback
        closes."""
        fresh = now_utc_naive()
        _insert_instance(
            engine, "root-3", status="completed",
            last_activity_at=fresh,
        )
        _insert_instance(
            engine, "child-3", status="completed",
            parent_id="root-3", last_activity_at=fresh,
        )
        repo = SQLModelInstanceRepository(engine=engine)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-3",
            task_completed_at=now_utc_naive(),
            bus_pending_count=0,
        )
        assert not verdict.live, (
            f"C1: all-terminal tree + fresh anchor MUST finalize "
            f"immediately (anchor plays no role); got {verdict}"
        )
        assert not verdict.timed_out, (
            f"C1: all-terminal tree MUST NOT carry timed_out=True "
            f"(the anchor plays no role here — timed_out=True is "
            f"reserved for the zombie-backstop path on non-terminal "
            f"members); got {verdict}"
        )

    @pytest.mark.asyncio
    async def test_u7_fixback_discriminating_pin_all_terminal_fresh_anchor(
        self, engine,
    ):
        """MINOR-1 (FIXBACK, 2026-09-28) — the discriminating pin.

        The C1 overshoot returns ``live=True`` whenever:

        * every tree member is terminal (root + descendants), AND
        * the freshest ``last_activity_at`` is RECENT (<6h).

        — even though the mission is dead. Per the council verdict
        (fixback review cycle 1): **the tree-activity anchor plays NO
        role on all-terminal trees**. The anchor's only legitimate
        use is the zombie-backstop on non-terminal trees that have
        gone quiet.

        This test exercises the EXACT shape that triggers the
        overshoot (all-terminal + fresh anchor) AND a stale
        ``task_completed_at`` (the wave-3 anchor the predecessor
        anchored to — preserved at zero relevance by the U7 design
        but still in the signature).

        PRE-fixback expectation: FAIL — the buggy fall-through
        returns ``live=True`` because the fresh anchor rolls the
        timeout window forward on a tree that is, in fact, dead.

        POST-fixback expectation: PASS — all-terminal short-circuits
        to ``live=False`` regardless of anchor age.
        """
        fresh = now_utc_naive() - timedelta(seconds=15)
        _insert_instance(
            engine, "root-fixb", status="completed",
            last_activity_at=fresh,
        )
        _insert_instance(
            engine, "child-fixb", status="completed",
            parent_id="root-fixb", last_activity_at=fresh,
        )
        repo = SQLModelInstanceRepository(engine=engine)
        # Stale task_completed_at to prove it is also ignored on
        # all-terminal trees (U7 already del-anchored; C1 makes
        # the rule authoritative across all shapes).
        stale_task_completed = now_utc_naive() - timedelta(
            seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 60
        )
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-fixb",
            task_completed_at=stale_task_completed,
            bus_pending_count=0,
        )
        assert not verdict.live, (
            "FIXBACK MINOR-1: all-terminal tree + fresh anchor MUST "
            "finalize immediately (live=False). The council verdict "
            "rules the anchor plays NO role on all-terminal trees. "
            f"Got: {verdict}"
        )
        assert not verdict.timed_out, (
            "FIXBACK MINOR-1: timed_out=True is the zombie-backstop "
            "tag — it is reserved for non-terminal trees with stale "
            f"anchors. All-terminal trees MUST NOT carry it. Got: {verdict}"
        )

    @pytest.mark.asyncio
    async def test_u7_fixback_zombie_backstop_fires_on_non_terminal_stale(
        self, engine,
    ):
        """FIXBACK MINOR-1 — zombie-backstop path (preserved).

        The council verdict: the anchor's ONLY legitimate use is the
        zombie-backstop path — non-terminal members + anchor stale
        ≥6h → force terminal (at-least-once wins). This test pins
        that path so the fixback does not close it.

        PRE-fixback expectation: PASS (no overshoot on this shape —
        the loop's non-terminal early-return fires ``live=True``
        regardless of age; but the sticky issue is: under the
        overshoot, the fall-through never reaches this branch
        because the early-return already returned live=True. So
        even though the loop returns live=True here, the
        ``timed_out`` flag never fires — confirming the need to
        suppress the early return in the fix).
        """
        stale_activity = now_utc_naive() - timedelta(
            seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 60
        )
        # Root terminal + descendant NON-terminal (waiting_children) +
        # anchor stale ≥6h.
        _insert_instance(
            engine, "root-zombie", status="completed",
            last_activity_at=stale_activity,
        )
        _insert_instance(
            engine, "child-zombie", status="waiting_children",
            parent_id="root-zombie", last_activity_at=stale_activity,
        )
        repo = SQLModelInstanceRepository(engine=engine)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-zombie",
            task_completed_at=stale_activity,
            bus_pending_count=0,
        )
        assert not verdict.live, (
            "FIXBACK MINOR-1 zombie-backstop: non-terminal + stale "
            f"anchor MUST force terminal. Got: {verdict}"
        )
        assert verdict.timed_out, (
            "FIXBACK MINOR-1 zombie-backstop: timed_out=True MUST be "
            "set on this shape (it is the operator-grep tag for "
            f"backstop fires). Got: {verdict}"
        )

    @pytest.mark.asyncio
    async def test_leg_a_bus_pending_is_live(self, engine):
        _insert_instance(engine, "root-4", status="completed")
        repo = SQLModelInstanceRepository(engine=engine)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-4",
            task_completed_at=now_utc_naive(),
            bus_pending_count=2,
        )
        assert verdict.live
        assert "leg a" in verdict.reason

    @pytest.mark.asyncio
    async def test_timeout_overrides_live_legs(self, engine):
        """(v, U7) Live legs always win — the backstop fires ONLY when
        the tree is genuinely quiet (every member terminal AND the
        freshest ``last_activity_at`` older than the window). Under the
        pre-U7 anchor (``task_completed_at``) the backstop would
        override live legs; under the new tree-anchor it cannot
        (live descendant activity rolls the anchor forward)."""
        # Root IS running + last_activity recent → leg (c) holds the
        # mission live; backstop must NOT fire, even if a stale per-work
        # ``task_completed_at`` was passed (the wave-3 shape).
        _insert_instance(
            engine,
            "root-5",
            status="running",
            last_activity_at=now_utc_naive(),
        )
        repo = SQLModelInstanceRepository(engine=engine)
        old_anchor = now_utc_naive() - timedelta(
            seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 60
        )
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-5",
            task_completed_at=old_anchor,
        )
        assert verdict.live, (
            "U7: live legs always win — root instance is "
            "non-terminal so the backstop must NOT fire on a stale "
            "task_completed_at anchor (wave-3 misfire shape)"
        )
        assert not verdict.timed_out

    @pytest.mark.asyncio
    async def test_zombie_backstop_fires_on_non_terminal_stale(self, engine):
        """FIXBACK (2026-09-28) — the council-ruling zombie-backstop
        path: a tree with at least one NON-TERMINAL member AND the
        freshest ``last_activity_at`` older than the window MUST fire
        the backstop (``live=False, timed_out=True``). This is the
        ONLY legitimate anchor use.

        Symmetric counterpart to ``test_timeout_overrides_live_legs``
        (the positive case where a fresh anchor holds the mission).
        Together they pin the two halves of the FIXBACK decision tree:
        * non-terminal + fresh anchor → ``live=True`` (defer);
        * non-terminal + stale anchor → ``live=False, timed_out=True``
          (zombie-break, at-least-once wins).
        """
        stale_activity = now_utc_naive() - timedelta(
            seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 60
        )
        # Root terminal + descendant non-terminal (waiting_children),
        # both carrying the stale anchor.
        _insert_instance(
            engine,
            "root-quiet",
            status="completed",
            last_activity_at=stale_activity,
        )
        _insert_instance(
            engine,
            "child-quiet",
            status="waiting_children",
            parent_id="root-quiet",
            last_activity_at=stale_activity,
        )
        repo = SQLModelInstanceRepository(engine=engine)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-quiet",
            task_completed_at=None,
        )
        assert not verdict.live, (
            "FIXBACK zombie-backstop: non-terminal descendant + stale "
            f"anchor MUST force terminal. Got: {verdict}"
        )
        assert verdict.timed_out and not verdict.error, (
            "FIXBACK zombie-backstop: timed_out=True MUST be set on "
            "this shape (operator-grep tag for backstop fires) AND "
            f"the row must not be in fail-open error mode. Got: {verdict}"
        )

    @pytest.mark.asyncio
    async def test_all_terminal_stale_anchor_does_not_carry_timed_out(
        self, engine,
    ):
        """FIXBACK (2026-09-28) — all-terminal + stale anchor MUST NOT
        carry ``timed_out=True`` (the anchor plays no role here; the
        row is dead on its own legs). The natural notify path drains
        the row immediately; the operator-grep tag
        ``ORPHAN_RELEASED`` is reserved for genuine zombie cases (a
        row that had to be held by the guard for ≥6h before
        finalizing).

        The pre-fixback ``test_backstop_fires_when_tree_actually_quiet``
        pinned WRONG semantics (all-terminal + stale → timed_out=True).
        The FIXBACK splits the assertion in two: this test pins
        all-terminal + stale → ``live=False, timed_out=False`` (anchor
        ignored), and ``test_zombie_backstop_fires_on_non_terminal_stale``
        above pins the true zombie shape.
        """
        stale_activity = now_utc_naive() - timedelta(
            seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 60
        )
        _insert_instance(
            engine,
            "root-quietall",
            status="completed",
            last_activity_at=stale_activity,
        )
        _insert_instance(
            engine,
            "child-quietall",
            status="completed",
            parent_id="root-quietall",
            last_activity_at=stale_activity,
        )
        repo = SQLModelInstanceRepository(engine=engine)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-quietall",
            task_completed_at=None,
        )
        assert not verdict.live, (
            "FIXBACK: all-terminal tree + stale anchor MUST finalize "
            f"immediately (live=False). Got: {verdict}"
        )
        assert not verdict.timed_out, (
            "FIXBACK: timed_out=True is the zombie-backstop tag — "
            "RESERVED for the non-terminal-stale-anchor shape. "
            "All-terminal trees MUST NOT carry it (the anchor plays "
            f"no role here). Got: {verdict}"
        )

    @pytest.mark.asyncio
    async def test_missing_anchor_does_not_open_the_door(self, engine):
        """No ``completed_at`` anchor = data gap, not zombie evidence —
        the backstop must NOT fire; the live legs still hold."""
        _insert_instance(engine, "root-6", status="waiting_children")
        repo = SQLModelInstanceRepository(engine=engine)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-6",
            task_completed_at=None,
        )
        assert verdict.live and not verdict.timed_out

    @pytest.mark.asyncio
    async def test_repo_error_fails_open(self, engine):
        repo = MagicMock(spec=SQLModelInstanceRepository)
        repo.get_tree_ids_permanent = MagicMock(
            side_effect=RuntimeError("db exploded")
        )
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-7",
            task_completed_at=now_utc_naive(),
        )
        assert not verdict.live
        assert verdict.error, "guard errors must fail OPEN (finalize)"

    @pytest.mark.asyncio
    async def test_unwired_repo_fails_open(self):
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=None,
            instance_id="root-8",
            task_completed_at=now_utc_naive(),
        )
        assert not verdict.live
        assert verdict.error


# ─────────────────────────────────────────────────────────────────────────────
# Part B — drift Pattern f2 site
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Part B — drift Pattern f2 site (REAL delivery chain)
# ─────────────────────────────────────────────────────────────────────────────


class TestPatternF2MissionLiveGuard:
    """Every test here runs the REAL notify chain: real
    ``WorkResolverService`` → real ``JobQueueService.notify_watchers``
    → real ``notify_work_watchers`` → real watcher-row CAS claim —
    only ``enqueue_message`` is a transport-boundary mock. Durable
    state is witnessed in the ``job_watchers`` repository."""

    @pytest.mark.asyncio
    async def test_mid_mission_leader_not_finalized(
        self, engine, repository, task_repository, lock_repo,
        instance_repo,
    ):
        """(i)+(4) Leader turn ends with children running → f2 sweep
        must NOT emit ``completed ✓``, must leave the JobItem ACTIVE,
        and the mission watcher row must SURVIVE IN THE REPOSITORY.

        The guard is then proven to be the ONLY difference: once the
        mission goes truly terminal, the very same settled state
        delivers exactly once through the real claim-first CAS path.
        """
        from unittest.mock import patch

        watcher_repo = JobWatcherRepository(engine)
        queue_service, transport = _make_real_queue_service(
            engine, repository, task_repository, instance_repo,
        )

        _insert_instance(engine, "leader-1", status="running")
        _insert_instance(
            engine, "worker-1", status="running", parent_id="leader-1"
        )
        _insert_job_item(engine, job_id="job-mlg-1", instance_id="leader-1")
        _insert_completed_task(
            engine,
            work_id="job-mlg-1",
            instance_id="leader-1",
            completed_at=now_utc_naive() - timedelta(seconds=300),
        )
        _add_watch(
            watcher_repo,
            "job-mlg-1",
            events=["mission_terminal", "completed"],
        )

        service = _make_recovery_service(
            repository, task_repository, lock_repo, instance_repo,
            queue_service,
        )
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        # No finalize; the observable skip seam.
        assert not [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
        ]
        skips = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_skipped_mission_live"
            and d.get("job_id") == "job-mlg-1"
        ]
        assert skips, f"mission-live skip must be observable: {stats['details']}"
        # JobItem stays ACTIVE — correct-by-design mid-mission.
        assert repository.get("job-mlg-1").admission_state == (
            AdmissionState.ACTIVE.value
        )
        # DURABLE WITNESS: the watcher row survives IN THE REPOSITORY
        # (guard held the sweep BEFORE notify → no CAS claim ran).
        _assert_rows_survive(watcher_repo, "job-mlg-1")
        # No emission of any kind (terminal OR in_progress) from the
        # sweep — the turn-end 'in progress ⟳' lane is not touched.
        assert _job_event_calls(transport) == []

        # ── Guard is the only difference: mission goes truly
        # terminal (root + descendant terminal, bus quiet) → the same
        # settled state now delivers exactly once via the REAL path.
        _set_instance_status(engine, "leader-1", "completed")
        _set_instance_status(engine, "worker-1", "completed")
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            stats2 = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        done = [
            d for d in stats2["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-mlg-1"
        ]
        assert done, f"mission-dead shape must finalize: {stats2['details']}"
        assert repository.get("job-mlg-1").admission_state == (
            AdmissionState.DONE.value
        )
        calls = _job_event_calls(transport)
        assert len(calls) == 1, (
            f"exactly ONE terminal delivery expected, got {len(calls)}"
        )
        _assert_completed_event(calls[0], "job-mlg-1", "watcher-inst")
        _assert_rows_claimed(watcher_repo, "job-mlg-1")

        # Idempotency: a THIRD sweep on the settled state no-ops —
        # the claim-first CAS already consumed the row.
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )
        assert len(_job_event_calls(transport)) == 1
        _assert_rows_claimed(watcher_repo, "job-mlg-1")

    @pytest.mark.asyncio
    async def test_true_terminal_finalizes_and_notifies_once(
        self, engine, repository, task_repository, lock_repo,
        instance_repo,
    ):
        """(ii) True mission terminal → terminal delivered exactly once
        via the REAL fan-out path; an adversarial SECOND sweep against
        the same settled state cannot produce a duplicate."""
        from unittest.mock import patch

        watcher_repo = JobWatcherRepository(engine)
        queue_service, transport = _make_real_queue_service(
            engine, repository, task_repository, instance_repo,
        )

        _insert_instance(engine, "leader-2", status="completed")
        _insert_job_item(engine, job_id="job-mlg-2", instance_id="leader-2")
        _insert_completed_task(
            engine,
            work_id="job-mlg-2",
            instance_id="leader-2",
            completed_at=now_utc_naive() - timedelta(seconds=300),
        )
        _add_watch(watcher_repo, "job-mlg-2")

        service = _make_recovery_service(
            repository, task_repository, lock_repo, instance_repo,
            queue_service,
        )
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        done = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-mlg-2"
        ]
        assert done, f"mission-dead shape must finalize: {stats['details']}"
        assert repository.get("job-mlg-2").admission_state == (
            AdmissionState.DONE.value
        )
        # Byte-path + exactly-once on the REAL delivery chain.
        calls = _job_event_calls(transport)
        assert len(calls) == 1, (
            f"exactly ONE terminal delivery expected, got {len(calls)}"
        )
        _assert_completed_event(calls[0], "job-mlg-2", "watcher-inst")
        _assert_rows_claimed(watcher_repo, "job-mlg-2")

        # ADVERSARIAL double-sweep: same settled state, sweep again —
        # the JobItem is DONE (no longer an f2 candidate) and the
        # watcher rows are CAS-consumed. Zero new deliveries.
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            stats2 = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )
        assert not [
            d for d in stats2["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
        ], "settled job must not re-appear as an f2 candidate"
        assert len(_job_event_calls(transport)) == 1
        _assert_rows_claimed(watcher_repo, "job-mlg-2")

    @pytest.mark.asyncio
    async def test_true_terminal_two_watchers_deliver_once_each(
        self, engine, repository, task_repository, lock_repo,
        instance_repo,
    ):
        """(ii) Two watcher rows on ONE job (distinct watching
        instances) → one sweep delivers to EACH exactly once, and the
        claim-first CAS consumes both rows in the same pass; a second
        sweep delivers nothing more."""
        from unittest.mock import patch

        watcher_repo = JobWatcherRepository(engine)
        queue_service, transport = _make_real_queue_service(
            engine, repository, task_repository, instance_repo,
        )

        _insert_instance(engine, "leader-2b", status="completed")
        _insert_job_item(engine, job_id="job-mlg-2b", instance_id="leader-2b")
        _insert_completed_task(
            engine,
            work_id="job-mlg-2b",
            instance_id="leader-2b",
            completed_at=now_utc_naive() - timedelta(seconds=300),
        )
        _add_watch(watcher_repo, "job-mlg-2b", instance_id="watcher-a")
        _add_watch(
            watcher_repo,
            "job-mlg-2b",
            instance_id="watcher-b",
            events=["mission_terminal", "completed"],
        )

        service = _make_recovery_service(
            repository, task_repository, lock_repo, instance_repo,
            queue_service,
        )
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        calls = _job_event_calls(transport)
        assert len(calls) == 2, (
            f"one delivery PER watcher expected, got {len(calls)}"
        )
        delivered_to = {c.kwargs["instance_id"] for c in calls}
        assert delivered_to == {"watcher-a", "watcher-b"}
        for c in calls:
            _assert_completed_event(c, "job-mlg-2b", c.kwargs["instance_id"])
        _assert_rows_claimed(watcher_repo, "job-mlg-2b")

        # Second sweep — rows consumed, no duplicate deliveries.
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )
        assert len(_job_event_calls(transport)) == 2

    @pytest.mark.asyncio
    async def test_crash_before_finalize_restart_sweep_delivers_exactly_once(
        self, engine, repository, task_repository, lock_repo,
        instance_repo,
    ):
        """(iii) THE original missing-report non-regression, built as
        the real crash→restart narrative:

        A mission runs to true completion (instance tree ALL-terminal,
        bus quiet) but the daemon crashes BETWEEN mission end and the
        JobItem finalize — the row is left stuck ACTIVE with its
        backing Task COMPLETED and an UNCLAIMED mission watcher row.

        On restart, a FRESH recovery service + queue service over the
        surviving durable state runs the drift sweep (the restart-path
        owner of the stuck-ACTIVE shape — a dual-backed ACTIVE JobItem
        resolves 'processing', so ``reconcile_terminal_watches``
        correctly skips it and Pattern f2 owns the repair). The sweep
        must finalize AND deliver the terminal through the REAL
        notify path: watcher row CAS-claimed exactly once, one
        ``[JOB_EVENT] ... completed ✓`` emission, byte-path + source
        contract pinned, and an adversarial second sweep cannot
        duplicate the delivery."""
        from unittest.mock import patch

        watcher_repo = JobWatcherRepository(engine)

        # ── Durable state the crash leaves behind ──
        _insert_instance(engine, "leader-crash", status="completed")
        _insert_instance(
            engine,
            "worker-crash",
            status="completed",
            parent_id="leader-crash",
        )
        _insert_job_item(
            engine, job_id="job-crash-1", instance_id="leader-crash"
        )
        _insert_completed_task(
            engine,
            work_id="job-crash-1",
            instance_id="leader-crash",
            completed_at=now_utc_naive() - timedelta(seconds=300),
        )
        _add_watch(
            watcher_repo,
            "job-crash-1",
            events=["mission_terminal", "completed"],
        )
        # Pre-restart sanity: the stranded shape is really stranded.
        assert repository.get("job-crash-1").admission_state == (
            AdmissionState.ACTIVE.value
        )
        _assert_rows_survive(watcher_repo, "job-crash-1")

        # ── RESTART: fresh services over the surviving state (no
        # in-memory carryover — the repos re-read the same engine). ──
        queue_service, transport = _make_real_queue_service(
            engine, repository, task_repository, instance_repo,
        )
        recovery = _make_recovery_service(
            repository, task_repository, lock_repo, instance_repo,
            queue_service,
        )
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            stats = await recovery.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        done = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-crash-1"
        ]
        assert done, (
            f"crash-after-mission-end MUST finalize on the restart "
            f"sweep (the original missing-report bug must not "
            f"regress): {stats['details']}"
        )
        assert repository.get("job-crash-1").admission_state == (
            AdmissionState.DONE.value
        )
        calls = _job_event_calls(transport)
        assert len(calls) == 1, (
            f"exactly ONE terminal delivery expected, got {len(calls)}"
        )
        _assert_completed_event(calls[0], "job-crash-1", "watcher-inst")
        _assert_rows_claimed(watcher_repo, "job-crash-1")

        # Adversarial second sweep — no duplicate terminal.
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            await recovery.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )
        assert len(_job_event_calls(transport)) == 1

    @pytest.mark.asyncio
    async def test_revive_mid_mission_holds_then_terminal_delivers(
        self, engine, repository, task_repository, lock_repo,
        instance_repo,
    ):
        """(iv) Instance REVIVED mid-mission (root terminal → RUNNING
        again) → the guard holds the finalize and the watcher row
        survives; once the true terminal lands, the terminal is still
        delivered exactly once via the REAL claim path."""
        from unittest.mock import patch

        watcher_repo = JobWatcherRepository(engine)
        queue_service, transport = _make_real_queue_service(
            engine, repository, task_repository, instance_repo,
        )

        _insert_job_item(engine, job_id="job-mlg-3", instance_id="leader-3")
        _insert_completed_task(
            engine,
            work_id="job-mlg-3",
            instance_id="leader-3",
            completed_at=now_utc_naive() - timedelta(seconds=300),
        )
        _add_watch(watcher_repo, "job-mlg-3")

        service = _make_recovery_service(
            repository, task_repository, lock_repo, instance_repo,
            queue_service,
        )

        # Phase 1 — root REVIVED mid-mission (running): guard skips.
        _insert_instance(engine, "leader-3", status="running")
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )
        assert repository.get("job-mlg-3").admission_state == (
            AdmissionState.ACTIVE.value
        )
        assert _job_event_calls(transport) == []
        _assert_rows_survive(watcher_repo, "job-mlg-3")

        # Phase 2 — the true terminal lands (root completes): the
        # terminal is delivered (at-least-once preserved).
        _set_instance_status(engine, "leader-3", "completed")
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )
        assert repository.get("job-mlg-3").admission_state == (
            AdmissionState.DONE.value
        )
        calls = _job_event_calls(transport)
        assert len(calls) == 1
        _assert_completed_event(calls[0], "job-mlg-3", "watcher-inst")
        _assert_rows_claimed(watcher_repo, "job-mlg-3")
        assert [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
        ], "revive→terminal must finalize (backstop preserved)"

    @pytest.mark.asyncio
    async def test_orphan_timeout_fires_when_tree_actually_quiet(
        self, engine, repository, task_repository, lock_repo,
        instance_repo,
    ):
        """(v, U7) The backstop fires only when the tree is genuinely
        quiet — every member terminal AND the freshest
        ``last_activity_at`` older than the window. The drift Pattern
        f2 site must therefore propagate the row all the way through
        the REAL notify path on that exact tree-shape.

        The pre-U7 anchor-based test was inverted: a running root +
        stale ``task_completed_at`` would fire the backstop under
        the old design but HOLDS under U7 (live leg wins)."""
        from unittest.mock import patch

        watcher_repo = JobWatcherRepository(engine)
        queue_service, transport = _make_real_queue_service(
            engine, repository, task_repository, instance_repo,
        )

        # U7-shaped tree: leader settled + every descendant settled +
        # last_activity_at older than the zombie window → backstop
        # fires and the drift site proceeds with finalize.
        old_activity = now_utc_naive() - timedelta(
            seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 300
        )
        _insert_instance(
            engine, "leader-5", status="completed",
            last_activity_at=old_activity,
        )
        _insert_job_item(engine, job_id="job-mlg-5", instance_id="leader-5")
        _insert_completed_task(
            engine,
            work_id="job-mlg-5",
            instance_id="leader-5",
            completed_at=old_activity,
        )
        _add_watch(watcher_repo, "job-mlg-5")

        service = _make_recovery_service(
            repository, task_repository, lock_repo, instance_repo,
            queue_service,
        )
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        done = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-mlg-5"
        ]
        assert done, (
            f"U7 zombie backstop must open the finalize door when "
            f"the tree is genuinely quiet (every member terminal + "
            f"freshest last_activity_at older than the window): "
            f"{stats['details']}"
        )
        assert repository.get("job-mlg-5").admission_state == (
            AdmissionState.DONE.value
        )
        calls = _job_event_calls(transport)
        assert len(calls) == 1
        _assert_completed_event(calls[0], "job-mlg-5", "watcher-inst")
        _assert_rows_claimed(watcher_repo, "job-mlg-5")

    @pytest.mark.asyncio
    async def test_guard_error_fails_open_to_finalize(
        self, engine, repository, task_repository, lock_repo,
        instance_repo,
    ):
        """(vi) Resolver/DB error inside the guard → fail-open: the
        finalize fires through the REAL notify path (an extra
        premature event is acceptable; a missing terminal is NOT)."""
        from unittest.mock import patch

        broken_repo = MagicMock(spec=SQLModelInstanceRepository)
        broken_repo.get_tree_ids_permanent = MagicMock(
            side_effect=RuntimeError("resolver exploded")
        )
        watcher_repo = JobWatcherRepository(engine)
        queue_service, transport = _make_real_queue_service(
            engine, repository, task_repository, instance_repo,
        )

        _insert_instance(engine, "leader-6", status="running")
        _insert_job_item(engine, job_id="job-mlg-6", instance_id="leader-6")
        _insert_completed_task(
            engine,
            work_id="job-mlg-6",
            instance_id="leader-6",
            completed_at=now_utc_naive() - timedelta(seconds=300),
        )
        _add_watch(watcher_repo, "job-mlg-6")

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=broken_repo,
            job_queue_service=queue_service,
            task_repository=task_repository,
            stale_task_recovery=None,
        )
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        done = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-mlg-6"
        ]
        assert done, (
            f"guard errors must FAIL OPEN to finalize: {stats['details']}"
        )
        calls = _job_event_calls(transport)
        assert len(calls) == 1
        _assert_completed_event(calls[0], "job-mlg-6", "watcher-inst")
        _assert_rows_claimed(watcher_repo, "job-mlg-6")


# ─────────────────────────────────────────────────────────────────────────────
# Part C — boot-sweep parity (``reconcile_terminal_watches``; REAL resolver
# + REAL notify chain)
# ─────────────────────────────────────────────────────────────────────────────


class TestBootSweepMissionLiveGuard:
    """The sweep runs against the REAL ``WorkResolverService`` over
    real Task + JobItem rows and the REAL notify chain; only
    ``enqueue_message`` is a transport-boundary mock.

    Mechanic note (verified against ``WorkResolverService.resolve_work``):
    a dual-backed work unit resolves to the JOBITEM record, so the boot
    sweep only ever fires when the JobItem row itself is settled — the
    stuck-ACTIVE crash shape belongs to the drift f2 sweep (Part B,
    ``test_crash_before_finalize_restart_sweep_delivers_exactly_once``).
    The boot sweep's crash narrative is crash-AFTER-finalize: the row
    settled but the notify was lost."""

    def _boot_sweep_service(
        self, engine, repository, task_repository, instance_repo,
    ) -> tuple[JobQueueService, MagicMock, JobWatcherRepository]:
        service, transport = _make_real_queue_service(
            engine, repository, task_repository, instance_repo,
        )
        return service, transport, service._watcher_repo

    @pytest.mark.asyncio
    async def test_boot_sweep_holds_live_mission_then_delivers_once(
        self, engine, repository, task_repository, instance_repo,
    ):
        """(i)+(4) boot twin — Mission LIVE + settled receipt + active
        mission_terminal watch → NO false terminal at boot, watcher
        row survives IN THE REPOSITORY. The guard is proven to be the
        only difference: once the mission goes terminal, the same
        settled state delivers exactly once through the real CAS
        claim path."""
        service, transport, watcher_repo = self._boot_sweep_service(
            engine, repository, task_repository, instance_repo,
        )

        _insert_instance(engine, "leader-boot-1", status="waiting_children")
        _insert_job_item(
            engine,
            job_id="job-boot-1",
            instance_id="leader-boot-1",
            admission_state=AdmissionState.DONE.value,
        )
        _insert_completed_task(
            engine,
            work_id="job-boot-1",
            instance_id="leader-boot-1",
            completed_at=now_utc_naive() - timedelta(seconds=120),
        )
        _add_watch(
            watcher_repo,
            "job-boot-1",
            events=["mission_terminal", "completed"],
        )
        # Sanity: the work row really resolves terminal (settled
        # receipt) — the ONLY thing standing between the watcher and
        # a premature event is the guard.
        record = service._work_resolver.resolve_work("job-boot-1")
        assert record.status == "completed"
        assert record.job_type == "task"

        count = await service.reconcile_terminal_watches()

        assert count == 0
        assert _job_event_calls(transport) == []
        # DURABLE WITNESS: rows survive in the repository.
        _assert_rows_survive(watcher_repo, "job-boot-1")

        # Guard is the only difference: mission goes terminal → the
        # same settled state delivers exactly once.
        _set_instance_status(engine, "leader-boot-1", "completed")
        count = await service.reconcile_terminal_watches()

        assert count == 1
        calls = _job_event_calls(transport)
        assert len(calls) == 1
        _assert_completed_event(calls[0], "job-boot-1", "watcher-inst")
        _assert_rows_claimed(watcher_repo, "job-boot-1")

        # Adversarial re-sweep — nothing left to deliver.
        assert await service.reconcile_terminal_watches() == 0
        assert len(_job_event_calls(transport)) == 1

    @pytest.mark.asyncio
    async def test_crash_after_finalize_restart_boot_sweep_delivers_exactly_once(
        self, engine, repository, task_repository, instance_repo,
    ):
        """(iii) boot half of the crash narrative — the mission
        genuinely ended (tree all-terminal, bus quiet), the JobItem
        finalized (DONE), but the daemon crashed BEFORE the terminal
        notification reached the watcher (row stranded, unclaimed).
        On restart the boot sweep ``reconcile_terminal_watches`` fires
        through the REAL notify path: watcher row CAS-claimed exactly
        once, one ``[JOB_EVENT] ... completed ✓`` emission, and an
        adversarial second sweep cannot duplicate it (at-least-once
        ACROSS restarts — the original missing-report bug's boot
        vector must not regress)."""
        service, transport, watcher_repo = self._boot_sweep_service(
            engine, repository, task_repository, instance_repo,
        )

        _insert_instance(engine, "leader-boot-2", status="completed")
        _insert_instance(
            engine,
            "worker-boot-2",
            status="completed",
            parent_id="leader-boot-2",
        )
        _insert_job_item(
            engine,
            job_id="job-boot-2",
            instance_id="leader-boot-2",
            admission_state=AdmissionState.DONE.value,
        )
        _insert_completed_task(
            engine,
            work_id="job-boot-2",
            instance_id="leader-boot-2",
            completed_at=now_utc_naive() - timedelta(seconds=120),
        )
        _add_watch(
            watcher_repo,
            "job-boot-2",
            events=["mission_terminal", "completed"],
        )
        # Pre-restart sanity: the stranded shape is really stranded.
        _assert_rows_survive(watcher_repo, "job-boot-2")
        assert _job_event_calls(transport) == []

        # RESTART sweep.
        count = await service.reconcile_terminal_watches()

        assert count == 1
        calls = _job_event_calls(transport)
        assert len(calls) == 1, (
            f"exactly ONE terminal delivery expected, got {len(calls)}"
        )
        _assert_completed_event(calls[0], "job-boot-2", "watcher-inst")
        _assert_rows_claimed(watcher_repo, "job-boot-2")

        # Adversarial second sweep — rows consumed, no duplicates.
        assert await service.reconcile_terminal_watches() == 0
        assert len(_job_event_calls(transport)) == 1
        _assert_rows_claimed(watcher_repo, "job-boot-2")

    @pytest.mark.asyncio
    async def test_boot_sweep_two_watchers_deliver_once_each(
        self, engine, repository, task_repository, instance_repo,
    ):
        """(ii) boot twin — two watcher rows on ONE settled job → the
        sweep delivers to EACH exactly once and the claim-first CAS
        consumes both rows in one pass; a second sweep delivers
        nothing more."""
        service, transport, watcher_repo = self._boot_sweep_service(
            engine, repository, task_repository, instance_repo,
        )

        _insert_instance(engine, "leader-boot-3", status="completed")
        _insert_job_item(
            engine,
            job_id="job-boot-3",
            instance_id="leader-boot-3",
            admission_state=AdmissionState.DONE.value,
        )
        _insert_completed_task(
            engine,
            work_id="job-boot-3",
            instance_id="leader-boot-3",
            completed_at=now_utc_naive() - timedelta(seconds=120),
        )
        _add_watch(watcher_repo, "job-boot-3", instance_id="watcher-a")
        _add_watch(
            watcher_repo,
            "job-boot-3",
            instance_id="watcher-b",
            events=["mission_terminal", "completed"],
        )

        count = await service.reconcile_terminal_watches()

        assert count == 2
        calls = _job_event_calls(transport)
        assert len(calls) == 2
        delivered_to = {c.kwargs["instance_id"] for c in calls}
        assert delivered_to == {"watcher-a", "watcher-b"}
        for c in calls:
            _assert_completed_event(c, "job-boot-3", c.kwargs["instance_id"])
        _assert_rows_claimed(watcher_repo, "job-boot-3")

        # Second sweep — no duplicate deliveries.
        assert await service.reconcile_terminal_watches() == 0
        assert len(_job_event_calls(transport)) == 2

    @pytest.mark.asyncio
    async def test_boot_sweep_receipt_kind_semantics_unchanged(
        self, engine, repository, task_repository, instance_repo,
    ):
        """(iv)c Receipt-kind watches (no ``mission_terminal`` event)
        are UNCHANGED: the terminal fires through the REAL notify path
        even when the mission instance is still live (the sweep-level
        guard is scoped to mission-keyed watches only)."""
        service, transport, watcher_repo = self._boot_sweep_service(
            engine, repository, task_repository, instance_repo,
        )

        _insert_instance(engine, "leader-boot-4", status="running")
        _insert_job_item(
            engine,
            job_id="job-boot-4",
            instance_id="leader-boot-4",
            admission_state=AdmissionState.DONE.value,
        )
        _insert_completed_task(
            engine,
            work_id="job-boot-4",
            instance_id="leader-boot-4",
            completed_at=now_utc_naive() - timedelta(seconds=120),
        )
        # Default (receipt) events only — NO mission_terminal.
        _add_watch(watcher_repo, "job-boot-4", instance_id="watcher-inst")

        count = await service.reconcile_terminal_watches()

        assert count == 1
        calls = _job_event_calls(transport)
        assert len(calls) == 1
        _assert_completed_event(calls[0], "job-boot-4", "watcher-inst")
        _assert_rows_claimed(watcher_repo, "job-boot-4")
