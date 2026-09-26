# FE E2E Lessons — Settings Scroll Acceptance Run (2026-09-26)

From commission: settings-scroll fix acceptance @ `dd14d975` (visual gate PASS; full report in RESULTS/2026-09-26-settings-scroll-fix-verification.md).

## 1. Playwright on this host needs `install-deps`, not just `install`
`npx playwright install chromium` alone is insufficient on this dev host: headless chromium then fails on missing `libatk-1.0.so.0`. Fix: `npx playwright install-deps chromium` (passwordless sudo; additive OS packages only). Budget ~1 min. Any future FE browser pack should pre-flight this.

## 2. The R15 snapshot-create control is an Apply-gated RADIO GROUP, not a slide toggle
- DOM: `input[type=radio][name="snapshot-create-preference"]` (values on/off) + a section-level Apply button.
- A bare radio click fires **zero** network calls (by design) and only raises the "Unsaved changes" dirty hint; persistence flows ONLY through Apply (PUT `/api/settings/snapshot-create`).
- Persistence contract verified 2026-09-26: after Apply, full reload → GET returns `{"enabled":true}` and DOM shows Enabled. E2E scripts that "click the toggle and expect a PUT" will false-fail; scripts that "click radio and expect persistence without Apply" will false-report RESET (this exact artifact occurred and was corrected mid-run).

## 3. Settings page scroll mechanism = the `.settings-container` ELEMENT, not the window
The fix (dd14d975) made the settings container the scroll container: window `scrollHeight == clientHeight` (never window-scrollable); `.settings-container` carries `overflow-y: auto` (compiled into lazy chunk as `min-height: 0` on `:host` + `overflow-y`). E2E scroll assertions must drive `el.scrollTop = el.scrollHeight` and read `el.scrollTop/el.scrollHeight` — `window.scrollTo`-based assertions will false-fail. Also: screenshots taken after element-scroll may show no scrollbar glyph (overlay scrollbars) — DOM measurements are the authoritative scrollability evidence, screenshots prove resulting visibility.

## 4. Dev bring-up scrub pattern held (no live-PG probe)
Standalone `#!/bin/bash` wrapper (never sourced, never /bin/sh), dynamic 3-pass POSTGRES_* unset, `POSTGRES_SURVIVORS=0` echo-verified BEFORE `exec ./dev.sh`. Daemon then loads its own `.env` (POSTGRES_DB=ensemble_dev) and logs `localhost:5432/ensemble_dev`. Zero incidents; live 9797 / demo 7979 PIDs stable across the whole session. Reuse this pattern verbatim for any future boot.
