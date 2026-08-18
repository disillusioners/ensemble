# Instances-State-Cache — SHIP CLOSURE (3rd cycle, final verification)

Date: 2026-08-18 (cycle 3) · Branch `feature/instances-state-cache` (uncommitted) · Worker: fbe83719 (all packs) · Predecessors: `2026-08-18-instances-state-cache-feature-test.md` (cycle 1), `2026-08-18-instances-state-cache-redrive.md` (cycle 2)

## Fixes verified this cycle
- **BUG5 (scoped CSS loss):** app-chat rule moved to GLOBAL `styles.scss:59-70` → VCR-created host now matches. **Runtime proof: computed z-index chat=90 (was `auto`), workspace=100, ladder + overlap geometry restored.**
- **BUG6 (404 nav-cache loop):** `clearInstance(deadId)` in confirmed-404 branch (chat.component.ts:976-981) → **Runtime proof: not-found renders → "Instances" click lands on `/instances` list (no loop); nav cache no longer holds dead id.** 500/no-clear contract untouched.

## Final pack matrix (this cycle)

| Pack | Result | Key evidence |
|------|--------|--------------|
| instances_state_e2e_regression (ACCEPTANCE 1) | ✅ **PASS 5/5** (14s) | R5: chat z=90 / workspace 100 · terminate re-verified post-BUG5: dialog → localStorage cleared → nav `/instances` · R6/R2/R4 green |
| instances_state_e2e_lazy (ACCEPTANCE 2) | ✅ **PASS 4/4** (18s) | 404 flow green (list landing, cache cleared) · A→B hold-release · mount-once keep-alive · navigate-away race |
| instances_state_e2e_core (SANITY) | ✅ **PASS 4/4** (11s) | Draft + scroll round-trip · same-node identity · SSE chat-scoped net=0 · zero console errors · no CSS-move side effects (geometry proven via R5 ladder + laid-out overflow in scroll test) |

Spec-drift adaptations (test-code only): `75a9ee13` (severed-SSE console filter, pre-classified noise, rationale in-spec), `42a2472a` (waitForURL pathname semantics). **New bugs: none.**

## Full-arc bug ledger (3 cycles)
| Bug | Cycle found | Cycle fixed | Verified |
|-----|-------------|-------------|----------|
| BUG1 draft lost | 1 | 2 | ✅ cycle 2 |
| BUG2 scroll lost | 1 | 2 | ✅ cycle 2 (fixture-clamp correction applied) |
| BUG3 build 6.09 MB > 6 MB | 1 | 2 (lazy mount) | ✅ cycle 2, 5.82 MB |
| BUG4 duplicate tab-bar | 1 | test-side | ✅ cycle 2 (selectors scoped) |
| BUG5 scoped-CSS loss on dynamic host | 2 | 3 (global styles) | ✅ cycle 3 |
| BUG6 404 nav-cache loop | 2 | 3 (clearInstance) | ✅ cycle 3 |

## Final gate state
- Jest full: **2048/2048** (57 suites) · Build: **PASS 5.82 MB** (base parity) · Core e2e: **4/4** · Regression e2e: **5/5** · Lazy e2e: **4/4** · T4: environmental (no app regressions) · ensure.md in-scope: PASS
- All 13 feature e2e tests green across 3 packs; 9 spec-evolution + 2 closure commits on the branch (all test artifacts).

## Residual (non-blocking, documented)
- Legacy spec fixture debt: `auto-scroll-to-bottom` (pinned deleted instance ids), `send-pause-button` T4 (daemon-terminated fixture mid-flow) — environment, needs fixture modernization.
- Pre-existing app polish: NG0100 relative-time interpolation (InstanceListComponent); Sass `lighten()` deprecation; component CSS budget warnings.
- Severed-SSE console pair on terminate: classified expected (logged-and-handled); spec filters with rationale; optional app polish = disconnect() before delete.
- E2E fixture leftover projects in dev DB (cleanup API 401-class; acceptable for dev).

## VERDICT: **SHIP** ✅
Both closure fixes runtime-proven against my stated acceptance criteria; zero new bugs; all feature packs green. Git finalize (leader's scope) may proceed.
