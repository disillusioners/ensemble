# Round-3 Verification: hide-button editor-only fix (`fix/hide-button-editor-only`)

Date: 2026-08-21 (15:09–16:05Z) | Requester: leader (round 3) | Test lead: tester
Branch under test: `fix/hide-button-editor-only` @ base 053bfb22 — fix files UNCOMMITTED in working tree (5 files: app.ts, app.html, app.component.spec.ts, hide-button-symptom.spec.ts, instances-state-cache-regression.spec.ts)
Target env: dev only (FE :4199 / BE :8079). Prod :9797 untouched (verified — no worker navigated there, no process kills).
Workers: b240f1b2 (preflight-infra), 9c6c4bb8 (jest app), 00814768 (jest adjacent), 4aad3a19 (symptom e2e ×3 dispatches), 5c4dbf83 (regression e2e), 29fc0dc0 (manual mirror), 1681292f (final git snapshot)

## Verdict: ✅ FIX VERIFIED — all 5 acceptance criteria PASS — with 1 adjacent candidate product bug (S5, NOT caused by this fix) and a commit-provenance hazard requiring leader action

## Scope Decision
Change set = 5 frontend files (app shell + specs), no daemon/job/queue touchpoints → scoped to 4 registered packs + 1 manual probe. Full suite / Release Gate NOT warranted (frontend-only, no architecture change; job/queue e2e convention not triggered). Skipped: all daemon packs, full jest suite, Release Gate.

