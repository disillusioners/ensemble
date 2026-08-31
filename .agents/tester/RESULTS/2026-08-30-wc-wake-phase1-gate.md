# Test Gate: WC-Wake Phase 1 (#8) — feature/wc-wake-report-integrity @ 7a484afb (+ gate rider fe64ea06)

Date: 2026-08-30
Gate: Independent phase gate (P1) — WC-target enqueue waking behind kill-switch ENSEMBLE_WC_WAKE_ENQUEUE. Production tip: 7a484afb (18 commits, range 1f8f8ed4..7a484afb, 7 daemon files: constants.py, graph.py, manager.py, routers/messages.py, services/instance_messaging.py, tools/instance.py, tools/job_queue.py). Gate rider: fe64ea06 (test-infra only — 6 pack scripts, zero production delta). BOTH flag states gated; merge posture = flag OFF.
Worker dispatches: 31 (≤3 concurrent — QueuePool cap held).
Tree note: .agents/approver/active.md modification = approver's intentional uncommitted restoration (not flagged, untouched).

## VERDICT: ✅ PASS — ALL 8 VERIFICATION SCOPES GREEN; P2 CLEARED TO DISPATCH ON SAME BRANCH

Merge (after P2 gates) may proceed with flag OFF at merge, per plan. The kill-switch OFF posture is proven byte-identical to base — the instant-revert contract holds.

---

## 1. Full regression — 21/21 packs GREEN, 0 new failures (scope #1)

| Pack | Result | Baseline delta |
|---|---|---|
| job_queue_unit_test | ✅ 1569P/0F/38S (34s) | exact |
| core_unit_test | ✅ 715P/3F/30S (19s) | 3F ALL quarantine-matched (agents_api ×2 + migration-cascade ×1); baseline was 713P/41F — failure mass DROPPED (cascade families now skip at collection); 0 NEW |
| api_unit_test | ✅ 213P/8S/0F (13s) | exact |
| instance_messaging_regression_test | ✅ 28P/0F (0.8s) | 0F on THE core changed seam |
| concurrency_atomic_unit_test | ✅ 98P/74S/0F (7s) | exact — ensure Critical #2+#3 |
| claim_guard_locks_unit_test | ✅ 178P/0F (2s) | exact |
| job_queue_tools_unit_test | ✅ 80P/0F (2s) | exact |
| completion_regression_test | ✅ 96P/37S/1-des (2s) | exact, deselect held |
| wedge_fix_suites_unit_test | ✅ 78P/0F (2s) | exact |
| turn_transitions_reconciler_unit_test | ✅ 48P/1-des (1.5s) | exact, deselect = pre-existing quarantine |
| stability_quick_wins_2_suites_unit_test | ✅ 15P/0F (1.7s) | exact |
| reconciler_paused_race_unit_test | ✅ 8P/0F (0.8s) | exact — PAUSED-reject intact under OFF |
| child_reports_unit_test | ✅ 15P/0F (1.1s) | exact |
| waiting_children_watchdog_unit_test | ✅ 47P/0F (1.3s) | exact |
| orphan_active_job_recovery_suites_unit_test | ✅ 41P/0F (1.1s) | exact |
| security_boundary_hygiene_suites_unit_test | ✅ 45P/0F (0.7s) | exact — messages.py contract tests hold |
| injection_api_unit_test (file-pack) | ✅ 34P/0F (1s) | 27→34: exactly ONE branch test added (flag-ON WC→enqueue/200 @ 231ebe5f); pre-existing flag-OFF 202 test also green → contract pinned BOTH states; other +6 predate branch |
| message_job_serialization_unit_test | ✅ 3P/0F (0.8s) | enqueue serialization shape holds |
| instance_messaging_queue_routing_unit_test | ✅ 16P/0F (1.1s) | routing-pivot seam guarded |
| job_visibility_tools_unit_test | ✅ 46P/0F (1.6s) | 0F; OFF posture (WC path dormant — see scope 3/8 for ON) |
| tools_suite_unit_test | ✅ 1044P/0F/5-des (27s) | 5 archive deselects held; test_instance_tools 195-test routing rewrite green |

