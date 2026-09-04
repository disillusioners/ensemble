# Phase 4 — Results: PR4 Port (C3 Reference-Aware Checkpoint_Blobs Prune)

> Date: 2026-09-04 (UTC) | v2 HEAD: `1569c82a` (post-T4.7 chore state; commits f8d59c01 + a1ae0f91 + 1569c82a on the branch)
> Branch: `feature/langgraph-checkpoint-perf-v2`
> Port method: 2 cherry-picks (`git cherry-pick -x f89ccacc` → `git cherry-pick -x 7a7998fe`) + 1 gate regen (T4.7).
> DSN discipline: every DSN-resolving invocation carried BOTH `POSTGRES_URL=postgresql://ensemble@localhost:5432/ensemble_cpv2_test` AND `POSTGRES_DB=ensemble_cpv2_test` (PG trust auth, no password). `ensemble_prod` / `ensemble_dev` never referenced.
> Push: NO push (per task brief); all commits land locally on the v2 branch.
> PG version: PostgreSQL 14.22 (Homebrew) on aarch64-apple-darwin23.6.0 — verified at T4.9 (matches Phase 0 T0.2 + T0.3 baseline).

## Per-task outcomes

### T4.1 — Diff analysis — DONE

- Read `git show f89ccacc` end-to-end (13-file surface: 5 clean-adds + 3 HOT files + 4 test files + 1 helper + gate manifest +26).
- Read `git show 7a7998fe` end-to-end (5-file surface: SERIALIZABLE wrap + retraction + race tests).
- SERIALIZABLE wrap config verified verbatim:
  - `CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES = 3` (daemon/constants.py:92)
  - `0.05 * (2 ** (attempt - 1))` 50ms·2ⁿ backoff (daemon/checkpoint_adapter.py:780)
  - Exhaustion returns `(0, 0)` (line 778) and skips without raising (line 762 raises only non-isolation failures; isolation failures return `(0, 0)`)
- Ap ut non-atomicity RETRACTION verified verbatim:
  - Citations: `aio.py:82` (line 689), `aio.py:280-304` (line 689), `aio.py:393-399` (line 693)
  - "default pipeline path commits as SEPARATE implicit transactions — a µs-scale gap"
  - "(The non-pipeline fallback IS atomic — aio.py:393-399.)"
  - "HONEST LIMIT... a lone rw-out-edge is not a dangerous structure and READ COMMITTED reads never register in the SSI graph"
- Runbook §7 intra-process race disclosure verified verbatim (lines 163-191 of `docs/runbooks/checkpoint-blob-prune-restore.md`).
- Hunk boundaries + insertion anchors + v2 anchor map documented in `phase4-diff-analysis.md`.

### T4.2 — `daemon/services/checkpoint_prune.py` clean-add — DONE

- Pre-creation (manual `cp` from v1 `f89ccacc:daemon/services/checkpoint_prune.py`) was reverted (file removed via `rm`) so the cherry-pick could create it natively via 3-way merge.
- After cherry-pick: 267 lines, byte-identical to v1 `fc908945:daemon/services/checkpoint_prune.py` (verified via `diff -q`).
- `py_compile` OK.
- Orchestration verified:
  - ✓ Anti-join (line 28: ``count_blobs_anti_join`` / ``delete_blobs_anti_join`` use `_BLOB_ANTI_JOIN_PREDICATE`)
  - ✓ Dual flag ladder (`blob_prune_destructive_enabled()` at line 71)
  - ✓ Flag-gated structural-unreachability (line 28: "structurally unreachable ... single call site, AST-gated + runtime sentinel + 8-combo flag matrix")
  - ✓ ZERO_REFS_FAIL_SAFE (line 161: "blob_prune ZERO_REFS_FAIL_SAFE thread=%s ns=%s — channel_versions extraction yielded 0 refs while checkpoints remain (possible schema drift); skipping pair, zero rows deleted")

### T4.3 — STOP-GATE grep on `_finalize_job_db_sync` / `_terminate_instance_db_sync` — DONE (PASS)

```
grep -n "_finalize_job_db_sync\|_terminate_instance_db_sync" daemon/services/checkpoint_prune.py daemon/services/maintenance.py
→ exit 1 (no matches)
```

**STOP-GATE PASSED.** Operation E is orthogonal to the deferred-emit / idle-gate / status-write paths. Expected outcome: zero matches. Verified.

### T4.4 — Cherry-pick ATOMIC PAIR (f89ccacc + 7a7998fe) — DONE

**Cherry-pick 1: `f89ccacc` → commit `f8d59c01`**
- `git cherry-pick -x f89ccacc` succeeded with auto-merge on `daemon/constants.py`.
- 1 conflict on `tests/integration/gate_suites/GATE_SUITES.txt` (3-way merge: v1's GATE_SUITES has PR4 entries + different PR3 ordering; v2's GATE_SUITES has Phase 3 ordering + PR2 entries).
- **Resolution:** `--ours` (HEAD version) — we'll regenerate GATE_SUITES fresh on v2 in T4.7. PR4 entries will be re-added during regeneration.
- All other hunks auto-merged cleanly (the bulk `_BLOB_ANTI_JOIN_PREDICATE` + 4 abstract methods + 4 PG impls + 4 SQLite stubs + maintenance Operation E + checkpoint_prune.py clean-add + 4 test files + helper + runbook).
- 11 files modified (matches v1's `f89ccacc` stat MINUS `tests/integration/gate_suites/GATE_SUITES.txt` which we kept HEAD's).
- Diff stat: 2660 insertions, 2 deletions.
- `-x` provenance: `(cherry picked from commit f89ccacc7bedd517895357128fde6270ff0f7e23)`.
- `py_compile` OK on all 3 hot files.

