"""HTTP API tests for the todo list + comment endpoints.

Endpoints under test (in ``daemon/routers/instances.py``):

  * ``GET  /api/instances/{instance_id}/todos``
        Returns the instance's todo list as a JSON array. Each item
        has ``index``, ``text``, ``status``, ``comment``. Empty list
        when the instance has no todos yet.
  * ``POST /api/instances/{instance_id}/todos/{index}/comment``
        Sets a comment on a todo item. Body: ``{"comment": "..."}``.
        Emits a ``todo_update`` SSE event on success.

These tests use FastAPI ``TestClient`` with a real ``TodoManager``
attached to a mock manager at ``manager._todo_manager``, matching the
shape ``daemon.tools.todo_tools`` consumes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_manager(*, has_instance: bool = True) -> MagicMock:
    """Build a mock InstanceManager with a real ``TodoManager`` attached.

    The mock ``get_instance`` returns a sentinel instance when
    ``has_instance=True`` (so 404 paths aren't triggered) and raises
    ``KeyError`` otherwise. ``_todo_manager`` is a real ``TodoManager``
    so the endpoint logic actually mutates state and we can assert on it.
    """
    from daemon.services.todo_manager import TodoManager

    manager = MagicMock()
    manager._todo_manager = TodoManager()

    async def _get_instance_present(instance_id: str):
        return MagicMock(instance_id=instance_id)

    async def _get_instance_missing(instance_id: str):
        raise KeyError(instance_id)

    manager.get_instance = (
        _get_instance_present if has_instance else _get_instance_missing
    )

    return manager


@pytest.fixture
def client_with_manager():
    """Wire the instances router into a TestClient with injectable manager.

    Returns ``(client, state)`` where ``state["manager"]`` lets each test
    swap in a custom manager mock (mirrors the pattern in
    ``tests/unit/routers/test_message_status_endpoint.py``).
    """
    from daemon.routers.instances import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    state: dict = {"manager": _make_manager()}

    @app.middleware("http")
    async def _inject_manager(request, call_next):
        request.app.state.manager = state["manager"]
        # ``app.state.live_hub`` may not exist in unit tests; the
        # router uses ``getattr(request.app.state, "live_hub", None)``
        # so a missing attribute is a no-op.
        return await call_next(request)

    return TestClient(app), state


@pytest.fixture
def client_with_live_hub():
    """Like ``client_with_manager`` but also wires a mock live_hub on state.

    Used for verifying the SSE re-emit path in the comment endpoint.
    The hub is a MagicMock with an async ``stream_todo_update`` method.
    """
    from daemon.routers.instances import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    state: dict = {"manager": _make_manager()}

    hub = MagicMock()
    hub.stream_todo_update = AsyncMock()

    @app.middleware("http")
    async def _inject(request, call_next):
        request.app.state.manager = state["manager"]
        request.app.state.live_hub = hub
        return await call_next(request)

    return TestClient(app), state, hub


# =============================================================================
# GET /api/instances/{instance_id}/todos
# =============================================================================


class TestGetInstanceTodos:
    """``GET /api/instances/{instance_id}/todos`` — list current todos."""

    def test_returns_empty_list_when_no_todos(self, client_with_manager):
        """Instance with no todo list returns ``[]`` (not 404)."""
        client, state = client_with_manager
        state["manager"] = _make_manager()

        resp = client.get("/api/instances/inst-1/todos")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_full_list_with_all_fields(self, client_with_manager):
        """Each item includes ``index``, ``text``, ``status``, ``comment``."""
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A", "B", "C"])
        state["manager"] = mgr

        resp = client.get("/api/instances/inst-1/todos")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 3
        for item in body:
            assert set(item.keys()) == {"index", "text", "status", "comment"}
        assert body[0]["text"] == "A"
        assert body[1]["text"] == "B"
        assert body[2]["text"] == "C"
        # Default empty comments surface in the payload.
        assert all(item["comment"] == "" for item in body)

    def test_reflects_status_changes(self, client_with_manager):
        """GET reflects the current state after a status update."""
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A", "B"])
        mgr._todo_manager.update("inst-1", 0, "done")
        state["manager"] = mgr

        resp = client.get("/api/instances/inst-1/todos")

        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["status"] == "done"
        assert body[1]["status"] == "pending"

    def test_reflects_comment_changes(self, client_with_manager):
        """GET surfaces the comment after ``set_comment``."""
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A"])
        mgr._todo_manager.set_comment("inst-1", 0, "hello")
        state["manager"] = mgr

        resp = client.get("/api/instances/inst-1/todos")

        assert resp.status_code == 200
        assert resp.json()[0]["comment"] == "hello"

    def test_404_when_instance_missing(self, client_with_manager):
        """Unknown instance returns 404 (delegated to ``get_instance``)."""
        client, state = client_with_manager
        state["manager"] = _make_manager(has_instance=False)

        resp = client.get("/api/instances/ghost/todos")

        assert resp.status_code == 404
        assert "Instance not found" in resp.json()["detail"]["message"]


# =============================================================================
# POST /api/instances/{instance_id}/todos/{index}/comment
# =============================================================================


class TestSetTodoComment:
    """``POST /api/instances/{instance_id}/todos/{index}/comment`` — annotate."""

    def test_sets_comment_and_returns_updated_item(self, client_with_manager):
        """A valid request returns the updated item with the new comment."""
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A", "B"])
        state["manager"] = mgr

        resp = client.post(
            "/api/instances/inst-1/todos/1/comment",
            json={"comment": "Please rephrase"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["index"] == 1
        assert body["text"] == "B"
        assert body["comment"] == "Please rephrase"
        # State persisted in the manager.
        items = mgr._todo_manager.get_all("inst-1")
        assert items[1]["comment"] == "Please rephrase"

    def test_empty_comment_clears_existing(self, client_with_manager):
        """Empty ``comment`` overwrites (clears) any prior annotation."""
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A"])
        mgr._todo_manager.set_comment("inst-1", 0, "first")
        state["manager"] = mgr

        resp = client.post(
            "/api/instances/inst-1/todos/0/comment",
            json={"comment": ""},
        )

        assert resp.status_code == 200
        assert resp.json()["comment"] == ""
        assert mgr._todo_manager.get_all("inst-1")[0]["comment"] == ""

    def test_missing_comment_field_defaults_to_empty(self, client_with_manager):
        """Omitting the ``comment`` field is treated as an empty string.

        The Pydantic model defaults ``comment`` to ``""``; a request body
        of ``{}`` must therefore succeed and clear the comment.
        """
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A"])
        mgr._todo_manager.set_comment("inst-1", 0, "preexisting")
        state["manager"] = mgr

        resp = client.post(
            "/api/instances/inst-1/todos/0/comment",
            json={},
        )

        assert resp.status_code == 200
        assert resp.json()["comment"] == ""

    def test_index_too_large_returns_400(self, client_with_manager):
        """Out-of-bounds index returns 400 with ``INVALID_REQUEST``."""
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A"])  # len=1
        state["manager"] = mgr

        resp = client.post(
            "/api/instances/inst-1/todos/5/comment",
            json={"comment": "x"},
        )

        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["code"] == "INVALID_REQUEST"
        # State untouched
        assert mgr._todo_manager.get_all("inst-1")[0]["comment"] == ""

    def test_negative_index_returns_400(self, client_with_manager):
        """Negative index returns 400 (ValueError from ``set_comment``)."""
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A", "B"])
        state["manager"] = mgr

        resp = client.post(
            "/api/instances/inst-1/todos/-1/comment",
            json={"comment": "x"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "INVALID_REQUEST"

    def test_no_todo_list_yet_returns_400(self, client_with_manager):
        """Instance exists but has no todo list yet returns 400.

        The spec maps ValueError to 400/404; we use 400 because the
        request payload (the index) is the invalid part, not the URL.
        """
        client, state = client_with_manager
        # No create() call — the instance exists but has no todo list.
        state["manager"] = _make_manager()

        resp = client.post(
            "/api/instances/inst-1/todos/0/comment",
            json={"comment": "x"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "INVALID_REQUEST"

    def test_404_when_instance_missing(self, client_with_manager):
        """Unknown instance returns 404 (instance check fires first)."""
        client, state = client_with_manager
        state["manager"] = _make_manager(has_instance=False)

        resp = client.post(
            "/api/instances/ghost/todos/0/comment",
            json={"comment": "x"},
        )

        assert resp.status_code == 404
        assert "Instance not found" in resp.json()["detail"]["message"]

    def test_emits_sse_update_on_success(self, client_with_live_hub):
        """A successful comment write triggers a ``stream_todo_update`` call.

        The router looks up ``request.app.state.live_hub`` and calls
        ``stream_todo_update(instance_id, current_todos)``. The mock
        records the call so we can assert the hub is pinged with the
        post-mutation state.
        """
        client, state, hub = client_with_live_hub
        mgr = state["manager"]
        mgr._todo_manager.create("inst-1", ["A", "B"])

        resp = client.post(
            "/api/instances/inst-1/todos/0/comment",
            json={"comment": "feedback"},
        )

        assert resp.status_code == 200
        assert hub.stream_todo_update.await_count == 1
        # The hub was called with the instance_id and the post-mutation list.
        call = hub.stream_todo_update.await_args
        assert call.args[0] == "inst-1"
        assert isinstance(call.args[1], list)
        assert call.args[1][0]["comment"] == "feedback"

    def test_sse_emission_failure_does_not_break_write(self, client_with_live_hub):
        """If the SSE hub raises, the comment is still persisted.

        Mirrors the ``_emit_update`` best-effort pattern in the tools
        layer: a transport hiccup never rolls back the write.
        """
        client, state, hub = client_with_live_hub
        mgr = state["manager"]
        mgr._todo_manager.create("inst-1", ["A"])
        hub.stream_todo_update.side_effect = RuntimeError("hub down")

        resp = client.post(
            "/api/instances/inst-1/todos/0/comment",
            json={"comment": "still want this"},
        )

        assert resp.status_code == 200
        assert resp.json()["comment"] == "still want this"
        # Mutation persisted even though the hub call blew up.
        assert mgr._todo_manager.get_all("inst-1")[0]["comment"] == "still want this"

    def test_no_live_hub_attribute_still_succeeds(self, client_with_manager):
        """The endpoint must work when ``app.state.live_hub`` is absent.

        Production wires ``live_hub`` during lifespan startup, but a
        stripped-down test app (or an early-startup race) may not have
        it. The router uses ``getattr(..., "live_hub", None)`` so the
        request should still succeed.
        """
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A"])
        state["manager"] = mgr
        # client_with_manager's middleware doesn't set live_hub, so it's absent.

        resp = client.post(
            "/api/instances/inst-1/todos/0/comment",
            json={"comment": "no hub"},
        )

        assert resp.status_code == 200
        assert resp.json()["comment"] == "no hub"


class TestSetTodoCommentIntegrationWithUpdate:
    """End-to-end: set a comment, then ``update`` the item to done.

    The comment set via the HTTP endpoint should be picked up by
    ``update()`` and surface in the reminder as ``"User commented: ..."``.
    """

    def test_comment_set_via_api_surfaces_in_update_reminder(self, client_with_manager):
        """Comment set via API → reminder includes ``"User commented: ..."``."""
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["Task A", "Task B"])
        state["manager"] = mgr

        # Set comment via the HTTP API.
        resp = client.post(
            "/api/instances/inst-1/todos/0/comment",
            json={"comment": "Reviewed — looks good"},
        )
        assert resp.status_code == 200

        # Now mark the item as done via the manager directly (the tool
        # layer is already covered by ``test_todo_tools.py``; this test
        # is about API → manager → reminder plumbing).
        result = mgr._todo_manager.update("inst-1", 0, "done")
        assert result is not None
        assert "User commented: Reviewed — looks good" in result["reminder"]
        # And the next-pending pointer still follows.
        assert "Next:" in result["reminder"]
        assert "Task B" in result["reminder"]
