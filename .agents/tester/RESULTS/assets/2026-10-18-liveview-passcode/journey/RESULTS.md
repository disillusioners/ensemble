# E2E — ORIGINAL-SCENARIO customer journey + browser spot-check — live-view passcode
Commission items 1+2 · 2026-10-18 · worktree `/home/nea/ensemble-src-wt-liveview-passcode` @ feature/live-view-passcode (1121f2604, v0.21.0)
Runtime: **~12.5 min** wall (boot 20s · mint 6s · journey 4s · browser ~40s incl. 2 debug cycles)

**Fence:** mktemp `/tmp/lvjourney.*` (escape-guarded) · dev DB ensemble_dev (pre-boot assert) · drill-only Fernet key · port 8081 (free-check) · local TLS edge 8443 for the browser lane · kill-only-what-I-started (both pids verified by bound port before TERM) · 8088 untouched · zero implementation-code changes.

**Boot corroboration** (journey/boot.log): `[Auth] derived public_path_set (18): [... '/views/livez', '/views/public/assets/mermaid/mermaid.min.js', '/views/public/{root_name}/{rel_path:path}', '/vscode']` + `[LiveViews] passcode substrate ready: strike_ceiling=10 grant_ttl=86400s rate_limit=5/5-per-min`.

---

## Item 1 — ORIGINAL SCENARIO: "A customer from another company can easily view the live view without login via passcode; view_link ALWAYS returns the passcode."

| # | Sub-item | RESULT | Evidence |
|---|----------|--------|----------|
| J1 | view_link TOOL-path mint; 5-field shape ALWAYS incl. passcode | **PASS** | j1-mint.txt |
| J2 | GET minted view unauthenticated → 200 zero-JS interstitial, CSP byte-equal, no leaks | **PASS** | j2.headers / j2.body |
| J3 | Unminted sibling → uniform daemon 404 (no existence leak) | **PASS** | j3.headers / j3.body |
| J4 | Wrong code POST → 200 failure re-render (unit-pinned), NO Set-Cookie, no attempt-count leak | **PASS** | j4.headers / j4.body |
| J5 | Correct code POST → 303 PRG + grant cookie with EVERY attribute, path-scoped, Max-Age=86400 | **PASS** | j5.headers |
| J6 | GET with grant → 200 VIEW content (markdown wrapper), not the interstitial | **PASS** | j6.headers / j6.body |
| J7 | Cookie persistence + PATH-SCOPING (sibling re-gates) + mermaid flow incl. byte-identical `?raw=1` | **PASS** | j7a–j7e transcripts |
| J8 | tmp-images public lane → served bytes sha256-identical to seeded blob, sidecar MIME image/png | **PASS** | j8a–j8c transcripts |
| J9 | Controls: `/api/instances` → 302 login; direct legacy lane → 302 login (public lane stayed open) | **PASS** | j9a / j9b headers |
| J10 | TTL: Max-Age=86400 on the wire; ~24h expiry logic cited (not waited) | **PASS** | j5.headers + unit cite |

**J1 mint seam: the TOOL seam** — real `view_link` langchain tool via `.invoke()` over the genuine services wired to the SAME ensemble_dev DB + SAME drill key as the running daemon (fake-manager facade carries only the two seams the tool reads: `manager.live_views_service` + `manager._view_passcode_service`). The drill used the service fallback; this run exercised the tool itself. 5-field labeled-line shape (Public URL / Passcode / Authenticated URL / Generation / Locked) verified on all 3 mints (hello.md, diagram.mmd, tmp-images id); idempotent repeat call returned the SAME passcode; no rotation-required fallback was needed.

**J2 header assertions matched the unit pins byte-structurally:** CSP exactly `default-src 'none'; script-src 'none'; style-src 'nonce-<32hex>'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'` + `X-Content-Type-Options: nosniff` + `Cache-Control: no-store` + `Referrer-Policy: no-referrer`; zero `<script` in body; viewport meta; `form method="post" action="/views/public/journeyroot1/hello.md"` with the path embedded; passcode absent from page source; no attempt-count strings.

**J5 Set-Cookie observed on the wire (complete):** `view_grant=<fernet>; Path=/views/public/journeyroot1/hello.md; HttpOnly; Secure; SameSite=Lax; Max-Age=86400` — full-match regex asserted (attribute set AND path scoping to the exact view path).

**J7 path-scoping detail:** repeat GET with same cookie → 200 (persistence); sibling `diagram.mmd` with the hello.md cookie in play → gate again (interstitial, path-scoped cookie not sent); its own flow → 303 + own-scope cookie → mermaid wrapper (title + `class="mermaid"` + vendored `mermaid.min.js` ref) + `?raw=1` byte-identical to the fixture (cmp). Rendering needs NO CDN — the mermaid asset is vendored same-origin (gate-free alias per T2.10). Note: the plain-markdown wrapper (hello.md) DOES load marked+DOMPurify from cdn.jsdelivr.net (reachable; pre-existing Phase-2 design, unchanged by passcode).

