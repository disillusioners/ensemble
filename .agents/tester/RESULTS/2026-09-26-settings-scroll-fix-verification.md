# Settings Scroll Fix — Acceptance Verification (fix/settings-scroll @ dd14d975)

Date: 2026-09-26 (12:51–13:10 UTC)
Commissioner gate for: the Settings scroll fix AND (downstream) the v0.15.2 release.
Fix under test: commit `dd14d97518c0f09a8ab8df18b3c0592023137b84` — "fix(settings): make settings container the scroll container (R15 snapshot toggle reachable)" on branch `fix/settings-scroll` (verified at HEAD by 4 independent workers pre-run).
Worker instances: jest-full `9383bccc`, jest-baseline `d12c6ddf`, env-bringup `cb480235`, fe-prod-build `d8bbba25`, playwright-visual `1fb80c03`.
Artifacts dir: `.agents/tester/RESULTS/assets/2026-09-26-settings-scroll/`

---

## VERDICT SUMMARY

| Item | Verdict |
|---|---|
| 1. FE Jest full suite @ dd14d975 | ✅ PASS (3388P/4F/0S — exact baseline match) |
| 1b. Pre-existing claim formally closed @ dd14d975^ (`e67e5cd8`) | ✅ CLOSED (same 4 failures reproduce at parent) |
| 2. FE production build @ dd14d975 | ✅ PASS (exit 0, ~30s, 0 errors) |
| 3a. Settings page scrolls (1440×900) | ✅ PASS |
| 3a. Section enumeration (all present + scroll-reachable) | ✅ PASS (5/5) |
| 3b. R15 snapshot toggle: reachable | ✅ PASS |
| 3b. R15 snapshot toggle: operable (click → state flips) | ✅ PASS (Apply-gated radio; PUT 200) |
| 3b. R15 snapshot toggle: persists across reload | ✅ PERSISTED (GET 200 `{"enabled":true}` + DOM after reload) |
| 3c. Screenshots (4+ required) | ✅ 6 captured, independently re-inspected |
| 3-spot. Fold-rescue @ 1280×720 | ✅ PASS |
| 4a. Regression pages (home, /sources, /migration) | ✅ PASS (all render, all legitimately fit) |
| 4b. App-shell sanity (z-ladder, no stray scrollbars) | 🟡 PARTIAL (see Gaps) |
| **OVERALL** | **✅ PASS** |
| **ORIGINAL SYMPTOM** ("Settings page does not scroll; R15 toggle unreachable") | **DEAD** |

---

## 1. FE Jest full suite @ dd14d975 — PASS

- Command: `CI=true npm test -- --watch=false` (package.json `"test": "jest"`), wrapped `timeout 300 bash .agents/tester/RESULTS/assets/2026-09-26-settings-scroll/fe_jest_full_test.sh` (dual-layer: inner 270s watchdog unused).
- Raw counts: **98 suites (96P/2F); 3392 tests: 3388 passed / 4 failed / 0 skipped**; Jest 18.196s, wall ~21s.
- **Settings-related failures: 0** — `src/app/pages/settings/settings.component.spec.ts` PASS and `src/app/services/settings.service.spec.ts` PASS.
- **Failures outside the two known suites: 0.** The exact 4:

| # | Spec | Test | Error |
|---|---|---|---|
| 1 | jobs-filter-state.model.spec.ts | canonical status/source tables exhaustiveness guard | 9-vs-8: model has extra `"completed (gate escalated — unverified)"` |
| 2 | jobs-grouping.model.spec.ts:497 | groupMetaLine renders agent+N jobs+ago | received `"leader · 5 jobs · 9/10/2026"` (absolute date, not "ago") |
| 3 | jobs-grouping.model.spec.ts:559 | groupHeaderTitle agent·timeAgo | received `"leader · 9/10/2026"` |
| 4 | jobs-grouping.model.spec.ts:578 | groupHeaderTitle timeAgo-only fallback | received `"9/10/2026"` |

- Pass-count delta vs the 3388 expectation: **0** (4 failures ⇒ 3388 passes exactly).

### 1b. Formal close of the pre-existing claim (commission requirement)

Re-ran ONLY the two suites at parent `dd14d975^` = `e67e5cd85f052896cb80bf22ba03d575f339e025` in an isolated worktree `/tmp/ens-settings-baseline` (node_modules symlinked; worktree removed via EXIT trap; `git worktree list` clean after — main checkout untouched).

