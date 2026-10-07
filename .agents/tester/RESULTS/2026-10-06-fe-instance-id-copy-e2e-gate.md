# FE Instance-ID Click-to-Copy — E2E Verification Gate

**Date:** 2026-10-06
**Branch:** `feature/fe-instance-id-click-copy` · implementation `4d245d39` → test-only `c5161d53` (+562/−0, 2 new test files)
**Base:** `c55f2b0a` (= origin/latest tip at branch cut)
**Worktree:** `/home/nea/ensemble-src-wt-fe-id-copy` (fenced `ENSEMBLE_SELF_ENV=dev`; `.env` untouched throughout)
**Worker instances:** recon `691906a3` · focus `eace45d9` · regression+attribution `19fbcac0`
**VERDICT: ✅ PASS — merge-ready from testing. Zero branch-caused failures across focused e2e + scoped regression + 4-leg baseline attribution (10/10 reds base-attributed).**

---

## Scope Decision

FE-only change (5 impl files: instance-list html/ts, chat html/ts, new `instance-id-copy/` component ×4 incl. its own spec) → scoped to:
- **Focused web automation** (mandatory for FE change): new spec on the snapshots-lane pattern.
- **Scoped regression**: existing instances-list + chat e2e specs only (9 spec files). `/jobs`-page specs (`fe_liveness_*`) excluded — not in the changed surface.
- Unit/jest NOT re-run (implementer already: 7/7 component, 84/84 instance-list, 119/119 chat). ensure.md is daemon-boot scoped — N/A (zero daemon files in diff, verified `git diff --stat c55f2b0a..c5161d53` = 10 files, all `frontend/`).

## Lane Decisions (per recon, no infra invented)

| Leg | Lane | Why |
|---|---|---|
| Focus | snapshots-lane clone: new `playwright.copy-id.config.ts` reusing `boot-e2e-snapshots-daemon.sh` + `proxy.conf.snapshots.json` (BE :18279, FE :14199, disposable PG :15532) | Only in-tree lane with config-level `clipboard-read/write` permissions (`playwright.snapshots.config.ts:88`); self-contained disposable stack (dev DB untouched); runtime clipboard precedent `e2e/snapshots.spec.ts:481` |
| Regression R1-R3 + baseline | dev lane (`playwright.config.ts`, BE :8079 via fenced worktree `dev.sh`, FE :4199 `ng serve`) | The habitat those specs were written for; manual bring-up mirroring config webServer blocks verbatim |
| Baseline attribution | FE swap onto same :4199 from temp worktree `/tmp/ens-idcopy-base` @ `c55f2b0a`; BE reused (premise verified: diff frontend-only) | Branch↔base A/B with identical BE/DB/browser; `cp -al` hardlink node_modules (2s, shared inodes verified) |

Mock lane rejected: `proxy.conf.mock.json` is a dead reservation (no committed mock-BE script). Serialization honored: one Playwright pack at a time.

## Pack 1 — Focused e2e `instance-id-copy.spec.ts` (snapshots lane): ✅ PASS

`cd frontend && timeout 300 npx playwright test -c playwright.copy-id.config.ts e2e/instance-id-copy.spec.ts` — 1 passed + 1 documented skip, **71s**. Real API-created instance `d1870357-2b1b-4600-a6b6-abb61fb1fb3a` on the disposable BE.

| # | Scenario | Evidence |
|---|---|---|
| S2 | Chip contract | inner `<button>`, `title="Copy full ID"`, aria-label carries FULL uuid; visible text `d1870357-2b1...` (truncated 12-char prefix) |
| S3 | Click → clipboard | `navigator.clipboard.readText()` === FULL 36-char uuid (exact equality, not the prefix) |
| S4 | Toast | `mat-snack-bar-container` text `Instance d1870357-…-abb61fb1fb3a copied` (full uuid verbatim) |
| S5 | Auto-dismiss | toast gone within 4s (duration 2000ms) |
| S6 | No navigation | URL still `/instances` after chip click; row persists (chip `preventDefault+stopPropagation` defeats row routerLink) |
| S7 | Chat header chip | deep-link to `/projects/all/instances/<uuid>`; header chip click → clipboard === FULL uuid + toast + dismiss |
| S8 | Keyboard | real `focus()` + `Enter` → copy + toast (**no synthetic-dispatch fallback needed**; `syntheticKbd=false, spaceBound=yes` — Space also native) |
| S9 | Error-toast path | `test.skip` documented — unit-covered (7/7); forcing clipboard rejection with perms granted is unreliable |

Substitutions (documented): API seeding via `request` fixture + own helper (`test-helpers.ts` hardcodes :8079 — wrong for disposable lane); MDC toast tag locator over class-only; assertions target inner `button` (a11y attrs live there, not on `app-instance-id-copy` host). Teardown verified: :18279/:14199/:15532 freed, no lingering processes, PG cluster + data dirs removed.

**Files committed (test-only):** `frontend/e2e/instance-id-copy.spec.ts` (442 lines), `frontend/playwright.copy-id.config.ts` (120 lines) → commit `c5161d53`.

## Packs R1–R3 — Scoped regression (dev lane): raw FAILs, all attributed pre-existing

Warm lane: manual bring-up mirroring config webServer verbatim (BE /livez+/readyz 200, ~190s; FE :4199 12s; same PIDs served all legs).