All runs pinned HEAD (7a484afb for production-tip runs; fe64ea06 for post-rider runs), flag default OFF, dual-layer timeouts intact, single-pack discipline throughout. Known latent RESULT-echo flaw (naive EXIT_CODE=$? under set -e) in repo packs did not mask any result — every pack adjudicated via pytest summary + raw exit.

## 1b. Attribution sweep (reviewer awareness flag) — CLOSED (scope #1b)
- Watchover family: **47** failures @ HEAD == **47** @ base 1f8f8ed4 — EXACT match on sorted FAILED names AND file:line signatures; test files byte-identical on branch; T6b NOT a vector (no fixture mocks deleted symbol; graph.py delta = comments + pairing-synth id only). Original quarantine row's "45" was row-arithmetic error (per-file sums to 47); signature text was stale (anchors shifted under later refactors; root chain unchanged — ThinkingChatOpenAI.default_streaming ClassVar). QUARANTINE.md row REFRESHED by tester (47 count + current 4-class signature family + re-verified stamp).
- job_queue_proxy_phase1 ×8: reproduces EXACTLY at base; covered by Misc-drift row; no update needed.
- Reviewer's "~80": wider sweep aggregate; row-named files account for exactly 55 (47+8). Other 5 watchover files: 0F/150P.
- **Net: ZERO branch-caused failures anywhere in the attribution surface.**

## 2. OFF-state byte-compatibility — PROVEN (scope #2, the revert path)
Pack `wc_wake_off_bytecompat_probe_test` (gate-created, committed fe64ea06): drives 4 site-windows (HTTP, agent-tool, job_inject-WC, job_inject-IDLE) at flag UNSET and flag="0", at HEAD vs base 1f8f8ed4 worktree (daemon.__file__ resolution proof per tree; trap-cleanup worktree). **ALL 4 SITES BYTE-IDENTICAL HEAD-vs-base under BOTH OFF windows**, including the byte-faithful job_inject legacy eligibility error string, verbatim: `'Instance is idle — job_inject only works on RUNNING or WAITING_CHILDREN instances. Use job_continue for IDLE/terminal instances.'` Kill-switch = instant byte-identical revert. RESULT: PASS (~10s).

## 3. ON-state wake path — PROVEN (scope #3)
- Pack `wc_wake_pure_hang_integration_test` (S6, flag ON set per-test): 3/3 runs green × 3 surfaces (HTTP POST /messages, agent-tool send_message, job_inject → enqueue), 38s cumulative, ZERO run-to-run inconsistency. Real engine: real InstanceManager (WAL SQLite), real WorkerPool(1), real JobProcessor, _ScriptedLLM one-seam. Wake evidence: MessageQueue+Task rows, WC→RUNNING flip, real turn.
- Pack `wc_wake_d1_w5_pairing_unit_test`: 54P/0F — D1 entry-seam pairing heal (incl. structural LangGraph-2013 mimic + guard + CLE-mirror R2 + R1 deterministic ids), W5 claim-order + FIFO single-turn + M1 requeue identity + S9 terminal-after-turn-1.
- D2 ordering (leftovers strictly before user), re-park cycle, PAUSED-reject (pausedrace 8P), RUNNING-lane untouched (tools_suite + instance_tools both-state parametrization) — all green via the above + scope-1 packs.
- Pack `wc_wake_flag_resolver_tools_unit_test`: 210P/0F (resolver 15 + instance_tools 195 both-flag-states routing map).

## 4. W5 semantics change — PINNED (scope #4)
W5 two-turn claim-order ON-state assertions + FIFO single-turn invariant + M1 requeue-identity (id()-dedupe) + S9 terminal-after-turn-1 — all in runA's 54P; red-green proves the W5 additions RED at base (5 RED / 9 weak honestly classified as pre-existing-behavior pins).

## 5. Resolver matrix + W1 pollution — PROVEN (scope #5)
- Pack `wc_wake_w1_pollution_probe_test`: 12/12 truth-table rows match real implementation — unset/0/false/no/off → OFF; 1/true/yes/on → ON; blank + unknown ('garbage'/'maybe') → **OFF + WARN** (W2 contract verified against live resolver).
- Permutation equivalence: resolver-alone 15P; resolver→tools and tools→resolver single-process both 210P — **B==C, order-independent; W1 cache-reset fixtures (4a6e22b5 + f111c7d3 + 7a484afb module-identity closure) seal pollution.**

