"""SPEC §10 area 5 — Errors: every v0.1-reachable typed code, correctly
shaped; ``data`` optional; ``TOOL_DENIED`` never emitted.

"Reachable from the MACHINE side" is the kit's scope: the codes the
center sends a machine are ``PROTOCOL_VERSION_UNSUPPORTED``,
``CAPABILITY_MISSING``, ``MACHINE_NOT_ENROLLED`` and ``INTERNAL`` (plus
``HEARTBEAT_TIMEOUT`` on the silence kill — pinned in
``test_transport.py``).  The agent-side codes (``MACHINE_BUSY``,
``SESSION_NOT_FOUND``, ``SESSION_EXPIRED``, the relayed tool codes) are
driven through the begin hook where it exists and named in COVERAGE.md
where it does not.
"""

from __future__ import annotations

import pytest

from .conformance_peer import (
    ALL_ERROR_CODES,
    ConformanceFinding,
    ConformancePeer,
    check_error_envelope,
    check_no_tool_denied,
)

pytestmark = [pytest.mark.conformance]

#: §6.5 — the v0.1-reachable set.
REACHABLE = {
    "PROTOCOL_VERSION_UNSUPPORTED",
    "CAPABILITY_MISSING",
    "AUTH_FAILED",
    "MACHINE_NOT_ENROLLED",
    "MACHINE_BUSY",
    "SESSION_NOT_FOUND",
    "SESSION_EXPIRED",
    "TOOL_NOT_FOUND",
    "TOOL_TIMEOUT",
    "TOOL_ERROR",
    "PAYLOAD_TOO_LARGE",
    "RATE_LIMITED",
    "HEARTBEAT_TIMEOUT",
    "INTERNAL",
}


def _peer(target, **kwargs) -> ConformancePeer:
    return ConformancePeer(
        url=target.url,
        token=target.token,
        machine_id=target.machine_id,
        ca_pem=target.ca_pem or None,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_machine_not_enrolled_shape(target) -> None:
    """§6.5 — a machine the center does not know is refused, then closed."""

    peer = _peer(target)
    try:
        await peer.connect(send_hello=False)
        answer = await peer.hello(machine_id="", expect_error=True)
        check_error_envelope(
            "errors", answer, "MACHINE_NOT_ENROLLED", transcript=peer.transcript
        )
        if not await peer.wait_closed(timeout=5.0):
            raise ConformanceFinding(
                "errors",
                "machine-not-enrolled-closes",
                "§6.5",
                "the center did not close the socket after MACHINE_NOT_ENROLLED",
                peer.transcript.excerpt(),
            )
        check_no_tool_denied(peer.transcript)
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_second_hello_internal(target) -> None:
    """§5.1/§6.5 — a second hello on one connection answers INTERNAL + close."""

    peer = _peer(target)
    try:
        await peer.connect()
        answer = await peer.hello(expect_error=True)
        check_error_envelope("errors", answer, "INTERNAL", transcript=peer.transcript)
        if not await peer.wait_closed(timeout=5.0):
            raise ConformanceFinding(
                "errors",
                "second-hello-closes",
                "§5.1",
                "the center did not close the socket after a second hello",
                peer.transcript.excerpt(),
            )
    finally:
        await peer.close()


def test_error_data_is_optional() -> None:
    """§6.5 — an error with NO ``data`` member is legal; the kit tolerates it.

    Pins the CHECK itself: ``expect_data=False`` accepts a data-less
    error, ``expect_data=True`` reds on it — so the tolerance is proven,
    not assumed.
    """

    bare = {
        "v": "0.1",
        "type": "error",
        "id": "x-1",
        "error": {"code": "INTERNAL", "message": "boom"},
    }
    check_error_envelope("errors", bare, "INTERNAL")  # must NOT raise
    try:
        check_error_envelope("errors", bare, "INTERNAL", expect_data=True)
    except ConformanceFinding as finding:
        assert "error-data-internal" in str(finding), finding
    else:  # pragma: no cover — the check would be vacuous
        raise AssertionError("expect_data=True accepted a data-less error")


def test_error_vocabulary_is_complete_and_tool_denied_reserved() -> None:
    """§6.5 — the kit's vocabulary matches the SPEC; TOOL_DENIED is v0.2+."""

    if set(ALL_ERROR_CODES) != REACHABLE | {"TOOL_DENIED"}:
        raise AssertionError(
            f"the kit's error vocabulary drifted: {sorted(set(ALL_ERROR_CODES) ^ (REACHABLE | {'TOOL_DENIED'}))}"
        )
    assert "TOOL_DENIED" not in REACHABLE
