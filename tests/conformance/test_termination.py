"""SPEC v0.2 — the session-termination generation.

THE AREA
--------

SPEC v0.2 §10 adds one area to the kit: the **client-originated
termination** (§6.10) and the **duplicate-machine-id refusal** (§5.6).
Both are wire-driven, so both are exercised through the same peer as
every other row — the machine dials out, says hello, and speaks.

THE ROWS
--------

1. **the v0.2 handshake** — a machine that asks for ``"0.2"`` gets a
   ``"0.2"`` connection: the reply's ``v`` equals the result's
   ``protocol_version``, and the centre advertises ``client_terminate``
   because the machine advertised it (§5.2/§5.3).
2. **the terminate receipt** — the machine asks for the end; the centre
   acks ``{session_id, ended: true, reason, machine_notified: true}``
   with the machine's OWN reason code, VERBATIM; ``why_not_ended`` is
   ABSENT and the socket STAYS OPEN (§6.10).
3. **the liberal acceptance** — an unknown ``reason_code`` and unknown
   members in the envelope are ACCEPTED, never a rejection (§6.10.3).
4. **the duplicate machine id** — the newcomer is refused with
   ``MACHINE_ID_CONFLICT`` and the INCUMBENT survives: its socket stays
   open and its session stays live.  NEITHER connection is evicted
   (§5.6).
5. **the reap race** — the sweep reaped the session and the machine asks
   afterwards: the answer is the IDEMPOTENT one (``ended: false``) with
   a MANDATORY ``reason`` — never an error (§6.10).
6. **the v0.2 ``session.end`` reason** — on a ``"0.2"`` connection the
   centre's ``session.end`` names the centre cause (``reaped_by_center``
   for the sweep's reap, ``superseded`` for an explicit end) — the
   v0.1 lane keeps exactly ``{"session_id": ...}`` (§6.9.2).

THE INDEPENDENCE CONTRACT
--------------------------

This module imports NOTHING from ``daemon.*``: every frame is a
transcription of the SPEC (the machine-side vocabulary lives in
``conformance_peer.py``, the in-kit reference centre included).
"""

from __future__ import annotations

import asyncio

import pytest

from .conformance_peer import (
    CLIENT_CAPABILITIES_V02,
    ConformanceFinding,
    ConformancePeer,
    PROTOCOL_VERSION,
    PROTOCOL_VERSION_V02,
    open_session,
    wait_until,
)

pytestmark = [pytest.mark.conformance]

#: The termination reason codes the shipped client implements (§6.10).
REASON_OPERATOR_FORCE = "operator_force"


def _peer(target, **kwargs) -> ConformancePeer:
    return ConformancePeer(
        url=target.url,
        token=target.token,
        machine_id=target.machine_id,
        ca_pem=target.ca_pem or None,
        **kwargs,
    )


def _v02_peer(target, **kwargs) -> ConformancePeer:
    """An alias of :func:`_peer` for the rows that speak v0.2.

    The FACTORY negotiates nothing: the version is chosen per row by
    the ``hello`` arguments (``protocol_version=PROTOCOL_VERSION_V02``
    plus ``CLIENT_CAPABILITIES_V02``). The alias only MARKS the intent
    of the row it serves.
    """
    return _peer(target, **kwargs)


def _finding(area: str, check: str, clause: str, detail: str) -> ConformanceFinding:
    return ConformanceFinding(area, check, clause, detail)


