"""The session-expiry hardening of the remote-hands gateway (the CENTER).

This file is the FIRST coverage of ``expire_sessions`` — the sweep used
to be a two-line ``drop the session`` with no test at all, and that is
exactly how the silent sweep shipped:

* the ``session.end`` was NEVER forwarded to the machine, so the machine
  kept its local lock while the centre said the slot was free;
* the centre-side roster pointer was never cleared, so the read surface
  reported ``busy: true`` with a DEAD session id;
* an expiry produced two different error codes depending on which path
  noticed it (``SESSION_EXPIRED`` on the command path,
  ``SESSION_NOT_FOUND`` after the sweep had popped the session).

THE ANTI-TAUTOLOGY RULE
------------------------

The canonical ids carry LETTERS (``sess-alpha``, ``machine-alpha``), so
an assertion about them is not the identity function — and every
assertion here is able to FAIL when the fix is reverted: the pre-fix
code sends no ``session.end`` at all, leaves the roster pointer in
place, and raises ``SESSION_NOT_FOUND`` where the fix answers a
reason-coded envelope.

The ack deadlines are pinned SMALL (tens of milliseconds). The real
command timeout is 30 s and the whole suite is fenced at 30 s per test:
a test that waits for the real deadline would kill the pytest session.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from daemon.services.mcps_gateway.audit import MemoryAuditSink, hash_args
from daemon.services.mcps_gateway.config import GatewayConfig
from daemon.services.mcps_gateway.protocol import (
    CODE_SESSION_EXPIRED,
    CODE_SESSION_NOT_FOUND,
    CODE_TOOL_TIMEOUT,
    PROTOCOL_VERSION,
)
from daemon.services.mcps_gateway.service import McpGatewayService
from daemon.services.mcps_gateway.store import InMemoryMachineStore
from daemon.services.mcps_gateway.tombstones import (
    LEDGER_MACHINE_TIMEOUT,
    LEDGER_MACHINE_UNREACHABLE,
    LEDGER_ONLY_REASONS,
    REASON_ALREADY_ENDED,
    REASON_EXPIRED,
    REASON_MACHINE_REFUSED,
    REASON_UNKNOWN_SESSION,
    RESPONSE_REASONS,
    SessionTombstone,
)

#: The canonical ids of the fixtures — LETTER-BEARING on purpose.
AGENT_ID = "ari"
MACHINE_ID = "machine-alpha"
SESSION_ID = "sess-alpha"

#: The pinned ack deadline of the ``session.end`` forward. TINY: a
#: wedged machine must not hold a test for the real (30 s) command
#: timeout.
ACK_TIMEOUT_MS = 50


class Clock:
    """A pinnable clock — the expiry is deterministic, never a sleep."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeMachine:
    """The machine side of the socket, as the service sees it.

    The machine answers the centre's requests automatically (the pending
    future is registered BEFORE the frame leaves, so an in-line answer
    is already in place when the centre awaits it). A test that needs a
    refusal puts it in ``errors``; a test that needs SILENCE puts the
    method in ``hold``.
    """

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
        #: methods the machine does NOT answer at all.
        self.hold: set[str] = set()
        #: the session.end frames the machine received (the sweep's forward).
        self.session_ends: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)
        if payload.get("type") != "request":
            return
        method = str(payload.get("method") or "")
        if method == "session.end":
            self.session_ends.append(payload)
        if method in self.hold:
            return
        if method in self.errors:
            await self._answer(payload.get("id"), error=self.errors[method])
            return
        await self._answer(
            payload.get("id"), result=self.answers.get(method, {})
        )

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code

    async def _answer(
        self,
        request_id: Any,
        *,
        result: Any = None,
        error: tuple[str, str] | None = None,
    ) -> None:
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

    async def hello(self) -> None:
        """Complete the hello exchange (§5.1)."""
        await self.service.handle_frame(
            self.connection,
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "type": "request",
                    "id": "req-0001",
                    "method": "hello",
                    "params": {
                        "protocol_version": PROTOCOL_VERSION,
                        "client": {"name": "mcps-client", "version": "0.1.0"},
                        "machine_id": MACHINE_ID,
                        "capabilities": {
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


def make_config(**overrides: Any) -> GatewayConfig:
    base: dict[str, Any] = dict(
        enabled=True,
        path="/api/mcps-gateway/ws",
        tokens=("tok-alpha",),
        heartbeat_interval_ms=15_000,
        heartbeat_timeout_ms=45_000,
        client_dead_detect_ms=45_000,
        command_timeout_ms=30_000,
        read_timeout_ms=5_000,
        audit_enabled=True,
        allowed_machines=(),
        session_ttl_ms=1_000,
        session_idle_timeout_ms=300_000,
        # The ack deadline of the session.end forward is PINNED SMALL:
        # a wedged machine must not hold a test for 30 s.
        session_end_ack_timeout_ms=ACK_TIMEOUT_MS,
    )
    base.update(overrides)
    return GatewayConfig(**base)


def make_service(
    audit_sink: Any = None,
    *,
    clock: Any = None,
    **overrides: Any,
) -> McpGatewayService:
    return McpGatewayService(
        make_config(**overrides),
        audit_sink=audit_sink if audit_sink is not None else MemoryAuditSink(),
        clock=clock if clock is not None else Clock(),
        monitor_interval_seconds=0.05,
        audit_write_timeout_seconds=5.0,
    )


async def open_machine(
    service: McpGatewayService, machine: FakeMachine
) -> FakeMachine:
    machine.connection = await service.open_connection(machine)
    await machine.hello()
    return machine


async def open_session(
    service: McpGatewayService, machine: FakeMachine
) -> dict[str, Any]:
    """Open a session and return the connect answer."""
    machine.answers["session.begin"] = {
        "session_id": SESSION_ID,
        "machine_id": MACHINE_ID,
        "expires_at_ms": 1792576800000,
        "tools": [{"name": "screenshot", "inputSchema": {"type": "object"}}],
    }
    return await service.connect(
        machine_id=MACHINE_ID, agent_id=AGENT_ID, reason="test"
    )


async def expired_session(
    service: McpGatewayService,
    machine: FakeMachine,
    clock: Clock,
) -> None:
    """Open a session and push the clock past its TTL."""
    await open_session(service, machine)
    clock.advance(2.0)


# ─────────────────────────────────────────────────────────────────────────────
# The sweep — it must TELL THE MACHINE
# ─────────────────────────────────────────────────────────────────────────────


class TestTheSweepForwardsTheSessionEnd:
    async def test_the_sweep_forwards_the_session_end_to_the_machine(self):
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)

        expired = await service.expire_sessions()

        assert len(expired) == 1
        assert expired[0].session_id == SESSION_ID
        # The machine received the session.end — the pre-fix code sent
        # NOTHING, so the machine kept its local lock forever.
        assert len(machine.session_ends) == 1
        frame = machine.session_ends[0]
        assert frame["method"] == "session.end"
        # The frame keeps EXACTLY the params it always had (the wire
        # contract does not change in this job).
        assert frame["params"] == {"session_id": SESSION_ID}

    async def test_the_sweep_does_not_close_the_socket(self):
        """R3: the machine keeps its socket and returns to ``ready``.

        The machine releases its local lock when it acknowledges the
        end; closing the socket would kill the connection instead.
        """
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)

        await service.expire_sessions()

        assert machine.closed is False
        assert machine.close_code is None

    async def test_the_sweep_clears_the_roster_pointer(self):
        """C2: the roster must not report ``busy`` with a DEAD session."""
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)

        record = service.store.get(MACHINE_ID)
        assert record is not None
        assert record.session_id == SESSION_ID, "the fixture must have bound it"

        await service.expire_sessions()

        record = service.store.get(MACHINE_ID)
        assert record is not None
        assert record.session_id is None
        assert record.agent_id is None

    async def test_the_sweep_releases_the_whole_batch_before_any_forward(self):
        """R4: the centre-side release of the WHOLE batch comes first.

        While the sweep waits for machine A's acknowledgement, machine B
        must already be released centre-side — otherwise the roster
        reports ``busy: true`` with a DEAD session id and B has no
        tombstone.
        """
        clock = Clock()
        service = make_service(clock=clock)
        events: list[str] = []
        machine = _RecordingMachine(service, events)
        await open_machine(service, machine)
        await open_session(service, machine)
        service._store = _RecordingStore(service._store, events)
        service._tombstones = _RecordingTombstones(service._tombstones, events)
        clock.advance(2.0)

        await service.expire_sessions()

        assert "store.clear_session" in events
        assert "tombstone.record" in events
        assert "machine.session.end" in events
        # The release AND the tombstone happen BEFORE the forward.
        assert events.index("store.clear_session") < events.index("machine.session.end")
        assert events.index("tombstone.record") < events.index("machine.session.end")

    async def test_the_sweep_forward_is_bounded_by_the_ack_deadline(self):
        """R6: the forward has its OWN deadline — not the command one.

        A wedged machine must not hold the sweep for the 30 s command
        timeout.
        """
        clock = Clock()
        service = make_service(clock=clock, command_timeout_ms=30_000)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)
        machine.hold.add("session.end")  # the machine never answers

        started = time.monotonic()
        await service.expire_sessions()
        elapsed = time.monotonic() - started

        assert elapsed < 0.9, (
            f"the sweep waited {elapsed:.3f}s — the forward is not bounded by "
            f"the {ACK_TIMEOUT_MS} ms ack deadline"
        )
        # The session is still released centre-side.
        assert service.sessions.for_agent(AGENT_ID) is None


