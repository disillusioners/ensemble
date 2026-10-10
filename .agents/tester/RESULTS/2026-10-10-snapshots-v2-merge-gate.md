# Snapshots v2 Redesign — Final Merge Gate (TEST ROUND)

Date: 2026-10-10
Branch: `feature/snapshots-redesign-v2` @ product tip `a05d4ae9d` (+ harness commit `5d411a395`)
Base: `8c1534f9a` — 11 product commits, FE-only (11 files, +3525/−1180, zero daemon-side; recon-verified `git diff --stat` + porcelain clean at dispatch)
Commission: full FE jest suite + focused real-browser web automation (9 legs + backdrop spot-check); conformance pre-approved (pin `53fa39ec6d…`, 34 code-verifiable PASS, browser legs deferred to this gate).

**VERDICT: FAIL (narrow)** — 1 of 10 browser legs red (AC-A11Y.3b, deterministic product defect / AC-vs-design conflict, defect D1) + one contract flag (D2, v1-e2e backdrop reliance). Everything else green; jest REDs are all base-attributed pre-existing.

Worker instance ledger: recon `ccdf4b97`; jest pack `fb11b61d`; base-proof `40692acd` (errored in 14:10Z LLM rate-limit window → revived ×1 → completed); harness `2c43600a` (wedged in same window mid-task, artifacts on disk → terminated → re-dispatched once) → replacement `ac81bc17` (completed, commit `5d411a395`); official confirmation run `948f8d8b` (skill: test-pack-execution). One-re-dispatch policy held; no evidence gaps.

---

## 1. Full FE jest suite — pack `fe_jest_full`

- Invocation: `EXPECTED_BRANCH=feature/snapshots-redesign-v2 timeout 300 bash test/packs/fe_unit_full_test.sh` (internal 240s timer untouched; stage-0 drift gate: no mid-run SHA change, `a05d4ae9d` stable).
- Raw result: **FAIL (exit 1)** — 112 suites / **3892 tests / 3888 passed / 4 failed**; jest 27.1s (pack wall ≪ 300s cap).
- **Adjudicated: PASS-WITH-PREEXISTING.** A/B base proof: scratch worktree at base `8c1534f9a` (detached, node_modules symlink; `package.json`/`package-lock.json` diff vs base = EMPTY), isolated run of the two failing suites → **IDENTICAL FAILING SET (4/4), node-for-node string-identical failure headers and received values.** Scratch worktree fully removed post-proof (`worktree list` clean).

| Test (file) | Branch | Base | Family / root cause | Disposition |
|---|---|---|---|---|
| `jobs-grouping.model.spec.ts` › groupMetaLine "renders agent + N jobs + ago when all three segments populated" | FAIL ("leader · 5 jobs · 9/10/2026", expected 'ago') | FAIL (identical) | TIME-BOMB — fixtures hard-code `2026-09-10`; `defaultGroupTimeAgo` (jobs-grouping.model.ts:293-298) falls back to `toLocaleDateString()` when age ≥ 7d → fails on ANY branch after ~2026-09-17 | pre-existing, base-attributed |
| `jobs-grouping.model.spec.ts` › groupHeaderTitle "renders \"agent · timeAgo\" when both populated" | FAIL | FAIL (identical) | same time-bomb | pre-existing, base-attributed |
| `jobs-grouping.model.spec.ts` › groupHeaderTitle "falls back to timeAgo-only when no agent" | FAIL | FAIL (identical) | same time-bomb | pre-existing, base-attributed |
| `jobs-filter-state.model.spec.ts` › "canonical status/source tables cover the model unions (exhaustiveness guard compiles)" | FAIL (9 values vs 8-value pin; extra `"completed (gate escalated — unverified)"`) | FAIL (identical) | SPEC-PIN DRIFT — 9th `JobStatus` (job.model.ts:20, present at base) never added to spec pin; `JOB_STATUS_VALUES` legitimately carries it (jobs-filter-state.model.ts:44) | pre-existing, base-attributed |

- Quarantine: 2 rows added to `QUARANTINE.md` (grouping time-bomb ×3; filter-state pin drift ×1) — they no longer count against future full-suite runs.
- Branch's own suites (`snapshots.component.spec.ts`, `snapshot-detail-drawer.component.spec.ts`, `snapshots-table.component.spec.ts` — the prior 65/65 targeted set) all PASS inside the full run.
- Zero jest-side regression from the v2 diff.

## 2. Web automation — pack `snapshots_v2_webauto` (REAL chromium; `ng serve` + `page.route` stubs; NO daemon/DB/uv-sync)

Harness commit `5d411a395`: `frontend/e2e/snapshots-v2-gate.spec.ts` (821L), `frontend/playwright.snapshots-v2stub.config.ts` (71L), `test/packs/snapshots_v2_webauto_test.sh` (125L, executable). Port 14199 (auto-picked 14199..14210, occupants never killed); dual-layer timeouts (280s playwright + 320s ceiling internal, 300s outer); catch-all `**/api/**` → 200 {} — zero backend leakage (only known boot polls `/api/settings/editor`, `/api/queues/defer-blocked` hit the catch-all, by design).

