"""SPEC §10 area 3 — Transport: heartbeat, silence kill, liveness, reconnect,
frame size.

Design notes:

* The heartbeat kill window is ADAPTIVE: the center advertises
  ``heartbeat_timeout_ms`` and the kit measures against the ADVERTISED
  value.  A window longer than the kit's wait budget is SKIPPED with the
  reason (a foreign center's knobs cannot be compressed from the machine
  side) — never silently ignored.
* The frame-size row targets the CLIENT-side MUST (§6.7 +
  ``internal/remote/transport.go:63-90``): the SPEC places NO
  enforcement duty on the center, so the center's behaviour on an
  oversized machine→center frame is RECORDED, never a default red.
  The peer's OWN duty (accept up to the ceiling, answer
  ``PAYLOAD_TOO_LARGE``, survive) is pinned in ``test_peer_selftest.py``.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from .conformance_peer import (
    ConformanceFinding,
    ConformancePeer,
    DEFAULT_MAX_FRAME_BYTES,
    check_close_code,
    check_g1_heartbeat_timeout_frame,
)

pytestmark = [pytest.mark.conformance]

#: The kit's wait budget for a silence kill: [T, 2T] must fit inside it.
SILENCE_BUDGET_MS = 8000


def _peer(target, **kwargs) -> ConformancePeer:
    return ConformancePeer(
        url=target.url,
        token=target.token,
        machine_id=target.machine_id,
        ca_pem=target.ca_pem or None,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_heartbeat_cadence_adopted(target) -> None:
    """§3.3 — the client adopts the SERVER's advertised heartbeat values."""

    peer = _peer(target)
    try:
        await peer.connect()
        result = peer.adopted
        interval = result.get("heartbeat_interval_ms")
        timeout = result.get("heartbeat_timeout_ms")
        dead = result.get("client_dead_detect_ms")
        # The receiver-side validation the shipped client applies
        # (``internal/remote/client.go:651-665``): timeout >= 3x interval.
        if not isinstance(interval, int) or interval <= 0:
            raise ConformanceFinding(
                "transport", "heartbeat-interval", "§3.3",
                f"heartbeat_interval_ms is {interval!r}", peer.transcript.excerpt(),
            )
        if not isinstance(timeout, int) or timeout < 3 * interval:
            raise ConformanceFinding(
                "transport", "heartbeat-timeout-ge-3x", "§3.3",
                f"heartbeat_timeout_ms={timeout!r} violates timeout >= 3x interval ({interval})",
                peer.transcript.excerpt(),
            )
        if not isinstance(dead, int) or dead < 3 * interval:
            raise ConformanceFinding(
                "transport", "client-dead-detect-ge-3x", "§3.3",
                f"client_dead_detect_ms={dead!r} violates dead-detect >= 3x interval ({interval})",
                peer.transcript.excerpt(),
            )
        # Adopt: heartbeat on the advertised cadence for ~3 beats.
        peer.start_heartbeats(interval)
        beats_before = sum(
            1 for f in peer.transcript.frames
            if f.direction == "machine->center" and '"heartbeat"' in f.raw
        )
        await asyncio.sleep(min(3 * interval / 1000.0, 4.0))
        beats_after = sum(
            1 for f in peer.transcript.frames
            if f.direction == "machine->center" and '"heartbeat"' in f.raw
        )
        if beats_after - beats_before < 2:
            raise ConformanceFinding(
                "transport", "heartbeat-cadence", "§3.3",
                "the adopted cadence did not produce >=2 heartbeats in the window",
                peer.transcript.excerpt(),
            )
        # The center did not kill a heartbeating machine.
        if peer._ws is None or peer._ws.state.name != "OPEN":
            raise ConformanceFinding(
                "transport", "heartbeating-machine-survives", "§3.3",
                "the center killed a machine that was heartbeating on the adopted cadence",
                peer.transcript.excerpt(),
            )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_kill_on_silence(target, strict_close) -> None:
    """§3.3/§6.8 — a silent machine is killed and closed after
    ``heartbeat_timeout_ms`` (measured against the ADVERTISED value)."""

    peer = _peer(target)
    try:
        await peer.connect()
        timeout_ms = int(peer.adopted.get("heartbeat_timeout_ms") or 0)
        if timeout_ms <= 0:
            raise ConformanceFinding(
                "transport", "kill-on-silence-precondition", "§3.3",
                "the center advertised no heartbeat_timeout_ms — the kill cannot be measured",
                peer.transcript.excerpt(),
            )
        if timeout_ms > SILENCE_BUDGET_MS:
            pytest.skip(
                f"the center advertises heartbeat_timeout_ms={timeout_ms}, beyond "
                f"the kit's wait budget ({SILENCE_BUDGET_MS} ms) — point the center "
                "at compressed knobs (the interop harness ships 500/1500) or raise "
                "the budget in test_transport.py (COVERAGE.md names this)"
            )
        started = time.monotonic()
        await peer.serve(silence=True, budget_s=(2 * timeout_ms / 1000.0) + 6)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        if peer.transcript.close_code is None and peer._ws is not None:
            peer._note_close()
        if peer.transcript.close_code is None:
            raise ConformanceFinding(
                "transport", "kill-on-silence", "§3.3",
                f"the center did not close the socket within {elapsed_ms:.0f} ms "
                f"(advertised timeout {timeout_ms} ms)",
                peer.transcript.excerpt(),
            )
        # The kill fires within [T, 2T] (+ small sweep slack).
        if elapsed_ms < timeout_ms * 0.9 or elapsed_ms > timeout_ms * 2 + 1500:
            peer.transcript.warn(
                f"kill-on-silence: observed {elapsed_ms:.0f} ms, advertised {timeout_ms} ms "
                "(the sweep granularity makes the exact edge fuzzy; recorded, not failed)"
            )
        # The typed refusal was sent in-band before the close — and the
        # G1 pin (current behaviour with marker) applies to its id.
        error_frames = [
            f.frame for f in peer.transcript.frames
            if f.direction == "center->machine" and (f.frame or {}).get("type") == "error"
        ]
        heartbeat_errors = [
            e for e in error_frames
            if ((e.get("error") or {}).get("code")) == "HEARTBEAT_TIMEOUT"
        ]
        if not heartbeat_errors:
            raise ConformanceFinding(
                "transport", "heartbeat-timeout-frame", "§3.3/§6.5",
                "the center closed a silent machine without a HEARTBEAT_TIMEOUT error frame",
                peer.transcript.excerpt(),
            )
        check_g1_heartbeat_timeout_frame(heartbeat_errors[0], peer.transcript)
        check_close_code(peer.transcript, strict=strict_close)
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_liveness_evidence_present(target) -> None:
    """§3.3 — the center must emit SOME evidence of life within
    ``client_dead_detect_ms`` (or a correct client declares it dead).

    The machine-observable proxy is an inbound PROTOCOL frame or a
    WebSocket control ping.  Pings are handled below the protocol layer
    by the ``websockets`` client and are not exposed to application
    code, so this check measures inbound frames and RECORDS (never
    fails) when only the invisible ping lane could have produced
    evidence — COVERAGE.md names the limitation.
    """

    peer = _peer(target)
    try:
        await peer.connect()
        dead_ms = int(peer.adopted.get("client_dead_detect_ms") or 0)
        window = min(max(dead_ms, 500), 3000) / 1000.0
        started = time.monotonic()
        saw_frame = False
        while time.monotonic() - started < window:
            try:
                await asyncio.wait_for(peer._ws.recv(), timeout=0.25)
                saw_frame = True
                break
            except (asyncio.TimeoutError, TimeoutError):
                continue
            except Exception:  # noqa: BLE001 — a close is evidence too
                saw_frame = True
                break
        if not saw_frame:
            peer.transcript.warn(
                f"liveness: no inbound frame within {window:.1f}s — the center may be "
                "pinging below the protocol layer (invisible to the websockets client "
                "API); a WS-ping-only center is legal under §3.3 (recorded, not failed)"
            )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_reconnect_fresh_session_no_resumption(target, serving, begin) -> None:
    """§6.8 — a fresh upgrade + hello + begin after a break; NO resumption."""

    first = _peer(target)
    old_session_ids: list[str] = []
    try:
        await first.connect()
        first.start_heartbeats(first.adopted.get("heartbeat_interval_ms", 1000))
        async with serving(first, budget_s=20):
            await _open(first, target, begin)
            old_session_ids = list(first.session_ids)
    finally:
        await first.close()

    if not old_session_ids:
        pytest.skip("no session was opened before the break (begin hook lane)")

    second = _peer(target)
    try:
        await second.connect()
        second.start_heartbeats(second.adopted.get("heartbeat_interval_ms", 1000))
        async with serving(second, budget_s=20):
            await _open(second, target, begin)
            if not second.session_ids:
                raise ConformanceFinding(
                    "transport", "reconnect-begin", "§6.8",
                    "a fresh connection could not open a session after the break",
                    second.transcript.excerpt(),
                )
            if second.session_ids[0] in old_session_ids:
                raise ConformanceFinding(
                    "transport", "no-resumption", "§6.8",
                    "the reconnected machine RESUMED the old session id — v0.1 has no resumption",
                    second.transcript.excerpt(),
                )
    finally:
        await second.close()


