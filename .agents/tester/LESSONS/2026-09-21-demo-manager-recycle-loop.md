# Demo Manager-Recycle Loop + Health-Gate Masking (2026-09-21)

Discovered during demo v0.13.9 final verification (worker dbd2abb2, 15:47–16:03 UTC). Full evidence: RESULTS/2026-09-21-demo-v0.13.9-final-verification.md.

## Finding
- Demo launcher (`/home/nea/agents-ensemble-demo/launcher.sh`) re-spawns every ~20s (PPID 1); each spawn cycles the in-process InstanceManager: `Starting Ensemble v0.13.9` → `Starting graceful shutdown...` ~5–6s later. Continuous since ≥15:40:16Z.
- **The HTTP layer is a separate surviving process** (PID 403195, up since 15:38:45) — livez/readyz stay green THROUGHOUT, masking the loop. Promote gates + 300s soak passed "green" while the manager recycled every ~20s.
- `job_recovery_service` inside each cycle finalizes freshly-picked-up jobs FAILED (`instance not found, marking FAILED`) → any job landing in a recycle window dies; terminal SSE events can fire ~6.5s BEFORE actual completion with wrong status.
- Functional-proof consequence: v0.13.9's empty-notification/null-result_summary fix could NOT be proven on demo — the clean completion path is never reached; recovery-service stamping hijacks the terminal seam (`finalized job no_job...` — observer bypasses the fix's stamping).

## Lessons
1. **livez/readyz are insufficient promote gates for manager health** — they measure the HTTP layer, which can survive an arbitrary number of manager recycles. A promote soak that is "green" under a recycle loop is a false pass. Gate hardening candidate: manager recycle-count/uptime in readyz, or launcher-supervision state.
2. **Job-level functional proof is the only trustworthy post-promote check** — a single trivial job through the public API exposed in ~13s what 5 minutes of health gates could not.
3. **Timing analysis beats status analysis**: comparing SSE terminal emission time (16:00:03) vs job completed_at (16:00:09.47) exposed premature-terminal; status alone said "failed" and would have been misread as an LLM failure.
4. Read-only verification discipline worked: the recycle loop was found WITHOUT boots/writes; /proc ownership was verified before reading (exe/cwd checks).

## Open (needs commission)
- Root cause of the loop (why graceful shutdown 5–6s after every manager boot) — not diagnosed under read-only mandate.
- Task↔JobItem mirror divergence reproduced on demo (`GET /api/jobs/{id}` completed vs JobItem failed) — known reconciliation-gap family.
