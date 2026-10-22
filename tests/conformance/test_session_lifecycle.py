"""SPEC §10 area 2 — Session lifecycle: begin → end, expiry, MACHINE_BUSY.

The center initiates ``session.begin`` / ``session.end`` (the center is
the REQUESTOR, §6.3); the machine answers.  A center whose agent side
cannot be driven needs a begin hook (``CONFORMANCE_BEGIN_CMD`` /
``CONFORMANCE_BEGIN_URL``) — without one these checks FAIL FAST with
the named gap (B8a); a center that cannot provide a hook is tested by
the machine-facing subset (COVERAGE.md names the gap).
"""

from __future__ import annotations

import asyncio

import pytest

from .conformance_peer import (
    ConformanceFinding,
    ConformancePeer,
    HookError,
    SessionBeginGap,
    open_session,
    wait_until,
)

pytestmark = [pytest.mark.conformance]


def _peer(target, **kwargs) -> ConformancePeer:
    return ConformancePeer(
        url=target.url,
        token=target.token,
        machine_id=target.machine_id,
        ca_pem=target.ca_pem or None,
        **kwargs,
    )


async def _ready(target, peer: ConformancePeer) -> None:
    await peer.connect()
    peer.start_heartbeats(peer.adopted.get("heartbeat_interval_ms", 1000))


@pytest.mark.asyncio
async def test_begin_to_end_and_back_to_ready(target, serving, begin) -> None:
    """§6.4/§6.8 — begin yields a ``sess_`` id + a catalog; end acks; socket
    stays open; the machine returns to ready (a second session opens)."""

    peer = _peer(target)
    try:
        await _ready(target, peer)
        async with serving(peer, budget_s=20):
            await open_session(peer, target)
            if not peer.session_ids[0].startswith("sess_"):
                raise ConformanceFinding(
                    "session",
                    "begin-session-id",
                    "§6.4",
                    f"the machine-minted session id {peer.session_ids[0]!r} "
                    "does not carry the sess_ shape",
                    peer.transcript.excerpt(),
                )
            if not peer.sent_catalog:
                raise ConformanceFinding(
                    "session",
                    "begin-catalog",
                    "§6.4",
                    "the session.begin answer carried no catalog",
                    peer.transcript.excerpt(),
                )
            if target.mode != "self-host":
                # The begin-hook contract covers BEGIN only; there is no
                # agent-side END verb to drive through a foreign hook, so
                # the end-ack lane is named in COVERAGE.md for pointed
                # mode and driven in-process here.
                peer.transcript.warn(
                    "session-end: the begin lane ran; the end-ack lane needs an "
                    "agent-side end verb the hook contract does not define "
                    "(COVERAGE.md names the pointed-mode gap)"
                )
                return
            await target.center.agent_end()
            await wait_until(lambda: bool(peer.ended_sessions), timeout=5)
            if not peer.ended_sessions:
                raise ConformanceFinding(
                    "session",
                    "end-frame-arrived",
                    "§6.8",
                    "no session.end request reached the machine",
                    peer.transcript.excerpt(),
                )
            # §6.8 — the socket stays open; a second session opens on it.
            if peer._ws is None or peer._ws.state.name != "OPEN":
                raise ConformanceFinding(
                    "session",
                    "socket-survives-end",
                    "§6.8",
                    "the socket closed on session.end (the machine must return to ready)",
                    peer.transcript.excerpt(),
                )
            second = await target.center.agent_begin()
            await wait_until(lambda: len(peer.session_ids) >= 2, timeout=5)
            if len(peer.session_ids) < 2:
                raise ConformanceFinding(
                    "session",
                    "ready-after-end",
                    "§6.8",
                    "a second session could not open on the same connection",
                    peer.transcript.excerpt(),
                )
            if second["session_id"] == peer.session_ids[0]:
                raise ConformanceFinding(
                    "session",
                    "fresh-session-id",
                    "§6.4",
                    "the second session reused the first id",
                    peer.transcript.excerpt(),
                )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_session_expiry_center_owned(target, serving, begin) -> None:
    """§3.3 — a session past the center's idle window is refused center-side.

    The TTL/idle window is CENTER-owned and NOT advertised on the wire;
    a foreign center's window cannot be compressed from the machine
    side, and §6.8 sends the machine no expiry frame.  This check runs
    in the self-consistency lane (the in-kit center's window is
    compressed to 2 s); pointed mode names the gap in COVERAGE.md.
    """

    if target.mode != "self-host":
        pytest.skip(
            "SESSION-EXPIRY-GAP: the TTL/idle window is center-owned and "
            "not advertised on the wire — the kit cannot compress a foreign "
            "center's window, and §6.8 sends the machine no expiry frame "
            "(COVERAGE.md names this gap)"
        )
    peer = _peer(target)
    try:
        await _ready(target, peer)
        async with serving(peer, budget_s=25):
            await open_session(peer, target)
            await asyncio.sleep(target.center.SESSION_IDLE_TIMEOUT_MS / 1000.0 + 0.6)
            from .conformance_peer import CenterRefused

            refused = ""
            try:
                await target.center.agent_call("kit_echo", {"payload": "late"})
            except CenterRefused as exc:
                refused = str(exc)
            if refused != "SESSION_EXPIRED":
                raise ConformanceFinding(
                    "session",
                    "expiry-refused",
                    "§3.3",
                    f"a call past the idle window was answered {refused or 'successfully'!r}, "
                    "want a SESSION_EXPIRED refusal",
                    peer.transcript.excerpt(),
                )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_machine_busy_refused_by_center_before_the_machine(target, serving, begin) -> None:
    """§8.3 — a second session on a busy machine is refused by the CENTER,
    before any frame reaches the machine."""

    peer = _peer(target)
    try:
        await _ready(target, peer)
        async with serving(peer, budget_s=20):
            await open_session(peer, target)
            before = len(peer.session_ids)
            refused = False
            try:
                await begin()
            except (HookError, SessionBeginGap):
                refused = True
            # A late frame would still be a §8.3 violation.
            await asyncio.sleep(0.4)
            if len(peer.session_ids) != before:
                raise ConformanceFinding(
                    "session",
                    "machine-busy-center-side",
                    "§8.3",
                    "a second session.begin reached the machine while busy — "
                    "the refusal must happen at the center",
                    peer.transcript.excerpt(),
                )
            if not refused:
                peer.transcript.warn(
                    "machine-busy: the second begin did NOT report a refusal through "
                    "the operator hook — the wire stayed clean (the §8.3 invariant), "
                    "but the hook could not evidence the refusal (reported)"
                )
    finally:
        await peer.close()
