"""Phase 3 / chat-source-worker-lane — Task #1 (HEADLINE) — saturation isolation.

Pins SC#1 / SC#3 (the feature's headline guarantee): the chat lane
remains responsive when the default pool is fully saturated.

Harness shape:

  * ``build_live_pool_manager(num_workers=WORKER_POOL_SIZE)`` —
    real ``InstanceManager`` over file-backed SQLite, BOTH pools live
    (default = 5 workers, chat = 2 workers, production sizes per
    ``daemon/constants.py``).
  * Five default-source rows are seeded with BLOCKING
    ``run_task`` mocks — every default worker is pinned inside
    ``run_task`` for the duration of the test; saturation is held
    via a ``release_evt`` until teardown.
  * One chat-source row is then seeded and the chat pool is woken
    (``manager._notify_all_pools()`` per D5 fan-out).
  * The test asserts ``task.worker_id.startswith("chat-worker-")``
    AND ``(claimed_at - enqueued_at) <= 3.5s`` AND the A2.2
    ``workers_woken_by_timeout`` DELTA over [enqueued, claimed]
    equals 0 — proving the notify path delivered the claim
    (D5), not the 3s poll fallback.

A2.2 / reviewer F11 — TWO-READ DELTA (FORBID pre-test zeroing):

  A live worker in the chat pool may concurrently bump
  ``workers_woken_by_timeout`` for unrelated reasons (an idle worker
  timing out for non-test reasons). Pre-test zeroing of the counter
  would race real increments and mask the regression the assertion
  is designed to catch (notify-vs-poll — a 3.5s assert alone cannot
  distinguish the two paths). The two-read DELTA is the race-free
  assertion: snapshot the counter BEFORE the chat row is enqueued,
  snapshot it AGAIN at claim time, assert delta == 0 over the
  [enqueued_at, claimed_at] window.

  The hooking point is ``WorkerPool.get_stats()`` →
  ``stats["workers_woken_by_timeout"]`` (no new code; the existing
  metric at ``daemon/services/worker_pool.py:1463-1493``).

``instance_messaging`` invocation note: the chat row is seeded
directly via the harness (``seed_chat_message``) — bypassing the
``enqueue_message_job`` HTTP/job stack — because the
saturation-isolation claim is a queue seam assertion, not a full
HTTP test. The chat pool still receives a real wake via
``manager._notify_all_pools()`` so the notify path is exercised.

Pre-test zeroing is FORBIDDEN (F11). The autouse lane-flag reset
fixture is module-shared and must NOT touch
``WorkerPool._stats["workers_woken_by_timeout"]`` — see the
``_reset_chat_lane_flag`` autouse below.
"""

from __future__ import annotations

import threading
import time

import pytest

