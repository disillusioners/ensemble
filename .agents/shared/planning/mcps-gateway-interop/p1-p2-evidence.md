# Phase-D1 / P1 + P2 — evidence log (the REAL pair)

> **VERDICT: PASS — P1 (15 assertion entries over the 14 gated numbers)
> and P2 (13 entries over the 8 gated numbers).**
>
> Branch `feature/mcps-gateway-interop` (HEAD `d9868bac3` at the push; the
> lane's own commits are listed in §9). The peer is the **shipped Go client**
> (`mcps_client`, built from the READ-ONLY repository into `/tmp`), the
> centre is the **shipped Python gateway** (the real `create_app()`, the real
> lifespan, uvicorn-native TLS on an ephemeral port, the real PostgreSQL
> fence database `ensemble_dev_test_only`). `daemon/` source was **NOT**
> modified — the centre-side frame transcript is a tap installed by the
> harness.
>
> Every assertion is asserted **from the SPEC** (`mcps_client/SPEC.md`),
> never from observed behaviour. Where the SPEC is silent or ambiguous the
> observation is **recorded, not failed** (§7).

---

## 0. Approver iteration-002 watch-list (verbatim, carried into P3-P5)

> 1. **(P4/D3c — record only) Install-site:** where AD6's literal wording (helper inside MemoryAuditSink.list_recent) and the operative D3c text disagree, the ROUTER-ONLY reading is correct — a helper installed inside list_recent would KeyError pin #12. Implement router-only when you get there (P4, not now).
> 2. **(P5/kit — record only) Frame-size row is a client-side MUST** (transport.go:63-90) — do NOT build an unsatisfiable center-side red for it; the kit's frame-size check targets what the CLIENT enforces.
> 3. **(P3/D3c — record only) Unset-stamp acceptance test:** insert the DB row DIRECTLY via raw SQL through the engine, bypassing record()'s now_utc_iso() substitution at repository.py:56 — otherwise the unset-stamp case silently gets a stamp and proves nothing.
> 4. **(ACT NOW — affects test_real_pair.py architecture) The "only tls_harness.py imports daemon.*" rule is factually stale:** the committed p0_gate.py also imports daemon.* directly (:303-304, :658). Corrected rule: the sanctioned DIRECT daemon-importers for the interop lane are exactly the three committed P0 files — tls_harness.py, p0_gate.py, go_client.py. Every D1-NEW file must stay daemon-import-free — meaning no direct `import daemon.*` statements in transcript.py or test_real_pair.py; access daemon objects (app, service seam, engine) ONLY through tls_harness's API (its helpers expose the booted app/service handles), or by shelling out to a sanctioned pattern. The KIT (P5) imports nothing daemon at all. Do NOT edit plan.md to reword the boundary sentence — approver owns the plan; carry the corrected reading in your evidence file instead.
> 5. **(ACT NOW — assertion posture) DRY_RUN/DESTRUCTIVE are no-op-pending-knob:** they gate NOTHING today. R-23's real protection = job_queue==0 assertion + PID-scoped lsof + machine_id-scoped DB assertions. Keep SETTING the env vars but never cite them as effective gates, and never weaken the explicit checks in their favor (P0's fix already labels them no-ops — keep that honest labeling in any new harness code).
> 6. **(ACT NOW — polish, cheap) Evidence consistency:** (a) p0-gate-evidence.md §0 says the run log shows 8 PASS + 4 FAIL — label the 4 FAILs explicitly as the A1/A2 harness-defect attempts (psycopg2-DSN, event-loop-blocking), not pair failures; (b) use "extend" not "add" for the §8.4 wording wherever you touch it; (c) the heartbeat pin cite is `protocol.go:266-278`; (d) the gateway subsystem's second unit-test home is a conscious choice — stay consistent with it.

**How this phase answered items 4-6:**

* **4 (ACT NOW)** — `transcript.py` and `test_real_pair.py` contain **no** `import daemon.*`
  statement (verified by source grep). Every daemon object (the app, the
  service seam, the engine, the audit sink, the typed error, the Plane kill
  switches, the bearer mint) is reached through `tls_harness`'s API:
  `tls_harness.issue_read_bearer()`, `tls_harness.gateway_error_code()`,
  `tls_harness.gateway_error_data()`, `tls_harness.plane_switches_effective()`.
  The three new helpers live in `tls_harness.py` — one of the three
  sanctioned direct importers. `plan.md` was **not** edited.
