# Phase D1 interop research — SPEC v0.1 + Go client vs. Python gateway

Read-only research. Nothing in `mcps_client` was modified.
- Client/SPEC repo: `/Users/nguyenminhkha/All/Code/opensource-projects/mcps_client` @ `9537082` ("Phase A — P2 remote client … fake-center conformance harness").
- Center repo: `agents-ensemble` @ `latest` `9632badd4`.
- Go read is PARTIAL (see §D.8). Anything not read is marked **UNVERIFIED: verify against go-recon findings**.

---

## 0. THE ONE BLOCKER (read this first)

**The shipped client refuses plaintext `ws://` at two layers; the daemon serves plaintext only.**

| Layer | Evidence |
|---|---|
| Config validation | `internal/config/config.go:930-933` — `remote.center_url` must start with `wss://` or `https://`, else `"must be wss:// - the remote hop is TLS only (SPEC.md 3.1)"` |
| Transport | `internal/remote/transport.go:266-273` — `ws`/`http` → error `"must be wss:// — the remote hop is TLS only (SPEC.md §3.1)"`; only `wss`/`https` accepted (https rewritten to wss) |
| No escape hatch | `RemoteConfig` has no insecure/allow-plaintext field — full struct at `internal/config/config.go:204-263` |
| Center has no TLS | `GatewayConfig` fields `daemon/services/mcps_gateway/config.py:160-176` — no TLS/cert/ssl knob; knob list `:9-22` |
| Center is plain HTTP | `dev.sh:155` — `uvicorn daemon.api:app --host $HOST --port $PORT` (no `--ssl-keyfile`); `dev.sh:141` `PORT=8079`, `:142` `HOST=0.0.0.0` |
| Center docs never mention TLS | grep `wss://|TLS|tls|plaintext|ws://` over `docs/mcps-gateway.md` → **no matches** |

Consequence: Phase D1 needs **TLS termination in front of the daemon** (self-signed cert + `remote.ca_pem`, optionally `remote.server_pin_sha256`), or a client code change. The client already supports exactly that trust shape (`internal/remote/transport.go:290-303`, pin parsing; config keys `ca_pem`, `server_pin_sha256` at `packaging/config.dev.example.yaml:137-141`). The fake center proves the path: it is TLS-only (`internal/fakecenter/fakecenter.go:557` `tls.Listen`), self-signed by default (`:572-609`), exposes `CAPEM()`/`SPKIPin()` (`:622-637`).

Path to use: `remote.center_url: wss://127.0.0.1:<TLS_PORT>/api/mcps-gateway/ws`. The client only defaults the path to `/remote-hands/v1` when the URL carries none (`internal/remote/transport.go:277-279`); the center's path default is `/api/mcps-gateway/ws` (`daemon/services/mcps_gateway/config.py:52`).

---

## PART B — SPEC v0.1 (`mcps_client/SPEC.md`, 913 lines)

### B.1 §3.1 / §3.3 — transport and heartbeat

**§3.1 remote mode (`SPEC.md:133-151`)**
- Direction: `Client → center, **outbound** only` (`:137`); no inbound connection to the machine (`:116-127`).
- Transport: `WebSocket Secure (WSS), TLS 1.2+` (`:138`).
- Framing: `One JSON object per WebSocket text message (§6.1)` (`:139`).
- Ordering: `TCP-ordered; a session's requests are processed in order` (`:140`).
- Concurrency: `Multiple in-flight requests per session; responses carry the request id` (`:141`).
- Compression: `Permitted (permessage-deflate), negotiated by the WebSocket stack` (`:142`).
- Keepalive: `Protocol-level heartbeat (§3.3), not TCP keepalive` (`:143`).
- Credential: `Authorization: Bearer <token> HTTP header on the WSS upgrade request, repeated on every reconnect (§4.1)` (`:144`).
- `Authentication happens at the upgrade, not in the protocol` (`:146`); a refused upgrade produces **no** protocol message (`:148-149`); `hello` is not auth; `there is no auth method (§6.3)` (`:151`).

**URL/path shape.** The spec never normatively fixes a path. The only path statement is the illustrative upgrade in §5.4: `GET /remote-hands/v1 HTTP/1.1` (`SPEC.md:355`). §5.4 is explicitly "v0.1 draft — illustrative … not a frozen contract" (`:347-350`). ⇒ Path is **center-chosen** in practice; the client hardcodes `/remote-hands/v1` as a *default only*.

**TLS vs plain WS in the POC tier.** §3.1 says WSS, TLS 1.2+ with a *pinned* server identity; pin is "in addition to normal CA validation; a valid-CA-but-unpinned certificate is rejected" (`:222`). There is **no** clause permitting plaintext ws:// anywhere. Local mode is the only non-TLS surface (`:153-169`).

**§3.3 heartbeat parameters (`SPEC.md:171-202`)**
- "The server advertises its heartbeat parameters in the `hello` result (§5.4). The client **MUST** adopt the server's values; the client does not get to pick." (`:173-174`)
- Table (`:176-185`): `heartbeat_interval_ms` default `15000`, range `5000–60000`; `heartbeat_timeout_ms` `45000`, `≥ 3 × interval`; `client_dead_detect_ms` `45000`, `≥ 3 × interval`; `reconnect_initial_backoff_ms` `1000`, `250–30000`; `reconnect_max_backoff_ms` `60000`, `≥ initial`; `reconnect_max_attempts` `unlimited`; `session_idle_timeout_ms` `300000`; `session_ttl_ms` `3600000`.
- Direction: "The heartbeat message is a **notification** — client → server, with no `id` and **no response required**." (`:187-188`). It "proves *client* liveness" (`:189`).
- **Server → client death**: "The server declares the client dead after `heartbeat_timeout_ms` of silence and kills the session (§6.8)." (`:192-193`)
- **Client → server death**: "the absence of any WS-level liveness evidence — no inbound frame of any kind (a pong, a server-originated ping, a protocol message) for `client_dead_detect_ms` — as server death. A write failure or a close frame is server death immediately." (`:194-200`)
- **Grace**: none specified; the kill fires at the advertised timeout. "Loss of the heartbeat is a **kill-switch**, not a retry hint" (`:202`).

### B.2 §5.1–§5.5 — hello / versioning / capabilities

**§5.1 (`SPEC.md:277-300`)** — upgrade first (bearer), then `hello`; refused upgrade = no protocol message (`:279-283`). "The `hello` exchange is **mandatory** and happens **once per connection**, before any session exists." (`:299-300`). Flow: `hello { protocol_version, capabilities, machine_id }` → `hello.result { protocol_version, capabilities, heartbeat_* }` (`:295-296`).

**§5.2 version negotiation (`SPEC.md:302-316`)**
- "The client declares `protocol_version` — a dotted string, currently `"0.1"`." (`:304`)
- "The server replies with the `protocol_version` it will speak for this connection." (`:305-306`)
- Mismatch: "If the server does not implement the requested version, it **MUST** reply with a typed error (`PROTOCOL_VERSION_UNSUPPORTED`, §6.5) and close the connection. It **MUST NOT** silently downgrade" (`:307-309`).
- "The client, on receiving an unsupported version, **MUST NOT** guess. It surfaces the mismatch to the operator" (`:310-313`).
- "`0.x` versions are **not** mutually compatible by default." (`:314-316`)
- **Who wins**: nobody "wins" — the server speaks only what it implements; there is no range/major-minor negotiation, no highest-common-version algorithm. Exact string equality is the only mechanism the text supports.
- **Close code: the spec never names one.** No numeric close code appears anywhere in SPEC.md.

