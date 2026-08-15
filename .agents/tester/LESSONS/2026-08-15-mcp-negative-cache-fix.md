# MCP Discovery Negative-Cache Fix — Test Campaign Lessons (df40ba3a)

**Date:** 2026-08-15 · **Branch:** `feature/auto-restart-upgrade-plan` @ `df40ba3a` (+ test commit `dbed289c`)
**Campaign:** `RESULTS/2026-08-15-mcp-negative-cache-fix-test.md`

## 1. Recon before pack dispatch changed the plan materially

Recon (read-only git/grep, no test runs) found **6 additional in-scope test files** the task brief's list missed — 2 of them import `McpService` directly (`test_mcp_concurrent.py`, `test_mcp_lazy_init.py`), 1 (`test_plane_domain_access.py`) mirrors `mcp_service.py` logic with line-number comment citations prone to drift. Direct-importer sweep: 160/160 PASS.

**Lesson:** For a core-service change, `grep -rl "<module>|<Class>" tests/` beats file-name matching alone. Name-matching catches neighbors (builtin/plane/resilience files); import-matching catches actual consumers. Both layers needed.

## 2. Lock-protected new state — the invalidate-path asymmetry is pre-existing pattern

New throttle state `_last_empty_discovery` (dict, `mcp_service.py:194`):
- Discovery path: ALL access under `_schema_cache_lock` (get :278, pop :294, set :297 — inside lock block @ :249) ✅
- `invalidate_schema_cache` (:489-507): sync `def` **cannot** take the asyncio lock — pops/clears lock-free, exactly as `_schema_cache` itself always has.

**Lesson (answer to "is the new state lock-protected like the rest?"):** Yes, *consistently with the rest* — including the rest's lock-free sync-invalidate asymmetry. Single-threaded event loop makes sync dict ops atomic; worst case is a stale marker re-written mid-invalidate that self-heals after one 30s window. Verified no concurrency regression via `concurrency_atomic_unit_test` (91P/74S/0F, exact baseline).

## 3. Coverage gaps found by recon, filled behaviorally

Developer's 57 tests covered 4/6 edge cases. Two gaps filled (+7 tests, `dbed289c`):
- **Invalidate-vs-marker**: no test ever seeded `_last_empty_discovery` before calling invalidate. New tests seed worst-case marker → invalidate → assert immediate re-discovery (specific + all-clear paths).
- **Resilience independence**: zero cross-talk tests existed. Rather than stubbing, the new tests register a REAL `CircuitBreaker` via production `ResilienceManager.register()` and assert both directions: OPEN breaker doesn't block discovery; failed discovery doesn't trip CLOSED breaker (failure_count stays 0).

**Lesson:** For layer-independence claims, behavioral tests with real collaborator objects beat structural greps — the grep (`discovery path never references self._resilience`) was confirmed, but the behavioral tests also pin the contract against future refactors that reintroduce coupling.

All 7 new tests passed first-run — fix behavior verified at every probed edge, zero production bugs.

## 4. Pack registration drift is routine — verify def-count vs pytest-count

Two stale registrations found: `mcp_warmup_pool_unit_test` (registered 50, actually 65 — parametrize growth) and `test_mcp_test_connection.py` (68→71). Also a def-count artifact in reverse: recon counted 84 `def test_` in `test_builtin_mcp_servers.py` but pytest collects 83 (one nested def).

**Lesson:** Trust `pytest --collect-only` over `grep -c "def test_"` for pack counts; refresh PACKS.md registrations when running packs (done this campaign).

## 5. Known follow-ups confirmed structurally (non-blocking, developer already aware)

- **Lock-hold amplification**: `_acquire_discovery_session` (awaited @ :453) runs INSIDE the lock held @ :249 → worst case ≈ 2×15s connect timeout + 1.5s ≈ 31.5s lock hold during eager warm. Not fixed in this commit; flagged for the follow-up arc.
- **pytest-timeout plugin drift**: `pyproject.toml` declares `timeout`/`timeout_method` but plugin absent from venv → `PytestConfigWarning` on every run. A prior PACKS.md entry claimed pytest-timeout 2.4.0 registered (LLM HA polish @ ba559598). Harmless to results (command-level wrappers provide the real guard) but the venv has drifted — worth reconciling.

## Verdict

SHIP. 553 tests, 0 failures, 0 production bugs, ensure.md in-scope 4/4, quarantine intact.
