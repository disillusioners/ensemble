"""DEFECT A (dispatch-lane stranding fix, 2026-09-14) — cross-lane pins.

Incident 2026-09-14 (ensemble_prod forensics): a leader spawned two
children WITHOUT dispatch; ``ask_questions`` pause-cascade paused the
idle children; the answer resume flipped them PAUSED→RUNNING (bare DB
flip, ``route_outcome=internal_child_noop``); the leader then
dispatched real tasks via agent-tool ``send_message``. All three
dispatch lanes read ``status="running"``, chose the RAM-FIFO injection
lane ("[Injection] Appended pending message ... queue_depth=1"), and
stranded the dispatches in ``_pending_injections`` forever — no graph
existed to drain them. The tree wedged at WAITING_CHILDREN.

This file pins the live-graph guard across the lanes that can target a
running-but-graphless instance:

  * ``InstanceManager.has_live_graph_task`` — the verifier (unit).
  * HTTP ``POST /api/instances/{id}/messages`` — graphless running
    target falls through to the durable enqueue path (200 enqueued),
    never a 202 that nothing would honor.
  * ``job_inject`` tool — graphless running target takes the durable
    wake-enqueue shape (mirrors the B1 WC branch), never the RAM FIFO.

The agent-tool ``send_message`` lane is pinned in its canonical home:
``tests/unit/tools/test_instance_tools.py`` (TestRunningGraphlessDurableFallback
/ TestRunningLiveGraphInjectionPreserved).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# InstanceManager.has_live_graph_task — the verifier (unit)
# ---------------------------------------------------------------------------


class TestHasLiveGraphTaskUnit:
    """The verifier returns True iff a live (not-done) graph task exists."""

    def _bare_manager(self):
        from daemon.manager import InstanceManager

        manager = InstanceManager.__new__(InstanceManager)
        manager._graph_tasks = {}
        return manager

    @pytest.mark.asyncio
    async def test_no_entry_returns_false(self):
        manager = self._bare_manager()
        assert manager.has_live_graph_task("inst-1") is False

    @pytest.mark.asyncio
    async def test_live_task_returns_true(self):
        manager = self._bare_manager()

        async def _never_ends():
            await asyncio.Event().wait()

        task = asyncio.create_task(_never_ends())
        try:
            manager._graph_tasks["inst-1"] = task
            assert manager.has_live_graph_task("inst-1") is True
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_done_task_returns_false(self):
        """A finished graph (task done, row not yet settled) is NOT a
        live consumer — the guard routes durable, exactly the graph-end
        race window."""
        manager = self._bare_manager()

        async def _instant():
            return None

        task = asyncio.create_task(_instant())
        await task
        manager._graph_tasks["inst-1"] = task
        assert manager.has_live_graph_task("inst-1") is False

    @pytest.mark.asyncio
    async def test_cancelled_task_returns_false(self):
        manager = self._bare_manager()

        async def _never_ends():
            await asyncio.Event().wait()

        task = asyncio.create_task(_never_ends())
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        manager._graph_tasks["inst-1"] = task
        assert manager.has_live_graph_task("inst-1") is False

    @pytest.mark.asyncio
    async def test_never_mutates_registry(self):
        """Read-only contract — the verifier must not pop or write
        ``_graph_tasks`` (multiple lanes consult it concurrently)."""
        manager = self._bare_manager()

        async def _never_ends():
            await asyncio.Event().wait()

        task = asyncio.create_task(_never_ends())
        try:
            manager._graph_tasks["inst-1"] = task
            before = dict(manager._graph_tasks)
            manager.has_live_graph_task("inst-1")
            manager.has_live_graph_task("missing-id")
            assert manager._graph_tasks == before
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# ---------------------------------------------------------------------------
# HTTP POST /messages — graphless running target takes durable enqueue
# ---------------------------------------------------------------------------


def _make_http_manager(*, status: str, has_live_graph: bool):
    """Manager mock for the messages router — command-dispatch aware.

    Unlike the (pre-existing rotting) ``tests/test_injection_api.py``
    baseline, ``command_dispatcher.dispatch`` is an AsyncMock returning
    ``kind="passthrough"`` so the route reaches the status routing —
    the awaited-dispatch hazard documented in the testing conventions.
    """
    manager = MagicMock()
    manager.is_write_paused = False
    manager.config = MagicMock()
    manager.config.llm.model_vision = None
    manager.get_instance_info = MagicMock(
        return_value={"status": status, "instance_id": "inst-abc"}
    )

    async def _get_instance(iid):
        return MagicMock(instance_id=iid)

    manager.get_instance = _get_instance
    manager.get_injection_count = MagicMock(return_value=0)
    manager.set_injection = MagicMock(
        return_value={"content": "x", "timestamp": "2026-09-14T00:00:00Z"}
    )
    manager.clear_injection = MagicMock(return_value=None)
    manager.has_live_graph_task = MagicMock(return_value=has_live_graph)

    outcome = MagicMock(kind="passthrough", sanitized_text=None, available=None)
    manager.command_dispatcher = MagicMock()
    manager.command_dispatcher.dispatch = AsyncMock(return_value=outcome)

    enqueue_result = MagicMock()
    enqueue_result.message_id = "msg-enqueued"
    enqueue_result.job_id = "job-enqueued"
    enqueue_result.queued = False
    manager.enqueue_message_job = AsyncMock(return_value=enqueue_result)
    return manager


def _make_client(manager):
    from daemon.routers.messages import router

    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def _inject_state(request, call_next):
        request.app.state.manager = manager
        request.app.state.live_hub = None
        return await call_next(request)

    return TestClient(app)


class TestHttpRouteGraphlessDurable:
    """HTTP lane: ``running`` + no live graph → durable enqueue (200),
    NOT the RAM-FIFO 202 that nothing would ever honor."""

    def test_graphless_running_returns_200_enqueued(self):
        manager = _make_http_manager(status="running", has_live_graph=False)
        client = _make_client(manager)

        resp = client.post(
            "/instances/inst-abc/messages", json={"content": "hi"}
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        # MessageResponse shape (no "status: injected" key) — the body
        # carries the durable JobItem mirror ids instead.
        assert body["message_id"] == "msg-enqueued"
        assert body["auto_resumed"] is False
        assert body.get("status") != "injected"
        # Durable JobItem path materialized the dispatch.
        manager.enqueue_message_job.assert_awaited_once()
        manager.set_injection.assert_not_called()
        # The guard consulted the verifier with the target id.
        manager.has_live_graph_task.assert_called_once_with("inst-abc")

    def test_live_running_still_returns_202_injected(self):
        """Regression pin: a genuinely-running (live graph) target keeps
        the 202 injection contract."""
        manager = _make_http_manager(status="running", has_live_graph=True)
        client = _make_client(manager)

        resp = client.post(
            "/instances/inst-abc/messages", json={"content": "hi"}
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "injected"
        manager.set_injection.assert_called_once()
        manager.enqueue_message_job.assert_not_awaited()


# ---------------------------------------------------------------------------
# job_inject tool — graphless running target takes durable wake-enqueue
# ---------------------------------------------------------------------------


def _make_job_inject_fixtures(*, status: str, has_live_graph: bool):
    """Build (manager, job_inject tool) mirroring the
    ``tests/unit/tools/test_job_visibility_tools.py`` harness."""
    from daemon.tools.job_queue import create_job_tools

    job_service = AsyncMock()
    job_service.use_virtual_job_resolver = False
    queue_mgmt_service = AsyncMock()
    dead_letter_service = MagicMock()

    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager.get_messages = AsyncMock(return_value=[])
    manager.has_live_graph_task = MagicMock(return_value=has_live_graph)

    instance = MagicMock()
    instance.instance_id = "inject-run"
    instance.status = status
    instance.parent_id = None
    manager._instance_repository.get = MagicMock(return_value=instance)

    record = MagicMock()
    record.job_id = "inject-run"
    record.instance_id = "inject-run"
    record.project_id = "proj-1"
    record.agent_id = "developer"
    job_service.get_work = AsyncMock(return_value=record)

    enqueue_result = MagicMock()
    enqueue_result.message_id = "msg-wake-1"
    manager.enqueue_message = AsyncMock(return_value=enqueue_result)
    manager._task_repo = MagicMock()
    manager._task_repo.has_instance_busy = MagicMock(return_value=False)

    tools = create_job_tools(
        job_service,
        queue_mgmt_service,
        dead_letter_service,
        manager=manager,
    )
    return manager, tools[16]


class TestJobInjectGraphlessDurable:
    """job_inject lane: ``running`` + no live graph → durable wake
    enqueue (mirrors the B1 WC branch shape), never ``set_injection``."""

    @pytest.mark.asyncio
    async def test_graphless_running_enqueues_not_injects(self):
        manager, job_inject = _make_job_inject_fixtures(
            status="running", has_live_graph=False
        )

        result = await job_inject.ainvoke(
            {"job_id": "inject-run", "message": "wake up"}
        )

        assert result["status"] == "enqueued"
        assert result["message_id"] == "msg-wake-1"
        assert result["queued"] is True
        manager.enqueue_message.assert_awaited_once()
        kwargs = manager.enqueue_message.await_args.kwargs
        assert kwargs["instance_id"] == "inject-run"
        assert kwargs["source"].startswith("internal_agent:")
        manager.set_injection.assert_not_called()
        manager.has_live_graph_task.assert_called_once_with("inject-run")

    @pytest.mark.asyncio
    async def test_live_running_still_injects(self):
        """Regression pin: genuinely-running (live graph) keeps the RAM
        FIFO injection — byte-identical pre-fix behavior."""
        manager, job_inject = _make_job_inject_fixtures(
            status="running", has_live_graph=True
        )

        result = await job_inject.ainvoke(
            {"job_id": "inject-run", "message": "mid-turn input"}
        )

        assert result["status"] == "injected"
        manager.set_injection.assert_called_once()
        manager.enqueue_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_graphless_busy_target_fails_fast(self):
        """The graphless durable branch re-engages the WC-style busy
        pre-check — a target with an in-flight durable task gets a
        clean error instead of a duplicate queued turn."""
        manager, job_inject = _make_job_inject_fixtures(
            status="running", has_live_graph=False
        )
        manager._task_repo.has_instance_busy = MagicMock(return_value=True)

        result = await job_inject.ainvoke(
            {"job_id": "inject-run", "message": "wake up"}
        )

        assert "error" in result
        assert "in flight" in result["error"]
        manager.enqueue_message.assert_not_awaited()
        manager.set_injection.assert_not_called()
