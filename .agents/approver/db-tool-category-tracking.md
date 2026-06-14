# Plan Tracking: Database Tool Category (`db`)

## Iteration 001 — 2026-06-14 16:24

**Verdict: REJECTED**

### Blocking Issues

1. **api.py line ordering bug (BLOCKER)**
   - Plan claims: "Reorder is NOT needed — `CredentialManager()` at api.py:232 is already constructed before `InstanceManager` at api.py:172"
   - Reality: InstanceManager is at line 172, CredentialManager is at line 232. Manager is constructed FIRST, credential_manager is defined 60 lines LATER.
   - Adding `credential_manager=credential_manager` to the line 172 call WILL raise `NameError: name 'credential_manager' is not defined`.
   - Required fix: Move `credential_manager = CredentialManager()` to BEFORE line 172, then add the kwarg. Plan must explicitly state the reorder requirement.

2. **Missing `manager.credential_manager` property (BLOCKER)**
   - Phase 3 line 250 uses `credential_manager = manager.credential_manager`
   - Phase 2 adds `self._credential_manager` as private attr and adds `@property db_pool_manager` and `@property db_connection_repository` — but NEVER adds `@property credential_manager`
   - Phase 3 will raise `AttributeError: 'InstanceManager' object has no attribute 'credential_manager'` at first tool call
   - Required fix: Add `@property credential_manager` returning `self._credential_manager` in Phase 2, OR pass `credential_manager` as a 5th arg to `create_db_tools()`.

3. **Error sanitization regex incomplete (HIGH)**
   - `_sanitize_error()` regex `password=\S+` misses PostgreSQL's native error format `password "..."` (quoted)
   - PostgreSQL/asyncpg typically uses `password "mySecret"` not `password=mySecret`
   - This is a security gap in a tool specifically designed for safe DB access
   - Required fix: Add patterns for `password\s+"[^"]*"`, and consider broader approach (truncate to exception class name + first N chars)

### Verified OK
- CredentialManager class exists at daemon/sources/credentials.py with encrypt/decrypt methods ✅
- app.state.credential_manager set at api.py:378 ✅
- SQLModelSourceRepository.__init__(self, engine: Engine) — no cred_mgr param ✅ (pattern match confirmed)
- Encryption in router layer (routers/sources.py:155) ✅
- create_source_repository factory pattern exists ✅
- CATEGORY_MODULES registry at _tool_registry.py:184 ✅
- register_tool_category decorator exists ✅
- create_instance_tools(manager, instance_id, agent_id) at instance.py:439 ✅
- manager.engine property at manager.py:977 ✅
- shutdown() steps list at manager.py:2859-2871 ✅ (exact format match)
- cleanup() is sync at manager.py:2786 ✅
- Dependencies confirmed in pyproject.toml (asyncpg, cryptography, sqlmodel, psycopg) ✅
- Agent meta.json allow lists match plan exactly ✅
- SELECT guard multi-statement injection works correctly ✅
- create_all() ordering is internally consistent ✅

---

## Iteration 002 — 2026-06-16 (v4)

**Verdict: APPROVED**

### Previous Blockers — Verification

1. **api.py line ordering (BLOCKER 1)** — ✅ RESOLVED
   - D8 (decisions.md:353) now correctly states: "Reorder IS needed"
   - Phase 2 task 6 explicitly describes moving `CredentialManager()` from line 232 to before line 172
   - Verified against codebase: InstanceManager at api.py:172, CredentialManager at api.py:232 — reorder correctly described

2. **Missing @property credential_manager (BLOCKER 2)** — ✅ RESOLVED
   - Phase 2 task 5 now explicitly adds THREE properties: `db_pool_manager`, `db_connection_repository`, AND `credential_manager`
   - decisions.md:240 shows the property implementation
   - Phase 3 correctly references `manager.credential_manager` at the factory (decisions.md:250)

3. **Error sanitization regex (BLOCKER 3)** — ✅ RESOLVED
   - `_sanitize_error()` now handles 5 redaction patterns (phase2-plan.md:228-268):
     - DSN format: `postgresql://user:password@host`
     - `password=value` format
     - PG native quoted: `password "value"` (the missing pattern)
     - role/user + password quoted: `role "x" password "y"`
     - Final safety net for residual `user:password@host`
   - Phase 4 tests explicitly cover all formats including PG native quoted

### Independent Verification (Fresh Evaluation)

- Plan is internally consistent — D1-D8 align coherently across all 4 phases
- Approach is feasible: asyncpg confirmed in pyproject.toml, factory pattern matches existing codebase
- api.py reordering is safe: `CredentialManager.__init__` is pure (Fernet env var only, no I/O, no InstanceManager dependency)
- Pool singleton at InstanceManager level correctly prevents pool proliferation
- Repository correctly has NO credential_manager (matches source repository pattern)
- Module-level model import is correct — `SQLModel.metadata.create_all()` runs in `__init__` at line 531
- Pool disposal correctly in `shutdown()` steps list (async), not `cleanup()` (sync)
- SELECT guard with string-literal stripping is well-designed defense-in-depth

### Notes (Non-blocking)
- DSN URL-encoding of passwords with special characters could be handled during implementation (e.g., `urllib.parse.quote()`)
- Query timeout parameter could be passed through to asyncpg pool `command_timeout` for additional enforcement
- All non-blocking; no changes required for approval
