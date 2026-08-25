# FT-1 — FIRED-scan caching in compact path (P2 closure-council follow-up)

**Source:** P2 closure-council follow-up (separate from FT-001/002/003 deferred tickets).
**Filed:** 2026-08-25 (P3 documentation pass) · **Effort class:** SMALL–MEDIUM
**Status:** OPEN (deferred to future batch)

> **Pre-filing check:** No prior filing of `FT-1*` under `.agents/shared/planning/pause-resume-terminate-tree-fix/` or `.agents/tester/`. Existing ticket IDs `FT-001..FT-005` (B7a / B7c / SSE / kill-switch / Lane-5) are distinct from this `FT-1`. Safe to file.

---

## Problem

In the compact path, the FIRED-scan (`_compact_fired_watchers_for_paused` region in `daemon/services/instance_lifecycle.py`, with the work-aggregation helper in `daemon/services/child_reports.py`) re-scans the FIRED-row set on every compaction pass. For trees that produce a steady trickle of FIRED rows during pause, the repeated scan becomes O(passes × fired_rows) when it could be O(passes + fired_rows).

## Context — P2 machinery in scope

- `daemon/services/instance_lifecycle.py` — `_compact_fired_watchers_for_paused` region (the compact-side driver).
- `daemon/services/child_reports.py` — the FIRED-row aggregation surface the compact path reads from.
- Paused-watchers lifecycle (P1 + P2 era): watchers are added on child outcome delivery and reaped on compact, with the FIRED set being the ones the compact driver collects per pass.

## Recommended approach

- **Cache / memoize the FIRED-row scan** within a single compact pass: build the FIRED-row set once at compact entry, hand the cached set to the per-watchers iterate.
- If the scan input is mutated mid-pass, invalidate the cache (event-driven invalidation keyed on `child_reports` row insert/update).
- Keep the cache scoped to the compact pass — do NOT lift to a module-level cache (the compact pass is the bounded transactional unit).

## Acceptance criteria

- Re-run the existing compact-side unit tests under `tests/unit/services/test_compact_fired_watchers_deliver_before_compact.py` (P2 deliverable): all pass with the cached path.
- Add a micro-benchmark test: compact N=1000 FIRED rows M=10 passes — assert the cached path is sub-linear in M (current linear).
- Cache invalidation: a FIRED row inserted mid-pass must be picked up by the next pass, not silently dropped by the cache.

## Risk

- Stale-cache class: if invalidation is missed, the compact driver could reap the wrong set. Mitigate by scoping cache to a single pass (no cross-pass sharing).
- Bypass needed for tests: `_build_compact_mock` in test harnesses likely needs a `bypass_cache=True` flag for fixture-driven test scenarios.
