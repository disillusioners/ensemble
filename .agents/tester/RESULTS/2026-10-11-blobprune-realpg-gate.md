# REAL-PG Gate — `feature/blob-prune-default-on` @ `0a2e78a0a` (base `1bab756c6`)

**Date:** 2026-10-11 (UTC) · **Gate owner:** Tester (instance 7aa18e76) · **Mode:** REPORT-ONLY (no fixes, no production edits)
**Worktree:** `/home/nea/ensemble-src-wt-blobprune-default` (branch HEAD verified `0a2e78a0a` before every leg)
**Change set:** 39 files, +3106/−684 (10 commits `3ce50e8db..0a2e78a0a`) — destructive-by-default checkpoint-blob prune (dual-arm env gate fully removed), LIMIT-batched chunk deletes (batch=1000, per-chunk SERIALIZABLE, retry on 40001/40P01), `partial_completion` audit visibility, `_is_due` never-due guard, FE dead-field removal (6 surfaces).

## VERDICT: **PASS-WITH-PREEXISTING**

One RED in the real-PG api suite, **node-for-node reproduced at base** (pre-existing, quarantined — see §6).
Every commission-required leg ran LIVE. Zero gate legs skipped on PG-unavailability (skip ≠ pass — enforced).
**No fixes applied; nothing outside `.agents/tester/` modified by the gate.**

## ACCEPTANCE CRITERION — **PROVEN** ✅

*"Destructive prune fires with NO env vars set — the no-env case must destructively DELETE."*

Proven by explicit pinned runs (worker 0a62ddda) under a structurally no-env environment:
ambient audit `env | grep -i CHECKPOINT_BLOB` → **empty**, plus explicit `env -u CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE -u CHECKPOINT_BLOB_PRUNE_DRY_RUN` scrub; all POSTGRES_*/SSL_CERT_* live-prod vars scrubbed (survivor count 0, quoted per leg).

| # | Pin (exact node) | Outcome |
|---|---|---|
| 1 | `test_maintenance_prune_direct_anti_join.py::TestDestructiveDefaultContract::test_kwarg_default_is_destructive_true` (kwarg-default signature: `destructive` default `is True`) | **PASSED** |
| 2 | `…::TestDestructiveDefaultContract::test_no_kwargs_reaches_delete_arm_by_default` — **THE no-env DELETE-awaited acceptance**: no kwarg, no env → `delete_blobs_anti_join` awaited once, count arm NOT awaited | **PASSED** |
| 3 | `…::TestStructuralGate::test_runtime_default_destructive_delete_called` (runtime default) | **PASSED** |
| 4 | `…::TestStructuralGate::test_delete_call_is_structurally_gated_by_destructive_flag` (AST wiring) + companion `test_source_contains_exactly_one_delete_call_site` | **PASSED / PASSED** |
| W | Witnesses: `test_checkpoint_prune_destructive_override.py::TestDestructiveOverrideKwarg::test_ast_dominance_pin_still_green`, `::test_default_reaches_delete_unconditionally` | **PASSED / PASSED** |

Step 1 summary: `7 passed in 0.36s`. No-env-remnants grep-proof suite `tests/unit/test_no_env_flag_remnants.py`: **10/10 PASSED** (both retired env vars absent from all load-bearing live surfaces; retired helper symbols gone; single-delete-site signature unchanged).

## 1. Real-PG gate — the 4 skip-loudly suites (ALL LIVE, disposable PostgreSQL 16.15)

Procedure per leg (prior-art recipe, RESULTS/2026-09-26 §40): `initdb -A trust -U ensemble` (PGDATA under mktemp `/tmp`), `pg_ctl -o "-p <port> -k $TMP -h 127.0.0.1"`, `create database ensemble_test`, `PG_TEST_*` wiring, live-evidence `select version(), current_database(), inet_server_port()`, teardown `pg_ctl stop -m fast` + rm + port-freed proof (pg_isready exit 2). Env scrub per leg: `env -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_USER -u POSTGRES_PORT -u POSTGRES_DB -u SSL_CERT_FILE -u SSL_CERT_DIR` → survivor grep count **0** (ambient live-prod `ensemble_prod@10.44.0.2` never touched; port 8088 never touched). Live-prod creds ARE ambient in every worker shell (7 vars) — scrub verified on all 5 PG legs.

| Suite | Port | Result | Counts | Runtime |
|---|---|---|---|---|
| `tests/integration/checkpoint_prune_limit_batching_real_pg.py` | 15432 | **PASS** | 4P/0F/0S | 4.93s |
| `tests/integration/checkpoint_prune_real_saver.py` | 15433 | **PASS** | 9P/0F/0S | 5.98s |
| `tests/integration/checkpoint_prune_restore_rehearsal.py` | 15434 | **PASS** | 1P/0F/0S | 2.44s |
| `tests/integration/test_maintenance_checkpoint_cleanup_api.py` | 15435 | **PASS-WITH-PREEXISTING** | 45P/1F/1S (quarantined RED, §6; 1S = documented designed skip) | 76.19s |

