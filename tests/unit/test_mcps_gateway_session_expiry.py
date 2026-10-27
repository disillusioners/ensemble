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
    PROTOCOL_VERSION_V02,
    GatewayAckTimeout,
    GatewayProtocolError,
)
from daemon.services.mcps_gateway.service import (
    AUDIT_ALLOWED_ERROR_CODES,
    AUDIT_ALLOWED_SESSION_ERROR_CODES,
    McpGatewayService,
)
from daemon.services.mcps_gateway.store import InMemoryMachineStore
from daemon.services.mcps_gateway.tombstones import (
    DEFAULT_TOMBSTONE_MAX_AGE_SECONDS,
    DEFAULT_TOMBSTONE_MAX_ENTRIES,
    LEDGER_MACHINE_TIMEOUT,
    LEDGER_MACHINE_UNREACHABLE,
    LEDGER_ONLY_REASONS,
    REASON_ALREADY_ENDED,
    REASON_EXPIRED,
    REASON_MACHINE_REFUSED,
    REASON_REAPED_BY_CENTER,
    REASON_SUPERSEDED,
    REASON_UNKNOWN_SESSION,
    RESPONSE_REASONS,
    SessionTombstone,
    TombstoneStore,
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

    def __init__(
        self,
        service: McpGatewayService,
        *,
        machine_id: str = MACHINE_ID,
        session_id: str = SESSION_ID,
        version: str = PROTOCOL_VERSION,
    ) -> None:
        self.service = service
        self.machine_id = machine_id
        self.session_id = session_id
        #: The protocol version the machine negotiates (§5.2). The
        #: default is the historical ``"0.1"`` — the sweep's forward
        #: params are byte-identical on that version.
        self.version = version
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
        #: methods whose SEND raises — the socket dies mid-flight.
        self.raise_on: set[str] = set()
        #: methods whose send CLOSES the connection first — the socket
        #: dies DURING the ack window (the centre's pending future is
        #: failed with a synthetic refusal).
        self.close_on_send: set[str] = set()
        #: the session.end frames the machine received (the sweep's forward).
        self.session_ends: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)
        if payload.get("type") != "request":
            return
        method = str(payload.get("method") or "")
        if method == "session.end":
            self.session_ends.append(payload)
        if method in self.raise_on:
            raise ConnectionResetError("the socket died")
        if method in self.close_on_send:
            await self.service.close_connection(self.connection, "the socket died")
            return
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
            "v": self.version,
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
                    "v": self.version,
                    "type": "request",
                    "id": "req-0001",
                    "method": "hello",
                    "params": {
                        "protocol_version": self.version,
                        "client": {"name": "mcps-client", "version": "0.1.0"},
                        "machine_id": self.machine_id,
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
    service: McpGatewayService,
    machine: FakeMachine,
    *,
    agent_id: str = AGENT_ID,
) -> dict[str, Any]:
    """Open a session and return the connect answer."""
    machine.answers["session.begin"] = {
        "session_id": machine.session_id,
        "machine_id": machine.machine_id,
        "expires_at_ms": 1792576800000,
        "tools": [{"name": "screenshot", "inputSchema": {"type": "object"}}],
    }
    return await service.connect(
        machine_id=machine.machine_id, agent_id=agent_id, reason="test"
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
        # The frame keeps EXACTLY the params it always had on a v0.1
        # connection (the wire contract does not change for it — the
        # ``reason`` member of §6.9.2 is VERSION-SCOPED to v0.2).
        assert frame["params"] == {"session_id": SESSION_ID}
        assert frame["v"] == PROTOCOL_VERSION

    async def test_the_sweep_tells_the_v02_machine_why_it_was_reaped(self):
        """§6.9.2 group (ii): the v0.2 sweep forward carries the reason.

        The v0.2 connection's ``session.end`` params gain the ``reason``
        member — ``reaped_by_center`` — and the machine's end-ack echoes
        it. A v0.1 connection keeps the historical params EXACTLY.
        """
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(
            service, FakeMachine(service, version=PROTOCOL_VERSION_V02)
        )
        await expired_session(service, machine, clock)

        expired = await service.expire_sessions()

        assert len(expired) == 1
        assert len(machine.session_ends) == 1
        frame = machine.session_ends[0]
        assert frame["params"] == {
            "session_id": SESSION_ID,
            "reason": REASON_REAPED_BY_CENTER,
        }
        assert frame["v"] == PROTOCOL_VERSION_V02

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

    def __init__(
        self,
        service: McpGatewayService,
        events: list[str],
        *,
        machine_id: str = MACHINE_ID,
        session_id: str = SESSION_ID,
    ) -> None:
        super().__init__(service, machine_id=machine_id, session_id=session_id)
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

    async def test_the_late_end_says_whether_the_machine_was_notified(self):
        """H-8: the flag is TRUTHFUL, not a hard-coded ``False``.

        The session was ended and the machine ACKNOWLEDGED it, so the
        machine WAS notified — the late end says so.
        """
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        first = await service.disconnect(agent_id=AGENT_ID)
        assert first["machine_notified"] is True

        second = await service.disconnect(agent_id=AGENT_ID)

        assert second["ended"] is False
        assert second["why_not_ended"] == REASON_ALREADY_ENDED
        assert second["machine_notified"] is True

    async def test_the_late_end_says_the_machine_was_not_notified(self):
        """The converse: a centre-side release notified NOBODY."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)
        # The socket is gone while the session is still live.
        machine.connection.closed = True
        service._connections.pop(machine.connection.connection_id)

        first = await service.disconnect(agent_id=AGENT_ID)
        assert first["machine_notified"] is False

        second = await service.disconnect(agent_id=AGENT_ID)

        assert second["why_not_ended"] == REASON_ALREADY_ENDED
        assert second["machine_notified"] is False

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

    def test_the_tombstone_bounds_are_the_exported_constants(self):
        """T-6: the bounds are named constants, not bare literals."""
        from daemon.services.mcps_gateway.tombstones import TombstoneStore

        store = TombstoneStore()
        assert store._max_entries == DEFAULT_TOMBSTONE_MAX_ENTRIES
        assert store._max_age_seconds == DEFAULT_TOMBSTONE_MAX_AGE_SECONDS
        assert DEFAULT_TOMBSTONE_MAX_ENTRIES == 512
        assert DEFAULT_TOMBSTONE_MAX_AGE_SECONDS == 3_600.0

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
        store = TombstoneStore(
            max_entries=3,
            max_age_seconds=DEFAULT_TOMBSTONE_MAX_AGE_SECONDS,
        )
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
            # Whether the machine was told WHEN THE SESSION ENDED — the
            # tombstone knows it (the explicit end records the ack, the
            # sweep records the forward's outcome).
            expected_notified = {
                "unknown": False,  # no session ever existed
                "expired": True,  # the sweep forwarded the end; it acked
                "refused": True,  # the machine answered (with a refusal)
            }[scenario]
            assert result["machine_notified"] is expected_notified, scenario

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
# The Direction-B envelope — its EXACT KEY SET
#
# The per-key assertions above cannot catch a STRAY member: an envelope
# that grew a member the agent-facing contract does not define would
# still pass every per-key check. The contract is
#
#   {session_id?, machine_id?, ended, detail, machine_notified,
#    why_not_ended?, result?}
#
# — the optional members are present IFF their condition holds, and
# NOTHING else is. The key set is pinned here, per outcome.
# ─────────────────────────────────────────────────────────────────────────────


class TestTheDirectionBEnvelopeKeySet:
    #: The members every Direction-B envelope carries.
    BASE = {"ended", "detail", "machine_notified"}

    async def test_the_acknowledged_envelope_carries_exactly_its_members(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        result = await service.disconnect(agent_id=AGENT_ID)

        assert set(result) == self.BASE | {
            "session_id",
            "machine_id",
            "result",
        }
        assert "why_not_ended" not in result

    async def test_the_authoritative_release_carries_exactly_its_members(self):
        """The centre released the session: no machine ack, no reason."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)
        machine.hold.add("session.end")  # the machine never answers

        result = await service.disconnect(agent_id=AGENT_ID)

        assert set(result) == self.BASE | {"session_id", "machine_id"}
        assert "why_not_ended" not in result
        assert "result" not in result

    async def test_the_unknown_session_envelope_carries_exactly_its_members(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))

        result = await service.disconnect(agent_id=AGENT_ID)

        assert set(result) == self.BASE | {"why_not_ended"}
        assert "session_id" not in result
        assert "machine_id" not in result

    async def test_the_expired_envelope_carries_exactly_its_members(self):
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)
        await service.expire_sessions()

        result = await service.disconnect(agent_id=AGENT_ID)

        assert set(result) == self.BASE | {
            "session_id",
            "machine_id",
            "why_not_ended",
        }
        assert "result" not in result


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

    async def test_the_ttl_ceiling_wins_the_tie_with_the_idle_timeout(self):
        """T-8(a): when BOTH bounds passed, the TTL is the cause.

        The tie-break is stated in ``_expiry_cause`` and must mirror
        ``SessionManager.expire_due``'s predicate — this pins it.
        """
        audit = MemoryAuditSink()
        clock = Clock()
        service = make_service(
            audit_sink=audit,
            clock=clock,
            session_ttl_ms=1_000,
            session_idle_timeout_ms=100,
        )
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)
        clock.advance(2.0)  # both the TTL (1 s) and the idle timeout (0.1 s) passed

        await service.expire_sessions()

        assert len(audit.records) == 1
        assert audit.records[0].error_code == "ttl"

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

    async def test_a_machine_that_refuses_the_expiry_keeps_the_refusal_in_the_ledger(
        self,
    ):
        """The sweep's REFUSAL must not be swallowed by the B-4 re-check.

        In the sweep the session was ALREADY released centre-side (PASS
        1), so a "the session is gone" test would be true for EVERY
        forward and would turn a genuine refusal into a false
        ``machine_unreachable``.
        """
        audit = MemoryAuditSink()
        clock = Clock()
        service = make_service(audit_sink=audit, clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)
        machine.errors["session.end"] = ("TOOL_ERROR", "the machine said no")

        await service.expire_sessions()

        assert len(audit.records) == 1
        record = audit.records[0]
        assert record.tool == "session.expire"
        assert record.error_code == REASON_MACHINE_REFUSED
        assert record.outcome == "error"

    async def test_a_machine_that_never_had_the_session_is_not_a_refusal(self):
        """T-7: the ``FORWARD_ALREADY_GONE`` arm.

        The machine answers ``SESSION_NOT_FOUND`` — it never held the
        session, so there is no local lock left to release. That is NOT
        a refusal: the ledger records the expiry CAUSE and a successful
        outcome.
        """
        audit = MemoryAuditSink()
        clock = Clock()
        service = make_service(audit_sink=audit, clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)
        machine.errors["session.end"] = (CODE_SESSION_NOT_FOUND, "no such session")

        await service.expire_sessions()

        assert len(audit.records) == 1
        record = audit.records[0]
        assert record.tool == "session.expire"
        assert record.error_code == "ttl"
        assert record.outcome == "ok"

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

    def test_the_command_audit_set_carries_none_of_the_centre_only_tokens(self):
        """The COMMAND rows' vocabulary must not carry a centre token.

        Direct constant-to-constant assertions — the centre-only tokens
        are spelled out as literals here, so the assertion does not
        restate the definition of either set (a helper that rebuilt
        the sets would prove nothing).
        """
        centre_only = {
            "machine_unreachable",
            "machine_timeout",
            "idle",
            "ttl",
            "expired",
            "already_ended",
            "unknown_session",
            "machine_refused",
        }
        # The command set: NONE of the centre-only tokens — a hostile
        # machine must not be able to forge one into a command row.
        assert AUDIT_ALLOWED_ERROR_CODES.isdisjoint(centre_only)
        # The session set: a SUPERSET of the command set, and it carries
        # the centre-only tokens (they are the session ledger's own).
        assert AUDIT_ALLOWED_SESSION_ERROR_CODES >= AUDIT_ALLOWED_ERROR_CODES
        assert centre_only <= AUDIT_ALLOWED_SESSION_ERROR_CODES

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


