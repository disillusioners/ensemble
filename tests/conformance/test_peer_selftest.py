"""The kit's selftest — proves the PEER is right, and that the kit can go
RED for the right reason.

Why this file exists: without it, every failure in the field is
ambiguous.  With it:

* a green run against the in-kit REFERENCE center proves the peer (and
  the check helpers) can pass a correct peer;
* each MUTATED center (B8e: ``v: "0.2"``, a missing
  ``heartbeat_timeout_ms``, a 403 refusal, an oversized inbound frame,
  a 1008 close) MUST make the corresponding check go RED with the
  CORRECT FINDING label — a kit that passes everything proves only that
  it asserts nothing;
* the pointing surface is pinned to EXACTLY five keys and EXACTLY eight
  enumerated keys in total (5 pointing + 2 begin-hook + 1 strict);
* the begin-hook contract (CMD / URL / unset) is pinned.

So a red in the field is a **FINDING** (center defect) unless THIS file
also reds — that would be a **CLIENT-BUG** (the kit's fault).
"""

from __future__ import annotations

import asyncio
import http.server
import threading

import pytest

from .conformance_peer import (
    BEGIN_HOOK_KEYS,
    CONFORMANCE_ENV_KEYS,
    POINTING_KEYS,
    STRICT_KEYS,
    CenterMutations,
    ConformanceFinding,
    ConformancePeer,
    HookError,
    InKitCenter,
    SessionBeginGap,
    Target,
    check_close_code,
    check_hello_result,
    check_upgrade_refused,
    hash_args_python_center,
    open_session,
    parse_hook_output,
    resolve_target,
    run_begin_hook,
)
from .conformance_peer import Transcript

pytestmark = [pytest.mark.conformance]


# ── the pointing surface (B8d) ────────────────────────────────────────────


def test_pointing_surface_is_exactly_five_keys() -> None:
    """The POINTING surface is exactly the five documented keys."""

    assert POINTING_KEYS == (
        "CONFORMANCE_CENTER_URL",
        "CONFORMANCE_TOKEN",
        "CONFORMANCE_CA_PEM",
        "CONFORMANCE_MACHINE_ID",
        "CONFORMANCE_EXPECTED_AGENT",
    )


def test_total_enumerated_key_set_is_eight() -> None:
    """The TOTAL enumerated set is 5 pointing + 2 begin-hook + 1 strict = 8.

    An undocumented knob cannot appear, and a documented one cannot
    vanish.
    """

    assert len(CONFORMANCE_ENV_KEYS) == 8, CONFORMANCE_ENV_KEYS
    assert set(CONFORMANCE_ENV_KEYS) == set(POINTING_KEYS) | set(BEGIN_HOOK_KEYS) | set(
        STRICT_KEYS
    )
    assert len(set(CONFORMANCE_ENV_KEYS)) == 8  # no duplicate


# ── the begin-hook contract (B8a) ─────────────────────────────────────────


def test_begin_hook_cmd_contract() -> None:
    """CMD: stdout line 1 = session id; an optional JSON echo follows."""

    out = run_begin_hook(
        "printf 'sess_abc123\\n{\"agent_catalog\": []}\\n'", None
    )
    assert out.ok and out.session_id == "sess_abc123"
    assert out.echo == {"agent_catalog": []}


def test_begin_hook_cmd_failure_is_hook_error() -> None:
    """A failing CMD is an OPERATOR error (HOOK-ERROR), never a finding."""

    with pytest.raises(HookError):
        run_begin_hook("exit 7", None)


def test_begin_hook_url_contract() -> None:
    """URL: a 2xx endpoint's body carries the same hook document."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server API
            body = b"sess_url123\n{\"agent_catalog\": []}\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # noqa: A003 — silence the stub
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        out = run_begin_hook(None, f"http://127.0.0.1:{port}/begin")
        assert out.ok and out.session_id == "sess_url123"
        assert out.echo == {"agent_catalog": []}
    finally:
        server.shutdown()
        server.server_close()


def test_begin_hook_url_failure_is_hook_error() -> None:
    """A non-2xx endpoint is an OPERATOR error."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args):  # noqa: A003
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with pytest.raises(HookError):
            run_begin_hook(None, f"http://127.0.0.1:{port}/begin")
    finally:
        server.shutdown()
        server.server_close()


def test_no_hook_fails_fast_with_the_named_gap() -> None:
    """Neither hook set ⇒ SessionBeginGap with the actionable message."""

    with pytest.raises(SessionBeginGap) as excinfo:
        run_begin_hook(None, None)
    message = str(excinfo.value)
    assert "SESSION-BEGIN-GAP" in message
    assert "CONFORMANCE_BEGIN_CMD" in message and "CONFORMANCE_BEGIN_URL" in message


