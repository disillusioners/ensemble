# Context Injection Restructure — Testing Summary
Date: 2026-07-28
Branch: `feature/context-injection-restructure` @ `2de4af3a`

## What Was Tested
Context injection restructure — a multi-phase refactor that changes how context is delivered to the LLM:
- Old: context baked into frozen system prompt
- New: context delivered as per-turn `[SYSTEM CONTEXT: ...]` tagged HumanMessages, injected into local `full_messages` inside `agent_node` (never entering checkpoint state = ephemeral)

## Key Insight: Ephemeral Pattern via LangGraph
LangGraph's `add_messages` reducer is APPEND-ONLY — you cannot filter at return.
The correct pattern: inject into LOCAL `full_messages` variable inside `agent_node`,
then don't include context messages in the return dict → they're never checkpointed.

This is a RAM-queue injection pattern, not a state-filtering pattern.

## Testing Approach
- **Blast radius: HIGH** — cross-module architecture refactor (11 source files, 6 modules)
- **9 packs dispatched in parallel** (1 worker per pack, skill `test-pack-execution`)
- All packs completed in < 30s each (well under 5-min cap)
- Total new tests: 209 (12 new test files from developer)
- 1 quick fix needed (commit `6e44157f` in context_messages unit tests)

## Critical Paths Verified
1. **Ephemerality** ✅ — context msgs NOT in checkpoint, ARE in local full_messages
2. **Backward compat** ✅ — system_prompt mode byte-identical to pre-refactor
3. **C1 loop-breaker fix** ✅ — repair SystemMessage NOT dropped when context injection active (commit `2de4af3a` correct)
4. **B3 retry safety** ✅ — skills survive LLM retry calls
5. **Injection order** ✅ — SystemMessage → [SYSTEM CONTEXT: Project] → [Shared Context] → [Skills] → history → user msg
6. **GET /messages API** ✅ — is_synthetic + context_kind fields, read-only

## Regression Results
- Core daemon sweep: 694 passed, 41 pre-existing failures (0 NEW)
- All 41 pre-existing failures are the documented SQLite migration bug + test isolation issues
- The refactor introduced zero regressions

## Pack Scripts Created
7 new pack scripts registered in PACKS.md:
- `context_messages_unit_test.sh` — unit (50+7 tests)
- `context_skills_unit_test.sh` — unit (22+19+11 tests)
- `context_graph_integration_test.sh` — integration CRITICAL (20 tests)
- `context_injection_integration_test.sh` — integration (14 tests)
- `legacy_agents_regression_test.sh` — regression (21 tests)
- `api_messages_integration_test.sh` — integration (9 tests)
- `context_freshness_hierarchy_test.sh` — integration+perf (14 tests)