## 6. T6b completeness — COMPLETE (scope #6)
5/5 checks PASS: symbols GONE from manager.py + instance_messaging.py; zero method-call-style callers in daemon/ (remaining hits = legitimate router endpoint, agent-tool closure, opencode dispatcher, docstrings); test-fixture census ZERO calls to deleted methods (A1/A2 migrated to enqueue_message; **arc-skip census = 0 as claimed**); migration evidenced (081360e3 −302 lines deletion; A1 unit-tier 8 files; A2 live-integration 3 files via _drive_turn; A3 docs diagram → enqueue_message). Deletion enforced by AttributeError semantics; 🟢 optional follow-up: a dedicated structural pin test.

## 7. Red-green + mock fidelity — BOTH GREEN (scope #7)
- Red-green @ base 1f8f8ed4 worktree (daemon.__file__ proof; worktree cleaned): D1 2013 mimic **STRONG RED** (exact 2013 shape); D1 pairing-guard collection-ImportError RED; W2 resolver collection-ImportError RED; R1 deterministic-ids 7/7 RED; W5 5 RED + 9 weak (classified honestly); control all-green at HEAD. Tests have teeth.
- Mock fidelity: **0 vacuous, 0 internal mocks, 0 mocked-order claims.** Boundary-only stubs everywhere; pure-hang rated best-in-class (real engine, one-seam LLM); W1 autouse hygiene gold-standard in all five flag-touching files. 1 INFO (WARN not caplog-captured in resolver tests — supplementary, boolean contract is load-bearing).

## 8. E2E capstone — PROVEN BOTH STATES (scope #8)
Pack `wc_wake_e2e_capstone_test` (real-engine harness reused from S6): **ON** = wake bounded ≤60s (both tests completed in 7.62s combined — prompt, not hour-scale), MessageQueue+Task rows, WC→RUNNING, real scripted-LLM turn, re-park/complete; **OFF** = 202 + injected body, ZERO durable rows, parent stays WC, LLM never sees the token — legacy documented stranding unchanged. RESULT: PASS.

## ensure.md Validation Results
Core (per .agents/tester/rules/ensure.md):
- **Critical 4/4**: ✅ #1 no regressions in changed packs (21/21 green, 0 new); ✅ #2 deadlock/concurrency (concurrency_atomic 98P/74S exact); ✅ #3 no sync DB on asyncio loop (same pack, thread-identity tests); ✅ #4 dev.sh `--timeout-graceful-shutdown 10` (static grep, 2 matches incl. live uvicorn flag — verified by recon).
- **Important 2/2**: ✅ #1 await-callers static check — PASS (0 violations across 8 daemon call-sites for `_get_system_prompt_tokens`/`_compute_context_usage`/`get_queue_stats`; 0 branch-added call sites; busy-gate call site pre-existing and properly awaited, untouched by branch); ✅ #2 original deadlock scenario (concurrency pack).
- **Nice-to-have 1/1**: ✅ #7 no dead code from fix (T6b census COMPLETE; ensure scope).
- Release Gate: NOT TRIGGERED for this gate decision — this is a phase gate behind a default-OFF kill-switch with byte-identical OFF posture proven; the full non-integration breadth was nevertheless covered via 21 packs + attribution sweep (55 failures all base-attributed). Contradictions: NONE.

## Code changes (this gate)
- fe64ea06 — test: 6 gate pack scripts (D1W5, flag-resolver+tools, pure-hang ×3, off-bytecompat, W1-pollution, e2e-capstone), test/packs only, committed by gate worker.
- .agents/tester/QUARANTINE.md — watchover row refreshed (45→47 + current signature family + re-verified stamp) by tester.
- .agents/tester/PACKS.md — 6 new pack rows registered by tester.

### Overall Status
- Regression ✅ 21/21 · OFF-bytecompat ✅ (revert path proven) · ON-wake ✅ (3 surfaces ×3 runs) · W5 ✅ · resolver+W1 ✅ · T6b ✅ · red-green ✅ · mockfid ✅ · attribution ✅ (0 branch-caused) · ensure.md Core 4/4
- **Testing Complete: ✅ READY — P1 gate PASSED. P2 waves cleared to dispatch on the same branch. Merge (after P2 gates) with flag OFF at merge.**
