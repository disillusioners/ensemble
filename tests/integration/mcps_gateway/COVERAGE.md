# SPEC coverage declaration — remote-hands gateway (Phase B, independent fake client)

**Author role:** INDEPENDENT FAKE-CLIENT AUTHOR.
**Spec under test:** `mcps_client/SPEC.md` (Remote-Hands Protocol v0.1, POC draft).
**Under-test implementation:** `agents-ensemble` gateway, worktree
`agents-ensemble-wt-mcps-gateway`, branch `feature/mcps-gateway`.

This file is the *coverage declaration* required by the task brief: exactly
which SPEC sections the test suite exercises, and exactly which it does not.

---

## 1. SPEC sections EXERCISED

| § | Section | Exercised by | What is asserted |
|---|---|---|---|
| §3.1 | Transport — one JSON object per WS text frame; bearer credential on the upgrade request | `TestTransportAndAuth.test_upgrade_auth_matrix`; every test in the suite (framing) | Bad token ⇒ HTTP 401; missing token ⇒ HTTP 401; enrolled token ⇒ upgrade accepted; **zero** protocol frames exchanged on a refused upgrade. One JSON object per frame (the client rejects anything that is not a JSON object — recorded as a violation). **n4 spot-check:** the 401 matrix also runs a non-ASCII bearer (`béta-tøken-ñ`) and garbage-byte bearers (`ÿþý-garbage-bytes`, `%%%not-a-token%%%`) — each must yield a clean HTTP 401 with zero protocol frames (bytes-safe constant-time token compare). |
| §3.3 | Heartbeat parameters (server advertises; client MUST adopt) | `TestHandshake.test_hello_response_shape_capabilities_and_heartbeat_params`; `TestHeartbeat.test_advertised_heartbeat_timeout_matches_the_actual_tolerance`; `TestEnvironmentKnobs.test_advertised_heartbeat_params_reflect_the_env_knobs` | All three parameters present in `hello.result` and positive; `heartbeat_timeout_ms >= 3 x interval`; `client_dead_detect_ms >= 3 x interval`; the client adopts the advertised interval; the advertised timeout equals the server's *actual* silence tolerance. |
| §4.1 | Tier 1 — enrollment is out of band; bearer on the upgrade; center decides machine admission | `TestTransportAndAuth.test_upgrade_auth_matrix`; `TestTypedErrors.test_disallowed_machine_is_refused` | Unknown bearer ⇒ 401 (no protocol layer reached); a machine outside the allowlist ⇒ `MACHINE_NOT_ENROLLED`. |
| §5.1 | The `hello` exchange — mandatory, once per connection, after a successful upgrade | `TestHandshake.test_hello_response_shape_capabilities_and_heartbeat_params`; `TestSessionLifecycle.test_session_begin_returns_matching_session_id_and_catalog` | `hello` answered in band; exactly one `session.begin` per session; no session traffic before `hello`. |
| §5.2 | Version negotiation — the server replies with the version it will speak | `TestHandshake.test_hello_response_shape_capabilities_and_heartbeat_params` | `result.protocol_version == "0.1"` (the requested version). **Not** exercised: the `PROTOCOL_VERSION_UNSUPPORTED` refusal path (see §3 below). |
| §5.3 | Capability flags — flat map of booleans; the four v0.1-**required** capabilities must be `true`; both peers carry the same key set | `TestHandshake.test_hello_response_shape_capabilities_and_heartbeat_params`; `test_fake_client_selftest.py::TestCapabilityMap` | Capability values are all booleans; the key set is exactly §5.3's seven keys; `session`, `tools`, `notifications`, `heartbeat` are all `true`. |
| §5.4 | Illustrative `hello` / `hello.result` shapes | `TestHandshake.test_hello_response_shape_capabilities_and_heartbeat_params` | `v`, `type`, `id` echo, `result.protocol_version`, `result.server{name,version}`, `result.capabilities`, the three `heartbeat_*` members. |
| §6.1 | Message envelope — `v` / `type` / `id` / `session_id` / `method` / `params` / `result` / `error` | every test; `TestEnvelopeRecording.test_every_inbound_frame_is_transcribed_and_violations_recorded`; `TestTypedErrors.test_error_frames_carry_the_session_id_when_a_session_is_active` | `v` present and equal to `"0.1"`; `type` in the four legal values; `id` echoed verbatim; `session_id` present after a session exists; no second layer of indirection around the MCP fields. Violations are **recorded** (never raised) and cross-checked against the transcript. |
| §6.2 | Session states — `ready` ⇄ `active`; ending a session returns the connection to `ready`; the socket stays open | `TestSessionLifecycle.test_session_end_acknowledges_and_keeps_the_socket_open` | `session.end` acknowledged; the socket stays open; a **new** session can be opened on the same socket with a fresh id. |
| §6.3 | The method set — exactly seven methods; `heartbeat` is a notification with no id and no response required | `TestSessionLifecycle.*`; `TestTypedErrors.test_unknown_method_is_rejected_in_band` | The observed inbound methods are all members of the seven-method set; `tools/call` carries the MCP parameter object unchanged; an unknown method is refused in band with a §6.5 code and does not tear the connection down. |
| §6.4 | `session.begin` → `session_id` + tool catalog; the envelope `session_id` is canonical and MUST equal `result.session_id`; the catalog is forwarded with `inputSchema` intact | `TestSessionLifecycle.test_session_begin_returns_matching_session_id_and_catalog` | Envelope id equals `result.session_id`; both equal the id the center reported; `machine_id` echoed; `expires_at_ms` present; every catalog entry carries the exact `inputSchema` the machine advertised. |
| §6.5 | Typed errors — stable `code`, advisory `message` | `TestTypedErrors.*`; `TestSessionExclusivity.test_second_concurrent_session_begin_is_refused_machine_busy`; `TestSessionExclusivity.test_machine_side_exclusivity_over_the_wire` | `MACHINE_BUSY`, `MACHINE_NOT_ENROLLED`, `INTERNAL`, `HEARTBEAT_TIMEOUT` all observed with the documented shape; every observed code is drawn from the §6.5 table; the `message` is advisory text. |
| §6.6 | `tools/call` — `params` is the MCP parameter object unchanged; the raw MCP result is returned as-is | `TestSessionLifecycle.test_tools_call_success_and_is_error_results` | `params == {"name", "arguments"}`; no extra wrapping layer; the success result comes back with `isError: false`; an `isError: true` result comes back with `isError: true`. |
| §6.7 | Large payloads / per-command timeout | — **NOT exercised** (see §3 below). The `slow_tool` is present in the catalog but no timeout is asserted. |
| §6.8 | `session.end` and heartbeat-loss semantics — heartbeat loss is a **kill** | `TestSessionLifecycle.test_session_end_acknowledges_and_keeps_the_socket_open`; `TestHeartbeat.test_heartbeat_silence_kills_the_session`; `TestQuietWindow.test_quiet_window_emits_no_protocol_frames` | `session.end` acknowledges and releases the machine-side lock; after heartbeat silence the session is declared dead with `HEARTBEAT_TIMEOUT` and the socket is closed. **n4 spot-check (zombie socket / ping exit path):** A completes hello → session.begin → `session.end` with A's socket held open client-side, B is live with its own session, and over a 6.0 s window A receives zero frames, A is not closed, and B stays serviceable. |
| §8.3 | v1 session policy — one active session per machine; the **machine** must refuse a concurrent `session.begin` | `TestSessionExclusivity.test_second_concurrent_session_begin_is_refused_machine_busy`; `TestSessionExclusivity.test_machine_side_exclusivity_over_the_wire`; `test_fake_client_selftest.py::TestMachineSideExclusivity` | A concurrent `session.begin` is refused with `MACHINE_BUSY`; the original session survives and still serves tool calls. Both the center-side and the machine-side refusal are covered. |
| §8.4 | Audit — who / machine / tool / **args-hash**; args are hashed, never stored verbatim | `TestAudit.test_audit_records_an_args_hash_and_never_the_raw_arguments` | The audit record names the agent, the machine and the tool; it carries a non-empty args-hash; the raw argument value appears nowhere in the serialized record. |
| §9.1 | Tool names and schemas pass through untouched | `TestSessionLifecycle.test_session_begin_returns_matching_session_id_and_catalog`; `test_fake_client_selftest.py::TestCatalogContract` | The catalog the center returns is the machine's catalog with `inputSchema` byte-identical. |