@pytest.mark.asyncio
async def test_the_v02_handshake_advertises_client_terminate(target) -> None:
    """§5.2/§5.3 — the connection RUNS the requested version, and the
    centre advertises ``client_terminate`` because the machine did."""

    peer = _v02_peer(target)
    try:
        await peer.connect(send_hello=False)
        answer = await peer.hello(
            protocol_version=PROTOCOL_VERSION_V02,
            capabilities=dict(CLIENT_CAPABILITIES_V02),
        )
        if answer.get("type") != "response":
            raise _finding(
                "termination",
                "hello-v02-accepted",
                "§5.2",
                f"the v0.2 hello was refused: {answer.get('error')!r}",
            )
        result = answer.get("result") or {}
        # The reply's version and the result's version MUST agree — the
        # shipped client fatals on a mismatch.
        if answer.get("v") != result.get("protocol_version"):
            raise _finding(
                "termination",
                "hello-v02-version-echo",
                "§5.2",
                f"the reply carries v={answer.get('v')!r} while the result "
                f"carries protocol_version={result.get('protocol_version')!r}",
            )
        if result.get("protocol_version") != PROTOCOL_VERSION_V02:
            raise _finding(
                "termination",
                "hello-v02-version-echo",
                "§5.2",
                f"the centre answered protocol_version="
                f"{result.get('protocol_version')!r}, want {PROTOCOL_VERSION_V02!r}",
            )
        caps = result.get("capabilities") or {}
        if caps.get("client_terminate") is not True:
            raise _finding(
                "termination",
                "hello-v02-client-terminate",
                "§5.3",
                f"the centre does not advertise client_terminate: {caps!r}",
            )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_the_terminate_receipt_ends_the_session_and_keeps_the_socket(
    target, serving, begin
) -> None:
    """§6.10 — the machine asks for the end; the centre acks with the
    machine's OWN reason code and the socket STAYS OPEN."""

    peer = _v02_peer(target)
    try:
        await peer.connect(send_hello=False)
        await peer.hello(
            protocol_version=PROTOCOL_VERSION_V02,
            capabilities=dict(CLIENT_CAPABILITIES_V02),
        )
        peer.start_heartbeats(peer.adopted.get("heartbeat_interval_ms", 1000))
        async with serving(peer, budget_s=20):
            await open_session(peer, target)
        session_id = peer.session_ids[0]

        ack = await peer.terminate(
            session_id=session_id, reason_code=REASON_OPERATOR_FORCE
        )

        if ack.get("type") != "response":
            raise _finding(
                "termination",
                "terminate-accepted",
                "§6.10.1",
                f"the termination was refused: {ack.get('error')!r}",
            )
        result = ack.get("result") or {}
        if result.get("session_id") != session_id:
            raise _finding(
                "termination",
                "terminate-session-id",
                "§6.10",
                f"the ack names session {result.get('session_id')!r}, "
                f"want {session_id!r}",
            )
        if result.get("ended") is not True:
            raise _finding(
                "termination",
                "terminate-ended",
                "§6.10",
                f"the ack says ended={result.get('ended')!r}, want True",
            )
        # The reason is the machine's OWN code, verbatim.
        if result.get("reason") != REASON_OPERATOR_FORCE:
            raise _finding(
                "termination",
                "terminate-reason-verbatim",
                "§6.10",
                f"the ack carries reason={result.get('reason')!r}, "
                f"want {REASON_OPERATOR_FORCE!r}",
            )
        # The successful path has NO ``why_not_ended`` — the shipped
        # client's kit pins its absence.
        if "why_not_ended" in result:
            raise _finding(
                "termination",
                "terminate-no-why-not-ended",
                "§6.10",
                f"the successful ack carries why_not_ended: {result!r}",
            )
        # The socket is NOT closed by a termination.
        if peer.transcript.close_code is not None:
            raise _finding(
                "termination",
                "terminate-socket-open",
                "§6.10.4",
                f"the centre closed the socket (code {peer.transcript.close_code})",
            )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_the_terminate_accepts_an_unknown_reason_code(
    target, serving, begin
) -> None:
    """§6.10.3 — the liberal acceptance: an UNKNOWN reason code is
    accepted and echoed back VERBATIM (never a rejection)."""

    unknown = "a_reason_code_the_centre_has_never_heard_of"
    peer = _v02_peer(target)
    try:
        await peer.connect(send_hello=False)
        await peer.hello(
            protocol_version=PROTOCOL_VERSION_V02,
            capabilities=dict(CLIENT_CAPABILITIES_V02),
        )
        peer.start_heartbeats(peer.adopted.get("heartbeat_interval_ms", 1000))
        async with serving(peer, budget_s=20):
            await open_session(peer, target)
        session_id = peer.session_ids[0]

        ack = await peer.terminate(session_id=session_id, reason_code=unknown)

        if ack.get("type") != "response":
            raise _finding(
                "termination",
                "terminate-liberal-acceptance",
                "§6.10.3",
                f"an unknown reason code was refused: {ack.get('error')!r}",
            )
        result = ack.get("result") or {}
        if result.get("ended") is not True:
            raise _finding(
                "termination",
                "terminate-liberal-acceptance",
                "§6.10.3",
                f"the ack says ended={result.get('ended')!r}, want True",
            )
        if result.get("reason") != unknown:
            raise _finding(
                "termination",
                "terminate-liberal-acceptance",
                "§6.10.3",
                f"the ack rewrote the reason code: {result.get('reason')!r}",
            )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_a_terminate_for_a_session_that_is_not_live_is_idempotent(
    target,
) -> None:
    """§6.10 — the IDEMPOTENT goal state is NEVER an error, and its
    ``reason`` is MANDATORY: on ``ended: false`` the shipped client
    RELEASES its local lock by default (the ended-elsewhere arm) and
    RETAINS it only for ``refused`` / ``machine_refused`` — sending the
    reason always makes the client's decision explicit."""

    peer = _v02_peer(target)
    try:
        await peer.connect(send_hello=False)
        await peer.hello(
            protocol_version=PROTOCOL_VERSION_V02,
            capabilities=dict(CLIENT_CAPABILITIES_V02),
        )
        peer.start_heartbeats(peer.adopted.get("heartbeat_interval_ms", 1000))
        ack = await peer.terminate(
            session_id="sess-never-existed", reason_code=REASON_OPERATOR_FORCE
        )

        if ack.get("type") != "response":
            raise _finding(
                "termination",
                "terminate-idempotent",
                "§6.10",
                f"the idempotent goal state produced an error: {ack.get('error')!r}",
            )
        result = ack.get("result") or {}
        if result.get("ended") is not False:
            raise _finding(
                "termination",
                "terminate-idempotent",
                "§6.10",
                f"the ack says ended={result.get('ended')!r} for a session "
                "that is not live, want False",
            )
        if not result.get("reason"):
            raise _finding(
                "termination",
                "terminate-reason-mandatory",
                "§6.10",
                f"the idempotent ack carries no reason: {result!r}",
            )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_the_terminate_tolerates_unknown_members(target) -> None:
    """§6.10.3 — unknown members in the envelope or the params must not
    cause a rejection."""

    peer = _v02_peer(target)
    try:
        await peer.connect(send_hello=False)
        await peer.hello(
            protocol_version=PROTOCOL_VERSION_V02,
            capabilities=dict(CLIENT_CAPABILITIES_V02),
        )
        peer.start_heartbeats(peer.adopted.get("heartbeat_interval_ms", 1000))
        ack = await peer.terminate(
            session_id="sess-never-existed",
            params={
                "reason_code": REASON_OPERATOR_FORCE,
                "detail": "a free-text detail the centre never parses",
                "a_member_from_the_future": {"nested": True},
            },
        )

        if ack.get("type") != "response":
            raise _finding(
                "termination",
                "terminate-liberal-acceptance",
                "§6.10.3",
                f"an unknown member caused a rejection: {ack.get('error')!r}",
            )
        result = ack.get("result") or {}
        if result.get("ended") is not False:
            raise _finding(
                "termination",
                "terminate-idempotent",
                "§6.10",
                f"the ack says ended={result.get('ended')!r}, want False",
            )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_the_duplicate_machine_id_refuses_the_newcomer(target) -> None:
    """§5.6 — a second connection claiming a LIVE machine id is refused
    with ``MACHINE_ID_CONFLICT``; the incumbent is NOT evicted."""

    incumbent = _peer(target)
    newcomer = _peer(target)
    try:
        await incumbent.connect()
        incumbent.start_heartbeats(incumbent.adopted.get("heartbeat_interval_ms", 1000))
        # The incumbent is LIVE and holds the machine id.
        await newcomer.connect(send_hello=False)
        answer = await newcomer.hello(machine_id=incumbent.machine_id)

        if answer.get("type") != "error":
            raise _finding(
                "termination",
                "duplicate-machine-id-refused",
                "§5.6",
                f"the duplicate machine id was accepted: {answer.get('result')!r}",
            )
        error = answer.get("error") or {}
        if error.get("code") != "MACHINE_ID_CONFLICT":
            raise _finding(
                "termination",
                "duplicate-machine-id-refused",
                "§5.6",
                f"the refusal carries code {error.get('code')!r}, "
                "want MACHINE_ID_CONFLICT",
            )
        data = error.get("data") or {}
        if data.get("machine_id") != incumbent.machine_id:
            raise _finding(
                "termination",
                "duplicate-machine-id-data",
                "§5.6",
                f"the refusal does not name the refused id: {data!r}",
            )
        # The INCUMBENT survives: its socket is still open.  The centre
        # never evicts a live connection to make room for a duplicate.
        if incumbent.transcript.close_code is not None:
            raise _finding(
                "termination",
                "duplicate-machine-id-incumbent-survives",
                "§5.6",
                f"the incumbent was evicted (close code "
                f"{incumbent.transcript.close_code})",
            )
    finally:
        await newcomer.close()
        await incumbent.close()


