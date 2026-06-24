# Phase 4: E2E Test Gating & Documentation

## Objective
Ensure E2E tests are properly gated so they ONLY run when explicitly required (big changes / explicit request), document the correct invocation process in ensure.md, clean up dead `--run-integration` references, and document known test pollution issues.

## Coupling
- **Depends on**: Phase 1 (integration marker audit), Phase 3 (xdist usage to document)
- **Coupling type**: loose — Phase 4 documents markers and usage patterns from Phases 1 and 3
- **Shared files with other phases**: `ensure.md` (Phase 3 defers ALL its ensure.md changes here — W2)
- **Shared APIs/interfaces**: none
- **Why this coupling**: Phase 3 explicitly defers its xdist/parallel-run ensure.md notes to Phase 4. Phase 4 is the sole modifier of ensure.md.

## Context
**User constraint**: "E2E tests in ensure.md should ONLY run when big change, explicitly required — they should NOT be part of the default test run."

The current `ensure.md` (at `.agents/tester/rules/ensure.md`) has E2E tests listed as "Critical" items that must pass. This creates pressure to always run them. The user wants E2E tests to be **opt-in only**, clearly documented as "run when making big changes or when explicitly requested."

Additionally, the `--run-integration` flag referenced in several test docstrings is a **dead reference** — it's not registered in pyproject.toml. Documentation should use the correct invocation. Phase 3's xdist parallel-run instructions are also deferred to this phase.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Restructure ensure.md E2E section | Move E2E tests from "Critical" to a new **"E2E Tests (Run Only On Big Changes)"** section. Add clear header explaining: "These tests require a running daemon and real LLM calls. Run ONLY when: (a) making significant architectural changes, (b) before releases, (c) when explicitly requested. They are NOT part of the default test run." | `.agents/tester/rules/ensure.md` |
| 2 | Document correct test invocation commands | Document three tiers: 1. **Default (fast)**: `python -m pytest tests/ -x --tb=short -q` 2. **Parallel (recommended)**: `python -m pytest tests/ -n auto -m 'not postgres' -q` 3. **With integration tests**: `python -m pytest --override-ini="addopts=" -m integration -v` 4. **E2E workflows (needs daemon)**: `./dev.sh &` then `python -m pytest tests/e2e/test_e2e_workflows.py -v -m integration` | `.agents/tester/rules/ensure.md` |
| 3 | Add E2E test run prerequisites | Document that E2E tests require: `OPENAI_API_KEY` set in `.env`, daemon running via `./dev.sh`, valid LLM credits/budget. Note cost implications. | `.agents/tester/rules/ensure.md` |
| 4 | Add Phase 3's deferred xdist documentation | Document the parallel test execution option that Phase 3 installed. Include the postgres exclusion caveat (`-m 'not postgres'`). Add a "Performance" section with the recommended parallel command. | `.agents/tester/rules/ensure.md` |
| 5 | Clean up dead `--run-integration` references (W3 expanded) | Replace all `--run-integration` references with correct commands. Complete list of files: - `tests/integration/test_instance_title_e2e.py` (L15) - `tests/integration/test_completion_report.py` (L14) - `tests/integration/test_agent_bootstrap.py` (L7, L14) - `tests/integration/test_message_queue_e2e.py` (L13, L16) - `tests/opencode/test_integration.py` (L11) | 5 test files (see list) |
| 6 | Add marker reference to ensure.md | Add a "Test Markers" reference section: - `integration`: Tests requiring live server / real LLM calls (excluded by default) - `postgres`: Tests requiring live PostgreSQL (excluded by default) - Default run: `not integration and not postgres` | `.agents/tester/rules/ensure.md` |
| 7 | Document known test pollution issues (W9) | Add a "Known Issues" section to ensure.md documenting: 1. **test_message_queue_e2e.py sys.modules pollution**: Lines 50-66 mutate `sys.modules` at module import time. If this file is collected alongside non-integration tests, it breaks langgraph mocks for the entire session. Currently mitigated by integration marker gating. 2. **test_api_router_extraction.py ordering pollution**: Shows cascading errors when run alongside other tests. Root cause TBD. Not a blocker for default runs. | `.agents/tester/rules/ensure.md` |
| 8 | Verify gating completeness | Run `python -m pytest tests/ --collect-only -q 2>&1 | grep -c "integration"` to confirm zero integration tests collected in default run. | — |

