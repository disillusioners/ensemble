# Cleanup Plan: Remove Old / Legacy Architecture Parts

| Field | Value |
|---|---|
| **Status** | PLAN ONLY — no implementation |
| **Branch** | `feature/cleanup-old-architecture` |
| **Prerequisite** | Decouple architecture migration (Phases A–D) is complete and in production |
| **Scope** | Remove dead kill-switch flags, vestigial DB columns, CM shadow class, Lease stubs; consolidate remaining duplicate paths |
| **Effort** | ~4 weeks (8 phases, some parallelizable) |
| **Critical path** | Phase 1 (gen counter) → Phase 5 (remove CM) → Phase 8 (remove USE_DEPENDENCY_BUS). Phase 2/3/4 can pipeline once Phase 1 lands. |
| **Key principle** | Each phase is independently shippable. No broken intermediate state. The generation counter extraction (Phase 1) is the critical dependency. |
| **Current flags** | `USE_DEPENDENCY_BUS=ON` (default), `USE_LEGACY_WAITING_FOR_CASCADE=OFF`, `USE_LEGACY_JOBQUEUE_DISPATCH=OFF` |

---

## 1. Objective

Reach a **zero-dead-code architecture**: the DependencyBus is the sole completion authority, the WorkerPool is the sole dispatcher, the execution gate is an `asyncio.Lock` (~10 lines), and CorrelationManager, kill-switch flags, and vestigial DB columns are fully removed. Every line of code in `daemon/` reflects the active architecture — no shadow paths, no legacy branches, no stub classes, no dead kill-switch flags.

---

## 2. Scope Assessment

**MEDIUM** — 8 phases, ~20 files touched, ~12 test files modified/removed, ~8 docs updated. No behavioral changes in any phase (the new architecture is already in production); all work is dead-code removal + consolidation.

**What's IN scope:**
- Remove 4 kill-switch flags + all gated conditional branches
- Remove CM class (1843 lines) after extracting generation counter
- **1 behavioral fix**: Thread child error status through `_retrigger_parent_finalize` (Phase 5, Task 5.7) — not dead-code removal, but required before CM deletion
- Remove 4 Lease stub classes + dead `isinstance` branches
- Remove `waiting_for` and `children` DB columns (manual migration)
- Consolidate `enqueue_message_via_jq` duplicate path
- Remove/update ~12 test files
- Update ~8 architecture/configuration docs

**What's OUT of scope:**
- Any behavioral change to DependencyBus, WorkerPool, or the execution pipeline
- Rewriting `_process_message_with_tracking` or langgraph execution core
- HTTP API contract changes
- Removing `instance_hierarchy` table (still live — actively queried by spawn, terminate, child_reports)

---

## 3. Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|--------------|----------|-----------|
| **1** | Extract generation counter | Move `_generation` from CM private dict into DependencyBus; add per-parent locking to bus; bus no longer needs CM for orphan-race detection | None | — | 2–3 days |
| **2** | Remove dead flags + stubs | Remove `USE_LEGACY_JOBQUEUE_DISPATCH` (fully dead); remove 4 Lease stub classes + dead `isinstance` branches | None (independent of Phase 1) | none | 1–2 days |
| **3** | Remove `USE_LEGACY_WAITING_FOR_CASCADE` | Remove kill-switch flag + all ~27 gated code paths; removes all SQL mutation branches for `waiting_for`; remove `DEBUG_COMPLETION_INVARIANT` | None (independent of Phase 1) | **parallel with coordination** (touches `job_feedback_observer.py` and `correlation_manager.py` alongside Phase 1) | 3–4 days |
| **4** | Remove `waiting_for` + `children` columns | Drop vestigial DB columns via migration; remove model fields + all remaining unconditional reads/resets; comprehensive grep-driven sweep across 19 files | Phase 3 complete | tight (within 4) | 2–3 days |
| **5** | Remove CorrelationManager class | Remove CM (1843 lines) after all `get_correlation_manager()` calls replaced with bus equivalents | Phase 1 complete | tight | 3–4 days |
| **6** | Consolidate remaining code | Merge `enqueue_message_via_jq` into single enqueue; review `_has_no_active_message_job`; dead branch cleanup in manager/pipeline | Phases 2, 3 complete | loose | 2 days |
| **7** | Tests + docs cleanup | Remove obsolete tests, update all architecture docs to reflect post-cleanup state; grep verification for zero remaining references | Phases 1–6 complete | — | 2–3 days |
| **8** | Remove `USE_DEPENDENCY_BUS` flag | Bus path is now unconditional sole path. Remove flag + all 45 references + `if use_dep_bus:` conditionals | Phase 5 complete (CM removed, bus is sole path, no CM fallback), Phase 3 complete (legacy flags removed) | loose | 2 days |

**Total:** ~4 weeks. Phases 2+3 can run in parallel with Phase 1 (with coordination for shared files). Phase 4 waits on Phase 3. Phase 5 waits on Phase 1. Phase 8 waits on Phase 5 + Phase 3.

---

## 4. Coupling Assessment

| Phase pair | Coupling type | Justification | Can overlap? |
|---|---|---|---|
| 1 → 5 | tight | Phase 5 removes CM, which Phase 1 is modifying (extracting `_generation`). Must complete Phase 1 first. | **No** |
| 2 → 6 | loose | Phase 6 reviews manager/pipeline for dead branches; Phase 2 removes Lease stubs from those same files. | **Yes** — but coordinate to avoid merge conflicts in `manager.py` |
| 3 → 4 | tight | Phase 4 drops the `waiting_for` column; Phase 3 removes the gated SQL mutations. Column can't be dropped while gated writes exist. | **No** |
| 3 → 6 | loose | Phase 6 cleanup may encounter `waiting_for` references that Phase 3 didn't catch. | **Yes** — Phase 6 runs after, sweeping leftovers |
| 3 → 8 | tight | Phase 8 removes `USE_DEPENDENCY_BUS` flag; Phase 3 removes legacy cascade flag. Both touch config and conditional branches in overlapping files. | **No** — Phase 8 after Phase 3 |
| 5 → 8 | tight | Phase 8 removes `USE_DEPENDENCY_BUS` flag. Until CM is removed (Phase 5), the bus path still has CM as a conceptual fallback. | **No** — Phase 8 after Phase 5 |
| 1 ↔ 2 | none | Different files entirely (CM/bus vs. execution_gate/config) | **Yes — fully parallel** |
| 1 ↔ 3 | **parallel with coordination** | Both edit `job_feedback_observer.py` (Phase 1: L944, 948, 992; Phase 3: L1234, 1772, 1829–1869) and `correlation_manager.py` (Phase 1: passthrough; Phase 3: remove `DEBUG_COMPLETION_INVARIANT`) | **Yes — but coordinate to avoid merge conflicts** |
| 5 → 7 | loose | Phase 7 removes CM-specific tests; Phase 5 must have removed the class first. | **No** |

**Parallelization strategy:** Launch Phase 1, Phase 2, and Phase 3 concurrently (three independent work streams, with Phase 1 ↔ Phase 3 requiring file-level coordination). Phase 4 waits on Phase 3. Phase 5 waits on Phase 1. Phase 6 sweeps after 2+3. Phase 7 finalizes. Phase 8 removes the last flag after CM is gone.

---

## 5. Coupling Map — What Depends on What

