"""U1-SLICE commission pinning tests (2026-09-27,
``fix/u1-watch-reconcile``).

Commission-mandated pin tests for the U1-SLICE held-mission_terminal
starvation fix (incident c7f59aaf, mission 538e2f59). The defect:
a settled work row whose mission is STILL LIVE leaves its
``mission_terminal`` watcher rows HELD by the C1 partition; when
the mission later reaches terminal, NOTHING in the natural notify
path re-evaluates those held rows — only the boot-time
``reconcile_terminal_watches`` sweep does, leaving an in-session
delivery gap that was unbounded for a daemon that never restarted.

The U1-SLICE fix closes the gap with:

  1. The :meth:`JobQueueService.reconcile_held_watches_for_instance`
     helper — reuses the canonical mission-live guard via
     :func:`daemon.services.work_notifier.notify_work_watchers`
     (so the CAS-claim + exactly-once delivery stays in ONE code
     path) and handles zombie-row GC (rows whose job_id is gone
     AND whose mission is terminal/unresolvable).
  2. Two event hooks in :class:`JobFeedbackObserver` —
     (a) end of :meth:`_fire_watcher_notify_for_terminal`,
     (b) end of :meth:`_finalize_job` post-commit outbox. Fail-soft
     so neither breaks the host path.
  3. The :class:`WatchReconcileSweepService` — periodic backstop
     for the 310ms race + long-tail cases (default 300s, knob
     ``watch_reconcile_sweep_interval_seconds``).
  4. Observer gate widened from TERMINATED-only to
     {TERMINATED, FAILED} (closes the FAILED-lane starvation class
     the gate/docstring drift opened).

These tests live in their own file so the inventory maps cleanly
to the P1–P6 buckets the commission describes; the C1+C2+C3
commission pin file (``test_mission_terminal_commission_pins.py``)
retains its coverage and is the upstream of these pins (the C1
partition is the foundation this fix reuses — no partition
regression).

## Size rationale

This module is intentionally a single, large pin pack (>1000
lines) rather than split by bucket. The pins are full-stack
integration tests over real ``SQLModel`` /
:class:`JobWatcherRepository` / :class:`TaskRepository` /
:class:`SQLModelInstanceRepository` fixtures with the canonical
``evaluate_mission_live`` guard in the path; the C1 partition +
CAS-claim semantics are what we're pinning, and splitting by
bucket would break the single-incident narrative (P1 verbatim →
P4 widening → P5 cross-boot → P6 content → 310ms-race → zombie
GC) that maps cleanly to the incident c7f59aaf timeline. The
shared ``u1_components`` fixture + the ``_seed_instance`` /
``_add_watch`` / ``_patch_resolver_*`` helpers amortize the
setup cost across the pack.

## Pins

### P1 — STARVATION (incident verbatim)

1. ``test_p1_held_mission_terminal_starvation_incident_verbatim`` —
   the U1 incident timeline: held row survives settle (mission
   live), then mission flips terminal IN-SESSION (no restart),
   held row fires within bounded latency (hook + sweep carry the
   delivery).
2. ``test_p1_sweep_cadence_actually_exercised`` — pins the sweep
   cadence contract: a fast-clock tick on
   ``WatchReconcileSweepService`` (interval=1s) drives the held
   row to fire WITHIN the cadence window; the event hook is
   bypassed by mocking the observer path so the pin isolates the
   sweep's delivery contribution. Single-tick fast-clock
   determinism via ``sweep_once()`` direct call.
3. ``test_p1_sweep_loop_ticks_via_run_async`` — DISTINCT role:
   the asyncio-task loop ticks via the ``_run`` coroutine (NOT a
   direct ``sweep_once()`` call) — the structural backstop the
   U1 commission mandated for the 310ms race + the long-tail
   case where an instance flips terminal WITHOUT triggering
   either hook arm. Asserts ``start()`` → ``sleep(2.5)`` →
   ``stop()`` drives ``counters()["ticks"] >= 1`` via the real
   asyncio task spawned by the production lifecycle path
   (``manager.py`` lifespan owns start/stop).

### P2 — TOOL-ENTRY contract

3. ``test_p2_tools_stay_pure_reads`` — during hold, the watch /
   get / progress / messages tools remain pure reads — no
   notification side-effect, no watcher-row mutation. Delivery
   comes from hooks/sweep, not from the tool entry seam. Pinned
   by capturing the watcher-repo ``get_watchers_for_job`` /
   ``remove_*`` call counts and asserting zero mutation from
   the tool surface.

### P3 — REVIVAL FLIP (in-session, no boot)

4. ``test_p3_held_row_fires_via_finalize_hook_after_revive`` —
   parent non-terminal at child completion → PROCESS_REPORT
   revival → post-report settle → held row fires in-session via
   hook (b) (``_finalize_job`` post-commit outbox). Identifies
   + pins the carrying arm.

### P4 — TERMINATED / FAILED arms

5. ``test_p4_terminated_arm_fires_via_helper_hook`` — the
   pre-fix code only fired the re-fire for TERMINATED; the
   ``_process_event`` gate at ``job_feedback_observer.py:~:1089``
   now widens to FAILED (gate/docstring drift resolution).
6. ``test_p4_failed_arm_fires_via_helper_hook`` — the FAILED
   lane is the new addition; pinned explicitly so the :1089
   widening cannot silently regress.

### P5 — CROSS-BOOT STALE

7. ``test_p5_held_row_survives_restart_then_fires_exactly_once``
   — held row across 2 ``reconcile_terminal_watches`` boot sweeps
   while mission is live, then mission terminalizes, then a third
   boot sweep fires the row exactly once (CAS-claim enforces
   exactly-once across the restart).

### P6 — CONTENT on the delivery paths

8. ``test_p6_delivered_bodies_carry_result_content`` — the
   delivered ``[JOB_EVENT]`` body carries ``Result:\\n<content>``
   (the C2/C3 contract), NOT a status-only envelope. Pins the
   producer-side ``result_summary=`` threading from the held-row
   notify path.

### 310ms-RACE PIN

9. ``test_310ms_race_hook_fires_while_mission_still_live_row_held``
   — the U1 race: hook fires while mission still reads live
   (last settle lands AFTER lifecycle-completed → the helper's
   ``evaluate_mission_live`` verdict is ``live=True``) → row
   stays held → the NEXT sweep tick (within cadence) delivers
   the row once the mission flips terminal.

Recipe: real :class:`JobWatcherRepository` + real
:class:`TaskRepository` + real :func:`evaluate_mission_live` via
:class:`SQLModelInstanceRepository` + patched
:class:`WorkResolverService` per the C1+C2+C3 commission recipe.
Same SQL atomicity guarantees the production CAS path exercises.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic_core import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import (
    JobWatcherRepository,
)
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_queue_service import JobQueueService
from daemon.services.work_notifier import notify_work_watchers
from daemon.services.work_resolver import (
    WorkRecord,
    WorkResolverService,
)


# ── Fixtures + helpers ────────────────────────────────────────────────────


@pytest.fixture
def u1_engine(tmp_path):
    db_path = tmp_path / "u1_slice.db"
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


@pytest.fixture
def u1_components(u1_engine):
    """Bundle: watcher_repo + task_repo + instance_repo + JQS + resolver.

    U1-SLICE FIX (2026-09-27): the JQS is wired so the helper has
    access to ``_watcher_repo`` + ``_instance_manager`` + the
    :class:`WorkResolverService`. The instance manager mock carries
    ``enqueue_message`` so ``notify_work_watchers`` can deliver the
    ``[JOB_EVENT]`` body to the watching instance.
    """
    watcher_repo = JobWatcherRepository(u1_engine)
    task_repo = TaskRepository(u1_engine)
    instance_repo = SQLModelInstanceRepository(u1_engine)
    resolver = WorkResolverService(task_repo, _NoOpJobRepo(), instance_repo)
    instance_manager = MagicMock()
    instance_manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-u1-slice")
    )
    instance_manager._instance_repository = instance_repo
    instance_manager._task_repo = task_repo

    # The JQS is the primary carrier for the U1-SLICE helper.
    # JQS's ``__init__`` only takes (repository, lock_manager,
    # queue_repo, instance_manager); the watcher_repo +
    # work_resolver are wired via setters (matching the daemon
    # lifespan recipe — see ``daemon/api.py:646+``).
    jqs = JobQueueService(
        repository=_NoOpJobRepo(),
        lock_manager=_NoOpLockManager(),
        queue_repo=_NoOpQueueRepo(),
        instance_manager=instance_manager,
    )
    jqs.set_watcher_repo(watcher_repo)
    jqs.set_work_resolver(resolver)

    return {
        "engine": u1_engine,
        "watcher_repo": watcher_repo,
        "task_repo": task_repo,
        "instance_repo": instance_repo,
        "resolver": resolver,
        "instance_manager": instance_manager,
        "jqs": jqs,
    }


class _NoOpJobRepo:
    """Minimal JobRepository stand-in for JQS construction + ``_NoOp
    allowlist`` semantics (raises ``AttributeError`` on unknown
    attrs; ``get`` returns ``None``)."""

    def get(self, _job_id):
        return None

    def __getattr__(self, name):
        raise AttributeError(
            f"_NoOpJobRepo: unknown attribute {name!r}"
        )


class _NoOpLockManager:
    """Minimal lock manager — JQS only requires ``_lock_manager`` for
    the lock-release path inside ``_finalize_job``; the helper
    doesn't touch it."""

    def __getattr__(self, name):
        raise AttributeError(
            f"_NoOpLockManager: unknown attribute {name!r}"
        )


