# Checkpoint-Cleanup int4 Overflow Fix — Closing-Gate Verification

**Date:** 2026-09-28 · **Tester instance:** this gate
**Branch:** `fix/checkpoint-cleanup-int4-overflow` @ `0386c344` (commits `0179f11e` + `0386c344`; base `64da8806` = latest tip, "New version 0.16.3")
**Incident:** POST `/api/maintenance/checkpoint-cleanup/execute` → HTTP 500 (`psycopg.errors.NumericValueOutOfRange`, `%(expected_bytes)s::INTEGER` bind cast) when dry-run `would_free_bytes` exceeded int4 — incident value **27,233,813,846** (~25.4 GiB). Incident doc (read-only): `/home/nea/agents-ensemble/docs/2026-09-28-checkpoint-cleanup-500-int4-overflow.md`.

## VERDICT: ✅ PASS — READY TO MERGE

Green criteria (all met):
1. **All affected suites pass** — every scoped pack green; the single red in the sweep is the pre-known attestation node, **proven base-identical at `64da8806`** (§5).
2. **Original symptom DEAD** — incident-value repro through the full execute path PASSED on real disposable PG (§2).
3. **No new failures vs base** — zero branch-caused failures across 372 passed nodes at HEAD; base A/B leg confirms the only red predates the branch.

## Scope Decision

Full-suite run was NOT requested and is NOT warranted: diff = 7 files, single-purpose DB-column widening; `daemon/manager.py` delta confined to the `_ensure_postgres_columns` statement list + docstrings (**execution-lane audit EMPTY** — zero intersection with task_processor / message_job_handler / claim_pending_task / job dispatch, so the e2e lane rule is satisfied by N/A). Ran: the fix's own unit+integration suites, the 4-file maintenance/checkpoint companion family, `tests/unit/repositories/` (repositories layer touched), `tests/migration/` + 2 schema-pin files (2 migration files touched), the registered concurrency pack (ensure.md Core, manager.py touched), FE Playwright focused web check + FE inspection. Skipped: whole-repo sweeps, FE Jest (no FE code changed), Release Gate e2e (not a release/architecture change).

## 1. Suite Results (all timeout-300-wrapped, all via new `scripts/run_tests_scrubbed.sh` where DB-adjacent)

| Pack | Scope | Result | Runtime |
|---|---|---|---|
| `int4_pg_api` | `tests/integration/test_maintenance_checkpoint_cleanup_api.py`, throwaway initdb PG :15532, `--override-ini="addopts=" -m "integration and postgres"` | ✅ **46P / 1S / 0F** (expected 46P/1S exact) | 74.02s |
| `int4_unit_service` | `tests/unit/services/test_maintenance_checkpoint_cleanup_service.py` | ✅ **82P / 0F / 0S** | 40.03s |
| `int4_companions` | lock_and_capture + destructive_override + prune_direct_anti_join + job_wiring_pin | ✅ **57P / 0F / 0S** (15/11/24/7 per file) | 12.77s |
| `int4_repositories` | `tests/unit/repositories/` full dir (9 files) | ✅ **116P / 0F / 0S** | 26.09s |
| `int4_breadth_migration` | `tests/migration/` + `test_ensure_deferred_schema_pin.py` + `test_critical_notes_migrations.py` | ✅ **71P / 1F / 5S** — the 1F is the pre-known attestation red (§5); 5S = PG-env skips (no DB touched) | 10.19s |
| `concurrency_atomic_unit_test` (registered) | ensure.md Core #2/#3 | ✅ **98P / 0F / 74S — baseline-exact, zero drift** | 64.70s |
| `int4_fe_pw` | Playwright `maintenance-checkpoint-cleanup` (14 cases) on boot-script stack (daemon :8099, FE :4299, disposable PG :15432) | ✅ **14 passed / 0 skipped** | 1.4m |
| `int4_base_red` | single attestation node at base `64da8806` (detached worktree `/tmp/ens-int4-base`, removed after) | ✅ **BASE-RED CONFIRMED** (expected FAIL reproduced; §5) | 0.30s |

**HEAD totals:** 372 passed / 1 failed (pre-existing, base-identical) / 80 skipped (1 designed SQLite-dialect fallback + 5 PG-env + 74 concurrency-pack design skips).

## 2. Original-Symptom Verification — **DEAD**