```
Phase 1 (gen counter) ──────► Phase 5 (remove CM) ──► Phase 8 (remove USE_DEPENDENCY_BUS)
                              Phase 7 (tests/docs)    ▲
Phase 2 (dead flags/stubs) ──► Phase 6 (consolidate) ─┘
                                 ▲
Phase 3 (legacy cascade) ─────► Phase 4 (drop columns) ─► Phase 8
```

---

## 6. Task Breakdown by Phase

---

### Phase 1: Extract Generation Counter into Bus (2–3 days)

#### Objective

The `_generation` dict currently lives as a private field on CorrelationManager (`cm._generation[_parent_id]`). The DependencyBus directly mutates it (`dependency_bus.py:365-366`) and accesses `cm._get_lock()` (`dependency_bus.py:368`). The `job_feedback_observer.py` reads `cm.get_generation()` for orphan-race detection. This tight coupling means the bus cannot function without CM. Extract the generation counter into the bus itself so CM becomes a pure shadow that can be removed in Phase 5.

#### Coupling

- **Depends on**: None
- **Shared files**: `daemon/services/dependency_bus.py`, `daemon/services/correlation_manager.py`, `daemon/services/job_feedback_observer.py`
- **Coordinate with**: Phase 3 (both touch `job_feedback_observer.py` and `correlation_manager.py`)
- **Why critical**: This is the ONLY remaining functional dependency between the bus and CM. Until it's broken, CM cannot be removed.

#### Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **1.1** | Add `_generation` + per-parent locking to DependencyBus | Add a `generation: dict[str, int]` field + `get_generation(parent_id)` / `increment_generation(parent_id)` methods to the bus. **CRITICAL**: Bus currently only has per-source-task locking (`self._locks` keyed by `source_task_id` at L250, `_get_lock` at L780). The bus has NO per-parent lock. CM has per-parent (`_get_lock(parent_id)` at correlation_manager.py:216). Create a NEW per-parent `asyncio.Lock` dict on bus: `self._parent_locks: dict[str, asyncio.Lock] = {}` + `_get_parent_lock(parent_id) -> asyncio.Lock`. **Lock strategy — SEQUENTIAL, never nested**: (1) Generation mutation uses CPython dict atomicity OUTSIDE any lock — plain `self.generation[parent_id] = self.generation.get(parent_id, 0) + 1` — same as CM's pattern at `correlation_manager.py:265-281`. The bump is a monotonic signal visible to a concurrent `_finalize_job` reading `get_generation()`. (2) Per-parent lock wraps ONLY the DB INSERT (`asyncio.to_thread(self._repo.insert, watcher)`). (3) Release per-parent lock. (4) Per-task lock wraps ONLY the cache update (`self._pending[source_task_id].append(follow_up)`). **Locks are sequential, never held simultaneously.** This avoids any deadlock cycle with `emit_terminal` (which acquires per-task lock only at L467). Do NOT nest parent lock inside task lock or vice versa. | `daemon/services/dependency_bus.py` |
| **1.2** | Replace bus's CM mutation | Replace `cm._generation[_parent_id] = cm._generation.get(_parent_id, 0) + 1` (L365-366) with `self.increment_generation(_parent_id)`. Replace `async with cm._get_lock(_parent_id)` (L368) with `async with self._get_parent_lock(_parent_id)`. | `daemon/services/dependency_bus.py:365-366, 368` |
| **1.3** | Replace observer's CM reads | Replace `cm.get_generation(instance_id)` calls (L948, L992) with `bus.get_generation(instance_id)`. Update the pre/post-commit orphan-race check to use bus API. | `daemon/services/job_feedback_observer.py:944, 948, 992` |
| **1.4** | Add CM passthrough (temporary) | Keep `cm.get_generation()` as a passthrough to `bus.get_generation()` so no callers break during transition. Mark with `# DEPRECATED: Phase 5 will remove this`. | `daemon/services/correlation_manager.py` |
| **1.5** | Test generation counter in bus | Add unit tests verifying generation increments correctly under concurrent access, survives bus restart (rebuilt from DB state via `bus.start()` which calls `_warm_cache()` internally), and orphan-race detection works without CM. **CRITICAL**: Rewrite `TestGenerationCounterBump.test_watch_bumps_cm_generation` (`tests/test_dependency_bus.py:502-538`) to assert `bus.get_generation(parent_id)` instead of `cm.get_generation(parent_id)`. Remove CM creation from the test setup. | `tests/test_dependency_bus.py` (extend + rewrite L502-538) |

#### Acceptance Criteria

- [ ] DependencyBus no longer references `cm._generation` or `cm._get_lock()`.
- [ ] DependencyBus has per-parent locking (`_parent_locks` + `_get_parent_lock`).
- [ ] `job_feedback_observer.py` reads generation from bus, not CM.
- [ ] CM's `get_generation()` is a passthrough (temporary, removed in Phase 5).
- [ ] `TestGenerationCounterBump` rewritten to test `bus.get_generation()` — no CM in test setup.
- [ ] All existing orphan-race detection tests pass.
- [ ] E2E workflow tests (`tests/e2e/test_e2e_workflows.py`) pass unchanged.

#### Risks & Rollback

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Generation counter loses atomicity when moved to bus's lock | Low | High | Generation mutation uses CPython dict atomicity outside any lock (same as CM at L265-281). Per-parent lock wraps only the DB INSERT. Verify with concurrent-access test (1.5). |
| Lock ordering deadlock (parent lock → task lock) | **N/A** | — | **Eliminated by design.** Locks are sequential, never nested: generation mutation outside lock, per-parent lock for DB INSERT, per-task lock for cache update. No path holds both locks simultaneously. `emit_terminal` takes only per-task lock. No cycle exists. |
| Orphan-race detection breaks during transition | Medium | High | Keep CM passthrough (1.4) as fallback; if bus generation has a bug, the passthrough still works. Rollback by reverting Phase 1 commit. |
| Bus restart doesn't restore generation correctly | Low | Medium | Generation is derivable from DB state; `bus.start()` calls `_warm_cache()` internally. Add rebuild verification test. |

**Rollback**: Revert the Phase 1 commit. CM's `_generation` dict and direct mutation path are untouched (only added bus as alternative). Fully reversible.

---

### Phase 2: Remove Dead Flags + Lease Stubs (1–2 days)

#### Objective

Remove `USE_LEGACY_JOBQUEUE_DISPATCH` (fully dead — 0 conditional branches in production code) and the 4 Lease stub classes (`LeaseLostError`, `LeaseContentionReason`, `LeaseContention`, `LeaseHolderKind`) that are backward-compat stubs after Phase C-M6 collapsed ExecutionGate to `asyncio.Lock`.

#### Coupling

- **Depends on**: None
- **Shared files**: `daemon/config.py`, `daemon/services/execution_gate.py`, `daemon/manager.py`, `daemon/services/task_processor.py`, `daemon/services/message_processing_pipeline.py`
- **Can run in parallel with**: Phase 1, Phase 3

#### Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **2.1** | Remove `USE_LEGACY_JOBQUEUE_DISPATCH` config | Remove flag from `JobSystemConfig`, `config.yaml`, and all comments referencing it. Zero conditional branches to remove. | `daemon/config.py:370-384`, `config.yaml:127` |
| **2.2** | Remove comment-only references | Clean up comments in `job_feedback_observer.py:567`, `task/repository.py:195`, `api.py:408`. | 3 files |
| **2.3** | Remove Lease stub classes | Delete `LeaseLostError` (L90), `LeaseContentionReason` (L94), `LeaseContention` (L113), `LeaseHolderKind` (L131) from `execution_gate.py`. | `daemon/services/execution_gate.py:90-131` |
| **2.4** | Remove dead `isinstance` branches | Remove dead isinstance checks: `manager.py:2870, 2880, 2915`; `task_processor.py:238, 359, 370`; `message_processing_pipeline.py:440, 442`. | 3 files |
| **2.5** | Remove Lease imports | Remove `from .execution_gate import {LeaseContention, ...}` from `manager.py:55`, `task_processor.py:11`, `message_processing_pipeline.py:86`. | 3 files |
| **2.6** | Update test imports | Remove Lease class imports from `test_pipeline_unified.py`, `test_pause_terminate_matrix.py`, `test_resume_gate.py`, `test_jq_error_reporting.py`. Remove any tests that specifically test Lease behavior (keep asyncio.Lock contract tests). Update `tests/unit/services/test_execution_gate.py` — verify only asyncio.Lock tests remain; remove any Lease-specific tests. Note: this file tests the asyncio.Lock gate, NOT Lease classes. | 5 test files |

#### Acceptance Criteria

- [ ] `USE_LEGACY_JOBQUEUE_DISPATCH` does not exist in any source file or config.
- [ ] No `LeaseContention`, `LeaseLostError`, `LeaseContentionReason`, `LeaseHolderKind` in `daemon/`.
- [ ] `execution_gate.py` contains only the `asyncio.Lock` implementation.
- [ ] `tests/unit/services/test_execution_gate.py` has no Lease-specific tests — only asyncio.Lock contract tests.
- [ ] All execution-gate threading tests pass (`test_gate_threading_serialization.py` — the 5 asyncio.Lock contract tests).

