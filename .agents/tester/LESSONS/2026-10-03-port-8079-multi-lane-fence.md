# Port 8079 is a MULTI-LANE resource — check for sibling dev daemons before any runtime pack

**Date:** 2026-10-03 · **Found during:** schedule-dialog pre-merge gate (RESULTS/2026-10-03-schedule-dialog-tz-list.md §5b)

## Pattern
Runtime smoke packs that boot `./dev.sh` on :8079 can hit the port already owned by ANOTHER lane's
dev daemon. This host has multiple concurrent commission lanes; observed occupier:
`/home/nea/dev-daemon-8079-v0.16.11/payload/ensemble-prod` (pid 3170090, started 08:35:26 2026-10-03,
cwd = the payload dir, NOT the repo) — an od-smoke lane's post-promote dev-DB smoke.

## Fence protocol (held correctly 2026-10-03)
1. Pre-boot `ss -ltnp | grep ':8079'` → if occupied: identify owner (`ps -o pid,ppid,lstart,command`,
   `readlink /proc/<pid>/cwd`). Owner cwd ≠ the testing repo ⇒ SIBLING LANE ⇒ STOP + report.
2. NEVER signal it (it is someone else's in-flight evidence run), NEVER treat its HTTP as your
   evidence (wrong code version — e.g. v0.16.11 predates later branch merges), NEVER boot a second
   daemon against the shared dev DB while a sibling smoke is mid-flight.
3. Fall back to substitute evidence: endpoint contract tests at the gated commit + any same-day
   live-runtime run on byte-identical code + FE DOM-level spec pins. Offer a verbatim re-run once
   the port frees.

## Why cross-tenant reads are invalid
A daemon from a different install runs a different commit set (promoted releases lag merged fix
branches). A 200 from it cannot evidence behavior of the commit under test — and a 404 (endpoint
not yet in that release) proves nothing either way.
