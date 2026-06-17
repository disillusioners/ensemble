# Phase 4: Counter Removal & Cleanup

## Objective
Deprecate and remove the `waiting_for` counter and `WAITING_CHILDREN` status now that CorrelationManager is the authoritative source for parent-child correlation. This eliminates the last remaining race surfaces and simplifies the codebase by removing 6 lifecycle sites that maintained the counter.

## Coupling
- **Depends on**: Phase 2 (observer uses correlation events), Phase 3 (cascade sites delegate to CM)
- **Coupling type**: tight — same files as Phase 3, removes the counter that Phase 3 kept for validation
- **Shared files with other phases**: `child_reports.py`, `error_reporting.py`, `manager.py`, `instance_messaging.py`, `message_job_handler.py`, `task_processor.py`
- **Shared APIs/interfaces**: `InstanceStatus` enum, `instances` table schema
- **Why this coupling**: Must complete Phase 3 first — the counter is only safe to remove when all consumers have switched to CM

## Context

### Current `waiting_for` Touchpoints

**97 references across 15 Python files** (verified via `grep -rn "waiting_for" daemon/ --include="*.py"`).

The counter is modified in 6 lifecycle sites and read in many more:

| # | Location | Operation | Purpose |
|---|----------|-----------|---------|
| 1 | `daemon/tools/instance.py:571` | Increment | When parent sends message to child: `SET waiting_for = COALESCE(waiting_for, 0) + 1` |
| 2 | `daemon/services/child_reports.py:424-438` | Decrement | When child response processed: `SET waiting_for = CASE WHEN ... RETURNING waiting_for` |
| 3 | `daemon/services/error_reporting.py:197-211` | Decrement | When child errors: same SQL as Site 2 |
| 4 | `daemon/services/child_reports.py:479` | Read | Cascade check: `parent.waiting_for == 0` |
| 5 | `daemon/services/error_reporting.py:240` | Read | Cascade check: `parent.waiting_for == 0` |
| 6 | `daemon/manager.py:2751-2753` | Read | Resume background: `waiting_for > 0` deferral |

After Phase 3, Sites 4 and 5 are already delegated to CM. Phase 4 removes the remaining sites and all 97 references.

### `WAITING_CHILDREN` Status Touchpoints

**43 matches across 12 files** (from Phase 3 dual-path exploration):

| File | Usage Count | Role |
|------|-------------|------|
| `instance_messaging.py` | 6 | Revival logic (IDLE/WAITING_CHILDREN/COMPLETED → RUNNING) |
| `message_job_handler.py` | 6 | Status transition + skip_complete logic |
| `child_reports.py` | 10 | Parent waits for children cascade |
| `task_processor.py` | 1 | Comment reference |
| `manager.py` | 1 | Resume background deferral |
| `error_reporting.py` | 4 | Error path: parent to WAITING_CHILDREN |
| `job_recovery_service.py` | 1 | Recovery filter |
| `task/repository.py` | 8 | FIFO placeholder carve-out |
| `project/repository.py` | 1 | Status query |
| `scheduler.py` | 1 | Scheduler source |
| `messages.py` | 1 | Log message |
| `instances.py` | 1 | Log message |

## Tasks

### Part A: Deprecate `waiting_for` Reads (Keep Writes as Rebuild Cache — Fix A1)

**⚠️ Revised scope:** We stop *reading* `waiting_for` for control-flow decisions, but KEEP *writing* to it as a durable rebuild cache for `rebuild_from_db()`. The column is never dropped.

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Keep** increment at send_message | `SET waiting_for = COALESCE(waiting_for, 0) + 1` STAYS — needed for crash-recovery rebuild | `daemon/tools/instance.py:565-583` |
| 2 | **Keep** decrement at child completion | SQL `UPDATE ... RETURNING waiting_for` STAYS — needed for crash-recovery rebuild | `daemon/services/child_reports.py:424-438`, `daemon/services/error_reporting.py:197-211` |
| 3 | Remove read at resume path | Replace `waiting_for > 0` check at manager.py:2751 with `correlation_manager.is_complete(instance_id)` | `daemon/manager.py:2751-2753` |
| 4 | Audit all 97 references | Replace every *read* of `waiting_for` for decision-making with CM equivalent; keep *writes* | All 15 files |
| 5 | Add deprecation logging | Log a WARNING if `waiting_for` is read from DB for decision-making (catch any missed reads — writes are expected and not logged) | `daemon/repositories/instance/repository.py` |

