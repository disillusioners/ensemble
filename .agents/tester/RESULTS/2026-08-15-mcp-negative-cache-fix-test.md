# MCP Schema-Discovery Negative-Cache Fix — Test Report

**Date:** 2026-08-15
**Commit:** `df40ba3a` on `feature/auto-restart-upgrade-plan` (base `005610fe` / v0.10.4)
**Tree state:** HEAD = `df40ba3ada62f02bc7eb9018ffb985f4ba6a7cde`, working tree clean of code changes (5 untracked `.agents/` docs only)
**Instance IDs:** 05ad8e63 (recon), d705075e (static), 5b780ec4 (pack-mcp-service), e0000238 (pack-builtin), 22969ea6 (pack-plane), a3056724 (pack-resilience), e763ffd6 (pack-neighbors), 17e47187 (pack-importers), 1d20934d (pack-concurrency), 24322b60 (gap-tests-author)

## Summary

- **Verdict: SHIP — all areas PASS, zero failures, zero blocking findings.**
- **553 tests executed across 15 test files + 2 static checks + 7 NEW gap-filling tests** (commit `dbed289c`).
- Direct pack: 57/57 (developer's expected count, no drift).
- Regression sweep: 4 registered packs exact-baseline green + 6 recon-discovered direct-importer files green (160/160).
- ensure.md in-scope Core: 4/4 PASS. Release-gate full e2e **not triggered** (change touches only `mcp_service.py` + its test file; zero job/task/queue files).
- Quarantined test: skipped as expected — no quarantine regression.
- Production bugs found: **0**. Production code modified by this campaign: **0** (test-only commit `dbed289c`).

### Scope Decision

> Full suite not requested; recon confirmed change = 2 files (1 production file `daemon/services/mcp_service.py` +204, 1 test file +376), single module (MCP service layer), no job/task/queue touch → scoped MCP blast-radius run warranted. Full suite and release-gate e2e NOT run (no job/task/queue files in diff, per the ensure.md convention note). Ran: direct pack + 4 registered MCP-layer packs + 6 recon-discovered direct-importer/coupler files + concurrency pack (ensure.md Critical) + 2 static checks + gap-test authoring. Skipped: full non-integration suite, e2e workflows, frontend, all non-MCP packs. Reason: MCP-service-local change with all its importers and layer neighbors covered.

## Per-Area Results

### 1. Developer's Suite — ✅ PASS
`tests/unit/test_mcp_service.py`: **57/57 in 0.96s** — exact match to developer's expected count (45→57 growth = the 12 new fix tests). No regressions in prior 45.

### 2. Regression Sweep (mcp_service.py is core) — ✅ PASS (all green, exact baselines)

| Pack / file | Baseline | Actual | Status |
|---|---|---|---|
| `mcp_disable_flags_unit_test` (builtin_servers) | 83 | **83/83** in 1.98s | ✅ exact |
| `plane_mcp_unit_test` | 53→60 expected | **60/60** in 1.11s | ✅ (7-test growth = PM-domain-access `mcp_full_access` matrix, not df40ba3a) |
| `mcp_resilience_unit_test` | 73 | **73/73** in 0.78s | ✅ exact |
| `mcp_connection_manager_unit_test` | 19 | **19/19** in 0.81s | ✅ exact |
| `mcp_warmup_pool_unit_test` | 50 (stale reg.) | **65/65** in 55.14s | ✅ (def-count confirmed; registration stale — refreshed below) |
| `mcp_cold_load_race_unit_test` | 5+1skip | **5P + 1 skip** in 0.80s | ✅ quarantine INTACT (skip marker @ line 241, not a failure) |
| `mcp_runtime_integration_test` | 14 | **14/14** in 3.70s | files ✅ |
| Importers sweep (6 files: mcp_concurrent 8, lazy_init 22, stdio_timeout 20, test_connection 71, mcp_lifecycle 13, plane_domain_access 26) | ~153 def-count | **160/160** in ~2.6s | ✅ (test_connection +3 file growth since last baseline; all pass) |

### 3. Behavioral Edge Cases — ✅ ALL COVERED (4 pre-existing + 3 gaps filled with 7 NEW tests)

| Case | Verdict | Evidence |
|---|---|---|
| (a) Throttle: t=0 empty → 30s window no re-attempt → post-30s re-attempt | ✅ covered (pre-existing) | `test_throttle_blocks_immediate_rediscovery` (:690), `test_empty_discovery_not_cached_second_call_re_discovers` (:641) |
| (b) `invalidate_schema_cache` clears cache AND throttle marker | ✅ **gap filled** | NEW `test_invalidate_clears_throttle_marker_specific_server` + `_all` — seeded worst-case marker dropped; immediate re-discovery verified |
| (c) Auth fail-fast: 401/403 → exactly 1 attempt | ✅ covered (pre-existing) | `test_auth_failure_fails_fast_no_retry` (:801, await_count==1) |
| (d) Retry bound: connect failure → max 2 attempts | ✅ covered (pre-existing) | `test_persistent_connect_failure_no_excess_retry` (:779, await_count==2) |
| (e) Resilience-layer independence (CB at tool-call layer vs discovery retry) | ✅ **gap filled** | NEW `test_open_breaker_does_not_block_discovery` (real breaker via `ResilienceManager.register`, forced OPEN — discovery still runs, breaker untouched) + `test_failed_discovery_does_not_trip_breaker` (real cold path, persistent connect failure → 2 attempts → `[]`; breaker stays CLOSED, failure_count==0) |
| (f) Success after transient failure → cached → cache hit | ✅ covered (composite: :751, :641, :673) | retry-success caching + no-3rd-discovery both asserted |
| (minor) 1.5s delay value pin | ✅ **filled** | NEW constant-pin tests (`SCHEMA_DISCOVERY_CONNECT_RETRY_DELAY_S==1.5`, `EMPTY_DISCOVERY_RETRY_THROTTLE_S==30.0`, from-import identity) |

All 7 new tests passed **first-run against current code** — the fix behaves as specified at every probed edge. Full file after additions: **64/64**.

### 4. ensure.md + Concurrency — ✅ PASS

- **No regressions in changed packs**: ✅ (all packs above PASS)
- **Deadlock / concurrency integrity** (Critical): ✅ `concurrency_atomic_unit_test` PASS — 91P/74S/0F in 8.8s, exact baseline
- **No sync DB calls on event loop** (Critical): ✅ covered by same pack (thread-identity tests green)
- **`dev.sh` timeout-graceful-shutdown flag** (Critical): ✅ present @ `dev.sh:102`
- **Callers await correctly** (Important): ✅ 3/3 named functions + diff adds only 2 new defs (sync `_is_auth_failure` :306, async `_acquire_discovery_session` :336 awaited at :453); no sync↔async conversions; `invalidate_schema_cache` stays sync with sync router callers
- **Full e2e gate**: NOT triggered — diff touches zero job/task/queue files (verified via `git show --name-status df40ba3a`)

**Lock protection of new throttle state** (task question 4): `_last_empty_discovery` (dict at `mcp_service.py:194`) is accessed **under `_schema_cache_lock`** in the discovery path (get @ :278, pop-on-success @ :294, set-on-empty @ :297 — all inside lock block acquired @ :249). `invalidate_schema_cache` (:489-507) is sync and pops/clears the marker **lock-free**, mirroring the pre-existing pattern for `_schema_cache` itself — sync dict ops are atomic on the event loop, worst case is a stale marker self-healing after one 30s window. **No concurrency regression**; the pattern is consistent with the codebase's existing design.

## Quick Fixes Applied

None required — zero failures anywhere in the campaign.

## Code Changes Summary

- `tests/unit/test_mcp_service.py` +273 lines: 7 gap-filling tests (3 classes: `TestSchemaNegativeCache`, `TestDiscoveryResilienceIndependence`, `TestDiscoveryTimingContract`) — **commit `dbed289c`** (test-only)
- Production code: **unchanged** by this campaign

## Non-Blocking Observations (for the follow-up list, not merge blockers)

1. **Lock-hold amplification confirmed structurally** (known follow-up): `_acquire_discovery_session` awaited at :453 runs **inside** the lock held at :249 → worst case ≈ 2×15s connect timeout + 1.5s ≈ 31.5s single-server lock hold during eager warm. Matches the developer's own note; no new information beyond confirmation.
2. **Pooled-path genuine-zero-tool servers** (known follow-up): out of scope of this campaign — pooled path skips the empty-discovery logic this fix targets.
3. **`_is_auth_failure` substring false-positive** (known follow-up): classifier tests pass; the substring-matching risk is noted in the developer's follow-up list.
4. **pytest-timeout config options inert** (pre-existing): `pyproject.toml` declares `timeout`/`timeout_method` but the plugin is not installed in this venv → `PytestConfigWarning` on every run. Note: a PACKS.md entry (LLM HA polish) claims pytest-timeout 2.4.0 was registered — the warning suggests the venv has drifted since. Worth checking `.venv` vs lockfile, but harmless to results (our timeout wrappers are command-level).
5. **PACKS.md registration drift**: `mcp_warmup_pool_unit_test` says 50/50; file now runs 65/65 (parametrize growth). Refreshed in PACKS.md this campaign. Similarly `test_mcp_test_connection.py` grew 68→71.

## Merge Verdict

**SHIP.** All 4 test areas from the brief PASS. 553 tests, 0 failures, 0 production bugs. The 2 coverage gaps found by recon were filled and verified green (commit `dbed289c`). ensure.md in-scope gates 4/4. Nothing blocks merging `df40ba3a` into latest.
