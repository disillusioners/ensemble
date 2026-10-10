# Playwright `waitForResponse` arming race — pathname-only predicates resolve on PRIOR echoes when the app double-fetches

Date: 2026-10-10 · Run: RESULTS/2026-10-10-snapshots-v2-daemon-lane-e2e-rerun.md (leg #3)

## Pattern
When an app fires the SAME endpoint more than once per interaction (here: snapshots v2 fires the list GET **twice** per filter write — the URL-mirror navigate re-triggers the fetch effect, ~19ms apart), a `page.waitForResponse` armed with a **pathname-only predicate** can resolve on the echo of the PREVIOUS sub-leg's request (no new params yet), causing a guaranteed-wrong assertion ("expected tags param, received []"). Deterministic — it is a race in the ORDER of arming vs. firing, not a flake: 2/2 identical failures.

## Fixes (either)
1. **Full-query-param predicate** — match the exact params the leg is asserting (the sibling wait in the same spec that used a param-count predicate was race-immune).
2. **Arm post-commit** — start the wait AFTER the interaction that should trigger the response, so prior echoes can never satisfy it.

Audit any OTHER waits sharing the pattern in the same spec when fixing one (the (d) status first-chip wait had the same exposure).

## Census-method caveat
A daemon/access-log census corroborates method+status but **strips query strings** — param-fidelity ground truth requires browser-side evidence: run the focused leg with `--grep "<test name>" --trace on` and read `trace.network` (CLI artifact knobs only — zero test-content change). This turned an ambiguous "no per-filter GET" into a proven wire contract with exact request pairs and params.

## Discrimination heuristic (product-regression vs test-race)
When a network-shape assertion fails: (1) capture the trace; (2) if the expected requests ARE on the wire with correct params but the wait resolved early → test-side race (fix the predicate); (3) if the expected requests are genuinely absent (after honoring any debounce interval) → product regression. In this run the §2.3 focus-watch leg fell in class (2) — the two-outcome commission protocol (pass vs product-regression) needed this third outcome.
