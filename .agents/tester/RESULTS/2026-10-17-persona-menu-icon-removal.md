# Persona Menu Icon Removal — Focused Functional Verification

Date: 2026-10-17
Commission: functional/runtime verification of a SMALL surgical UI change (static review already APPROVED — 0 critical/warning)
Worktree: `/home/nea/ensemble-src-wt-persona-menu-icon-removal` (branch `feature/remove-persona-menu-icon`, base latest @ a3e9f1bda)
Change under test: commit `1c5c9ddba` — single file `frontend/src/app/app.html` (+8/−9): persona nav `<mat-icon>person</mat-icon>` removed; four matBadge attributes (`[matBadge]="personaService.pendingCount()"`, `[matBadgeHidden]="… === 0"`, `matBadgeColor="warn"`, `matBadgeSize="small"`) re-anchored onto the `<a class="nav-link persona-nav-link">` host.
Test-only commit added: `a117176ed` — new spec `frontend/src/app/app.persona-nav.spec.ts` (168 lines).
Worker instances: 2a274e00 (unit), 888c3617 (browser), 7130360e (build).

## Summary

- Total packs: 4 runs (2 unit + 1 browser + 1 build) | Passed: 4 | Failed: 0 | Timeouts: 0
- ensure.md (blast-radius scoped): in-scope Core item PASS; Release Gate NOT triggered
- Quick fixes: 1 (test-code only — selector fix inside the NEW spec, pre-commit)
- Quarantined tests touched: 0 (existing FE e2e quarantined lanes out of scope)

## Scope Decision

Full suite NOT warranted: single-file template change (+8/−9), frontend-only, no backend surface, no architecture impact. Ran: `fe_persona_unit` (×2), `fe_persona_browser`, `fe_persona_build_gate`. Skipped: all backend packs (`concurrency_atomic_unit_test` etc. — no backend files in the change set), full FE jest suite, existing FE e2e suite (quarantine-laden lanes unrelated to the app-shell nav).

