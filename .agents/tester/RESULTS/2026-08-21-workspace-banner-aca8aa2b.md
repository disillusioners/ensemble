# Post-Merge Live Verification: workspace error-banner layout + structured tree API errors (`aca8aa2b`)

Date: 2026-08-21 (17:40–19:35Z) | Requester: leader (post-merge gate) | Test lead: tester
Commit under test: `aca8aa2b` — merged to `latest` as `3b4da6a6` (verified ancestor-OK; working tree clean at session start)
Target env: dev only (FE :4199 / BE :8079). Prod :9797 untouched by all workers (verified — no :9797 calls, no kills).
Workers: bbc07263 (preflight-infra), a9982a00 (unit-ws-fe), 1c9f0b06 (backend-ws-api), 49570aa8 (banner-e2e), 4d693cb0 (s5-rerun ×2: pack run + spec re-pin)
Test-code commits on `latest`: `95644c25` (banner e2e pack), `81219eaf` (hide-button spec re-pin). Zero app-source changes.

## Verdict: ✅ VERIFIED — all 4 acceptance criteria PASS — 0 regressions from aca8aa2b. 2 pre-existing non-aca8aa2b findings routed for follow-up (1 daemon bug candidate, 1 test-infra debt PAID DOWN in-session).

## Scope Decision
Change set = 5 files (FE workspace SCSS + service + 2 spec files, BE tests/test_workspace_api.py). No daemon/job/queue touchpoints → job-system e2e convention NOT triggered; Release Gate NOT warranted. Scoped packs: workspace FE unit, workspace API backend, banner live e2e (NEW), hide-button S5 re-run (+ in-session spec re-pin). Skipped: daemon packs, full jest, Release Gate.

## Acceptance Criteria Results

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | Banner slim, not full-height; tab-bar button clickable while banner shows | ✅ PASS | Banner 1280×**61** @ y=56 (vscode mode) / @ y=121 (builtin) vs pre-fix baseline 1280×**664** @ y=56 — asserted <120px, 0.092/0.098 top-fraction. Builtin mode: zero ws-btn overlap. Text structured: `error_outline Project has no main_directory configured (400) close` — no "Http failure". Architectural note: while the workspace overlay (z=100) is open, tab-bar ws-btn click is blocked by the OVERLAY (hit-test: `insideErrorBanner: false`, `insideAppWorkspace: true`), pre-existing by design, NOT the banner (this re-attributes part of the r3 S5 diagnosis). Sanctioned paths verified: header hide → ws-btn re-open responsive (152ms), dismiss → responsive (42ms), below-banner point hits viewer pane (pre-fix: inside stretched banner). |
| 2 | Structured error surfaced | ✅ PASS | Backend returns `{"detail":{"error":"Project has no main_directory configured"}}` (HTTP 400, curl-verified); service `extractErrorMessage` unwraps `detail.error` → `detail` string → legacy string (service.ts:239-267); banner text verbatim contains `main_directory`, no "Http failure". Unit-pinned: 66/66 service suite incl. extraction chain. |
| 3 | No layout regressions on success path | ✅ PASS | Scratch fixture project (real main_directory in /tmp): tree 2 entries (src/, docs/), `errorBannerCount: 0`, viewer pane 1280×563 = 100% width / 84.8% height of overlay, fixture content rendered. Screenshot banner-t3-success.png. Fixture fully cleaned (project DELETE 200, /tmp removed, instances deleted, pref restored). |
| 4 | Unit suites hold: comp 92/92, svc 66/66 | ✅ PASS | `workspace_frontend_unit_test` 294/294 across 9 suites; per-suite: workspace.component **92/92**, workspace.service **66/66** — exact match to leader pin. Backend pin: `workspace_api_integration_test` **36/36** (+2 from aca8aa2b structured-error tests). |

## Test Plan Execution

