"""Edge probes — stored_watches quarantine GC + re-arm hook (e8a61da3).

Verification commission: edge-case list beyond the sibling pins in
``tests/job_queue/test_retry_supersede_watch_recovery.py``. Every probe
drives REAL units (no mocking of the units under test); the only
test doubles sit at the messaging boundary (``enqueue_message``
recorder) and at the injected-failure seam (E6), mirroring the
construction patterns of the sibling pins suite.

Probes:

* E1 — Concurrent sweep race: TWO concurrent ``reconcile_stored_
  watches`` ticks over one loss-eligible row → exactly ONE CAS winner,
  exactly ONE pure ``mission_terminal`` carrier (UPSERT idempotence),
  row rehydrated exactly once, no raise.
* E2 — Malformed ``original_watch_events`` JSON on a stored row → the
  GC loss arm still rehydrates a pure ``["mission_terminal"]`` row;
  the repository-level defensive parse
  (``StoredWatchRepository._row_to_model``) and the hook-side
  ``_parse_watch_events`` both proven.
* E3 — Watcher instance TERMINATED before rehydrate → the GC loss arm
  has no instance-status gate; the real notify path then reports the
  ACTUAL delivery outcome (claim + enqueue to the terminated
  instance's id — revive semantics live in the messaging layer, not
  the notifier), claimed exactly once.
* E4 — Empty-string ``instance_id`` boundary (DISCOVERY): the hook's
  self-gate checks ``is None`` only. Records what actually happens —
  no product verdict asserted beyond observed facts.
* E5 — Migration re-run idempotency (SQLite side): the UP statement
  list of ``20261011_000001_create_stored_watches.sql`` executed twice
  against a scratch engine → no error, index set unchanged. Read-only
  note on the checksummed runner: ``MigrationRunner.run_pending_
  migrations`` skips versions already in the ledger
  (``get_applied_versions``), and the file checksum is recorded at
  apply time — a re-run through the runner is a ledger no-op and a
  modified file is a checksum-drift refusal by design; the
  ``IF NOT EXISTS`` guards exist for drift/manual re-apply outside
  the runner.
* E6 — A4 GC log-and-continue under injected errors: a stored-watch
  repo whose ``list_aged_armed`` raises must not escape the GC leg
  (zero-counters + errors=1 directly; contained by the sweep's own
  fence via ``sweep_once``), and a subsequent good tick still
  processes the row.

E7 (coverage verification for the already-pinned edges —
TestDuplicateDeferralFence, retry_never_arrived, instance_id_none) is
run — not authored — by the pack wrapper
``test/packs/watchloss_edges_mock_test.sh`` against the sibling pins
file; green/red per node is reported by the runner of that wrapper.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlmodel import SQLModel

# Model imports register tables on SQLModel.metadata BEFORE create_all
# (conftest contract). Instance/Task/JobWatcher imported explicitly for
# the same reason.
from daemon.repositories.instance.models import Instance  # noqa: F401
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.job_queue.models import (  # noqa: F401
    JobItem,
)
from daemon.repositories.job_queue.stored_watch_models import (  # noqa: F401
    STORED_WATCH_STATUS_ARMED,
    STORED_WATCH_STATUS_REHYDRATED,
    StoredWatch,
)
from daemon.repositories.job_queue.stored_watch_repository import (
    StoredWatchRepository,
)
from daemon.repositories.task.models import Task  # noqa: F401
from daemon.repositories.job_queue.watcher_models import (  # noqa: F401
    ALL_WATCHABLE_EVENTS,
    JobWatcher,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_repository import (
    JobWatcherRepository,
)
from daemon.repositories.task.models import TaskStatus  # noqa: F401
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_queue_service import JobQueueService
from daemon.services.turn_transitions import RetryTurn, _parse_watch_events
from daemon.services.watch_reconcile_sweep import WatchReconcileSweepService
from daemon.services.work_notifier import notify_work_watchers
from daemon.services.work_resolver import WorkResolverService


# ─────────────────────────────────────────────────────────────────────────────
# Local engine — file-backed SQLite (default QueuePool), the same recipe
# as tests/vfy/test_watchloss_chain_regression.py::watchloss_engine and
# tests/job_queue/test_work_notifier_n1_pin.py::n1_engine.
#
# Required for E1: concurrent ``reconcile_stored_watches`` coroutines
# dispatch their repo calls via ``asyncio.to_thread``, so multiple
# threads check out connections SIMULTANEOUSLY. ``StaticPool`` would
# share one connection across threads (unsafe under concurrent
# statements); a file-backed DB gives each thread its own connection
# and WAL + busy_timeout serialize the writers.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def edges_engine(tmp_path):
    db_path = tmp_path / "watchloss_edges.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    # Defensive explicit creates (matches the sibling fixture pattern).
    JobWatcher.metadata.create_all(eng)
    StoredWatch.metadata.create_all(eng)

    yield eng
    eng.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# Seed helpers (construction patterns from the sibling pins suite)
# ─────────────────────────────────────────────────────────────────────────────


def _edge_seed_instance(
    eng,
    instance_id: str,
    *,
    parent_id: str | None = None,
    status: str = "running",
    last_activity: str | None = None,
) -> None:
    # Fresh default anchor: the mission-live guard's 6h zombie-backstop
    # must see the seeded instance as active at any wall-clock run time.
    if last_activity is None:
        last_activity = (_now_naive() - timedelta(minutes=5)).isoformat()
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


def _edge_seed_task(
    eng,
    *,
    work_id: str,
    instance_id: str,
    status: str = "running",
) -> int:
    with eng.begin() as c:
        result = c.execute(
            text(
                "INSERT INTO task (task_type, instance_id, message_id, "
                "status, retry_count, created_at, cancel_requested, "
                "retry_scheduled, work_id, is_deferred, is_background) "
                "VALUES ('process_message', :iid, 'm1', :status, 0, "
                "'2026-10-11T08:00:00', 0, 0, :wid, 0, 0)"
            ),
            {"iid": instance_id, "status": status, "wid": work_id},
        )
        return result.lastrowid


def _edge_seed_watch(
    eng,
    job_id: str,
    watcher: str,
    *,
    events: list[str] | None = None,
) -> None:
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
                "ev": json.dumps(
                    list(events)
                    if events is not None
                    else list(ALL_WATCHABLE_EVENTS)
                ),
            },
        )


def _edge_seed_stored_watch(
    eng,
    *,
    original_work_id: str,
    retry_work_id: str,
    watcher: str = "leader-1",
    mission: str = "leader-1",
    work: str = "worker-1",
    age_seconds: int = 7 * 3600,  # past the 6h orphan window
    original_watch_events: list[str] | None = None,
    raw_events: str | None = None,
) -> str:
    """Seed an ``armed`` stored_watches row; returns its id.

    ``raw_events`` bypasses ``json.dumps`` so a test can plant a
    literally malformed JSON string in ``original_watch_events`` (E2).
    """
    stored_id = f"sw-{original_work_id}-{watcher}"
    if raw_events is not None:
        events_literal = raw_events
    else:
        events_literal = json.dumps(
            list(original_watch_events)
            if original_watch_events is not None
            else ["completed", "failed"]
        )
    created = _now_naive() - timedelta(seconds=age_seconds)
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
                "ev": events_literal,
                "mission": mission,
                "work": work,
                "status": STORED_WATCH_STATUS_ARMED,
                "created": created,
            },
        )
    return stored_id


def _now_naive():
    from daemon.services.timestamps import now_utc_naive

    return now_utc_naive()


def _edge_stored_rows(eng) -> list:
    with eng.begin() as c:
        return c.execute(
            text(
                "SELECT original_work_id, retry_work_id, "
                "watcher_instance_id, mission_instance_id, "
                "work_instance_id, status FROM stored_watches "
                "ORDER BY created_at"
            )
        ).fetchall()


def _edge_stored_status(eng, stored_id: str) -> str:
    with eng.begin() as c:
        return c.execute(
            text(
                "SELECT status FROM stored_watches "
                "WHERE stored_watch_id = :sid"
            ),
            {"sid": stored_id},
        ).fetchone().status


def _edge_watch_rows(eng, job_id: str) -> list[tuple[str, str]]:
    """(instance_id, raw watch_events) pairs for a work_id."""
    with eng.begin() as c:
        rows = c.execute(
            text(
                "SELECT instance_id, watch_events FROM job_watchers "
                "WHERE job_id = :jid"
            ),
            {"jid": job_id},
        ).fetchall()
    out = []
    for r in rows:
        raw = r.watch_events
        out.append((r.instance_id, raw if isinstance(raw, str) else json.dumps(list(raw or []))))
    return out


def _edge_watch_events(eng, job_id: str) -> list[str]:
    rows = _edge_watch_rows(eng, job_id)
    if not rows:
        return []
    return json.loads(rows[0][1])


def _set_instance_status(eng, instance_id: str, status: str) -> None:
    with eng.begin() as c:
        c.execute(
            text("UPDATE instances SET status = :s WHERE instance_id = :i"),
            {"s": status, "i": instance_id},
        )


def _set_task_status(eng, work_id: str, status: str) -> None:
    with eng.begin() as c:
        c.execute(
            text("UPDATE task SET status = :s WHERE work_id = :w"),
            {"s": status, "w": work_id},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Service wiring doubles (boundary-only, per the sibling pins convention)
# ─────────────────────────────────────────────────────────────────────────────


class _FakeInstanceManager:
    """instance_manager double carrying the REAL instance repository
    (the mission-live guard walks it) + a recording enqueue."""

    def __init__(self, instance_repo):
        self._instance_repository = instance_repo
        self.deliveries: list[tuple[str, str]] = []
        self.enqueue_message = AsyncMock(side_effect=self._enqueue)

    async def _enqueue(self, *, instance_id, message, source=None, **kw):
        self.deliveries.append((instance_id, message))
        return None


def _edge_notify_deps(eng):
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


def _edge_gc_service(eng) -> JobQueueService:
    """JobQueueService with the REAL GC seams wired (same recipe as
    the sibling pins ``_make_gc_service``)."""
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
    svc.set_work_resolver(
        WorkResolverService(
            task_repo=TaskRepository(eng),
            job_repo=JobRepository(eng),
            instance_repo=SQLModelInstanceRepository(engine=eng),
        )
    )
    return svc


def _edge_loss_case_setup(eng) -> str:
    """Seed the canonical loss-case shape and return the stored id.

    Aged armed row; original task cancelled (original work resolves —
    zombie-GC immunity); NO retry child task (retry never arrived);
    NO carrier row anywhere; mission (leader → worker tree) live.
    """
    _edge_seed_instance(eng, "leader-1", status="running")
    _edge_seed_instance(eng, "worker-1", parent_id="leader-1", status="running")
    _edge_seed_task(eng, work_id="W1", instance_id="worker-1", status="cancelled")
    return _edge_seed_stored_watch(
        eng, original_work_id="W1", retry_work_id="W2-gone"
    )


# ═════════════════════════════════════════════════════════════════════════════
# E1 — Concurrent sweep race
# ═════════════════════════════════════════════════════════════════════════════


class TestE1ConcurrentSweepRace:
    @pytest.mark.asyncio
    async def test_two_concurrent_gc_ticks_rehydrate_exactly_once(
        self, edges_engine
    ):
        """Two concurrent ``reconcile_stored_watches`` ticks over ONE
        loss-eligible row: no raise; exactly ONE rehydrated outcome
        (one CAS winner); exactly ONE pure ``mission_terminal`` watch
        row on the original work_id (the ``add_watch`` UPSERT is
        idempotent — both ticks may run it, one row results); the
        stored row reads ``rehydrated`` exactly once.
        """
        eng = edges_engine
        stored_id = _edge_loss_case_setup(eng)
        svc = _edge_gc_service(eng)

        # Two ticks race — asyncio.gather interleaves them at the
        # await points; their repo calls genuinely run concurrently
        # in the thread pool (file-backed SQLite serializes writers).
        results = await asyncio.gather(
            svc.reconcile_stored_watches(),
            svc.reconcile_stored_watches(),
        )
        r1, r2 = results[0], results[1]

        # Exactly one CAS winner across both ticks.
        assert r1["rehydrated"] + r2["rehydrated"] == 1
        assert r1["errors"] == 0 and r2["errors"] == 0
        assert r1["scanned"] == 1 and r2["scanned"] == 1

        # Exactly ONE carrier row on the original work_id, and it is
        # PURE mission_terminal (UPSERT idempotence — not two rows,
        # not event-merged duplicates).
        watch_rows = _edge_watch_rows(eng, "W1")
        assert len(watch_rows) == 1
        watcher_id, raw_events = watch_rows[0]
        assert watcher_id == "leader-1"
        assert json.loads(raw_events) == ["mission_terminal"]

        # No carrier leaked onto the retry work_id.
        assert _edge_watch_rows(eng, "W2-gone") == []

        # The stored row transitioned exactly once (final CAS state).
        assert _edge_stored_status(eng, stored_id) == "rehydrated"


# ═════════════════════════════════════════════════════════════════════════════
# E2 — Malformed events JSON on a stored watch
# ═════════════════════════════════════════════════════════════════════════════


class TestE2MalformedStoredEvents:
    @pytest.mark.asyncio
    async def test_malformed_original_watch_events_still_rehydrates_pure_row(
        self, edges_engine
    ):
        """A stored row whose ``original_watch_events`` column holds a
        literally malformed JSON string must not break the GC loss
        arm: the rehydrate still produces a pure
        ``["mission_terminal"]`` row, no raise. The defensive parse
        lives in ``StoredWatchRepository._row_to_model`` (repo read
        path) — proven by re-reading the row through the real repo —
        plus the hook-side ``_parse_watch_events`` pins.
        """
        eng = edges_engine
        stored_id = _edge_loss_case_setup(eng)

        # Overwrite events with LITERALLY malformed JSON.
        with eng.begin() as c:
            c.execute(
                text(
                    "UPDATE stored_watches SET original_watch_events = :ev "
                    "WHERE stored_watch_id = :sid"
                ),
                {"ev": '{"bad":', "sid": stored_id},
            )

        result = await _edge_gc_service(eng).reconcile_stored_watches()

        assert result["rehydrated"] == 1
        assert result["errors"] == 0
        assert _edge_stored_status(eng, stored_id) == "rehydrated"

        # The rehydrated carrier is the pure survival event set.
        assert _edge_watch_events(eng, "W1") == ["mission_terminal"]

        # Repo-level defensive parse proven through the REAL repo:
        # the malformed column reads back as [] (never raises).
        repo = StoredWatchRepository(eng)
        provenance = repo.get_by_original_work("W1")
        assert len(provenance) == 1
        assert provenance[0].original_watch_events == []

        # Hook-side defensive parser: malformed → [], None → [],
        # valid-but-non-list → [], list items coerced to str.
        assert _parse_watch_events('{"bad":') == []
        assert _parse_watch_events(None) == []
        assert _parse_watch_events('{"a": 1}') == []
        assert _parse_watch_events(["completed", 7]) == ["completed", "7"]


# ═════════════════════════════════════════════════════════════════════════════
# E3 — Watcher terminal-mid-recovery
# ═════════════════════════════════════════════════════════════════════════════


class TestE3WatcherTerminatedMidRecovery:
    @pytest.mark.asyncio
    async def test_terminated_watcher_rehydrates_then_claims_exactly_once(
        self, edges_engine
    ):
        """The watcher instance row flips TERMINATED before the GC
        tick. OBSERVED BEHAVIOR (reported, per commission):

        * The GC loss arm has NO instance-status gate — ``add_watch``
          re-arms the pure carrier regardless of the watcher's
          terminal state (the FK checks existence, not status).
        * Mid-mission the pure row is HELD (worker-1 still running →
          the mission-live guard says live).
        * At true mission terminal the REAL notify path claims the
          row (atomic DELETE...RETURNING) and enqueues to the
          TERMINATED instance's id — the notifier performs NO
          TERMINATED gate; enqueue-to-terminal-instance revive
          semantics belong to the messaging layer (here: the
          recording boundary fake).
        * Row claimed exactly once; a re-fire finds nothing.
        """
        eng = edges_engine
        _edge_seed_instance(eng, "leader-1", status="terminated")
        _edge_seed_instance(eng, "worker-1", parent_id="leader-1", status="running")
        _edge_seed_task(eng, work_id="W1", instance_id="worker-1", status="cancelled")
        stored_id = _edge_seed_stored_watch(
            eng, original_work_id="W1", retry_work_id="W2-gone"
        )

        # GC loss arm rehydrates despite the TERMINATED watcher.
        result = await _edge_gc_service(eng).reconcile_stored_watches()
        assert result["rehydrated"] == 1
        assert result["errors"] == 0
        assert _edge_stored_status(eng, stored_id) == "rehydrated"
        assert _edge_watch_events(eng, "W1") == ["mission_terminal"]

        manager, resolver, watcher_repo = _edge_notify_deps(eng)

        # Mission still live (worker-1 running) → pure row is HELD.
        held = await notify_work_watchers(
            "W1",
            "cancelled",
            instance_manager=manager,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
        )
        assert held == 0
        assert manager.deliveries == []
        assert _edge_watch_rows(eng, "W1") != []  # survived the hold

        # True mission terminal (worker-1 flips; leader-1 already
        # TERMINATED — the tree is terminal either way).
        _set_instance_status(eng, "worker-1", "completed")
        fired = await notify_work_watchers(
            "W1",
            "cancelled",
            instance_manager=manager,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
        )

        # ACTUAL delivery outcome: fired once, enqueued to the
        # TERMINATED instance's id, no exception.
        assert fired == 1
        assert len(manager.deliveries) == 1
        assert manager.deliveries[0][0] == "leader-1"

        # Claimed exactly once — the row is gone and a re-fire is a no-op.
        assert _edge_watch_rows(eng, "W1") == []
        again = await notify_work_watchers(
            "W1",
            "cancelled",
            instance_manager=manager,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
        )
        assert again == 0
        assert len(manager.deliveries) == 1


# ═════════════════════════════════════════════════════════════════════════════
# E4 — Empty-string instance_id (boundary DISCOVERY probe)
# ═════════════════════════════════════════════════════════════════════════════


class TestE4EmptyStringInstanceId:
    def test_empty_string_passes_none_gate_full_retry_run(
        self, edges_engine
    ):
        """DISCOVERY PROBE — records OBSERVED behavior only (no
        product verdict).

        The hook self-gate at ``daemon/services/turn_transitions.py:841``
        checks ``if self.instance_id is None`` — an empty string is
        NOT None, so the gate does not fire. This probe records what
        actually happens when the full ``RetryTurn.run()`` executes
        with ``instance_id=""``:

        * does the retry transaction commit (no IntegrityError)?
        * is ``mission_instance_id`` bound as "" (NOT NULL satisfied
          by the empty string)?
        * does the F6 re-key still complete?

        OBSERVATIONS (asserted as observed facts):
        * No exception — the retry transaction COMMITS.
        * ``_resolve_mission_instance_id`` walks from "" and finds no
          instances row → returns "" → the stored row binds
          ``mission_instance_id=""`` AND ``work_instance_id=""``.
        * The F6 re-key completes: the watcher row lands on the child
          work_id WITH the survival ``mission_terminal`` arm.
        """
        from daemon.services.timestamps import now_utc_naive

        eng = edges_engine
        # Watcher instance rows seeded for the FK-ON harness (the
        # sibling pins run without foreign_keys pragma; this fixture
        # enforces it). leader-1 = the watcher; worker-1 = the
        # original work owner. Neither affects the "" gate probe: the
        # mission-anchor walk starts FROM instance_id="" and no
        # instances row with id "" exists either way.
        _edge_seed_instance(eng, "leader-1", status="running")
        _edge_seed_instance(eng, "worker-1", parent_id="leader-1", status="running")
        _edge_seed_task(eng, work_id="W1", instance_id="worker-1")
        _edge_seed_watch(eng, "W1", "leader-1")

        turn = RetryTurn(
            parent_work_id="W1",
            child_work_id="W2",
            task_repo=None,
            instance_id="",  # ← the boundary value under probe
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

        # OBSERVED: no raise — the full retry transaction commits.
        with eng.begin() as conn:
            result = turn.run(conn)

        # OBSERVED: the hook ran (side effects present) — "" passed
        # the None-gate.
        side_effects = list(getattr(result, "cross_turn_side_effects", []) or [])
        assert "rearm_stored_watches" in side_effects and "migrate_job_watchers" in side_effects, f"got {side_effects}"

        # OBSERVED: mission_instance_id bound as "" (NOT NULL
        # satisfied by the empty string); work_instance_id likewise.
        rows = _edge_stored_rows(eng)
        assert len(rows) == 1
        (
            orig,
            retry,
            watcher,
            mission_anchor,
            work_anchor,
            status,
        ) = rows[0]
        assert mission_anchor == ""
        assert work_anchor == ""
        assert orig == "W1" and retry == "W2"
        assert watcher == "leader-1"
        assert status == "armed"

        # OBSERVED: the F6 re-key STILL completed — the watcher row
        # moved to the child work_id with the survival arm applied.
        assert _edge_watch_rows(eng, "W1") == []
        w2_events = _edge_watch_events(eng, "W2")
        assert "mission_terminal" in w2_events
        assert len(w2_events) == 11  # 10 defaults + mission_terminal

        # OBSERVED downstream anchor shape (recorded for the report):
        # the GC's future guard anchor on this row is "" — a root
        # walk from "" resolves nothing (no instances row), so the
        # anchor is a dead anchor by construction.


# ═════════════════════════════════════════════════════════════════════════════
# E5 — Migration re-run idempotency (SQLite side)
# ═════════════════════════════════════════════════════════════════════════════


class TestE5MigrationRerunIdempotency:
    def test_up_statement_list_executes_twice_clean(self, tmp_path):
        """The UP statement list of
        ``daemon/migrations/versions/20261011_000001_create_stored_
        watches.sql`` executed TWICE against a scratch SQLite engine:

        * no error on the second execution (every statement is
          ``IF [NOT] EXISTS``-guarded);
        * the index set is byte-identical before vs after the re-run.

        Read-only runner note (no runner invoked): the checksummed
        runner records the file's SHA-256 in ``schema_migrations`` at
        apply time and ``run_pending_migrations`` consults
        ``get_applied_versions`` first — a re-run through the runner
        is a ledger no-op and a MODIFIED file trips checksum-drift
        refusal by design. The ``IF NOT EXISTS`` guards exist for
        drift/manual re-apply OUTSIDE the runner (the contract the
        file's own header documents for the SQLite driver).
        """
        project_root = Path(__file__).resolve().parents[2]
        sql_path = (
            project_root
            / "daemon"
            / "migrations"
            / "versions"
            / "20261011_000001_create_stored_watches.sql"
        )
        assert sql_path.exists(), f"migration file missing: {sql_path}"
        content = sql_path.read_text(encoding="utf-8")

        # Extract the UP section exactly as daemon/migrations/runner.py
        # MigrationFile.parse does (regex copied verbatim).
        up_match = re.search(
            r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL
        )
        assert up_match, "migration file missing -- UP section"
        up_sql = up_match.group(1).strip()

        db_path = tmp_path / "migration_scratch.db"

        def _index_set() -> list[str]:
            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND tbl_name = 'stored_watches' "
                    "ORDER BY name"
                ).fetchall()
            finally:
                conn.close()
            return [r[0] for r in rows]

        # First apply.
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(up_sql)
            conn.commit()
        finally:
            conn.close()
        first = _index_set()

        # Second apply — must be a clean no-op.
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(up_sql)
            conn.commit()
        finally:
            conn.close()
        second = _index_set()

        assert first == second
        assert "uq_stored_watches_original_work_watcher" in first
        assert len([n for n in first if n.startswith("idx_")]) == 4

        # And the table itself survived both passes with its PK.
        conn = sqlite3.connect(db_path)
        try:
            cols = [
                r[1]
                for r in conn.execute(
                    "PRAGMA table_info(stored_watches)"
                ).fetchall()
            ]
        finally:
            conn.close()
        assert cols == [
            "stored_watch_id",
            "original_work_id",
            "retry_work_id",
            "watcher_instance_id",
            "original_watch_events",
            "mission_instance_id",
            "work_instance_id",
            "status",
            "created_at",
            "updated_at",
        ]


# ═════════════════════════════════════════════════════════════════════════════
# E6 — A4 GC log-and-continue under injected errors
# ═════════════════════════════════════════════════════════════════════════════


class _BoomStoredWatchRepo:
    """Failure-injection double for the candidate scan (E6).

    Injected ONLY at the service's own ``set_stored_watch_repo`` seam
    (the production wiring point) — the GC unit itself is unmodified.
    """

    def list_aged_armed(self, **kwargs):
        raise RuntimeError("boom: candidate scan injected failure")

    def transition_status(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("must never be reached after a scan failure")


class TestE6GcLogAndContinue:
    @pytest.mark.asyncio
    async def test_scan_raise_contained_and_next_tick_processes_rows(
        self, edges_engine
    ):
        """``list_aged_armed`` raising must not escape the GC leg:

        * direct GC call → the documented zero-counters + errors=1
          dict, no raise;
        * ``sweep_once`` → contained by the sweep's own log-and-
          continue fence (stored_rehydrated stays 0, cumulative
          errors_total increments), no raise;
        * a subsequent GOOD tick still processes the seeded row
          (rehydrated=1, row transitions).
        """
        eng = edges_engine
        stored_id = _edge_loss_case_setup(eng)
        svc = _edge_gc_service(eng)
        svc.set_stored_watch_repo(_BoomStoredWatchRepo())

        # 1. Direct GC contract: zero-counters + errors=1, no raise.
        direct = await svc.reconcile_stored_watches()
        assert direct == {
            "scanned": 0,
            "released": 0,
            "rehydrated": 0,
            "errors": 1,
        }
        # The row was never touched (scan failed before processing).
        assert _edge_stored_status(eng, stored_id) == "armed"

        # 2. Seam-level: the sweep contains it too (its own fence).
        sweep = WatchReconcileSweepService(svc)
        tick1 = await sweep.sweep_once()
        assert tick1["stored_rehydrated"] == 0
        # OBSERVED BEHAVIOR (finding, recorded not judged): the GC
        # leg CONTAINS its internal error (returns errors=1 in its
        # dict — the WARNING is logged at
        # job_queue_service.py:1151) and never raises, so the sweep's
        # ``except`` fences never fire and ``cumulative_errors``
        # stays 0. The GC's internal error count does NOT propagate
        # into the sweep's cumulative_errors counter (that counter
        # only ingests exceptions that ESCAPE a leg). Log-and-continue
        # contract holds; observability gap only.
        assert sweep.counters()["errors_total"] == 0

        # 3. Subsequent GOOD tick processes the row.
        svc.set_stored_watch_repo(StoredWatchRepository(eng))
        tick2 = await sweep.sweep_once()
        assert tick2["stored_rehydrated"] == 1
        assert _edge_stored_status(eng, stored_id) == "rehydrated"
        assert _edge_watch_events(eng, "W1") == ["mission_terminal"]