## Commissioned Verification Points — all PASS

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | Persona item renders WITHOUT icon; siblings unaffected | ✅ PASS | Unit: 6/6 — zero `mat-icon` in `.persona-nav-link`, sibling nav item still renders its `mat-icon`. Browser (c+d): zero `mat-icon` in persona item, no `person` glyph; 5 mat-icons render elsewhere in shell (notifications/settings/folder_open/visibility_off/description) — icon rendering intact globally. Note: sibling MAIN-nav items (Instances/Sources/Schedules/Jobs/Skills) never carried icons; "unaffected" is evidenced by the unit sibling-nav assertion + shell-wide mat-icon presence. |
| 2 | "Persona" label displays; routerLink works | ✅ PASS | Unit: label present, anchor href resolves to persona route. Browser (e): `.persona-nav-link` visible, `href='/persona'`, first text node `'Persona'`. |
| 3 | Badge: count>0 → badge with count; count=0 → hidden | ✅ PASS | Unit: pendingCount 0 → hidden; 3 → visible text "3"; back to 0 → hidden. Browser (f+g) real DOM, API stubbed via route interception (`GET /api/prompts/pending-count` → `{count:3}`): `.mat-badge-content` visible, text `'3'`; flip to `{count:0}` + reload → host carries `mat-badge-hidden`, badge content `display:none`. |
| 4 | Badge pixel placement sane (visible, not clipped/overlapping label) | ✅ PASS | Browser (h), viewport 1440×900: badge box 6.0×6.0 px at (712.1, 10.5) — non-zero, fully within viewport; badge center (715.1, 13.5) inside anchor rect (642.0, 10.5, 76.2×34.0) within ≤8px expansion (anchored at link's top-right corner); badge ∩ label area = 36.0 / 2589.3 px² = **1.39%** of label area (threshold ≤30% — does not cover the "Persona" glyphs); anchor (642.0→718.1) within navbar (243.0→718.1) and viewport. |

## Pack Results

| Pack | Command | Result | Runtime |
|---|---|---|---|
| `fe_persona_unit` #1 (existing app-shell spec) | `cd frontend && timeout 120 ./node_modules/.bin/jest src/app/app.component.spec.ts --runInBand --no-coverage` | ✅ PASS 53/53 | 5.6 s |
| `fe_persona_unit` #2 (new focused spec) | `timeout 120 ./node_modules/.bin/jest src/app/app.persona-nav.spec.ts --runInBand --no-coverage` | ✅ PASS 6/6 (after 1 test-code quick fix) | 3.1 s |
| `fe_persona_browser` | scratch `ng serve --host 127.0.0.1 --port 14987 --no-open` + `timeout 300 node <pack>` (Playwright 1.60, route-intercept `**/*`) | ✅ PASS 17/17 | setup ~30 s boot / pack 6.0 s |
| `fe_persona_build_gate` | `cd frontend && timeout 300 npm run build` | ✅ PASS exit 0; 0 warnings referencing app.html/persona/matBadge; `dist/frontend` artifacts confirmed fresh | 28 s (26.8 s compile) |

## Quick Fixes Applied

- 2a274e00 (unit worker): new-spec selector fix before commit — `.mat-badge` is the HOST class on the `<a>`; the badge element is the appended `span.mat-badge-content`; hidden state = host `.mat-badge-hidden` + `display:none`, while `textContent` ALWAYS reflects the bound value. Root cause: Material badge DOM semantics (see LESSONS/2026-10-17-material-badge-dom-semantics.md). Fix: test-code only, < 20 lines; re-run PASS; included in commit `a117176ed`. No production files touched (verified: `git status` clean post-commit, only the spec file staged).

## Safety / Cleanup Evidence

- Port usage: 14987 ONLY (test range). 4199/8079/8088 untouched (verified free before/after).
- Zero real API egress in browser pack — ALL requests route-intercepted at the Playwright level (persona endpoint stubbed; other `/api/**` benign empty shapes; streams + non-localhost aborted). Live daemon never contacted.
- Dev server PID 3431901 confirmed dead; port 14987 confirmed free (`ss -ltnp` empty); no orphan processes (`pgrep` on serve pattern → none).
- Scratch dir `/tmp/persona-verify-3430607/` removed; worktree `git status --short` clean after each worker.

## Gotchas Recorded

- Material badge DOM semantics (host class vs appended span vs hidden criterion) → LESSONS/2026-10-17-material-badge-dom-semantics.md.
- Sibling main-nav items never had icons — verification of "siblings unaffected" must not assert icons ON siblings; assert sibling nav intact + shell-wide mat-icon presence instead.
- Console noise under full route interception (JobQueueIndicator/HomeComponent shape mismatches from benign `{}` stubs) is expected diagnostic noise, NOT failures — do not gate on zero-console-errors (documented CSP/stub quarantine class).
- `matBadgeSize="small"` single-digit badge renders a 6×6 px dot-with-number — small but visible; placement thresholds must account for the tiny box.

## ensure.md Validation (scoped)

- Core — "No regressions in changed packs": ✅ PASS (all scoped/ad-hoc packs green)
- Core — concurrency/async/dev.sh items: out of blast radius (no backend files changed) — not run
- Release Gate: NOT triggered (small, non-architecture, non-promote change)

## Gaps / Not Covered

- `app.routes.spec.ts` not run — the primary app-shell spec `app.component.spec.ts` (2127 lines) was the canonical existing pack; the routes spec does not exercise the nav template.
- Real-backend integration of `pending-count` NOT covered — by design: commission mandates route interception over real data; no live daemon contact permitted.
- Screenshot `/tmp/persona-badge-count3.png` (77,910 bytes, badge=3 state) is volatile (/tmp) — may vanish on reboot.
- Existing FE e2e suite and quarantined lanes untouched — out of scope for this change.

## Code Changes Summary (all committed on branch)

- `frontend/src/app/app.persona-nav.spec.ts` (new, 168 lines) — badge-binding pin spec. Commit: `a117176ed`
- `.agents/tester/` docs (this file + PACKS.md + LESSONS) — committed by docs worker (hash in final report)

## Overall Status

- Unit: ✅ PASS | Browser: ✅ PASS | Build gate: ✅ PASS | ensure.md (scoped): ✅ PASS
- **Testing Complete: ✅ READY** — all four commissioned verification points PASS at the required layers; production code unchanged by the test phase (only the test-only spec commit added).