### Part B: Column Drop (~~Deferred~~ Cancelled — Fix A1)

**⚠️ The `waiting_for` column is NOT dropped.** It remains as a permanent rebuild-only cache. This avoids the `rebuild_from_db()` breakage identified in A1. The column is cheap (one integer per instance row) and provides reliable crash recovery.

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 6 | ~~Add migration to drop column~~ | **CANCELLED** — column stays for rebuild | N/A |
| 7 | Document `waiting_for` as rebuild-only | Add docstring/comment to model: "This field is written for crash-recovery rebuild only. Do not read for runtime decisions — use CorrelationManager." | `daemon/repositories/instance/models.py:65` |

### Part C: Deprecate `WAITING_CHILDREN` Status

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 9 | Make `WAITING_CHILDREN` a derived state | Instead of setting `status = WAITING_CHILDREN`, CM tracks this in-memory; status stays `PROCESSING` | `daemon/services/correlation_manager.py` |
| 10 | Remove `WAITING_CHILDREN` from revival logic | `enqueue_message` and `enqueue_message_via_jq` no longer revive from `WAITING_CHILDREN` (instances stay `PROCESSING` while children run) | `daemon/services/instance_messaging.py:773-783, 1396-1409` |
| 11 | Remove `WAITING_CHILDREN` from message_job_handler | Status transition logic at lines 129-167, 328-352 uses CM's `is_parent_complete()` instead | `daemon/services/message_job_handler.py` |
| 12 | Remove `WAITING_CHILDREN` from task repository | FIFO placeholder carve-out at lines 167, 179-180, 190, 232, 268, 605, 649 | `daemon/repositories/task/repository.py` |
| 13 | Remove from recovery service | `job_recovery_service.py:38` filter | `daemon/services/job_recovery_service.py` |
| 14 | Remove from scheduler source | `daemon/sources/adapters/scheduler.py:563` | `daemon/sources/adapters/scheduler.py` |

### Part D: Remove `WAITING_CHILDREN` from Enum (Deferred)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 15 | Remove `WAITING_CHILDREN` from `InstanceStatus` enum | After all consumers removed | `daemon/repositories/instance/models.py:28`, `daemon/models/instance.py:13` |
| 16 | Add API backward compatibility shim | If any external API consumers send `waiting_children`, translate to `processing` with a correlation warning | `daemon/routers/` |

**⚠️ Task 15-16 should only be done after confirming no external API consumers depend on `WAITING_CHILDREN`.**

### Part E: Testing

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 17 | Test: system works without `waiting_for` | Full test suite passes with counter removed | All tests |
| 18 | Test: system works without `WAITING_CHILDREN` | Full test suite passes with status removed | All tests |
| 19 | Test: CM rebuild works with `waiting_for` as cache | Restart daemon, verify CM rebuilds from `instances` table using `waiting_for > 0` + message_queue | `tests/test_cm_rebuild.py` |
| 20 | Test: deprecation log fires for missed reads | Inject a fake read of `waiting_for` and verify deprecation log appears | `tests/test_waiting_for_deprecation.py` |

## Key Design Decisions

### 1. Read Removal Only — Column Kept as Rebuild Cache (Fix A1)
**Decision**: Phase 4 removes all *reads* of `waiting_for` for control-flow decisions. The column is NOT dropped — it remains as a permanent rebuild-only cache. Writes (increment/decrement) continue.
**Rationale**:
- `rebuild_from_db()` queries `waiting_for > 0` to find parents needing correlation tracking after restart
- If we stopped writing, the column would read 0 for all rows → rebuild finds nothing → parents stuck
- `message_queue` is direction-blind (no `sender_id`), so it can't reliably reconstruct correlation alone
- Keeping the writes is cheap (single atomic SQL per send_message/completion)
- The column is never dropped — it's a permanent crash-recovery artifact

### 2. `WAITING_CHILDREN` Replaced by In-Memory CM State
**Decision**: Instead of `status = WAITING_CHILDREN`, instances stay `PROCESSING` while children are running. CM tracks "waiting for children" in-memory.
**Rationale**:
- `WAITING_CHILDREN` is a transient state that adds complexity to every status-checking code path (43 matches in 12 files)
- The information it carries ("this instance is processing but has children running") is exactly what CM tracks
- Removing it simplifies the status machine: `IDLE → PROCESSING → COMPLETED | ERROR | TERMINATED`
- API consumers who query status get `PROCESSING` (which is accurate — the parent IS processing, just waiting on children)

