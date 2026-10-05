# G1 Full-Dir Sweep — Chunk Plan (amended, dispatch-ready)

Commission: durability F-1+F-2 merge gate · branch `feature/durability-f1-f2` @ `fb0f4655` (base `18827dbd`) · worktree `/home/nea/ensemble-src-wt-durability`
Source: g1-inventory worker (740e898c) read-only report 2026-10-05, amended by Test Leader (Worker E lane + reconciliation + ordering).
Status: STAGED — dispatch only after G4 (demo E2E) completes (quiet machine for straddle windows; prior E2E saw ~60s tool-timeout kills under load).

## Measured scope (trust measurement, not estimates)

- Default-collected (addopts `-m 'not integration and not postgres and not slow'`): **19,652** per-dir sum. Commission's "~7000+" is stale — report deviation in G5.
- tests/postgres: 329 (`-m postgres`, serial only — conftest auto-skips under xdist).
- Live-OpenCode `integration`-marked: 442 — EXCLUDED per prior-gate precedent (no OpenCode provisioned). Optional D2 lane NOT run.
- `tests/e2e/test_context_injection_hybrid.py` — EXCLUDED (import-time HTTP call to localhost:8079 = collection error without live daemon).
- **Open item:** ~149 top-level `tests/test_*.py` files (~3,500 tests) may NOT be inside the 19,652 dir-sum (inventory's note 6 vs E.2 reconciliation is ambiguous). Worker E enumerates + runs them explicitly; final reconciliation resolves the true total.

## Invocation conventions (binding for all lanes)

- Common: `cd /home/nea/ensemble-src-wt-durability`, `timeout 1500s .venv/bin/pytest <targets> -p no:cacheprovider --tb=short -q` (default addopts kept — integration/postgres/slow deselected by design), log to /tmp/ac-gate/g1-<ID>.log, exit 124=TIMEOUT.
- Import gate + HEAD check (fb0f4655) in every worker preflight.
- PG lane (D1) ONLY: `PG_TEST_HOST=localhost PG_TEST_PORT=5432 PG_TEST_DB=ensemble_test PG_TEST_USER=ensemble PG_TEST_PASSWORD=ensemble_dev` + `--override-ini="addopts=" -m postgres`, NO xdist.
- Per-test inner timeout: pyproject `timeout=30/thread` (untouched).
- No `-x`. `--continue-on-collection-errors` NOT set for run chunks (failures visible); collection issues surface per-chunk.

## Chunks

| ID | Worker | Targets | Expected |
|---|---|---|---|
| A1 | A | tests/unit/tools | 3,351 |
| A2 | A | tests/unit/services | 2,399 |
| A3 | A | tests/unit/{routers,repositories,rag,job_queue,persistence,models,graph,config,checkpoint_adapter,job_state,sources} | 995 |
| B1 | B | tests/unit root a–h (ignore-glob split per inventory H.2, subdirs ignored) | ~2,200 |
| B2 | B | tests/unit root i–p (same) | ~2,900 |
| B3 | B | tests/unit root q–z (same) | ~2,800 |
| C1 | C | tests/job_queue tests/services tests/repositories tests/migration | 3,066 |
| C2 | C | tests/message_queue_redesign tests/api tests/manager tests/lint tests/static tests/performance tests/property tests/integration tests/opencode + e2e 7-file explicit list (answer_dismiss_flow, full_chain_turn_reconciler, pause_during_report_resume_turn_handle, pause_during_report_turn_then_resume, pause_resume_unchanged, promote_stop_frozen_amnesty_e2e, wanderer_orchestrator_e2e) | 1,972 |
| E1 | E | top-level `find tests -maxdepth 1 -name 'test_*.py'` a–m slice | ~1,750 |
| E2 | E | top-level n–z slice | ~1,750 |
| D1 | E (serial, after E2) | tests/postgres (PG convention) | 329 |

Expected grand total (default lanes): 19,652 + ~3,500 (if root files are additive) = ~23,152 — reconciled from actual per-chunk collected counts at aggregation.

## Known pre-existing REDs (classify on sight, NOT new)

1. ×3 `TestSite1InlineMirrorFinalize::{test_hook_fires_settled_and_dual_fire_is_deduped, test_pre_terminal_task_completes_on_success_fully, test_hook_skips_when_finalize_races_to_none}` — tests/job_queue/test_event_driven_completion.py:262 (C1) — v0.13.10-era, MagicMock JSON vs repository.py:2602.
2. ×1 `TestTitleGeneration::test_triggers_title_on_idle_to_running` — tests/test_enqueue_shared.py:486 (E lane) — enqueue_shared idle→running drift, v0.13.10-era.
Also classify against `.agents/tester/QUARANTINE.md` active families (orphan-sweep pair, migration boolean-default, real-saver caplog pair, llm-stream-stall 74-family, release_journal 14-family) — QUARANTINE families are known failures in their OWN packs; if they surface in sweep dirs they are pre-existing, cite the QUARANTINE row. Anything else = NEW → blocker candidate → targeted base A/B (fresh worktree @18827dbd, failing files only).

## Aggregation duties (Test Leader)

- Sum per-chunk collected/passed/failed/skipped; reconcile vs full-tree; name any gap.
- Classify every failure: known-4 / QUARANTINE family / NEW.
- NEW failures → dispatch targeted base-A/B worker before verdict.
