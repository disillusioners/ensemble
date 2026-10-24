"""Unit tests for the remote-hands gateway service (the CENTER side).

These drive the protocol against a FAKE TRANSPORT — no socket, no
FastAPI app — because the service is deliberately importable without the
app (``daemon/services/mcps_gateway/service.py``).

The fake machine here is NOT the interop proof: the INDEPENDENTLY
transcribed fake CLIENT (written from SPEC.md alone, importing nothing
from the gateway) lives in ``tests/integration/test_mcps_gateway_ws.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import datetime, timedelta
from typing import Any

import pytest

from daemon.services.mcps_gateway.audit import MemoryAuditSink, hash_args
from daemon.services.mcps_gateway.config import GatewayConfig
from daemon.services.mcps_gateway.protocol import (
    CODE_CAPABILITY_MISSING,
    CODE_HEARTBEAT_TIMEOUT,
    CODE_MACHINE_BUSY,
    CODE_MACHINE_NOT_ENROLLED,
    CODE_PROTOCOL_VERSION_UNSUPPORTED,
    CODE_SESSION_EXPIRED,
    CODE_SESSION_NOT_FOUND,
    CODE_TOOL_TIMEOUT,
    PROTOCOL_VERSION,
    REQUIRED_CLIENT_CAPABILITIES,
    SERVER_CAPABILITIES,
)
from daemon.services.mcps_gateway.service import McpGatewayService


class FakeMachine:
    """The machine side of the socket, as the service sees it."""

    def __init__(self, service: McpGatewayService) -> None:
        self.service = service
        self.connection = None
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.close_code: int | None = None
        #: method → the result the machine answers with.
        self.answers: dict[str, Any] = {}
        #: method → (code, message) — the refusal the machine answers with.
        self.errors: dict[str, tuple[str, str]] = {}

    # ── the transport interface ─────────────────────────────────────────

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code

    # ── helpers ─────────────────────────────────────────────────────────

    def frames_of_type(self, type_: str) -> list[dict[str, Any]]:
        return [f for f in self.sent if f.get("type") == type_]

    def requests(self) -> list[dict[str, Any]]:
        return self.frames_of_type("request")

    def last_request(self) -> dict[str, Any] | None:
        reqs = self.requests()
        return reqs[-1] if reqs else None

    async def hello(
        self,
        *,
        machine_id: str = "mach_1",
        version: str = PROTOCOL_VERSION,
        capabilities: dict[str, bool] | None = None,
        request_id: str = "req-0001",
    ) -> dict[str, Any]:
        """Send the hello request and return the answer frame."""
        await self.service.handle_frame(
            self.connection,
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "type": "request",
                    "id": request_id,
                    "method": "hello",
                    "params": {
                        "protocol_version": version,
                        "client": {"name": "mcps-client", "version": "0.1.0"},
                        "machine_id": machine_id,
                        "capabilities": capabilities
                        if capabilities is not None
                        else {
                            "session": True,
                            "tools": True,
                            "notifications": False,
                            "heartbeat": True,
                            "screenshot_delta": False,
                            "multi_session": False,
                            "client_cert": False,
                        },
                    },
                }
            ),
        )
        responses = self.frames_of_type("response")
        return responses[-1] if responses else {}

    async def heartbeat(self) -> None:
        await self.service.handle_frame(
            self.connection,
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "type": "notification",
                    "method": "heartbeat",
                    "params": {},
                }
            ),
        )

    async def answer_last(
        self, result: Any = None, error: tuple[str, str] | None = None
    ) -> dict[str, Any]:
        """Answer the request the center sent last."""
        request = self.last_request()
        assert request is not None, "the center sent no request"
        payload: dict[str, Any] = {
            "v": PROTOCOL_VERSION,
            "type": "error" if error else "response",
            "id": request["id"],
        }
        if error:
            payload["error"] = {"code": error[0], "message": error[1]}
        else:
            payload["result"] = result if result is not None else {}
        await self.service.handle_frame(self.connection, json.dumps(payload))
        return payload

    async def answer_request(
        self, request_id: str, result: Any = None, error: tuple[str, str] | None = None
    ) -> None:
        """Answer ONE specific request by id."""
        payload: dict[str, Any] = {
            "v": PROTOCOL_VERSION,
            "type": "error" if error else "response",
            "id": request_id,
        }
        if error:
            payload["error"] = {"code": error[0], "message": error[1]}
        else:
            payload["result"] = result if result is not None else {}
        await self.service.handle_frame(self.connection, json.dumps(payload))

    async def answer_session_begin(
        self, session_id: str = "sess_abc", machine_id: str = "mach_1"
    ) -> None:
        await self.answer_last(
            {
                "session_id": session_id,
                "machine_id": machine_id,
                "expires_at_ms": 1792576800000,
                "tools": [{"name": "screenshot", "inputSchema": {"type": "object"}}],
            }
        )


def make_config(**overrides: Any) -> GatewayConfig:
    base: dict[str, Any] = dict(
        enabled=True,
        path="/api/mcps-gateway/ws",
        tokens=("tok-alpha", "tok-beta"),
        heartbeat_interval_ms=15_000,
        heartbeat_timeout_ms=45_000,
        client_dead_detect_ms=45_000,
        command_timeout_ms=1_000,
        audit_enabled=True,
        allowed_machines=(),
        session_ttl_ms=3_600_000,
        session_idle_timeout_ms=300_000,
    )
    base.update(overrides)
    # The server's own silence tolerance mirrors the detect window unless
    # the test sets it explicitly — they are the two ends of the same
    # liveness contract.
    if "client_dead_detect_ms" in overrides and "heartbeat_timeout_ms" not in overrides:
        base["heartbeat_timeout_ms"] = overrides["client_dead_detect_ms"]
    return GatewayConfig(**base)


def make_service(
    audit_sink: Any = None,
    *,
    pre_hello_timeout_ms: int = 30_000,
    audit_write_timeout_seconds: float = 5.0,
    **overrides: Any,
) -> McpGatewayService:
    return McpGatewayService(
        make_config(**overrides),
        audit_sink=audit_sink if audit_sink is not None else MemoryAuditSink(),
        monitor_interval_seconds=0.05,
        pre_hello_timeout_ms=pre_hello_timeout_ms,
        audit_write_timeout_seconds=audit_write_timeout_seconds,
    )


async def open_machine(
    service: McpGatewayService,
    machine: FakeMachine,
    *,
    machine_id: str = "mach_1",
) -> FakeMachine:
    """Open the connection and complete the hello exchange."""
    machine.connection = await service.open_connection(machine)
    await machine.hello(machine_id=machine_id)
    return machine


async def connect_machine(
    service: McpGatewayService,
    machine: FakeMachine,
    *,
    agent_id: str = "ari",
    machine_id: str = "mach_1",
    session_id: str = "sess_abc",
) -> dict[str, Any]:
    """Open a session and answer the ``session.begin`` request."""
    task = asyncio.create_task(
        service.connect(machine_id=machine_id, agent_id=agent_id, reason="ticket-1")
    )
    await asyncio.sleep(0.01)
    await machine.answer_session_begin(session_id=session_id, machine_id=machine_id)
    return await task


# ─────────────────────────────────────────────────────────────────────────────
# The upgrade authentication (§4.1)
# ─────────────────────────────────────────────────────────────────────────────


class TestUpgradeAuth:
    def test_a_configured_token_is_accepted(self):
        service = make_service()
        assert service.authenticate("Bearer tok-alpha") is True
        assert service.authenticate("Bearer tok-beta") is True

    def test_an_unknown_token_is_refused(self):
        service = make_service()
        assert service.authenticate("Bearer nope") is False

    def test_a_missing_or_malformed_header_is_refused(self):
        service = make_service()
        assert service.authenticate(None) is False
        assert service.authenticate("") is False
        assert service.authenticate("tok-alpha") is False
        assert service.authenticate("Basic tok-alpha") is False

    def test_no_configured_token_refuses_everybody(self):
        """Fail closed: an empty token set means nobody may enroll."""
        service = make_service(tokens=())
        assert service.authenticate("Bearer tok-alpha") is False
        assert service.authenticate("Bearer anything") is False


# ─────────────────────────────────────────────────────────────────────────────
# The hello exchange (§5)
# ─────────────────────────────────────────────────────────────────────────────


class TestHello:
    async def test_a_valid_hello_is_answered_with_the_three_heartbeat_params(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))

        responses = machine.frames_of_type("response")
        assert len(responses) == 1
        result = responses[0]["result"]
        assert responses[0]["id"] == "req-0001"
        assert responses[0]["v"] == PROTOCOL_VERSION
        assert result["protocol_version"] == PROTOCOL_VERSION
        assert result["server"]["name"]
        # ALL THREE — a partial triple is fatal on the client side.
        assert result["heartbeat_interval_ms"] == 15_000
        assert result["heartbeat_timeout_ms"] == 45_000
        assert result["client_dead_detect_ms"] == 45_000
        # The hello frames carry NO session id.
        assert "session_id" not in responses[0]

    async def test_the_capability_map_is_honest(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        caps = machine.frames_of_type("response")[0]["result"]["capabilities"]
        assert caps == SERVER_CAPABILITIES
        assert caps["notifications"] is False
        assert caps["client_cert"] is False
        assert caps["multi_session"] is False
        assert caps["session"] is True
        assert caps["tools"] is True
        assert caps["heartbeat"] is True

    async def test_an_unsupported_version_is_refused_and_the_socket_closes(self):
        service = make_service()
        machine = FakeMachine(service)
        machine.connection = await service.open_connection(machine)
        await machine.hello(version="0.9")

        errors = machine.frames_of_type("error")
        assert len(errors) == 1
        assert errors[0]["error"]["code"] == CODE_PROTOCOL_VERSION_UNSUPPORTED
        assert errors[0]["error"]["data"] == {"requested": "0.9", "supported": ["0.1"]}
        assert errors[0]["id"] == "req-0001"
        assert machine.closed is True

    async def test_a_machine_outside_the_allowlist_is_refused(self):
        service = make_service(allowed_machines=("mach_allowed",))
        machine = FakeMachine(service)
        machine.connection = await service.open_connection(machine)
        await machine.hello(machine_id="mach_other")

        errors = machine.frames_of_type("error")
        assert errors[0]["error"]["code"] == CODE_MACHINE_NOT_ENROLLED
        assert errors[0]["error"]["data"] == {"machine_id": "mach_other"}
        assert machine.closed is True

    async def test_a_missing_capability_is_refused(self):
        service = make_service()
        machine = FakeMachine(service)
        machine.connection = await service.open_connection(machine)
        await machine.hello(capabilities={"session": True, "tools": True, "heartbeat": False})

        errors = machine.frames_of_type("error")
        assert errors[0]["error"]["code"] == CODE_CAPABILITY_MISSING
        assert errors[0]["error"]["data"]["missing"] == ["heartbeat"]
        assert errors[0]["error"]["data"]["required"] == list(REQUIRED_CLIENT_CAPABILITIES)
        assert machine.closed is True

    async def test_a_client_that_advertises_no_notifications_is_still_talked_to(self):
        """The shipped client advertises ``notifications: false``."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        assert machine.frames_of_type("error") == []

    async def test_a_second_hello_is_refused(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await machine.hello(request_id="req-0002")

        errors = machine.frames_of_type("error")
        assert len(errors) == 1
        assert machine.closed is True


# ─────────────────────────────────────────────────────────────────────────────
# The heartbeat (§3.3 / §6.8)
# ─────────────────────────────────────────────────────────────────────────────


class TestHeartbeat:
    async def test_a_heartbeat_notification_refreshes_the_liveness_clock(self):
        service = make_service(client_dead_detect_ms=50)
        machine = await open_machine(service, FakeMachine(service))
        first = machine.connection.last_heartbeat
        await asyncio.sleep(0.02)
        await machine.heartbeat()
        assert machine.connection.last_heartbeat > first

    async def test_a_silent_machine_is_killed_with_a_heartbeat_timeout(self):
        service = make_service(client_dead_detect_ms=50)
        machine = await open_machine(service, FakeMachine(service))
        await asyncio.sleep(0.08)
        killed = await service.check_heartbeats()
        assert len(killed) == 1
        errors = machine.frames_of_type("error")
        assert len(errors) == 1
        assert errors[0]["error"]["code"] == CODE_HEARTBEAT_TIMEOUT
        # The refusal carries NO id (it is not the answer of a request).
        assert "id" not in errors[0]
        assert machine.closed is True

    async def test_the_kill_takes_the_session_with_it(self):
        service = make_service(client_dead_detect_ms=50)
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)
        await asyncio.sleep(0.08)
        await service.check_heartbeats()
        with pytest.raises(Exception) as excinfo:
            await service.command(agent_id="ari", tool="screenshot")
        assert excinfo.value.code == CODE_SESSION_NOT_FOUND

    async def test_only_a_heartbeat_refreshes_the_clock(self):
        """A notification of another kind does NOT keep the machine alive."""
        service = make_service(client_dead_detect_ms=50)
        machine = await open_machine(service, FakeMachine(service))
        await asyncio.sleep(0.08)
        await service.handle_frame(
            machine.connection,
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "type": "notification",
                    "method": "notifications/progress",
                }
            ),
        )
        killed = await service.check_heartbeats()
        assert len(killed) == 1