| Item | Pack | Result | Detail |
|---|---|---|---|
| 1 Unit FE | workspace_frontend_unit_test | ✅ PASS | 294/294, 9 suites, 6.0s; pins 92/92 + 66/66 exact |
| 3 Backend | workspace_api_integration_test | ✅ PASS | 36/36, 5.1s (+2 tests from aca8aa2b) |
| 2a/2b Live synthetic | workspace_banner_e2e (NEW, commit 95644c25) | ✅ PASS | T1 banner geometry both editor modes + structured text; screenshots banner-t1-vscode-mode.png / banner-t1-builtin-mode.png |
| 2c Live success | workspace_banner_e2e T3 | ✅ PASS | Scratch fixture; tree+viewer fill, no banner; screenshot banner-t3-success.png; cleanup all-true |
| 2d Dismiss | workspace_banner_e2e T4 | ✅ PASS | `aria-label="Dismiss error"` found → banner gone → tab bar responsive 42ms |
| 4 S5 re-run | hide_button_symptom_e2e | ✅ PASS (after in-session re-pin 81219eaf) | First run: 6/8 stale-spec RED (zero regressions from aca8aa2b — S5's formerly-intercepted click path now works; failure was a stale label assert). Root cause: b9a69e13 silently reverted r3 S1/S2 re-pin; S4/S5/S6/S5b never re-pinned; r3 "7/8" record was mixed spec/bundle state, NOT reproducible at HEAD. Test Architecture Fix applied (345 ins/361 del, S3/S7 byte-identical) → **8/8 PASS in 35.8s** verified |

## Findings (non-aca8aa2b)

1. 🟠 **[pre-existing daemon bug, follow-up ticket]** `vscode_server_manager.py` restart-guard deadlock: `user_stopped` is checked before the only code that clears it (220→290) — `PUT editor=vscode` after switching to `builtin` can never restart the managed code-server. Current dev state: pref=`vscode`, server=`stopped` (auto-starts at next daemon boot; boot log confirms). Discovered when banner T3 flipped editor pref for fixture test; restore PUT skipped by this bug. Prod untouched. Suggested fix: clear `user_stopped` on the PUT-editor path (or check order inversion). Severity: low-medium — affects dev workflows flipping editor modes via API/UI.
2. 🟢 **[test-infra debt, PAID DOWN]** hide-button spec was 6/8 stale at HEAD due to the r3 provenance hazard (b9a69e13 revert). Re-pinned to round-3 semantics @ 81219eaf; pack green 8/8. LESSONS entry written.
3. 🟢 **[environment, disclosed]** Concurrent browser-worker collision: s5-rerun's failure artifacts were wiped once by banner-e2e's playwright run (shared `frontend/test-results/`). No verdict impact (all output captured verbatim in run logs first). Future: serialize browser workers or partition artifact dirs per worker.
4. 🟢 **[dev DB composition]** agents-ensemble project absent from dev DB (:8079) — all 100 projects are main_directory=null synthetics. Success-path AC3 required a scratch fixture project (created+deleted in-session; standard e2e pattern here). Also ~+N e2e-synthetic projects accumulated this session (harmless, unique names).
5. 🟢 **[record correction]** Prior r3 report line "S5 RED = candidate product bug: error-banner intercepts tab-bar clicks" — partially misattributed. The full-height banner DID visually cover the tab bar (now fixed by aca8aa2b), but the click-block while overlay-open is the z=100 overlay architecture (by design, pre-existing). r3 report's "7/8" mixed-state caveat documented.

## ensure.md (Core, blast-radius scoped)

- ✅ Critical #1 (no regressions in changed packs): workspace_frontend_unit_test 294/294, workspace_api_integration_test 36/36, workspace_banner_e2e 2/2 (new), hide_button_symptom_e2e 8/8 (post re-pin) — ALL PASS
- ✅ Critical #4 (dev.sh `--timeout-graceful-shutdown 10`): present at dev.sh:102, matches running BE cmdline (preflight grep evidence)
- Concurrency/DB-loop requirements: out of scope (no daemon files changed). Release Gate: not triggered (FE+tests only, no job/task/queue files).

## Quarantine
None new — no flaky tests this session (all failures root-caused deterministic: stale spec). Existing QUARANTINE.md entries untouched and not applicable to the scoped packs.

## Artifacts
- Screenshots: frontend/test-results/banner-t1-vscode-mode.png, banner-t1-builtin-mode.png, banner-t3-success.png
- Evidence JSON (all bounding boxes verbatim): frontend/test-results/workspace-banner-evidence.json
- Commits: 95644c25 (banner e2e pack), 81219eaf (spec re-pin) on `latest`

## Code Changes Summary (all test-code; committed)
- frontend/e2e/workspace-error-banner.spec.ts (NEW) + test/packs/workspace_banner_e2e_test.sh (NEW) + PACKS.md row — commit 95644c25
- frontend/e2e/hide-button-symptom.spec.ts (re-pin S1/S2/S4/S5/S6/S5b to round-3 semantics) — commit 81219eaf

### Overall Status
- Unit Tests: ✅ PASS (92/92 + 66/66 exact pins; 294/294 total)
- Backend: ✅ PASS (36/36)
- Live UI (banner): ✅ PASS (T1-T4)
- S5 symptom: ✅ GONE (interception path now works; pack 8/8 post re-pin)
- ensure.md Core: ✅ PASS
- **aca8aa2b acceptance gate: ✅ READY — verified, no regressions; 2 follow-ups routed (daemon `user_stopped` bug; none blocking)**