def test_pointed_target_without_hook_reports_no_hook() -> None:
    target = resolve_target({"CONFORMANCE_CENTER_URL": "wss://center.example/x"})
    assert target.mode == "pointed" and not target.has_begin_hook
    hooked = resolve_target(
        {
            "CONFORMANCE_CENTER_URL": "wss://center.example/x",
            "CONFORMANCE_BEGIN_CMD": "echo sess-1",
        }
    )
    assert hooked.has_begin_hook


def test_hook_output_parser() -> None:
    session_id, echo = parse_hook_output("  sess_x \n\n")
    assert session_id == "sess_x" and echo == {}
    session_id, echo = parse_hook_output("sess_y\nnot-json\n")
    assert session_id == "sess_y" and echo == {}


# ── the green baseline: the peer passes a CORRECT center ─────────────────


async def _reference_run() -> None:
    center = await InKitCenter().start()
    peer = ConformancePeer(
        url=center.url, token=center.token, machine_id=center.machine_id, ca_pem=str(center.cert_path)
    )
    try:
        await peer.connect()
        check_hello_result(
            "handshake", peer.transcript.inbound()[-1], peer.transcript.sent()[-1]["id"],
            transcript=peer.transcript,
        )
        peer.start_heartbeats(peer.adopted["heartbeat_interval_ms"])
        task = asyncio.create_task(peer.serve(budget_s=15))
        try:
            outcome = await open_session(peer, Target(mode="self-host", center=center))
            assert outcome.session_id and peer.session_ids
            result = await center.agent_call("kit_echo", {"payload": "x", "n": 1})
            assert result == {"content": [{"type": "text", "text": "conformance-echo"}], "isError": False}
            ack = await center.agent_end()
            assert ack["session_id"] == outcome.session_id
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        await peer.close()
        await center.stop()


def test_peer_green_against_the_reference_center() -> None:
    """The peer + the checks pass a CORRECT center (the discriminators' base)."""

    asyncio.run(_reference_run())


# ── the mutated-center negative controls (B8e) ───────────────────────────


async def _run_check_against_mutation(mutations: CenterMutations, check) -> None:
    center = await InKitCenter(mutations=mutations).start()
    peer = ConformancePeer(
        url=center.url, token=center.token, machine_id=center.machine_id, ca_pem=str(center.cert_path)
    )
    try:
        await peer.connect()
        await check(peer, center)
    finally:
        await peer.close()
        await center.stop()


async def _check_version_reply(peer: ConformancePeer, center: InKitCenter) -> None:
    answer = peer.transcript.inbound()[-1]
    check_hello_result(
        "handshake", answer, peer.transcript.sent()[-1]["id"], transcript=peer.transcript
    )


def test_mutated_version_reply_reds_the_handshake() -> None:
    """A center answering protocol_version "0.2" makes the kit RED with the
    CORRECT label (FINDING handshake/hello-protocol-version)."""

    mutations = CenterMutations(hello_version_reply="0.2")
    with pytest.raises(ConformanceFinding) as excinfo:
        asyncio.run(_run_check_against_mutation(mutations, _check_version_reply))
    assert "FINDING handshake/hello-protocol-version" in str(excinfo.value)
    assert "transcript:" in str(excinfo.value)


async def _check_heartbeat_params(peer: ConformancePeer, center: InKitCenter) -> None:
    answer = peer.transcript.inbound()[-1]
    check_hello_result(
        "handshake", answer, peer.transcript.sent()[-1]["id"], transcript=peer.transcript
    )


def test_mutated_missing_heartbeat_timeout_reds_the_handshake() -> None:
    """A center omitting ``heartbeat_timeout_ms`` makes the kit RED with the
    CORRECT label (FINDING handshake/hello-heartbeat-params)."""

    mutations = CenterMutations(omit_heartbeat_timeout_ms=True)
    with pytest.raises(ConformanceFinding) as excinfo:
        asyncio.run(_run_check_against_mutation(mutations, _check_heartbeat_params))
    assert "FINDING handshake/hello-heartbeat-params" in str(excinfo.value)


async def _check_refused_status(peer: ConformancePeer, center: InKitCenter) -> None:
    # The mutated center refuses the upgrade with 403 instead of 401.
    raise AssertionError("unreachable — the refusal happens in connect()")


