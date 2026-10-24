"""SPEC §10 area 4 — MCP fidelity: catalog semantics, tools/call
pass-through, and the args hash.

The SPEC says "byte-identically"; the shipped client RE-ENCODES catalogs
(``internal/remote/protocol.go:305-331``), so the kit asserts SEMANTIC
equality and reports the SPEC-vs-implementation tension (finding).

Agent-side observability: the machine sees what it SENT and what it
RECEIVED; the AGENT-visible rendering is only reachable through the
begin-hook ECHO document (the hook's optional JSON line).  When the
center/hook provides the echo, the agent-side comparisons run; when it
does not, the gap is named (COVERAGE.md) rather than silently skipped.
"""

from __future__ import annotations

import json

import pytest

from .conformance_peer import (
    DENIAL_ERROR_CODE,
    ConformanceFinding,
    ConformancePeer,
    KIT_CALL_ARGUMENTS,
    hash_args_go_client,
    hash_args_python_center,
    kit_call_result,
    open_session,
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


def _agent_catalog(echo: dict) -> list | None:
    catalog = echo.get("agent_catalog")
    return catalog if isinstance(catalog, list) else None


@pytest.mark.asyncio
async def test_catalog_semantics_round_trip(target, serving, begin) -> None:
    """§6.4/§10 — the agent-visible catalog is SEMANTICALLY the machine's.

    Byte identity is NOT asserted: the shipped client re-encodes the
    catalog (``protocol.go:305-331``), so the kit compares names and the
    ``inputSchema`` JSON semantics and REPORTS the SPEC-vs-code tension.
    """

    peer = _peer(target)
    try:
        await peer.connect()
        peer.start_heartbeats(peer.adopted.get("heartbeat_interval_ms", 1000))
        async with serving(peer, budget_s=20):
            outcome = await open_session(peer, target)
            agent_catalog = _agent_catalog(outcome.echo)
            if agent_catalog is None:
                pytest.skip(
                    "CATALOG-FIDELITY-GAP: the begin-hook did not echo the "
                    "agent-visible catalog (the hook contract's optional JSON "
                    "line) — the machine cannot observe the agent side "
                    "(COVERAGE.md names this gap)"
                )
            sent = {t["name"]: t for t in peer.transcript_sent_catalog()}
            seen = {t.get("name"): t for t in agent_catalog if isinstance(t, dict)}
            if set(sent) != set(seen):
                raise ConformanceFinding(
                    "mcp-fidelity",
                    "catalog-names",
                    "§6.4",
                    f"the agent-visible tool names {sorted(seen)} differ from the "
                    f"machine's {sorted(sent)}",
                    peer.transcript.excerpt(),
                )
            for name, original in sent.items():
                if seen[name].get("inputSchema") != original.get("inputSchema"):
                    raise ConformanceFinding(
                        "mcp-fidelity",
                        "catalog-schema-semantics",
                        "§6.4/§10",
                        f"the inputSchema of {name!r} changed in transit "
                        f"(semantic inequality): {json.dumps(seen[name].get('inputSchema'))[:200]}",
                        peer.transcript.excerpt(),
                    )
            peer.transcript.warn(
                "catalog-fidelity: SEMANTIC equality held; byte identity is NOT "
                "asserted because the client re-encodes the catalog "
                "(internal/remote/protocol.go:305-331) — the SPEC §10 \"byte-identically\" "
                "wording vs the implementation is a reported finding"
            )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_tools_call_pass_through(target, serving, begin) -> None:
    """§6.6 — the MCP object travels unchanged; the result comes back raw."""

    peer = _peer(target)
    try:
        await peer.connect()
        peer.start_heartbeats(peer.adopted.get("heartbeat_interval_ms", 1000))
        async with serving(peer, budget_s=25):
            await open_session(peer, target)
            if target.mode != "self-host":
                pytest.skip(
                    "TOOLS-CALL-GAP: driving a tools/call means the AGENT calls a "
                    "tool; the hook contract has no agent-call verb (the self-host "
                    "lane drives it in-process) — COVERAGE.md names this gap"
                )
            result = await target.center.agent_call("kit_echo", KIT_CALL_ARGUMENTS)
            received = peer.calls_received[-1]["params"] if peer.calls_received else None
            if received != {"name": "kit_echo", "arguments": KIT_CALL_ARGUMENTS}:
                raise ConformanceFinding(
                    "mcp-fidelity",
                    "call-params-unchanged",
                    "§6.6",
                    f"the forwarded params {received!r} are not the MCP object "
                    f"unchanged (want name+arguments verbatim)",
                    peer.transcript.excerpt(),
                )
            if result != kit_call_result():
                raise ConformanceFinding(
                    "mcp-fidelity",
                    "call-result-raw",
                    "§6.6",
                    f"the agent-visible result {result!r} is not the machine's raw "
                    f"MCP result {kit_call_result()!r}",
                    peer.transcript.excerpt(),
                )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_args_hash_under_the_centers_canonicalisation(target, serving, begin) -> None:
    """§8.4 — the center's args hash equals ``sha256(canonical_json(args))``
    under ITS documented canonicalisation; the two heads' divergence is
    reported (never compared as "hash-shaped strings")."""

    peer = _peer(target)
    try:
        await peer.connect()
        peer.start_heartbeats(peer.adopted.get("heartbeat_interval_ms", 1000))
        async with serving(peer, budget_s=25):
            outcome = await open_session(peer, target)
            if target.mode != "self-host":
                pytest.skip(
                    "ARGS-HASH-GAP: comparing the center's stored hash needs the "
                    "ledger row echoed through the begin hook (the optional JSON "
                    "line); without it the kit can only compute the hashes — "
                    "COVERAGE.md names this gap"
                )
            await target.center.agent_call("kit_echo", KIT_CALL_ARGUMENTS)
            audit_row = (outcome.echo.get("agent_audit") or [{}])[-1]
            expected = hash_args_python_center(KIT_CALL_ARGUMENTS)
            stored = audit_row.get("args_hash")
            if stored != expected:
                raise ConformanceFinding(
                    "mcp-fidelity",
                    "args-hash-canonical",
                    "§8.4",
                    f"the center's args_hash {stored!r} != sha256(canonical_json(args)) "
                    f"under the documented canonicalisation ({expected})",
                    peer.transcript.excerpt(),
                )
            go_hash = hash_args_go_client(KIT_CALL_ARGUMENTS)
            if go_hash != expected:
                peer.transcript.warn(
                    "args-hash: the two audit heads are NOT JOINABLE for these "
                    f"arguments (center={expected[:16]}… go={go_hash[:16]}…) — §8.4 "
                    "names neither algorithm nor canonicalisation; the SPEC "
                    "amendment recommendation is JCS (RFC 8785) or sorted keys + "
                    "ensure_ascii=false + shortest-roundtrip (reported finding)"
                )
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_denied_outcome_via_the_policy_lane(target, serving, begin) -> None:
    """§9.1/§6.5 — the client policy lane: a denied tool is omitted from the
    catalog and a call to it is refused ``TOOL_NOT_FOUND`` (never
    ``TOOL_DENIED``), audited machine-side as ``denied``."""

    peer = _peer(target, deny_tools=["kit_probe"])
    try:
        await peer.connect()
        peer.start_heartbeats(peer.adopted.get("heartbeat_interval_ms", 1000))
        async with serving(peer, budget_s=25):
            await open_session(peer, target)
            sent_catalog = peer.transcript_sent_catalog()
            if any(t["name"] == "kit_probe" for t in sent_catalog):
                raise ConformanceFinding(
                    "mcp-fidelity",
                    "denied-tool-omitted",
                    "§9.1",
                    "a denied tool remained in the machine's catalog",
                    peer.transcript.excerpt(),
                )
            if target.mode != "self-host":
                pytest.skip(
                    "DENIED-OUTCOME-GAP: driving the denied call needs the agent-side "
                    "call verb (self-host drives it in-process) — COVERAGE.md names it"
                )
            from .conformance_peer import CenterRefused

            refused = ""
            try:
                await target.center.agent_call("kit_probe", {})
            except CenterRefused as exc:
                refused = str(exc)
            if refused != DENIAL_ERROR_CODE:
                raise ConformanceFinding(
                    "mcp-fidelity",
                    "denied-refusal-code",
                    "§6.5/§9.1",
                    f"the denied call was refused {refused or 'successfully'!r}, "
                    f"want {DENIAL_ERROR_CODE} (TOOL_DENIED is v0.2-reserved)",
                    peer.transcript.excerpt(),
                )
            if not any(row.get("outcome") == "denied" for row in peer.machine_audit):
                raise ConformanceFinding(
                    "mcp-fidelity",
                    "denied-audited-machine-side",
                    "§8.4",
                    "the machine did not audit the denial with outcome 'denied'",
                    peer.transcript.excerpt(),
                )
    finally:
        await peer.close()