**§5.3 capability flags (`SPEC.md:318-345`)**
- "A capability whose value is `true` is supported; `false` or absent is not. There is no "unknown" state, and there is no negotiation dialogue beyond this one map." (`:322-324`)
- Table (`:326-334`): `session` (implements session.begin/end) **required**; `tools` (tools/list + tools/call) **required**; `notifications` (relays MCP notifications) **required**; `heartbeat` (sends and expects protocol heartbeats) **required**; `screenshot_delta` optional; `multi_session` optional (v1 policy: **no**); `client_cert` optional, reserved v0.2+, false in v0.1.
- "A peer **MUST** treat a **missing** key as not-supported, exactly as it treats an explicit `false`." (`:336-339`)
- "A capability that is not supported is **not an error** — it is a signal." (`:341-345`)
- **Gap:** §5.3 defines no failure mode. `CAPABILITY_MISSING` exists only in the §6.5 table ("A required capability was absent from the peer's map", `:595`) with no MUST, no direction, and **no instruction to close the connection** (unlike version mismatch).

**§5.4 hello shapes (`SPEC.md:347-411`)** — explicitly "illustrative … proposals, not a frozen contract" (`:349-350`).
- Request envelope: `v`, `type:"request"`, `id`, `method:"hello"`, `params{protocol_version, client{name,version}, machine_id, capabilities{7 flags}}` (`:366-386`).
- Result envelope: `v`, `type:"response"`, `id`, `result{protocol_version, server{name,version}, capabilities{7 flags}, heartbeat_interval_ms, heartbeat_timeout_ms, client_dead_detect_ms}` (`:389-410`).
- **Required/optional is not marked per-field anywhere.** The only required/optional markings live in the §6.1 envelope table (`:440-450`); for hello *params/result members* the spec is silent. Working reading: everything shown is required, `client`/`server` blocks included. (Both implementations send/require the full set — see F.)

**§5.5 clean failure on version mismatch (`SPEC.md:413-429`)** — byte-for-byte:
```json
{
  "v": "0.1",
  "type": "error",
  "id": "req-0001",
  "error": {
    "code": "PROTOCOL_VERSION_UNSUPPORTED",
    "message": "protocol version 0.9 is not supported by this center",
    "data": { "requested": "0.9", "supported": ["0.1"] }
  }
}
```
"The connection is closed immediately after the error is sent. The client does **not** retry — retrying a version mismatch is a loop, not a recovery." (`:428-429`). Mandated: typed code, `data.requested`, `data.supported` (array), close after send, no retry. **Not** mandated: message text, close code, timing.

### B.3 §6.1 — envelope (`SPEC.md:435-459`)

| Field | Type | Required | Meaning (`SPEC.md`) |
|---|---|---|---|
| `v` | string | yes | `Protocol version of this message ("0.1")` (`:442`) |
| `type` | string | yes | `request \| response \| error \| notification` (`:443`) |
| `id` | string | request/response/error | `Correlation id. Echoed verbatim on the reply. Client-generated, unique per connection.` (`:444`) |
| `session_id` | string | after a session exists | `Absent on hello and on pre-session errors.` (`:445`) |
| `ts` | integer | optional | `Unix epoch milliseconds, sender clock. Informational only` (`:446`) |
| `method` | string | request | `The method being invoked (§6.3)` (`:447`) |
| `params` | object | request | `Method parameters` (`:448`) |
| `result` | object | response | `Success payload` (`:449`) |
| `error` | object | error | `Typed error object (§6.5)` (`:450`) |

- **No `jsonrpc` key.** "The envelope carries MCP fields (`method`, `params`, `result`, `error`) — it does not wrap them in a second layer of indirection." (`:456-457`)
- "One JSON object per WebSocket message. No batching in v0.1." (`:454`)
- "`id` values are opaque strings; the responder **MUST** echo the exact string." (`:455`)
- `ts` is sender clock, "never for protocol decisions" (`:458-459`).
- **Notification shape** is never spelled out as a table row; derivable: `type:"notification"` + `method` (+ optional `params`), **no `id`**, no reply (heartbeat description `:187-188`; method table `:495`).
- **Correlation**: responses carry the request `id` (`:141`, `:444`); one in-flight map per connection is enough.

### B.4 §6.3 / §6.4 / §6.5 — methods, session.begin, errors

**§6.3 method set (`SPEC.md:481-505`)** — "**Seven** methods … **four** defined by this spec (`hello`, `heartbeat`, `session.begin`, `session.end`) and **three** MCP methods forwarded verbatim (`tools/list`, `tools/call`, `notifications/*`)." (`:483-485`)

| Method | Type | Direction | Purpose (`:487-495`) |
|---|---|---|---|
| `hello` | request | client → center | Handshake |
| `heartbeat` | notification | client → center | Liveness; one-way; server declares client dead after timeout |
| `session.begin` | request | center → client | Open session; returns catalog |
| `session.end` | request | center → client | Close session |
| `tools/list` | request | center → client | MCP core, forwarded |
| `tools/call` | request | center → client | MCP core, forwarded |
| `notifications/*` | notification | both | MCP core, forwarded |

- "`session.begin` is what *returns* the catalog in the common path — the center does not need a separate `tools/list` round-trip" (`:497-499`); `tools/list` "remains available for a mid-session refresh" (`:499`).
- "**There is no `auth` method.**" (`:501`)

**§6.4 session.begin (`SPEC.md:507-579`)**
- Request params: `{ "machine_id": ..., "context": { "agent_id": ..., "reason": ... } }` (`:519-525`).
- Response: envelope `session_id` **plus** `result{ session_id, machine_id, expires_at_ms, tools:[…] }` (`:529-568`).
- **Tool-catalog entry schema shown**: `{ "name", "description", "inputSchema": { type, properties, required?, additionalProperties } }` (`:539-565`). No `title`, `_meta`, `annotations`, `outputSchema`, `icons` appear in the example.
- "The `session_id` in the **envelope** is the canonical one … The `session_id` repeated inside `result` is the same value … the two **MUST** match, and a mismatch is a protocol error." (`:570-573`)
- Catalog contract: "tool entries are the **MCP tool objects as returned by the embedded server**, forwarded with their `inputSchema` intact. The wrapper **MUST NOT** rewrite, rename, or drop schemas. If a tool is gated off … it is **omitted from the catalog**" (`:575-579`).

