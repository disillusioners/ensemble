"""Unit tests for the user message injection API (Phase 3).

Covers the state-aware ``POST /api/instances/{id}/messages`` routing
introduced in Phase 3:

    * RUNNING → set RAM injection queue, emit ``injection_pending`` SSE
      with ``pending_count``, return 202 Accepted.
    * WAITING_CHILDREN → same as RUNNING (the queue survives the parent
      pause; consumed on the next agent turn).
    * PAUSED → existing auto-resume behavior (**NO CHANGE — C4**).
      The test asserts the existing ``resume_instance_cascade`` +
      ``resume_processing_job`` path is still wired up untouched.
    * IDLE / terminal → existing ``enqueue_message_job`` path
      (**NO CHANGE**).
    * Empty / whitespace-only content → 400 (S4) BEFORE any routing.
    * Append-list semantics (Phase 3): a 2nd injection on a populated
      queue APPENDS — no ``injection_cleared`` event is emitted. The
      pending_count grows by 1 each time.

Also covers the fallback query endpoint (Task 6):

    * GET /api/instances/{id}/injection → ``{pending, pending_count,
      content, timestamp}``.

The tests use ``fastapi.testclient.TestClient`` against a minimal app
that only mounts the ``messages`` router — no DB, no MCP pool, no
full daemon manager. The manager surface is mocked so the router's
routing logic is exercised in isolation.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_wc_wake_enqueue_flag_cache():
    """Reset the WC-wake kill-switch cache around EVERY test in this module.

    W1 (2026-08-30 pre-flip batch): flag-ON tests set
    ``ENSEMBLE_WC_WAKE_ENQUEUE=1`` and call ``_reset_wc_wake_enqueue_for_tests()``
    so the resolver re-reads the env — but monkeypatch only restores the ENV at
    teardown; the resolver's module-global cache stays ``True`` and leaks into
    later flag-implicit tests (both the cross-file-order and subset-by-name
    vectors reproduce ``assert 200 == 202`` on the legacy 202 expectation).
    Clear the cache BEFORE and AFTER every test so each test resolves the flag
    from the ambient env. Module-scoped on purpose — a suite-global autouse in
    ``tests/conftest.py`` would mask intentional flag-state tests and add
    overhead everywhere.
    """
    from daemon.services.instance_messaging import (
        _reset_wc_wake_enqueue_for_tests,
    )

    _reset_wc_wake_enqueue_for_tests()
    yield
    _reset_wc_wake_enqueue_for_tests()


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _make_manager(
    *,
    status: str,
    instance_id: str = "inst-abc",
    pending_list: list[dict] | None = None,
    pending_count: int = 0,
    set_return: dict | None = None,
    queued: bool = False,
):
    """Build a mock InstanceManager with the surface used by the messages router.

    The mock stands in for ``InstanceManager`` without spinning up a
    real database or MCP pool. Defaults are wired so each test only
    customizes what it cares about.

    Args:
        status: The status string returned by ``get_instance_info``.
        instance_id: The instance ID used in the response payload.
        pending_list: Pre-existing queue (for append tests).
        pending_count: The count returned by ``get_injection_count``.
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
    manager.get_injection = MagicMock(return_value=pending_list)
    manager.get_injection_count = MagicMock(return_value=pending_count)
    if set_return is None:
        # Default returns a static content/timestamp. Individual tests
        # can pass a custom ``set_return`` for exact payload assertions.
        set_return = {"content": "user message", "timestamp": "2026-07-13T00:00:00+00:00"}

    # ``set_injection`` echoes the input content into its return dict so
    # tests that POST ``{"content": "hello agent"}`` see ``"hello agent"``
    # echoed back in the response body. Mirrors the real
    # ``InstanceManager.set_injection`` behavior — including the
    # CONDITIONAL ``echo_id`` key (message-display-latency Phase 1):
    # byte-identical entry shape when absent, ``echo_id`` key present
    # when passed.
    def _set_injection(iid, content, source=None, echo_id=None):
        entry = {"content": content, "timestamp": "2026-07-13T00:00:00+00:00"}
        if echo_id is not None:
            entry["echo_id"] = echo_id
        return entry

    manager.set_injection = MagicMock(side_effect=_set_injection)
    manager.clear_injection = MagicMock(return_value=pending_list)

    # enqueue_message_job (async) — used by the IDLE/terminal path.
    # Wrapped in AsyncMock so tests can use ``assert_awaited_once`` etc.
    enqueue_result = MagicMock()
    enqueue_result.message_id = "msg-enqueued"
    enqueue_result.job_id = "job-enqueued"
    enqueue_result.queued = queued
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

    Records every call so tests can assert on the call sequence.
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
    """RUNNING and WAITING_CHILDREN route through the RAM injection queue."""

    def test_running_routes_to_injection_with_202(self, client_and_state):
        """RUNNING → set_injection + inject pending_count + emit injection_pending + return 202."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="running", pending_count=1)
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
        # Phase 3: pending_count is in the response body
        assert body["pending_count"] == 1

        # set_injection was called with the content AND the router-minted
        # stable echo_id (message-display-latency Phase 1). No ``source``
        # kwarg — provenance stays an agent-tool-only feature.
        state["manager"].set_injection.assert_called_once()
        call_kwargs = state["manager"].set_injection.call_args.kwargs
        assert state["manager"].set_injection.call_args.args == (
            "inst-abc",
            "hello agent",
        )
        assert call_kwargs.get("source") is None
        posted_echo_id = call_kwargs.get("echo_id")
        assert posted_echo_id is not None
        # The echo_id is a valid UUID4 string minted by the router.
        assert str(uuid.UUID(posted_echo_id)) == posted_echo_id
        assert uuid.UUID(posted_echo_id).version == 4

        # SSE emit: injection_pending FIRST, then the POST-time
        # user_message echo (message-display-latency Phase 1) — two calls.
        assert state["live_hub"].stream_message.await_count == 2
        calls = state["live_hub"].stream_message.await_args_list

        # ---- Call 1: injection_pending — shape unchanged except the
        # ADDITIVE ``echo_id`` correlation field (leader decision: YES). ----
        pending_call = calls[0]
        assert pending_call.kwargs["event_type"] == "injection_pending"
        assert pending_call.args[0] == "inst-abc"
        payload = pending_call.kwargs["message"]
        assert payload["event_type"] == "injection_pending"
        assert payload["content"] == "hello agent"
        assert payload["instance_id"] == "inst-abc"
        # Phase 3: pending_count is in the SSE payload
        assert payload["pending_count"] == 1
        assert payload["echo_id"] == posted_echo_id

        # ---- Call 2: POST-time user_message — same echo_id, POST stamp. ----
        user_call = calls[1]
        assert user_call.kwargs["event_type"] == "user_message"
        assert user_call.kwargs["instance_id"] == "inst-abc"
        user_payload = user_call.kwargs["message"]
        assert user_payload["message_id"] == posted_echo_id
        assert user_payload["role"] == "user"
        assert user_payload["content"] == "hello agent"
        # created_at = the entry's POST timestamp (the mock's fixed stamp)
        assert user_payload["created_at"] == "2026-07-13T00:00:00+00:00"
        assert user_payload["instance_id"] == "inst-abc"

    def test_running_202_body_includes_message_id_existing_keys_unchanged(self, client_and_state):
        """message-display-latency Phase 1: the 202 body gains
        ``message_id`` (the router-minted echo id) — ADDITIVE; every
        pre-existing key keeps its name and value.

        Phase 1 fix also adds ``created_at`` — ADDITIVE — using the
        SAME entry POST timestamp the SSE echo carries (same-id-same-
        stamp principle). Without this key the FE provisional was
        getting ``undefined`` and ``evictPendingByAge`` was treating
        the unparseable timestamp as expired.
        """
        client, state = client_and_state
        state["manager"] = _make_manager(status="running", pending_count=1)
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-abc/messages",
            json={"content": "hello agent"},
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        # Existing keys unchanged
        assert set(body) >= {
            "status",
            "instance_id",
            "content",
            "timestamp",
            "pending_count",
        }
        assert body["status"] == "injected"
        assert body["content"] == "hello agent"
        assert body["timestamp"] == "2026-07-13T00:00:00+00:00"
        # Additive: created_at == the entry POST timestamp (same stamp
        # the SSE echo carries — same-id-same-stamp).
        assert body["created_at"] == "2026-07-13T00:00:00+00:00"
        # Additive: message_id == the same echo_id handed to set_injection
        # (single id continuity at POST time)
        echo_id = state["manager"].set_injection.call_args.kwargs["echo_id"]
        assert body["message_id"] == echo_id

    def test_post_time_user_message_survives_sse_outage(self, client_and_state):
        """A POST-time SSE failure must NOT fail the POST (best-effort echo;
        the injection is already queued)."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="running", pending_count=1)
        hub = _make_live_hub()
        hub.stream_message = AsyncMock(side_effect=RuntimeError("SSE down"))
        state["live_hub"] = hub

        resp = client.post(
            "/instances/inst-abc/messages",
            json={"content": "hello agent"},
        )

        # The 202 + body are unaffected by the SSE outage.
        assert resp.status_code == 202, resp.text
        assert resp.json()["status"] == "injected"
        # The manager call still happened exactly once.
        state["manager"].set_injection.assert_called_once()

    def test_waiting_children_post_time_user_message_shape(self, client_and_state):
        """WAITING_CHILDREN gets the same POST-time user_message echo
        (same shape, same entry POST timestamp)."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="waiting_children", pending_count=1)
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-abc/messages",
            json={"content": "please advise"},
        )

        assert resp.status_code == 202, resp.text
        assert resp.json()["message_id"] == (
            state["manager"].set_injection.call_args.kwargs["echo_id"]
        )
        calls = state["live_hub"].stream_message.await_args_list
        assert [c.kwargs["event_type"] for c in calls] == [
            "injection_pending",
            "user_message",
        ]
        user_payload = calls[1].kwargs["message"]
        assert user_payload["role"] == "user"
        assert user_payload["content"] == "please advise"
        assert user_payload["message_id"] == (
            state["manager"].set_injection.call_args.kwargs["echo_id"]
        )
        assert user_payload["created_at"] == "2026-07-13T00:00:00+00:00"

    def test_waiting_children_routes_to_injection_with_202(self, client_and_state):
        """WAITING_CHILDREN → injection path (queue survives the parent wait)."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="waiting_children", pending_count=1)
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-abc/messages",
            json={"content": "please advise"},
        )

        assert resp.status_code == 202, resp.text
        assert resp.json()["status"] == "injected"
        state["manager"].set_injection.assert_called_once()

    def test_waiting_children_routes_to_enqueue_with_200_flag_on(
        self, client_and_state, monkeypatch: pytest.MonkeyPatch
    ):
        """wc-wake-report-integrity (T4 + C1-Q2): when
        ``ENSEMBLE_WC_WAKE_ENQUEUE=1``, WAITING_CHILDREN targets fall
        to the enqueue branch (durable wake, 200 ``MessageResponse``)
        instead of the legacy FIFO injection (202).

        This pins the HTTP side of the routing pivot — the
        ``injection_pending`` SSE does NOT fire for WC under flag ON
        (FE sees the message via the normal turn-start
        ``user_message`` pre-emit instead).
        """
        from daemon.services.instance_messaging import (
            _reset_wc_wake_enqueue_for_tests,
        )

        monkeypatch.setenv("ENSEMBLE_WC_WAKE_ENQUEUE", "1")
        _reset_wc_wake_enqueue_for_tests()

        client, state = client_and_state
        state["manager"] = _make_manager(status="waiting_children", queued=True)
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-abc/messages",
            json={"content": "please advise"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Enqueue branch returns the standard MessageResponse shape
        # with message_id + job_id + queued (D4 contract).
        assert body["message_id"]
        assert body["job_id"]
        assert body["queued"] is True
        # No injection, no SSE for the injection_pending channel.
        state["manager"].set_injection.assert_not_called()
        state["manager"].enqueue_message_job.assert_awaited_once()
        state["live_hub"].stream_message.assert_not_called()

    def test_running_does_not_enqueue_message_job(self, client_and_state):
        """Injection path MUST NOT fall through to enqueue_message_job."""
        client, state = client_and_state
        manager = _make_manager(status="running")
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-abc/messages", json={"content": "x"})

        manager.enqueue_message_job.assert_not_called()
        manager.resume_instance_cascade.assert_not_called()

    def test_pending_count_reflects_post_set_state(self, client_and_state):
        """Phase 3: pending_count returned to the client reflects the queue depth
        AFTER this set_injection call. The mock simulates the post-append depth.
        """
        client, state = client_and_state
        # Manager starts with 1 pending, appends one more → reports 2.
        state["manager"] = _make_manager(
            status="running",
            pending_list=[{"content": "old", "timestamp": "t-old"}],
            pending_count=2,
        )
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-abc/messages",
            json={"content": "new"},
        )

        assert resp.status_code == 202
        body = resp.json()
        assert body["pending_count"] == 2
        # SSE payload also carries the pending_count — on the FIRST call
        # (injection_pending); the POST-time user_message echo follows it.
        pending_call = state["live_hub"].stream_message.await_args_list[0]
        assert pending_call.kwargs["event_type"] == "injection_pending"
        assert pending_call.kwargs["message"]["pending_count"] == 2


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
        # message-display-latency fix: PAUSED 200 fallback body now
        # ALSO carries ``created_at`` (additive, mirrors the 202 body
        # contract so the FE doesn't have to branch on response shape).
        # Asserted as ``is not None`` — the exact stamp is
        # wall-clock-dependent so we lock the shape, not the value.
        assert body.get("created_at") is not None
        # ISO-8601 with timezone — defensible regex check, not a value pin.
        assert isinstance(body["created_at"], str)
        assert "T" in body["created_at"]

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
        """PAUSED → must NOT touch the RAM injection queue."""
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
        """IDLE → must NOT touch the injection queue."""
        client, state = client_and_state
        manager = _make_manager(status="idle")
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-abc/messages", json={"content": "x"})

        manager.set_injection.assert_not_called()


