# Source-Config-Edit-Reload Bugfix — Verification Gate

- **Date**: 2026-09-18
- **Branch**: `feature/fix-source-config-edit-reload` @ `581b15ea` (2 commits on base `a6442bff`):
  - `28179686` — evict-on-stop + dead-reload removal + 12 tests
  - `581b15ea` — scheduler rebuild branch + TOCTOU ordering + 4 tests
- **Footprint** (git diff --name-status a6442bff..581b15ea): 4 production modules + 1 new test file, **zero `frontend/` paths**:
  `M daemon/routers/schedules.py` · `M daemon/routers/sources.py` · `M daemon/sources/base.py` · `M daemon/sources/registry.py` · `A tests/test_source_config_edit_reload_bugfix.py`
- **Reviewer**: APPROVED prior to gate.
- **Method**: 8 workers, every run `uv run python -m pytest` from worktree root (bare `pytest` never invoked), dual-layer timeout (`timeout 300` wrapper + repo per-test pytest-timeout), READ-ONLY mandate (0 fixes, 0 production changes, 0 staging). Drift check (`git rev-parse --abbrev-ref HEAD` + short sha) before/after every git session on all workers — pinned `feature/fix-source-config-edit-reload` / `581b15ea` throughout.
- **Throwaway worktrees** (repo convention, main worktree never checked out/stashed/reset): `/tmp/ens-wt-scer-base-a6442bff`, `/tmp/ens-wt-scer-reds-a6442bff`, `/tmp/ens-wt-scer-mid-28179686` — all removed and verified gone (`git worktree list` clean). Foreign dirt in main worktree (11 disclosed entries) untouched; porcelain snapshots byte-identical before/after.

## VERDICT: ✅ PASS — SHIP

| Task | Result | Key evidence |
|---|---|---|
| 1. Original-symptom verification | ✅ PASS | 16/16 PASS @ HEAD (0.58s); 12F/4P @ base a6442bff — EXACT match to predicted 12 reds, one root-cause class |
| 2. Scheduler regression (review-caught) | ✅ PASS | 3F/1P @ intermediate 28179686 (pause→resume HTTP 500 chain); all 4 green @ HEAD |
| 3. Regression breadth | ✅ PASS | 1,035 neighborhood tests @ HEAD: 0 branch-caused reds; 3 reds = pre-existing, base-proven (2 verbatim @ a6442bff + 1 by-construction) |
| 4. FE check | ✅ SKIPPED (documented) | Zero `frontend/` paths in diff; daemon-internal fix; FE surface unchanged |
| 5. RESULTS file | ✅ | This file |
| **Overall** | **SHIP** | Original symptom closed + regression healed + zero collateral |

---

## Task 1 — Original-symptom verification ✅

**Original bug**: Slack source running with received-agent 'ari' → stop source → edit config received-agent 'ari'→'jober' → start source → inbound messages still routed to OLD agent (edits ignored until daemon restart). Fixed mechanism: `stop_adapter` evicts from `registry._adapters` BEFORE the awaited status persist; `/schedules/{id}/start` gained a rebuild branch.

