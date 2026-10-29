"""P0 T9 — worker threads are NAMED at construction.

The thread-CPU family (F2) groups threads by their thread name; an
unnamed worker folds into the ``other`` residual, so the group's
numbers stop being honest from the first render. The name is
``worker-{lane}-{worker_id}`` — the CLAIM lane, so BOTH worker pools
(the default lane and the chat lane) are covered.
"""

from __future__ import annotations

import inspect

from daemon.services.worker_pool import Worker


class _FakeTaskProcessor:
    """The minimum surface ``Worker.__init__`` touches."""

    _task_repo = object()


def _make_worker(worker_id: str, lane: str) -> Worker:
    return Worker(
        worker_id=worker_id,
        task_processor=_FakeTaskProcessor(),
        worker_pool=object(),
        lane=lane,
    )


def test_the_default_lane_worker_is_named():
    worker = _make_worker("worker-0", "default")
    assert worker.name == "worker-default-worker-0"


def test_the_chat_lane_worker_is_named():
    worker = _make_worker("chat-worker-0", "chat")
    assert worker.name == "worker-chat-chat-worker-0"


def test_the_named_worker_is_still_a_daemon_thread():
    worker = _make_worker("worker-3", "default")
    assert worker.daemon is True


def test_the_name_is_set_at_construction_not_at_start():
    """The name must be honest from the FIRST render — it is assigned
    in ``__init__`` (the thread object), not when it starts."""
    source = inspect.getsource(Worker.__init__)
    assert 'name=f"worker-{lane}-{worker_id}"' in source


def test_both_pool_lanes_reach_the_worker_name():
    """The chat pool passes ``lane="chat"``; the default pool leaves the
    default lane — both reach the worker's name."""
    from daemon.services import pool_orchestrator

    source = inspect.getsource(pool_orchestrator)
    assert 'lane="chat"' in source
    assert 'worker_id_prefix="chat-worker-"' in source
