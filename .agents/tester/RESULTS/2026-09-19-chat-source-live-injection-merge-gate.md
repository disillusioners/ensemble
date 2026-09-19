# Merge Gate: chat-source-live-injection — VERDICT: **PASSED**

**Date:** 2026-09-19
**Branch:** `feature/chat-source-live-injection` @ `903856891a0d487513a270a197b503a3d6fbba32` (FROZEN base; every run rev-parse-pinned at execution time) + gate-owned test-extension commit `5b54fde56c47a0d5402b34f6523c1a820ccc10ff` (test-only, +620/−0, 2 files, add-only, scope independently verified)
**Base:** `latest` @ `306468f8` (merge-base verified)
**Delta:** 3 production files / 290+/4− (`daemon/sources/registry.py`, `daemon/constants.py`, `daemon/manager.py`) + 4 test files
**Method:** 8 workers (recon, read-only audit, 4 scoped runs, flake A/B adjudication, close-out verification). All runs `uv run python -m pytest` only, dual-layer timeouts, `-m "integration or not integration"` addopts override, report-only (zero production changes, zero daemon/ edits).

---

## Verdict

**✅ PASSED — CLEARED FOR MERGE** (from branch ref including `5b54fde5`, or merge `90385689` and apply the test commit — giter's choice; the extension commit sits directly atop `90385689`).

### Scope Decision
> Full census NOT warranted: change touches 3 production files (+290/−4) in ONE routing gate (`_handle_message` chat-injection branch + allowlist constants + manager seam), no architecture impact, surrounded by the chat regression pack. Scoped runs: feature files, adjacent 4-file unit scope, chat pack, companion e2e, concurrency Core pack. Prior full census (~21k tests) ran 2026-09-19 on chat-lane-followups; nothing in this delta reaches beyond the chat ingest path. Release Gate NOT triggered (not big/critical/architecture).

---

## 1. User-scenario proof (section 1 of the task)

**Original suite @ 90385689: producer-side PROVEN, consumer-side HOLES → all important holes CLOSED by gate extension `5b54fde5`.**

| Element | Original verdict | Now (post-extension) |
|---|---|---|
| (a) Realistic adapter-shaped metadata | **SOLID** — e2e fixtures cross-checked against REAL adapter mint sites (slack `adapter.py:811-830`, telegram `telegram.py:558-580`, discord `adapter.py:1024-1099`); AST defense-in-depth pins (`TestAllowlistSeededFromAdapterMintSites`, e2e:507-673) assert adapter literal keys ⊆ per-provider allowlist, catching drift both directions | unchanged |
| (b) `_pending_injections` populated | Producer-side SOLID (direct real-dict inspection, e2e:322-340); **consumer-side HOLE** | **CLOSED** — new `test_injection_drain_contract_round_trips_through_fifo` (e2e:672-792): real `manager.get_injection()` peek non-destructive, `manager.clear_injection()` pop destructive, content/source/echo_id round-trip — the exact contract `daemon/graph.py:204-227` consumes |
| (c) Typing fired exactly once | SOLID slack (e2e:368-369) + telegram (e2e:404-409) `assert_awaited_once` + correct chat_id; discord hasattr-guard pinned at unit only | unchanged (discord e2e skip-pin deliberately skipped — harness re-registration conflict; unit pin unit:1108-1161 covers the guard) |
| (d) ZERO MessageQueue/Task rows | SOLID for slack (real SQL, e2e:349-365); telegram/discord not queried; durable-side docstring over-promised (Task/JobItem unasserted) | **CLOSED** — telegram (e2e:479-505) + discord (e2e:511-535) row-absence added; durable test extended with Task row (+`message_id` cross-table invariant) AND JobItem row (e2e:617-668) |
| (e) Harness reality | **SOLID** — real InstanceManager, real file-backed SQLite (NullPool+WAL), real `asyncio.create_task` graph task on the same loop; only setup seams patched (MigrationRunner, engine factory, graph builder); adapter MagicMock supplies externals only; `get_instance_info`/`has_live_graph_task`/`set_injection`/`_pending_injections`/`enqueue_message_job` all REAL | unchanged |
| (f) Multi-message continuity ("keeps sending") | **HOLE** — all e2e single-message; unit 2-message test runs on MagicMock manager | **CLOSED** — new `test_three_consecutive_messages_grow_fifo_in_order` (e2e:794-1029): 3 consecutive slack messages → FIFO depth 3, send-order preserved, 3 distinct echo_ids, chat provenance on every entry, still zero MessageQueue/Task rows |

**Final statement:** the user's scenario is now proven by the suite END-TO-END at both seams: realistic per-provider envelopes (verified against real adapter mint code) → real manager routing gate → real `_pending_injections` FIFO (multi-message, ordered, distinct echo_ids) → real drain contract (the API `agent_node` consumes) → zero durable-lane rows → typing exactly-once with correct chat_id.

## 2. Mock-quality audit (unit file) — CLEAN

- **All 3 declared `inspect.signature` pins are REAL, not vacuous**: `TestFacadeSignaturePin` (unit:166-227) imports the REAL `InstanceManager` (unit:64) and inspects `set_injection` / `has_live_graph_task` / `get_instance_info` — asserts real param lists AND not-coroutine. No pin runs inspect on a mock or a string.
- **All mocks sync/async-correct vs real callables** (verified against production defs): `set_injection` (sync, `manager.py:2734`), `has_live_graph_task` (sync, `:3802`), `get_instance_info` (sync), `enqueue_message_job` (AsyncMock vs real async 6+3 kwargs, `:6893`), `InstanceMapper.get_or_create_instance` (AsyncMock vs real async, `mapper.py:276`). No wrong param names, no return-shape mismatches.
- **Minor residuals (documented, not blocking):** no `spec=` anywhere (test-side kwarg typos would pass vacuously; mitigated by kwargs-by-key assertions); no signature pin for `enqueue_message_job` (known-deferred class).
- **Facade-forwarding risk:** `set_injection` is NOT a facade (direct `_pending_injections` append — no forwarding seam). `enqueue_message_job` IS a forwarding facade; unit mocks cannot catch future non-forwarding kwargs, BUT this delta adds 0 new kwargs (registry call site uses only pre-existing kwargs — verified) and `test_chat_source_enqueue_wake_e2e.py` exercises the REAL facade chain. Residual risk theoretical for this PR; the extension's JobItem assertion now also wires the full 3-layer facade chain (manager → messaging_service → job_queue_service) in e2e.

## 3. Edge-case pin matrix — 8/8 PINNED (leader's list)

| # | Edge case | Status | Where |
|---|---|---|---|
| 1 | RUNNING + graphless → durable (DEFECT-A) | ✅ unit | unit:419-462 (enqueue awaited; set_injection not called; graph probe called once) |
| 2 | PAUSED/IDLE/terminal → durable | ✅ unit (6 statuses parametrized) | unit:481-510 |
| 3 | Unknown source_type + empty metadata → durable | ✅ unit (short-circuits BEFORE graph probe) | unit:664-711 + no-adapter sister unit:714-751 |
| 4 | Image-bearing → durable even RUNNING+live | ✅ unit | unit:532-577 (images thread through) |
| 5 | Unknown metadata key on known provider → durable | ✅ unit + e2e | unit:580-623 + e2e:443-498 (now incl. Task+JobItem) |
| 6 | Empty/whitespace content → durable | ✅ unit (3 variants parametrized) | unit:1381-1407 |
| 7 | Typing exactly-once; durable path unchanged | ✅ unit (4 cases) + e2e (slack+telegram awaited-once, chat_id pinned) | unit:1027-1204, e2e:368-369/404-409 |
| 8 | No double-delivery (inject never mints rows) | ✅ e2e ALL 3 providers (post-extension) | slack e2e:349-365 + telegram/discord added |

**Added beyond the 8 (safe-direction pins):** `get_instance_info` → None (DB miss) and dict-without-`status` → durable fallthrough (new unit class `TestProbeNoneStatusDurableFallback`, unit:1465-1589).
**Reported, not added (out of mandate, follow-up candidates):** `force_new_instance=True` WITHOUT `/new` combined path; discord typing skip-pin at e2e; durable-path typing assertion at e2.

## 4. Regression sweep — numbers per command

| Command (all from repo root, rev-parse-pinned) | Result @ 90385689 | Result @ 5b54fde5 |
|---|---|---|
| `timeout 300 uv run python -m pytest tests/unit/test_chat_source_live_injection.py tests/integration/test_chat_source_live_injection_e2e.py -m "integration or not integration" -q --tb=short` | **43/43 PASS** (1.38s) — expected 43, delta 0 | **47/47 PASS** (1.62s; +4 gate extensions) |
| Adjacent 4 files (`test_source_reservation.py` 38 + `test_constants.py` 32 + `test_sources_system_fix.py` 29 + `test_sources.py` 6, same flags) | **105/105 PASS** (2s) — dev baseline reproduced EXACTLY | n/a (files untouched by extension) |
| `timeout 300 bash test/packs/regression_chat_source_integration_test.sh` (chat pack) | **57/57 PASS** (23.0s; prior-gate baseline 50 → +7 branch e2e via self-maintaining glob) | **59/59 PASS** (22.6s; +2 e2e extensions) |
| Companion 2-file (`enqueue_wake_e2e` + `saturation_isolation`, `--override-ini="addopts=" -m integration`) | 2P/1F → **FLAKE** (see below) | — |
| `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` | **98P/0F/74S PASS** (8.27s; skips pre-existing PG-only/quarantine) | n/a |
| Static: `grep -n "timeout-graceful-shutdown 10" dev.sh` | **PRESENT** (dev.sh:102) | — |

### Flake adjudication (saturation A2.2) — PRE-EXISTING, not branch-caused
- Node `test_chat_source_saturation_isolation.py::TestSaturationIsolation::test_chat_message_claimed_by_chat_worker_under_saturation` failed ONCE in the 2-file pairing (A2.2 `workers_woken_by_timeout delta=1` — one 3s poll tick landed in the [enqueue, claim] window under post-12-file-pack CPU load; claim latency itself healthy at 0.042s).
- Retry budget: solo ×3 @ HEAD **P/P/P**; identical pairing ×3 @ HEAD **P/P/P**; identical pairing ×3 @ BASE `306468f8` (detached /tmp worktree, removed after) **P/P/P**; node green inside the full chat pack; green at both prior gates.
- Root cause (read-only): NO shared test state (function-scoped fixtures, fresh InstanceManager per test, per-test SQLite) — pure OS-scheduling contention tripping the strict `delta==0` pin.
- **Classification: pre-existing load-sensitive flake — does NOT meet the leader's BLOCKING bar ("demonstrably caused by this delta").** Quarantine decision: **NOT quarantined (MONITORED)** — the node has never failed inside its registered pack; quarantining would cost the A2.2 notify-path headline pin. Monitoring condition + follow-up candidates (process isolation; bound-relaxation needs owner decision — weakens a pin): LESSONS/2026-09-19-flaky-test-saturation-a2p2-load-context.md.

## 5. Out-of-scope confirmations
- **Frontend:** zero frontend changes in this delta (all 7 delta files are daemon/tests) — no FE automation run, per task.
- **No production fixes by the gate:** 0 `daemon/` modifications; only test-file additions (+620/−0) in `5b54fde5`, close-out independently verified (7/7 claims: 2 files exactly, add-only 0 removed lines, harness untouched, 47 collected, HEAD lineage).
- **Known-deferred hardening untouched:** set_injection contract test, typing guard hardening, AST Subscript. Note: the deferred "JobItem direct pin" item is now partially delivered by the durable-row symmetry extension (JobItem row asserted at e2e).

## ensure.md Validation (Core, blast-radius scoped)
- ✅ Critical #1 no regressions in changed packs: ALL scoped packs PASS (feature 47/47, adjacent 105/105, chat 59/59, companion adjudicated non-branch)
- ✅ Critical #2/#3 concurrency integrity + no sync DB on loop: concurrency pack 98P/0F
- ✅ Critical #4 `dev.sh --timeout-graceful-shutdown 10`: PRESENT (static)
- ⊘ Important #1/#2 (await-callers grep, parent→child→complete): scoped out — functions/scenario untouched by this delta
- ⊘ Nice-to-have dead-code check: N/A (delta adds code, deletes none)
- Release Gate: NOT triggered (scoped change; see Scope Decision)

## Code changes summary (gate-owned; committed)
- `5b54fde56c47a0d5402b34f6523c1a820ccc10ff` "test: extend live-injection suite — drain contract, multi-message FIFO, durable-row symmetry, provider row-parity, none-status fallback": `tests/integration/test_chat_source_live_injection_e2e.py` +513, `tests/unit/test_chat_source_live_injection.py` +107. Add-only (0 removed lines), no other files, verified by close-out worker.

## Worker roster
recon e6cf5bdd · audit+extension f175cb91 · newfiles 63eddf6c · adjacent 9b356727 · chatpack 71878a8d · companion+adjudication f4c254ab · conc/statics 48ba8ccc · close-out verify 5d9db26d. All dual-layer-timeout wrapped or read-only; base A/B used disposable /tmp detached worktree (removed).

## Gaps / follow-ups (non-blocking)
1. 🟠 Saturation A2.2 flake hardening (isolation or owner-decision on bound) — LESSONS entry above.
2. 🟢 force_new-without-/new combined path unpinned (no adapter mints it today).
3. 🟢 discord typing skip-pin + durable typing assertion at e2e (unit covers both guards).
4. 🟢 chat pack header census stale (says 50; live 59 post-extension — doc-only).
5. 🟢 no `spec=` in unit mocks; no `enqueue_message_job` signature pin (known-deferred class).

---

### Overall Status
- Feature files: ✅ PASS (47/47 incl. gate extensions)
- Adjacent scope: ✅ PASS (105/105)
- Chat regression pack: ✅ PASS (59/59)
- Companion e2e: ✅ PASS (flake adjudicated pre-existing, base-verified)
- ensure.md Core: ✅ PASS (4/4 critical in scope)
- **Testing Complete: ✅ READY — cleared for merge (include 5b54fde5)**


---

# RE-GATE @ synced tip `6c2de0f4` — VERDICT: **PASSED** (merge authorized)

**Trigger:** `latest` moved under the original gate (concurrent LCA feature `b2371a9b` merged); branch synced — evidence docs commit re-landed as `72a16b2d` (+141, 3 tester-doc files), clean sync-merge `6c2de0f4` (parents 72a16b2d + b2371a9b verified). Original PASS was pinned @ 5b54fde5 / base 306468f8.

**Specific risk covered — graph.py injection-drain seam (semantic read, read-only worker):**
- Lineage verified: 6c2de0f4 parents = 72a16b2d + b2371a9b; 5b54fde5 (test extension, +620/−0) is 72a16b2d's parent; the 2 feature test files diff **5b54fde5..6c2de0f4 = 0 lines** (byte-identical across sync).
- `git diff 5b54fde5..6c2de0f4 -- daemon/graph.py` = 2 hunks, both inside `create_attestation_gate_node` (≈line 5221+ and 5663+ — ~5,400 lines downstream of the drain region): (1) docstring/comment budget-invariant text only; (2) one added `nudge_inject_ts=%s` format arg to an existing `logger.info` deny-nudge line (lazy `now_utc_iso` import). No control-flow change, no queue interaction.
- **Drain seam byte-identity proof (md5 @ both SHAs):** `InjectionQueueManager` wrapper (graph.py:204-227) identical; `set_injection`/`get_injection`/`get_injection_count`/`clear_injection` (manager.py:2734-2862) identical; `daemon/manager.py` whole-file diff ZERO lines; consumer sequence peek→consume→clear (instance_messaging.py:3825/3947) zero-line diff; all producer sites (routers/messages.py, tools/instance.py, tools/job_queue.py, sources/registry.py:1029) + lifecycle cleanup zero-line diff; `_pending_injections` init (manager.py:780) untouched.
- Drain invariants re-verified: peek non-destructive, pop destructive+idempotent, FIFO order, drain timing vs LLM call unchanged (LCA changes live in the attestation-gate node, not the agent_node drain path), no duplicate/skip drains (only new executable line is a log arg).

**Re-pins @ 6c2de0f4 (all rev-parse-pinned at run time):**
| Suite | Result |
|---|---|
| Feature files (unit+e2e, marker override) | **47/47 PASS** (1.88s) |
| Adjacent 4 unit files | **105/105 PASS** (0.75s; 38+32+29+6 exact) |
| Chat pack `regression_chat_source_integration_test.sh` | **59/59 PASS** (22.94s; known-dispositioned saturation node GREEN first try — flake clause not triggered) |
| Incoming LCA tests in combined tree (`test_attestation_fused_judge.py` + `test_attestation_resolver_stage2.py`) | **56/56 PASS** (0.27s; zero cross-feature interference) |

Saturation A/B intentionally NOT re-run (previously dispositioned pre-existing; not re-litigated per instruction).

**Re-gate workers:** graph-read 0a0e142e · newfiles 31640812 · adjacent a5ff777d · attest 51ea5163 · chatpack f899bd70. All read-only or dual-layer-timeout wrapped; zero modifications, zero commits at 6c2de0f4.

**Re-gate verdict: ✅ PASSED — `--no-ff` merge of `6c2de0f4` to `latest` + push is authorized.**
