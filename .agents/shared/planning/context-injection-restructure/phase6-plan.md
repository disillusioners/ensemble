# Phase 6: Backward Compatibility, Testing & Rollout

## Objective
Ensure existing agents continue to work during rollout via per-agent feature flag. Comprehensive test matrix covering all surfaces. Migration path and documentation.

## Coupling
- **Depends on**: Phases 1-5 (tight — all features must be complete)
- **Coupling type**: tight
- **Shared files with other phases**: All
- **Why this coupling**: This phase validates and wraps up all prior work

## Context
- Phases 1-5 completed: New context injection pipeline is functional
- Default mode is `system_prompt` (backward compat) until explicitly flipped
- ~15+ test files need updates (estimated 6-8 hours per reviewer S2)
- Need migration path for existing agents

## Tasks

### Backward Compatibility (3 tasks)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Verify `system_prompt` mode byte-identical | All agents with `context_injection_mode: system_prompt` (or no flag → default) must produce LLM inputs byte-identical to pre-refactor. Run diff test. | `tests/regression/test_legacy_agents.py` (new) |
| 2 | Document migration path | Update docs: how to flip `context_injection_mode` in meta.json. Explain the two modes. | `docs/context_injection_migration.md` (new) |
| 3 | Deprecation path for `context_injection: true` | Legacy flag maps to `human_messages` mode. Add deprecation warning log. | `daemon/loader.py` |

### Test Updates — Directly Affected (5 tasks, ~4 hours)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 4 | Update `test_auto_load_skills.py` | ~19 tests. Add mode param. Test `system_prompt` (appender active) and `human_messages` (appender dormant). | `tests/unit/test_auto_load_skills.py` |
| 5 | Update `test_shared_context_injection.py` + `test_shared_context_prompt_injection.py` | ~14 + ~4 tests. Add mode param. Verify prompt-injection defense works in both modes. | `tests/unit/test_shared_context_injection.py`, `tests/unit/test_shared_context_prompt_injection.py` |
| 6 | Rewrite `test_shared_context_message_body_injection.py` | ~10 tests. String prepending is REMOVED. Convert to test the HumanMessage builder output instead. | `tests/unit/test_shared_context_message_body_injection.py` |
| 7 | Update skill injection tests | `test_send_message_load_skill.py` (6 tests) + related. Assert `[SYSTEM CONTEXT: Skills]` prefix instead of `[System Inject]`. | `tests/tools/test_send_message_load_skill.py` |
| 8 | Update `test_auto_load_metrics.py` | ~5 tests. Tracking behavior changes when appender is dormant. | `tests/unit/test_auto_load_metrics.py` |

### Test Updates — Integration Affected (3 tasks, ~2-3 hours)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 9 | Update LLM input/response shape tests | Tests touching `full_messages`, `agent_node`, message construction. Verify context messages appear in LLM input but NOT checkpoint. | `tests/integration/test_shared_context_e2e.py`, `tests/services/test_context_usage_emission.py` |
| 10 | Update skill lifecycle tests | `test_skill_evolution_e2e.py` (24 tests), `test_skill_cross_phase_flow_c.py` (12 tests). Touches injection path. | `tests/integration/test_skill_*.py` |
| 11 | Update message branching tests | `test_option_b_message_branching.py` (15 tests). Touches injection path. | `tests/services/test_option_b_message_branching.py` |

### Cross-Cutting Tests (2 tasks, ~1-2 hours)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 12 | DB matrix test (SQLite + PostgreSQL) | Run full test suite on both databases. Verify no SQLite-only or PostgreSQL-only assumptions. | CI config |
| 13 | Instance hierarchy test | Test root + child instance context resolution. Child inherits parent's context_key. | `tests/integration/test_context_hierarchy.py` (new) |

### Rollout (2 tasks)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 14 | Canary rollout | Flip `context_injection_mode: human_messages` for ONE agent. Monitor 1-3 days: checkpoint size, freshness, no regressions. | `agents/<agent>/meta.json` |
| 15 | Full rollout + KB update | After canary, batch rollout. Remove legacy flag. Update KB. | `docs/`, `experience()` KB call |

## Key Files
- `tests/regression/test_legacy_agents.py` — NEW: byte-identical regression test
- `tests/unit/test_auto_load_skills.py` — UPDATED: dual-mode
- `tests/unit/test_shared_context_injection.py` — UPDATED: dual-mode
- `tests/unit/test_shared_context_message_body_injection.py` — REWRITTEN: test builder
- `tests/unit/test_shared_context_prompt_injection.py` — UPDATED: dual-mode + defense instruction
- `tests/tools/test_send_message_load_skill.py` — UPDATED: new prefix
- `tests/integration/test_skill_evolution_e2e.py` — UPDATED
- `tests/integration/test_skill_cross_phase_flow_c.py` — UPDATED
- `tests/services/test_option_b_message_branching.py` — UPDATED
- `tests/integration/test_context_hierarchy.py` — NEW
- `docs/context_injection_migration.md` — NEW
- `daemon/loader.py` — MODIFIED: deprecation warning

## Canary Monitoring Checklist
- [ ] Checkpoint DB size (context not persisted → should be smaller)
- [ ] Context freshness (mid-session changes reflected)
- [ ] No error rate increase
- [ ] LLM token usage (context per-turn but ephemeral — net neutral)
- [ ] GET /messages displays correctly in frontend
- [ ] Skill injection works (both auto and `<meta>`)
- [ ] Compaction retry correctly re-appends context

## Constraints
- `system_prompt` mode MUST be byte-identical to pre-refactor behavior
- No test file DELETED (only updated/rewritten)
- PostgreSQL and SQLite must both pass
- Canary agent should be non-critical initially

## Deliverables
- [ ] `system_prompt` mode regression test passes (byte-identical)
- [ ] All ~15 affected test files updated
- [ ] Both SQLite and PostgreSQL pass
- [ ] Migration documentation written
- [ ] Canary agent flipped and monitored
- [ ] KB updated with new architecture pattern
- [ ] Deprecation path documented