class _RecordingStore:
    """Delegates to the real store; records the roster release."""

    def __init__(self, inner: Any, events: list[str]) -> None:
        self._inner = inner
        self._events = events

    def clear_session(self, machine_id: str, session_id: str) -> Any:
        self._events.append("store.clear_session")
        return self._inner.clear_session(machine_id, session_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RecordingTombstones:
    """Delegates to the real tombstone store; records the write."""

    def __init__(self, inner: Any, events: list[str]) -> None:
        self._inner = inner
        self._events = events

    def record(self, tombstone: Any) -> None:
        self._events.append("tombstone.record")
        return self._inner.record(tombstone)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RecordingMachine(FakeMachine):
    """FakeMachine that records the session.end it receives."""

    def __init__(self, service: McpGatewayService, events: list[str]) -> None:
        super().__init__(service)
        self._events = events

    async def send_json(self, payload: dict[str, Any]) -> None:
        if payload.get("method") == "session.end":
            self._events.append("machine.session.end")
        await super().send_json(payload)


# ─────────────────────────────────────────────────────────────────────────────
# The tombstone — a LATE end is answered honestly
# ─────────────────────────────────────────────────────────────────────────────


class TestTheTombstone:
    async def test_a_late_end_after_the_sweep_answers_expired(self):
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)

        await service.expire_sessions()

        result = await service.disconnect(agent_id=AGENT_ID)
        assert result["ended"] is False
        assert result["why_not_ended"] == REASON_EXPIRED
        # The session it refers to is named — the agent owned it.
        assert result["session_id"] == SESSION_ID
        assert result["machine_id"] == MACHINE_ID
        assert result["detail"]

    async def test_a_double_end_answers_already_ended(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        first = await service.disconnect(agent_id=AGENT_ID)
        assert first["ended"] is True

        second = await service.disconnect(agent_id=AGENT_ID)
        assert second["ended"] is False
        assert second["why_not_ended"] == REASON_ALREADY_ENDED
        assert second["session_id"] == SESSION_ID

    async def test_an_unknown_session_answers_unknown_session_without_a_machine_id(
        self,
    ):
        """The answer must not become an existence oracle."""
        service = make_service()

        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["ended"] is False
        assert result["why_not_ended"] == REASON_UNKNOWN_SESSION
        assert "machine_id" not in result
        assert "session_id" not in result
        assert result["detail"]

    async def test_the_tombstone_is_bounded_by_age(self):
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)
        await service.expire_sessions()

        # Past the horizon the honest answer reverts to unknown_session.
        clock.advance(3_601.0)
        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["why_not_ended"] == REASON_UNKNOWN_SESSION
        assert "machine_id" not in result

    async def test_the_tombstone_is_bounded_by_count(self):
        from daemon.services.mcps_gateway.tombstones import (
            SessionTombstone,
            TombstoneStore,
        )

        store = TombstoneStore(max_entries=3, max_age_seconds=3_600.0)
        for index in range(5):
            store.record(
                SessionTombstone(
                    session_id=f"sess-{index}",
                    reason=REASON_EXPIRED,
                    at=float(index),
                    agent_id=AGENT_ID,
                    machine_id=MACHINE_ID,
                )
            )
        assert len(store) == 3
        # The OLDEST entries are the ones evicted.
        latest = store.latest_for_agent(AGENT_ID, now=10.0)
        assert latest is not None
        assert latest.session_id == "sess-4"


