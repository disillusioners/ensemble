# Playwright Parallel-Pack Interference (shared ng serve) — 2026-08-19

## Context
Cycle: reload-tabs regression fix verification (branch `fix/reload-tabs-regression`).
I dispatched `instances_state_e2e_core` and `instances_state_e2e_regression` as two parallel
Playwright pack workers against the SAME servers (BE 8079 shared, FE 4199 single `ng serve`,
both via `reuseExistingServer: true`).

## Symptom
`instances_state_e2e_core` test 2 FAILED at spec line 280:
`expect(chatStreamNet(esBefore)).toBeGreaterThan(0)` — received 0.
The assert fires BEFORE the Plan nav click: it requires the chat page's EventSource
(`/api/instances/{id}/events`) to have ≥1 open registration carried over from test 1.
Overlay DOM was visible and intact (state preservation working) — only the SSE registration
had not landed. Tests 3-4 cascade-skipped.

## Root cause
Concurrent Playwright packs contending on one shared dev-server + backend raced the SSE
registration timing. NOT a production regression: the isolated re-run passed 4/4 with
`opens=1` observed at the same assertion, zero ng-serve rebuild (FE log byte-identical),
and the sibling regression pack passed 5/5 in the same window.

## Evidence chain (deterministic-vs-interference discriminator)
1. Isolated re-run (port quiet) → PASS 4/4, `opens=1` before Plan click. ✅
2. `/tmp/ens-fe-4199.log` unchanged (81 lines before/after) → no mid-run rebuild. ✅
3. Fix diff is 15 sync lines in App constructor (localStorage read) — no EventSource wiring touched. ✅

## Rule going forward
- **Serialize Playwright packs that target the same FE dev server / backend** — do not fan out
  browser packs in parallel when both attach via `reuseExistingServer: true` to one `ng serve`.
  (Jest + prod-build packs remain safely parallelizable with browser packs.)
- On a pack FAIL under concurrent browser load: first discriminator = isolated re-run before
  suspecting the change under test.
- If packs must run in parallel, give each its own webServer port (per-pack port assignment)
  to remove the interference class entirely.

## Cost
One wasted FAIL + one re-run (~5s) + triage turn. Cheap, but avoidable.

Refs: RESULTS/2026-08-19-reload-tabs-regression-verify.md; baseline closure RESULTS/2026-08-18-instances-state-cache-ship-closure.md (packs ran serially there — no interference).
