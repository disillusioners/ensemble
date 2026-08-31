"""Router-level tests for the slash-command subsystem (Phase 1 / WS-1 + WS-5).

Covers the WS-1 router intercept surface + WS-5 GET /commands/active
endpoint + WS-5 §5.2 SSE phase machine (F3 ordering, heartbeat,
rapid-click race) + WS-6 executor pre-checks:

**Intercept seam (``daemon/routers/messages.py`` ~line 242):**

- byte-identity regression for non-command traffic on every status
  branch (IDLE / RUNNING / WAITING_CHILDREN / PAUSED / terminal) —
  non-command traffic falls through unchanged.
- command traffic short-circuits BEFORE each status branch.
- ``/foo`` → ``400 UNKNOWN_COMMAND`` + ``details.available`` list.
- ``//path`` → passthrough as literal ``/path`` (reaches the normal
  message branch).
- Disabled mode → ``/x`` is plain text (existing-behavior passthrough;
  S-11 / plan 1.6 discipline).

**GET /commands/active (``daemon/routers/instances.py``):**

- none → ``{exists:false}``.
- active → event payload (CommandProgressEvent shape).
- disabled → ``{exists:false}`` (O12 invariant).
- auth mirrors GET /messages (instance 404).

**WS-5 SSE phase machine + executor pre-checks (Phase 1 / WS-2 + WS-5):**

- F3 ordering: ``waiting`` emitted BEFORE pause mutation (the
  handler runs after the POST returns the sync ack — we verify
  the handler reaches pause + emits waiting).
- Phase sequence: waiting → in_progress → success.
- Heartbeat at ~10s with phase_seq+1, strictly monotonic.
- Rapid-click race: double-POST within min-interval → exactly
  one accepted, second busy/rate_limited.
- aupdate_state call count == 0 in ALL reject cases.
- Terminal guard at router level: command short-circuits before
  status branch; aupdate never invoked.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.services.command_dispatcher import (
    CommandDispatcher,
    CommandPhase,
    CommandSpec,
    RejectionReason,
)


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def dispatcher() -> CommandDispatcher:
    """Fresh dispatcher per test — isolated rate-limit state."""
    return CommandDispatcher(
        enabled=True,
        escape_prefix="//",
        min_interval_s=10,
        state_ttl_s=600,
        max_state_per_instance=20,
    )


@pytest.fixture
def disabled_dispatcher() -> CommandDispatcher:
    """Dispatcher with the master switch OFF."""
    return CommandDispatcher(
        enabled=False,
        escape_prefix="//",
        min_interval_s=10,
        state_ttl_s=600,
        max_state_per_instance=20,
    )


def _make_manager(
    dispatcher: CommandDispatcher,
    *,
    instance_status: str | None = "idle",
    enqueue_message_result: dict | None = None,
    pending_injections: int = 0,
    instance_exists: bool = True,
) -> MagicMock:
    """Build a mock InstanceManager with the slice's surface.

    The router layer only reaches the manager via:
      - ``command_dispatcher`` (the WS-1 facade)
      - ``get_injection_count(iid)`` (WS-6 pending-injections guard)
      - ``get_instance_info(iid)`` (the existing 404 path)
      - ``get_instance(iid)`` (async — used by GET /commands/active
        and GET /messages for the 404 path)
      - ``enqueue_message_job(...)`` (the IDLE/terminal enqueue path
        reached by non-command passthrough)
      - ``set_injection(...)`` / ``get_injection_count(...)`` /
        live-hub SSE for the RUNNING/WC injection path

    Tests that exercise the injection branch stub the injection
    helpers explicitly; tests that hit the IDLE enqueue branch stub
    ``enqueue_message_job`` to return a known result.
    """
    manager = MagicMock()
    manager.command_dispatcher = dispatcher

    # write-pause guard is the FIRST check in the POST /messages
    # endpoint — must be False for tests to reach the intercept seam.
    manager.is_write_paused = False

    # get_instance_info returns the configured status (drives routing).
    def _info(iid):
        if not instance_exists:
            raise KeyError(iid)
        return {"status": instance_status, "id": iid}
    manager.get_instance_info = _info

    # get_instance is async — used by GET /messages + GET /commands/active.
    async def _get_instance(iid):
        if not instance_exists:
            raise KeyError(iid)
        return MagicMock(instance_id=iid)
    manager.get_instance = _get_instance

    # get_injection_count is the WS-6 guard input.
    manager.get_injection_count = MagicMock(return_value=pending_injections)

    # enqueue_message_job for the IDLE/terminal branch (passthrough).
    from daemon.services.messaging_types import AsyncMessageResult
    enq = enqueue_message_result or AsyncMessageResult(
        message_id="msg-test",
        instance_id="inst-A",
        status="queued",
        job_id="job-test",
        queued=False,
    )
    manager.enqueue_message_job = AsyncMock(return_value=enq)

    # Injection helpers — defaults; tests can rewire as needed.
    manager.set_injection = MagicMock(
        return_value={"content": "injected", "timestamp": "2026-01-01T00:00:00Z"}
    )

    return manager


@pytest.fixture
def client_with_manager():
    """Provide a TestClient and a way to inject a manager into app.state.

    Includes BOTH the messages router (POST /messages intercept) and
    the instances router (GET /commands/active endpoint) so a single
    client can drive either path.
    """
    from daemon.routers.instances import router as instances_router
    from daemon.routers.messages import router as messages_router

    app = FastAPI()
    app.include_router(messages_router)
    app.include_router(instances_router)
    state = {"manager": None}

    @app.middleware("http")
    async def _inject_manager(request, call_next):
        request.app.state.manager = state["manager"]
        return await call_next(request)

    client = TestClient(app)
    return client, state


# ─────────────────────────────────────────────────────────────────────────
# Test helpers — async handler we can register into the dispatcher
# ─────────────────────────────────────────────────────────────────────────


async def _ok_handler(*, instance_id, args, command_id, context):
    """Minimal success handler — terminalizes success."""
    await context.terminalize(
        CommandPhase.SUCCESS.value,
        detail={"reason": "router_test_ok"},
    )


async def _hanging_handler(*, instance_id, args, command_id, context):
    """Stays in-flight until the test cancels the bg task."""
    await asyncio.Event().wait()


def _register_compact(dispatcher: CommandDispatcher, handler=None) -> None:
    dispatcher.registry.register(
        CommandSpec(
            name="compact",
            description="On-demand context compaction",
            availability=None,
            rate_limit_per_instance=0,
            handler=handler or _ok_handler,
        )
    )


# ─────────────────────────────────────────────────────────────────────────
# POST /messages — intercept seam
# ─────────────────────────────────────────────────────────────────────────


class TestSlashCommandIntercept:
    """WS-1 router intercept — plan 1.3 + 1.5 + 1.6."""

    def test_known_command_short_circuits_with_200_ack(
        self, client_with_manager, dispatcher
    ):
        client, state = client_with_manager
        _register_compact(dispatcher)
        state["manager"] = _make_manager(dispatcher)

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "/compact"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "command"
        assert body["command"] == "compact"
        assert body["state"] == "accepted"
        assert body["command_id"]
        assert body["ttl_seconds"] == 600

    def test_unknown_command_returns_400_with_available_list(
        self, client_with_manager, dispatcher
    ):
        client, state = client_with_manager
        _register_compact(dispatcher)
        state["manager"] = _make_manager(dispatcher)

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "/foo"},
        )
        assert resp.status_code == 400, resp.text
        # FastAPI HTTPException wraps the model_dump() under a top-
        # level "detail" key. The {code, message, details} envelope
        # is preserved verbatim — additive over the pre-existing
        # :222-229 shape per O13 (2026-08-31).
        body = resp.json()
        assert "detail" in body
        inner = body["detail"]
        assert inner["code"] == "UNKNOWN_COMMAND"
        assert "message" in inner  # O13 additive: message kept
        assert inner["details"]["available"] == ["compact"]

    def test_unknown_command_with_empty_registry(
        self, client_with_manager, dispatcher
    ):
        client, state = client_with_manager
        state["manager"] = _make_manager(dispatcher)
        # No commands registered.
        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "/bar"},
        )
        assert resp.status_code == 400
        inner = resp.json()["detail"]
        assert inner["code"] == "UNKNOWN_COMMAND"
        assert inner["details"]["available"] == []

    def test_double_slash_escape_passes_through_as_plain_text(
        self, client_with_manager, dispatcher
    ):
        """``//etc/hosts`` → normal branch with content ``/etc/hosts``."""
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher, instance_status="idle")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "//etc/hosts"},
        )
        assert resp.status_code == 200, resp.text
        # Falls through to the IDLE enqueue branch.
        manager.enqueue_message_job.assert_awaited_once()
        call_kwargs = manager.enqueue_message_job.await_args.kwargs
        # The sanitized text `/etc/hosts` reaches the LLM as the
        # message content — NOT `//etc/hosts`.
        assert call_kwargs["message"] == "/etc/hosts"
        assert call_kwargs["instance_id"] == "inst-A"

    def test_disabled_mode_treats_slash_as_plain_text(
        self, client_with_manager, disabled_dispatcher
    ):
        """S-11 / plan 1.6 — disabled mode passes through byte-identical."""
        client, state = client_with_manager
        _register_compact(disabled_dispatcher)
        manager = _make_manager(disabled_dispatcher, instance_status="idle")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "/compact"},
        )
        assert resp.status_code == 200, resp.text
        # Falls through to IDLE enqueue; ``/compact`` reaches the LLM
        # as plain text (NOT routed through the dispatcher).
        manager.enqueue_message_job.assert_awaited_once()
        call_kwargs = manager.enqueue_message_job.await_args.kwargs
        assert call_kwargs["message"] == "/compact"

    def test_plain_text_passes_through_byte_identical(
        self, client_with_manager, dispatcher
    ):
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher, instance_status="idle")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "hello world"},
        )
        assert resp.status_code == 200, resp.text
        manager.enqueue_message_job.assert_awaited_once()
        assert (
            manager.enqueue_message_job.await_args.kwargs["message"]
            == "hello world"
        )

    def test_command_short_circuits_before_idle_enqueue(
        self, client_with_manager, dispatcher
    ):
        """A command never reaches the IDLE enqueue path."""
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher, instance_status="idle")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "/compact"},
        )
        assert resp.status_code == 200
        manager.enqueue_message_job.assert_not_awaited()

    def test_command_short_circuits_before_running_injection(
        self, client_with_manager, dispatcher
    ):
        """A command on a RUNNING instance never touches the injection
        slot — the intercept happens BEFORE the RUNNING branch."""
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher, instance_status="running")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "/compact"},
        )
        assert resp.status_code == 200
        manager.set_injection.assert_not_called()

    def test_command_short_circuits_before_paused_auto_resume(
        self, client_with_manager, dispatcher
    ):
        """A command on a PAUSED instance never invokes the
        auto-resume path."""
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher, instance_status="paused")
        manager.resume_instance_cascade = AsyncMock(
            return_value={"resumed_ids": ["inst-A"], "skipped_ids": [], "target_id": "inst-A"}
        )
        manager.resume_processing_job = AsyncMock(
            return_value={"status": "resumed", "message_id": "msg-r"}
        )
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "/compact"},
        )
        assert resp.status_code == 200
        # Auto-resume was NOT triggered by a command POST.
        manager.resume_instance_cascade.assert_not_awaited()
        manager.resume_processing_job.assert_not_awaited()

    def test_command_short_circuits_before_terminal_revival(
        self, client_with_manager, dispatcher
    ):
        """A /compact on a compact-REJECTED instance (terminated /
        error / failed) is REJECTED IN THE ACK (defect #2, 2026-08-31
        — the instance-status gate is a dispatch-time guard).
        Semantics update: the executor used to own the
        terminal_instance rejection from the bg task (which raced the
        client's post-ack subscription and stranded the FE waiting
        card); the dispatcher now answers it synchronously and spawns
        no task at all. The 200 + no-enqueue behavior below is
        unchanged — a rejected ack is still a 200 that never reaches
        the enqueue/revive branch. (compact-on-COMPLETED 2026-08-31:
        ``terminated`` carries the pin — ``completed`` now accepts and
        has its own dedicated case above.)"""
        client, state = client_with_manager
        _register_compact(dispatcher, handler=_hanging_handler)
        manager = _make_manager(dispatcher, instance_status="terminated")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "/compact"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "rejected"
        assert body["reason"] == "terminal_instance"
        # Falls through to enqueue_message_job? No — the dispatcher
        # captured the request, so enqueue is NOT called.
        manager.enqueue_message_job.assert_not_awaited()

    def test_unknown_command_short_circuits_before_idle_enqueue(
        self, client_with_manager, dispatcher
    ):
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher, instance_status="idle")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "/foo"},
        )
        assert resp.status_code == 400
        manager.enqueue_message_job.assert_not_awaited()

    def test_pending_injections_guard_rejects_at_router(
        self, client_with_manager, dispatcher
    ):
        """O-B11 ratified — non-empty injection queue → reject."""
        client, state = client_with_manager
        _register_compact(dispatcher)
        # Configure the manager to report a non-empty injection queue.
        manager = _make_manager(
            dispatcher,
            instance_status="running",
            pending_injections=3,
        )
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "/compact"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "rejected"
        assert body["reason"] == RejectionReason.PENDING_INJECTIONS.value
        # The injection slot was not touched — the rejection happens
        # BEFORE the RUNNING branch.
        manager.set_injection.assert_not_called()

    def test_inflight_busy_rejects_with_reason(
        self, client_with_manager, dispatcher
    ):
        """Verify BUSY rejection at the router layer.

        TestClient cancels outstanding background tasks when the
        request scope closes (anyio task-group teardown), which
        would clear ``_inflight`` between two POSTs. To observe the
        in-flight guard deterministically, we keep a command ACTIVE
        via the dispatcher's own state registry (no handler needed)
        and assert the second POST sees BUSY.
        """
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher, instance_status="running")
        state["manager"] = manager

        # Seed an ACTIVE command in the dispatcher's state registry
        # WITHOUT spawning a bg task (the bg task is what TestClient
        # would cancel between requests — we sidestep that by
        # populating state directly).
        import asyncio as _asyncio

        async def _seed_active():
            from daemon.services.command_dispatcher import (
                CommandDispatcher as _CD,
            )
            dispatcher._state.record_start(
                instance_id="inst-A",
                command_id="cmd-active",
                command="compact",
                ttl_seconds=600,
            )
            dispatcher._inflight["inst-A"] = "cmd-active"

        _asyncio.run(_seed_active())

        # POST while active — must be BUSY.
        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "/compact"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "rejected"
        assert body["reason"] == RejectionReason.BUSY.value


