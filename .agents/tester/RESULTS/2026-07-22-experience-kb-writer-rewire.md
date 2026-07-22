# Test Report: experience tool rewire to kb-writer
Date: 2026-07-22
Branch: `feature/experience-use-kb-writer`
Commits: f932c673 (rewire), 258da1d4 (frontend exclusions)

## Summary
- **Total tests run**: 4,637 (6 targeted files + broader unit sweep)
- **Passed**: 4,622 | **Failed**: 15 (all PRE-EXISTING) | **Skipped**: 33
- **New regressions**: 0
- **Quick fixes applied**: 0 (none needed)
- **Quarantined**: N/A (no QUARANTINE.md exists)

## Scope Decision
> Change touches 5 files across 2 modules (knowledge tools + frontend notification exclusion lists). The `experience` tool spawn target changed from `experiencer` to `kb-writer`. Moderate blast radius — touches agent spawning/filtering, so related + regression checks were warranted. Full suite (E2E/integration) NOT warranted. Ran 6 targeted test files + broader `tests/unit/` sweep + static code verification.

## Per-File Results

### Directly Affected
| Test File | Result | Tests | Runtime |
|-----------|--------|-------|---------|
| `tests/unit/tools/test_knowledge_tools.py` | ✅ PASS | 110/110 | 3.83s |
| `tests/unit/services/test_invoked_as_tool.py` | ✅ PASS | 14/14 | 0.90s |

### Related
| Test File | Result | Tests | Runtime |
|-----------|--------|-------|---------|
| `tests/unit/test_kb_writer_tools.py` | ✅ PASS | 23/23 | 0.80s |
| `tests/unit/test_hide_kb_instances.py` | ✅ PASS | 15/15 | 1.46s |
| `tests/test_registry.py` | ✅ PASS | 48/48 | 0.70s |
| `tests/test_tool_filter.py` | ✅ PASS | 53/53 | 0.90s |

### Broader Regression Sweep
| Scope | Result | Tests | Runtime |
|-------|--------|-------|---------|
| `tests/unit/` (full directory) | ✅ PASS (0 new regressions) | 4378 passed, 15 pre-existing fail, 33 skipped | 101.12s |

## Static Verification Results

### Verification 1: No dangling "experiencer" spawn references — ✅ PASS
- `grep -rn 'experiencer' daemon/tools/knowledge_tools.py daemon/mcp/kb_server.py` → **no matches**
- `knowledge_tools.py:384` spawns via `agent_id="kb-writer"` (confirmed)
- `kb_server.py:362` calls `_enqueue_experience_job` → spawns `kb-writer` (confirmed)
- Remaining `"experiencer"` references are only in: `KB_AGENT_IDS` frozenset (instance filtering), docstrings, and `utils.py:512` example string — none are spawn paths

### Verification 2: Experiencer agent still defined & discoverable — ✅ PASS
- `agents/experiencer/meta.json` exists (id="experiencer", version 1.1.0)
- `AgentRegistry.get("experiencer")` returns valid agent with tools.allow=['rag','help','time','mcp','context','shared_context']
- `kb-writer` also discoverable with restricted tools.allow=['rag_insert_text','tool_help','time']

## Pre-Existing Failures (NOT regressions — all 15)

| # | Test File | Category | Root Cause |
|---|-----------|----------|------------|
| 1 | test_builtin_mcp_servers | WebFetch/MCP | `MCP_DISABLE_BUILT_IN_WEBFETCH` env var set |
| 2-7 | test_coder_developer_migration (6) | Coder migration / SQLite-PG | Migration file not found / `DROP CONSTRAINT IF EXISTS` SQLite syntax |
| 8-10 | test_gaia_agent (3) | Stale snapshot | tools.allow now includes `"proc"` (ancestor commit 2e5861fd) |
| 11 | test_mcp_cold_load_race | SQLite/PG | `DROP CONSTRAINT IF EXISTS` not valid in SQLite |
| 12 | test_wanderer_agent (tools_allow) | Stale test | Category list changed after proc + experience removal (ancestors) |
| 13 | test_wanderer_agent (soul) | Stale test | soul.md no longer mentions "experience" (ancestor a813454e) |
| 14-15 | test_webfetch_builtin (2) | WebFetch/MCP | `MCP_DISABLE_BUILT_IN_WEBFETCH` env var + stale flag |

All 15 failures are in test files NOT touched by this branch (verified via `git diff --name-only`).

## Key Tests Confirmed (experience → kb-writer rewiring)

From `test_knowledge_tools.py`:
- ✅ `test_experience_uses_kb_writer_agent` — spawn target is kb-writer
- ✅ `test_experience_sends_correct_message` — correct message format
- ✅ `test_experience_returns_immediately` — fire-and-forget pattern
- ✅ Full `TestExperienceAutoSave` suite
- ✅ Full `TestExperienceJobEnqueue` suite

From `test_invoked_as_tool.py`:
- ✅ `test_experience_passes_invoked_as_tool_true` — invoked_as_tool flag propagation
- ✅ `test_full_experience_flow_with_invoked_as_tool` — end-to-end flag flow
- ✅ `test_experience_without_rag_still_returns_error` — error handling

From `test_registry.py`:
- ✅ Both `kb-writer` AND `experiencer` discoverable as standalone agents

## ensure.md Validation
- No `ensure.md` validations needed for this scoped change (knowledge tool rewiring; not a deadlock/concurrency/architecture change). The relevant `ensure.md` requirements (concurrency atomic, async DB calls, dev.sh flag) are unrelated to this branch's change set.

## Overall Verdict

- **Directly affected tests**: ✅ PASS (124/124)
- **Related tests**: ✅ PASS (139/139)
- **Regression sweep**: ✅ PASS (0 new regressions; 15 pre-existing failures classified)
- **Static verification**: ✅ PASS (no dangling refs; experiencer still discoverable)

### **OVERALL: ✅ PASS — READY TO MERGE**
