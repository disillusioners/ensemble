# Inherited-Gate Salvage Protocol + Pack-Script `set -e` Flaw (2026-08-29 reconciler-wedge-fix gate)

## Situation
Gate inherited from a terminated predecessor (74/75 workers done, then wedged on
DB QueuePool exhaustion from a 75-worker fan-out — the ensemble daemon's own
pool 5+10 saw 261 overflow warnings; a child wedged pre-LLM and an operator
cascade terminated the tree).

## What was salvageable: NOTHING (and how we knew in 2 minutes)
- `.agents/tester/RESULTS/` — no gate file for the branch (nothing dated 2026-08-29).
- `.agents/tester/PACKS.md.new` — 0 bytes: a dead in-progress artifact, deleted content of value.
- `/tmp` predecessor artifacts DID exist (probe scripts, A/B logs, DBs) — but per
  the inheritance mandate ("treat prior worker outputs as UNVERIFIED unless the
  evidence is on disk and reproducible") they were used ONLY as harness-pattern
  references; every assertion was re-derived by fresh probes with fresh fixtures.

**Rule of thumb:** an inherited gate's salvage surface = RESULTS/ + PACKS.md +
QUARANTINE.md (baselines ARE citable — they're on disk and reproducible).
Worker chat outputs die with the workers. Budget 1 inventory worker to establish
this, then plan the full re-verification.

## Modest fan-out worked perfectly
14 dispatches total, hard-capped at 3 concurrent, staggered waves
(inventory → 3+3 packs → probes+audit → RED/e2e → core). No pool pressure, no
wedges. The predecessor's 75-worker approach was ~5× more dispatches than the
scoped blast radius needed: 9 packs + 4 behavioral verifications + 1 audit +
1 RED A/B covered the entire mandate.

## Pack-script convention flaw (repo-wide, 111 packs)
`set -euo pipefail` + `EXIT_CODE=$?` after the pytest invocation means the
script EXITS at the failing pytest before reaching the `RESULT: FAIL/TIMEOUT`
echo — exit codes 1/124 still propagate (gating semantics intact), but the
documented output contract (`RESULT:` line always printed) breaks on any
failure. Observed independently by two workers (W0 inventory, watchdog pack).
**Fix pattern:** `timeout 120s ... || EXIT_CODE=$?` (or `set +e` around the
run) then branch on EXIT_CODE. Candidate for a batch maintenance pass —
low priority since exit codes drive the gates, not the echo.

## Worker-reuse for tiny follow-ups
The core-pack worker was reused (revive path) for a 30-second ensure.md static
check — cheaper than a fresh spawn, context already warm. Good default for
post-pack spot-checks in the same area.

## Gate verdict
PASS — see RESULTS/2026-08-29-reconciler-wedge-fix-gate.md.
