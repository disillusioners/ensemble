# Go-client recon input (mid-flight, from d1-go-recon) — CAPTURED VERBATIM

> This file is a **verbatim capture of an external recon report** plus a short
> `PLANNER-VERIFIED` block. It is input to the plan, not the plan. Every claim
> below marked UNVERIFIED was not re-checked here.

## Verbatim: headline

The Go client is **TLS-ONLY. Plaintext `ws://` is impossible.**

- `internal/config/config.go:927-933` — config load REJECTS any `center_url` not prefixed `wss://` or `https://`.
- `internal/remote/transport.go:266-273` — dial-time `normalizeURL` errors on `ws`/`http`: "the remote hop is TLS only (SPEC.md §3.1)".
- No flag/env/config switch relaxes this. `https→wss` is silently rewritten; path is honored verbatim.

## Verbatim: harness-constraining facts

- Config is YAML-only for `center_url` (`remote.center_url`); flags/env do NOT set it (env only overrides the two tokens). `-config <path>` MUST be passed explicitly (macOS default path is `~/Library/Application Support/mcps-client/config.yaml`, NOT `~/.config`).
- `remote.enabled: true` required (default false, whole section skipped).
- Unknown YAML keys are a HARD ERROR (`config.go:437`); second YAML doc is fatal. Config must be schema-exact for this build.
- `remote.ca_pem` accepts PEM text or a file path; added to (not replacing) system trust. `server_pin_sha256` optional (no pin → warns once). No `insecure_skip_verify` knob.
- Enrollment is OUT OF BAND: no enrollment RPC/endpoint. Client presents `Authorization: Bearer <token>` on the WS upgrade only, re-presents on every reconnect, persists NOTHING. 401 on upgrade = `ErrAuthFailed` = FATAL, no retry. Any other upgrade failure = non-fatal backoff retry.
- Identity = `remote.machine_id`, else `mach_` + 8 hex of sha256(hostname).
- No self-test / dry-run / one-shot mode: `dist/mcps-client` is a foreground long-runner. Only exit-early flags: `-version`, `-check-config`, `-print-launchagent`. Logs are JSON on stderr. `-check-config` is usable as a harness preflight.
- STUB CHILD: no built-in stub/disabled/echo mode; child is ALWAYS spawned (single child, not a list). `/bin/cat` will NOT work — the SDK performs a real MCP `initialize`. In-tree legit stub = the adversarial recording child: `go test -c -o /tmp/… ./internal/adversarial` then config `child.command: <that binary>`, `args: ["-test.run=^$"]`, `env: ["MCPS_ADVERSARIAL_CHILD_MODE=1"]`. It serves `secret_tool` (success: "the secret tool ran") and `echo_tool` (ALWAYS errors, embeds args verbatim — useful for the TOOL_ERROR path, not the happy path). Requires no .app/TCC/npm. Compiling to /tmp does not modify the read-only repo.
- Child spawns eagerly at boot but catalog is fetched LAZILY on `session.begin` (10s catalog deadline) → a `session.begin` fired before the stub finishes initialize can return `TOOL_TIMEOUT`. Harness must wait for the `child started` log line (~1s) before driving.
- Harness must use explicit ports: whether the client's own `listen.addr` accepts port 0 is UNVERIFIED; `listen.addr` must be loopback + `listen.token` ≥32 chars non-placeholder is REQUIRED even for remote-only use.

## Verbatim: ranked wire-level hazards

1. TLS-only (above).
2. `v` is validated on EVERY inbound frame BEFORE the type switch — responses, errors, AND notifications. Missing/wrong `v` ⇒ connection torn down, never retried.
3. Capability asymmetry: client ADVERTISES `notifications:false` and REQUIRES only {session, tools, heartbeat}. A gateway enforcing SPEC §5.3's four-required set (incl. `notifications`) refuses with `CAPABILITY_MISSING`. The mcps_client repo's own SPEC-derived harness declares the 4-set while its fake center defaults to the 3-set — the client's two own test kistors disagree.
4. The client sends NO WS pings. Client declares the server dead after `client_dead_detect_ms` (45s) with no inbound frame of ANY kind.
5. Client offers NO `Sec-WebSocket-Protocol`; a gateway requiring/selecting a subprotocol fails the upgrade. Version lives in the path, not a header.
6. Text frames only; binary frames dropped.
7. hello reply MUST echo `id` verbatim or the client waits the full 20s hello timeout and reconnects.
8. `session.begin.machine_id` MUST equal the machine_id advertised in hello, else `MACHINE_NOT_ENROLLED`.
9. One session per machine: second `session.begin` → `MACHINE_BUSY` (client-side too).
10. `tools/call` refused unless the tool is in the session catalog returned by this gateway (`TOOL_NOT_FOUND`).
11. Heartbeat params: all three or none. Zero/negative → FATAL; out-of-range positive → adopted with warning; all-zero → fallback + warning.
12. Catalog is RE-ENCODED client-side: `inputSchema` keys are SORTED, unknown tool members DROPPED. Byte-identical comparison against the child's raw output will differ and that is NOT a protocol bug — compare semantically, never byte-identically.
13. Client sends no `ts` and no `jsonrpc`.
14. `client.name` on the wire is `mcps-client` (HYPHEN), not the SPEC example's `mcps_client`.
15. Heartbeat `params` is `{}` and the envelope carries `session_id` when a session is open.
16. Session ids are `sess_`+16 hex, NOT ULIDs (SPEC example `sess_01JQZK4M8T`) — a gateway regex assuming Crockford base32 breaks.
17. Error answers for an unknown session carry NO `session_id` (deliberately cleared).
18. Client DROPS inbound `notifications/*` even though §6.3 marks direction "both".
19. Backoff jitter is subtractive: delay ∈ [0.8·base, base].
20. Frame limit applies to the WHOLE frame; oversized inbound REQUEST → `PAYLOAD_TOO_LARGE` and connection survives; anything else → drop.
21. Request queue bounded at 32 with `RATE_LIMITED` shedding → a gateway pipelining >32 requests sees out-of-order answers.

