"""Fix e8a61da3 (Option A) — usage-limit retry-supersede watch recovery.

Regression tests reproducing the EXACT double-silence chain (RCA job
043d64a8; design source of truth:
``.agents/shared/planning/watch-loss-fix-options/options-matrix.md``,
user-picked A+E):

    usage-limit cancel → F6 re-key → retry-receipt settle →
    mission-terminal MUST notify

The chain before the fix: the usage-limit deferral lane cancelled the
parent task silently (worker_pool.py — "per-attempt observables are
ONE log line"), the F6 re-key moved the transport-only watcher row
onto the retry child's fresh work_id, the retry's own settle
CAS-consumed the row (C1 shape (1) — fully claimable), and the true
mission terminal found NO row → ZERO terminal events (the
double-silence).

The fix (Option A): the F6 re-key transaction (``RetryTurn.apply``)
quarantines each re-keyed watcher into ``stored_watches`` AND arms
``mission_terminal`` onto the row BEFORE the re-key. The re-armed row
survives the retry's settle (C1 matching_readonly — deliver-but-keep)
and is claimed exactly once at the true mission terminal.

Covers, by test:

* AC1 — the full chain: settle mid-mission delivers the transport
  envelope but KEEPS the row; the mission-terminal fire claims +
  delivers exactly once. Runs the REAL ``notify_work_watchers`` C1
  partition + the REAL ``evaluate_mission_live`` guard.
* AC2 — exactly-one stored row per (original work, watcher) even
  under a duplicate deferral (first-store-wins fence); chain
  deferrals (W1→W2→W3) store DISTINCT rows per hop.
* AC3 — M2 boundary pin: default-flag retries (the timeout lane /
  stale-recovery shapes) arm NOTHING — no stored row, no
  ``mission_terminal`` in the row's events. ``force_cancel_and_
  schedule_retry`` equally stays unarmed.
* AC4 — GC leg (depth 4): carrier alive → released; carrier lost +
  retry never arrived → rehydrated as a pure ``mission_terminal`` row
  on the ORIGINAL work_id; carrier lost + retry terminal + mission
  terminal → released (legitimate consume, no double delivery).

A/B convention: every test fails on the pre-fix tree (no
``stored_watches`` table / no arming) and passes on the post-fix tree.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# Model imports register tables on SQLModel.metadata BEFORE the
# session-scoped engine fixture runs create_all (conftest contract).
from daemon.repositories.job_queue.stored_watch_models import (  # noqa: F401
    StoredWatch,
)
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_repository import (
    JobWatcherRepository,
)
from daemon.repositories.job_queue.stored_watch_repository import (
    StoredWatchRepository,
)
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_queue_service import JobQueueService
from daemon.services.work_notifier import notify_work_watchers
from daemon.services.work_resolver import WorkResolverService


# ─────────────────────────────────────────────────────────────────────────────
# Local engine (session-scoped conftest engine is shared; these tests
# mutate watcher/instance state in ways that read cleaner isolated).
# ─────────────────────────────────────────────────────────────────────────────


def _make_engine():
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def iso_engine():
    eng = _make_engine()
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _seed_instance(
    eng,
    instance_id: str,
    *,
    parent_id: str | None = None,
    status: str = "running",
    last_activity: str | None = "2026-10-11T08:00:00",
) -> None:
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO instances (instance_id, parent_id, agent_id, "
                "agent_dir, status, project_id, created_at, updated_at, "
                "version, last_activity_at) VALUES "
                "(:iid, :pid, 'developer', 'agents/developer', :status, "
                "'p', '2026-10-11T07:00:00', '2026-10-11T07:00:00', 1, "
                ":la)"
            ),
            {
                "iid": instance_id,
                "pid": parent_id,
                "status": status,
                "la": last_activity,
            },
        )


def _seed_task(
    eng,
    *,
    work_id: str,
    instance_id: str,
    status: str = "running",
    retry_count: int = 0,
) -> int:
    with eng.begin() as c:
        result = c.execute(
            text(
                "INSERT INTO task (task_type, instance_id, message_id, "
                "status, retry_count, created_at, cancel_requested, "
                "retry_scheduled, work_id, is_deferred, is_background) "
                "VALUES ('process_message', :iid, 'm1', :status, :rc, "
                "'2026-10-11T08:00:00', 0, 0, :wid, 0, 0)"
            ),
            {"iid": instance_id, "status": status, "rc": retry_count, "wid": work_id},
        )
        return result.lastrowid


def _seed_default_watch(eng, job_id: str, watcher: str) -> None:
    """Seed a DEFAULT (transport-only, 10-event) watcher row."""
    from daemon.repositories.job_queue.watcher_models import (
        ALL_WATCHABLE_EVENTS,
    )

    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO job_watchers (watch_id, job_id, instance_id, "
                "watch_events, created_at) VALUES "
                "(:wid, :jid, :inst, :ev, '2026-10-11T08:00:00')"
            ),
            {
                "wid": f"watch-{job_id}-{watcher}",
                "jid": job_id,
                "inst": watcher,
                "ev": json.dumps(list(ALL_WATCHABLE_EVENTS)),
            },
        )


def _get_watch_events(eng, job_id: str) -> list[str]:
    with eng.begin() as c:
        row = c.execute(
            text("SELECT watch_events FROM job_watchers WHERE job_id = :jid"),
            {"jid": job_id},
        ).fetchone()
    if row is None:
        return []
    raw = row.watch_events
    return json.loads(raw) if isinstance(raw, str) else list(raw or [])


def _stored_rows(eng) -> list[tuple]:
    with eng.begin() as c:
        return c.execute(
            text(
                "SELECT original_work_id, retry_work_id, watcher_instance_id, "
                "mission_instance_id, work_instance_id, status "
                "FROM stored_watches ORDER BY created_at"
            )
        ).fetchall()


class _FakeInstanceManager:
    """Minimal instance_manager double for ``notify_work_watchers``.

    Carries the real ``_instance_repository`` (the mission-live guard
    walks it) and records ``enqueue_message`` deliveries for
    assertions.
    """

    def __init__(self, instance_repo):
        self._instance_repository = instance_repo
        self.deliveries: list[tuple[str, str]] = []
        self.enqueue_message = AsyncMock(side_effect=self._enqueue)

    async def _enqueue(self, *, instance_id, message, source=None, **kw):
        self.deliveries.append((instance_id, message))
        return None


def _notify_deps(eng):
    task_repo = TaskRepository(eng)
    job_repo = JobRepository(eng)  # REAL — a mock poisons resolve_work
    instance_repo = SQLModelInstanceRepository(engine=eng)
    resolver = WorkResolverService(
        task_repo=task_repo,
        job_repo=job_repo,
        instance_repo=instance_repo,
    )
    watcher_repo = JobWatcherRepository(engine=eng)
    manager = _FakeInstanceManager(instance_repo)
    return manager, resolver, watcher_repo


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — the full chain: cancel → re-key → settle → mission terminal
# ─────────────────────────────────────────────────────────────────────────────


class TestRetrySupersedeChain:
    @pytest.mark.asyncio
    async def test_settle_keeps_row_mission_terminal_fires_exactly_once(
        self, iso_engine
    ):
        """The EXACT double-silence chain, end-to-end through the real
        C1 partition and the real mission-live guard.

        Pre-fix: the settle consumed the transport-only row and the
        mission-terminal fire found zero watchers. Post-fix: the
        re-armed row is delivered-but-KEPT at settle, then claimed +
        delivered exactly once at the true mission terminal.
        """
        eng = iso_engine
        _seed_instance(eng, "leader-1", status="running")
        _seed_instance(eng, "worker-1", parent_id="leader-1", status="running")
        task_id = _seed_task(eng, work_id="W1", instance_id="worker-1")
        _seed_default_watch(eng, "W1", "leader-1")

        repo = TaskRepository(eng)
        # (i) usage-limit deferral cancel — rearm path.
        retry = repo.schedule_retry(
            task_id=task_id, max_retries=3, rearm_watchers=True
        )
        assert retry is not None and retry.work_id != "W1"

        # Quarantine row exists with BOTH linkages (depth 3).
        rows = _stored_rows(eng)
        assert len(rows) == 1
        assert rows[0].original_work_id == "W1"
        assert rows[0].retry_work_id == retry.work_id
        assert rows[0].watcher_instance_id == "leader-1"
        assert rows[0].mission_instance_id == "leader-1"  # tree ROOT
        assert rows[0].work_instance_id == "worker-1"
        assert rows[0].status == "armed"

        # The watcher row moved to the retry AND carries the survival
        # event (depth 2) on top of its original 10 events.
        events = _get_watch_events(eng, retry.work_id)
        assert "mission_terminal" in events
        assert len(events) == 11  # 10 originals + mission_terminal

        # (ii) retry-receipt settle mid-mission — transport terminal
        # fires while the mission (worker tree) is still live.
        with eng.begin() as c:
            c.execute(
                text("UPDATE task SET status='completed' WHERE work_id=:w"),
                {"w": retry.work_id},
            )
        manager, resolver, watcher_repo = _notify_deps(eng)
        delivered = await notify_work_watchers(
            retry.work_id,
            "completed",
            instance_manager=manager,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
            result_summary="retry settled",
        )
        # Settle delivered the transport envelope exactly once…
        assert delivered == 1
        assert len(manager.deliveries) == 1
        assert "completed ✓" in manager.deliveries[0][1]
        # …but the row SURVIVED (matching_readonly — deliver-but-keep).
        assert _get_watch_events(eng, retry.work_id) != []

        # (iii) TRUE mission terminal — the whole work tree flips.
        with eng.begin() as c:
            c.execute(
                text("UPDATE instances SET status='completed' "
                     "WHERE instance_id IN ('leader-1','worker-1')")
            )
        terminal_delivered = await notify_work_watchers(
            retry.work_id,
            "completed",
            instance_manager=manager,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
            result_summary="mission over",
        )
        # The re-keyed row reaches the true terminal: claimed + fired.
        assert terminal_delivered == 1
        assert len(manager.deliveries) == 2
        # …and the row is now GONE (CAS-claimed exactly once).
        assert _get_watch_events(eng, retry.work_id) == []
        # A third fire finds nothing (exactly-once, no duplicates).
        again = await notify_work_watchers(
            retry.work_id,
            "completed",
            instance_manager=manager,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
        )
        assert again == 0
        assert len(manager.deliveries) == 2


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — first-store-wins fence + chain deferrals
# ─────────────────────────────────────────────────────────────────────────────


class TestDuplicateDeferralFence:
    def test_duplicate_rearm_on_same_parent_stores_exactly_one_row(
        self, iso_engine
    ):
        """Two re-arm passes over the same (parent, watcher) pair —
        the ON CONFLICT DO NOTHING fence keeps exactly ONE quarantine
        row (the duplicate-deferral edge from the matrix)."""
        from daemon.services.turn_transitions import RetryTurn

        eng = iso_engine
        _seed_instance(eng, "worker-1", status="running")
        _seed_task(eng, work_id="W1", instance_id="worker-1")
        _seed_default_watch(eng, "W1", "leader-1")

        with eng.begin() as conn:
            RetryTurn(
                parent_work_id="W1",
                child_work_id="W2",
                task_repo=None,
                instance_id="worker-1",
                child_kwargs={
                    "task_type": "process_message",
                    "message_id": None,
                    "retry_count": 1,
                    "next_retry_at": None,
                    "created_at": "2026-10-11T08:00:00",
                    "cancel_requested": False,
                    "retry_scheduled": False,
                    "is_deferred": False,
                    "is_background": False,
                },
                rearm_watchers=True,
            ).run(conn)
        with eng.begin() as conn:
            RetryTurn(
                parent_work_id="W1",
                child_work_id="W2-alt",
                task_repo=None,
                instance_id="worker-1",
                child_kwargs={
                    "task_type": "process_message",
                    "message_id": None,
                    "retry_count": 1,
                    "next_retry_at": None,
                    "created_at": "2026-10-11T08:00:00",
                    "cancel_requested": False,
                    "retry_scheduled": False,
                    "is_deferred": False,
                    "is_background": False,
                },
                rearm_watchers=True,
            ).run(conn)

        rows = _stored_rows(eng)
        assert len(rows) == 1  # first-store-wins
        assert rows[0].retry_work_id == "W2"

    def test_chain_deferrals_store_distinct_rows_per_hop(
        self, iso_engine
    ):
        """W1→W2 then W2→W3 (the retry child itself defers again):
        each hop is a distinct (original, retry) pair → two rows; the
        watcher row walks with the chain and keeps its armed events."""
        eng = iso_engine
        _seed_instance(eng, "worker-1", status="running")
        t1 = _seed_task(eng, work_id="W1", instance_id="worker-1")
        _seed_default_watch(eng, "W1", "leader-1")

        repo = TaskRepository(eng)
        second = repo.schedule_retry(
            task_id=t1, max_retries=3, rearm_watchers=True
        )
        with eng.begin() as c:
            t2 = c.execute(
                text("SELECT id FROM task WHERE work_id=:w"),
                {"w": second.work_id},
            ).fetchone().id
            c.execute(
                text("UPDATE task SET status='running' WHERE id=:i"),
                {"i": t2},
            )
        third = repo.schedule_retry(
            task_id=t2, max_retries=3, rearm_watchers=True
        )

        rows = _stored_rows(eng)
        assert [(r.original_work_id, r.retry_work_id) for r in rows] == [
            ("W1", second.work_id),
            (second.work_id, third.work_id),
        ]
        # The watcher row rides the chain: keyed on W3, still armed.
        events = _get_watch_events(eng, third.work_id)
        assert "mission_terminal" in events


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — M2 boundary pin: NO default arming outside the recovery path
# ─────────────────────────────────────────────────────────────────────────────


class TestM2BoundaryPin:
    def test_default_schedule_retry_arms_nothing(self, iso_engine):
        """The default flag (timeout lane / stale-recovery shapes):
        plain F6 re-key, NO quarantine row, NO mission_terminal — the
        M2 opt-in contract stays opt-in outside the recovery path."""
        eng = iso_engine
        _seed_instance(eng, "worker-1", status="running")
        task_id = _seed_task(eng, work_id="W1", instance_id="worker-1")
        _seed_default_watch(eng, "W1", "leader-1")

        repo = TaskRepository(eng)
        retry = repo.schedule_retry(task_id=task_id, max_retries=3)
        assert retry is not None

        assert _stored_rows(eng) == []
        events = _get_watch_events(eng, retry.work_id)
        assert "mission_terminal" not in events
        assert len(events) == 10  # untouched default set

    def test_force_cancel_and_schedule_retry_arms_nothing(self, iso_engine):
        """``force_cancel_and_schedule_retry`` (stale-recovery lane)
        constructs RetryTurn WITHOUT the flag — nothing armed."""
        eng = iso_engine
        _seed_instance(eng, "worker-1", status="running")
        task_id = _seed_task(
            eng, work_id="W1", instance_id="worker-1", status="running"
        )
        _seed_default_watch(eng, "W1", "leader-1")

        repo = TaskRepository(eng)
        retry = repo.force_cancel_and_schedule_retry(
            task_id=task_id, max_retries=3, reason="stale"
        )
        assert retry is not None
        assert _stored_rows(eng) == []
        assert "mission_terminal" not in _get_watch_events(eng, retry.work_id)


# ─────────────────────────────────────────────────────────────────────────────
# AC4 — GC leg (depth 4): retry-never-arrives carrier
# ─────────────────────────────────────────────────────────────────────────────


def _seed_stored_watch(
    eng,
    *,
    original_work_id: str,
    retry_work_id: str,
    watcher: str = "leader-1",
    mission: str = "leader-1",
    work: str = "worker-1",
    age_seconds: int = 7 * 3600,  # past the 6h orphan window
) -> str:
    from daemon.repositories.job_queue.stored_watch_models import (
        STORED_WATCH_STATUS_ARMED,
    )
    from daemon.services.timestamps import now_utc_naive

    stored_id = f"sw-{original_work_id}-{watcher}"
    created = now_utc_naive() - timedelta(seconds=age_seconds)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO stored_watches (stored_watch_id, "
                "original_work_id, retry_work_id, watcher_instance_id, "
                "original_watch_events, mission_instance_id, "
                "work_instance_id, status, created_at, updated_at) "
                "VALUES (:sid, :orig, :retry, :watcher, :ev, :mission, "
                ":work, :status, :created, :created)"
            ),
            {
                "sid": stored_id,
                "orig": original_work_id,
                "retry": retry_work_id,
                "watcher": watcher,
                "ev": json.dumps(["completed", "failed"]),
                "mission": mission,
                "work": work,
                "status": STORED_WATCH_STATUS_ARMED,
                "created": created,
            },
        )
    return stored_id


def _stored_status(eng, stored_id: str) -> str:
    with eng.begin() as c:
        return c.execute(
            text(
                "SELECT status FROM stored_watches "
                "WHERE stored_watch_id = :sid"
            ),
            {"sid": stored_id},
        ).fetchone().status


def _make_gc_service(eng) -> JobQueueService:
    """JobQueueService with the real GC seams wired (watcher repo,
    stored-watch repo, work resolver, instance repository)."""
    svc = JobQueueService(
        repository=MagicMock(),
        lock_manager=MagicMock(),
        queue_repo=MagicMock(),
        instance_manager=_FakeInstanceManager(
            SQLModelInstanceRepository(engine=eng)
        ),
    )
    svc.set_watcher_repo(JobWatcherRepository(engine=eng))
    svc.set_stored_watch_repo(StoredWatchRepository(engine=eng))
    task_repo = TaskRepository(eng)
    svc.set_work_resolver(
        WorkResolverService(
            task_repo=task_repo,
            job_repo=JobRepository(eng),  # REAL — a mock poisons resolve_work
            instance_repo=SQLModelInstanceRepository(engine=eng),
        )
    )
    return svc


class TestStoredWatchGCLeg:
    @pytest.mark.asyncio
    async def test_carrier_alive_releases_without_touching_it(
        self, iso_engine
    ):
        """Late-child-arrival fence: the re-armed carrier row still
        exists on retry_work_id → natural path owns coverage; the GC
        only marks the stored row released."""
        eng = iso_engine
        _seed_instance(eng, "leader-1", status="running")
        _seed_instance(eng, "worker-1", parent_id="leader-1", status="running")
        _seed_task(eng, work_id="W1", instance_id="worker-1", status="cancelled")
        _seed_task(eng, work_id="W2", instance_id="worker-1", status="pending")
        stored_id = _seed_stored_watch(
            eng, original_work_id="W1", retry_work_id="W2"
        )
        # Carrier: the re-armed row alive on W2.
        JobWatcherRepository(engine=eng).add_watch(
            job_id="W2",
            instance_id="leader-1",
            watch_events=["completed", "mission_terminal"],
        )

        result = await _make_gc_service(eng).reconcile_stored_watches()

        assert result["released"] == 1
        assert result["rehydrated"] == 0
        assert _stored_status(eng, stored_id) == "released"
        # The live carrier is UNTOUCHED.
        assert "mission_terminal" in _get_watch_events(eng, "W2")

    @pytest.mark.asyncio
    async def test_retry_never_arrived_rehydrates_pure_mission_row(
        self, iso_engine
    ):
        """The retry never landed (work unresolvable) and the carrier
        is gone → the GC rehydrates a PURE ``mission_terminal`` row on
        the ORIGINAL work_id: silent hold while the mission is live,
        claimed exactly once at mission terminal by the existing
        sweep, immune to zombie-GC (original work always resolves)."""
        eng = iso_engine
        _seed_instance(eng, "leader-1", status="running")
        _seed_instance(eng, "worker-1", parent_id="leader-1", status="running")
        _seed_task(eng, work_id="W1", instance_id="worker-1", status="cancelled")
        # NO W2 task row — the retry child never arrived.
        stored_id = _seed_stored_watch(
            eng, original_work_id="W1", retry_work_id="W2-gone"
        )

        result = await _make_gc_service(eng).reconcile_stored_watches()

        assert result["rehydrated"] == 1
        assert _stored_status(eng, stored_id) == "rehydrated"
        events = _get_watch_events(eng, "W1")
        assert events == ["mission_terminal"]

        # The rehydrated row is a valid C1 carrier: mid-mission it is
        # HELD (delivered nothing), at mission terminal it fires.
        manager, resolver, watcher_repo = _notify_deps(eng)
        held = await notify_work_watchers(
            "W1",
            "cancelled",
            instance_manager=manager,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
        )
        assert held == 0  # pure mission_terminal row: no transport match
        assert manager.deliveries == []

        with eng.begin() as c:
            c.execute(
                text("UPDATE instances SET status='completed' "
                     "WHERE instance_id IN ('leader-1','worker-1')")
            )
        fired = await notify_work_watchers(
            "W1",
            "cancelled",
            instance_manager=manager,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
        )
        assert fired == 1
        assert len(manager.deliveries) == 1
        assert _get_watch_events(eng, "W1") == []

    @pytest.mark.asyncio
    async def test_legitimate_consume_releases_no_double_delivery(
        self, iso_engine
    ):
        """Carrier gone + retry work TERMINAL + mission TERMINAL →
        the carrier was consumed at the true mission-terminal moment
        (C1 shape 4). The GC releases — rehydrating here would
        double-deliver the terminal."""
        eng = iso_engine
        _seed_instance(eng, "leader-1", status="completed")
        _seed_instance(eng, "worker-1", parent_id="leader-1", status="completed")
        _seed_task(eng, work_id="W1", instance_id="worker-1", status="cancelled")
        _seed_task(eng, work_id="W2", instance_id="worker-1", status="completed")
        stored_id = _seed_stored_watch(
            eng, original_work_id="W1", retry_work_id="W2"
        )

        result = await _make_gc_service(eng).reconcile_stored_watches()

        assert result["released"] == 1
        assert result["rehydrated"] == 0
        assert _stored_status(eng, stored_id) == "released"
        # No rehydrated carrier was minted on the original work_id.
        assert _get_watch_events(eng, "W1") == []

    @pytest.mark.asyncio
    async def test_unaged_rows_are_not_touched(self, iso_engine
    ):
        """The 6h orphan window gates the scan: a fresh armed row is
        invisible to the GC (natural path still owns it)."""
        eng = iso_engine
        _seed_instance(eng, "leader-1", status="running")
        _seed_instance(eng, "worker-1", parent_id="leader-1", status="running")
        _seed_task(eng, work_id="W1", instance_id="worker-1", status="cancelled")
        stored_id = _seed_stored_watch(
            eng,
            original_work_id="W1",
            retry_work_id="W2",
            age_seconds=60,  # fresh — inside the window
        )

        result = await _make_gc_service(eng).reconcile_stored_watches()

        assert result["scanned"] == 0
        assert _stored_status(eng, stored_id) == "armed"

    @pytest.mark.asyncio
    async def test_status_cas_is_exactly_once(self, iso_engine):
        """The armed→released CAS: a second transition attempt on an
        already-released row loses (rowcount 0) — the GC's side
        effects can never double-run."""
        eng = iso_engine
        _seed_instance(eng, "leader-1", status="running")
        repo = StoredWatchRepository(engine=eng)
        stored_id = _seed_stored_watch(
            eng, original_work_id="W1", retry_work_id="W2"
        )
        assert repo.transition_status(stored_id, "released") is True
        assert repo.transition_status(stored_id, "released") is False
        assert repo.transition_status(stored_id, "rehydrated") is False


# ─────────────────────────────────────────────────────────────────────────────
# Hook defensive guard (e8a61da3 Minor-1, verifier)
# ─────────────────────────────────────────────────────────────────────────────


class TestHookDefensiveGuard:
    """e8a61da3 Minor-1 (verifier): the re-arm hook self-gates when
    ``instance_id`` is None — binding None into the NOT-NULL
    ``stored_watches.mission_instance_id`` column would
    ``IntegrityError``-abort the retry transaction itself, escalating
    a watch-loss into retry-breakage. The hook MUST short-circuit;
    the F6 re-key itself MUST still execute.

    Pin: ``RetryTurn(rearm_watchers=True, instance_id=None)`` →
    no ``stored_watches`` row, no exception, F6 re-key UPDATE runs
    unchanged. Direct-hook invocation isolates the guard from the
    child-task INSERT path (``task.instance_id`` is NOT NULL, so the
    full ``RetryTurn.run()`` cannot pre-stage ``None`` end-to-end).
    """

    def test_instance_id_none_skips_hook_and_f6_rekey_still_executes(
        self, iso_engine
    ):
        from daemon.services.turn_transitions import RetryTurn

        eng = iso_engine
        _seed_default_watch(eng, "W1", "leader-1")

        turn = RetryTurn(
            parent_work_id="W1",
            child_work_id="W2",
            task_repo=None,
            instance_id=None,
            child_kwargs={
                "task_type": "process_message",
                "message_id": None,
                "retry_count": 1,
                "next_retry_at": None,
                "created_at": "2026-10-11T08:00:00",
                "cancel_requested": False,
                "retry_scheduled": False,
                "is_deferred": False,
                "is_background": False,
            },
            rearm_watchers=True,
        )

        # 1. Hook must NOT raise; MUST return [] (no quarantine work).
        with eng.begin() as conn:
            result = turn._rearm_watchers_in_txn(conn)
        assert result == []

        # 2. No quarantine row was created.
        assert _stored_rows(eng) == []

        # 3. F6 re-key SQL is structurally UNCHANGED — still migrates
        #    the parent-keyed watcher onto the child work_id with
        #    default events untouched (no mission_terminal arm).
        with eng.begin() as conn:
            conn.execute(
                text(
                    "UPDATE job_watchers SET job_id = :child_work_id "
                    "WHERE job_id = :parent_work_id"
                ),
                {"child_work_id": "W2", "parent_work_id": "W1"},
            )

        events_on_w2 = _get_watch_events(eng, "W2")
        assert len(events_on_w2) == 10  # default set, untouched
        assert "mission_terminal" not in events_on_w2  # NO arming
        assert _get_watch_events(eng, "W1") == []  # rekeyed away
