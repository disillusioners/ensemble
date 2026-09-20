# Test Report: SVG favicon fetch re-gate — `feature/frontend-icon` @ 7673a049
Date: 2026-09-19 (follow-up to 2026-09-19-frontend-favicon-verification.md)
Instance: worker 3395e660 (icon-verify, reused — same harness for valid before/after comparison)
Gate defined by caller: favicon.svg must be fetched for the tab icon in this harness (merge-blocking)

## Headline
**SVG-FETCH GATE: FAIL.** Chrome 153 headless (same netlog harness that caught the original ERR_ABORTED) does NOT fetch favicon.svg — with the new `sizes="any"` disambiguation the SVG is never even requested (stronger than the original abort). ICO is fetched every run.

## Netlog evidence (3 runs, --incognito + cache-bust)
| URL | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| /favicon.svg | 0 | 0 | 0 |
| /favicon.ico | 6 | 5 | 6 |
| /apple-touch-icon.png | 0 | 0 | 0 |

## Side checks
- Rebuild: PASS — exit 0, 14.8s, exactly 10 warnings (baseline). Dist index.html carries the disambiguated declarations (sizes="any" SVG first, sizes="16x16 32x32 48x48" ICO second, apple-touch 180x180, theme-color).
- ICO frames: PASS — PIL info['sizes'] = exactly {(16,16),(32,32),(48,48)}, no dupes; file 6896→6120B (dedup effective).
- ICO 200-resolvable fallback: curl HEAD → 200 image/x-icon 6120B.
- Shell clean: app-root renders, 0 page errors, 17 console errors all expected /api/* 404s.

## Validity caveat (material to the merge decision)
- Measurement is HEADLESS Chrome 153 only. The dev's shipped pattern (SVG `sizes="any"` first + ICO sized) is the standard documented pattern for HEADED Chrome, which generally prefers SVG. Two hypotheses remain undiscriminated:
  (a) headless Chrome categorically prefers ICO → harness limitation, fix fine for real users;
  (b) selection genuinely doesn't flip → fix broken everywhere.
  Cheap discriminator the dev may run before choosing a fallback: same harness with headed Chrome (or one manual headed check).
- Worker transparency: an interim Playwright page-event observation showed one favicon.svg [200] (briefly suggesting "fixed"); cross-validation against netlog proved it a fluke — Playwright page events do NOT surface browser-internal favicon fetches (5/5 runs blind). Netlog is the only reliable observer in this harness.

## Routing (per caller's pre-specified FAIL path)
Fallback fix goes back to the developer. Worker's options, least→most invasive:
1. Swap link order — ICO first (`rel="shortcut icon"`), SVG last (harness's observed behavior: last rel="icon" wins in headless)
2. Drop the ICO link — SVG only; Chromium's built-in /favicon.ico request still resolves for legacy clients
3. rel="mask-icon" — different use case (Safari pinned tab), not recommended here
Any chosen fallback needs a follow-up run in this same harness.

## Environment hygiene
Main checkout (foreign session) untouched; worktree /tmp/agents-ens-icon-verify2 removed; port 10091 freed; 8088 never touched. Runtime ~4 min.

## Overall Status
- SVG-FETCH GATE: ❌ FAIL (merge-blocking per caller's definition)
- Rebuild: ✅ PASS · ICO frames: ✅ PASS
- **Verdict: NOT merge-ready on the gate as defined; decision + discriminator headed-Chrome run belong to the developer**
