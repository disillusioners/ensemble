# Test Report: hide-button-toggle-affordance follow-up (branch fix/hide-button-toggle-affordance, uncommitted vs 289b0f51)

Date: 2026-08-20 (round 2)
Instance IDs: 348af756 (wave-0 env), e189cf41 (pack 1), 4e00ea5d (pack 2), fbf634b1 (pack 3), 41792d37 (pack 4 + S5b)

## Summary
- 4 packs: unit 48/48 + adjacent 119/119 + regression e2e 7/7 + symptom e2e 8/8 (S1–S7 + S5b). **All green. Zero product defects found.**
- Acceptance criteria 1–6: ALL verified (unit + live runtime). Commitments: `cf341d39` (S5/S6/S7) + `9abdb308` (S5b) — test code only; developer's 3 uncommitted files untouched.
- Quarantine: 0. Flakes: 0 (pack 4 ran 3× green; S5b 3/3 after the goto→SPA-nav fix).

## Per-Item Results

### 1. Unit — ✅ PASS
`app_component_unit_test.sh`: **48/48** in 1.5s (expected 48; all 5 new areas observed: isWorkspaceRecoverable suite, 5th anyOverlayVisible term, 3-tier precedence, showTierActive gate, same-projectId re-show handler).

### 2. Live web automation of the toggle — ✅ PASS
`hide_button_symptom_e2e.sh`: **8/8 in 19.8s** (3× consecutive green).
- **S5 workspace toggle**: PASS — workspace hides (flex→none, workspaceProjectId retained), show-tier flips on when nothing visible, re-show same project. Divergence note (documented, correct design): show-tier is gated `!detailVisible()` — while chat is visible the button keeps hide affordance (branch 4 semantics; a "Show" label would lie). Matches implementation lineage W3/N3, NOT a regression.
- **S5b branch-2 probe (added this round)**: PASS — in the true firing state (workspace recoverable, chat hidden via non-detail route so isHiddenButRecoverable=false, show-tier active): header click → `show(workspaceProjectId())` fires → workspace re-shown flex, SAME project (projectTabs.activeTabId unchanged), chat stays hidden, URL unchanged. 6.1s isolated, 3/3 deterministic.
- **S6 /plan dead-click**: PASS — default-tier affordance (not show-tier) on /plan; click does NOT re-show workspace nor chat (no-op for overlays under plane z-1000).
- **S7 chat-wins precedence**: PASS — both recoverable → show-tier (chat tier); click re-shows CHAT (same instance + fingerprint), workspace stays hidden; affordance flips back to hide.

### 3. Previous e2e pack — ✅ PASS
`instances_state_e2e_regression.sh`: **7/7 in 9.0s** (R6, R2, R4, N1, Reload-while-hidden, R5, Terminate) — chat-only guarantees intact under the affordance change.

### 4. Adjacent suites — ✅ PASS
`adjacent_chat_unit_test.sh`: **119/119** (33 view-state + 86 chat) — exact baseline parity.

### 5. Reviewer S2/S5 new e2e — ✅ DELIVERED
Committed: `cf341d39` (+862: S5/S6/S7, helpers readWorkspaceSnapshot/workspaceBtnForActiveTab/workspaceOverlayHideBtn, noise filter extended with /api/workspace/ + /vscode-folder 404s + URL discrimination via msg.location()) + `9abdb308` (+357: S5b). Runtime behavior the unit mock cannot express is now pinned.

## Acceptance Criteria Matrix
| # | Criterion | Unit | Live |
|---|---|---|---|
| 1 | Visible → hide affordance; click hides workspace, id retained | ✅ | ✅ S5 |
| 2 | Recoverable → unhide affordance; click re-shows SAME project | ✅ (b) | ✅ S5b (branch 2 true state) + S5 |
| 3 | Mirrors "view workspace" semantics | — | ✅ S5 (project-tab button parity used in both directions) |
| 4 | Chat wins when both recoverable; visible overlays take precedence for hide affordance | ✅ | ✅ S7 + affordance matrix |
| 5 | /plan: no workspace re-show via header (dead-click) | ✅ | ✅ S6 |
| 6 | Previous guarantees (instance preservation, URL, modifier-clicks, ALT+`) | ✅ | ✅ S1–S4 + pack 3 7/7 |

## Affordance semantics (observed end-to-end, authoritative)
| State | icon | aria |
|---|---|---|
| Workspace visible | visibility_off | Hide overlay |
| Workspace hidden, chat visible | visibility_off | Hide overlay (show-tier gated !detailVisible) |
| Nothing visible, ≥1 recoverable (non-plan) | visibility | Show overlay |
| On /plan (recoverable ignored) | visibility_off | Hide overlay (default tier) |
| Chat re-shown after both-recoverable click | visibility_off | Hide overlay |

## ensure.md (scoped)
Core #1 (no regressions in changed packs): **PASS** — all 4 packs green. Backend untouched → concurrency/dev.sh N/A. Release Gate not triggered (UI-only).

## New Lesson recorded
LESSONS/2026-08-20-spa-nav-vs-page-goto.md — `page.goto()` full-reload nulls singleton WorkspaceOverlayService state (workspaceProjectId→null, button unmounts); URL transitions that must preserve service singletons MUST use in-app SPA navigation (click routerLink). Bit S5b authoring; fixed by clicking Sources nav link (app.html:18).

## Code Changes Summary
- frontend/e2e/hide-button-symptom.spec.ts — commits `cf341d39` + `9abdb308` (test only)
- Developer's app.ts/app.html/app.component.spec.ts: UNTOUCHED (diff preserved: 623+/54−)
- .agents/tester/PACKS.md, RESULTS/, LESSONS/ — updated this session

## Overall Status
- Unit: ✅ 48/48 · Adjacent: ✅ 119/119 · Regression e2e: ✅ 7/7 · Symptom e2e: ✅ 8/8
- **VERDICT: SHIP ✅ — all acceptance criteria unit- AND runtime-verified; no product defects; spec coverage extended and committed.**