@pytest.mark.asyncio
async def test_the_reap_race_answers_the_idempotent_goal_state(
    target, serving, begin
) -> None:
    """§6.10 — the sweep reaped the session and the machine asks
    afterwards: the answer is the IDEMPOTENT one, NEVER an error, and it
    carries a MANDATORY reason (on ``ended: false`` the shipped client
    releases its local lock by default — the ended-elsewhere arm — and
    retains it only for ``refused`` / ``machine_refused``)."""

    peer = _v02_peer(target)
    try:
        await peer.connect(send_hello=False)
        await peer.hello(
            protocol_version=PROTOCOL_VERSION_V02,
            capabilities=dict(CLIENT_CAPABILITIES_V02),
        )
        peer.start_heartbeats(peer.adopted.get("heartbeat_interval_ms", 1000))
        async with serving(peer, budget_s=20):
            await open_session(peer, target)
        session_id = peer.session_ids[0]

        # The centre's own sweep reaps the session.  The window is
        # centre-owned and NOT on the wire, so the row can only compress
        # it against a centre that owns a short one (the self-host lane).
        await asyncio.sleep(2.5)

        ack = await peer.terminate(session_id=session_id, reason_code="operator_force")

        if ack.get("type") != "response":
            raise _finding(
                "termination",
                "reap-race-idempotent",
                "§6.10",
                f"the reap race produced an error: {ack.get('error')!r}",
            )
        result = ack.get("result") or {}
        if result.get("ended") is not False:
            raise _finding(
                "termination",
                "reap-race-idempotent",
                "§6.10",
                f"the reap race says ended={result.get('ended')!r}, want False",
            )
        if not result.get("reason"):
            raise _finding(
                "termination",
                "reap-race-reason-mandatory",
                "§6.10",
                f"the reap-race ack carries no reason: {result!r}",
            )
        if target.mode == "self-host":
            # The in-kit centre's sweep owns the reap and remembers it:
            # the answer names the centre cause (§6.9.2 group (ii)).  A
            # FOREIGN centre's reap window is not observable from the
            # machine side, so the token is asserted in the
            # self-consistency lane only.
            if result.get("reason") != "reaped_by_center":
                raise _finding(
                    "termination",
                    "reap-race-reason",
                    "§6.9.2",
                    f"the reap-race ack carries reason="
                    f"{result.get('reason')!r}, want 'reaped_by_center'",
                )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_the_v01_connection_is_unchanged(target) -> None:
    """§5.2 — a v0.1 connection is NOT changed by v0.2: the capability
    map carries no ``client_terminate`` key and the version stays
    ``"0.1"``."""

    peer = _peer(target)
    try:
        await peer.connect(send_hello=False)
        answer = await peer.hello(protocol_version=PROTOCOL_VERSION)
        peer.start_heartbeats(peer.adopted.get("heartbeat_interval_ms", 1000))
        result = answer.get("result") or {}
        if result.get("protocol_version") != PROTOCOL_VERSION:
            raise _finding(
                "termination",
                "v01-unchanged",
                "§5.2",
                f"a v0.1 connection answered "
                f"{result.get('protocol_version')!r}, want {PROTOCOL_VERSION!r}",
            )
        caps = result.get("capabilities") or {}
        if "client_terminate" in caps:
            raise _finding(
                "termination",
                "v01-unchanged",
                "§5.3",
                f"the v0.1 capability map carries client_terminate: {caps!r}",
            )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_the_v02_session_end_names_the_reap_cause(target, serving, begin) -> None:
    """§6.9.2 group (ii) — on a v0.2 connection the centre's
    ``session.end`` names WHY the session ended.

    The v0.1 lane keeps the params EXACTLY ``{"session_id": ...}``
    (``test_session_expiry_notifies_the_machine``); the v0.2 lane adds
    the ``reason`` member — ``reaped_by_center`` for the sweep's reap.

    The reap window is centre-owned and not advertised on the wire, so
    the lane is SELF-HOST ONLY (the same gap the v0.1 expiry lane
    names).
    """
    if target.mode != "self-host":
        pytest.skip(
            "SESSION-EXPIRY-GAP: the TTL/idle window is center-owned and "
            "not advertised on the wire — the kit cannot compress a "
            "foreign center's window, so the expiry lane is SELF-HOST ONLY "
            "(COVERAGE.md names this gap)"
        )
    peer = _v02_peer(target)
    try:
        await peer.connect(send_hello=False)
        await peer.hello(
            protocol_version=PROTOCOL_VERSION_V02,
            capabilities=dict(CLIENT_CAPABILITIES_V02),
        )
        peer.start_heartbeats(peer.adopted.get("heartbeat_interval_ms", 1000))
        async with serving(peer, budget_s=25):
            await open_session(peer, target)
            expired_id = peer.session_ids[0]
            await wait_until(lambda: expired_id in peer.ended_sessions, timeout=10)
            if expired_id not in peer.ended_sessions:
                raise _finding(
                    "termination",
                    "v02-expiry-notifies-machine",
                    "§6.8",
                    "no session.end reached the machine when the center's idle "
                    "window passed",
                )
            frame = next(
                (
                    f
                    for f in peer.session_end_frames
                    if str((f.get("params") or {}).get("session_id") or "") == expired_id
                ),
                None,
            )
            params = dict((frame or {}).get("params") or {})
            if params.get("reason") != "reaped_by_center":
                raise _finding(
                    "termination",
                    "v02-expiry-end-reason",
                    "§6.9.2",
                    f"the session.end frame carried {params!r} — a v0.2 "
                    "connection must carry reason='reaped_by_center'",
                )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_the_v02_explicit_end_names_the_superseded_cause(
    target, serving, begin
) -> None:
    """§6.9.2 group (ii) — an explicit end on a v0.2 connection names
    ``superseded``.

    The explicit end is an AGENT-side verb, and the begin-hook contract
    covers BEGIN only — so the lane is SELF-HOST ONLY (the in-kit
    centre drives the agent side in-process).
    """
    if target.mode != "self-host":
        pytest.skip(
            "SESSION-END-GAP: the explicit end is an agent-side verb and "
            "the begin-hook contract covers BEGIN only — the end lane is "
            "SELF-HOST ONLY (COVERAGE.md names this gap)"
        )
    peer = _v02_peer(target)
    try:
        await peer.connect(send_hello=False)
        await peer.hello(
            protocol_version=PROTOCOL_VERSION_V02,
            capabilities=dict(CLIENT_CAPABILITIES_V02),
        )
        peer.start_heartbeats(peer.adopted.get("heartbeat_interval_ms", 1000))
        async with serving(peer, budget_s=25):
            await open_session(peer, target)
            await target.center.agent_end()
            await wait_until(lambda: bool(peer.ended_sessions), timeout=10)
            if not peer.session_end_frames:
                raise _finding(
                    "termination",
                    "v02-explicit-end-frame",
                    "§6.8",
                    "no session.end reached the machine on the explicit end",
                )
            params = dict((peer.session_end_frames[-1].get("params") or {}))
            if params.get("reason") != "superseded":
                raise _finding(
                    "termination",
                    "v02-explicit-end-reason",
                    "§6.9.2",
                    f"the session.end frame carried {params!r} — a v0.2 "
                    "connection must carry reason='superseded'",
                )
    finally:
        await peer.close()