# ---------------------------------------------------------------------------
# ``queued`` field on the NORMAL-path response
# ---------------------------------------------------------------------------


class TestQueuedField:
    """The NORMAL response propagates the enqueue-time capacity snapshot."""

    @pytest.mark.parametrize("queued", [False, True])
    def test_queued_value_is_propagated(self, client_and_state, queued):
        """The router uses ``AsyncMessageResult.queued`` without another DB read."""
        client, state = client_and_state
        manager = _make_manager(status="idle")
        manager.enqueue_message_job.return_value.queued = queued
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-abc/messages", json={"content": "hi"})

        assert resp.status_code == 200, resp.text
        assert resp.json()["queued"] is queued
        assert resp.json()["job_id"] == "job-enqueued"

    def test_queued_defaults_false_for_older_result_object(self, client_and_state):
        """Older enqueue results without ``queued`` remain backward compatible."""
        client, state = client_and_state
        manager = _make_manager(status="idle")
        legacy_result = type(
            "LegacyAsyncMessageResult",
            (),
            {"message_id": "msg-enqueued", "job_id": "job-enqueued"},
        )()
        manager.enqueue_message_job.return_value = legacy_result
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-abc/messages", json={"content": "hi"})

        assert resp.status_code == 200, resp.text
        assert resp.json()["queued"] is False