* **5 (ACT NOW)** — `DRY_RUN=1` / `DESTRUCTIVE` unset are still **set** by the
  harness (they are the harness's existing code), but they are **never cited
  as an effective gate** anywhere in the new lane: the R-23 protection that
  is actually asserted is the `job_queue_items` count (0, delta-based), the
  **PID-scoped** `lsof` listener scan, and the **`machine_id`-scoped** DB
  assertions. The two Plane switches are read back through the daemon's own
  readers (their effect, not their environment).
* **6a (ACT NOW)** — `p0-gate-evidence.md` now labels the four FAIL attempts
  explicitly (A1 the psycopg2 DSN, A1b the un-emptied ledger + A2, A2 the
  blocking event-loop probe, F1 the fence-role boot failure) and its header
  count is corrected to the nine green runs the log actually contains.
* **6b** — the §8.4 wording in this file uses **"extend"** ("the centre
  **extends** the protocol with the `session_id` column"), never "add".
* **6c** — the heartbeat pin is cited as `internal/remote/protocol.go:266-278`.
* **6d** — no second unit-test home was created for the gateway subsystem:
  the interop lane lives in `tests/integration/mcps_gateway/` (the existing
  home) and touches no unit-test tree at all.
* **1, 2, 3** — recorded only; they bind P3/P4/P5 and are **not** acted on
  here. (Item 2 is honoured in the negative: §7 records that no centre-side
  red is built for the frame-size row.)

---

## 1. The compressed timing profile — the EXACT values used

**The centre** (the environment of the booted real app; every value is
asserted back on the booted application):

| Knob | Value |
|---|---|
| `MCPS_GATEWAY_HEARTBEAT_INTERVAL_MS` | **500** |
| `MCPS_GATEWAY_HEARTBEAT_TIMEOUT_MS` | **1500** |
| `MCPS_GATEWAY_CLIENT_DEAD_DETECT_MS` | **1500** |
| `MCPS_GATEWAY_COMMAND_TIMEOUT_MS` | **1200** |
| `MCPS_GATEWAY_SESSION_TTL_MS` | **60000** |
| `MCPS_GATEWAY_SESSION_IDLE_TIMEOUT_MS` | **30000** |
| `MCPS_GATEWAY_PING_INTERVAL_MS` | **200** |
| `MCPS_GATEWAY_ENABLED` / `TOKENS` / `PATH` / `AUDIT_ENABLED` | `1` / one 32-byte hex / `/api/mcps-gateway/ws` / `1` |
| `MCPS_GATEWAY_ALLOWED_MACHINES` | `mach_interop,mach_probe` (the probe needs a machine of its own) |

**The client** (the YAML's `remote:` block — these are the client's
**fallbacks**; the client *adopts* the centre's advertised values at
runtime):

| Knob | Value | Why |
|---|---|---|
| `heartbeat_interval_ms` | **5000** | The client's config validator REFUSES a value outside 5000-60000 (SPEC §3.3's range, `internal/config/config.go`), so the compressed 500 cannot be written here. The client **adopts** the centre's 500 at runtime and says so in its log (assertion #5). |
| `heartbeat_timeout_ms` | **45000** | ≥ 3 × the configured interval. |
| `client_dead_detect_ms` | **45000** | ≥ 3 × the configured interval. |
| `reconnect_initial_backoff_ms` | **250** | The first backoff after a dropped socket (SPEC §3.3's floor). |
| `reconnect_max_backoff_ms` | **2000** | The ceiling of the exponential backoff. |
| `tool_call_timeout_ms` | **5000** | **Above** the centre's 1200 ms on purpose: the centre's arm must win the timeout race (assertion #26). |

**The kill window and the tolerance band.** The kill granularity is the
monitor's sweep interval, `min(interval, timeout/10)` = `min(default, 1500/10)`
= **150 ms**. The band asserted is `[timeout, timeout × 2]` = **[1500 ms,
3000 ms]**, and the measurement is the centre's **own**: the unique log line
`"the machine %r was silent for %d ms; its session is killed"` — observed
**1512 / 1516 / 1520 / 1567 ms** across the attempts (the sweep fires on the
150 ms grid, so the silence lands just past the advertised tolerance).

**The measured heartbeat cadence** (centre-side, from the transcript):
`11 frames, gaps = [0.401, 0.401, 0.403, 0.403, 0.402, 0.402, 0.403, 0.402,
0.999, 0.5] s` — the ~401 ms gap is the client's 500 ms ticker (the client
sends slightly early); the 999 ms gap is the deliberate SIGSTOP of the
timeout arm.

---

## 2. The exact commands

```bash
# STEP 0 — the push (before any new work)
git push -u origin feature/mcps-gateway-interop
git ls-remote --heads origin feature/mcps-gateway-interop
#   → d9868bac3131302e770c4fd269600c7c555952d5  refs/heads/feature/mcps-gateway-interop

# the gate itself (its OWN process — the env is patched BEFORE daemon.api
# is imported, which is what the isolation requires)
.venv/bin/python -m tests.integration.mcps_gateway.test_real_pair --phase p1 \
    --work-dir /tmp/mcps-d1/real-pair --postgres-user "$(whoami)"
.venv/bin/python -m tests.integration.mcps_gateway.test_real_pair --phase p2 \
    --work-dir /tmp/mcps-d1/real-pair --postgres-user "$(whoami)"

# the scoped verification (the tasking's commands)
timeout 300 uv run pytest tests/integration/mcps_gateway/test_real_pair.py -m integration -q
timeout 240 uv run pytest tests/integration/mcps_gateway/test_mcps_gateway_protocol.py \
    tests/integration/mcps_gateway/test_fake_client_selftest.py -q

# the fake-client regression, actually RUN (the default addopts deselect it)
timeout 240 uv run pytest tests/integration/mcps_gateway/test_mcps_gateway_protocol.py \
    tests/integration/mcps_gateway/test_fake_client_selftest.py -m integration -q
```

**Results:** the gate `2 passed in 23.68s` and again `2 passed in 22.37s`
(stability: the same set of failures — none — twice); the fake-client
regression `34 passed in 26.76s`; the deselect proof
`no tests collected (37 deselected)` in the default lane.

---

## 3. The per-assertion ledger

### P1 — the happy path (assertions 1-6, 9-13, 20-22)

| # | Assertion | Verdict | Evidence |
|---|---|---|---|
| 1 | Upgrade with a **bad bearer** ⇒ 401 + the challenge, **no protocol frame**, fatal for the client | **PASS** | `status=401 challenge='Bearer realm="ensemble-gateway"' protocol_frames_during_the_refusal=0`; the client logged *"the remote hop stopped"* with **0** "reconnecting to the center" lines |
| 2 | Upgrade with the good bearer ⇒ 101 | **PASS** | `status=101 close_code=1000` |
| 3 | The `hello` answer echoes the `id` verbatim | **PASS** | `hello_id='req-0001' ack_id='req-0001'` |
| 4 | The negotiated version is `0.1` | **PASS** | `protocol_version='0.1'` |
| 5 | The three heartbeat parameters are advertised **and adopted** by the client | **PASS** | advertised `{500, 1500, 1500}`; the client logged *"the center advertised heartbeat parameters outside the permitted range of SPEC.md 3.3; they are adopted as they are"* with `heartbeat_interval_ms=500`, `client_dead_detect_ms=1500` |
| 6 | The server capability map is exactly the v0.1 map | **PASS** | `{"client_cert":false,"heartbeat":true,"multi_session":false,"notifications":false,"screenshot_delta":false,"session":true,"tools":true}` |
| 9 | `session.begin` ⇒ a non-empty `session_id`, the requested `machine_id`, a list of tools | **PASS** | `session_id='sess_0f0776605bb70bd8' tools=2`; the wire answer (frame 018 of the P2 transcript) carries `session_id`, `machine_id`, `expires_at_ms`, `tools[]` |
| 10 | The centre's catalogue is **semantically** equal to the child's own catalogue | **PASS** | `centre_tools=['echo_tool','secret_tool'] child_tools=['echo_tool','secret_tool']` — the child's catalogue was read back from the child's own record file, independently of the centre |
| 11 | `tools/call` returns the MCP result as-is | **PASS** | `result_keys=['_meta','content','resultType'] content=[{"type":"text","text":"the secret tool ran"}]` (the `isError` member is absent — see §7) |
| 12 | `tools/call` relays the peer's typed error **verbatim** | **PASS** | `code='TOOL_ERROR'`, message `'mcpbridge: call echo_tool: calling "tools/call": invalid argument: {"password":"S3CRET-VALUE-4711"}'` — the child's own text, with its arguments |
| 13 | `session.end` is acknowledged, the slot is released, the socket stays open | **PASS** | `ended=True heartbeats_after_the_end=3 roster_session_id=None` — three heartbeats still arrived **after** the end |
| 20 | The roster read carries every field the FE model names | **PASS** | `status=200 rows=1` and the row carries `machine_id, presence, online, connected, last_seen_at, session_id, agent_id, busy` |
| 21 | The session read carries every field and an **array** of tools | **PASS** | `status=200 rows=1 tools=['echo_tool','secret_tool']` |
| 22 | The audit read renames `created_at` to `timestamp` and counts the **page** | **PASS** | `status=200 count=1 page=1` for `?limit=1&offset=0`, `limit`/`offset`/`gateway_enabled` echoed, and the row carries `timestamp, agent_id, instance_id, machine_id, tool, outcome, session_id, error_code, args_hash` |

### P2 — the negative / diagnostic paths (assertions 7, 8, 14, 15, 16, 24, 25, 26)

| # | Assertion | Verdict | Evidence |
|---|---|---|---|
| 7 | A `hello` **without** `tools` ⇒ `CAPABILITY_MISSING` + close; a `hello` **with** `notifications:false` is **accepted** | **PASS** (×2) | the refusal: `{"code":"CAPABILITY_MISSING","message":"the machine does not support the capabilities ['tools']","data":{"missing":["tools"],"required":["session","tools","heartbeat"]}}` + `close_code=1000`; the acceptance: a full `hello_ack` with the v0.1 capability map |
| 8 | An unsupported version ⇒ `PROTOCOL_VERSION_UNSUPPORTED` + `data{requested,supported}` + close, **no silent downgrade** | **PASS** | `{"code":"PROTOCOL_VERSION_UNSUPPORTED","data":{"requested":"0.9","supported":["0.1"]}} close_code=1000 silent_downgrade=False` |
| 14 | A second `connect` for the same machine ⇒ `MACHINE_BUSY`, refused **before** any frame reaches the machine | **PASS** (×2) | `code='MACHINE_BUSY'`; the transcript carries **exactly ONE** `session.begin` frame across both attempts (`before=1 after=1`) |
| 15 | The centre demonstrably sends the liveness pings; the client survives the idle window | **PASS** (×2) | `pings_sent=5` (the private path `service._connections[cid].transport.pings_sent`, reached deliberately and documented); the client logged **no** *"the center went silent"* |
| 16 | The heartbeat kill — all seven parts | **PASS** (×4) | see §5 |
| 24 | Every server-initiated close is 1000 | **OBSERVED** | `close codes observed: client=[] probes=[1000, 1000]` — the SPEC names **no** close code, so the assertion is pinned behind `CONFORMANCE_STRICT_CLOSE_CODE` (default: warn) and the **gap is flagged** (§7) |
| 25 | The centre never issues a `tools/list` | **PASS** | `methods sent by the centre: ['session.begin']` |
| 26 | The **centre's** deadline wins the timeout race | **PASS** | `code='TOOL_TIMEOUT' data={"method":"tools/call","timeout_ms":1200} elapsed=1.207s` (the client's own timeout is 5000 ms) |

**Not gated here (P3's alone):** assertions 17-19 (the audit-DB proof) are
**observed** in P1 and never failed by this lane.

---

## 4. The transcript (centre-side, the tap)

The centre's frames, in order, from the P2 run (the P1 run is the same shape
without the probe frames). `send` = centre → machine, `recv` = machine →
centre.

```
000 recv {"v": "0.1", "type": "request", "id": "probe-cap-1", "method": "hello", "params": {..., "capabilities": {"session": true, "tools": false, ...}}}
001 send {"v": "0.1", "type": "error", "id": "probe-cap-1", "error": {"code": "CAPABILITY_MISSING", "message": "the machine does not support the capabilities ['tools']", "data": {"missing": ["tools"], "required": ["session", "tools", "heartbeat"]}}}
002 recv {"v": "0.1", "type": "request", "id": "probe-cap-2", "method": "hello", "params": {..., "capabilities": {"session": true, "tools": true, "notifications": false, ...}}}
003 send {"v": "0.1", "type": "response", "id": "probe-cap-2", "result": {"protocol_version": "0.1", "server": {"name": "ensemble-gateway", "version": "0.1.0"}, "capabilities": {..., "notifications": false, ...}, "heartbeat_interval_ms": 500, "heartbeat_timeout_ms": 1500, "client_dead_detect_ms": 1500}}
004 recv {"v": "0.1", "type": "notification", "method": "heartbeat", "params": {}}      ← the probe heartbeats (a conformant machine)
013 recv {"v": "0.1", "type": "request", "id": "probe-ver-1", "method": "hello", "params": {"protocol_version": "0.9", ...}}
014 send {"v": "0.1", "type": "error", "id": "probe-ver-1", "error": {"code": "PROTOCOL_VERSION_UNSUPPORTED", "message": "protocol version 0.9 is not supported by this center", "data": {"requested": "0.9", "supported": ["0.1"]}}}
015 recv {"v":"0.1","type":"request","id":"req-0001","method":"hello","params":{"protocol_version":"0.1","client":{"name":"mcps-client","version":"0.1.0"},"machine_id":"mach_interop","capabilities":{...}}}     ← the SHIPPED client: compact JSON
016 send {"v": "0.1", "type": "response", "id": "req-0001", "result": {"protocol_version": "0.1", ...}}
017 send {"v": "0.1", "type": "request", "id": "ctr-0001", "method": "session.begin", "params": {"machine_id": "mach_interop", "context": {"agent_id": "ari", "reason": "p2-gate"}}}
018 recv {"v":"0.1","type":"response","id":"ctr-0001","session_id":"sess_1b26fbef5c95717b","result":{"session_id":"sess_1b26fbef5c95717b","machine_id":"mach_interop","expires_at_ms":1792686212662,"tools":[{"name":"echo_tool",...,"inputSchema":{"properties":{"password":{"type":"string"}},"type":"object"}},{"name":"secret_tool",...}]}}
021 send {"v": "0.1", "type": "request", "id": "ctr-0002", "session_id": "sess_1b26fbef5c95717b", "method": "tools/call", "params": {"name": "secret_tool", "arguments": {"command": "uptime", "timeout_ms": 5000}}}
022 send {"v": "0.1", "type": "error", "error": {"code": "HEARTBEAT_TIMEOUT", "message": "the machine stopped heartbeating; the session is dead", "data": {"machine_id": "mach_interop"}}}      ← NO "id": the SPEC-vs-code gap
023 recv {"v":"0.1","type":"notification","session_id":"sess_1b26fbef5c95717b","method":"heartbeat","params":{}}
024 recv {"v":"0.1","type":"request","id":"req-0002","method":"hello",...}               ← the client RECONNECTS (fresh upgrade + hello)
025 send {"v": "0.1", "type": "response", "id": "req-0002", "result": {...}}
026 send {"v": "0.1", "type": "request", "id": "ctr-0003", "method": "session.begin", "params": {"machine_id": "mach_interop", "context": {"agent_id": "ari", "reason": "p2-rebegin"}}}
027 recv {"v":"0.1","type":"response","id":"ctr-0003","session_id":"sess_e28e8a8d33de2621","result":{...}}
```

**The direction matters** and is worth stating: the centre is the
**requestor** of `session.begin` / `session.end` / `tools/call`, so their
answers travel machine → centre and are recorded as `recv` frames. The only
answer the centre *originates* is the `hello` acknowledgement (a `send`
frame).

---

## 5. Assertion 16 — the heartbeat kill, part by part

**The mechanism (named, as the plan requires):** the client **subprocess is
SIGSTOPped** (so it stops heartbeating) and SIGCONTed afterwards. The
alternative the plan allows — a harness frame-filter dropping outbound
heartbeats — was NOT used; the raw probes' own heartbeats (a conformant
machine's behaviour) are what the filter would have emulated.

| Part | Requirement | Observation |
|---|---|---|
| (a) | exactly **ONE** `type="error"` frame with `error.code == "HEARTBEAT_TIMEOUT"` and **NO `id`**, sent before the close | `error_frames=1 (before the kill: 0) for mach_interop: 1 id=None` — the frame is transcript frame 022 above; it carries `data.machine_id` and **no** `id` |
| (b) | the unique log line `"the machine %r was silent for %d ms; its session is killed"` | `silence=1520 ms` (the band is [1500, 3000]) — the centre's own measurement |
| (c) | `service.sessions.get(old_id) is None` **AND** the roster no longer points at it | `sessions.get(old)=None roster.session_id=None roster.connected=False` |
| (d) | an **explicit** re-begin opens a **NEW** session id (the centre does **not** auto-reopen) | `old='sess_69206d7441bda362' new='sess_533ee2ec0edcadc8' reconnected=True` — the re-begin was the harness's own `svc.connect` call, after the client reconnected on its own (transcript frames 024-027) |
| (e) | the tolerance band `[timeout, timeout × 2]` | 1512 / 1516 / 1520 / 1567 ms across the attempts — inside [1500, 3000] |
| — | the client's own reaction | the client **drops** the uncorrelated error (it has no pending request for it) and never surfaces `HEARTBEAT_TIMEOUT` to the operator — see §7 |

---

## 6. The mismatch ledger of this phase (harness defects found and fixed)

Every one of these was a **harness** defect (this lane's own code), found
and fixed during the run. **None** was a daemon defect and none was a pair
failure; they are listed because the plan's reporting format asks for the
mismatch ledger per attempt.

| Attempt | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 (P1) | `AttributeError: 'Frame' object has no attribute 'result'` | TWO frame models existed: `tls_harness.Frame` (the P0 one) and `transcript.Frame` (the new one). The assertions read the new accessors off the old class. | The frame model was **unified**: `tls_harness` now re-exports `transcript.Frame`/`transcript.Transcript` (one frame model in the lane). The P0 gate still passes (§8). |
| 2 (P1) | `no-orphan-stub-child — pids=[69057]` | The phase raised before it returned the client handle, so the runner's teardown could not stop the client (and its stub child). | The client handle travels back through a `ClientBox`; the teardown always stops it, whatever the phase does. |
| 3 (P1) | `#9/#10/#11/#13` FAIL with `tools=2` but an empty catalogue | The assertions looked for the machine's answer in the **send** frames. The centre is the *requestor*, so the answers are **recv** frames. | `_response_to()` takes the direction; the `hello` acknowledgement stays a `send` frame. |
| 4 (P1) | `#11` FAIL on `isError is False` | The shipped client **omits** `isError` when it is false (the MCP default). The SPEC's §6.6 example shows it explicitly. | The assertion now requires `isError is not True` (the result is the MCP result as-is) and the omission is **recorded** (§7) — the plan's design item 15. |
| 5 (P2) | `#8` FAIL with `silent_downgrade=True` | A leaked variable: the #8 probe reused the #7b probe's "accepted" list. | Each probe keeps its own answer list. |
| 6 (P2) | `#16(a)` FAIL with `error_frames=2` | The raw probe (a conformant machine would heartbeat, the probe did not) was killed for silence and put a **second** `HEARTBEAT_TIMEOUT` frame into the transcript. | The probes now **heartbeat** (every 400 ms) while they wait — the behaviour of a conformant machine. The assertion also scopes the frames to `data.machine_id == mach_interop`. |
| 7 (P2) | `#16(c)` FAIL: `roster.session_id` still pointed at the dead session | **A REAL ORDERING FINDING in the centre**, not a harness artefact — see §7.1. The roster release is the **last** statement of the connection teardown, i.e. it happens **after** the awaited socket close; on a wedged peer (the SIGSTOPped client) the close handshake cannot complete, so the roster keeps the dead session pointer for the whole duration. | The kill sequence was corrected (SIGCONT **before** waiting for the roster's release) and the finding is reported (§7.1). The assertion itself was NOT weakened: it waits for the end state (bounded) and asserts it. |
| 8 (P2) | `#16(d)` FAIL: `MACHINE_NOT_ENROLLED … offline` | The "did the client reconnect?" test counted **all** `hello` frames — including the raw probes' own hellos — so it was answered before the client had reconnected. | The count is now scoped to the client's own machine id. |
| 9 (wrapper) | `NameError: name 'subprocess' is not defined` | The wrapper's `subprocess` import was lost in a rewrite of the file. | The import is back. |

---

## 7. The NEW wire-level findings (the discovery channel)

### 7.1 A centre-side ordering finding — the roster's §8.3 pointer is released AFTER the awaited socket close

**Precisely:** `McpGatewayService.close_connection` pops the connection and
releases the **session manager**'s slot first, but the **roster** release
(`_store.mark_offline`, which clears `connection_id` / `session_id` /
`agent_id`) is the **last** statement of the teardown — **after**
`await connection.transport.close()`. The WebSocket close handshake is an
**await**: on a wedged peer it does not complete, and the read surface keeps
reporting the dead session as `busy: true` for the whole duration.

Observed: with the client SIGSTOPped, the session manager had already
released the slot (`sessions.get(old_id) is None`) while the roster still
carried `session_id='sess_d04d43fd55d71a75'` and `connected: true`; the
roster's release only happened after the client was SIGCONTed and the close
handshake completed.

**Impact:** the §8.3 lock itself is never held twice (the session manager
releases first), so this is **not** a double-session hazard. It is a stale
read surface: `/api/mcps-gateway/machines` reports `busy: true` with a dead
session while the close handshake is pending. **Severity: low; it is a
read-surface staleness, not a protocol violation** — the SPEC names no
ordering for the roster's release. Reported, not fixed (the fix is
centre-side and belongs to a later phase).

### 7.2 SPEC-vs-code gaps (reported, never failed)

| # | Gap | Evidence | Status |
|---|---|---|---|
| G1 | **The `HEARTBEAT_TIMEOUT` error frame carries NO `id`**, while SPEC §6.1 says the `id` is **required** on request/response/error. | transcript frame 022; the violation recorder: `missing_id x1` — *"a error frame carries no 'id'"* | SPEC-vs-code gap. The centre's own behaviour is deliberate (the error has no request to correlate), but the SPEC's envelope table says the member is required. **This is the client's blind spot too**: the shipped client DROPS an uncorrelated error (it has no pending request for it), so the code is never surfaced to the operator. |
| G2 | **The catalogue is re-encoded**, while SPEC §10's kit clause says *"byte-identically"*. The observed mechanisms: **whitespace compaction** (the client answers compact JSON, the centre emits spaced JSON), **HTML-escaping**, **`omitempty`** (the `isError` member disappears when false), **unknown-member drop**, and — new, precise — **the keys of the `inputSchema` OBJECT are sorted** by the client's marshaller (the child reported `type` then `properties`; the centre received `properties` then `type`). The tool object itself is a struct (field order preserved). | the centre's catalogue vs the child's own record file (§3, #10) | SPEC-vs-implementation tension, already anticipated by the plan ("the client re-encodes catalogs ⇒ assert SEMANTIC fidelity and document the tension"). The **semantic** equality holds. |
| G3 | The `tools/call` answer carries `isError` **absent** when false, plus two members of the client's own (`_meta`, `resultType`); SPEC §6.6's example shows `"isError": false` explicitly. | `result_keys=['_meta','content','resultType']` | The centre relays the result **as-is** (which is what §6.6 asks for); the deviation is the peer's encoding. Reported. |
| G4 | The SPEC names **no close code** anywhere, yet every server-initiated close is **1000**. | `probes=[1000, 1000]`, `client=[1000]` | Gap flagged; the assertion is pinned behind `CONFORMANCE_STRICT_CLOSE_CODE` (default: warn). |
| G5 | The centre's `tools/call` timeout is enforced by the **centre** (§9.3 places the per-command deadline on the client), and the centre's arm wins because its deadline is the shorter one. | `data={"method":"tools/call","timeout_ms":1200}` | Not a gap — the SPEC explicitly allows a centre-side bound ("the centre's own bound on how long it waits for the machine"), and §9.3's client-side deadline is a separate, longer one. The determinism requirement (#26) is met. |

### 7.3 Observations recorded, not gated

| Observation | Why it is not gated |
|---|---|
| **S1 — the non-object tool result is unreachable with the shipped peer.** The shipped client answers a `tools/call` with the MCP result **object** (always — the MCP result shape is an object), so the centre's re-wrap branch (`a non-object result becomes {"result": …}`) cannot be triggered by it. The `internal/adversarial` child exposes exactly two tools (`secret_tool`, `echo_tool`) and has **no** env/mode switch for a non-object tool (read-only inspection). | The plan's sanctioned fallback (a tiny stdio MCP server we own) would not help: the Go SDK parses the child's answer into a typed `CallToolResult`, so a non-object result cannot survive the hop either. The observation is therefore **structural**, and it is a **latent centre-side defect** that the real pair cannot reach. The plan's own wording ("recorded as an observation, never asserted green") is honoured. |
| **Assertions 17-19** (the audit-DB proof: the exact-value correlation, the independently recomputed SHA-256, the three expected-divergence markers) | **P3 is their sole gate** (Rev-2). P1 observed them: the success row's `args_hash` recomputes to `8de14a6f7d7e368cb09e8e62fb2e340a6f59821655b832c9dbc134fe14db1449` (the independently computed SHA-256 of `{"command":"uptime","timeout_ms":5000}`), and **0** rows carry the raw failing argument. The three divergence markers were **not** exercised here (they belong to P3). |
| The roster release ordering (§7.1) | A centre-side finding, not an assertion of the SPEC. |
| The `job_queue_items` count, the Plane kill switches, the audit-failure counter | Lane hygiene (gates), not SPEC assertions — and the `DRY_RUN`/`DESTRUCTIVE` variables are explicitly **not** cited as effective gates (watch-list item 5). |

---

## 8. The teardown proof

Every run ends with the same proof (the gates of the report):

| Proof | P1 | P2 |
|---|---|---|
| the uvicorn server task **COMPLETED** (not "timed out") | `server-task-completed — the uvicorn server task finished cleanly` | idem |
| the shutdown budget | ≥ 30 s (`SHUTDOWN_BUDGET_SECONDS = 30.0`); the observed shutdown took **0.213 s** (P1) / **0.126 s** (P2) | idem |
| **no listener left** (the PID-scoped `lsof -p <pid> -iTCP -sTCP:LISTEN` scan, against the baseline captured **before** the bind) | `no-listener-left — remaining=[] baseline=[]` | idem |
| the gateway's own port is closed | `gateway-port-closed — port=60746 listening=False` | idem |
| the client process is gone | `client-process-stopped — exit=0` | idem |
| the client's **process group** is gone (the stub child lives in it) | `no-orphan-client-process — pgid=87411 pid=87411` | idem |
| the stub child is gone | `no-orphan-stub-child — pids=[]` | idem |
| `job_queue_items` stayed 0 (delta against the boot baseline) | `count=0 baseline=0` | idem |
| the Plane kill switches are **effective** (read back through the daemon's own readers) | `sync_enabled=False reason='disabled by kill-switch (PLANE_SYNC_ENABLED=false)' mcp_switch_off=True mcp_available=False` | idem |
| no audit write failure, `audit_failures == 0` | PASS | PASS |

**The fence:** the engine is
`postgresql+psycopg://nguyenminhkha:***@127.0.0.1:5432/ensemble_dev_test_only`
— the **fence** database only; `ensemble_prod` is never referenced by any
DSN this lane writes, and every DB assertion is scoped to
`machine_id == "mach_interop"`.

**The regression gates:** the P0 gate still passes after the `tls_harness`
change (`P0 GATE VERDICT: PASS (attempts: 1)`), the 34-test fake-client
suite is green (`34 passed in 26.76s`), and the default lane deselects the
new package (`37 deselected`).

---

## 9. The files and the commits

**Files created**

| File | What |
|---|---|
| `tests/integration/mcps_gateway/transcript.py` | The frame transcript + the envelope violation recorder — **daemon-free** (pure stdlib). Records, never raises: missing/wrong `v`, missing `id`, uncorrelatable ids, session-id mismatch. |
| `tests/integration/mcps_gateway/test_real_pair.py` | The assertions **and** the runner (the file is both: `python -m … --phase p1` runs the gate in its own process; the pytest wrapper shells out to it). **Daemon-free.** |
| `.agents/shared/planning/mcps-gateway-interop/p1-p2-evidence.md` | This file. |

**Files changed**

| File | What |
|---|---|
| `tests/integration/mcps_gateway/tls_harness.py` | (a) the frame model is now `transcript.Frame`/`transcript.Transcript` (one model in the lane); (b) **three** new sanctioned daemon-facing helpers — `issue_read_bearer()` (the AUTH-ON read lane), `gateway_error_code()` / `gateway_error_data()` (the typed refusal), `plane_switches_effective()` (the R-23 kill-switch proof). The P0 behaviour is unchanged (the P0 gate still passes). |
| `tests/integration/mcps_gateway/go_client.py` | `write_client_config()` gained **optional** keyword knobs for the timing profile; the defaults reproduce the previous file **byte for byte** (the P0 gate is unaffected). |
| `tests/integration/mcps_gateway/harness.py` | **Only** the export of the env-knob table (`GatewayEnv`) and the teardown helpers (`_EnvPatch`, `port_is_listening`); not one line of the existing behaviour changed — the 34-test suite is green. |
| `tests/integration/mcps_gateway/COVERAGE.md` | **Extended** with the interop lane's §-by-§ coverage and the independence attestation (what was read, what was NOT read, every implementation fact unavoidably learned). |
| `.agents/shared/planning/mcps-gateway-interop/p0-gate-evidence.md` | The four FAIL attempts labelled (watch-list 6a) and the header count corrected. |

**The commits** (logical, conventional; only the files created/touched by
this phase were added — the pre-existing untracked `.agents/` files and
`.agents/approver/**` were left alone):

1. `test(mcps-gateway): add the daemon-free frame transcript and the violation recorder`
2. `test(mcps-gateway): add the real-pair interop gate (P1 happy path, P2 negative paths)`
3. `test(mcps-gateway): extend the interop harness for the P1/P2 lane`
4. `docs(mcps-gateway): extend the SPEC coverage declaration with the interop lane`
5. `docs(mcps-gateway): Phase-D1 P1+P2 evidence log — the real pair against the real gateway`

---

## 10. The open questions (carried, not decided)

1. **The centre's roster release ordering (§7.1)** — should the §8.3
   pointer be released **before** the awaited socket close? The centre-side
   fix is one line (move the `mark_offline` call above the `await`), but it
   touches the connection teardown of the shipped daemon — a later phase's
   call, not this lane's.
2. **The `HEARTBEAT_TIMEOUT` frame's missing `id` (G1)** — a SPEC-level
   question for the `mcps_client` owner: either the SPEC's §6.1 envelope
   table gets an exception for **unsolicited** errors, or the centre starts
   carrying an id on them (and the client starts surfacing uncorrelated
   errors instead of dropping them). The plan's §8.4 canonicalisation
   amendment is still an open user decision.
3. **The close code (G4)** — the SPEC names none; the kit pins 1000 and
   flags the gap. The `CONFORMANCE_STRICT_CLOSE_CODE` knob is the P5
   kit's business.
