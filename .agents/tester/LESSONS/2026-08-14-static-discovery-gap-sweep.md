# Static-Discovery Gap Sweep: 2026-08-14 (PM domain-access round)

## Context
PM domain-access change (57d1e07d) threads agent_id through spawn_instance_with_mcp / ensure_mcp_preloaded. PACKS.md had no pack covering 4 test files that directly reference those symbols — a static grep sweep (dispatched as a read-only worker check) surfaced them after the main pack selection.

## What happened
- Read-only worker grepped tests/ for the two changed call sites; found `test_mcp_runtime_integration.py`, `test_title_generation_trigger.py`, `test_mcp_cold_load_race.py`, `test_paused_instance_ttl.py` outside every pack in the planned set.
- Ad-hoc gap pack ran them: 71/75 PASS, 4 FAIL — all 4 pre-existing (attribution via `git diff 57d1e07d~1..HEAD`: none in the changed hunk region).
- 3 mock-drift quick fixes (8cd206f1) + 1 quarantine (SQLite `DROP CONSTRAINT` migration failure) → re-run green 74p/1s.

## Lesson
When a change touches a shared call site (spawn/preload machinery), grep the test tree for the symbol names BEFORE finalizing pack selection — PACKS.md pack-to-module mapping misses test files that exercise a call site without testing the owning module. The sweep is cheap (one read-only worker, ~2 min) and caught a real coverage hole the pack list would have shipped without.

## Also recorded
- Pre-existing SQLite migration 20260714_000001 `DROP CONSTRAINT` incompatibility keeps redding SQLite-path tests (2nd occurrence after 2026-08-10). Production fix pending; quarantine is the interim mechanism.