Invocation shape (identical all legs): `timeout 300 env <scrub> PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=<port> PG_TEST_USER=ensemble PG_TEST_PASSWORD=ensemble_dev PG_TEST_DB=ensemble_test .venv/bin/python -m pytest <suite> --override-ini="addopts=" --override-ini="timeout=240" [-m "integration and postgres"] -p no:cacheprovider --tb=short [-v|-q]`
Venv: worktree `.venv`, Python **3.14.7**, `import daemon` → `<worktree>/daemon/__init__.py` (editable-install trap checked).

**Zero PG-availability skips across all four suites** — every skip-loudly gate probe passed against a live server.

### Multi-chunk exhaustion (commission-critical — never live at HEAD before this gate)
`TestMultiChunkExhaustionDialedBatch::test_pair_exhausts_across_multiple_dialed_chunks` **ran LIVE and PASSED**:
dials `CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE=5`, stages 12 turns → ~20 orphans (keep-2-oldest), asserts ≥3 chunks each ≤5. Debug-log corroboration: first prune `deleted=44 bytes=62865` (⇒ ≥9 data-bearing chunks), **second prune `deleted=0`** → candidate set genuinely exhausted, nothing leaked. `stopped_early=0` in the summary line for the healthy run.

### Real-40001 retry (chunk primitive `(rows, stop_reason)` re-verified live after fix cycle)
`checkpoint_prune_real_saver.py::TestRealSaverSerializableRetry::test_real_40001_aborts_delete_then_retry_completes` **PASSED** (real SQLSTATE-40001 via second serializable txn; retry completes). All 4 suites were mechanically touched by the fix cycle's signature change — **all re-ran live green at `0a2e78a0a`** (the earlier 4/4 evidence is superseded by this gate).

### QUARANTINE #15 observation (no action taken)
The quarantined real_saver caplog pair (`test_real_saver_write_retention_prune_blob_prune_resume` :317, `test_real_saver_dry_run_report_line_shape` :948) **PASSED on-branch** — the 2026-09-26 logger-mismatch appears fixed by intermediate commits. Owner may un-quarantine after the 3×-clean rule; not done here (report-only).

## 2. Scoped regression battery — ALL PASS

| Pack | Command (essence) | Result | Runtime |
|---|---|---|---|
| Unit battery (6 files, fixed list) | `timeout 300 env <scrub> pytest test_checkpoint_prune_limit_batching.py test_checkpoint_prune_destructive_override.py test_maintenance_prune_direct_anti_join.py test_maintenance_run_lock_and_capture.py test_maintenance_checkpoint_cleanup_service.py test_checkpoint_cleanup_job_wiring_pin.py --timeout=240 -q` | **PASS 150/150** (15+11+20+15+82+7) | 56.22s |
| Maintenance suite | `timeout 300 env <scrub> pytest tests/test_maintenance.py --timeout=240 -q` | **PASS 81/81** (guard pins green, count matched expectation exactly) | 1.63s |
| Wiring pin | incl. above (`test_checkpoint_cleanup_job_wiring_pin.py` 7/7, zero PG skips) | **PASS** | — |
| smoke.sh (wired, PACKS :710/:729) | `timeout 300 env <scrub> PATH=/usr/lib/postgresql/16/bin:$PATH bash tests/manual/maintenance_console_smoke.sh` | **PASS 12/12** (origin-403, dry-run, confirm-gate 400, kill-switch 4×503, boot-sweep heal; port 8199 freed, DBs dropped) | 23s |

**Partial-completion signal — 5/5 PASSED (verbose, class `TestPartialCompletionSignal`)**: `test_retry_exhaustion_keeps_partials_and_surfaces_reason` (**the unit-level real-retry-wrap EXHAUSTION test** — commission item 3 minimum ✅), `test_limit_guard_break_surfaces_over_batch_limit`, `test_healthy_run_has_no_partial_block`, `test_duck_adapter_without_side_channel_never_phantoms`, `test_dry_run_flavor_never_carries_partial_signal`.

### partial_completion REAL-PG observation — NOT AVAILABLE (honest gap, non-blocking)
`grep partial_completion` → **zero occurrences** in both `checkpoint_prune_limit_batching_real_pg.py` and `test_maintenance_checkpoint_cleanup_api.py`; no node exercises a partial signal against real PG, and the api fixture tears down its schema (no `maintenance_runs` rows queryable post-run). The commission's "ideally one real-PG observation" is therefore **not satisfied by existing test surface** — the minimum (unit-level real-retry-wrap exhaustion) is satisfied. If wanted, it needs a new integration test (out of scope for a report-only gate).

## 3. FE focused — PASS

