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
    pending_list: list[dict] | None = None,
    pending_count: int = 0,
    set_return: dict | None = None,
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
    # ``InstanceManager.set_injection`` behavior.
    def _set_injection(iid, content):
        return {"content": content, "timestamp": "2026-07-13T00:00:00+00:00"}

    manager.set_injection = MagicMock(side_effect=_set_injection)
    manager.clear_injection = MagicMock(return_value=pending_list)

    # enqueue_message_job (async) — used by the IDLE/terminal path.
    # Wrapped in AsyncMock so tests can use ``assert_awaited_once`` etc.
    enqueue_result = MagicMock()
    enqueue_result.message_id = "msg-enqueued"
    enqueue_result.job_id = "job-enqueued"
    manager.enqueue_message_job = AsyncMock(return_value=enqueue_result)

    # _job_queue_service — by default the snapshot lookup is disabled so
    # the existing tests (which don't care about ``queued``) don't have
    # to wire up an ``AsyncMock`` for ``get_job``. Tests that DO assert
    # on the queued field can override this with a stub that returns a
    # synthetic JobItem via ``set_job_queue_service_with_state``.
    manager._job_queue_service = None

    # resume_instance_cascade (async) — used by the PAUSED path.
    manager.resume_instance_cascade = AsyncMock(return_value={
        "target_id": instance_id,
        "resumed_ids": [instance_id],
        "skipped_ids": [],
    })

    # resume_processing_job (async) — used by the PAUSED path.
    manager.resume_processing_job = AsyncMock(return_value={"status": "resumed", "instance_id": instance_id})

    return manager