async def _open(peer: ConformancePeer, target, begin) -> None:
    from .conformance_peer import open_session

    await open_session(peer, target)


@pytest.mark.asyncio
async def test_frame_size_observation(target) -> None:
    """§6.7 — an oversized machine→center frame: RECORD the center's answer.

    The §6.7 MUST is CLIENT-side (``transport.go:63-90``) — the SPEC
    places no enforcement duty on the center.  So this check passes on
    EITHER branch and records which one the center took: a
    ``PAYLOAD_TOO_LARGE`` answer (a center mirroring the client MUST —
    the reference center here does) or a silent accept (the known
    v0.1/§6.7 gap of this repo's daemon, reported as a warning).
    """

    peer = _peer(target)
    try:
        await peer.connect()
        pad = "x" * (DEFAULT_MAX_FRAME_BYTES + 65536)
        oversized = {
            "v": "0.1",
            "type": "request",
            "id": "oversized-1",
            "method": "hello",
            "params": {"protocol_version": "0.1", "machine_id": peer.machine_id, "pad": pad},
        }
        await peer._send_frame(oversized)
        answer = None
        try:
            answer = await peer._recv_frame(timeout=5.0)
        except (asyncio.TimeoutError, TimeoutError):
            answer = None
        except Exception:  # noqa: BLE001 — a close is an answer too
            answer = None
        code = None
        if isinstance(answer, dict) and answer.get("type") == "error":
            code = ((answer.get("error") or {}).get("code"))
        if code == "PAYLOAD_TOO_LARGE":
            # The conformant branch: the shape must be right.
            if answer.get("id") not in ("oversized-1", None):
                raise ConformanceFinding(
                    "transport", "payload-too-large-id", "§6.1",
                    f"the PAYLOAD_TOO_LARGE error carries id {answer.get('id')!r}",
                    peer.transcript.excerpt(),
                )
            message = ((answer.get("error") or {}).get("message"))
            if not isinstance(message, str) or not message:
                raise ConformanceFinding(
                    "transport", "payload-too-large-message", "§6.5",
                    "the PAYLOAD_TOO_LARGE error carries no advisory message",
                    peer.transcript.excerpt(),
                )
            peer.transcript.warn(
                "frame-size: the center answered PAYLOAD_TOO_LARGE and the connection "
                "survived (the center mirrors the §6.7 client-side MUST)"
            )
        else:
            peer.transcript.warn(
                "frame-size: an oversized machine→center frame was NOT answered "
                f"PAYLOAD_TOO_LARGE (answer={json.dumps(answer)[:120] if answer else 'none/closed'}) — "
                "the SPEC puts the enforcement MUST on the client (§6.7), so this is a "
                "RECORDED observation, not a center red (the daemon's §6.7 gap is known)"
            )
    finally:
        await peer.close()