# ─────────────────────────────────────────────────────────────────────────────
# The session lifecycle (§6.4 / §8.3)
# ─────────────────────────────────────────────────────────────────────────────


class TestSessions:
    async def test_connect_returns_the_session_id_and_the_catalog(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        result = await connect_machine(service, machine)

        assert result["session_id"] == "sess_abc"
        assert result["machine_id"] == "mach_1"
        assert result["tools"] == [{"name": "screenshot", "inputSchema": {"type": "object"}}]
        # The center REQUESTS the session — the machine answers.
        request = machine.last_request()
        assert request["method"] == "session.begin"
        assert request["params"]["machine_id"] == "mach_1"
        assert request["params"]["context"]["agent_id"] == "ari"

    async def test_a_second_session_on_the_same_machine_is_machine_busy(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        # No second session.begin reaches the machine.
        before = len(machine.requests())
        with pytest.raises(Exception) as excinfo:
            await service.connect(machine_id="mach_1", agent_id="leader")
        assert excinfo.value.code == CODE_MACHINE_BUSY
        assert len(machine.requests()) == before

    async def test_an_unknown_machine_is_machine_not_enrolled(self):
        """R3: a machine that is NOT on the roster says so — the detail
        distinguishes it from a machine that is merely offline."""
        service = make_service()
        with pytest.raises(Exception) as excinfo:
            await service.connect(machine_id="mach_ghost", agent_id="ari")
        assert excinfo.value.code == CODE_MACHINE_NOT_ENROLLED
        assert excinfo.value.data["enrolled"] is False
        assert "not in the enrolled roster" in excinfo.value.message

    async def test_an_offline_machine_says_it_is_enrolled(self):
        """R3: an ENROLLED but offline machine must NOT read as
        "re-enrollment required" — it is waiting for a reconnect."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await service.close_connection(machine.connection, "the socket died")

        with pytest.raises(Exception) as excinfo:
            await service.connect(machine_id="mach_1", agent_id="ari")
        assert excinfo.value.code == CODE_MACHINE_NOT_ENROLLED
        assert excinfo.value.data["enrolled"] is True
        assert excinfo.value.data["presence"] == "offline"
        assert "offline" in excinfo.value.message
        assert "reconnect pending" in excinfo.value.message

    async def test_a_command_without_a_session_is_session_not_found(self):
        service = make_service()
        with pytest.raises(Exception) as excinfo:
            await service.command(agent_id="ari", tool="screenshot")
        assert excinfo.value.code == CODE_SESSION_NOT_FOUND

    async def test_disconnect_ends_the_session_and_frees_the_machine(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        task = asyncio.create_task(service.disconnect(agent_id="ari"))
        await asyncio.sleep(0.01)
        await machine.answer_last({"session_id": "sess_abc"})
        result = await task

        assert result["ended"] is True
        # The machine keeps the socket open.
        assert machine.closed is False
        # The machine lock is released.
        assert service.sessions.for_machine("mach_1") is None

    async def test_a_session_does_not_survive_a_dropped_socket(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        await service.close_connection(machine.connection, "the socket died")
        with pytest.raises(Exception) as excinfo:
            await service.command(agent_id="ari", tool="screenshot")
        assert excinfo.value.code == CODE_SESSION_NOT_FOUND

    async def test_an_expired_session_is_session_expired(self):
        service = make_service(session_idle_timeout_ms=0)
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        with pytest.raises(Exception) as excinfo:
            await service.command(agent_id="ari", tool="screenshot")
        assert excinfo.value.code == CODE_SESSION_EXPIRED


# ─────────────────────────────────────────────────────────────────────────────
# The close teardown ORDER (interop gate #16c — the pin)
# ─────────────────────────────────────────────────────────────────────────────


class _RecordingStore:
    """Delegates to the real store; records ``mark_offline`` calls."""

    def __init__(self, inner: Any, events: list[str]) -> None:
        self._inner = inner
        self._events = events

    def mark_offline(self, machine_id: str, connection_id: str) -> Any:
        self._events.append("store.mark_offline")
        return self._inner.mark_offline(machine_id, connection_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RecordingSessions:
    """Delegates to the real SessionManager; records the release call."""

    def __init__(self, inner: Any, events: list[str]) -> None:
        self._inner = inner
        self._events = events

    def end_for_connection(self, connection_id: str) -> list[Any]:
        self._events.append("sessions.end_for_connection")
        return self._inner.end_for_connection(connection_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RecordingMachine(FakeMachine):
    """FakeMachine whose socket close is also recorded."""

    def __init__(self, service: McpGatewayService, events: list[str]) -> None:
        super().__init__(service)
        self._events = events

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self._events.append("transport.close")
        await super().close(code=code, reason=reason)


class TestCloseConnectionOrdering:
    async def test_close_releases_the_roster_before_the_socket(self):
        """The #16c release order, PINNED as an observable sequence:
        session-manager release → ``mark_offline`` → ``transport.close``
        (the close LAST, in ``finally``).  The release order is only
        narratively stated in the service — this test turns a future
        reorder (e.g. awaiting the socket close before the §8.3 roster
        release) RED instead of silently shipping the busy-window bug.
        """
        service = make_service()
        events: list[str] = []
        service._store = _RecordingStore(service._store, events)
        service._sessions = _RecordingSessions(service._sessions, events)
        machine = await open_machine(service, _RecordingMachine(service, events))
        # The roster release is guarded by machine_id — the hello must
        # have landed for the pin to exercise the real path.
        assert machine.connection.machine_id == "mach_1"

        await service.close_connection(machine.connection, "order pin")

        assert events == [
            "sessions.end_for_connection",
            "store.mark_offline",
            "transport.close",
        ]


# ─────────────────────────────────────────────────────────────────────────────
# The command forwarding (§6.6)
# ─────────────────────────────────────────────────────────────────────────────


class TestCommand:
    async def test_the_call_is_forwarded_unwrapped_and_the_result_is_raw(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        task = asyncio.create_task(
            service.command(agent_id="ari", tool="click", arguments={"x": 412, "y": 288})
        )
        await asyncio.sleep(0.02)
        request = machine.last_request()
        assert request["method"] == "tools/call"
        # The MCP object is UNWRAPPED at the top level.
        assert request["params"] == {"name": "click", "arguments": {"x": 412, "y": 288}}
        assert request["session_id"] == "sess_abc"
        await machine.answer_last(
            {"content": [{"type": "text", "text": "clicked"}], "isError": False}
        )
        result = await task
        # The result is the RAW MCP result — no wrapping layer.
        assert result == {"content": [{"type": "text", "text": "clicked"}], "isError": False}

    async def test_the_refusal_of_the_machine_is_relayed_verbatim(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        task = asyncio.create_task(service.command(agent_id="ari", tool="nope"))
        await asyncio.sleep(0.02)
        await machine.answer_last(error=("TOOL_NOT_FOUND", "not in the catalog"))
        with pytest.raises(Exception) as excinfo:
            await task
        assert excinfo.value.code == "TOOL_NOT_FOUND"
        assert excinfo.value.message == "not in the catalog"

    async def test_a_command_that_is_not_answered_in_time_is_a_tool_timeout(self):
        service = make_service(command_timeout_ms=50)
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        with pytest.raises(Exception) as excinfo:
            await service.command(agent_id="ari", tool="click")
        assert excinfo.value.code == CODE_TOOL_TIMEOUT


# ─────────────────────────────────────────────────────────────────────────────
# The audit (§8.4)
# ─────────────────────────────────────────────────────────────────────────────


class TestAudit:
    async def test_the_arguments_are_hashed_and_never_stored(self):
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        task = asyncio.create_task(
            service.command(agent_id="ari", tool="type", arguments={"text": "hunter2"})
        )
        await asyncio.sleep(0.02)
        await machine.answer_last({"ok": True})
        await task

        assert len(audit.records) == 1
        record = audit.records[0]
        assert record.agent_id == "ari"
        assert record.machine_id == "mach_1"
        assert record.tool == "type"
        assert record.outcome == "ok"
        assert record.args_hash == hash_args({"text": "hunter2"})
        assert "hunter2" not in record.args_hash
        assert "hunter2" not in json.dumps(record.__dict__, default=str)

    async def test_the_created_at_is_the_model_default_iso_format(self):
        """FIX 5 — ONE ISO format for the audit stamp.

        The row used to be stamped ``%Y-%m-%dT%H:%M:%S.%fZ`` while the
        MODEL default is ``datetime.now(timezone.utc).isoformat()`` (the
        ``+00:00`` form): two formats for the same column. The write now
        goes through the sanctioned ``now_utc_iso()`` helper, so the
        stamped value matches the model's own default byte-for-byte.
        """
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        task = asyncio.create_task(
            service.command(agent_id="ari", tool="type", arguments={"text": "x"})
        )
        await asyncio.sleep(0.02)
        await machine.answer_last({"ok": True})
        await task

        assert len(audit.records) == 1
        created_at = audit.records[0].created_at
        assert created_at.endswith("+00:00"), (
            f"the audit stamp {created_at!r} is not the model default format"
        )
        # It parses as an aware UTC instant.
        parsed = datetime.fromisoformat(created_at)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)

    async def test_an_audit_failure_does_not_abort_the_command(self):
        class ExplodingSink:
            def record(self, record):
                raise RuntimeError("the audit table is gone")

        service = McpGatewayService(
            make_config(),
            audit_sink=ExplodingSink(),
        )
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        task = asyncio.create_task(service.command(agent_id="ari", tool="click"))
        await asyncio.sleep(0.02)
        await machine.answer_last({"ok": True})
        result = await task
        assert result == {"ok": True}
        assert service.audit_failures == 1


class TestTheAuditWriteIsOffTheEventLoop:
    async def test_a_stalled_audit_sink_cannot_stall_the_event_loop(self):
        """FIX 1 — the blocking DB write must NOT sit on the event loop.

        The DB-backed sink is SYNCHRONOUS: it opens a ``Session(engine)``
        and commits. Called inline from the async command path, a slow or
        stalled PostgreSQL froze the WHOLE daemon event loop — every
        agent, every socket, the gateway's own sweep.

        The write is bridged through ``asyncio.to_thread`` (the repo's
        convention for a blocking repository), so the loop keeps turning
        while the audit is in flight.
        """

        class StalledSink:
            """A sink whose write BLOCKS — the shape of a slow commit."""

            def __init__(self) -> None:
                self.records: list[Any] = []

            def record(self, record: Any) -> None:
                time.sleep(0.4)
                self.records.append(record)

        sink = StalledSink()
        service = make_service(audit_sink=sink)
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        ticker_task = asyncio.create_task(ticker())
        try:
            task = asyncio.create_task(service.command(agent_id="ari", tool="click"))
            await asyncio.sleep(0.02)
            await machine.answer_last({"ok": True})
            result = await task
        finally:
            ticker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker_task

        # The command still succeeded and the row was still written.
        assert result == {"ok": True}
        assert len(sink.records) == 1
        # The loop kept turning WHILE the stalled write was in flight.
        assert ticks >= 5, (
            f"the event loop turned only {ticks} times during the audit "
            "write — the blocking database write is still ON the event loop"
        )


# ─────────────────────────────────────────────────────────────────────────────
# The kill switch
# ─────────────────────────────────────────────────────────────────────────────


class TestKillSwitch:
    async def test_the_tools_refuse_while_the_transport_is_off(self):
        service = make_service(enabled=False)
        with pytest.raises(Exception) as excinfo:
            await service.connect(machine_id="mach_1", agent_id="ari")
        assert "disabled" in excinfo.value.message

    async def test_the_roster_refuses_while_the_transport_is_off(self):
        """R2: ALL FOUR operations refuse while the gateway is disabled."""
        service = make_service(enabled=False)
        with pytest.raises(Exception) as excinfo:
            service.roster()
        assert "disabled" in excinfo.value.message

    async def test_every_operation_refuses_while_the_transport_is_off(self):
        """R2: connect, command, disconnect AND roster refuse alike."""
        service = make_service(enabled=False)
        for call in (
            lambda: service.roster(),
            lambda: service.connect(machine_id="mach_1", agent_id="ari"),
            lambda: service.command(agent_id="ari", tool="screenshot"),
            lambda: service.disconnect(agent_id="ari"),
        ):
            with pytest.raises(Exception) as excinfo:
                result = call()
                if hasattr(result, "__await__"):
                    await result
            assert "disabled" in excinfo.value.message


# ─────────────────────────────────────────────────────────────────────────────
# F2 — the advertised ``heartbeat_timeout_ms`` IS the tolerance
# ─────────────────────────────────────────────────────────────────────────────


class TestAdvertisedTimeoutIsTheRealTolerance:
    async def test_the_kill_fires_at_the_advertised_timeout(self):
        """F2: no hidden grace.

        The value the center advertises as ``heartbeat_timeout_ms``
        (§3.3 — "how long the server tolerates silence before it declares
        the session dead") has to BE the tolerance. A kill at
        "timeout + grace" makes the advertisement a lie, and a client that
        trusts the advertisement must not be wrong about when the server
        will declare it dead.
        """
        timeout_ms = 300
        service = make_service(
            heartbeat_timeout_ms=timeout_ms,
            heartbeat_interval_ms=100,
            client_dead_detect_ms=timeout_ms,
        )
        machine = FakeMachine(service)
        machine.connection = await service.open_connection(machine)
        await machine.hello()

        started = time.monotonic()
        await service.ensure_monitor()
        # The machine never heartbeats.
        for _ in range(200):
            if machine.closed:
                break
            await asyncio.sleep(0.01)
        elapsed_ms = (time.monotonic() - started) * 1000

        assert machine.closed is True, "the silent machine was never killed"
        # The kill fires ON SCHEDULE — the only slack is the sweep
        # granularity, which is derived from the timeout (≤ 10 %).
        assert elapsed_ms >= timeout_ms, (
            f"the machine was declared dead after {elapsed_ms:.0f} ms — "
            f"BEFORE the advertised timeout of {timeout_ms} ms"
        )
        assert elapsed_ms <= timeout_ms * 1.25, (
            f"the machine was declared dead after {elapsed_ms:.0f} ms — the "
            f"advertised timeout is {timeout_ms} ms, so the advertisement is "
            "not the real tolerance (a hidden grace?)"
        )
        await service.close_all()

    async def test_the_advertised_value_is_the_configured_one(self):
        service = make_service(heartbeat_timeout_ms=1234)
        machine = FakeMachine(service)
        machine.connection = await service.open_connection(machine)
        await machine.hello()
        result = machine.frames_of_type("response")[0]["result"]
        assert result["heartbeat_timeout_ms"] == 1234


# ─────────────────────────────────────────────────────────────────────────────
# F3 — the session_id on the error frames
# ─────────────────────────────────────────────────────────────────────────────


class TestErrorFramesCarryTheSessionId:
    async def test_an_in_session_error_carries_the_session_id(self):
        """F3: §6.1 — the session_id is required "after a session exists"."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        await service.handle_frame(
            machine.connection,
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "type": "request",
                    "id": "req-unknown",
                    "method": "totally.unknown.method",
                    "params": {},
                }
            ),
        )
        errors = machine.frames_of_type("error")
        assert len(errors) == 1
        assert errors[0]["error"]["code"] == "INTERNAL"
        assert errors[0]["session_id"] == "sess_abc"

    async def test_an_error_for_a_dead_session_carries_no_session_id(self):
        """F3: only errors that are PRECISELY about an unknown session
        carry none."""
        service = make_service(client_dead_detect_ms=50)
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)
        await asyncio.sleep(0.08)
        await service.check_heartbeats()
        errors = machine.frames_of_type("error")
        assert len(errors) == 1
        assert errors[0]["error"]["code"] == "HEARTBEAT_TIMEOUT"
        assert "session_id" not in errors[0]

    async def test_a_pre_session_error_carries_no_session_id(self):
        """§6.1: the session_id is absent on hello and on pre-session errors."""
        service = make_service()
        machine = FakeMachine(service)
        machine.connection = await service.open_connection(machine)
        await machine.hello(version="0.9")
        errors = machine.frames_of_type("error")
        assert errors[0]["error"]["code"] == "PROTOCOL_VERSION_UNSUPPORTED"
        assert "session_id" not in errors[0]