Executed twice — smoke run + independent official confirmation run — **both 74s, identical per-leg results → DETERMINISTIC.**

| # | Leg | Result | Evidence (measured) |
|---|---|---|---|
| 1 | [SMOKE] gear-nav → table → 7 drawer sections → v1/v2 hooks → toggle R/W | ✅ PASS | headings exactly `["Task summary","Git anchor","Runtime / Model","Supersedes chain","Tags","Timestamps","Context"]`; `.mat-drawer-backdrop` count=0; PUT#1 `{"enabled":false}` → UI OFF, PUT#2 `{"enabled":true}` → UI ON (2-click dirty→save protocol; method+payload asserted) |
| 2 | [AC-2.4] chrome ≤ 200px @1280×900 | ✅ PASS | control 56 + filter 52 + stats 36 = **144** ≤ 200 (claim 147 incl. 3px borders — within rounding tolerance) |
| 3 | [AC-3.5] table scrolls, page chrome doesn't, paginator pinned | ✅ PASS | `.table-scroll` scrollHeight 2258 > clientHeight 644; scrollTop set to 300; document scrollHeight 900 = innerHeight, page scrollTop 0; paginator bbox bottom 900 = viewport 900 (delta 0) — 60 stubbed rows, pageSize 50 |
| 4 | [AC-4.5] drawer header sticky during body scroll | ✅ PASS | `.drawer-body` scrollTop 189 after scroll-to-bottom; header y 200 → 200 (delta 0) |
| 5 | [AC-6.1] status chips icons + running spinner + reduced-motion | ✅ PASS | icons exact: check_circle / autorenew / history / error / warning; `.status-running .status-icon` animation-name `…_spin`; `reducedMotion:'reduce'` → animationName `none`, duration 0s |
| 6 | [AC-6.2] 2-region ≥1280 / single-column below | ✅ PASS | @1280: `.drawer-grid` display `grid`, cols `240px 216px`, rail visible; @1024: grid `flex`, rail `none` |
| 7 | [AC-6.3] filters ↔ URL queryParams + back/forward re-seed | ✅ PASS | URL `status=failed` + captured wire request `…?status=failed&limit=25&offset=0`; active-count "1 filter"; goBack re-seeds prior state; goForward re-seeds filtered state; `sort=title_asc` round-trips; default sort removes param |
| 8 | [AC-A11Y.3a] Tab trap — success drawer | ✅ PASS | initial focus `drawer-close` (cdkFocusInitial); 20 Tabs, 0 escapes outside drawer; visited {digest-copy, drawer-copy-id, drawer-close, drawer-predecessor, drawer-digest-toggle}; drawer-close visited 4× (wrap proof) |
| 9 | [AC-A11Y.3a] Tab trap — error drawer (detail 500) | ✅ PASS | visited {drawer-retry, drawer-close} ×6; 0 escapes |
| 10 | [AC-A11Y.3b] Esc closes popover only; **second Esc closes drawer** | ❌ **FAIL** | see defect D1; `spec.ts:798` `expect(drawerPanel).toBeHidden()` → Expected hidden, Received visible, 23 polls × 10s; drawer retains `mat-drawer-opened` |

Evidence dir: `/tmp/snapv2-gate-evidence/` (`run-output.log`, `results.json`, `artifacts/…/test-failed-1.png`, `error-context.md`).

## 3. Backdrop ruling — REGRESSION FLAG (D2)

