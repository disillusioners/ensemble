# Phase 4: PR4 Port — Blob Prune (Cherry-Pick PAIR `f89ccacc` + `7a7998fe`)

> Rev 2.2 — delta-review sweep (2026-09-04): stale-text alignment (5 warnings + 3 suggestions); APPROVE sign-off precondition

## Objective

Land v1's PR4 (checkpoint_blobs prune: Operation E in `daemon/services/maintenance.py::CheckpointCleanupJob.execute` + new `daemon/services/checkpoint_prune.py` orchestration + 4 new abstract methods on `CheckpointerAdapter` + 4 concrete impls on `PostgresCheckpointerAdapter` + SQLite stub impls + 4 constants on `daemon/constants.py` + runbook `docs/runbooks/checkpoint-blob-prune-restore.md`) onto v2. Cherry-pick the mandatory PAIR `f89ccacc` (PR4 feat) + `7a7998fe` (PR4 critical fix: SERIALIZABLE wrap + retraction + race tests, 9/9 GREEN on real PG 14.22 per v1 `7a7998fe`) — landing `f89ccacc` alone would re-introduce the 🔴 data-integrity finding (false atomicity claim + undisclosed µs race). The pair is the load-bearing destructive-enable pre-flight evidence for any future `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1` flip (operator's runbook §1 references the re-review per FR-8).

## Port Method

