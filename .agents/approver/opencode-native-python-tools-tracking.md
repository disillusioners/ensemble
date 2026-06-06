# Tracking: opencode-native-python-tools

## Iteration 001 — 2026-06-07

**Verdict: REJECTED**

### Blocking Issues

1. **BLOCKER: `SQLModel.metadata.create_all(engine)` creates all 22+ tables on dedicated DB**
   - Location: `daemon/opencode/repository.py:318` (production code) + Phase 3 plan `create_opencode_engine()` in factory.py
   - Expected: Only `opencode_sessions` table created on the dedicated engine
   - Found: `SQLModel.metadata` is global — `create_all()` emits DDL for every registered model (instances, projects, job_queues, message_queue, tasks, events, etc.) into `opencode_sessions.db`, completely defeating the "separate persistence layer" architecture
   - Fix: Replace with `OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)` in BOTH locations

2. **BLOCKER: Migration SQL file targets the wrong database**
   - Location: `daemon/migrations/versions/20260606_000002_create_opencode_sessions_table.sql`
   - Expected: Schema applied to `data/opencode_sessions.db` (dedicated DB)
   - Found: `MigrationRunner` runs against `self._engine` (the main ensemble engine at `manager.py:524`). The migration would create `opencode_sessions` table in `data/instances.db`, polluting the main DB's `schema_migrations` history, while the dedicated DB would have no schema
   - Fix: Delete the migration file. Table creation handled by `__table__.create()` at engine-factory time (after Blocker 1 fix)

3. **Phase 3 references nonexistent manager methods**
   - Location: `phase3-plan.md:115` ("In `InstanceManager.start()`"), `phase3-plan.md:129` ("In `InstanceManager.stop()`")
   - Expected: `initialize()` and `shutdown()` (verified at `manager.py:972` and `manager.py:2563`)
   - Found: Plan says `start()` and `stop()` — these methods do not exist. Implementation would raise `AttributeError`

### Important Issues (not blocking, but should be fixed)

4. **Tools access private `registry._repository`** — 8 tool functions use `registry._repository.get(...)` with `# type: ignore[attr-defined]`. Should add a public delegate method `get_session_record()` on `OpenCodeSessionRegistry`.

5. **`wait_any` polls sessions sequentially** — 3 sessions × 30s poll = 90s between full sweeps. Go binary polls in parallel via goroutines. Python equivalent: `asyncio.gather()`.

6. **Phase 5 test mocks wrong httpx method** — Tests patch `httpx.AsyncClient.post` but production code uses `self._client.request(method, ...)` at `client.py:298`. Tests would silently pass against real network calls.

7. **`asyncio.get_event_loop().time()` deprecated** — Python 3.10+ recommends `asyncio.get_running_loop().time()`. Project runs 3.13+.

### Notes

- Production code (Phase 1) is solid — all C1-C9 claims verified accurate
- Line counts match exactly (3,093 lines)
- Architecture decisions (separate DB, factory pattern, closure injection) are sound
- Phase 4 skill prompt rewrite is well-structured
- The blockers are in the integration layer (Phases 2-3), not the core port

## Iteration 002 — 2026-06-07

**Verdict: REJECTED**

### Iteration 001 Status

All 3 blockers from iteration 001 are fixed:
- ✅ Blocker 1: `repository.py:325` now uses `OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)`
- ✅ Blocker 2: Migration file deleted (confirmed via `ls`)
- ✅ Blocker 3: Phase 3 plan now references `initialize()` and `shutdown()`

All 4 important issues from iteration 001 are fixed:
- ✅ Issue 5: `asyncio.gather()` used in `wait_any` (lines 364, 379)
- ✅ Issue 6: Tests patch `client._request` (line 184)
- ✅ Issue 7: `asyncio.get_running_loop().time()` used (lines 303, 347)
- ⚠️ Issue 4: Partially fixed — 6/8 tools use `get_session_record()`, 2 still use `_repository.get()`

### New Blocking Issues Found in Iteration 002

1. **BLOCKER: Name collision — `external_opencode_send_message` used for both tool and server import**
   - Location: `phase2-plan.md:114` (`_send` helper), `phase2-plan.md:168` (tool def), `phase2-plan.md:559` (module import)
   - Expected: `_send` calls the server's `external_opencode_send_message` dispatcher (returns `OpenCodeResponse`)
   - Found: `_send` is a closure inside the factory. When it resolves `external_opencode_send_message`, Python finds the local `@tool`-decorated function first (line 168 shadows module-level import). `_send` calls the LangChain tool with an `OpenCodeRequest` as positional arg — type mismatch, runtime failure
   - Fix: Rename module-level import to `_server_send_message` (or similar alias)

2. **BLOCKER: Phase 3 plan contradicts its own constraints**
   - Location: `phase3-plan.md:48-61` (raw `create_engine` + manual pragmas) vs `phase3-plan.md:164` (constraint: "must use `create_engine_from_config`")
   - Expected: Use existing `create_engine_from_config(DatabaseConfig.sqlite(...))` pattern (consistent with `manager.py:510-511`)
   - Found: Inline re-implementation of engine creation with manual `@event.listens_for` pragma setup, missing `event` import, missing `PRAGMA foreign_keys=ON`
   - Fix: Rewrite to use `create_engine_from_config` pattern

### Important Issues

3. **Issue 4 fix incomplete** — `phase2-plan.md:437` and `phase2-plan.md:485` still use `registry._repository.get()` instead of `registry.get_session_record()`

4. **Dead migration test** — `phase5-plan.md:394-419` references `test_opencode_migration.py` for a migration file that no longer exists

### Notes

- The name collision (Blocker 1) is a subtle Python scoping issue — the kind of bug that passes review but fails at first runtime
- Phase 1 production code remains solid
- The fixes from iteration 001 were well-executed; these are genuinely new findings

## Iteration 003 — 2026-06-07

**Verdict: APPROVED**

### Iteration 002 Status

All 2 blockers from iteration 002 are fixed:
- ✅ Blocker 1 (name collision): Module-level import aliased as `_server_send_message` (line 60); `_send` closure calls `_server_send_message` (line 120)
- ✅ Blocker 2 (engine creation): Phase 3 Step 1 uses `create_engine_from_config(DatabaseConfig.sqlite(...))` (lines 53-55); no raw `create_engine` or manual `@event.listens_for`

All 2 important issues from iteration 002 are fixed:
- ✅ Issue 3 (get_session_record): All 8 tools now use `await registry.get_session_record()` — verified at lines 197, 251, 304, 361, 443, 491 (tools 2-7). Tools 1 and 8 don't need it (dispatch via action).
- ✅ Issue 4 (dead migration test): Replaced with `test_table_creation.py` testing `__table__.create()` idempotency + no ensemble table leakage (lines 396-446)

### Non-Blocking Observations

1. **Stale duplicate import** — `phase2-plan.md:565` has `from daemon.opencode.server import external_opencode_send_message` (un-aliased) after the code block's return list. This is inside the code block but is dead code — it doesn't override `_server_send_message` and doesn't affect `_send`. Remove during implementation for clarity.

2. **Test `test_only_opencode_table_created`** (phase5-plan.md:428-438) only asserts 3 specific ensemble tables are absent. Could also assert the full table count is exactly 1, but current assertions are sufficient.

### Notes

- Plan has been through 3 iterations of improvement
- All blockers from all iterations are resolved
- Production code (Phase 1) remains solid throughout
- Integration plans (Phases 2-5) are now internally consistent and correct
- Ready for implementation
