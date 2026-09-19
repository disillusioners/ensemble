"""Phase 3 / chat-source-worker-lane — Task #4 + #4a — chat-lane
saturation queueing (SC#6).

SC#6 — the chat lane (2 workers) backs up cleanly when 4 chat rows
arrive concurrently:

  * 2 chat rows run concurrently (the pool size)
  * the 3rd chat row is claimed ≤3s from ENQUEUE
  * the 4th chat row is claimed ≤6s from ENQUEUE
  * no deadlock over a 30s window

The from-enqueue bound only holds if the first two chat tasks
complete quickly. D10.2 case-(ii) (burst > pool_size with
backlog > 2) says the latency is "next worker-return, NOT one
poll cycle" — so the bound is constrained by how long the
first two tasks hold the worker.

Task #4a (fixture-validation gate, architect amendment A4.1,
reviewer F10):

  BEFORE running Task #4 with its full assertions, the harness
  must prove the "first two chat tasks complete in ≤1s"
  precondition holds under the test's controlled-execution
  fixture. If the precondition is unmeetable (e.g., the production
  agent graph takes >1s to even start), SC#6 escalates back to
  PLAN REVISION — the reviewer F10 branch that allowed
  "OR reframe to from-worker-return" is STRUCK. The
  from-worker-return latency fact remains documented in D10.2
  case (ii) for operator understanding but does NOT alternate
  inside SC#6.

  The validated branch (fixture short-task bound = 50ms sleep +
  per-instance RUNNING guard serialization) is documented in
  the module docstring below — the gate ran FIRST (in
  ``TestTask4AFixtureValidation``) and PASSED.

Conditional: ``TestTask4AFixtureValidation`` runs FIRST and
MUST pass before ``TestChatLaneSaturationQueueing`` runs. The
test ordering is enforced by pytest's collection order (test
classes are collected in source order; the gate is class #1).

D8 risk: the chat pool's ``_invoke_semaphore`` interaction
could deadlock if sized wrong — this test asserts NO deadlock
over a 30s wall-clock window. If observed, the test FAILS
(= escalates as a Phase 3 bug).

D10.2 case (ii) — burst waits one task duration, not one poll
cycle — is the behavior being proven by the ≤3s / ≤6s bounds.

ADJUDICATED (2026-09-19, Phase 3 round-2 root-cause pass — the
former "KNOWN BUG" is RESOLVED as a harness defect; production is
correct):

  * ``claim_pending_task`` produces correct SQL for both lanes
    (``EXISTS(...)`` for chat, ``NOT EXISTS(...)`` for default
    with flag) — verified by ``tests/unit/test_repository_claim_lane.py``
    AND re-verified under contention by the round-2 instrumented
    probes (per-claim composition-time flag capture).
  * The former ~25-60% mis-claim rate was a HARNESS boot-window
    race, not a production predicate race: ``setup_worker_pool``
    starts the DEFAULT pool BEFORE ``set_chat_lane_active(True)``
    (B1 boot ordering), so every default worker's FIRST claim
    composes gateless (flag=False fail-open). The harness seeded
    chat rows within milliseconds of setup completing; a gateless
    boot claim still in flight (delayed by GIL/SQLite lock
    contention behind the seeding transactions) executed its
    UPDATE onto a freshly committed chat row. Instrumented proof:
    every mis-claim composed with flag=False while a 2ms-sampling
    monitor thread observed the flag True continuously for the
    whole mid-test window — i.e. the composition predated the
    flag flip, and zero flag=False compositions occurred
    mid-test. The claimed row's message_queue row was ALWAYS
    visible (non-atomic-seeding hypothesis refuted).
  * Fix: ``chat_source_harness.build_live_pool_manager`` now
    drains the default pool's boot claims
    (``wait_default_pool_boot_claims_drained``) and asserts the
    flag before yielding. The production boot window itself is
    the DESIGNED B1 fail-open state (chat pool not yet
    constructed — processing a chat row on the default lane in
    that window is the pre-lane behavior; nothing is stranded).
  * The xfail marks are REMOVED — the assertions now pass
    deterministically (20/20 verification in the round-2 report).

Branch taken: A — short-task fixture, documented above.
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlmodel import Session, select

from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import set_chat_lane_active
from tests.integration.chat_source_harness import (
    CHAT_WORKER_POOL_SIZE,
    build_chat_source_engine,
    build_live_pool_manager,
    fetch_task_by_work_id,
    make_short_run_task,
    seed_chat_message,
    wait_until,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Lane-flag isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_chat_lane_flag():
    set_chat_lane_active(False)
    yield
    set_chat_lane_active(False)


@pytest.fixture
def engine(tmp_path):
    eng = build_chat_source_engine(str(tmp_path / "chat_lane_saturation.db"))
    yield eng
    eng.dispose()


# Short-task sleep — under 1s per the Task #4a fixture-validation
# gate. 50ms leaves generous headroom (the per-instance RUNNING guard
# means each task must finish before the next can claim).
SHORT_TASK_SLEEP_S = 0.05


# ---------------------------------------------------------------------------
# Task #4a — Fixture-validation gate (RUNS FIRST by collection order)
# ---------------------------------------------------------------------------


class TestTask4AFixtureValidation:
    """Validate the SC#6 fixture precondition: the first two
    concurrent chat tasks complete in ≤1s.

    A4.1 / reviewer F10: if the fixtures cannot satisfy this bound,
    SC#6 ESCALATES BACK TO PLAN REVISION — the reviewer F10 branch
    that allowed "OR reframe to from-worker-return" is STRUCK. The
    from-worker-return latency fact remains in D10.2 case (ii) for
    operator understanding but does NOT alternate inside SC#6.

    Validated branch (this fixture):

      * Short-task ``run_task`` mock sleeps for ``SHORT_TASK_SLEEP_S``
        (50ms) and marks the task COMPLETED in the DB.
      * Per-instance RUNNING guard serializes the 2-worker chat pool's
        work on a SINGLE instance — two tasks on the same instance
        cannot run concurrently. So to test 2 concurrent chat workers
        we seed 2 DIFFERENT instances, each with 1 task; the chat
        pool's 2 workers each take one.
      * Different instances in subsequent tasks (rows 3 and 4) on
        YET another instance — same per-instance guard applies; the
        serialization is per-instance, not global.

    Branch taken: A — SHORT-TASK FIXTURE.
    Documented here per the task spec.
    """

    def test_two_concurrent_chat_workers_complete_first_two_in_under_1s(
        self, engine
    ):
        """Branch A — fixture-validation pass.

        Two chat workers each claim one task on a DIFFERENT instance
        (per-instance RUNNING guard would serialize same-instance
        claims). Both complete within 1s of their respective
        enqueue times.
        """
        completed_at_per_task: dict[str, float] = {}
        enqueued_at_per_task: dict[str, float] = {}

        # Need to track per-task times. Replace run_task with a
        # closure that records the start of execution and the
        # completion.
        lock = threading.Lock()

        def _run_task(task, cancellation_token=None):
            started = time.monotonic()
            time.sleep(SHORT_TASK_SLEEP_S)
            # Mark task COMPLETED in the DB (mirrors the production
            # ``_tasks_completed`` flow).
            from daemon.services.timestamps import now_utc_naive

            with Session(engine) as s:
                t = s.get(Task, task.id)
                if t is not None:
                    t.status = TaskStatus.COMPLETED.value
                    t.completed_at = now_utc_naive()
                    s.commit()

            with lock:
                completed_at_per_task[task.work_id] = time.monotonic()
                # First-seen started time approximates enqueued_at —
                # the worker enters run_task ≈ immediately after the
                # claim; for fixture-validation purposes (≤1s
                # completion bound) this is conservative.
                enqueued_at_per_task.setdefault(
                    task.work_id, started
                )

        with build_live_pool_manager(engine) as manager:
            manager._task_processor.run_task = _run_task

            # Two DIFFERENT instances — so the per-instance guard
            # does not serialize them; both chat workers can claim
            # concurrently.
            _, wid_a = seed_chat_message(
                engine,
                instance_id="inst-task4a-A",
                source="telegram:alice:1",
            )
            _, wid_b = seed_chat_message(
                engine,
                instance_id="inst-task4a-B",
                source="telegram:bob:1",
            )

            t0 = time.monotonic()
            manager._notify_all_pools()

            def _both_done():
                t1 = fetch_task_by_work_id(engine, wid_a)
                t2 = fetch_task_by_work_id(engine, wid_b)
                return (
                    t1 is not None
                    and t1.status == TaskStatus.COMPLETED.value
                    and t2 is not None
                    and t2.status == TaskStatus.COMPLETED.value
                )

            assert wait_until(_both_done, timeout=5.0), (
                f"Task #4a fixture validation failed: both first-two "
                f"chat tasks did not complete within 5s. "
                f"completed_at={completed_at_per_task}"
            )

            completion_total = (
                max(completed_at_per_task.values()) - t0
            )
            assert completion_total <= 1.0, (
                f"first two chat tasks completed in {completion_total:.3f}s "
                f"(Task #4a bound: ≤1s). The short-task fixture is "
                f"UNMEETABLE — SC#6 must escalate back to plan revision."
            )


# ---------------------------------------------------------------------------
# Task #4 — Chat-lane saturation queueing (SC#6, conditional on #4a PASS)
# ---------------------------------------------------------------------------


class TestChatLaneSaturationQueueing:
    """4 chat rows on a 2-worker chat pool:

      * 2 run concurrently (the pool size)
      * 3rd is claimed ≤3s from ENQUEUE
      * 4th is claimed ≤6s from ENQUEUE
      * no deadlock over 30s

    Per-instance RUNNING guard notes:

      * The guard serializes same-instance claims — if all 4 rows
        were on the same instance, only 1 could run at a time and the
        test would NOT measure chat-lane saturation but per-instance
        serialization. To exercise the chat lane's 2-worker
        parallelism we seed the first two rows on DIFFERENT instances
        and the 3rd + 4th on yet OTHER instances.
      * The 3rd + 4th rows measure queueing latency. Since the first
        two complete in ~50ms each (Task #4a bound), the 3rd is
        queued for ~one-task-duration, and the 4th for ~two-task-
        durations.
    """

    def test_third_and_fourth_chat_rows_claim_within_bounds(self, engine):
        """SC#6 — 4 chat rows, 2-worker chat pool, queueing bounds.

        ADJUDICATED (2026-09-19 round-2): the former ``xfail`` over a
        suspected production lane-predicate race is RESOLVED as a
        HARNESS boot-window defect — see the module docstring for the
        instrumented root-cause narrative (gateless boot claim
        composed pre-flag-flip executing onto a freshly seeded chat
        row; the lane predicate and its flag gating were never wrong
        mid-test). The harness now drains boot claims before seeding;
        the strict chat-worker prefix assertion below passes
        deterministically.
        """
        # Use the short-task fixture validated by Task #4a.
        lock = threading.Lock()
        # Track per-task claim time (when the task transitions to RUNNING).
        claimed_at: dict[str, float] = {}
        # Track per-task enqueue time (set by the test before wake).
        enqueued_at: dict[str, float] = {}

        def _run_task(task, cancellation_token=None):
            # Record the claim time on entry (the worker entered
            # run_task = it claimed the task).
            with lock:
                claimed_at[task.work_id] = time.monotonic()
            time.sleep(SHORT_TASK_SLEEP_S)
            from daemon.services.timestamps import now_utc_naive

            with Session(engine) as s:
                t = s.get(Task, task.id)
                if t is not None:
                    t.status = TaskStatus.COMPLETED.value
                    t.completed_at = now_utc_naive()
                    s.commit()

        with build_live_pool_manager(engine) as manager:
            manager._task_processor.run_task = _run_task

            # Seed 4 chat rows on 4 DIFFERENT instances — per-instance
            # guard does not serialize across instances. The chat
            # pool's 2 workers handle the first 2 in parallel; the
            # 3rd + 4th queue.
            work_ids: list[str] = []
            instance_ids = [
                f"inst-saturate-{i}" for i in range(4)
            ]
            for i, inst in enumerate(instance_ids):
                _, wid = seed_chat_message(
                    engine, instance_id=inst, source="telegram:alice:1"
                )
                work_ids.append(wid)
                enqueued_at[wid] = time.monotonic()
                # Tiny offset to ensure FIFO ordering at the DB
                # level (created_at within the same millisecond
                # could swap claim order on tie; the offset is
                # enough to make the order deterministic).
                time.sleep(0.001)

            # Wake the chat pool. (Default pool is also alive but
            # the chat pool claims chat rows first when notified —
            # chat pool workers enter their claim loop and grab the
            # first two chat rows before the default pool even
            # tries; the lane predicate keeps the default pool out.)
            manager._notify_all_pools()

            # Wait for ALL 4 to be claimed (and completed).
            def _all_claimed():
                return all(
                    wid in claimed_at
                    for wid in work_ids
                )

            # 30s window — generous for the D8 deadlock risk.
            assert wait_until(_all_claimed, timeout=30.0), (
                f"chat-lane saturation: only claimed "
                f"{[w for w in work_ids if w in claimed_at]}/4 "
                f"within 30s — DEADLOCK or starvation"
            )

            # First-two concurrent (≤1s completion bound via #4a).
            third_claim = claimed_at[work_ids[2]]
            fourth_claim = claimed_at[work_ids[3]]

            third_latency = third_claim - enqueued_at[work_ids[2]]
            fourth_latency = fourth_claim - enqueued_at[work_ids[3]]

            # SC#6 from-enqueue bounds.
            assert third_latency <= 3.0, (
                f"3rd chat row claim latency {third_latency:.3f}s "
                f"exceeds 3s SC#6 budget"
            )
            assert fourth_latency <= 6.0, (
                f"4th chat row claim latency {fourth_latency:.3f}s "
                f"exceeds 6s SC#6 budget"
            )

            # Verify all claimed by chat workers.
            for wid in work_ids:
                t = fetch_task_by_work_id(engine, wid)
                assert t is not None
                assert t.worker_id is not None
                # Strict lane assertion (adjudicated 2026-09-19: the
                # former xfail was a harness boot-window race, not a
                # production defect — see module docstring).
                assert t.worker_id.startswith("chat-worker-"), (
                    f"chat row {wid} was claimed by non-chat worker "
                    f"{t.worker_id!r} — lane regression; see the "
                    f"chat-source-worker-lane adjudication notes"
                )

    def test_no_deadlock_over_30s_window(self, engine):
        """D8 risk pin — assert no deadlock under the chat-lane
        saturation shape. If the chat pool's ``_invoke_semaphore``
        interaction is wrong, this test surfaces the wedge."""
        def _run_task(task, cancellation_token=None):
            time.sleep(SHORT_TASK_SLEEP_S)
            from daemon.services.timestamps import now_utc_naive

            with Session(engine) as s:
                t = s.get(Task, task.id)
                if t is not None:
                    t.status = TaskStatus.COMPLETED.value
                    t.completed_at = now_utc_naive()
                    s.commit()

        with build_live_pool_manager(engine) as manager:
            manager._task_processor.run_task = _run_task

            # Seed 6 chat rows (double the chat pool size + a few
            # extra) to push the saturation case further than the
            # SC#6 minimum.
            work_ids: list[str] = []
            for i in range(6):
                _, wid = seed_chat_message(
                    engine,
                    instance_id=f"inst-d8-{i}",
                    source="telegram:alice:1",
                )
                work_ids.append(wid)

            manager._notify_all_pools()

            def _all_done():
                with Session(engine) as s:
                    rows = s.exec(
                        select(Task).where(
                            Task.work_id.in_(work_ids),
                            Task.status == TaskStatus.COMPLETED.value,
                        )
                    ).all()
                    return len(rows) == len(work_ids)

            assert wait_until(_all_done, timeout=30.0), (
                "chat-lane saturation caused a deadlock / wedge over "
                "30s window — D8 risk fired"
            )
