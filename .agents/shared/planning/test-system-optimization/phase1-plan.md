# Phase 1: Suite Runner Fix

## Objective
Make the default test suite runnable without hanging by adding integration markers to unmarked integration test files, installing pytest-timeout as a global safety net, and pre-allocating ALL pyproject.toml changes so Phase 3 can append without conflicts.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: —
- **Shared files with other phases**: `pyproject.toml` (Phase 3 also modifies — see Sequencing Constraint below)
- **Shared APIs/interfaces**: none
- **Why this coupling**: Phase 3 adds pytest-xdist config to the same `pyproject.toml` section. To avoid merge conflicts, Phase 1 pre-allocates ALL dependency lines and config keys.

### ⚠️ Sequencing Constraint (pyproject.toml)
Phase 1 and Phase 3 both modify `pyproject.toml`. **Phase 1 must complete first.** Phase 1 pre-allocates:
- `pytest-timeout` dependency → Phase 3 appends `pytest-xdist` after it
- `timeout` + `timeout_method` config keys → Phase 3 adds no new keys
This ensures Phase 3 only appends lines, never modifies existing ones.

## Context
The default test suite hangs forever because `test_instance_title_e2e.py` makes real OpenAI API calls and only has a `skipif(OPENAI_API_KEY)` guard — which does NOT trigger when `OPENAI_API_KEY` is set in the dev environment. The file lacks `@pytest.mark.integration`, so `addopts = "-m 'not integration and not postgres'"` doesn't exclude it. 6 files total have this problem. Additionally, `pytest-timeout` is not installed, so there's no global safety net to kill hung tests.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Install pytest-timeout | Add `pytest-timeout>=2.3` to `[project.optional-dependencies] dev` in pyproject.toml. Run `uv sync` or `pip install -e ".[dev]"`. | `pyproject.toml` |
| 2 | Configure pytest-timeout | Add `timeout = 30` and `timeout_method = "thread"` to `[tool.pytest.ini_options]` in pyproject.toml. This kills any test that runs >30s. | `pyproject.toml` |
| 3 | Add integration marker to test_instance_title_e2e.py | Change `pytestmark = pytest.mark.skipif(...)` to `pytestmark = [pytest.mark.integration, pytest.mark.skipif(...)]` at line 54. | `tests/integration/test_instance_title_e2e.py` |
| 4 | Add integration marker to test_completion_report.py | Change `pytestmark = pytest.mark.skipif(...)` to `pytestmark = [pytest.mark.integration, pytest.mark.skipif(...)]` at line 37. | `tests/integration/test_completion_report.py` |
| 5 | Add integration marker to test_inner_soul.py | Change `pytestmark = pytest.mark.skipif(...)` to `pytestmark = [pytest.mark.integration, pytest.mark.skipif(...)]` at line 26. | `tests/integration/test_inner_soul.py` |
| 6 | Add integration marker to test_agent_bootstrap.py | Change `pytestmark = pytest.mark.skipif(...)` to `pytestmark = [pytest.mark.integration, pytest.mark.skipif(...)]` at line 15. | `tests/integration/test_agent_bootstrap.py` |
| 7 | Add integration marker to test_migration_e2e.py | Add `pytest.mark.integration` to the pytestmark list at line 124. | `tests/e2e/test_migration_e2e.py` |
| 8 | Add integration marker to test_mcp_kb_e2e.py | Add `pytest.mark.integration` to the pytestmark list at line 19. | `tests/e2e/test_mcp_kb_e2e.py` |
| 9 | Verify with test run | Run `python -m pytest tests/ -x --tb=short -q --collect-only` to confirm no integration tests are collected. Then run a quick subset to confirm no hang. | — |

## Key Files
- `pyproject.toml` — pytest configuration, dependencies
- `tests/integration/test_instance_title_e2e.py` — hang culprit (real OpenAI calls)
- `tests/integration/test_completion_report.py` — real LLM calls
- `tests/integration/test_inner_soul.py` — real LLM calls
- `tests/integration/test_agent_bootstrap.py` — real LLM calls
- `tests/e2e/test_migration_e2e.py` — migration E2E, needs live env
- `tests/e2e/test_mcp_kb_e2e.py` — MCP KB E2E, needs live env

## Files Already Correctly Marked (DO NOT TOUCH)
- `tests/integration/test_message_queue_e2e.py` — ✅ has `[integration, skipif]`
- `tests/integration/test_migration_e2e_comprehensive.py` — ✅ has `[skipif, integration]`
- `tests/e2e/test_e2e_workflows.py` — ✅ has `[integration, skipif(daemon_running)]`

## Files That Don't Need Markers (Pure Unit/Mocked)
- `tests/integration/test_compaction_e2e.py` — fully mocked LLM, runs fine by default
- `tests/integration/test_mcp_lifecycle.py` — fully mocked, runs by default
- `tests/integration/test_mock_provider_reasoning_content.py` — local mock HTTP server, runs by default
- `tests/integration/test_multi_turn_resume.py` — uses mocks, runs by default
- `tests/integration/test_dlq_project_normalization.py` — pure DB tests
- `tests/integration/test_job_create.py` — pure DB tests
- `tests/integration/test_migration.py` — migration tests, no external deps

## Constraints
- Do NOT remove the existing `skipif` conditions — they serve as a secondary gate
- The integration marker must be added to the existing `pytestmark` list, not replace it
- `timeout_method = "thread"` is required for asyncio tests (signal-based timeout doesn't work with asyncio)
- **Pre-allocate ALL pyproject.toml changes in this phase** so Phase 3 can append without conflicts

## Deliverables
- [ ] `pytest-timeout` installed and configured with 30s timeout
- [ ] All 6 integration test files have `@pytest.mark.integration` marker
- [ ] `python -m pytest tests/ -x --tb=short -q` completes without hanging
- [ ] Integration tests only collected with `pytest -m integration`
- [ ] pyproject.toml changes pre-allocated for Phase 3 append