### 3. CM Rebuild Uses `waiting_for` as Rebuild-Only Cache (Fix A1)
**Decision**: CM rebuilds from `instances` table querying `waiting_for > 0` cross-referenced with `message_queue` for real UUIDs. The `waiting_for` column is kept permanently as a rebuild cache.
**Rationale**:
- `waiting_for > 0` identifies which parents have pending responses
- `message_queue` cross-reference provides the real `message_id` UUIDs for correlation keys
- The column is written but never read for runtime decisions — only for crash recovery
- This avoids the direction-blindness problem of `message_queue` alone

### 4. Dual-Path Enqueue Simplification
**Decision**: Remove `WAITING_CHILDREN` from the revival logic in both `enqueue_message` and `enqueue_message_via_jq`.
**Rationale**:
- Both currently revive from `{IDLE, WAITING_CHILDREN, COMPLETED} → RUNNING`
- After removal: revive from `{IDLE, COMPLETED} → RUNNING`
- `PROCESSING` instances don't need revival (they're already processing)
- Simplifies the duplicated enqueue code (closer to Phase 5 consolidation)

### 5. Rebuild Strategy After Column Removal (Fix A1)
**Decision**: Keep `waiting_for` as a **rebuild-only cache** — continue writing to it for as long as the column exists, but never read it for control-flow decisions.
**Rationale**:
- Phase 1's `rebuild_from_db()` queries `instances WHERE waiting_for > 0` to find parents needing correlation tracking
- If Phase 4 stopped writing to `waiting_for`, the column would read 0 for all rows → rebuild finds nothing → CM state empty after restart → parents stuck in PROCESSING forever
- The `message_queue` table alone is **direction-blind** (no `sender_id` column) — you can't distinguish parent→child sends from child→parent completion reports, so reconstructing correlation purely from `message_queue` is unreliable
- **Simplest fix**: keep writing `waiting_for` (it's a single atomic SQL increment/decrement), use it ONLY for crash-recovery rebuild, never for runtime decisions
- This means Part A of Phase 4 is revised: we stop *reading* `waiting_for` for decisions, but keep *writing* it as a durable rebuild cache

**Revised Phase 4 Part A scope:**
- ✅ Remove all *reads* of `waiting_for` for control-flow decisions (Sites 4, 5, 6, and the 97 references)
- ❌ Do NOT remove the *writes* (increment at `send_message`, decrement at child completion)
- The column becomes write-only from the control-flow perspective — a durable crash-recovery cache
- Phase 4 Part B (column drop) is **deferred indefinitely** — keeping the column is cheap and provides crash recovery

**Alternative considered and rejected:**
- *(B) Persistent `correlation_state` table*: Overkill — `waiting_for` already serves this purpose
- *(C) `source_instance_id` column on `message_queue`*: Too invasive for this phase; would be a separate migration project
- *(D) PROCESSING job_queue_items + hierarchy join*: Fragile — depends on job state which may not exist for WorkerPool path

**What this means for `rebuild_from_db()`:**
- Phase 1 rebuild logic is **unchanged** — still queries `waiting_for > 0` + `message_queue` for real UUIDs
- After Phase 4, `waiting_for` is still written (just not read for decisions), so rebuild still works
- The column is never dropped — it's a permanent crash-recovery artifact

## Migration Path for External Consumers

Any code that checks for `WAITING_CHILDREN`:

| Pattern | Before | After |
|---------|--------|-------|
| Status display | `if status == "waiting_children"` | `if cm.is_waiting_for_children(instance_id)` |
| Revival logic | `revive from {IDLE, WAITING_CHILDREN, COMPLETED}` | `revive from {IDLE, COMPLETED}` |
| Status query | `WHERE status = 'waiting_children'` | N/A (status is `processing`) |
| Job recovery | `filter(WAITING_CHILDREN)` | `filter(PROCESSING)` + CM check |

## Key Files

| File | Changes |
|------|---------|
| `daemon/tools/instance.py:565-583` | **Keep** increment (rebuild cache) — remove any decision reads |
| `daemon/services/child_reports.py:424-438` | **Keep** decrement SQL (rebuild cache) — remove cascade reads |
| `daemon/services/error_reporting.py:197-211` | **Keep** decrement SQL (rebuild cache) — remove cascade reads |
| `daemon/manager.py:2751-2753` | Replace read with CM check |
| `daemon/repositories/instance/models.py:65` | Add docstring: "rebuild-only cache, do not read for decisions" (Fix A1) |
| `daemon/repositories/instance/models.py:28` | Remove `WAITING_CHILDREN` from enum (deferred) |
| `daemon/services/instance_messaging.py:773-783, 1396-1409` | Remove WAITING_CHILDREN revival |
| `daemon/services/message_job_handler.py:129-167, 328-352` | Replace WAITING_CHILDREN checks with CM |
| `daemon/repositories/task/repository.py` (8 locations) | Remove WAITING_CHILDREN FIFO carve-out |
| `daemon/services/job_recovery_service.py:38` | Remove WAITING_CHILDREN filter |
| `daemon/sources/adapters/scheduler.py:563` | Remove WAITING_CHILDREN reference |

## Constraints
- `waiting_for` column is NOT dropped — kept as permanent rebuild cache (Fix A1)
- `waiting_for` writes (increment/decrement) continue — needed for crash-recovery rebuild
- All `waiting_for` *reads* for control-flow are replaced with CM equivalents
- `WAITING_CHILDREN` removal must not break API contract (add backward compat shim)
- External API consumers may depend on `WAITING_CHILDREN` in status responses
- The soak period between deprecation and removal must catch all edge cases
- `InstanceStatus` is defined in TWO places (`models/instance.py` and `repositories/instance/models.py`) — both must be updated

## Verification Strategy

### Deprecation Phase (Part A + C)
1. **Full test suite**: All tests pass with `waiting_for` no longer READ for control flow (writes continue as rebuild cache)
2. **Log monitoring**: Zero `waiting_for` deprecation warnings after 24h soak
3. **Shadow mode**: CM shadow mode (from Phase 1) still shows zero mismatches — now CM is the only source
4. **Manual testing**: Spawn children, complete them, verify parent transitions correctly without `waiting_for`

### Removal Phase (Part B + D) — Deferred Indefinitely per ADR-011
**⚠️ Part B (column drop) and Part D (enum removal) are deferred indefinitely.** Per ADR-011, the `waiting_for` column is permanently retained as a write-only rebuild cache, and the `WAITING_CHILDREN` enum value is retained for API backward compatibility. No migration test, negative schema test, or rejection test is performed because no schema change is planned.

1. **No migration test** — there is no column-drop migration. The `waiting_for` column remains in the schema indefinitely.
2. **No negative-schema test** — querying the `waiting_for` column is expected to succeed; it is the rebuild cache source.
3. **No enum-rejection test** — `status = WAITING_CHILDREN` is still accepted by the enum and required for API backward compatibility. (Internal callers must not SET this status; external API responses may still INCLUDE it.)

## Rollback Plan

### Read Removal Phase (Part A + C)
1. Restore `waiting_for` reads for control-flow decisions
2. Restore `WAITING_CHILDREN` status transitions
3. CM's shadow mode validation resumes comparing with `waiting_for`
4. **Safe**: No schema changes, all data intact, writes never stopped

### No Column Drop Phase (Fix A1)
- `waiting_for` column is never dropped — no rollback needed for schema
- The column persists with accurate values (writes continued throughout)

## Deliverables
- [ ] All `waiting_for` *reads* for control-flow replaced with CM equivalents (writes kept)
- [ ] `WAITING_CHILDREN` no longer set as a status
- [ ] `correlation_manager.register_message_send()` runs alongside `waiting_for` increment (kept)
- [ ] `correlation_manager.resolve_response()` runs alongside `waiting_for` decrement (kept)
- [ ] `correlation_manager.is_complete()` replaces all decision reads
- [ ] All 97 `waiting_for` references audited — reads replaced, writes kept
- [ ] `waiting_for` column documented as "rebuild-only cache" (Fix A1)
- [ ] Deprecation logging active for any missed `waiting_for` reads (not writes)
- [ ] Revival logic simplified (IDLE/COMPLETED only)
- [ ] All `WAITING_CHILDREN` references removed from status checks
- [ ] Full test suite passes
- [ ] ~~Migration script for column drop~~ — cancelled, column kept (Fix A1)
- [ ] API backward compatibility shim added
