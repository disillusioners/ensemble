# MCPS Gateway — server-side (Phases A/B) ground truth for cross-implementation interop

Repo: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble` @ `latest` (9632badd4)
Scope: server side only (daemon). Shipped Go client (`mcps_client`) is external.
Read-only research; no source file modified.

---

## 1. WS ROUTE

- **Registration site** — `daemon/api.py:4563-4589` (inside `create_app`):
  - `gateway_config = load_gateway_config()` — `daemon/api.py:4563`
  - `if gateway_config.enabled:` — `daemon/api.py:4566` (import at `4567`, build at `4569-4571`)
  - mount inside `api_router` (prefix `/api`) — `daemon/api.py:4572-4573`
  - app-level mount for a path outside `/api` — `daemon/api.py:4599-4600` (AFTER `app.include_router(api_router)` at `4591`, BEFORE the SPA catch-all)
  - route object itself: `router.add_api_websocket_route(tail, _default_endpoint, name="mcp_gateway_ws")` — `daemon/routers/mcps_gateway.py:441-445`
- **Kill switch** `MCPS_GATEWAY_ENABLED` **DEFAULT OFF** — `daemon/services/mcps_gateway/config.py:164` (`enabled: bool = False`), parsed at `config.py:287` (`_env_bool("ENABLED", False)`), truthy set `1/true/yes/on` (`config.py:95`, `104-126`).
  - "Off" means the route **is not registered at all** → daemon answers **404** (not "registered but rejecting"): `daemon/api.py:4551-4557`, `4584-4589`; router docstring `daemon/routers/mcps_gateway.py:24-30`.
  - The service object still exists (manager mounts it unconditionally) so the four `mcp_rpc_*` tools can refuse with a typed error: `daemon/manager.py:1451-1456`, `daemon/api.py:2401`, gate `daemon/services/mcps_gateway/service.py:195-214` (code `INTERNAL`, message "the remote-hands gateway is disabled").
  - Boot-time only: the service holds a boot-time config snapshot (`service.py:167`; `manager.py:1453-1454`) and the route table is composed once at boot → **activation requires a daemon restart** (`docs/mcps-gateway.md:159-166`).
- **Configurable path** — knob `MCPS_GATEWAY_PATH`; default `DEFAULT_PATH = "/api/mcps-gateway/ws"` (`config.py:52`, read `config.py:282-284`, returned `config.py:288`).
  - Normalization + `/api` stripping: `daemon/routers/mcps_gateway.py:365-379`; tail becomes the route path (`368`, `441-445`).
  - A path outside `/api` mounts at app level (`api.py:4599-4600`).
- **Upgrade-time auth** — **before `accept()`**:
  - `authorization = websocket.headers.get("authorization")` — `daemon/routers/mcps_gateway.py:293` (test-provider branch `401-411`)
  - `if not service.authenticate(authorization):` — `router:294`; implementation `daemon/services/mcps_gateway/service.py:235-261`:
    - empty token set ⇒ refuse (fail closed) `service.py:247-252`
    - `Authorization` must be exactly `Bearer <token>` (case-insensitive scheme) `service.py:255-257`
    - constant-time byte comparison over EVERY configured token `service.py:261` → `config.py:196-221` (`hmac.compare_digest`, UTF-8 bytes, no short-circuit)
  - Refusal = **`websocket.send_denial_response(Response(status_code=401, headers={"WWW-Authenticate": 'Bearer realm="ensemble-gateway"'}, content=b"unauthorized\n"))`** — `router:299-307`; body const `router:53`; realm `config.py:93`.
  - **401, never 403** (client treats 401 as fatal, 403 as transient → reconnect loop): `router:14-22`, `docs/mcps-gateway.md:299-305`.
  - **No protocol message is exchanged on a refused upgrade** (`router:7-9`).
  - `await websocket.accept()` happens only after auth — `router:309`.
  - Service not wired on `app.state` ⇒ **503** `gateway unavailable` — `router:279-291` (and `390-400` in the test branch).
- **OAuth2 must not own this path** — the WS route is tagged **module-lane** by route NAME (not path prefix, because the path is configurable): `daemon/api.py:4136-4138` (`tag_module_lane(_ws_route)`), rationale `api.py:4123-4135`. Without the tag the OAuth2 middleware would 401 the enrollment bearer (`docs/mcps-gateway.md:467-472`).
- **Phase C read surface is a DIFFERENT lane** (OAuth + `mcp:read` scope) and is registered **unconditionally**: `daemon/api.py:4546`; router `daemon/routers/mcps_gateway_read.py:81`; scope decorator `read.py:142-143`, `181-182`, `221-223`.

---

## 2. ENROLLMENT / AUTH MODEL

- **`MCPS_GATEWAY_TOKENS` = plain comma-separated list of shared secrets** — parser `_env_list` (`config.py:152-157`), read at `config.py:289`.
  - **No token→machine mapping, no JSON, no per-machine scoping**: `GatewayConfig.tokens: tuple[str, ...]` (`config.py:166`).
  - Multiple tokens allowed; the center does not care which one is presented (`docs/mcps-gateway.md:231-233`).
  - **Empty ⇒ nobody may enroll** (fail closed) — `config.py:178-181`, `service.py:247-252`.
  - **Mismatch behavior**: an unknown token ⇒ HTTP 401 at the upgrade (no session, no roster entry, no frame).
- **`MCPS_GATEWAY_ALLOWED_MACHINES` = comma-separated allowlist of `machine_id`s** — `config.py:172`, `183-194`, `295`.
  - **Empty ⇒ every authenticated machine is enrolled** (`config.py:191-194`).
  - Non-empty ⇒ exact string membership; a machine outside it is refused `MACHINE_NOT_ENROLLED` **at hello** (`service.py:499-513`).
- **Machine identity comes ONLY from the hello frame**, never from the token: `machine_id = str(params.get("machine_id") or "")` — `service.py:499`; roster upsert `service.py:561`; `_by_machine` bind `service.py:562-563`.
  - ⇒ **Any valid token can claim ANY `machine_id`** (no binding between credential and machine). The only gate is the allowlist.
  - `params.client` (`{name, version}`) is **log-only** — `service.py:565-572`.
  - Empty `machine_id` ⇒ `MACHINE_NOT_ENROLLED` + close — `service.py:499-513`.
  - The upgrade carries **no** machine identity; identity appears only after hello.
- **Token rotation** = edit env + restart daemon (`docs/mcps-gateway.md:242-247`).

---

## 3. FRAME PROTOCOL

### Envelope (§6.1)
- Builder `make_envelope` — `daemon/services/mcps_gateway/protocol.py:182-211`. Keys, in insertion order: `v` (always `"0.1"`, `protocol.py:32`, `198`), `type`, then optional `id`, `session_id`, `method`, `params`, `result`, `error`.
  - **`ts` is NEVER emitted** — the module docstring lists `ts` as part of §6.1 (`protocol.py:14-15`) but `make_envelope` has no `ts` key (`protocol.py:198-211`). Spec-doc/code divergence.
  - `id` is included only when not None (`protocol.py:199-200`); `session_id` only when truthy (`201-202`).
- Types: `request|response|error|notification` — `protocol.py:38-41`.

### hello (client → center, `type:"request"`, `method:"hello"`)
- Params read by the server: `protocol_version` (`service.py:481`), `machine_id` (`service.py:499`), `capabilities` dict (`service.py:515-516`), `client{name,version}` (`service.py:565-566`).
- **hello_ack: DOES NOT EXIST.** There is no `hello_ack` method/type — repo-wide grep for `hello_ack` returns **zero** matches. The ack is a plain **`type:"response"`** envelope with **no `method`** — `service.py:574-588`.
- hello result fields (exact): `protocol_version` ("0.1"), `server{name:"ensemble-gateway", version:"0.1.0"}` (`SERVER_NAME` `protocol.py:35`; `server_version` default `service.py:165`), `capabilities` (7-key map, `protocol.py:91-99`), `heartbeat_interval_ms`, `heartbeat_timeout_ms`, `client_dead_detect_ms` — `service.py:579-586`.
- Capability map advertised by the center: `session:true, tools:true, notifications:false, heartbeat:true, screenshot_delta:false, multi_session:false, client_cert:false` — `protocol.py:91-99`.
- **Required of the machine**: `session`, `tools`, `heartbeat` only — `protocol.py:83` (`notifications` deliberately NOT required, `protocol.py:79-82`). Missing ⇒ `CAPABILITY_MISSING` + close — `service.py:517-536`.
- **Unknown capability keys are ignored** (only the required three are checked) — `service.py:517-519`.

### Version negotiation
- hello `params.protocol_version != "0.1"` ⇒ **typed error frame** `PROTOCOL_VERSION_UNSUPPORTED`, `data:{requested, supported:["0.1"]}` — `service.py:481-497`; then socket close (`496`).
- **Inbound frame with missing/wrong `v` ⇒ NO error frame, socket closed** (mirrors the shipped client's own behavior) — `service.py:340-361`.
- Second hello on one connection ⇒ `INTERNAL` error frame + close — `service.py:466-479`.
- Unknown request method from the client ⇒ `INTERNAL` error frame (with the live `session_id` if a session is open) — `service.py:376-401`, `_live_session_id` `service.py:403-416`.
- **Close code on every server-initiated close is 1000** (normal) regardless of cause — `daemon/routers/mcps_gateway.py:189-194` (`close(code=1000, reason="")`), invoked from `service.py:316`.

### Heartbeat
- **Direction: client → center only**, `type:"notification"`, `method:"heartbeat"`, no id, no response — `service.py:418-430`; server **never** sends a heartbeat (the fake client treats one as a protocol violation: `tests/integration/mcps_gateway/fake_remote_client.py:536-539`).
- The heartbeat notification is the **ONLY** liveness refresh (traffic does not refresh) — `service.py:422-429` (`connection.last_heartbeat` + `store.heartbeat`).
- Knobs: interval 15000 ms, timeout 45000 ms, client-dead-detect 45000 ms — `config.py:55`, `59`, `63`, `167-169`; all three **advertised** in the hello result (`service.py:583-585`).
- **Omitted-pong vs dead-socket**: there is **no pong tracking**. Liveness is (a) a WebSocket **control-frame PING** every `MCPS_GATEWAY_PING_INTERVAL_MS` (default 200 ms) — `router:67`, `203-260`, cadence from `service.config.ping_interval_ms` (`router:136-150`) — and (b) the heartbeat-silence timer. Dead-socket detection = **no heartbeat notification within `heartbeat_timeout_ms`**, with **zero grace** — `service.py:703-726`, sweep interval `min(0.1 s, timeout/10)` — `service.py:125`, `190-193`.
- **What the server sends when it declares the machine dead**: (1) the session is killed silently first — `service.py:714`; (2) an **in-band error frame with `id: None`** (unsolicited, uncorrelated) `HEARTBEAT_TIMEOUT`, `data:{machine_id}` — `service.py:715-725`; (3) the socket is closed (code 1000) — `service.py:726`.
- A connection that upgrades but never says hello is reaped after **30 s** with **no frame** — `service.py:109`, `687-702`.

### session.begin (center → client, `type:"request"`)
- Params sent: `{"machine_id": <id>, "context": {"agent_id": <agent>, "reason": <str>}}` — `service.py:854-858`. No `session_id` on the envelope (the session does not exist yet).
- Catalog payload shape expected in the answer: `result.session_id` (string, required — `service.py:860-867`), `result.machine_id` (optional; if present it MUST equal the requested id, else `INTERNAL` — `service.py:868-873`), `result.tools` = list of MCP tool objects (verbatim; a non-list ⇒ `[]` — `service.py:875-876`). The client may also return `expires_at_ms` — **ignored** (the center enforces its own TTL: `sessions.py:172`).
- Catalog is stored unmodified and re-emitted to the agent (`service.py:875-903`, `sessions.py:62`); tool names are projected for the agent view `sessions.py:76`.

### tools/call (center → client, `type:"request"`)
- Envelope: `session_id` = live session id — `service.py:942-947`; params are the **MCP object unwrapped**: `{"name": <tool>, "arguments": {...}}` — `service.py:945`.
- **The daemon never invokes a child/stdio MCP server** — there is no spawn/subprocess logic anywhere in `daemon/services/mcps_gateway/`; the center forwards over the WS and the **Go wrapper** invokes its child. (Server-side grep: no child-process code in the package.)
- Timeout: `MCPS_GATEWAY_COMMAND_TIMEOUT_MS` (default 30000 ms) — `config.py:69`, `170`, `238`; applied in `_request` — `service.py:625-632` ⇒ on expiry `TOOL_TIMEOUT` with `data:{method, timeout_ms}`.
- Answer handling: response envelope `result` must be a dict, else it is wrapped `{"result": <value>}` — `service.py:454-455`. An `type:"error"` answer becomes a `GatewayProtocolError` with the peer's `code`/`message`/`data` **relayed verbatim** (any code, not filtered) — `service.py:444-452`.
- Tool errors are audited as `outcome="error"` (or `"timeout"`) — `service.py:948-960`; successes as `outcome="ok"` — `service.py:961-970`.

### session.end (center → client)
- Params `{"session_id": <id>}` + envelope `session_id` — `service.py:1032-1037`.
- The center releases the slot **only after the client acknowledges** — `service.py:1039-1040`; a client refusal propagates unchanged and **the session SURVIVES** — `service.py:1027-1031`.
- Agent-facing return: `{"session_id", "machine_id", "ended": true, "result"}` — `service.py:1041-1046`.
- Best-effort release of a machine-side session the center refuses (race S2): `session.end` with a **2000 ms** bound — `service.py:887-896`, `973-1003`.
- Semantics: the socket stays open, the machine returns to `ready` — `service.py:1005-1014`, `docs/mcps-gateway.md:421`.

---

## 4. SESSION LIFECYCLE + EXCLUSIVITY

- **`session_id` is minted by the MACHINE**, the center adopts it — `sessions.py:41-44`, `service.py:860`.
- **Lock implementation = IN-MEMORY dicts under a `threading.RLock`** (no DB row, no CAS):
  - `_sessions`, `_by_machine`, `_by_agent` + `_lock` — `daemon/services/mcps_gateway/sessions.py:89-94`
  - `assert_machine_free` (pre-flight) — `sessions.py:118-133`
  - `begin()` re-checks under the same lock (atomic slot) — `sessions.py:174-205`; machine-busy raise `178-182`; **agent-busy raise (reason `agent_already_holds_session`)** `183-202`
  - `end` (idempotent) `sessions.py:208-218`; `end_for_connection` `220-229`; `touch` `231-237`; `expire_due` `239-256`
- **Center-side exclusivity (two dimensions)**:
  - machine-busy pre-flight — `service.py:836-838`
  - agent-drives-one-remote (§8.2) — `service.py:840-852` (`MACHINE_BUSY`, `data.reason="agent_already_holds_session"`)
  - post-round-trip atomic re-check (loser of the race is refused **by the center**, never delegated to the machine) — `sessions.py:148-206`, rationale `sessions.py:148-161`
- **`MACHINE_BUSY` emission site + wire shape**: the center **never emits `MACHINE_BUSY` as a wire frame** — it is an **agent-facing tool payload** only:
  - `{"error": {"code": "MACHINE_BUSY", "message": "...", "data": {"machine_id","session_id"[,"reason","agent_id"]}}}` — builder `daemon/tools/mcp_rpc_tools.py:90-99`, mapper `102-104`; producers `sessions.py:129-133`, `178-182`, `192-202`; surfaced by `mcp_rpc_connect` `mcp_rpc_tools.py:232-233`.
  - On the wire, `MACHINE_BUSY` can only appear if the **machine** emits it, relayed verbatim by `service.py:444-452` (envelope `type:"error"`, `error.code`).
- **Session kill on abrupt disconnect**: router `finally` → `service.close_connection(connection, "the client disconnected")` — `router:332-335` ⇒
  - `sessions.end_for_connection` (session dies, id is dead — no resumption) — `service.py:298`, `sessions.py:220-229`
  - pending request futures failed with `INTERNAL` — `service.py:306-314`
  - socket closed — `service.py:315-318`
  - roster → `mark_offline` (guarded by connection_id, so a racing reconnect is not clobbered) — `service.py:319-320`, `store.py:139-150`
- **Reconnect supersedes the old socket** — old connection + its session closed immediately (no `MACHINE_BUSY` window) — `service.py:542-556`.
- **TTL / IDLE enforcement**:
  - knobs `MCPS_GATEWAY_SESSION_TTL_MS` (3600000) / `MCPS_GATEWAY_SESSION_IDLE_TIMEOUT_MS` (300000) — `config.py:72`, `75`, `173-174`
  - `expires_at = now + ttl` at session creation — `sessions.py:172`
  - **enforced by the housekeeping sweep** — `SessionManager.expire_due` `sessions.py:239-256`, driven by `service.expire_sessions` `service.py:729-736` from `_monitor_loop` `service.py:653-662` (interval `service.py:190-193`)
  - **and re-checked inline on every command** — `service.py:933-939` ⇒ ends the session, clears the store pointer, raises `SESSION_EXPIRED`
  - **Gap: expiry does NOT send `session.end` to the machine** (`service.py:729-736` only logs) — the machine keeps its own lock until its own timeout.
- **Monitor lifecycle**: started on the first connection (`ensure_monitor` `service.py:638-651`, called at `router:319`), never stopped except `close_all` (`service.py:1149-1159`) — **`close_all` has no caller in the daemon** (grep: only `manager.py:14037` closes the *MCP* service) ⇒ on daemon shutdown the gateway monitor task and sockets are not closed by the daemon (the process exit drops them).

---

## 5. TYPED ERROR TAXONOMY

- **Definitions** (all literals) — `daemon/services/mcps_gateway/protocol.py:102-116`:
  `PROTOCOL_VERSION_UNSUPPORTED`(102) `CAPABILITY_MISSING`(103) `AUTH_FAILED`(104) `MACHINE_NOT_ENROLLED`(105) `MACHINE_BUSY`(106) `SESSION_NOT_FOUND`(107) `SESSION_EXPIRED`(108) `TOOL_NOT_FOUND`(109) `TOOL_DENIED`(110) `TOOL_TIMEOUT`(111) `TOOL_ERROR`(112) `PAYLOAD_TOO_LARGE`(113) `RATE_LIMITED`(114) `HEARTBEAT_TIMEOUT`(115) `INTERNAL`(116).
- **Sets**: `CENTER_EMITTED_CODES` — `protocol.py:122-134`; `RELAYED_CODES` (machine-originated) — `protocol.py:137-145`.
- **Wire shape** — `protocol.py:172-179` (`to_error_object`), `214-231` (`make_error_envelope`):
  `{"v":"0.1","type":"error","id":<id|null>,"session_id":<optional>,"error":{"code":<str>,"message":<str, OMITTED if empty>,"data":<obj, OMITTED if empty>}}`
  - `message` is advisory and MUST NOT be parsed — `protocol.py:155-158`.
  - An error for an unknown session carries **no** `session_id` — `protocol.py:222-225`.
- **Agent-facing shape (tools)** — `daemon/tools/mcp_rpc_tools.py:90-99`: `{"error":{"code","message"[,"data"]}}`.

| Code | Where emitted | Trigger |
|---|---|---|
| `PROTOCOL_VERSION_UNSUPPORTED` | **wire** `service.py:484-495` | hello `params.protocol_version != "0.1"` (then close) |
| `CAPABILITY_MISSING` | **wire** `service.py:521-534` | machine lacks `session`/`tools`/`heartbeat` (then close); `data:{missing, required}` |
| `MACHINE_NOT_ENROLLED` | **wire** `service.py:501-511` | hello `machine_id` empty or not in `MCPS_GATEWAY_ALLOWED_MACHINES` (then close). Also **tool payload** `service.py:817-834` (unknown machine → `enrolled:false`; enrolled-but-offline → `enrolled:true, presence:"offline"`) |
| `MACHINE_BUSY` | **tool payload only** `sessions.py:129-133`, `178-182`, `192-202` | machine already carries a session / agent already drives another machine |
| `SESSION_NOT_FOUND` | **tool payload only** `service.py:922-927`, `1017-1025`; builders `sessions.py:260-271` (unknown id → `data.session_id`), `273-285` (agent has none → `data.agent_id`, **no** `data.session_id`) | command/disconnect with no live session |
| `SESSION_EXPIRED` | **tool payload only** `service.py:933-939`; builder `sessions.py:287-294` | TTL or idle timeout passed |
| `HEARTBEAT_TIMEOUT` | **wire** `service.py:715-725` (id=None) | heartbeat silence > `heartbeat_timeout_ms`; session killed, socket closed |
| `TOOL_TIMEOUT` | **tool payload only** `service.py:627-632` | center deadline (`COMMAND_TIMEOUT_MS`) on `session.begin`/`tools/call`/`session.end` |
| `INTERNAL` | **wire** `service.py:468-477` (second hello), `388-401` (unknown method), `309-313` (pending request killed by close); **tool payload** `service.py:209-214` (gateway disabled), `862-873` (session.begin without id / machine mismatch), `mcp_rpc_tools.py:176-179`, `221-224`, `260-263`, `302-305` (agent not authorized) | see left |
| `AUTH_FAILED` | **never in-band** | 401 at the upgrade only — `protocol.py:121`, `router:299-307` |
| `TOOL_DENIED` | **never emitted** (reserved v0.2+) — `protocol.py:110`, `120`; `docs/mcps-gateway.md:308-310` | — |
| `TOOL_NOT_FOUND`, `TOOL_ERROR`, `PAYLOAD_TOO_LARGE`, `RATE_LIMITED` | **relay only** (`protocol.py:137-145`) — no center emission site anywhere in `daemon/services/mcps_gateway/` | machine's own error, relayed verbatim `service.py:444-452` |

- **Flags**:
  - Codes present in the vocabulary but **never emitted by this center**: `AUTH_FAILED` (in-band), `TOOL_DENIED`, `TOOL_NOT_FOUND`, `TOOL_ERROR`, `PAYLOAD_TOO_LARGE`, `RATE_LIMITED`.
  - `MACHINE_BUSY`, `SESSION_NOT_FOUND`, `SESSION_EXPIRED`, `TOOL_TIMEOUT` are in `CENTER_EMITTED_CODES` (`protocol.py:122-134`) but reach the **agent as a tool payload**, never as a wire frame — an interop assertion that expects them **on the socket** will not see them.
  - No code is emitted under a different name than the SPEC literal (all strings match §6.5 verbatim).
  - `PAYLOAD_TOO_LARGE` is **unreachable** center-side: frame-size constants exist (`protocol.py:149-150`, 8 MiB default / 32 MiB hard) but are **never enforced** — `docs/mcps-gateway.md:534`, `589`.

---

## 6. AUDIT WRITE PATH

- **Gate** `MCPS_GATEWAY_AUDIT_ENABLED` (default **true**) — `config.py:171`, `294`; checked **first** in `_audit_command` ⇒ `false` = no row, no attempt — `service.py:1088-1089`.
- **Sink abstraction** — `AuditSink` Protocol `daemon/services/mcps_gateway/audit.py:99-107`; `AuditReader` Protocol (optional read side) `audit.py:110-126`.
  - Implementations: `NullAuditSink` (drops) `audit.py:128-132`; `MemoryAuditSink` (**tests only**) `audit.py:135-172`; **DB sink** `daemon/repositories/mcps_gateway/repository.py:37-97`.
  - **Production wiring**: `McpGatewayAuditRepository(engine=self._engine)` — `daemon/manager.py:1453-1456`; the service default when no sink is passed is `NullAuditSink` (`service.py:169`) ⇒ a service built without a sink silently drops the ledger.
  - **Test wiring**: `MemoryAuditSink` — `tests/integration/mcps_gateway/harness.py:187-193` (the protocol suite asserts against the in-memory sink, not the DB).
- **Table** `mcp_gateway_audit` — model `daemon/repositories/mcps_gateway/models.py:43-111`; migration **`daemon/migrations/versions/20261022_000001_create_mcp_gateway_audit.sql:78-99`** (SQLite `.sql`; PostgreSQL via `SQLModel.metadata.create_all`, late import at `daemon/api.py:1003`).
- **Full column list** (migration `78-90` / model `80-111`):
  `id TEXT PK` (UUID4) · `created_at TEXT NOT NULL` (ISO-8601 UTC TEXT) · `agent_id TEXT NOT NULL` · `instance_id TEXT NULL` · `machine_id TEXT NOT NULL` · `tool TEXT NOT NULL` · `args_hash TEXT NOT NULL` · `outcome TEXT NOT NULL DEFAULT 'ok'` · `session_id TEXT NULL` · `error_code TEXT NULL` · `detail TEXT NULL`.
  - Indexes `(agent_id,created_at)`, `(machine_id,created_at)`, `(tool,created_at)` — migration `92-99`.
  - **There is NO arguments column** (by design) — `models.py:8-15`, `migration:20-25`.
- **Which events are audited**: **`tools/call` ONLY** — success `service.py:962-970`, failure/timeout `service.py:948-960`. **No audit row** for hello, heartbeat, `session.begin`, `session.end`, connect/disconnect, or socket lifecycle (no `_audit_command` call sites there).
- **Field provenance on a `tools/call`**:
  - `agent_id` = the **tool caller's** agent id, captured at tool-factory time — `daemon/tools/mcp_rpc_tools.py:168-169` (→ `226-231`, `265-270`). **It is never supplied by the wire**; the center sends it TO the machine inside `session.begin.params.context.agent_id` (`service.py:857`).
  - `instance_id` = the caller's instance id, same origin — `mcp_rpc_tools.py:168`, `228-229`, `267-268`. The agent can never supply an author value (`mcp_rpc_tools.py:157-159`).
  - `machine_id`, `session_id` = from the center's **live session record** — `service.py:953-956`, `961-968`.
  - `tool` = the remote tool name as given by the agent — `service.py:956`.
  - `outcome` = `ok` / `error` / `timeout` — `audit.py:52-55`; **`OUTCOME_DENIED` is defined but never used** (no call site passes it).
  - `error_code` = the exception code, **validated** against the protocol vocabulary: unknown peer codes become `"UNRECOGNIZED_ERROR_CODE"` — `service.py:1095-1098`, allow-list `service.py:97-99`.
  - `detail` = **fixed center-authored classification** + `[CODE]`; peer free text NEVER enters the ledger — `service.py:1099-1108`, constants `service.py:87-92`.
  - `created_at` = `now_utc_iso()` — `service.py:1120` (fallback `repository.py:56`).
- **Args hashing** — `daemon/services/mcps_gateway/audit.py:58-65`:
  - algorithm **SHA-256**, output **lower-case hex digest** (`hashlib.sha256(...).hexdigest()`), **no truncation** (64 chars).
  - input = `canonical_json(arguments if arguments is not None else {})` — `canonical_json` = `json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)` — **raw args JSON, keys SORTED, tight separators, `default=str` for non-JSON types** — `daemon/services/mcps_gateway/protocol.py:246-252`.
  - The raw arguments never leave that function (`audit.py:59-62`).
- **Write mechanics** — off the event loop (`asyncio.to_thread`), **awaited** (durable before the command returns), **bounded 5 s** (`DEFAULT_AUDIT_WRITE_TIMEOUT_SECONDS` `service.py:104`, `1122-1130`); failures logged + counted (`service.audit_failures` `service.py:1131-1145`), **never abort the command**.
- **Read side** — `service.audit_tail` duck-types `list_recent` on the sink (write-only sinks ⇒ empty tail, never an error) — `service.py:758-776`; DB read is `created_at DESC` (TEXT lexicographic) + limit/offset — `repository.py:73-97`; HTTP surface `daemon/routers/mcps_gateway_read.py:221-244` (renames `created_at`→`timestamp`, `read.py:247-265`).

---

## 7. PERSISTENCE

| Artifact | Store | Survives socket disconnect | Survives daemon restart |
|---|---|---|---|
| Machine roster | **in-memory** `InMemoryMachineStore` (`store.py:115-175`) | **YES** — row kept, `connection_id=None` → `presence="offline"` (`store.py:139-150`, `54-65`) | **NO** — `store.py:12-16`; `docs/mcps-gateway.md:88-91` |
| Live sessions | **in-memory** `SessionManager` (`sessions.py:89-94`) | **NO** — killed with the connection (`sessions.py:220-229`) — no tombstone | NO |
| Audit ledger | **DB** `mcp_gateway_audit` | YES | YES |

- **What the Phase C read surface sees for a disconnected machine** — `daemon/routers/mcps_gateway_read.py:142-178`:
  the machine is **still listed** with `presence="offline"`, `online=false`, `connected=false`, `session_id=null`, `agent_id=null`, `busy=false`, `last_seen_at` = last heartbeat stamp. It disappears only on daemon restart (empty roster — `read.py:149-152`).
- **Sessions**: the read surface lists only LIVE sessions, `state` is hard-coded `"live"` (`read.py:89`, `201`) — an ended/expired session leaves **no** row (no tombstone) — `read.py:83-88`.
- **Audit** is the only durable artifact; it keeps rows for machines that are long gone.

---

## 8. REAL-CLIENT READINESS — "works with the fake client, maybe not with a real one"

- **No `hello_ack` frame type/method exists** (repo-wide grep: 0 matches) — the ack is `type:"response"`. A client expecting a distinct ack type will not match it.
- **No `ts` in any emitted envelope** (`protocol.py:182-211`) although §6.1 lists it (`protocol.py:14-15`) — a client that requires `ts` fails; the fake client never checks it.
- **Every server-initiated close uses code 1000** (`router:189-194` + `service.py:316`) — protocol violation, capability missing, machine-not-enrolled and heartbeat timeout all look like a **normal closure** to a real client that branches on close codes (1002/1008).
- **The liveness PING depends on private Starlette/uvicorn internals**: `_websocket_protocol()` walks `websocket._send.__closure__` cells looking for an object with `.ping` (`router:77-103`), then `protocol.ping()` (`router:243`). If the ASGI layout changes the evidence vanishes and only a WARNING is logged (`router:172-184`) — a §3.3-conformant client reads the inbound silence as server death and **reconnect-loops**. The fake client does **not** implement client-side death detection/reconnect (`tests/integration/mcps_gateway/COVERAGE.md:64`) ⇒ this seam is untested.
- **Production code branches on a test artifact**: `_IN_PROCESS_PEER_HOSTS = ("testclient","testserver")` (`router:74`, used `106-133`) suppresses the warning for Starlette's in-process client.
- **Response correlation is by exact string `id` only** (`service.py:438-455`); a Go client that echoes a numeric id, or re-serializes the id, never resolves the future ⇒ the request dies as `TOOL_TIMEOUT` (30 s). The fake client echoes the exact string (`fake_remote_client.py:590`).
- **The echoed `session_id` on an answer is never validated** (`service.py:438-455`) — a client that omits or mismatches it is silently accepted (divergence from §6.4 "canonical").
- **Liveness is heartbeat-only**: traffic does NOT refresh the liveness clock (`service.py:422-429`) — a real client that heartbeats slower than the advertised 15 s interval, or that stops heartbeating while busy, is killed at 45 s **with zero grace** (`service.py:680-726`).
- **Session lifecycle is not reachable over the wire alone**: `connect`/`command`/`disconnect` require an agent holding the `mcp_rpc` category **and** `agent_id ∈ {ari, leader}` (`mcp_rpc_tools.py:82-87`, `173-179`, `220-224`, `259-263`, `301-305`). The integration suite bypasses the agent layer entirely by constructing `McpGatewayService` directly and injecting `app.state.mcps_gateway_service` (`tests/integration/mcps_gateway/harness.py:186-199`) ⇒ the tested path is **not** the path a real agent drives. A cross-implementation interop test must either drive the service directly (in-process) or run an `ari`/`leader` instance.
- **The audit sink in the protocol suite is the in-memory fake** (`harness.py:187-193`); production is the **DB** sink (`manager.py:1455`) — the DB write path (synchronous `Session(engine)` + `commit`, `repository.py:67-69`) is **not** exercised by the wire suite, and the in-memory sink is what "audit works" actually proves.
- **Config is a boot-time snapshot in the service** (`service.py:167`, `manager.py:1453-1454`) even though `docs/mcps-gateway.md:164-166` claims the knobs are "re-read at call time" — flipping `AUDIT_ENABLED`, `COMMAND_TIMEOUT_MS`, `SESSION_TTL_MS`, `ALLOWED_MACHINES` at runtime does **not** change the running service (only the route registration reads env at boot, `api.py:4563`). The docs' claim is **inaccurate for the service path**.
- **TTL/idle expiry never tells the machine** (`service.py:729-736`) — the machine keeps its own lock and will answer the next `session.begin` with `MACHINE_BUSY`; the center has no compensating release path (only the S2 race path at `service.py:887-896`).
- **`session.begin` params carry a non-MCP `context` object** (`service.py:857`) — a strict client that rejects unknown params fails; the fake client ignores extras.
- **The center's own TTL overrides the machine's `expires_at_ms`** (`service.py:860-903`; `sessions.py:172`).
- **`session.end` needs an answer**: a client that ends locally without replying leaves the center's session alive until the command timeout (`service.py:1032-1040`).
- **Non-conformant frames get silence, not a typed refusal**: binary frames dropped (`router:324-328`), unparseable/unknown-type frames logged only (`service.py:331-374`), unknown `v` ⇒ close with **no error frame** (`service.py:340-361`).
- **Frame-size limits are not enforced** (`protocol.py:149-150`; `docs/mcps-gateway.md:534`).
- **`heartbeat.params.machine_id` is never checked** (`service.py:418-430`) — any heartbeat on the connection refreshes whatever machine the connection claimed at hello.
- **Center-initiated closes / no graceful shutdown**: `McpGatewayService.close_all()` (`service.py:1149-1159`) has **no caller** in the daemon (grep: `manager.py:14037` closes the MCP service only) ⇒ a daemon restart drops the sockets without a close frame; the client must treat that as a transport failure and reconnect.
- **Path expectation for the reference client**: the shipped Go client appends its own `/remote-hands/v1` when the configured URL has **no** path — the configured center URL must include the full `/api/mcps-gateway/ws` (`docs/mcps-gateway.md:249-264`); the client also refuses non-TLS schemes (rewrites `http(s)://` → `wss://`).
- **Reference-client shape assumptions baked into the server**: hello params carry `protocol_version`, `machine_id`, `capabilities`, `client{name,version}`; the answer must carry `result.session_id` (string), optional `result.machine_id` (must equal the requested id if present) and `result.tools` as a list of MCP tool objects (`service.py:481-513`, `860-876`). A client that names the version field differently (e.g. `version`) gets `PROTOCOL_VERSION_UNSUPPORTED`.

