# Phase 3 Quick Fixes

## Date: 2026-04-23

### Commit 77a4ad7 — Missing `Any` import in daemon/utils.py
- **File**: `daemon/utils.py` line 8
- **Root cause**: `validate_instance_mode` function had `dict[str, Any]` type annotation but `Any` wasn't imported from `typing`
- **Fix**: Added `Any` to the typing imports
- **Discovery**: Found during Phase 3 test writing (test_api_router_extraction.py)
- **Impact**: Minor — would only affect runtime type checking

### Commit 47d3e9e — Test fixtures + webhooks router consistency
- **Files**: 
  - `tests/test_api.py` (+12/-5)
  - `tests/test_agents_api.py` (+10/-7)
  - `tests/test_scheduler_api.py` (+5/-3)
  - `daemon/routers/webhooks.py` (+3/-2)
- **Root cause 1 (test fixtures)**: Phase 3 moved `manager` from module-level `daemon.api.manager` to `app.state.manager`. Test fixtures were still patching the old module-level variable, causing all API tests to fail.
  - Fix: Updated fixtures to set `app.state.manager` directly instead of patching the module
- **Root cause 2 (webhooks)**: `daemon/routers/webhooks.py` was calling `_source_repository.get_source_config()` with direct `await` while all other routers use `await asyncio.to_thread(...)` for consistency.
  - Fix: Updated to use `asyncio.to_thread()` pattern
- **Discovery**: Found during full unit test suite run
- **Impact**: Critical — 148 API tests would fail without fixture update
