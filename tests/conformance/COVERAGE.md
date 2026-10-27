# Conformance kit — coverage & independence attestation

The kit maps 1:1 onto the six SPEC §10 areas. Precision beats volume:
this file says what IS exercised, what is NOT and WHY, and what the kit
read while being written.

## Where the evidence stands (honest scope statement — B8f)

* **A green kit run against THIS repo's center is a SELF-CONSISTENCY
  result** (or a pointed run against a center you supply). It proves
  the kit's peer and checks are right — not that a second implementation
  interoperates.
* **Cross-center behavioural proof is DEFERRED.** The protocol repo's
  `internal/fakecenter` is Go-`internal/` and library-only (no `cmd/`
  entrypoint), so it cannot be driven as a subprocess from Python.
* What IS proven today:
  1. the **mutated-center negative control** (the kit goes RED with the
     correct FINDING labels — `test_peer_selftest.py`);
  2. the **real-pair interop gate** in `tests/integration/mcps_gateway/`
     (the shipped Go client against the real daemon over WSS/TLS);
  3. a **pointed-mode run against an external center process** (five env
     keys + `CONFORMANCE_BEGIN_URL`) — recorded in
     `.agents/shared/planning/mcps-gateway-interop/p5-evidence.md`.

## §-by-§ exercised