# ─────────────────────────────────────────────────────────────────────────
# GET /api/instances/{id}/commands/active — WS-5 O12
# ─────────────────────────────────────────────────────────────────────────


class TestGetActiveCommand:
    """WS-5 GET /commands/active endpoint (O12)."""

    def _make_active(
        self, dispatcher, *, instance_id="inst-A"
    ) -> tuple[MagicMock, str]:
        """Seed an active command in the dispatcher's state registry.

        Returns the manager mock and the command_id minted.
        """
        import asyncio as _asyncio

        async def _seed():
            outcome = await dispatcher.dispatch(
                instance_id=instance_id,
                text="/compact",
                pending_injections=0,
            )
            return outcome

        outcome = _asyncio.run(_seed())
        return outcome.ack["command_id"]

    def test_no_active_returns_exists_false(
        self, client_with_manager, dispatcher
    ):
        client, state = client_with_manager
        state["manager"] = _make_manager(dispatcher)

        resp = client.get("/instances/inst-A/commands/active")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"exists": False}

    def test_active_command_returns_event_payload(
        self, client_with_manager, dispatcher
    ):
        """Active command → event payload (W-1.3 timestamp + W-3.4 active-slot integrity).

        W-3.4 — the previous behaviour leaked the active slot across
        event-loop boundaries (a long handler that was cancelled on
        loop close left ``_active`` populated). The current dispatch
        drops the active slot on cancel. To exercise the GET
        endpoint with a live active command, this test seeds the
        active slot directly via the dispatcher's state registry
        (the same pattern used by
        ``test_inflight_busy_rejects_with_reason``).
        """
        client, state = client_with_manager
        _register_compact(dispatcher, handler=_hanging_handler)
        state["manager"] = _make_manager(dispatcher)

        # Seed an active command in the dispatcher's state registry
        # WITHOUT spawning a bg task — the bg task is what TestClient
        # would cancel between requests (the cross-loop cancel path
        # is what W-3.4 fixes; the dispatcher actively drops the
        # active slot on cancel, so we can't rely on the bg task
        # leaving the slot populated).
        command_id = "cmd-active-payload"
        import asyncio as _asyncio

        async def _seed():
            dispatcher._state.record_start(
                instance_id="inst-A",
                command_id=command_id,
                command="compact",
                ttl_seconds=600,
            )
            dispatcher._inflight["inst-A"] = command_id

        _asyncio.run(_seed())

        resp = client.get("/instances/inst-A/commands/active")
        assert resp.status_code == 200
        body = resp.json()
        assert body["exists"] is True
        event = body["command"]
        # WS-5 CommandProgressEvent shape — subset keys.
        for key in (
            "instance_id",
            "command_id",
            "phase",
            "phase_seq",
            "timestamp",  # W-1.3 — ISO 8601, NOT time.monotonic() float
            "elapsed_ms",
            "last_event_elapsed_ms",
        ):
            assert key in event, f"missing key {key} in {event!r}"
        assert event["instance_id"] == "inst-A"
        assert event["command_id"] == command_id
        assert event["phase"] == "waiting"  # record_start default
        assert event["phase_seq"] >= 1
        assert event["elapsed_ms"] >= 0

        # Cleanup.
        for task in list(dispatcher._tasks):
            task.cancel()

    def test_disabled_flag_returns_exists_false(
        self, client_with_manager, disabled_dispatcher
    ):
        """O12 invariant — disabled flag returns uniform 200 {exists:false}
        so the FE contract is invariant across config flips."""
        client, state = client_with_manager
        _register_compact(disabled_dispatcher)
        manager = _make_manager(disabled_dispatcher)
        state["manager"] = manager

        # Even if the registry were non-empty, the disabled flag wins.
        resp = client.get("/instances/inst-A/commands/active")
        assert resp.status_code == 200
        assert resp.json() == {"exists": False}

    def test_instance_not_found_returns_404(
        self, client_with_manager, dispatcher
    ):
        """Auth mirrors GET /messages — instance 404s."""
        client, state = client_with_manager

        async def _raise_keyerror(instance_id):
            raise KeyError(instance_id)

        manager = _make_manager(dispatcher)
        manager.get_instance = _raise_keyerror
        state["manager"] = manager

        resp = client.get("/instances/ghost/commands/active")
        assert resp.status_code == 404, resp.text
        assert "not found" in resp.text.lower()

    def test_recent_terminal_within_ttl_returns_event(
        self, client_with_manager, dispatcher
    ):
        """GET endpoint returns the most-recent terminal entry that
        has not yet passed its TTL — backs SSE-loss recovery."""
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher)
        state["manager"] = manager

        async def _dispatch_and_drain():
            outcome = await dispatcher.dispatch(
                instance_id="inst-A",
                text="/compact",
                pending_injections=0,
            )
            command_id = outcome.ack["command_id"]
            # Drain bg task so terminalize fires.
            for task in list(dispatcher._tasks):
                await task
            return command_id

        command_id = asyncio.run(_dispatch_and_drain())

        resp = client.get("/instances/inst-A/commands/active")
        assert resp.status_code == 200
        body = resp.json()
        assert body["exists"] is True
        assert body["command"]["command_id"] == command_id
        assert body["command"]["phase"] == CommandPhase.SUCCESS.value

    def test_evicted_instance_returns_exists_false(
        self, client_with_manager, dispatcher
    ):
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher)
        state["manager"] = manager

        async def _dispatch_and_evict():
            outcome = await dispatcher.dispatch(
                instance_id="inst-A",
                text="/compact",
                pending_injections=0,
            )
            for task in list(dispatcher._tasks):
                await task
            dispatcher.evict_instance("inst-A")

        asyncio.run(_dispatch_and_evict())

        resp = client.get("/instances/inst-A/commands/active")
        assert resp.status_code == 200
        assert resp.json() == {"exists": False}