---

## Appendix — file map (server side)

| File | Lines | Owns |
|---|---|---|
| `daemon/routers/mcps_gateway.py` | 455 | WS transport adapter: upgrade auth, read loop, teardown, liveness ping |
| `daemon/routers/mcps_gateway_read.py` | 276 | Phase C OAuth read surface (`/api/mcps-gateway/{machines,sessions,audit}`) |
| `daemon/routers/mcps_gateway_schemas.py` | 167 | FE-facing response models |
| `daemon/services/mcps_gateway/config.py` | 322 | knobs + kill switch |
| `daemon/services/mcps_gateway/protocol.py` | 297 | wire vocabulary, codes, envelope builder |
| `daemon/services/mcps_gateway/service.py` | 1167 | the center (hello, heartbeat, sessions, forwarding, audit) |
| `daemon/services/mcps_gateway/sessions.py` | 297 | session manager + §8.3 lock |
| `daemon/services/mcps_gateway/store.py` | 191 | machine roster (in-memory) |
| `daemon/services/mcps_gateway/audit.py` | 186 | audit record, sinks, args hashing |
| `daemon/repositories/mcps_gateway/{models,repository}.py` | 114 / 100 | `mcp_gateway_audit` table + sink |
| `daemon/migrations/versions/20261022_000001_create_mcp_gateway_audit.sql` | 106 | SQLite DDL |
| `daemon/tools/mcp_rpc_tools.py` | 341 | the four agent tools |
| `daemon/manager.py` | 1451-1456, 13840-13857 | service construction + property |
| `daemon/api.py` | 4563-4600, 4136-4138, 2401, 1003 | route registration, module-lane tag, service wiring, model import |
| `docs/mcps-gateway.md` | 868 | operator runbook (as-built) |