Test (verified by source read BEFORE running — it does what the incident demands):
`tests/integration/test_maintenance_checkpoint_cleanup_api.py::TestExpectedBytesBigIntegerIntegration::test_execute_accepts_incident_oversize_bytes_pg`
- Feeds `_INCIDENT_EXPECTED_BYTES = 27_233_813_846` through the **full execute path** (API harness POST `{SECTION_PREFIX}/execute` with `confirm: True`).
- Asserts acceptance (**HTTP 202 Accepted** — the endpoint's documented contract: async work pickup + poll `/runs/{run_id}`; pre-fix this POST returned **500**), then run terminal state `succeeded`, then the **persisted run row** `expected_bytes == 27233813846`, `kind == "manual_execute"`. Pre-fix failure mode would be `DataError(NumericValueOutOfRange)` at the audit-row INSERT.
- Verdict line: `PASSED …::test_execute_accepts_incident_oversize_bytes_pg` (disposable PG :15532, per-test UUID-named databases, R-10 ensemble_prod-refusal asserted in-suite).
- Sibling boundary nodes also green: `test_execute_accepts_just_over_int4_pg`, `test_execute_accepts_int4_max_pg`.

> Note: the commission brief said "expect HTTP 200" — the endpoint returns **202** by design (out-of-band execute + polling); the incident's own verification recipe (no 500, `manual_execute` row with the full value) is satisfied. Unit-level twins of all three boundary cases also PASSED in the 82-node unit pack.

## 3. W1 Widening-Mechanism Gate — PASS

`…::TestExpectedBytesBigIntegerIntegration::test_ensure_postgres_columns_widens_existing_int4_column` — **PASSED**. The test (source-verified) fabricates the LEGACY int4 `maintenance_runs` table via raw DDL, runs boot-order parity (`create_all` then ensure), invokes the **real production** `InstanceManager._ensure_postgres_columns` on a stub (`__new__` + `_engine` + `is_postgres=True`), asserts `data_type: integer → bigint`, then **re-runs the ensure pass and asserts idempotency**. The production statement is the probe-gated DO block (4-qualifier WHERE: schema/table/column/`data_type='integer'`) added at `manager.py:6954-6966`; the unit pack's AST pin (`test_manager_widening_statement_exists_and_is_probe_gated`) additionally proves exactly ONE widening literal, `DO $$…END $$` shape, and all four qualifiers.

**Boot-shape sanity (real boot):** the FE Playwright stack booted the actual daemon on disposable PG :15432 — canary `state='ready'`, all backend-dependent cases green, zero column-ensure/boot errors (boot log: `[boot] OK — daemon is READY on :8099 (DB ensemble_e2e_maint_main)`).

## 4. Fix Shape (verified from diff, not commit messages)

- `daemon/repositories/maintenance_runs/models.py` — `expected_bytes` `Integer → BigInteger` (+ incident-referencing description).
- `daemon/manager.py` — **only** the new widening tuple in the `_ensure_postgres_columns` statements list (+ docstring/comment updates; lane audit EMPTY).
- `daemon/migrations/versions/20260928_000001_widen_maintenance_runs_expected_bytes.sql` — NEW, `-- MANUAL: TRUE`; runner auto-apply skips it (`runner.py:737-746`) and no-ops on PG anyway (`runner.py:721-726`); PG widening is owned by the ensure-path DO block at boot. Informational UP/DOWN sections.
- `daemon/migrations/versions/20260927_000001_create_maintenance_runs_table.sql` — canonical DDL updated: **`expected_bytes INTEGER → BIGINT` (type token + comment — NOT purely comment-only as first characterized)**. **SAFE disposition (checksum-ledger analysis, quoted code in worker report):** checksums are recorded at apply time (`runner.py:66-70`, ledger `schema_migrations.checksum`) but **never re-verified anywhere**; pending-set math is version-ID membership only (`runner.py:306-314`); SQLite never re-executes applied files; PG never executes `.sql` files. Fresh SQLite DBs get BIGINT (affinity INTEGER — same storage). Stale ledger hash for the file is write-only forensic metadata.
- `scripts/run_tests_scrubbed.sh` — NEW; family-wide `POSTGRES_*` + libpq scrub with echo-verify and exit-78 on leak; execs `.venv/bin/pytest`. **Functionally proven by every pack in this gate** (ambient live `POSTGRES_*` present throughout — host 10.44.0.2/ensemble_prod — zero leak events, zero live contact).
- Test files: unit 82 / integration 47 nodes (counts above).

## 5. Pre-Existing Red — Attribution (confirmed, not fixed)

`tests/migration/test_attestation_migration.py::TestNoBooleanIntegerDefaultInShippedMigrations::test_no_boolean_int_literal_default`
- **At HEAD (0386c344):** FAIL — sole red of the breadth pack; assertion names `20260915_120000_critical_notes_lifecycle.sql: 'BOOLEAN NOT NULL DEFAULT 0'` (predates branch; not in `_LEGACY_BOOLEAN_DEFAULT_0_ALLOWLIST`).
- **At BASE (64da8806, detached worktree, POSTGRES-scrubbed, file-parse-only):** **FAIL — verbatim identical** (same offender, same assertion, `tests/migration/test_attestation_migration.py:514`). **BASE-RED CONFIRMED** → zero causal reach from this branch. Worktree removed cleanly.
- The attestation scan reads the whole shipped corpus — both branch-touched migration files were scanned and produced **no offender lines**.
- No dedicated runner test exercises the MANUAL widen file — **by design** (MANUAL = operator-applied; behavioral coverage is W1's real execution). Noted as observation only.

## 6. FE Assessment — NO FE IMPACT (confirmed)

- No FE source changed (the e2e spec is pre-existing on the branch, last touched by `3ed24952` maintenance-console work — NOT part of this diff; the commission's file list was inaccurate on this point).
- Inspection: `checkpoint-cleanup.component.ts` `formatBytes(n: number)` — type-agnostic JS numbers (binary 1024 units, `Number.isFinite`, `toFixed`); grep for `BigInt|MAX_SAFE_INTEGER|2**31|2**63` across component+service: **zero matches**. Accepted-value range widening cannot alter FE behavior.
- Focused web check: Playwright 14/14 (availability gating ×3, dual-flavor status, dry-run render, execute confirm/cancel, stale re-run, 409 adoption, interrupted convergence, cross-origin 403, kill-switch banner). Ports 8099/4299/15432 **all freed** (known F1 teardown leak did not materialize); live daemons PID-stable (9797→2770191, 7979→2761449, untouched).

## 7. ensure.md Validation (Core scoped; Release Gate not warranted)

| Requirement | Status | Evidence |
|---|---|---|
| Core #1 — no regressions in changed packs | ✅ PASS | all scoped packs green; sole red base-attributed (§5) + QUARANTINE.md row added |
| Core #2 — deadlock/concurrency integrity | ✅ PASS | `concurrency_atomic_unit_test` 98P/0F/74S baseline-exact |
| Core #3 — no sync DB calls on event loop | ✅ PASS | same pack (thread-identity tests) |
| Core #4 — `dev.sh --timeout-graceful-shutdown 10` | ✅ PASS | static: `dev.sh:102` |
| Important #1 — awaiters of converted async fns | N/A | diff touches none of the named functions |
| Important #2 — original deadlock scenario | ✅ PASS | covered by concurrency pack |
| Nice-to-have — no dead code | N/A | no deletions in diff |
| Release Gate | NOT RUN | single-purpose fix, no cross-module architecture change; lane audit EMPTY |

## 8. Anomalies & Corrections (none blocking)

1. **Brief vs reality — 3 corrections:** (a) FE spec is NOT in the diff (pre-existing file); (b) execute returns **202**, not 200 (endpoint contract; symptom-dead equivalence holds); (c) diff contains an 8th-touched file: `20260927_000001` (type-token + comment; SAFE per §4). W1 test lives in the integration file (not the unit file as the brief implied) — verified real regardless.
2. **"36P" companion ambiguity:** no single maintenance/checkpoint file collects 36. 15P = `test_maintenance_run_lock_and_capture.py` (exact). Ran the full candidate family as superset (57 total) — every plausible "36P" composition is covered.
3. **Dirty working tree (pre-existing, untouched):** `M .agents/tidier/notes.md`, `?? _u7s15_boot_isolated.sh`, `?? data_dev_scratch/` (U7/S15-era residue; owner decision pending per critical notes).
4. **Tooling PATH gaps (workers):** `uv` at `/home/nea/.local/bin/uv` and PG binaries at `/usr/lib/postgresql/16/bin` are NOT on inherited PATH — resolved per-worker by PATH prepend. LESSONS recorded.
5. **Report-only log noise:** `maintenance.py:1056/:1147` "not enough values to unpack (expected 3, got 0)" ERROR lines under PASSING negative-path mock tests (companion pack) — mock-shape artifact; if that sub-op ever sees a real backend returning empty rows it degrades to a logged error. Worth a glance in review; not a gate finding.
6. `test_jsonb_migration` 5 skips performed localhost:5432 reachability probes (local cluster; live prod is a different host 10.44.0.2) — env skips, zero live-host contact.

## 9. Safety Audit

Ambient LIVE `POSTGRES_*` (ensemble_prod @ 10.44.0.2) present in every worker shell. Mitigations, all verified: `scripts/run_tests_scrubbed.sh` family-wide scrub + echo-verify (exit 78 on leak — never fired); manual `env -u …` scrubs with `DB-ENV-SURVIVORS=0` checks; disposable UUID-named DBs on throwaway initdb clusters (:15532 BE, :15432 FE-script-owned); in-suite R-10 ensemble_prod refusals; FE spec's own prod-substring refusal. Live listeners 9797/7979/5432 verified present-before==present-after (PIDs stable). Worktree removed. Zero commits, zero source modifications, zero process kills outside own disposable clusters (each port+cmdline-verified).

## 10. Gaps

None. All 11 verification nodes completed with evidence; no re-dispatches; no incomplete nodes. Deferred-by-design items are documented in §5/§6 (MANUAL-file runner test N/A; FE Jest N/A — no FE change).

**Workers:** int4-inspect (71c69531, read-only) · int4-pack-intapi (ab7e19a2) · int4-pack-unit (49da21f9) · int4-pack-comp (49cabd4f) · int4-pack-repos (114bfaf2) · int4-pack-breadth (1891f0fc) · int4-pack-conc (78e88433) · int4-base-red (225783e7) · int4-fe-e2e (e18b9e45) — 9 workers, 8 with `load_skill="test-pack-execution"`, 1 read-only inspector.