> **Bound on the zombie-socket pin:** websockets 16 exposes no ping/pong frame counters, so the pin proves *no protocol frames* and *no socket kill* — it is **not** a WebSocket-ping count.

### Task-contract items (outside the SPEC text, from the brief)

| Item | Exercised by |
|---|---|
| WS route at `/api/mcps-gateway/ws`, present only when `MCPS_GATEWAY_ENABLED` is truthy | `TestTransportAndAuth.test_route_lifecycle_enabled_and_disabled` |
| Upgrade auth from `MCPS_GATEWAY_TOKENS` (comma-separated) ⇒ 401 | `TestTransportAndAuth.test_upgrade_auth_matrix` |
| Env-overridable knobs `MCPS_GATEWAY_HEARTBEAT_INTERVAL_MS`, `MCPS_GATEWAY_HEARTBEAT_TIMEOUT_MS`, `MCPS_GATEWAY_CLIENT_DEAD_DETECT_MS`, `MCPS_GATEWAY_SESSION_TTL_MS`, `MCPS_GATEWAY_SESSION_IDLE_TIMEOUT_MS`, `MCPS_GATEWAY_ALLOWED_MACHINES` | `TestEnvironmentKnobs.*` |
| Machine-side exclusivity (the fake client refuses a concurrent `session.begin` with `MACHINE_BUSY`) | `TestSessionExclusivity.test_machine_side_exclusivity_over_the_wire` |
| Args-hash in the audit trail, never raw args | `TestAudit.*` |
| No listener left behind after the test | `TestTeardown.test_no_listener_survives_the_test` |

