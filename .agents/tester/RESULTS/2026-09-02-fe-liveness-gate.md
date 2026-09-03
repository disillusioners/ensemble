# Test Report: FE Liveness — FULL gate incl. web automation
Date: 2026-09-02
Branch: `feature/job-queue-fe-liveness` @ `de493472` (branch tip under test; tester test-infra commits landed on top during the gate — final HEAD `f59b0916`, FE source identical to `de493472`)
Base: `latest` @ `89a082c2`
Instance IDs: recon `28cfa6ce` · jest `83bcd92d` · static `b4b55b03` · web-badge `a6aecf76` · web-chips `bfc8d344`

## FINAL VERDICT: ✅ PASS — merge-ready from the testing side, 0 branch-caused regressions, 1 non-blocking coverage GAP flagged (🟠)

### Summary
- **Static gate**: jest 66/66 suites, 2396/2396 tests, 0 failures (9.3s, `--no-cache`, rev-parse bracketed) · tsc exit 0 · build exit 0 (11.3s) · exactly 7 pre-existing SCSS budget warnings (byte-match baseline)
- **Web automation (real Chromium/Playwright)**: 4 cases EXERCISED green with screenshots; 5 cases NOT-REACHABLE (dev DB naturally idle) — all unit-verified with named `it()` cross-refs; 1 honest GAP (drawer-AMBER)
- **ensure.md (in-scope)**: PASS — changed packs 4/4 PASS + dev.sh static check PASS; Release Gate NOT warranted
- **Quick fixes applied**: 5 test-infra commits (packs + e2e specs only; zero FE-source/daemon changes)
- **Quarantined**: 0 new (known flake `instance.service.spec.ts` did not fire)

### Scope Decision
> Task-defined FULL FE gate (static + web automation) — honored in full. Backend packs (concurrency, BE unit/integration, pytest E2E Release Gate) NOT run: branch range contains **ZERO `daemon/` files** (asserted by recon: 23 `frontend/src/` + 1 `docs/job-task-system.md`, +1252/−59, 7 commits — brief said "8", off-by-one in brief). FE-only UI change → BE blast radius nil.

### 1. Static gate (independent re-run)

| Check | Result | Evidence |
|---|---|---|
| Full FE jest (`frontend_full_unit_test.sh`) | ✅ PASS — `Test Suites: 66 passed, 66 total` / `Tests: 2396 passed, 2396 total` / 9.323s, `--no-cache`, bracket `feature/job-queue-fe-liveness @ 4c17f442` before=after | worker `83bcd92d`; log `/tmp/fe_full_unit_run2.log` |
| `npx tsc --noEmit` | ✅ TSC_EXIT=0 | worker `b4b55b03`; `/tmp/fe_static_pack_run.log` |
| `npm run build` | ✅ BUILD_EXIT=0 (11.3s) | same |
| SCSS warnings | ✅ exactly **7 budget warnings**, byte-match to dev baseline (bundle initial 5.83 MB + 6 component SCSS budgets) | `/tmp/fe_static_build.log` |
| Zero daemon/ files in range | ✅ asserted (`git diff --name-only 89a082c2..de493472`) | worker `28cfa6ce` |

Count discrepancy resolved: brief's "+75 scoped" was dev's scoped-selection count, already inside the 2396 total (recon: net-new `it()` lines in range = 35; on-branch total across the 5 touched spec files = 292). Independent full-suite run matches dev's baseline exactly (66/2396).

### 2. Web automation case matrix (real browser, `:4199`, dev DB as-is — NO fabrication)

**Live-state inventory**: 0 non-terminal jobs; 0 live missions; 10 recent rows all `{completed, message, mission_liveness: completed}`. Endpoints (GET only via FE proxy): `/api/health`, `/api/jobs?status=queued,active`, `/api/jobs?status=completed,failed,cancelled,dead_letter&limit=10`.

#### Badge matrix (`fe_liveness_badge.spec.ts`, commit `72bc4914`)
| State | Disposition | Evidence |
|---|---|---|
| 1 — jobs present → X/Y + 'Live missions: N' tooltip | NOT-REACHABLE (0 non-terminal jobs) | unit-verified: job-queue-indicator spec — `"2/3"`/`"0/3"` its + `CASE C` (X/Y + tooltip) |
| 2 — 0 jobs + N missions → `missions: N` blue+pulse | NOT-REACHABLE (0 live missions; all mirrors settled) | unit-verified: `CASE A`, de-dup it, defensive ACTIVE-scan it |
| 3 — idle → muted `0/0` | **EXERCISED ✅** | `badge_state3_idle.png` — `.queue-count`=0/0, `idle` class, no pulse dot, hover tooltip `Running: 0 / Pending: 0`; vision-verified |

#### Chip matrix + panel smoke (`fe_liveness_chips.spec.ts`, commit `f59b0916`)
| Case | Disposition | Evidence |
|---|---|---|
| R1 — message row → receipt chip + mission chip w/ label | **EXERCISED ✅** | `chips_R1_receipt_mission.png` — `.receipt-chip`(`message`) + `.mission-chip`(`mission: completed`, `.mission-settled`) co-rendered |
| R2 — paused mission → AMBER (drawer hard-coded-blue fix) | NOT-REACHABLE (0 paused missions) + **GAP 🟠** | unit cluster-coverage only (job-card `CASE 1`, model `paused as live`); `job-detail-drawer.component.spec.ts` has ZERO mission/paused/amber its → fix has no dedicated coverage anywhere |
| R3 — mission-kind row / null liveness → NO chip | NOT-REACHABLE (0 such rows in dev DB) | unit-verified: `CASE 3`, `CASE 4`, `CASE 4b`, legacy-rows it |
| P1 — chip click inside panel row → row NOT selected/navigated | **EXERCISED ✅** | `chips_P1_click_before/after.png` — URL unchanged `/jobs`, panel menu stays open, stopPropagation holds (fix @ 985f86d2 live-verified) |
| P2 — Enter-key on chip → same | **EXERCISED ✅** | `chips_P2_enter_before/after.png` — real bubbling keydown on chip host exercises the `(keydown.enter)` stopPropagation binding; MatMenu/CDK focus-interception workaround documented in-spec |
| S1 — SSE in-flight settle patches chip w/o refetch (bonus) | NOT-REACHABLE (0 active jobs to observe settling) | unit-verified: jobs.component spec — `jobs[] path: settled mission_liveness overwrites live row`, `present-as-null: explicit null CLEARS, absent KEEPS` |