- **v1 e2e DOES rely on backdrop-click-to-close**: `frontend/e2e/snapshots.spec.ts:565-588` (step 11b) asserts `.mat-drawer-backdrop` `toBeVisible()` then clicks `[data-test="drawer-backdrop"]` expecting the drawer to close. Sole e2e reliance found (jobs/schedules pages use their own over-mode drawers — unrelated).
- **v2 renders no backdrop at all**: drawer is `mode="side"`; Material's `hasBackdrop` getter resolves false for side mode (`@angular/material` sidenav: `_drawerHasBackdrop → drawer.mode !== 'side'`), so the backdrop element is never in the DOM — recon source-proof + live DOM count=0 (legs 1/2) + neutral page click leaves the drawer open.
- **Ruling per commission rule ("if yes, flag the regression")**: FLAGGED. Step 11b is structurally dead against v2 and will redden the v1 daemon-lane e2e on its next run. The `data-test="drawer-backdrop"` hook on the container was deliberately preserved for the v1 contract (template comment), but the *behavior* the v1 spec asserts cannot exist in side mode.
- Routing options (leader's call): (a) update v1 spec step 11b to side-mode assertions (test-debt fix, recommended — matches v2 design intent), or (b) restore over-mode/backdrop semantics (contradicts v2 design). Related: step 11a's panel-focus-then-Esc recipe needs one deliberate look when the v1 lane is next touched.

## 4. v1-contract runtime checklist (all ✓ via live DOM)

- v1 hooks present: `metrics-capture-card` ✓ · `drawer-backdrop` attr on `mat-drawer-container` ✓ · `snapshot-row` ×60 ✓ · `paginator` ✓ · `paginator-page-1` (programmatic stamp) ✓
- v2 selectors present: `filter-clear` ✓ · `filter-active-count` ✓ · `snapshot-row` ✓ · `drawer-predecessor` ✓ · `drawer-digest-toggle` ✓
- Gear-menu: `menu-snapshots` entry intact and navigates to `/snapshots` ✓ · drawer 7-section DOM order ✓
- Toggle R/W: `PUT /api/settings/snapshot-create` with `{"enabled": <boolean>}` asserted in both directions ✓

## 5. Defects found

**D1 — 🟠 important (a11y interaction / AC-vs-design conflict).** AC-A11Y.3b "Esc again closes the drawer" fails on BOTH popover paths (metrics + info).
Repro (deterministic, 2/2 runs): open drawer → click `.metrics-pill` (page-level trigger, outside drawer host) → popover opens → Esc #1 → popover closes, focus restores to `.metrics-pill` (`inDrawer=false`), drawer stays open ✓ → Esc #2 → **nothing happens**; drawer keeps `mat-drawer-opened`.
Root cause: R3-2 component-scoped `@HostListener('keydown.escape')` on the drawer host (`snapshot-detail-drawer.component.ts:228-233`) — Esc pressed while focus sits on a page-level popover trigger never enters the drawer subtree, so the listener cannot fire. The product's own unit spec (k.4) pins this as INTENTIONAL ("Esc outside the drawer subtree does NOT close the drawer; inside does"). The commission AC as written expects close regardless of focus.
NOT fixed (product frozen). Routing options: (a) widen R3-2 — document-level Esc listener gated on `drawerOpen()` state; (b) re-scope AC-A11Y.3b to "Esc closes the drawer when focus is inside the drawer" and accept as-built (harness already covers the scoped behavior green via AC-A11Y.3a); (c) move the popover triggers inside the focus trap.
Evidence: `/tmp/snapv2-gate-evidence/artifacts/snapshots-v2-gate--AC-A11Y-00c5c-closes-drawer-metrics-info--chromium/{test-failed-1.png,error-context.md}`.

**D2 — 🟠 important (test-contract regression, v1 e2e).** See §3 — v1 `snapshots.spec.ts` step 11b backdrop-click-to-close reliance structurally dead against v2 side-mode. Test-debt class; needs v1-spec update or explicit design reversal.

**No other defects.** The 4 jest REDs are pre-existing base-attributed families (§1), not defects of this branch.

## 6. Artifacts committed (branch)

- `5d411a395` — `test(snapshots): v2 merge-gate web-automation harness (real-browser, stubbed API)` — 3 NEW files (+1017): `frontend/e2e/snapshots-v2-gate.spec.ts`, `frontend/playwright.snapshots-v2stub.config.ts`, `test/packs/snapshots_v2_webauto_test.sh`. Pathspec-only; zero product files.
- Lane-docs commit (this gate's docs): `test(snapshots): gate lane docs — RESULTS/PACKS/QUARANTINE (v2 merge gate)` — `.agents/tester/RESULTS/2026-10-10-snapshots-v2-merge-gate.md`, `.agents/tester/PACKS.md`, `.agents/tester/QUARANTINE.md` (hash recorded below on commit).

## 7. Final verdict

**FAIL (narrow)** — one deterministic red on a commissioned AC leg (D1) plus one contract flag (D2), both requiring leader fix-routing. Supporting surface is fully green: full jest 3888/3892 with all 4 REDs base-attributed + quarantined; 9/10 browser legs PASS with measured evidence, reproduced deterministically across two independent runs.
If D1 is resolved by re-scoping the AC (option b), this gate re-verdicts PASS on existing evidence without a re-run (the scoped behavior is already asserted green). If options (a)/(c) are chosen, one harness re-run of the pack (~75s) re-verifies.

---

### Scope Decision
Full FE jest suite warranted (final merge gate, page-level redesign); scoped OUT: daemon-side ensure.md Core items (concurrency pack, dev.sh probe — FE-only diff, out of blast radius), Release Gate E2E (no daemon/architecture change), v1 daemon-lane Playwright suite (not commissioned; known-dead step 11b per §3).

### Operational notes
- 14:10Z LLM rate-limit window: worker `40692acd` errored → single revive → completed; worker `2c43600a` wedged (authored artifacts on disk, never reported) → watchdog notice → terminated → ONE replacement `ac81bc17` → completed. No evidence gaps; Cardinal re-dispatch budget respected.
- Worktree search-tool boundary (grep/glob unreachable outside main workdir) handled via bash throughout — workers briefed upfront.
