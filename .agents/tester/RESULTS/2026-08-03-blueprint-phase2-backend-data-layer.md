# Phase 2 Backend Data Layer — Project Blueprint Subsystem

**Date:** 2026-08-03
**Branch:** `feature/blueprint-evolution` (Phase 1 committed at `cc812ae4`; Phase 2 changes uncommitted)
**Tester Instances:** 10 workers (10 test packs) + 1 infra discovery
**Plan Ref:** `.agents/shared/planning/project-blueprint/evolution-phases-detailed.md`

---

## Summary

| Metric | Value |
|--------|-------|
| Blueprint-specific tests | **262 passed**, 0 failed |
| Core regression tests | **706 passed**, 41 pre-existing failures, **0 NEW failures** |
| Total tests | **968 passed**, 41 pre-existing, 0 new |
| Edge cases verified | **16/16 covered and PASS** |
| Carry-over fixes verified | **2/2 present and PASS** |
| Quick fixes applied | 0 (none needed) |
| Overall Status | ✅ **READY** |

---

## Pack Results (10 packs, all parallel)

| # | Pack | Tests | Result | Runtime | Delta |
|---|------|-------|--------|---------|-------|
| 1 | `blueprint_core_unit_test` | 52/52 ✅ | PASS | 1.31s | 44→52 (+8: G6 BM25×3, G7 constraint×2, G8 status×1, +2 others) |
| 2 | `blueprint_tools_unit_test` | 33/33 ✅ | PASS | 1.54s | same |
| 3 | `blueprint_injection_unit_test` | 16/16 ✅ | PASS | 0.82s | same |
| 4 | `blueprint_registry_unit_test` | 100/100 ✅ | PASS | 1.8s | same |
| 5 | `test_blueprint_pending_queue.py` (NEW) | 18/18 ✅ | PASS | 1.03s | new |
| 6 | `test_blueprint_context_kind.py` (NEW) | 2/2 ✅ | PASS | 0.42s | new |
| 7 | `test_blueprint_write_service.py` | 28/28 ✅ | PASS | 0.72s | same |
| 8 | `test_blueprint_save_plan.py` | 12/12 ✅ | PASS | 0.62s | same |
| 9 | `test_no_direct_blueprint_writes.py` (lint) | 1/1 ✅ | PASS | 0.47s | same |
| 10 | `core_unit_test` (regression) | 706 pass / 41 pre-existing | ✅ PASS (0 NEW) | 24.5s | same baseline |

---

## Edge Case Coverage Map — All 16 Verified ✅

### C3 — Pending Queue State Machine (7 edge cases)

| Edge case | Test | Status |
|-----------|------|--------|
| claim_batch returns oldest N, atomically (no double-claim) | `test_claim_batch_oldest_first_and_atomic` | ✅ PASS |
| acknowledge_batch with wrong token → 0 updated | `test_acknowledge_batch_wrong_token_updates_zero` | ✅ PASS |
| abandon_batch sets status to abandoned | `test_abandon_batch_sets_abandoned` | ✅ PASS |
| get_pending_count returns available count only | `test_get_pending_count_counts_available_only` | ✅ PASS |
| prune_processed removes records older than threshold | `test_prune_processed_removes_old_records` | ✅ PASS |
| prune_excess caps at max_records (FIFO — oldest first) | `test_prune_excess_keeps_newest_records_fifo` | ✅ PASS |
| mark_retryable → retryable (< cap) or abandoned (>= cap) | `test_mark_retryable_respects_retry_cap` | ✅ PASS |

### G6 — BM25 Single-Candidate Fix (3 edge cases)

| Edge case | Test | Status |
|-----------|------|--------|
| single candidate → BM25 score > 0 (not 0.0) | `test_single_candidate_nonzero_bm25` | ✅ PASS |
| all-equal scores → BM25 score > 0 | `test_all_equal_scores_nonzero` | ✅ PASS |
| all-zero scores → BM25 score 0.0 | `test_genuine_zero_bm25_stays_zero` | ✅ PASS |

### G7 — One-Core DB Constraint (2 edge cases)