Node v22.23.2/npm 10.9.8; `npm ci` 29.7s (897 pkgs, lockfile untouched); **Jest focused spec `checkpoint-cleanup.component.spec.ts` 119/119 PASSED** (8.49s — 21 describe blocks: step gating, wiznav ROUND-3 witnesses, Status Strip render, orientation locator, real MatStepper timing); `ng build` exit 0 via `./node_modules/.bin/ng` ONLY (local devDep; no bare npx/tsc anywhere). 7 pre-existing SCSS-budget warnings, none on the touched surface. **Playwright e2e deliberately skipped** — disproportionate for dead-field removal (commission-concordant).

## 4. ensure.md (worktree branch-carried copy) — Core ALL GREEN

- Changed-pack regressions: the scoped packs above ✅
- `concurrency_atomic_unit_test` (registered pack, `test/packs/concurrency_atomic_unit_test.sh`, dual-layer native): `timeout 300 env <scrub> bash test/packs/concurrency_atomic_unit_test.sh` → **PASS 99P/0F/74S in 76s** — baseline-exact (98P baseline +1P informational, matches 2026-10-08 record)
- dev.sh static check: `grep -n timeout-graceful-shutdown dev.sh` → flag present and wired on the uvicorn launch line (dev.sh:142, value 10) → **PASS**. dev.sh NEVER booted (worktree-refusal guard respected; port 8088 untouched)
- Release Gate items: NOT run — not warranted (no promote; scoped maintenance-subsystem change; commission defined the gate surface)

## 5. Environment & discipline

- Ambient live-prod creds (`POSTGRES_*` → `ensemble_prod@10.44.0.2`, `SSL_CERT_*`) present in every worker shell — scrubbed + survivor-count-verified (0) on **all** test legs; disposable PGs only (ports 15432-15435, 15440); all torn down with port-freed proofs; no leftover `ensemble_blob_prune*` DBs.
- One transient worker error (blobprune-pg-api, JSON-parse) — revived ONCE with re-anchored task, completed cleanly (its leg's evidence above).
- Pre-existing dirty file `.agents/shared/planning/designer-agent/install-audit.jsonl` (mtime 19:27Z, worktree bootstrap) — untouched by the gate, NOT committed.
- Known unrelated py3.13 interpreter-rot: N/A here (worktree venv is 3.14.7). langchain_core pydantic-v1 UserWarning ambient on all pytest runs (pre-existing).

## 6. The one RED — pre-existing, A/B-proven, quarantined

`TestExpectedBytesBigIntegerIntegration::test_ensure_postgres_columns_widens_existing_int4_column` (suite :1016):
`psycopg.errors.UndefinedTable: relation "drift_events" does not exist` ← `CREATE INDEX IF NOT EXISTS ix_drift_events_plugin ON drift_events(plugin)` from `daemon/manager.py` `_ensure_postgres_columns` (:7639 @ HEAD).

**A/B at base `1bab756c6`** (worker f85aca27: temp detached worktree `/tmp/blobprune-base-ab`, own venv, `daemon.__file__` import-proved inside it, disposable PG :15440, single-node run): **byte-identical failure** — same class, relation, DDL. `1F @ HEAD ≡ 1F @ base`. Static corroboration: index DDL introduced @ `27b48af20` (ancestor of base); branch diff does not touch `daemon/manager.py`; no suite-file hunk near :1016. The prior `46P+1S` baseline predates `27b48af20`.
⇒ **NOT a blobprune regression.** Quarantined (QUARANTINE.md Active, 2026-10-11 row); details + suggested fix commission in `LESSONS/2026-10-11-drift-events-base-red-discovered.md`.

## Worker instances

4cf03750 (env/discovery) · 31086087 (pg-limit) · ed98588e (pg-saver) · 4c583617 (pg-rehearsal) · c4ad955f (pg-api, revived-once) · dde077de (units) · b189a67b (maint) · 0a62ddda (accept) · eb93611a (fe) · e18c9a5e (smoke) · f85aca27 (A/B base) · 0fecf716 (ensure tail)

## Bottom line

| Commission item | Status |
|---|---|
| 4 real-PG suites live (skip≠pass), incl. multi-chunk exhaustion driven to exhaustion | ✅ (45+9+4+1 = 59 live PG tests; 1 pre-existing quarantined RED; 1 designed skip) |
| Scoped regression battery (units incl. 5 partial-signal, maintenance 81, override/anti-join/lock-and-capture, no-env-remnants, smoke.sh) | ✅ all green |
| Partial-completion visibility | ✅ unit-level real-retry-wrap exhaustion; ❌ real-PG observation (no existing test surface — gap documented) |
| FE quick/focused | ✅ 119/119 Jest + build clean; Playwright skipped (disproportionate) |
| Acceptance criterion (no-env destructive firing) | ✅ **PROVEN** (4 pins + witnesses + 10/10 remnants-proof, structurally no-env) |
| **Overall** | **PASS-WITH-PREEXISTING** (drift_events RED, base-attributed, quarantined) |