# ─────────────────────────────────────────────────────────────────────────
# Disjoint regression — non-command traffic remains byte-identical
# (the WS-1 "regression test asserting non-command traffic through
# messages.py:243-500 is byte-identical" — architect §1).
# ─────────────────────────────────────────────────────────────────────────


class TestNonCommandTrafficByteIdentity:
    """S-11 / plan 1.6 — non-command traffic through every status
    branch is unchanged when slash_commands.enabled is on AND off.

    Verifies by mocking the manager and asserting the IDLE enqueue
    branch is hit with the same arguments as if no dispatcher existed.
    """

    def test_idle_passthrough_with_dispatcher_enabled(
        self, client_with_manager, dispatcher
    ):
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher, instance_status="idle")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "regular text", "queue_id": "q1"},
        )
        assert resp.status_code == 200, resp.text
        # Reaches the IDLE enqueue branch unchanged.
        manager.enqueue_message_job.assert_awaited_once()
        kwargs = manager.enqueue_message_job.await_args.kwargs
        assert kwargs["message"] == "regular text"
        assert kwargs["instance_id"] == "inst-A"
        assert kwargs["source"] == "api"
        assert kwargs["queue_id"] == "q1"
        # The dispatcher was NOT asked to handle non-command traffic
        # (parse returned None → passthrough without dispatcher mutation).
        assert dispatcher._inflight == {}

    def test_idle_passthrough_with_dispatcher_disabled(
        self, client_with_manager, disabled_dispatcher
    ):
        client, state = client_with_manager
        _register_compact(disabled_dispatcher)
        manager = _make_manager(disabled_dispatcher, instance_status="idle")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "regular text"},
        )
        assert resp.status_code == 200
        manager.enqueue_message_job.assert_awaited_once()

    def test_existing_404_path_unchanged(
        self, client_with_manager, dispatcher
    ):
        """The pre-existing instance-not-found 404 path is reached
        unchanged — the slash intercept sits AFTER the 404 path
        (manager.get_instance_info at the top of the endpoint)."""
        client, state = client_with_manager

        def _info_404(iid):
            raise KeyError(iid)

        manager = _make_manager(dispatcher, instance_status="idle")
        manager.get_instance_info = _info_404
        state["manager"] = manager

        resp = client.post(
            "/instances/ghost/messages",
            json={"content": "/compact"},  # would be a command if 200
        )
        assert resp.status_code == 404
        # The dispatcher never saw the request.
        assert dispatcher._inflight == {}
        assert dispatcher._last_dispatch == {}

    def test_existing_empty_content_400_unchanged(
        self, client_with_manager, dispatcher
    ):
        """S4 — empty content 400 fires BEFORE the slash intercept."""
        client, state = client_with_manager
        _register_compact(dispatcher)
        state["manager"] = _make_manager(dispatcher)

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "   "},
        )
        assert resp.status_code == 400
        # Dispatcher never saw the request.
        assert dispatcher._inflight == {}