**§6.5 typed errors (`SPEC.md:581-628`)** — COMPLETE list, verbatim:
`PROTOCOL_VERSION_UNSUPPORTED`, `CAPABILITY_MISSING`, `AUTH_FAILED`, `MACHINE_NOT_ENROLLED`, `MACHINE_BUSY`, `SESSION_NOT_FOUND`, `SESSION_EXPIRED`, `TOOL_NOT_FOUND`, `TOOL_DENIED`, `TOOL_TIMEOUT`, `TOOL_ERROR`, `PAYLOAD_TOO_LARGE`, `RATE_LIMITED`, `HEARTBEAT_TIMEOUT`, `INTERNAL` (`:594-608`).
- Classes: protocol (`PROTOCOL_VERSION_UNSUPPORTED`, `CAPABILITY_MISSING`), auth (`AUTH_FAILED`, `MACHINE_NOT_ENROLLED`), conflict (`MACHINE_BUSY`), lifecycle (`SESSION_NOT_FOUND`, `SESSION_EXPIRED`), tool (`TOOL_NOT_FOUND`, `TOOL_TIMEOUT`, `TOOL_ERROR`), authz (`TOOL_DENIED`, "**Reserved for v0.2+ — not reachable in v0.1**", `:602`), transport (`PAYLOAD_TOO_LARGE`, `RATE_LIMITED`, `HEARTBEAT_TIMEOUT`), internal (`INTERNAL`).
- "`AUTH_FAILED` is returned as an HTTP 401 on the **WSS upgrade** … The remaining codes travel in-band as `error` objects" (`:587-590`).
- Wire shape: `{"v","type":"error","id","session_id"?,"error":{"code","message","data"?}}` (`:612-623`). "The `data` member is **optional** and its contents are **not** part of the contract. A client **MUST** tolerate its absence" (`:626-628`). Marked "*v0.1 draft, illustrative*" (`:610`).
- **Which codes the center MUST emit:** the spec states one hard MUST — `PROTOCOL_VERSION_UNSUPPORTED` (§5.2 `:307-308`). `AUTH_FAILED` is structurally mandatory (401 on refusal). The rest are conditional obligations implied by the semantics (`MACHINE_BUSY` in §8.3 `:789`, `SESSION_EXPIRED`/`TOOL_TIMEOUT` semantics). §10 requires the **kit** to make every v0.1-reachable code reachable and reported with the documented shape (`:880`), which effectively turns the table into the conformance target.

### B.5 §6.6 tools/call, §6.8 session.end + heartbeat loss

**§6.6 (`SPEC.md:630-663`)**
- Request: `params{ "name": ..., "arguments": {...} }` with `session_id` in the envelope (`:632-643`).
- Response: `result` is the MCP result **as-is** — `{"content":[{"type":"text","text":…}],"isError":false}` (`:649-660`).
- Pass-through rule: "The client forwards this to the embedded MCP server … and returns the MCP result **as-is**" (`:646-647`); "`params` is the **MCP `tools/call` parameter object, unchanged**. `name` is the tool name as advertised in the session catalog." (`:662-663`).
- Timeout: §6.7 (`:678-679`) + §9.3 (`:844-858`) — "The timeout is enforced by the **client**"; on expiry `TOOL_TIMEOUT` "including the offending tool name and the timeout value in `error.data`"; "A timeout **does not** end the session"; "A hung embedded server is a separate failure … the wrapper's supervisor restarts it and the *session* is ended".

**§6.8 (`SPEC.md:688-710`)**
- `session.end`: "The client ends the session locally (releasing the local lock), returns an acknowledgement, and the connection returns to `ready`." (`:690-692`)
- Heartbeat loss = kill, not pause: server kills the session, not resumable (`:694-699`).
- The client that loses the server "**reconnects** with exponential backoff (§3.3) and starts over from the beginning — a fresh upgrade (re-presenting the bearer token), a fresh `hello`, then a fresh `session.begin`. There is no session resumption in v0.1." (`:700-703`)
- Trigger: no inbound frame for `client_dead_detect_ms`, or write failure / close frame (`:704-707`).
- "Reconnect **does not** require re-enrollment, as long as the bearer token is still valid … Re-enrollment is only needed after an auth failure (`AUTH_FAILED`)" (`:708-710`).
- **Who closes on death:** the spec says the *server kills the session* and the *client drops the socket and reconnects*. It never says the server sends `session.end` on death, and never says the server closes the socket after killing — but §6.2's diagram has `heartbeat lost → killed` (`:466`) and the client must reconnect, which implies a socket teardown.

### B.6 §8.2 / §8.3 — exclusivity

- **§8.2 (`SPEC.md:768-779`)**: one agent controls **one** remote at a time; "Overlapping two machines from a single agent thread is out of scope for v1 — it doubles the audit surface and doubles the blast radius of a mistake." No error code is named for violating it.
- **§8.3 (`SPEC.md:781-797`)**: "**Policy (v1): one active session per machine.**" When a second agent requests a session on a busy machine the center "**MUST NOT** multiplex it. It has exactly two legal behaviors: **Queue** the request … or **Reject** it immediately with `MACHINE_BUSY` (§6.5)." "Which of the two a center chooses is a product decision, but the *center* decides it, and it **MUST** be one of the two. The machine itself **MUST** refuse a concurrent `session.begin` for the same `machine_id` — the lock is enforced on the machine too, so a buggy or malicious center cannot bypass it."
- Busy code: **`MACHINE_BUSY`** (`:789`, `:598`).
- Note §8 is prefixed "**RECOMMENDED — NOT WIRE PROTOCOL**" (`:751-755`) — except that the machine-side refusal is phrased as a MUST. §8.3's machine MUST is the only MUST in §8.

### B.7 §8.4 audit

Verbatim (`SPEC.md:799-804`):
> "Every forwarded command should be logged by the center with **who / machine / tool / args-hash**. Args are **hashed**, not stored verbatim, by default: a typed password should not end up in a log file because it was the argument to a tool."

- **REQUIRED field list (our DB assertion set)**: `who`, `machine`, `tool`, `args-hash` — four concepts, no field names, no types, no table.
- **"args hashed"**: the spec names **no algorithm, no encoding, no canonicalisation**. It says only "hashed, not stored verbatim, by default". `hash` appears nowhere else in SPEC.md.
- **Optional fields**: none named. §4.2 (target tier) adds "Non-repudiable, queryable, exportable" (`:248`) — properties, not fields.
- §8 is RECOMMENDED, not wire protocol (`:751-755`).

### B.8 §10 conformance kit — verbatim status + suggested pointer

Verbatim (`SPEC.md:862-885`):
> `## 10. Conformance kit (future)`
> "> **Stub — this section is a placeholder.** No conformance suite exists yet."
> "The plan is a **shared test suite** that **both** centers must pass, so that "implements the remote-hands protocol" is a checkable claim rather than a claim of similarity. The kit is expected to live in this repo (or in a dedicated `conformance` package) and to be runnable against a center with nothing but a URL and a machine."
> Table rows (`:874-881`): Handshake / Session lifecycle / Transport / MCP fidelity ("survive the round trip **byte-identically**") / Errors ("Every **v0.1-reachable** typed code … reachable") / Auth ("the credential carrier is the upgrade header, not a protocol message … the kit tests the **upgrade** behaviour, not a token-issuance flow").
> "Until the kit exists, conformance is established by **reading both implementations against this document** — which is exactly the friction the kit is meant to remove." (`:883-885`)

