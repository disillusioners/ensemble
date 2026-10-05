# Merge-Gate Test Report: durability F-1 + F-2

Date: 2026-10-05 (commission opened 2026-10-04)
Tester: Test Leader instance 6830971a — 11 worker instances dispatched (4 pack runners, 1 E2E driver ×3 rounds, 1 inventory, 4 sweep lanes, 1 base-A/B, 1 ensure), all reported
Subject: branch `feature/durability-f1-f2`, worktree `/home/nea/ensemble-src-wt-durability`, code-state pin **`fb0f4655`** (base `18827dbd`, 12 commits)
Gate type: MERGE GATE (independent validation; nothing trusted from prior lanes)
Ruling honored: evidence-only — NO code changes, NO fixes from the test lane; failures get root-cause reports.

## Overall Verdict

**❌ NOT READY for merge.**
- **G1 full-dir sweep: FAIL** — 298 branch-side failure nodeids attributed by fresh-base A/B (`18827dbd`): 283 PRE-EXISTING / 8 per-A-B BRANCH-CAUSED / 7 DIVERGENT (env-class) — **post-wrap correction: the langgraph entry is REFUTED (uv.lock diff EMPTY; test file identical base↔branch; 12/12 pass in BOTH venvs on re-run — transient venv state during the sweep) → 7 CONFIRMED branch-caused**. The commission's expected 4 known REDs (v0.13.10-era) are confirmed **exactly 4, unchanged**. Any NEW failure was the blocker bar → the 7 confirmed entries block, with full repro in `/tmp/ac-gate/g1-ab-verdict.md` (durable copy: `RESULTS/2026-10-05-durability-f1f2-g1-sweep-artifacts/`).
- **G2 pack re-runs: PASS** (15/15 + 56/56).
- **G3 auto-continue regression chain: PASS** (14/14 + 94/94 incl. MANDATORY real-seam integration 15/15 on real PG).
- **G4 F-2 demo E2E (UNCONDITIONAL): FAIL (main leg)** — a true straddle capture (marker PENDING, wake ready, SIGSTOP→verify→SIGKILL) leaves the parent **wedged in `waiting_children` indefinitely** (no heal in 300s; RDRS lane-2 emits zero sweep lines for the child; watchdog interval 3600s; a manual ping deadlocks against the worker-pool per-instance guard). Controls: (b) non-straddle PASS, (c) run-twice idempotency PASS; kill-switch-OFF boot config verified (behavioral OFF→ON contrast moot — ON does not heal the captured variant); multi-child mixed leg = declared gap (capture-luck).
- **G5** = this file.

## Scope Decision