# ─────────────────────────────────────────────────────────────────────────────
# O1 — no protocol frames outside the §6.3 exchanges
# ─────────────────────────────────────────────────────────────────────────────


class TestNoProtocolNoise:
    async def test_the_service_emits_no_uncorrelatable_frames(self):
        """O1: every frame the center writes either answers a request it
        received or initiates a real §6.3 exchange.

        The center used to emit a liveness "response" with an id nobody
        asked for — an orphan frame that is not part of the protocol.
        The liveness evidence is a WebSocket CONTROL frame now (the
        transport's job), so the protocol layer stays clean.
        """
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        # Let the housekeeping sweep run a few times.
        await service.ensure_monitor()
        await asyncio.sleep(0.4)

        client_request_ids = {"req-0001"}
        for frame in machine.sent:
            kind = frame.get("type")
            if kind == "response":
                assert frame.get("id") in client_request_ids, (
                    f"the center emitted an uncorrelatable response: {frame}"
                )
            elif kind == "request":
                assert frame.get("method") in (
                    "session.begin",
                    "session.end",
                    "tools/list",
                    "tools/call",
                ), f"the center emitted a non-§6.3 request: {frame}"
            elif kind == "error":
                # An error answers a request of the client (or is the
                # heartbeat kill, which carries no id).
                assert frame.get("id") in client_request_ids or "id" not in frame, (
                    f"the center emitted an uncorrelatable error: {frame}"
                )
            else:
                raise AssertionError(f"the center emitted an unknown frame type: {frame}")
        await service.close_all()