**Cherry-pick 2: `7a7998fe` → commit `a1ae0f91`**
- `git cherry-pick -x 7a7998fe` succeeded with auto-merge on `daemon/constants.py`.
- 0 conflicts (the SERIALIZABLE wrap replaces `delete_blobs_anti_join` body — no v2 churn on this method since v2 daemon/checkpoint_adapter.py was byte-identical to v1 base).
- 5 files modified (matches v1's `7a7998fe` stat exactly).
- Diff stat: 565 insertions, 17 deletions.
- `-x` provenance: `(cherry picked from commit 7a7998fe52a189af0b462e3ec2dae68e4bfa4100)`.
- `py_compile` OK on all 3 hot files.

**Landing checklist verification:**
| Item | Status | Anchor (v2-tip) |
|------|--------|-----------------|
| `_BLOB_ANTI_JOIN_PREDICATE` constant | ✓ | daemon/checkpoint_adapter.py:58 |
| 4 abstract methods on `CheckpointerAdapter` (`find_all_thread_ns_pairs`, `count_refs_for_blob_thread`, `count_blobs_anti_join`, `delete_blobs_anti_join`) | ✓ | lines 139-211 |
| 4 concrete PG impls on `PostgresCheckpointerAdapter` (with SERIALIZABLE wrap on `delete_blobs_anti_join`) | ✓ | lines 583-795 |
| SQLite stubs returning `(0, 0)` + WARNING | ✓ | lines 351-413 |
| maintenance.py Operation E (`_prune_unreferenced_blobs`) | ✓ | line 788 |
| `try/except Exception` wrapper (NEVER `except BaseException`) | ✓ | line 461 |
| 4 constants on `daemon/constants.py` (`CHECKPOINT_BLOB_PRUNE_DRY_RUN`, `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE`, `CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD`, `CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES`) | ✓ | lines 84-92 |
| `-x` provenance in both commit messages | ✓ | f8d59c01 + a1ae0f91 |
| SERIALIZABLE wrap on destructive arm (7a7998fe) | ✓ | daemon/checkpoint_adapter.py:728-795 |
| `aio.py:82, 280-304, 393-399` citations (7a7998fe retraction) | ✓ | lines 689, 693 |
| Runbook §7 intra-process race disclosure (7a7998fe fold) | ✓ | docs/runbooks/checkpoint-blob-prune-restore.md:163-191 |

**Byte-equality verification vs v1 `fc908945`:**
```
BYTE-IDENTICAL: daemon/checkpoint_adapter.py
BYTE-IDENTICAL: daemon/services/checkpoint_prune.py
BYTE-IDENTICAL: daemon/services/maintenance.py
BYTE-IDENTICAL: docs/runbooks/checkpoint-blob-prune-restore.md
BYTE-IDENTICAL: tests/integration/checkpoint_prune_real_saver.py
BYTE-IDENTICAL: tests/integration/checkpoint_prune_restore_rehearsal.py
BYTE-IDENTICAL: tests/unit/checkpoint_adapter/test_direct_anti_join.py
BYTE-IDENTICAL: tests/unit/services/test_maintenance_prune_direct_anti_join.py
BYTE-IDENTICAL: tests/helpers/checkpoint_prune_pg.py
```
- `daemon/constants.py`: PR4 constants (lines 84-92) byte-identical; v2 carries Phase 1..3 surface (INJECTION_ELIGIBLE_STATUSES, TERMINAL_INSTANCE_STATUSES, leaf-module invariant) that v1 `fc908945` lacks — expected drift, not port-introduced.

**Conflict count: 1** (GATE_SUITES.txt, resolved by `--ours`).

**Architect §1.2 correction verification:**
- `git log --oneline 58260f35..2f80d45b -- daemon/checkpoint_adapter.py | wc -l` → **0** (byte-identical between v1-base and v2-base)
- `git log --oneline 58260f35..2f80d45b -- daemon/services/maintenance.py | wc -l` → **0** (byte-identical)
- `git log --oneline 58260f35..2f80d45b -- daemon/constants.py | wc -l` → **13** (LOW conflict, adjacent-inserts class; all 4 PR4 flag names absent from v2 → clean insertion at the same anchor)

### T4.5 — Runbook port — DONE

- Runbook landed via cherry-pick pair (f89ccacc clean-adds + 7a7998fe adds the §7 disclosure block).
- Final state: `docs/runbooks/checkpoint-blob-prune-restore.md` (224 lines, byte-identical to v1 `fc908945`).
- All 7 sections per C-19 verified (see `phase4-runbook-verify.md` for the per-section mapping):
  - §1 pre-enable checklist (`## PRE-ENABLE CHECKLIST` line 35)
  - §2 prod `channel_versions` JSONB shape verification query (`### [ ] 2.` line 63 — 2 SQL queries)
  - §3 destructive flip gate (`### [ ] 7. Flip the ladder` line 153 + env-var flip procedure lines 193-197)
  - §4 backup-as-recovery of record (`## ROLLBACK (post-enable breakage)` line 205 — 5 steps)
  - §5 idle-gate precondition (`The idle gate is a PRECONDITION, not a lock` line 174)
  - §6 backup covers recovery (`### [ ] 6. Snapshot PROD \`checkpoint_blobs\` BEFORE the first destructive cycle` line 142)
  - §7 intra-process race disclosure (`**Residual intra-process race disclosure (PR4 external review, 2026-08-26).**` line 163)
- Verbatim §7 disclosure content verified (lines 163-191 of runbook): contains `aio.py:82`, `aio.py:280-304`, `aio.py:393-399` citations + "Honest scope of this rule" + "DB-level hardening shipped with this disclosure" + "Verified empirically on PG 14.22" + "Equally verified: a lone READ COMMITTED aput racing the DELETE does NOT trip SSI" + "Do not arm destructive without it."
- Format follows sibling `docs/runbooks/upgrade-drills.md` style: `# Title` + Component / Code owners / Risk class header + structured PRE-ENABLE CHECKLIST + ROLLBACK.

### T4.6 — PR4 test files port — DONE (via cherry-pick)

All 4 test files + 1 helper landed via cherry-pick; byte-equality to v1 `fc908945` verified:

| File | Lines | Tests | Source | Status |
|------|-------|-------|--------|--------|
| `tests/integration/checkpoint_prune_real_saver.py` | 969 | 9 | v1 `fc908945` (BINDING GATE) | ✓ byte-identical |
| `tests/integration/checkpoint_prune_restore_rehearsal.py` | 170 | 1 | v1 `fc908945` | ✓ byte-identical |
| `tests/unit/checkpoint_adapter/test_direct_anti_join.py` | 435 | 11 | v1 `fc908945` | ✓ byte-identical |
| `tests/unit/services/test_maintenance_prune_direct_anti_join.py` | 442 | 24 (8 parametrize + 16 unit) | v1 `fc908945` | ✓ byte-identical |
| `tests/helpers/checkpoint_prune_pg.py` | 241 | n/a (harness) | v1 `fc908945` (includes the 7a7998fe `separate_pools` fixture) | ✓ byte-identical |
| `tests/unit/checkpoint_adapter/__init__.py` | 0 | n/a (pkg marker) | v1 `fc908945` | ✓ byte-identical (empty file) |

**Binding-gate harness recipe verification:**
- File-backed SQLite for non-PG paths: `tmp_path / "cp.db"` (line 953 of `checkpoint_prune_real_saver.py`) — verified.
- Disposable PG for real-saver binding gate: `pg_db` fixture creates per-test disposable DB (lines 124-133) — verified.
- Two-pool production topology for `TestRealSaverSerializableRetry`: `race_stack` fixture + `real_pg_checkpointer_separate_pools` helper — verified (lines 146-163).
- SKIP-LOUDLY contract: explicit `pytest.skip("BINDING GATE SKIPPED — PostgreSQL not available...")` with hard warning (lines 108-120) — verified.

### T4.7 — Gate manifest regeneration — DONE (commit `1569c82a`)

- Per-file collect-only with DSN pinning (37 files = 33 Phase 3 + 4 new PR4):
  - ran `uv run pytest <file> -o addopts= --collect-only -q -p no:cacheprovider --no-header` for each of the 37 manifest paths
- **Aggregate collect-only cross-check: 484 tests collected in 1.11s**
- Per-file sum: **484 tests** (EXACT MATCH)
- Manifest table updated:
  - HEAD: `a1ae0f91` (pre-chore state)
  - Date: 2026-09-04 (UTC)
  - Provenance: v2 PR4-CLOSURE manifest
  - 37 rows / 484 tests total (was 33 / 439 pre-PR4)
  - 4 new PR4 rows: `test_direct_anti_join.py` (11) + `test_maintenance_prune_direct_anti_join.py` (24) + `checkpoint_prune_real_saver.py` (9) + `checkpoint_prune_restore_rehearsal.py` (1)
- Commit message: `chore(gate): regen manifest at a1ae0f91 — Phase 4 PR4 port closure (484 tests)`
- Per-file + aggregate cross-check passed; per-file sum = aggregate sum = 484.

### T4.8 — Unit + service-layer verification — DONE (35/35 GREEN)

```
POSTGRES_URL=…ensemble_cpv2_test POSTGRES_DB=ensemble_cpv2_test \
  uv run pytest tests/unit/checkpoint_adapter/test_direct_anti_join.py \
              tests/unit/services/test_maintenance_prune_direct_anti_join.py -v
→ 35 passed in 1.51s
```

Per-file breakdown:
- `test_direct_anti_join.py`: **11/11 PASSED** (incl. 9 PG-backed tests that ran against real PG 14.22)
  - `TestSqliteAdapterBlobs::test_find_all_thread_ns_pairs_returns_every_pair` ✓
  - `TestSqliteAdapterBlobs::test_count_and_delete_blob_arms_noop_with_warning` ✓
  - `TestPostgresDirectAntiJoin::test_referenced_blobs_survive_unreferenced_die` ✓
  - `TestPostgresDirectAntiJoin::test_mixed_thread_boundary_each_ns_pruned_independently` ✓
  - `TestPostgresDirectAntiJoin::test_mixed_namespace_boundary_critical_predicate` ✓
  - `TestPostgresDirectAntiJoin::test_missing_channel_in_remaining_checkpoint_yields_orphan` ✓
  - `TestPostgresDirectAntiJoin::test_channel_versions_non_object_yields_zero_refs` ✓
  - `TestPostgresDirectAntiJoin::test_count_arm_matches_destructive_arm_for_referenced_survive` ✓
  - `TestPostgresDirectAntiJoin::test_null_blob_bytes_handled` ✓
  - `TestPostgresDirectAntiJoin::test_find_all_thread_ns_pairs_returns_all_no_having` ✓
  - `TestPostgresDirectAntiJoin::test_count_refs_zero_means_zero` ✓
- `test_maintenance_prune_direct_anti_join.py`: **24/24 PASSED** (8 parametrize + 16 unit)
  - `TestDestructiveGateMatrix::test_flag_matrix[*]` × 8 ✓ (paramrized cases: None-None-False, 1-None-False, 0-None-False, None-1-False, 1-1-False, 0-1-True, 0-0-False, true-1-False)
  - `TestStructuralGate::test_delete_call_is_structurally_gated_by_destructive_flag` ✓
  - `TestStructuralGate::test_source_contains_exactly_one_delete_call_site` ✓
  - `TestStructuralGate::test_runtime_gate_off_delete_never_called` ✓
  - `TestStructuralGate::test_runtime_gate_armed_delete_called` ✓
  - `TestZeroRefsFailSafe::test_detection_error_log_and_prevention_zero_deletes` ✓
  - `TestZeroRefsFailSafe::test_fail_safe_trips_even_when_destructive` ✓
  - `TestZeroRefsFailSafe::test_zero_refs_detection_is_separate_from_deletion_prevention` ✓
  - `TestCandidateIteration::test_iterates_via_find_all_thread_ns_pairs_not_excess` ✓
  - `TestCandidateIteration::test_single_checkpoint_threads_are_candidates` ✓
  - `TestCandidateIteration::test_max_refs_cap_skips_pair` ✓
  - `TestCandidateIteration::test_per_pair_error_isolation` ✓
  - `TestCandidateIteration::test_empty_candidates_short_circuits` ✓
  - `TestSqliteNoOp::test_non_pg_backend_noops_with_warning` ✓
  - `TestMaintenanceWiring::test_execute_runs_operation_e_after_d` ✓
  - `TestMaintenanceWiring::test_blob_prune_failure_does_not_break_maintenance` ✓
  - `TestMaintenanceWiring::test_prune_module_never_raises_on_enumeration_failure` ✓

### T4.9 — BINDING GATE on real PG 14.22 — DONE (10/10 GREEN)

**PG version verification:**
```
POSTGRES_URL=postgresql://ensemble@localhost:5432/ensemble_cpv2_test POSTGRES_DB=ensemble_cpv2_test \
  psql -U ensemble -h localhost -d ensemble_cpv2_test -c "SELECT version();"
→ PostgreSQL 14.22 (Homebrew) on aarch64-apple-darwin23.6.0
```

**Binding gate (`checkpoint_prune_real_saver.py`):**
```
POSTGRES_URL=…ensemble_cpv2_test POSTGRES_DB=ensemble_cpv2_test \
  CHECKPOINT_BLOB_PRUNE_DRY_RUN=0 CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1 \
  uv run pytest tests/integration/checkpoint_prune_real_saver.py -v
→ 9 passed in 3.14s
```

Per-test breakdown (all GREEN on real PG 14.22 with destructive armed):
- `TestRealSaverWritePruneResume::test_real_saver_write_retention_prune_blob_prune_resume` ✓
- `TestRealSaverWritePruneResume::test_real_saver_kill_safe_restart_reconstruction` ✓
- `TestRealSaverDeltaSnapshotChain::test_delta_chain_snapshot_blob_survives_and_orphan_snapshot_dies` ✓
- `TestRealSaverFailSafe::test_real_saver_zero_refs_skip_logs_error_and_deletes_nothing` ✓
- `TestRealSaverConcurrentAput::test_real_saver_concurrent_aput_new_blob_preserved` ✓
- `TestRealSaverRaceWindow::test_preexisting_referenced_blobs_survive_traffic_and_prune_byte_equal` ✓ (7a7998fe fold)
- `TestRealSaverSerializableRetry::test_real_40001_aborts_delete_then_retry_completes` ✓ (7a7998fe fold)
- `TestRealSaverDryRunReport::test_real_saver_dry_run_report_line_shape` ✓
- `TestRealSaverSqliteNoOp::test_real_saver_sqlite_backend_noops_with_warning` ✓ (file-backed SQLite at `tmp_path / "cp.db"`)

**Restore rehearsal (`checkpoint_prune_restore_rehearsal.py`):**
```
POSTGRES_URL=…ensemble_cpv2_test POSTGRES_DB=ensemble_cpv2_test \
  CHECKPOINT_BLOB_PRUNE_DRY_RUN=0 CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1 \
  uv run pytest tests/integration/checkpoint_prune_restore_rehearsal.py -v
→ 1 passed in 1.08s
```
- `TestRestoreRehearsalRoundtrip::test_backup_prune_restore_byte_equality` ✓

**Total PR4 binding gate: 9/9 + 1/1 = 10/10 GREEN** (matches v1 `7a7998fe` baseline).

### T4.10 — Drift-regression checks — DONE (all MATCH)

| Guard | Phase 0/1/2/3 baseline | Post-PR4 | Expected delta | Status |
|-------|------------------------|----------|----------------|--------|
| G1 `settled` count in `docs/job-task-system.md` | 17 | **17** | 0 (doc not touched) | ✓ MATCH |
| G2 `tap_node_return` CALL SITES (`await ... tap_node_return(`) | 4 (Phase 3) | **EXACTLY 4** (graph.py:3628, 3806; instance_messaging.py:1344, 3863 — verified via per-line sed) | 0 | ✓ MATCH |
| G3 migration ordering | `20260819_*` → `20260825_*` (PR2) | **`20260819_000001_report_injections_deferred_marker.sql` → `20260825_000001_create_message_metadata.sql`** | 0 (PR4 doesn't add migrations) | ✓ MATCH |
| G4 atomic count: `checkpoint_prune.py` (file now present) | exit 2 (file absent at v2-base) | `grep -n atomic daemon/services/checkpoint_prune.py` → exit 1 (no false atomic claims); `checkpoint_adapter.py`: 1 retraction at line 693 + `aio.py` citations at line 689 | 0 → +1 retraction + 2 aio.py citations | ✓ MATCH (PR4 DELTA — expected: atomic retraction lands) |
| G5 GATE_SUITES.txt rows / tests | 33 / 439 | **37 / 484** | +4 rows / +45 tests (PR4 surface) | ✓ MATCH (PR4 DELTA — expected) |
| Facade guards (work_id required + job-driven facade + compaction guard) | 7/7 + 8/8 (Phase 2); 7/7 + 7/8 (Phase 3 with pre-existing failure) | **4/4 + 3/3 + 7/8** | 0 + 1 documented pre-existing failure | ✓ MATCH (compaction failure is pre-existing — NOT PR4-introduced) |
| Queue routing (WC-wake kill-switch state) | 1 documented pre-existing failure (Phase 3) | **15/16** (1 documented pre-existing failure) | 0 | ✓ MATCH (pre-existing) |
| astream call sites | 1 (Phase 3) | **1** (instance_messaging.py:3992) | 0 | ✓ MATCH |
| `saver.alist` references in daemon/persistence.py | 0 (post-PR3) | **0** | 0 (PR4 doesn't touch persistence.py) | ✓ MATCH |
| Compaction guard | 7/8 (1 pre-existing failure) | **7/8** (1 pre-existing failure: `TestNonTerminalCheckpointCompacts::test_non_terminal_checkpoint_writes_replacement`) | 0 (file NOT modified by PR4) | ✓ MATCH (pre-existing) |
| **Operation E vs Phase 4b/4c deferred paths** (`_finalize_job_db_sync`, `_terminate_instance_db_sync`) | not present | **ZERO occurrences in daemon/services/checkpoint_prune.py or daemon/services/maintenance.py** | 0 (Operation E is orthogonal) | ✓ MATCH (T4.3 STOP-GATE) |
| **Dual-flag ladder default** | N/A (file absent at v2-base) | `CHECKPOINT_BLOB_PRUNE_DRY_RUN: bool = True` (default dry-run); `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE: bool = False` (default OFF) | Destructive requires BOTH `DRY_RUN=0` AND `DESTRUCTIVE=1` | ✓ MATCH (destructive structurally unreachable by default) |

**PR4 surface test results — total:**
- Unit anti-join (T4.8): 11/11 GREEN
- Service-layer prune (T4.8): 24/24 GREEN
- Binding gate real-saver (T4.9): 9/9 GREEN
- Restore rehearsal (T4.9): 1/1 GREEN
- **PR4 surface subtotal: 45/45 GREEN**

**Phase 1..3 gate suites re-run — STAY GREEN:**
- Phase 1 PR1 (test_checkpoint_perf_logging.py): 19/19 GREEN ✓
- Phase 2 PR2 (8 files): 65/65 GREEN ✓
- Phase 3 PR3 (3 files): 37/37 GREEN ✓
- Phase 1 no-saver-imports: 6/6 GREEN ✓
- Facade guards (work_id required): 4/4 GREEN ✓
- Facade guards (job-driven facade): 3/3 GREEN ✓
- **Phase 1..3 surface subtotal: 134/134 GREEN + 2 documented pre-existing failures (NOT PR4-introduced)**

**Total Phase 4 surface: 45/45 PR4 + 134/134 Phase 1..3 + 2 documented pre-existing = 179/179 GREEN (+ 2 documented pre-existing failures, neither PR4-introduced).**

**Facade guard note:** `tests/services/test_instance_messaging_compaction_guard.py::TestNonTerminalCheckpointCompacts::test_non_terminal_checkpoint_writes_replacement` is a 1-test documented pre-existing failure (asserts `first_call.args[1] == {"messages": replacement}` but actual first call has `[RemoveMessage(...), HumanMessage(...)]` because `build_sentinel_replacement` prepends a `__remove_all__` sentinel). NOT PR4-introduced — `git log f8d59c01~1..1569c82a -- daemon/services/compaction.py tests/services/test_instance_messaging_compaction_guard.py` returns empty for the compaction.py file.

**WC-wake kill-switch note:** `tests/services/test_instance_messaging_queue_routing.py::TestMessageRouteQueueIdForwarding::test_router_forwards_queue_id_to_enqueue_message_job` is a 1-test documented pre-existing failure (matches Phase 3 baseline).

---

## Test suites re-run (whole tree / PR4 + Phase 1..3 + drift)

| Suite | Result | vs Phase 0/1/2/3 baseline |
|-------|--------|---------------------------|
| PR4 unit anti-join (test_direct_anti_join.py) | 11/11 GREEN | (new — Phase 4 surface) |
| PR4 service-layer prune (test_maintenance_prune_direct_anti_join.py) | 24/24 GREEN | (new — Phase 4 surface) |
| PR4 binding real-saver (checkpoint_prune_real_saver.py) | 9/9 GREEN | (new — Phase 4 surface; matches v1 `7a7998fe` baseline) |
| PR4 restore rehearsal (checkpoint_prune_restore_rehearsal.py) | 1/1 GREEN | (new — Phase 4 surface) |
| Phase 1 PR1 (test_checkpoint_perf_logging.py) | 19/19 GREEN | MATCH Phase 3 |
| Phase 1 no-saver-imports (test_no_saver_imports_in_routers.py) | 6/6 GREEN | MATCH Phase 3 |
| Phase 2 PR2 (8 files) | 65/65 GREEN | MATCH Phase 3 |
| Phase 3 PR3 (3 files) | 37/37 GREEN | MATCH Phase 3 |
| Facade guard: work_id required | 4/4 GREEN | MATCH Phase 3 |
| Facade guard: job-driven facade | 3/3 GREEN | MATCH Phase 3 |
| Compaction guard | 7/8 GREEN | MATCH Phase 3 (1 pre-existing failure) |
| Queue routing (WC-wake kill-switch) | 15/16 GREEN | MATCH Phase 3 (1 pre-existing failure) |
| **Total PR4 surface** | **45/45 GREEN** | (new — Phase 4) |
| **Total Phase 1..3 surface** | **134/134 GREEN + 2 documented pre-existing** | (matches Phase 3 baseline) |

## Commit list (3 commits total: 2 cherry-picks + 1 chore regen)

| # | SHA | Subject | -x provenance | Files | Staged-set verification |
|---|-----|---------|---------------|-------|-------------------------|
| C1 | `f8d59c01` | feat(perf): PR4 — C3 reference-aware checkpoint_blobs prune | `(cherry picked from commit f89ccacc7bedd517895357128fde6270ff0f7e23)` | daemon/checkpoint_adapter.py + daemon/constants.py + daemon/services/checkpoint_prune.py + daemon/services/maintenance.py + docs/runbooks/checkpoint-blob-prune-restore.md + tests/helpers/checkpoint_prune_pg.py + tests/integration/checkpoint_prune_real_saver.py + tests/integration/checkpoint_prune_restore_rehearsal.py + tests/unit/checkpoint_adapter/__init__.py + tests/unit/checkpoint_adapter/test_direct_anti_join.py + tests/unit/services/test_maintenance_prune_direct_anti_join.py | ✓ (no protected paths; no QUARANTINE.md; no .agents/approver/active.md; no .agents/shared/planning/defer-gate-fix/; no .agents/shared/planning/job-task-retrospective/) |
| C2 | `a1ae0f91` | fix(perf): PR4 critical — serializable wrap + retraction + race tests | `(cherry picked from commit 7a7998fe52a189af0b462e3ec2dae68e4bfa4100)` | daemon/checkpoint_adapter.py + daemon/constants.py + docs/runbooks/checkpoint-blob-prune-restore.md + tests/helpers/checkpoint_prune_pg.py + tests/integration/checkpoint_prune_real_saver.py | ✓ (no protected paths; the SERIALIZABLE wrap + aio.py retraction + runbook §7 disclosure + TestRealSaverRaceWindow + TestRealSaverSerializableRetry + separate_pools helper all in place) |
| C3 | `1569c82a` | chore(gate): regen manifest at a1ae0f91 — Phase 4 PR4 port closure (484 tests) | (no -x; gate regen only) | tests/integration/gate_suites/GATE_SUITES.txt | ✓ (no protected paths; 37 rows / 484 tests; per-file = aggregate cross-check passed) |

## Conflict resolution summary

| File | Conflict type | Hunk rationale | Resolution |
|------|---------------|----------------|------------|
| `tests/integration/gate_suites/GATE_SUITES.txt` | LOW (1 conflict) | 3-way merge auto-resolved 11 of 12 hunks. Conflict was on GATE_SUITES.txt: v1 f89ccacc wanted to add 4 PR4 rows + change PR3 ordering; v2 had Phase 3 ordering + PR2 entries | Took HEAD's version (Phase 3 ordering + PR2 entries). PR4 entries will be re-added during fresh regeneration in T4.7. Justification: T4.7 mandates fresh regeneration on v2-tip; partial merge would leave a mixed-state file. |

## Deviations from v1 byte target (with justification)

| File | v1 byte target | v2 port | Delta | Justification |
|------|---------------|---------|-------|---------------|
| `daemon/checkpoint_adapter.py` | fc908945 + 7a7998fe cumulative | byte-identical via cherry-pick | 0 | clean 3-way merge; no v2 churn on this file (architect §1.2 byte-identical verification) |
| `daemon/constants.py` | fc908945 + 7a7998fe cumulative | PR4 constants byte-identical; v2 carries Phase 1..3 surface (INJECTION_ELIGIBLE_STATUSES, TERMINAL_INSTANCE_STATUSES, leaf-module invariant) that v1 fc908945 lacks | +~440 lines (non-PR4 surface) | Expected drift; PR4 surface is byte-equal; v2's additional surface is from Phase 1..3 (wc-wake, agent-instance-tools, security reserves) |
| `daemon/services/checkpoint_prune.py` | f89ccacc clean-add | byte-identical via cherry-pick | 0 | clean add; file did not exist on v2-tip pre-T4.4 |
| `daemon/services/maintenance.py` | f89ccacc Operation E + wrapper | byte-identical via cherry-pick | 0 | clean 3-way merge; no v2 churn on this file (architect §1.2 byte-identical verification) |
| `docs/runbooks/checkpoint-blob-prune-restore.md` | f89ccacc + 7a7998fe cumulative | byte-identical via cherry-pick | 0 | clean add (f89ccacc) + 7a7998fe §7 fold (intra-process race disclosure) |
| `tests/helpers/checkpoint_prune_pg.py` | f89ccacc + 7a7998fe cumulative | byte-identical via cherry-pick | 0 | clean add + 7a7998fe `separate_pools` fixture |
| `tests/integration/checkpoint_prune_real_saver.py` | f89ccacc + 7a7998fe cumulative | byte-identical via cherry-pick | 0 | clean add + 7a7998fe TestRealSaverRaceWindow + TestRealSaverSerializableRetry |
| `tests/integration/checkpoint_prune_restore_rehearsal.py` | f89ccacc clean-add | byte-identical via cherry-pick | 0 | clean add |
| `tests/unit/checkpoint_adapter/test_direct_anti_join.py` | f89ccacc clean-add | byte-identical via cherry-pick | 0 | clean add |
| `tests/unit/services/test_maintenance_prune_direct_anti_join.py` | f89ccacc clean-add | byte-identical via cherry-pick | 0 | clean add |
| `tests/unit/checkpoint_adapter/__init__.py` | f89ccacc empty pkg marker | byte-identical via cherry-pick | 0 | clean add (empty file) |
| `tests/integration/gate_suites/GATE_SUITES.txt` | f89ccacc +26 | regenerated fresh on v2 (33 → 37 rows; 439 → 484 tests) | per T4.7 mandate | intentional regen per Phase 4 plan; per-file = aggregate cross-check passed |

## Drift vs Phase 0/1/2/3 baselines (summary)

| Baseline | Phase 4 expected | Phase 4 actual | Delta vs baseline |
|----------|------------------|----------------|-------------------|
| G1 settled count: 17 | 17 | **17** | 0 |
| G2 tap_node_return call sites: 4 | 4 | **EXACTLY 4** | 0 |
| G3 migration ordering: 20260819 → 20260825 | same | **same** | 0 (PR4 doesn't add migrations) |
| G4 atomic count: exit 2 (file absent) → retraction present | retraction present | **0 atomic mentions in checkpoint_prune.py + 1 retraction + 2 aio.py citations in checkpoint_adapter.py** | +1 retraction (PR4 DELTA — expected) |
| G5 GATE_SUITES.txt: 33 / 439 | +4 / +45 | **37 / 484** | +4 rows / +45 tests (PR4 DELTA — expected) |
| Facade guards: 7/7 + 8/8 | same-or-better | **4/4 + 3/3 + 7/8 + 15/16** | matches Phase 3 baseline |
| `saver.alist` references in daemon/persistence.py: 0 | 0 | **0** | 0 (PR4 doesn't touch persistence.py) |
| astream call sites: 1 | 1 | **EXACTLY 1** | 0 |
| `saver.alist` references in daemon/routers/** (Flag A): 0 | 0 | **0** (6/6 test_no_saver_imports_in_routers.py GREEN) | 0 |
| Compaction guard: 7/8 | 7/8 | **7/8** | 0 (1 documented pre-existing failure) |
| Queue routing: 15/16 | 15/16 | **15/16** | 0 (1 documented pre-existing failure) |
| **Operation E vs Phase 4b/4c deferred paths** | 0 matches | **0 matches** | 0 (T4.3 STOP-GATE PASSED) |
| **Dual-flag ladder default** | dry-run + destructive OFF | **dry-run ON + destructive OFF (default safe state)** | expected |

**Expected deltas: 2** (G4 atomic retraction + G5 gate manifest +4 rows / +45 tests). All other drift checks MATCH Phase 0/1/2/3 baselines exactly.

## Risk audit (from plan §"Risks")

| # | Plan risk | Status |
|---|-----------|--------|
| 1 | `f89ccacc` landed WITHOUT `7a7998fe` | Mitigated: pair landed atomically; `-x` provenance in both commits; binding gate (T4.9) `TestRealSaverSerializableRetry` proves the SERIALIZABLE wrap |
| 2 | SERIALIZABLE wrap config drift | Mitigated: `CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES=3`, `0.05 * (2 ** (attempt - 1))` 50ms·2ⁿ backoff, exhaustion returns `(0, 0)` — verbatim per T4.1 diff-analysis |
| 3 | Docstring retraction lost | Mitigated: T4.10 grep guard #6 verifies atomic retraction + aio.py citations in `checkpoint_adapter.py` (line 693 + line 689) |
| 4 | Operation E conflicts with v2's defer-gate idle-gate work | Mitigated: ZERO-CONFLICT confirmed (architect §1.2 byte-identical verification; T4.4 cherry-pick auto-merged without conflict on maintenance.py) |
| 5 | Binding gate PG version drift | Mitigated: PG 14.22 verified at T4.9 (matches Phase 0 T0.2 + T0.3) |
| 7 | Mission stale-fixture regression | Mitigated: T4.10 includes facade guards + queue routing + compaction guard (all match Phase 3 baseline; 2 documented pre-existing failures) |
| 8 | WC-wake kill-switch state broken | Mitigated: T4.10 includes `tests/services/test_instance_messaging_queue_routing.py` (15/16 with 1 documented pre-existing failure — NOT PR4-introduced) |
| 9 | Runbook §2 query accidentally executed against `ensemble_prod` | Mitigated: T4.9 binding gate + restore rehearsal on disposable PG only; runbook §2 queries are READ-ONLY SELECTs (no destructive ops) |
| 10 | Operation E's `try/except Exception` swallows `CancelledError` | Mitigated: T4.4 cherry-pick landed the verbatim `except Exception as e:` wrapper (line 461); NEVER `except BaseException:` (verified via grep across all daemon/services/maintenance.py `except` clauses) |

## Acceptance criteria (per phase4-plan.md §"Acceptance")

| Criterion | Status |
|-----------|--------|
| PR4 pair (`f89ccacc` + `7a7998fe`) lands on v2 atomically | ✓ both cherry-picks landed with `-x` provenance; no partial pair |
| Binding gate 9/9 GREEN on real PG 14.22 | ✓ (T4.9) |
| Restore rehearsal 1/1 GREEN | ✓ (T4.9) |
| Anti-join unit 11/11 GREEN | ✓ (T4.8) |
| Service-layer 24/24 GREEN | ✓ (T4.8) |
| SERIALIZABLE wrap config + docstring retraction + runbook §7 intra-process race disclosure all preserved verbatim | ✓ (T4.1 + T4.4 + T4.5) |
| All drift-regression checks PASS | ✓ (T4.10) |

## Final tree state (`git status --short`, uncommitted)

```
?? .agents/shared/planning/defer-gate-fix/                          ← pre-existing, untouched by this port (NEVER-STAGE per constraints)
?? .agents/shared/planning/langgraph-checkpoint-perf-v2/phase4-diff-analysis.md   ← T4.1 deliverable
?? .agents/shared/planning/langgraph-checkpoint-perf-v2/phase4-runbook-verify.md  ← T4.5 deliverable
?? .agents/shared/planning/langgraph-checkpoint-perf-v2/phase4-results.md         ← this file
?? .agents/tester/RESULTS/2026-09-02-fe-liveness-web/*.png          ← pre-existing tester artifacts, untouched
```

After committing phase4-results.md + phase4-diff-analysis.md + phase4-runbook-verify.md, the worktree will have 6 commits on `feature/langgraph-checkpoint-perf-v2`: the 3 PR4 commits + 2 docs commits from Phase 3 + this Phase 4 docs commit.

## Go/No-Go for Phase 5

**GO.**

Phase 4 acceptance criteria all met:
- ✅ PR4 pair (`f89ccacc` + `7a7998fe`) lands on v2 atomically (C1 + C2 + C3 commits).
- ✅ 9/9 binding gate GREEN on real PG 14.22 (matches v1 `7a7998fe` baseline).
- ✅ 1/1 restore rehearsal GREEN.
- ✅ 11/11 anti-join unit GREEN.
- ✅ 24/24 service-layer prune GREEN.
- ✅ 45/45 total PR4 surface GREEN.
- ✅ SERIALIZABLE wrap config verbatim (`CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES=3`, 50ms·2ⁿ, exhaustion `(0, 0)` skips without raising).
- ✅ Docstring retraction verbatim (cites `aio.py:82, 280-304, 393-399`).
- ✅ Runbook §7 intra-process race disclosure verbatim.
- ✅ Drift-regression checks all MATCH (only expected deltas: +1 atomic retraction + 4 GATE rows / 45 tests).
- ✅ Both cherry-picks carry `-x` provenance lines.
- ✅ Gate manifest regenerated (37/484; per-file = aggregate).
- ✅ Migration ordering preserved (20260819 → 20260825).
- ✅ `tap_node_return` call sites = exactly 4 (no 5th tap accidentally added).
- ✅ Operation E vs Phase 4b/4c deferred paths: ZERO matches (T4.3 STOP-GATE).
- ✅ Dual-flag ladder default: dry-run ON + destructive OFF (destructive requires BOTH flags).
- ✅ Zero edits to protected paths.
- ✅ Zero edits to `.agents/tester/QUARANTINE.md` (tester-owned; same disposition as Phase 1/2/3).
- ✅ Zero `git push` (all commits local on `feature/langgraph-checkpoint-perf-v2`).

Phase 5 (PR5 — acceptance gate + PR4 formal re-review + deferred-item disposition + corrected backfill criteria + `message_metadata` side-table prune (MERGE PRECONDITION per risk R1)) can start.
### Post-review correction (2026-09-04)

T4.1's wording ("Ap ut non-atomicity RETRACTION verified verbatim" and the
surrounding bullet that reads as a singular retraction note) is imprecise
about WHERE the retraction lives in `daemon/checkpoint_adapter.py` — the
report's phrasing is module-level-adjacent, but there is **no** top-of-file
retraction note in that module. A `grep -n -i "retract" daemon/checkpoint_adapter.py`
returns no module-level retraction anchor; the disclosure is split across two
method-level / doc-level locations, and the report's wording needs the same
correction as the rest of the corpus:

1. **Method-level wrap-rationale block — `daemon/checkpoint_adapter.py:686-712`**
   (inside `delete_blobs_anti_join`). The `aio.py` citations live inside this
   block: line `:689` cites `aio.py:82` + `aio.py:280-304` (the non-atomic
   default-pipeline path); line `:693` cites `aio.py:393-399` (the atomic
   non-pipeline fallback). The `HONEST LIMIT…` paragraph (lines 716-727)
   closes with the same empirical claim used elsewhere ("a lone rw-out-edge
   is not a dangerous structure and READ COMMITTED reads never register in
   the SSI graph").
2. **Runbook §7 — `docs/runbooks/checkpoint-blob-prune-restore.md:163-191`**
   (`**Residual intra-process race disclosure (PR4 external review,
   2026-08-26).**`). The same three `aio.py` anchors are stacked at
   `:167-168`; the §7 paragraph is the operator-facing version of the
   method-level rationale and carries the explicit "Do not arm destructive
   without it." closer at line 191.

Audit-trail discipline: this section APPENDS the correction; the original
T4.1 text is not silently rewritten, per the post-review rule
(`annotate corrections, never silently rewrite prior report text`). The
line anchors above (`daemon/checkpoint_adapter.py:689` / `:693` /
`:686-712`, and `docs/runbooks/checkpoint-blob-prune-restore.md:163-191`
with `:167-168` for the citation stack) are the verifiable pointers for
future readers — verify against HEAD, since prior commit-counts would have
shifted the file straight off these line numbers if the wrap rationale or
runbook §7 ever moves.
