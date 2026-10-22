"""SPEC §10 area 6 — Auth: the upgrade header is the credential carrier.

The kit tests the UPGRADE behaviour, never a token-issuance flow
(§10): enrollment is out of band in v0.1.  A rejected bearer is HTTP
**401** (never 403 — only 401 is fatal to the shipped client) with NO
protocol frame exchanged; a wrong ``server_pin_sha256`` fails the
handshake even though the chain verifies.

``AUTH_FAILED`` never travels in-band (§6.5) — the refusal is the HTTP
status itself.
"""

from __future__ import annotations

import pytest

from .conformance_peer import (
    ConformanceFinding,
    ConformancePeer,
    UpgradeRefused,
    check_upgrade_refused,
)

pytestmark = [pytest.mark.conformance]


def _peer(target, **kwargs) -> ConformancePeer:
    return ConformancePeer(
        url=target.url,
        token=kwargs.pop("token", target.token),
        machine_id=target.machine_id,
        ca_pem=target.ca_pem or None,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_bearer_travels_on_the_upgrade(target) -> None:
    """§4.1 — the credential is the upgrade header; no in-band auth method."""

    peer = _peer(target)
    try:
        await peer.connect()
        if not peer.transcript.sent():
            raise ConformanceFinding(
                "auth",
                "upgrade-then-hello",
                "§4.1",
                "the connection carried no protocol frame after the bearer upgrade",
                "",
            )
        methods = {f.get("method") for f in peer.transcript.sent()} | {
            f.get("method") for f in peer.transcript.inbound()
        }
        if any(isinstance(m, str) and "auth" in m.lower() for m in methods):
            raise ConformanceFinding(
                "auth",
                "no-in-band-auth",
                "§4.1/§10",
                f"an in-band auth method appeared on the wire: {sorted(m for m in methods if m)}",
                peer.transcript.excerpt(),
            )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_rejected_bearer_is_401_never_403(target) -> None:
    """§4.1/§6.5 — a wrong token answers HTTP 401 and NO protocol frame.

    The "expired bearer" lever (S5) resolves to the same lane: the token
    set is FLAT and rotation is the operator's job (``config.py`` in the
    reference center) — a well-formed but unknown token is the
    machine-observable shape of an expired one.
    """

    peer = _peer(target, token="definitely-not-a-valid-token")
    try:
        try:
            await peer.connect(send_hello=False)
        except UpgradeRefused as exc:
            check_upgrade_refused(exc.status, area="auth")
            if peer.transcript.frames:
                raise ConformanceFinding(
                    "auth",
                    "no-frame-on-refusal",
                    "§4.1",
                    "a protocol frame was exchanged on a refused upgrade",
                    peer.transcript.excerpt(),
                )
            return
        raise ConformanceFinding(
            "auth",
            "rejected-bearer-refused",
            "§4.1",
            "the center ACCEPTED a bogus bearer — every upgrade must be authenticated",
            peer.transcript.excerpt(),
        )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_wrong_server_pin_fails_the_handshake(target) -> None:
    """§4.1 — a wrong ``server_pin_sha256`` refuses the handshake even
    though the certificate chain verifies."""

    peer = _peer(target, server_pin_sha256="00" * 32)
    try:
        try:
            await peer.connect(send_hello=False)
        except ConnectionError as exc:
            if "PIN-MISMATCH" not in str(exc):
                raise ConformanceFinding(
                    "auth",
                    "pin-mismatch-label",
                    "§4.1",
                    f"the pin refusal did not identify itself: {exc}",
                    "",
                ) from exc
            if peer.transcript.frames:
                raise ConformanceFinding(
                    "auth",
                    "pin-before-hello",
                    "§4.1",
                    "a protocol frame was exchanged before the pin was checked",
                    peer.transcript.excerpt(),
                )
            return
        raise ConformanceFinding(
            "auth",
            "wrong-pin-refused",
            "§4.1",
            "a wrong server pin did NOT fail the handshake (the chain verified "
            "and the pin was ignored)",
            peer.transcript.excerpt(),
        )
    finally:
        await peer.close()


def test_enrollment_is_out_of_band() -> None:
    """§10 — the kit never drives a token-issuance flow.

    A stance pin on the kit itself: the peer has no issuance surface —
    the credential is CONFIGURED, never minted over the wire.
    """

    peer = ConformancePeer(url="wss://127.0.0.1:1/x", token="t", machine_id="m")
    surface = [name for name in dir(peer) if "enroll" in name.lower() or "issue" in name.lower()]
    if surface:
        raise AssertionError(
            f"the peer grew a token-issuance surface {surface} — v0.1 enrollment "
            "is out of band (SPEC §10)"
        )