# ─────────────────────────────────────────────────────────────────────────────
# The end contract — every outcome, one shape
# ─────────────────────────────────────────────────────────────────────────────


class TestTheEndContract:
    async def test_an_acknowledged_end_reports_the_verbatim_result(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        machine.answers["session.end"] = {"session_id": SESSION_ID, "released": True}
        await open_session(service, machine)

        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["ended"] is True
        assert result["machine_notified"] is True
        assert result["session_id"] == SESSION_ID
        assert result["machine_id"] == MACHINE_ID
        # The machine's VERBATIM acknowledgement.
        assert result["result"] == {"session_id": SESSION_ID, "released": True}
        assert "why_not_ended" not in result
        # The centre-side state is released.
        assert service.sessions.for_agent(AGENT_ID) is None
        assert service.store.get(MACHINE_ID).session_id is None

    async def test_a_machine_session_not_found_answers_ended_and_notified(self):
        """R1(b): the machine no longer held the session."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        machine.errors["session.end"] = (
            CODE_SESSION_NOT_FOUND,
            "there is no live session",
        )
        await open_session(service, machine)

        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["ended"] is True
        assert result["machine_notified"] is True
        assert "machine_session_already_gone" in result["detail"]
        # The machine did not acknowledge — there is no result payload.
        assert "result" not in result
        # There is no machine lock left to release.
        assert service.sessions.for_agent(AGENT_ID) is None

    async def test_a_machine_refusal_answers_machine_refused_and_the_session_survives(
        self,
    ):
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        machine.errors["session.end"] = ("TOOL_ERROR", "the machine said no")
        await open_session(service, machine)

        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["ended"] is False
        assert result["why_not_ended"] == REASON_MACHINE_REFUSED
        assert "TOOL_ERROR" in result["detail"]
        assert result["machine_notified"] is True
        # The session SURVIVES the refusal.
        assert service.sessions.for_agent(AGENT_ID) is not None
        assert service.sessions.for_machine(MACHINE_ID) is not None
        assert service.store.get(MACHINE_ID).session_id == SESSION_ID
        # The ledger records the REFUSAL — that is what answers "why did
        # this session not end?".
        assert len(audit.records) == 1
        assert audit.records[0].tool == "session.end"
        assert audit.records[0].error_code == REASON_MACHINE_REFUSED
        assert audit.records[0].outcome == "error"

    async def test_a_relayed_tool_timeout_is_a_refusal_and_the_session_survives(
        self,
    ):
        """R6/C7: a RELAYED timeout is NOT the centre's own deadline.

        The discrimination is by TYPE, never by the code string: a
        relayed ``TOOL_TIMEOUT`` is a plain refusal, so the session
        SURVIVES it.
        """
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        machine.errors["session.end"] = (CODE_TOOL_TIMEOUT, "the tool timed out")
        await open_session(service, machine)

        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["ended"] is False
        assert result["why_not_ended"] == REASON_MACHINE_REFUSED
        assert CODE_TOOL_TIMEOUT in result["detail"]
        assert service.sessions.for_agent(AGENT_ID) is not None

    async def test_an_unreachable_machine_answers_ended_without_notification(self):
        """R1(d): the centre released the session AUTHORITATIVELY."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)
        # The socket is gone while the session is still live.
        machine.connection.closed = True
        service._connections.pop(machine.connection.connection_id)

        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["ended"] is True
        assert result["machine_notified"] is False
        assert result["detail"]
        # The centre-side state is released.
        assert service.sessions.for_agent(AGENT_ID) is None
        assert service.store.get(MACHINE_ID).session_id is None

    async def test_the_centre_ack_deadline_answers_ended_without_notification(self):
        """R1(e): the CENTRE's own deadline fired."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)
        machine.hold.add("session.end")  # the machine never answers

        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["ended"] is True
        assert result["machine_notified"] is False
        # The deadline in ms is stated in the detail.
        assert str(ACK_TIMEOUT_MS) in result["detail"]
        # The session is released.
        assert service.sessions.for_agent(AGENT_ID) is None
        assert service.store.get(MACHINE_ID).session_id is None

    async def test_an_unended_response_always_carries_a_reason_and_a_detail(self):
        """The banned-shape guard: ``why_not_ended`` IFF ``ended`` is False."""
        scenarios = ("unknown", "expired", "refused")

        for scenario in scenarios:
            clock = Clock()
            service = make_service(clock=clock)
            machine = await open_machine(service, FakeMachine(service))
            if scenario == "unknown":
                pass
            elif scenario == "expired":
                await expired_session(service, machine, clock)
                await service.expire_sessions()
            else:
                machine.errors["session.end"] = ("TOOL_ERROR", "no")
                await open_session(service, machine)

            result = await service.disconnect(agent_id=AGENT_ID)

            assert result["ended"] is False, scenario
            assert result.get("why_not_ended"), scenario
            assert result.get("detail"), scenario
            assert result["why_not_ended"] in RESPONSE_REASONS, scenario
            assert result["machine_notified"] is False or scenario == "refused"

    async def test_a_response_never_carries_a_ledger_only_token(self):
        """The two DISJOINT reason sets — the invariant, end to end."""
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)
        await service.expire_sessions()

        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["why_not_ended"] not in LEDGER_ONLY_REASONS
        assert LEDGER_MACHINE_UNREACHABLE not in RESPONSE_REASONS
        assert LEDGER_MACHINE_TIMEOUT not in RESPONSE_REASONS


# ─────────────────────────────────────────────────────────────────────────────
# The SINGLE OWNER of the expiry reap
# ─────────────────────────────────────────────────────────────────────────────


class TestTheSweepIsTheSoleOwnerOfTheReap:
    async def test_the_command_path_does_not_release_the_session(self):
        """R2: the command path REFUSES, the sweep REAPS."""
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)

        with pytest.raises(Exception) as excinfo:
            await service.command(agent_id=AGENT_ID, tool="screenshot")

        assert excinfo.value.code == CODE_SESSION_EXPIRED
        # The session is STILL there — the sweep owns the reap. A release
        # here would hide the session from the sweep, and the machine
        # would keep its lock while the roster said the slot was free.
        assert service.sessions.for_agent(AGENT_ID) is not None
        assert service.store.get(MACHINE_ID).session_id == SESSION_ID

    async def test_the_sweep_still_reaps_what_the_command_path_refused(self):
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)

        with pytest.raises(Exception):
            await service.command(agent_id=AGENT_ID, tool="screenshot")

        await service.expire_sessions()

        assert service.sessions.for_agent(AGENT_ID) is None
        assert len(machine.session_ends) == 1

    async def test_a_command_after_the_sweep_answers_session_expired(self):
        """ADD-1: the SAME expiry must not produce two error codes."""
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)
        await service.expire_sessions()

        with pytest.raises(Exception) as excinfo:
            await service.command(agent_id=AGENT_ID, tool="screenshot")

        assert excinfo.value.code == CODE_SESSION_EXPIRED
        assert excinfo.value.data.get("session_id") == SESSION_ID

    async def test_a_command_without_any_session_answers_session_not_found(self):
        service = make_service()

        with pytest.raises(Exception) as excinfo:
            await service.command(agent_id=AGENT_ID, tool="screenshot")

        assert excinfo.value.code == CODE_SESSION_NOT_FOUND
        assert excinfo.value.data.get("agent_id") == AGENT_ID
        assert "session_id" not in excinfo.value.data


# ─────────────────────────────────────────────────────────────────────────────
# The audit ledger — the answer to "why did this session not end?"
# ─────────────────────────────────────────────────────────────────────────────


class TestTheSessionAuditRows:
    async def test_a_reaped_session_leaves_a_session_expire_row(self):
        audit = MemoryAuditSink()
        clock = Clock()
        service = make_service(audit_sink=audit, clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)

        await service.expire_sessions()

        assert len(audit.records) == 1
        record = audit.records[0]
        assert record.tool == "session.expire"
        assert record.outcome == "ok"
        # The expiry cause is storable (the vocabulary extension).
        assert record.error_code == "ttl"
        assert record.machine_id == MACHINE_ID
        assert record.session_id == SESSION_ID
        assert record.agent_id == AGENT_ID
        # The args are HASHED, never stored (§8.4).
        assert record.args_hash == hash_args({"session_id": SESSION_ID})
        assert SESSION_ID not in record.args_hash

    async def test_an_idle_expiry_records_the_idle_cause(self):
        audit = MemoryAuditSink()
        clock = Clock()
        service = make_service(
            audit_sink=audit,
            clock=clock,
            session_ttl_ms=3_600_000,
            session_idle_timeout_ms=100,
        )
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)
        clock.advance(0.2)

        await service.expire_sessions()

        assert len(audit.records) == 1
        assert audit.records[0].error_code == "idle"
        assert audit.records[0].outcome == "ok"

    async def test_an_unreachable_machine_leaves_the_machine_unreachable_ledger_row(
        self,
    ):
        audit = MemoryAuditSink()
        clock = Clock()
        service = make_service(audit_sink=audit, clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)
        # The socket is gone while the session is still live.
        machine.connection.closed = True
        service._connections.pop(machine.connection.connection_id)

        await service.expire_sessions()

        assert len(audit.records) == 1
        record = audit.records[0]
        assert record.tool == "session.expire"
        assert record.error_code == LEDGER_MACHINE_UNREACHABLE
        assert record.outcome == "error"
        # The detail is a FIXED classification — never the peer's text.
        assert "unreachable" in record.detail

    async def test_a_wedged_machine_leaves_the_machine_timeout_ledger_row(self):
        audit = MemoryAuditSink()
        clock = Clock()
        service = make_service(audit_sink=audit, clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)
        machine.hold.add("session.end")

        await service.expire_sessions()

        assert len(audit.records) == 1
        record = audit.records[0]
        assert record.tool == "session.expire"
        assert record.error_code == LEDGER_MACHINE_TIMEOUT
        assert record.outcome == "timeout"

    async def test_an_explicit_end_leaves_a_session_end_row(self):
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        await service.disconnect(agent_id=AGENT_ID)

        assert len(audit.records) == 1
        record = audit.records[0]
        assert record.tool == "session.end"
        assert record.outcome == "ok"
        assert record.machine_id == MACHINE_ID
        assert record.session_id == SESSION_ID
        assert record.args_hash == hash_args({"session_id": SESSION_ID})

    async def test_an_unknown_end_leaves_no_row(self):
        """There is no machine to attribute a row to."""
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)

        await service.disconnect(agent_id=AGENT_ID)

        assert audit.records == []

    async def test_an_audit_failure_does_not_abort_the_end(self):
        class ExplodingSink:
            def record(self, record: Any) -> None:
                raise RuntimeError("the audit table is gone")

        service = make_service(audit_sink=ExplodingSink())
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["ended"] is True
        assert service.audit_failures == 1


# ─────────────────────────────────────────────────────────────────────────────
# The roster release guard (the regression pin)
# ─────────────────────────────────────────────────────────────────────────────


class TestTheRosterReleaseIsGuarded:
    async def test_a_stale_session_id_does_not_clear_a_new_session(self):
        """R5: the roster release is session-id-guarded.

        A machine that re-helloed and bound a NEW session must KEEP it:
        the sweep of the OLD session must not release the NEW one.
        """
        store = InMemoryMachineStore()
        store.upsert(MACHINE_ID, "conn-alpha", 0.0)
        store.bind_session(MACHINE_ID, SESSION_ID, AGENT_ID)

        store.clear_session(MACHINE_ID, "sess-stale")

        record = store.get(MACHINE_ID)
        assert record is not None
        assert record.session_id == SESSION_ID
        assert record.agent_id == AGENT_ID

    async def test_the_sweep_does_not_clear_a_newer_session(self):
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)
        # The machine re-helloed and bound a NEW session in the meantime.
        service.store.bind_session(MACHINE_ID, "sess-bravo", AGENT_ID)

        await service.expire_sessions()

        record = service.store.get(MACHINE_ID)
        assert record is not None
        assert record.session_id == "sess-bravo"


# ─────────────────────────────────────────────────────────────────────────────
# The tool layer — the envelope reaches the agent UNWRAPPED
# ─────────────────────────────────────────────────────────────────────────────


class TestTheDisconnectTool:
    async def test_the_tool_returns_the_reason_coded_envelope(self):
        """The tool never wraps a session-state outcome in an error."""
        from daemon.tools.mcp_rpc_tools import create_mcp_rpc_tools

        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)
        tools = {
            tool.name: tool
            for tool in create_mcp_rpc_tools(
                service, current_instance_id="inst-alpha", agent_id=AGENT_ID
            )
        }

        result = await tools["mcp_rpc_disconnect"].ainvoke({})

        assert "error" not in result
        assert result["ended"] is True
        assert result["machine_notified"] is True
        assert result["session_id"] == SESSION_ID
        assert result["detail"]

    async def test_the_tool_stays_agent_addressed(self):
        """CORRECT-4: the tool takes NO argument — it is agent-scoped."""
        from daemon.tools.mcp_rpc_tools import create_mcp_rpc_tools

        service = make_service()
        tools = {
            tool.name: tool
            for tool in create_mcp_rpc_tools(
                service, current_instance_id="inst-alpha", agent_id=AGENT_ID
            )
        }
        schema = tools["mcp_rpc_disconnect"].args_schema
        assert schema is not None
        assert list(schema.model_fields) == []

    async def test_the_tool_still_refuses_while_the_transport_is_off(self):
        """The kill switch is the ONLY thing that raises."""
        from daemon.tools.mcp_rpc_tools import create_mcp_rpc_tools

        service = make_service(enabled=False)
        tools = {
            tool.name: tool
            for tool in create_mcp_rpc_tools(
                service, current_instance_id="inst-alpha", agent_id=AGENT_ID
            )
        }

        result = await tools["mcp_rpc_disconnect"].ainvoke({})

        assert result["error"]["code"] == "INTERNAL"
        assert "disabled" in result["error"]["message"]


# ─────────────────────────────────────────────────────────────────────────────
# The vocabulary itself
# ─────────────────────────────────────────────────────────────────────────────

class TestTheReasonVocabulary:
    def test_the_two_reason_sets_are_disjoint(self):
        assert RESPONSE_REASONS.isdisjoint(LEDGER_ONLY_REASONS)
        assert LEDGER_MACHINE_UNREACHABLE not in RESPONSE_REASONS
        assert LEDGER_MACHINE_TIMEOUT not in RESPONSE_REASONS

    def test_the_response_vocabulary_is_exactly_the_four_reasons(self):
        assert RESPONSE_REASONS == frozenset(
            {"expired", "already_ended", "unknown_session", "machine_refused"}
        )

    async def test_the_runtime_guard_keeps_a_ledger_only_token_out_of_a_response(
        self,
    ):
        """The RUNTIME half of the invariant — the guard is EXERCISED.

        The two tests above are a statement about the CONSTANTS. The
        GUARD (``McpGatewayService._response_reason``) is what enforces
        the invariant at the moment a reason is put into a response, and
        NOTHING in the current write paths can produce a ledger-only
        tombstone — so the guard is defence-in-depth, and without this
        test it could be deleted or reordered with EVERY test still
        green.

        A ledger-only token is therefore injected straight into the
        tombstone store: the response must fall back to the neutral
        reason and must never carry the token.
        """
        clock = Clock()
        service = make_service(clock=clock)
        service.tombstones.record(
            SessionTombstone(
                session_id=SESSION_ID,
                reason=LEDGER_MACHINE_UNREACHABLE,
                at=clock.now,
                agent_id=AGENT_ID,
                instance_id="",
                machine_id=MACHINE_ID,
                cause="",
            )
        )

        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["ended"] is False
        # The guard's fallback: the neutral reason, never the token.
        assert result["why_not_ended"] == REASON_UNKNOWN_SESSION
        assert result["why_not_ended"] not in LEDGER_ONLY_REASONS
        assert LEDGER_MACHINE_UNREACHABLE not in json.dumps(result, default=str)
