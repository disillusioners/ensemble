"""Unit tests for the user message injection API (Phase 2 / Tasks 3-6).

Covers the state-aware ``POST /api/instances/{id}/messages`` routing
introduced in Phase 2:

    * RUNNING → set RAM injection slot, emit ``injection_pending`` SSE,
      return 202 Accepted.
    * WAITING_CHILDREN → same as RUNNING (slot survives the parent
      pause; consumed on the next agent turn).
    * PAUSED → existing auto-resume behavior (**NO CHANGE — C4**).
      The test asserts the existing ``resume_instance_cascade`` +
      ``resume_processing_job`` path is still wired up untouched.
    * IDLE / terminal → existing ``enqueue_message_job`` path
      (**NO CHANGE**).
    * Empty / whitespace-only content → 400 (S4) BEFORE any routing.
    * Replacement semantics: a 2nd injection on a slot that already
      holds content emits ``injection_cleared`` for the OLD content
      BEFORE ``injection_pending`` for the new one (Task 5).

Also covers the fallback query endpoint (Task 6):

    * GET /api/instances/{id}/injection → ``{pending, content, timestamp}``.

The tests use ``fastapi.testclient.TestClient`` against a minimal app
that only mounts the ``messages`` router — no DB, no MCP pool, no
full daemon manager. The manager surface is mocked so the router's
routing logic is exercised in isolation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _make_manager(
    *,
    status: str,
    instance_id: str = "inst-abc",
    pending: dict | None = None,
    set_return: dict | None = None,
):
    """Build a mock InstanceManager with the surface used by the messages router.

    The mock stands in for ``InstanceManager`` without spinning up a
    real database or MCP pool. Defaults are wired so each test only
    customizes what it cares about.

    Args:
        status: The status string returned by ``get_instance_info``.
        instance_id: The instance ID used in the response payload.
        pending: Pre-existing pending injection (for replacement tests).
        set_return: Return value of ``set_injection`` (timestamp + content).
    """
    manager = MagicMock()

    # is_write_paused — always False for the happy-path tests.
    manager.is_write_paused = False

    # config.llm.model_vision — False for default tests; vision tests can override.
    manager.config = MagicMock()
    manager.config.llm.model_vision = None

    # get_instance_info — returns the status dict that drives routing.
    manager.get_instance_info = MagicMock(return_value={"status": status, "instance_id": instance_id})

    # get_instance (async) — used by the query endpoint.
    async def _get_instance(iid):
        return MagicMock(instance_id=iid)
    manager.get_instance = _get_instance

    # Injection slot helpers — all sync.
    manager.get_injection = MagicMock(return_value=pending)
    if set_return is None:
        # Default returns a static content/timestamp. Individual tests
        # can pass a custom ``set_return`` for exact payload assertions.
        set_return = {"content": "user message", "timestamp": "2026-07-13T00:00:00+00:00"}

    # ``set_injection`` echoes the input content into its return dict so
    # tests that POST ``{"content": "hello agent"}`` see ``"hello agent"``
    # echoed back in the response body. Mirrors the real
    # ``InstanceManager.set_injection`` behavior.
    def _set_injection(iid, content):
        return {"content": content, "timestamp": "2026-07-13T00:00:00+00:00"}

    manager.set_injection = MagicMock(side_effect=_set_injection)
    manager.clear_injection = MagicMock(return_value=pending)

    # enqueue_message_job (async) — used by the IDLE/terminal path.
    # Wrapped in AsyncMock so tests can use ``assert_awaited_once`` etc.
    enqueue_result = MagicMock()
    enqueue_result.message_id = "msg-enqueued"
    enqueue_result.job_id = "job-enqueued"
    manager.enqueue_message_job = AsyncMock(return_value=enqueue_result)

    # resume_instance_cascade (async) — used by the PAUSED path.
    manager.resume_instance_cascade = AsyncMock(return_value={
        "target_id": instance_id,
        "resumed_ids": [instance_id],
        "skipped_ids": [],
    })

    # resume_processing_job (async) — used by the PAUSED path.
    manager.resume_processing_job = AsyncMock(return_value={"status": "resumed", "instance_id": instance_id})

    return manager


def _make_live_hub():
    """Build a mock LiveEventHub with stream_message as an AsyncMock.

    Records every call so tests can assert on the call sequence
    (e.g., cleared-before-pending on replacement).
    """
    hub = MagicMock()
    hub.stream_message = AsyncMock()
    return hub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client_and_state():
    """Yield (TestClient, state_dict) so tests can inject a manager + hub.

    Mirrors the pattern in ``tests/unit/routers/test_message_status_endpoint.py``
    — mount only the messages router on a bare FastAPI app, then middleware-
    inject the manager and live_hub into ``app.state`` per request.
    """
    from daemon.routers.messages import router

    app = FastAPI()
    app.include_router(router)
    state: dict = {"manager": None, "live_hub": None}

    @app.middleware("http")
    async def _inject_state(request, call_next):
        request.app.state.manager = state["manager"]
        request.app.state.live_hub = state["live_hub"]
        return await call_next(request)

    client = TestClient(app)
    yield client, state


# ---------------------------------------------------------------------------
# Routing — RUNNING / WAITING_CHILDREN → injection (202)
# ---------------------------------------------------------------------------


class TestInjectionPath:
    """RUNNING and WAITING_CHILDREN route through the RAM injection slot."""

    def test_running_routes_to_injection_with_202(self, client_and_state):
        """RUNNING → set_injection + emit injection_pending + return 202."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="running")
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-abc/messages",
            json={"content": "hello agent"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "injected"
        assert body["instance_id"] == "inst-abc"
        assert body["content"] == "hello agent"
        assert "timestamp" in body

        # set_injection was called with the content
        state["manager"].set_injection.assert_called_once_with("inst-abc", "hello agent")

        # SSE emit: exactly one injection_pending event
        state["live_hub"].stream_message.assert_called_once()
        call = state["live_hub"].stream_message.await_args
        # W5 contract: stream_message reused with custom event_type.
        # ``message`` is passed as a keyword arg (the daemon's
        # convention), so read from ``call.kwargs``.
        assert call.kwargs["event_type"] == "injection_pending"
        assert call.args[0] == "inst-abc"
        payload = call.kwargs["message"]
        assert payload["event_type"] == "injection_pending"
        assert payload["content"] == "hello agent"
        assert payload["instance_id"] == "inst-abc"

    def test_waiting_children_routes_to_injection_with_202(self, client_and_state):
        """WAITING_CHILDREN → injection path (slot survives the parent wait)."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="waiting_children")
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-abc/messages",
            json={"content": "please advise"},
        )

        assert resp.status_code == 202, resp.text
        assert resp.json()["status"] == "injected"
        state["manager"].set_injection.assert_called_once()

    def test_running_does_not_enqueue_message_job(self, client_and_state):
        """Injection path MUST NOT fall through to enqueue_message_job."""
        client, state = client_and_state
        manager = _make_manager(status="running")
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-abc/messages", json={"content": "x"})

        manager.enqueue_message_job.assert_not_called()
        manager.resume_instance_cascade.assert_not_called()


# ---------------------------------------------------------------------------
# Routing — PAUSED → existing auto-resume (NO CHANGE / C4)
# ---------------------------------------------------------------------------


class TestPausedBranchUnchanged:
    """C4: PAUSED branch must remain untouched (cascade-resume + resume_processing_job)."""

    def test_paused_returns_200_with_auto_resumed_flag(self, client_and_state):
        """PAUSED → 200 with the existing ``auto_resumed=True`` payload shape."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="paused")
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-abc/messages",
            json={"content": "resume with this"},
        )

        # C4: status code MUST be 200 (NOT 202, NOT 409).
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["auto_resumed"] is True
        assert body["content"] == "resume with this"
        assert body["message_id"] is None

    def test_paused_calls_resume_instance_cascade_and_processing_job(self, client_and_state):
        """The PAUSED path must still invoke the auto-resume helpers."""
        client, state = client_and_state
        manager = _make_manager(status="paused")
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-abc/messages", json={"content": "go"})

        manager.resume_instance_cascade.assert_awaited_once_with("inst-abc")
        manager.resume_processing_job.assert_awaited_once()

    def test_paused_does_not_set_injection(self, client_and_state):
        """PAUSED → must NOT touch the RAM injection slot."""
        client, state = client_and_state
        manager = _make_manager(status="paused")
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-abc/messages", json={"content": "go"})

        manager.set_injection.assert_not_called()
        manager.get_injection.assert_not_called()

    def test_paused_does_not_emit_injection_pending(self, client_and_state):
        """PAUSED → must NOT emit injection_pending (no SSE injection event)."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="paused")
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-abc/messages", json={"content": "go"})

        state["live_hub"].stream_message.assert_not_called()


# ---------------------------------------------------------------------------
# Routing — IDLE / terminal → existing enqueue path (NO CHANGE)
# ---------------------------------------------------------------------------


class TestEnqueuePath:
    """IDLE / QUEUED / terminal statuses fall through to enqueue_message_job."""

    @pytest.mark.parametrize(
        "status",
        ["idle", "queued", "completed", "error", "failed", "terminated"],
    )
    def test_non_running_status_routes_to_enqueue_with_200(self, client_and_state, status):
        """Non-RUNNING / non-WAITING_CHILDREN statuses route to enqueue_message_job."""
        client, state = client_and_state
        manager = _make_manager(status=status)
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-abc/messages",
            json={"content": "enqueue me"},
        )

        # 200 (NOT 202) — the legacy enqueue path
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["auto_resumed"] is False
        assert body["resume_info"] is None
        # enqueue_message_job was called
        manager.enqueue_message_job.assert_awaited_once()

    def test_idle_does_not_set_injection(self, client_and_state):
        """IDLE → must NOT touch the injection slot."""
        client, state = client_and_state
        manager = _make_manager(status="idle")
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-abc/messages", json={"content": "x"})

        manager.set_injection.assert_not_called()


# ---------------------------------------------------------------------------
# Validation — empty content (S4)
# ---------------------------------------------------------------------------


class TestEmptyContentValidation:
    """S4: empty / whitespace-only content is rejected with 400 BEFORE routing."""

    @pytest.mark.parametrize("content", ["", "   ", "\n", "\t\n  "])
    def test_empty_or_whitespace_returns_400(self, client_and_state, content):
        """Empty / whitespace-only content → 400, no slot set, no enqueue."""
        client, state = client_and_state
        manager = _make_manager(status="running")
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-abc/messages", json={"content": content})

        assert resp.status_code == 400, resp.text
        # Validation runs BEFORE routing, so no manager side-effects.
        manager.set_injection.assert_not_called()
        manager.enqueue_message_job.assert_not_called()
        manager.resume_instance_cascade.assert_not_called()

    def test_empty_content_does_not_emit_sse(self, client_and_state):
        """Empty content → no SSE event (the validation gate fires first)."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="running")
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-abc/messages", json={"content": ""})

        state["live_hub"].stream_message.assert_not_called()