**Cherry-pick the PAIR as a unit (Option A)** — per technical-analysis.md table row PR4. Both commits modify `daemon/checkpoint_adapter.py` + `daemon/services/checkpoint_prune.py` (clean add) + `daemon/constants.py` + `daemon/services/maintenance.py`. The `7a7998fe` commit additionally modifies the destructive arm with SERIALIZABLE wrap + retraction. Cherry-pick `f89ccacc` first, then `7a7998fe` — both as a pair. If 3-way merge fails on `daemon/services/maintenance.py` (MED conflict; v2's defer-gate idle-gate work widened Operation A/B/D's predicates), fall back to Option B manual re-apply for the Operation E addition only. The 4 constants land verbatim (LOW conflict; v2's churn in this file is minimal).

## Files Touched

| File | Change Type | Source / Resolution Rule |
|------|-------------|--------------------------|
| `daemon/services/checkpoint_prune.py` | CLEAN ADD (PR4 feat) | v1 `f89ccacc` content verbatim — orchestration (anti-join + dual flag ladder + flag-gated structural-unreachability) |
| `docs/runbooks/checkpoint-blob-prune-restore.md` | CLEAN ADD | v1 `f89ccacc` content verbatim — operator-facing destructive-enable runbook (sections §1-§7 per C-19) |
| `daemon/checkpoint_adapter.py` | HOT — cherry-pick pair | Add `_BLOB_ANTI_JOIN_PREDICATE` constant + 4 new abstract methods on `CheckpointerAdapter` (`find_all_thread_ns_pairs`, `count_refs_for_blob_thread`, `count_blobs_anti_join`, `delete_blobs_anti_join`) + 4 concrete impls on `PostgresCheckpointerAdapter` + SQLite stub impls (return `(0, 0)` with WARNING). The `7a7998fe` commit additionally modifies the destructive DELETE arm with SERIALIZABLE wrap + retraction + 40001/40P01 retry. **Architect §1.2 correction (was LOW conflict in TA):** `git diff 58260f35..2f80d45b` returns ZERO lines for `daemon/checkpoint_adapter.py` — byte-identical. Anchors `:85` (abstract-method anchor — `find_excess_checkpoint_groups`), `:378` (PG adapter), `:210` (SQLite) all intact. Expect cherry-pick to succeed cleanly; manual fix-up column retained as fallback. |
| `daemon/services/maintenance.py` | HOT — cherry-pick pair with manual fix-up | Add Operation E (`_prune_unreferenced_blobs`) + the wrapper `try: await self._prune_unreferenced_blobs() except Exception as e: logger.error(...)` after Operation D in `CheckpointCleanupJob.execute()`. **Architect §1.2 correction (was MED conflict in TA):** `git diff 58260f35..2f80d45b` returns ZERO lines for `daemon/services/maintenance.py` — byte-identical; defer-gate fix landed in `job_queue_service.py`, NOT maintenance.py. Operation E anchor `:448→:450` (the position after Operation D) intact. The defer-gate widening predicates in v2's churn did NOT touch Operation D/E. Expect cherry-pick to succeed cleanly; the manual-fix-up column retained as defensive fallback. SERIALIZABLE wrap config (`CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES=3`, 50ms·2ⁿ backoff) from `7a7998fe` preserved verbatim. |
| `daemon/constants.py` | LOW — cherry-pick pair | Add 4 constants at line ~73: `CHECKPOINT_BLOB_PRUNE_DRY_RUN`, `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE`, `CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD`, `CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES` (the last is added by `7a7998fe`). **Architect §1.2 confirmation:** adjacent-inserts conflict class unchanged — all 4 PR4 flag names absent from v2 (grep-verified); `IDEMPOTENCY_KEY_TTL_HOURS` anchor `:75` intact. Resolution per technical-analysis.md §"`daemon/constants.py`": insert at the same logical location (after `IDEMPOTENCY_KEY_TTL_HOURS` per v1's pattern); verify v2 has not added a constant with the same name (it hasn't). |
| `tests/integration/checkpoint_prune_real_saver.py` | CLEAN ADD (port if missing on v2) | v1 binding-gate harness (969 lines; 9/9 GREEN on PG 14.22 per v1 `7a7998fe`) — THE BINDING GATE. If v2 already has this file, verify byte-equality with v1 `fc908945`. If missing, port verbatim |
| `tests/unit/checkpoint_adapter/test_direct_anti_join.py` | CLEAN ADD (port if missing) | v1 anti-join unit tests (11 tests) |
| `tests/unit/services/test_maintenance_prune_direct_anti_join.py` | CLEAN ADD (port if missing) | v1 service-layer prune tests (24 tests; fail-safe + structural gate) |
| `tests/integration/checkpoint_prune_restore_rehearsal.py` | CLEAN ADD (port if missing) | v1 PR4 restore roundtrip test (1 test; byte-equality) |
| `tests/integration/gate_suites/GATE_SUITES.txt` | REGENERATE per PR4 closure | (Per the file's own header method; v2-specific counts) |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| T4.1 | Read v1's PR4 pair diff end-to-end: `git show f89ccacc` (PR4 feat) + `git show 7a7998fe` (PR4 critical fix). Document hunk shapes + v2 insertion anchors in `phase4-diff-analysis.md`. Verify the SERIALIZABLE wrap config (`CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES=3`, 50ms·2ⁿ backoff, exhaustion returns `(0,0)` and skips without raising) + the docstring retraction (`aio.py:82, 280-304, 393-399` cited verbatim) + the runbook §7 intra-process race disclosure. | Phase 1..3 GREEN (can land in parallel to Phase 3 if desired — independent structure-wise; sequential in this plan for orderly review) | Diff-analysis file exists; SERIALIZABLE wrap config + retraction + runbook §7 all documented |
| T4.2 | Verify `daemon/services/checkpoint_prune.py` does NOT exist on v2-tip; if v2 added a stub, port v1's content over it. Create the file from v1 `f89ccacc` content verbatim. Verify file imports + orchestration (anti-join + dual flag ladder + flag-gated structural-unreachability + ZERO_REFS_FAIL_SAFE). | T4.1 | File created (or overwritten) byte-identical to v1 `fc908945:daemon/services/checkpoint_prune.py` |
| T4.3 | Verify Operation E does NOT interact with Phase 4b/4c deferred paths (`_finalize_job_db_sync` + `_terminate_instance_db_sync` — per technical-analysis.md open question 10): `grep -n "_finalize_job_db_sync\|_terminate_instance_db_sync" daemon/services/checkpoint_prune.py daemon/services/maintenance.py` — if a touch is found, flag for architect. The expected outcome is zero matches (Operation E is in `CheckpointCleanupJob.execute`, a maintenance loop; orthogonal to job/instance finalization). | T4.2 | grep result captured; if matches → STOP, request architect adjudication |
| T4.4 | Cherry-pick `f89ccacc` first + `7a7998fe` second as a 2-commit pair on `daemon/checkpoint_adapter.py` + `daemon/services/maintenance.py` + `daemon/constants.py`. Use `git cherry-pick -x --3way f89ccacc` then `git cherry-pick -x --3way 7a7998fe`. If 3-way merge fails on `daemon/services/maintenance.py` Operation E hunk, fall back to Option B manual re-apply for that hunk only (per technical-analysis.md §"`daemon/services/maintenance.py`" resolution rule: Operation E goes after the LAST current operation). Do NOT land `f89ccacc` alone — the pair is mandatory. **Provenance AC (adversarial-review W6):** `cherry-pick -x` used for both picks; commit messages carry `(cherry picked from commit f89ccacc...)` + `(cherry picked from commit 7a7998fe...)` provenance lines. | T4.1 | Both commits land on v2; `daemon/checkpoint_adapter.py` has `_BLOB_ANTI_JOIN_PREDICATE` + 4 abstract methods + 4 concrete impls + SQLite stubs + SERIALIZABLE wrap + retraction; `daemon/services/maintenance.py` has Operation E + `try/except` wrapper + SERIALIZABLE config; `daemon/constants.py` has 4 constants; v2's prior changes preserved; `-x` provenance present in both commit messages |
| T4.5 | Create `docs/runbooks/checkpoint-blob-prune-restore.md` from v1 `f89ccacc` content verbatim — verify all 7 sections (per C-19): §1 pre-enable checklist, §2 prod `channel_versions` JSONB shape verification query (per FR-11), §3 destructive flip gate, §4 backup-as-recovery of record, §5 idle-gate precondition, §6 backup covers recovery, §7 intra-process race disclosure. Verify the runbook format follows v2's `.agents/shared/conventions.md` runbook template. | T4.4 | Runbook exists; all 7 sections present; format matches v2 conventions; result recorded in `phase4-runbook-verify.md` |
| T4.6 | Port PR4 test files: `tests/integration/checkpoint_prune_real_saver.py` (969 lines, BINDING GATE) + `tests/unit/checkpoint_adapter/test_direct_anti_join.py` (11 tests) + `tests/unit/services/test_maintenance_prune_direct_anti_join.py` (24 tests) + `tests/integration/checkpoint_prune_restore_rehearsal.py` (1 test). For each: if v2-tip has the file, verify byte-equality with v1 `fc908945`; if missing, port verbatim. The binding gate harness (`checkpoint_prune_real_saver.py`) MUST use the file-backed SQLite recipe for non-PG paths + the disposable PG for the real-saver binding gate (per NFR-13). | T4.4 | All 4 test files present; binding gate harness uses file-backed SQLite recipe; result recorded |
| T4.7 | Create + REGENERATE `tests/integration/gate_suites/GATE_SUITES.txt` post-PR4-closure per the file's own header method (per-file `uv run pytest <file> -o addopts= --collect-only -q -p no:cacheprovider --no-header`); cross-check with aggregate collect-only. Record regen provenance (commit SHA, regen date). | T4.6 | File regenerated with v2 PR4-closure counts; per-file + aggregate match |
| T4.8 | Run PR4 port verification: `pytest tests/unit/checkpoint_adapter/test_direct_anti_join.py tests/unit/services/test_maintenance_prune_direct_anti_join.py -v` (PG-skip-loudly on SQLite; WARNING stub). For PG binding gate (T4.9 is the full binding gate run; this task is the unit + service-layer verification). | T4.6 | 11/11 + 24/24 GREEN (PG-skip-loudly on SQLite per the WARNING stub); result recorded in `phase4-results.md` |
| T4.9 | Run PR4 binding gate on real PG (v2 binding-gate instance, per Phase 0 T0.2 disposable PG): `CHECKPOINT_BLOB_PRUNE_DRY_RUN=0 CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1 pytest tests/integration/checkpoint_prune_real_saver.py -v` against disposable PG 14.22. Must be 9/9 GREEN (matching v1 `7a7998fe` baseline). SKIP-LOUDLY only when PG unreachable; skip does NOT count as GREEN. Also run `tests/integration/checkpoint_prune_restore_rehearsal.py` (1/1 GREEN, byte-equality). | T4.6 | Binding gate 9/9 GREEN on real PG 14.22; restore rehearsal 1/1 GREEN; SKIP-LOUDLY contract verified (does not silently pass); result recorded |
| T4.10 | Run drift-regression checks: 6 vocabulary grep guards (specifically grep #6 — verify every "atomic" mention in `daemon/services/checkpoint_prune.py` + `daemon/checkpoint_adapter.py` cites the retraction + `aio.py:82, 280-304, 393-399`) + facade-forwarding guards + mission stale-fixture 7-node family + Phase 1..3 gate suites (must all stay GREEN). Specifically verify: PR4's Operation E does NOT modify the defer-gate / idle-gate / status-write paths. | T4.9 | All checks PASS; "atomic" mentions all cite retraction + `aio.py` line numbers; v2-baseline counts unchanged from Phases 0..3; `phase4-results.md` records deltas (expected: 0 deltas) |

## Coupling

- **Independent of:** Phase 1, Phase 2, Phase 3 (PR4's structure is self-contained — Operation E in `CheckpointCleanupJob.execute` + adapter abstract methods + orchestration service; can technically land in parallel to Phase 3 if desired; sequential in this plan for orderly review)
- **Tight with:** Phase 5 (Phase 5 FR-8 PR4 re-review artifact RE-VERIFIES the four race folds landed at `7a7998fe`; Phase 5 T5.7 dispatches a reviewer instance for the artifact; Phase 5 T5.8 verifies the 9 verification items per AC-8.2)
- **Independent of:** Phase 5's other items (Phase 5's FR-1..4, FR-5/6, FR-7, FR-9, D-1..D-3, FR-14 — all can proceed in parallel with the FR-8 re-review dispatch)

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | **`f89ccacc` landed WITHOUT `7a7998fe`** (re-introduces the 🔴 data-integrity finding: false atomicity claim + undisclosed µs race) | High (data integrity) | T4.4 cherry-picks as a 2-commit pair; if either commit fails 3-way merge, the pair stays unified (NO individual commit lands); the binding gate (T4.9) is the regression boundary — if `f89ccacc` alone lands, the binding gate would FAIL the race fold tests in `7a7998fe` |
| 2 | **SERIALIZABLE wrap config drift** (retry count, backoff, exhaustion semantics) | High (data integrity) | T4.1 verifies the config exactly: `CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES=3`, 50ms·2ⁿ backoff, exhaustion returns `(0, 0)` and skips without raising; T4.9 binding gate verifies via `TestSerializableRetryPath` |
| 3 | **Docstring retraction lost** (port reverts retraction, re-introducing false "atomic" claim) | High (audit trail broken) | T4.10 grep guard #6 verifies every "atomic" mention cites the retraction + `aio.py:82, 280-304, 393-399` |
| 4 | **Operation E conflicts with v2's defer-gate idle-gate work** (TA-claimed MED — **architect §1.2 CORRECTED to ZERO-CONFLICT**; `git diff 58260f35..2f80d45b` is EMPTY for maintenance.py; defer-gate fix landed in `job_queue_service.py`, NOT maintenance.py. Operation E anchor `:448→:450` intact) | Medium | T4.4 manual fix-up is defensive only (cherry-pick expected clean per architect); Operation E goes after the LAST current operation in `CheckpointCleanupJob.execute` (v2 may have added new ops between D and the position v1 expected E) |
| 5 | **Binding gate PG version drift** (v2's disposable PG may not be 14.22) | High (NFR-5/NFR-6 binding) | Phase 0 T0.2 + T0.3 verified PG ≥14.22; Phase 4 T4.9 re-verifies at binding-gate time |
| 6 | **PR4 docs format drift** (v2's runbook conventions differ from v1's) | Low | T4.5 visual diff vs v2's `.agents/shared/conventions.md`; preserve v1's structure if conventions match |
| 7 | **Mission stale-fixture regression** (port adds Operation E to `CheckpointCleanupJob.execute`; the operation does NOT touch the deferred-emit / idle-gate / status-write paths) | Medium (mission-program integrity) | T4.10 includes `tests/job_queue/` (regression_job_queue partition) + the 7-node quarantine list; T4.1 + T4.3 verify Operation E structural compatibility |
| 8 | **WC-wake kill-switch state broken** (port-time `daemon/manager.py` edit could regress the kill-switch flag wiring) | Medium | T4.10 includes `tests/services/test_instance_messaging_queue_routing.py` |
| 9 | **Runbook §2 query accidentally executed against `ensemble_prod`** (the binding gate creates a disposable PG; the migration's PG DDL is via `_ensure_postgres_columns` which fires on every manager init — risk of touching prod on dev envs that point at prod) | High (data integrity) | Phase 0 T0.2 verified disposable PG; Phase 5 T5.9 + T5.10 D-1 verification is read-only SELECT only (audit-log verifiable); NEVER run any write against `ensemble_prod` |
| 10 | **Operation E's `try/except Exception` swallows CancelledError** (C-14 violation) | High (pause propagation broken) | T4.4 verifies `try/except` wrapper is verbatim from v1 (which uses `except Exception as e:`, NEVER `except BaseException:`) |

## Drift-Regression Checks (from technical-analysis.md §"Drift-Regression Verification Protocol")

Run AFTER T4.10 commits:
- `tests/integration/checkpoint_prune_real_saver.py` — PR4 BINDING GATE (9/9 GREEN on real PG 14.22; SKIP-LOUDLY on PG unreachable; skip NOT green)
- `tests/integration/checkpoint_prune_restore_rehearsal.py` — restore roundtrip (1/1 GREEN, byte-equality)
- `tests/unit/checkpoint_adapter/test_direct_anti_join.py` — anti-join unit (11/11 GREEN; PG-skip-loudly on SQLite)
- `tests/unit/services/test_maintenance_prune_direct_anti_join.py` — service-layer prune (24/24 GREEN)
- 6 vocabulary grep guards — specifically grep #6 (atomic claim retraction); grep #4 (verify `tap_node_return` count still exactly 4 from PR2); grep #5 (migration ordering preserved)
- Facade-forwarding guards
- Mission stale-fixture 7-node family
- Phase 1..3 gate suites (must all stay GREEN)
- `tests/services/test_instance_messaging_queue_routing.py` — WC-wake kill-switch state
- `tests/services/test_instance_messaging_compaction_guard.py` — facade-forwarding real-dispatch integration

## Tests Ported vs Regenerated

| Item | Treatment | Rationale |
|------|-----------|-----------|
| `tests/integration/checkpoint_prune_real_saver.py` | **PORT** (969 lines, BINDING GATE) | THE binding gate; 9/9 GREEN on real PG 14.22 per v1 `7a7998fe` baseline; NEVER mock |
| `tests/integration/checkpoint_prune_restore_rehearsal.py` | **PORT** | Restore roundtrip byte-equality |
| `tests/unit/checkpoint_adapter/test_direct_anti_join.py` | **PORT** (11 tests) | Anti-join unit (real PG SQL or PG-skip-loud) |
| `tests/unit/services/test_maintenance_prune_direct_anti_join.py` | **PORT** (24 tests) | Service-layer prune (fail-safe + structural gate) |
| `tests/integration/gate_suites/GATE_SUITES.txt` | **REGENERATE** | Per-PR closure; v2-specific counts |

## Rollback Note

**Precondition:** rollback may only proceed with the **destructive flag verified OFF** (per approver finding) — i.e. `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE` MUST NOT be `1` (or its destructive-equivalent) at the time of rollback. The PR4 dry-run default leaves the destructive arm disabled, so this precondition is satisfied on the v2 baseline state; if the destructive arm has been flipped on in the operator's env, the operator MUST flip it back to `0` (and complete the §7 disclosure signoff per the runbook) before invoking rollback. Failure to verify this precondition risks rolling back code while the destructive flag is still armed on the next maintenance cycle.

`git revert <commit>` per Phase 4 commit (subject to the precondition above). The commit set is: `<f89ccacc-feature>` `<7a7998fe-critical-fix>` `<runbook-clean-add>` `<binding-gate-tests-port>` `<gate_suites-regen>`. Revert order: regen last, `7a7998fe` third (most recent, the critical fix), `f89ccacc` second (the feat), tests-port fourth (if applicable), runbook first. **Critical**: revert `7a7998fe` BEFORE `f89ccacc` to avoid the data-integrity window (the pair MUST stay unified for as long as the destructive arm is enabled). Phase 4's structural change is Operation E + the anti-join methods — reverting removes the destructive prune path (acceptable as the dry-run default is the safe state; no production data loss occurs because the dry-run is the only enabled path on v2 base).

## Acceptance / Effort / Impact / Blast

| Field | Value |
|-------|-------|
| Acceptance | PR4 pair (`f89ccacc` + `7a7998fe`) lands on v2 atomically; binding gate 9/9 GREEN on real PG 14.22; restore rehearsal 1/1 GREEN; anti-join unit 11/11 GREEN; service-layer 24/24 GREEN; SERIALIZABLE wrap config + docstring retraction + runbook §7 intra-process race disclosure all preserved verbatim; all drift-regression checks PASS |
| Effort | **L** (2-4 hours; pair cherry-pick + binding gate run on real PG + 4 test files port) |
| Impact | **H** (destructive-enable pre-flight evidence; future `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1` flip is gated by this) |
| Blast radius | **H** (5 hot files + new orchestration service + new runbook; SERIALIZABLE wrap changes transaction semantics; docstring retraction changes audit trail) |

## Requirements Traceability

- **FR-1** (PR5 binding gate on real PG) — Phase 4 lands the binding gate harness; Phase 5 T5.1 re-runs it
- **FR-8** (PR4 formal re-review artifact) — Phase 4 lands the code that Phase 5's reviewer instance re-verifies
- **NFR-5** (destructive DELETE MUST NOT delete a blob referenced by a remaining checkpoint) — Phase 4 binding gate `TestRealSaverRaceWindow` is the primary regression boundary
- **NFR-6** (destructive DELETE MUST abort-and-retry on SSI conflict) — Phase 4 binding gate `TestSerializableRetryPath` + T4.10 verifies
- **NFR-7** (lone-READ-COMMITTED-racer µs-window MUST be acknowledged in code comments and runbook) — Phase 4 T4.10 grep guard #6 + runbook §7 verification
- **C-19** (destructive flip gated by runbook §1-§7 checklist) — Phase 4 T4.5 verifies all 7 sections
- **D-1** (prod `channel_versions` JSONB shape verification) — Phase 5 T5.9 runs the runbook §2 query; Phase 4 T4.5 ensures the runbook §2 query is verbatim
- **D-2** (seq-index decision) — Phase 5 T5.10 evidence-based decision (Phase 4 does NOT add or change the `seq` column or its index)
- **D-3** (`is_retry` re-tap drift) — Phase 5 T5.13 adds the explicit test; Phase 4's Operation E is orthogonal to the re-tap invariant
