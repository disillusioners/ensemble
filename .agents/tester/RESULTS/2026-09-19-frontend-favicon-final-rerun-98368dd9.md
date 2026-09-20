# Test Report: FINAL harness re-run — `feature/frontend-icon` @ 98368dd9
Date: 2026-09-19 (fourth and final in arc: verification → re-gate → discriminator → fallback re-run)
Instance: worker 3395e660 (icon-verify; same netlog harness throughout)
Change under test: ICO `<link>` removed (1 file, 1 deletion); SVG-only `rel="icon"`; favicon.ico file still ships via public/ glob (undeclared)

## VERDICT: PASS — SVG fetched for the tab. Merge proceeds.

## Netlog counts (Chrome 153.0.8010.52)
| Run | Mode | /favicon.svg | /favicon.ico | /apple-touch-icon.png | "headless" str |
|---|---|---|---|---|---|
| 1 | headed | 6 | 0 | 0 | 0 |
| 2 | headed | 6 | 0 | 0 | 0 |
| 3 | headed | 6 | 0 | 0 | 0 |
| 4 | headless (secondary) | 6 | 0 | 0 | 115 |

Only tab-icon URL requested across all 4 runs: /favicon.svg. Mode verification: no --headless in headed argv; osascript visible-window check; per-run isolated --user-data-dir; cache-bust.

## Side checks — all PASS
- /favicon.ico direct: 200 image/x-icon; file ships in dist (6120B, public/ glob) — legacy path intact for clients that request it
- /favicon.svg direct: 200 image/svg+xml (1504B)
- Built dist index.html head: theme-color + SVG icon (sizes="any") + apple-touch-icon (180x180); ICO link genuinely ABSENT (grep count 0; live-DOM check false)
- Build: exit 0 (12.9s), exactly 10 warnings (baseline)
- Shell clean; console = 17-error baseline exactly; 0 page errors

## Notable browser-behavior finding
Chrome 153 did NOT convention-fetch /favicon.ico in any run despite no `<link>` for it — a declared SVG icon suppresses the ICO convention auto-request. Legacy direct-request path still serves 200. Behavior consistent across headed AND headless (unlike the two-link case, both modes now pick SVG).

## Arc summary (4 runs, one harness)
1. f02a1340 (two links, no sizes): ICO-only; SVG aborted — defect found
2. 7673a049 (sizes disambiguation): still ICO-only, headless AND headed — fix class refuted
3. Discriminator: mode-independence proven; option-2 fallback routed
4. 98368dd9 (SVG-only declaration): SVG fetched in every mode — GATE PASS

## Hygiene
Server PID 16313 killed; port 10091 freed; 8088 never touched; worktree /tmp/agents-ens-icon-verify4 removed; main checkout (foreign session) untouched. Runtime ~3.5 min.

## Overall Status
- SVG-FETCH GATE: ✅ PASS (headed 3/3 + headless 1/1)
- Build/assets/shell/regression: ✅ all PASS
- **Merge proceeds per pre-committed verdict semantics. Arc closed.**
