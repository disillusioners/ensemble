# Phase-D1 / P3 — evidence log (audit DB proof + read parity)

> **VERDICT: PASS — 33 assertion entries over the 4 gated numbers
> (17: 10 entries, 18: 2, 19: 3 markers, 23: 18), 36 lane-hygiene
> gates, zero mismatches.**
>
> Branch `feature/mcps-gateway-interop` (HEAD `c1479a137` at the start;
> the lane's own commits are listed in §9). The peer is the **shipped Go
> client** (`mcps_client`, built from the READ-ONLY repository into
> `/tmp`), the centre is the **shipped Python gateway** (the real
> `create_app()`, the real lifespan, uvicorn-native TLS on an ephemeral
> port, the real PostgreSQL fence database `ensemble_dev_test_only`).
> `daemon/` source was **NOT modified**.
>
> P3 is the **SOLE GATE** for assertions 17-19 (Rev-2: P1 observes, P3
> gates) and owns the BE↔FE read-parity proof (#23) on the **same live
> daemon** with **AUTH ON** (B12). No D2/D3/kit/docs work — per B10.

---

## 0. Approver watch-list items that bind P3 (from the P1+P2 evidence §0)

* **Item 3 (P3/D3c — record only)** — the unset-stamp acceptance test
  must insert the DB row DIRECTLY via raw SQL through the engine,
  bypassing `record()`'s `now_utc_iso()` substitution at
  `repository.py:56`. **Recorded for P4** (it is D3c's acceptance test,
  not P3's): P3's ledger reads confirmed the substitution — every
  flow-written row carried a non-empty `created_at` (the §8.4 presence
  gate would have caught a bare stamp).
* **Item 4 (import discipline, honored)** — **no NEW file was added.**
  The P3 phase extends the committed `test_real_pair.py` (the
  mission-sanctioned "extend it" branch) and the sanctioned importer
  `tls_harness.py` gains two helpers (`independent_fence_engine`,
  `record_audit_canary`). Re-derived by grep at P3 time: the direct
  `daemon.*` importers are exactly `harness.py` (pre-existing),
  `p0_gate.py` (pre-existing), `tls_harness.py` (pre-existing, extended).
  `go_client.py` imports **no** daemon module (grep: 0) — the
  iteration-002 list that named it was off by one; the grep is
  authoritative. `test_real_pair.py` and `transcript.py` stay at
  **zero** direct `daemon.*` imports (grep: 0, after the P3 edits).
* **Item 5 (DRY_RUN/DESTRUCTIVE no-ops)** — unchanged posture: still
  SET by the harness, never cited as gates; the effective R-23
  protections asserted are `job_queue_items == 0` (baseline-equal), the
  PID-scoped `lsof` scan, and the `machine_id`-scoped DB assertions.

---

## 1. The timing profile + the audit lane (the EXACT values used)

The **centre** profile is the P1/P2 compressed profile, unchanged:
`MCPS_GATEWAY_HEARTBEAT_INTERVAL_MS=500`,
`MCPS_GATEWAY_HEARTBEAT_TIMEOUT_MS=1500`,
`MCPS_GATEWAY_CLIENT_DEAD_DETECT_MS=1500`,
`MCPS_GATEWAY_COMMAND_TIMEOUT_MS=1200`,
`MCPS_GATEWAY_SESSION_TTL_MS=60000`,
`MCPS_GATEWAY_SESSION_IDLE_TIMEOUT_MS=30000`,
`MCPS_GATEWAY_PING_INTERVAL_MS=200`.
The **client** fallbacks are the P1/P2 set (`tool_call_timeout_ms=5000`
— the centre's 1200 ms arm wins the race; backoff 250/2000 ms).

**NEW in P3 — the client's audit lane is ON.** The P3 client config
sets `audit.enabled: true` (`go_client.write_client_config(audit_enabled=True)`;
the default `False` keeps the P1/P2 YAML byte-identical — verified by
the still-green P1/P2 gates). The shipped client then writes
`client-audit.jsonl` with one `audit.Record` per forwarded call,
carrying its OWN `args_hash = audit.HashArgs(args)`
(`internal/remote/client.go:1244-1255`, `internal/audit/audit.go`).
**Consequence for assertion 19: the Go-side hashes are ACTUAL (read
from the client's own records), not derived.** The documented
derivation from `audit.go` was still transcribed
(`go_canonical_hash_derived`) and is cross-checked against the actual
records gate-by-gate — all three derivation-faithful gates PASS, which
pins the transcription (and would have caught a wrong fallback had the
jsonl lane not been reachable).

The **timeout row** was cheap exactly as the tasking predicted: the
1200 ms compressed deadline + SIGSTOP/SIGCONT (the P2 #26 mechanism)
drove a `TOOL_TIMEOUT` row in ~1.3 s. The session **survived** the
request timeout (a timeout kills the REQUEST, not the session — gated),
and the client's late `tools/call` answer (frame 016 in the first run's
transcript) was tolerated by the centre without an envelope violation.

---

## 2. The exact commands

```bash
# the P3 gate (its OWN pytest process; the phase runs in a subprocess
# whose env is patched BEFORE daemon.api is imported)
timeout 330 uv run pytest \
  tests/integration/mcps_gateway/test_real_pair.py -m integration -q -k p3
# → 1 passed in 10.46s   (verdict: PASS, 0 FAIL, 0 mismatches)

# the same gate, runner form (the wrapper shells out to exactly this)
FENCE_ROLE=$(uv run python -c "…tls_harness.resolve_fence_role()")   # → nguyenminhkha
timeout 330 uv run python -m tests.integration.mcps_gateway.test_real_pair \
  --phase p3 --work-dir /tmp/mcps-d1/real-pair --postgres-user "$FENCE_ROLE"
# → exit 0, "=== Phase D1 P3 GATE — PASS ==="

# the P1/P2 regression (the dispatch, #16(c) hardening and the
# EVIDENCE_PATH removal all touch their file)
timeout 600 uv run pytest \
  tests/integration/mcps_gateway/test_real_pair.py -m integration -q -k "p1 or p2"
# → 2 passed in 23.81s

# the fake-client regression (expect 34)
timeout 240 uv run pytest \
  tests/integration/mcps_gateway/test_mcps_gateway_protocol.py \
  tests/integration/mcps_gateway/test_fake_client_selftest.py -m integration -q
# → 34 passed in 26.73s

# the P0 gate regression (tls_harness.py is shared — touched by the two
# new helpers)
timeout 300 uv run pytest tests/integration/mcps_gateway/test_p0_gate.py \
  -m integration -q
# → 1 passed in 12.34s

# the deselect proof (the default addopts must deselect the P3 test)
timeout 120 uv run pytest tests/integration/mcps_gateway/test_real_pair.py -q
# → 3 deselected in 0.16s
```

**Stability**: the P3 gate ran **4 times green** (2 pytest-form, 2
runner-form) with an identical PASS set each time; the last runner-form
run is the canonical one quoted below (session
`sess_8e84cf19bd54bef7`, gateway port 51901, `go_build 0.428s`,
`boot 4.465s`, `server_shutdown 0.195s`; whole gate ≈ 10.5 s).

---

## 3. The per-assertion ledger

### 17 — the audit rows in the REAL DB (10 entries, all PASS)

The full canary set (B11 extended), asserted BEFORE the client boots:

| Entry | Verdict | Detail |
|---|---|---|
| the-audit-sink-writes-through-the-manager-engine | PASS | `service._audit._engine is app.state.manager.engine` — identical=True, sink=McpGatewayAuditRepository |
| the-audit-sink-is-enabled | PASS | `config.audit_enabled is True` (identity, not truthiness) |
| the-canary-row-round-trips-to-a-second-independent-engine | PASS | a row written through `service._audit` (machine `mach_canary_p3`, ASCII args `{"probe":"p3-canary"}`) read back through a **freshly constructed engine** on the same fence DSN: rows=1, `args_hash` stored == recomputed (`adb23a3a…f1209`) |

The flow's rows (read through a SECOND independent engine, scoped
`machine_id="mach_interop"`, counted by DELTA — the pre-flow 0 is the
runner's own gate, not an assumption):

| Entry | Verdict | Detail |
|---|---|---|
| the-ledger-grew-by-exactly-the-six-commands-of-the-flow | PASS | delta=6 (ok + error + 3 markers + timeout) |
| the-ok-row-correlates-by-EXACT-VALUE | PASS | `session_id=='sess_8e84cf19bd54bef7' AND tool=='secret_tool' AND outcome=='ok' AND args_hash=='8de14a6f…1449'` (machine/agent also exact) |
| the-error-row-correlates-by-EXACT-VALUE-and-carries-an-error-code | PASS | `tool=='echo_tool' AND outcome=='error' AND error_code=='TOOL_ERROR'` (== the typed client code observed on the raise) `AND args_hash=='1cee6ce1…5984'` |
| the-timeout-row-correlates-by-EXACT-VALUE | PASS | `outcome=='timeout' AND error_code=='TOOL_TIMEOUT' AND args_hash==<the same recomputed COMMAND_ARGS hash>` |
| every-row-carries-the-8.4-field-set | PASS | `timestamp(=created_at)/session_id/machine_id/agent_id/tool/args_hash/outcome` non-empty on all 6 rows; `error_code` non-null on the 4 error/timeout rows |
| the-outcomes-stay-within-the-8.4-vocabulary | PASS | observed `{ok, error, timeout}` ⊆ `{ok,error,denied,timeout}` |
| no-column-carries-the-raw-arguments | PASS | the probe secret `S3CRET-VALUE-4711` and the full canonical `COMMAND_ARGS` JSON appear in NO column of any row |

**Outcome coverage**: an `ok` row (secret_tool), an `error` row
(echo_tool — `error_code='TOOL_ERROR'`), AND the `timeout` row (the
piggybacked #26 mechanism) — three of the four §8.4 outcomes in one
ledger; `denied` remains kit territory (it needs the policy lane).

**Plan column-list reconciliation (observation, not a gate)**: the
observed DB columns are exactly `['agent_id','args_hash','created_at',
'detail','error_code','id','instance_id','machine_id','outcome',
'session_id','tool']` — the plan #17's full storage list (which adds
`instance_id`/`detail`/`created_at`/`id` beyond §8.4's wire set) is
**fully present**; `instance_id` is NULL (the flow passes none) and
`detail` carries the centre's FIXED classification (`"the remote
command completed [TOOL_ERROR]"`-shaped — never peer free text), so no
divergence to report.

### 18 — the independently recomputed SHA-256 (2 entries, all PASS)

| Entry | Verdict | Detail |
|---|---|---|
| the-args-hash-matches-a-second-non-python-implementation | PASS | payload `{"command":"uptime","timeout_ms":5000}` → Python `8de14a6f…1449` == `shasum -a 256` (stdin) == the stored row |
| the-error-rows-hash-also-matches-the-recomputation | PASS | payload `{"password":"S3CRET-VALUE-4711"}` → Python == shasum == stored (`1cee6ce1…5984`) |

The recomputation is done in the test file (`canonical_args_hash`,
stdlib-only) and cross-checked with **shasum** — a second
implementation, not a second Python call.

### 19 — the three EXPECTED-DIVERGENCE markers (3 entries + 3 gates, all PASS)

Each marker is an INDEPENDENT assertion comparing the Python-canonical
hash against the **Go-ACTUAL** hash (from the client's own audit
record for that call), each labelled *"if this fails, Go was aligned —
update the SPEC note and drop the marker"*:

| Marker | Python hash | Go hash (ACTUAL) | Differ |
|---|---|---|---|
| (i) UTF-8 escaping — `{"text":"héllo"}` | `d50b6f6a…15ce` | `87d1eca4…34c` | YES |
| (ii) HTML escaping — `{"text":"<b>&"}` | `a1568aaf…dc0c` | `c7164d1e…582` | YES |
| (iii) number canonicalisation — `{"n":1.0}` | `3b6b06ec…081` | `2bfd14f4…bd` | YES |

The three `go-derivation-faithful-*` gates PASS: the transcription of
`audit.go`'s `canonical()` (sorted keys + raw UTF-8 + HTML-escape +
shortest-roundtrip floats) reproduces the actual Go hashes byte for
byte on all three markers — the derivation and the jsonl agree.

**Go-side hash equality is NEVER asserted** (only inequality, per the
contract). SPEC §8.4 (`SPEC.md:799-804`) still names no algorithm —
the JCS/sorted-keys amendment remains a D5 pointer recommendation, and
the two audit heads remain **non-joinable** for non-ASCII / `<>&` /
float args.

### 23 — BE↔FE field parity on the same live daemon, AUTH ON (18 entries, all PASS)

| Entry | Verdict | Detail |
|---|---|---|
| **the-401-body-is-FLAT** (positive control, BEFORE any happy path) | PASS | no token + `Accept: application/json` ⇒ 401 `{"error":"AUTH_REQUIRED","message":"Authentication required"}` — exactly the `middleware/auth.py:596-608` shape |
| **no-token-with-accept-wildcard-is-a-302-to-the-login-page-not-401** (the Accept gotcha) | PASS | no token + `Accept: */*` ⇒ **302**, `Location: /api/auth/login?return_to=/api/mcps-gateway/audit` — `/api/mcps-gateway/` is NOT in `_401_HEURISTIC_PREFIXES` (`middleware/auth.py:84-91`). **D3a consumes this**: any FE fetch that lets the browser default `Accept` through sees a redirect, not a 401 |
| **the-403-body-is-NESTED-under-detail-with-SCOPE_INSUFFICIENT** | PASS | a VALID minted token with scopes=`("profile",)` ⇒ 403, top-level keys exactly `{detail}`, `detail={"error":"SCOPE_INSUFFICIENT","message":"Token lacks required scope","required":["mcp:read"]}` (`decorators.py:108-114`). **Both auth-shape pins landed** — no D3a/P4 carry-over needed |
| the-live-machine-row-is-busy-and-names-the-session | PASS | during the session: `busy=True`, `session_id=<live>`, `agent_id='ari'`, `online=True`, `connected=True` |
| the-live-session-row-is-live-locked-and-carries-the-REAL-catalog | PASS | `state='live'`, `machine_locked=True`, `tools=['echo_tool','secret_tool']` == the child's own recorded catalog (sorted) |
| the-busy-flag-transitions-true-then-false-across-begin-end | PASS | pre-connect `False` → live `True` → post-end `False`, `session_id` back to `null` |
| the-session-disappears-from-the-session-read-after-the-end | PASS | 0 matching sessions after `session.end` |
| the-machines-wrapper-is-exactly-machines-count-gateway_enabled | PASS | key set EXACT; count == page length |
| the-sessions-wrapper-is-exactly-sessions-count-gateway_enabled | PASS | key set EXACT; count == page length |
| the-audit-wrapper-is-exactly-audit-count-limit-offset-gateway_enabled | PASS | key set EXACT; count == page length |
| **the-audit-count-is-the-PAGE-LENGTH-not-the-ledger-total** | PASS | `?limit=1` ⇒ `count=1`, page=1 while the default page held 7 rows — the FE footgun (`mcps-gateway.model.ts`: "The length of THIS PAGE — NOT the ledger total") is honoured by the BE |
| the-audit-default-page-is-limit-50-offset-0 | PASS | no query params ⇒ `limit=50, offset=0` echoed |
| the-audit-echoes-the-applied-limit-and-offset | PASS | `?limit=1&offset=1` ⇒ echoed `1/1` |
| **the-audit-pagination-bounds-answer-422** | PASS | `limit=0` ⇒ 422, `limit=201` ⇒ 422, `offset=-1` ⇒ 422 (FastAPI validation detail shape) |
| **machines-and-sessions-take-no-query-params** | PASS | `?limit=1&offset=1` on BOTH ⇒ 200, identical wrapper shapes — the endpoints declare no query params, so extras are silently IGNORED (no 422, no behaviour change) |
| the-17-18-audit-rows-are-visible-in-the-audit-read | PASS | the exact-value ok row (hash-matched) is on the wire page |
| **be-keys-are-a-superset-of-the-fe-model-fields-on-all-three-shapes** | PASS | machine row ⊇ 9 FE fields, session row ⊇ 11, audit row ⊇ 9 (`frontend/src/app/models/mcps-gateway.model.ts:42-122`) |
| **the-array-keys-are-always-present-so-the-fe-defaults-are-never-load-bearing** | PASS | `machines`/`sessions`/`audit` were LISTS in every response of the run (9 bodies checked) — `res.machines ?? []`-style defaults (`mcps-gateway.service.ts:232,246,267-268`) never fire |

The **Phase C "no BE↔FE parity link" gap is now closed**: every field
the FE models name is delivered by the BE on the same live daemon, the
wrapper shapes are exact, and the FE's defensive defaults are proven
dead code in practice.

---

## 4. The transcript — the markers ON THE WIRE (canonical run)

The divergence of assertion 19 is directly visible in the centre-side
transcript (the client's error message quotes its OWN re-encoding of
the args):

```
008 send {"v": "0.1", "type": "request", "id": "ctr-0004", "session_id": "sess_8e84cf19bd54bef7", "method": "tools/call", "params": {"name": "echo_tool", "arguments": {"text": "h\u00e9llo"}}}
009 recv {"v":"0.1","type":"error","id":"ctr-0004",…,"error":{"code":"TOOL_ERROR","message":"mcpbridge: call echo_tool: calling \"tools/call\": invalid argument: {\"text\":\"h\\u00e9llo\"}",…}}
010 send {"v": "0.1", … "id": "ctr-0005", … "params": {"name": "echo_tool", "arguments": {"text": "<b>&"}}}
011 recv {"v":"0.1","type":"error","id":"ctr-0005",…,"error":{"code":"TOOL_ERROR","message":"… invalid argument: {\"text\":\"\u003cb\u003e\u0026\"}",…}}
012 send {"v": "0.1", … "id": "ctr-0006", … "params": {"name": "echo_tool", "arguments": {"n": 1.0}}}
013 recv {"v":"0.1","type":"error","id":"ctr-0006",…,"error":{"code":"TOOL_ERROR","message":"… invalid argument: {\"n\":1.0}",…}}
```

Read: the CENTRE sends `ensure_ascii` Python JSON (`h\u00e9llo` — the
client's log re-renders it); the CLIENT's own free text shows the HTML
escape (`\u003cb\u003e\u0026`) for the `<b>&` marker — the two
encodings meet on the same wire, and their hashes diverge exactly as
the markers pin.

The timeout call: `ctr-0007` (secret_tool under SIGSTOP) has NO
response frame before the heartbeats resume — the centre's 1200 ms arm
fired, the row landed with `outcome='timeout'`,
`error_code='TOOL_TIMEOUT'`, and the late client answer arrived after
`ctr-0008` (session.end) was already acked; the session survived and
closed cleanly (`ended=True`).

---

## 5. The mismatch ledger of this phase (the lane's own defects)

Exactly ONE lane defect was found and fixed mid-phase (attempt 1 of 3
budget — not exhausted):

* **A1 — the client's audit jsonl is APPEND-ONLY across runs, and the
  first marker correlation was order-global.** The first green run
  showed `go_records=18, echo_error_records=12` — three sessions' worth
  (the earlier P3 attempts had appended to the same
  `client-audit.jsonl`), and the marker rows were picked as
  `go_echo_error_rows[1:4]` — GLOBALLY, i.e. the PREVIOUS run's marker
  rows. The assertion still held (the markers compare hash FUNCTIONS —
  same args ⇒ same Go hash in every run) but the correlation claim
  ("this flow's rows") was wrong. **Fix**: correlate by THIS run's
  `session_id` (the client stamps the remote-hands session on every
  record) and gate the session-scoped count exactly
  (`this_session=6, echo_error_records=4`). The canonical run shows the
  fixed shape. The append-only behaviour itself is recorded as an
  operator observation for the kit (P5), not a defect.

No OTHER mismatch: every assertion passed first-try in every run.

---

## 6. The NEW findings of the read surface

1. **FE-assumption vs real-shape divergence: NONE.** All three row
   shapes are supersets of the FE models; the wrapper key sets are
   EXACTLY what the FE models declare; `count` is the page length as
   documented. The Phase C gap closes clean — no FE fix required by
   the backend's actual behaviour.
2. **The Accept gotcha (recorded for D3a/P4):** an unauthenticated
   `Accept: */*` GET on `/api/mcps-gateway/audit` answers **302 →
   `/api/auth/login?return_to=<path>`**, not 401 — the gateway prefix
   is not in `_401_HEURISTIC_PREFIXES`. Machine clients MUST send
   `Accept: application/json` explicitly. Verified escape hatch
   (`_looks_like_browser`, `middleware/auth.py:658-668`): an Accept
   that CONTAINS `application/json` (Angular HttpClient's default
   `application/json, text/plain, */*`) or `text/html` is NOT a
   browser — only a bare `*/*` or a MISSING Accept triggers the 302
   (curl's default `Accept: */*` DOES). D3a's 401-relabel must not
   mistake the 302 lane for a missing-401 defect.
3. **Both auth-shape pins landed live**: 401 FLAT vs 403 NESTED under
   `detail` (with `required` preserved) — D3a's `rawCode`-first branch
   has its two reference bodies pinned from the real daemon, not from
   unit mocks.
4. **The client's audit jsonl is append-only across runs** (never
   truncated, no rotation) — an operator note for the kit; the session
   id on every record is the isolation key.
5. **`/machines` and `/sessions` silently IGNORE unknown query params**
   (FastAPI default) — pinned so a future param addition is a
   conscious shape change, not drift.

---

## 7. The rulings recorded (they bind later phases)

* **G1 — HEARTBEAT_TIMEOUT carries no id vs SPEC §6.1: OPEN-SPEC.**
  The centre's error frame has NO `id` (pinned by P2 #16a) while
  §6.1 requires one on every error; the shipped client DROPS
  uncorrelated errors, so the machine can never see why it was killed.
  **Owner: the mcps_client SPEC owner**; options: amend §6.1 for
  unsolicited errors, or carry an id. The conformance kit (P5) pins
  the CURRENT behaviour (error frame, no id) with a marker comment
  pointing at G1. The daemon was NOT altered.
* **Roster-release ordering defect: CONFIRMED for P4.** The centre
  releases the roster's §8.3 pointer AFTER the awaited socket close
  (P2 §7.1). P4 owns the one-line fix (release the roster pointer
  BEFORE the awaited close); P2 #16c's honest failure is the regression
  pin; the defect mechanics are cited in the P4 commit message. **P3
  records the disposition only** (and hardened #16c — see §10).

---

## 8. The teardown proof (canonical run)

```
[PASS] server-task-completed — the uvicorn server task finished cleanly (0.195s)
[PASS] no-orphan-stub-child — pids=[]
[PASS] client-process-stopped — exit=0
[PASS] no-orphan-client-process — pgid=93328 dead
[PASS] no-listener-left — remaining=[] baseline=[]   (PID-scoped lsof)
[PASS] gateway-port-closed — port=51901 listening=False
lsof listeners after the run: (none)
```

Plus the R-23 hygiene, all green: `job_queue_items == 0` (== baseline),
both Plane kill-switches effective (read through the daemon's own
readers), no `"audit row could not be written"` log line,
`audit_failures == 0`.

---

## 9. The files and the commits

* `tests/integration/mcps_gateway/test_real_pair.py` — the P3 phase
  (`_phase_p3`, 33 assertion entries), the dispatch + `--phase p3`, the
  `test_p3_audit_db_proof_and_read_parity` wrapper, the Go-canonical
  derivation + `shasum_sha256`, the marker/session constants, the
  #16(c) roster-presence hardening, and the dead `EVIDENCE_PATH`
  removal.
* `tests/integration/mcps_gateway/tls_harness.py` —
  `independent_fence_engine` (the SECOND engine) and
  `record_audit_canary` (the daemon-typed canary writer), both behind
  the sanctioned importer.
* `tests/integration/mcps_gateway/go_client.py` — the
  `audit_enabled` YAML knob (default off; P1/P2 byte-identical) and
  `read_client_audit_log`.
* `.agents/shared/planning/mcps-gateway-interop/p3-evidence.md` — this
  file.
* `.agents/shared/planning/mcps-gateway-interop/p0-gate-evidence.md` —
  the P0 gate's own append-on-run block from the regression re-run
  (the designed behaviour of `p0_gate.write_evidence`; +91 lines, one
  PASS block, disclosed here).

Commits: see `git log` on the branch (two logical commits: the gate,
then the evidence).

---

## 10. The optional lane-hygiene dispositions

* **#16(c) roster-row PRESENCE check — DONE.** The assertion now
  requires the roster row to EXIST (`bool(roster_row)`) AND its
  `session_id` cleared — a vanished row can no longer pass vacuously.
  Re-proven green by the P2 regression run.
* **Dead `EVIDENCE_PATH` in test_real_pair.py — REMOVED** (it pointed
  at `p1-p2-evidence.md` and had zero users — the P1/P2 evidence was
  hand-curated). Disclosed: the P3 gate does NOT append its own
  evidence blocks anywhere; this file is the P3 evidence, hand-curated
  the same way.

---

## 11. Open questions (carried, not decided)

1. The §8.4 canonicalisation SPEC amendment (JCS/RFC 8785, or
   sorted-keys + `ensure_ascii=false` + shortest-roundtrip) — still a
   D5 pointer-text recommendation; P3's actual-hash evidence now gives
   it three concrete divergence pairs to cite.
2. The `denied` outcome row — not drivable in this lane (needs the
   client's policy/deny lane); kit territory (P5).
3. Whether the FE should send an explicit `Accept: application/json`
   on the gateway reads (defensive against the §6.2 gotcha) — D3a's
   call, not P3's.
