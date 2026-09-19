"""Phase A / chat-lane-followups — Item 2 — production enqueue→wake e2e.

Whole-arc review finding #2 (merge-gate report
``RESULTS/2026-09-19-chat-source-worker-lane-merge-gate.md``): the
existing chat-source integration suite seeds rows directly via the
harness (``tests.integration.chat_source_harness.seed_chat_message``)
and wakes pools manually via ``manager._notify_all_pools()``. The
production enqueue→wake path that drives wake site #12
(``daemon/services/instance_messaging.py:~2100`` — every chat row
traverses this seam) was NOT exercised end-to-end by any test.

This file closes that gap: build the real ``InstanceManager`` with
BOTH pools live, then call the production
``manager.enqueue_message(instance_id, message, source='telegram:...')``
facade (the SAME call HTTP ``POST /messages`` issues internally; the
SAME call the wake site at ``instance_messaging.py:2100`` reaches via
``self._manager._notify_all_pools()``). Assert the chat pool's
``notifications_sent`` counter incremented — the chat pool DID receive
the fan-out wake produced by the production path.

HARD CONSTRAINT: the test must NOT call
``manager._notify_all_pools()`` or ``notify_work()`` manually. The
whole point is that the production enqueue path itself delivers the
wake; we only observe what it produces.

Companion assertion: the default pool ALSO received the wake (the
helper iterates ``self._pools`` which holds BOTH pools per
``daemon/manager.py:6805-6838`` — single ``_notify_all_pools`` call
fans out). Both-pools fan-out observable verifies the D5 fan-out
contract at the registry-mint ingress shape.

Difference from the existing
``test_chat_source_saturation_isolation.py::test_chat_notify_counter_increments_via_wake``:
that test calls ``manager._notify_all_pools()`` DIRECTLY (per its
own A2.2 disclosure: "the wake in this test is a MANUAL
``manager._notify_all_pools()`` call, NOT the production
enqueue→notify path"). This test calls
``manager.enqueue_message(...)`` and observes the wake that the
production path itself produces.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.integration.chat_source_harness import (
    build_chat_source_engine,
    build_live_pool_manager,
    chat_lane_flag_reset_fixture,
    wait_until,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Lane-flag isolation — shared state must not leak between tests
# ---------------------------------------------------------------------------


# Shared autouse lane-flag reset — the @pytest.fixture(autouse=True)
# decoration travels with the harness factory's returned object, so
# this single module-level assignment wires it for every test here.
chat_lane_flag_reset = chat_lane_flag_reset_fixture()


# ---------------------------------------------------------------------------
# Engine fixture — file-backed SQLite, NullPool, WAL + case_sensitive_like
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    eng = build_chat_source_engine(str(tmp_path / "chat_enqueue_wake.db"))
    yield eng
    eng.dispose()


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestProductionEnqueueWakeE2E:
    """Real manager + real pools + REAL production enqueue→wake path."""

    def test_chat_pool_receives_wake_via_production_enqueue(self, engine):
        """Driver: a single ``manager.enqueue_message(...)`` call with
        ``source='telegram:alice:1'`` produces a wake that the chat
        pool receives (notifications_sent counter advances).

        Production path exercised:
          HTTP /messages → ``manager.enqueue_message``
          → ``InstanceMessagingService.enqueue_message``
          → ``_prepare_enqueued_message`` (writes MessageQueue + Task
             rows in one transaction)
          → ``self._manager._notify_all_pools()`` at
             ``daemon/services/instance_messaging.py:2104``
          → ``_notify_all_pools`` fan-out
             (``daemon/manager.py:6805-6838``: iterates
             ``self._pools`` = [default, chat], calls
             ``pool.notify_work()`` on each)
          → ``chat_worker_pool._stats['notifications_sent'] += 1``
             (``daemon/services/worker_pool.py:1343``).

        The test ONLY observes the wake — it does NOT manually call
        ``_notify_all_pools`` or ``notify_work``. That is the entire
        contract this test pins.

        Both-pools observable (cheap, no extra assertion cost): the
        default pool ALSO receives the wake (the same
        ``_notify_all_pools`` call fans out to both pools per D5).
        Asserted so a future regression that breaks the fan-out (e.g.,
        one pool dropped from ``self._pools``) is caught here.
        """
        with build_live_pool_manager(engine) as manager:
            # No-op run_task: workers claim + immediately complete the
            # task. Keeps the DB writable for any follow-on inspect
            # but does not block — the wake is observed on the
            # ``_stats`` counter, not on the claim/complete lifecycle.
            def _noop_run_task(task, cancellation_token=None):
                return None

            manager._task_processor.run_task = _noop_run_task

            chat_pool = manager._chat_worker_pool
            default_pool = manager._worker_pool
            assert chat_pool is not None, (
                "chat pool not constructed — production wiring broken"
            )
            assert default_pool is not None

            # Give chat workers a tick to enter ``wait_for_work`` so
            # the notifications_sent counter is meaningful (mirrors
            # the existing ``test_chat_notify_counter_increments_via_wake``
            # boot-tick treatment).
            time.sleep(0.05)

            # Snapshot both pools' wake counters BEFORE the production
            # enqueue. Two-read DELTA avoids any pre-test-zeroing race
            # against concurrent worker activity.
            chat_before = chat_pool.get_stats()["notifications_sent"]
            default_before = default_pool.get_stats()["notifications_sent"]

            # --- The production call under test. ---
            # ``manager.enqueue_message`` is the SAME async facade that
            # HTTP ``POST /messages`` calls internally
            # (``daemon/manager.py:7251`` → ``_messaging_service.enqueue_message``
            # → ``_prepare_enqueued_message`` → ``_notify_all_pools()``
            # at ``daemon/services/instance_messaging.py:2104``).
            asyncio.run(
                manager.enqueue_message(
                    instance_id="inst-prod-wake",
                    message="hi",
                    source="telegram:alice:1",
                )
            )

            # --- Observable assertion: chat pool woke. ---
            def _chat_incremented() -> bool:
                return (
                    chat_pool.get_stats()["notifications_sent"]
                    >= chat_before + 1
                )

            assert wait_until(_chat_incremented, timeout=2.0), (
                f"chat pool notifications_sent did NOT increment after "
                f"production enqueue — production wake site #12 "
                f"(instance_messaging.py:2100-2120) did NOT deliver a "
                f"wake to the chat pool. before={chat_before}, "
                f"after={chat_pool.get_stats()['notifications_sent']}"
            )

            # --- Companion assertion: default pool ALSO woke. ---
            # D5 fan-out iterates self._pools which holds BOTH pools;
            # a regression that drops one pool from the fan-out
            # surfaces here.
            def _default_incremented() -> bool:
                return (
                    default_pool.get_stats()["notifications_sent"]
                    >= default_before + 1
                )

            assert wait_until(_default_incremented, timeout=2.0), (
                f"default pool notifications_sent did NOT increment "
                f"after production enqueue — _notify_all_pools fan-out "
                f"skipped the default pool. before={default_before}, "
                f"after={default_pool.get_stats()['notifications_sent']}"
            )

            # Both-pools DELTAs — read freshly so the assertion
            # message reflects the actual post-call values.
            chat_after = chat_pool.get_stats()["notifications_sent"]
            default_after = default_pool.get_stats()["notifications_sent"]
            chat_delta = chat_after - chat_before
            default_delta = default_after - default_before
            # At least one notify call to each pool (the production
            # path may fan out via one combined _notify_all_pools call
            # — each pool sees exactly one notify increment for our
            # one enqueue).
            assert chat_delta >= 1, (
                f"chat pool DELTA < 1 over the enqueue: delta={chat_delta} "
                f"(before={chat_before}, after={chat_after})"
            )
            assert default_delta >= 1, (
                f"default pool DELTA < 1 over the enqueue: delta={default_delta} "
                f"(before={default_before}, after={default_after})"
            )
