# Plan Tracking: MCP STDIO Server Warm-Up Pool

## Iteration 001 — 2026-05-19 16:29
**Verdict**: REJECTED
**Approver**: Independent Approver (council-assisted)

### Blocking Issues

1. **`send_request({"method": "ping"})` — Wrong API** (phase1-plan.md, phase2-plan.md)
   - Expected: Use `ClientSession.send_ping()` which exists in the MCP SDK
   - Found: Plan uses `conn.session.send_request({"method": "ping"})` in health check (phase1-plan.md:133) and liveness probe (phase2-plan.md:131)
   - `send_request()` requires a typed request object and `result_type` parameter, not a dict
   - Appears in: Decision 6 (decisions.md:41), health_check implementation (phase1-plan.md:133), _probe_connection (phase2-plan.md:131)
   - Fix: Replace all `send_request({"method": "ping"})` with `send_ping()`

2. **`registry.get_all().items()` — Runtime AttributeError** (phase3-plan.md)
   - Expected: Iterate a list of BuiltinServerDefinition, or use `registry.definitions.items()` for dict
   - Found: Phase 3 startup code uses `for name, definition in registry.get_all().items():` (phase3-plan.md:73)
   - `get_all()` returns `list[BuiltinServerDefinition]`, NOT a dict — `.items()` will raise `AttributeError`
   - Fix: Either use `registry.definitions.items()` or iterate as `for definition in registry.get_all()` and access `definition.name`

## Iteration 002 — 2026-05-19 17:36
**Verdict**: REJECTED
**Approver**: Independent Approver (council-assisted)

### Previous Issues — Status
1. ✅ `send_request` → `send_ping()` — FIXED in rev2
2. ✅ `registry.get_all().items()` → `for definition in registry.get_all()` — FIXED in rev2

### Blocking Issues

1. **`get_builtin_server_registry()` — Wrong Function Name** (phase2-plan.md:143-144, phase3-plan.md:70)
   - Expected: `get_registry()` from `daemon.mcp.builtin_servers` — verified at `__init__.py:52`
   - Found: Phase 2's `_is_builtin_stdio()` imports and calls `get_builtin_server_registry()` (phase2-plan.md:143-144); Phase 3's `_init_warmup_pool()` calls `get_builtin_server_registry()` (phase3-plan.md:70)
   - This function does not exist — will cause `ImportError` at runtime
   - Fix: Replace all `get_builtin_server_registry()` with `get_registry()`

### Notes (Non-Blocking)
- Lock description vs implementation: Decision 8 and risk table (plan-overview.md:106) state that `acquire()` callers "wait for lock" during health checks, but the actual `acquire()` implementation (phase1-plan.md:91-103) uses `get_nowait()` with no lock. During health checks, `acquire()` will return `None` (not block). This is safe (cold-start fallback works) but the risk table overstates the mitigation. The actual lock purpose is mutual exclusion between `health_check()` and `_replenish()`, not acquire protection.
- Minor path typo in Phase 1 Task 6: `daemon/mcp_warmup_pool.py` (missing `/` after `mcp`) vs correct `daemon/mcp/warmup_pool.py` used everywhere else.

## Iteration 003 — 2026-05-19 16:52
**Verdict**: APPROVED
**Approver**: Independent Approver (council-assisted, 2 sequential sessions)

### Previous Issues — Status
1. ✅ `send_request` → `send_ping()` — Confirmed FIXED
2. ✅ `registry.get_all().items()` → proper iteration — Confirmed FIXED
3. ✅ `get_builtin_server_registry()` → `get_registry()` — Confirmed FIXED
4. ✅ Path typo in Phase 1 Task 6 — Confirmed FIXED

### Verification Summary
- **API Correctness**: 11/11 verified against codebase (council session 1)
- **Integration Correctness**: 2/2 — Phase 2 split-server approach is structurally compatible
- **Shutdown Correctness**: Verified — drain step insertable before close_all_connections
- **Config Schema**: Verified — McpPoolConfig follows existing pattern
- **Internal Consistency**: PASS — all method names, data structures, and dependency chains consistent
- **Completeness**: 8/8 success criteria mapped, 9/9 risks mitigated, 11/11 decisions implemented
- **Safety**: PASS — orphaned subprocesses handled via tracked tasks + cancel in drain(); stale connections handled via liveness probe
- **Edge Cases**: PASS — all edge cases have explicit handling or acceptable degradation

### Notes (Non-Blocking)
1. `start_health_check()` is listed as Phase 3 Task 5 (not Phase 1 deliverable) — this is correct sequencing but could be clearer in Phase 1's deliverables list
2. `asyncio.CancelledError` not caught in `_create_pooled_connection` — in Python 3.9+, CancelledError is BaseException, not Exception. If a task is cancelled mid-subprocess-spawn, the current `except Exception` won't clean up. Low risk (only during daemon shutdown), but worth a one-line fix during implementation
3. Lock description vs acquire behavior: `acquire()` uses `get_nowait()` with no lock, so during health checks, acquire returns None (doesn't block). This is safe but the risk table wording ("acquire() callers wait for lock") is slightly misleading