| Pack | Files | Raw result | Runtime |
|---|---|---|---|
| R1 | instances-project-tabs, state-cache core/lazy/regression | FAIL 6F/10P (authoritative via `.last-run.json`) | 222s |
| R2 | send-pause-button, tab-workspace-sync ×3 | FAIL 3F/7P (+5 halted siblings) | 183s |
| R3 | slash-command-compact | FAIL 1F/3P (+12 halted) | 47s |

## Baseline attribution (4 legs @ base `c55f2b0a`) — final ledger

Discriminator: FAIL@base → pre-existing confirmed; PASS@base → re-run once (2 base-passes = branch-suspect). Halt-on-failure semantics documented; authoritative counts always from `test-results/.last-run.json`.

| Ledger | Spec:line | Branch | Base | Verdict |
|---|---|---|---|---|
| R1-F1 | instances-project-tabs:124 tab active-class | FAIL | FAIL (identical) | **PRE-EXISTING** (tab-persistence product bug; ProjectTabBarComponent untouched) |
| R1-F2..F5 | instances-state-cache-lazy :85/:161/:208/:243 | FAIL ×4 | FAIL ×4 | **PRE-EXISTING** (Plane CSP `frame-ancestors` env class; allowlist lacks :4199 — LESSONS 2026-08-19) |
| R1-F6 | instances-state-cache-regression:600 R5 Escape | FAIL ×4 (R1 + 3× isolated) | PASS ×1, FAIL ×3 (3× isolated) | **PRE-EXISTING flaky** — see below |
| R2-F1..F3 | tab-workspace-sync :188/:323/:274 | FAIL ×3 | FAIL ×3 | **PRE-EXISTING** (`<mat-toolbar from <app-workspace>` pointer-intercept layout bug) |
| R3-F1 | slash-command-compact:349 SC2a | FAIL | FAIL (byte-identical strings) | **PRE-EXISTING** (spec↔UI copy drift; UI copy `kept the summaries that completed…` pre-exists chip-wrap) |

**R5 deep-dive (8 attempts, 1P/7F, identical signature `Expected "none", Received "flex"`, `toPass` 10s exceeded):** spec's own comment self-flags "signal-driven flip lags the Escape press". Isolated `-g "R5"` retry budgets: branch 0/3, base 0/3. The lone base pass (Part-1 run 1, fresh dev server) is statistical noise. **QUARANTINED** with retry-budget evidence; recommended test-side fix: `toPass({timeout:10000→20000})` at :685; un-quarantine = fix + 3× clean.

Base-only drift (not a regression, not quarantined): `instances-state-cache-regression:233` R2 sidebar count FAIL@base ×2 / PASS@branch — dev-DB state accumulation class.

**Tally: 10/10 reds base-attributed. 0 branch-caused. 0 branch-suspects. Quarantined: 4 families + 1 flaky (see QUARANTINE.md 2026-10-06 rows).**

## Follow-up debt surfaced (none block this branch)

- 🟠 mat-toolbar/tab-bar layering bug (tab-workspace-sync trio) — product layout debt, pre-existing
- 🟠 tab persistence active-class (instances-project-tabs:124) — product debt, pre-existing
- 🟠 Plane CSP allowlist lacks :4199 (lazy spec ×4) — env config; lift quarantine when allowlisted
- 🟠 SC2a copy drift — align spec string or product copy (also unblocks 12 halted siblings incl. O17)
- 🟢 R5 `toPass` timeout bump — test-side
- 🟢 send-pause Test-4 "purple paused status" soft-passes (sidebar not visible at `/`) — spec drift surface
- 🟢 Halt-on-failure + workers=1 silently drops later tests (O17 never executed anywhere this commission) — consider splitting risky tests into own files

## Teardown

9 lane processes stopped (all cmdline-verified mine; graceful TERM; uvicorn `--timeout-graceful-shutdown 10` honored; 0 SIGKILL); :8079/:4199/:18279/:14199/:15532/:10080 unbound; `/tmp/ens-idcopy-base` worktree removed (hardlink-safe); other worktrees/ports untouched; **:8088 never connected/bound/killed** (status-checked only). Feature worktree intact @ `c5161d53`; 3 Playwright-regenerated PNG artifacts restored to committed state post-teardown.

## Environment substitutions summary

1. Clipboard validated via REAL `navigator.clipboard` (config-level permission grant, snapshots-lane pattern) — no stub needed.
2. Keyboard path used REAL focus+Enter/Space — no synthetic dispatch needed.
3. API seeding via `request` fixture (dev-port hardcode in shared helper bypassed).
4. `/healthz` → daemon exposes `/livez`+`/readyz` (v0.17.1).

## Documentation updated

- [x] RESULTS/2026-10-06-fe-instance-id-copy-e2e-gate.md (this file)
- [x] QUARANTINE.md — 5 new rows (R5 flaky + 4 deterministic pre-existing families)
- [x] PACKS.md — commission section + pack rows
- [x] LESSONS/2026-10-06-fe-attribution-leg-recipe.md — FE baseline-attribution recipe + reporter-count gotchas
- [x] MOCK_TESTS.md — N/A (no mock services; disposable real stack)
- [x] rules/ensure.md — untouched (user-owned); boot-probe scope N/A for FE-only diff
