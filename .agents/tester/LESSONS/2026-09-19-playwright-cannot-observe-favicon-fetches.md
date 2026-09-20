# Lesson: Playwright cannot observe favicon fetches — use standalone Chrome netlog

Date: 2026-09-19
Context: frontend favicon verification arc (`feature/frontend-icon` @ f02a1340 → 7673a049)

## Finding
Playwright's page-level network events (`page.on('response')` / request events) do NOT surface browser-internal favicon requests in headless Chromium 148 / Chrome 153. 5/5 runs returned "(none)" for both SVG and ICO icons while the browser demonstrably fetched the ICO at the network level.

- A single positive observation (one `favicon.svg [200]` via Playwright in the re-verify run) was a **fluke** — it nearly produced a false PASS on a merge-blocking gate.
- Aborted favicon requests CAN surface as failure events (the original run's `net::ERR_ABORTED` capture), which makes Playwright evidence asymmetric and misleading: failures visible, successes invisible.

## Rule
For ANY favicon / browser-internal-asset verification in this repo:
1. Use standalone Chrome with `--log-net-log=<file>` — netlog is the only reliable observer.
2. Never gate on Playwright page events for favicons; at most use them as a weak negative signal.
3. Cross-validate any positive Playwright favicon observation against netlog before trusting it.
4. Controls that worked: `--incognito` + cache-bust query param + ≥3 runs.

## Secondary findings (same arc)
- PIL ICO frame iteration returns only the first frame even for multi-frame ICOs — use `Image.open(path).info['sizes']` for the full frame set (verify dedup by exact set comparison).
- Chrome 153 icon selection (headed AND headless — verified IDENTICAL in the follow-up discriminator run): with two `rel="icon"` links, it fetched only the ICO in BOTH tested declaration shapes (no sizes; SVG sizes="any" first + sized ICO second). An earlier "headless≠headed, scope to mode tested" caveat here was REFUTED 2026-09-19 by 3× GUI-window netlog runs (osascript-verified visible window, no --headless in argv, 0 "headless" strings in netlog — Chrome 153.0.8010.52): svg=0/ico=6 per run in headed mode, identical to headless. RESOLVED by the option-2 fallback (drop the ICO `<link>`, SVG-only — commit 98368dd9): final re-run fetched /favicon.svg in 3/3 headed + 1/1 headless runs, ICO count 0. Two closing facts: (1) a declared SVG icon makes Chrome 153 fetch the SVG in BOTH modes; (2) Chrome 153 does NOT convention-fetch /favicon.ico when an SVG icon is declared — the legacy path serves only direct requesters (file must still ship via the public/ glob, which it does).

Artifacts: RESULTS/2026-09-19-frontend-favicon-verification.md, RESULTS/2026-09-19-frontend-favicon-reverification-7673a049.md, RESULTS/2026-09-19-frontend-favicon-headed-discriminator.md, RESULTS/2026-09-19-frontend-favicon-final-rerun-98368dd9.md