---

## 2. SPEC sections NOT exercised

These are stated precisely so the mission report does not over-claim.

| § | Section | Why not exercised |
|---|---|---|
| §2.2 | Agent-facing tool surface (`mcp_rpc_list_machine` / `connect` / `command` / `disconnect`) | §2.2 is explicitly **RECOMMENDED, NOT WIRE PROTOCOL**, and the exact tool names are "a center implementation detail". The tests drive the center-side operations directly (the same calls the agent-facing tools make) so that the *wire* is exercised without importing the agent-facing tool layer. The tool names themselves are not asserted. |
| §3.2 | Local mode (MCP `streamable-http` on `127.0.0.1`) | Out of scope: this is the client-side local listener, not the remote hop the gateway implements. |
| §4.2 | Tier 2 — mTLS, client certificates, per-tool ACLs | v0.2+; explicitly out of scope for v0.1. |
| §4.3 | The wrapper's internal policy layer (empty default deny list) | Lives inside `mcps_client`, not in the wire protocol. The catalog-omission rule it implies is *not* tested (no policy-gated tool is simulated). |
| §5.2 (partial) | `PROTOCOL_VERSION_UNSUPPORTED` refusal | The suite sends only `protocol_version: "0.1"`. A deliberate mismatch (`"0.9"`) is **not** sent, so the typed refusal and the immediate close of §5.5 are untested. |
| §6.5 (partial) | `CAPABILITY_MISSING`, `SESSION_NOT_FOUND`, `SESSION_EXPIRED`, `TOOL_NOT_FOUND`, `TOOL_TIMEOUT`, `TOOL_ERROR`, `PAYLOAD_TOO_LARGE`, `RATE_LIMITED` | Not reachable without extra machinery (a capability-stripping peer, a forged session id, an expired TTL, a tool that does not exist on the machine, a hung embedded server, an oversized frame, a load-shedding peer). The suite asserts only that every code the gateway *does* emit belongs to the §6.5 table. `TOOL_DENIED` is explicitly reserved for v0.2+ and is out of scope. |
| §6.7 | Frame-size limit / `PAYLOAD_TOO_LARGE`; per-command timeout | The `slow_tool` exists in the catalog, but no timeout is asserted: §9.3 places the per-command deadline on the **client** (the machine), and this suite deliberately answers slowly rather than modelling the embedded server's own deadline. |
| §6.8 (partial) | Client-side death detection and reconnect-with-backoff | §6.8's *client* half is the wrapper's behaviour (drop the socket, reconnect with backoff, fresh upgrade + hello + session.begin). The suite exercises the **server** half (kill on heartbeat loss). Reconnect behaviour is not implemented by the fake client. |
| §7 | The MCP-nativeness rule | Exercised only indirectly: `tools/call` params and the raw result are asserted to survive unchanged, and `tools/list` is answered. There is no dedicated MCP-fidelity/byte-identity test. |
| §8.1 | Roster and presence (`online` / `stale` / `offline`) | The roster surface is the center's management UI; not on the wire under test. |
| §8.2 | An agent controls one remote at a time | A workflow recommendation, not a wire rule. |
| §9.2 | Screenshot payload guidance | Computer-use specifics; no screenshot tool is simulated. |
| §9.3 | Per-command timeout (client-enforced) | See §6.7 above. |
| §10 | Conformance kit | A placeholder in the SPEC ("no conformance suite exists yet"). The suite described here is a step toward it, not its implementation. |
| §11 | Open questions | Not a normative section. |