# ─────────────────────────────────────────────────────────────────────────────
# The EXPLICIT-END arms — the same hardening the sweep twin carries
# ─────────────────────────────────────────────────────────────────────────────


class TestTheExplicitEndArms:
    """The explicit end must be as hardened as the sweep's forward.

    The sweep twin (``_forward_session_end_outcome``) catches a broad
    exception and records the machine as UNREACHABLE. The explicit end
    did not: a socket that died between the liveness check and the send
    escaped as an untyped transport exception, the TOOL raised, and the
    agent got a bare ``{"error": {...}}`` while the session stayed live
    centre-side — verbatim the bare-error failure mode this branch
    exists to remove.
    """

    async def test_a_socket_that_dies_during_the_end_answers_the_unreachable_envelope(
        self,
    ):
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)
        machine.raise_on.add("session.end")

        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["ended"] is True
        assert result["machine_notified"] is False
        assert result["detail"]
        # The centre released the session AUTHORITATIVELY — it is gone.
        assert service.sessions.for_agent(AGENT_ID) is None
        assert service.store.get(MACHINE_ID).session_id is None
        # The ledger records WHY (the ledger-only token).
        assert len(audit.records) == 1
        assert audit.records[0].error_code == LEDGER_MACHINE_UNREACHABLE
        assert audit.records[0].tool == "session.end"

    async def test_a_connection_that_dies_in_the_ack_window_is_not_a_refusal(self):
        """B-4: the machine answered NOTHING — that is not a refusal.

        ``close_connection`` fails the pending futures with a synthetic
        INTERNAL refusal, so the refusal arm would claim
        ``machine_refused`` (whose documented meaning is "the session
        SURVIVES") — but the session died WITH the connection. The
        tombstone answers instead.
        """
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)
        machine.close_on_send.add("session.end")

        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["why_not_ended"] != REASON_MACHINE_REFUSED
        assert result["why_not_ended"] == REASON_ALREADY_ENDED
        assert result["machine_notified"] is False
        # The session is gone (it died with the connection).
        assert service.sessions.for_agent(AGENT_ID) is None

    async def test_the_sweep_records_an_unreachable_machine_when_the_socket_dies(
        self,
    ):
        """B-4's sweep twin: the ledger must not record a false refusal."""
        audit = MemoryAuditSink()
        clock = Clock()
        service = make_service(audit_sink=audit, clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)
        machine.close_on_send.add("session.end")

        await service.expire_sessions()

        assert len(audit.records) == 1
        record = audit.records[0]
        assert record.tool == "session.expire"
        assert record.error_code == LEDGER_MACHINE_UNREACHABLE
        assert record.outcome == "error"

    async def test_the_ack_timeout_type_is_session_end_only(self):
        """T-2: the TYPE carries a session-end contract, so only the
        session.end path may raise it.

        The command path may NOT release the session authoritatively, so
        it must not inherit a type whose contract says it may.
        """
        # The command's own deadline is PINNED SMALL: a test must never
        # wait the real 30 s command timeout (the suite is fenced at 30 s
        # per test).
        service = make_service(command_timeout_ms=100)
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)
        machine.hold.add("tools/call")

        with pytest.raises(GatewayProtocolError) as excinfo:
            await service.command(agent_id=AGENT_ID, tool="screenshot")

        assert excinfo.value.code == CODE_TOOL_TIMEOUT
        # NOT the session-end subtype.
        assert not isinstance(excinfo.value, GatewayAckTimeout)

    async def test_a_peer_cannot_smuggle_a_ledger_only_token_into_the_response(self):
        """B-3: the code slot of the peer's frame is PEER-CONTROLLED."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        # The machine poisons the code slot with a LEDGER-ONLY token.
        machine.errors["session.end"] = (LEDGER_MACHINE_TIMEOUT, "the tool timed out")
        await open_session(service, machine)

        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["ended"] is False
        assert result["why_not_ended"] == REASON_MACHINE_REFUSED
        # The poisoned token is NOT echoed anywhere in the response.
        assert LEDGER_MACHINE_TIMEOUT not in json.dumps(result, default=str)
        assert "machine_timeout" not in result["detail"]
        # The neutral marker replaces it.
        assert "UNRECOGNIZED_ERROR_CODE" in result["detail"]

    def test_a_non_string_peer_code_is_neutralised_without_raising(self):
        """H-2: the TYPE guard — an unhashable code must not raise HERE.

        A raise in the sanitiser would land in the broad transport arm
        of the end — i.e. it would resurrect the bare-error failure mode
        the sanitiser exists to prevent. The input is unreachable from a
        peer (the code slot is coerced to ``str`` when the frame is
        delivered), but the guard costs one line and is pinned here.
        """
        for code in ({"code": "x"}, ["x"], None, 42, ("x",)):
            assert (
                McpGatewayService._safe_peer_code(code) == "UNRECOGNIZED_ERROR_CODE"
            )

    async def test_the_ack_timeout_detail_and_the_flag_definition_agree(self):
        """T-1(c): the detail says the machine did NOT acknowledge, and
        the definition says the flag means ACKNOWLEDGED.

        The flag is NOT flipped to ``True`` for the ack-timeout case
        (the frame may have been written, but nothing acknowledged it):
        the DEFINITION is what changes — ``machine_notified`` means
        "whether the machine ACKNOWLEDGED the session end", so
        ``False`` means the centre released the session AUTHORITATIVELY
        and the machine's local lock may persist.
        """
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)
        machine.hold.add("session.end")

        result = await service.disconnect(agent_id=AGENT_ID)

        assert result["machine_notified"] is False
        # The DETAIL says the machine did not acknowledge it.
        assert "did not acknowledge" in result["detail"]
        # And the service's own docstring defines the flag the same way.
        # The docstring is hard-wrapped, so the needle is matched against
        # the whitespace-collapsed text.
        doc = " ".join(McpGatewayService.disconnect.__doc__.split())
        assert "ACKNOWLEDGED the session end" in doc
        assert "released the session AUTHORITATIVELY" in doc

    async def test_a_protocol_code_is_still_echoed_in_the_detail(self):
        """The sanitiser must not swallow the PROTOCOL vocabulary."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        machine.errors["session.end"] = ("TOOL_ERROR", "the machine said no")
        await open_session(service, machine)

        result = await service.disconnect(agent_id=AGENT_ID)

        assert "TOOL_ERROR" in result["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# The audit vocabulary — the COMMAND rows keep the PROTOCOL vocabulary
# ─────────────────────────────────────────────────────────────────────────────


class TestTheAuditVocabularySplit:
    async def test_a_peer_cannot_forge_a_centre_token_into_a_command_row(self):
        """Item 5: the command rows keep the protocol-only vocabulary."""
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)
        # The machine answers with a CENTRE token in the code slot.
        machine.errors["tools/call"] = ("idle", "the machine said no")

        with pytest.raises(Exception):
            await service.command(agent_id=AGENT_ID, tool="screenshot")

        assert len(audit.records) == 1
        record = audit.records[0]
        assert record.tool == "screenshot"
        # The centre token did not survive: it is not in the command
        # vocabulary any more.
        assert record.error_code == "UNRECOGNIZED_ERROR_CODE"

    async def test_the_session_rows_keep_the_centre_tokens(self):
        """The session rows are where the centre tokens belong."""
        audit = MemoryAuditSink()
        clock = Clock()
        service = make_service(audit_sink=audit, clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await expired_session(service, machine, clock)

        await service.expire_sessions()

        assert len(audit.records) == 1
        assert audit.records[0].error_code == "ttl"
        assert audit.records[0].tool == "session.expire"


# ─────────────────────────────────────────────────────────────────────────────
# The reserved audit tool names (item 6)
# ─────────────────────────────────────────────────────────────────────────────


class TestTheReservedAuditToolNames:
    async def test_a_command_with_a_reserved_tool_name_is_refused(self):
        """The ledger's namespace: one ``tool`` value, one meaning."""
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        with pytest.raises(Exception) as excinfo:
            await service.command(agent_id=AGENT_ID, tool="session.end")

        assert excinfo.value.code == "INTERNAL"
        assert "reserved" in excinfo.value.message
        # Nothing reached the machine.
        assert not [f for f in machine.sent if f.get("method") == "tools/call"]
        # And no COMMAND row carries the reserved name.
        assert audit.records == []

    async def test_the_advertised_catalogue_omits_the_reserved_names(self):
        """The centre never advertises a tool it would refuse.

        The remote catalogue travels verbatim from the machine — but the
        two RESERVED names are the centre's own audit vocabulary, so
        they are filtered OUT of the catalogue the agent sees. Only the
        NAME is dropped; every other tool's schema is untouched.
        """
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        machine.answers["session.begin"] = {
            "session_id": SESSION_ID,
            "machine_id": MACHINE_ID,
            "expires_at_ms": 1792576800000,
            "tools": [
                {"name": "session.end", "inputSchema": {"type": "object"}},
                {"name": "session.expire", "inputSchema": {"type": "object"}},
                {"name": "screenshot", "inputSchema": {"type": "object"}},
            ],
        }

        result = await service.connect(
            machine_id=MACHINE_ID, agent_id=AGENT_ID, reason="test"
        )

        advertised = [tool.get("name") for tool in result["tools"]]
        assert advertised == ["screenshot"]
        # The session's own catalogue is filtered the same way.
        assert [
            tool.get("name") for tool in service.sessions.for_agent(AGENT_ID).catalog
        ] == ["screenshot"]

    async def test_a_normal_command_is_unaffected(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)
        machine.answers["tools/call"] = {"content": [{"type": "text", "text": "ok"}]}

        result = await service.command(agent_id=AGENT_ID, tool="screenshot")

        # The machine's result comes back VERBATIM.
        assert result == {"content": [{"type": "text", "text": "ok"}]}
        # And the call really was forwarded, with the tool name intact.
        calls = [f for f in machine.sent if f.get("method") == "tools/call"]
        assert len(calls) == 1
        assert calls[0]["params"]["name"] == "screenshot"


# ─────────────────────────────────────────────────────────────────────────────
# The TWO-PASS sweep at N=2 (item 11 — the batch property)
# ─────────────────────────────────────────────────────────────────────────────


class TestTheTwoPassSweepAtN2:
    async def test_the_whole_batch_is_released_before_any_machine_is_told(self):
        """N=2: the property the two-pass design exists for.

        The sibling's D1 defect was about session B being popped-but-
        unreleased while session A's forward was awaited: the roster
        reported ``busy: true`` with a DEAD session id and B had no
        tombstone, so a late end for B answered ``unknown_session``
        instead of ``expired``. N=1 cannot prove that — the batch IS
        the property.
        """
        clock = Clock()
        service = make_service(clock=clock)
        events: list[str] = []
        machine_alpha = _RecordingMachine(
            service, events, machine_id="machine-alpha", session_id="sess-alpha"
        )
        machine_bravo = _RecordingMachine(
            service, events, machine_id="machine-bravo", session_id="sess-bravo"
        )
        await open_machine(service, machine_alpha)
        await open_machine(service, machine_bravo)
        # Two AGENTS: one agent may hold only one session (§8.2).
        await open_session(service, machine_alpha, agent_id="ari")
        await open_session(service, machine_bravo, agent_id="leader")
        service._store = _RecordingStore(service._store, events)
        service._tombstones = _RecordingTombstones(service._tombstones, events)
        clock.advance(2.0)

        expired = await service.expire_sessions()

        assert len(expired) == 2
        # BOTH machines were told.
        assert events.count("machine.session.end") == 2
        first_forward = events.index("machine.session.end")
        # BOTH roster pointers cleared and BOTH tombstones written
        # BEFORE the first machine frame.
        assert events[:first_forward].count("store.clear_session") == 2
        assert events[:first_forward].count("tombstone.record") == 2
        # And the roster really is free for BOTH machines.
        assert service.store.get("machine-alpha").session_id is None
        assert service.store.get("machine-bravo").session_id is None

    async def test_both_expired_sessions_answer_expired_after_the_sweep(self):
        """The tombstone of BOTH sessions answers the late end."""
        clock = Clock()
        service = make_service(clock=clock)
        machine_alpha = FakeMachine(service, machine_id="machine-alpha", session_id="sess-alpha")
        machine_bravo = FakeMachine(service, machine_id="machine-bravo", session_id="sess-bravo")
        await open_machine(service, machine_alpha)
        await open_machine(service, machine_bravo)
        await open_session(service, machine_alpha, agent_id="ari")
        await open_session(service, machine_bravo, agent_id="leader")
        clock.advance(2.0)

        await service.expire_sessions()

        for agent_id, session_id in (("ari", "sess-alpha"), ("leader", "sess-bravo")):
            result = await service.disconnect(agent_id=agent_id)
            assert result["ended"] is False
            assert result["why_not_ended"] == REASON_EXPIRED
            assert result["session_id"] == session_id
