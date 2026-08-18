# Instances-State-Cache — RE-DRIVE (fix verification) after BUG1-4 fixes

Date: 2026-08-18 (second cycle) · Branch `feature/instances-state-cache` (uncommitted) · Workers: e935666e (build), 707d9ffd (jest), fbe83719 (all e2e) · Predecessor: `2026-08-18-instances-state-cache-feature-test.md`

## Re-drive verdicts (checklist vs result)

| # | Item | Verdict | Evidence |
|---|------|---------|----------|
| 1 | Draft round-trip | ✅ **FIXED** | `"e2e-draft-PERSIST"` survives Plan round-trip (assert + run-1 snapshot independently show it) |
| 1b | Scroll round-trip | ✅ **FIXED** (with honest correction) | Measurable after fixture fix (30×40px overflow, readiness gate): ≥90 preserved. NOTE: the pre-fix "scroll=0" datum was fixture-clamped (empty instance → no overflow) — the teardown bug was real (draft+marker proved it) but scroll-0 alone was never valid evidence. |
| 2 | A→B switch | ✅ **PASS** | Hold releases on switch: B renders B's data, A's draft gone (no bleed), same app-chat host node, B's own draft survives round-trip (re-hold works) |
| 3 | First-open lazy mount + race | ✅ **PASS** | Cold `/`: 0 app-chat → first open: 1 → round-trip: same host node (mount-once keep-alive). Navigate-away-during-load: no double-mount, no stuck loading, no errors |
| 4 | Build gate | ✅ **FIXED** | 5.82 MB = exact base parity (was 6.09 FAIL); exit 0; only known pre-existing warnings |
| 5 | Regression flows | ⚠️ **3/5 PASS, 1 REGRESSION (BUG5), 1 suppressed** | R6 ✅ (adapted: absent-OR-hidden semantics under lazy mount) · R2 ✅ · R4 ✅ · R5 ❌ → BUG5 · terminate: not re-run this cycle (serial abort after R5); pre-fix runtime proof stands but MUST be re-verified post-BUG5-fix |
| 6 | T4 re-triage | ✅ **RESOLVED (both ENVIRONMENT)** | auto-scroll-to-bottom: pinned 2026 fixture instance deleted from dev DB (`INSTANCE_NOT_FOUND`) — app correctly shows not-found UI; spec needs self-created fixtures. send-pause-button: T1-T3 PASS (new positive signal — spawn→SSE→send→pause flow works on fixed tree); T4 fail = daemon terminated the e2e-spawned instance mid-flow (disabled textarea is CORRECT UI for terminated). Zero app regressions. |
| 7 | Jest sanity | ✅ **PASS** | 2048/2048, 57 suites, 9.1s (+11 vs baseline; chat 86/86, app 19/19 confirmed) |

## NEW BUGS (this cycle — route to leader, NOT fixed)

### BUG5 🔴 CRITICAL — Lazy root-mount drops ALL component-scoped app-chat CSS
- **Runtime proof:** R5 `z-index` assert: chat computed `z-index = auto` (pre-fix: 90); workspace 100 intact.
- **Mechanism:** `app.scss` is component-scoped (`styleUrl`); `app-chat { z-index:90; position:absolute; inset:0; background; … }` (app.scss:221–228) compiles to `app-chat[_ngcontent-…]`. The OLD template-declared `<app-chat>` carried the scoping attribute; the NEW dynamically created host (`createComponent`, app.html:110 `#chatHost`) does not → rule never matches.
- **Blast radius:** entire overlay layout — z-index ladder (chat 90 < workspace 100) gone, absolute positioning/inset/background gone; stacking falls back to DOM order, exactly what app.scss:180–198 documents against.
- **Fix directions:** global `styles.scss` move / `::ng-deep` / inline styles in `lazyChatMountEffect` next to the display write / stamp scoping attribute on created host.
- **Why core pack missed it:** its asserts are visibility/identity-based, not computed-layout-based.

### BUG6 🟠 — 404-on-deep-link never clears the dead id from nav cache (user loop)
- **Repro:** deep-link `/projects/{pid}/instances/00000000-…` → not-found UI renders (correct, no errors) → click "Instances" nav → lands on not-found page AGAIN (loop).
- **Mechanism:** 404 handler (chat.component.ts:976–978) sets `instanceNotFound` without `viewState.clearInstance(id)`; only the polling validator (:521–528) clears, and it races the user's click.
- **Fix direction:** add `clearInstance(instanceId)` in the 404 error handler.
- **Evidence:** `frontend/test-results/instances-state-cache-lazy-071c7-…/error-context.md` (nav href retains dead id; nav click never reaches /instances in 10s).

## Follow-up required post-BUG5-fix
- Re-run `instances_state_e2e_regression` (terminate was suppressed; its pre-fix proof predates BUG5 code)
- Re-run `instances_state_e2e_core` R5-equivalent layout asserts implicitly covered by regression pack

## T4 legacy-spec debt (environment, not this feature)
- `auto-scroll-to-bottom.spec.ts` — pins deleted fixture ids (`e1c467e6-…` gone); needs self-created instance + seeded long message.
- `send-pause-button.spec.ts` T4 — e2e-spawned instances get terminated by daemon mid-flow; needs resilient fixture strategy. T1–T3 green.
- Pre-existing app polish (leader note): NG0100 `ExpressionChangedAfterItHasBeenCheckedError` from `InstanceListComponent` relative-time interpolation.

## Packs (this cycle)
- `frontend_prod_build` — ✅ PASS 5.82 MB
- `frontend_jest_regression` — ✅ PASS 2048/2048
- `instances_state_e2e_core` — ✅ PASS 4/4 (fixture-validity adaptation `41bdda48`)
- `instances_state_e2e_regression` — ❌ FAIL (BUG5; R6 adaptation `c5a31a06`)
- `instances_state_e2e_lazy` (NEW) — ❌ FAIL 3/4 (BUG6; A→B/mount-once/race all PASS) — `831a04eb`, `4ffe12f8`
- T4 specs run ad-hoc (ENVIRONMENT verdicts, no pack)

## Overall Verdict: **NOT READY** (2nd cycle)
- Original BUG1-3: **FIXED and runtime-verified**. BUG4: test-side resolved (confirmed by instances-project-tabs selector scoping).
- New: **BUG5 (critical — overlay layout/CSS loss)** and **BUG6 (nav-cache dead-id loop)** block ship.
- One more developer round on BUG5+BUG6 (both small, fix directions documented), then: re-run regression + lazy packs (terminate + R5 + 404-nav asserts are the acceptance).