def set_job_queue_service_with_state(manager, admission_state: str | None) -> None:
    """Wire ``manager._job_queue_service.get_job`` to return a synthetic JobItem.

    Tests that assert on the ``queued`` field inject this stub so the
    router's snapshot lookup resolves a JobItem with the desired
    ``admission_state``. Pass ``admission_state=None`` to simulate a
    missing JobItem row (the lookup returns ``None``).

    Args:
        manager: The MagicMock manager returned by ``_make_manager``.
        admission_state: The string value the synthetic JobItem should
            expose on its ``admission_state`` attribute. ``None`` makes
            ``get_job`` return ``None`` (row missing / purged).
    """
    job_queue_service = MagicMock()

    async def _get_job(job_id):
        if admission_state is None:
            return None
        synthetic = MagicMock()
        synthetic.admission_state = admission_state
        return synthetic

    job_queue_service.get_job = AsyncMock(side_effect=_get_job)
    manager._job_queue_service = job_queue_service


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
        # Phase 3: pending_count is in the SSE payload
        assert payload["pending_count"] == 1

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
        # SSE payload also carries the pending_count
        call = state["live_hub"].stream_message.await_args
        assert call.kwargs["message"]["pending_count"] == 2


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
    """``queued`` snapshot — read off ``JobItem.admission_state`` after enqueue.

    The router performs a synchronous lookup against
    ``manager._job_queue_service.get_job(result.job_id)`` immediately
    after ``enqueue_message_job`` returns and sets ``queued=True`` iff
    ``JobItem.admission_state == 'queued'``. The field defaults to
    ``False`` when the JobItem cannot be read (service missing, lookup
    raised, row purged) — the message is still enqueued in that case.
    """

    def test_queued_true_when_admission_state_is_queued(self, client_and_state):
        """admission_state='queued' → response body carries queued=True."""
        client, state = client_and_state
        manager = _make_manager(status="idle")
        set_job_queue_service_with_state(manager, admission_state="queued")
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-abc/messages", json={"content": "hi"})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["queued"] is True
        # job_id propagates from the AsyncMessageResult mock
        assert body["job_id"] == "job-enqueued"

    @pytest.mark.parametrize(
        "admission_state",
        ["active", "done", "dead"],
    )
    def test_queued_false_when_admission_state_is_not_queued(
        self, client_and_state, admission_state
    ):
        """admission_state != 'queued' → queued=False regardless of value.

        Covers the active (worker claimed) and terminal (done, dead)
        cases. All three resolve to queued=False because the field is
        strictly the ``QUEUED == 'queued'`` predicate.
        """
        client, state = client_and_state
        manager = _make_manager(status="idle")
        set_job_queue_service_with_state(manager, admission_state=admission_state)
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-abc/messages", json={"content": "hi"})

        assert resp.status_code == 200, resp.text
        assert resp.json()["queued"] is False

    def test_queued_false_when_job_item_not_found(self, client_and_state):
        """JobItem row missing (purged / not yet visible) → queued=False.

        The router MUST NOT raise on a missing row — the message is
        already enqueued, so the response still succeeds.
        """
        client, state = client_and_state
        manager = _make_manager(status="idle")
        set_job_queue_service_with_state(manager, admission_state=None)
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-abc/messages", json={"content": "hi"})

        assert resp.status_code == 200, resp.text
        assert resp.json()["queued"] is False

    def test_queued_false_when_job_queue_service_missing(self, client_and_state):
        """``_job_queue_service is None`` (test fixture default) → queued=False.

        Mirrors the no-service-fanout path: the router skips the lookup
        and returns the default False rather than failing the request.
        """
        client, state = client_and_state
        manager = _make_manager(status="idle")
        # Explicitly NOT calling set_job_queue_service_with_state — the
        # _make_manager helper defaults _job_queue_service to None.
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-abc/messages", json={"content": "hi"})

        assert resp.status_code == 200, resp.text
        assert resp.json()["queued"] is False

    def test_queued_lookup_failure_does_not_fail_request(self, client_and_state):
        """``get_job`` raising (DB blip) → queued=False, request still succeeds.

        Defensive contract: a transient lookup error must not regress
        the enqueue. The router logs at WARNING and falls back to
        ``queued=False``.
        """
        client, state = client_and_state
        manager = _make_manager(status="idle")
        job_queue_service = MagicMock()

        async def _raise(job_id):
            raise RuntimeError("synthetic DB error")

        job_queue_service.get_job = AsyncMock(side_effect=_raise)
        manager._job_queue_service = job_queue_service
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-abc/messages", json={"content": "hi"})

        assert resp.status_code == 200, resp.text
        assert resp.json()["queued"] is False
        # The lookup was actually attempted
        job_queue_service.get_job.assert_awaited_once_with("job-enqueued")

    def test_queued_lookup_uses_job_id_from_enqueue_result(self, client_and_state):
        """The router passes ``result.job_id`` into ``get_job`` — not the message_id.

        The lookup is keyed on the JobItem primary key (== Task.work_id),
        which is what ``AsyncMessageResult.job_id`` carries.
        """
        client, state = client_and_state
        manager = _make_manager(status="idle")
        # Override the default ``job_id`` so we can assert the lookup
        # uses it verbatim.
        manager.enqueue_message_job.return_value.job_id = "job-xyz"
        set_job_queue_service_with_state(manager, admission_state="queued")
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post("/instances/inst-abc/messages", json={"content": "hi"})

        assert resp.status_code == 200, resp.text
        assert resp.json()["queued"] is True
        manager._job_queue_service.get_job.assert_awaited_once_with("job-xyz")


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

        # Only ONE SSE call — the pending event. NO ``injection_cleared``.
        state["live_hub"].stream_message.assert_called_once()
        call = state["live_hub"].stream_message.await_args
        assert call.kwargs["event_type"] == "injection_pending"
        payload = call.kwargs["message"]
        assert payload["content"] == "second message"
        assert payload["pending_count"] == 2

    def test_first_injection_emits_only_pending(self, client_and_state):
        """First injection (empty queue) → only pending, no spurious cleared event."""
        client, state = client_and_state
        state["manager"] = _make_manager(status="running", pending_count=1)
        state["live_hub"] = _make_live_hub()

        client.post("/instances/inst-abc/messages", json={"content": "first"})

        # Exactly one SSE call — the pending event, no cleared.
        state["live_hub"].stream_message.assert_called_once()
        call = state["live_hub"].stream_message.await_args
        assert call.kwargs["event_type"] == "injection_pending"
        # pending_count is in the payload
        assert call.kwargs["message"]["pending_count"] == 1

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