**Suggested POINTER text (not applied — repo is read-only for us):**
> **Pointer (2026-10-22).** The client half of a harness exists in this repo: `internal/fakecenter` is the server side of this protocol (independently transcribed, TLS on loopback) and `internal/remote/conformance_test.go` drives the real client against it over a real WSS connection. The reference center's interop run and the divergences it found are recorded outside this repo, in `agents-ensemble/.agents/shared/planning/mcps-gateway-interop/research-spec-go.md`. What §10 still does not have is the *runner*: a kit that points a client at an arbitrary center with nothing but a URL and a machine.

### B.9 §11 open questions bearing on interop (`SPEC.md:889-897`)

1. **Port / token configuration** (`:893`) — "Which loopback port does the local `streamable-http` listener bind? How is the local bearer token minted, stored, and rotated? How is the enrollment token delivered in the field?" → bears directly on D1 setup: there is no enrollment endpoint, so the test token must be pre-provisioned on both sides by hand.
2. **Multi-session policy evolution** (`:894`) — lock vs queue vs lease vs approval gate. Center chose reject (`sessions.py:1-13`).
3. **Streaming vs polling for events** (`:895`) — "v0.1 forwards notifications opportunistically, without a delivery guarantee." Both sides currently relay **nothing** (F.3).
4. Windows/Linux permission models (`:896`) — not interop-relevant for D1 (macOS).

---

## PART D — Go client drive

### D.1 Build

- **Command**: `make build` → `go build -trimpath -ldflags "-X main.version=$(VERSION)" -o dist/mcps-client ./cmd/mcps-client` (`Makefile:23-24`); `VERSION ?= 0.1.0` (`:13`). Equivalent raw: `go build -o dist/mcps-client ./cmd/mcps-client`.
- **Output**: `dist/mcps-client` (`Makefile:14,24`). `make build` does **not** assemble the `.app` (`Makefile:5-6`, `README.md:182-185`); `make app` = `scripts/build.sh --bundle` (`Makefile:64-65`).
- **Go version**: `go 1.26` (`go.mod:3`).
- **Deps**: `github.com/gorilla/websocket v1.5.3`, `github.com/modelcontextprotocol/go-sdk v1.8.0`, `gopkg.in/yaml.v3 v3.0.1` (+ indirects) (`go.mod:5-20`).
- **Build tags**: none in the Makefile; no `-tags` anywhere in `Makefile`. **UNVERIFIED: verify against go-recon findings** (no build-tag search of the tree was possible).
- **Network**: `go build` needs the module cache for the three direct deps (a cold cache ⇒ network). No `vendor/` directory at repo root (`list_directory` of the repo root: `Makefile README.md SPEC.md cmd/ docs/ go.mod go.sum internal/ packaging/ scripts/` + stray `*.db` files). `make test-integration` additionally resolves the npm package (`Makefile:37-42`); a plain build never runs npm.
- **Test gates**: `make test` = `go test -short ./...` (`:30-31`); `make test-integration` = `go test ./...` (`:41-42`); `make check` = vet + short tests (`:60-61`).

### D.2 CLI surface

**Flags** (all `flag` package, no subcommands) — `cmd/mcps-client/main.go:66-81`:
`-config` (path to YAML; default `""`), `-log-level` (default `info`: debug/info/warn/error), `-version`, `-print-launchagent`, `-check-config`, `-launchagent-exec`, `-launchagent-label`, `-launchagent-log-dir`, `-launchagent-path`.
**No** `--center-url`, `--enroll-token`, `--machine-id` flags: the center URL, the token and the machine id are **YAML/env only**.

**Env vars**:
- `MCPS_CLIENT_CONFIG` — config path fallback before the default (`main.go:96-99`).
- `MCPS_CLIENT_TOKEN` — local listener bearer; **wins over file**, applied before validation (`internal/config/config.go:51,416-423`).
- `MCPS_CLIENT_ENROLLMENT_TOKEN` — remote bearer; wins over file (`config.go:59,420-422`).
- `MCPS_NPX_BIN` — absolute npx path injected into the child env for the bundled launcher (`config.go:68`).

**Config path resolution**: `-config` → `MCPS_CLIENT_CONFIG` → `os.UserConfigDir()/mcps-client/config.yaml` (`main.go:96-102`; `config.go:1219-1225`).

**YAML keys + defaults** (`packaging/config.dev.example.yaml`, `packaging/config.example.yaml`, `internal/config/config.go:299-339`):

| Key | Default | Source |
|---|---|---|
| `listen.addr` | `127.0.0.1:8765` (loopback-only, enforced) | `config.go:35,499-501`; dev yaml `:35` |
| `listen.token` | **required**, ≥32 chars, placeholders refused | `config.go:44,523-527`; dev yaml `:43` |
| `listen.path` | `/mcp` | `config.go:303`; dev yaml `:45` |
| `child.command` | `npx` (dev) / bundled `computer-use-mcp.sh` (app) | `config.go:36`; dev yaml `:58` |
| `child.args` | `["-y","@zavora-ai/computer-use-mcp@7.6.1"]` | `config.go:37,308`; dev yaml `:59` |
| `child.env` | `[]` (secret-looking vars stripped) | dev yaml `:63`; `config.go:1166-1217` |
| `child.dir` | `""` (inherit) | dev yaml `:65` |
| `child.terminate_grace_ms` | `5000` | `config.go:309` |
| `restart.*` | enabled `true`, initial `1000`, max `60000`, max_attempts `0` (unlimited), multiplier `2.0`, jitter `0.2` | `config.go:311-318` |
| `policy.enabled` | `true` | `config.go:319-322` |
| `policy.deny_tools` | `[]` (empty = nothing gated) | dev yaml `:80-87` |
| `audit.enabled` | `false` | `config.go:323-327` |
| `audit.path` | `~/.config/mcps-client/audit.jsonl` (example) | dev yaml `:100` |
| `remote.enabled` | `false` | `config.go:330` |
| `remote.center_url` | `""` (required when enabled, must be wss/https) | `config.go:927-933` |
| `remote.enrollment_token` | `""` (required when enabled; `bearer_token` is a deprecated alias) | `config.go:934-939,217-228` |
| `remote.machine_id` | `""` → derived `mach_<8 hex>` from hostname | `config.go:229-231`; `remote/client.go:409-417` |
| `remote.server_pin_sha256` | `""` (empty ⇒ trust store + loud warning) | dev yaml `:137`; `client.go:355-362` |
| `remote.ca_pem` | `""` (PEM text or file path, added to system roots) | dev yaml `:141` |
| `remote.heartbeat_interval_ms` / `_timeout_ms` / `client_dead_detect_ms` | `15000` / `45000` / `45000` | `config.go:331-333` |
| `remote.reconnect_initial_backoff_ms` / `_max_backoff_ms` | `1000` / `60000` | `config.go:334-335` |
| `remote.tool_call_timeout_ms` | `30000` | `config.go:336` |
| `remote.max_frame_bytes` | `8388608` (must stay < 33554432) | dev yaml `:161`; `config.go:996-1000` |

Log level is a **flag only** (no YAML key). Unknown YAML keys are a **hard error** (`config.go:427-429,437`).

### D.3 Enrollment

