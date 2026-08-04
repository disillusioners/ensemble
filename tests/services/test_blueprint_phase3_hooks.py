"""Hook tests for the C8 / Phase 3 pending-queue wiring.

Covers:

* ``experience()`` drops a row in the Blueprint pending queue
  (``source_type='experience'``).
* ``project_history_add(entry_type='feature')`` drops a row
  (``source_type='history'``).
* ``project_history_add(entry_type='milestone')`` drops a row.
* ``project_history_add(entry_type='bugfix')`` does NOT drop a row.
* Pending repo failures are logged at WARNING, never propagated.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.tools.project_history import create_project_history_tools


# ── Test doubles ──────────────────────────────────────────────────────


def _make_pending_repo() -> MagicMock:
    """Return a MagicMock pending repo whose ``enqueue`` records calls."""
    repo = MagicMock()
    enqueued: list[dict] = []
    repo.enqueue = MagicMock(side_effect=lambda **kw: enqueued.append(kw) or MagicMock())
    repo._enqueued = enqueued  # for tests to inspect.
    return repo


def _make_manager_with_repo(pending_repo: MagicMock) -> MagicMock:
    """Build a manager stub exposing ``_blueprint_pending_repo``."""
    mgr = MagicMock()
    mgr._blueprint_pending_repo = pending_repo
    # The project store is what the history tools call for validation.
    project = MagicMock()
    project.project_id = "proj-1"
    project.name = "Test"
    mgr.project_store = MagicMock()
    mgr.project_store.get = MagicMock(return_value=project)
    mgr.project_store.add_history_entry = MagicMock(
        return_value={
            "id": "entry-1",
            "project_id": "proj-1",
            "entry_type": "feature",
            "summary": "s",
            "details": None,
        }
    )
    return mgr


# ── experience() ───────────────────────────────────────────────────────


def test_experience_enqueues_pending_row(monkeypatch):
    """experience() drops a row in the pending queue with source_type='experience'."""
    monkeypatch.setenv("LIGHTRAG_HOST", "http://localhost:8724")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "test-key")
    monkeypatch.setenv("LIGHTRAG_WORKSPACE", "ws")

    pending_repo = _make_pending_repo()
    mgr = MagicMock()
    mgr._blueprint_pending_repo = pending_repo

    # Mock the instance metadata to return a project_id.
    inst = MagicMock()
    inst.project_id = "proj-1"
    inst.instance_metadata = {"project_id": "proj-1"}
    mgr._instance_repository = MagicMock()
    mgr._instance_repository.get = MagicMock(return_value=inst)
    mgr._instance_repository.get_tree_root_id = MagicMock(return_value="root")

    # Mock the job queue service to avoid enqueueing real jobs.
    mgr._job_queue_service = MagicMock()
    q = MagicMock(); q.queue_id = "q"
    mgr._job_queue_service._queue_repo.get_by_name = MagicMock(return_value=q)

    # Suppress the real kb-writer enqueue.
    from daemon.tools import knowledge_tools
    async def _noop(*_a, **_kw): return None
    monkeypatch.setattr(knowledge_tools, "_enqueue_experience_job", _noop)
    # _save_experience_result is wrapped in asyncio.to_thread(...), so
    # the patched function is called from a worker thread and must be
    # a regular sync function, not a coroutine.
    def _noop_sync(*_a, **_kw): return None
    monkeypatch.setattr(knowledge_tools, "_save_experience_result", _noop_sync)

    from daemon.tools.knowledge_tools import create_knowledge_tools
    tools = create_knowledge_tools(mgr, "parent-iid", "agent")
    exp_tool = next(t for t in tools if t.name == "experience")

    result = asyncio.run(exp_tool.ainvoke({"text": "Some knowledge."}))
    assert "Knowledge recording started" in result

    # The pending queue got a row.
    assert pending_repo.enqueue.call_count == 1
    call = pending_repo.enqueue.call_args.kwargs
    assert call["project_id"] == "proj-1"
    assert call["source_type"] == "experience"
    assert call["source_payload"] == {"text": "Some knowledge."}


def test_experience_no_pending_repo_does_not_raise(monkeypatch):
    """If the manager has no _blueprint_pending_repo, experience() is a no-op for the hook."""
    monkeypatch.setenv("LIGHTRAG_HOST", "http://localhost:8724")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "test-key")
    monkeypatch.setenv("LIGHTRAG_WORKSPACE", "ws")

    mgr = MagicMock(spec=["_instance_repository", "_job_queue_service"])
    inst = MagicMock()
    inst.project_id = "proj-1"
    inst.instance_metadata = {"project_id": "proj-1"}
    mgr._instance_repository = MagicMock()
    mgr._instance_repository.get = MagicMock(return_value=inst)
    mgr._instance_repository.get_tree_root_id = MagicMock(return_value="root")
    mgr._job_queue_service = MagicMock()
    q = MagicMock(); q.queue_id = "q"
    mgr._job_queue_service._queue_repo.get_by_name = MagicMock(return_value=q)

    # No _blueprint_pending_repo attribute on the manager.
    from daemon.tools import knowledge_tools
    async def _noop(*_a, **_kw): return None
    monkeypatch.setattr(knowledge_tools, "_enqueue_experience_job", _noop)
    def _noop_sync(*_a, **_kw): return None
    monkeypatch.setattr(knowledge_tools, "_save_experience_result", _noop_sync)

    from daemon.tools.knowledge_tools import create_knowledge_tools
    tools = create_knowledge_tools(mgr, "iid", "agent")
    exp_tool = next(t for t in tools if t.name == "experience")

    # Should not raise.
    result = asyncio.run(exp_tool.ainvoke({"text": "x"}))
    assert "Knowledge recording started" in result


def test_experience_pending_repo_failure_logged_at_warning(monkeypatch, caplog):
    """If pending_repo.enqueue raises, experience() returns normally and a WARNING is logged."""
    monkeypatch.setenv("LIGHTRAG_HOST", "http://localhost:8724")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "test-key")
    monkeypatch.setenv("LIGHTRAG_WORKSPACE", "ws")

    pending_repo = MagicMock()
    pending_repo.enqueue = MagicMock(side_effect=RuntimeError("queue down"))
    mgr = MagicMock()
    mgr._blueprint_pending_repo = pending_repo

    inst = MagicMock()
    inst.project_id = "proj-1"
    inst.instance_metadata = {"project_id": "proj-1"}
    mgr._instance_repository = MagicMock()
    mgr._instance_repository.get = MagicMock(return_value=inst)
    mgr._instance_repository.get_tree_root_id = MagicMock(return_value="root")
    mgr._job_queue_service = MagicMock()
    q = MagicMock(); q.queue_id = "q"
    mgr._job_queue_service._queue_repo.get_by_name = MagicMock(return_value=q)

    from daemon.tools import knowledge_tools
    async def _noop(*_a, **_kw): return None
    monkeypatch.setattr(knowledge_tools, "_enqueue_experience_job", _noop)
    def _noop_sync(*_a, **_kw): return None
    monkeypatch.setattr(knowledge_tools, "_save_experience_result", _noop_sync)

    from daemon.tools.knowledge_tools import create_knowledge_tools
    tools = create_knowledge_tools(mgr, "iid", "agent")
    exp_tool = next(t for t in tools if t.name == "experience")

    with caplog.at_level(logging.WARNING, logger="daemon.tools.knowledge_tools"):
        result = asyncio.run(exp_tool.ainvoke({"text": "x"}))
    assert "Knowledge recording started" in result
    assert any(
        "Blueprint pending-queue INSERT failed" in rec.message
        for rec in caplog.records
    ), f"Expected WARNING log, got: {[r.message for r in caplog.records]}"


def test_experience_skips_default_project(monkeypatch):
    """The system default project never feeds the pending queue via experience().

    The default project is a virtual bookkeeping project — no
    blueprints are built for it, so even experience() calls on it
    must not enqueue pending rows.
    """
    from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME

    monkeypatch.setenv("LIGHTRAG_HOST", "http://localhost:8724")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "test-key")
    monkeypatch.setenv("LIGHTRAG_WORKSPACE", "ws")

    pending_repo = _make_pending_repo()
    mgr = MagicMock()
    mgr._blueprint_pending_repo = pending_repo

    # The instance metadata points to the default project.
    default_project_id = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"
    inst = MagicMock()
    inst.project_id = default_project_id
    inst.instance_metadata = {"project_id": default_project_id}
    mgr._instance_repository = MagicMock()
    mgr._instance_repository.get = MagicMock(return_value=inst)
    mgr._instance_repository.get_tree_root_id = MagicMock(return_value="root")

    # _project_repository.get returns the default project.
    default_project = MagicMock()
    default_project.project_id = default_project_id
    default_project.name = SYSTEM_DEFAULT_PROJECT_NAME
    mgr._project_repository = MagicMock()
    mgr._project_repository.get = MagicMock(return_value=default_project)

    mgr._job_queue_service = MagicMock()
    q = MagicMock(); q.queue_id = "q"
    mgr._job_queue_service._queue_repo.get_by_name = MagicMock(return_value=q)

    # Suppress the real kb-writer enqueue + shared-context save.
    from daemon.tools import knowledge_tools
    async def _noop(*_a, **_kw): return None
    monkeypatch.setattr(knowledge_tools, "_enqueue_experience_job", _noop)
    def _noop_sync(*_a, **_kw): return None
    monkeypatch.setattr(knowledge_tools, "_save_experience_result", _noop_sync)

    from daemon.tools.knowledge_tools import create_knowledge_tools
    tools = create_knowledge_tools(mgr, "parent-iid", "agent")
    exp_tool = next(t for t in tools if t.name == "experience")

    result = asyncio.run(exp_tool.ainvoke({
        "text": "Some knowledge about the default project.",
        "project_id": default_project_id,
    }))
    assert "Knowledge recording started" in result

    # The pending queue got NO row — the default project is excluded.
    pending_repo.enqueue.assert_not_called()


# ── project_history_add() ─────────────────────────────────────────────


def _make_history_tool(manager: Any):
    tools = create_project_history_tools(
        manager.project_store,
        current_instance_id="iid",
        agent_id="agent_id",
        manager=manager,
    )
    return next(t for t in tools if t.name == "project_history_add")


def test_history_add_enqueues_pending_for_feature():
    """feature → enqueue(source_type='history')."""
    pending_repo = _make_pending_repo()
    mgr = _make_manager_with_repo(pending_repo)
    tool = _make_history_tool(mgr)
    out = tool.invoke({
        "project_id": "proj-1",
        "entry_type": "feature",
        "summary": "Added foo",
        "details": "details here",
    })
    # The history entry was written…
    assert mgr.project_store.add_history_entry.called
    # …and a pending row was enqueued.
    assert pending_repo.enqueue.call_count == 1
    call = pending_repo.enqueue.call_args.kwargs
    assert call["project_id"] == "proj-1"
    assert call["source_type"] == "history"
    assert call["source_payload"]["entry_type"] == "feature"
    assert call["source_payload"]["summary"] == "Added foo"


def test_history_add_enqueues_pending_for_milestone():
    """milestone → enqueue(source_type='history')."""
    pending_repo = _make_pending_repo()
    mgr = _make_manager_with_repo(pending_repo)
    tool = _make_history_tool(mgr)
    tool.invoke({
        "project_id": "proj-1",
        "entry_type": "milestone",
        "summary": "v1 shipped",
    })
    assert pending_repo.enqueue.call_count == 1
    assert pending_repo.enqueue.call_args.kwargs["source_payload"]["entry_type"] == "milestone"


@pytest.mark.parametrize("entry_type", ["bugfix", "commit", "note", "deployment", "config_change", "phase", "other"])
def test_history_add_skips_for_non_structural(entry_type):
    """Only feature/milestone enqueue. Other types do NOT enqueue."""
    pending_repo = _make_pending_repo()
    mgr = _make_manager_with_repo(pending_repo)
    tool = _make_history_tool(mgr)
    tool.invoke({
        "project_id": "proj-1",
        "entry_type": entry_type,
        "summary": "x",
    })
    pending_repo.enqueue.assert_not_called()


def test_history_add_no_manager_no_enqueue():
    """manager=None keeps the legacy behaviour: no pending-queue row."""
    mgr = MagicMock()  # not used; passed as None
    tools = create_project_history_tools(
        mgr.project_store if False else MagicMock(),  # the store, not the manager
        current_instance_id="iid",
        agent_id="agent_id",
        manager=None,
    )
    tool = next(t for t in tools if t.name == "project_history_add")
    # Stub the store: project exists, add_history_entry returns a row.
    project = MagicMock(); project.project_id = "proj-1"
    store = tools[0].func.__closure__  # not used; we patch the bound store directly
    # The cleanest path: re-create the tool with a real store mock.
    store2 = MagicMock()
    store2.get = MagicMock(return_value=project)
    store2.add_history_entry = MagicMock(return_value={"id": "x", "entry_type": "feature", "summary": "s"})
    tools2 = create_project_history_tools(
        store2, current_instance_id="iid", agent_id="agent_id", manager=None,
    )
    tool2 = next(t for t in tools2 if t.name == "project_history_add")
    tool2.invoke({"project_id": "proj-1", "entry_type": "feature", "summary": "x"})
    # The history entry was written; no pending repo attribute to call.
    assert store2.add_history_entry.called


def test_history_add_pending_repo_failure_logged_at_warning(caplog):
    """If pending_repo.enqueue raises, history_add still returns successfully and a WARNING is logged."""
    pending_repo = MagicMock()
    pending_repo.enqueue = MagicMock(side_effect=RuntimeError("queue down"))
    mgr = _make_manager_with_repo(pending_repo)
    tool = _make_history_tool(mgr)

    with caplog.at_level(logging.WARNING, logger="daemon.tools.project_history"):
        out = tool.invoke({
            "project_id": "proj-1",
            "entry_type": "feature",
            "summary": "x",
        })
    # The history write succeeded.
    assert mgr.project_store.add_history_entry.called
    # A WARNING was logged.
    assert any(
        "Blueprint pending-queue INSERT failed" in rec.message
        for rec in caplog.records
    )


def test_history_add_skips_default_project():
    """The system default project never feeds the pending queue.

    The default project is a virtual bookkeeping project — no
    blueprints are built for it, so feature/milestone history
    entries on it must not enqueue pending rows.
    """
    from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME

    pending_repo = _make_pending_repo()

    # Build a manager whose project store returns the default project.
    mgr = MagicMock()
    mgr._blueprint_pending_repo = pending_repo
    default_project = MagicMock()
    default_project.project_id = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"
    default_project.name = SYSTEM_DEFAULT_PROJECT_NAME
    mgr.project_store = MagicMock()
    mgr.project_store.get = MagicMock(return_value=default_project)
    mgr.project_store.add_history_entry = MagicMock(
        return_value={
            "id": "entry-1",
            "project_id": default_project.project_id,
            "entry_type": "feature",
            "summary": "s",
            "details": None,
        }
    )

    tool = _make_history_tool(mgr)
    out = tool.invoke({
        "project_id": default_project.project_id,
        "entry_type": "feature",
        "summary": "A feature on the default project",
    })
    # The history entry was still written…
    assert mgr.project_store.add_history_entry.called
    # …but no pending-queue row was enqueued.
    pending_repo.enqueue.assert_not_called()
