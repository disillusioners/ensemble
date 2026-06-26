# Phase 6: Column/Docs/Test Final Cleanup

## Objective

Finalize the documentation and test cleanup for the architecture migration. Verify that the `InstanceInfo.children` field semantics are correct (populated from junction table, not the dropped column), clean up any residual stale references in docs, and update the LESSONS documents to reflect the actual code state.

## Coupling

- **Depends on**: None (independent — can run in parallel with any other phase)
- **Coupling type**: independent
- **Shared files with other phases**: none directly (docs and the `InstanceInfo` model field)
- **Shared APIs/interfaces**: none
- **Why this coupling**: Documentation and model-field cleanup is orthogonal to the D11+D13 code changes

## Context

### What's Already Done (Verified by Exploration)

- ✅ `waiting_for` and `children` DB columns dropped from `instances` table via `_ensure_postgres_drop_legacy_columns()` at `manager.py:1917`
- ✅ Migration `20260621_000002` already fixed — NO `DROP TABLE instance_hierarchy`, only drops the two columns
- ✅ `_ensure_postgres_drop_legacy_columns()` already extended with real `ALTER TABLE` statements
- ✅ Dead test files (`test_kill_switch_legacy_path.py`, `test_correlation_authority_shadow.py`, `test_unified_dispatcher_shadow.py`) already deleted
- ✅ "bus is default / CM is fallback" framing removed from daemon source code (0 hits)
- ✅ `waiting_for` references in daemon source reduced to 2 (the ALTER TABLE statement + log message)
- ✅ `.children` attribute reads: 0 hits in daemon source
- ✅ DB-level `Instance` SQLModel: both `waiting_for` and `children` fields removed

### What Remains

- The `InstanceInfo` Pydantic model (`daemon/models/instance.py:52`) still has a `children: list[str] | None` field. This is **semantically correct** — it's populated from the `instance_hierarchy` junction table via `list_child_ids()`, NOT from the dropped DB column. It should be kept but its docstring should be updated to clarify it's the API response field, not a DB column.
- Historical plan docs (`docs/plans/decouple-*.md`, `docs/plans/cleanup-old-architecture.md`) still contain references to CM, kill-switches, and the old architecture. These are historical planning docs — they should either be marked as archived/historical or updated with a "COMPLETED" status header.
- The LESSONS documents (`architecture-migration-status-2026-06-26.md`, `instance-06f500af-stuck-waiting-children-2026-06-26.md`) describe items as "incomplete" that are now partially/fully done. These should be updated to reflect actual state after D11+D13 land.

### Known Grep False Positives (Minor Notes)