- **No enrollment exchange exists.** The "enrollment token" is used verbatim as the upgrade bearer: `remote.enrollment_token` → `Options.EnrollmentToken` (`main.go:360`) → `Authorization: Bearer <token>` on every dial (`internal/remote/transport.go:201-206`).
- **No state file, no write-back, no first-run exchange.** The only persisted artifact is the YAML config. There is no state dir, no XDG state path, nothing to go stale.
- **Re-enrollment per run: never.** "Reconnect **does not** require re-enrollment, as long as the bearer token is still valid" (`SPEC.md:708-710`); the client only stops on `AUTH_FAILED` (`client.go:525-530`).
- **Test hygiene**: point `-config` (or `MCPS_CLIENT_CONFIG`) at a throwaway YAML per run; override `remote.machine_id` to pin the roster identity instead of relying on the hostname hash. There is no other mutable state.
- **Machine id derivation**: `mach_` + first 4 bytes of `sha256(hostname)` in hex (`client.go:409-417`).

### D.4 STUB CHILD

- **Yes — the child is an arbitrary MCP stdio command.** Keys: `child.command` + `child.args` (+ `child.env`, `child.dir`). Defaults only *default* to the pinned `cu` server (`config.go:34-38,306-310`); nothing else hardcodes `computer-use-mcp` in the launch path (`main.go:167-193` builds the supervisor from `cfg.Child.*`).
- **Resolution**: an absolute `child.command` must exist, be a file and executable (else a loud warning, not fatal); a bare name is resolved against PATH to an absolute path at load time (`config.go:736-749`, `:720-735`). A command that cannot be resolved is **not fatal** — the supervisor retries with backoff (`config.go:728-733`).
- **Built-in stub server usable as the child?** **No standalone stub binary ships.** `cmd/` contains only `mcps-client` (`list_directory cmd/`). The tests use "test stub children (in-process or re-executed test binaries)" (`docs/poc-findings.md:146-149`). The fake center's `echo` tool is a *center-side catalog entry*, not a child (`internal/fakecenter/fakecenter.go:489-493`). **UNVERIFIED: verify against go-recon findings** whether a test helper in `internal/mcpbridge` or `internal/supervisor` is invokable as a child binary.
- Practical D1 stub: any trivially-written stdio MCP server via `child.command`/`child.args` (e.g. a small python script), since the wrapper only requires an MCP handshake on stdio.

### D.5 Pointing at `ws://127.0.0.1:<PORT>/api/mcps-gateway/ws`

- **Key**: `remote.center_url` (YAML) — there is no CLI flag. Path must be **explicit**; the client only defaults to `/remote-hands/v1` when the URL has no path (`transport.go:277-279`).
- **Plaintext `ws://` is REFUSED.** `config.go:930-933` (load-time) and `transport.go:266-273` (dial-time, `"the remote hop is TLS only (SPEC.md §3.1)"`). `http://` is refused too; only `wss://`/`https://` pass, `https` rewritten to `wss` (`transport.go:266-269`).
- **TLS trust knobs** that make a local self-signed terminator work: `remote.ca_pem` (PEM or path, added to system roots — `config.go:239-241,259-262`) and `remote.server_pin_sha256` (hex SPKI, base64, or `sha256/<base64>` — `transport.go:111-118`, `remote.ParsePin`). Pin is enforced **in addition** to chain validation (`transport.go:283-303`). No pin ⇒ loud warning, trust store only (`client.go:355-362`).
- **Consequence for D1**: `remote.center_url: wss://127.0.0.1:<TLS_PORT>/api/mcps-gateway/ws` behind a TLS terminator, **or** a code change to the client. Nothing in the client's config can enable plaintext today.

### D.6 Test/driving aids

- **`internal/fakecenter`** = a fake **CENTER** (server side), "the seed of the conformance kit that SPEC.md §10 asks for" (`fakecenter.go:1-21`). Independently transcribed wire vocabulary (literals, not aliases — `:8-17,50-112`). Binds `127.0.0.1:0` over **TLS** (`:538-560`, `tls.Listen` at `:557`), `URL()` returns `wss://<addr>/remote-hands/v1` (`:611-617`), self-signed cert (`:572-609`), `CAPEM()`/`SPKIPin()` (`:622-637`), WS ping every 200 ms (`:266-270,347-354`).
- **Options worth reusing as test knobs**: `Token`, `ProtocolVersion`, `SupportedVersions`, `Capabilities`, `Heartbeat`, `DropAfterUpgrade`, `OmitHeartbeat`, `Catalog`, `CallResult`, `TLS`, `PingInterval`, `EnrolledMachineIDs`, `RequiredClientCapabilities`, `SessionIdleTimeout`, `SessionTTL`, `ExclusiveSessions`, `HeartbeatTimeoutGrace`, `AgentID`, `Reason` (`:299-399`).
- **Can it be inverted/reused against a real center?** **No.** It is the server half. It is **library-only** — no `cmd/` entrypoint, no `main` — so it cannot even be run as a process; it is driven from Go tests (`internal/remote/conformance_test.go`, `client_test.go` etc.).
- **Script that runs the client against a URL?** **No.** `scripts/` contains only `build.sh` and `install-launchagent.sh` (`list_directory scripts/`). The README documents **only** the local-mode run (`README.md:76-87`) plus the `.app`/LaunchAgent install (`:187-214`); there is **no documented "run against a real center" procedure** — the remote hop is enabled only through YAML and the README never shows it.
- The fake center's ping cadence is the reason a center MUST produce liveness evidence (`docs/spec-interpretations.md:177-184`): "A center that only answers requests — and never pings — looks dead to a **correct** client, and the client reconnects in a loop."

### D.7 Emitted artifacts useful for assertions

**Audit JSONL** (`internal/audit/audit.go`)
- Enabled by `audit.enabled` (default **false**), path `audit.path` (`config.go:270-276`; example `packaging/config.dev.example.yaml:100`). File created `0600`, parent dir `0700` (`audit.go:136,140`).
- **One JSON object per line** (`audit.go:8-11,158-165`).
- Fields (`audit.go:92-113`): `timestamp`, `session_id` (omitempty), `machine_id` (omitempty), `agent_id` (omitempty), `tool`, `args_hash`, `outcome`, `detail` (omitempty).
- `timestamp` = `2006-01-02T15:04:05.000Z` UTC (`audit.go:170-173`).
- `outcome` ∈ `ok|denied|timeout|error` (`audit.go:50-60`). `detail` is a **fixed classification**, never child text (`audit.go:62-85`), redacted against the args (`audit.go:251+`).
- `args_hash` = SHA-256, lower-case hex, over canonicalised args (`audit.go:175-206`) — see F.6 for the exact canonicalisation mismatch with the center.

**Log lines** (slog, level from `-log-level`):
- `"the remote hop is enabled"` with `center_url`, `machine_id` — `main.go:379-381`.
- `"the center accepted the handshake"` with `heartbeat_interval_ms`, `client_dead_detect_ms` — `client.go:578-580`.
- `"reconnecting to the center"` with `delay`, `attempt` — `client.go:470`.
- `"the remote hop stopped"` (fatal) — `client.go:463`, re-logged at `main.go:300`.
- `"the center went silent; the connection is dropped"` with `silence`, `limit` — `client.go:927-929`.
- `"the center closed the connection"` with `code`, `reason` — `transport.go:250`.
- `"the embedded MCP server was restarted; the session is ended"` — `client.go:384-385`.
- `"the center sent a notification; the client relays none …"` (warning) — `client.go:852-853`.
- `"the request queue is full; the load is shed"` — `client.go:832-833`.

