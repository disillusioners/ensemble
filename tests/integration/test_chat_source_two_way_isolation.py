"""Phase 3 / chat-source-worker-lane — Task #5 — strict two-way isolation.

Pins SC#14 with full per-row lineage: enqueue 10 chat rows + 10 default
rows on the SAME instance pool (different instances, one row each),
run with default pool=5 and chat pool=2, and assert EVERY row's
``task.worker_id`` prefix matches its ``source`` prefix class
(chat rows → ``chat-worker-``, default rows → ``worker-``).

Interleave order (note m): pinned deterministic
``chat, default, chat, default, …`` — both pools see their own rows
AND the other pool's rows arriving in a known verifiable order.
Random order is non-deterministic and a misrouted row could pass by
chance; the interleaved pin ensures both pools demonstrably skip the
other pool's rows in the known order.

Testable contract: 20 rows × strict prefix match = 20 distinct
assertions. Misrouting probability per row (under the lane predicate
spec) is 0; per-row assertion is deterministic.

B1 cross-reference (phase3-plan.md Exit #0, approver S4 — no
duplicate home): the fail-open test (chat pool ABSENT → default
claims chat rows) lives in ``tests/integration/test_chat_source_pool_wiring.py::
test_b1_conditional_fail_open_chat_pool_absent_then_present`` (Phase 2
home). The flag-True strict two-way case is pinned here end-to-end.

ADJUDICATED (2026-09-19, Phase 3 round-2): the former KNOWN BUG
(default-pool workers occasionally mis-claim chat-prefixed rows
under contention) is RESOLVED as a HARNESS boot-window race —
NOT a production predicate defect. See
``tests/integration/test_chat_source_lane_saturation.py`` module
docstring for the instrumented root-cause narrative; the harness
now drains the default pool's gateless boot claims before
seeding (``chat_source_harness.wait_default_pool_boot_claims_drained``).
The xfail mark is removed — the strict assertions below pass
deterministically.
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
    WORKER_POOL_SIZE,
    build_chat_source_engine,
    build_live_pool_manager,
    fetch_task_by_work_id,
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
    eng = build_chat_source_engine(str(tmp_path / "chat_two_way.db"))
    yield eng
    eng.dispose()


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestStrictTwoWayIsolation:
    """SC#14 — NO row misrouted, regardless of pool state.

    Pinned deterministic interleaved enqueue order:
    ``chat, default, chat, default, …`` — 10 chat rows + 10 default
    rows alternating. Both pools see their own rows AND see the
    other pool's rows arriving in the verifiable sequence.
    """

    def test_no_row_misrouted_across_20_rows(self, engine):
        """Strict two-way isolation: 20 distinct per-row assertions
        pass — chat rows → chat workers, default rows → default
        workers. The interleaved enqueue order is pinned to
        ``(chat, default, chat, default, ...)``."""
        def _complete_run_task(task, cancellation_token=None):
            time.sleep(0.02)
            from daemon.services.timestamps import now_utc_naive

            with Session(engine) as s:
                t = s.get(Task, task.id)
                if t is not None:
                    t.status = TaskStatus.COMPLETED.value
                    t.completed_at = now_utc_naive()
                    s.commit()

        with build_live_pool_manager(engine) as manager:
            manager._task_processor.run_task = _complete_run_task

            # Pinned interleaved enqueue order (note m):
            # chat, default, chat, default, ... — 10 of each.
            chat_work_ids: list[str] = []
            default_work_ids: list[str] = []
            for i in range(10):
                # Chat row
                _, chat_wid = seed_chat_message(
                    engine,
                    instance_id=f"inst-two-way-chat-{i}",
                    source="telegram:alice:1",
                )
                chat_work_ids.append(chat_wid)
                # Default row
                _, default_wid = seed_chat_message(
                    engine,
                    instance_id=f"inst-two-way-default-{i}",
                    source=f"agent:ari-{i}",
                )
                default_work_ids.append(default_wid)

            manager._notify_all_pools()

            # Wait for ALL 20 to be claimed AND completed.
            def _all_done():
                return all(
                    (fetch_task_by_work_id(engine, wid) is not None
                     and fetch_task_by_work_id(engine, wid).status
                     == TaskStatus.COMPLETED.value)
                    for wid in chat_work_ids + default_work_ids
                )

            assert wait_until(_all_done, timeout=15.0), (
                f"not all 20 rows completed within 15s; "
                f"pending chat={sum(1 for w in chat_work_ids if fetch_task_by_work_id(engine, w) is None or fetch_task_by_work_id(engine, w).status != TaskStatus.COMPLETED.value)} "
                f"pending default={sum(1 for w in default_work_ids if fetch_task_by_work_id(engine, w) is None or fetch_task_by_work_id(engine, w).status != TaskStatus.COMPLETED.value)}"
            )

            # Assertion 1 of 20 (× 10 chat rows): chat row → chat worker.
            chat_prefix_issues = []
            for wid in chat_work_ids:
                t = fetch_task_by_work_id(engine, wid)
                assert t.worker_id is not None
                if not t.worker_id.startswith("chat-worker-"):
                    chat_prefix_issues.append((wid, t.worker_id))
            assert chat_prefix_issues == [], (
                f"{len(chat_prefix_issues)}/10 chat rows misrouted to "
                f"non-chat workers: {chat_prefix_issues}"
            )

            # Assertion 2 of 20 (× 10 default rows): default row →
            # default worker.
            default_prefix_issues = []
            for wid in default_work_ids:
                t = fetch_task_by_work_id(engine, wid)
                assert t.worker_id is not None
                if not t.worker_id.startswith("worker-"):
                    default_prefix_issues.append((wid, t.worker_id))
            assert default_prefix_issues == [], (
                f"{len(default_prefix_issues)}/10 default rows "
                f"misrouted to non-default workers: "
                f"{default_prefix_issues}"
            )

    def test_pool_sizes_match_production_constants(self, engine):
        """Sanity: the live pool sizes match the production
        constants — ``default pool = WORKER_POOL_SIZE=5``,
        ``chat pool = CHAT_WORKER_POOL_SIZE=2``. This is the
        configuration the SC#14 strict two-way assertion runs
        against (interleave order pinned; pool sizes production)."""
        with build_live_pool_manager(engine) as manager:
            assert len(manager._worker_pool._workers) == WORKER_POOL_SIZE
            assert (
                len(manager._chat_worker_pool._workers)
                == CHAT_WORKER_POOL_SIZE
            )
