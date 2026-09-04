# Phase 5 — T5.1 Binding Gate Results (PR5 Acceptance Gate, FR-1 / AC-1.1)

> Recorded by: coder (Phase-5 Stage-1 implementer)
> Date: 2026-09-04 (UTC)
> Commit SHA at run time: `d5f3a2b0` (branch `feature/langgraph-checkpoint-perf-v2`; tip verified pre-run via `git branch --show-current` + `git rev-parse --short HEAD`)

## Verdict

**T5.1 PASS — 9 passed, 0 failed, 0 skipped** on real disposable PG. Matches the v1 `7a7998fe` baseline (9/9). ZERO skips — the SKIP-LOUDLY contract did not fire; PG was reachable and every test ran for real. Per plan T5.1 acceptance ("9/9 GREEN recorded in phase5-binding-gate-results.md") this is the recorded evidence.

## A2 — Disposable PG parity (PIN-PARITY ≥14.22)

Command (DSN-pinned, disposable DB only — `ensemble_prod` / `ensemble_dev` never referenced):

```
psql -h localhost -U ensemble -d ensemble_cpv2_test -c "SELECT version();"
```

Output (verbatim):

```
                                                            version
-------------------------------------------------------------------------------------------------------------------------------
 PostgreSQL 14.22 (Homebrew) on aarch64-apple-darwin23.6.0, compiled by Apple clang version 16.0.0 (clang-1600.0.26.6), 64-bit
(1 row)
```

→ PostgreSQL **14.22** — satisfies PIN-PARITY ≥14.22 (same major.minor family as prod, per architect §2.1). Matches the Phase 0 T0.2/T0.3 + Phase 4 baseline. The `ensemble_cpv2_test` database already existed; **no `createdb` was required** (recorded command would have been `createdb -h localhost -U ensemble ensemble_cpv2_test`).

## A3 — Environment sanity

```
uv sync                      # bare (never --extra dev; dev deps included per PEP 735 group)
→ Resolved 123 packages in 1ms / Audited 116 packages

uv run python -c "import daemon.services.maintenance, daemon.repositories.message_metadata.repository; print('imports ok')"
→ imports ok
```

## DSN discipline (binding for every DSN-resolving command)

Every command in this run that could resolve a DSN carried BOTH env vars (Phase-4 precedent, phase4-results.md header):

```
POSTGRES_URL=postgresql://ensemble@localhost:5432/ensemble_cpv2_test
POSTGRES_DB=ensemble_cpv2_test
```

`ensemble_prod` and `ensemble_dev` were never referenced by any command in this session.

## A4 — Collect-only pre-check (fixtures resolve; burns nothing)

```
POSTGRES_URL=postgresql://ensemble@localhost:5432/ensemble_cpv2_test POSTGRES_DB=ensemble_cpv2_test \
  uv run pytest tests/integration/checkpoint_prune_real_saver.py --collect-only -q
→ 9 tests collected in 0.09s
```

## A5 — THE GATE RUN

Command (verbatim, DSN-pinned):

```
POSTGRES_URL=postgresql://ensemble@localhost:5432/ensemble_cpv2_test POSTGRES_DB=ensemble_cpv2_test \
  uv run pytest tests/integration/checkpoint_prune_real_saver.py -v
```

Result: **9 passed in 3.18s** — 0 failed, 0 skipped.

Last ~20 lines of `-v` output (verbatim):

```
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverWritePruneResume::test_real_saver_write_retention_prune_blob_prune_resume PASSED [ 11%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverWritePruneResume::test_real_saver_kill_safe_restart_reconstruction PASSED [ 22%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverDeltaSnapshotChain::test_delta_chain_snapshot_blob_survives_and_orphan_snapshot_dies PASSED [ 33%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverFailSafe::test_real_saver_zero_refs_skip_logs_error_and_deletes_nothing PASSED [ 44%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverConcurrentAput::test_real_saver_concurrent_aput_new_blob_preserved PASSED [ 55%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverRaceWindow::test_preexisting_referenced_blobs_survive_traffic_and_prune_byte_equal PASSED [ 66%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverSerializableRetry::test_real_40001_aborts_delete_then_retry_completes PASSED [ 77%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverDryRunReport::test_real_saver_dry_run_report_line_shape PASSED [ 88%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverSqliteNoOp::test_real_saver_sqlite_backend_noops_with_warning PASSED [100%]

============================== 9 passed in 3.18s ===============================
```

## Post-T5.19 regression re-run (B5)

After the T5.19 prune code landed (see T5.19 results), the gate was re-run once more with the identical DSN-pinned command:

**9 passed, 0 failed, 0 skipped** — no regression from the prune work.

## Notes

- This file satisfies plan T5.1's acceptance: "9/9 GREEN recorded in `phase5-binding-gate-results.md`".
- The binding gate remains the destructive-enable regression boundary: any future FAIL or SKIP-as-pass here is a STOP-and-request-architect condition per T5.1.
- This file covers ONLY T5.1 (+ the B5 regression re-run). It does NOT constitute the FR-8 reviewer re-review (T5.7 requires a dispatched reviewer instance, not the implementer).
