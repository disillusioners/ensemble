"""N1 pins (duplicate-delivery window, 2026-09-03) — claim-first ordering.

Surgical-fix pins for ``daemon.services.work_notifier.notify_work_watchers``
+ ``daemon.repositories.job_queue.watcher_repository`` (claim-first
reorder on terminal statuses).

## Bug shape

Pre-N1 the notifier ran notify-then-claim (``SELECT`` →
``enqueue_message`` per watcher → ``DELETE ... RETURNING``). Two
concurrent terminal callers could both pass the ``SELECT`` with the
same watcher row, each deliver a ``[JOB_EVENT]`` to that watcher
before either ran the ``DELETE`` — a bounded ≤2 duplicate-delivery
window. The repo-level ``claim_watchers_for_job`` CAS existed but
couldn't help unless it ran BEFORE the ``enqueue_message`` loop.

## Fix

* New repo method
  ``JobWatcherRepository.claim_watchers_for_job_for_instances(job_id,
  instance_ids)`` — DELETE...RETURNING scoped to the matching
  ``instance_id`` subset so the held-for-mission rows (M2
  ``mission_terminal`` opt-in with non-terminal mission liveness)
  survive untouched.
* Notifier reordered to CLAIM-FIRST on terminal statuses:
  ``SELECT`` → in-memory partition → ``claim_watchers_for_job_for_instances``
  → notify ONLY the CAS winners. Non-terminal statuses never claim.

## Pins

1. ``test_concurrent_terminal_no_duplicate_delivery`` — two concurrent
   ``notify_work_watchers`` calls on a terminal ``work_id`` with one
   watcher → exactly ONE ``enqueue_message`` (the CAS winner). Pre-N1
   this would deliver twice; post-N1 it delivers once.
2. ``test_concurrent_terminal_no_duplicate_delivery_multi_watchers``
   — same shape with three watchers → exactly THREE deliveries, no
   double counts across the 2 concurrent callers.
3. ``test_claim_precedes_notify`` — instrumented ordering log proves
   the CAS call lands strictly BEFORE the ``enqueue_message`` call
   in the SAME caller thread. The pre-N1 ordering was the opposite
   (notify-then-claim); the post-N1 ordering is claim-then-notify.
4. ``test_held_mission_terminal_survives_concurrent_terminal`` —
   ``mission_terminal`` opt-in with non-terminal mission liveness →
   row survives concurrent terminal callers (the new claim WHERE
   clause excludes held rows).
5. ``test_non_terminal_never_claims`` — ``in_progress`` event fires
   the notify but NEVER calls claim (read-only path); the watcher row
   remains in the DB for the eventual terminal event.

## Test technique

* file-backed SQLite + default ``QueuePool`` (the recipe from
  ``tests/job_queue/test_watcher_repository_concurrent.py`` and the
  project convention in the Testing & QC Conventions doc). StaticPool
  shares a single connection across threads and SQLite's per-connection
  parameter binding is not safe under concurrent statements — it would
  mask the very CAS we need to observe.
* ``threading.Barrier(N)`` so all N concurrent callers fire the
  notify at the same instant — the race window is injected rather
  than relying on scheduler luck.
* ``ThreadPoolExecutor`` for bounded, explicit concurrency.
* Real ``JobWatcherRepository`` + ``TaskRepository`` +
  ``WorkResolverService`` so the test exercises the actual SQL
  atomicity the production code relies on.
* ``AsyncMock`` for ``instance_manager.enqueue_message`` so we can
  count delivery attempts without standing up the LangChain tool
  stack.

## Differential proof

Copy ONLY this file into a ``git worktree`` at pre-fix base
``68202403`` (``uv sync`` inside the worktree; verify
``daemon.__file__`` resolves there). Pre-fix expects:

* Pin 1 → 2 ``enqueue_message`` calls (FAIL — duplicate delivery)
* Pin 2 → 6 ``enqueue_message`` calls (FAIL — duplicate per watcher)
* Pin 3 → ordering log shows notify BEFORE claim (FAIL — wrong order)
* Pin 4 → row deleted (FAIL — held rows incorrectly claimed)
* Pin 5 → still passes (the non-terminal read-only path is unchanged)

Run with::

    pytest tests/job_queue/test_work_notifier_n1_pin.py -v
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.work_notifier import notify_work_watchers
from daemon.services.work_resolver import WorkResolverService


# ── Helpers ───────────────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    agent_id: str = "developer",
    project_id: str = "test-project",
) -> str:
    """Insert an ``Instance`` row so ``resolve_work`` can find it."""
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
                status="running",
                created_at=now_iso,
                updated_at=now_iso,
                paused_at=None,
                parent_id=None,
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
) -> str:
    """Insert a Task row and return the work_id."""
    wid = work_id or str(uuid4())
    with Session(engine) as s:
        task = Task(
            work_id=wid,
            task_type="process_report",
            instance_id=instance_id,
            status=status,
            created_at=datetime.now(timezone.utc),
            is_deferred=False,
        )
        s.add(task)
        s.commit()
    return wid


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def n1_engine(tmp_path):
    """File-backed SQLite engine with default QueuePool.

    Required so concurrent threads each check out their own
    connection — the same recipe as
    ``tests/job_queue/test_watcher_repository_concurrent.py``. With
    ``StaticPool`` the per-connection parameter binding is not safe
    under concurrent statements and the DELETE...RETURNING CAS we
    want to observe is masked by a single-connection serialisation.
    """
    db_path = tmp_path / "n1_pin.db"
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
    JobWatcher.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def n1_components(n1_engine):
    """Bundle: watcher_repo + task_repo + resolver + enqueue mock.

    Returns a ``NamedTuple``-like dict so each test can grab what it
    needs. The ``enqueue_message`` mock is on a plain ``MagicMock``
    instance_manager so we can count calls deterministically.
    """
    watcher_repo = JobWatcherRepository(n1_engine)
    task_repo = TaskRepository(n1_engine)
    instance_repo = SQLModelInstanceRepository(n1_engine)

    # WorkResolverService needs all three backing repos.
    resolver = WorkResolverService(task_repo, _NoOpJobRepo(), instance_repo)

    instance_manager = MagicMock()
    instance_manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-test")
    )

    return {
        "engine": n1_engine,
        "watcher_repo": watcher_repo,
        "task_repo": task_repo,
        "resolver": resolver,
        "instance_manager": instance_manager,
    }


class _NoOpJobRepo:
    """Minimal stand-in for ``JobRepository`` — ``WorkResolverService``
    only uses the Task + Instance paths for the work_ids these tests
    seed, so we hand it an inert job-side stub that returns ``None``
    for everything. This keeps the resolver usable without pulling
    in a JobRepository that needs full job_queue schema setup.
    """

    def get(self, _job_id: str):
        return None

    def __getattr__(self, name: str):
        # Defensive: if ``WorkResolverService`` reaches for a method
        # we haven't stubbed, return a callable that yields ``None``.
        return lambda *_a, **_kw: None


# ── Pin 1: two concurrent terminal callers, one watcher, no duplicate ────────


class TestN1ClaimFirstConcurrentDelivery:
    """N1 — concurrent terminal ``notify_work_watchers`` callers must
    deliver each ``[JOB_EVENT]`` exactly once.

    Pre-N1 (notify-then-claim): both callers SELECT the same watcher
    row, each delivers BEFORE either DELETEs → 2 deliveries on the
    same watcher.

    Post-N1 (claim-first): both callers SELECT the same row, both
    attempt the CAS, only the winner receives the row back, only the
    winner delivers → 1 delivery.
    """

    @pytest.mark.asyncio
    async def test_concurrent_terminal_no_duplicate_delivery(self, n1_components):
        """Two concurrent ``notify_work_watchers`` calls on a terminal
        ``work_id`` with one watcher → exactly ONE ``enqueue_message``.

        Deterministic interleave injection: a ``threading.Barrier(2)``
        is installed as a wrapper around ``watcher_repo.get_watchers_for_job``
        so BOTH callers MUST complete their SELECT before either
        proceeds to the next step. This guarantees both callers see
        the same row at the same instant — the race window the
        notify-then-claim ordering was vulnerable to is now
        deterministic rather than relying on scheduler luck.

        Differential proof: at pre-fix base 68202403, this test
        asserts ``enqueue_message.await_count == 1`` (FAIL — the
        notify-then-claim flow delivers twice on the same watcher
        because both racers have already finished their
        ``enqueue_message`` call before either reaches
        ``claim_watchers_for_job``). Post-fix, the same test asserts
        ``== 1`` (PASS — claim runs first, only the CAS winner
        delivers).
        """
        engine = n1_components["engine"]
        real_watcher_repo = n1_components["watcher_repo"]
        resolver = n1_components["resolver"]
        instance_manager = n1_components["instance_manager"]

        # Seed: 1 instance (the producer), 1 watcher instance, 1 watcher row.
        _seed_instance(engine, instance_id="inst-prod-1")
        _seed_instance(engine, instance_id="watcher-1")
        wid = _seed_task(engine, instance_id="inst-prod-1")
        real_watcher_repo.add_watch(wid, "watcher-1")

        # Barrier(2) — installed on the SELECT so both racers see the
        # same row at the same instant. The race window is INJECTED.
        select_barrier = threading.Barrier(2)
        select_call_count = 0
        select_lock = threading.Lock()
        original_get_watchers = real_watcher_repo.get_watchers_for_job

        def sync_select(job_id):
            nonlocal select_call_count
            with select_lock:
                select_call_count += 1
            select_barrier.wait()  # both threads block here
            return original_get_watchers(job_id)

        # Replace get_watchers_for_job on the repo instance with the
        # synchronised wrapper. The notifier calls this via
        # ``asyncio.to_thread`` so the barrier runs in worker threads,
        # not on the event loop.
        real_watcher_repo.get_watchers_for_job = sync_select

        # Two concurrent terminal calls.
        def call_notify() -> int:
            return asyncio.run(
                notify_work_watchers(
                    wid,
                    "completed",
                    error=None,
                    instance_manager=instance_manager,
                    work_resolver=resolver,
                    watcher_repo=real_watcher_repo,
                )
            )

        with ThreadPoolExecutor(max_workers=2) as ex:
            outcomes = list(ex.map(lambda _: call_notify(), range(2)))

        # Restore the original SELECT before any post-execution call —
        # the assertion below needs to read remaining rows without
        # blocking on the barrier again.
        real_watcher_repo.get_watchers_for_job = original_get_watchers

        # The barrier was reached by BOTH callers — proves both
        # SELECT'd the row at the same instant.
        assert select_call_count == 2, (
            f"N1 interleave injection: both callers must hit SELECT; "
            f"got {select_call_count}."
        )

        # Post-N1 invariants:
        # 1. Exactly ONE delivery happened.
        assert instance_manager.enqueue_message.await_count == 1, (
            f"N1 (duplicate-delivery window): concurrent terminal "
            f"notify_work_watchers calls must produce exactly one "
            f"[JOB_EVENT] enqueue; got "
            f"{instance_manager.enqueue_message.await_count} "
            f"(outcomes={outcomes})."
        )
        # 2. The two caller outcomes sum to 1 — exactly one caller
        #    delivered (notify==1), the other returned 0 (lost CAS).
        assert sum(outcomes) == 1, (
            f"N1: caller outcomes must sum to exactly 1 (one CAS "
            f"winner); got {outcomes}."
        )
        # 3. The watcher row is gone (the CAS winner deleted it).
        remaining = real_watcher_repo.get_watchers_for_job(wid)
        assert remaining == [], (
            f"N1: watcher row must be deleted by the CAS winner; "
            f"found {remaining}."
        )
        # 4. The delivered message carries the canonical
        #    ``[JOB_EVENT]`` prefix + the ``completed ✓`` glyph.
        call_kwargs = instance_manager.enqueue_message.await_args.kwargs
        msg = call_kwargs["message"]
        assert "[JOB_EVENT]" in msg
        assert "completed ✓" in msg
        assert call_kwargs["instance_id"] == "watcher-1"
        assert call_kwargs["source"] == f"internal_agent:job_event:{wid}:completed"


# ── Pin 2: multi-watcher scaling — no duplicates across N watchers ────────────


class TestN1ClaimFirstMultiWatcher:
    """N1 scales: N watchers × 2 concurrent callers → exactly N
    deliveries (not 2N).

    Pre-N1: each of the 2 callers would deliver to all 3 watchers
    → 6 ``enqueue_message`` calls (3 × 2).

    Post-N1: each watcher's row wins exactly one CAS, only one
    caller delivers to it → 3 ``enqueue_message`` calls (3 × 1).
    """

    @pytest.mark.asyncio
    async def test_concurrent_terminal_no_duplicate_delivery_multi_watchers(
        self, n1_components,
    ):
        engine = n1_components["engine"]
        real_watcher_repo = n1_components["watcher_repo"]
        resolver = n1_components["resolver"]
        instance_manager = n1_components["instance_manager"]

        _seed_instance(engine, instance_id="inst-prod-2")
        wid = _seed_task(engine, instance_id="inst-prod-2")

        # Three watchers — each gets its own DB row.
        watcher_ids = [f"watcher-multi-{i}" for i in range(3)]
        for wid_inst in watcher_ids:
            _seed_instance(engine, instance_id=wid_inst)
            real_watcher_repo.add_watch(wid, wid_inst)

        # Deterministic interleave: barrier on the SELECT forces both
        # callers to see the same 3-row snapshot simultaneously.
        select_barrier = threading.Barrier(2)
        original_get_watchers = real_watcher_repo.get_watchers_for_job

        def sync_select(job_id):
            select_barrier.wait()
            return original_get_watchers(job_id)

        real_watcher_repo.get_watchers_for_job = sync_select

        def call_notify() -> int:
            return asyncio.run(
                notify_work_watchers(
                    wid,
                    "completed",
                    error=None,
                    instance_manager=instance_manager,
                    work_resolver=resolver,
                    watcher_repo=real_watcher_repo,
                )
            )

        with ThreadPoolExecutor(max_workers=2) as ex:
            outcomes = list(ex.map(lambda _: call_notify(), range(2)))

        # Restore the original SELECT before any post-execution call.
        real_watcher_repo.get_watchers_for_job = original_get_watchers

        # Each watcher is delivered exactly once across both callers.
        assert instance_manager.enqueue_message.await_count == 3, (
            f"N1 multi-watcher: 3 watchers × 2 concurrent callers "
            f"must yield exactly 3 deliveries (one per watcher), "
            f"got {instance_manager.enqueue_message.await_count}."
        )
        # The set of recipient instance_ids is exactly the 3 watchers.
        delivered = {
            call.kwargs["instance_id"]
            for call in instance_manager.enqueue_message.await_args_list
        }
        assert delivered == set(watcher_ids), (
            f"N1 multi-watcher: delivery set must match watcher set; "
            f"got {delivered}, expected {set(watcher_ids)}."
        )
        # Outcomes sum to 3 — the winner caller delivered 3, the
        # loser delivered 0.
        assert sum(outcomes) == 3, (
            f"N1 multi-watcher: caller outcomes must sum to 3 "
            f"(winner delivers all); got {outcomes}."
        )
        # All watcher rows are gone.
        assert real_watcher_repo.get_watchers_for_job(wid) == []


# ── Pin 3: claim precedes notify in the SAME caller ──────────────────────────


class TestN1ClaimFirstOrdering:
    """N1 ordering pin: the CAS call lands BEFORE the
    ``enqueue_message`` call in the same caller thread.

    Pre-N1 (notify-then-claim): the first ``enqueue_message`` call
    happens BEFORE the ``claim_watchers_for_job`` call.

    Post-N1 (claim-first): the ``claim_watchers_for_job_for_instances``
    call happens BEFORE any ``enqueue_message`` call.

    The test wraps the repo + manager with a thin logging proxy that
    records the wall-clock order of method calls; the assertion
    checks the relative ordering, not the absolute timestamps.
    """

    @pytest.mark.asyncio
    async def test_claim_precedes_notify(self, n1_components):
        engine = n1_components["engine"]
        real_watcher_repo = n1_components["watcher_repo"]
        real_instance_manager = n1_components["instance_manager"]

        # Logged proxy: record wall-clock order of the two key calls.
        call_log: list[str] = []
        call_lock = threading.Lock()

        class _LoggedWatcherRepo:
            """Wraps ``JobWatcherRepository`` so the two CAS methods
            append a log entry, in wall-clock order, on each call.
            """

            def __init__(self, real):
                self._real = real

            def __getattr__(self, name: str):
                return getattr(self._real, name)

            def claim_watchers_for_job(self, job_id):
                with call_lock:
                    call_log.append(f"claim:{job_id}")
                return self._real.claim_watchers_for_job(job_id)

            def claim_watchers_for_job_for_instances(self, job_id, instance_ids):
                with call_lock:
                    call_log.append(
                        f"claim_for_instances:{job_id}:{','.join(sorted(instance_ids))}"
                    )
                return self._real.claim_watchers_for_job_for_instances(
                    job_id, instance_ids
                )

        class _LoggedInstanceManager:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name: str):
                return getattr(self._real, name)

            async def enqueue_message(self, **kwargs):
                with call_lock:
                    call_log.append(
                        f"enqueue:{kwargs.get('instance_id', '?')}"
                    )
                return await self._real.enqueue_message(**kwargs)

        logged_watcher_repo = _LoggedWatcherRepo(real_watcher_repo)
        logged_instance_manager = _LoggedInstanceManager(real_instance_manager)

        # Seed: 1 watcher.
        _seed_instance(engine, instance_id="inst-prod-3")
        _seed_instance(engine, instance_id="watcher-3")
        wid = _seed_task(engine, instance_id="inst-prod-3")
        real_watcher_repo.add_watch(wid, "watcher-3")

        # Sequential (no concurrency needed — we want to pin the
        # ordering inside ONE call).
        notified = await notify_work_watchers(
            wid,
            "completed",
            error=None,
            instance_manager=logged_instance_manager,
            work_resolver=n1_components["resolver"],
            watcher_repo=logged_watcher_repo,
        )
        assert notified == 1

        # Pin: the CAS call (whichever name it lives under) is
        # recorded BEFORE any ``enqueue`` entry.
        claim_indices = [
            i for i, entry in enumerate(call_log) if entry.startswith("claim")
        ]
        enqueue_indices = [
            i for i, entry in enumerate(call_log) if entry.startswith("enqueue")
        ]
        assert claim_indices, (
            f"N1 ordering: claim call must be recorded; got log={call_log}"
        )
        assert enqueue_indices, (
            f"N1 ordering: enqueue call must be recorded; got log={call_log}"
        )
        assert min(claim_indices) < min(enqueue_indices), (
            f"N1 ordering: claim MUST precede enqueue_message in the "
            f"SAME caller thread (claim-first invariant); got "
            f"log={call_log}."
        )
        # Pin: the new method name is the one actually called
        # (post-N1 contract), not the legacy ``claim_watchers_for_job``.
        claim_for_instances_calls = [
            entry for entry in call_log if entry.startswith("claim_for_instances")
        ]
        assert len(claim_for_instances_calls) == 1, (
            f"N1 ordering: ``claim_watchers_for_job_for_instances`` "
            f"must be called exactly once; got "
            f"{claim_for_instances_calls}."
        )
        # The legacy method (claim-all) must NOT be called by the
        # notifier anymore — the new method is the only CAS primitive.
        legacy_claim_calls = [
            entry for entry in call_log
            if entry.startswith("claim:") and not entry.startswith("claim_for_instances")
        ]
        assert legacy_claim_calls == [], (
            f"N1 ordering: legacy ``claim_watchers_for_job`` must "
            f"NOT be called by the notifier (it would re-open the "
            f"duplicate-delivery window for held-for-mission rows); "
            f"got {legacy_claim_calls}."
        )


# ── Pin 4: held-for-mission rows survive concurrent terminal callers ──────────


class TestN1ClaimFirstHeldMissionSurvives:
    """N1 — ``mission_terminal`` opt-in watchers with non-terminal
    mission liveness must SURVIVE concurrent terminal callers (their
    rows stay in the DB for the future terminal event).

    Pre-N1: notify-then-claim used ``claim_watchers_for_job`` (no
    instance_id filter) — it deleted ALL rows for ``work_id``
    regardless of the mission-hold gate. A held-for-mission watcher
    was lost the moment ANY concurrent terminal caller landed.

    Post-N1: the new ``claim_watchers_for_job_for_instances`` is
    scoped to the matching ``instance_id`` subset, so held rows are
    excluded from the CAS WHERE clause and survive untouched.
    """

    @pytest.mark.asyncio
    async def test_held_mission_terminal_survives_concurrent_terminal(
        self, n1_components,
    ):
        engine = n1_components["engine"]
        watcher_repo = n1_components["watcher_repo"]
        resolver = n1_components["resolver"]
        instance_manager = n1_components["instance_manager"]

        # Mirror row (job_type="message"): ``mission_liveness`` is
        # the canonical mission-side vocabulary.
        wid = "wid-mirror-1"
        _seed_instance(engine, instance_id="inst-mirror-1")
        _seed_instance(engine, instance_id="watcher-held")

        # Need a JobItem-backed record that the resolver can find.
        # WorkResolverService looks up JobItem by job_id. For the
        # held case we don't need a real JobItem — the held gate
        # checks ``work_record.job_type == "message"`` and
        # ``mission_liveness not in {completed, failed, cancelled}``.
        # We construct a synthetic WorkRecord that mimics the mirror
        # shape and patch ``resolver.resolve_work`` to return it.
        from daemon.services.work_resolver import WorkRecord

        mirror_record = WorkRecord(
            work_id=wid,
            kind="job",
            status="completed",  # transport terminal
            instance_id="inst-mirror-1",
            project_id="test-project",
            agent_id="developer",
            result_summary=None,
            error=None,
            created_at=datetime.now(timezone.utc),
            job_type="message",
            mission_liveness="processing",  # mission NOT terminal
        )

        original_resolve = resolver.resolve_work
        resolver.resolve_work = MagicMock(return_value=mirror_record)

        # Held watcher (mission_terminal opt-in, mission live).
        held_watcher = MagicMock()
        held_watcher.instance_id = "watcher-held"
        held_watcher.watch_events = ["mission_terminal"]
        # Manually insert a real JobWatcher row for the held
        # watcher so the repo's SELECT path returns it.
        with Session(engine) as s:
            s.add(JobWatcher(
                job_id=wid,
                instance_id="watcher-held",
                watch_events=["mission_terminal"],
            ))
            s.commit()

        # Two concurrent callers. Both will partition this watcher
        # into the held bucket (mission live). The new claim
        # method's WHERE clause excludes held instance_ids — so
        # neither caller deletes the row.
        barrier = threading.Barrier(2)

        def call_notify() -> int:
            barrier.wait()
            return asyncio.run(
                notify_work_watchers(
                    wid,
                    "completed",
                    error=None,
                    instance_manager=instance_manager,
                    work_resolver=resolver,
                    watcher_repo=watcher_repo,
                )
            )

        try:
            with ThreadPoolExecutor(max_workers=2) as ex:
                outcomes = list(ex.map(lambda _: call_notify(), range(2)))
        finally:
            resolver.resolve_work = original_resolve

        # Held path: no notification fires, no claim runs.
        assert instance_manager.enqueue_message.await_count == 0, (
            f"N1 held-mission: held watcher must NOT receive any "
            f"[JOB_EVENT] (mission live); got "
            f"{instance_manager.enqueue_message.await_count} calls."
        )
        assert sum(outcomes) == 0, (
            f"N1 held-mission: both callers must return 0; "
            f"got {outcomes}."
        )
        # The held row SURVIVED — its ``instance_id`` was excluded
        # from the claim WHERE clause.
        remaining = watcher_repo.get_watchers_for_job(wid)
        assert len(remaining) == 1, (
            f"N1 held-mission: held watcher row must SURVIVE "
            f"concurrent terminal callers; got {remaining}."
        )
        assert remaining[0].instance_id == "watcher-held"


# ── Pin 5: non-terminal (in_progress) never claims ────────────────────────────


class TestN1ClaimFirstNonTerminalNeverClaims:
    """N1 — ``in_progress`` (non-terminal) notifies in read-only mode
    and NEVER claims.

    Pre-N1: the old implementation had an
    ``if notified > 0: claim_watchers_for_job`` path that ran on
    EVERY status — but the M2 fix later branched on
    ``_is_terminal(status)`` to skip claim on non-terminal. The N1
    pin verifies the post-N1 code preserves that branch and adds
    the explicit ``claim_watchers_for_job_for_instances``-never-called
    invariant.

    This is the regression guard for the held-watcher-row path:
    if someone refactors the notifier and accidentally calls the
    new claim method on non-terminal statuses, the watcher row is
    deleted before the terminal event fires.
    """

    @pytest.mark.asyncio
    async def test_non_terminal_never_claims(self, n1_components):
        engine = n1_components["engine"]
        watcher_repo = n1_components["watcher_repo"]
        resolver = n1_components["resolver"]
        instance_manager = n1_components["instance_manager"]

        _seed_instance(engine, instance_id="inst-prog-1")
        _seed_instance(engine, instance_id="watcher-prog-1")
        wid = _seed_task(
            engine, instance_id="inst-prog-1",
            status=TaskStatus.RUNNING.value,
        )
        # Watcher subscribes to in_progress only (the M2 dual-terminal
        # gate is irrelevant here — in_progress is not a transport
        # terminal event).
        with Session(engine) as s:
            s.add(JobWatcher(
                job_id=wid,
                instance_id="watcher-prog-1",
                watch_events=["in_progress"],
            ))
            s.commit()

        notified = await notify_work_watchers(
            wid,
            "in_progress",
            error=None,
            instance_manager=instance_manager,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
            progress="50%",
        )

        # Notify fires once (the in_progress match)…
        assert notified == 1
        assert instance_manager.enqueue_message.await_count == 1
        # …but the watcher row SURVIVES — non-terminal never claims.
        remaining = watcher_repo.get_watchers_for_job(wid)
        assert len(remaining) == 1, (
            f"N1 non-terminal: watcher row must SURVIVE in_progress "
            f"(terminal event hasn't fired yet); got {remaining}."
        )
        assert remaining[0].instance_id == "watcher-prog-1"