**Exit codes**
- Any `run()` error → `fmt.Fprintf(os.Stderr, "mcps-client: %v\n", err)` + `os.Exit(1)` (`main.go:57-62`).
- **A fatal remote error does NOT exit the process**: it is logged and the local listener keeps serving (`main.go:293-303`). So `remote hop died` ⇒ **exit code 0 while the process runs**; only a *local* failure (bad config, listener bind, child-ready timeout) yields 1.
- SIGINT/SIGTERM → graceful shutdown → `nil` → exit 0 (`main.go:196-233`).

### D.8 What this read did NOT cover (hand to go-recon)

**UNVERIFIED: verify against go-recon findings** for:
1. `internal/remote/queue.go`, `backoff.go`, `state.go` — exact state names/transitions, queue capacity constant `DefaultQueueCapacity`, jitter algorithm.
2. Exact WebSocket **close codes** the client emits on fatal paths (`conn.Close()` from gorilla vs an explicit `WriteControl`); whether a close frame is sent at all on a version error mid-connection.
3. Any build tags / platform files (`process_unix.go`/`process_other.go` exist under `internal/supervisor`).
4. `internal/mcpbridge` internals (local hop, SDK re-encoding, catalog raw bytes) beyond what `docs/spec-interpretations.md:396-414` states.
5. The 1571-line `internal/fakecenter` beyond `Options`/`Start`/`URL`/`Cert`/`CAPEM`/`SPKIPin` — i.e. its scenario knobs' exact behaviours.
6. Whether any test helper binary can serve as a stub `child.command`.

---

## PART F — CROSS-IMPLEMENTATION AMBIGUITY

### F.1 The Go side's committed interpretations (`docs/spec-interpretations.md`, 443 lines — read fully)

| # | Interpretation (short) | Spec | Code |
|---|---|---|---|
| 1.1 | Policy denial ⇒ **`TOOL_NOT_FOUND`**, not `TOOL_DENIED` (gated tool is absent from the catalog) | §6.5 `:602`, §6.4 `:578`, §9.1 | `remote.DenialErrorCode`, `client.go:94-107` |
| 1.2 | `tools/call` with no session ⇒ **`SESSION_NOT_FOUND`** | §6.2 `:477-478`, §6.5 silent | `client.go:1073-1082` |
| 1.3 | Unknown method ⇒ **`INTERNAL`** (no METHOD_NOT_FOUND exists) | §6.3, §6.5 | `client.go:947-950` |
| 1.4 | `session.begin` for another machine ⇒ **`MACHINE_NOT_ENROLLED`** (mirror of the center-side code) | §6.4/§6.5 `:597` | `client.go:971-979` |
| 1.5 | Timeout classified by the **call deadline**, not the child's error text | §9.3 | `client.go:1127-1136` |
| 1.6 | Tool outside the session catalog ⇒ **`TOOL_NOT_FOUND`**, disambiguated by non-contractual `data.gated` | §6.5 `:601` | `client.go:1103-1113` |
| 1.7 | Error for an unknown session carries **no `session_id`** | §6.1 `:445` | `client.go:1084-1091` |
| 2.1 | A hello result version ≠ requested ⇒ **fatal, not retried** | §5.2/§5.5 | `client.go:626-631` |
| 2.2 | Heartbeat params **outside** the permitted range are adopted (loudly); non-positive ⇒ fatal | §3.3 `:173-174` | `client.go:651-665` |
| 2.3 | Missing heartbeat params ⇒ **configured fallbacks** | §5.4 | `client.go:640-650` |
| 2.4 | **Every** inbound envelope's `v` is checked; mismatch ⇒ fatal, socket closed | §6.1 `:442`, §5.2 | `client.go:785-796` |
| 2.5 | `MACHINE_NOT_ENROLLED` ⇒ **distinct fatal error** (no retry) | §6.5 `:597` | `client.go:678-692` |
| 3.1 | Client advertises **`notifications:false`** and **demands no** notifications capability; required = {session,tools,heartbeat} | §5.3 `:330` | `client.go:282-305`, `protocol.go:87-117` |
| 4.1 | Pin = **SHA-256 of SPKI**, accepted hex / base64 / `sha256/<base64>` | §4.1 `:221` | `transport.go:111-118` |
| 4.2 | No pin ⇒ trust store, warned once; still no TOFU | §4.1 | `client.go:355-362` |
| 4.3 | A conforming center **must** produce liveness evidence (WS ping/pong/traffic) | §3.3 `:194-200` | `transport.go:231-252` |
| 4.4 | Oversized **inbound** frame: request answered `PAYLOAD_TOO_LARGE`, connection survives; other types drop the socket; never truncate | §6.7 `:674-677` | `transport.go:63-90`, `client.go:797-816` |
| 4.5 | A configured frame limit ≥ hard ceiling is **refused** (fail closed) | §6.7 | `config.go:983-1000` |
| 4.6 | Refusal realm = `mcps_client` (spec silent) | §4.1 | `fakecenter.go:123-134` |
| 5.1 | **Accept concurrently, execute serially** (per-session FIFO) | §3.1 `:140-141` | `queue.go`, `client.go:725-739` |
| 5.2 | Session belongs to its connection; dropped with the socket (else next begin ⇒ MACHINE_BUSY) | §6.8 `:700-703` | `client.go:516-521,543-546` |
| 5.3 | Reconnect backoff counts **consecutive failures to establish**; reset after a handshake | §3.3 | `client.go:419-445` |
| 5.4 | Socket break during handshake ⇒ handshake fails at once | §5.1/§6.8 | `client.go:479-508` |
| 5.5 | Catalog fetch deadline (**10 s**) ⇒ `TOOL_TIMEOUT`; other fetch failures ⇒ `INTERNAL` | §6.4/§6.5 | `client.go:47-55,1162-1195` |
| 5.6 | Queue capacity **32**; overflow shed immediately with `RATE_LIMITED` | §6.5 `:606` | `queue.go`, `client.go:821-839` |
| 5.7 | Restarted child ⇒ session ended **locally**, connection stays | §9.3 `:856-858` | `client.go:372-388` |
| 5.8 | **Heartbeat carries the `session_id`** when a session is open | §6.1 `:445`, §6.4 `:570` | `client.go:860-901` |
| 5.9 | `tools/list` served **with or without** a session; `tools/call` always needs one | §6.2 `:477` vs §6.3 `:499` | `client.go:1039-1053` |
| 5.10 | Session TTL is **advertised, not enforced** by the client (center owns it) | §3.3/§6.4/§6.8 | `client.go:992-1009` |
| 5.11 | Terminal state named **`killed`** | §6.2 `:466` | `state.go` (`StateKilled`) |
| 5.12 | `session.end` is idempotent even with unreadable params | §6.8 | `client.go:1013-1037` |
| 5.13 | A center notification is **dropped**, loudly | §6.3 `:495`, §5.3 | `client.go:842-853` |
| 6.1 | **args-hash = SHA-256 over canonicalised JSON**, lower-case hex | §8.4 (silent) | `audit.go:175-206` |
| 6.2 | Record fields `timestamp, session_id, machine_id, agent_id, tool, args_hash, outcome, detail` | §8.4 (prose only) | `audit.go:92-113` |
| 6.3 | Record's machine id = **the wrapper's own** machine | §8.4 | `client.go:1244-1254` |
| 6.4 | Audit `detail` is a **fixed classification**, never peer text | §8.4 `:802-804` | `audit.go:62-85` |
| 6.5 | Redaction: whole-args removed always; ≥8-byte values everywhere; short values only as whole tokens | §8.4 | `audit.go:251+` |
| 7.1 | Catalog is **re-encoded by the typed SDK layer** (keys sorted; unknown members dropped; `icons` now forwarded) — §10's "byte-identical" criterion is **not met** | §6.4 `:575-579`, §10 `:879` | `remote/protocol.go:305-331`, `mcpbridge` |
| 9.1 | **Amendment request**: §3.3 should say who owns `session_ttl_ms` (client says center) | §3.3/§6.4/§6.8 | — |