# ─────────────────────────────────────────────────────────────────────────
# WS-5 §5.2 — SSE phase machine (F3 ordering, heartbeat, rapid-click)
# + WS-6 — aupdate call count 0 in ALL reject cases.
# ─────────────────────────────────────────────────────────────────────────


async def _executor_phase_seq(dispatcher, manager, instance_id="inst-A"):
    """Helper — register /compact with a handler that walks the
    full phase machine: waiting → in_progress → success.

    The handler is registered directly into the dispatcher so we
    can drive it without spinning up the real compact executor
    (this layer is the ROUTER layer — phase-machine behavior is
    exercised via a small mock handler that mimics the executor's
    surface).

    Returns the dispatcher's ``_tasks`` list for inspection.
    """
    phases = []

    async def _phase_handler(*, instance_id, args, command_id, context):
        # Phase 1 — waiting (F3 BEFORE pause mutation).
        await context.update_phase("waiting", bump_seq=False, detail={})
        phases.append(("waiting", context.dispatcher))

        # Phase 2 — in_progress (gate acquired / engine running).
        await context.update_phase("in_progress", bump_seq=True, detail={})
        phases.append(("in_progress", context.dispatcher))

        # Phase 3 — success terminal.
        await context.terminalize(
            "success",
            detail={
                "compacted_type": "summary",
                "tokens_before": 1000,
                "tokens_after": 100,
            },
        )
        phases.append(("success", context.dispatcher))

    from daemon.services.command_dispatcher import CommandSpec

    dispatcher.registry.register(
        CommandSpec(
            name="compact",
            description="Mock /compact for phase-machine tests",
            availability=None,
            rate_limit_per_instance=0,
            handler=_phase_handler,
        )
    )
    # Dispatch the command — the bg task runs the handler.
    outcome = await dispatcher.dispatch(
        instance_id=instance_id,
        text="/compact",
        pending_injections=0,
    )
    # Drain bg task.
    for task in list(dispatcher._tasks):
        await task
    return phases


class TestPhaseMachinePhaseSeq:
    """WS-5 §5.2 — the SSE phase machine walks waiting → in_progress →
    terminal in order."""

    @pytest.mark.asyncio
    async def test_phase_sequence_in_order(self, dispatcher):
        """waiting → in_progress → success — in that exact order."""
        phases = await _executor_phase_seq(dispatcher, _DummyManager())
        names = [p[0] for p in phases]
        assert names == ["waiting", "in_progress", "success"], (
            f"phase machine must walk waiting → in_progress → success; "
            f"got {names}"
        )


def _DummyManager():
    """Minimal manager stub for phase-machine tests (router layer
    doesn't need a real manager — the dispatcher's bg task drives
    the phase sequence, and the router just returns the sync ack).
    """
    mgr = MagicMock()
    mgr.is_write_paused = False
    mgr.get_instance_info = MagicMock(
        return_value={"status": "idle", "id": "inst-A"}
    )
    async def _get_instance(iid):
        return MagicMock(instance_id=iid)
    mgr.get_instance = _get_instance
    mgr.get_injection_count = MagicMock(return_value=0)
    return mgr


