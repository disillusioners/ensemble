# Test Gate: WC-Wake Phase 2 — Report Integrity — feature/wc-wake-report-integrity @ f8c5ce8f

Date: 2026-08-31 (gate opened 2026-08-30)
Gate: Independent phase gate (P2) — report-integrity (completion-gate seam). Gate range **fe64ea06..f8c5ce8f** (6 commits: 16f3e563 Wave-1 instruments → d4642381 Wave-1 prompts → ee9a5196 B.S.1-i predicate → cde2f6a2 B.S.1-ii log → 26fe4d9f B.S.1-iii+B.S.2-8 dormant enforcement → f8c5ce8f W1/W2/S-doc follow-ups). Production tip f8c5ce8f; gate authored test-only commits on top (ddfc5fc6, f96a239f, 22a6df4b, 2134de9e, 8cd73e0d, d6c96cd6, 6b914035 — all `test:` prefix, daemon/ untouched, verified by every worker via merge-base).
P1 gate: PASSED 2026-08-30 (RESULTS/2026-08-30-wc-wake-phase1-gate.md) — not re-gated.
Shipping state gated: **BOTH flags OFF** (WC-wake ENSEMBLE_WC_WAKE_ENQUEUE + B-guard WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED).
Worker dispatches: 18 (≤3 concurrent — QueuePool cap held throughout).
Change set (recon): 51 files, +8040/−60 — daemon/ 11 (2 NEW modules: report_integrity_guard.py 889L, report_integrity_metrics.py 105L), tests/ 11 (10 new/modified files, 263 function-level tests), agents/ 25 prompt files (2 themes), docs/ 2, planning 2. Zero new pack scripts from dev → gate authored 6 packs (P1 pattern).

## VERDICT: ✅ PASS — ALL 8 VERIFICATION SCOPES GREEN; 1 mock-fidelity FINDING (important) caught and FIXED in-gate; CLEARED FOR MERGE (P1+P2, both flags OFF)

Merge may proceed with both flags OFF at merge per plan. Enforcement flip is operator-owned post-soak (checklist at bottom). The dual-OFF posture is proven behaviorally (scope 2) — not just byte-compat.

---

## Scope Decision

P2 touches the completion-gate seam (child_reports/observer/watchdog/guard/config/constants/manager-boot) but NOT the P1 wake-routing files (job_queue.py, instance_messaging.py, tools/instance.py, routers/messages.py, graph.py — recon-verified absent from range). Regression scoped to 13 packs: the completion/report/watchdog/reconciler/orphan/observer families + the 5 anchors (job_queue, core, api, concurrency, security). Skipped: tools_suite (tools/ untouched), claim_guard/job_queue_tools/injection/messaging-routing/stability-sw2 packs (no code overlap — covered at P1). Full suite NOT warranted: dual default-OFF flags + scoped seam; breadth nevertheless carried by core_unit (715 tests) + job_queue anchor (1569). WC-wake OFF = spot only (byte-compat re-proven via P1 pack at HEAD-vs-base).

## 1. Full regression — 13/13 packs GREEN, 0 new failures

| Pack | Result | Baseline delta |
|---|---|---|
| job_queue_unit_test | ✅ 1569P/0F/38S (34.6s) | **EXACT ANCHOR — zero drift** |
| core_unit_test | ✅ 715P/3F/30S (18.3s) | exact; 3F all quarantine-matched (agents_api ×2 + migration-cascade ×1) |
| api_unit_test | ✅ 213P/8S/0F (12.9s) | exact |
| instance_messaging_regression_test | ✅ 28P/0F (0.9s) | exact |
| concurrency_atomic_unit_test | ✅ 98P/74S/0F (7.6s) | exact — ensure Critical #2/#3 |
| completion_regression_test | ✅ 96P/37S/1-des (2.2s) | exact, deselect held |
| wedge_fix_suites_unit_test | ✅ 78P/0F (0.7s) | exact |
| turn_transitions_reconciler_unit_test | ✅ 50P/0F/1-des (1.5s) | +2 vs P1 48P = B.S.4 notice-turn bridge additions (26fe4d9f), attributed, pure additive; deselect held |
| reconciler_paused_race_unit_test | ✅ 8P/0F (0.8s) | exact |
| child_reports_unit_test | ✅ 47P/0F → **48P/0F @ d6c96cd6** (2.0s) | file grew IN PLACE 15→47 (32 P2-added tests, name-set arithmetic exact) +1 gate suppression test; 2 vacuous guards de-vacuated (see §7) |
| waiting_children_watchdog_unit_test | ✅ 49P/0F (1.4s) | +2 vs P1 47P = B.S.5 shared-cooldown (26fe4d9f), pure additive; pack header comment stale 47→49 (doc-only, non-blocking) |
| orphan_active_job_recovery_suites_unit_test | ✅ 41P/0F (1.1s) | exact |
| security_boundary_hygiene_suites_unit_test | ✅ 45P/0F (0.7s) | exact |