Full-dir sweep explicitly mandated by the commission (merge gate; deferred from the dev lane after its 300s-chunk timeouts). Executed as 4 parallel lanes + serial PG lane, per-chunk `timeout 1500s` (commission-sanctioned generous cap), default addopts (`-m 'not integration and not postgres and not slow'`), `--continue-on-collection-errors`, no `-x`. Excluded per prior-gate precedent: LLM/live-daemon lanes (`tests/e2e/test_context_injection_hybrid.py` import-time HTTP; 442 integration-marked live-OpenCode tests; `slow`). Count correction: commission said "~7000+"; **measured default-collected scope ≈ 24,225** (19,652 dir-sum + 4,573 root-file lane E — a coverage gap in the inventory's first draft that lane E closed) + 329 PG lane. ~24.5K tests executed across all lanes.

## Methodology deviations (all documented, all evidence-preserving)

1. **Code-state pin override:** branch HEAD advanced past `fb0f4655` by the G4 lane's own evidence commits (`b24cab33`, `e1e9eadb`, `8a1a196f` — all paths under `.agents/`). Every sweep/A-B/ensure worker verified `git diff --name-only fb0f4655..HEAD | grep -vE '^\.agents/' | wc -l` == 0 before running; execution HEAD `8a1a196f` is the hash of record; code under test byte-identical to the pin.
2. **TERM→KILL deviation in G4:** `kill -TERM`'s graceful drain DELIVERS pending wakes (observed in round 1, invalidating the straddle). Round 2+ used SIGSTOP→DB-verify→SIGKILL (the crash-durability model F-1/F-2 target). Disclosed in EVIDENCE findings.md.
3. **`--timeout-method=signal` (invocation-level CLI, C2d/C2e + base attestation leg):** the project's thread-mode 30s timeout cannot interrupt C-level `ssl.recv` blocks hit by live-LLM fused-judge tests (two whole-chunk 1500s stalls proved it). Signal mode kills them at 30s; no file changes; pyproject untouched.
4. **Phantom file:** the inventory's e2e list included `test_wanderer_orchestrator_e2e.py` (never committed on this lineage — inventory error). Hardened C2 to whole-dir + explicit `--ignore` of the hybrid landmine.
5. Minor: my dispatch placed the enqueue_shared known-RED in E2; it lives in E1 (worker re-tagged correctly).

---

## 1. Pack Results (G2 + G3)

| Pack / suite | Result | Counts | Runtime | Notes |
|---|---|---|---|---|
| discard_on_startup_dependency_bus_race_unit_test.sh (G2a, plan 3.1) | **PASS** | 15/15 incl. AST name-binding pin `test_no_bare_boot_epoch_name_in_manager` (ast.walk over manager.py, bare-name guard) | 4.46s pytest / 9s wall | — |
| auto_continue_boot_pass_unit_test.sh (G2b, plan 3.3) | **PASS** | **56/56 — actual count UNCHANGED; the review's "grew 56→70" claim is refuted by direct pytest output** | 17.3s | wraps boot_pass + candidates + terminalizer (§8 keep-green files) |
| auto_continue_interleaving_unit_test.sh (G3a) | **PASS** | 14/14 (AC4 6-row matrix incl. Δ1) | 5.75s | 19 pre-existing SQLAlchemy warnings |
| F-2 RDRS suite (G3b, plan 3.2+3.5): 5 files | **PASS** | 94/94; **MANDATORY real-seam integration `test_report_delivery_double_delivery_pg.py` 15/15 GREEN on real PG** (10-pairing cross-actor matrix + natural-completion-vs-recovered-marker + transition-before-reconcile + straddle-seed) | 49.1s | PG_TEST_* + `--override-ini="addopts="` per repo convention |

## 2. G1 Full-Dir Sweep

Lanes (execution HEAD `8a1a196f`, code-state `fb0f4655`, import-gate verified per lane):

| Lane | Scope | Collected | P | F | E | S/Skips | Notable |
|---|---|---|---|---|---|---|---|
| A | tests/unit subdirs (tools/services/routers/repositories/rag/job_queue/persistence/models/graph/config/checkpoint_adapter/job_state/sources) | 6,745 (exact plan match) | 6,705 | 35 | 0 | 5 | 0 timeout-flags; failures = documented families (archive×5, explore-caller×2, prompt-section×4, job_queue_proxy×7, post_restart_arm×2, watch×2, anti-drift×3, context_injection F-4×1, b1_wc×1(+1 env), service_tool_manager×1, stop_instance_subtree×3, work_router×1…) |
| B | tests/unit root files (glob a–h / i–p / q–z; 344 files) | 7,864 | — | 67 | 23 | 53 | 0 timeout-flags; heavy llm-stream-stall 74-family overlap (builtin_mcp mock-gap ×21, find_near ×5, coder_developer ×6, api-module-size, project_manager prompt) |
| E1+E2 | tests/ root files (149 files — the closed coverage gap) | 4,573 | 4,278 | 118 | 8 | 127+ | 0 timeout-flags; ~60 vision-model env-rot; enqueue_shared known-RED in E1 |
| D1 (PG lane, serial) | tests/postgres | 329 | 261 | 8 | 24 | 36 | `_MinimalManagerProxy` cluster ×24 = PRE-EXISTING at base (A/B) |
| C1 | tests/job_queue + services + repositories + migration | 3,066 (exact) | 2,979 | 29 | 0 | 58+3D | 3 KNOWN-4 + 13 quarantine-family + 13 new→all PRE-EXISTING |
| C2→C2e | remainder dirs (integration, opencode, e2e-minus-hybrid, property, message_queue_redesign, api, manager, lint, static, performance) | ~3,600 reached | — | 33+6TO | 1 | — | two whole-chunk 1500s stalls on live-LLM judge hangs (turn_state_machine [QUARANTINE row 66], attestation_bound_escalation [env-class, A/B-confirmed]) → resolved via signal-mode; C2d 812-integration + C2e 545 executed end-to-end |

**Branch-side failure total:** 338 entries → 298 unique nodeids A/B-attributed (below). Known-4 census: **exactly the 4 expected** (3× TestSite1InlineMirrorFinalize @ tests/job_queue/test_event_driven_completion.py:262 — MagicMock JSON vs task_repository; 1× enqueue_shared idle→running @ tests/test_enqueue_shared.py:486) — all four fail identically at base. UNCHANGED, as the commission required.

## 3. Base A/B (`18827dbd`, fresh detached worktree `/home/nea/ensemble-src-wt-d1f2-base`, own `uv sync` venv, no-`.env` parity both legs, location-vs-commit confound structurally eliminated)

**283 PRE-EXISTING · 8 per-A/B BRANCH-CAUSED (→ 7 CONFIRMED after wrap) · 7 DIVERGENT (env-class) = 298.**
(The 6 C2d live-LLM signal-kills + 1 macOS-path ENV-DEFECT are classified by mechanism, not A/B — disclosed.)

**The CONFIRMED branch-caused set (7; G1 blockers, full repro in artifacts):**
1. `tests/unit/test_fm11_shield_gap.py::TestW12CrashMidShield::test_lost_write_shape_backstop_recovers_within_one_cycle` — **F-1 wedge family regression (passes at base)**
2. `tests/unit/test_resume_router_report_dup.py::TestLostReportRecoveredExactlyOnce::test_recovery_is_exactly_once_across_sweep_passes` — **F-1 wedge family regression (passes at base)**
3. `tests/test_council_tools.py::TestSpawnCouncilor::test_non_team_member_agent_raises_value_error…` — identity-line parse artifact; test is canonical target
4. `tests/test_report_lane_phase2.py::TestErrorPropagation::test_error_flag_uses_fallback_when_message_missing` — **root-caused: F-1's intentional truthy-error gate (decisions §1) + §1a defensive WARNING observed firing at dependency_bus.py:693; the test encodes the PRE-fix contract (expects `_parent_errored` flip on error=None). Stale-test-contract drift — the sibling-drift hazard class. Also the sole delta in the ensure concurrency pack (98P→97P baseline).**
5. `tests/test_terminal_orphan_matrix.py::test_jobitem_task_status_matrix[pending-True-active]` — parse artifact noted
6-7. `tests/postgres/test_list_queues_with_admittable_work_pg.py` ×2 — `admission_state=active requires a job_locks row` (PG guard enforcement vs test fixture)

**REFUTED post-wrap (was per-A/B branch-caused #1):** `tests/unit/test_mcp_tool_timeout.py::TestToolNodeIntegration::test_tool_node_handles_timeout` (`ModuleNotFoundError: langgraph._internal`) — `git diff 18827dbd..fb0f4655 -- uv.lock` EMPTY; test file identical base↔branch; langgraph pinned 1.0.9 both sides; re-run today passes 12/12 in BOTH the base and branch venvs. Transient venv state during the sweep — not a branch regression. (Methodology lesson recorded: import-error branch-caused entries get a dual-venv re-run before final verdict.)

## 4. G4 — F-2 Demo E2E (UNCONDITIONAL; gates promote)

Environment: R18 dev-lane recipe (8079/ensemble_dev, `QUEUE_DISCARD_ON_STARTUP=true`, 4-point isolation per boot, INCIDENT-1-hardened bash wrapper with fail-loud env assertions). Evidence: `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/` (findings.md, timeline.md, recipe-pointer.md, 18+ boot logs, 8+ db-assertion files). Commits: `b24cab33` (round 1) → `e1e9eadb` (redo) → `8a1a196f` (forensics). Port safety: 9797 (live, pid 3321986) and 7979 (demo, pid 3457886) untouched throughout (pid-stability proven); 8079 restored to v0.16.11 (/livez verified) after every round.

**Round-1 adjudication (why the redo was required):** TERM's graceful drain delivered the pending wake pre-death (report_injections TASK_DELIVERED + watchdog heal) — the straddle was NOT achieved; lane-2's skip was correct for a marker-minted case but proved nothing about the target path.

**Redo (R1, decisive):** TRUE straddle captured (marker `PENDING`, wake `ready`, SIGSTOP in the same poll iteration → DB-verify → SIGKILL; timestamps + raw psql in `db-assertions/`). Post-reboot on the branch build:
- **Parent NEVER healed** — `waiting_children` through t+300s. RDRS lane-2: **zero** sweep lines for the child (its anchor-less admission never fires — in reality the anchor is always minted pre-kill). WaitingChildrenWatchdog: interval **3600s** (code-verified) — not a practical backstop. Lock artifact ruled out (pg_stat_activity clean).
- **Manual ping → deadlock:** parent → `running` on the API task; the worker-pool per-instance guard then refuses to claim the still-`ready` wake; parent uncompleted at 150s. Chicken-and-egg.
- **Notable:** the wake row SURVIVED the reboot (`ready` at t+30/120/300) — the F-1 epoch-belt wipe change appears to preserve it, shifting the wedge from "wiped wake" to "preserved-but-unclaimable wake" (same wedge, new mechanism — recorded for the fix commission; not concluded here).
- Criteria: a) FAIL · b) FAIL (manual ping required) · c) PASS (exactly one internal_report row) · d) PASS (child not re-executed) · e) PARTIAL (marker present; zero admission counters).