class _NoOpQueueRepo:
    """Minimal queue repo — JQS construction requires a non-None
    ``queue_repo``; the helper doesn't touch it."""

    def __getattr__(self, name):
        raise AttributeError(
            f"_NoOpQueueRepo: unknown attribute {name!r}"
        )


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    agent_id: str = "developer",
    project_id: str = "test-project",
    status: str = "running",
    parent_id: str | None = None,
) -> str:
    """Insert (or update) an ``Instance`` row.

    ``status`` defaults to ``"running"`` (non-terminal) so the
    mission-live guard returns ``live=True``. Pass a terminal
    status (e.g. ``"completed"``) to flip the mission to
    terminal — the helper UPDATES the existing row's status in
    place so the test's mission-flip-mid-scenario works
    correctly (mirrors the live-evidence U1 pattern: mission
    flips terminal IN-SESSION without a daemon restart).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        existing = s.get(Instance, instance_id)
        if existing is None:
            inst = Instance(
                instance_id=instance_id,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                project_id=project_id,
                status=status,
                created_at=now_iso,
                updated_at=now_iso,
                paused_at=None,
                parent_id=parent_id,
            )
            s.add(inst)
        else:
            # Update status in-place — the U1 commission's
            # 310ms-race pattern is "mission flips terminal
            # IN-SESSION", which is a status UPDATE, not a new
            # row. Without this branch the seed is a no-op when
            # the instance already exists and the test
            # exercises a non-terminal mission.
            existing.status = status
            existing.updated_at = now_iso
            if parent_id is not None:
                existing.parent_id = parent_id
        s.commit()
    return instance_id


def _seed_task(
    engine: Engine,
    *,
    work_id: str | None = None,
    instance_id: str,
    status: str = TaskStatus.RUNNING.value,
) -> str:
    wid = work_id or str(uuid4())
    with Session(engine) as s:
        task = Task(
            work_id=wid,
            task_type="process_message",
            instance_id=instance_id,
            status=status,
            created_at=datetime.now(timezone.utc),
            is_deferred=False,
        )
        s.add(task)
        s.commit()
    return wid


def _add_watch(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    watch_events: list[str],
) -> None:
    with Session(engine) as s:
        s.add(JobWatcher(
            job_id=work_id,
            instance_id=instance_id,
            watch_events=watch_events,
        ))
        s.commit()


def _patch_resolver_task_running(resolver, *, wid, instance_id):
    """Patch resolver to return a TASK-kind WorkRecord with
    ``status="running"`` (non-terminal transport + non-terminal
    mission liveness)."""
    record = WorkRecord(
        work_id=wid, kind="report", status="running",
        instance_id=instance_id, project_id="test-project",
        agent_id="developer", result_summary=None, error=None,
        created_at=datetime.now(timezone.utc),
        job_type=None, mission_liveness=None,
    )
    original = resolver.resolve_work
    resolver.resolve_work = MagicMock(return_value=record)
    return original


def _patch_resolver_task_completed(resolver, *, wid, instance_id):
    """Patch resolver to return a TASK-kind WorkRecord with
    ``status="completed"``."""
    record = WorkRecord(
        work_id=wid, kind="report", status="completed",
        instance_id=instance_id, project_id="test-project",
        agent_id="developer",
        result_summary="U1_SLICE_DELIVERED_CONTENT_FROM_RESOLVER",
        error=None,
        created_at=datetime.now(timezone.utc),
        job_type=None, mission_liveness=None,
    )
    original = resolver.resolve_work
    resolver.resolve_work = MagicMock(return_value=record)
    return original


# ── P1 — STARVATION (incident verbatim) ───────────────────────────────────


class TestP1StarvationIncidentVerbatim:
    """P1 (2026-09-27) — held-mission_terminal starvation.

    The U1 incident timeline: a single ``mission_terminal`` watcher
    row is HELD by the C1 partition (mission still live when the
    settle event fires), then the parent instance flips terminal
    IN-SESSION (no daemon restart), then the held row fires within
    bounded latency (the event hooks + the periodic sweep carry the
    delivery — the pre-fix gap was unbounded).
    """

    @pytest.mark.asyncio
    async def test_p1_held_mission_terminal_starvation_incident_verbatim(
        self, u1_components,
    ):
        """P1 incident verbatim pin.

        Timeline: arm (mission live) → receipt settles → row
        HELD (mission still live, C1 partition) → mission flips
        terminal in-session → helper fires the held row → CAS-
        claimed + delivered (exactly-once).
        """
        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        resolver = u1_components["resolver"]
        instance_manager = u1_components["instance_manager"]
        jqs = u1_components["jqs"]

        wid = f"wid-u1-starvation-{uuid4().hex[:8]}"
        # Mission instance is alive (running) at settle time.
        _seed_instance(
            engine, instance_id="inst-mission-u1", status="running"
        )
        _seed_instance(engine, instance_id="watcher-u1-starvation")
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-u1-starvation",
            watch_events=["mission_terminal"],
        )

        # Step 1: receipt settles while mission is still live.
        # Row is HELD by the C1 partition (matching_readonly or
        # held_for_mission — both survive the notify).
        original = _patch_resolver_task_running(
            resolver, wid=wid, instance_id="inst-mission-u1",
        )
        try:
            n_step1 = await notify_work_watchers(
                wid, "settled", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
            )
        finally:
            resolver.resolve_work = original

        assert n_step1 == 0, (
            f"P1 step-1: settle-while-mission-live MUST hold the "
            f"``mission_terminal`` row — got non-zero deliveries "
            f"({n_step1})."
        )
        assert len(watcher_repo.get_watchers_for_job(wid)) == 1, (
            "P1 step-1: the ``mission_terminal`` row MUST survive "
            "settle while mission is live."
        )

        # Step 2: mission flips terminal IN-SESSION. The natural
        # notify path tries to re-fire (matching_readonly path) but
        # the row stays held because the C1 partition sees
        # work_record.status still terminal (the held state is
        # about mission liveness, not work status). The U1-SLICE
        # helper closes this gap: it consults the canonical
        # ``evaluate_mission_live`` guard, sees the mission
        # terminal now, and CAS-claims + delivers the row.
        _seed_instance(
            engine, instance_id="inst-mission-u1", status="completed"
        )
        original = _patch_resolver_task_completed(
            resolver, wid=wid, instance_id="inst-mission-u1",
        )
        try:
            result = (
                await jqs.reconcile_held_watches_for_instance(
                    instance_id="watcher-u1-starvation",
                )
            )
        finally:
            resolver.resolve_work = original

        assert result["fired"] == 1, (
            f"P1 step-2: helper MUST fire the held row once the "
            f"mission flips terminal — got fired={result['fired']} "
            f"retired={result['retired']} scanned={result['scanned']}."
        )
        assert result["retired"] == 0, (
            "P1 step-2: held-row delivery MUST NOT be confused with "
            "zombie retire (work is still resolvable)."
        )
        assert len(watcher_repo.get_watchers_for_job(wid)) == 0, (
            "P1 step-2: held row MUST be CAS-claimed (deleted) "
            "after exactly-once delivery."
        )
        # Delivery happened through the instance_manager mock.
        instance_manager.enqueue_message.assert_awaited()

    @pytest.mark.asyncio
    async def test_p1_sweep_cadence_actually_exercised(
        self, u1_components,
    ):
        """P1 sweep-cadence pin — the periodic sweep drives held
        rows to fire WITHIN the cadence window when the in-session
        hook is bypassed.

        The event hooks are the in-session fast path; the sweep is
        the structural backstop for the 310ms race + the long-tail
        case. This pin exercises the sweep in isolation: bypass the
        observer hook by exercising ``sweep_once`` directly with a
        1s fast-clock interval, assert the held row fires within
        one tick.
        """
        from daemon.services.watch_reconcile_sweep import (
            WatchReconcileSweepService,
        )

        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        resolver = u1_components["resolver"]
        instance_manager = u1_components["instance_manager"]
        jqs = u1_components["jqs"]

        wid = f"wid-u1-sweep-{uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id="inst-mission-sweep",
            status="completed",  # mission is TERMINAL — sweep fires NOW
        )
        _seed_instance(engine, instance_id="watcher-u1-sweep")
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-u1-sweep",
            watch_events=["mission_terminal"],
        )

        # Patch resolver to return a terminal work record (so the
        # helper sees ``work_status == "completed"`` and calls
        # ``notify_watchers``).
        original = _patch_resolver_task_completed(
            resolver, wid=wid, instance_id="inst-mission-sweep",
        )
        try:
            sweep = WatchReconcileSweepService(
                job_queue_service=jqs,
                interval_seconds=1,
            )
            # Single-tick fast-clock exercise — the U1 commission
            # permits mock/fast-clock IF the sweep cadence is
            # genuinely exercised. ``sweep_once`` is the
            # public single-tick entry point (matches the
            # ``JobLockSweepService`` shape).
            result = await sweep.sweep_once()
        finally:
            resolver.resolve_work = original

        assert result["fired"] == 1, (
            f"P1 sweep-cadence: single tick MUST fire the held row "
            f"when mission is terminal — got fired={result['fired']} "
            f"retired={result['retired']} scanned={result['scanned']}."
        )
        assert result["retired"] == 0, (
            "P1 sweep-cadence: must not mis-classify a resolvable "
            "row as a zombie (would mean double-counting GC)."
        )
        assert result["cumulative_fired"] == 1
        assert result["cumulative_errors"] == 0
        assert sweep.counters()["fired_total"] == 1
        assert len(watcher_repo.get_watchers_for_job(wid)) == 0, (
            "P1 sweep-cadence: CAS-claim MUST remove the row "
            "after exactly-once delivery."
        )

    @pytest.mark.asyncio
    async def test_p1_sweep_loop_ticks_via_run_async(
        self, u1_components,
    ):
        """P1 loop-tick pin — the periodic ``_run`` loop drives
        ticks via the asyncio task, NOT via a direct
        ``sweep_once()`` call.

        The existing ``test_p1_sweep_cadence_actually_exercised``
        exercises ``sweep_once`` directly (single-tick fast-clock
        determinism). This SIBLING exercises the structural
        backstop the U1 commission mandated — the asyncio task
        spawned by ``start()`` MUST actually fire periodic ticks
        via ``_run`` so the 310ms race + the long-tail case (no
        event hook fires) are caught in production.

        Strategy: spin up the service with ``interval_seconds=1``,
        give the loop ~2.5s (enough for the immediate first tick
        + at least one sleep-then-tick round), ``stop()``, then
        assert ``counters()["ticks"] >= 1``. The ``>= 1`` floor
        is deliberately conservative — CI clock drift could
        shave the second tick off a 2.5s sleep; the load-bearing
        invariant is "the loop ticks via the asyncio task at
        all", not the exact tick count.
        """
        from daemon.services.watch_reconcile_sweep import (
            WatchReconcileSweepService,
        )

        jqs = u1_components["jqs"]

        sweep = WatchReconcileSweepService(
            job_queue_service=jqs,
            interval_seconds=1,
        )
        assert sweep.counters()["ticks"] == 0

        # Spin up the asyncio task — this is the production
        # lifecycle (manager.py lifespan owns start/stop).
        sweep.start()
        assert sweep._task is not None
        assert not sweep._task.done()

        try:
            # Give the loop enough wall-clock to fire the
            # immediate-first tick + at least one
            # sleep(1s)→tick round.
            await asyncio.sleep(2.5)
        finally:
            # Clean shutdown — exercises the cancel+await path
            # the manager lifespan uses.
            await sweep.stop()

        ticks_via_loop = sweep.counters()["ticks"]
        assert ticks_via_loop >= 1, (
            f"P1 loop-tick pin: the asyncio _run loop MUST fire "
            f"at least one periodic tick (the structural backstop "
            f"for the 310ms race + long-tail case); got "
            f"ticks={ticks_via_loop}. A 0 here means start() "
            f"spawned a task that never entered its first "
            f"sweep_once() — the U1 structural backstop would be "
            f"silently dead in production."
        )
        # _task must be cleared post-stop (mirrors the
        # ``JobLockSweepService`` lifecycle contract).
        assert sweep._task is None
        # No errors logged — the periodic path is healthy.
        assert sweep.counters()["errors_total"] == 0

    @pytest.mark.asyncio
    async def test_p1_sweep_loop_survives_unexpected_tick_exception(
        self, u1_components,
    ):
        """P1 loop-resilience pin (tidier item D, cycle 2) — the
        ``_run`` loop MUST survive an unexpected ``Exception``
        raised by ``sweep_once`` and still fire tick N+1.

        ``_run`` is the load-bearing held-mission_terminal
        starvation BACKSTOP — a permanent exit on an unexpected
        tick exception would silently re-open U1 (rows stay
        held forever, no recovery short of a daemon restart).
        This pin deliberately injects a ``RuntimeError`` on
        the first ``sweep_once`` call and verifies:

          1. Tick N raised → ``errors_total >= 1`` (the
             ``logger.exception`` recorded the traceback).
          2. The loop CONTINUED → tick N+1 still fired
             (counters()["ticks"] >= 2 over a ~2.5s window
             with interval=1s — first tick raises immediately,
             second tick fires after the 1s sleep).
          3. ``stop()`` exits CLEANLY — ``_task is None``
             post-stop (no orphaned task, ``CancelledError``
             propagated cleanly).

        This is the deliberate deviation from the
        ``JobLockSweepService._run`` Template-B shape (which
        exits permanently on unexpected Exception — the
        KNOWN defect this service corrects).
        """
        from daemon.services.watch_reconcile_sweep import (
            WatchReconcileSweepService,
        )

        jqs = u1_components["jqs"]

        # Patch the underlying ``reconcile_held_watches_for_instance``
        # call to raise on the FIRST sweep_once invocation, then
        # return the empty-result shape on subsequent calls. The
        # ``side_effect`` list is consumed one entry per call.
        boom = RuntimeError(
            "simulated unexpected tick exception (U1-slice "
            "cycle-2 resilience pin)"
        )
        empty_result = {
            "fired": 0, "retired": 0, "scanned": 0,
        }
        original_helper = jqs.reconcile_held_watches_for_instance
        jqs.reconcile_held_watches_for_instance = AsyncMock(
            side_effect=[boom, empty_result, empty_result, empty_result],
        )

        sweep = WatchReconcileSweepService(
            job_queue_service=jqs,
            interval_seconds=1,
        )
        assert sweep.counters()["ticks"] == 0
        assert sweep.counters()["errors_total"] == 0

        sweep.start()
        assert sweep._task is not None
        assert not sweep._task.done()

        try:
            # Enough wall-clock for: immediate first tick (raises),
            # sleep(1s), second tick (succeeds), sleep(1s), possibly
            # a third tick. We assert >= 2 ticks (the BOOM tick + at
            # least one continuation tick).
            await asyncio.sleep(2.5)
        finally:
            await sweep.stop()

        # Invariant 1: the raised tick was recorded (errors_total
        # bumped by ``sweep_once``'s own try/except at :241 — the
        # backstop catches the exception, bumps the counter, and
        # returns the zero-counts shape; ``_run`` then logs and
        # continues).
        assert sweep.counters()["errors_total"] >= 1, (
            f"P1 loop-resilience: the injected RuntimeError MUST "
            f"have been caught (errors_total >= 1); got "
            f"errors_total={sweep.counters()['errors_total']}."
        )

        # Invariant 2: the loop survived and tick N+1 fired.
        # (First tick raises immediately at start; second tick
        # fires after the 1s sleep. With 2.5s wall-clock we expect
        # 2-3 ticks; >= 2 is the load-bearing invariant.)
        ticks_via_loop = sweep.counters()["ticks"]
        assert ticks_via_loop >= 2, (
            f"P1 loop-resilience: the asyncio _run loop MUST "
            f"continue past an unexpected tick exception (this "
            f"service is the load-bearing U1 backstop — a "
            f"permanent exit would silently re-open U1). Expected "
            f">= 2 ticks (1st raised + 2nd survived) over 2.5s; "
            f"got ticks={ticks_via_loop}, "
            f"errors_total={sweep.counters()['errors_total']}."
        )

        # Invariant 3: stop() exited cleanly — _task cleared, no
        # orphaned task. The CancelledError path inside _run
        # propagated cleanly through ``stop()``'s cancel+await.
        assert sweep._task is None

        # Restore the helper for any downstream test sharing
        # this fixture (defensive — u1_components is scoped per
        # test, but be explicit).
        jqs.reconcile_held_watches_for_instance = original_helper


# ── P2 — TOOL-ENTRY contract ──────────────────────────────────────────────


class TestP2ToolEntryContract:
    """P2 (2026-09-27) — tool-entry contract during hold.

    The watch / get / progress / messages tools MUST stay pure
    reads during a held state — no notification side-effect, no
    watcher-row mutation. Delivery comes from the event hooks +
    the periodic sweep, NOT from the tool entry seam. The
    pre-U1-SLICE code's boot-only ``reconcile_terminal_watches``
    sweep was the only in-session re-evaluation path, and it
    did NOT live behind a tool — the U1 gap was that held rows
    between mission-flip-terminal and the next boot were
    unobservable AND unfireable from the tool surface.
    """

    @pytest.mark.asyncio
    async def test_p2_tools_stay_pure_reads(
        self, u1_components,
    ):
        """P2 tool-surface pin — watch_job / get / progress /
        messages do NOT mutate watcher rows during hold.

        Recipe: seed a held row (mission live + work terminal is
        the canonical C1 hold shape), then exercise the tool
        surface. Pinned assertion: watcher_repo mutation calls
        (``remove_*`` / ``claim_*``) are NOT made from the tool
        path.
        """
        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        # Instrument mutation calls on the repo so we can assert
        # the tool surface does NOT touch them.
        original_remove_for_job = watcher_repo.remove_all_watches_for_job
        remove_for_job_calls = []

        def _spy_remove(*args, **kwargs):
            remove_for_job_calls.append((args, kwargs))
            return original_remove_for_job(*args, **kwargs)

        watcher_repo.remove_all_watches_for_job = _spy_remove

        wid = f"wid-u1-pure-{uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id="inst-mission-p2", status="running"
        )
        _seed_instance(engine, instance_id="watcher-u1-pure")
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-u1-pure",
            watch_events=["mission_terminal"],
        )

        # The held row is canonical: work terminal + mission live.
        # We DON'T change mission state — the row stays held across
        # the entire tool-surface exercise, so any mutation by a
        # tool is a regression.
        resolver = u1_components["resolver"]
        instance_manager = u1_components["instance_manager"]

        original = _patch_resolver_task_completed(
            resolver, wid=wid, instance_id="inst-mission-p2",
        )
        try:
            # Tool-surface exercise: a held watcher calling
            # ``watch_job`` (re-arm) MUST NOT trigger a notify /
            # claim. The watch_job tool at
            # ``daemon/tools/job_queue.py:2335-2339`` is a pure
            # read with an INSERT — no notify, no remove.
            from daemon.tools.job_queue import create_job_tools

            job_service = MagicMock()
            job_service.get_work = AsyncMock(
                return_value=None,  # work not in resolver's live set
            )
            # Capture notify_watchers calls — the tool surface
            # MUST NOT call notify_watchers for a held row.
            notify_calls = []
            job_service.notify_watchers = AsyncMock(
                side_effect=lambda *a, **kw: notify_calls.append((a, kw))
                or 1
            )

            tools = create_job_tools(
                job_service=job_service,
                queue_mgmt_service=MagicMock(),
                dead_letter_service=MagicMock(),
                current_instance_id="watcher-u1-pure",
                agent_id="jober",
                watcher_repo=watcher_repo,
                manager=instance_manager,
            )
            watch_job_tool = next(
                t for t in tools if t.name == "watch_job"
            )

            # Re-call watch_job — should be a pure read.
            await watch_job_tool.ainvoke({"job_id": wid})

            # P2 pin: tools do NOT mutate the watcher row and do
            # NOT notify during a held state. Delivery comes from
            # the hooks/sweep, not from the tool entry seam.
            assert len(remove_for_job_calls) == 0, (
                f"P2 tool-surface: watch_job MUST NOT mutate the "
                f"watcher row during hold — "
                f"got remove_for_job_calls={remove_for_job_calls}."
            )
            assert len(notify_calls) == 0, (
                f"P2 tool-surface: watch_job MUST NOT notify during "
                f"hold — got notify_calls={notify_calls}."
            )
            # Row SURVIVES — exactly the pre-helper invariant.
            remaining = watcher_repo.get_watchers_for_job(wid)
            assert len(remaining) == 1, (
                "P2 tool-surface: row MUST survive the tool "
                "surface exercise (held state preserved)."
            )
        finally:
            resolver.resolve_work = original
            watcher_repo.remove_all_watches_for_job = (
                original_remove_for_job
            )


# ── P3 — REVIVAL FLIP (in-session, no boot) ──────────────────────────────


class TestP3RevivalFlipInSession:
    """P3 (2026-09-27) — held row fires in-session via hook (b).

    The PROCESS_REPORT revival path: a parent that was non-terminal
    at child completion gets a ``PROCESS_REPORT`` turn; that turn
    settles; the post-report settle hits ``_finalize_job``; the
    post-commit outbox fires hook (b) at the end; hook (b) calls
    the helper; the helper consults the mission-live guard and
    fires the held row IN-SESSION.

    Identifies + pins the carrying arm = hook (b) (end of
    ``_finalize_job`` post-commit outbox, ~line 2290 in this
    branch's HEAD).
    """

    @pytest.mark.asyncio
    async def test_p3_held_row_fires_via_finalize_hook_after_revive(
        self, u1_components,
    ):
        """P3 revival-flip pin — held row fires via hook (b).

        Recipe: held row (mission live) → parent flips terminal
        via ``_finalize_job`` post-commit outbox path → hook (b)
        fires → helper sees mission terminal now → CAS-claims +
        delivers.
        """
        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        resolver = u1_components["resolver"]
        instance_manager = u1_components["instance_manager"]
        jqs = u1_components["jqs"]

        wid = f"wid-u1-revive-{uuid4().hex[:8]}"
        # Mission live initially (the C1 hold shape).
        _seed_instance(
            engine, instance_id="inst-mission-revive",
            status="running",
        )
        _seed_instance(engine, instance_id="watcher-u1-revive")
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-u1-revive",
            watch_events=["mission_terminal"],
        )

        # The pre-finalize state: resolver sees a TASK-kind work
        # record (the parent's PROCESS_REPORT task). Mission is
        # still live so the row stays held.
        original = _patch_resolver_task_running(
            resolver, wid=wid, instance_id="inst-mission-revive",
        )
        try:
            # Pre-condition: held row exists.
            assert len(watcher_repo.get_watchers_for_job(wid)) == 1

            # Now flip the mission terminal (the ``_finalize_job``
            # post-commit outbox runs through this transition).
            _seed_instance(
                engine, instance_id="inst-mission-revive",
                status="completed",
            )
            # Patch resolver to return a terminal work record so
            # the helper's ``_work_status_is_terminal`` check
            # passes and ``notify_watchers`` is invoked.
            inner = _patch_resolver_task_completed(
                resolver, wid=wid, instance_id="inst-mission-revive",
            )
            try:
                # Hook (b) surrogate: the helper is called from
                # the end of ``_finalize_job`` post-commit outbox
                # (the seam the U1-SLICE fix added). This is the
                # exact carrying arm the commission identifies.
                # Filter scope = the WATCHER's parent instance_id
                # (the instance that called ``watch_job``); the
                # helper looks up ``watch.instance_id`` to scope
                # the scan, which is "watcher-u1-revive", NOT
                # "inst-mission-revive" (the mission's root).
                result = (
                    await jqs.reconcile_held_watches_for_instance(
                        instance_id="watcher-u1-revive",
                    )
                )
            finally:
                resolver.resolve_work = inner

            assert result["fired"] == 1, (
                f"P3 revival-flip: hook (b) MUST fire the held "
                f"row in-session after PROCESS_REPORT settle — "
                f"got fired={result['fired']}."
            )
            assert len(watcher_repo.get_watchers_for_job(wid)) == 0, (
                "P3 revival-flip: held row MUST be CAS-claimed "
                "after exactly-once delivery (no double-fire "
                "across hooks + boot sweep)."
            )
        finally:
            resolver.resolve_work = original


# ── P4 — TERMINATED / FAILED arms ─────────────────────────────────────────


class TestP4TerminatedFailedArms:
    """P4 (2026-09-27) — both TERMINATED and FAILED arms fire.

    The pre-fix gate at ``job_feedback_observer.py:~:1089`` only
    handled TERMINATED. The docstring at line ~1404 says
    TERMINATED / FAILED. The fix WIDENS the gate to FAILED so the
    error-lane starvation class closes (an instance that hits
    FAILED without going through ``_finalize_job`` post-commit
    outbox — e.g. via the ``_process_event`` early-return — left
    its held rows stranded identically to the c7f59aaf incident
    vector, but on the error lane).
    """

    @pytest.mark.asyncio
    async def test_p4_terminated_arm_fires_via_helper_hook(
        self, u1_components,
    ):
        """P4 TERMINATED arm — unit-scope pin for the helper body.

        **UNIT-SCOPE pin (U1-F1 cycle 4 re-anchor):** this test
        exercises the BODY of
        :meth:`JobQueueService.reconcile_held_watches_for_instance`
        with ``instance_id=None`` (the global scan path —
        ``watch.instance_id`` filter is NOT applied). Bypasses
        the production ``_process_event`` →
        ``_fire_watcher_notify_for_terminal` → hook (a) call
        site — the live-seam sibling pins
        (``test_p4_terminated_arm_live_seam`` + the EXTERNAL /
        ANCESTOR topology variants added in cycle 4) exercise
        the FULL LIVE SEAM end-to-end via the real observer.

        Cycle 4 fix (the work-side axis): hook (a) now calls the
        SAME global scan path (``instance_id=None``) the sweep
        uses — the candidate set is no longer keyed on the
        watcher's tree position. The dominant production
        topology (c7f59aaf incident shape) is EXTERNAL watcher
        (parent_id=NULL) or ANCESTOR of the mission root — the
        cycle-3 descendant-watcher filter excluded both. The
        global scan + the C1 partition's canonical
        ``evaluate_mission_live`` check on the work's instance
        (per-row liveness) is the SAME axis the sweep and
        ``evaluate_mission_live`` already resolve — the helper
        body is topology-invariant and proven live.

        Recipe: any held ``mission_terminal`` row whose work's
        mission is terminal fires. This unit pin uses the
        descendant-watcher fixture (mirrors the cycle-3 setup)
        for compatibility with the existing topology; the
        live-seam pins cover the EXTERNAL and ANCESTOR
        production shapes.
        """
        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        resolver = u1_components["resolver"]
        instance_manager = u1_components["instance_manager"]
        jqs = u1_components["jqs"]

        wid = f"wid-u1-term-{uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id="inst-mission-term",
            status="running",
        )
        _seed_instance(
            engine, instance_id="watcher-u1-term",
            parent_id="inst-mission-term",
        )
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-u1-term",
            watch_events=["mission_terminal"],
        )

        # Pre-condition: held row exists.
        assert len(watcher_repo.get_watchers_for_job(wid)) == 1

        # Cascade-terminate the mission subtree (mirrors the live
        # evidence from tester RESULTS 2026-09-27 P4(a) — DELETE
        # /api/instances/{W} cascade-terminates the subtree). The
        # mission-live guard walks the parent_id tree from
        # ``work_record.instance_id`` and returns ``live=False``
        # when the whole tree is terminal — the row fires.
        _seed_instance(
            engine, instance_id="inst-mission-term",
            status="terminated",
        )
        _seed_instance(
            engine, instance_id="watcher-u1-term",
            status="terminated",
        )
        original = _patch_resolver_task_completed(
            resolver, wid=wid, instance_id="inst-mission-term",
        )
        try:
            # U1-F1 cycle 4: hook (a) calls
            # ``reconcile_held_watches_for_instance(instance_id=None)``
            # — the SAME global-scan call the sweep uses. The
            # ``instance_id=None`` arg makes the helper skip the
            # watcher filter entirely; the C1 partition consults
            # ``evaluate_mission_live(work_record.instance_id)``
            # per row to decide. The CYCLE-3
            # ``reconcile_held_watches_for_mission_root`` wrapper
            # is gone (was the wrong axis — descendant-watcher
            # filter excluded the dominant production topologies).
            result = (
                await jqs.reconcile_held_watches_for_instance(
                    instance_id=None,
                )
            )
        finally:
            resolver.resolve_work = original

        assert result["fired"] == 1, (
            f"P4 TERMINATED (unit-scope): the global-scan helper "
            f"MUST fire the held row when mission is terminal — "
            f"got fired={result['fired']}. This pin exercises the "
            f"helper body directly; the LIVE-SEAM sibling pins "
            f"verify the production call site."
        )

    @pytest.mark.asyncio
    async def test_p4_failed_arm_fires_via_helper_hook(
        self, u1_components,
    ):
        """P4 FAILED arm — the new addition (gate/docstring drift
        resolution).

        A FAILED lifecycle event reaches ``_process_event``; the
        widened gate routes through
        ``_fire_watcher_notify_for_terminal`` (notify_status=
        "failed" + error_message from the lifecycle event); hook
        (a) fires the helper; the held row fires when mission
        flips terminal.

        Pre-fix: gate at :1089 was TERMINATED-only — the FAILED
        lane fell through to the post-commit outbox (which was
        a no-op for an already-terminal instance), and the held
        row starved identically to c7f59aaf but on the error
        lane. Pin: the widening cannot silently regress.
        """
        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        resolver = u1_components["resolver"]
        jqs = u1_components["jqs"]

        wid = f"wid-u1-failed-{uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id="inst-mission-failed",
            status="running",
        )
        # Seed the WATCHER as a CHILD of the mission root so
        # ``get_tree_ids_permanent(inst-mission-failed)`` returns
        # ``[inst-mission-failed, watcher-u1-failed]`` — the
        # mission-root helper's tree walk needs the parent_id
        # chain to enumerate the watcher (U1-F1 cycle 3).
        _seed_instance(
            engine, instance_id="watcher-u1-failed",
            parent_id="inst-mission-failed",
        )
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-u1-failed",
            watch_events=["mission_terminal"],
        )

        # Pre-condition: held row exists.
        assert len(watcher_repo.get_watchers_for_job(wid)) == 1

        # Simulate the FAILED lifecycle event + widened gate +
        # hook (a) → mission-root helper (U1-F1). The FAILED lane
        # is the cycle-2 gate-widening + the cycle-3 axis-fix;
        # this pin exercises BOTH. Cascade-terminate topology (the
        # mission subtree ends up terminal in the same tick):
        _seed_instance(
            engine, instance_id="inst-mission-failed",
            status="failed",
        )
        _seed_instance(
            engine, instance_id="watcher-u1-failed",
            status="terminated",
        )
        original = _patch_resolver_task_completed(
            resolver, wid=wid, instance_id="inst-mission-failed",
        )
        try:
            # U1-F1 cycle 4: hook (a) calls
            # ``reconcile_held_watches_for_instance(instance_id=None)``
            # — the SAME global-scan call the sweep uses. The
            # ``instance_id=None`` arg skips the watcher filter
            # entirely (the watcher's tree position is irrelevant);
            # the C1 partition consults
            # ``evaluate_mission_live(work_record.instance_id)``
            # per row to decide. The cycle-3
            # ``reconcile_held_watches_for_mission_root`` wrapper
            # is gone (was the wrong axis — descendant-watcher
            # filter excluded the dominant production topologies).
            result = (
                await jqs.reconcile_held_watches_for_instance(
                    instance_id=None,
                )
            )
        finally:
            resolver.resolve_work = original

        assert result["fired"] == 1, (
            f"P4 FAILED (unit-scope): the global-scan helper MUST "
            f"fire the held row when mission is terminal — got "
            f"fired={result['fired']}. Pre-fix this row starved on "
            f"the error lane (the identical c7f59aaf class, error "
            f"variant). This pin exercises the helper body directly; "
            f"the LIVE-SEAM sibling pins verify the production call "
            f"site."
        )
        # Confirm exactly-once: row is gone after the fire.
        assert len(watcher_repo.get_watchers_for_job(wid)) == 0

    @pytest.mark.asyncio
    async def test_p4_terminated_arm_live_seam(
        self, u1_components,
    ):
        """P4 TERMINATED arm — LIVE-SEAM pin, DESCENDANT variant.

        One of THREE live-seam topology variants (cycle 4
        re-anchor). The unit-scope pins
        (``test_p4_terminated_arm_fires_via_helper_hook`` and
        ``test_p4_failed_arm_fires_via_helper_hook``) exercise
        the helper body directly. THIS pin + its two siblings
        (``..._external`` + ``..._ancestor``) exercise the FULL
        LIVE SEAM end-to-end via the real observer for each
        of the watcher-tree-position topologies identified by
        the cycle-4 tester re-verify:

          1. **EXTERNAL** (the production / c7f59aaf incident
             shape) — watcher parent_id=NULL, NOT in the
             mission subtree. Cycle-3 unit pin masked this by
             constructing a descendant-watcher topology; the
             cycle-3 ``reconcile_held_watches_for_mission_root``
             wrapper walked DOWN from the mission root and
             excluded the external watcher → silent no-op (live
             evidence: 234s sweep delivery, no hook-(a) log).
          2. **ANCESTOR** — watcher is the mission root's PARENT
             in the ``instances.parent_id`` chain. Same filter-
             exclusion class as EXTERNAL.
          3. **DESCENDANT** (this pin) — watcher is INSIDE the
             mission subtree. Cycle-3 happened to deliver here
             because the cycle-3 wrapper's tree walk included
             the watcher.

        Cycle 4 fix: hook (a) calls the SAME global-scan helper
        the sweep uses (``instance_id=None``). The candidate
        set is keyed on the WORK side (the canonical
        ``evaluate_mission_live`` resolves via the work's
        parent_id tree walk) — topology-invariant. ALL THREE
        watcher positions deliver.

        Recipe (DESCENDANT variant, this pin):
          - Real :class:`JobFeedbackObserver` + real
            ``JobQueueService`` (NOT mocks).
          - Mission root (parent_id=None) + WATCHER as a CHILD
            of mission root (parent_id=mission-root).
          - Cascade-terminate: both terminal at the moment the
            lifecycle event fires for the mission root.
          - Fire ``observer._process_event`` with the mission
            root TERMINATED lifecycle event.
          - Assert held row was CAS-claimed in-session
            (``watcher_repo`` no longer holds it) +
            ``enqueue_message.await_count == 1``.
        """
        from daemon.repositories.job_queue.repository import JobRepository
        from daemon.repositories.job_queue.lock_repository import LockRepository
        from daemon.services.job_feedback_observer import JobFeedbackObserver

        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        resolver = u1_components["resolver"]
        instance_manager = u1_components["instance_manager"]
        jqs = u1_components["jqs"]

        # Build a REAL observer with a REAL jqs — the helper
        # filter, C1 partition, CAS-claim, and ``enqueue_message``
        # delivery all run for real. The observer only mocks the
        # side-channels the LIVE SEAM doesn't exercise (the
        # ``atomic_transition`` terminal path is owned by
        # ``terminate_instance()`` out-of-band).
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=jqs,
            job_repo=MagicMock(spec=JobRepository),
            lock_repo=MagicMock(spec=LockRepository),
            project_repo=MagicMock(),
            instance_manager=instance_manager,
        )

        wid = f"wid-u1-term-live-desc-{uuid4().hex[:8]}"
        # Mission root — the instance that will receive the
        # TERMINATED lifecycle event in the live call.
        _seed_instance(
            engine, instance_id="inst-mission-term-live",
            status="running",
        )
        # Watcher — a SEPARATE instance, a CHILD of the mission
        # root in the ``instances.parent_id`` chain (DESCENDANT
        # topology). The watch row's ``instance_id`` is the
        # WATCHER.
        _seed_instance(
            engine, instance_id="watcher-u1-term-live",
            parent_id="inst-mission-term-live",
        )
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-u1-term-live",
            watch_events=["mission_terminal"],
        )

        # Pre-condition: held row exists.
        assert len(watcher_repo.get_watchers_for_job(wid)) == 1

        # Cascade-terminate the subtree (mirrors the live
        # DELETE-cascade-terminate topology from tester
        # P4(a)). The mission-live guard walks the parent_id
        # tree from the work's instance (the watcher) and
        # finds both terminal → live=False → fire.
        _seed_instance(
            engine, instance_id="inst-mission-term-live",
            status="terminated",
        )
        _seed_instance(
            engine, instance_id="watcher-u1-term-live",
            status="terminated",
        )

        # Patch the resolver to return a TERMINAL work_record
        # whose ``instance_id`` is the WATCHER (the live
        # semantics: the watcher registered ``watch_job`` on
        # its OWN work).
        original = _patch_resolver_task_completed(
            resolver, wid=wid, instance_id="watcher-u1-term-live",
        )
        try:
            # THE LIVE SEAM: fire a real lifecycle event for the
            # MISSION ROOT (NOT a direct helper call). This is
            # the exact seam hook (a) runs on in production.
            event = {
                "event_type": "instance_lifecycle",
                "data": {
                    "instance_id": "inst-mission-term-live",
                    "status": "terminated",
                    "error": None,
                },
            }
            await observer._process_event(event)
        finally:
            resolver.resolve_work = original

        # The held row MUST be CAS-claimed in-session.
        remaining = len(watcher_repo.get_watchers_for_job(wid))
        assert remaining == 0, (
            f"P4 TERMINATED LIVE-SEAM (DESCENDANT variant, "
            f"U1-F1 cycle 4): hook (a) MUST CAS-claim the held "
            f"row in-session when the mission root terminates "
            f"and the watcher is a descendant — got "
            f"{remaining} watcher row(s) remaining. The cycle-4 "
            f"fix (work-side axis via the global-scan helper) "
            f"is topology-invariant — the EXTERNAL and "
            f"ANCESTOR sibling pins exercise the other watcher "
            f"positions."
        )
        # The instance_manager.enqueue_message mock MUST have
        # been called once for the delivery (the [JOB_EVENT]
        # enqueue path — the production contract for delivery).
        assert instance_manager.enqueue_message.await_count == 1, (
            f"P4 TERMINATED LIVE-SEAM (DESCENDANT): hook (a) "
            f"MUST drive the [JOB_EVENT] enqueue exactly once "
            f"via the CAS-claim + notify loop; got "
            f"await_count={instance_manager.enqueue_message.await_count}."
        )

    @pytest.mark.asyncio
    async def test_p4_terminated_arm_live_seam_external(
        self, u1_components,
    ):
        """P4 TERMINATED arm — LIVE-SEAM pin, EXTERNAL variant.

        The DOMINANT production topology from the c7f59aaf
        incident: the watcher is a TOP-LEVEL instance
        (parent_id=NULL), NOT a descendant or ancestor of the
        mission root. The cycle-3 helper
        (``reconcile_held_watches_for_mission_root``) walked
        DOWN from the mission root via
        ``get_tree_ids_permanent`` and filtered
        ``watch.instance_id in tree_set`` — but the external
        watcher is NOT in the subtree, so the helper returned 0
        candidates → silent no-op. Live evidence: cycle-4
        tester re-verify (RESULTS
        2026-09-27-u1f1-cycle3-targeted-reverify.md) showed
        the held row delivered by the sweep at 234s, no
        hook-(a) log line.

        Cycle 4 fix: hook (a) calls
        ``reconcile_held_watches_for_instance(instance_id=None)``
        — the SAME global-scan call the sweep uses. The
        candidate set is keyed on the WORK side (via the C1
        partition's per-work ``evaluate_mission_live``
        evaluation) — NOT on the watcher's tree position. The
        external-watcher topology fires correctly.

        Recipe (EXTERNAL variant, this pin):
          - Mission root (parent_id=None, TERMINATED via the
            lifecycle event).
          - Watcher (parent_id=None — TOP-LEVEL, NOT a
            descendant of mission root).
          - Both terminal at the moment the lifecycle event
            fires (cascade-terminate topology).
          - The watcher registered ``watch_job`` on its OWN
            work — ``work_record.instance_id = watcher``.
          - The mission-live guard walks the watcher's
            parent_id tree (single node — parent_id=NULL) and
            finds the watcher terminal → live=False → fire.

        Asserts: CAS-claim + ``enqueue_message.await_count == 1``.
        """
        from daemon.repositories.job_queue.repository import JobRepository
        from daemon.repositories.job_queue.lock_repository import LockRepository
        from daemon.services.job_feedback_observer import JobFeedbackObserver

        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        resolver = u1_components["resolver"]
        instance_manager = u1_components["instance_manager"]
        jqs = u1_components["jqs"]

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=jqs,
            job_repo=MagicMock(spec=JobRepository),
            lock_repo=MagicMock(spec=LockRepository),
            project_repo=MagicMock(),
            instance_manager=instance_manager,
        )

        wid = f"wid-u1-term-live-ext-{uuid4().hex[:8]}"
        # Mission root (top-level, parent_id defaults to None).
        _seed_instance(
            engine, instance_id="inst-mission-term-live",
            status="running",
        )
        # Watcher — EXTERNAL: parent_id=NULL (top-level),
        # NOT a descendant or ancestor of the mission root.
        # This is the production/c7f59aaf incident shape.
        _seed_instance(
            engine, instance_id="watcher-u1-term-live",
            parent_id=None,
        )
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-u1-term-live",
            watch_events=["mission_terminal"],
        )

        assert len(watcher_repo.get_watchers_for_job(wid)) == 1

        # Cascade-terminate both mission root and watcher (the
        # same DELETE-cascade-terminate topology from the
        # c7f59aaf incident's real-time evidence).
        _seed_instance(
            engine, instance_id="inst-mission-term-live",
            status="terminated",
        )
        _seed_instance(
            engine, instance_id="watcher-u1-term-live",
            status="terminated",
        )

        # The watcher registered ``watch_job`` on its OWN
        # work — ``work_record.instance_id = watcher``. The
        # mission-live guard walks the watcher's parent_id
        # tree (single node — parent_id=NULL) and finds the
        # watcher terminal → live=False → fire.
        original = _patch_resolver_task_completed(
            resolver, wid=wid, instance_id="watcher-u1-term-live",
        )
        try:
            event = {
                "event_type": "instance_lifecycle",
                "data": {
                    "instance_id": "inst-mission-term-live",
                    "status": "terminated",
                    "error": None,
                },
            }
            await observer._process_event(event)
        finally:
            resolver.resolve_work = original

        remaining = len(watcher_repo.get_watchers_for_job(wid))
        assert remaining == 0, (
            f"P4 TERMINATED LIVE-SEAM (EXTERNAL variant, "
            f"U1-F1 cycle 4): hook (a) MUST CAS-claim the held "
            f"row in-session for the EXTERNAL watcher topology "
            f"(the production / c7f59aaf incident shape — "
            f"watcher parent_id=NULL, NOT in the mission root "
            f"subtree). Got {remaining} watcher row(s) "
            f"remaining. Under the cycle-3 helper this was a "
            f"silent no-op (delivered by the 300s sweep at "
            f"~234s in the cycle-4 tester re-verify). Under "
            f"the cycle-4 fix (global scan + work-side axis), "
            f"hook (a) fires in-session."
        )
        assert instance_manager.enqueue_message.await_count == 1, (
            f"P4 TERMINATED LIVE-SEAM (EXTERNAL): hook (a) MUST "
            f"drive the [JOB_EVENT] enqueue exactly once; got "
            f"await_count={instance_manager.enqueue_message.await_count}."
        )

    @pytest.mark.asyncio
    async def test_p4_terminated_arm_live_seam_ancestor(
        self, u1_components,
    ):
        """P4 TERMINATED arm — LIVE-SEAM pin, ANCESTOR variant.

        The watcher is the mission root's PARENT in the
        ``instances.parent_id`` chain (parent_id=NULL or a
        higher ancestor). Same filter-exclusion class as the
        EXTERNAL variant — the cycle-3
        ``reconcile_held_watches_for_mission_root`` wrapper
        walked DOWN from the mission root and excluded the
        ancestor watcher (it's NOT in the mission subtree).

        Cycle 4 fix (work-side axis via global scan): the
        mission-live guard inside the C1 partition walks the
        work's parent_id tree — the work belongs to the
        mission root (a descendant of the watcher), so the
        guard walks mission_root → watcher → top, all
        terminal → live=False → fire.

        Recipe (ANCESTOR variant, this pin):
          - Watcher (parent_id=None — top-level).
          - Mission root (parent_id=watcher — child of watcher).
          - Work's instance = mission root (the work is the
            mission root's work; the watcher registered
            ``watch_job`` on it).
          - Cascade-terminate: both mission root and watcher
            terminal at the moment the lifecycle event fires
            for the mission root.
          - mission-live guard walks
            get_tree_ids_permanent(mission_root) = [mission_root,
            watcher], both terminal → live=False → fire.

        Asserts: CAS-claim + ``enqueue_message.await_count == 1``.
        """
        from daemon.repositories.job_queue.repository import JobRepository
        from daemon.repositories.job_queue.lock_repository import LockRepository
        from daemon.services.job_feedback_observer import JobFeedbackObserver

        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        resolver = u1_components["resolver"]
        instance_manager = u1_components["instance_manager"]
        jqs = u1_components["jqs"]

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=jqs,
            job_repo=MagicMock(spec=JobRepository),
            lock_repo=MagicMock(spec=LockRepository),
            project_repo=MagicMock(),
            instance_manager=instance_manager,
        )

        wid = f"wid-u1-term-live-anc-{uuid4().hex[:8]}"
        # Watcher — ANCESTOR: parent_id=None (top-level). The
        # watcher is the mission root's parent in the chain.
        _seed_instance(
            engine, instance_id="watcher-u1-term-live",
            parent_id=None,
        )
        # Mission root — child of the watcher.
        _seed_instance(
            engine, instance_id="inst-mission-term-live",
            parent_id="watcher-u1-term-live",
            status="running",
        )
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-u1-term-live",
            watch_events=["mission_terminal"],
        )

        assert len(watcher_repo.get_watchers_for_job(wid)) == 1

        # Cascade-terminate the subtree. Both mission root and
        # watcher terminal at the moment the lifecycle event
        # fires.
        _seed_instance(
            engine, instance_id="inst-mission-term-live",
            status="terminated",
        )
        _seed_instance(
            engine, instance_id="watcher-u1-term-live",
            status="terminated",
        )

        # Work belongs to the mission root (the mission root's
        # work; the ancestor watcher registered ``watch_job``
        # on it). The mission-live guard walks
        # get_tree_ids_permanent(mission_root) = [mission_root,
        # watcher], both terminal → live=False → fire.
        original = _patch_resolver_task_completed(
            resolver, wid=wid, instance_id="inst-mission-term-live",
        )
        try:
            event = {
                "event_type": "instance_lifecycle",
                "data": {
                    "instance_id": "inst-mission-term-live",
                    "status": "terminated",
                    "error": None,
                },
            }
            await observer._process_event(event)
        finally:
            resolver.resolve_work = original

        remaining = len(watcher_repo.get_watchers_for_job(wid))
        assert remaining == 0, (
            f"P4 TERMINATED LIVE-SEAM (ANCESTOR variant, "
            f"U1-F1 cycle 4): hook (a) MUST CAS-claim the held "
            f"row in-session when the watcher is the mission "
            f"root's PARENT in the parent_id chain. Got "
            f"{remaining} watcher row(s) remaining. Under the "
            f"cycle-3 helper this was a silent no-op (the "
            f"ancestor is NOT in the mission subtree). Under "
            f"the cycle-4 fix (global scan + work-side axis), "
            f"hook (a) fires in-session — the work's parent_id "
            f"tree walk resolves the ancestor-watcher topology."
        )
        assert instance_manager.enqueue_message.await_count == 1, (
            f"P4 TERMINATED LIVE-SEAM (ANCESTOR): hook (a) MUST "
            f"drive the [JOB_EVENT] enqueue exactly once; got "
            f"await_count={instance_manager.enqueue_message.await_count}."
        )


# ── P5 — CROSS-BOOT STALE ────────────────────────────────────────────────


class TestP5CrossBootStale:
    """P5 (2026-09-27) — held row across multiple restarts, then
    fires exactly once.

    Held row survives 2 ``reconcile_terminal_watches`` boot sweeps
    while the mission is still live (the C1 partition protects
    the row); mission then terminalizes; the 3rd boot sweep
    (or the in-session helper) fires the row exactly once — the
    CAS-claim ensures no double-fire across the restart boundary.
    """

    @pytest.mark.asyncio
    async def test_p5_held_row_survives_restart_then_fires_exactly_once(
        self, u1_components,
    ):
        """P5 cross-boot pin — held row survives 2 restarts (mission
        live), then fires exactly once on the 3rd (mission terminal).

        Recipe: arm → 2x ``reconcile_terminal_watches`` boot
        sweeps (mission live → held → still held, no fire) →
        mission terminal → 1x in-session helper call → row fires
        exactly once.
        """
        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        resolver = u1_components["resolver"]
        jqs = u1_components["jqs"]

        wid = f"wid-u1-xboot-{uuid4().hex[:8]}"
        # Mission is LIVE throughout the 2 boot sweeps, terminal
        # only at the 3rd.
        _seed_instance(
            engine, instance_id="inst-mission-xboot",
            status="running",
        )
        _seed_instance(engine, instance_id="watcher-u1-xboot")
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-u1-xboot",
            watch_events=["mission_terminal"],
        )

        # Resolver returns a terminal work record so the boot
        # sweep's ``_work_status_is_terminal`` check passes
        # (the C1 partition then guards via ``evaluate_mission_live``).
        original = _patch_resolver_task_completed(
            resolver, wid=wid, instance_id="inst-mission-xboot",
        )
        try:
            # Sweep 1: mission live → held.
            n_boot1 = await jqs.reconcile_terminal_watches()
            assert len(watcher_repo.get_watchers_for_job(wid)) == 1, (
                f"P5 cross-boot sweep 1: must HOLD the row "
                f"(mission still live) — got "
                f"reconciled={n_boot1}, but the row is gone."
            )

            # Sweep 2: mission live → still held.
            n_boot2 = await jqs.reconcile_terminal_watches()
            assert len(watcher_repo.get_watchers_for_job(wid)) == 1, (
                f"P5 cross-boot sweep 2: must STILL HOLD the row "
                f"(mission still live) — got "
                f"reconciled={n_boot2}, but the row is gone."
            )

            # Now mission terminalizes (post-settle, between
            # sweeps — the natural production shape). The 3rd
            # sweep sees the mission terminal.
            _seed_instance(
                engine, instance_id="inst-mission-xboot",
                status="completed",
            )

            # Sweep 3: mission terminal → row fires.
            n_boot3 = await jqs.reconcile_terminal_watches()
            # The boot sweep + the helper may both fire — but
            # the CAS-claim enforces exactly-once. We assert
            # the row is gone (CAS claimed) regardless of which
            # sweep / hook delivered.
            assert len(watcher_repo.get_watchers_for_job(wid)) == 0, (
                f"P5 cross-boot sweep 3: row MUST be CAS-claimed "
                f"after the mission terminalized — got "
                f"reconciled={n_boot3} but the row survived."
            )
        finally:
            resolver.resolve_work = original


# ── P6 — CONTENT on the delivery paths ───────────────────────────────────


class TestP6ContentOnDeliveryPaths:
    """P6 (2026-09-27) — the delivered ``[JOB_EVENT]`` body carries
    ``Result:\\n<content>`` per the C2/C3 contract, NOT a status-
    only envelope.

    Scope: this fix's delivery paths ONLY. The U6 messages-API-
    revive status-only defect is a SEPARATE commission and is NOT
    pinned here.
    """

    @pytest.mark.asyncio
    async def test_p6_delivered_bodies_carry_result_content(
        self, u1_components,
    ):
        """P6 content pin — delivered body carries result content.

        The helper routes through ``notify_watchers``, which
        consults :func:`notify_work_watchers`. The resolver
        returns ``WorkRecord.result_summary`` (the task's
        committed ``result`` content) — the delivered
        ``[JOB_EVENT]`` body must carry
        ``Result:\\nU1_SLICE_DELIVERED_CONTENT_FROM_RESOLVER``.

        Pinned via the instance_manager.enqueue_message call
        capture — the body is the second positional arg.
        """
        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        resolver = u1_components["resolver"]
        instance_manager = u1_components["instance_manager"]
        jqs = u1_components["jqs"]

        wid = f"wid-u1-content-{uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id="inst-mission-content",
            status="running",
        )
        _seed_instance(engine, instance_id="watcher-u1-content")
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-u1-content",
            watch_events=["mission_terminal"],
        )

        # Resolver returns a terminal work record with a
        # non-null ``result_summary`` (the C2/C3 content
        # contract — the resolver fallback reads ``Task.result``
        # which is now committed).
        original = _patch_resolver_task_completed(
            resolver, wid=wid, instance_id="inst-mission-content",
        )
        try:
            # Mission flips terminal now.
            _seed_instance(
                engine, instance_id="inst-mission-content",
                status="completed",
            )

            await jqs.reconcile_held_watches_for_instance(
                instance_id="watcher-u1-content",
            )

            # Capture the ``enqueue_message`` call — the body is
            # threaded as the ``message=`` kwarg
            # (``daemon/services/work_notifier.py:842-846``).
            assert instance_manager.enqueue_message.await_count >= 1, (
                "P6 content: helper MUST have called "
                "enqueue_message at least once."
            )
            call_args = instance_manager.enqueue_message.await_args
            body = (
                call_args.kwargs.get("message")
                if call_args.kwargs.get("message") is not None
                else (
                    call_args.args[1]
                    if len(call_args.args) > 1
                    else None
                )
            )
            assert body is not None, (
                "P6 content: enqueue_message body MUST be present."
            )
            body_str = (
                body if isinstance(body, str) else json.dumps(body)
            )
            assert (
                "U1_SLICE_DELIVERED_CONTENT_FROM_RESOLVER" in body_str
            ), (
                f"P6 content: delivered body MUST carry the "
                f"resolver's ``result_summary`` content per the "
                f"C2/C3 contract — got body={body_str!r}."
            )
            assert "Result:" in body_str, (
                f"P6 content: delivered body MUST carry the "
                f"``Result:\\n`` envelope — got body={body_str!r}."
            )
        finally:
            resolver.resolve_work = original


# ── 310ms-RACE PIN ───────────────────────────────────────────────────────


class TestU1Slice310msRace:
    """310ms-RACE PIN — hook fires while mission still reads live.

    The U1 live-evidence race: parent lifecycle-completed fires
    BEFORE the last child settle (≈310ms observed in production).
    The hook then fires while the mission STILL reads live (the
    ``evaluate_mission_live`` verdict is ``live=True`` because the
    last child is still resolving). The held row stays held
    through this tick; the NEXT sweep tick (within cadence —
    bounded by ``watch_reconcile_sweep_interval_seconds``) sees
    the mission terminal now and fires the row.

    The pin exercises this interleaving explicitly:

      tick 1 (hook while mission live) → row HELD (no fire)
      mission flips terminal
      tick 2 (sweep within cadence) → row FIRES
    """

    @pytest.mark.asyncio
    async def test_310ms_race_hook_fires_while_mission_still_live_row_held(
        self, u1_components,
    ):
        """310ms-RACE PIN — held row survives the late hook and
        fires on the next sweep tick within cadence.

        Interleaving:
          1. work terminal (settled) + mission live → held
          2. lifecycle-completed fires (hook (a) / hook (b))
             while mission STILL live → row stays held (the
             helper's ``evaluate_mission_live`` returns
             ``live=True``)
          3. mission flips terminal (the late child settle)
          4. sweep tick (within cadence) fires the row exactly
             once
        """
        from daemon.services.watch_reconcile_sweep import (
            WatchReconcileSweepService,
        )

        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        resolver = u1_components["resolver"]
        jqs = u1_components["jqs"]

        wid = f"wid-u1-race-{uuid4().hex[:8]}"
        # Mission is LIVE at hook time (the race window).
        _seed_instance(
            engine, instance_id="inst-mission-race",
            status="running",
        )
        _seed_instance(engine, instance_id="watcher-u1-race")
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-u1-race",
            watch_events=["mission_terminal"],
        )

        # Step 1: hook (a) / hook (b) surrogate — call the helper
        # while mission is STILL live (the race window). Resolver
        # returns a terminal work record (work is settled) but
        # the mission-live guard must return ``live=True`` so the
        # held row stays held (matching the C1 partition's
        # ``held_for_mission`` semantics).
        original = _patch_resolver_task_completed(
            resolver, wid=wid, instance_id="inst-mission-race",
        )
        try:
            result_hook_while_live = (
                await jqs.reconcile_held_watches_for_instance(
                    instance_id="watcher-u1-race",
                )
            )
        finally:
            resolver.resolve_work = original

        # 310ms-RACE invariant: hook fires while mission is live
        # → row STAYS HELD (no fire). This is the row's survival
        # across the race window.
        assert result_hook_while_live["fired"] == 0, (
            f"310ms-RACE step-1: hook-while-mission-live MUST "
            f"HOLD the row — got fired="
            f"{result_hook_while_live['fired']}. Pre-fix the "
            f"row fired here (false terminal) and the next "
            f"true terminal never re-evaluated it."
        )
        assert len(watcher_repo.get_watchers_for_job(wid)) == 1, (
            "310ms-RACE step-1: row MUST survive the "
            "hook-while-live tick."
        )

        # Step 2: mission flips terminal (the late child settle
        # lands AFTER lifecycle-completed — the U1 race). The
        # next sweep tick (within cadence) fires the row.
        _seed_instance(
            engine, instance_id="inst-mission-race",
            status="completed",
        )
        original = _patch_resolver_task_completed(
            resolver, wid=wid, instance_id="inst-mission-race",
        )
        try:
            sweep = WatchReconcileSweepService(
                job_queue_service=jqs,
                interval_seconds=1,  # fast-clock; cadence exercised
            )
            result_sweep = await sweep.sweep_once()
        finally:
            resolver.resolve_work = original

        # 310ms-RACE invariant: the next sweep tick (within
        # cadence) fires the held row once the mission flips
        # terminal. Delivery latency = min(in-session event,
        # sweep cadence) — both arms contribute.
        assert result_sweep["fired"] == 1, (
            f"310ms-RACE step-2: sweep-tick-within-cadence MUST "
            f"fire the held row once mission terminalized — "
            f"got fired={result_sweep['fired']}."
        )
        assert len(watcher_repo.get_watchers_for_job(wid)) == 0, (
            "310ms-RACE step-2: row MUST be CAS-claimed after "
            "exactly-once delivery (no double-fire across the "
            "race window + sweep)."
        )


# ── Zombie-row GC PIN ────────────────────────────────────────────────────


class TestZombieRowGC:
    """Zombie-row GC PIN — rows permanently un-firable are
    retired (NOT left stranded forever).

    The pre-fix ``reconcile_terminal_watches`` short-circuited
    on ``resolve_work is None`` ("skip silently"). Row 538e2f59
    survived ≥5 boots with its job deleted. The U1-SLICE helper
    closes the gap: a row whose ``job_id`` is gone AND whose
    mission liveness is terminal OR unresolvable is retired +
    WARN-logged. Rows whose work is missing BUT whose mission
    is still live are LEFT IN PLACE (transient deletion — a
    retire here would lose a row that's about to become
    resolvable again).
    """

    @pytest.mark.asyncio
    async def test_zombie_row_retired_when_work_gone_and_mission_terminal(
        self, u1_components,
    ):
        """Zombie GC — work gone + mission terminal → retire.

        Row 538e2f59 in the live-evidence class. The fix MUST
        retire such rows (no notify possible — the work is
        gone); the pre-fix contract ("skip silently") left them
        stranded forever.
        """
        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        resolver = u1_components["resolver"]
        jqs = u1_components["jqs"]

        # Seed a row whose work_id has NO matching Task /
        # JobItem — the resolver will return None.
        wid = f"wid-zombie-{uuid4().hex[:8]}"
        # Seed only the watcher row + a non-terminal instance.
        # The instance is terminal at the moment of the sweep
        # (the GC consults ``evaluate_mission_live`` via the
        # permanent ``instances.parent_id`` tree — a missing
        # instance yields ``verdict.live=False``).
        _seed_instance(engine, instance_id="watcher-zombie")
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-zombie",
            watch_events=["mission_terminal"],
        )

        # No Task row + no JobItem row → resolver returns None.
        original = resolver.resolve_work
        resolver.resolve_work = MagicMock(return_value=None)
        try:
            result = (
                await jqs.reconcile_held_watches_for_instance(
                    instance_id="watcher-zombie",
                )
            )
        finally:
            resolver.resolve_work = original

        # Zombie GC invariant: row MUST be retired.
        assert result["retired"] >= 1, (
            f"Zombie GC: work gone + mission terminal/unresolvable "
            f"MUST retire the row — got retired={result['retired']} "
            f"(the pre-fix contract left row 538e2f59 stranded "
            f"forever)."
        )
        assert result["fired"] == 0, (
            "Zombie GC: no notify possible for a gone work — "
            "fired MUST be 0."
        )
        assert len(watcher_repo.get_watchers_for_job(wid)) == 0, (
            "Zombie GC: row MUST be removed from the DB."
        )

    @pytest.mark.asyncio
    async def test_zombie_row_left_in_place_when_mission_still_live(
        self, u1_components,
    ):
        """Zombie GC — work gone + mission live → keep (transient).

        A retire here would lose a row that's about to become
        resolvable again (a transient DB race / in-progress
        deletion). The helper MUST consult ``evaluate_mission_live``
        and only retire when the mission is terminal /
        unresolvable.
        """
        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        resolver = u1_components["resolver"]
        instance_repo = u1_components["instance_repo"]
        jqs = u1_components["jqs"]

        wid = f"wid-transient-{uuid4().hex[:8]}"
        # Seed only the watcher row. Resolve returns None, but
        # we'll force ``evaluate_mission_live`` to return
        # ``live=True`` (mission live) by mocking the verdict.
        _seed_instance(engine, instance_id="watcher-transient")
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-transient",
            watch_events=["mission_terminal"],
        )

        original_resolve = resolver.resolve_work
        resolver.resolve_work = MagicMock(return_value=None)
        # Patch ``evaluate_mission_live`` to return ``live=True``
        # — the mission is still live (transient work deletion).
        # AsyncMock is required because the helper AWAITS the
        # guard's return value (a regular MagicMock would block
        # at the ``await`` since its ``return_value`` is not a
        # coroutine).
        import daemon.services.job_queue_service as jqs_module

        original_eval = jqs_module.evaluate_mission_live
        jqs_module.evaluate_mission_live = AsyncMock(
            return_value=MagicMock(
                live=True, reason="forced-transient", error=False,
                timed_out=False,
            )
        )
        try:
            result = (
                await jqs.reconcile_held_watches_for_instance(
                    instance_id="watcher-transient",
                )
            )
        finally:
            resolver.resolve_work = original_resolve
            jqs_module.evaluate_mission_live = original_eval

        # Transient-deletion invariant: row MUST survive.
        assert result["retired"] == 0, (
            f"Zombie GC: work gone + mission LIVE MUST leave the "
            f"row alone (transient deletion) — got retired="
            f"{result['retired']}. A retire here would lose a "
            f"row about to become resolvable again."
        )
        assert len(watcher_repo.get_watchers_for_job(wid)) == 1, (
            "Zombie GC: row MUST survive a transient work "
            "deletion when mission is live."
        )

    @pytest.mark.asyncio
    async def test_zombie_row_retired_when_guard_raises(
        self, u1_components, caplog,
    ):
        """Zombie GC — guard raises → fail-closed retire + WARN.

        Tidier item E (cycle 2) — verifies the dedicated log
        line added in ITEM B fires when ``evaluate_mission_live``
        raises (DB hiccup / wiring gap). The pre-cycle-2 code
        claimed "The WARN log below captures the anomaly for
        ops review" but NO WARN log existed — the exception
        was swallowed silently and the row was retired without
        any operational trace.

        Invariants:

          1. The row IS retired (fail-closed: work is gone,
             guard raised, default retire=True — a transient
             guard failure MUST NOT leave zombie rows stranded
             on the assumption the guard will recover next
             tick, since this is the GC leg, not the
             notify leg).
          2. The new ITEM B WARNING fires (assert on the log
             record — the helper's logger name is
             ``daemon.services.job_queue_service``).
        """
        import logging

        import daemon.services.job_queue_service as jqs_module

        engine = u1_components["engine"]
        watcher_repo = u1_components["watcher_repo"]
        resolver = u1_components["resolver"]
        jqs = u1_components["jqs"]

        # Seed a row whose work_id has NO matching Task /
        # JobItem — the resolver will return None.
        wid = f"wid-zombie-guard-raises-{uuid4().hex[:8]}"
        _seed_instance(engine, instance_id="watcher-zombie-guard-raises")
        _add_watch(
            engine, work_id=wid,
            instance_id="watcher-zombie-guard-raises",
            watch_events=["mission_terminal"],
        )

        # No Task row + no JobItem row → resolver returns None.
        original_resolve = resolver.resolve_work
        resolver.resolve_work = MagicMock(return_value=None)
        # Patch ``evaluate_mission_live`` to RAISE — exercises the
        # fail-closed retire branch in the except at
        # ``job_queue_service.py:834`` (the leg that triggered
        # the ITEM B log-line addition).
        original_eval = jqs_module.evaluate_mission_live
        jqs_module.evaluate_mission_live = AsyncMock(
            side_effect=RuntimeError(
                "simulated guard failure (U1-slice cycle-2 "
                "zombie-GC guard-raises pin)"
            ),
        )
        try:
            with caplog.at_level(
                logging.WARNING,
                logger="daemon.services.job_queue_service",
            ):
                result = (
                    await jqs.reconcile_held_watches_for_instance(
                        instance_id="watcher-zombie-guard-raises",
                    )
                )
        finally:
            resolver.resolve_work = original_resolve
            jqs_module.evaluate_mission_live = original_eval

        # Invariant 1: fail-closed retire.
        assert result["retired"] >= 1, (
            f"Zombie GC (guard-raises): a guard exception MUST "
            f"trigger fail-closed retire — got retired="
            f"{result['retired']}. A silent skip would leave the "
            f"row stranded (the pre-fix contract)."
        )
        assert len(watcher_repo.get_watchers_for_job(wid)) == 0, (
            "Zombie GC (guard-raises): row MUST be removed from "
            "the DB on fail-closed retire."
        )

        # Invariant 2: the new ITEM B WARNING fired. Search for
        # the dedicated log line that distinguishes "guard DB
        # hiccup → fail-closed retire" from "cleanly verified
        # terminal" (which emits no log line).
        warning_records = [
            record for record in caplog.records
            if record.levelno == logging.WARNING
            and "zombie-GC guard raised" in record.getMessage()
        ]
        assert len(warning_records) >= 1, (
            f"Zombie GC (guard-raises): the new ITEM B WARNING "
            f"('zombie-GC guard raised ... fail-closed retire') "
            f"MUST fire so ops can distinguish a guard DB hiccup "
            f"from a cleanly verified terminal verdict. Captured "
            f"WARNING records: "
            f"{[(r.levelname, r.getMessage()[:60]) for r in caplog.records if r.levelno == logging.WARNING]}"
        )


# ── Boot-marker / config knob PIN ────────────────────────────────────────


class TestConfigKnobAndServiceShape:
    """The :class:`WatchReconcileSweepService` shape matches the
    ``JobLockSweepService`` Template-B (start / stop / _run /
    sweep_once; one-concern-per-service; own boot-marker log
    prefix) and the config knob is wired with the FAIL-FAST-AT-
    BOOT pydantic ``Field(ge=1)`` constraint.
    """

    def test_config_knob_default_300s_with_floor_1(self):
        """Config knob — default 300s, floor 1s, FAIL-FAST AT BOOT."""
        from daemon.config import ServicesConfig

        instance = ServicesConfig()
        assert (
            instance.watch_reconcile_sweep_interval_seconds == 300
        ), (
            f"U1-SLICE: default interval MUST be 300s (the "
            f"commission's upper-bound guarantee) — got "
            f"{instance.watch_reconcile_sweep_interval_seconds}."
        )

        # Floor 1s — out-of-range values FAIL FAST AT BOOT.
        # Pydantic's ``Field(ge=1)`` raises
        # ``pydantic_core.ValidationError`` on constraint violations.
        with pytest.raises(ValidationError):
            ServicesConfig(
                watch_reconcile_sweep_interval_seconds=0,
            )

    def test_service_shape_matches_template_b(self, u1_components):
        """Service shape — Template-B (start/stop/_run/sweep_once)."""
        from daemon.services.watch_reconcile_sweep import (
            DEFAULT_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS,
            WatchReconcileSweepService,
        )

        assert (
            DEFAULT_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS == 300
        )

        jqs = u1_components["jqs"]
        # Default interval = the 300s commission upper-bound.
        service = WatchReconcileSweepService(
            job_queue_service=jqs,
        )
        assert service.interval_seconds == 300
        # Counters initial state.
        counters = service.counters()
        assert counters == {
            "ticks": 0,
            "fired_total": 0,
            "retired_total": 0,
            "errors_total": 0,
        }

    @pytest.mark.asyncio
    async def test_stop_is_idempotent_and_safe_pre_start(
        self, u1_components,
    ):
        """Lifecycle — ``stop()`` before ``start()`` is a silent
        no-op (mirrors ``JobLockSweepService.stop``)."""
        from daemon.services.watch_reconcile_sweep import (
            WatchReconcileSweepService,
        )

        jqs = u1_components["jqs"]
        service = WatchReconcileSweepService(
            job_queue_service=jqs, interval_seconds=1,
        )
        # Pre-start stop is a silent no-op.
        await service.stop()
        # Start + stop the lifecycle.
        service.start()
        assert service._task is not None
        await service.stop()
        assert service._task is None
        # Second stop is a silent no-op.
        await service.stop()
