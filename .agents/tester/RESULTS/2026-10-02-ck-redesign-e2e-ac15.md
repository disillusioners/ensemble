# Test Report: ck-redesign-2026q4 — checkpoint-cleanup wizard e2e + AC-15 runtime verification

Date: 2026-10-02T01:26Z
Worktree: `/home/nea/ensemble-worktrees/ck-ui-test` (branch `feature/checkpoint-cleanup-ui-redesign`)
Base `f469dccf` → test commit **`b56e290a`** (9 files, +646/−10, test-only + axe devDep; NOT pushed — giter's lane)
Worker instances: `2edd1e13` (infra/discovery, no skill) · `88172066` (e2e pack, `e2e-test`) · `6081a92a` (unit regression, `test-pack-execution`) · `08e35aa4` (evidence verify, no skill)
Spec contract: `.agents/designer/specs/maintenance-checkpoint-cleanup-redesign.md` @ `386da2c3` (pin `4c6fe34a`), §10 + AC-15 clause.

## Summary
- **E2e: 19/20 PASS** — the single FAIL is the **AC-15 runtime budget breach** (measured **768px** vs **≤760px** at 1024×768). STOP RULE honored: zero production edits; escalated for designer/developer re-amend round.
- **Unit regression: 142/142 PASS** (96 + 4 + 42 exact-match).
- **ensure.md Core #1** (no regressions in changed packs): **FAIL** — by the AC-15 w2 gate only. The red is a real production-layout breach, not test rot; every other gate green.
- Quarantined: 0 (w2 failure is deterministic, not flaky — retry budget not applicable).
- Quick fixes applied: 0.

### Scope Decision
Change set = frontend e2e test code + fixtures only (component redesign already on branch). Scoped packs: the maintenance ck e2e spec(s) + the 3 mandated Jest suites. "Other maintenance e2e specs" regression set verified **EMPTY** (grep evidence — ck spec is the only maintenance-touching e2e). ensure.md scoped to Core #1; backend concurrency/dev.sh gates out of scope (no backend change). Full suite not warranted.

## AC-15 Runtime Measurement (GROUND TRUTH — settles static-vs-runtime)
- Viewport 1024×768: `document.documentElement.scrollHeight` = **768px** → **8px over** the ≤760px budget.
- Co-assertions at 1024×768 (all PASS): `ck-status-strip` in-viewport; `mat-stepper[orientation="horizontal"]` visible; `ck-continue-btn` `.first()` in-viewport.
- Screenshot baseline committed: `frontend/e2e/maintenance-checkpoint-cleanup-wizard.spec.ts-snapshots/checkpoint-cleanup-1024x768-maintenance-linux.png` (maxDiffPixelRatio 0.02).
- §2.9 lever ledger (worker analysis): lever-6 (container top padding, ~−16px) APPLIED; lever-7 (inner gap, ~−8px) APPLIED; lever-8 (section gap, ~−8px) APPLIED. Static estimate cluster (712–738px) diverged from runtime (768px) — runtime wins per amend v3. Remaining gap ≈ one lever-8 equivalent. **Decision round required: re-amend §2.9 ledger (new lever) or capture the last ~8px — designer/developer call, NOT test-side.**
- Orientation flip @1023px (PASS): vertical stepper active, horizontal selector absent. Continue-in-viewport assertion scoped to ≥1024px only — vertical stacked-header layout pushes the footer below fold **by design** (spec §5.2).
- w2 left as a **hard assertion = standing red budget gate** until the re-amend round lands (soft-assert would let a spec violation go green).

## A11y (W8 verification)
- Axe scan (loaded page, both orientations): **NOT zero** — 3 violation classes / 9 nodes:
  - `button-name` (critical) ×1
  - `color-contrast` (serious) ×2
  - `dlitem` (serious) ×6
- W8 `$text-muted #8b96a8` on bg-card: **no flagged node** in this scan (the 2 color-contrast nodes are other tokens; exact token identification pending designer round). Direct ≥4.5:1 confirmation therefore unproven-but-unflagged.
- w4 encoded with `expect.soft` → reports violations without blocking the pack; remediation is production-side.

## Wizard Navigation — per-test results (single invocation, 2 specs, 2.8 min wall, ≤5-min cap)
Legacy spec `maintenance-checkpoint-cleanup.spec.ts` — 11 of 15 tests updated to navigate the wizard (`ck-continue-btn` Step 1→2; `mat-step-header` clicks Step 2→3→4). 15/15 PASS:
1–3 gear-menu/availability P · 4–5 status dual-flavor P · 6 3-tone badge map P (`details[open]` forced via `page.evaluate` — Playwright visibility race) · 7 dry-run counts P · 8 execute-cancel P · 9 execute+poll→"Run completed" P (explicit Step-4 header click after poll — auto-advance races Material focus restoration in headless) · 10 stale dry-run P · 11 409-adopt P · 12 interrupted P · 13 cross-origin 403 P (real-daemon, `origin_not_trusted`) · 14 maintenance_disabled 503 P · 15 post-budget continuation P (virtual clock, "Run completed" wording).

New spec `maintenance-checkpoint-cleanup-wizard.spec.ts` — 4/5 PASS:
- w1 Status Strip visible + DOM order before stepper P
- **w2 AC-15 runtime FAIL (STOP)** — 768 > 760
- w3 orientation flip @1023px P
- w4 axe scan P-soft (3 classes reported above)
- w5 happy path P

Preserved anchors: `ck-last-skipped-summary` (Step-1 landing, legacy test 6 ~L354), `ck-last-run-skipped-summary` (Step-4), `ck-continue-btn`, `ck-back-btn`, `ck-back-to-start-btn`. Amend-v4 testid splits honored (component source = truth).

## Happy-Path Evidence (web automation)
`frontend/e2e/screenshots/ck-wizard-happy-path-step{1,2,3,4}.png` (4 × 1024×768).
Flow: open page → Status Strip visible → Continue (Step 1) → dry-run (Step 2) → review summary → Continue (Step 3) → execute → poll → Step 4 "Run completed" + "Bytes freed" + `ck-back-to-start-btn` visible.

## Isolation & Leak Safety
- Worktree venv isolation proven: `daemon.__file__` → `/home/nea/ensemble-worktrees/ck-ui-test/daemon/__init__.py`.
- Isolated stack 4299 (FE) / 8099 (daemon) / 15432 (disposable PG); `ENSEMBLE_SELF_ENV=dev` on all backend processes; ports 4199/8079 untouched; 8088 never approached.
- Post-run leak checks clean: no `pg_e2e_maint_*` dirs, no daemon pid files, no orphan uvicorn; ports freed.
- Jest worker HEAD discipline: f469dccf before/after (unchanged); e2e commit is the only tree change.

## Findings / Action Needed
1. 🔴 **AC-15 runtime breach 768 > 760 (8px over)** — STOP RULE honored; needs spec re-amend round (§2.9 ledger) or a sanctioned production lever. Do NOT force-pass test-side.
2. 🟠 **Material stepper focus-restoration oscillation** — production defect family: focus on a button inside a NON-ACTIVE step body makes the stepper emit `selectionChange` → brief Step1↔Step2 oscillation with the `[selectedIndex]` binding. Tests bypass via `mat-step-header` clicks. Separate commission candidate (likely `[cdkStepperFocusable]` config or signal-only stepper).
3. 🟠 **Axe violations 3 classes / 9 nodes** — production remediation round (designer/developer).
4. 🟢 `frontend/package.json` `test` script uses deprecated `--testPathPattern` → Jest 30.3.0 rejects it before any spec runs. Workaround: invoke `node_modules/.bin/jest <paths>` directly. Script fix out of this commission's scope.
5. 🟢 4th spec `checkpoint-cleanup.service.spec.ts` (51 it) lives in the same dir — excluded by scope; dir-scoped Jest runs would sweep it (142+51=193).

## Regression Gates
- Unit: **142/142** (~6s, both cold and warm runs within 120s cap).
- Other maintenance e2e: regression set **EMPTY** (verified by grep); `playwright.maintenance.config.ts` untouched (no shared-config semantic drift).

## Documentation Updated
- RESULTS/2026-10-02-ck-redesign-e2e-ac15.md (this file)
- PACKS.md — new commission section (3 pack rows)
- LESSONS/2026-10-02-ck-e2e-wizard-navigation-gotchas.md

## Code Changes Summary
- Commit `b56e290a` `test(e2e): checkpoint-cleanup wizard navigation + AC-15 runtime assertions (ck-redesign-2026q4)` — 9 files +646/−10: legacy ck spec (wizard nav), new wizard spec (5 cases), baseline PNG, 4 evidence PNGs, package.json+lock (axe devDep `@4.13.0`).
- Zero production (`frontend/src/`) changes. No push.

## Overall Status
- Unit Tests: ✅ PASS (142/142)
- E2e pack: ⚠️ 19/20 — w2 AC-15 gate RED by measurement
- A11y: ⚠️ 9 nodes / 3 classes (soft-reported)
- **Testing complete: ❌ NOT READY** — blocked on the AC-15 spec re-amend decision round (production-side). Tester lane deliverables complete; w2 stands as the standing red budget gate.


---

## Round 2 — v5 FINAL VERIFICATION (2026-10-02T02:20Z) — commit `d8ee196e`

Inputs: spec amend v5 `b5fa06db` (pin `c74a6db9`) + developer remediation `6d79bc2d` (4 component files +88/−99: `editable="false"` ×2 steppers, rose/blue AA tokens, Status Strip flattened `<dl>`, touch-ups). Ancestry verified `6d79bc2d → b5fa06db → b56e290a → f469dccf`.

### w2 → v5 no-overflow gate: PASS
Encoded exactly per spec (expect.poll `{sh, ch, ok}` + 3 co-assertions + screenshot). Measured at 1024×768: **scrollHeight=768, clientHeight=768 → noOverflow PASS**. The old ≤760 constant was structurally unsatisfiable (DOM invariant `scrollHeight = max(clientHeight, contentHeight)`); v5 demoted it to an informational static target.

### Baseline regen: BLESSED (sanctioned deltas only)
Old baseline extracted from `b56e290a` pre-regen; regen via `--update-snapshots=all` (default skips PASSING screenshots — gotcha logged). Programmatic diff (eyeball verdict; vision-tool pass unavailable — image confinement to main workdir):
- 8894 px changed (1.13% < 2% cap) · bbox (36,212)–(877,351) · **0 pixels outside bbox** — chrome byte-frozen (header, title, stepper frame, Step-1 card)
- 358 px `#3b82f6→#5b9bf5` (stepper selected indicator + underline) — sanctioned §3
- 0 px rose on this view (rose lives on Step-3 echo, absent from Step-1 baseline) — consistent
- 8536 px strip-internal glyph antialiasing — sanctioned dt/dd restructure
- Overlay evidence: worktree `.agents/tester/RESULTS/2026-10-02-ck-baseline-diff.png` (untracked)

### Full pack re-run: 20/20 PASS (2.8 m, single invocation, leak checks clean)
w1 strip DOM order · **w2 no-overflow PASS** · w3 1023px flip · w4 axe (soft, see below) · w5 happy path (4 evidence PNGs refreshed: strip reflow, editable-free headers, run-again banner `#5b9bf5`).

### Unit re-run on `6d79bc2d`: 142/142 PASS (5.8 s)
Remediation validated by existing pins — zero pin/test churn.

### Axe re-scan: ⚠ counts persist 1/2/6 — BUT nodes migrated (report-only per STOP rule)
- **button-name ×1**: maintenance cause DEAD (`hasEditableButton:false` ×4 verified — no edit-pencil). Residual = `.bell-button` app-header notifications icon (mattooltip not an accessible name) — pre-existing, OUT OF ck scope.
- **color-contrast ×2**: in-section tokens FIXED; residual = `.queue-count` + `.version` app-header `#64748b` on `#0f172a` (globals untouched by v5) — pre-existing, OUT OF scope.
- **dlitem ×6 — UNRESOLVED, IN-SCOPE**: `<dl role="status">` ARIA role override orphans the dt/dd children (semantics quirk the v5 flatten could not satisfy). Fix shape: outer `<div role="status">` wrapping inner plain `<dl>` — production change, correctly NOT made.

### Commit
`d8ee196e` `test(e2e): AC-15 no-overflow gate + baseline regen post a11y remediation (ck-redesign-2026q4 v5)` — 6 files +42/−30 (wizard spec, baseline, 4 evidence PNGs). HEAD now `d8ee196e`; no push.

### Round-2 verdict
**20/20 e2e + 142/142 unit + baseline blessed + 2-of-3 axe causes dead in-section.** Lane GREEN pending leader decisions: (a) dlitem — `<div role=status>` restructure vs documented waiver; (b) 3 pre-existing app-header a11y nodes — own ticket (not ck-redesign scope).


---

## Round 3 — v6 CLOSURE CONFIRMATION (2026-10-02T02:45Z) — NO COMMIT, tip `40536f57`

(worktree copy; canonical mirror in shared checkout `.agents/tester/`)

- Full pack **20/20** (2.7 m) · w2 no-overflow 768=768 PASS.
- **Baseline PASSED AS-IS** — d8ee196e PNG unchanged (md5 4b98a8cec2716d990432a6d916ea3eef); zero-visual-delta claim holds.
- **Axe dlitem ×6 → 0 in-section (v6 CONFIRMED)**; remaining 3 = app-header pre-existing (`.bell-button`, `.queue-count`, `.version` — follow-up ticket). In-section: **0**.
- Unit **142/142** @ `40536f57` (independently confirmed).
- Side note: wrapper boundary → 1–2px drift in auto-regenerated happy-path PNGs (≤0.78%/file, content identical) — REVERTED to committed state (auto page.screenshot rewrite ≠ sanctioned delta).
- Files changed: NONE · HEAD `40536f57` unchanged · leak checks clean.

**LANE VERDICT: LANE COMPLETE @ `40536f57` — merge-gate ready.**