# ─────────────────────────────────────────────────────────────────────────────
# S1 — the audit NEVER carries peer text
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditNeverCarriesPeerText:
    async def test_quoted_arguments_never_reach_the_audit_detail(self):
        """S1: a hostile machine can quote the arguments in its error
        message; the ledger must not become the place they land."""
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        secret = "hunter2-the-password"
        task = asyncio.create_task(
            service.command(
                agent_id="ari",
                tool="type",
                arguments={"text": secret},
            )
        )
        await asyncio.sleep(0.02)
        # The machine answers with an error whose MESSAGE quotes the args.
        await machine.answer_last(
            error=("TOOL_ERROR", f"the tool refused the arguments {secret!r}")
        )
        with pytest.raises(Exception):
            await task

        assert len(audit.records) == 1
        record = audit.records[0]
        assert secret not in record.detail
        assert secret not in (record.error_code or "")
        assert secret not in json.dumps(record.__dict__, default=str)
        # The classification is FIXED and the code is the protocol code.
        assert "the machine refused the command" in record.detail
        assert record.error_code == "TOOL_ERROR"

    async def test_an_unrecognized_peer_error_code_is_not_recorded_verbatim(self):
        """S1: the error CODE arrives from the peer too."""
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        task = asyncio.create_task(service.command(agent_id="ari", tool="type"))
        await asyncio.sleep(0.02)
        await machine.answer_last(
            error=("the arguments were hunter2", "the tool refused")
        )
        with pytest.raises(Exception):
            await task

        record = audit.records[0]
        assert "hunter2" not in record.error_code
        assert record.error_code == "UNRECOGNIZED_ERROR_CODE"
        assert "hunter2" not in record.detail

    async def test_the_peer_text_stays_agent_facing(self):
        """S1: the refusal text still reaches the AGENT (only the ledger
        is scrubbed)."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        task = asyncio.create_task(service.command(agent_id="ari", tool="type"))
        await asyncio.sleep(0.02)
        await machine.answer_last(error=("TOOL_ERROR", "the machine said no"))
        with pytest.raises(Exception) as excinfo:
            await task
        assert excinfo.value.message == "the machine said no"


# ─────────────────────────────────────────────────────────────────────────────
# S2 — the machine slot is taken ATOMICALLY
# ─────────────────────────────────────────────────────────────────────────────


class TestSessionSlotIsAtomic:
    async def test_the_loser_of_a_connect_race_is_refused_with_machine_busy(self):
        """S2: two connects race through the await seam.

        The pre-flight check (``assert_machine_free``) runs BEFORE the
        round-trip to the machine, so both racers pass it. The slot is
        taken by the CENTER, atomically, in ``SessionManager.begin`` —
        the loser is refused with ``MACHINE_BUSY`` by the CENTER and
        never learns the machine's own refusal.
        """
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))

        task_a = asyncio.create_task(
            service.connect(machine_id="mach_1", agent_id="ari")
        )
        await asyncio.sleep(0.01)
        task_b = asyncio.create_task(
            service.connect(machine_id="mach_1", agent_id="leader")
        )
        await asyncio.sleep(0.01)

        requests = machine.requests()
        assert len(requests) == 2, "both racers must reach the machine"
        request_a, request_b = requests[0], requests[1]

        # A wins the slot.
        await machine.answer_request(
            request_a["id"],
            {"session_id": "sess_a", "machine_id": "mach_1", "tools": []},
        )
        await asyncio.sleep(0.01)
        # B's session.begin is answered too — but the center refuses it.
        await machine.answer_request(
            request_b["id"],
            {"session_id": "sess_b", "machine_id": "mach_1", "tools": []},
        )

        await task_a
        with pytest.raises(Exception) as excinfo:
            await task_b
        assert excinfo.value.code == "MACHINE_BUSY"
        # The center's slot still belongs to the winner.
        assert service.sessions.for_machine("mach_1").session_id == "sess_a"

    async def test_the_session_manager_never_blind_overwrites_the_slot(self):
        """S2: the manager itself refuses the overwrite."""
        from daemon.services.mcps_gateway.sessions import SessionManager

        manager = SessionManager()
        manager.begin(
            session_id="sess_1",
            machine_id="mach_1",
            agent_id="ari",
            instance_id="inst-1",
            connection_id="conn-1",
            catalog=[],
        )
        with pytest.raises(Exception) as excinfo:
            manager.begin(
                session_id="sess_2",
                machine_id="mach_1",
                agent_id="leader",
                instance_id="inst-2",
                connection_id="conn-2",
                catalog=[],
            )
        assert excinfo.value.code == "MACHINE_BUSY"
        assert manager.for_machine("mach_1").session_id == "sess_1"


# ─────────────────────────────────────────────────────────────────────────────
# S3 — a reconnect supersedes the previous socket
# ─────────────────────────────────────────────────────────────────────────────


class TestReconnectSupersedesTheOldSession:
    async def test_the_superseded_session_dies_with_the_old_socket(self):
        """S3: a reconnect must not leave the old session alive."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)
        assert service.sessions.for_machine("mach_1") is not None

        # The machine reconnects on a NEW socket.
        second = FakeMachine(service)
        second.connection = await service.open_connection(second)
        await second.hello(machine_id="mach_1")

        # The old session is gone and the old socket is closed.
        assert service.sessions.for_machine("mach_1") is None
        assert machine.closed is True
        # The new connection is the live one.
        assert service.store.get("mach_1") is not None

    async def test_the_reconnected_machine_is_not_told_machine_busy(self):
        """S3: the practical consequence — the reconnected machine can
        open a session immediately instead of waiting for the timeout."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        second = FakeMachine(service)
        second.connection = await service.open_connection(second)
        await second.hello(machine_id="mach_1")

        result = await connect_machine(
            service, second, session_id="sess_second", machine_id="mach_1"
        )
        assert result["session_id"] == "sess_second"


# ─────────────────────────────────────────────────────────────────────────────
# R1 — the audit write is bounded
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditWriteIsBounded:
    async def test_a_stalled_audit_write_does_not_hang_the_command(self):
        """R1: a stalled PostgreSQL must not hang an agent tool call."""

        class StalledSink:
            def __init__(self) -> None:
                self.started = asyncio.Event()

            def record(self, record):
                self.started.set()
                time.sleep(2.0)

        sink = StalledSink()
        service = McpGatewayService(
            make_config(),
            audit_sink=sink,
            audit_write_timeout_seconds=0.1,
        )
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)

        task = asyncio.create_task(service.command(agent_id="ari", tool="click"))
        await asyncio.sleep(0.02)
        await machine.answer_last({"ok": True})
        result = await task

        # The command result stands and the audit failure is counted.
        assert result == {"ok": True}
        assert service.audit_failures == 1


# ─────────────────────────────────────────────────────────────────────────────
# R2 — the connection bookkeeping
# ─────────────────────────────────────────────────────────────────────────────


class TestConnectionBookkeeping:
    async def test_a_connection_that_never_says_hello_is_reaped(self):
        """R2a: a token-holder that upgrades and never speaks must not
        hold a socket and a roster entry forever."""
        service = make_service(pre_hello_timeout_ms=50)
        machine = FakeMachine(service)
        machine.connection = await service.open_connection(machine)
        # No hello.
        await asyncio.sleep(0.08)
        reaped = await service.check_heartbeats()
        assert len(reaped) == 1
        assert machine.closed is True

    async def test_the_connection_ids_are_unique_after_a_close(self):
        """R2b: a counter-derived id collides after a close; a
        collision would overwrite a LIVE entry."""
        service = make_service()
        first = FakeMachine(service)
        first.connection = await service.open_connection(first)
        second = FakeMachine(service)
        second.connection = await service.open_connection(second)
        await service.close_connection(first.connection, "closed")
        third = FakeMachine(service)
        third.connection = await service.open_connection(third)

        ids = {
            first.connection.connection_id,
            second.connection.connection_id,
            third.connection.connection_id,
        }
        assert len(ids) == 3, f"the connection ids collided: {ids}"
        # The live entries are still distinct objects.
        assert (
            service._connections[second.connection.connection_id]
            is second.connection
        )
        assert (
            service._connections[third.connection.connection_id]
            is third.connection
        )


# ─────────────────────────────────────────────────────────────────────────────
# P1 / P2 — the contract details
# ─────────────────────────────────────────────────────────────────────────────


class TestContractDetails:
    async def test_session_not_found_carries_no_agent_id_as_a_session_id(self):
        """P1: ``data.session_id`` carries a SESSION id."""
        service = make_service()
        with pytest.raises(Exception) as excinfo:
            await service.command(agent_id="ari", tool="screenshot")
        assert excinfo.value.code == CODE_SESSION_NOT_FOUND
        assert "session_id" not in excinfo.value.data
        assert excinfo.value.data.get("agent_id") == "ari"

    async def test_a_dead_session_refusal_never_carries_the_agent_id(self):
        """P1: the ``session_id`` member carries a SESSION id — the agent
        id never lands in that slot."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await connect_machine(service, machine)
        await service.close_connection(machine.connection, "the socket died")
        with pytest.raises(Exception) as excinfo:
            await service.command(agent_id="ari", tool="screenshot")
        assert excinfo.value.code == CODE_SESSION_NOT_FOUND
        session_id = excinfo.value.data.get("session_id")
        assert session_id != "ari"
        if session_id is not None:
            assert session_id.startswith("sess_")

    async def test_a_frame_with_a_wrong_version_closes_the_socket(self):
        """P2: the version of EVERY inbound frame is enforced (§6.1)."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await service.handle_frame(
            machine.connection,
            json.dumps(
                {
                    "v": "0.9",
                    "type": "notification",
                    "method": "heartbeat",
                    "params": {},
                }
            ),
        )
        assert machine.closed is True

    async def test_a_frame_without_a_version_closes_the_socket(self):
        """P2: a frame that does not name its version is not a v0.1
        frame."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await service.handle_frame(
            machine.connection,
            json.dumps({"type": "notification", "method": "heartbeat"}),
        )
        assert machine.closed is True
