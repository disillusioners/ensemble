# Phase 1 — Generation Counter Extraction: Findings

**Date:** 2026-06-22
**Branch:** `feature/cleanup-old-architecture`
**Commits:** `59b6b68` (initial), `779f9ca9` (C1 fix)

## What Changed
Generation counter extracted from CorrelationManager (CM) into DependencyBus. The counter prevents orphan-race conditions — when a parent instance's children complete, the generation counter prevents stale completions from firing.

The C1 fix mirrors CM's 3 generation bump sites to the bus:
1. `register_message_send()` (correlation_manager.py:281)
2. `register_job_send()` (line 358) — called by watch_job tool
3. `resolve_job()` (line 607)

## Key Insight: Why the C1 Fix Was Critical
After Phase 1 initial extraction, `cm.register_job_send()` (called from `watch_job` tool at `tools/job_queue.py:645`) still bumped `cm._generation`, but the observer (`job_feedback_observer.py`) reads `bus.get_generation()` for late registration detection. This silently broke orphan-race protection.

Fix: At each CM bump site, add `bus = get_dependency_bus(); if bus is not None: bus.increment_generation(parent_id)`.

## Test Execution
- **482 tests total across SQLite + PostgreSQL + E2E**
- **0 failures, 0 quick fixes needed**
- All tests passed cleanly on first run

## Verification Coverage

| Area | Test File(s) | Result |
|------|-------------|--------|
| Generation counter mirror (CM→Bus) | TestCMGenerationMirror | ✅ All 3 sites verified |
| Orphan-race detection (SQLite) | TestOrphanRaceE2E, cascade races | ✅ PASS |
| Orphan-race detection (PG) | premature_completion A/B/C | ✅ PASS |
| Concurrent access (SQLite) | cascade_concurrency, cascade_race3 | ✅ PASS |
| Concurrent access (PG) | concurrent_enqueue/jsonb/locks | ✅ PASS |
| Bus restart survival | test_dependency_bus.py | ✅ PASS |
| E2E real workflows | test_e2e_workflows.py (4 tests) | ✅ PASS |
| No double-bumping | CM shadow tests | ✅ PASS |

## Gotcha: E2E Test Python Version
E2E tests (`tests/e2e/test_e2e_workflows.py`) must be run with `.venv/bin/python` (Python 3.13 with MCP SDK). System `python3.14` causes the `_swap_real_mcp_for_e2e` fixture to skip all tests. The daemon (`dev.sh`) works on either Python — only the test runner needs the venv.

## Conclusion
Phase 1 generation counter extraction is **SAFE TO MERGE**. The C1 fix correctly mirrors all CM bump sites to the bus, and orphan-race detection works through both the bus path and CM passthrough path. No regressions detected.
