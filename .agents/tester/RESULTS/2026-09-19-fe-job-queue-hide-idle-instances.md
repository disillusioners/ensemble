# Test Report: FE job-queue "Live conversations" idle-instance hiding

Date: 2026-09-19
Branch/commit under test: `feature/job-queue-hide-idle-instances` @ `93bd86eb` (4 files, 386+/4−; production = pure helper `filterIdleInstanceRows` in `frontend/src/app/models/instance-node.model.ts` + single `instanceRoots` seam in `job-queue-indicator.component.ts`)
Worker instances: d92ea074 (infra), b2ce6d66 (focused jest), 027abadf (full suite), b0b4d0db (static), fc79636c (analysis), cdc756fc (e2e smoke), 4a91862a (quick fix)

## Summary
- Verdict: **READY — all four plan sections PASS**; 0 new regressions vs base; 1 low-risk coverage gap found and closed test-only (`f4ae1b9f`)
- Focused specs: 204/204 @ 93bd86eb (→ 206 incl. B4 pin after `f4ae1b9f`, model spec 72/72 verified)
- Full FE suite: 3222 passed / 3 failed of 3225 @ 93bd86eb — the 3 failures are EXACTLY the pre-existing `jobs-grouping.model.spec.ts` relative-time set (base 1042de8c); **NEW failures: 0**
- Static: tsc 0 errors; `ng build` exit 0; warnings 10/10 = baseline (verbatim set matched)
- UI smoke (mock BE :10080 + real `ng serve` :4199 + Playwright): MIXED 11/11, ALLIDLE 4/4, 0 uncaught pageerrors
- Quick fixes applied: 1 (test-only, +43 lines, single file, commit `f4ae1b9f`)
- Quarantined: 0 new (3 pre-existing jobs-grouping failures remain the known baseline set)

## Scope Decision
> FE-only change (4 files, single module pair: instance-node model + job-queue indicator; no daemon/ code) → FE-only verification: focused feature specs, full FE jest suite, FE static (tsc+build), source analysis, mock-backed browser smoke. No BE packs run — no daemon file in the change set. Full BE suite not warranted.

## ensure.md Validation Results (blast-radius scoped)
- **Critical**: "No regressions in changed packs" — **PASS** (all FE packs green; full-suite pack PASS-with-baseline-note: the 3 pre-existing jobs-grouping failures are outside the change set and constitute the documented base baseline — same class as QUARANTINE.md pre-existing entries; not newly quarantinable from this FE-only gate since they pre-date the branch)
- Out of scope for this change set (BE/daemon requirements, untouched by an FE-only branch): concurrency_atomic_unit_test, sync-DB-calls coverage, dev.sh flag check
- No contradictions between ensure.md methods and pack rules in this gate (FE packs used throughout; no bare/unbounded commands)

## Per-Plan-Section Verdicts

### 1. Static / verified baseline — PASS
- Focused jest (`(instance-node.model|job-queue-indicator)`, jest 30): **204/204, 0 failed**, exactly the 2 expected suites, 1.05s. Matches developer claim.
- Full FE suite (tracked `test/packs/frontend_full_unit_test.sh`, EXPECTED_BRANCH override, dual-layer timeout): 91 suites, **3222P/3F/3225** in 12.1s. Failure set = exactly `jobs-grouping.model.spec.ts` ×3 (`groupMetaLine … 'ago'` :497, `groupHeaderTitle … 'ago'` :559, `… timeAgo-only` :578 — formatter emits absolute date `"9/10/2026"` instead of relative time). Identical to base-1042de8c baseline claim; **NEW failures: 0**. Script's literal `RESULT: FAIL` reflects jest's nonzero exit only — adjudicated PASS-with-baseline-note.
- `npx tsc --noEmit -p tsconfig.app.json`: **0 errors**. `npm run build`: **exit 0**, **10 warnings = exact baseline set** (verbatim list captured; delta 0). No SHA drift mid-run.

### 2. Mock fidelity (TrueAuto) — PASS (no drift)
- Real wire: `GET /api/instances?…` returns wrapped `InstanceListResponse {instances,total,limit,offset,has_more}`; component unwraps `.instances` (poll leg `:719`, panel-open leg `:929`). Both spec legs inject via the same real-typed `InstanceRow` fixture factory (all 15 fields); statuses used exist in the 10-value BE enum.
- Verdicts: poll leg FIDELITY-OK; panel-open leg FIDELITY-OK; model-spec fixtures FIDELITY-OK (exercises full 10-value status space).
- Non-blocking notes: service-spec mock envelope omits unread `limit`/`offset`; FE `InstanceListResponse` omits BE `truncated` (never read); legacy `InstanceStatus` type lacks `'waiting'` (documented in docblock; consuming path uses `InstanceNodeStatus`) — all informational, no consumed-path drift.