- Result: **same 4 failures** (jobs-grouping ×3 timeAgo, jobs-filter-state ×1 enum-drift, diff `@@ -1,8 +1,9 @@`), 57P/4F/61 total in the two suites.
- **Statement: the 4 failures pre-date the fix — claim formally CLOSED.** Characterisation note for the record: 1 of the 4 is the literal JOB_STATUS_VALUES enum drift; the other 3 are timeAgo date-formatting assertions in jobs-grouping — both families live in exactly the two commission-named suites.
- Log: `assets/2026-09-26-settings-scroll/fe_jest_baseline.log`; script `fe_jest_baseline_test.sh`.
- Quarantine: the 4 tests are registered in QUARANTINE.md (2026-09-26 row) so future FE gates are not redned by them; fix is a separate test-debt commission.

## 2. FE production build @ dd14d975 — PASS

- Command: `npm run build` (= `ng build`) in `frontend/`, dual-layer wrapped; **exit 0, ~30s, 0 errors**.
- Fix verification inside the artifact: Settings lazy chunk `chunk-6CRMNNZQ.js` (23.75 kB) **contains the fix's compiled SCSS** — `min-height: 0` in the `:host` rule + `overflow-y` present.
- Pre-existing warnings only: SASS `lighten()` deprecation (settings.component.scss:140), initial-bundle 5.88 MB vs 1 MB budget, six component SCSS budget overages. Unrelated to the 2-line fix.
- Log: `assets/2026-09-26-settings-scroll/fe_prod_build.log`.

## 3. Visual / browser verification (the core gate) — PASS

Environment (bring-up worker `cb480235`, all evidence in its report + `dev_daemon.log`/`fe_dev_server.log`):
- Dev daemon: `./dev.sh` on 8079, v0.15.1, standalone `#!/bin/bash` scrub wrapper, **`POSTGRES_SURVIVORS=0` echo-verified before exec**; daemon's own log shows `localhost:5432/ensemble_dev` (dev DB — NOT live `ensemble_prod`). Live 9797 / demo 7979 / self 8088 untouched (port survey recorded).
- FE dev server: `ng serve --port 4199 --host 127.0.0.1`, bundle complete 19.4s.
- Proxy proof: `GET /api/projects` via 4199 → 200 JSON (`__system_default__` project present).

Playwright pack: `fe_settings_scroll_e2e_test.mjs` (chromium headless 148.0.7778.96; `npx playwright install-deps chromium` needed on this host — see LESSONS). Runtime 15.8s. Log: `fe_settings_e2e.log`.

### 3a. Scroll gate — PASS
- **Scroll mechanism = the `.settings-container` element (the fix), NOT the window**: container `scrollHeight=1957 / clientHeight=844`, `overflowY=auto`; window `scrollHeight=900=clientHeight` (not window-scrollable — by design of the fix).
- Scrolled to bottom: `elScrollTop=1113/elMax=1113` (max reached); last section ("Snapshot Usage Metrics") bottom=868 ≤ vh=900; Agent Snapshots box `{top:66,bottom:617}` fully in view.

### 3a. Section matrix (all in-DOM AND scroll-reachable) — 5/5 PASS

| Section | in-DOM | reachable | bbox @1440×900 |
|---|---|---|---|
| Language Preference | yes | YES | y:156–410 |
| Editor | yes | YES | y:434–844 |
| Blueprint Peak Hours | yes | YES | y:613–900 |
| Agent Snapshots | yes | YES | y:202–753 |
| Snapshot Usage Metrics | yes | YES | y:673–900 |

### 3b. R15 snapshot toggle — reachable → operable → persisted — PASS
- Control: Apply-gated radio group `input[name="snapshot-create-preference"]` (values on/off) + section Apply button — **not** a slide toggle.
- Before: Disabled (default) selected; `GET /api/settings/snapshot-create` → 200 `{"enabled":false}`.
- Operate: radio → "Enabled" + dirty hint "Unsaved changes" (0 PUTs on bare radio click — correct Apply-gated design); Apply → **PUT 200** req `{"enabled":true}` res `{"enabled":true}`; snackbar "Snapshot-create enabled — capturing is now allowed for creators".
- **Persistence: PERSISTED** — after full reload: `GET` → 200 `{"enabled":true}` AND DOM shows Enabled selected.
- Environment restored: reverted via Apply → PUT 200 `{"enabled":false}`; **final state left = disabled (original); net API effect zero**.

### 3c. Screenshots (6 captured; independently re-inspected via vision — content corroborated)

