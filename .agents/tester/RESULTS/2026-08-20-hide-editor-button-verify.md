# Test Report: hide-editor-button fix (branch fix/hide-editor-button-keep-instance, uncommitted vs latest)

Date: 2026-08-20
Instance IDs: 61a816da (infra setup), e551075a (pack 1), 528bc136 (pack 2), 0675fbe4 (pack 3 + evidence + flaky/diag), e006c330 (pack 4)

## Summary
- Packs run: 4 (2 unit, 2 e2e). Unit: 161/161 PASS. Live symptom automation: 4/4 PASS.
- Regression e2e spec: functional behavior of ALL acceptance criteria verified PASS (incl. both NEW tests); the spec itself cannot go green due to 1 deterministic test bug (R4) + known environmental CSP noise (5 unfiltered hygiene asserts) — both test-side, neither fix-caused.
- Quick fixes applied: 0 to app source (forbidden by brief). Test infra committed: `10798324` (hide-button-symptom spec + pack script).
- Quarantined: 0 (R4 proven deterministic test bug, not flaky — quarantine anti-pattern per flaky-test-management).

## Scope Decision
Full requested scope accepted as-given (4 test-plan items); backend packs and Release Gate NOT run — change is frontend UI-only (4 files, no job/task/queue touchpoints), so full e2e convention (claim_pending_task etc.) not triggered per project note. No scope reduction applied; blast radius confirmed small.

## Per-Item Results (against the test plan)

### 1. Unit spec app.component.spec.ts — ✅ PASS
- Pack: `test/packs/app_component_unit_test.sh` (jest, scoped)
- **42 passed / 0 failed / 42 total** (1.5s) — independent verification matches developer's 42/42.
- Covers: hideActiveOverlay pure-toggle branches, anyOverlayVisible 4th term, affordance flip, onInstancesNavClick dead-click guard, syncDetailVisibility, reload-tabs regression controls.

### 2. E2E regression spec (first real execution) — ⚠️ functional PASS / spec cannot go green (test-side defects)
- Pack: `test/packs/instances_state_e2e_regression.sh` (serial workers=1). Full pack FAIL: serial abort after R6 hygiene fail.
- Scoped evidence runs (`-g` per test, pristine tree):
  - **R6**: functional PASS (cache survives reload, overlay stays hidden) / hygiene FAIL (CSP env)
  - **R4**: functional FAIL — **deterministic TEST BUG** (see Flaky/Diag section)
  - **N1 (NEW)**: functional PASS (all 9 asserts; nav-link re-show restores cached instance, URL unchanged) / hygiene FAIL (CSP env)
  - **Reload-while-hidden (NEW)**: functional PASS (all asserts; overlay re-opens with cached id) / hygiene FAIL (CSP env)
- R2/R5/Terminate: not executed this session (serial abort + time); R5/Terminate have their own filters and were green 2026-08-19.