### 3. Edge cases — PASS (7/7 matrix; 1 gap found → closed)
| Case | Verdict |
|---|---|
| Idle ROOT hidden | COVERED + CODE-CONFIRMED (filter `rows.filter(r => r.status !== 'idle')` before root loop; spec :929 + seam :3099) |
| Idle CHILD hidden | COVERED + CODE-CONFIRMED (spec :938) |
| Live child under idle parent → orphan-promoted root | COVERED + CODE-CONFIRMED (`parentId` miss → `rootNodes.push`; specs :949, :1044) + DOM-verified (`aria-level=1`) |
| Receipts under idle instance surface via queued/recentFlat | CODE-CONFIRMED (routing status-blind: `mission_id ?? instance_id` miss → queued/recentFlat). Composition was UNPINNED → **closed by `f4ae1b9f`** (2 tests, 72/72) + DOM-verified (Z4 receipt in RECENT) |
| All-idle → graceful empty state | COVERED + CODE-CONFIRMED (spec :916, :1016; four empty buckets, early-return) + DOM-verified ("No jobs" / "Queue is currently idle") |
| Terminal statuses visible | COVERED + CODE-CONFIRMED (spec :895, :975; `isTerminalInstanceStatus` → recentRoots) + DOM-verified (DONE-ROOT/ERR-ROOT in RECENT) |
| Non-idle non-terminal visible (running/waiting/paused/queued/waiting_children) | COVERED + CODE-CONFIRMED (spec :895; seam tests use running+waiting) |

Seam integrity: single `instanceRoots` computed (`:339-353`) funnels BOTH legs; production pin asserts `filterIdleInstanceRows(source)` present AND `callSites.length === 1` — no unfiltered call site can slip in. Receipts NOT filtered (jobs arrive as independent arrays; routing instance-status-blind). Minor comment nits: component-spec cross-references to the identity-grep pin point at the model spec; one regex pin matches via docblock (non-load-bearing).

### 4. Web/UI smoke — PASS (live DOM; fallback not needed)
- Setup: mock BE (Python stdlib) :10080 serving real-wire-shaped JSON for the indicator's forkJoin legs; real FE `ng serve --proxy-config proxy.conf.mock.json` :4199 (untracked proxy conf — zero tracked-file edits; real daemon NOT booted: 8079 left down, avoiding any prod-PG risk; prod daemon 9797 untouched).
- MIXED dataset 11/11: IDLE-ROOT & IDLE-CHILD absent panel-wide; RUN-ROOT visible (aria-level=1); ORPHAN-CHILD (waiting_children under hidden idle root) promoted to root (aria-level=1); DONE-ROOT & ERR-ROOT visible in RECENT; receipts render incl. MOCK-RCPT-IDLEPARENT Z4 (bound to the hidden idle root) surfaced via recentFlat.
- ALLIDLE dataset 4/4: clean empty state, no MOCK row leaks, app responsive (dataset switched by mock-only restart; ≤2 poll ticks).
- Console: 0 uncaught pageerrors both phases; expected 404 noise only (notifications SSE, /api/projects, /api/settings/*, /api/health, /api/agents, /api/migration/availability).
- Evidence: `test/packs-fe-smoke-mock/evidence/` (2 screenshots + 2 panel-text snapshots); drivers kept for re-run. Cleanup verified: mock + ng serve killed by recorded PID, ports 10080/4199 free.

## Quick Fixes Applied
- 4a91862a (quick-fix skill): B4 composition gap in `frontend/src/app/models/instance-node.model.spec.ts`
  - Root cause: both routing halves individually pinned, but no test composed idle-filtered rows + job bound to the idle instance → queued/recentFlat (docblock promise at instance-node.model.ts:185-194 unpinned)
  - Fix: +2 tests (+43 lines) reusing `mkRow`/`createMockJob`/production call order; assert bucket membership AND idle-root absence
  - Verification: model spec 70→72 all-pass; focused pattern total now 206
  - Commit: `f4ae1b9f` (single file proven via `git show --stat`; selective `git add`, foreign artifacts untouched)

## Failures (all pre-existing, outside change set)
- `jobs-grouping.model.spec.ts:497/:559/:578` — groupMetaLine/groupHeaderTitle emit absolute date (`"9/10/2026"`) not relative ("…ago"); identical set on base 1042de8c. Not this branch's regression; candidate for existing test-debt follow-up.

## Gaps
- None open. (B4 gap closed by f4ae1b9f; smoke fallback not needed.)

## Action Needed
- [ ] (optional, test-debt) jobs-grouping relative-time formatter contract — pre-existing, route to FE owner
- [ ] (optional) FE pack consolidation: untracked `fe_unit_full_test.sh` (2026-09-16) vs tracked `frontend_full_unit_test.sh` near-duplicates — giter to land/discard

## Documentation Updated
- [x] PACKS.md — FE gate section (4 pack registrations + integrity note: FE packs were missing from main tables)
- [x] MOCK_TESTS.md — smoke spec + last-run PASS
- [x] LESSONS/2026-09-19-fe-idle-hide-b4-composition-pin.md — quick fix record
- [x] RESULTS/2026-09-19-fe-job-queue-hide-idle-instances.md — this report
- [ ] rules/ensure.md — untouched (user-owned)

## Code Changes Summary
- `frontend/src/app/models/instance-node.model.spec.ts` (+43, test-only) — commit `f4ae1b9f` on feature/job-queue-hide-idle-instances
- Zero production changes by testing; zero tracked-file edits outside that spec; untracked smoke assets left for giter (frontend/proxy.conf.mock.json, test/packs-fe-smoke-mock/**)

## Overall Status
- Focused specs: ✅ PASS (204/204 @ 93bd86eb; 206 incl. pin)
- Full FE suite: ✅ PASS-with-baseline-note (0 NEW failures)
- Static (tsc+build): ✅ PASS (10/10 baseline warnings)
- Mock fidelity: ✅ PASS (no drift)
- Edge cases: ✅ PASS (7/7; gap closed)
- UI smoke: ✅ PASS (11/11 + 4/4, live DOM)
- **Testing Complete: ✅ READY**
