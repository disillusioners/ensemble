# Lesson: Skill Evolution Cross-Phase Integration Testing

**Date**: 2026-07-11
**Branch**: feature/skill-evolution

## Key Findings

### 1. Cross-Phase Test Isolation Issues
When running integration tests alongside unit tests, SQLAlchemy state can leak. The Flow B tests needed `pytestmark = pytest.mark.integration` to be properly excluded from default test runs.

**Fix**: Added `pytestmark = pytest.mark.integration` to `tests/integration/test_skill_cross_phase_flow_b.py`

### 2. Stale Phase 2 Assertions
Phase 4 changes removed the Phase 2 stub for `skill_feedback`, but 3 unit tests still asserted the old stub behavior.

**Fix**: Updated test assertions to match current soft-fail behavior (commit `9436caad`)

### 3. Frontend Missing Material Imports
`SkillDetailComponent` was missing `MatFormFieldModule` and `MatInputModule` imports, causing Angular build failure.

**Fix**: Added imports to component (commit `188e889b`)

### 4. Help Tool Mock Registry
When `registry.get_resolved` was mocked for tool filtering tests, the security tests' fixtures weren't updated, causing denied tools to appear as available.

**Fix**: Added `mock_registry.get_resolved.return_value = mock_agent_meta` to both TestToolHelpSecurity fixtures (commit `f3b6ca08`)

### 5. API/Frontend Contract Issues
The ensure.md validation session found and fixed API/frontend contract mismatches (duration key, response wrapping).

**Fix**: Commit `7596fcce`

## Patterns for Future Testing

- Always run integration tests with `-m integration` marker
- When mocking registry functions, check ALL test classes that use the same fixtures
- Cross-phase integration tests should use real SQLite in-memory DB with real repositories, only mocking external APIs (LLM, embeddings)
- Test isolation is critical — each test should create its own engine, not share across tests