- **`waiting_for` grep**: Will hit `daemon/opencode/state.py` (~3 false positives — `waiting_for_input` state reason). These are **expected** and should NOT be removed — they are a legitimate state reason, not the legacy `instances.waiting_for` column.
- **`.children` grep**: Returns ~4 hits — all in explanatory comments, not active reads. Document as expected.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **6.1** | Update `InstanceInfo.children` docstring | The field at `instance.py:52` is correct (populated from `instance_hierarchy`), but the docstring should explicitly say "API response field populated from `instance_hierarchy` junction table — NOT a DB column on the instances table" to prevent confusion. | `daemon/models/instance.py:52` |
| **6.2** | Verify zero residual `waiting_for` / `children` column reads | Run `grep -rn 'waiting_for\|\.children\b' daemon/ --include="*.py"`. Expected: only the 2 `manager.py` hits (ALTER TABLE + log), plus ~3 false positives in `opencode/state.py` (`waiting_for_input`), plus ~4 `.children` comment-only hits. Any ADDITIONAL hits must be audited and either removed (if dead code) or documented (if intentional). | — |
| **6.3** | Mark historical plan docs as COMPLETED | Add a status header to the top of each plan doc: `docs/plans/decouple-execution-plan.md`, `docs/plans/decouple-job-task-message-correlation.md`, `docs/plans/cleanup-old-architecture.md`. Header: "**STATUS: COMPLETED (2026-06-26) — This is a historical planning document. The migration is complete. See LESSONS/ for final status.**" Do NOT rewrite the docs — they are valuable historical context. | `docs/plans/decouple-execution-plan.md`, `docs/plans/decouple-job-task-message-correlation.md`, `docs/plans/cleanup-old-architecture.md` |
| **6.4** | Update LESSONS status document | Update `LESSONS/architecture-migration-status-2026-06-26.md` with a new section "## Post-Migration Update (2026-06-26)" documenting: (a) which items were already done when exploration ran, (b) which items D11+D13 completed, (c) the actual final state. This prevents future confusion when someone reads the LESSONS doc and finds "incomplete" items that are actually done. | `LESSONS/architecture-migration-status-2026-06-26.md` |
| **6.5** | Update LESSONS bug investigation document | Update `LESSONS/instance-06f500af-stuck-waiting-children-2026-06-26.md` with the resolution status: `cancel_for_source` was already implemented, the startup sweep was added, and D13 structurally eliminates the bug class. | `LESSONS/instance-06f500af-stuck-waiting-children-2026-06-26.md` |
| **6.6** | Verify `instance_hierarchy` table still functional | Run the relevant tests that exercise `instance_hierarchy` queries (spawn, terminate, child_reports). Confirm all pass. This is the final verification that the migration didn't break the junction table. | `tests/` (E2E + integration tests that exercise hierarchy) |
| **6.7** | Final acceptance grep sweep | Run comprehensive greps: `grep -rn 'USE_DEPENDENCY_BUS\|use_dependency_bus\|CorrelationManager\|correlation_manager\|USE_LEGACY\|MessageJobHandler\|_has_no_active_message_job\|dispatch_path.*jobqueue' daemon/ --include="*.py"`. Expected: 0 hits (or only in comments explicitly explaining what was removed). | — |
| **6.8** | Fix migration file docstring (cosmetic) | Migration `daemon/migrations/versions/20260621_000002_drop_legacy_completion_columns.sql` has a docstring referencing wrong `runner.py` line numbers (lines 63-64 say "runner.py lines 446-448"). Verify the actual line numbers and fix the cosmetic reference. Low priority — does not affect functionality. | `daemon/migrations/versions/20260621_000002_drop_legacy_completion_columns.sql:63-64` |

## Key Files

- `daemon/models/instance.py` — `InstanceInfo.children` field (line 52)
- `docs/plans/decouple-execution-plan.md` — historical plan, mark as COMPLETED
- `docs/plans/decouple-job-task-message-correlation.md` — historical plan, mark as COMPLETED
- `docs/plans/cleanup-old-architecture.md` — historical plan, mark as COMPLETED
- `LESSONS/architecture-migration-status-2026-06-26.md` — update with post-migration status
- `LESSONS/instance-06f500af-stuck-waiting-children-2026-06-26.md` — update with resolution

## Constraints

- **Do NOT rewrite historical docs**: They are valuable planning context. Just add status headers.
- **InstanceInfo.children is NOT the dropped DB column**: It's a Pydantic response model field populated from the junction table. Do NOT remove it — it's the API response field that clients use to see child instances.
- **`instance_hierarchy` table is LIVE**: 42+ query sites. Do NOT touch it.
- **Known grep false positives**: `waiting_for` will hit `opencode/state.py` (~3 false positives: `waiting_for_input`). `.children` returns ~4 comment-only hits. Document these in the final grep report — do NOT attempt to remove them.

## Deliverables

- [ ] `InstanceInfo.children` docstring clarified
- [ ] Historical plan docs marked as COMPLETED
- [ ] LESSONS documents updated with actual post-migration state
- [ ] Final acceptance grep sweep passes (0 unexpected hits, known false positives documented)
- [ ] `instance_hierarchy` tests pass
- [ ] Migration file docstring cosmetic fix (runner.py line numbers)