**J10 citation:** `tests/unit/services/test_view_passcode_service.py:1596` `test_issue_sets_exp_to_now_plus_ttl` (+ expiry-rejection pins at :1632/:1660/:1685) prove the 86400s grant TTL incl. rejection one-second past expiry; the wire observation (J5) pins the attribute.

## Item 2 — Headless-browser spot-check (chrome 154 headless via bundled puppeteer, local TLS edge 8443)

| # | Sub-item | RESULT | Evidence |
|---|----------|--------|----------|
| B1 | Interstitial loads; `document.scripts.length === 0` in a REAL browser | **PASS** | b1-interstitial-desktop.png |
| B2 | Type passcode → submit → navigation → view renders (h1 "Journey Hello"; marked CDN loaded) | **PASS** | b2-view-desktop.png |
| B3 | Wrong code in-browser → "That code was not recognised.", still gated, no view | **PASS** | b3-wrong.png |
| B4 | Mobile 390×844: no horizontal overflow (390≤390), input+button visible in viewport | **PASS** | b4-interstitial-mobile.png |
| B5 | Mermaid view: gate flow → wrapper → 1 `<svg>` rendered via vendored same-origin asset (no CDN needed) | **PASS** | b5-mermaid.png |

## Defects / observations (severity-tagged; NOTHING fixed, implementation untouched)

- **NOTE (harness, not product) — `Origin: null` under untrusted TLS:** Chrome coerces the form-submission origin to `Origin: null` (and drops Referer) when the serving cert is not trusted (self-signed drill edge; SPKI-pin attempt did not restore real origins in this headless build; no NSS certutil on host to do a proper CA import). The daemon's response is the D6:77 same-host check failing CLOSED: `403 {"error":"forbidden"}` — correct-by-design (an opaque origin is unverifiable). Real deployments ride a trusted edge (door.ensem.dev) and are unaffected. Anchor: `daemon/routers/live_views.py:721-763` (`_same_host_origin_check`) + `:1527`. B-flow verification injected the same-host `Origin` via CDP (disclosed harness emulation) — the grant mechanics themselves are curl-proven unmodified (J4/J5).
- **NOTE (pre-existing, unchanged by passcode) — markdown wrapper CDN dependency:** plain-markdown views load marked@12.0.2 + DOMPurify from cdn.jsdelivr.net (`daemon/services/live_views.py:1556-1560`); the CSP allowlists them by design. Offline LAN customers would get raw-source rendering for .md views (mermaid .mmd views are immune — vendored same-origin asset). Flagged for awareness only; out of scope.
- **NOTE (operational) — drill-DB passcode rows persist:** passcode rows live in the shared ensemble_dev `live_view_passcodes` table keyed (root_name, rel_path); a row minted under a DIFFERENT later key surfaces the rotation-required envelope via the tool (typed `Error: ...` → documented recovery `refresh=True`). journeyroot1 was fresh this run (no recovery needed). No action.

## Evidence inventory (this directory)

- HTTP transcripts: `j1-mint.txt`, `j2..j10` → `jN.headers` + `jN.body` (17 files incl. j7a–j7e, j8a–j8c, j9a/j9b)
- Screenshots: `b1-interstitial-desktop.png` (1280×800), `b2-view-desktop.png` (1280×800), `b3-wrong.png` (1280×800), `b4-interstitial-mobile.png` (390×844), `b5-mermaid.png` (1280×800)
- Logs: `boot.log` (auth public_path_set + substrate lines), `edge-proxy.log` (TLS edge incl. `Origin: null` debug capture), `b-run.log`
- Scripts (fenced, rerunnable): `scripts/boot.sh`, `scripts/mint_tool.sh`, `scripts/journey.sh`, `scripts/tls_proxy.py`, `scripts/browser.js`, `scripts/cleanup.sh`
- State pointer `.drill-tmp` removed by cleanup (fence deleted)

## Verdict

**Item 1 (original scenario): PASS — 10/10.** A customer with only the public URL + 6-char passcode completes the journey with zero login/OAuth contact; `view_link` ALWAYS returns the passcode (5-field shape, tool seam). The rest of the world stays gated (J9).
**Item 2 (browser spot-check): PASS — 5/5.** Zero-JS interstitial confirmed in a real browser; form interaction works end-to-end; mermaid renders via the vendored same-origin asset; mobile layout clean.
