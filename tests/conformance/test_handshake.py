"""SPEC §10 area 1 — Handshake: upgrade + ``hello`` + version + capabilities.

Every check cites its SPEC clause; a failure raises
:class:`ConformanceFinding` whose label names the area and the check, so
a red in the field is adjudicable (FINDING vs CLIENT-BUG via
``test_peer_selftest.py``).
"""

from __future__ import annotations

import pytest

from .conformance_peer import (
    CLIENT_CAPABILITIES,
    ConformanceFinding,
    ConformancePeer,
    PROTOCOL_VERSION,
    check_error_envelope,
    check_hello_result,
)

pytestmark = [pytest.mark.conformance]


def _peer(target) -> ConformancePeer:
    return ConformancePeer(
        url=target.url,
        token=target.token,
        machine_id=target.machine_id,
        ca_pem=target.ca_pem or None,
    )


@pytest.mark.asyncio
async def test_upgrade_and_hello_handshake(target) -> None:
    """§5.1/§5.2/§5.4 — a bearer upgrade; hello echoed with version, caps, heartbeats."""

    peer = _peer(target)
    try:
        await peer.connect()
        answer = peer.transcript.inbound()[-1]
        sent = peer.transcript.sent()[-1]
        check_hello_result("handshake", answer, sent["id"], transcript=peer.transcript)
        # §5.4 — the SERVER half of the result is named.
        server = answer["result"].get("server") or {}
        if not isinstance(server, dict) or not server.get("name"):
            raise ConformanceFinding(
                "handshake",
                "hello-server-name",
                "§5.4",
                f"the hello result carries no server name: {server!r}",
                peer.transcript.excerpt(),
            )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_version_mismatch_refused_no_downgrade(target) -> None:
    """§5.2/§5.5 — PROTOCOL_VERSION_UNSUPPORTED + data{requested,supported} + close."""

    peer = _peer(target)
    try:
        await peer.connect(send_hello=False)
        answer = await peer.hello(protocol_version="9.9", expect_error=True)
        error = check_error_envelope(
            "handshake",
            answer,
            "PROTOCOL_VERSION_UNSUPPORTED",
            expect_data=False,
            transcript=peer.transcript,
        )
        data = error.get("data") or {}
        # The SPEC row promises the negotiated pair where the center
        # provides data; tolerate a data-less error (§6.5 makes data
        # optional) but a PRESENT data member must carry the pair.
        if data:
            if data.get("requested") != "9.9":
                raise ConformanceFinding(
                    "handshake",
                    "version-mismatch-data",
                    "§5.2",
                    f"data.requested is {data.get('requested')!r}, want '9.9'",
                    peer.transcript.excerpt(),
                )
            if PROTOCOL_VERSION not in (data.get("supported") or []):
                raise ConformanceFinding(
                    "handshake",
                    "version-mismatch-data",
                    "§5.2",
                    f"data.supported {data.get('supported')!r} does not list {PROTOCOL_VERSION!r}",
                    peer.transcript.excerpt(),
                )
        # NO downgrade: the center must not answer a response envelope,
        # and it must CLOSE the socket.
        if answer.get("type") == "response":
            raise ConformanceFinding(
                "handshake",
                "no-silent-downgrade",
                "§5.2",
                "the center answered a hello response for an unsupported version",
                peer.transcript.excerpt(),
            )
        closed = await peer.wait_closed(timeout=5.0)
        if not closed:
            raise ConformanceFinding(
                "handshake",
                "version-mismatch-closes",
                "§5.2",
                "the center did not close the socket after the version refusal",
                peer.transcript.excerpt(),
            )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_capability_map_offering_side_reading(target) -> None:
    """§5.3 — ``notifications: false`` is accepted (offering-side reading).

    The kit advertises ``notifications: false`` (the shipped client's
    honest advertisement); enforcing the literal "required" column
    against the machine would kill every connection.
    """

    peer = _peer(target)
    try:
        await peer.connect()
        answer = peer.transcript.inbound()[-1]
        if answer.get("type") != "response":
            raise ConformanceFinding(
                "handshake",
                "notifications-false-accepted",
                "§5.3",
                "the center refused a machine advertising notifications:false",
                peer.transcript.excerpt(),
            )
        # And the map the center advertises must be a flat boolean map.
        caps = (answer.get("result") or {}).get("capabilities")
        if not isinstance(caps, dict) or not all(isinstance(v, bool) for v in caps.values()):
            raise ConformanceFinding(
                "handshake",
                "capability-map-shape",
                "§5.3",
                f"the advertised capability map is not flat booleans: {caps!r}",
                peer.transcript.excerpt(),
            )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_missing_required_capability_refused(target) -> None:
    """§5.3 — a machine missing ``tools`` is refused CAPABILITY_MISSING."""

    peer = _peer(target)
    try:
        await peer.connect(send_hello=False)
        caps = dict(CLIENT_CAPABILITIES)
        caps.pop("tools", None)
        answer = await peer.hello(capabilities=caps, expect_error=True)
        error = check_error_envelope(
            "handshake",
            answer,
            "CAPABILITY_MISSING",
            transcript=peer.transcript,
        )
        data = error.get("data") or {}
        if data and "tools" not in (data.get("missing") or []):
            raise ConformanceFinding(
                "handshake",
                "capability-missing-data",
                "§5.3",
                f"data.missing {data.get('missing')!r} does not name 'tools'",
                peer.transcript.excerpt(),
            )
        closed = await peer.wait_closed(timeout=5.0)
        if not closed:
            raise ConformanceFinding(
                "handshake",
                "capability-missing-closes",
                "§5.3",
                "the center did not close the socket after CAPABILITY_MISSING",
                peer.transcript.excerpt(),
            )
    finally:
        await peer.close()