## Verbatim: SPEC §10 conformance-kit text

`mcps_client/SPEC.md:862-885` is still the stub:

> **Stub — this section is a placeholder.** No conformance suite exists yet.

Intended areas listed there: Handshake / Session lifecycle (incl. expiry, MACHINE_BUSY) / Transport (heartbeat cadence, server-side kill-on-silence, client-side death detection, reconnect-with-backoff, frame-size limit) / MCP fidelity (byte-identically) / Errors (every v0.1-reachable §6.5 code; `TOOL_DENIED` reserved v0.2+ = out of scope) / Auth (upgrade-header carrier, rejected bearer = HTTP 401, expired bearer, wrong server pin; enrollment out of band). Closing line:

> Until the kit exists, conformance is established by reading both implementations against this document — which is exactly the friction the kit is meant to remove.

The kit's checks should map onto exactly those six areas so the eventual SPEC §10 update is a pointer, not a rewrite. NOTE: the "byte-identically" wording is NOT satisfiable by the shipped client (see hazard 12) — report the tension; assert semantic fidelity and document the deviation.

## Verbatim: other

- §8.4 audit fields per the client's interpretation doc: `timestamp, session_id, machine_id, agent_id, tool, args_hash, outcome, detail`, SHA-256 over CANONICALISED args. The harness must independently recompute the hash from the known arguments and compare.
- Do NOT plan any change to the mcps_client repo. Compiling its test binary to `/tmp` is fine.
- Pre-existing at base (do NOT misattribute as branch regressions): PG tripwire, pytest-9 `INTERNALERROR`, g7 pack collection artifact.

---

# PLANNER-VERIFIED (checked against code in this session)

| Claim | Verdict | Evidence |
|---|---|---|
| Client is TLS-only, no dev switch | **CONFIRMED** | `mcps_client/internal/config/config.go:931-932`; `internal/remote/transport.go:266-273` (`normalizeURL`); grep for `insecure\|dev_mode\|allow_plaintext` in `internal/config`, `internal/remote`, `packaging/` → only test-local `InsecureSkipVerify`. |
| Client answers server WS pings and treats them as liveness | **CONFIRMED — hazard 4 is MITIGATED in principle** | `internal/remote/transport.go:235-246`: `SetPingHandler` → `c.touch()` + `WriteControl(Pong)`; `SetPongHandler` → `c.touch()`. |
| Center pings every 200 ms as a WS control frame | **CONFIRMED** | `daemon/routers/mcps_gateway.py:67` (`PING_INTERVAL_SECONDS = 0.2`), `:203-260` (`_ping_loop`/`_ping_once`). |
| Center's ping depends on private Starlette/uvicorn closure walking (fails silently to a warning) | **CONFIRMED** | `daemon/routers/mcps_gateway.py:77-103` (`_websocket_protocol`), `:106-133` (`_is_real_server_connection`), `:172-184`. ⇒ live-pair test MUST assert `pings_sent`/idle survival, not assume it. |
| Center requires only {session, tools, heartbeat} | **CONFIRMED — hazard 3 is a NON-issue at the center** | `daemon/services/mcps_gateway/protocol.py:79-83` (`REQUIRED_CLIENT_CAPABILITIES`), comment explicitly says `notifications` must not be enforced. |
| Center echoes the hello `id` | **CONFIRMED — hazard 7 satisfied** | `daemon/services/mcps_gateway/service.py:574-588` (`id=request_id`). |
| Center never validates a subprotocol | **CONFIRMED by absence** | no `subprotocol`/`Sec-WebSocket-Protocol` reference in `daemon/routers/mcps_gateway.py`. |
| Center adopts the machine-minted `sess_` id verbatim (no ULID/Crockford regex) | **CONFIRMED** | `daemon/services/mcps_gateway/service.py:860-867`; `sessions.py:41-44`. |
| Center serves plaintext HTTP/WS only — no SSL knobs in the daemon entrypoint | **CONFIRMED (this is the shipped-pair gap)** | no `ssl_certfile`/`ssl_keyfile` anywhere under `daemon/`; entrypoint `start.py` / `run_app.py`; `tests/integration/mcps_gateway/harness.py:204-212` builds `uvicorn.Config` without TLS. ⇒ the shipped pair cannot connect as shipped without an external TLS terminator. |
