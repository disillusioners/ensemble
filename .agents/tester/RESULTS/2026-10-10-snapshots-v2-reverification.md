# Snapshots v2 Redesign — RE-VERIFICATION (fix round) — VERDICT: PASS-WITH-PREEXISTING

Date: 2026-10-10 · Branch `feature/snapshots-redesign-v2` @ `f19b4d705` (2 fix commits atop gate tip `e97d6c33c`: `95d57adb2` D1 gated document-level Esc capture-phase listener + `menusOpen` counter; `f19b4d705` D2 v1-e2e side-mode rewrite of steps 11a/11b).
Prior verdict: FAIL (narrow) — RESULTS/2026-10-10-snapshots-v2-merge-gate.md (D1 = AC-A11Y.3b second-Esc no-op; D2 = v1-e2e backdrop contract dead in side-mode).

**VERDICT: PASS-WITH-PREEXISTING** — D1 fixed and independently verified deterministic ×2; D2 validated to full daemon-free depth (daemon-lane run recorded PENDING, post-merge); jest failure surface identical to the base-attributed quarantined set, zero new failures. This verdict gates the giter merge.

Fix-diff fence check: `e97d6c33c..f19b4d705` = 6 files, +361/−46, ALL under `frontend/` (e2e/snapshots.spec.ts; snapshot-detail-drawer ts+spec; snapshots page ts+html+spec). Zero touches of `agents/`, `.agents/shared/planning/**`, `.agents/tester/**`. Claim matched exactly.

## 1. D1 leg (AC-A11Y.3b) — FIXED, deterministic ×2
Pack `snapshots_v2_webauto` run twice at `f19b4d705`: **10/10 PASS in BOTH runs** (69s / 71s, port 14199, `expected: 10, unexpected: 0, flaky: 0` both). D1 leg evidence, byte-identical both runs:
- metrics path: Esc#1 closes popover only, drawer still open, focus on `.metrics-pill` `inDrawer=false` (R3-2 invariant held) → Esc#2 → **drawer closed** ✓
- info path: same sequence → **drawer closed** ✓

## 2. R3-2 invariant — HELD (runtime + code review)
- Runtime: popover-Esc-only assertion (drawer still open after first Esc) passes inside the D1 leg, both runs, both paths.
- Code (95d57adb2 review): document-capture handler registered `snapshot-detail-drawer.component.ts:295`, torn down symmetrically via `destroyRef.onDestroy` `:296-298` (same capture flag — no leak; drawer mounts inside `@if(drawerOpen() && …)` so lifecycle pairs). `menusOpen` signal (`snapshots.component.ts:192`) wired to all four page-level triggers (info/metrics/status/sort), floored decrement guard `:198-201`, passed as drawer input, Gate 1 (`:176-178`) returns early while >0. Handler contains no stopPropagation/preventDefault — CDK overlay still receives Esc (no capture-swallow). Capture-phase choice verified load-bearing against Material 21.2.5 (element-scoped bubble-phase drawer listener, sidenav.mjs:206-215 — a bubble-phase document listener would race the post-close decrement).
- Dedup: Gate 2 (`:180-188`) skips inside-host targets; component HostListener (`:318-321`) fires only for through-host bubbling → mutually exclusive per keypress, exactly one `close.emit()`. Material's own drawer-element Esc closer is convergent (same end state; `closedStart`→`onCloseDrawer` idempotent), never double-emits the component output.

## 3. A11Y.3a Tab trap — UNPERTURBED
0 escapes in both runs, both drawers (success: 20 tabs, wrap proven, initial focus drawer-close; error: 12 tabs, retry/close cycle). The document-level listener did not perturb the trap.

## 4. Collateral sweep — CLEAN
SMOKE (gear-menu → `/snapshots`, 7-section order, v1+v2 hooks, toggle PUT round-trip), AC-6.3 (URL sync + back/forward re-seed + sort round-trip), AC-6.2 (two-region @1280 / single @1024) — PASS both runs. No collateral from the +361/−46 diff.

## 5. Full FE jest suite — NO NEW FAILURES
`fe_jest_full` @ `f19b4d705`: 112 suites / **3895 tests / 3891 PASS / 4 FAIL (24.2s)**; drift gate `f19b4d705` stable. All 4 failures byte-identical to the quarantined base-attributed set (jobs-grouping time-bomb ×3 — `Expected 'ago' / Received toLocaleDateString`; jobs-filter-state 9-vs-8 pin ×1). **No 5th failure, no altered signature.** +3 tests vs baseline (fix-commit spec additions), all passing.

## 6. D2 depth-check (daemon-free) — VALIDATED
- Rewrite read (`f19b4d705`): `.mat-drawer-backdrop` now appears exactly once in the whole v1 spec — as the NEW absence assertion `toHaveCount(0)` (spec:590). No visibility/click-to-close reliance remains.
  - Step 11a (spec:548): Esc from page-focus (clicked row, no pane focus) closes drawer + zero page errors (D1 leg); re-open → pane-focus Esc closes (inside-path regression leg).
  - Step 11b (spec:574): backdrop count 0; neutral `.page-title` click keeps drawer open; Esc (page focus) closes; `drawer-close` button closes; zero page errors. Selectors verified in source (`.page-title` html:9; `drawer-close` drawer html:52).
- Structural backing: Material 21.2.5 side-mode never renders the backdrop (`_drawerHasBackdrop → mode !== 'side'`, sidenav.mjs:718-721 + `@if(hasBackdrop)` :780) — `toHaveCount(0)` is correct.
- Collection: `playwright test --config playwright.snapshots.config.ts --list` → exit 0, **12 tests in 1 file**, rewritten 11a (:548) + 11b (:574) collected; abort-guards clean (no listeners on 18279/15532 before/after — zero daemon/PG contact).

## 7. Known-note continuity
`width: 576px;` (WITH space) confirmed at `snapshots.component.scss:771` (795-line file). Future greps must use the space variant.

## Pending items (post-merge, non-blocking)
1. **v1 daemon-lane e2e full run** (12 tests incl. rewritten 11a/11b) against the disposable-PG lane — the only unrun depth; recorded PENDING.
2. **Comment nit (non-blocking)**: step 11a's second-leg comment attributes the pane-focus close to the component HostListener; by DOM topology the pane (`mat-drawer`, parent of the app- host) is OUTSIDE `hostEl.contains(target)` → that leg actually closes via the document capture handler (+ Material's convergent listener). Assertions correct and regression-valid; only the mechanism attribution in the comment is imprecise. Optional one-line comment fix in a future `test(snapshots):` commit.
3. Standing repo debt (pre-existing, outside this gate): jobs-grouping time-bomb fixtures + jobs-filter-state pin (QUARANTINE.md rows carry fix recipes).
4. Worker-side cosmetic (no repo change): re-verify run-1 wrapper's `/bin/sh` PIPESTATUS echo — worker's own shell, not the pack script.

## Instance ledger (this round)
pack ×2 `ae71f029` (test-pack-execution) · jest `de63cf80` (test-pack-execution) · D2-depth review `1a2ffe79`. All evidence-only; worktree clean at `f19b4d705` throughout.

### Overall Status
- Web automation: ✅ PASS 10/10 ×2 (D1 resolved)
- Full FE jest: ✅ PASS-with-preexisting (3891/3895; 4 quarantined base-attributed)
- D2: ✅ validated daemon-free; daemon-lane run PENDING (post-merge)
- **Re-verification verdict: PASS-WITH-PREEXISTING — merge gate is GREEN for giter.**