Boot/teardown: each wave self-managed BE (`bash dev.sh` bg, health ~2s, graceful teardown, 8079 FREE after); FE `:4199` = user's node PID 37364 reused, never restarted; **8088 never touched**; no orphan uvicorn; no processes killed beyond workers' own boots.

### 3. Adjudication (caused vs pre-existing)
| Observation | Verdict | Basis |
|---|---|---|
| jest 0 failures | — (nothing to adjudicate) | full suite green |
| Known flake `instance.service.spec.ts` (mergeInstances) | did not fire | passed in-suite; no quarantine action |
| 7 SCSS budget warnings | **pre-existing** (exact baseline match) | byte-identical to dev evidence |
| +3 non-budget lines in SCSS_WARNING_COUNT=10 (NG8113 unused-import ×1, sass deprecations ×2) | **pre-existing** (grep-scope artifact, not regressions) | emitting files untouched by branch; `git diff de493472..4c17f442 -- frontend/` empty |
| Wave-A run #1 vite-error-overlay intercept | environment artifact (user's ng serve HMR chrome), not product | probe confirmed transient; guard added to spec; run #2 clean |
| Wave-B P2 run #1 Enter failure | test-harness artifact (MatMenu trigger retained focus; CDK keyboard handler closed menu before chip) | product contract intact — fixed in-spec via dispatchEvent, documented |

### 4. ensure.md (in-scope) — PASS
- **Core Critical "No regressions in changed packs"**: ✅ — all 4 packs in the FE change set PASS (frontend_full_unit_test, fe_static_typecheck_build_test, fe_liveness_badge_e2e_test, fe_liveness_chips_e2e_test)
- **Core Critical "dev.sh --timeout-graceful-shutdown 10"**: ✅ static grep (dev.sh:102)
- Core concurrency/sync-DB items: BE packs — out of blast radius (zero daemon files), not run by design
- **Release Gate**: NOT warranted (FE-only UI feature; no cross-module/architecture change)
- No contradictions between ensure.md methods and pack rules this run; no Improvement Notices

### 5. Gaps & follow-ups
- 🟠 **GAP — drawer-AMBER fix uncovered**: the `job-detail-drawer` hard-coded-blue→AMBER fix (named in the task brief) has NO unit coverage (`job-detail-drawer.component.spec.ts` has zero mission/paused/amber/chip its) and was NOT browser-exercisable (no paused mission in dev DB; fabrication forbidden). Cluster semantics (paused∈live) and the shared color helper are unit-covered; the drawer render path is not. Suggested follow-up: add a drawer spec test asserting paused → `rgb(245, 158, 11)` (amber-500).
- 🟢 Badge states 1-2 + S1 SSE settle: unit-covered but not browser-exercised (DB naturally idle). If a future dev DB has live missions, re-run the two specs — skips will auto-resolve to exercised.
- 🟢 `fe_static_typecheck_build_test` grep pattern `WARNING|budget` sweeps in non-budget advisories (10 vs 7). Narrow in a future pass, or keep as adjudication data (current posture).

### 6. Test-infra commits added to branch (all test-code only, single-file staging, `.agents/` never staged)
| Commit | File | What |
|---|---|---|
| `004d479a` | test/packs/fe_static_typecheck_build_test.sh | NEW pack (recon) |
| `4c17f442` | test/packs/frontend_full_unit_test.sh | bracket + `--no-cache` + RESULT-echo fix |
| `064145ad` | test/packs/fe_static_typecheck_build_test.sh | drift semantics: branch-strict, SHA-as-data |
| `72bc4914` | frontend/e2e/fe_liveness_badge.spec.ts | NEW badge e2e |
| `f59b0916` | frontend/e2e/fe_liveness_chips.spec.ts | NEW chips/click e2e |

### Documentation Updated
- [x] PACKS.md — 2 rows updated (frontend_full_unit_test, fe_static_typecheck_build_test) + 2 new e2e rows
- [x] RESULTS/2026-09-02-fe-liveness-gate.md — this report
- [x] LESSONS/2026-09-02-fe-liveness-e2e-gotchas.md — vite-overlay guard, MatMenu Enter interception, SCSS grep-scope
- [x] QUARANTINE.md — no changes (0 new quarantines)
- [ ] rules/ensure.md — untouched (user-owned)

### Overall Status
- Static gate: ✅ PASS (jest 66/2396 · tsc 0 · build 0 · 7 pre-existing warnings)
- Web automation: ✅ PASS (4 exercised + 5 not-reachable/unit-verified + 1 GAP flagged)
- ensure.md in-scope: ✅ PASS (4/4 changed packs + static check; Release Gate N/A)
- **Testing Complete: ✅ READY** — recommend merge; address drawer-AMBER unit coverage as a follow-up (non-blocking)