# ---------------------------------------------------------------------------
# Replacement semantics (Task 5)
# ---------------------------------------------------------------------------


class TestReplacementClearedThenPending:
    """A 2nd injection on a populated slot emits cleared-then-pending."""

    def test_replacement_emits_cleared_then_pending(self, client_and_state):
        """When the slot already holds content, cleared (OLD) fires BEFORE pending (NEW).

        Call order matters: the cleared event must reach the listener
        BEFORE the pending event so a frontend consumer can collapse the
        old "pending" state and render the new content without flicker.
        """
        client, state = client_and_state
        existing = {
            "content": "first message",
            "timestamp": "2026-07-13T00:00:00+00:00",
        }
        new_entry = {
            "content": "second message",
            "timestamp": "2026-07-13T00:00:01+00:00",
        }
        manager = _make_manager(
            status="running",
            pending=existing,
            set_return=new_entry,
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-abc/messages", json={"content": "second message"})

        assert resp.status_code == 202, resp.text

        # Two SSE calls in order: cleared (OLD content) → pending (NEW content)
        assert state["live_hub"].stream_message.await_count == 2

        first = state["live_hub"].stream_message.await_args_list[0]
        assert first.kwargs["event_type"] == "injection_cleared"
        first_payload = first.kwargs["message"]
        assert first_payload["content"] == "first message"
        assert first_payload["event_type"] == "injection_cleared"

        second = state["live_hub"].stream_message.await_args_list[1]
        assert second.kwargs["event_type"] == "injection_pending"
        assert second.kwargs["message"]["content"] == "second message"

    def test_first_injection_does_not_emit_cleared(self, client_and_state):
        """First injection (empty slot) → only pending, no spurious cleared event."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="running", pending=None)
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-abc/messages", json={"content": "first"})

        # Exactly one SSE call — the pending event, no cleared.
        state["live_hub"].stream_message.assert_called_once()
        call = state["live_hub"].stream_message.await_args
        assert call.kwargs["event_type"] == "injection_pending"


# ---------------------------------------------------------------------------
# Query endpoint (Task 6)
# ---------------------------------------------------------------------------


class TestPendingInjectionQuery:
    """GET /api/instances/{id}/injection — fallback sync endpoint."""

    def test_query_returns_pending_true_with_content(self, client_and_state):
        """Slot populated → ``pending=True`` + content + timestamp."""
        client, state = client_and_state
        state["manager"] = _make_manager(
            status="running",
            pending={
                "content": "still queued",
                "timestamp": "2026-07-13T00:00:00+00:00",
            },
        )
        state["live_hub"] = _make_live_hub()

        resp = client.get("/instances/inst-abc/injection")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == "inst-abc"
        assert body["pending"] is True
        assert body["content"] == "still queued"
        assert body["timestamp"] == "2026-07-13T00:00:00+00:00"

    def test_query_returns_pending_false_when_slot_empty(self, client_and_state):
        """Empty slot → ``pending=False`` + null content/timestamp."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="running", pending=None)
        state["live_hub"] = _make_live_hub()

        resp = client.get("/instances/inst-abc/injection")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == "inst-abc"
        assert body["pending"] is False
        assert body["content"] is None
        assert body["timestamp"] is None

    def test_query_returns_404_for_unknown_instance(self, client_and_state):
        """Unknown instance → 404 (typo'd ID surfaces clearly)."""
        client, state = client_and_state
        manager = MagicMock()
        manager.is_write_paused = False
        manager.config = MagicMock()
        manager.config.llm.model_vision = None

        async def _raise_keyerror(iid):
            raise KeyError(iid)
        manager.get_instance = _raise_keyerror
        manager.get_injection = MagicMock(return_value=None)
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.get("/instances/inst-missing/injection")

        assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# 404 for unknown instance on POST
# ---------------------------------------------------------------------------


class TestInstanceNotFound:
    """Unknown instance on POST /messages surfaces as 404 BEFORE routing."""

    def test_unknown_instance_returns_404(self, client_and_state):
        """POST /messages on a missing instance → 404, no slot set."""
        client, state = client_and_state
        manager = MagicMock()
        manager.is_write_paused = False
        manager.config = MagicMock()
        manager.config.llm.model_vision = None

        def _raise_keyerror(iid):
            raise KeyError(iid)
        manager.get_instance_info = _raise_keyerror
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-missing/messages", json={"content": "x"})

        assert resp.status_code == 404, resp.text
        state["live_hub"].stream_message.assert_not_called()


# ---------------------------------------------------------------------------
# Constraints from the plan
# ---------------------------------------------------------------------------


class TestPlanConstraints:
    """Explicit guardrails from the Phase 2 plan (W5, S4, C4)."""

    def test_no_409_for_paused(self, client_and_state):
        """C4: PAUSED must NEVER return 409 — the existing auto-resume flow is preserved."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="paused")
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-abc/messages", json={"content": "x"})

        assert resp.status_code != 409, (
            "C4 violation: PAUSED instances must auto-resume, not be rejected"
        )
        assert resp.status_code == 200

    def test_write_paused_returns_503(self, client_and_state):
        """Write-paused guard fires before any routing (matches existing behavior)."""
        client, state = client_and_state
        manager = _make_manager(status="running")
        manager.is_write_paused = True
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-abc/messages", json={"content": "x"})

        assert resp.status_code == 503, resp.text