# ---------------------------------------------------------------------------
# Validation — empty content (S4)
# ---------------------------------------------------------------------------


class TestEmptyContentValidation:
    """S4: empty / whitespace-only content is rejected with 400 BEFORE routing."""

    @pytest.mark.parametrize("content", ["", "   ", "\n", "\t\n  "])
    def test_empty_or_whitespace_returns_400(self, client_and_state, content):
        """Empty / whitespace-only content → 400, no queue set, no enqueue."""
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
# Append-list semantics (Phase 3): no more injection_cleared
# ---------------------------------------------------------------------------


class TestAppendListSemantics:
    """Phase 3: a 2nd injection APPENDS — no injection_cleared is emitted."""

    def test_second_injection_appends_no_cleared(self, client_and_state):
        """When a queue is non-empty, the new message APPENDS — no cleared event.

        The old single-slot replace semantics emitted ``injection_cleared``
        BEFORE ``injection_pending``. Under Phase 3 append-list semantics
        the cleared event is GONE: the new pending_count simply reflects
        the appended queue depth.
        """
        client, state = client_and_state
        # Simulate a queue with one pre-existing entry; the new pending
        # count after the append is 2.
        existing = [
            {"content": "first message", "timestamp": "2026-07-13T00:00:00+00:00"},
        ]
        manager = _make_manager(
            status="running",
            pending_list=existing,
            pending_count=2,
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-abc/messages", json={"content": "second message"})

        assert resp.status_code == 202, resp.text

        # TWO SSE calls — injection_pending + POST-time user_message
        # (message-display-latency Phase 1). NO ``injection_cleared``.
        calls = state["live_hub"].stream_message.await_args_list
        assert state["live_hub"].stream_message.await_count == 2
        assert calls[0].kwargs["event_type"] == "injection_pending"
        assert calls[1].kwargs["event_type"] == "user_message"
        event_types = {c.kwargs["event_type"] for c in calls}
        assert "injection_cleared" not in event_types
        payload = calls[0].kwargs["message"]
        assert payload["content"] == "second message"
        assert payload["pending_count"] == 2

    def test_first_injection_emits_only_pending_then_post_echo(self, client_and_state):
        """First injection (empty queue) → injection_pending + POST-time
        user_message echo; no spurious cleared event."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="running", pending_count=1)
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-abc/messages", json={"content": "first"})

        # Exactly two SSE calls — pending + POST-time user_message; no cleared.
        calls = state["live_hub"].stream_message.await_args_list
        assert state["live_hub"].stream_message.await_count == 2
        assert calls[0].kwargs["event_type"] == "injection_pending"
        assert calls[1].kwargs["event_type"] == "user_message"
        # pending_count is in the pending payload
        assert calls[0].kwargs["message"]["pending_count"] == 1

    def test_response_body_includes_pending_count(self, client_and_state):
        """Phase 3: the 202 response body carries pending_count."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="running", pending_count=3)
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-abc/messages", json={"content": "x"})

        assert resp.status_code == 202
        body = resp.json()
        assert body["pending_count"] == 3


# ---------------------------------------------------------------------------
# Query endpoint (Task 6)
# ---------------------------------------------------------------------------


class TestPendingInjectionQuery:
    """GET /api/instances/{id}/injection — fallback sync endpoint."""

    def test_query_returns_pending_true_with_content(self, client_and_state):
        """Queue populated → ``pending=True`` + content + timestamp + pending_count."""
        client, state = client_and_state
        state["manager"] = _make_manager(
            status="running",
            pending_list=[{"content": "still queued", "timestamp": "2026-07-13T00:00:00+00:00"}],
            pending_count=1,
        )
        state["live_hub"] = _make_live_hub()

        resp = client.get("/instances/inst-abc/injection")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == "inst-abc"
        assert body["pending"] is True
        assert body["content"] == "still queued"
        assert body["timestamp"] == "2026-07-13T00:00:00+00:00"
        assert body["pending_count"] == 1

    def test_query_returns_pending_false_when_queue_empty(self, client_and_state):
        """Empty queue → ``pending=False`` + null content/timestamp + pending_count=0."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="running", pending_count=0)
        state["live_hub"] = _make_live_hub()

        resp = client.get("/instances/inst-abc/injection")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instance_id"] == "inst-abc"
        assert body["pending"] is False
        assert body["content"] is None
        assert body["timestamp"] is None
        assert body["pending_count"] == 0

    def test_query_returns_oldest_entry_for_multi_message_queue(self, client_and_state):
        """Phase 3: GET response surfaces the OLDEST pending entry under
        ``content`` / ``timestamp`` for backward compatibility. The
        ``pending_count`` field exposes the full queue depth.
        """
        client, state = client_and_state
        state["manager"] = _make_manager(
            status="running",
            pending_list=[
                {"content": "oldest", "timestamp": "2026-07-13T00:00:00+00:00"},
                {"content": "middle", "timestamp": "2026-07-13T00:00:01+00:00"},
                {"content": "newest", "timestamp": "2026-07-13T00:00:02+00:00"},
            ],
            pending_count=3,
        )
        state["live_hub"] = _make_live_hub()

        resp = client.get("/instances/inst-abc/injection")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pending"] is True
        assert body["pending_count"] == 3
        # The OLDEST entry is echoed for backward compatibility
        assert body["content"] == "oldest"
        assert body["timestamp"] == "2026-07-13T00:00:00+00:00"

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
        manager.get_injection_count = MagicMock(return_value=0)
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
        """POST /messages on a missing instance → 404, no queue set."""
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
    """Explicit guardrails from the Phase 3 plan (W5, S4, C4)."""

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
