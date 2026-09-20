# Test Report: headed-Chrome discriminator — `feature/frontend-icon` @ 7673a049
Date: 2026-09-19 (third in arc: verification → re-gate → discriminator)
Instance: worker 3395e660 (icon-verify, reused; same netlog harness, only change headless=False)
Purpose: settle (a) headless-prefers-ICO harness limitation vs (b) selection genuinely doesn't flip. Pre-committed decision tree, hard cap.

## BRANCH: (b) — headed Chrome ALSO ICO-only. Hypothesis (a) refuted.

## Netlog counts (HEADED, real GUI window, 3 runs)
| URL | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| /favicon.svg | 0 | 0 | 0 |
| /favicon.ico | 6 | 6 | 6 |
| /apple-touch-icon.png | 0 | 0 | 0 |

No "favicon.svg" string anywhere in any run's netlog; ICO confirmed via netlog URL grep.

## Headed-mode confirmation (multi-signal)
- Chrome 153.0.8010.52 (--version)
- Launch argv contained NO --headless (full flags logged: --incognito, per-run --user-data-dir=/tmp/chrome-headed{N}, --log-net-log, --net-log-capture-mode=Everything, window size/position)
- Visible window: osascript System Events query returned Google Chrome in visible processes (Run 1)
- Multi-process renderer tree (Helper Renderer, GPU, network service) — headed characteristic
- 0 occurrences of "headless" in every netlog
- Per-run isolated user-data-dirs

## Side checks
- Build: exit 0 (13.4s), exactly 10 warnings (baseline)
- Shell clean: app-root rendered (12353 chars), title "Frontend", 0 page errors
- Console: 17 errors = baseline (15× /api/* 404s + 2 app-wrapped health/agents messages) — no new noise
- All 4 head declarations present with new sizes attributes (verified in headed probe)

## Conclusion
The sizes="any" disambiguation does not flip Chromium's icon choice in EITHER mode. On Chrome 153 (headed and headless), this declaration set renders the multi-size ICO on the tab; the SVG is served 200 but never requested. Per the pre-committed tree: caller routes ONE fallback to the developer (option 2: drop the ICO <link>, SVG-only; /favicon.ico stays convention-served for legacy clients), one harness re-run after, then merge regardless — both artifacts carry the new design, user-visible outcome safe either way.

## Hygiene
Server PID 15053 killed; port 10091 freed; 3 Chrome test instances killed; worktree /tmp/agents-ens-icon-verify3 removed; main checkout (feature/chat-lane-followups, foreign session) untouched; 8088 never touched. Runtime ~4 min.

## Overall Status
- Discriminator: ✅ executed as specified — BRANCH (b)
- SVG-FETCH GATE: remains ❌ FAIL (now confirmed mode-independent)
- Next (caller-owned): dev applies fallback option 2 → one harness re-run → merge regardless