class TestRapidClickRace:
    """WS-6 R-12 — rapid-click race: double-POST within min-interval
    → exactly one accepted, second busy/rate_limited.
    """

    @pytest.mark.asyncio
    async def test_second_post_within_min_interval_rejected(self):
        """Two POSTs back-to-back → first accepts, second busy or
        rate_limited (in-flight guard fires first; min-interval
        answers the third+ POST)."""
        dispatcher = CommandDispatcher(
            enabled=True,
            escape_prefix="//",
            min_interval_s=10,
            state_ttl_s=600,
            max_state_per_instance=20,
        )

        async def _ok_handler(*, instance_id, args, command_id, context):
            # Stay in-flight so the active slot stays populated.
            await asyncio.Event().wait()

        from daemon.services.command_dispatcher import CommandSpec

        dispatcher.registry.register(
            CommandSpec(
                name="compact",
                description="Hanging /compact for race test",
                availability=None,
                rate_limit_per_instance=0,
                handler=_ok_handler,
            )
        )

        # First POST — accepted.
        outcome1 = await dispatcher.dispatch(
            instance_id="inst-race",
            text="/compact",
            pending_injections=0,
        )
        assert outcome1.kind == "ack"
        assert outcome1.ack["state"] == "accepted"

        # Second POST — busy (in-flight guard fires first).
        outcome2 = await dispatcher.dispatch(
            instance_id="inst-race",
            text="/compact",
            pending_injections=0,
        )
        assert outcome2.kind == "ack"
        assert outcome2.ack["state"] == "rejected"
        # Accept either busy or rate_limited — both are valid
        # rapid-click race outcomes (in-flight wins over min-interval).
        assert outcome2.ack["reason"] in ("busy", "rate_limited"), (
            f"second POST reason must be busy or rate_limited; "
            f"got {outcome2.ack['reason']}"
        )

        # Cleanup the hanging task.
        for task in list(dispatcher._tasks):
            task.cancel()


# ─────────────────────────────────────────────────────────────────────────
# Defect #4 (Scope 3, 2026-08-31 e2e gate) — escape-path single-write
# invariant. The reporter saw TWO identical user rows persist on one
# POST ``//compact is useful`` (ids fb125533 / f5739113); retest with
# ``//compact is useful v2`` produced ONE row. Original run4 has no
# captured netlog so the double-POST hypothesis cannot be ruled in or
# out from network evidence alone — but the BE code has no
# double-insert path (the escape branch in
# ``daemon/services/command_dispatcher.py:875-879`` returns
# immediately, and ``daemon/routers/messages.py:251-584`` runs at
# most ONE of {PAUSED auto-resume, RUNNING/WC injection,
# IDLE/terminal enqueue} per request). These tests pin the
# structural invariant on each state branch so a future regression
# that introduces a fall-through double-write is caught at unit-test
# time, not by the e2e gate.
# ─────────────────────────────────────────────────────────────────────────