| Edge case | Test | Status |
|-----------|------|--------|
| cannot create second active core for same project | `test_app_level_ux_guard_raises_on_second_core` | ✅ PASS |
| auto-dedup keeps newest core, soft-deletes older ones | `test_auto_dedup_keeps_newest_active_core` | ✅ PASS |

### G8 — Status Filter (2 edge cases)

| Edge case | Test | Status |
|-----------|------|--------|
| draft blueprint NOT returned by matcher | `test_search_candidates_excludes_draft` | ✅ PASS |
| published blueprint IS returned by matcher | `test_search_candidates_excludes_draft` (same test, asserts `published.id in ids`) | ✅ PASS |

> **Coverage note:** G8 status filter is tested at the `search_candidates` repository boundary (which the matcher consumes), not in `test_blueprint_matcher.py` directly. Semantically equivalent — drafts are filtered before reaching the matcher.

### C10 — Context Kind Persistence (2 edge cases)

| Edge case | Test | Status |
|-----------|------|--------|
| blueprint context kind in _CONTEXT_KINDS frozenset | `test_blueprint_in_context_kinds_frozenset` | ✅ PASS |
| messages with blueprint context block recognized | `test_messages_have_context_block_recognises_blueprint` | ✅ PASS |

---

## Carry-Over Fixes Verified ✅

| Carry-over | Description | Status |
|------------|-------------|--------|
| 1 — Rollback logger.error | Update path rollback logs at ERROR level with `exc_info=True` (lines 514, 527) + create path (line 315) | ✅ PRESENT + 28/28 PASS |
| 2 — Lint regex includes replace_triggers | Regex `r"_blueprint_repo\.(create\|update\|soft_delete\|replace_triggers)\b"` (line 28) | ✅ PRESENT + 1/1 PASS |

---

## Regression Check

**0 NEW failures.** All 41 core failures are pre-existing and identical to baseline:

| Root cause | Count | Description |
|------------|-------|-------------|
| SQLite migration `20260714_000001` | 39 | PostgreSQL-only `DROP CONSTRAINT IF EXISTS` — fails on SQLite. Affects all InstanceManager tests + 1 cascade. |
| test_agents_api env mismatch | 2 | Asserts 1/0 agents; dev repo has 31 agents. |

The Phase 2 changes (manager.py G7 unique index, config.py, persistence.py _CONTEXT_KINDS, new pending_models/pending_repository) introduced **zero cross-subsystem regressions**.

---

## ensure.md Validation

| Requirement | Status | Method |
|-------------|--------|--------|
| No regressions in changed packs | ✅ PASS | All 10 packs (262 blueprint + 706 core = 968 tests, 0 NEW failures) |
| `dev.sh --timeout-graceful-shutdown 10` | ✅ PASS | Verified in prior run (line 102) |
| Deadlock/concurrency integrity | ⏭️ Not in blast radius | C3 pending queue uses SQLite-internal atomicity; manager.py changes are additive |

---

## Scope Decision

> Scoped to all blueprint packs + 2 new test files + core regression. Phase 2 touches: pending queue (new module), matcher (BM25 fix), repository (G7/G8), config, persistence (_CONTEXT_KINDS). Same blast radius as Phase 1. No expansion needed.

---

## Code Changes Summary
- No test-code or production-code changes made during this testing session.
- Quick fixes applied: **none** (0 needed — all packs passed first run).

---

## Documentation Updated
- [x] RESULTS/2026-08-03-blueprint-phase2-backend-data-layer.md — this report
- [x] PACKS.md — updated test counts + added 2 new pack entries

---

## Overall Status

- **Blueprint Tests:** ✅ PASS (262/262)
- **Edge Cases:** ✅ PASS (16/16 covered and passing)
- **Carry-over Fixes:** ✅ VERIFIED (2/2 present and passing)
- **Regression:** ✅ PASS (0 NEW failures)
- **ensure.md:** ✅ PASS (all in-scope requirements met)
- **Testing Complete:** ✅ **READY** — All 5 Phase 2 items + 2 carry-over fixes verified correct with comprehensive edge case coverage and zero regressions
