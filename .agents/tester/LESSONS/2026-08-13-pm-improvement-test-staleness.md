# PM System Improvement — Test-Staleness Patterns (2026-08-13)

## Summary
3 commits (50c68ed6, 867907a2, 6539f56b) introduced new features across MCP resilience, Plane MCP server, PM agent v2, and security gates. Testing found **8 test-staleness issues** across 3 test files — all caused by production code evolving without corresponding test updates. No production bugs found.

## Pattern 1: Production Feature Added Without Test Update

### Sources Circuit Breaker CR-4 (3 tests)
- **Root cause**: Commit `6539f56b` added HALF_OPEN concurrent probe flag to `daemon/sources/circuit_breaker.py`, but `tests/test_sources_circuit_breaker.py` still asserted pre-CR-4 behavior (both concurrent probes return True).
- **Also**: `tests/test_sources_registry.py` had 2 tests missing `source_type=None` param added to mapper signature.
- **Fix**: Updated test assertions to match new contract. Commit `c9ca95d7`.

### MCP Builtin Servers Blueprint G4 (2 tests)
- **Root cause**: Commit `cc812ae4` (blueprint feature) added `config.blueprint.embedding_model` read in `daemon/manager.py:824`, but the `mock_config` fixture in `tests/unit/test_builtin_mcp_servers.py` didn't mock the `blueprint` attribute. This caused 17 errors across 4 test classes.
- **Also**: `test_warmup_registers_enabled_builtin` had a patch scope issue — module-level import binding bypassed `patch()`.
- **Fix**: Added `config.blueprint` mock + fixed patch target. Commit `9308d961`.

### PM Agent v1→v2 (5 tests)
- **Root cause**: PM agent upgraded from v1 to v2 (`team_members: [] → ["leader"]`, `version: "1.0.0" → "2.0.0"`, new tools in allow/deny), but tests still asserted v1 contracts.
- **Fix**: Updated 5 assertions. Commit `eff691b6`.

## Lesson
When production code adds new fields, params, or behavioral changes:
1. **Grep test files** for old contract assertions before running
2. **Mock fixtures** must stay in sync with production attribute reads
3. **Patch targets** must match import binding style (module-level vs local)

## New Security Tests Added (15 tests)
CR-1 through CR-6 review items from deep review (commit `6539f56b`). 9 were already covered by Phase 4 tests; 15 new tests written for gaps. Commit `63067337`.

## Commits
| Commit | Files | Lines |
|--------|-------|-------|
| `eff691b6` | 1 | +9/-15 |
| `c9ca95d7` | 2 | ~15 net |
| `9308d961` | 1 | +12/-2 |
| `63067337` | 3 | +590 |