### 3. Focused web automation of ORIGINAL symptom — ✅ PASS (4/4, 12.5s, screenshots)
- Pack: `test/packs/hide_button_symptom_e2e.sh` + new spec `frontend/e2e/hide-button-symptom.spec.ts` (commit `10798324`)
- **S1 BUTTON RE-SHOW (acceptance)**: PASS — hide → display none, URL unchanged, aria flips "Hide overlay"→"Show overlay" (icon visibility_off→visibility); 2nd click → display flex, SAME instance (6db07768…, Leader), full message fingerprint equality, aria back to "Hide overlay", localStorage activeInstanceId unchanged. Screenshots: s1-01/02/03.png.
- **S2 NAV-LINK RE-SHOW**: PASS — hide → click "Instances" nav → URL unchanged, overlay flex, identical instance + messages.
- **S3 ALT+` PARITY**: PASS — chat identity/messages unchanged; documented: hotkey gates on `activeProjectId !== 'all'` (app.ts:627), so on default `all` tab it is a no-op (no toggle) — parity contract held; note for future specs: pin non-`all` tab to prove hotkey toggling.
- **S4 CTRL-CLICK NATIVE NAV**: PASS — no intercept; popupOpened=false (headless chromium), URL unchanged, cached id intact (hidden-but-recoverable preserved).
- Affordance flip (Show overlay/visibility icon when recoverable): CONFIRMED end-to-end with screenshots.

### 4. Adjacent suites regression sanity — ✅ PASS
- Pack: `test/packs/adjacent_chat_unit_test.sh`
- instances-view-state.service.spec.ts 33/33 + chat.component.spec.ts 86/86 = **119/119 PASS** (~2.4s). Console noise = expected warn paths + Angular deprecation notices only.

## R4 Flaky-Budget + Differential Diagnostics (conflict resolution)
- Contradiction: pack3 R4 FAIL ×2 vs pack4 S1 PASS ×1, same tree/servers.
- **Retry budget (3×, no code change): 0P/3F — NOT flaky.** Identical failure each time (spec:323, expected "flex", received "none").
- Instrumented throwaway diag (deleted after): click-2 DOES flip display to flex at +307ms (reads: +191ms immediate = "none", +307ms waited = "flex"); button DOM node never replaced (handle attached throughout); aria-label tracks state correctly; activeInstanceId preserved; URL never navigates.
- **Root cause: R4 immediate-read races ~100ms Angular change-detection lag and deterministically loses.** S1 passes because it uses `await expect(async () => {...}).toPass({ timeout: 5000 })`. Rejected: flakiness (0 variance), button re-render (handle attached), context/tab difference (diag reproduced with S1's specific-card setup).
- **Quarantine: NO** — consistently-failing test is broken, not flaky (skill anti-pattern).

## ensure.md Validation (scoped)
- Core #1 (no regressions in changed packs): **PASS with caveats** — app_component PASS, adjacent PASS, symptom e2e PASS; regression e2e pack failures are proven test-side (1 test bug + env CSP noise), functional behavior independently verified PASS. No product regression found anywhere.
- Core #2/#3 (concurrency packs): N/A — backend untouched.
- Core #4 (dev.sh flag): untouched by change; not re-validated (no daemon change).
- Release Gate: not triggered (UI-only change).

## Follow-ups (test-side, recommended; NOT applied — spec file carries developer's uncommitted +225/-8)
1. **R4 race fix** (~4 lines, inside developer's hunk — recommend developer applies or authorizes after commit): replace immediate read at lines 318-323 with the `toPass({ timeout: 5000 })` pattern (verbatim template in LESSONS/2026-08-20-r4-immediate-read-race.md). R2/R6 share the same immediate-read pattern — apply there too.
2. **CSP hygiene filter** (clean hunk site: `page.on('console')` handler, spec lines 56-63, outside developer hunks): add `isFilteredNoise()` collection-time filter for `plane.ensem.dev | frame-ancestors` (working implementation exists at hide-button-symptom.spec.ts:98-105). Without it, R6/R2/R4/N1/Reload-while-hidden can never pass a serial run on dev :4199.
3. S3 note: to prove hotkey toggling in future, pin a non-`all` project tab first (gate at app.ts:627).

## Failures (verbatim)
1. R6 hygiene (full-pack run): `expect(consoleErrors).toEqual([])` — received 4× `[error] Framing 'https://plane.ensem.dev/' violates the following Content Security Policy directive: "frame-ancestors 'self' https://*.ensem.dev https://*.mtri.app http://localhost:8079 http://localhost:9797".` — ENV (dev :4199 not in allowlist).
2. R4 functional (spec:323): `Expected: "flex" / Received: "none"` — TEST BUG (immediate-read race, deterministic; see diagnostics).
3. N1 (:411) + Reload-while-hidden (:496) hygiene: same CSP class as #1 — ENV.

## Documentation Updated
- [x] PACKS.md — 4 pack rows registered + last-run statuses
- [x] LESSONS/2026-08-20-r4-immediate-read-race.md
- [x] RESULTS/2026-08-20-hide-editor-button-verify.md (this file)
- [x] QUARANTINE.md — unchanged (no flaky tests; R4 is deterministic test bug)

## Code Changes Summary
- frontend/e2e/hide-button-symptom.spec.ts (NEW) + test/packs/hide_button_symptom_e2e.sh (NEW) — commit `10798324`
- test/packs/app_component_unit_test.sh, test/packs/adjacent_chat_unit_test.sh (NEW, this commit session — see final report for hash)
- .agents/tester/PACKS.md, RESULTS/, LESSONS/ — documentation updates
- Developer's 4 files: UNTOUCHED (verification-only; working tree preserves their uncommitted diff exactly)

## Overall Status
- Unit Tests: ✅ PASS (161/161)
- Live symptom e2e: ✅ PASS (4/4)
- Regression e2e: ⚠️ functional PASS / pack cannot go green until 2 test-side follow-ups land (R4 race fix + CSP filter)
- ensure.md (scoped): ✅ PASS (no product regression)
- **VERDICT: fix verified GOOD — SHIP-ready from a product-behavior standpoint; spec-repair follow-ups recommended before merge to un-break the regression pack.**