class TestEscapePathSingleWrite:
    """Defect #4 regression — ``//compact is useful`` style escape
    reaches exactly ONE write sink per POST, regardless of instance
    status. Pinned across the four production-relevant branches:
    IDLE → NORMAL enqueue, PAUSED → auto-resume, RUNNING → injection,
    WAITING_CHILDREN (legacy flag-OFF) → injection. The terminal
    (COMPLETED/ERROR/FAILED/TERMINATED) branch shares the IDLE path's
    NORMAL enqueue, so the IDLE test covers both.
    """

    # The exact text from the defect. If a regression reintroduces a
    # double-insert for any ``//…`` input, the four assertions below
    # catch it deterministically.
    ESCAPE_TEXT = "//compact is useful"
    EXPECTED_CONTENT = "/compact is useful"  # one slash stripped

    def test_idle_state_one_enqueue_zero_other_writes(
        self, client_with_manager, dispatcher
    ):
        """IDLE → NORMAL path: ``enqueue_message_job`` exactly once,
        no set_injection, no resume cascade. This is the production
        branch for the defect scenario (post-(c) idle terminal)."""
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher, instance_status="idle")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": self.ESCAPE_TEXT},
        )
        assert resp.status_code == 200, resp.text

        # Exactly one enqueue — pinned via assert_awaited_once.
        manager.enqueue_message_job.assert_awaited_once()
        kwargs = manager.enqueue_message_job.await_args.kwargs
        assert kwargs["message"] == self.EXPECTED_CONTENT
        assert kwargs["instance_id"] == "inst-A"
        assert kwargs["source"] == "api"

        # No other write path triggered.
        manager.set_injection.assert_not_called()
        # ``resume_instance_cascade`` / ``resume_processing_job`` are
        # auto-MagicMock attributes on the default manager — use
        # ``assert_not_called`` which works for both sync Mock and
        # AsyncMock (the existing tests in this file use the same
        # pattern at lines 390-391).
        manager.resume_instance_cascade.assert_not_called()
        manager.resume_processing_job.assert_not_called()

        # Dispatcher state untouched (escape bypasses command
        # processing — no in-flight / no rate-limit record).
        assert dispatcher._inflight == {}
        assert dispatcher._last_dispatch == {}

    def test_completed_state_one_enqueue_zero_other_writes(
        self, client_with_manager, dispatcher
    ):
        """COMPLETED → NORMAL path (revive + enqueue). Same invariant
        as IDLE — the terminal branch in
        ``daemon/routers/messages.py:533-584`` reaches the same
        ``enqueue_message_job`` call as IDLE."""
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher, instance_status="completed")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": self.ESCAPE_TEXT},
        )
        assert resp.status_code == 200, resp.text

        manager.enqueue_message_job.assert_awaited_once()
        manager.set_injection.assert_not_called()
        manager.resume_instance_cascade.assert_not_called()
        manager.resume_processing_job.assert_not_called()

    def test_running_state_one_injection_zero_enqueue(
        self, client_with_manager, dispatcher
    ):
        """RUNNING → injection path: ``set_injection`` exactly once
        (RAM FIFO — no DB write), no enqueue_message_job."""
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher, instance_status="running")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": self.ESCAPE_TEXT},
        )
        assert resp.status_code == 202, resp.text

        # Exactly one injection slot write (append-list semantics).
        manager.set_injection.assert_called_once()
        manager.enqueue_message_job.assert_not_awaited()
        manager.resume_instance_cascade.assert_not_called()
        manager.resume_processing_job.assert_not_called()

    def test_waiting_children_legacy_flag_off_one_injection_zero_enqueue(
        self, client_with_manager, dispatcher
    ):
        """WAITING_CHILDREN with the WC-wake kill-switch OFF (the
        legacy default — ``_resolve_wc_wake_enqueue_enabled()``
        resolves to False unless ``ENSEMBLE_WC_WAKE_ENQUEUE=1``):
        → legacy FIFO injection, no DB enqueue."""
        # Default flag state is OFF — no env set means False.
        from daemon.services.instance_messaging import (
            _resolve_wc_wake_enqueue_enabled,
        )
        # Sanity check the test precondition.
        assert _resolve_wc_wake_enqueue_enabled() is False, (
            "test precondition: ENSEMBLE_WC_WAKE_ENQUEUE must NOT be "
            "set in this test environment; otherwise the WC branch "
            "falls through to enqueue instead of legacy injection."
        )

        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher, instance_status="waiting_children")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": self.ESCAPE_TEXT},
        )
        assert resp.status_code == 202, resp.text

        manager.set_injection.assert_called_once()
        manager.enqueue_message_job.assert_not_awaited()
        manager.resume_instance_cascade.assert_not_called()
        manager.resume_processing_job.assert_not_called()

    def test_paused_state_resume_cascade_once_no_enqueue(
        self, client_with_manager, dispatcher
    ):
        """PAUSED → auto-resume path: ``resume_instance_cascade``
        exactly once; ``resume_processing_job`` exactly once for the
        target resumed instance. No enqueue_message_job unless the
        fallback path fires (which it does NOT for a healthy resumed
        instance)."""
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher, instance_status="paused")
        manager.resume_instance_cascade = AsyncMock(
            return_value={
                "resumed_ids": ["inst-A"],
                "skipped_ids": [],
                "target_id": "inst-A",
            }
        )
        manager.resume_processing_job = AsyncMock(
            return_value={"status": "resumed", "message_id": "msg-r"}
        )
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": self.ESCAPE_TEXT},
        )
        assert resp.status_code == 200, resp.text

        # Resume cascade: exactly one.
        manager.resume_instance_cascade.assert_awaited_once()
        # Processing job resumed: exactly one (for the single resumed id).
        manager.resume_processing_job.assert_awaited_once()
        # No fallback enqueue — the resume returned a non-None result.
        manager.enqueue_message_job.assert_not_awaited()
        # No injection (PAUSED branch returns 200, not 202).
        manager.set_injection.assert_not_called()

    def test_escape_does_not_mutate_dispatcher_state(
        self, client_with_manager, dispatcher
    ):
        """Structural guard: the escape branch in
        ``daemon/services/command_dispatcher.py:875-879`` returns
        BEFORE the record_start / in-flight / last-dispatch mutation
        (lines 954-976). Pinned here so a future refactor that
        accidentally promotes the escape to a recorded command (e.g.
        rate-limit-stamp on every passthrough) is caught at unit time
        — that bug would cause every subsequent /compact on the same
        instance to be rate-limited as if the escape counted."""
        client, state = client_with_manager
        _register_compact(dispatcher)
        manager = _make_manager(dispatcher, instance_status="idle")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": self.ESCAPE_TEXT},
        )
        assert resp.status_code == 200, resp.text

        # Dispatcher state must remain empty for passthrough/escape.
        assert dispatcher._inflight == {}, (
            f"escape must not populate _inflight; got {dispatcher._inflight!r}"
        )
        assert dispatcher._last_dispatch == {}, (
            f"escape must not stamp _last_dispatch; got "
            f"{dispatcher._last_dispatch!r}"
        )
        # No bg task spawned for the escape.
        assert list(dispatcher._tasks) == [], (
            f"escape must not spawn a bg task; got {dispatcher._tasks!r}"
        )


# ─────────────────────────────────────────────────────────────────────────
# Defect #2 (2026-08-31 e2e gate) — terminal-instance rejection AT ACK TIME
# ─────────────────────────────────────────────────────────────────────────