#### Risks & Rollback

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| A dead `isinstance` branch was actually live | Low | Medium | These branches checked for `LeaseContention` exceptions that can never be raised (Lock doesn't raise them). Grep for any remaining references post-removal. |
| Test removal deletes a still-useful contract test | Low | Low | Only remove tests that specifically import/assert on Lease classes. Keep all `asyncio.Lock` behavior tests. |

**Rollback**: Revert the commit. The flag and stubs are additive — removing them and reverting is a no-op on behavior.

---

### Phase 3: Remove `USE_LEGACY_WAITING_FOR_CASCADE` (3–4 days)

#### Objective

Remove the kill-switch flag and all ~27 gated code paths. This eliminates all SQL mutation branches for `waiting_for` (increment/decrement/cascade), all A8/A9 hard-error assertion branches, the `DEBUG_COMPLETION_INVARIANT` flag, and the flag itself from config.

#### Coupling

- **Depends on**: None (flag is OFF by default, gated branches are dead in production)
- **Shared files**: `child_reports.py`, `instance.py`, `instance_lifecycle.py`, `job_feedback_observer.py`, `error_reporting.py`, `manager.py`, `job_processor.py`, `correlation_manager.py`
- **Must complete before**: Phase 4 (column drop), Phase 8 (flag removal)
- **Coordinate with**: Phase 1 (both touch `job_feedback_observer.py` and `correlation_manager.py`)

#### Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **3.1** | Remove flag from config | Remove `use_legacy_waiting_for_cascade` from `JobSystemConfig`, `config.yaml`. | `daemon/config.py`, `config.yaml` |
| **3.2** | Remove SQL mutation branches (flag ON paths) | Delete gated blocks: `child_reports.py:902-951, 1959-1985` (decrement); `instance.py:640, 740-874` (increment); `instance_lifecycle.py:1008, 1048-1055` (reads meta). Remove the `if USE_LEGACY_WAITING_FOR_CASCADE:` wrapper, keeping only the `else` (CM/bus) path. | `child_reports.py`, `instance.py`, `instance_lifecycle.py` |
| **3.3** | Remove A8/A9 hard-error branches | Delete assertion/error branches that only exist under the flag: `error_reporting.py:244, 285, 356, 363`; `child_reports.py:889, 960, 1093, 1109, 1128, 1946, 1995, 2017, 2029, 2046`; `job_feedback_observer.py:1234, 1772, 1843`; `manager.py:3074, 3090`; `job_processor.py:246, 255`. | 5 files |
| **3.4** | Remove premature-completion parameterization | Remove `[ON, OFF]` parameterization from `tests/postgres/test_premature_completion_regression.py` and `tests/postgres/test_premature_completion_edge_cases.py`. Keep only `OFF` (now the sole path) assertions. | 2 test files |
| **3.5** | Remove `DEBUG_COMPLETION_INVARIANT` | This flag exists only to observe CM-vs-`waiting_for` divergence. Once the legacy path is removed, divergence is impossible. Remove flag + `_check_invariant()` / `_is_debug_invariant_enabled()` / `_should_log_mismatch()` / `_should_log_match()` from CM (or defer to Phase 5 if CM removal is imminent). | `daemon/config.py`, `daemon/services/correlation_manager.py` |

#### Acceptance Criteria

- [ ] `USE_LEGACY_WAITING_FOR_CASCADE` does not exist in any source file or config.
- [ ] `DEBUG_COMPLETION_INVARIANT` flag and all divergence-logging code removed.
- [ ] No `if use_legacy_waiting_for_cascade:` or equivalent conditional branches in `daemon/`.
- [ ] No SQL `UPDATE instances SET waiting_for = ...` statements remain (all mutations removed).
- [ ] `test_kill_switch_legacy_path.py` removed or converted to bus-authority tests (see Phase 7).
- [ ] Premature-completion regression tests run OFF-only (parameterization removed).
- [ ] All E2E and premature-completion regression tests pass.

#### Risks & Rollback

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| A gated branch was mistakenly identified as dead | Medium | High | The flag is `OFF` by default, so removing `if FLAG:` and keeping the `else` is the safe direction. Run full test suite + E2E after removal. |
| Unconditional reads/resets of `waiting_for` break | Low | Medium | Phase 3 only removes gated mutations. Unconditional reads remain — they are removed in Phase 4. |
| Premature-completion regression tests fail under single-path | Low | High | These tests were parameterized `[ON, OFF]`. Under single-path, only the `OFF` assertions are meaningful. Parameterization removed in Task 3.4. |

**Rollback**: Revert the commit. The flag-gated code is restored. Fully reversible since flag is OFF in production — removing dead branches and reverting is a no-op on live behavior.

---

### Phase 4: Remove `waiting_for` + `children` Columns (2–3 days)

#### Objective

Drop the vestigial `waiting_for` and `children` DB columns. After Phase 3, `waiting_for` has no mutations and only unconditional reads/resets remain. The `children` JSON column is already deprecated (model comment says canonical source is `instance_hierarchy`). This phase includes a comprehensive grep-driven sweep across all 19 files that reference `waiting_for`.

> **⚠️ MANUAL_ONLY migration.** This phase requires a DB migration. Test against PostgreSQL (primary dev/test DB), not just SQLite. The `.sql` migration must work on PostgreSQL — NO-OP `.sql` migrations on PostgreSQL are a known past bug. The existing migration `20260621_000002` already NO-OPs on PostgreSQL for column additions.

#### Coupling

- **Depends on**: Phase 3 complete (no gated mutations remaining)
- **Shared files**: `daemon/repositories/instance/models.py`, `daemon/repositories/instance/repository.py`, `daemon/services/instance_lifecycle.py`, `daemon/services/job_feedback_observer.py`, and 15 more files from the grep sweep
- **Must complete before**: Nothing (Phase 5 and 6 don't touch these columns)

#### Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **4.1** | Comprehensive grep-driven sweep + remove unconditional reads/resets | **`grep -rn "waiting_for" daemon/ --include="*.py"` = 324 matches across 19 files.** This is a COMPREHENSIVE sweep — not just the ~7 sites listed below. Files: `api.py`, `config.py`, `manager.py`, `models/instance.py`, `opencode/state.py`, `repositories/instance/models.py`, `repositories/instance/repository.py`, `repositories/task/repository.py`, `routers/instances.py`, `services/child_reports.py`, `services/correlation_manager.py`, `services/error_reporting.py`, `services/instance_lifecycle.py`, `services/job_feedback_observer.py`, `services/job_processor.py`, `services/job_queue_service.py`, `services/message_processing_pipeline.py`, `tools/instance.py`. Every reference must be audited. Many are comments/docstrings (safe to clean up), but **code references that read the column must be removed**. Specific unconditional read sites: `instance_lifecycle.py:650` (`child_ids = list(meta.children)` — cascade-to-children reads `meta.children` from the `children` column; **must replace with `instance_hierarchy` query**); `instance_lifecycle.py:1173, 1246-1247, 1558, 1565`; `job_feedback_observer.py:780, 830, 1993-2010` (the `SELECT waiting_for ... FOR UPDATE` gate). | 19 files |
| **4.2** | Remove model fields | Remove `waiting_for` and `children` from `Instance` model. | `daemon/repositories/instance/models.py:73` |
| **4.3** | Remove `_enrich_instance()` children overwrite | Remove the code at `repository.py:71, 92` that overwrites `children` from junction table on every read. | `daemon/repositories/instance/repository.py` |
| **4.4** | Remove `to_dict()` children field | Remove `children` from serialization. | `daemon/repositories/instance/models.py:106` |
| **4.5** | Fix existing migration — DO NOT create a second migration | **CRITICAL**: The existing migration `daemon/migrations/versions/20260621_000002_drop_legacy_completion_columns.sql` has at line 99: `DROP TABLE IF EXISTS instance_hierarchy;` and recreates it empty at line 125. **This table is LIVE** (spawn INSERT, terminate DELETE, child_reports). Task 4.5: **Remove the `instance_hierarchy` DROP and re-CREATE from the existing migration `20260621_000002`. Do NOT create a second migration. Only the `waiting_for` and `children` column drops should remain.** | `daemon/migrations/versions/20260621_000002_drop_legacy_completion_columns.sql:99, 125` |
| **4.6** | Remove repository methods | Remove `get_all_with_waiting_for()` (L536-547) and `update_waiting_for()` (L653-676) from the instance repository. These methods directly reference the dropped columns. | `daemon/repositories/instance/repository.py:536-547, 653-676` |
| **4.7** | Verify instance_hierarchy still works | `instance_hierarchy` table is STILL LIVE. Verify all 6+ query sites still function after column drop. This is especially critical because Task 4.5 removes the DROP TABLE from the migration — confirm no regression. | `repository.py` (6 sites), `instance_lifecycle.py:650` (now using hierarchy query), `1835`, `child_reports.py:1077, 2013`, `error_reporting.py:318` |
| **4.8** | PostgreSQL column-drop verification test | Write PostgreSQL column-drop verification test in `tests/postgres/test_legacy_column_drop.py`: (1) Run migration `20260621_000002` on PG test DB, (2) Verify columns absent via `SELECT column_name FROM information_schema.columns WHERE table_name='instances'`, (3) Verify rollback restores columns, (4) Verify `instance_hierarchy` table still intact. | `tests/postgres/test_legacy_column_drop.py` (new) |
| **4.9** | Extend `_ensure_postgres_drop_legacy_columns()` | **CRITICAL**: `daemon/manager.py:1832`: `_ensure_postgres_drop_legacy_columns()` is currently a NO-OP (just logs a debug message). Extend it with actual `ALTER TABLE instances DROP COLUMN IF EXISTS waiting_for` and `DROP COLUMN IF EXISTS children` statements. This is the PostgreSQL-safe equivalent of the SQLite `.sql` migration. Without this, PostgreSQL databases will NOT have the columns dropped. | `daemon/manager.py:1832-1853` |

#### Acceptance Criteria

- [ ] `waiting_for` and `children` columns do not exist in the DB schema.
- [ ] No reference to `waiting_for` or `children` column in any source file (324 matches → 0 in code).
- [ ] `get_all_with_waiting_for()` and `update_waiting_for()` removed from repository.
- [ ] `instance_lifecycle.py:650` replaced with `instance_hierarchy` query.
- [ ] `instance_hierarchy` table NOT dropped by migration (DROP/CREATE removed from `20260621_000002`).
- [ ] `instance_hierarchy` table queries all functional (6+ sites).
- [ ] `_ensure_postgres_drop_legacy_columns()` actually drops columns on PostgreSQL (no longer a no-op).
- [ ] PostgreSQL column-drop test passes (Task 4.8).
- [ ] Migration tested on PostgreSQL (not just SQLite).
- [ ] E2E parent-child workflow tests pass.

#### Risks & Rollback

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Migration drops `instance_hierarchy` (live table) | **CRITICAL** | **CRITICAL** | Task 4.5 removes the DROP/CREATE from `20260621_000002`. Verify with Task 4.7. |
| Migration fails on PostgreSQL | Medium | High | Test migration on staging PostgreSQL (Task 4.8). Have rollback migration (`ALTER TABLE ... ADD COLUMN`) ready. Extend `_ensure_postgres_drop_legacy_columns()` (Task 4.9). |
| `instance_hierarchy` queries break due to model change | Low | High | Phase 4 only removes `waiting_for`/`children` — `instance_hierarchy` is a separate table. Run full test suite. |
| Unconditional read at `job_feedback_observer.py:1993-2010` was load-bearing | Medium | Critical | This `SELECT ... FOR UPDATE` was a dual completion gate. After Phase 1, the bus's generation counter replaces it. **Verify Phase 1 is complete and tested before Phase 4.** |
| `instance_lifecycle.py:650` reading `meta.children` crashes after column drop | **HIGH** | **CRITICAL** | Task 4.1 replaces this with `instance_hierarchy` query. Must be done BEFORE column drop migration runs. |

**Rollback**: Restore columns via `ALTER TABLE ... ADD COLUMN`. Code revert restores the model fields and reads. Data is not lost (columns were vestigial). Re-add DROP/CREATE of `instance_hierarchy` to migration only if absolutely needed (should never be needed — it should never have been dropped).

---

### Phase 5: Remove CorrelationManager Class (3–4 days)

#### Objective

Remove the CorrelationManager class entirely (1843 lines). After Phase 1 extracts the generation counter, CM is a pure shadow — all its remaining methods are either passthroughs to the bus or unused. Replace all `get_correlation_manager()` call sites with bus equivalents, then delete the class.

#### Verified Bus Method Reference

The DependencyBus public API (verified against `daemon/services/dependency_bus.py`):

| Method | Type | Line | Signature |
|--------|------|------|-----------|
| `watch` | async | L268 | `watch(self, source_task_id, follow_up)` |
| `emit_terminal` | async | L411 | `emit_terminal(self, task_id, outcome)` |
| `pending_watchers` | async | L539 | `pending_watchers(self, source_task_id)` |
| `count_pending_for_target` | async | L577 | `count_pending_for_target(self, target_instance_id)` |
| `count_pending_for_target_sync` | sync | L600 | `count_pending_for_target_sync(self, target_instance_id)` |
| `cancel_for_target` | async | L630 | `cancel_for_target(self, target_instance_id)` |
| `start` | async | L705 | `start(self)` — calls `_warm_cache()` internally |
| `stop` | async | L755 | `stop(self)` |
| `mark_enqueued` | async | L907 | `mark_enqueued(self, watch_id)` |
| `mark_enqueued_by_source_target` | async | L931 | `mark_enqueued_by_source_target(self, ...)` |
| `get_dependency_bus()` | module-level | L987 | Singleton accessor |
| `set_dependency_bus(bus)` | module-level | L999 | Singleton setter |

**DOES NOT EXIST on bus** (do NOT reference these — they are fictional): `is_complete`, `rearm`, `rebuild`, `register_watcher`, `fire_watcher`, `get_pending_count`, `clear`, `get_generation` (added in Phase 1), `bump_generation`.

#### Coupling

- **Depends on**: Phase 1 complete (generation counter + per-parent locking in bus)
- **Shared files**: `daemon/services/correlation_manager.py` (deleted), all call-site files
- **Can run in parallel with**: Phase 2, Phase 3 (but not Phase 1)

#### Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **5.1** | Map CM methods to verified bus equivalents | Enumerate every public method on CM. Map each to its **verified** bus equivalent (see table above). **CORRECT mapping** (do NOT use fictional method names): | `daemon/services/correlation_manager.py` |

**CM → Bus Method Mapping (VERIFIED):**

| CM Method | Bus Equivalent | Notes |
|---|---|---|
| `register_message_send` | `bus.watch()` | Different signature: bus takes `(source_task_id, FollowUp)` not `(parent_id, child_id, msg_id)` |
| `register_job_send` | `bus.watch()` | Same — different signature |
| `resolve_response` | `bus.emit_terminal()` | |
| `resolve_job` | `bus.emit_terminal()` | |
| `get_pending_count` | `bus.count_pending_for_target_sync()` | **MUST use sync variant** in worker threads |
| `is_complete` | DOES NOT EXIST — use `bus.count_pending_for_target_sync() == 0` | Needs to be inlined or added as bus convenience method |
| `clear_for_instance` | `bus.cancel_for_target()` | |
| `rearm_parent` | DOES NOT EXIST — verify if needed for bus path | Audit if bus path requires re-arm logic |
| `rebuild_from_db` | `bus.start()` | Calls `_warm_cache()` internally |
| `get_generation` | `bus.get_generation()` | Added in Phase 1 |
| `_get_lock` | `bus._get_parent_lock()` | Added in Phase 1 |

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **5.2** | Replace all call sites (explicit list) | Replace `get_correlation_manager()` with `get_dependency_bus()` at all call sites. **8 additional sites with alias imports + private-attribute accesses must be explicitly listed and verified:** `child_reports.py:1395` (`from .correlation_manager import get_correlation_manager as _get_cm_for_a_fix`); `child_reports.py:1396` (`cm = _get_cm_for_a_fix()`); `child_reports.py:1398` (`async with cm._get_lock(instance_id)` — private attribute access); `child_reports.py:2021` (`from .correlation_manager import get_correlation_manager as get_cm_for_cascade`); `child_reports.py:2022` (`cm = get_cm_for_cascade()`); `job_feedback_observer.py:953` (`async with cm._get_lock(instance_id)` — private attribute access); `dependency_bus.py:365-366` (`cm._generation[_parent_id]` — private dict mutation); `dependency_bus.py:368` (`async with cm._get_lock(_parent_id)` — private attribute access). Plus standard call sites: `error_reporting.py:343`, `child_reports.py:1096, 1482`, `instance.py:731`, `job_feedback_observer.py:742, 944, 1688, 1837`, `manager.py:3053`, `instance_lifecycle.py:893, 994`, `job_processor.py:216`. | 10+ files |
| **5.3** | Remove CM wrapper functions | Remove the 5 wrapper functions at `correlation_manager.py:1573, 1614, 1656, 1688, 1722` (module-level convenience wrappers). | `daemon/services/correlation_manager.py` |
| **5.4** | Delete CM class + file | Delete `daemon/services/correlation_manager.py` (1843 lines). Remove from any `__init__.py` exports. | `daemon/services/correlation_manager.py` |
| **5.5** | Remove `get_correlation_manager()` | Remove the singleton accessor function. Any remaining callers should have been replaced in 5.2. | `daemon/services/correlation_manager.py` or wherever the accessor lives |
| **5.6** | Remove CM startup/shutdown | CM lifecycle is in `daemon/api.py`, NOT `daemon/main.py`. Remove: `daemon/api.py:116-119` (import of `init_correlation_manager`, `shutdown_correlation_manager`, ...); `daemon/api.py:365` (`await init_correlation_manager(...)`); `daemon/api.py:505` (`await shutdown_correlation_manager(app)`). Bus startup/shutdown remains. | `daemon/api.py:116-119, 365, 505` |
| **5.7** | Thread child error status through `_retrigger_parent_finalize` (BEHAVIORAL) | **🔴 BLOCKING — This is a behavioral change, not dead-code removal.** Currently `_retrigger_parent_finalize` (`child_reports.py:496`) calls `_finalize_job` with `InstanceStatus.COMPLETED.value` unconditionally. But when a child errors, `error_reporting.py:673` calls `_emit_terminal_via_bus(status="error")`. The `status` parameter is received by `_emit_terminal_via_bus` (L223-226) but only consumed for logging by `bus.emit_terminal` (outcome is logged at L508, not threaded to finalization). After Phase 5 deletes CM, `handle_correlation_complete` (which received `terminal_status` from CM's `_determine_terminal_status` at correlation_manager.py:232-243 — conservative: any error child → parent "error") is gone. `_retrigger_parent_finalize` becomes the sole finalization path but has no mechanism to propagate error status. **Fix**: (1) Thread `status` from `_emit_terminal_via_bus` → `_retrigger_parent_finalize(target_id, terminal_status=status)`. (2) `_retrigger_parent_finalize` passes `terminal_status` to `_finalize_job(job, instance_id, terminal_status, error=...)` instead of hardcoded `InstanceStatus.COMPLETED.value`. (3) Conservative semantics: if ANY child errored, parent finalizes as "error" (mirrors CM's `_determine_terminal_status` "any error → error" rule). Track error state per-parent across multiple child resolutions — the bus's `Outcome` on each `emit_terminal` carries `status` and `error`. **Note**: this is the behavioral equivalent of CM's `ParentCorrelation.had_error` flag + `_determine_terminal_status`. Add a per-parent error tracking mechanism (bus-side dict or DB column on dependency_watchers) that accumulates "any child errored" before finalization. | `daemon/services/child_reports.py:223-226, 359, 407, 496`, `daemon/services/dependency_bus.py` |
| **5.8** | Ensure sync variants for worker threads | Bus has both async and sync variants: `count_pending_for_target` (async, L577) and `count_pending_for_target_sync` (sync, L600). CM's `get_pending_count` is sync. Ensure all call-site replacements use the correct sync/async variant. **Worker thread call sites MUST use `_sync` variants**: `child_reports.py` sync paths, `job_feedback_observer.py` sync paths. Verify no async method is called from a sync context after replacement. | `daemon/services/dependency_bus.py`, all call-site files |

#### Acceptance Criteria

- [ ] `daemon/services/correlation_manager.py` does not exist.
- [ ] No `CorrelationManager` or `get_correlation_manager` reference in any source file.
- [ ] No `cm._generation`, `cm._get_lock`, `cm.get_generation` references.
- [ ] No alias imports of `get_correlation_manager` (e.g., `_get_cm_for_a_fix`, `get_cm_for_cascade`).
- [ ] Child error status threaded through `_retrigger_parent_finalize` (Task 5.7): `_emit_terminal_via_bus(status)` → `_retrigger_parent_finalize(target_id, terminal_status=status)` → `_finalize_job(job, instance_id, terminal_status)`. No hardcoded `COMPLETED` when child errored. Conservative rule: any child error → parent "error" (mirrors CM's `_determine_terminal_status`).
- [ ] All worker-thread call sites use `_sync` bus variants (Task 5.8).
- [ ] `TestBusSoleAuthority` test class added to `tests/test_dependency_bus.py` mirroring the 6 CM correctness scenarios: (1) register→resolve→complete, (2) register→resolve→incomplete, (3) orphan-race re-arm, (4) error-path completion, (5) multi-child completion, (6) terminate-cancel. **These must pass BEFORE CM deletion.**
- [ ] All E2E workflow tests pass (`tests/e2e/test_e2e_workflows.py` — 4 tests).
- [ ] Premature-completion regression tests pass.

#### Risks & Rollback

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| A CM method has no bus equivalent | Medium | High | Audit (5.1) is the precondition. If a method has no equivalent, either add it to the bus or verify the method is unused (shadow-only). `is_complete` and `rearm_parent` have NO bus equivalent — handle explicitly. |
| `_retrigger_parent_finalize` hardcodes COMPLETED, masking child errors | **Certain** | **Critical** | Task 5.7 is a BEHAVIORAL FIX: thread `status` from `_emit_terminal_via_bus` → `_retrigger_parent_finalize` → `_finalize_job`. Without this, deleting CM removes the only path that propagated error status. `TestBusSoleAuthority` scenario (4) verifies the fix. |
| Sync/async mismatch in worker threads | Medium | Critical | Task 5.8 ensures `_sync` variants used in worker threads. Verify with thread-context tests. |
| Alias imports missed (e.g., `_get_cm_for_a_fix`) | Medium | High | Task 5.2 lists all 8 alias/private-access sites explicitly. Grep for `as .*cm` and `from.*correlation_manager` post-replacement. |
| Call site semantics subtly differ (e.g., fire-and-forget vs. await) | Medium | High | Each call site replacement must preserve the original's await behavior. Review each site individually. |

**Rollback**: Restore `correlation_manager.py` and revert call-site changes. Since Phase 1 left CM as a passthrough, restoring is straightforward.

---

### Phase 6: Consolidate Remaining Code (2 days)

#### Objective

Merge the `enqueue_message_via_jq` duplicate path into a single enqueue function. Review `_has_no_active_message_job` for necessity. Clean up any remaining dead branches in `manager.py` and `message_processing_pipeline.py` exposed by Phases 2+3.

#### Coupling

- **Depends on**: Phases 2 + 3 complete (dead branches removed)
- **Shared files**: `daemon/services/instance_messaging.py`, `daemon/manager.py`, `daemon/routers/messages.py`, `daemon/tools/job_queue.py`, `daemon/services/child_reports.py`

#### Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **6.1** | Consolidate `enqueue_message_via_jq` | Merge into single `manager.enqueue_message()`. Update 4 callers: `manager.py:2135, 2165`, `routers/messages.py:119`, `tools/job_queue.py:473`. These are currently unconditional — the function is NOT legacy-gated, just a duplicate path. | `daemon/services/instance_messaging.py:1495`, 4 callers |
| **6.2** | Review `_has_no_active_message_job` | Defense-in-depth guard at `daemon/services/child_reports.py:611`, called from 4 sites (~L1209, L1646, L1772, L2052). **Decision (2026-06-24): KEEP the guard, document why.** The bus covers parent→child correlation (``dependency_watchers``) but does NOT see the MESSAGE-worker lifecycle on ``job_item`` the guard checks — orthogonal concerns. See the method docstring for the full bus-vs-guard separation analysis. | `daemon/services/child_reports.py:611` |
| **6.3** | Sweep manager.py for dead branches | After Phase 2 (Lease removal) and Phase 3 (flag removal), grep `manager.py` for any orphaned code paths that referenced removed constructs. | `daemon/manager.py` |
| **6.4** | Sweep message_processing_pipeline.py | Same sweep for pipeline. Check for dead branches after Lease and flag removal. | `daemon/services/message_processing_pipeline.py` |

#### Acceptance Criteria

- [x] Single `enqueue_message()` function — no `enqueue_message_via_jq` duplicate.
- [x] `_has_no_active_message_job` either removed (with justification) or documented. **KEEP + documented** (2026-06-24) — bus covers ``dependency_watchers`` (parent→child correlation) but not ``job_item`` (MESSAGE-worker lifecycle) the guard checks; orthogonal concerns. See method docstring at `daemon/services/child_reports.py:611` for the full analysis.
- [ ] No dead branches in `manager.py` or `message_processing_pipeline.py`.
- [ ] All message dispatch tests pass.

#### Risks & Rollback

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| `enqueue_message_via_jq` has subtly different behavior (priority, metadata) | Medium | Medium | Audit the function's implementation before merging. Preserve any unique parameters in the unified function. |
| Removing `_has_no_active_message_job` introduces a race | Low | High | If uncertain, keep it. It's a cheap guard. Only remove if clearly redundant after bus analysis. |

**Rollback**: Revert per-task. Each consolidation is independent.

---

### Phase 7: Tests + Docs Cleanup (2–3 days)

#### Objective

Remove obsolete tests, update all architecture and configuration docs to reflect the post-cleanup state. No dead test references, no stale documentation about flags that no longer exist.

#### Coupling

- **Depends on**: Phases 1–6 complete
- **Shared files**: `tests/`, `docs/`

---

## 7. Test Strategy

### Tests to REMOVE (obsolete after cleanup)

| Test File | Reason | Phase |
|-----------|--------|-------|
| `tests/test_kill_switch_legacy_path.py` (A14, 15 tests) | Kill switch flag removed in Phase 3. Legacy path no longer exists. | 7 (after Phase 3) |
| `tests/test_correlation_manager.py` (77KB) | CM class removed in Phase 5. | 7 (after Phase 5) |
| `tests/test_correlation_authority_shadow.py` | Shadow mode no longer exists — bus is sole authority. | 7 (after Phase 5) |
| `tests/test_correlation_shadow.py` | Same as above. | 7 (after Phase 5) |
| `tests/test_cm_resilience.py` | CM-specific resilience tests. Migrate any bus-relevant tests to `test_dependency_bus.py`. | 7 (after Phase 5) |
| `tests/test_unified_dispatcher_shadow.py` | Shadow mode tests for dispatcher. Remove flag-parameterized portions (lines 298, 333, 784, 823 reference removed flag). | 7 (after Phase 2) |

### Tests to MODIFY

| Test File | Change | Phase |
|-----------|--------|-------|
| `tests/postgres/test_premature_completion_regression.py` | Remove `[ON, OFF]` parameterization. Keep only `OFF` (now the sole path) assertions. | **3.4** (moved from Phase 7 to Phase 3) |
| `tests/postgres/test_premature_completion_edge_cases.py` | Same — remove `ON` parameterization. | **3.4** (moved from Phase 7 to Phase 3) |
| `tests/test_pipeline_unified.py` | Remove Lease class imports/assertions. Keep pipeline contract tests. | 2.6 |
| `tests/test_pause_terminate_matrix.py` | Remove Lease class imports/assertions. | 2.6 |
| `tests/test_resume_gate.py` | Remove Lease class imports/assertions. Keep asyncio.Lock contract tests. | 2.6 |
| `tests/test_jq_error_reporting.py` | Remove Lease class imports/assertions. | 2.6 |
| `tests/unit/services/test_execution_gate.py` | Verify only asyncio.Lock tests remain. Remove any Lease-specific tests. Note: this file tests the asyncio.Lock gate, NOT Lease classes. | 2.6 |
| `tests/test_gate_threading_serialization.py` | Keep all 5 asyncio.Lock contract tests. No change. | — |
| `tests/test_dependency_bus.py` | Extend with generation counter tests (Phase 1.5), rewrite `TestGenerationCounterBump` (Phase 1.5), add `TestBusSoleAuthority` (Phase 5), migrate CM resilience tests (Phase 7). | 1.5, 5, 7 |

### Tests to KEEP (unchanged)

| Test File | Why kept |
|-----------|----------|
| `tests/e2e/test_e2e_workflows.py` (4 tests) | E2E validation — validates the full architecture post-cleanup |
| `tests/postgres/test_inflight_flag_flip.py` | Still relevant — verifies crash recovery (update to remove flag references) |
| `tests/test_watch_job_integration.py` | Watch_job correlation via bus — still valid |

### Test Execution Order Per Phase

| Phase | Test verification |
|-------|-------------------|
| 1 | `test_dependency_bus.py` (extended, `TestGenerationCounterBump` rewritten), `tests/e2e/test_e2e_workflows.py`, premature-completion regression |
| 2 | `tests/unit/services/test_execution_gate.py`, `test_gate_threading_serialization.py`, `test_pipeline_unified.py`, `test_pause_terminate_matrix.py`, `test_resume_gate.py` |
| 3 | `tests/e2e/test_e2e_workflows.py`, premature-completion regression (OFF-only), full test suite |
| 4 | E2E parent-child, `instance_hierarchy` query verification, PostgreSQL column-drop test (`tests/postgres/test_legacy_column_drop.py`) |
| 5 | `TestBusSoleAuthority` (must pass BEFORE CM deletion), `tests/e2e/test_e2e_workflows.py`, premature-completion regression, `test_dependency_bus.py` |
| 6 | Message dispatch tests, full test suite |
| 7 | Full test suite — verify zero failures, zero obsolete test references |
| 8 | Full test suite, grep verification (zero `USE_DEPENDENCY_BUS` references) |

---

## 8. Documentation Updates

### Docs to UPDATE

| Doc File | Update Needed | Phase |
|----------|---------------|-------|
| `docs/architecture/completion-authority.md` (212 lines) | **Major rewrite.** Remove "Three Authorities" section — bus is the sole authority. Remove kill-switch references, interaction matrix, `waiting_for` call-site table. This becomes a short "Bus is the completion authority" doc, or is merged into `message-processing-and-correlation.md`. | 7 |
| `docs/configuration/completion-flags.md` (125 lines) | **Delete or gut.** Both flags (`USE_LEGACY_WAITING_FOR_CASCADE`, `DEBUG_COMPLETION_INVARIANT`) are removed. If any completion-related config remains, keep a stub; otherwise delete. | 7 |
| `docs/architecture/message-processing-and-correlation.md` (238 lines) | Update to reflect post-Phase-D + post-cleanup state. Remove CM references, kill-switch references. Bus is the sole correlation mechanism. | 7 |
| `docs/architecture/execution-gate-threading-model.md` (151 lines) | Remove Lease class references. Update to reflect pure `asyncio.Lock` model (no stubs). | 2 |
| `docs/architecture/job-task-pause-resume.md` (1355 lines) | Remove kill-switch references. Update completion sections to bus-only. | 7 |
| `docs/architecture.md` (top-level) | Remove kill-switch references. Update architecture overview. | 7 |
| `docs/architecture/unified-dispatch-architecture.md` | Update if it references removed constructs. | 7 |
| `config.yaml` | Remove `use_legacy_waiting_for_cascade`, `use_legacy_jobqueue_dispatch`, `debug_completion_invariant`, `use_dependency_bus` keys. | 3.1, 2.1, 8 |

### Docs to ARCHIVE or DELETE (historical)

| Doc File | Action | Reason |
|-----------|--------|--------|
| `docs/architecture/concurrency-model.md` | Archive to `docs/archive/` | Pre-deouple architecture description |
| `docs/architecture/message-queue-problems.md` | Archive | Historical problem description (pre-fix) |
| `docs/architecture/message-queue-redesign.md` | Archive | Historical redesign proposal (implemented) |

### Docs UNCHANGED

| Doc File | Why unchanged |
|-----------|---------------|
| `docs/plans/decouple-execution-plan.md` | Historical plan document — keep as-is for traceability |
| `docs/plans/decouple-review.md` | Historical review — keep as-is |
| `docs/plans/unified-dispatcher.md` | Historical proposal — keep as-is |

---

## 9. Phase 8: Remove `USE_DEPENDENCY_BUS` Flag (2 days)

### Objective

The bus path is now unconditional and is the sole completion authority (CM removed in Phase 5, legacy flags removed in Phase 3). Remove the `USE_DEPENDENCY_BUS` flag and all 45 references. All `if use_dep_bus:` branches are now unconditional — keep only the bus path code.

### Coupling

- **Depends on**: Phase 5 complete (CM removed, bus is sole path, no CM fallback), Phase 3 complete (legacy flags removed)
- **Shared files**: `daemon/config.py`, `daemon/tools/instance.py`, `daemon/services/error_reporting.py`, `daemon/services/child_reports.py`, `daemon/services/job_feedback_observer.py`

### Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **8.1** | Remove flag from config | Remove `use_dependency_bus` from `JobSystemConfig` (config.py:397-410), `config.yaml`, and environment variable `ENSEMBLE_JOB_SYSTEM_USE_DEPENDENCY_BUS`. | `daemon/config.py:397-410`, `config.yaml` |
| **8.2** | Remove all `if use_dep_bus:` conditionals | `grep -rn "use_dependency_bus\|USE_DEPENDENCY_BUS" daemon/ --include="*.py"` = 45 references. Remove all `if use_dep_bus:` / `_is_dependency_bus_enabled()` conditionals. Keep only the bus path code (the `if True` branch). Specific sites: `tools/instance.py:264, 284, 606, 685`; `error_reporting.py:64, 88, 646`; `child_reports.py:112, 129, 163, 232, 410, 1649, 1681`; `job_feedback_observer.py:228, 242, 276, 1743, 1952`. | 5+ files |
| **8.3** | Remove docstring/comment references | Clean up all remaining comment references to `USE_DEPENDENCY_BUS`, `use_dep_bus`, `dependency bus enabled`, etc. | All daemon/ files |
| **8.4** | Remove flag from test fixtures | Remove `use_dependency_bus` parameterization from any remaining test fixtures. Bus is now always ON. | `tests/` |

### Acceptance Criteria

- [ ] `USE_DEPENDENCY_BUS` / `use_dependency_bus` does not exist in any source file or config.
- [ ] No `if use_dep_bus:` / `_is_dependency_bus_enabled()` conditional branches in `daemon/`.
- [ ] No environment variable `ENSEMBLE_JOB_SYSTEM_USE_DEPENDENCY_BUS` references.
- [ ] `grep -rn "use_dependency_bus\|USE_DEPENDENCY_BUS" daemon/ --include="*.py"` returns 0 results.
- [ ] All E2E workflow tests pass.
- [ ] Full test suite passes.

### Risks & Rollback

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| A conditional branch was mistakenly identified as dead | Low | Medium | The flag is ON by default, so removing `if FLAG:` and keeping the bus branch is the safe direction. Run full test suite. |
| Removing flag breaks a test fixture that parameterizes over it | Medium | Low | Task 8.4 updates test fixtures. Grep tests for `use_dependency_bus` parameterization. |

**Rollback**: Revert the commit. The flag is additive — removing it and reverting is a no-op on behavior.

---

## 10. Cross-Phase Risk Register

| Risk | Phase(s) | Likelihood | Impact | Mitigation |
|------|----------|------------|--------|------------|
| Generation counter extraction breaks orphan-race detection | 1, 4, 5 | Medium | Critical | Phase 1 keeps CM passthrough; test before removing (Phase 5). Phase 4's column drop must verify Phase 1 is stable. New per-parent lock added to bus — locks are sequential (never nested): generation outside lock, per-parent for INSERT, per-task for cache. |
| Column drop migration drops `instance_hierarchy` (live table) | 4 | **CRITICAL** | **CRITICAL** | Task 4.5 removes DROP/CREATE from `20260621_000002`. Task 4.7 verifies table intact. Task 4.8 PG test. |
| Column drop migration fails on PostgreSQL | 4 | Medium | High | Test on staging PostgreSQL (Task 4.8). Extend `_ensure_postgres_drop_legacy_columns()` (Task 4.9). Have `ADD COLUMN` rollback ready. |
| CM removal misses an alias import or private-attribute access | 5 | Medium | High | Task 5.2 lists all 8 alias/private sites explicitly. Grep for `as .*cm` and `cm\._` after 5.2. |
| `_retrigger_parent_finalize` hardcodes COMPLETED, masking child errors | 5 | **Certain** | **Critical** | Task 5.7 threads error status through the finalization chain. `TestBusSoleAuthority` scenario (4) verifies error propagation. Without this fix, all parents finalize as COMPLETED regardless of child errors. |
| Sync/async mismatch in worker threads | 5 | Medium | Critical | Task 5.8 ensures `_sync` variants used. |
| Removing `enqueue_message_via_jq` breaks a caller with unique params | 6 | Medium | Medium | Audit function signature before merging. Preserve unique parameters. |
| Test removal deletes a useful regression guard | 7 | Low | Medium | Only remove tests for removed code. Migrate bus-relevant assertions to `test_dependency_bus.py`. Add `TestBusSoleAuthority` before CM deletion. |
| `USE_DEPENDENCY_BUS` removal misses a conditional branch | 8 | Low | Medium | Grep for all 45 references. Run full test suite. |

---

## 11. Shippability Checklist

Each phase must be independently shippable. Verify before merging:

### Phase 1
- [ ] Bus has generation counter + per-parent locking (`_parent_locks`, `_get_parent_lock`)
- [ ] Locks are SEQUENTIAL (generation outside lock, per-parent for INSERT, per-task for cache — never nested)
- [ ] CM passthrough works
- [ ] `TestGenerationCounterBump` rewritten to test `bus.get_generation()`
- [ ] No behavioral change

### Phase 2
- [ ] Dead flag removed
- [ ] Lease stubs removed
- [ ] Execution gate is pure asyncio.Lock
- [ ] `tests/unit/services/test_execution_gate.py` has no Lease tests

### Phase 3
- [ ] Kill switch removed
- [ ] `DEBUG_COMPLETION_INVARIANT` removed
- [ ] No SQL mutations for `waiting_for`
- [ ] Premature-completion parameterization removed (moved from Phase 7)
- [ ] All tests pass under single path

### Phase 4
- [ ] Columns dropped
- [ ] `instance_hierarchy` NOT dropped by migration (DROP/CREATE removed)
- [ ] `instance_lifecycle.py:650` replaced with hierarchy query
- [ ] Repository methods (`get_all_with_waiting_for`, `update_waiting_for`) removed
- [ ] `_ensure_postgres_drop_legacy_columns()` actually drops columns
- [ ] PostgreSQL column-drop test passes
- [ ] Comprehensive grep sweep: 324 → 0 code references

### Phase 5
- [ ] CM file deleted
- [ ] Zero CM references (including aliases)
- [ ] `TestBusSoleAuthority` passes (6 scenarios)
- [ ] `_retrigger_parent_finalize` threads error status (Task 5.7) — no hardcoded COMPLETED when child errored
- [ ] Sync variants used in worker threads
- [ ] E2E tests pass

### Phase 6
- [ ] Single enqueue function
- [ ] No dead branches

### Phase 7
- [ ] Zero obsolete tests
- [ ] Docs reflect current architecture
- [ ] No references to removed flags/classes/columns anywhere
- [ ] **Grep verification**: Run `grep -rn 'CorrelationManager\|USE_LEGACY\|LeaseContention\|waiting_for\|USE_DEPENDENCY_BUS\|get_correlation_manager' daemon/ tests/` and verify zero hits (excluding historical `docs/plans/` files and CHANGELOG).

### Phase 8
- [ ] `USE_DEPENDENCY_BUS` flag removed
- [ ] Zero `use_dep_bus` / `_is_dependency_bus_enabled()` references
- [ ] All tests pass

---

## Appendix A: File Impact Summary

| File | Phases Touching It | Net Change |
|------|-------------------|------------|
| `daemon/services/correlation_manager.py` | 1, 3, 5 | **DELETED** (1843 lines removed) |
| `daemon/services/dependency_bus.py` | 1 | +generation counter + per-parent locking (~40 lines) |
| `daemon/services/job_feedback_observer.py` | 1, 3, 4, 5, 8 | Major cleanup (~100 lines removed) |
| `daemon/services/child_reports.py` | 3, 5, 6, 8 | Major cleanup (~80 lines removed) |
| `daemon/services/instance_lifecycle.py` | 3, 4, 5 | Major cleanup (~60 lines removed); L650 replaced with hierarchy query |
| `daemon/tools/instance.py` | 3, 5, 8 | Remove `use_dep_bus` branches (~30 lines) |
| `daemon/services/execution_gate.py` | 2 | Remove Lease stubs (~50 lines) |
| `daemon/manager.py` | 2, 4, 5, 6 | Remove dead branches + extend `_ensure_postgres_drop_legacy_columns()` (~40 lines) |
| `daemon/services/message_processing_pipeline.py` | 2, 6 | Remove Lease refs + dead branches (~20 lines) |
| `daemon/services/task_processor.py` | 2 | Remove Lease refs (~15 lines) |
| `daemon/services/error_reporting.py` | 3, 5, 8 | Remove A8/A9 branches + CM calls + `use_dep_bus` conditionals |
| `daemon/services/job_processor.py` | 3, 5 | Remove flag branches + CM calls |
| `daemon/repositories/instance/models.py` | 4 | Remove 2 column fields |
| `daemon/repositories/instance/repository.py` | 4 | Remove `_enrich_instance()` children overwrite + `get_all_with_waiting_for()` + `update_waiting_for()` |
| `daemon/config.py` | 2, 3, 8 | Remove 4 flags |
| `daemon/api.py` | 5 | Remove CM init/shutdown (L116-119, 365, 505) |
| `daemon/services/instance_messaging.py` | 6 | Consolidate enqueue function |
| `daemon/migrations/versions/20260621_000002_drop_legacy_completion_columns.sql` | 4 | Remove `instance_hierarchy` DROP/CREATE (L99, L125) |
| `config.yaml` | 2, 3, 8 | Remove 4 flag keys |

**Estimated total: ~2500 lines removed, ~40 lines added.**
