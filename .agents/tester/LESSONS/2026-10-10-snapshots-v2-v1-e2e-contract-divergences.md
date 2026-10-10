# Snapshots v2 — v1 e2e pack contract divergences (daemon-lane run @ c7b467444) + lane recipe confirmation

Date: 2026-10-10 · Run: RESULTS/2026-10-10-snapshots-v2-daemon-lane-e2e.md

## Lesson 1: v1 e2e packs rot at the LOCATOR/BEHAVIOR contract layer when a redesign ships — diagnose by census, not by re-run
The snapshots v2 merge shipped with the 12-test v1 spec pack green at collection depth and the two legs the gate knew about (11a/11b) rewritten. At full daemon depth, 5 MORE legs failed — all the same rot class the gate already met once (D2 backdrop contract): the v1 spec encodes v1 widget choices that Design A replaced. Deterministic (flaky=0), so quarantine does NOT apply — this is test-debt, fix is rebase-or-retire.

Divergence map (source-line evidence from the run):
1. mat-radio toggle → button (`Snapshot creation: ON`, snapshots component :40/:44; zero mat-radio in v2 templates). Kills v1 legs #2 and #7; #7's PUT assertion was never reached (census: zero PUT /api/settings/snapshot-create; v2 polls GET ×10).
2. Accessible name `Clear all filters` (aria-label, :277) — v1 regex `/Clear filters/i` can never match non-contiguous substrings. Use exact/`{ name: 'Clear all filters' }` or a substring of the real label.
3. `ul.metrics-list` markup removed; `[data-test="metrics-capture-card"]` hook kept (:59) — data-test hooks survive redesigns more often than structural selectors. Prefer data-test + role-based locators over descendant-markup chains.
4. Filter-to-list behavior changed: v1 asserted refetch-per-filter (GET /api/snapshots per interaction); v2 census shows ~1 GET per page-load, none per filter interaction. Before rewriting such a leg, CHECK THE DESIGN SPEC for the intended data-flow — asserting the old network shape would re-rot the test against the new design.

Rule of thumb: after a UI redesign, run the v1 e2e pack once at full depth, then triage failures into (a) locator rot (rewrite locator), (b) behavior-contract rot (check design spec, rewrite assertion), (c) real product bugs. In this run: 5× (a)/(b), 0× (c).

## Lesson 2: the daemon-lane recipe is fully self-contained — do not hand-build what the config boots
`frontend/playwright.snapshots.config.ts` webServer chain: `frontend/scripts/boot-e2e-snapshots-daemon.sh` (disposable native PG16 :15532, initdb -A trust, /tmp data dir, scrubs inherited POSTGRES_*/ENSEMBLE_DB_DSN, /readyz canary 60×1s asserting status=='ready') + `npx ng serve --port 14199` (proxies /api+/ws → :18279). To run the lane you only need: free ports 18279/14199/15532 (pre-check with ss; NEVER kill occupants), `env -u POSTGRES_* -u DATABASE_URL`, `ENSEMBLE_SELF_ENV=dev`, a placeholder `OPENAI_API_KEY` (lifespan requirement only — LLM never invoked by this pack), frontend `npm ci`, then `timeout 300 ./node_modules/.bin/playwright test --config playwright.snapshots.config.ts e2e/snapshots.spec.ts` from frontend/. Teardown is 3-layer idempotent (TERM/INT trap + EXIT trap + globalTeardown) and verified clean. Prior attempt (2026-10-06) FAIL+HALTed at an NG8002 FE compile — that was a dev-FE compile issue, NOT a lane issue; at c7b467444 the chain boots clean in ~40s (initdb→banner 15s→canary 25s).