| Path (under `assets/2026-09-26-settings-scroll/`) | Content (vision-verified) |
|---|---|
| `01-settings-top-1440.png` | Settings top @1440×900, 5 sections in DOM |
| `02-settings-bottom-1440.png` | Scrolled bottom: Agent Snapshots + Snapshot Usage Metrics in frame, radios rendered, Disabled selected (pre-toggle) |
| `03-toggle-before.png` | Toggle before: Disabled (default), Apply disabled |
| `04-toggle-after.png` | **Enabled selected + snackbar "Snapshot-create enabled — capturing is now allowed for creators"** |
| `05-settings-bottom-720.png` | @1280×720 bottom: Agent Snapshots content + radio control visible/clickable (fold-rescue); revert snackbar visible |
| `06-regression-home.png` | Home page render |

### 3-spot. Fold-rescue @ 1280×720 — PASS
- Container `scrollHeight=1957 / clientHeight=664`, scroll to bottom `elScrollTop=1293/elMax=1293`; Agent Snapshots box intersects viewport (top:-114, bottom:437 ≤ 720); toggle box `{y:150,bottom:163}` in viewport. Vision-verified screenshot 05.

## 4. Regression spot-checks

- (a) Home `/`: renders; `scrollHeight=900=clientHeight` → legitimately fits (no scroll needed), no horizontal scrollbar (1440/1440). PASS.
- (b) `/sources` AND `/migration`: both render, both legitimately fit (900/900), no h-scrollbar. PASS.
- (c) App-shell sanity: **PARTIAL** — see Gaps.
  - plane overlay (z=1000): **verified live** on `/plan` — `display=flex`, computed z-index **1000** ✓.
  - chat overlay (z=90) + workspace overlay (z=100): **not runtime-verifiable this session** — the dev daemon has ZERO instances (`a.instance-item count=0`), and the chat overlay cannot be mounted without fabricating state (refused). Static evidence only: `frontend/src/styles.scss:63-67` carries `app-chat { z-index: 90 }`.
  - No stray horizontal scrollbars observed on any visited page.

## Gaps (explicit)

1. Chat-overlay z=90 and workspace-overlay z=100 were NOT measured in a live browser — no instance exists on the dev daemon to mount them. Static CSS only. Non-blocking for this fix (the change is scoped to settings.component.scss and touches no overlay/z-index styles), but the 4b item is recorded as PARTIAL, not PASS. A follow-up with a spawned instance could close it if the commissioner wants the full ladder runtime-verified.
2. Screenshot `07-chat-overlay.png` not captured (same root cause — honest skip, no fabrication).
3. Teardown of the dev daemon + FE dev server: dispatched to bring-up worker at end of run — see §Environment Teardown addendum below (updated on worker confirmation).

## Scope Decision (blast radius)

FE-only change (2 SCSS lines in `frontend/src/app/pages/settings/settings.component.scss`; no daemon/production code touched). ensure.md Core items scoped accordingly:
- "No regressions in changed packs" — blast-radius change set = the FE packs above: all PASS. ✅
- `dev.sh --timeout-graceful-shutdown 10` static check — FOUND at dev.sh:102 (free grep, done during bring-up). ✅
- `concurrency_atomic_unit_test` + async-await greps — **out of scope** (daemon-lane; no daemon-code change in dd14d975). Not run — reduction reported per blast-radius rule.
- Release Gate — not run (FE-only fix; the v0.15.2 release gate is a separate downstream commission).
No contradictions between ensure.md methods and pack execution were encountered in the in-scope items.

## Quick Fixes Applied

None — zero failures attributable to the fix; nothing to fix.

## Code Changes Summary

None. No repo source file was modified by any worker (verified via per-worker reports + main-checkout HEAD/branch re-verification). Artifacts written only under `.agents/tester/RESULTS/`. One environment-side addition: `npx playwright install-deps chromium` installed missing OS libs (additive, host-level, reported by worker E). Nothing to commit (test artifacts are not code changes).

## Environment Teardown — ✅ CONFIRMED COMPLETE

Bring-up worker (`cb480235`) report, post-teardown:
- `proc_stop(proc-660c3a05)` FE dev server → SIGTERM, exit 143, immediate.
- `proc_stop(proc-16a80596)` daemon wrapper → SIGTERM (exit -15); daemon ran its full graceful sequence within ~10s (workers, EventBus, MCP pool drained, DB engine disposed, "Graceful shutdown complete", "Stopping reloader process [1564935]"). No force retries needed.
- Port verification: `ss -ltnp` EMPTY for 8079/4199/8088 after teardown; **9797 live (PID 1562018) and 7979 demo (PID 1562017) UNCHANGED since 12:17**.
- Orphan scan: daemon tree PIDs 1564935/1564937/1564937-context7 (1564972) all vanished — `ps -p` returns empty; cascade confirmed, no raw kills.

---
*Report assembled by tester (Test Leader) from 5 worker reports + independent vision re-inspection of screenshots 02/04/05. All raw logs under `assets/2026-09-26-settings-scroll/`.*
