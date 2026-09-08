# Verification Gate — Content-NOT-NULL Sentinel Self-Heal Fix (ef1432ca)

Date: 2026-09-08 | Worktree: `agents-ensemble-wt-content-notnull` | Branch: `feature/fix-report-injection-content-notnull` | Fix: `ef1432ca` (base `50080e81`)
Gate tip after verification commits: `4d3ff8a9` (ef1432ca..4d3ff8a9 = 4 test/packs-only commits; zero source modifications)
Incident closed against: sweep `no_row_backstop` on b7ead8a4's three deferred pairs (aae1539c/8629bc77/50b7c9a9, RESUME_ROUTER) failed EVERY pass with NotNullViolation (content=None vs prod legacy NOT NULL) after a phantom-conflict misretry — incident logs 2026-09-08 15:27:09 +07.

## VERDICT: ✅ CLOSED — self-heal works against the REAL legacy-NOT-NULL shape, proven on real PostgreSQL (LEG M1L) + SQLite legacy simulation (schema-pin), with deterministic-violation classification proven single-INSERT no-retry. No contract amendments this gate; two report-only findings for the leader (§6).

## 1. Fix under test (ef1432ca — 8 files, +1446/−15: repository.py +218, 2 new test files, 5 grown test files)
- Sentinel INSERT: marker carries `content=_DEFERRED_MARKER_CONTENT_SENTINEL` (`""`, repository.py:786) instead of None.
- Deterministic-violation classification (repository.py:555+): `_is_obligation_triple_unique_violation` discriminator (PG constraint-name `uq_report_injections_oblig_triple` OR SQLite all-three-columns) → unique-violation races converge via insert-on-missing; ANY other IntegrityError (NotNull/ FK/ check/ non-triple unique) → **immediate re-raise, no phantom-conflict retry**, truthful `deterministic IntegrityError` log.
- Schema pin test documents the drift: model/create_all `content` NULLABLE (models.py:300-302) vs prod legacy NOT NULL.

## 2. GATE-PREMISE CORRECTION (load-bearing discovery)
The migration runner is a **NO-OP on PostgreSQL** (runner.py:482-484 gates on "sqlite" in URL); PG schema = create_all + `_ensure_postgres_columns` (additive idempotent). NOTHING in the chain sets `content NOT NULL` — the only report_injections migration (20260819_000001) never touches content. Legacy NOT NULL exists **natively in prod's own history only**. Consequence: a migration-built disposable PG is nullable (LEG M0 live-confirmed: `MigrationRunner.run_pending_migrations() → []`, `is_nullable='YES'`), and the REAL legacy-NOT-NULL proof on PG requires `ALTER TABLE report_injections ALTER COLUMN content SET NOT NULL` simulation — implemented as authorized LEG M1L. This corrects the task premise ("build via FULL MIGRATION CHAIN") — the chain cannot carry what it never had.

## 3. Pre-fix proof (strongest form — incident mode reproduced)
Parent `50080e81` /tmp worktree (own venv, daemon.__file__ real-path verified, cleaned):
- Schema-pin file: **4/7 FAIL**, incl. `test_legacy_not_null_schema_accepts_marker` → `sqlalchemy.exc.IntegrityError: NOT NULL constraint failed: report_injections.content [parameters: ..., None, None, ...]` + the exact incident log pair (`phantom conflict ... inserting DEFERRED marker` / `repeated IntegrityError ... persistent conflict; re-raising`) that looped every sweep on b7ead8a4.
- Classification file: **9/11 FAIL** (ImportError on fix-era `_is_obligation_triple_unique_violation` — structural corroboration).
- At HEAD: 7/7 + 11/11 (see §4). Pack: `schema_pin_prefix_worktree_test.sh` (commit `0f84bd9b`).

## 4. Pack results (worktree, own venv, daemon.__file__ gate per run; relaxed HEAD gate held on every run)