| §10 area | Check | What is asserted | Citation |
|---|---|---|---|
| Handshake | upgrade + hello | echoed `id`, `protocol_version == "0.1"`, the capability map, the three heartbeat params, the server name | §5.1/§5.2/§5.4 |
| Handshake | version mismatch | `PROTOCOL_VERSION_UNSUPPORTED` (+ `data{requested,supported}` when data is present), no downgrade, close | §5.2/§5.5 |
| Handshake | capability map | `notifications:false` ACCEPTED (offering-side reading); flat boolean map; a missing `tools` ⇒ `CAPABILITY_MISSING` + close | §5.3 |
| Session lifecycle | begin → end | `sess_` machine-minted id, catalog accepted, `session.end` acked, the socket survives, a second session opens (self-host lane; pointed mode asserts begin + names the end gap) | §6.4/§6.8 |
| Session lifecycle | expiry — the refusal | a call past the center's idle window is refused `SESSION_EXPIRED` (self-host lane — the window is center-owned and not on the wire) | §3.3 |
| Session lifecycle | expiry — the wire notification | on a CENTER-owned expiry the machine RECEIVES a `session.end` frame (params EXACTLY `{"session_id": ...}` — the wire contract does not change), the socket STAYS OPEN, and the machine released its lock → a NEW session opens on the SAME connection (self-host lane) | §3.3/§6.8 |
| Session lifecycle | `MACHINE_BUSY` | a second begin reaches NO frame at the machine — the refusal is the CENTER's. The kit's machine side ALSO models the machine's OWN lock (a concurrent begin is refused `MACHINE_BUSY`, like the shipped client), so a STRANDED lock — a centre that reaped a session without telling the machine — is observable | §8.3 |
| Transport | heartbeat adoption | the advertised values satisfy the shipped client's own validation (timeout ≥ 3× interval) and the machine heartbeats on them without being killed | §3.3 |
| Transport | kill-on-silence | close within [T, 2T] of the ADVERTISED timeout; the `HEARTBEAT_TIMEOUT` error frame; the G1 pin (below); the S2 close-code pin | §3.3/§6.8 |
| Transport | client-side death detection | inbound evidence measured within `min(client_dead_detect, 3 s)`; WS pings are invisible to the `websockets` client API — a ping-only center is legal and RECORDED, not failed | §3.3 |
| Transport | reconnect | a fresh upgrade + hello + begin after a break; a DIFFERENT session id (no resumption) | §6.8 |
| Transport | frame size | the center's answer to an oversized machine→center frame is RECORDED (see NOT-exercised note — the MUST is client-side) | §6.7 |
| MCP fidelity | catalog semantics | the agent-visible catalog (hook ECHO) has the machine's names + `inputSchema` SEMANTICS; byte identity is NOT asserted (the client re-encodes — a reported SPEC-vs-code tension) | §6.4/§10 |
| MCP fidelity | `tools/call` pass-through | params arrive as the MCP object unchanged; the result returns raw (self-host lane) | §6.6 |
| MCP fidelity | args hash | the center's stored hash equals `sha256(canonical_json(args))` under the center's documented canonicalisation; the two heads' divergence is REPORTED | §8.4 |
| MCP fidelity | `denied` outcome | via the CLIENT policy lane (`policy.deny_tools`): the denied tool is omitted from the catalog; a call to it answers `TOOL_NOT_FOUND` (`gated:true` — `TOOL_DENIED` is v0.2-reserved); the machine audits outcome `denied` (self-host lane) | §9.1/§6.5/§8.4 |
| Errors | reachable codes | `PROTOCOL_VERSION_UNSUPPORTED`, `CAPABILITY_MISSING`, `MACHINE_NOT_ENROLLED`, `INTERNAL` (second hello), `HEARTBEAT_TIMEOUT` — exact code + advisory message; `data` optional (the tolerance itself is pinned); `TOOL_DENIED` NEVER appears | §6.5 |
| Auth | upgrade carrier | the bearer rides the upgrade `Authorization` header; no in-band auth method exists | §4.1 |
| Auth | rejected bearer | HTTP **401**, never 403, no protocol frame; the expired-bearer lever resolves to the same lane (flat token set — rotation is the operator's job) | §4.1/§6.5 |
| Auth | wrong server pin | a wrong `server_pin_sha256` refuses the handshake though the chain verifies | §4.1 |
| Auth | enrollment out of band | the peer has no issuance surface (stance pin) | §10 |
| **Termination (v0.2)** | the v0.2 handshake | a machine that asks for `"0.2"` gets a `"0.2"` connection — the reply's `v` EQUALS the result's `protocol_version` (the shipped client fatals on a mismatch) — and the centre advertises `client_terminate` because the machine advertised it | §5.2/§5.3 |
| **Termination (v0.2)** | the terminate receipt | the machine asks for the end; the centre NEVER refuses; the ack is `{session_id, ended: true, reason, machine_notified: true}` with the machine's OWN reason code VERBATIM; `why_not_ended` is ABSENT; the socket STAYS OPEN | §6.10/§6.10.1 |
| **Termination (v0.2)** | the liberal acceptance | an UNKNOWN `reason_code` is accepted and echoed VERBATIM; unknown members in the envelope or the params are TOLERATED (never a rejection) | §6.10.3 |
| **Termination (v0.2)** | the idempotent goal state | a terminate for a session that is NOT live is NEVER an error: `ended: false` with a MANDATORY `reason` (the shipped client RELEASES its local lock by default on `ended: false` — the ended-elsewhere arm — and RETAINS it only for `refused` / `machine_refused`; sending the reason always makes the decision explicit instead of leaving it to a fallback) | §6.10 |
| **Termination (v0.2)** | the reap race | the sweep reaped the session and the machine asks afterwards ⇒ `ended: false` with the centre-cause reason `reaped_by_center` (self-host lane — the window is centre-owned) | §6.10/§6.9.2 |
| **Termination (v0.2)** | the duplicate machine id | a second connection claiming a LIVE machine id is refused with `MACHINE_ID_CONFLICT` (+ `data{machine_id, …}`); the INCUMBENT is NOT evicted (its socket stays open). NEITHER connection is evicted | §5.6 |
| **Termination (v0.2)** | the v0.1 connection is unchanged | a v0.1 connection answers `protocol_version: "0.1"` and its capability map carries NO `client_terminate` key — v0.2 does not leak into v0.1 | §5.2/§5.3 |
| **Termination (v0.2)** | the v0.2 `session.end` reason | on a `"0.2"` connection the centre's `session.end` names the centre cause — `reason: "reaped_by_center"` for the sweep's reap and `reason: "superseded"` for an explicit end (the v0.1 lane keeps EXACTLY `{"session_id": ...}`) | §6.9.2 |

## §-by-§ NOT exercised (with the reason)

| Row | Why not |
|---|---|
| **The v0.2 termination rows in POINTED mode** | The rows are WIRE-driven and need a machine that speaks v0.2 — the peer does (§6.10). They run against any centre that answers a `"0.2"` hello. The ONE exception is the reap-race's centre-cause token (`reaped_by_center`): the reap window is centre-owned and not advertised on the wire, so the token is asserted in the self-consistency lane only (the general row — `ended: false` + a mandatory reason — runs in both lanes). |
| **Session expiry in pointed mode** | The TTL/idle window is CENTER-owned and NOT advertised on the wire — the `hello` result carries only the three `heartbeat_*` parameters — so the kit **cannot compress a foreign center's window**. The CONTRACT itself is in the kit: a center-owned expiry DOES reach the machine as a `session.end` frame (the machine releases its own lock and the connection returns to `ready` — SPEC §6.8; that frame is the only thing that frees the machine's lock, which is why a SILENT reap strands it). But the kit can only OBSERVE it against a center whose window it can compress — the self-host lane (compressed 2 s window). Against a foreign center the window is as long as the operator configured, so the lane stays **SELF-HOST ONLY**. |
| **The agent-facing end envelope** (`ended` / `why_not_ended` / `machine_notified` / the audit ledger's `error_code`) | That is a **CENTER-TOOL convention**, NOT wire: nothing about it crosses the protocol, so the kit — which drives the wire — cannot observe it. It is out of the kit's scope by construction; the center's own runbook and doc-pins own it. |
| **`session.end` / `tools/call` / args-hash / denied in pointed mode** | The begin-hook contract covers BEGIN only; there is no agent-side END/call verb to drive through a foreign hook. The self-host lane drives them in-process; the begin-hook ECHO document (`agent_catalog`, `agent_audit`) closes the catalog + args-hash rows when a center provides it. |
| **Frame-size limit as a CENTER red** | SPEC §6.7 puts the enforcement MUST on the CLIENT (`internal/remote/transport.go:63-90`). A center-side red would be UNSATISFIABLE by a conformant-but-not-enforcing center, so the row targets the client-side MUST: the PEER's duty (answer `PAYLOAD_TOO_LARGE` above the limit, accept up to the ceiling, survive) is pinned in `test_peer_selftest.py`, and the center's behaviour is recorded — the reference daemon's §6.7 gap is a known, reported observation, not a kit red. |
| **WS-ping observability** | The `websockets` client answers pings below the protocol layer and exposes no received-ping callback; a ping-only center's liveness evidence is invisible to the application. Recorded as a warning, never failed. |
| **Token issuance / enrollment flow** | Out of band in v0.1 by design (§10). The kit never mints credentials. |
| **Notification relay** | Both implementations relay nothing in v0.1 (`notifications: false` advertised honestly); nothing to exercise. |
| **Streaming/screenshot payload fidelity** | Requires a real computer-use server behind the machine — post cu-builtin removal (2026-10-24) such a server attaches via the mcps_client wrapper + gateway/mcp_rpc path, not a daemon builtin; the kit's peer serves a synthetic catalog. Out of scope for a wire kit. |

## The deliberate pins (and the marker on each)

* **G1 — the `HEARTBEAT_TIMEOUT` error frame carries NO `id`.** SPEC
  §6.1 says the `id` is required on request/response/error envelopes;
  the reference center deliberately omits it (no request to correlate)
  and the shipped client DROPS uncorrelated errors. The kit pins the
  CURRENT behaviour with a comment pointing at G1 — a future SPEC change
  flips the pin deliberately.
* **S2 — close code 1000.** The SPEC names no close code anywhere.
  Default: a recorded warning; `CONFORMANCE_STRICT_CLOSE_CODE=1` makes
  it a hard assert.
* **§5.3 offering-side reading.** `notifications: false` is accepted:
  the "required" column binds the OFFERING side; enforcing it against
  the machine would kill every shipped-client connection.
* **§8.4 canonicalisation divergence.** The two audit heads are NOT
  JOINABLE for non-ASCII / `<>&` / float arguments (Python
  `ensure_ascii=True` + `default=str` vs Go HTML-escaping +
  shortest-roundtrip float64). Go's float64 normalisation also collapses
  `{"n":1}` and `{"n":1.0}` to one hash. The kit computes BOTH forms and
  reports the divergence; the SPEC amendment recommendation is JCS
  (RFC 8785) or sorted keys + `ensure_ascii=false` + shortest-roundtrip.

## Independence attestation

**Read list** (what the kit's author consulted): `mcps_client/SPEC.md`
v0.1; the shipped client's transport/protocol/audit sources under
`mcps_client/internal/` (for the client-side MUSTs and known
divergences); this repo's `tests/integration/mcps_gateway/` harness
structure (for the TLS/cert recipe only); the plan §2/D4 contract.

**Not read for wire shapes**: `daemon/services/mcps_gateway/` service
implementation (the kit's every frame is a SPEC transcription; the
reference center's CURRENT behaviours are pinned as marked pins, each
carrying its marker and a citation of the observed behaviour, not an
implementation copy).

**Leaks**: none of the kit's modules imports `daemon.*`. The kit's
dependencies are stdlib + `websockets` (the repo's declared set). The
sanctioned-importer list of the interop lane is UNCHANGED:
`tls_harness.py`, `p0_gate.py`, `harness.py` (and `go_client.py` imports
none).
