"""Watchloss chain regression — usage-limit retry-supersede double-silence.

Commission Priority 1 (RCA job 043d64a8, e8a61da3). The defect chain:

  usage-limit error mid-mission
    → (1) silent cancel in worker_pool deferral lane
        (worker_pool._handle_usage_limit → _usage_limit_episode_decide
         → schedule_retry → RetryTurn.run with rearm_watchers=True
         at HEAD = OPTION A depth 1+2 fix e8a61da3)
    → (2) F6 watch re-key in turn_transitions (RetryTurn.run lines
         ~691-700 at BASE / 747-759 at HEAD; BASE re-keys only,
         HEAD also quarantines + arms mission_terminal on the watcher
         row BEFORE the re-key in the same transaction)
    → (3) retry-receipt settle: notify_work_watchers with
         ``status="completed"`` and ``work_id=child_wid``;
         the work_notifier CAS-claims the watcher row
         (matching_claimable bucket when standard_match fires)
    → watched mission loses ALL terminal events (mission_terminal
       NEVER fires) — the row was CAS-consumed at settle BEFORE
       mission_terminal could fire; the defrauded watcher has no
       mission-terminal notification in the audit row.

A/B differential proof (REQUIRED by the commission):
  - HEAD @ e3fea4556 (post-fix): mission_terminal arming persists in
    ``job_watchers.watch_events``; the row survives CAS settle (multi-
    kind retire rule fires mission_terminal later); the watcher
    receives ``mission_terminal`` notification EXACTLY ONCE.
  - BASE @ 5cb964187 (pre-fix): no arming; row's watch_events stay
    transport-only; the settle CAS-claims the row (matching_claimable
    on transport kind, mission_terminal never subscribed); watcher
    receives ZERO mission_terminal notifications.

The same test file MUST collect AND run at both trees
(see dual-tree compatibility contract below).

# Seams driven (file:line at both trees)

  * Worker._handle_usage_limit
      BASE @ daemon/services/worker_pool.py:911  (entry point — unchanged
        shape at HEAD; only the schedule_retry call inside
        _usage_limit_episode_decide adds rearm_watchers=True at HEAD).
      HEAD @ daemon/services/worker_pool.py:911  (entry point identical,
        inside _usage_limit_episode_decide the schedule_retry call
        now passes rearm_watchers=True — see diff hunks:
        @@ -998,6 +998,13 @@).

  * TaskRepository.schedule_retry
      BASE @ daemon/repositories/task/repository.py:4854
      HEAD @ daemon/repositories/task/repository.py:4854
        (signature stable at both trees; HEAD's internal RetryTurn
        constructor now accepts rearm_watchers=False default kwarg —
        test never references it directly per the dual-tree rule).

  * RetryTurn.run (F6 re-key — the migration UPDATE + the
    new HEAD-only quarantine+arm logic)
      BASE @ daemon/services/turn_transitions.py:622
      HEAD @ daemon/services/turn_transitions.py:622  (+242 lines
        added for _rearm_watchers_in_txn + JSONB dialect handling;
        test never references these symbols directly).

  * WorkResolverService + JobWatcherRepository.get_watchers_for_job
    + JobWatcherRepository.claim_watchers_for_job_for_instances
      BASE @ daemon/services/work_resolver.py:990  +
               daemon/repositories/job_queue/watcher_repository.py:62
      HEAD @ same file:line  (no signature change).

  * notify_work_watchers
      BASE @ daemon/services/work_notifier.py:286  (notify partition
        + claim-first CAS + enqueue_message — unchanged at HEAD aside
        from receiving the arming effect from upstream).

  * evaluate_mission_live  (real guard — mission_live True/False
    controls matching_readonly vs matching_claimable partition)
      BASE @ daemon/services/mission_live_guard.py:164
      HEAD @ daemon/services/mission_live_guard.py:164  (the
        mission-live guard itself is unchanged in the Option A+E fix;
        only its CALLER (job_recovery_service Pattern (f1)) was
        touched — not exercised in this chain).

# Dual-tree compatibility contract

The file may import/call ONLY APIs existing at BOTH 5cb964187 and
e3fea4556 (no HEAD-only symbols; no test helpers the branch added).
Inline helpers used here must exist at both trees. Verified by
collect+run at /tmp/ens-vfy-head (HEAD) and /tmp/ens-vfy-base (BASE).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

from daemon.llm_error_classifier import UsageLimitError
from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.work_notifier import notify_work_watchers
from daemon.services.work_resolver import WorkRecord, WorkResolverService


# ─── helpers ────────────────────────────────────────────────────────────────


class _NoOpJobRepo:
    """Minimal stand-in for ``JobRepository`` — the resolver's
    ``resolve_work`` only consults the Task + Instance paths for the
    work_ids this test seeds (no JobItem rows exist), and the
    ``add_watch`` ↔ JobRepository path is bypassed (the watcher_repo
    uses ``job_watchers`` directly).

    Defensive ``__getattr__`` returns a callable that yields ``None``
    for every method we don't stub — same recipe as
    ``tests/job_queue/test_work_notifier_n1_pin.py::_NoOpJobRepo``.
    """

    def get(self, _job_id: str):
        return None

    def __getattr__(self, name: str):
        return lambda *_a, **_kw: None


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    status: str = "running",
    parent_id: str | None = None,
    agent_id: str = "developer",
) -> str:
    """Insert an ``Instance`` row so ``resolve_work`` and the
    mission-live guard can find it."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        existing = s.get(Instance, instance_id)
        if existing is None:
            inst = Instance(
                instance_id=instance_id,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                project_id="test-project",
                status=status,
                created_at=now_iso,
                updated_at=now_iso,
                paused_at=None,
                parent_id=parent_id,
            )
            s.add(inst)
            s.commit()
    return instance_id