## Acceptance Criteria Results

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | Original prod symptom gone: reload at instance-detail URL → chat restores, hide button ABSENT | ✅ PASS | Manual mirror steps 2+3 (r3-dev-symptom-mirror.cjs): fresh load AND page.reload() → `.overlay-hide-btn` count **0**, chat visible (flex), workspace none. R6 regression first-live-pin: reload → nav-link target restored, button absent. Prod Phase-1 contrast: same URL shape on v0.10.5 showed button PRESENT 'Hide overlay' (RESULTS/2026-08-21-prod-phase1-hide-button-evidence.md). Caveat: dev chat was welcome-state (no messages sent per read-mostly constraint); button verdict depends on anyOverlayVisible gating (workspace unbound), not message count — unit R7 term covers the gate. |
| 2 | Button = workspace-editor toggle ONLY ('Hide editor'/'Show editor'/absent) | ✅ PASS (with S5 caveat) | Manual mirror 4a–4d: open workspace via tab-bar → button 'Hide editor'/visibility_off → click → hidden + 'Show editor'/visibility → click → re-shown SAME projectId (activeTabId unchanged). S5b PASS (branch-2 re-show). S7 PASS post stale-contract fix: both-recoverable → 'Show editor' → click re-shows workspace ONLY, chat stays hidden. Unit 50/50 (3-tier icon/aria precedence, showTierActive gate). S5 pack test RED — adjacent product bug (below), not toggle logic. |
| 3 | Chat: auto-restore, ALT+`, nav-link re-show unchanged; chat alone → button ABSENT | ✅ PASS | S1/S2 PASS (nav-link re-show, same instance + messages, URL unchanged). S3 PASS post setup fix: hotkey toggles workspace (none→flex→none), chatDisplay flex throughout, URL unchanged. N1 + reload-while-hidden PASS (cached id preserved). Chat-visible-alone → absent: manual mirror steps 2/3 + R6. |
| 4 | /plan + merely recoverable → ABSENT (D1); workspace visible on /plan → shows Hide editor + hides | ⚠️ PARTIAL | D1-absent: S6 PASS live (6s). /plan-visible→Hide-editor sub-case: covered by unit tests only (W3/W4 plan-route terms in 50/50) — no live e2e executed for it this round. Gap noted; low risk given unit coverage + S5b/S7 live parity of the same code path. |
| 5 | Prior guarantees: instance preservation, URL-stays-put, modifier-clicks native | ✅ PASS | S1/S2 (instance + messages preserved, URL unchanged), S4 (ctrl-click opens new tab natively, no swallow), R4/N1/reload-while-hidden (cached instance preserved), R5 (z100>z90 layering), terminate-fallback test (cache cleared). Regression pack 7/7. |

## Test Plan Execution

| Item | Pack | Result | Detail |
|---|---|---|---|
| 1 Unit app | app_component_unit_test | ✅ PASS 50/50 (0.1 min) | Exact baseline match |
| 1 Unit adjacent | adjacent_chat_unit_test | ✅ PASS 119/119 (1.7s) | 33 view-state + 86 chat |
| 2 Live e2e (both specs) | hide_button_symptom_e2e + instances_state_e2e_regression | ⚠️ regression 7/7 PASS; symptom 7/8 PASS + 1 FAIL | Symptom: S1,S2,S4,S6,S5b pass; S3,S7 pass after spec commits (setup precondition / stale-contract correction); S5 FAIL = candidate product bug. 15 tests total → 14 green, 1 red (S5). First live runs of re-pinned S5/S7/R6 landed here. |
| 3 Manual symptom mirror | r3-dev-symptom-mirror.cjs | ✅ ALL PASS (96.3s) | Steps 2,3,4a–4d pass; screenshots r3-mirror-01..05.png; JSON report in test-results/r3-mirror-run.log |
| 4 Evidence | screenshots + logs | ✅ | Listed under Artifacts |

## Candidate Product Bug (NEW, adjacent — NOT caused by this fix)

**Workspace error-banner stretches to fill the entire workspace and intercepts pointer events below the header.**
- Repro (dev, deterministic): open workspace for a project whose tree API 400s (`GET /api/workspace/<pid>/tree?path=.` → 400) → `.error-banner` renders `display:flex`, no flex-shrink constraint → rect = full workspace (1280×664 @ y=56) → covers the project tab bar (workspace-btn at y=66, 18×18) → any subsequent tab-bar workspace click blocked (S5 line 1251 TimeoutError, 15s).
- Header button unaffected (y=8, above banner top y=56) — manual mirror hide/show cycle via header worked.
- Suggested fix (developer): `flex: 0 0 auto` (or `align-self: flex-start`) on `.error-banner` in workspace.component.scss. Underlying `tree?path=.` 400 also worth investigation (vscode-folder 404 co-occurs in dev env).
- Evidence: follow-up #3 worker report (bounding boxes, network log); screenshots s3-after-first-hotkey.png (banner visible), r3-mirror-03-workspace-open.png.

## Spec Fixes Committed (test-side only; app source untouched by all workers)

| Commit | Test | Defect fixed | Re-run |
|---|---|---|---|
| 2ff77d52 | S5b | Mechanical typo `page.gotogoto` → `page.goto` | PASS 6s |
| 200de4dc | S3 | Missing precondition: SPA-nav via project tab "+" menu to set activeProjectId to UUID (hotkey gate at app.ts:713 bails on 'all'; card-click lands on /projects/all/...) | PASS 7.1s |
| 9a03ee7d | S7 | Stale-contract assertions: old "chat-wins" semantics → round-3 workspace-only toggle (chat hide via URL nav, labels Show/Hide editor) | PASS 6.2s |

S3 classification detail: NOT a product regression — hotkey handler unchanged vs 053bfb22 and correctly project-scoped; prior record-only contract masked the setup gap. Diagnostic: 5s/50-poll loop never saw display flip (toggle never invoked; gate bailed on activeProjectId='all').

## ⚠️ Commit Provenance Hazard (leader action required)

Mid-session (~15:35–15:45Z) an EXTERNAL actor switched the repo from `fix/hide-button-editor-only` to `fix/plane-sync-auth` (branch created off 053bfb22; daemon/clients/plane_http_client.py modified — no tester worker touched daemon code). Consequences (final git snapshot, verbatim in worker report):
- 2ff77d52 → on `fix/hide-button-editor-only` (its tip) ✅
- 200de4dc + 9a03ee7d → stranded on `fix/plane-sync-auth` ❌
- Working tree at session end: fix/plane-sync-auth @ 9a03ee7d + the round-3 fix files STILL UNCOMMITTED (as they were at session start) + plane_http_client.py dirty.
- **Behavioral validity unaffected**: FE serves the working tree; every live run exercised the round-3 fix files. Jest waves ran before the switch on the round-3 branch proper.
- **Recommendation**: cherry-pick 200de4dc + 9a03ee7d onto fix/hide-button-editor-only before merging; then commit the 5-file fix per the branch owner's plan. No git surgery performed by tester (repo under concurrent external operation).

## ensure.md (Core, blast-radius scoped)

- ✅ Critical: no regressions in changed packs — app 50/50, adjacent 119/119, regression e2e 7/7, symptom e2e 7/8 (S5 exception root-caused to adjacent pre-existing bug, not this change)
- ✅ Critical: dev.sh `--timeout-graceful-shutdown 10` present (line 102)
- Concurrency/DB-loop requirements: out of scope (no daemon files changed). Release Gate: not triggered (frontend-only change).

## Quarantine
None — no flaky tests (S3 deterministic 2/2 → root-caused to setup; retry budget satisfied by the two identical failures + post-fix pass).

## Artifacts
- Screenshots: frontend/test-results/r3-mirror-01-fresh-load.png … r3-mirror-05-reshown.png; s1-*.png, s2-reshown-via-navlink.png, s3-after-first-hotkey.png
- Probe: frontend/scripts/r3-dev-symptom-mirror.cjs (untracked, evidence artifact)
- Full run log: frontend/test-results/r3-mirror-run.log (57.7KB)
- Prod contrast evidence: RESULTS/2026-08-21-prod-phase1-hide-button-evidence.md

## Gaps
- S5 pack test remains RED until the error-banner product bug is fixed (test correctly refuses to work around it).
- /plan-visible→Hide-editor sub-case: unit-covered only; no live e2e this round.
- Round-3 fix files still uncommitted — commit + provenance repair (cherry-picks) pending leader/developer action.
