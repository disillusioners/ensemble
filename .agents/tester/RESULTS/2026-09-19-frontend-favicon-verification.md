# Test Report: frontend favicon set — `feature/frontend-icon` @ f02a1340
Date: 2026-09-19T06:02Z
Instance IDs: worker 3395e660-4adb-4f55-a1d5-3b9e8dfbc299 (icon-verify)
Requested by: user (independent re-verification; developer claims not trusted)

## Summary
- Tests: 4 | Pass: 3 | Mixed: 1 (Test 3 — functional pass + 1 behavioral defect)
- Build: exit 0 both tsc and npm run build; warnings exactly 10 = known baseline (1× NG8113, 2× deprecation, 7× budget)
- All 6 favicon assets built, correct dimensions, ICO multi-frame, SVG well-formed
- **Defect (🟠 important): Chromium never displays favicon.svg** — ICO link declared second without `sizes` attrs; SVG request aborted (net::ERR_ABORTED), netlog shows only favicon.ico fetched
- Regression guard: no missing static assets vs index.html/angular.json
- Quick fixes applied: none (defect is behavioral link-declaration, out of quick-fix scope)
- Quarantined: 0

## Scope Decision
Scoped verification, single feature branch, asset-only change (8 files: 5 binaries + svg + index.html + helper script). No pack fan-out — one worker, one throwaway worktree (`/tmp/agents-ens-icon-verify`), node_modules symlinked from main checkout. Foreign main checkout (`feature/chat-lane-followups`, active session) untouched; worktree cleaned up; port 10091 freed; 8088 never touched. Runtime ~5 min.

## Results

### Test 1 — Build: PASS
- `npx tsc --noEmit -p tsconfig.app.json` exit 0 (1.8s)
- `npm run build` exit 0 (13.6s); exactly 10 warnings = baseline
- dist/frontend/browser/: favicon.ico 6896B, favicon.svg 1504B, apple-touch-icon.png 14280B, icons/favicon-16.png 679B, favicon-32.png 1738B, favicon-48.png 2971B — all 6 present

### Test 2 — Asset Integrity: PASS
- favicon.ico: 3 frames {16×16, 32×32, 48×48} via PIL `info['sizes']`
- PNGs exact: 16×16, 32×32, 48×48, 180×180
- favicon.svg well-formed XML, all files > 100B
- Note: PIL ICO frame-iteration returns only first frame — `info['sizes']` is the correct check (worker initially false-alarmed, corrected)

### Test 3 — Web Smoke: MIXED (criteria met; 1 defect)
- Angular shell renders; no JS exceptions; 17 console errors all /api/* 404s (daemon not running — expected)
- Head contains all 4: theme-color meta, SVG icon link, ICO icon link, apple-touch-icon link
- HTTP 200: /favicon.svg, /favicon.ico, /apple-touch-icon.png
- **DEFECT: browser fetched favicon.ico for the tab icon; favicon.svg request aborted (net::ERR_ABORTED)**
  - Scope: headless Chromium probe only; other engines not tested
  - Worker's mechanism claim: both `rel="icon"` links lack `sizes` → Chromium picks last-declared (ICO second)
  - Suggested fix: `sizes="any"` on SVG link, or `rel="shortcut icon"` on ICO, or reorder (SVG last)

### Test 4 — Regression Guard: PASS
- angular.json `{"glob": "**/*", "input": "public"}` copies all 6 files
- All index.html-referenced local assets present in dist; none missing

## Action Needed
- [ ] Dev decision before merge: fix ambiguous icon declaration (add `sizes="any"` to SVG `<link>` or reorder) if SVG was intended as the primary tab icon; ICO-only display may be acceptable if not
- Severity: 🟠 important — tab still shows a valid new multi-size ICO (not blank/broken), so not a hard blocker

## Overall Status
- Build/Assets/Regression: ✅ PASS
- Web smoke: ✅ literal criteria PASS / ⚠️ intent-level defect (SVG never displayed on Chromium)
- **Verdict: assets correctly built and shipped; one behavioral finding routed to dev for merge decision**