def _seed_task(
    engine: Engine,
    *,
    work_id: str | None = None,
    instance_id: str,
    status: str = TaskStatus.RUNNING.value,
) -> int:
    """Insert a Task row, return its integer primary key."""
    wid = work_id or str(uuid4())
    with Session(engine) as s:
        task = Task(
            work_id=wid,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=instance_id,
            status=status,
            created_at=datetime.now(timezone.utc),
            is_deferred=False,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return task.id


def _patch_resolver_record(
    resolver: WorkResolverService,
    *,
    work_id: str,
    status: str,
    instance_id: str,
    job_type: str | None = "task",
    mission_liveness: str | None = None,
) -> MagicMock:
    """Point ``resolver.resolve_work`` at a synthetic WorkRecord and
    return the configured MagicMock (caller invokes it to introspect
    resolve_work calls)."""
    record = WorkRecord(
        work_id=work_id,
        kind="report",
        status=status,
        instance_id=instance_id,
        project_id="test-project",
        agent_id="developer",
        result_summary=None,
        error=None,
        created_at=datetime.now(timezone.utc),
        job_type=job_type,
        mission_liveness=mission_liveness,
    )
    patched = MagicMock(return_value=record)
    resolver.resolve_work = patched
    return patched


def _force_terminal(engine: Engine, *instance_ids: str) -> None:
    """Mark instances terminal so the mission-live guard returns
    ``live=False`` on the next ``evaluate_mission_live`` call.

    The guard consults ``instances.parent_id`` (the permanent tree);
    setting every member of the worker subtree terminal ensures
    ``any_non_terminal = False`` and the guard short-circuits to
    ``live=False`` at the ALL-TERMINAL branch
    (mission_live_guard.py:339-347) — no zombie backstop involved.
    """
    from daemon.constants import TERMINAL_INSTANCE_STATUSES

    terminal_value = next(iter(TERMINAL_INSTANCE_STATUSES))
    with Session(engine) as s:
        for iid in instance_ids:
            row = s.get(Instance, iid)
            if row is not None:
                row.status = terminal_value
        s.commit()


# ─── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def watchloss_engine(tmp_path):
    """File-backed SQLite engine with default QueuePool.

    Required so concurrent threads each check out their own
    connection — the same recipe as
    ``tests/job_queue/test_work_notifier_n1_pin.py::n1_engine``.
    With ``StaticPool`` the per-connection parameter binding is not
    safe under concurrent statements and the DELETE...RETURNING CAS
    we want to observe would be masked by a single-connection
    serialisation. The worker_pool's deferral path opens its own
    ``engine.begin()`` for the schedule_retry transaction (a separate
    connection), so the test fixture must use a non-shared pool.
    """
    db_path = tmp_path / "watchloss.db"
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
    # JobWatcher may be declared outside the canonical SQLModel.metadata
    # discovery graph in some wiring paths — explicitly create its table
    # here (matches n1_pin fixture pattern, defensive).
    JobWatcher.metadata.create_all(eng)

    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def watchloss_components(watchloss_engine):
    """Bundle: real repos for task/instance/watcher + resolver +
    enqueue mock.

    Returns a dict so each test can grab what it needs. The
    ``enqueue_message`` mock on a plain ``MagicMock``
    ``instance_manager`` lets the test count delivery attempts
    deterministically (per the work_notifier n1_pin contract:
    ``AsyncMock`` + ``_instance_repository`` so the
    ``evaluate_mission_live`` guard can walk the permanent tree).
    """
    watcher_repo = JobWatcherRepository(watchloss_engine)
    task_repo = TaskRepository(watchloss_engine)
    instance_repo = SQLModelInstanceRepository(watchloss_engine)

    # WorkResolverService needs all three backing repos.
    resolver = WorkResolverService(task_repo, _NoOpJobRepo(), instance_repo)

    instance_manager = MagicMock()
    instance_manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-watchloss")
    )
    instance_manager._instance_repository = instance_repo

    return {
        "engine": watchloss_engine,
        "watcher_repo": watcher_repo,
        "task_repo": task_repo,
        "resolver": resolver,
        "instance_manager": instance_manager,
        "instance_repo": instance_repo,
    }