### 1a. HEAD @ 581b15ea — 16/16 PASS
- Command: `timeout 300 uv run python -m pytest tests/test_source_config_edit_reload_bugfix.py -v`
- Result: **16 passed / 0 failed / 0 skipped in 0.58s**, exit 0.
- Router-level end-to-end symptom pin green: `test_router_start_source_uses_edited_config_after_stop` (#12).

### 1b. BASE @ a6442bff — 12F/4P (EXACT match to prediction)
- Method: throwaway worktree `git worktree add --detach /tmp/ens-wt-scer-base-a6442bff a6442bff`; suite copied in as UNTRACKED file (never committed); `uv sync` warm-up; timed run same command; worktree removed after.
- Result: **12 failed / 4 passed in 1.39s**, exit 1.

### Base-compare table (16 rows)

| # | Test (short) | @ base a6442bff | @ HEAD 581b15ea | Base failure class |
|---|---|---|---|---|
| 1 | stop_adapter_evicts_adapter_from_registry_dict | **FAILED** | PASSED | `registry.get("src-1")` returned live adapter — no eviction from `_adapters` |
| 2 | stop_then_register_new_adapter_does_not_raise_value_error | **FAILED** | PASSED | `ValueError: Adapter already registered: src-1` (registry.py:90) |
| 3 | slack_stop_edit_start_uses_new_default_agent | **FAILED** | PASSED | `ValueError: Adapter already registered: slack-1` |
| 4 | slack_stop_then_start_via_registry_start_adapter_path | **FAILED** | PASSED | stale adapter returned by `get` |
| 5 | discord_stop_edit_start_uses_new_agent_key | **FAILED** | PASSED | stale adapter returned by `get` |
| 6 | list_adapters_excludes_evicted_stopped_source | **FAILED** | PASSED | stopped source still listed |
| 7 | get_after_stop_returns_none | **FAILED** | PASSED | stale adapter returned by `get` |
| 8 | scheduler_stop_still_cleans_supervisor_and_persists_status | **FAILED** | PASSED | scheduler stop path also leaks adapter |
| 9 | slack_adapter_construction_reads_default_agent_at_init | PASSED | PASSED | control: construction reads DB correctly |
| 10 | discord_adapter_construction_reads_agent_key_at_init | PASSED | PASSED | control |
| 11 | telegram_adapter_construction_reads_default_agent_at_init | PASSED | PASSED | control |
| 12 | router_start_source_uses_edited_config_after_stop | **FAILED** | PASSED | router-level symptom: `start_source` reuses stale registry entry (real `SlackAdapter`) |
| 13 | scheduler_pause_resume_round_trip_regression | **FAILED** | PASSED | pause/resume path leaks adapter |
| 14 | scheduler_start_after_boot_skip_rebuilds_adapter | **FAILED** | PASSED | resume-after-restart 500 (pydantic `status=None` enum error) |
| 15 | stop_adapter_evicts_before_persisting_status | **FAILED** | PASSED | TOCTOU: adapter still present during `await update_source_status` |
| 16 | scheduler_start_when_adapter_already_running_is_idempotent | PASSED | PASSED | control: idempotency invariant holds regardless |

- 12 base failures = the 9 no-eviction/stale-reuse reds among the `28179686`-authored set (#1–#8 + router #12) + the 3 review-round-2 additions (#13–#15). Controls #9–#11 (adapter-construction reads config at init) and #16 (idempotency) green at base — clean separation of symptom surface from invariant pins.
- **Adjudication**: single root-cause class at base (no eviction → stale `_adapters` entry → register-ValueError / stale-get / stale-router-reuse / scheduler-leak); all 12 green at HEAD. Original symptom CLOSED at router AND registry level.

## Task 2 — Scheduler regression (review-caught) ✅

- Method: throwaway worktree @ `28179686` (the intermediate commit: eviction in, rebuild branch NOT yet in); HEAD suite copied as UNTRACKED **distinct-filename** `tests/test_source_config_edit_reload_bugfix_head16.py` (tracked 12-test file at that commit NOT overwritten — base-proof hygiene); the 4 `581b15ea`-added tests verified via `git diff 28179686..581b15ea -- tests/test_source_config_edit_reload_bugfix.py` (+495 lines, single trailing hunk) then run by exact node id.
- Result @ 28179686: **3 failed / 1 passed in 1.02s**.

| Test | @ 28179686 | @ HEAD | Evidence @ 28179686 |
|---|---|---|---|
| test_scheduler_pause_resume_round_trip_regression | **FAILED** | PASSED | `assert 500 == 200`: `SourceActionResponse status=None` → pydantic enum error (`Input should be 'stopped','starting','running' or 'error', input=None`); chain: evicted registry → `registry.py:494 Adapter not found: sched-1` → `start_adapter` False → route builds `status=None` → `schedules.py:290 Failed to start scheduler` |
| test_scheduler_start_after_boot_skip_rebuilds_adapter | **FAILED** | PASSED | same 500 chain (boot-skip case; registry already empty at start) |
| test_stop_adapter_evicts_before_persisting_status | **FAILED** | PASSED | TOCTOU pin: `observed['adapter_present_at_persist']` True — pre-fix pop happens AFTER the persist await |
| test_scheduler_start_when_adapter_already_running_is_idempotent | PASSED | PASSED | control — rebuild branch must not regress already-running case |

- **Adjudication**: the review-caught regression is REAL at the intermediate commit (pause→resume after eviction = HTTP 500 every time, exactly as reported in review) and HEALED at HEAD by the `581b15ea` rebuild branch. The leader's fallback option ("run at base and document the resume-after-restart pre-existing failure instead") was NOT needed — the intermediate-commit worktree dance succeeded.

## Task 3 — Regression breadth beyond dev's scoped run ✅ (dev ran 20 source-subsystem files: 933P/5xfailed)

### 3a-1. Top-level sources/scheduler theme neighborhood @ HEAD — PASS
- Command: `timeout 300 uv run python -m pytest <15 enumerated files> -q --tb=short`
- Files: test_discord_adapter, test_scheduler_adapter, test_scheduler_api, test_scheduler_instance_mode, test_slack_adapter, test_source_formatter_edge_cases, test_source_formatters, test_sources_circuit_breaker, test_sources_dispatcher, test_sources_mapper, test_sources_persistence, test_sources_registry, test_sources_system_fix, test_telegram_adapter, tests/unit/routers/test_source_reservation.
- Result: **833 passed / 0 failed / 5 xfailed (838 collected) in 16.0s**, exit 0. 838 = exact per-file sum from the discovery inventory; the 5 xfails match the dev run's 5 xfailed (same-neighborhood corroboration).

### 3a-2. tests/test_api.py @ HEAD — expected-reds-only
- Command: `timeout 300 uv run python -m pytest tests/test_api.py -q --tb=short`
- Result: **45 passed / 2 failed (47 collected) in ~1.1s**. The ONLY 2 reds are the known pre-existing pair (below), signatures byte-identical to base. Zero other reds.

### 3b. tests/unit registry-adjacent @ HEAD — PASS modulo 1 pre-existing
- Command: `timeout 300 uv run python -m pytest tests/unit/test_api_router_extraction.py tests/unit/services/test_command_dispatcher.py tests/unit/routers/test_source_reservation.py -q --tb=short`
- Result: **149 passed / 1 failed (150 collected) in 3.54s**. The 1 red is pre-existing (below). Excluded as adjudicated false-positive themes: test_completion_registry.py, test_long_tool_nudge_registry.py, test_usage_limit_schedule.py, test_b_kill_switch_registry.py (different subsystems' "registry"/"schedule").

### Quarantine adjudications (3 reds total across the neighborhood; 0 branch-caused)

| Red | Base-proof | Adjudication |
|---|---|---|
| `tests/test_api.py::test_send_message_success` | FAILED identically @ a6442bff (throwaway worktree, verbatim traceback): `TypeError: object Mock can't be used in 'await' expression` @ `daemon/routers/messages.py:249` (`await manager.command_dispatcher.dispatch`) | **PRE-EXISTING** — messages.py:249 await-rot family (QUARANTINE row 2026-09-15 critical-notes gate; known F5 fixture fix). Excluded. |
| `tests/test_api.py::test_global_exception_handler` | FAILED identically @ a6442bff, same line/exception (via global-handler path) | **PRE-EXISTING** — same family. Excluded. |
| `tests/unit/test_api_router_extraction.py::TestApiModuleSize::test_api_module_is_small` (`daemon/api.py` 2619 ≥ 1600 band, assert @ :778) | By construction: `git diff --name-status a6442bff..581b15ea` contains NO `daemon/api.py` entry → file byte-identical base↔HEAD → failure necessarily identical at base | **PRE-EXISTING** — `api_module_size` family (QUARANTINE 2026-09-14 consolidated row, `2489>1600`; documented drift 2400→2489→2619 from intervening commits). Excluded. |

## Task 4 — FE check ✅ (SKIPPED, documented)

> FE automation skipped: daemon-internal fix — `git diff --name-status a6442bff..581b15ea` touches zero `frontend/` paths (4 daemon modules + 1 test file); the FE calls the same routes with unchanged success response shapes, and no FE e2e path exercises Slack source config stop→edit→start routing without live Slack credentials.

## ensure.md (Core, blast-radius scoped — scoped bugfix; Release Gate NOT warranted)

| Requirement | Status | Evidence |
|---|---|---|
| Core #1 — No regressions in changed packs | ✅ PASS | All scoped packs green modulo the 3 quarantined pre-existing exclusions above |
| Core #2/#3 — Concurrency integrity / no sync DB on loop | ✅ PASS | `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` → 98P/74S/0F in 8.09s (baseline parity) |
| Core #4 — dev.sh `--timeout-graceful-shutdown 10` | ✅ PASS | grep @ dev.sh:102 (live flag), comment at :99 |
| Important #1/#2 | N/A-scoped | No async-conversion callers touched; no deadlock surface in diff |
| Release Gate | NOT RUN | Scoped bugfix, no architecture change (per ensure.md scoping rule) |

## Exact commands (authoritative)

```
# T1a HEAD
timeout 300 uv run python -m pytest tests/test_source_config_edit_reload_bugfix.py -v
# T1b BASE (throwaway worktree /tmp/ens-wt-scer-base-a6442bff @ a6442bff, suite copied untracked)
timeout 300 uv run python -m pytest tests/test_source_config_edit_reload_bugfix.py -v
# T2 INTERMEDIATE (worktree /tmp/ens-wt-scer-mid-28179686 @ 28179686, HEAD suite copied as ..._head16.py)
timeout 300 uv run python -m pytest "tests/test_source_config_edit_reload_bugfix_head16.py::test_scheduler_pause_resume_round_trip_regression" "tests/test_source_config_edit_reload_bugfix_head16.py::test_scheduler_start_after_boot_skip_rebuilds_adapter" "tests/test_source_config_edit_reload_bugfix_head16.py::test_stop_adapter_evicts_before_persisting_status" "tests/test_source_config_edit_reload_bugfix_head16.py::test_scheduler_start_when_adapter_already_running_is_idempotent" -v --tb=short
# T3c BASE known-reds (worktree /tmp/ens-wt-scer-reds-a6442bff @ a6442bff)
timeout 300 uv run python -m pytest "tests/test_api.py::test_send_message_success" "tests/test_api.py::test_global_exception_handler" -v --tb=short
# T3a-1 HEAD neighborhood (15 files)
timeout 300 uv run python -m pytest tests/test_discord_adapter.py tests/test_scheduler_adapter.py tests/test_scheduler_api.py tests/test_scheduler_instance_mode.py tests/test_slack_adapter.py tests/test_source_formatter_edge_cases.py tests/test_source_formatters.py tests/test_sources_circuit_breaker.py tests/test_sources_dispatcher.py tests/test_sources_mapper.py tests/test_sources_persistence.py tests/test_sources_registry.py tests/test_sources_system_fix.py tests/test_telegram_adapter.py tests/unit/routers/test_source_reservation.py -q --tb=short
# T3a-2 HEAD API file
timeout 300 uv run python -m pytest tests/test_api.py -q --tb=short
# T3b HEAD unit neighborhood
timeout 300 uv run python -m pytest tests/unit/test_api_router_extraction.py tests/unit/services/test_command_dispatcher.py tests/unit/routers/test_source_reservation.py -q --tb=short
# ensure Core #2/#3 + #4
timeout 300 bash test/packs/concurrency_atomic_unit_test.sh
grep -n "timeout-graceful-shutdown" dev.sh
```

## Aggregate counts

- **@ HEAD 581b15ea**: 1,051 tests run (16 + 838 + 47 + 150) → **1,043 passed / 3 failed (all pre-existing: await-rot ×2 + api_module_size ×1) / 5 xfailed**. **Branch-caused failures: 0.**
- **@ base a6442bff**: suite 12F/4P; known-reds 2F (identical signatures).
- **@ intermediate 28179686**: round-2 set 3F/1P.
- **ensure Core**: concurrency 98P/74S/0F.

## Workers (instance ids)

| Worker | Instance | Task |
|---|---|---|
| scer-discover | 2e38287b | T0 inventory (no skill) |
| scer-head-suite | 04d0157a | T1a |
| scer-base-suite | bd61929d | T1b |
| scer-intermediate | 9d214b59 | T2 |
| scer-toplvl-sources | ccf8360b | T3a-1 |
| scer-toplvl-api | b8d9ffe3 | T3a-2 |
| scer-unit-adjacent | 70b40535 | T3b |
| scer-reds-base | 8a3d7a8f | T3c |
| scer-ensure-core | cc85309e | ensure Core #2-#4 |

## Follow-ups (non-blocking)

1. 🟠 F5 (carried): AsyncMock-ify `manager.command_dispatcher.dispatch` in `tests/test_api.py` `mock_manager` fixture — closes the messages.py:249 await-rot family (2 reds here + wider family).
2. 🟢 `api_module_size` band: daemon/api.py 2619 vs <1600 — test-debt band assert, red since the 2026-09-14 gate (2400→2489→2619 drift); route to owner for band re-baseline or continued extraction.
3. 🟢 Stale `test/packs/sources_unit_test.sh` (Aug 24) not used by this gate — consider refreshing/retiring if superseded by direct file-scoped runs.
