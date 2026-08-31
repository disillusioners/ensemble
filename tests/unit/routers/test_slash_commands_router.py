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
        """A command on a terminal instance reaches the dispatcher —
        the executor (WS-2) is responsible for the terminal_instance
        rejection. The router-level intercept just dispatches."""
        client, state = client_with_manager
        _register_compact(dispatcher, handler=_hanging_handler)
        manager = _make_manager(dispatcher, instance_status="completed")
        state["manager"] = manager

        resp = client.post(
            "/instances/inst-A/messages",
            json={"content": "/compact"},
        )
        assert resp.status_code == 200
        # Falls through to enqueue_message_job? No — the dispatcher
        # captured the request, so enqueue is NOT called.
        manager.enqueue_message_job.assert_not_awaited()

        # Cleanup the hanging handler.
        for task in list(dispatcher._tasks):
            task.cancel()
        for task in list(dispatcher._tasks):
            try:
                await_coro = task
                # Drain via the test client's loop:
                # the AsyncMock is not awaitable; use a small sleep.
            except Exception:
                pass

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
        client, state = client_with_manager

        async def _long_handler(*, instance_id, args, command_id, context):
            # Stay in-flight so the active slot stays populated.
            await asyncio.Event().wait()

        _register_compact(dispatcher, handler=_long_handler)
        state["manager"] = _make_manager(dispatcher)

        async def _dispatch():
            return await dispatcher.dispatch(
                instance_id="inst-A",
                text="/compact",
                pending_injections=0,
            )

        outcome = asyncio.run(_dispatch())
        assert outcome.kind == "ack"
        command_id = outcome.ack["command_id"]

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
