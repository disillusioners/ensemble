# Phase 0 — v2-Base Gate-Suite Pre-Counts (T0.4)

> Date: 2026-09-03
> Method (per v1's `tests/integration/gate_suites/GATE_SUITES.txt` header):
> one pytest subprocess per file — `uv run pytest <file> -o addopts= --collect-only -q -p no:cacheprovider --no-header` — strips the v2 default `addopts = "-m 'not integration and not postgres'"` so integration tests collect.
> Pinned DSN: `postgresql://ensemble:<pw>@localhost:5432/ensemble_cpv2_test` (per dispatcher adjudication).

## v1's `GATE_SUITES.txt` (37 rows) → v2-base pre-counts

| # | File | v1 manifest count | v2 collect-only | Status |
|---|------|-------------------|-----------------|--------|
| 1 | tests/e2e/test_pause_resume_unchanged.py | 1 | **1** | OK |
| 2 | tests/unit/test_pause_resume_root.py | 18 | **18** | OK |
| 3 | tests/integration/test_pause_race_resume_flow.py | 1 | **1** | OK |
| 4 | tests/integration/test_pause_race_resume_drain.py | 1 | **1** | OK |
| 5 | tests/integration/test_pause_race_resume_reenqueue.py | 1 | **1** | OK |
| 6 | tests/integration/test_pause_race_enqueue_resume_flow.py | 1 | **1** | OK |
| 7 | tests/integration/test_pause_race_window_held.py | 1 | **1** | OK |
| 8 | tests/integration/test_pause_race_window_held_enqueue.py | 1 | **1** | OK |
| 9 | tests/e2e/test_full_chain_turn_reconciler.py | 3 | **3** | OK |
| 10 | tests/repositories/test_turn_reconciler.py | (PR1+) | **25** | OK |
| 11 | tests/repositories/test_turn_reconciler_paused_race.py | (PR1+) | **5** | OK |
| 12 | tests/integration/test_multi_turn_resume.py | (PR1+) | **3** | OK |
| 13 | tests/integration/test_crash_recovery_paused.py | (PR1+) | **10** | OK |
| 14 | tests/unit/test_turn_handle_transitions.py | (PR1+) | **21** | OK |
| 15 | tests/unit/test_paused_auto_resume_fallback.py | (PR1+) | **5** | OK |
| 16 | tests/unit/test_terminal_reason_mirror_set_regression.py | (PR1+) | **1** | OK |
| 17 | tests/services/test_instance_messaging_compaction_guard.py | (PR1+) | **8** | OK |
| 18 | tests/unit/test_compaction.py | (PR1+) | **129** | OK |
| 19 | tests/integration/test_compaction_e2e.py | (PR1+) | **3** | OK |
| 20 | tests/integration/test_api_messages.py | (PR1+) | **7** | OK |
| 21 | tests/test_persistence.py | (PR1+) | **23** | OK |
| 22 | tests/test_maintenance.py | (PR1+) | **69** | OK |
| 23 | tests/unit/persistence/test_checkpoint_perf_logging.py | (PR1) | — | **MISSING — Phase 1 port** |
| 24 | tests/unit/repositories/test_message_metadata_repository.py | (PR2) | — | **MISSING — Phase 2 port** |
| 25 | tests/unit/services/test_message_tap_slot.py | (PR2) | — | **MISSING — Phase 2 port** |
| 26 | tests/unit/repositories/test_message_tap_to_repo_liveness.py | (PR2) | — | **MISSING — Phase 2 port** |
| 27 | tests/unit/repositories/test_message_metadata_revive_stability.py | (PR2) | — | **MISSING — Phase 2 port** |
| 28 | tests/unit/repositories/test_message_metadata_paused_question_flow.py | (PR2) | — | **MISSING — Phase 2 port** |
| 29 | tests/integration/test_message_metadata_hook_placement.py | (PR2) | — | **MISSING — Phase 2 port** |
| 30 | tests/integration/test_message_metadata_liveness.py | (PR2) | — | **MISSING — Phase 2 port** |
| 31 | tests/unit/persistence/test_get_instance_messages_no_alist.py | (PR3) | — | **MISSING — Phase 3 port** |
| 32 | tests/integration/test_get_instance_messages_response_shape_frozen_fixture.py | (PR3) | — | **MISSING — Phase 3 port** |
| 33 | tests/integration/test_message_metadata_lifecycle_wiring.py | (PR3) | — | **MISSING — Phase 3 port** |
| 34 | tests/unit/checkpoint_adapter/test_direct_anti_join.py | (PR4) | — | **MISSING — Phase 4 port** |
| 35 | tests/unit/services/test_maintenance_prune_direct_anti_join.py | (PR4) | — | **MISSING — Phase 4 port** |
| 36 | tests/integration/checkpoint_prune_real_saver.py | (PR4 binding gate) | — | **MISSING — Phase 4 port** |
| 37 | tests/integration/checkpoint_prune_restore_rehearsal.py | (PR4) | — | **MISSING — Phase 4 port** |

## Totals (v2-base, pre-port)

- **v2-base collected tests (existing 22 files):** **337**
- **Aggregate cross-check:** `337 tests collected in 0.77s` (matches per-file sum)
- **Missing files:** 15 (flagged for port per their phase column)
- **Total v1 manifest at `fc908945`:** 411 tests across 37 rows
- **v2 delta vs v1:** −74 tests (337 existing + 0 missing = 337 vs 411); the missing tests will arrive with their respective phases (1..4).

## pyproject.toml `[tool.pytest.ini_options]` diff (v1-base vs v2-tip)

```
[fc908945 — v1 base]
[tool.pytest.ini_options]
markers = [
    "integration: …",
    "postgres: …",
    "no_xdist: …",
]
# NO addopts line — defaults apply.
```

```
[2f80d45b — v2 tip]
[tool.pytest.ini_options]
markers = [
    "integration: …",
    "postgres: …",
    "no_xdist: …",
]
addopts = "-m 'not integration and not postgres'"
asyncio_mode = "auto"
timeout = 30
timeout_method = "thread"
```

### Implications

- v2 default `addopts = "-m 'not integration and not postgres'"` EXCLUDES integration tests by default. The v1 method's `-o addopts=` override strips this filter so integration tests collect. **v2 regen commands MUST keep `-o addopts=`** (otherwise the integration rows won't be picked up).
- v2 adds `asyncio_mode = "auto"` (pytest-asyncio config) — relevant for `async def test_*` collection.
- v2 adds `timeout = 30` + `timeout_method = "thread"` — per-test 30s thread-based timeout (NOT auto-cancelling event loops).

## Method notes

- Per-file counts are extracted from `tail -3` of each `--collect-only` run, looking for `N test[s] collected`.
- Aggregate cross-check uses one pytest subprocess over all 22 existing paths — confirms per-file sum.
- Missing files are NOT included in the per-file or aggregate counts (would error on import).