# ─── The test ──────────────────────────────────────────────────────────────


class TestUsageLimitDeferralArmsMissionTerminal:
    """Watchloss chain regression — drive the full chain end-to-end
    through REAL worker_pool deferral + REAL F6 re-key + REAL notify.

    Defect chain (RCA job 043d64a8, e8a61da3):

        worker_pool._handle_usage_limit
          (silent cancel at BASE; at HEAD the schedule_retry call
           inside _usage_limit_episode_decide passes
           rearm_watchers=True)
        → TaskRepository.schedule_retry → RetryTurn.run
          (BASE: bare F6 re-key UPDATE; HEAD: arming in same txn)
        → WorkResolverService.resolve_work + JobWatcherRepository
          (.get_watchers_for_job → .claim_watchers_for_job_for_instances
          CAS)
        → notify_work_watchers (CAS-claim + enqueue_message)

    The 5-step design (per commission spec §B):
      1. Watcher subscribed with transport-only events (NO
         mission_terminal opt-in — the bug class; the deferral path
         should arm it during F6 re-key at HEAD).
      2. Inject usage-limit → REAL worker_pool deferral runs.
      3. F6 re-key fires → row now keys onto the retry child.
      4. Drive first settle (retry child completes; mission still
         live) → at BASE the transport-only row is CAS-consumed
         (matching_claimable); at HEAD the row survives
         (matching_readonly because mission_terminal is armed and
         mission_live=True).
      5. Mark mission terminal; drive second settle → the watcher
         receives ``mission_terminal`` notification EXACTLY ONCE at
         HEAD; at BASE the row was already deleted at step 4 and
         the second settle has ZERO deliveries.
    """

    @pytest.mark.asyncio
    async def test_usage_limit_deferral_arms_mission_terminal(
        self, watchloss_components
    ):
        engine = watchloss_components["engine"]
        task_repo = watchloss_components["task_repo"]
        watcher_repo = watchloss_components["watcher_repo"]
        resolver = watchloss_components["resolver"]
        instance_manager = watchloss_components["instance_manager"]
        instance_repo = watchloss_components["instance_repo"]

        # ── Step 0: seed the watched mission (single-instance tree) ──
        # The mission is the worker instance (no leader/child tree —
        # the work_notifier guard's get_tree_ids_permanent walks DOWN
        # from the work_record.instance_id; the simplest mission-tree
        # shape that exercises the all-terminal short-circuit at
        # mission_live_guard.py:339-347 is a single ROOT instance).
        worker_id = "watchloss-worker"
        watcher_id = "watchloss-watcher"
        _seed_instance(engine, instance_id=worker_id, status="running")
        _seed_instance(engine, instance_id=watcher_id, status="running")

        parent_wid = str(uuid4())
        parent_task_id = _seed_task(
            engine,
            work_id=parent_wid,
            instance_id=worker_id,
            status=TaskStatus.RUNNING.value,
        )

        # Step 1: Watcher subscribed with TRANSPORT-ONLY events
        # (NO mission_terminal opt-in). This is the bug class:
        # a watcher that did NOT explicitly opt in to
        # mission_terminal, but the deferral path should still arm
        # it — otherwise the settle CAS-consumes the transport-only
        # row at the retry-receipt and the watched mission loses
        # ALL terminal events. The fix (HEAD) arms the row with
        # mission_terminal during the F6 re-key; at BASE the row
        # stays transport-only and the CAS-claim at settle kills
        # mission_terminal entirely.
        original_events = ["completed", "failed"]
        watcher_repo.add_watch(
            parent_wid, watcher_id, watch_events=list(original_events)
        )

        # FIDELITY-GUARD 0: pre-state — proves the test was set up
        # correctly. A test that started with mission_terminal already
        # in events would not exercise the deferral-path arming —
        # it'd trivially pass on both trees.
        pre_rows = watcher_repo.get_watchers_for_job(parent_wid)
        assert len(pre_rows) == 1, (
            f"fidelity-G0: expected exactly 1 watcher row seeded on "
            f"parent_wid, got {len(pre_rows)}"
        )
        assert "mission_terminal" not in pre_rows[0].watch_events, (
            f"fidelity-G0: the test exercises the deferral-path "
            f"arming; the seeded watcher must NOT already opt in to "
            f"mission_terminal (would mask the fix). "
            f"Got watch_events={pre_rows[0].watch_events!r}"
        )

        # ── Step 2: REAL worker_pool deferral path ────────────────
        # Build a Worker via __new__ to bypass __init__'s threading
        # machinery (we never start the worker thread; we drive the
        # deferral handler directly). task_processor carries the
        # REAL TaskRepository so schedule_retry fires the real
        # RetryTurn → F6 re-key path. manager._instance_repository
        # backs the anchor read/write helpers (SOFT-FAIL on None).
        task_processor = MagicMock()
        task_processor._task_repo = task_repo
        task_processor._manager = MagicMock(
            _instance_repository=instance_repo
        )

        from daemon.services.worker_pool import Worker

        worker = Worker.__new__(Worker)
        worker.worker_id = "watchloss-test-worker"
        worker._task_processor = task_processor
        worker._max_retries = 3
        # Use the canonical defaults (21600s window; W5 schedule;
        # jitter disabled for determinism in the test). The values
        # match daemon/services/usage_limit_schedule.py:42-55 and
        # stay stable across the BASE/HEAD diff (no signature
        # change).
        worker._usage_limit_window_seconds = 21600.0
        worker._usage_limit_retry_delays = (180.0, 300.0, 600.0, 900.0)
        worker._usage_limit_retry_jitter_fraction = 0.0

        parent_task = task_repo.get(parent_task_id)
        assert parent_task is not None, (
            "fidelity-G1a: parent task must exist before deferral"
        )
        assert parent_task.status == TaskStatus.RUNNING.value, (
            f"fidelity-G1a: parent task must be RUNNING for "
            f"schedule_retry gate to pass; got {parent_task.status!r}"
        )

        # Inject the usage-limit error. The original error class
        # carries the proxy message verbatim (see
        # daemon/llm_error_classifier.py:362-372, 1051) — the test
        # uses the canonical "Token Plan usage limit" shape that
        # the in-window deferral path is gated on.
        err = UsageLimitError(
            ValueError("Token Plan usage limit reached (2056)")
        )
        # Must not raise (worker_pool._handle_usage_limit handler
        # is bound by NEVER-raises contract — rev2 §3.3).
        worker._handle_usage_limit(parent_task, err)

        # FIDELITY-GUARD 1: deferral state — the parent task must
        # have transitioned to CANCELLED with retry_scheduled=True
        # (the gate UPDATE inside schedule_retry at
        # BASE/HEAD:4955-4970 — atomic, race-safe). A test that
        # passes the deferral assertions but finds the parent
        # still RUNNING exercised the wrong code path.
        parent_after = task_repo.get(parent_task_id)
        assert parent_after.status == TaskStatus.CANCELLED.value, (
            f"fidelity-G1b: usage-limit deferral must cancel parent "
            f"task; got status={parent_after.status!r}"
        )
        assert parent_after.retry_scheduled is True, (
            "fidelity-G1b: deferral must set retry_scheduled=True "
            "on the parent"
        )

        # Identify the retry child minted by schedule_retry.
        children = task_repo.get_by_instance(worker_id)
        child_candidates = [t for t in children if t.work_id != parent_wid]
        assert len(child_candidates) == 1, (
            f"fidelity-G2: schedule_retry must mint exactly one "
            f"retry child on worker_id={worker_id}; got "
            f"{len(child_candidates)} candidates"
        )
        child_task = child_candidates[0]
        child_wid = child_task.work_id
        assert child_task.retry_count == 1, (
            f"fidelity-G2: retry child must have retry_count=1; "
            f"got {child_task.retry_count}"
        )
        assert child_task.status == TaskStatus.PENDING.value, (
            f"fidelity-G2: retry child must be PENDING; got "
            f"{child_task.status!r}"
        )

        # ── Step 3: F6 re-key fired (BASE + HEAD common behaviour) ──
        # Both trees' RetryTurn.run executes the bare F6 re-key
        # UPDATE job_watchers SET job_id = :child WHERE job_id =
        # :parent (BASE: turn_transitions.py:691-700; HEAD: same
        # hunk shifted ~50 lines by the new quarantine code). The
        # row must have moved OFF parent and ONTO child.
        watchers_on_parent = watcher_repo.get_watchers_for_job(parent_wid)
        assert len(watchers_on_parent) == 0, (
            f"F6 re-key must move watcher OFF parent work_id "
            f"(found {len(watchers_on_parent)} rows still keyed on "
            f"parent) — proves the re-key UPDATE fired"
        )
        watchers_on_child = watcher_repo.get_watchers_for_job(child_wid)
        assert len(watchers_on_child) == 1, (
            f"F6 re-key must move watcher ONTO child work_id "
            f"(found {len(watchers_on_child)} rows on child)"
        )
        rekeyed_row = watchers_on_child[0]
        assert rekeyed_row.instance_id == watcher_id, (
            f"re-keyed row must reference the seeded watcher_id; "
            f"got {rekeyed_row.instance_id!r}"
        )

        # Capture the watch_events state BEFORE the first settle
        # notify — this is the canonical "arming effect" snapshot.
        # At HEAD the deferral path passes rearm_watchers=True into
        # schedule_retry → RetryTurn._rearm_watchers_in_txn INSIDE
        # the same transaction as the F6 re-key (turn_transitions.py
        # HEAD: _rearm_watchers_in_txn ~759-955). Each parent-keyed
        # watcher is (a) quarantined into stored_watches with
        # first-store-wins fence and (b) appended with
        # ``mission_terminal`` in its watch_events (dedup,
        # idempotent). At BASE no arming fires; the row's events
        # stay transport-only. The events list captured here is the
        # driver for everything that follows — the work_notifier's
        # multi-kind retire rule keys off it.
        post_rekey_events = list(rekeyed_row.watch_events)

        # ── Step 4: first settle (retry-receipt) ────────────────
        # The retry child completes normally → notify_work_watchers
        # fires with status="completed", work_id=child_wid. The
        # mission is still LIVE (worker instance is RUNNING), so the
        # work_notifier's mission-live guard returns live=True. The
        # partition logic (work_notifier.py:689-803) places the row
        # in:
        #   - matching_claimable (transport kind fires AND
        #     mission_terminal fires NOW — i.e. mission_live=False).
        #     This is the BASE path because the row has no
        #     mission_terminal subscription; standard_match=True
        #     alone is sufficient. The row is CAS-claimed + delivered.
        #   - matching_readonly (transport kind fires NOW but
        #     mission_terminal still pending — mission_live=True).
        #     This is the HEAD path: the row has BOTH
        #     mission_terminal AND a transport kind in events; with
        #     mission_live=True, mission_terminal is pending, so the
        #     row is delivered WITHOUT claim and survives for the
        #     later mission-terminal fire.
        #   - held_for_mission (only mission_terminal subscribed,
        #     mission_live=True): not our case.
        _patch_resolver_record(
            resolver,
            work_id=child_wid,
            status="completed",
            instance_id=worker_id,
            job_type="task",
            mission_liveness=None,
        )

        notified_settle_1 = await notify_work_watchers(
            child_wid,
            "completed",
            error=None,
            instance_manager=instance_manager,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
        )

        # FIDELITY-GUARD 2: first settle drove the real notify path.
        assert notified_settle_1 == 1, (
            f"fidelity-G2: first settle must deliver to the watcher "
            f"exactly once (matching_claimable or matching_readonly "
            f"bucket); got notified={notified_settle_1}"
        )
        assert instance_manager.enqueue_message.await_count == 1, (
            f"fidelity-G2: enqueue_message must fire ONCE on first "
            f"settle (the watcher received a [JOB_EVENT] envelope); "
            f"got {instance_manager.enqueue_message.await_count}"
        )

        # FIDELITY-GUARD 3: row state after first settle — this is
        # the immediate differentiator per the multi-kind retire rule.
        # At BASE: row CAS-consumed (matching_claimable because no
        # mission_terminal subscription); the row is GONE from the
        # DB — any subsequent mission_terminal firing has no row to
        # fire on (the 043d64a8 watch-loss class).
        # At HEAD: row SURVIVES (matching_readonly because
        # mission_terminal is armed AND mission_live=True —
        # mission_terminal is pending; the row is delivered but not
        # claimed so it stays for the later mission-terminal fire).
        post_settle_rows = watcher_repo.get_watchers_for_job(child_wid)
        assert len(post_settle_rows) == 1, (
            f"row state after first settle (HEAD semantics): the "
            f"re-keyed watcher must SURVIVE the first settle "
            f"(matching_readonly bucket — mission_terminal armed "
            f"AND mission_live=True, so the row is delivered "
            f"without claim and waits for the mission-terminal "
            f"moment). At BASE the row is CAS-consumed at first "
            f"settle (transport-only events → matching_claimable) "
            f"and any subsequent mission_terminal firing has no "
            f"row to fire on (043d64a8 watch-loss class). "
            f"Found {len(post_settle_rows)} rows on child after "
            f"first settle; expected 1 (HEAD) or 0 (BASE)."
        )

        # The arming carries mission_terminal in events for HEAD.
        # Surface this snapshot for the diagnostic.
        surviving_row = post_settle_rows[0]
        surviving_events = list(surviving_row.watch_events)

        # ── Step 5: drive mission-terminal settle ────────────────
        # Mark the worker instance terminal so the mission-live guard
        # short-circuits to live=False at the ALL-TERMINAL branch
        # (mission_live_guard.py:339-347). With mission_live=False
        # and the re-keyed row's events still in place, the
        # work_notifier partitions into matching_claimable (last
        # firing event — both completed AND mission_terminal fire
        # NOW). The row is CAS-claimed + delivered EXACTLY ONCE.
        _force_terminal(engine, worker_id)

        # Patch the resolver again so the second notify sees the
        # terminal-state work record (mission_liveness is sourced
        # from the linked instance in production; here the patched
        # record carries the same instance_id so the guard walks
        # the all-terminal tree).
        _patch_resolver_record(
            resolver,
            work_id=child_wid,
            status="completed",
            instance_id=worker_id,
            job_type="task",
            mission_liveness=None,
        )

        pre_settle_2_count = instance_manager.enqueue_message.await_count

        notified_settle_2 = await notify_work_watchers(
            child_wid,
            "completed",
            error=None,
            instance_manager=instance_manager,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
        )

        settle_2_deliveries = (
            instance_manager.enqueue_message.await_count
            - pre_settle_2_count
        )

        # FIDELITY-GUARD 4: mission-terminal settle drove the real
        # notify path (and the work_notifier ran claim-first on
        # this settle).
        assert notified_settle_2 == 1, (
            f"fidelity-G4: second settle (mission terminal) must "
            f"deliver exactly once (matching_claimable bucket — "
            f"both transport kind AND mission_terminal fire NOW "
            f"with mission_live=False); got notified={notified_settle_2}"
        )
        assert settle_2_deliveries == 1, (
            f"fidelity-G4: mission-terminal settle must produce "
            f"exactly ONE enqueue_message call (the watcher "
            f"receives the [JOB_EVENT] envelope carrying the "
            f"mission_terminal firing); got {settle_2_deliveries}"
        )

        # Inspect the delivered envelope at step 5 to confirm it
        # carries the canonical [JOB_EVENT] shape and the right
        # source token (canonical home per work_notifier.py:226-242).
        all_calls = instance_manager.enqueue_message.await_args_list
        last_call = all_calls[-1]
        assert last_call.kwargs["instance_id"] == watcher_id, (
            f"mission-terminal delivery must target the seeded "
            f"watcher_id; got instance_id={last_call.kwargs['instance_id']!r}"
        )
        assert last_call.kwargs["source"] == (
            f"internal_agent:job_event:{child_wid}:completed"
        ), (
            f"mission-terminal delivery source token must carry the "
            f"canonical internal_agent:job_event:<work_id>:<status> "
            f"shape (work_notifier.py:226-242); got "
            f"{last_call.kwargs['source']!r}"
        )
        assert "[JOB_EVENT]" in last_call.kwargs["message"], (
            f"mission-terminal envelope must carry the [JOB_EVENT] "
            f"parser header; got message[:200]={last_call.kwargs['message'][:200]!r}"
        )

        # ── THE EXACTLY-ONCE MISSION_TERMINAL ASSERTION ─────────────
        # Discrimination surface for the 043d64a8 RCA:
        #   - HEAD: the row survives the first settle (matching_readonly)
        #     and fires mission_terminal at the mission-terminal settle
        #     (matching_claimable with mission_live=False). The
        #     mission_terminal delivery count == 1.
        #   - BASE: the row is CAS-consumed at the first settle
        #     (matching_claimable on transport kind alone — no
        #     mission_terminal subscription). The second settle
        #     finds zero watchers → mission_terminal delivery count
        #     == 0.
        #
        # We capture the watch_events list AS THE WORK_NOTIFIER SAW IT
        # (post-rekey snapshot, before the CAS-claim ran) so the
        # HEAD arming effect is observable independent of the row's
        # current existence. The surviving row's events match the
        # captured snapshot (the first settle did not claim it at
        # HEAD); the BASE snapshot shows the row gone (the first
        # settle consumed it). Counting mission_terminal deliveries
        # uses the post-rekey snapshot — the canonical "arming
        # effect" the work_notifier partitioned against.
        #
        # At HEAD: post_rekey_events contains 'mission_terminal'
        # (arming fired) → mission_terminal_count == 1.
        # At BASE: post_rekey_events does NOT contain 'mission_terminal'
        # → mission_terminal_count == 0 (the row was consumed at
        # first settle before any mission_terminal could fire).
        mission_terminal_armed = "mission_terminal" in post_rekey_events

        # Count the mission-terminal settle delivery (settle_2)
        # as the mission_terminal firing.
        if mission_terminal_armed:
            assert settle_2_deliveries == 1, (
                f"HEAD fix must result in exactly ONE "
                f"mission_terminal delivery at the mission-terminal "
                f"settle (settle_2); got {settle_2_deliveries}. "
                f"At HEAD the row survives the first settle "
                f"(matching_readonly because mission_terminal is "
                f"armed + mission_live=True) and the mission-"
                f"terminal settle (matching_claimable with "
                f"mission_live=False) fires mission_terminal "
                f"exactly once. Got post_rekey_events="
                f"{post_rekey_events!r}, surviving_events="
                f"{surviving_events!r}."
            )
        else:
            assert settle_2_deliveries == 0, (
                f"Pre-fix BASE: the row was CAS-consumed at the "
                f"first settle (transport-only events → "
                f"matching_claimable because mission_terminal is "
                f"NOT in events); the mission-terminal settle finds "
                f"zero watchers → mission_terminal delivery count "
                f"== 0. This is the 043d64a8 watch-loss class. "
                f"Got settle_2_deliveries={settle_2_deliveries}; "
                f"post_rekey_events={post_rekey_events!r}."
            )

        # Final discriminator: mission_terminal delivery count ==
        # 1 at HEAD, == 0 at BASE.
        mission_terminal_count = (
            settle_2_deliveries if mission_terminal_armed else 0
        )
        assert mission_terminal_count == 1, (
            f"HEAD fix must result in exactly ONE "
            f"mission_terminal delivery to the watcher across the "
            f"chain (1 settle_1 + 1 settle_2 = 2 total enqueues, "
            f"with the settle_2 carry being the mission_terminal "
            f"firing); got mission_terminal_count="
            f"{mission_terminal_count}. "
            f"At BASE settle_2 finds 0 watchers (row was CAS-"
            f"consumed at settle_1 because the row's events did "
            f"NOT include mission_terminal — no arming fired in "
            f"the deferral path), so mission_terminal NEVER "
            f"notifies. post_rekey_events={post_rekey_events!r}; "
            f"surviving_events after settle_1="
            f"{surviving_events!r}; total enqueues="
            f"{instance_manager.enqueue_message.await_count}."
        )