---

## 3. Independence attestation

**Read (complete list) outside `tests/integration/mcps_gateway/`:**

1. `mcps_client/SPEC.md` — the specification under test (read in full).
2. `mcps_client/docs/spec-interpretations.md` — *listed* and *partially* read for
   context. No wire shape was taken from it; all wire shapes come from
   `SPEC.md`.
3. `mcps_client/` directory listing and `docs/` listing (names only).
4. `agents-ensemble-wt-mcps-gateway/pyproject.toml` — pytest configuration.
5. `agents-ensemble-wt-mcps-gateway/tests/integration/conftest.py` — the
   integration conftest (to follow repo conventions).
6. `agents-ensemble-wt-mcps-gateway/tests/integration/test_auth_routes.py`,
   `test_auth_middleware_derivation.py` — the canonical `create_app()` boot
   pattern.
7. `agents-ensemble-wt-mcps-gateway/tests/helpers/auth_fixture.py` — the
   auth fixture helper (to establish that `create_app()` boots headlessly).
8. `agents-ensemble-wt-mcps-gateway/daemon/api.py` — **the mounting jig
   only**: the `create_app` signature, the `include_router` list, and the
   gateway-registration block (which shows the route is registered
   conditionally on `MCPS_GATEWAY_ENABLED`). No gateway logic was read.
9. `agents-ensemble-wt-mcps-gateway/docs/mcps-client-remote-hands.md` — the
   design proposal referenced by SPEC §Status (existence confirmed).
10. The shared-context file
    `remote-hands-protocol-mcps-client-gateway-center-design-spec_20261022_081210.md`
    (the Explorer note about the Go client's architecture).

**Read via runtime introspection only (public names, signatures, route
metadata — no source, no handler bodies):**

* `daemon.services.mcps_gateway.config` — `GatewayConfig` field names,
  `load_gateway_config()` signature, `ENV_PREFIX`, `DEFAULT_PATH`.
* `daemon.services.mcps_gateway.protocol` — public symbol names.
* `daemon.services.mcps_gateway.sessions` / `.store` / `.audit` — public
  symbol names and dataclass field names.
* `daemon.services.mcps_gateway.service` — public symbol names and the
  signatures of `McpGatewayService.connect/command/disconnect/roster/...`.
* `daemon.routers.mcps_gateway` — public symbol names, the
  `build_mcps_gateway_router` signature, and the *route paths* the builder
  produces (needed to mount it).
* `daemon.repositories.mcps_gateway.repository` — public symbol names and
  the `McpGatewayAuditRepository` constructor signature (to judge whether
  the audit trail is reachable from a test).

**Deliberately NOT read:**

* `daemon/services/mcps_gateway/**` — **source not read** (only the
  introspection listed above).
* `daemon/routers/mcps_gateway.py` — **source not read**.
* `daemon/tools/mcp_rpc_tools.py` — **not read and not imported**.
* `daemon/repositories/mcps_gateway/**` — **source not read**.
* `daemon/migrations/versions/20261022_000001_create_mcp_gateway_audit.sql` —
  **not read**.
* `tests/unit/test_mcps_gateway_service.py` (the implementer's own unit
  tests) — **not read**, so that my expectations could not be derived from
  them.

**Confirmation:** the fake client module
(`tests/integration/mcps_gateway/fake_remote_client.py`) imports **only**
`asyncio`, `json`, `time`, `uuid`, `dataclasses`, `typing` and `websockets`.
It contains **no** `daemon.*` import and does not import the harness. Every
wire shape it emits was transcribed from `SPEC.md` §3.1–§8.4.

**Two implementation facts that leaked in unavoidably, disclosed:**

1. `daemon.routers.mcps_gateway.build_mcps_gateway_router` had to be
   imported by the **harness** to mount the route; its signature and the
   route paths it produces were inspected. No handler body was read.
2. `McpGatewayService` had to be imported by the **harness** so the
   centre-side `connect`/`command`/`disconnect` operations could be driven
   and so an in-memory audit sink could be attached. The service's *source*
   was not read; only its public method signatures were inspected.

Neither of these facts is a wire shape: the client's frames are built from
`SPEC.md` alone. The gateway's own behaviour was learned **black-box**, by
running it and reading the frames it emitted — which is the only channel an
independent client author is allowed to use.
