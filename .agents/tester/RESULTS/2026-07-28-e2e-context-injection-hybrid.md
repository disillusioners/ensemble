# E2E Test Report: Hybrid Context Injection Verification

**Date:** 2026-07-28
**Branch:** `latest` (includes `feature/context-injection-restructure`)
**Feature commit:** `d6fb6461` — feat: hybrid context injection — project context persistent, skills ephemeral
**Test commit:** `2c9c283a` — test(e2e): fix pack cd path, stale project_id, slow-turn prompt
**Daemon:** v0.9.8 on localhost:8079 (PostgreSQL)

## Summary

- **Total scenarios:** 2 | **Passed:** 2 | **Failed:** 0 | **Errors:** 0
- **Overall Status:** ✅ READY — hybrid context injection fix verified

## Scope Decision

> Targeted E2E verification of the hybrid context injection fix. Change set is focused (context injection logic in `graph.py`, `context_messages.py`, `agent_node`). Scoped to 1 E2E test pack with 2 scenarios. Full suite not warranted — unit/feature tests already validated in `RESULTS/2026-07-28-context-injection-restructure.md` (209 feature tests pass).

## Verification Results

### Scenario 1: Project Context Persistent — ✅ PASS (~270s)

**Instance:** `developer` with project_id
**Messages:** 2 real-LLM turns

| Check | Result | Evidence |
|-------|--------|----------|
| Turn 1: `[SYSTEM CONTEXT: Related Project]` exists | ✅ PASS | `context_kind=project, is_synthetic=true` message found |
| Turn 1: project context BEFORE user message | ✅ PASS | Correct ordering — context at earlier index |
| Turn 1: content has `[SYSTEM CONTEXT:` tag | ✅ PASS | Content contains expected tag |
| Turn 2: project context appears EXACTLY ONCE | ✅ PASS | 1 project context message after 2 turns (not duplicated) |

**Daemon log confirmation:**
```
[Hybrid] Prepended 1 persistent context message(s) to graph_input for ... (project_injected=False)
```

### Scenario 2: Skills Ephemeral — ✅ PASS (~67s)

**Instance:** `tester` with project_id (has `skill_injection: true`)
**Messages:** 2 real-LLM turns

| Check | Result | Evidence |
|-------|--------|----------|
| Turn 1: `context_kind=skills` message present | ✅ PASS | 1 skill injected (`unit-test` skill) |
| Turn 2: `context_kind=skills` message present AGAIN | ✅ PASS | Re-injected (`integration-test` skill) |
| Project context still appears once after 2 turns | ✅ PASS | Consistent with persistence |

**Daemon log confirmation:**
```
[ContextSlot] Injected 1 ephemeral context message(s) for ... before LLM call
```

## Quick Fixes Applied (test code only)

Commit `2c9c283a` — 3 test-code bugs fixed by worker:

1. **Pack `cd` path bug** (1 line): `cd "${SCRIPT_DIR}/.."` → `cd "${SCRIPT_DIR}/../.."` — script entered `test/` instead of repo root
2. **Stale hardcoded `PROJECT_ID`** (~12 lines): Replaced with dynamic `_resolve_project_id()` lookup by name
3. **Slow-turn prompt** (1 line): Changed action-triggering "Run the unit tests now" → read-only "Which one is most relevant to integration tests?"
4. Added `-s` flag to pack script for diagnostic output

## Architecture Verification Summary

The hybrid context injection fix works as designed:

| Context Type | Lifecycle | Behavior | Verified |
|-------------|-----------|----------|----------|
| Project context (`Related Project`) | **Persistent** | Injected once on first message; survives in checkpoint; NOT duplicated on subsequent turns | ✅ |
| Skills (`Skills`) | **Ephemeral** | Re-injected every turn; never checkpointed; allows dynamic skill changes without invalidating prefix cache | ✅ |

This enables prefix-cache optimization: `[SystemMessage + persistent context (stable)] + [ephemeral skills] + [conversation history]` — the first N messages never change between turns.

## Warnings / Follow-ups

- **Pack timeout structural issue:** Combined pack (~337s for both scenarios) exceeds the 300s cap. Scenario 1 alone takes ~270s. Worker verified scenarios individually. **Recommended:** split into `e2e_context_injection_project.sh` + `e2e_context_injection_skills.sh`.
- **Skills are query-dependent:** Scenario 2 passed, but skill injection depends on BM25/embedding search matching the query. Not all queries will trigger skills.

## Documentation Updated

- [x] RESULTS/2026-07-28-e2e-context-injection-hybrid.md — this report
- [ ] PACKS.md — needs new entry + pack split (follow-up)
- [x] LESSONS/2026-07-28-context-injection-e2e-findings.md — test architecture findings

## Code Changes Summary

- `tests/e2e/test_context_injection_hybrid.py` (new, 311 lines) — E2E test with 2 scenarios
- `test/packs/e2e_context_injection_test.sh` (new, 22 lines) — pack script
- Commit: `2c9c283a`
