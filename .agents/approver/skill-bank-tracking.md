# Tracking: Skill Bank Feature

## Iteration 001 — 2026-07-13
**Verdict:** REJECTED
**Plan:** `.agents/shared/planning/skill-bank/plan-overview.md`

### Blocking Issues

1. **Validation bypassed — empty name/content accepted**
   - `SkillBankService` with validation is defined (Phase 2 Task 1) but never wired into manager
   - Router (Phase 2 Task 3) calls `manager._skill_bank_repo` directly, bypassing service
   - Result: `POST /api/skill-bank` with `{"name": "", "content": ""}` succeeds
   - Router catches `ValueError` but nothing raises it
   - Expected: name/content validated non-empty; Found: no validation in path
   - Fix: Either wire `SkillBankService` in manager + use in router, OR add `min_length=1` to Pydantic schemas, OR add validation in repository

2. **Missing `daemon/repositories/__init__.py` import — table won't be created on fresh PG**
   - Plan adds `SkillBankItem` to `daemon/repositories/skill/models.py` but never mentions updating `daemon/repositories/__init__.py`
   - The file explicitly comments that model imports are needed so `SQLModel.metadata.create_all()` registers tables
   - Without the import, `create_all` won't discover the `SkillBankItem` model on fresh PG databases
   - Directly contradicts success criterion #1: "skill_bank table created on both SQLite and PostgreSQL (fresh + existing DBs)"
   - Expected: `SkillBankItem` imported in `__init__.py`; Found: not in plan's file manifest

3. **Internal contradiction — service layer defined but bypassed**
   - Phase 2 claims to "follow mcp_servers pattern" (no service layer)
   - Phase 2 Task 1 defines `SkillBankService` with validation logic
   - Phase 2 Task 3 router accesses repo directly, never uses the service
   - Pick one: either follow mcp_servers (no service) or wire the service properly

### Important Convention Issues (non-blocking individually, but should be addressed)

4. **Missing `is_write_paused` check on write endpoints**
   - `mcp_servers.py` (the plan's stated reference pattern) checks `manager.is_write_paused` on all POST/PUT/DELETE
   - Plan's skill_bank router skips this entirely
   - Writes during DB migration would succeed when they should return 503

5. **Wrong method name in plan prose**
   - Plan repeatedly references `_create_postgres_objects()` — this method does not exist
   - Actual method: `_ensure_postgres_columns()` at `daemon/manager.py:2460`
   - Line references (~3057) are roughly correct; method name is wrong

### Non-blocking observations
- VARCHAR(N) vs TEXT divergence between model and DDL — established project convention, benign
- Error envelope (`detail=str(e)`) differs from mcp_servers' `ErrorResponse(...).model_dump()` — but skills.py also uses simpler `detail={"error": str(e)}`, so there's precedent
- Route ordering instruction is correct and sufficient

---

## Iteration 002 — 2026-07-13
**Verdict:** APPROVED
**Plan:** `.agents/shared/planning/skill-bank/plan-overview.md` (Rev 2)

### Previous Issues — Resolution Status

1. ✅ **Validation bypassed** — FIXED. Service layer dropped entirely. Pydantic `Field(min_length=1)` on `name` and `content` in `SkillBankItemCreate` schema. FastAPI returns 422 on empty strings. Verified against mcp_servers pattern.
2. ✅ **Missing `__init__.py` import** — FIXED. Phase 1 Task 2 explicitly adds `SkillBankItem` to `daemon/repositories/__init__.py` imports + `__all__`. Listed in file manifest as modified file #2 (marked CRITICAL).
3. ✅ **Internal contradiction (service layer)** — FIXED. No `SkillBankService` class anywhere in the plan. Router accesses `manager._skill_bank_repo` directly. No service file in manifest.
4. ✅ **Missing `is_write_paused`** — FIXED. All write endpoints (POST/PUT/DELETE) have `if manager.is_write_paused: raise HTTPException(status_code=503, ...)` guard. Verified in Phase 2 code spec.
5. ✅ **Wrong method name** — FIXED. All references now correctly use `_ensure_postgres_columns()` (verified at `daemon/manager.py:2460`).

### Independent Verification (codebase spot-checks)

- `_ensure_postgres_columns()` confirmed at line 2460 ✓
- `is_write_paused` property confirmed at line 1546 ✓
- `mcp_servers.py` pattern: `_get_manager`, `asyncio.to_thread`, `is_write_paused` at lines 283/348/481/568/612 ✓
- `__init__.py` imports skill models from `.skill.models`, has explicit comment about `create_all()` for DependencyWatcher ✓
- Skill repos gated behind `if self.config.skill_evolution` at line 772; skill bank wiring specified OUTSIDE this gate ✓
- `_ensure_postgres_columns()` statements array ends after `skill_ab_tests` index at line 3057 — insertion point correct ✓
- Factory pattern matches existing `create_skill_repository()` signature ✓
- Angular routes: `skills/:id` at line 14; `skills/bank` placement before it is correctly specified ✓
- Council evaluation: APPROVED, HIGH confidence, 15 file:line references verified

### Non-blocking Observations

- File manifest count slightly off (says "10 new / 9 modified", actually 8 new / 8 modified — leftover from dropped service file). Cosmetic only.
- Index name inconsistency: `ix_skill_bank_project_id` (model) vs `idx_skill_bank_project` (DDL). Established project convention (skills table has same discrepancy), benign.
- `PUT` cannot set `project_id=null` via update due to `if v is not None` filter. Acceptable design decision for a template store — document in API contract or add explicit `UNSET_PROJECT_ID` handling later.