def test_mutated_403_reds_the_auth_lane() -> None:
    """A center refusing with 403 (rather than 401) makes the kit RED with
    the CORRECT label (FINDING auth/upgrade-401-never-403)."""

    async def scenario() -> None:
        center = await InKitCenter(mutations=CenterMutations(refuse_token=True)).start()
        peer = ConformancePeer(
            url=center.url, token="wrong", machine_id=center.machine_id, ca_pem=str(center.cert_path)
        )
        try:
            try:
                await peer.connect(send_hello=False)
            except Exception as exc:  # UpgradeRefused
                status = getattr(exc, "status", None)
                check_upgrade_refused(status, area="auth")
            else:  # pragma: no cover
                raise AssertionError("the mutated center accepted a bogus bearer")
        finally:
            await peer.close()
            await center.stop()

    with pytest.raises(ConformanceFinding) as excinfo:
        asyncio.run(scenario())
    assert "FINDING auth/upgrade-401-never-403" in str(excinfo.value)
    assert "403" in str(excinfo.value)


async def _check_peer_oversized_must(peer: ConformancePeer, center: InKitCenter) -> None:
    # The mutated center sent an oversized REQUEST right after hello; the
    # PEER must answer PAYLOAD_TOO_LARGE and keep the connection (§6.7
    # client-side MUST, transport.go:63-90).
    async def _until_payload_too_large() -> bool:
        from .conformance_peer import wait_until

        return await wait_until(
            lambda: any('"PAYLOAD_TOO_LARGE"' in str(f) and f.direction == "machine->center"
                        for f in peer.transcript.frames),
            timeout=10,
        )

    task = asyncio.create_task(peer.serve(budget_s=15))
    try:
        answered = await _until_payload_too_large()
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    assert answered, "the peer did not answer an oversized inbound request with PAYLOAD_TOO_LARGE"
    assert peer._ws is not None and peer._ws.state.name == "OPEN", (
        "the peer dropped the connection on an oversized request — §6.7 requires survival"
    )


def test_peer_answers_oversized_inbound_with_payload_too_large() -> None:
    """The CLIENT-side §6.7 MUST: answer PAYLOAD_TOO_LARGE, survive.

    The frame-size row targets the CLIENT-side MUST
    (``internal/remote/transport.go:63-90``) — never an unsatisfiable
    center-side red.
    """

    mutations = CenterMutations(oversized_request=True)
    asyncio.run(_run_check_against_mutation(mutations, _check_peer_oversized_must))


# ── the close-code strict knob (S2) ──────────────────────────────────────


def test_close_code_default_is_a_warning_strict_is_an_assert() -> None:
    """A non-1000 close is a recorded WARNING by default and a RED under
    ``CONFORMANCE_STRICT_CLOSE_CODE=1`` (the SPEC names no close code)."""

    transcript = Transcript()
    transcript.close_code = 1008
    check_close_code(transcript, strict=False)  # must NOT raise
    assert transcript.warnings and "close-code" in transcript.warnings[-1]
    with pytest.raises(ConformanceFinding) as excinfo:
        check_close_code(transcript, strict=True)
    assert "FINDING transport/close-code-1000" in str(excinfo.value)


async def _check_close_code_mutation(peer: ConformancePeer, center: InKitCenter) -> None:
    # Force a CENTER-initiated close: go silent and let the heartbeat
    # kill fire; the mutated center closes with its mutated code.
    await peer.serve(silence=True, budget_s=6.0)
    await peer.wait_closed(timeout=3.0)
    check_close_code(peer.transcript, strict=True, area="transport")


def test_mutated_close_code_1008_reds_under_strict() -> None:
    """A 1008 close is invisible by default and RED under the strict knob."""

    mutations = CenterMutations(close_code=1008)
    with pytest.raises(ConformanceFinding) as excinfo:
        asyncio.run(_run_check_against_mutation(mutations, _check_close_code_mutation))
    assert "FINDING transport/close-code-1000" in str(excinfo.value)
    assert "1008" in str(excinfo.value)


# ── the args-hash canonicalisation is real, not "hash-shaped" ────────────


def test_args_hash_matches_the_documented_canonicalisation() -> None:
    """The kit computes a REAL hash: equal for the same args whatever the
    key order; different for different args."""

    a = {"b": 1, "a": [1, {"c": True}]}
    b = {"a": [1, {"c": True}], "b": 1}
    assert hash_args_python_center(a) == hash_args_python_center(b)
    assert hash_args_python_center(a) != hash_args_python_center({"b": 1})
    assert len(hash_args_python_center(a)) == 64