from tests.integration.chat_source_harness import (
    WORKER_POOL_SIZE,
    build_chat_source_engine,
    build_live_pool_manager,
    fetch_task_by_work_id,
    make_blocking_run_task,
    seed_chat_message,
    wait_until,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Lane-flag isolation — shared state must not leak between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_chat_lane_flag():
    """Hard-reset the B1 module flag around EVERY test — shared
    module state must not leak into other suites."""
    from daemon.repositories.task.repository import set_chat_lane_active

    set_chat_lane_active(False)
    yield
    set_chat_lane_active(False)


# ---------------------------------------------------------------------------
# Engine fixture — file-backed SQLite, NullPool, WAL + case_sensitive_like
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    eng = build_chat_source_engine(str(tmp_path / "chat_saturation.db"))
    yield eng
    eng.dispose()


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestSaturationIsolation:
    """Default pool FULLY saturated → chat row claimed in ≤3.5s AND
    no poll-fallback timeout fired over the [enqueue, claim] window.

    Note (i) — the harness MUST construct the default pool at
    ``num_workers=WORKER_POOL_SIZE=5`` (production size) so the
    5-job saturation actually saturates the pool. The Phase 2
    wiring-test recipe uses ``num_workers=1`` because it only needs
    one worker for the boot-line + shutdown assertions; the
    saturation test needs 5.
    """

    def test_chat_message_claimed_by_chat_worker_under_saturation(self, engine):
        """SC#3 — when the default pool is fully saturated (5 long jobs
        filling all 5 workers), a chat message enqueued AFTER the
        saturation is established must be claimed by a chat worker
        in ≤3.5s from enqueue."""
        # One release event shared across ALL 5 default workers so the
        # test can hold the saturation window open until teardown.
        default_release = threading.Event()
        default_claimed: list[str] = []

        with build_live_pool_manager(engine) as manager:
            # Mock default-pool run_task to block — saturation hold.
            manager._task_processor.run_task = make_blocking_run_task(
                release_evt=default_release,
                record_to=default_claimed,
            )

            # Seed 5 default-source rows — matches WORKER_POOL_SIZE=5.
            default_work_ids: list[str] = []
            for i in range(WORKER_POOL_SIZE):
                _, wid = seed_chat_message(
                    engine,
                    instance_id=f"inst-default-{i}",
                    source=f"agent:tester-{i}",
                )
                default_work_ids.append(wid)

            # Wake both pools — default workers claim and block; chat
            # workers idle (no chat row yet).
            manager._notify_all_pools()

            # Wait for ALL 5 default workers to enter run_task. This
            # is the saturation confirmation: every default worker is
            # now blocked, no chat row exists yet, so the chat workers
            # will see empty claim attempts and start their 3s timeout
            # wake loop.
            assert wait_until(
                lambda: len(default_claimed) == WORKER_POOL_SIZE,
                timeout=5.0,
            ), (
                f"default pool did not saturate: "
                f"claimed={default_claimed} (expected {WORKER_POOL_SIZE})"
            )

            # Sanity: chat pool exists with 2 workers (it should — we
            # constructed both pools at production size).
            assert len(manager._chat_worker_pool._workers) == 2

            # --- A2.2 TWO-READ DELTA — read 1 (BEFORE enqueue) ---
            chat_stats_before = manager._chat_worker_pool.get_stats()
            timeouts_before = chat_stats_before["workers_woken_by_timeout"]

            # Enqueue ONE chat row and stamp the enqueued_at.
            enqueued_at = time.monotonic()
            _, chat_work_id = seed_chat_message(
                engine,
                instance_id="inst-chat-1",
                source="telegram:alice:1",
            )

            # Wake the chat pool explicitly (the registry-minted
            # wake fires ``_notify_all_pools`` which fans out to both;
            # we replicate that surface here).
            manager._notify_all_pools()

            # Wait for the chat worker to claim the row.
            def _chat_claimed():
                task = fetch_task_by_work_id(engine, chat_work_id)
                return task is not None and task.status == "running"

            assert wait_until(_chat_claimed, timeout=3.5), (
                f"chat row {chat_work_id} not claimed by chat worker "
                f"within 3.5s — saturation isolation FAILED"
            )

            claimed_at = time.monotonic()
            # --- A2.2 TWO-READ DELTA — read 2 (AT claim) ---
            chat_stats_after = manager._chat_worker_pool.get_stats()
            timeouts_after = chat_stats_after["workers_woken_by_timeout"]

            elapsed = claimed_at - enqueued_at
            assert elapsed <= 3.5, (
                f"chat row claim latency {elapsed:.3f}s exceeds 3.5s "
                f"SC#3 budget"
            )

            # --- A2.2 DELTA assertion (FORBID pre-test zeroing) ---
            delta = timeouts_after - timeouts_before
            assert delta == 0, (
                f"A2.2 violation: workers_woken_by_timeout DELTA "
                f"over [enqueue, claim] = {delta} (expected 0). "
                f"The notify path did NOT deliver the claim; the "
                f"chat worker woke via the 3s poll fallback. "
                f"timeouts_before={timeouts_before} "
                f"timeouts_after={timeouts_after}."
            )

            # Claimed by a chat worker, NOT a default worker.
            task = fetch_task_by_work_id(engine, chat_work_id)
            assert task.worker_id is not None
            assert task.worker_id.startswith("chat-worker-"), (
                f"chat row was claimed by non-chat worker "
                f"{task.worker_id!r} (SC#1 violation)"
            )

            # The 5 default rows were claimed by default workers (the
            # sanity baseline — proves the saturation really did fill
            # the default pool).
            for wid in default_work_ids:
                t = fetch_task_by_work_id(engine, wid)
                assert t is not None
                assert t.worker_id is not None
                assert t.worker_id.startswith("worker-"), (
                    f"default row {wid} claimed by non-default worker "
                    f"{t.worker_id!r} (saturation harness broken)"
                )

            # Release so teardown can join the threads cleanly.
            default_release.set()

    def test_chat_notify_counter_increments_via_wake(self, engine):
        """A2.2 MANDATORY companion assertion: the
        ``notifications_sent`` counter MUST increment when the chat
        pool is woken (proves the wake reached the chat pool — the
        complement of the no-timeout assertion).

        Combined with ``test_chat_message_claimed_by_chat_worker_under_saturation``:
          * the notify counter incremented (this test) → wake reached
            the chat pool
          * the wake did NOT trigger the timeout counter (sibling test)
            → wake was a notification wake, not a poll-fallback wake
        """
        default_release = threading.Event()
        with build_live_pool_manager(engine) as manager:
            manager._task_processor.run_task = make_blocking_run_task(
                release_evt=default_release,
            )

            for i in range(WORKER_POOL_SIZE):
                seed_chat_message(
                    engine,
                    instance_id=f"inst-d-{i}",
                    source=f"agent:tester-{i}",
                )
            manager._notify_all_pools()
            assert wait_until(
                lambda: len(manager._chat_worker_pool._workers) == 2
                and all(w.is_alive() for w in manager._chat_worker_pool._workers),
                timeout=3.0,
            )

            # Read baseline (after default pool is saturated, but no
            # chat rows yet).
            # Sleep a hair so the chat workers' wait_for_work enters
            # its first cycle — the notifications_sent counter is
            # only meaningful after at least one notify_work() has
            # been called.
            time.sleep(0.05)
            before = manager._chat_worker_pool.get_stats()[
                "notifications_sent"
            ]

            # Single explicit notify — same surface that
            # _prepare_enqueued_message would invoke via
            # _notify_all_pools().
            manager._notify_all_pools()

            # After the notify call the counter must have advanced.
            # Wait briefly for the increment to land (the increment
            # is under the stats_lock; we read under the same lock
            # via get_stats, so the read is consistent).
            def _incremented():
                return (
                    manager._chat_worker_pool.get_stats()["notifications_sent"]
                    >= before + 1
                )

            assert wait_until(_incremented, timeout=2.0), (
                f"chat pool notifications_sent did not increment after "
                f"_notify_all_pools() — before={before}, "
                f"after={manager._chat_worker_pool.get_stats()['notifications_sent']}"
            )

            default_release.set()
