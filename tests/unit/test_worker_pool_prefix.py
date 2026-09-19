"""Unit tests for the WorkerPool worker_id_prefix / lane constructor seam.

``WorkerPool.__init__`` gains BOTH ``worker_id_prefix: str = "worker-"``
and ``lane: str = "default"`` (chat-source-worker-lane, Task #8 /
reviewer F3 — one seam, one PR):

* the prefix stamps constructed worker ids — worker ``i`` is named
  ``f"{worker_id_prefix}{i}"`` (default preserves today's
  ``worker-0`` shape; the chat pool passes ``"chat-worker-"`` so
  ``task.worker_id`` lineage distinguishes pools, D10.5);
* the lane flows Worker → shared ``TaskProcessor.claim_task`` →
  ``TaskRepository.claim_pending_task`` as a CLAIM ARGUMENT per claim —
  the TaskProcessor stays the SHARED SINGLETON (never processor state).

Defaults preserve every existing caller byte-for-byte.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from daemon.services.task_processor import TaskProcessor
from daemon.services.worker_pool import WorkerPool


def _make_recording_processor(expected_claims: int, done: threading.Event):
    """TaskProcessor stand-in recording (worker_id, lane) per claim.

    Returning None sends every worker into ``wait_for_work`` (condition
    wait) — no task is ever processed; ``pool.stop()`` wakes and joins
    them.
    """
    processor = MagicMock(name="TaskProcessor")
    claims: list[tuple[str, str]] = []
    lock = threading.Lock()

    def _claim(worker_id: str, lane: str = "default"):
        with lock:
            claims.append((worker_id, lane))
            reached = len(claims)
        if reached >= expected_claims:
            done.set()
        return None

    processor.claim_task.side_effect = _claim
    return processor, claims


def _drain_pool(pool: WorkerPool) -> None:
    pool.stop(timeout=10.0)


class TestWorkerPoolPrefixKwarg:
    """worker_id_prefix stamps the constructed worker ids (D10.5)."""

    def test_chat_pool_worker_ids_use_prefix(self):
        """``WorkerPool(worker_id_prefix="chat-worker-", num_workers=2)``
        produces ``chat-worker-0`` / ``chat-worker-1``."""
        done = threading.Event()
        processor, claims = _make_recording_processor(2, done)
        pool = WorkerPool(
            task_processor=processor,
            num_workers=2,
            worker_id_prefix="chat-worker-",
            lane="chat",
        )

        pool.start()
        try:
            assert done.wait(timeout=10.0), (
                f"workers never claimed; calls={claims}"
            )
        finally:
            _drain_pool(pool)

        worker_ids = {w for w, _ in claims}
        assert worker_ids == {"chat-worker-0", "chat-worker-1"}
        assert [w for w in pool._workers] and all(
            w.worker_id.startswith("chat-worker-") for w in pool._workers
        )

    def test_default_ctor_preserves_today_shape(self):
        """No kwargs → ``worker-0`` ids and lane="default" — the
        pre-lane behavior byte-for-byte."""
        done = threading.Event()
        processor, claims = _make_recording_processor(1, done)
        pool = WorkerPool(task_processor=processor, num_workers=1)

        pool.start()
        try:
            assert done.wait(timeout=10.0), (
                f"worker never claimed; calls={claims}"
            )
        finally:
            _drain_pool(pool)

        assert claims[0][0] == "worker-0"
        assert pool._workers[0].worker_id == "worker-0"


class TestWorkerPoolLaneKwarg:
    """lane flows Worker → claim_task as a per-claim ARGUMENT (F3)."""

    def test_chat_pool_workers_claim_with_lane_chat(self):
        done = threading.Event()
        processor, claims = _make_recording_processor(2, done)
        pool = WorkerPool(
            task_processor=processor,
            num_workers=2,
            worker_id_prefix="chat-worker-",
            lane="chat",
        )

        pool.start()
        try:
            assert done.wait(timeout=10.0), (
                f"workers never claimed; calls={claims}"
            )
        finally:
            _drain_pool(pool)

        assert claims, "no claims recorded"
        assert all(lane == "chat" for _, lane in claims), claims

    def test_default_pool_claims_with_lane_default(self):
        done = threading.Event()
        processor, claims = _make_recording_processor(1, done)
        pool = WorkerPool(task_processor=processor, num_workers=1)

        pool.start()
        try:
            assert done.wait(timeout=10.0)
        finally:
            _drain_pool(pool)

        assert claims == [("worker-0", "default")]


class TestTaskProcessorLaneForwarding:
    """TaskProcessor.claim_task forwards lane to the repository — the
    shared-singleton seam (lane is a claim argument, not state)."""

    def test_claim_task_forwards_lane_to_repository(self):
        processor = object.__new__(TaskProcessor)  # seam under test: claim_task only
        repo = MagicMock(name="TaskRepository")
        repo.claim_pending_task.return_value = None
        processor._task_repo = repo

        processor.claim_task("chat-worker-0", lane="chat")

        repo.claim_pending_task.assert_called_once_with(
            "chat-worker-0", lane="chat"
        )

    def test_claim_task_default_lane_matches_pre_lane_calls(self):
        """A pre-lane caller shape (no lane arg) forwards lane="default"
        — the repository default keeps the behavior identical."""
        processor = object.__new__(TaskProcessor)
        repo = MagicMock(name="TaskRepository")
        repo.claim_pending_task.return_value = None
        processor._task_repo = repo

        processor.claim_task("worker-0")

        repo.claim_pending_task.assert_called_once_with(
            "worker-0", lane="default"
        )

    def test_processor_holds_no_lane_state(self):
        """F3 strike enforcement: the lane lives on the POOL/Worker and
        travels per-claim — a TaskProcessor instance carries no lane
        attribute that could fork per-pool processors."""
        processor = object.__new__(TaskProcessor)
        assert not any(
            name.startswith(("_lane", "lane")) for name in vars(processor)
        )
