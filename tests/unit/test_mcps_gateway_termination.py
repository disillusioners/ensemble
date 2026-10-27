"""The v0.2 session-termination generation of the remote-hands gateway.

WHAT THIS FILE COVERS
---------------------

SPEC v0.2 adds ONE client-originated method (``session.terminate``),
ONE capability (``client_terminate``) and ONE reason member on the
centre's ``session.end`` params — plus the duplicate-machine-id refusal
(§5.6) and the orderly-termination close (§6.10.5).

THE ANTI-TAUTOLOGY RULE
-----------------------

The canonical ids carry LETTERS (``sess-alpha``, ``machine-alpha``), so
an assertion about them is not the identity function — and every
assertion here is able to FAIL when the implementation is reverted:

* without the terminate receipt the session stays live (the ``ended``
  ack never arrives and the slot is never freed),
* without the version scoping the ``session.end`` params would carry a
  ``reason`` on a v0.1 connection (and the v0.2 params would lack it),
* without the duplicate-id refusal the newcomer would silently EVICT the
  incumbent,
* without the close classification the orderly termination would be
  recorded as a machine death (``machine_notified: false``).

THE ANTI-TAUTOLOGY RULE, THE OTHER WAY
--------------------------------------

Every ack is pinned by its EXACT KEY SET, not only by the presence of
its members: the shipped client reads ``result.reason`` and
``result.why_not_ended``, and a stray ``why_not_ended`` on the
successful path would make it take the WRONG branch.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from daemon.services.mcps_gateway.audit import MemoryAuditSink
from daemon.services.mcps_gateway.config import GatewayConfig
from daemon.services.mcps_gateway.protocol import (
    AUDIT_TOOL_SESSION_TERMINATE,
    CAP_CLIENT_TERMINATE,
    CODE_INTERNAL,
    METHOD_SESSION_END,
    PROTOCOL_VERSION,
    PROTOCOL_VERSION_V02,
    TYPE_ERROR,
    TYPE_REQUEST,
    TYPE_RESPONSE,
)
from daemon.services.mcps_gateway.service import McpGatewayService
from daemon.services.mcps_gateway.tombstones import (
    REASON_ALREADY_ENDED,
    REASON_REAPED_BY_CENTER,
    REASON_SUPERSEDED,
)

#: The canonical ids of the fixtures — LETTER-BEARING on purpose.
AGENT_ID = "ari"
MACHINE_ID = "machine-alpha"
SESSION_ID = "sess-alpha"

#: The capability map a v0.1 machine advertises — the historical seven
#: keys; a v0.1 machine has never heard of ``client_terminate``.
CAPS_V01: dict[str, bool] = {
    "session": True,
    "tools": True,
    "notifications": False,
    "heartbeat": True,
    "screenshot_delta": False,
    "multi_session": False,
    "client_cert": False,
}

#: The capability map a v0.2 machine advertises. ``client_terminate`` is
#: the v0.2 addition: the machine tells the centre it can terminate a
#: session from its own side. Exactly the v0.1 map plus the one key.
CAPS_V02: dict[str, bool] = {**CAPS_V01, CAP_CLIENT_TERMINATE: True}


class Clock:
    """A pinnable clock — the expiry is deterministic, never a sleep."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeMachine:
    """The machine side of the socket, as the service sees it."""

    def __init__(
        self,
        service: McpGatewayService,
        *,
        machine_id: str = MACHINE_ID,
        session_id: str = SESSION_ID,
        version: str = PROTOCOL_VERSION_V02,
        capabilities: dict[str, bool] | None = None,
    ) -> None:
        self.service = service
        self.machine_id = machine_id
        self.session_id = session_id
        self.version = version
        self.capabilities = capabilities if capabilities is not None else dict(CAPS_V02)
        self.connection = None
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.close_code: int | None = None
        #: The session.end frames the machine received.
        self.session_ends: list[dict[str, Any]] = []

    # ── the transport interface ─────────────────────────────────────────

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)
        if (
            payload.get("type") == TYPE_REQUEST
            and payload.get("method") == METHOD_SESSION_END
        ):
            self.session_ends.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code

    # ── helpers ─────────────────────────────────────────────────────────

    def frames_of_type(self, type_: str) -> list[dict[str, Any]]:
        return [f for f in self.sent if f.get("type") == type_]

    def last_response(self) -> dict[str, Any] | None:
        responses = self.frames_of_type(TYPE_RESPONSE)
        return responses[-1] if responses else None

    async def hello(self) -> dict[str, Any]:
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
                        "client": {"name": "mcps-client", "version": "0.2.0"},
                        "machine_id": self.machine_id,
                        "capabilities": dict(self.capabilities),
                    },
                }
            ),
        )
        response = self.last_response()
        return response if response is not None else {}

    async def terminate(
        self,
        *,
        session_id: str | None = None,
        reason_code: Any = "operator_force",
        params: dict[str, Any] | None = None,
        extra_envelope: dict[str, Any] | None = None,
        request_id: str = "req-term-1",
    ) -> dict[str, Any]:
        """Send a ``session.terminate`` request and return the ack."""
        payload: dict[str, Any] = {
            "v": self.version,
            "type": "request",
            "id": request_id,
            "method": "session.terminate",
            "session_id": session_id if session_id is not None else self.session_id,
            "params": params
            if params is not None
            else {"reason_code": reason_code},
        }
        if extra_envelope:
            payload.update(extra_envelope)
        await self.service.handle_frame(self.connection, json.dumps(payload))
        for frame in reversed(self.sent):
            if frame.get("type") in (TYPE_RESPONSE, TYPE_ERROR) and (
                    frame.get("id") == request_id
            ):
                return frame
        return {}

    async def heartbeat(self) -> None:
        await self.service.handle_frame(
            self.connection,
            json.dumps(
                {
                    "v": self.version,
                    "type": "notification",
                    "method": "heartbeat",
                    "params": {},
                }
            ),
        )

    async def answer_last(self, result: Any = None) -> None:
        requests = self.frames_of_type(TYPE_REQUEST)
        assert requests, "the center sent no request"
        request = requests[-1]
        await self.service.handle_frame(
            self.connection,
            json.dumps(
                {
                    "v": self.version,
                    "type": "response",
                    "id": request["id"],
                    "result": result if result is not None else {},
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
        command_timeout_ms=1_000,
        audit_enabled=True,
        allowed_machines=(),
        session_ttl_ms=1_000,
        session_idle_timeout_ms=300_000,
        session_end_ack_timeout_ms=50,
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
    task = asyncio.create_task(
        service.connect(machine_id=machine.machine_id, agent_id=agent_id, reason="test")
    )
    await asyncio.sleep(0.01)
    await machine.answer_last(
        {
            "session_id": machine.session_id,
            "machine_id": machine.machine_id,
            "expires_at_ms": 1792576800000,
            "tools": [{"name": "screenshot", "inputSchema": {"type": "object"}}],
        }
    )
    return await task


# ─────────────────────────────────────────────────────────────────────────────
# §6.10 — the client-originated termination receipt
# ─────────────────────────────────────────────────────────────────────────────


class TestSessionTerminate:
    async def test_the_terminate_ends_the_session_and_acknowledges(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        ack = await machine.terminate(reason_code="operator_force")

        result = ack["result"]
        assert result["session_id"] == SESSION_ID
        assert result["ended"] is True
        # The reason is the machine's OWN code, verbatim.
        assert result["reason"] == "operator_force"
        assert result["machine_notified"] is True
        # The ack carries the request id and the negotiated version.
        assert ack["id"] == "req-term-1"
        assert ack["v"] == PROTOCOL_VERSION_V02

    @pytest.mark.parametrize(
        ("reason_code", "expected_members"),
        [
            # A normal code: the reason rides the ack.
            (
                "operator_force",
                {"session_id", "ended", "reason", "machine_notified"},
            ),
            # A LEDGER-ONLY token is NOT echoed: the member is OMITTED
            # (the two reason sets are disjoint by construction) — the
            # key set is still EXACT, never a subset.
            (
                "machine_unreachable",
                {"session_id", "ended", "machine_notified"},
            ),
        ],
    )
    async def test_the_ack_carries_exactly_the_expected_members(
        self, reason_code, expected_members
    ):
        """The exact key set: a stray ``why_not_ended`` would make the
        shipped client take the wrong branch — and a stray ``reason``
        would put a ledger-only token on the wire."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        ack = await machine.terminate(reason_code=reason_code)

        assert set(ack["result"]) == expected_members
        assert "why_not_ended" not in ack["result"]

    async def test_a_ledger_only_reason_code_is_omitted_from_the_response(self):
        """Spec defect #3 — a ledger-only token must never reach a
        RESPONSE.

        The machine may send any ``reason_code``; ``machine_unreachable``
        and ``machine_timeout`` are the CENTRE's own release-path tokens
        and must not be relayed back into a response. The member is
        OMITTED (§6.9.7 — the end-ack's ``reason`` is present-only-when-
        known); the shipped client does not validate ``reason`` on
        ``ended: true``.
        """
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        ack = await machine.terminate(reason_code="machine_unreachable")

        result = ack["result"]
        assert result["ended"] is True
        assert "reason" not in result
        assert result["machine_notified"] is True
        assert set(result) == {"session_id", "ended", "machine_notified"}

    async def test_a_ledger_only_reason_code_is_still_recorded_verbatim(self):
        """The LEDGER keeps the raw code VERBATIM — only the RESPONSE is
        sanitized."""
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        await machine.terminate(reason_code="machine_unreachable")

        row = [r for r in audit.records if r.tool == AUDIT_TOOL_SESSION_TERMINATE][0]
        assert row.extra["reason_code"] == "machine_unreachable"
        assert "machine_unreachable" in row.detail

    async def test_a_genuinely_unknown_reason_code_still_echoes_verbatim(self):
        """The liberal-acceptance rule is UNCHANGED: every other code —
        known or not — is echoed verbatim."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        ack = await machine.terminate(reason_code="operator_latte")

        assert ack["result"]["reason"] == "operator_latte"
        assert ack["result"]["ended"] is True

    async def test_the_idempotent_ack_carries_exactly_four_members(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        await machine.terminate()
        ack = await machine.terminate(request_id="req-term-2")

        assert set(ack["result"]) == {
            "session_id",
            "ended",
            "reason",
            "machine_notified",
        }

    async def test_the_session_is_removed_from_the_registry(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        await machine.terminate()

        assert service.sessions.get(SESSION_ID) is None
        assert service.sessions.for_machine(MACHINE_ID) is None
        record = service.store.get(MACHINE_ID)
        assert record is not None
        assert record.session_id is None

    async def test_the_socket_stays_open(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        await machine.terminate()

        assert machine.closed is False

    async def test_a_new_begin_succeeds_after_the_terminate(self):
        """§6.10: the slot is free again — no MACHINE_BUSY."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)
        await machine.terminate()

        result = await open_session(service, machine, agent_id="ari")

        assert result["session_id"] == SESSION_ID
        assert service.sessions.for_machine(MACHINE_ID) is not None

    async def test_a_late_tools_call_answer_still_resolves(self):
        """§6.10.4: the socket stays open, so a late answer still lands."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        task = asyncio.create_task(service.command(agent_id=AGENT_ID, tool="click"))
        await asyncio.sleep(0.02)
        # The machine terminates the session while the call is in flight.
        await machine.terminate()
        await machine.answer_last({"ok": True})

        assert await task == {"ok": True}

    async def test_the_session_id_is_read_from_the_envelope(self):
        """The id rides the ENVELOPE — never the params."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        ack = await machine.terminate(
            session_id=SESSION_ID,
            params={"reason_code": "operator_force", "session_id": "sess-other"},
        )

        assert ack["result"]["session_id"] == SESSION_ID
        assert ack["result"]["ended"] is True

    async def test_an_unknown_reason_code_is_accepted_verbatim(self):
        """The liberal-acceptance rule: no validation gate on the code."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        ack = await machine.terminate(reason_code="something_the_center_never_heard_of")

        assert ack["result"]["ended"] is True
        assert ack["result"]["reason"] == "something_the_center_never_heard_of"

    async def test_extra_envelope_fields_are_tolerated(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        ack = await machine.terminate(
            params={"reason_code": "operator_force", "detail": "ignored by the center"},
            extra_envelope={"future_member": {"anything": True}},
        )

        assert ack["result"]["ended"] is True

    async def test_the_reason_code_is_recorded_verbatim_in_the_audit_row(self):
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        await machine.terminate(reason_code="operator_force")

        rows = [r for r in audit.records if r.tool == AUDIT_TOOL_SESSION_TERMINATE]
        assert len(rows) == 1
        row = rows[0]
        assert row.session_id == SESSION_ID
        assert row.machine_id == MACHINE_ID
        # The RAW code rides a member that is not the guarded error_code.
        assert row.extra["reason_code"] == "operator_force"
        assert "operator_force" in row.detail
        # An UNRECOGNIZED code must NOT be rewritten into the error_code
        # slot (the guard would turn it into UNRECOGNIZED_ERROR_CODE).
        assert row.error_code == ""

    async def test_an_unrecognized_reason_code_is_not_rewritten(self):
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        await machine.terminate(reason_code="totally_unknown")

        row = [r for r in audit.records if r.tool == AUDIT_TOOL_SESSION_TERMINATE][0]
        assert row.error_code == ""
        assert row.extra["reason_code"] == "totally_unknown"
        assert "totally_unknown" in row.detail

    async def test_the_terminate_never_refuses(self):
        """§6.10.1: the centre NEVER refuses a termination request."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        ack = await machine.terminate()

        assert ack["type"] == TYPE_RESPONSE
        assert "error" not in ack

    async def test_a_terminate_for_a_session_that_is_not_live_is_idempotent(self):
        """The NOT-LIVE arm: the idempotent goal state.

        ``machine_notified`` is ``False`` — the centre delivered NOTHING
        to the machine on this arm (it had no session to end and it
        told the machine nothing).
        """
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))

        ack = await machine.terminate()

        result = ack["result"]
        assert result["ended"] is False
        assert result["reason"] == REASON_ALREADY_ENDED
        assert result["machine_notified"] is False

    async def test_the_reap_race_answers_reaped_by_center(self):
        """The sweep reaped the session; the machine asks afterwards.

        The TOMBSTONE-HIT arm: ``machine_notified`` is ``False`` here
        too — the sweep's forward is the centre's own frame, and this
        receipt delivered nothing to the machine.

        THE COMPANION of the ownership guard: the tombstone belongs to
        the ASKING machine, so the consult is OURS and the centre may
        name the centre cause.
        """
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        # The sweep reaps the session (its TTL passed).
        clock.advance(2.0)
        await service.expire_sessions()

        ack = await machine.terminate()

        result = ack["result"]
        assert result["ended"] is False
        assert result["reason"] == REASON_REAPED_BY_CENTER
        assert result["machine_notified"] is False

    async def test_a_foreign_tombstone_is_not_resolved_into_a_positive_answer(self):
        """§6.9.3 — THE LOOKUP IS SCOPED TO THE RESPONDER'S OWNERSHIP.

        M2's session was reaped by the sweep, so an ``expired``
        tombstone that belongs to **M2** exists.  M1 asks about that
        session id: the centre must NOT resolve M2's tombstone into a
        positive answer (``reaped_by_center``) — the reply is the
        NEUTRAL arm, the same shape as no tombstone at all, and the
        ledger row is attributed to the ASKING machine.
        """
        audit = MemoryAuditSink()
        clock = Clock()
        service = make_service(audit_sink=audit, clock=clock)

        beta = await open_machine(
            service,
            FakeMachine(service, machine_id="machine-beta", session_id="sess-beta"),
        )
        await open_session(service, beta)

        # The sweep reaps M2's session: an `expired` tombstone owned by
        # machine-beta.
        clock.advance(2.0)
        await service.expire_sessions()

        alpha = await open_machine(
            service,
            FakeMachine(service, machine_id="machine-alpha", session_id="sess-alpha"),
        )
        ack = await alpha.terminate(session_id="sess-beta")

        result = ack["result"]
        assert result["ended"] is False
        assert result["reason"] == REASON_ALREADY_ENDED
        assert result["machine_notified"] is False
        # The ledger row belongs to the ASKING machine, not to M2.
        rows = [r for r in audit.records if r.tool == AUDIT_TOOL_SESSION_TERMINATE]
        assert rows, "the receipt must still be audited"
        assert rows[-1].machine_id == "machine-alpha"

    async def test_a_foreign_live_session_answers_exactly_like_a_truly_unknown_one(
        self,
    ):
        """§6.10.2 — the FOREIGN-SESSION LEAK BAN (Fork C).

        "whatever the responder returns — the ``unknown_session`` outcome
        code or the typed ``SESSION_NOT_FOUND`` — the answer MUST be
        indistinguishable from the answer for a truly unknown id. No
        existence oracle for another workspace's live session."

        THE ANTI-TAUTOLOGY RULE: the two payloads are compared
        MEMBER-BY-MEMBER (every member but the caller's own session id
        must be EQUAL), so a distinguishable answer fails even when it
        is "semantically the same" — a different reason code, a
        different key set or a different flag all fail.
        """
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        other = FakeMachine(service, machine_id="machine-beta")
        other.connection = await service.open_connection(other)
        await other.hello()

        foreign = await other.terminate(
            session_id=SESSION_ID, request_id="req-foreign"
        )
        unknown = await other.terminate(
            session_id="sess-never-existed", request_id="req-unknown"
        )

        foreign_result = dict(foreign["result"])
        unknown_result = dict(unknown["result"])
        # The ONLY member that may differ is the session id itself — it
        # is the caller's own input.
        foreign_result.pop("session_id")
        unknown_result.pop("session_id")
        assert foreign_result == unknown_result
        # The neutral answer of the never-existed arm, spelled out.
        assert unknown_result["reason"] == REASON_ALREADY_ENDED
        assert unknown_result["ended"] is False
        assert unknown_result["machine_notified"] is False
        # The foreign session is STILL live — the answer did not end it.
        assert service.sessions.get(SESSION_ID) is not None

    async def test_the_terminate_of_another_connections_session_is_not_ended(self):
        """The centre never ends another connection's session."""
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        other = FakeMachine(service, machine_id="machine-beta")
        other.connection = await service.open_connection(other)
        await other.hello()

        ack = await other.terminate(session_id=SESSION_ID)

        assert ack["result"]["ended"] is False
        # The live session is untouched.
        assert service.sessions.get(SESSION_ID) is not None

    async def test_an_absent_reason_code_omits_the_reason_member(self):
        """§6.9.3 / §6.10.1 (Fork B) — ``reason`` is present-when-known.

        "``reason`` is required when known and omitted when sanitized or
        unknown (never empty-string)". A machine that sent NO
        ``reason_code`` has told the centre NOTHING about the cause, so
        the member is OMITTED.

        THE ANTI-TAUTOLOGY RULE: the assertion is on the KEY SET — a
        ``reason: ""`` would satisfy a truthiness check but NOT this
        one.
        """
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        ack = await machine.terminate(params={})

        result = ack["result"]
        assert result["ended"] is True
        assert "reason" not in result
        assert set(result) == {"session_id", "ended", "machine_notified"}

    async def test_an_empty_reason_code_never_produces_an_empty_reason(self):
        """The same rule for an EMPTY cause — ``reason: ""`` is forbidden.

        The member is "omitted when sanitized or unknown — never
        empty-string", and an empty string is not a cause.
        """
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        ack = await machine.terminate(params={"reason_code": ""})

        result = ack["result"]
        assert result["ended"] is True
        assert "reason" not in result
        assert set(result) == {"session_id", "ended", "machine_notified"}

    async def test_an_absent_reason_code_is_still_recorded_verbatim(self):
        """The LEDGER keeps the raw value VERBATIM — only the RESPONSE is
        sanitized (§6.9.1: the audit is the truth, the response is the
        sanitized view).

        THE BAN IS RESPONSE-SCOPED (§6.10.2: "the ban is response-scoped;
        the ledger is exempt — it is the audit truth"), so the ledger's
        copy of an absent cause is the empty verbatim value — and the
        ledger NEVER invents a cause either.
        """
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        await machine.terminate(params={})

        rows = [r for r in audit.records if r.tool == AUDIT_TOOL_SESSION_TERMINATE]
        assert rows, "the receipt must still be audited"
        assert rows[-1].extra["reason_code"] == ""
        # No cause was invented for the row.
        assert "[" not in rows[-1].detail
        assert rows[-1].error_code == ""


class TestTerminateNeedsTheCapability:
    async def test_a_v01_connection_refuses_the_terminate(self):
        """A v0.1 connection never negotiated ``client_terminate``."""
        service = make_service()
        machine = await open_machine(
            service,
            FakeMachine(
                service,
                version=PROTOCOL_VERSION,
                capabilities=dict(CAPS_V01),
            ),
        )
        await open_session(service, machine)

        ack = await machine.terminate()

        assert ack["type"] == TYPE_ERROR
        assert ack["error"]["code"] == CODE_INTERNAL
        # NO session effect: the session is still live.
        assert service.sessions.get(SESSION_ID) is not None

    async def test_a_v02_connection_without_the_capability_refuses_the_terminate(self):
        service = make_service()
        caps = dict(CAPS_V02)
        caps[CAP_CLIENT_TERMINATE] = False
        machine = await open_machine(
            service, FakeMachine(service, capabilities=caps)
        )
        await open_session(service, machine)

        ack = await machine.terminate()

        assert ack["type"] == TYPE_ERROR
        assert ack["error"]["code"] == CODE_INTERNAL
        assert service.sessions.get(SESSION_ID) is not None


# ─────────────────────────────────────────────────────────────────────────────
# §6.10.5 — the orderly-termination close
# ─────────────────────────────────────────────────────────────────────────────


class TestOrderlyTerminationClose:
    def test_the_router_reads_the_close_code_and_the_reason(self):
        """The ROUTER seam: the ASGI disconnect message carries the
        close code and the reason, and the classification reads them."""
        from daemon.routers.mcps_gateway import _close_classification

        reason, orderly = _close_classification(
            {"type": "websocket.disconnect", "code": 1000, "reason": "session.terminate"}
        )
        assert reason == "session.terminate"
        assert orderly is True

    def test_any_other_close_is_not_an_orderly_termination(self):
        from daemon.routers.mcps_gateway import _close_classification

        for message in (
            {"type": "websocket.disconnect", "code": 1000, "reason": "bye"},
            {"type": "websocket.disconnect", "code": 1006, "reason": "session.terminate"},
            {"type": "websocket.disconnect", "code": 1001, "reason": "going away"},
            {"type": "websocket.disconnect"},
        ):
            _reason, orderly = _close_classification(message)
            assert orderly is False, message

    async def test_the_1000_session_terminate_close_is_an_orderly_termination(self):
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        await service.close_connection(
            machine.connection,
            "session.terminate",
            orderly_termination=True,
        )

        tombstones = service.tombstones.entries()
        assert len(tombstones) == 1
        # The machine ACKNOWLEDGED: it is the one that asked.
        assert tombstones[0].machine_notified is True
        rows = [r for r in audit.records if r.tool == AUDIT_TOOL_SESSION_TERMINATE]
        assert len(rows) == 1
        assert rows[0].session_id == SESSION_ID

    async def test_any_other_close_is_a_machine_death(self):
        audit = MemoryAuditSink()
        service = make_service(audit_sink=audit)
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        await service.close_connection(machine.connection, "the client disconnected")

        tombstones = service.tombstones.entries()
        assert len(tombstones) == 1
        assert tombstones[0].machine_notified is False
        rows = [r for r in audit.records if r.tool == AUDIT_TOOL_SESSION_TERMINATE]
        assert rows == []


# ─────────────────────────────────────────────────────────────────────────────
# §6.9.6 — the version-AND-capability-scoped ``reason`` member of the
# session.end params
# ─────────────────────────────────────────────────────────────────────────────


class TestVersionAndCapabilityScopedSessionEndParams:
    """§6.9.6 — the ``reason`` member is VERSION-AND-CAPABILITY-SCOPED.

    "on a ``0.2``-negotiated connection where BOTH capability maps carry
    ``client_terminate: true``"; on a v0.1 connection — and on a v0.2
    connection WITHOUT the negotiated capability — "the params stay
    exactly ``{"session_id": …}``".
    """

    async def test_the_v01_session_end_params_stay_exactly_the_session_id(self):
        """A v0.1 connection's frames are byte-identical to the old ones."""
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(
            service,
            FakeMachine(
                service,
                version=PROTOCOL_VERSION,
                capabilities=dict(CAPS_V01),
            ),
        )
        await open_session(service, machine)

        clock.advance(2.0)
        await service.expire_sessions()

        assert len(machine.session_ends) == 1
        assert machine.session_ends[0]["params"] == {"session_id": SESSION_ID}

    async def test_the_v02_sweep_sends_reaped_by_center(self):
        """A v0.2 connection whose machine advertised the capability."""
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        clock.advance(2.0)
        await service.expire_sessions()

        assert len(machine.session_ends) == 1
        assert machine.session_ends[0]["params"] == {
            "session_id": SESSION_ID,
            "reason": REASON_REAPED_BY_CENTER,
        }
        assert machine.session_ends[0]["v"] == PROTOCOL_VERSION_V02

    async def test_the_v02_explicit_end_sends_superseded(self):
        service = make_service()
        machine = await open_machine(service, FakeMachine(service))
        await open_session(service, machine)

        task = asyncio.create_task(service.disconnect(agent_id=AGENT_ID))
        await asyncio.sleep(0.02)
        await machine.answer_last({"session_id": SESSION_ID, "ended": True})
        answer = await task

        assert answer["ended"] is True
        assert len(machine.session_ends) == 1
        assert machine.session_ends[0]["params"] == {
            "session_id": SESSION_ID,
            "reason": REASON_SUPERSEDED,
        }

    async def test_a_v02_connection_without_the_capability_sends_bare_params(self):
        """§6.9.6 — the capability half of the gate.

        A v0.2 machine that did NOT advertise ``client_terminate`` is a
        connection on which the reason must NOT ride: "on a ``0.2``
        connection without the negotiated capability — the frame carries
        no reason member and its params stay exactly ``{"session_id":
        …}``".

        THE ANTI-TAUTOLOGY RULE: the machine's map has no
        ``client_terminate`` key, so the version ALONE cannot carry the
        reason — a version-only gate puts the member on the wire and
        this test fails.
        """
        clock = Clock()
        service = make_service(clock=clock)
        machine = await open_machine(
            service,
            FakeMachine(
                service,
                version=PROTOCOL_VERSION_V02,
                capabilities=dict(CAPS_V01),
            ),
        )
        await open_session(service, machine)

        clock.advance(2.0)
        await service.expire_sessions()

        assert len(machine.session_ends) == 1
        assert machine.session_ends[0]["params"] == {"session_id": SESSION_ID}
        assert machine.session_ends[0]["v"] == PROTOCOL_VERSION_V02

    async def test_a_v02_connection_without_the_capability_sends_bare_params_on_an_explicit_end(
        self,
    ):
        """The explicit end rides the SAME gate as the sweep."""
        service = make_service()
        machine = await open_machine(
            service,
            FakeMachine(
                service,
                version=PROTOCOL_VERSION_V02,
                capabilities=dict(CAPS_V01),
            ),
        )
        await open_session(service, machine)

        task = asyncio.create_task(service.disconnect(agent_id=AGENT_ID))
        await asyncio.sleep(0.02)
        await machine.answer_last({"session_id": SESSION_ID, "ended": True})
        answer = await task

        assert answer["ended"] is True
        assert len(machine.session_ends) == 1
        assert machine.session_ends[0]["params"] == {"session_id": SESSION_ID}
