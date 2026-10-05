# Merge-Gate Test Report: durability F-1 + F-2

Date: 2026-10-05 (commission opened 2026-10-04)
Tester: Test Leader instance 6830971a — 11 worker instances dispatched (4 pack runners, 1 E2E driver ×3 rounds, 1 inventory, 4 sweep lanes, 1 base-A/B, 1 ensure), all reported
Subject: branch `feature/durability-f1-f2`, worktree `/home/nea/ensemble-src-wt-durability`, code-state pin **`fb0f4655`** (base `18827dbd`, 12 commits)
Gate type: MERGE GATE (independent validation; nothing trusted from prior lanes)
Ruling honored: evidence-only — NO code changes, NO fixes from the test lane; failures get root-cause reports.

## Overall Verdict

**❌ NOT READY for merge.**
- **G1 full-dir sweep: FAIL** — 298 branch-side failure nodeids attributed by fresh-base A/B (`18827dbd`): **283 PRE-EXISTING / 8 BRANCH-CAUSED / 7 DIVERGENT (env-class)**. The commission's expected 4 known REDs (v0.13.10-era) are confirmed **exactly 4, unchanged**. Any NEW failure was the blocker bar → the 8 branch-caused entries block, with full repro in `/tmp/ac-gate/g1-ab-verdict.md` (durable copy: `RESULTS/2026-10-05-durability-f1f2-g1-sweep-artifacts/`).
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

**283 PRE-EXISTING · 8 BRANCH-CAUSED · 7 DIVERGENT (env-class) = 298.**
(The 6 C2d live-LLM signal-kills + 1 macOS-path ENV-DEFECT are classified by mechanism, not A/B — disclosed.)

**The 8 BRANCH-CAUSED (G1 blockers, full repro in artifacts):**
1. `tests/unit/test_mcp_tool_timeout.py::TestToolNodeIntegration::test_tool_node_handles_timeout` — `ModuleNotFoundError: langgraph._internal` (attribution sub-class — dependency-pin delta vs venv drift — recorded in `g1-sweep-artifacts/uv-lock-check.txt`)
2. `tests/unit/test_fm11_shield_gap.py::TestW12CrashMidShield::test_lost_write_shape_backstop_recovers_within_one_cycle` — **F-1 wedge family regression (passes at base)**
3. `tests/unit/test_resume_router_report_dup.py::TestLostReportRecoveredExactlyOnce::test_recovery_is_exactly_once_across_sweep_passes` — **F-1 wedge family regression (passes at base)**
4. `tests/test_council_tools.py::TestSpawnCouncilor::test_non_team_member_agent_raises_value_error…` — identity-line parse artifact; test is canonical target
5. `tests/test_report_lane_phase2.py::TestErrorPropagation::test_error_flag_uses_fallback_when_message_missing` — **root-caused: F-1's intentional truthy-error gate (decisions §1) + §1a defensive WARNING observed firing at dependency_bus.py:693; the test encodes the PRE-fix contract (expects `_parent_errored` flip on error=None). Stale-test-contract drift — the sibling-drift hazard class. Also the sole delta in the ensure concurrency pack (98P→97P baseline).**
6. `tests/test_terminal_orphan_matrix.py::test_jobitem_task_status_matrix[pending-True-active]` — parse artifact noted
7-8. `tests/postgres/test_list_queues_with_admittable_work_pg.py` ×2 — `admission_state=active requires a job_locks row` (PG guard enforcement vs test fixture)

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
2. 🔴 **8 branch-caused test failures** — 2 F-1-family regressions (fm11_shield_gap, resume_router_report_dup), 1 stale-contract (report_lane_phase2 — update test to post-F-1 semantics), 2 PG job_locks fixture, 1 langgraph import (see uv-lock-check), 2 parse-artifact-adjacent. Dev-lane scope.
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
- G1: **FAIL** (8 branch-caused; known-4 confirmed unchanged; 283 pre-existing documented)
- G2: **PASS** (both packs green; count claim 56→70 refuted, actual 56)
- G3: **PASS** (keep-green chain + F-2 suite + mandatory real-seam integration green)
- G4: **FAIL** (main leg never-heal + intervention deadlock; controls b/c PASS; gaps disclosed)
- ensure.md Core: **FAIL** (R1 single test = branch-caused entry #5; R2/R3 green)
- **Overall: ❌ NOT READY — merge blocked pending the G4 coverage fix + the 8 branch-caused failures' remediation.**