### F.2 Divergence table

| SPEC clause | Go-side reading | Center-side reading (file:line) | Likely divergence | Test that would catch it |
|---|---|---|---|---|
| **§3.1 transport** `:138` "WSS, TLS 1.2+" | wss:// only, TLS1.2 min, plaintext refused at load **and** dial (`config.go:930-933`, `transport.go:266-273,296`) | Plain HTTP/WS only; no TLS knob (`config.py:160-176`; `dev.sh:155`) | **HARD BLOCKER** — client cannot dial the daemon at all | Point client at `ws://…` ⇒ config error; point at `wss://` to bare uvicorn ⇒ TLS handshake failure. D1 needs a terminator |
| **§5.4 path** `:355` `/remote-hands/v1` (illustrative) | Default path `/remote-hands/v1` when URL has none (`transport.go:47-50,277-279`) | `/api/mcps-gateway/ws` (`config.py:52`) | None if URL carries the path; a bare `wss://host:port` silently dials the wrong path (404) | Dial `wss://host:port` (no path) ⇒ expect 404 |
| **§5.2 version string** `:304` "dotted string, currently 0.1" | Exact `"0.1"`, compared with `!=` (`protocol.go:33`, `client.go:626`) | Exact `"0.1"`, compared with `!=` (`protocol.py:32`, `service.py:482`) | None | hello with `"0.1.0"` ⇒ both must reject with `PROTOCOL_VERSION_UNSUPPORTED` |
| **§5.2/§5.5 mismatch failure** `:307-309,428-429` | Center's error envelope → `fatalFromReply` → fatal, no retry, socket closed (`client.go:619-621,678-692`) | Sends typed error with `data{requested,supported}` then `close_connection` (`service.py:482-497`) | None on the typed error. **Close code is 1000 on both sides** (center `transport.close()` default `1000`, `router:189-194`, `service.py:316`) — spec names no code | Assert envelope code+data; assert client does **not** reconnect; assert close code (kit must pin a value the spec never gave) |
| **§5.3 capability failure mode** `:595` | Requires {session,tools,heartbeat}; missing ⇒ **fatal** (`protocol.go:87-117`, `client.go:632-634`) | Requires {session,tools,heartbeat}; missing ⇒ error + **close** (`protocol.py:77-83`, `service.py:515-536`) | Consistent between the two, but both **deviate from a literal §5.3** (`notifications` is "required" and both treat it as offering-side only) | hello with `notifications:false` must be accepted by both; hello missing `tools` must be refused by both |
| **§6.1 `id` type** `:444` "opaque strings" | `"req-%04d"` (`client.go:1416-1421`), echoed verbatim (`client.go:1261-1277`) | `"ctr-%04d"` (`service.py:592-594`), routed by exact string (`service.py:438-455`) | None (both strings) — but note the client's counter is **per-process, not per-connection**, so ids continue across reconnects (allowed: "unique per connection" is a minimum) | Assert the response `id` equals the request `id` byte-for-byte |
| **§6.1 `id` required on errors** `:444` | Always echoes the request id on error (`client.go:1292-1298`) | Heartbeat kill sends an error with **`id=None`** (`service.py:715-725`) | **DIVERGENCE**: §6.1 marks `id` required for `error`; the client cannot correlate it (`deliver` drops it, `client.go:1400-1414`) ⇒ `HEARTBEAT_TIMEOUT` is **unobservable** to the client despite §6.5 `:607` giving it a client action | Kill heartbeats; assert (a) client logs reconnect, (b) client **never** surfaces `HEARTBEAT_TIMEOUT`; kit should flag the id-less error |
| **§6.1 `session_id` on pre-session errors** `:445` | Omits it when the session was never validated (`client.go:1076-1091`) | Omits it via `_live_session_id` when no live session (`service.py:403-416`, `protocol.py:214-231`) | None | Error for unknown session ⇒ no `session_id` in either direction |
| **§6.3 method direction** `:487-495` | Client sends only `hello` (request) + `heartbeat` (notification); answers session/tools (`client.go:936-951`) | Center sends only session.begin/end/tools/call; never `tools/list` (`protocol.py:57-66`) | None, but `tools/list` is **dead on the wire** — the client supports it (`client.go:1039-1053`) and the center never issues it (`protocol.py:48-53`) | A kit must not require `tools/list` from a v0.1 center |
| **§6.4 session.begin params** `:519-525` | Sends `{"machine_id","context":{"agent_id","reason"}}`; refuses another machine (`client.go:955-985`) | Sends `{"machine_id","context":{"agent_id","reason"}}` (`service.py:854-858`) | None | Assert machine_id round-trip + another-machine refusal code |
| **§6.4 catalog field names** `:539-565` | Emits `name,title,description,inputSchema,outputSchema,annotations,_meta,icons` (`protocol.go:305-331`) — camelCase `inputSchema` | Stores and re-serves the catalog **untouched** (`sessions.py:49-51`, `service.py:875-876`) | None on names. **Byte-level**: the client's schema is the SDK re-encode (keys sorted, unknown members dropped) ⇒ §10 "byte-identically" fails by construction (`spec-interpretations.md:396-414`, `poc-findings.md:125`) | Compare the child's catalog bytes with what `connect()` returns |
| **§6.4 `session_id` envelope == result** `:570-573` | Both set to the minted id (`client.go:1002-1010`) | Reads `result.session_id`, does **not** compare it to the envelope (`service.py:860-867`) | **LATENT**: center would accept a mismatched pair the spec calls a protocol error | Craft a response whose envelope/result ids differ ⇒ center must reject; today it does not |
| **§6.5 error naming** `:594-608` | Literals transcribed (`protocol.go:120-140`); emits `TOOL_NOT_FOUND` for denials (`client.go:94-107`) | Literals transcribed (`protocol.py:101-116`); `TOOL_DENIED` never emitted (`protocol.py:110`) | None on names | Assert every emitted code ∈ §6.5 set; assert `TOOL_DENIED` never appears |
| **§6.5 `data` optional** `:626-628` | Client tolerates absence; adds non-contractual `gated` (`client.go:1100,1112`) | Center adds `requested/supported/missing/…`; tolerates any dict (`service.py:444-452`) | None | Strip `data` from every error ⇒ both must still behave |
| **§6.6 tools/call pass-through** `:646-663` | Sends `{"name","arguments"}`; returns the raw MCP result object (`client.go:1156-1159`) | Sends `{"name","arguments": arguments or {}}`; returns result, wrapping non-dicts as `{"result": …}` (`service.py:941-947,454-455`) | **LATENT**: a non-object MCP result (e.g. a JSON array) is re-wrapped by the center | A stub child returning a non-dict result |
| **§6.6/§9.3 timeout** `:678-679,844-858` | Client enforces 30 s, answers `TOOL_TIMEOUT` with `data{tool,timeout_seconds}` (`client.go:1115-1153`) | Center enforces its **own** 30 s on the same call (`config.py:69`, `service.py:602-632`) | **RACE**: equal defaults ⇒ either side may win; both emit `TOOL_TIMEOUT` with different `data` | Set `MCPS_GATEWAY_COMMAND_TIMEOUT_MS` < client `tool_call_timeout_ms` to test the center arm deterministically |
| **§6.8 session.end** `:690-692` | Requires a matching session, acks `{"session_id"}`; unreadable params still end (`client.go:1013-1037`) | Sends `session.end` on disconnect and on refused-connect release (`service.py:1005-1046,973-1003`) | None | `mcp_rpc_disconnect` ⇒ client acks, socket stays open, next begin works |
| **§6.8 death semantics** `:694-710` | Client drops session on socket loss, reconnects from scratch (`client.go:516-546,455-477`) | Center kills the session on heartbeat timeout, sends `HEARTBEAT_TIMEOUT` (id-less), closes (`service.py:703-727`) | Behaviorally consistent; **the code is unobservable** (see `id` row). Center does **not** send `session.end` on death — the spec doesn't require it | Heartbeat-silence scenario: assert reconnect + fresh `session.begin`, and that the old session id is dead |
| **§8.2 one remote per agent** `:768-779` | No client-side notion | Rejects a second machine for the same agent with `MACHINE_BUSY` + `data.reason="agent_already_holds_session"` (`service.py:840-852`) | None; spec names no code, center reuses `MACHINE_BUSY` | Connect agent A to M1 then M2 ⇒ `MACHINE_BUSY` |
| **§8.3 exclusivity** `:781-797` | Machine refuses a second `session.begin` with `MACHINE_BUSY` (`client.go:964-985`) | Center rejects first, before any frame (`sessions.py:118-133`, `service.py:836-838`) | None — both halves implemented (center = reject, not queue) | Two agents on one machine ⇒ second gets `MACHINE_BUSY` and **no** frame reaches the machine |
| **§8.4 audit fields** `:799-804` | `timestamp, session_id, machine_id, agent_id, tool, args_hash, outcome, detail` (`audit.go:92-113`) | `id, created_at, agent_id, instance_id, machine_id, tool, args_hash, outcome, session_id, error_code, detail` (`repositories/mcps_gateway/models.py:80-111`) | **Naming/coverage divergence**: `who` is `agent_id` on both (center adds `instance_id`); `machine` = `machine_id`; `args-hash` = `args_hash` (both snake_case); client has `timestamp`, center has `created_at` | DB assertion set: `agent_id, machine_id, tool, args_hash` non-empty; `outcome ∈ {ok,error,denied,timeout}`; **no column contains the raw arguments** |
| **§8.4 args-hash algorithm** (spec silent) | SHA-256 over `json.Marshal(decoded)` — Go: sorted map keys, **HTML escaping** (`<`,`>`,`&` → `\u003c…`), U+2028/2029 escaped, non-ASCII **raw UTF-8**, floats shortest-form, `nil` args ⇒ `"null"` (`audit.go:181-206`) | SHA-256 over `json.dumps(sort_keys=True, separators=(",",":"))` — Python: **`ensure_ascii=True`** ⇒ all non-ASCII escaped, no HTML escaping, floats keep `.0`, `None` args ⇒ `"{}"` (`protocol.py:246-252`, `audit.py:58-65`) | **REAL DIVERGENCE** for any args with non-ASCII, `<>&`, U+2028/2029, floats like `1.0`, or nil args. ASCII-only flat objects agree | Same `tools/call` args through both paths, compare `args_hash`: `{"text":"héllo <b> 1.0"}` ⇒ differs; `{"x":1,"y":2}` ⇒ matches |
| **§10 byte-identical MCP fidelity** `:879` | Explicitly **not met** (typed SDK layer, `spec-interpretations.md:396-414`) | Passes catalog through untouched, but cannot restore what the client dropped | Known, documented client-side limitation | Catalog byte-diff child vs center ⇒ differs (keys sorted) |
| **§3.3 client-death detection** `:194-200` | Needs an inbound frame within `client_dead_detect_ms` (`client.go:903-934`) | Pings via ASGI protocol introspection every 200 ms; **degrades silently to no-ping** if the protocol object is unreachable (`router:77-103,172-184`) | **Operational divergence**: a uvicorn layout change ⇒ client reconnect loop with no protocol-level cause | Hold a connection > `client_dead_detect_ms` and assert `transport.pings_sent > 0` / no reconnect |
| **Kill switch** (`config.py:3-7`, `router:24-30`) | 404 on upgrade ⇒ **transient** ⇒ reconnect loop (`transport.go:208-219`, `client.go:455-477`) | Route absent when `MCPS_GATEWAY_ENABLED` off; empty `TOKENS` ⇒ 401 on every upgrade (`service.py:247-252`) | 404 = retry loop; **401 = fatal, remote hop stops** (`client.go:525-530`) while the local listener keeps serving | Boot with `ENABLED=0` ⇒ expect 404 + retries; boot with `ENABLED=1` + no tokens ⇒ expect fatal `AUTH_FAILED` |

### F.3 Facts worth pinning in the D1 harness

- Both sides independently concluded **`notifications` is offering-side only** (`protocol.go:87-104`, `protocol.py:77-83`) — a literal §5.3 reader would kill every connection. The kit must encode the interpretation, not the literal text.
- Both sides **transcribe the wire vocabulary as literals** (`protocol.py:1-7`, `fakecenter.go:8-17`) so a rename cannot silently pass.
- The center mints **no** session id; it adopts the machine's (`sessions.py:41-43`, `service.py:860`). `connect()` returns `expires_at_epoch_ms` on the *agent* surface (`service.py:898-903`) while the wire uses `expires_at_ms` (`protocol.go:340`) — agent-surface naming only, no wire impact.
- Center pre-hello reap at 30 s (`service.py:106-109,687-702`) is not in the spec.
- Center refuses an offline-but-enrolled machine with `MACHINE_NOT_ENROLLED` + `data.presence="offline"` (`service.py:823-834`) — no `MACHINE_OFFLINE` exists in §6.5.
- Client's `hello` id keeps incrementing across reconnects (`client.go:241-242,1416-1421`); harmless, but a kit asserting `req-0001` on every connection would fail.