## Proposed ensure.md Structure

```markdown
## Critical (must pass before merge)
- [ ] Default test suite passes → `python -m pytest tests/ -x --tb=short -q`
- [ ] Deadlock fix tests pass → `python -m pytest tests/test_deadlock_fix.py -v`
- [ ] No sync DB calls on asyncio event loop
- [ ] `dev.sh` includes `--timeout-graceful-shutdown 10`

## Performance (optional but recommended)
- [ ] Parallel test suite passes → `python -m pytest tests/ -n auto -m 'not postgres' -q`

## E2E Tests (Run ONLY On Big Changes / Releases / Explicit Request)
> ⚠️ These tests require a running daemon + real LLM calls. They cost money and time.
> Run ONLY when: making significant architectural changes, before releases, or when explicitly asked.

Prerequisites: `OPENAI_API_KEY` in `.env`, daemon running via `./dev.sh`

- [ ] E2E: Happy path → `pytest tests/e2e/test_e2e_workflows.py::test_parent_child_workflow_happy_path -v -m integration`
- [ ] E2E: Pause/Resume → `pytest tests/e2e/test_e2e_workflows.py::test_pause_after_spawn_then_resume -v -m integration`
- [ ] E2E: Terminate/Revive → `pytest tests/e2e/test_e2e_workflows.py::test_terminate_after_spawn_then_revive -v -m integration`
- [ ] E2E: Wave spawn + defer queue → `pytest tests/e2e/test_e2e_workflows.py::test_wave_spawn_with_defer_queue -v -m integration`

## Test Markers Reference
| Marker | Purpose | Default |
|--------|---------|---------|
| `integration` | Live server / real LLM calls | **Excluded** |
| `postgres` | Live PostgreSQL required | **Excluded** |

Run specific markers: `pytest --override-ini="addopts=" -m integration`

## Known Issues
1. **test_message_queue_e2e.py sys.modules pollution**: Mutates `sys.modules` at import time.
   Mitigated by integration marker gating. Should be refactored to session-scoped fixture.
2. **test_api_router_extraction.py ordering pollution**: Cascading errors when run with other tests.
   Root cause TBD.
```

## Key Files
- `.agents/tester/rules/ensure.md` — primary test validation documentation (sole modifier)
- `tests/integration/test_instance_title_e2e.py` — dead `--run-integration` reference (L15)
- `tests/integration/test_completion_report.py` — dead `--run-integration` reference (L14)
- `tests/integration/test_agent_bootstrap.py` — dead `--run-integration` references (L7, L14)
- `tests/integration/test_message_queue_e2e.py` — dead `--run-integration` references (L13, L16)
- `tests/opencode/test_integration.py` — dead `--run-integration` reference (L11)

## Constraints
- **E2E tests must NOT be in the default test run** — this is the user's critical constraint
- The ensure.md restructure must make it clear that E2E is opt-in, not default
- Do not remove E2E test instructions entirely — just reclassify them
- Keep the "Critical" section focused on fast, default-suite validations
- Dead `--run-integration` references should be replaced with the actual working command
- **This phase is the sole modifier of ensure.md** (Phase 3 defers its ensure.md notes here)

## Deliverables
- [ ] ensure.md restructured with E2E in separate "opt-in" section
- [ ] Parallel test execution documented (from Phase 3)
- [ ] Correct E2E invocation commands documented
- [ ] Dead `--run-integration` references cleaned up (all 5 files)
- [ ] Test markers reference added
- [ ] Known issues documented (W9)
- [ ] Default run command prominently documented
- [ ] Zero integration tests collected in default `pytest tests/` run