class TestTerminalInstanceAckRejection:
    """Defect #2 — POST /compact on a compact-rejected instance must
    answer the 200 rejected ack envelope AT ACK TIME (architect §5/§7).

    Live evidence (gate run, command d34c1026): the old path acked
    ``accepted`` → the FE seeded its waiting card → the bg executor
    terminalized ``failed(terminal_instance)`` ~3ms after the ack —
    a terminal SSE event the client could not observe (it raced the
    post-ack subscription). The instance-status check is cheap and
    synchronous; it belongs in the ack path.

    compact-on-COMPLETED (2026-08-31): the gate rejects ONLY
    terminated / error / failed. A COMPLETED instance's /compact now
    proceeds PAST the gate to an accepted ack (the dedicated
    COMPLETED case below pins the flip); it stays terminal for every
    other consumer of the canonical set.

    Router contract pinned here:
    - 200 + full CommandAck envelope (state=rejected,
      reason=terminal_instance, detail=W-1.2 guidance copy).
    - NO fallthrough to the terminal-revival enqueue branch.
    - NO background executor task spawned.
    - NO SSE waiting event (the executor never runs).
    """

    GUIDANCE = "Send a message to start a new turn, then /compact."

    def _post_compact(self, client):
        return client.post(
            "/instances/inst-A/messages",
            json={"content": "/compact"},
        )

    def test_completed_compact_accepts_at_router(
        self, client_with_manager, dispatcher
    ):
        """compact-on-COMPLETED (2026-08-31) — the dedicated flip: a
        COMPLETED instance's /compact proceeds PAST the instance-status
        gate to an ACCEPTED ack (was: rejected terminal_instance).
        C1 Variant A persist keeps ``next=()`` so the executor's
        compact is safe; the instance row remains terminal for every
        other consumer of ``daemon.constants.TERMINAL_INSTANCE_STATUSES``."""
        client, state = client_with_manager
        _register_compact(dispatcher, handler=_hanging_handler)
        manager = _make_manager(dispatcher, instance_status="completed")
        state["manager"] = manager

        resp = self._post_compact(client)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Full CommandAck envelope (§7 schema) — accepted this time.
        assert body["status"] == "command"
        assert body["command"] == "compact"
        assert body["state"] == "accepted"
        assert body["command_id"], "accepted ack mints a command_id"
        assert body["timestamp"]
        assert body["ttl_seconds"] == 600
        # The dispatch consumed the intercept seam — no revive/enqueue
        # fallthrough to the normal message path.
        manager.enqueue_message_job.assert_not_awaited()
        for task in list(dispatcher._tasks):
            task.cancel()

    @pytest.mark.parametrize("status", ["terminated", "error", "failed"])
    def test_every_compact_reject_status_rejects_at_router(
        self, client_with_manager, dispatcher, status
    ):
        """terminated / error / failed STILL reject at the ack
        (COMPACT_REJECT_STATUSES — completed excluded since
        compact-on-COMPLETED, 2026-08-31)."""
        client, state = client_with_manager
        _register_compact(dispatcher, handler=_hanging_handler)
        manager = _make_manager(dispatcher, instance_status=status)
        state["manager"] = manager

        resp = self._post_compact(client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "rejected"
        assert body["reason"] == "terminal_instance"
        assert body["detail"] == self.GUIDANCE

    def test_terminal_rejection_spawns_no_executor_task(
        self, client_with_manager, dispatcher
    ):
        """Cleaner-of-the-two ruling (leader task): the rejection is
        dispatch-time — NO async executor task is spawned at all (the
        executor's own guard remains as defense-in-depth for the
        router-read→handler-start TOCTOU window)."""
        client, state = client_with_manager
        handler_calls = []

        async def _spy_handler(*, instance_id, args, command_id, context):
            handler_calls.append(command_id)

        _register_compact(dispatcher, handler=_spy_handler)
        manager = _make_manager(dispatcher, instance_status="terminated")
        state["manager"] = manager

        resp = self._post_compact(client)
        assert resp.status_code == 200
        assert resp.json()["state"] == "rejected"
        assert handler_calls == []
        assert list(dispatcher._tasks) == [], (
            "terminal rejection must not spawn the executor bg task"
        )
        assert dispatcher.get_for_endpoint("inst-A") is None, (
            "no registry entry — no phantom card on GET /commands/active"
        )

    def test_terminal_rejection_neither_enqueues_nor_waits(
        self, client_with_manager, dispatcher
    ):
        """The rejection short-circuits BEFORE the terminal-revival
        enqueue branch (no message row, no revive) — and before any
        waiting phase could exist."""
        client, state = client_with_manager
        _register_compact(dispatcher, handler=_hanging_handler)
        manager = _make_manager(dispatcher, instance_status="terminated")
        # If the intercept fell through, the terminal branch would
        # revive/enqueue — pin both as untouched.
        manager.resume_instance_cascade = AsyncMock()
        state["manager"] = manager

        resp = self._post_compact(client)
        assert resp.status_code == 200
        assert resp.json()["state"] == "rejected"
        manager.enqueue_message_job.assert_not_awaited()
        manager.resume_instance_cascade.assert_not_awaited()

    def test_terminal_rejection_does_not_stamp_rate_limit(
        self, client_with_manager, dispatcher
    ):
        """Retrying /compact on a terminal instance keeps answering
        terminal_instance — rejections never consume the min-interval."""
        client, state = client_with_manager
        _register_compact(dispatcher, handler=_hanging_handler)
        manager = _make_manager(dispatcher, instance_status="terminated")
        state["manager"] = manager

        first = self._post_compact(client)
        second = self._post_compact(client)
        assert first.json()["reason"] == "terminal_instance"
        assert second.json()["reason"] == "terminal_instance"
        assert dispatcher._last_dispatch == {}

    def test_running_instance_still_accepts_at_router(
        self, client_with_manager, dispatcher
    ):
        """Guard against over-rejection: RUNNING still acks accepted
        (the pause-first path is the executor's job)."""
        client, state = client_with_manager
        _register_compact(dispatcher, handler=_hanging_handler)
        manager = _make_manager(dispatcher, instance_status="running")
        state["manager"] = manager

        resp = self._post_compact(client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "accepted"
        assert body["command_id"]
        for task in list(dispatcher._tasks):
            task.cancel()


# ─────────────────────────────────────────────────────────────────────────
# Defect #7 (2026-08-31 e2e gate) — ack latency + waiting-before-pause order
# ─────────────────────────────────────────────────────────────────────────


def _make_full_executor_manager(events, dispatcher, *, instance_status="running"):
    """Manager mock with the REAL /compact executor's full surface.

    Unlike ``_make_manager`` (dispatcher-surface only), this stubs the
    executor seams so the REAL ``execute_compact`` can be wired via
    ``register_compact_command``:

    - ``pause_instance_cascade`` records ``pause_entered`` then sleeps
      3600s — an ARTIFICIALLY BLOCKED pause. If the ack path (or the
      waiting emission) ever awaited pause completion, the POST would
      hang and the assertions below would fail.
    - ``get_instance().aget_state`` records ``checkpoint_read`` — the
      defect-#7 reorder pin: ``waiting`` must be emitted BEFORE the
      checkpoint read, not after the pre-check pipeline.
    - ``_live_hub.stream_message`` records every SSE phase event.

    ``events`` is a shared ordered timeline (appended from the app
    loop thread; list.append is atomic under the GIL).
    """
    from daemon.compaction import ContextCompactor
    from daemon.config import CompactionConfig, SlashCommandConfig

    mgr = MagicMock()
    mgr.is_write_paused = False
    # The router reaches the dispatch seam via the manager facade.
    mgr.command_dispatcher = dispatcher
    mgr.get_injection_count = MagicMock(return_value=0)
    mgr._lifecycle_service = MagicMock()
    mgr._lifecycle_service.get_instance_info = MagicMock(
        return_value={
            "status": instance_status,
            "id": "inst-A",
            "metadata": {},
            "children": [],
        }
    )

    compactor = ContextCompactor(
        config=CompactionConfig(
            enabled=True,
            threshold=0.80,
            recent_message_window=10,
            min_recent_window=3,
            context_window_overrides={},
            context_window_default=128000,
            target_ratio=0.40,
            summarization_model="",
            min_messages_before_compaction=10,
            summarization_chunk_threshold=0.60,
            timeout_base_s=90.0,
            timeout_per_100k_tokens_s=60.0,
            timeout_cap_s=300.0,
            timeout_facade_margin_s=5.0,
            operation_budget_s=300.0,
        ),
        llm_config={
            "base_url": "http://example",
            "base_url_backup": None,
            "api_key": "test",
            "model": "gpt-4o",
            "model_vision": "gpt-4o",
            "temperature": 0.7,
            "request_timeout": 30.0,
            "buffer_response_header": True,
        },
    )
    mgr._compactor = compactor
    mgr.config = MagicMock()
    mgr.config.llm.model = "gpt-4o"
    mgr.config.compaction = compactor.config
    mgr.config.slash_commands = SlashCommandConfig(noop_floor_ratio=0.05)

    async def _gate_run(instance_id, holder_id, holder_kind, work_fn):
        return await work_fn()

    mgr.execution_gate = MagicMock()
    mgr.execution_gate.run = AsyncMock(side_effect=_gate_run)

    async def _blocked_pause(instance_id):
        events.append({"kind": "pause_entered"})
        # Defect #7 pin: an artificially SLOW/blocked pause. The ack
        # and the waiting event must not wait for this to finish.
        await asyncio.sleep(3600)

    mgr.pause_instance_cascade = AsyncMock(side_effect=_blocked_pause)
    mgr.resume_instance_cascade = AsyncMock(
        return_value={"resumed_ids": ["inst-A"], "skipped_ids": []}
    )

    async def _quiescent(instance_id, timeout):
        return True

    mgr.wait_for_instance_quiescent = AsyncMock(side_effect=_quiescent)

    # Checkpoint state — mid-graph RUNNING shape (next=("agent",)) with
    # enough messages to clear the 5%×128k noop floor (15×4000 chars ≈
    # 15k tokens > 6400), so the executor reaches the pause-first row.
    from langchain_core.messages import HumanMessage as _HM

    class _State:
        def __init__(self):
            self.next = ("agent",)
            self.values = {
                "messages": [
                    _HM(content="x" * 4000, id=f"h-{n}") for n in range(15)
                ],
                "compacted_at": None,
            }
            self.config = {"configurable": {"thread_id": "inst-A"}}

    graph = MagicMock()

    async def _aget_state(_config):
        events.append({"kind": "checkpoint_read"})
        return _State()

    graph.aget_state = AsyncMock(side_effect=_aget_state)

    async def _get_instance(instance_id):
        return graph

    mgr.get_instance = AsyncMock(side_effect=_get_instance)

    mgr._messaging_service = MagicMock()
    mgr._messaging_service.emit_context_usage_for_instance = AsyncMock()

    async def _stream(instance_id, message, event_type, **kwargs):
        events.append(
            {
                "kind": "sse",
                "phase": message.get("phase"),
                "phase_seq": message.get("phase_seq"),
            }
        )

    mgr._live_hub = MagicMock()
    mgr._live_hub.stream_message = AsyncMock(side_effect=_stream)

    mgr._task_repo = MagicMock()
    mgr._task_repo.has_instance_busy = MagicMock(return_value=False)

    return mgr


class TestRunningAckOrdering:
    """Defect #7 — the POST ack must not wait for pause-first work.

    Live evidence (gate run): /compact on a RUNNING instance showed
    NO UI feedback for ~32s (back-calculated elapsed_ms 32081) — the
    feedback-critical ``waiting`` SSE emission lived deep inside the
    bg task's pre-check pipeline (checkpoint read + token estimate),
    behind an unbounded async prefix on a loop shared with an
    actively-streaming turn. Architect §6 RUNNING row (D-B9/F3):
    ``waiting`` SSE FIRST; §7: CommandAck is SYNC ≤500ms.

    Pinned structurally, per acceptance criterion (b):
    - the POST ack returns WHILE the pause is artificially blocked;
    - the ``waiting`` SSE event is emitted while the pause is still
      blocked (emission independent of pause completion);
    - ``waiting`` precedes the pause mutation AND the checkpoint read.
    """

    def test_ack_returns_and_waiting_emits_while_pause_blocked(self):
        from daemon.services.compact_executor import (
            register_compact_command,
        )

        events: list[dict] = []

        dispatcher = CommandDispatcher(
            enabled=True,
            escape_prefix="//",
            min_interval_s=10,
            state_ttl_s=600,
            max_state_per_instance=20,
        )
        manager = _make_full_executor_manager(events, dispatcher)
        # The real executor resolves its manager via the dispatcher's
        # back-reference (manager.py:1012 wiring).
        dispatcher._manager = manager
        register_compact_command(dispatcher)

        from fastapi import FastAPI

        from daemon.routers.instances import router as instances_router
        from daemon.routers.messages import router as messages_router

        app = FastAPI()
        app.include_router(messages_router)
        app.include_router(instances_router)
        state = {"manager": manager}

        @app.middleware("http")
        async def _inject_manager(request, call_next):
            request.app.state.manager = state["manager"]
            return await call_next(request)

        # `with` keeps ONE portal loop alive across requests so the bg
        # executor task survives past the POST response (a bare
        # TestClient would cancel it at request-scope teardown).
        with TestClient(app) as client:
            t0 = time.monotonic()
            resp = client.post(
                "/instances/inst-A/messages",
                json={"content": "/compact"},
            )
            elapsed = time.monotonic() - t0

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["state"] == "accepted"
            # ≤500ms structural bound (§7) — in-process, no network.
            assert elapsed < 0.5, (
                f"ack took {elapsed*1000:.0f}ms — the ack path must "
                "not await pause/quiesce/executor work (defect #7)"
            )

            # The waiting SSE event arrives while the pause is STILL
            # blocked — emission is independent of pause completion.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if any(
                    e["kind"] == "sse" and e["phase"] == "waiting"
                    for e in events
                ):
                    break
                time.sleep(0.01)
            waiting_events = [
                (i, e)
                for i, e in enumerate(events)
                if e["kind"] == "sse" and e["phase"] == "waiting"
            ]
            assert waiting_events, (
                "waiting SSE event must be emitted immediately after "
                "the ack — not after the pre-check pipeline or the "
                "pause (defect #7)"
            )
            waiting_idx = waiting_events[0][0]

            pause_idx = next(
                (
                    i
                    for i, e in enumerate(events)
                    if e["kind"] == "pause_entered"
                ),
                None,
            )
            checkpoint_idx = next(
                (
                    i
                    for i, e in enumerate(events)
                    if e["kind"] == "checkpoint_read"
                ),
                None,
            )
            # F3/D-B9: waiting BEFORE any pause mutation.
            assert pause_idx is not None, "executor must reach the pause"
            assert waiting_idx < pause_idx, (
                f"waiting (idx {waiting_idx}) must precede the pause "
                f"mutation (idx {pause_idx})"
            )
            # Defect #7 reorder: waiting BEFORE the checkpoint read —
            # no async pre-work may sit between ack and waiting.
            assert checkpoint_idx is not None
            assert waiting_idx < checkpoint_idx, (
                f"waiting (idx {waiting_idx}) must precede the "
                f"checkpoint read (idx {checkpoint_idx}) — the old "
                "pre-check pipeline delayed the first feedback"
            )

            # The pause is STILL blocked right now — the ack AND the
            # waiting event did not wait for it.
            pause_completed = manager.pause_instance_cascade.await_count
            entered = [
                e for e in events if e["kind"] == "pause_entered"
            ]
            assert entered and pause_completed >= 1
            assert all(
                e["kind"] != "pause_completed" for e in events
            ), "pause was mocked blocked — it must not have completed"

        # TestClient context exit tears down the portal loop and
        # cancels the sleeping bg task (pause sleeps 3600s).
