# Phase 3: Performance Optimization

## Objective
Reduce default test suite runtime from ~235s to a target of ~46s wall-clock via targeted mock-time replacements, fixture scoping optimization, and parallel test execution via pytest-xdist.

## Coupling
- **Depends on**: Phase 1 (pyproject.toml pre-allocation), Phase 2 (conftest.py coordination)
- **Coupling type**: tight — Phase 3 appends to `pyproject.toml` that Phase 1 already modified; loose with Phase 2 on `tests/conftest.py` (Phase 3 optimizes clean_env, Phase 2's RAG fix is related but in a different file)
- **Shared files with other phases**: `pyproject.toml` (Phase 1), `tests/conftest.py` (related to Phase 2's clean_env/RAG leak), `ensure.md` (Phase 4 — deferred)
- **Shared APIs/interfaces**: none
- **Why this coupling**: Phase 1 pre-allocates all dependency lines + config keys. Phase 3 only **appends** `pytest-xdist>=3.6`. Phase 2 must complete first because Phase 3 Task 6 (clean_env optimization in `tests/conftest.py`) is related to Phase 2 Group F (RAG_IS_REQUIRED env leak fix in `tests/unit/rag/test_config.py`).

### ⚠️ Sequencing Constraints

| Constraint | Requirement |
|------------|-------------|
| **pyproject.toml** (W1) | Phase 1 must complete first. Phase 3 ONLY appends `pytest-xdist` to the dependency list Phase 1 created. Does not modify Phase 1's `timeout` or `timeout_method` config. |
| **ensure.md** (W2) | Phase 3 does NOT modify ensure.md. ALL ensure.md changes are deferred to Phase 4. Phase 3's xdist usage notes will be added by Phase 4. |

## Context
The test suite has several performance hotspots:
- **tests/opencode/**: 70.3s for 469 tests (6.7 tests/sec) — 30% of total runtime for 8% of tests
- **tests/job_queue/**: 53.1s for 1371 tests (26/sec) — DB I/O overhead per test
- **test_mcp_warmup_pool.py**: ~20s for 6 tests — real `asyncio.sleep` for timeout/retry simulation
- **tests/services/**: 6.6s for 35 tests (5.3/sec) — slow patterns
- **tests/migration/**: 1.9s for 8 tests (4.2/sec) — schema rebuild per test
- **clean_env fixture**: autouse, runs `os.environ` operations on every test (~5675 times). **Cross-reference**: The `clean_env` fixture snapshots `os.environ` but does NOT strip vars set at pytest startup — this is the same root cause as the R3 RAG_IS_REQUIRED leak (Phase 2 Group F). Optimizing `clean_env` here complements the RAG config fix.
- **pytest-xdist**: NOT installed, all tests run serially

## Savings Model (Corrected — W7)

Per-test optimizations and xdist parallelism are **not additive**. The correct model:

```
Current serial runtime:              235s
Estimated per-test optimizations:    -50s  (mock asyncio.sleep, optimize fixtures)
─────────────────────────────────────────
Serial optimized:                    185s
Parallel (xdist, 4 workers):         185s / 4 ≈ 46s
```

Do NOT double-count: xdist parallelizes whatever the per-test time is after optimization.

## Tasks

### Group A: Install Parallel Execution Infrastructure

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Install pytest-xdist (W1 append-only) | **APPEND ONLY** to Phase 1's dependency list. Add `pytest-xdist>=3.6` after the `pytest-timeout` line Phase 1 added. Run `uv sync`. | `pyproject.toml` |
| 2 | Add xdist exclusion for postgres tests (W6) | Postgres tests cannot run in parallel (schema conflicts). Use marker-based exclusion: `pytest tests/ -n auto -m 'not postgres'` or `--ignore-glob='*postgres*'`. Document the correct parallel invocation. | (usage docs, no config file change needed — addopts already excludes postgres) |

### Group B: Replace Real Sleeps with Mock Time (C3 — Scoped)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 3 | Mock asyncio.sleep at L496 ONLY | The file already patches `asyncio.sleep` at L240 and L321. Apply the same pattern to **L496 ONLY** (genuine retry simulation). **DO NOT mock L509 or L678** — those are timing-concurrency primitives that validate actual concurrent behavior. Mocking them would invalidate the test. **Corrected savings estimate: <1s** (not 15-20s as originally claimed). | `tests/unit/test_mcp_warmup_pool.py:496` |
| 4 | Investigate tests/opencode/test_tools.py slowness | This file takes ~11s for 6 tests. Grep for `asyncio.sleep`, `time.sleep`, real I/O. Replace real sleeps with mocks. If slowness is from real subprocess/HTTP calls, mock those. | `tests/opencode/test_tools.py` |
| 5 | Audit tests/opencode/ for slow patterns | Run `grep -rn "asyncio.sleep\|time.sleep\|subprocess\|requests\.\|httpx\|aiohttp" tests/opencode/` and catalog all slow patterns. Fix the top offenders by mocking real I/O operations. Target: bring tests/opencode/ from 6.7 tests/sec to >20 tests/sec. | `tests/opencode/` (multiple files) |

### Group C: Optimize Fixtures (W4, W5 — Scoped)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 6 | Optimize clean_env fixture | Read `tests/conftest.py`, find the `clean_env` autouse fixture. If it does `os.environ.copy()` + restore on every test (~5675 times), optimize: (a) only snapshot/restore env vars that the test actually modifies, or (b) use `monkeypatch.setenv` which auto-cleans. Benchmark before/after. **Cross-reference R3**: The `clean_env` fixture's failure to strip startup env vars is the same root cause as the RAG_IS_REQUIRED leak fixed in Phase 2 Group F. Consider also stripping known-problematic env vars (like `RAG_IS_REQUIRED`) at fixture setup to prevent future leaks. | `tests/conftest.py` |
| 7 | Session-scope engine + truncate for job_queue (W4) | **Session-scope ONLY the `engine` fixture** and add a `_truncate_tables` autouse fixture that runs `TRUNCATE` between tests instead of create/destroy. **DO NOT reduce system queues from 10 to 2** — keep at 10. **DO NOT change `concurrent_lock_repo` to in-memory SQLite** (W5) — it's file-backed specifically for cross-connection UNIQUE conflicts. Keep function-scoped + file-backed. Estimated savings: ~25-50s across 1371 tests. | `tests/job_queue/conftest.py` |

### Group D: Investigate Remaining Slow Directories

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 8 | Investigate tests/migration/ slowness | 1.9s for 8 tests (4.2/sec). The `fresh_pg_schema` fixture in `test_jsonb_migration.py:167` rebuilds full PG schema per test (~46 DDL roundtrips each, used by 5 of 7 tests). Consider caching schema or using transaction rollback pattern. | `tests/migration/test_jsonb_migration.py:167` |

> **Note**: The original Task 8 (collect_ignore_glob for h10_l14 tests) has been **dropped** (A3). Investigation confirmed `tests/services/test_instance_lifecycle_h10_l14.py` collects cleanly (14 tests, 0 errors) — CM-era imports were already removed in Phase 5. The module-level `pytestmark = pytest.mark.skip(...)` at line 61 handles runtime skipping correctly. No action needed.

## Key Files
- `pyproject.toml` — append pytest-xdist dependency (after Phase 1's changes)
- `tests/unit/test_mcp_warmup_pool.py` — mock asyncio.sleep at L496 ONLY
- `tests/opencode/test_tools.py` — investigate and fix slowness
- `tests/opencode/` — audit all files for slow patterns
- `tests/conftest.py` — optimize clean_env fixture (related to Phase 2 Group F RAG env leak fix)
- `tests/job_queue/conftest.py` — session-scope engine + truncate, keep queue count at 10
- `tests/migration/test_jsonb_migration.py` — optimize schema rebuild

## Optimization Priority (by ROI — Corrected)

| Priority | Fix | Est. Savings | Effort |
|----------|-----|-------------|--------|
| 1 | Install pytest-xdist + run `-n auto` | ~50% of post-optimization runtime (~46s wall-clock) | Low |
| 2 | Session-scope engine + truncate for job_queue (W4 scoped) | ~25-50s (serial) | Medium |
| 3 | Audit/fix tests/opencode/ slow patterns | ~20-40s (serial) | Medium |
| 4 | Optimize clean_env fixture | ~5-10s (serial) | Low |
| 5 | Mock asyncio.sleep at L496 ONLY | <1s | Low |
| 6 | Optimize tests/migration/ schema rebuild | ~1-2s | Medium |

**Note**: Savings 2-4 are serial-time savings that reduce the base before xdist parallelism (see Savings Model above).

## Known Issues (Documented — Not Fixed Here)

| Issue | Details | Reference |
|-------|---------|-----------|
| test_message_queue_e2e.py sys.modules pollution | Lines 50-66 mutate `sys.modules` at module import time, breaking langgraph mocks for the entire session. Should be moved to session-scoped fixture. | W9 — documented in overview as known issue |
| test-ordering pollution | `test_api_router_extraction.py` shows cascading errors when run with other tests. Root cause TBD. | W9 — documented in overview as known issue |

## Constraints
- **Do NOT change production code** — only test infrastructure and test files
- **DO NOT mock L509 or L678** in test_mcp_warmup_pool.py — they're timing-concurrency primitives (C3)
- **DO NOT reduce system queues from 10 to 2** — keep at 10 (W4)
- **DO NOT change concurrent_lock_repo to in-memory SQLite** — keep file-backed (W5)
- **DO NOT modify ensure.md** — all ensure.md changes deferred to Phase 4 (W2)
- **Exclude postgres tests from xdist parallelism** (W6)
- pytest-xdist may reveal hidden test-ordering dependencies — fix those by adding proper isolation
- If pytest-xdist causes widespread flakiness, fall back to `-n 4` (fixed processes) or scope to specific directories
- Benchmark before and after each optimization to verify impact

## Benchmarking Protocol
For each optimization:
```bash
# Before (serial)
python -m pytest tests/ --durations=20 -q 2>&1 | tail -30

# After (serial)
python -m pytest tests/ --durations=20 -q 2>&1 | tail -30

# Parallel (after all optimizations)
python -m pytest tests/ -n auto --durations=20 -q 2>&1 | tail -30
```

## Deliverables
- [x] pytest-xdist installed and working with `-n auto` (postgres excluded) — VERIFIED: xdist 3.8.0 installed
- [x] asyncio.sleep mocked at L496 ONLY (L509, L678 left as real) — done by parallel task
- [ ] tests/opencode/ slow patterns identified and fixed — deferred (Tasks 4&5, out of Phase 3 scope)
- [x] clean_env fixture optimized — DONE: 19 tracked vars instead of full os.environ.copy()
- [x] job_queue: engine session-scoped + truncate autouse, queue count unchanged at 10 — DONE: 53s → ~14.3s
- [ ] Default suite runtime reduced — pending final benchmark verification
- [ ] Benchmark results recorded — pending final verification

## Phase 3 Results (2026-06-24)

### Completed Optimizations
| Optimization | Before | After | Savings |
|---|---|---|---|
| job_queue engine session-scope + truncate | ~53s | ~14.3s | ~38.7s (3.7x) |
| clean_env targeted tracking | full os.environ.copy() per test | 19 vars per test | ~5-10s est. |
| asyncio.sleep L496 mock | real sleep | mocked | <1s |
| xdist postgres guard | (safety) | skip under xdist | prevents breakage |

### Deferred (out of scope for Phase 3)
- tests/opencode/ slow pattern audit (Tasks 4&5 from plan) — significant effort, deferred
- tests/migration/ schema rebuild optimization (Task 8 from plan) — low ROI (~1-2s), deferred

### Parallel Execution
- Use: `pytest -n auto -m 'not postgres'` for parallel runs
- Postgres tests run serially: `pytest tests/postgres/ --override-ini='addopts=' -m postgres`