**Coverage-model lesson (recorded to KB):** packs register PATHS, not test lists — P2 grew 3 registered files in place (child_reports 15→47, watchdog 47→49, turn_reconciler +2). Every delta above was attributed to a specific branch commit via git; zero unexplained drift.

## 2. OFF means OFF (behavioral) — PROVEN (scope 2, the core shipping-posture proof)

Pack `ri_off_behavioral_probe_test` (gate-authored, commit 22a6df4b): drives the **REAL** async `JobFeedbackObserver._finalize_job` live completion path (pre-fetch → to_thread sync helper → post-commit enforcement call) with real file-backed SQLite repos, real WriteGuardSession, seeded incident shape (report_injections PENDING + terminal child + FIRED-unenqueued watcher):
- **One-log-line**: flag UNSET → exactly ONE `[ReportIntegrityGuard]` record (the declared-waiting violation WARNING at observer_finalize_job); zero predicate-FAILED/MALFORMED/enforcement lines.
- **Zero-writes**: full-table snapshot before/after byte-identical — no message_queue/Task/JobItem rows, seeded durable rows field-identical, `_B_NOTICE_LEDGER == {}` after site call AND direct enforcement call; enqueue spy (recorder, not raise-trap) await_count == 0.
- **Completes**: parent WAITING_CHILDREN → COMPLETED stamp lands (fail-OPEN semantics).
- **No-delay**: enforcement short-circuit sub-ms; scoped asyncio.wait_for spy — budget (5s) never awaited; flag-off branch returns before NOTICE_ENQUEUE_BUDGET.
- **Parity**: identical under unset and ="0" (parametrized).
- **S3 spot**: flag OFF leaves always-on instruments on — junk report gets pure-suffix `[REPORT SANITY: ...]` append + NR-3 counter +1 (B-flag gates ONLY (b) enforcement).
- **Teeth**: ON-contrast control in same pack — flag ON + same shape → 1 notice + ledger populated (assertions discriminate).
RESULT: PASS 4/4 (1.1s); combined run with dev repro 9/9 — no module-global pollution either direction.

**WC-wake OFF spot**: `wc_wake_off_bytecompat_probe_test` re-run at HEAD vs base 1f8f8ed4 — all 4 site-windows (HTTP, agent-tool, job_inject-WC, job_inject-IDLE) BYTE-IDENTICAL under both OFF windows, legacy job_inject error string verbatim. Worktree cleaned. RESULT: PASS (~7s).

## 3. Always-on instruments safety — PROVEN (scope 3)

Pack `ri_repair_instruments_unit_test` (commit f96a239f): repair surface 61P/0F + semantic verification of test_child_reports.py P2 additions:
- **W1 fixture VERIFIED** (from f8c5ce8f fix): history [user, assistant(content='', tool_calls=[bash]), final-text] → pre-flight asserts filtered_content_bearing == 1 (defect shape present) → marker MUST NOT fire (`_fire(...) is False`). Companion junk shape → marker PRESENT.
- Healthy reports: marker ABSENT, content UNCHANGED (`raw == "done"` exact-equality asserts), composes ONCE across prefix/concat.
- Excluded agents (wanderer/explorer/watcher — NR-2 constant incl. watcher via lift): marker absent.
- NR-3 counter: +1 on junk / repair-disabled / skip_repair (§6 placement BEFORE both short-circuits — all terminal completions count); 0 on tool-bearing / long history. **Negative-side caveat resolved in-gate**: mock-fidelity review found the 2 negative guards vacuous (missing `await` — coroutine never ran, assertion trivially true). Quick-fix d6c96cd6 added the awaits → both guards now genuinely execute NR-3 logic AND PASS → **production confirmed correct** (no finding against daemon code).
- Version seams: SANITY_FLAG_VERSION==1 pin, marker text byte-pin, exclusion-set constant pin. **Gate-found gap closed**: suppression-on-version-bump (rollback seam) had NO test → +14-line test added (f96a239f), green.

