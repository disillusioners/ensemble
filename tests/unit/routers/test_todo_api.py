"""HTTP API tests for the todo list, comment, edge, and graph endpoints.

Endpoints under test (in ``daemon/routers/instances.py``):

  * ``GET    /api/instances/{instance_id}/todos``
        Returns the instance's todo list as a JSON array. Each item
        has the **frozen Phase 3 schema** (seven keys): ``id``,
        ``index``, ``text``, ``status``, ``comment``, ``next_ids``,
        ``subtasks``. Empty list ``[]`` when the instance has no
        todos yet.
  * ``POST   /api/instances/{instance_id}/todos/{node_id}/comment``
        Sets a comment on a todo node. Body: ``{"comment": "..."}``.
        ``node_id`` may be either a generated ``n-`` prefixed ID
        (preferred) or a numeric insertion-order index string (backward
        compat — auto-detected via ``isdigit()``). Emits a
        ``todo_update`` SSE event on success.
  * ``POST   /api/instances/{instance_id}/todos/edges``
        Adds a directed edge to the graph.
        Body: ``{"from_id": "...", "to_id": "..."}``.
        Returns ``{"nodes": [...], "edges": [...]}`` on success.
        Returns ``400`` for missing nodes, self-loops, or cycles.
  * ``DELETE /api/instances/{instance_id}/todos/edges``
        Removes a directed edge. Same body shape. Returns the updated
        graph on success; ``404`` for missing instance / node / edge.
  * ``GET    /api/instances/{instance_id}/todos/graph``
        Returns the graph snapshot ``{"nodes": [...], "edges": [...]}``
        for clients that need explicit edge enumeration.

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
        """Each item includes the seven frozen-schema keys.

        Phase 3 expanded the payload from four to six keys so the
        frontend can render node identity (``id``) and successor
        adjacency (``next_ids``) without a second round-trip; Phase 1b
        of the todo-subtasks feature expanded it from six to seven
        keys by adding ``subtasks``. The full set is the *frozen*
        contract documented on ``TodoGraphManager._to_dict``:

            ``id``, ``index``, ``text``, ``status``, ``comment``, ``next_ids``, ``subtasks``
        """
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A", "B", "C"])
        state["manager"] = mgr

        resp = client.get("/api/instances/inst-1/todos")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 3
        expected_keys = {"id", "index", "text", "status", "comment", "next_ids", "subtasks"}
        for item in body:
            assert set(item.keys()) == expected_keys
            # New graph-aware fields surface with sensible defaults:
            # ``id`` is non-empty ``n-`` prefixed; ``next_ids`` is a list
            # (empty for the terminal node, populated for predecessors).
            assert item["id"].startswith("n-")
            assert isinstance(item["next_ids"], list)
        assert body[0]["text"] == "A"
        assert body[1]["text"] == "B"
        assert body[2]["text"] == "C"
        # Default empty comments surface in the payload.
        assert all(item["comment"] == "" for item in body)

    def test_reflects_status_changes(self, client_with_manager):
        """GET reflects the current state after a status update.

        Uses ``update_by_index`` (the backward-compat shim) because this
        test exercises the legacy index-based contract; newer code paths
        key on node IDs directly.
        """
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A", "B"])
        mgr._todo_manager.update_by_index("inst-1", 0, "done")
        state["manager"] = mgr

        resp = client.get("/api/instances/inst-1/todos")

        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["status"] == "done"
        assert body[1]["status"] == "pending"

    def test_reflects_comment_changes(self, client_with_manager):
        """GET surfaces the comment after ``set_comment_by_index``."""
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A"])
        mgr._todo_manager.set_comment_by_index("inst-1", 0, "hello")
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
        mgr._todo_manager.set_comment_by_index("inst-1", 0, "first")
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
        mgr._todo_manager.set_comment_by_index("inst-1", 0, "preexisting")
        state["manager"] = mgr

        resp = client.post(
            "/api/instances/inst-1/todos/0/comment",
            json={},
        )

        assert resp.status_code == 200
        assert resp.json()["comment"] == ""

    def test_index_too_large_returns_404(self, client_with_manager):
        """Out-of-bounds index returns 404 with ``TODO_NOT_FOUND``.

        We return ``404`` (not ``400``) because the URL addresses a
        specific item that doesn't exist — REST resource-not-found
        semantics rather than payload-malformed semantics.
        """
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A"])  # len=1
        state["manager"] = mgr

        resp = client.post(
            "/api/instances/inst-1/todos/5/comment",
            json={"comment": "x"},
        )

        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["code"] == "TODO_NOT_FOUND"
        assert "Todo node '5' not found" in body["detail"]["message"]
        # State untouched
        assert mgr._todo_manager.get_all("inst-1")[0]["comment"] == ""

    def test_negative_index_returns_404(self, client_with_manager):
        """Negative index returns 404 with ``TODO_NOT_FOUND``.

        ``ValueError`` from ``set_comment`` (index out of range) is mapped
        to ``404`` rather than the previous ``400`` — the addressed item
        does not exist on the instance.
        """
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A", "B"])
        state["manager"] = mgr

        resp = client.post(
            "/api/instances/inst-1/todos/-1/comment",
            json={"comment": "x"},
        )

        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["code"] == "TODO_NOT_FOUND"
        assert "Todo node '-1' not found" in body["detail"]["message"]

    def test_no_todo_list_yet_returns_404(self, client_with_manager):
        """Instance exists but has no todo list yet returns 404.

        A ``set_comment`` on an instance that has never had a list
        addresses a non-existent todo item, so the response is ``404``
        with ``TODO_NOT_FOUND`` — consistent with the negative-index and
        out-of-bounds cases above.
        """
        client, state = client_with_manager
        # No create() call — the instance exists but has no todo list.
        state["manager"] = _make_manager()

        resp = client.post(
            "/api/instances/inst-1/todos/0/comment",
            json={"comment": "x"},
        )

        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "TODO_NOT_FOUND"

    def test_comment_too_long_returns_400(self, client_with_manager):
        """Comment exceeding ``MAX_COMMENT_LENGTH`` returns 400.

        We enforce the 1000-char cap in the handler (not via Pydantic's
        auto-422) for uniform error shape with the rest of the API. The
        error code is ``INVALID_REQUEST`` to signal a payload-side issue,
        and the message surfaces the actual limit so clients can trim.
        """
        from daemon.routers.instances import MAX_COMMENT_LENGTH

        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A"])
        state["manager"] = mgr

        over_limit = "a" * (MAX_COMMENT_LENGTH + 1)
        resp = client.post(
            "/api/instances/inst-1/todos/0/comment",
            json={"comment": over_limit},
        )

        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["code"] == "INVALID_REQUEST"
        assert str(MAX_COMMENT_LENGTH) in body["detail"]["message"]
        # State untouched — the over-length comment must NOT be persisted.
        assert mgr._todo_manager.get_all("inst-1")[0]["comment"] == ""

    def test_comment_at_max_length_succeeds(self, client_with_manager):
        """A comment exactly at ``MAX_COMMENT_LENGTH`` chars is accepted.

        Off-by-one boundary check — the boundary value must succeed;
        only strict greater-than must fail.
        """
        from daemon.routers.instances import MAX_COMMENT_LENGTH

        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A"])
        state["manager"] = mgr

        at_limit = "a" * MAX_COMMENT_LENGTH
        resp = client.post(
            "/api/instances/inst-1/todos/0/comment",
            json={"comment": at_limit},
        )

        assert resp.status_code == 200
        assert resp.json()["comment"] == at_limit

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
        result = mgr._todo_manager.update_by_index("inst-1", 0, "done")
        assert result is not None
        assert "User commented:\n---\nReviewed — looks good\n---\n" in result["reminder"]
        # And the next-pending pointer still follows.
        assert "Next:" in result["reminder"]
        assert "Task B" in result["reminder"]


# =============================================================================
# POST /api/instances/{instance_id}/todos/{node_id}/comment — node-ID form
# =============================================================================


class TestSetTodoCommentByNodeId:
    """``POST .../todos/{node_id}/comment`` — using a real ``n-`` prefixed ID.

    The router auto-detects via ``node_id.isdigit()``: a generated
    ``n-`` prefixed ID is never all-numeric, so it dispatches to
    ``set_comment()`` (the strict path). The legacy numeric-index form
    is covered by :class:`TestSetTodoComment` above; this class focuses
    on the preferred node-ID path.
    """

    def test_set_comment_by_node_id(self, client_with_manager):
        """A real ``n-`` prefixed node ID routes to ``set_comment`` and persists.

        We grab the generated ``id`` from the manager's return value so
        the test exercises the same shape the frontend will pass — no
        hand-crafted IDs that might drift from the generator's format.
        """
        client, state = client_with_manager
        mgr = _make_manager()
        nodes = mgr._todo_manager.create("inst-1", ["A", "B"])
        state["manager"] = mgr
        target_id = nodes[1]["id"]
        # Sanity: the generator always prefixes with ``n-``, so this
        # path never falls through to ``set_comment_by_index``.
        assert target_id.startswith("n-")
        assert not target_id.isdigit()

        resp = client.post(
            f"/api/instances/inst-1/todos/{target_id}/comment",
            json={"comment": "by node id"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == target_id
        assert body["text"] == "B"
        assert body["comment"] == "by node id"
        # And persisted in the underlying state.
        assert mgr._todo_manager.get_all("inst-1")[1]["comment"] == "by node id"

    def test_set_comment_by_numeric_index_still_works(self, client_with_manager):
        """Numeric string ``"0"`` still routes to ``set_comment_by_index``.

        Backward compat: the URL ``/todos/0/comment`` continues to work
        via the router's ``node_id.isdigit()`` branch. Asserts the
        resulting dict matches the updated item (using the real ``id``
        from the manager so the test stays decoupled from the ID
        generator's internal format).
        """
        client, state = client_with_manager
        mgr = _make_manager()
        nodes = mgr._todo_manager.create("inst-1", ["A", "B"])
        state["manager"] = mgr

        resp = client.post(
            "/api/instances/inst-1/todos/0/comment",
            json={"comment": "numeric index"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["index"] == 0
        assert body["id"] == nodes[0]["id"]
        assert body["comment"] == "numeric index"

    def test_unknown_node_id_returns_404(self, client_with_manager):
        """Unknown non-numeric ``node_id`` returns 404 with ``TODO_NOT_FOUND``.

        The list must exist (so the path doesn't collapse into the
        ``no todo list yet`` 404) — but the addressed node is absent,
        so the router returns the addressed-resource-not-found error.
        """
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A"])  # list exists
        state["manager"] = mgr

        resp = client.post(
            "/api/instances/inst-1/todos/n-doesnotexist/comment",
            json={"comment": "x"},
        )

        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["code"] == "TODO_NOT_FOUND"
        assert "Todo node 'n-doesnotexist' not found" in body["detail"]["message"]


# =============================================================================
# POST /api/instances/{instance_id}/todos/edges — graph mutation
# =============================================================================


class TestAddTodoEdge:
    """``POST /api/instances/{instance_id}/todos/edges`` — add a directed edge."""

    def test_add_edge_success_returns_updated_graph(self, client_with_manager):
        """Successful edge creation returns 200 with the full graph snapshot.

        The response shape is the same ``{"nodes": [...], "edges": [...]}``
        structure the graph endpoint returns — clients can re-render
        from the response body alone (the SSE re-emit is best-effort).
        """
        client, state = client_with_manager
        mgr = _make_manager()
        # Branching DAG: A → B (declared via edge), then add B → C.
        mgr._todo_manager.create_graph(
            "inst-1",
            nodes=[
                {"id": "n-a", "text": "A"},
                {"id": "n-b", "text": "B"},
                {"id": "n-c", "text": "C"},
            ],
            edges=[{"from": "n-a", "to": "n-b"}],
        )
        state["manager"] = mgr

        resp = client.post(
            "/api/instances/inst-1/todos/edges",
            json={"from_id": "n-b", "to_id": "n-c"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"nodes", "edges"}
        assert {"from": "n-b", "to": "n-c"} in body["edges"]
        # The pre-existing edge A → B is still in the result.
        assert {"from": "n-a", "to": "n-b"} in body["edges"]

    def test_add_edge_node_not_found_returns_400(self, client_with_manager):
        """Adding an edge with an unknown node returns 400 ``INVALID_REQUEST``.

        Implementation note: ``add_edge`` collapses *both* "node not
        found" and "would create a cycle" into a single ``None``
        return — the router does not distinguish between them at the
        HTTP layer, by design (both are caller-side payload errors).
        This test pins that contract so a future refactor that changes
        the status code trips a review.
        """
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create_graph(
            "inst-1",
            nodes=[{"id": "n-a", "text": "A"}],
            edges=[],
        )
        state["manager"] = mgr

        resp = client.post(
            "/api/instances/inst-1/todos/edges",
            json={"from_id": "n-a", "to_id": "n-missing"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "INVALID_REQUEST"

    def test_add_edge_self_loop_returns_400(self, client_with_manager):
        """A self-loop (``from_id == to_id``) returns 400 ``INVALID_REQUEST``.

        Self-loops are unconditionally cycles (Kahn's algorithm cannot
        reduce the source's in-degree past 1), so the manager treats
        them as a hard reject rather than a recoverable error.
        """
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create_graph(
            "inst-1",
            nodes=[{"id": "n-a", "text": "A"}],
            edges=[],
        )
        state["manager"] = mgr

        resp = client.post(
            "/api/instances/inst-1/todos/edges",
            json={"from_id": "n-a", "to_id": "n-a"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "INVALID_REQUEST"

    def test_add_edge_creates_cycle_returns_400(self, client_with_manager):
        """Adding an edge that closes a cycle returns 400 ``INVALID_REQUEST``.

        With a linear chain ``A → B → C``, attempting ``C → A`` would
        form ``A → B → C → A`` — rejected by ``_has_cycle`` after the
        candidate edge is appended. The manager rolls back the
        insertion so the stored graph stays a DAG.
        """
        client, state = client_with_manager
        mgr = _make_manager()
        nodes = mgr._todo_manager.create("inst-1", ["A", "B", "C"])
        state["manager"] = mgr
        node_a_id = nodes[0]["id"]
        node_c_id = nodes[2]["id"]

        resp = client.post(
            "/api/instances/inst-1/todos/edges",
            json={"from_id": node_c_id, "to_id": node_a_id},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "INVALID_REQUEST"
        # Rollback: the rejected edge must NOT be persisted.
        items = mgr._todo_manager.get_all("inst-1")
        assert node_a_id not in items[2]["next_ids"]

    def test_add_edge_emits_sse_on_success(self, client_with_live_hub):
        """A successful add_edge call re-emits ``stream_todo_update``.

        Mirrors the comment endpoint's re-emit pattern: every graph
        mutation pings the SSE hub with the post-mutation snapshot so
        connected clients re-render immediately.
        """
        client, state, hub = client_with_live_hub
        mgr = state["manager"]
        mgr._todo_manager.create_graph(
            "inst-1",
            nodes=[
                {"id": "n-a", "text": "A"},
                {"id": "n-b", "text": "B"},
            ],
            edges=[],
        )

        resp = client.post(
            "/api/instances/inst-1/todos/edges",
            json={"from_id": "n-a", "to_id": "n-b"},
        )

        assert resp.status_code == 200
        assert hub.stream_todo_update.await_count == 1
        call = hub.stream_todo_update.await_args
        assert call.args[0] == "inst-1"
        assert isinstance(call.args[1], list)


# =============================================================================
# DELETE /api/instances/{instance_id}/todos/edges — graph mutation
# =============================================================================


class TestRemoveTodoEdge:
    """``DELETE /api/instances/{instance_id}/todos/edges`` — drop a directed edge."""

    def test_remove_edge_success_returns_updated_graph(self, client_with_manager):
        """Removing an existing edge returns 200 with the updated graph.

        ``create(["A", "B"])`` auto-builds the linear edge ``A → B``,
        so this test uses the auto-generated IDs from the manager's
        return value rather than hand-crafting them.
        """
        client, state = client_with_manager
        mgr = _make_manager()
        nodes = mgr._todo_manager.create("inst-1", ["A", "B"])
        state["manager"] = mgr
        node_a_id = nodes[0]["id"]
        node_b_id = nodes[1]["id"]

        resp = client.request(
            "DELETE",
            "/api/instances/inst-1/todos/edges",
            json={"from_id": node_a_id, "to_id": node_b_id},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"nodes", "edges"}
        # The removed edge is gone from the response.
        assert {"from": node_a_id, "to": node_b_id} not in body["edges"]
        # And from the underlying state — node A's ``next_ids`` is empty.
        items = mgr._todo_manager.get_all("inst-1")
        assert items[0]["next_ids"] == []

    def test_remove_edge_not_found_returns_404(self, client_with_manager):
        """Removing a non-existent edge returns 404 ``TODO_NOT_FOUND``.

        With a linear chain ``A → B → C`` (auto-built by ``create``),
        the edge ``A → C`` does not exist — removing it must report a
        clean 404, not silently succeed.
        """
        client, state = client_with_manager
        mgr = _make_manager()
        nodes = mgr._todo_manager.create("inst-1", ["A", "B", "C"])
        state["manager"] = mgr
        node_a_id = nodes[0]["id"]
        node_c_id = nodes[2]["id"]

        resp = client.request(
            "DELETE",
            "/api/instances/inst-1/todos/edges",
            json={"from_id": node_a_id, "to_id": node_c_id},
        )

        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["code"] == "TODO_NOT_FOUND"

    def test_remove_edge_missing_instance_returns_404(self, client_with_manager):
        """An unknown ``instance_id`` short-circuits to 404 before edge lookup.

        This is the ``_check_instance_exists`` guard firing first —
        identical shape to the GET /todos and POST comment endpoints.
        """
        client, state = client_with_manager
        state["manager"] = _make_manager(has_instance=False)

        resp = client.request(
            "DELETE",
            "/api/instances/ghost/todos/edges",
            json={"from_id": "n-a", "to_id": "n-b"},
        )

        assert resp.status_code == 404
        assert "Instance not found" in resp.json()["detail"]["message"]

    def test_remove_edge_emits_sse_on_success(self, client_with_live_hub):
        """A successful remove_edge call re-emits ``stream_todo_update``.

        Same pattern as :class:`TestAddTodoEdge.test_add_edge_emits_sse_on_success`
        — graph mutations emit a re-render signal so the frontend
        stays in sync without polling.
        """
        client, state, hub = client_with_live_hub
        mgr = state["manager"]
        nodes = mgr._todo_manager.create("inst-1", ["A", "B"])
        node_a_id = nodes[0]["id"]
        node_b_id = nodes[1]["id"]

        resp = client.request(
            "DELETE",
            "/api/instances/inst-1/todos/edges",
            json={"from_id": node_a_id, "to_id": node_b_id},
        )

        assert resp.status_code == 200
        assert hub.stream_todo_update.await_count == 1


# =============================================================================
# GET /api/instances/{instance_id}/todos/graph — graph snapshot
# =============================================================================


class TestGetTodoGraph:
    """``GET /api/instances/{instance_id}/todos/graph`` — graph snapshot endpoint."""

    def test_get_graph_returns_nodes_and_edges_structure(self, client_with_manager):
        """A linear chain produces ``{"nodes": [3 items], "edges": [2 items]}``.

        A chain of N nodes has exactly N-1 edges; we assert the counts
        match so the structure is unambiguous (not just truthy).
        """
        client, state = client_with_manager
        mgr = _make_manager()
        mgr._todo_manager.create("inst-1", ["A", "B", "C"])
        state["manager"] = mgr

        resp = client.get("/api/instances/inst-1/todos/graph")

        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"nodes", "edges"}
        assert isinstance(body["nodes"], list)
        assert isinstance(body["edges"], list)
        assert len(body["nodes"]) == 3
        assert len(body["edges"]) == 2

    def test_get_graph_edges_are_from_to_pairs(self, client_with_manager):
        """Each edge is shaped exactly ``{"from": str, "to": str}``.

        The shape matches the input accepted by ``create_graph`` and
        is the contract the frontend graph renderer consumes — see
        ``TodoGraphManager.get_graph`` for the source of truth.
        """
        client, state = client_with_manager
        mgr = _make_manager()
        nodes = mgr._todo_manager.create("inst-1", ["A", "B"])
        state["manager"] = mgr
        node_a_id = nodes[0]["id"]
        node_b_id = nodes[1]["id"]

        resp = client.get("/api/instances/inst-1/todos/graph")

        body = resp.json()
        # Two-node chain yields exactly one edge A → B.
        assert body["edges"] == [{"from": node_a_id, "to": node_b_id}]
        # Each edge dict has exactly two keys.
        for edge in body["edges"]:
            assert set(edge.keys()) == {"from", "to"}
            assert isinstance(edge["from"], str)
            assert isinstance(edge["to"], str)

    def test_get_graph_returns_empty_when_no_todos(self, client_with_manager):
        """Instance with no todo list returns ``{"nodes": [], "edges": []}``.

        Same shape as the populated case — clients can always index
        ``body["nodes"]`` and ``body["edges"]`` without a None check.
        """
        client, state = client_with_manager
        # Fresh manager — no ``create()`` call yet.
        state["manager"] = _make_manager()

        resp = client.get("/api/instances/inst-1/todos/graph")

        assert resp.status_code == 200
        assert resp.json() == {"nodes": [], "edges": []}

    def test_get_graph_404_when_instance_missing(self, client_with_manager):
        """Unknown ``instance_id`` returns 404 (instance guard fires first)."""
        client, state = client_with_manager
        state["manager"] = _make_manager(has_instance=False)

        resp = client.get("/api/instances/ghost/todos/graph")

        assert resp.status_code == 404
        assert "Instance not found" in resp.json()["detail"]["message"]