**Interpretation for the leader:** the passing G3b real-seam integration suite and the failing demo are not in contradiction — the tests SEED the exact anchor-less shape lane-2 was built for; the REACHABLE real-world crash state (marker PENDING at wipe time, the original F-2 incident's shape) is a different shape that NO mechanism heals on this build. The design's own claim (decisions §12a: marker-minted cases "already handled by lanes 1/3/4") is contradicted by observation.

**Controls:** (b) non-straddle restart PASS ([BOOT_CONTINUE] candidates=1, child resumed from checkpoint, single hello, parent woken — no regression to the auto-continue path); (c) run-twice idempotency PASS (lane-2 zero new work, counters unchanged, no duplicate injection). **Gaps:** kill-switch-OFF behavioral leg (config verified `no_row_backstop=False`; wedge-capture for it missed 3+3 attempts — window <100 ms; moot while ON doesn't heal) and multi-child mixed leg (capture-luck; the mixed-filter counters were exercised only in the skip direction). **R0 crash attribution:** the L2-blocking daemon crash (`RuntimeError: DependencyBus is not initialized…` at `child_reports.py:2841`, A8 hard error) is **PRE-EXISTING at base** (file untouched by the branch) — but it is itself a real durability bug (leftover child completion at boot crashes the daemon) worth a follow-up commission.

## 5. ensure.md (Core, blast-radius scoped)

- **Critical R1 (concurrency pack): ❌ FAIL as-observed** — 97P/74S/**1F** vs 98P/74S/0F baseline; the 1F = branch-caused entry #5 above (F-1 intentional semantics vs stale test contract; §1a warning firing verbatim in the log). The ensure worker's foreign-venv contamination caveat is REFUTED by cross-evidence (branch-side failure from import-gate-verified lane E; base leg in isolated fresh venv passed; the warning cites F-1-only code).
- **Critical R2 (dev.sh `--timeout-graceful-shutdown 10`): ✅ PASS** (grep verbatim, live uvicorn invocation).
- **Critical R3 (no regressions in changed packs): ✅ CITED-green** — all four changed-scope packs PASS (§1).
- Quarantine-aware throughout; no `pytest -x`; no ensure.md contradiction notices this run.

## 6. Findings & Follow-ups (for the leader)

1. 🔴 **G4 main-leg FAIL** — reachable straddle state (marker PENDING at crash) never heals; watchdog 3600s; manual ping deadlocks. Fix commission needed (coverage of the PENDING-marker variant, or a claim-guard exemption for wake claims, or wipe/markers interplay redesign).
2. 🔴 **7 confirmed branch-caused test failures** — 2 F-1-family regressions (fm11_shield_gap, resume_router_report_dup), 1 stale-contract (report_lane_phase2 — update test to post-F-1 semantics), 2 PG job_locks fixture, 2 parse-artifact-adjacent. (An 8th per-A/B entry, mcp_tool_timeout/langgraph, was REFUTED post-wrap: empty uv.lock diff + dual-venv 12/12 re-pass — transient venv state.) Dev-lane scope.
3. 🟠 Base durability bug (pre-existing): leftover child completion at boot crashes the daemon (`child_reports.py:2841` A8). Separate commission.
4. 🟠 tests/integration live-LLM fused-judge tests hang whole chunks without an LLM endpoint (thread-mode timeout can't kill SSL recv). Recommend scripted-judge fixtures or a quarantine row; signal-mode is the sweep workaround.
5. 🟢 Sweep-visible pre-existing reds: 283 nodeids (clusters in ab-verdict.md) — candidate for the standing F-3-style triage commission; QUARANTINE.md additions optional.
6. 🟢 Inventory phantom-file + root-file gap fixed in the chunk-plan artifact; reuse `2026-10-05-durability-f1f2-g1-chunk-plan.md` for future full-dir gates.

### Gaps
- G4 kill-switch-OFF behavioral leg + multi-child mixed leg: not completed (capture window <100 ms; documented attempts in EVIDENCE). Moot for the verdict given the main-leg FAIL; required for any re-gate after a fix.
- C2d collection accounting has ±386 ambiguity in the worker's summary (deselected-vs-collected overlap); authoritative per-test data in the chunk log (copied to artifacts).
- 442 integration-marked (live-OpenCode) tests excluded per precedent — never in scope.

## 7. Code Changes Summary (test lane)

- **NONE to production or test code.** No quick fixes (commission ruling).
- Evidence commits by the G4 lane: `b24cab33`, `e1e9eadb`, `8a1a196f` (all `.agents/tester/EVIDENCE/…` only — the cause of the documented code-state-pin drift).
- This file + G1 artifacts + chunk plan: committed by the wrap worker (hash in §8 when landed).

## 8. Artifacts

- G1 sweep: `RESULTS/2026-10-05-durability-f1f2-g1-sweep-artifacts/` (identity files ×4 lanes + base, `g1-ab-verdict.md` [458 lines, authoritative 3-bucket tables], gzipped chunk logs, ensure log, uv-lock check) — durable copies; originals `/tmp/ac-gate/` (volatile).
- G4: `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/` (committed).
- Chunk plan: `RESULTS/2026-10-05-durability-f1f2-g1-chunk-plan.md`.

---

### Gate Verdicts
- G1: **FAIL** (7 confirmed branch-caused + 1 refuted false-positive; known-4 confirmed unchanged; 283 pre-existing documented)
- G2: **PASS** (both packs green; count claim 56→70 refuted, actual 56)
- G3: **PASS** (keep-green chain + F-2 suite + mandatory real-seam integration green)
- G4: **FAIL** (main leg never-heal + intervention deadlock; controls b/c PASS; gaps disclosed)
- ensure.md Core: **FAIL** (R1 single test = branch-caused entry #4 above; R2/R3 green)
- **Overall: ❌ NOT READY — merge blocked pending the G4 coverage fix + the 7 confirmed branch-caused failures' remediation.**

Wrap commit (this file v1 + chunk plan + 26 artifact files): `e51939c6`. Post-wrap correction (langgraph refutation + 4 force-added .log evidence files + lessons) rides the follow-up commit.

---

# RE-GATE (2026-10-05, fix HEAD `f53a0638`)

Fix commission: `3a2bbdf8` (G4: additive lane 6 `stuck_wake`) → `e3644431` (G1: test-side fixes for the 7) → `f53a0638` (review close-out). Dev claims: G1 7→0; G4 heals via lane 6; kill-switch default ON; lanes 1-5 byte-identical. Re-gate verdicts:

## G1-r: **PASS — 0 branch-caused confirmed** (defensible equivalent)
Scope basis (preflight-verified diff surface): daemon delta = 2 files +267/−2 (report_injection repository + report_delivery_recovery service); lane additivity byte-verified (lanes 1-5 untouched; only sqlalchemy import + constructor log-line append). Sweep = the previously-failing surface (all prior-gate failing FILES, 3 batches) + PG lane files + sampled new-code consumers: **303 [PERSISTING-PRE-EXISTING] · 35 [FIXED] · 0 [NEW?]**. All 7 expected fixes verified FIXED (fm11 ×1, resume_router ×1, council_tools ×24 — whole file fixed by the vision-alias fixture, report_lane_phase2 ×1 post-rename to `test_error_flag_flip_with_truthy_error_sets_fallback`, terminal_orphan_matrix ×1, list_queues ×2 — sealed 3-clean-pass vs 1 contended-fail adjudication, log `g1r-listqueues-confirm.log`). **KNOWN-4: 4/4 unchanged** (files untouched by the fix — preflight empty-diff). Sampling of new-code consumers (batch C, 5 files): 96P/0F. Deviations documented: signal-mode belt on the live-LLM-judge file (same as prior gate), QUARANTINE-row-66 deselect (re-failed standalone as expected — persisting).

## G2-r: **PASS** — F-1 pack 15/15 @4.5s (AST pin + S1-S7 + FP1 by name); boot_pass 56/56 exactly (no count change).

## G3-r: **PASS** — interleaving 14/14; F-2 family **140/140** (Batch-1 PG 39/39 on a serialized clean DB incl. both lane-6 real-shape PG tests green: `test_stuck_wake_lane_heals_captured_wedge_on_pg`, `test_stuck_wake_lane_is_noop_on_empty_db_on_pg`; Batch-2 unit 101/101 incl. report_lane_phase2 30/30 post-rename + ledger 14/14 dev-claim verified); PG-suite dev claim **24/24 VERIFIED**; full PG lane comparative: 263P/8F/24E/34S = prior clusters unchanged (+2P/−2S = exactly the 2 new lane-6 tests). NOTE: one concurrent-PG-dispatch contamination event (mass teardown FK error + the list_queues contended fail) — adjudicated as the documented shared-`ensemble_test` race (conftest's own xdist guard exists for it); all affected evidence superseded by serialized clean runs (adjudication record `g3r-f2-batch1-adjudication.md` in artifacts).

## G4-r: **❌ FAIL (main leg) — the fix does not heal the LIVE wedge**
True stuck-wake straddle captured per the LESSONS SIGSTOP recipe (marker PENDING; wake task claimed-RUNNING by worker-4; heartbeat staled 183s > 90s threshold; SIGSTOP→verify→SIGKILL; ≥100s dead-wait) → boot → lane 6 RAN but `find_stuck_wake_candidates` returned **0 candidates** → parent never healed (a=FAIL, b/c/d=PASS, e=PARTIAL).
**Root cause (evidence-backed, rebuttable):** the lane-6 query joins `ri.child_message_id = mq.message_id`, but the REAL captured data carries `mq.message_id ≠ ri.child_message_id` (ddbeef1d ≠ 34cedf8d; the wake row correlates via its `source` pattern — a relaxed source-join returns exactly 1 row). This is the join-key hazard phase1-plan §3 explicitly warned about (queue-side id vs marker id semantics). The seeded PG test passes because the SEED satisfies the join; reality does not — the same tests-seed-the-shape/reality-differs divergence as the first gate, one layer deeper.
Secondary findings: (1) **kill-switch env gap** — lane 6 has NO `config.py` env var (constructor param only; `pool_orchestrator.py:308` defaults ON) → not operator-reachable; unit-level OFF tests green (2/2); R2 behavioral leg covered only at unit level + documented gap. (2) **§12e decisions.md structural corruption** (orphan duplicate header ~line 1281 from f53a0638; docs-only). (3) Multi-child mixed leg INCOMPLETE (capture-miss + the pre-existing base crash `child_reports.py:2841` recurred mid-leg — still pre-existing, untouched). (4) R4(i) no-double-retry verified code-level only (lane 6 never processed live). **Positive:** manual-ping deadlock GONE (post-heal-state ping → normal response); non-straddle auto-continue intact; run-twice idempotency intact. Evidence @ `e5e60d31` in EVIDENCE/ (r1r/r2r/r3r/r4r files).

## RE-GATE OVERALL: ❌ NOT READY — G4-r blocks (bar was all-gates-green)
G1-r ✅ · G2-r ✅ · G3-r ✅ · G4-r ❌. The single blocking defect is precise: the lane-6 find-query join key does not match the real message_queue/report_injections data shape. Everything else the fix commission claimed is verified green.

### Follow-up ledger (for the next fix round)
1. 🔴 Lane-6 join-key fix: correlate via `mq.source` pattern (or add the id mapping per phase1-plan §3 option (i)) — then re-prove with the LIVE SIGSTOP recipe (the PG-seeded test alone demonstrably does not guard this seam).
2. 🟠 `report_delivery_recovery_lane_stuck_wake` env var in config.py + pool_orchestrator wiring (lanes 1-5 parity).
3. 🟠 §12e cleanup (orphan header, dangling fragment).
4. 🟢 Multi-child mixed live leg remains unproven (retry after the join fix).
5. 🟢 Serialize ALL PG-lane dispatches against `ensemble_test` (this gate's contamination events) — logged to LESSONS.

---

# FINAL RE-GATE (2026-10-05, fix HEAD `c498c6d9`)

Delta (preflight-verified, all anchored): source-correlation join `mq.source LIKE 'internal_report:' || ri.child_instance_id || ':%'` (repository.py:2131-2136, colon-anchored, old id-join gone from executable SQL) · live-shape seeds (real `34cedf8d≠ddbeef1d` invariant) + 2 new pins (source-correlation, sibling-boundary) · env-var parity (config.py:1380 Field + pool_orchestrator.py:337 wiring) · §12e repaired. "5 files +401/−33" holds under code/test/docs scope (raw range also carries this gate's ~26 evidence artifacts). The "3-RED meta" is a documented temporary revert in the commit message; the durable automated guard = the 3 live-shape pins.

## Suite results at `c498c6d9` — ALL GREEN
F-1 pack 15/15 (AST pin green) · boot_pass 56/56 · interleaving 14/14 · **F-2 family 142/142** (4-test lane-6 quartet green incl. the rewritten live-shape heals-test + both new pins; PG serialized, contention-checked) · spot re-run `test_boot_report_recovery.py` 14/14 (sole non-family consumer of the changed wiring). G1-r/G2-r/G3-r results at f53a0638 stand per leader ruling (delta blast radius fully covered by the above).

## G4-r2 LIVE legs (SIGSTOP recipe; evidence prefix f3-; commits `a519f241` + `08b64b7d`)
- **LEG 1 (main straddle): ❌ FAIL against the bar — one criterion short.** PROVEN live: lane 6 finds the candidate via the source correlation (join fix works on the REAL shape — recovered=1), force-cancel→retry chain with same-message_id + retry_count=1 + no double-retry (criteria a/d/e/f PASS). **Residual defect:** lane 6 marks `report_injections TASK_DELIVERED` + resolves the wake row, but the injection path **does not enqueue a graph turn for the parent** — the retry task skips ("already delivered via injection"), so the parent stays `waiting_children` until a MANUAL PING (criterion b "zero manual pings" violated; c shows the deviation). Root cause is surgically narrow: last-mile wake — schedule the parent turn after TASK_DELIVERED via injection (or route the retry's skip into a resume).
- **LEG 2 (multi-child mixed): PASS** — selectivity proven on the real shape: ONLY child2 recovered (child=afd68442), child1 not re-processed, exactly ONE new internal_report row, no duplicates (same auto-wake caveat as LEG 1 applied to the synthesis step).
- **LEG 3 (kill-switch env): PASS** — end-to-end env wiring proven live: OFF boot → `stuck_wake=False` in the constructor log + wedge persists ≥120s; default-ON reboot → heals via lane 6. The G4-r operational gap is CLOSED. (Name-note: the working env value format verified live = false/False/0; the operative env name per the live leg is recorded in the f3-leg3 evidence.)

## FINAL VERDICT: ❌ NOT READY — one remaining defect (LEG 1 criterion b)
Progression across three rounds, each verified live: (1) no lane admits the shape → (2) lane admits but can't find it (join key) → (3) lane finds + delivers + is selective + is kill-switchable, but doesn't auto-wake the parent. Remaining fix is the last mile: enqueue the parent graph turn upon lane-6 injection delivery. Re-gate scope after that fix: LEG 1 only (the recipe + harness are committed and fast).