| # | Pack | Result | Counts | Runtime |
|---|------|--------|--------|---------|
| 1 | ensure_deferred_schema_pin_unit_test (NEW) | ✅ PASS | 7/7 (incl. legacy-NOT-NULL marker + migration-runner legs; was 4/7 FAIL at parent) | 0.45s |
| 2 | violation_classification_unit_test (NEW) | ✅ PASS | 11/11 (4× immediate-reraise-no-retry, 5 discriminator dialect/shape tests; was 9/11 FAIL at parent) | 0.36s |
| 3 | ensure_deferred_unit_test | ✅ PASS | 11/11 (FAILED parametrize ADDED — closes last gate's 1-line gap) | 0.58s |
| 4 | self_heal_zero_row_unit_test | ✅ PASS | 4/4 (one-pass heal + anti-flap + mutation guard) | 0.50s |
| 5 | report_injection_regression_unit_test | ✅ PASS | 61/61 (incl. ratified after-terminal pin) | 0.84s |
| 6 | report_delivery_recovery_regression_unit_test | ✅ PASS | 27/27 | 0.81s |
| 7 | dependency_bus_fire_for_terminated (addendum) | ✅ PASS | 6/6 | 0.14s |
| 8 | child_parent_lifecycle_regression_test | ✅ PASS | 220P/19S — bit-exact parity | 10.04s |
| 9 | completion_regression_test | ✅ PASS | 96P/37S/1-des — bit-exact parity | 1.76s |
| 10 | child_reports_unit_test | ✅ PASS | 48/48 — parity | 1.28s |
| 11 | concurrency_atomic_unit_test (ensure.md Critical) | ✅ PASS | 98P/74S — baseline-exact | 6.71s |
| 12 | ensure_deferred_pg_migration_smoke_integration_test (NEW, LOAD-BEARING) | ✅ PASS | 5/5 legs (below) | 2s |

**Head totals: 589 passed / 130 skipped / 1 deselected / 0 failed.**

## 5. PG live-path (load-bearing — disposable `ensemble_test_sentinel_ef1432ca`, prod NEVER touched, DB dropped post-run, guards active)
- **M0**: migration chain LIVE-confirmed NO-OP on PG (`run_pending_migrations() → []`); information_schema verbatim `content / is_nullable='YES' / text` — drift premise holds (sentinel is the bridge).
- **M1**: zero-row incident shape → sweep heals ONE pass (`recovered=1, errors=0`; sentinel row `state=PENDING, content=''`; seam fired exactly once `source='sweep_no_row_backstop'`; pass-2 anti-flap clean; 0 flap-log hits).
- **M1L (new, authorized)**: `UPDATE ... WHERE content IS NULL` (0 rows) → `ALTER COLUMN content SET NOT NULL` (`is_nullable='NO'` verified) → FRESH zero-row pair under the REAL constraint → **sweep heals ONE pass against content NOT NULL** (`recovered=1`; sentinel `''` satisfies the constraint); constraint restored in finally; anti-flap clean.
- **M2a**: real 2-session barrier race → both racers same `injection_id`, exactly 1 row, loser's in-place reason update, winner DEFERRED.
- **M2b**: NotNullViolation re-raised IMMEDIATELY; **insert_calls=1** (single-INSERT mechanism proven — classifier re-raises before the retry seam); 0 rows landed; `deterministic IntegrityError` log ×1, `phantom conflict`/`insert-on-missing` logs ×0; `orig` = `psycopg.errors.NotNullViolation` verbatim.

## 6. Report-only findings (leader attention — no fixes applied)
1. 🟠 **Doc-truth inversion**: repository.py:166-168 (+ ef1432ca commit message) claim backfill happens "BEFORE transitioning DEFERRED→PENDING"; actual order = transition FIRST, reconcile/backfill after (manager.py:9215-9244; blind overwrite makes it functionally moot). Suggest a comment/commit-message correction.
2. 🟢 **Pre-existing falsy-content live-drain drop**: graph.py:4435-4437 (`if not report_content: continue`) — reachable only in a narrow transition-commit→reconcile-commit window; `None` was equally falsy pre-fix (sentinel neither causes nor widens). Backlog candidate.
3. ℹ️ **Sentinel round-trip VERIFIED-OK** (scratch, real APIs): `''` DEFERRED → real `_reconcile_deferred_report` → blind unconditional content overwrite (manager.py:8039-8046, no falsy guard) → `claim_for_injection` delivers real payload; fetch-failure → `'[No response content]'` placeholder. Uncovered by tests (closest test re-implements backfill inline with content=None) — a real-path pin would be a good follow-up.

## 7. ensure.md (Core, blast-radius scoped): 4/4 Critical PASS (changed packs PASS · concurrency baseline-exact · dev.sh flag FOUND worktree:102 · async-await items out of scope). Release Gate not warranted. No method contradictions.

## 8. Test-infra incidents handled this gate (all flagged + committed)
- **uv-venv preflight false-positive** (commit `37479796`): realpath-equality on `.venv/bin/python` can never pass under uv-managed venvs (symlink to shared interpreter); authoritative gate = `daemon.__file__` real-path (editable-install target). Both new packs fixed; LESSONS updated.
- **LEG M1L authored** (commit `4d3ff8a9`, +230/−7): the legacy-NOT-NULL sweep leg the gate premise required.

## 9. Commits made by this gate (test/packs-only, on the worktree branch): `0f84bd9b` (prefix pack) · `0b9201ec` (3 pack scripts) · `37479796` (preflight fix) · `4d3ff8a9` (LEG M1L). Zero source modifications; zero test-file edits; no ratification items.

## 10. Workers (13 dispatches, 10 instances): infra 2b866d91 · prefix 4296e060 · reg-injection 83fef6fc · reg-recovery aae8fac7 · lifecycle 2c170f77 · completion abf7f927 · childreports a932781c · concurrency 4536b9fe · new-suites 50cf1153 (×2: preflight false-positive → authorized fix + re-run) · ensure-unit 0be1bb1f · selfheal 7e9d3cfd · pg-migration ea8870cc · roundtrip 66fee28d

## 11. POST-MERGE OPERATIONAL STEP (leader action — NOT executed by this gate; no prod writes)
After merge to latest + daemon restart, the prod stuck pairs **b7ead8a4 × aae1539c / 8629bc77 / 50b7c9a9 (RESUME_ROUTER)** should self-heal within ONE 5-min sweep pass: sentinel `''` markers satisfy prod's native NOT NULL, transition to PENDING, backfill, deliver. Verify via the sweep logs (expect `recovered=1`-class entries and NO `phantom conflict`/`persistent conflict` pairs for those triples). If they do NOT heal, re-open with those log lines.