## 4. Predicate exactness (detection, log-only) — PROVEN (scope 4)

Pack `ri_guard_enforcement_unit_test` (commit 2134de9e): 72P/0F. Scope-4 checklist all VERIFIED with named tests:
- Primary signal: report_injections PENDING/DEFERRED + terminal child (completed/failed/error/terminated × parametrized) fires.
- Corroborating: dependency_watchers FIRED ∧ enqueued_at IS NULL fires; multiple accumulate.
- Dormant on healthy: no-rows, non-terminal child, pending/cancelled watcher → empty.
- **Durable-row reads**: real Session + repo methods (count_pending_for_parent / count_fired_unenqueued_for_parent) — NOT bus cache, NOT instances.status (docstring-pinned).
- **Same-tx inline (B.S.7)**: same-tx visibility test + observer gate-defer tests (early-bus-gate / in-session-bus-gate / in-session-tasks-gate defer → predicate skipped) — (b) evaluates only on both-counts-zero, LAST.
- **Content-blind (D2.18)**: notice never reads report content; guard predicate does not read sanity flag (split-versioning independence test).

## 5. Dormant enforcement correctness (flag ON in tests only) — PROVEN (scope 5)

Same pack + fail-open suite:
- **Fail-OPEN 4 playbooks**: predicate raises → COMPLETED proceeds + WARNING names exception; malformed result → proceeds (dropped); 5s budget timeout (enqueue sleeps 30s) → absorbed, completion stands (NOTICE_ENQUEUE_BUDGET_SECONDS==5.0 pinned); enqueue exception → absorbed, ledger NOT recorded. Never blocks — verified at root-path and observer-path.
- **Notice channel**: source `system:report-integrity-guard` (== REPORT_INTEGRITY_GUARD_NOTICE_SOURCE) via manager.enqueue_message, priority 0, metadata report_integrity_notice=true, child id + [REPORT SANITY: ...] citation when SANITY_FLAG_VERSION==1 (S1 gate), **NEVER inside [SYSTEM NOTE] frame**.
- **Dedupe**: same violation signature → 1 notice; new episode (different child) → re-notify; clean evaluation closes episode.
- **Kill-switch registry (B.S.8)**: env name exact-pin, default-0 binding, env flip reaches fresh Settings, resolver reads the constant (not a literal); **(a)-guard reserved-unused** (constant exists, ZERO config consumers, name appears only in constants.py — grep-proven).
- **Reserved-origin interplay CORRECT** (security pack + live check): `system:` is a reserved PREFIX member (constants.py:442, startswith match :481-482) → `system:report-integrity-guard` admitted; 17-member set UNCHANGED across range; single durable mint site (guard's enqueue, MessageQueue only, NO JobItem — JAFP-compliant); no other new source= mints in the P2 delta; pinned by test_source_reservation.py (in-pack green) + literal pins in 6 test files.

## 6. Prompt edits — PROVEN (scope 6)

Pack `ri_prompts_registry_unit_test` (commit 8cd73e0d): **49P / 0F / 17S of 66 nodes** (0.54s).
- 12 D2.10 parents × scrutiny guidance (canonical-home pinned), 11 D2.11 work-turn agents × opening-discipline cardinal, explorer exempt (absence-asserted), 4 grandfathered parents (blueprinter/devops/giter/jober) skip-with-reason, dynamic registry walk (29 agents) enforces non-empty-team_members ⇒ (d), dispatch-mirror scan extends to skills-template.
- **17S fully reconciled** = 13 no-team + 4 grandfathered, ALL from this file — matches the dev review's 243P/17S mystery exactly.
- **Cardinal-integrity audit 11/11 OK** (section-aware, HEAD vs d4642381~1): no duplicate numbers, no P2-introduced collisions; developer[v2] (d) EXTENDS existing Cardinal #4 in place (single #4 confirmed by diff); governor dual 1-4 sub-lists are byte-identical pre-P2 convention (naive-scan artifact, not duplication); per-section restart conventions honored (project-manager, worker).
- Guide conformance: docs/agent-prompt-writing-guide.md new "Report scrutiny" section is the canonical-home contract the suite enforces; mirrors are presence-based (consistent with C2-D2.14 text-presence scope).

## 7. Mock fidelity + red-green — 1 FINDING (fixed), teeth BINDING (scope 7)

- **Mock fidelity (12 files read fully)**: 0 internal mocks, 0 mocked-order claims (the single order claim matches production-guaranteed sequence bus→tasks→(b) verified in source), boundary-only stubs, flag/module-cache autouse hygiene in all flag-touching files, StaticPool honesty (same-tx test uses same session; gate probe upgraded to file-backed). Gate probe (test_ri_off_behavioral_probe.py) rated strongest-in-set (recorder-spy + ON-contrast anti-tautology design). **FINDING (important): 2 vacuous NR-3 negative guards** (missing `await`, test_child_reports.py:1618/:1637, from 16f3e563) — assertions could never fail. **Fixed in-gate** (d6c96cd6): awaits added → guards genuinely execute → PASS → production correct; no daemon finding. Post-fix: 11.5/12 files meet the full P1 bar outright, 0 outstanding violations.
- **Red-green (3 spots, worktree provenance via daemon.__file__, editable-install .pth hazard neutralized)**:
  1. W1 marker fixture @ 26fe4d9f (pre-fix): **RED** — exact assertion `assert True is False` @ :1750 ("W1 defect: marker fires on minimal-tool history that contains a real tool call — must not").
  2. (b) detection @ fe64ea06: **RED** — ModuleNotFoundError: report_integrity_guard (collection).
  3. (b) enforcement @ fe64ea06: **RED** — same (guard-attributed via sanctioned fallback test_notice_content_contract; primary repro RED was sibling-module import — both reported).
  Teeth verdict: **BINDING — no vacuous passes** (strongest class: assertion-level RED at pre-fix commit).

## 8. E2E capstone (43070f6f-class incident replay) — PROVEN BOTH STATES (scope 8)

Two-tier evidence:
- **Integration tier** — `ri_incident_repro_integration_test` (commit ddfc5fc6): dev repro 5/5 (1.27s). Hermetic-with-real-completion-path: real ChildReportsService + DependencyBus + predicate + WritePauseGuard; manager facade mocked (documented); OFF test proves exactly-1 violation WARNING + enqueue-not-called + ledger empty; ON test proves notice kwargs contract (source/metadata/child-id/marker/not-in-frame).
- **Real-engine tier** — `ri_e2e_capstone_real_engine_test` (commit 6b914035): **real InstanceManager (file-backed WAL SQLite) + real WorkerPool(1)/JobProcessor/JobQueueService/WorkResolverService + live completion entry** (_process_child_completion_db_sync via to_thread + real _dispatch_post_commit_side_effects), scripted-LLM one-seam. 2/2 PASS (2.39s):
  - OFF: guard WARNING fires at child_reports.root_completion (guard SAW it, log-only) + parent reaches COMPLETED (documented log-only semantics) + ZERO system:report-integrity-guard MessageQueue rows + ledger empty + marker rides junk envelope.
  - ON: parent COMPLETED→RUNNING revive via REAL enqueue_message → EXACTLY ONE real MessageQueue row (source pinned, report_integrity_notice=true, priority=0, child id + REPORT SANITY citation, NOT in [SYSTEM NOTE] frame) + ledger records episode.
  - Honest bar note: real-manager + live completion entry; the parent's notice-consumption graph turn is not driven (assertion target is the durable row). Dual-read gate noted: env ON alone does not defeat a config False bound at boot — see flip checklist step 2.

## ensure.md Validation Results

Core:
- **Critical 4/4**: ✅ #1 no regressions (13/13 scoped packs green, 0 new); ✅ #2 deadlock/concurrency (concurrency_atomic 98P/74S exact, 10 thread-identity tests ran green, no skip markers); ✅ #3 no sync DB on asyncio loop (same pack, thread-identity); ✅ #4 dev.sh `--timeout-graceful-shutdown 10` (grep: comment line 99 + live flag line 102).
- **Important 2/2**: ✅ #1 await-callers (8 call-sites of the 3 converted symbols all awaited; P2 added 0 new call-sites of them; the 1 NEW P2 async fn enforce_declared_waiting_violations has 3 call-sites, all awaited); ✅ #2 original deadlock scenario (concurrency pack).
- **Nice-to-have 1/1**: ✅ #7 no dead code (reserved-unused (a) constant + 4 W2 dead-site symmetry attaches ALL carry explanatory comments; 5/5 DOCUMENTED; categorization nuance: child_reports:~1155 site is actually a live stage-ii attach — documented either way).
- Release Gate: NOT TRIGGERED — phase gate behind dual default-OFF flags with behavioral OFF proof; consistent with P1 posture. Contradictions: NONE.

## Gate-authored artifacts (all committed, test-code only)

| Commit | Artifact |
|---|---|
| ddfc5fc6 | test/packs/ri_incident_repro_integration_test.sh |
| f96a239f | test/packs/ri_repair_instruments_unit_test.sh + 1 suppression test (scope-3(e) gap) |
| 22a6df4b | tests/integration/test_ri_off_behavioral_probe.py + test/packs/ri_off_behavioral_probe_test.sh |
| 2134de9e | test/packs/ri_guard_enforcement_unit_test.sh |
| 8cd73e0d | test/packs/ri_prompts_registry_unit_test.sh |
| d6c96cd6 | vacuous-guard await fix + ri_guard_enforcement pack echo safe-form |
| 6b914035 | test/packs/ri_e2e_capstone_real_engine_test.{py,sh} |

PACKS.md: 6 new pack rows + anchor/growth-row stamps (job_queue EXACT, child_reports, watchdog, turn_transitions, completion). QUARANTINE.md: no new rows (zero new flaky failures; the 2 vacuous tests were fixed, not quarantined).

## 🟠 Notes for leader (non-blocking)

1. **Flip checklist dependency (from b10 finding)**: the B-guard is a DUAL-READ gate — resolver cache AND ReportIntegrityConfig field, BOTH bind at boot from the same env var. A post-boot env change alone does NOT activate enforcement (config veto). The flip procedure MUST be: set env → RESTART daemon → verify boot log.
2. `waiting_children_watchdog_unit_test.sh` header comment says 47, file now 49 (doc-only drift).
3. Recon's "NEW test file" classification for test_child_reports.py / test_waiting_children_watchdog.py / test_turn_reconciler.py was in-place growth (file totals ≠ P2 deltas) — corrected during gate; KB gotcha recorded by a worker.
4. Pack RESULT-echo `set -e` flaw fixed in the one NEW gate pack that had it (ri_guard_enforcement); repo-legacy packs still carry the latent flaw (adjudication contract already bypasses it).

## Operator Flip Checklist (post-merge, after ≤2-week stage-ii log soak — carries into the final report)

1. **Soak**: with flags OFF, watch logs for `[ReportIntegrityGuard] declared-waiting violation` WARNING frequency (stage-ii log ships in this merge — the 2 LIVE sites: observer_finalize_job, child_reports.root_completion). False-fire rate decides.
2. **Flip (if soak clean, or immediately on any silent-death incident per D2.5-FLIP)**: set `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED=1` in the daemon environment → **RESTART the daemon** (dual-read: resolver cache + config both bind at boot; env-only change post-boot does NOT activate) → verify the one-shot `emit_report_integrity_b_guard_boot_log` INFO line at boot.
3. **Verify**: first genuine incident → parent gets the adjudication notice (source system:report-integrity-guard) instead of silent completion; healthy completions unaffected (fail-OPEN).
4. **Instant revert**: unset (or ="0") → restart → log-only posture restored (proven byte-identical behaviorally, scope 2; unset and "0" are parity-identical).
5. WC-wake flag ENSEMBLE_WC_WAKE_ENQUEUE stays OFF per its own ≤2wk soak decision (separate flip, unchanged from P1 handoff).

### Overall Status
- Regression ✅ 13/13 (anchor EXACT) · OFF-behavioral ✅ (1 log / 0 writes / 0 delay, teeth-proven) · instruments-safe ✅ (W1 fixture pinned; 1 gap + 2 vacuous guards caught & fixed in-gate) · predicate ✅ (exactness checklist 9/9) · enforcement ✅ (fail-OPEN 4 playbooks, reserved-origin CORRECT, kill-switch registry) · prompts ✅ (49P/0F/17S reconciled; cardinals 11/11) · fidelity+red-green ✅ (BINDING; 1 finding fixed) · E2E ✅ (real engine, both states)
- ensure.md Core 4/4 + Important 2/2 + Nice-to-have 1/1; Release Gate not triggered; contradictions NONE
- **Testing Complete: ✅ READY — P2 gate PASSED. giter may merge the full branch (P1+P2) with BOTH flags OFF. Final report carries the operator flip checklist above.**
