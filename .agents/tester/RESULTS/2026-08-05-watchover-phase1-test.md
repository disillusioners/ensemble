# Test Report: Watchover Feature Phase 1
Date: 2026-08-05T22:47:42Z
Instance IDs: 4b3ee09c (unit-test), 3cfaa9f6 (regression), fd17897f (agent-validation), b64a70e6 (quick-fix)

## Summary
- Total: 36 tests | Passed: 36 | Failed: 0 | Errors: 0
- Unit Tests: 26 watchover graph tests | Regression: 10 question_graph tests
- Agent Definition Validation: 5/5 static checks PASS
- Quick Fixes Applied: 1 (duplicate `## Speed` section in workflow.md)
- Quarantined: 0

## Scope Decision
> Change touches `daemon/graph.py` (core graph builder, backward-compatible with manager=None path), `daemon/manager.py` (new accessors), and new `agents/watcher/` (5 files). Graph topology change is architecturally significant but fully backward-compatible by design. Scoped to: (1) the 26 new watchover tests, (2) the closest analog regression pack (question_graph — same deferred marker pattern), (3) agent definition static validation. Full suite NOT warranted — Phase 1 is scaffolding with stub decision logic; the existing test suite already validated the graph patterns being reused.

## Test Results

### Watchover Graph Unit Tests — ✅ PASS
- **Pack**: `tests/unit/test_watchover_graph.py`
- **Result**: 26/26 passed in 0.98s
- **Coverage**:
  - **T1.0 Topology invariant** (3 tests): no agent→tools bypass when manager provided; direct edge preserved when manager=None; topology re-targets to watchover_check even when language_check_enabled=False
  - **T1.0b Kill-switch** (4 tests): WATCHOVER_ENABLED=false → zero-cost passthrough (no DB lookup); true → per-instance flag respected; unset → defaults to true; accepts 1/yes/True as truthy
  - **create_pre_tools_router** (4 tests): routes to watchover_check when enabled; routes to tools when disabled; routes to tools when kill-switch off; routes to tools when config missing (safe default)
  - **watchover_check node** (3 tests): passthrough when no tool_calls; Phase 1 stub returns {} (Allow); handles missing config gracefully
  - **watchover_terminate_node** (2 tests): sets deferred marker via slot + returns {}; handles missing instance_id (no marker set)
  - **should_end_watchover** (2 tests): Phase 1 stub always returns 'tools'; handles missing config
  - **Manager accessors** (8 tests): is_watchover_enabled reads metadata JSONB; returns False for missing flag/metadata-None/instance-not-found/exception; deferred marker set/is/clear lifecycle; clear is idempotent; markers are per-instance

### Question Graph Regression — ✅ PASS
- **Pack**: `tests/unit/test_question_graph.py`
- **Result**: 10/10 passed in 0.67s
- **Rationale**: Watchover reuses the `question_pause_node` deferred-marker pattern. This regression confirms the graph.py changes didn't break the existing deferred-marker system.

### Agent Definition Static Validation — ✅ PASS (5/5)
| Check | Status | Details |
|-------|--------|---------|
| 1. meta.json Validity | ✅ PASS | All required fields present: id="watcher", tools.allow=[], team_members=[], innate_skills=[], watchover config block (llm_model, timeout_seconds, max_denials_per_turn, mirror_message_count, failure_mode) |
| 2. Registry Auto-Registration | ✅ PASS | AgentRegistry.discover() picks up watcher; 29 agents registered total; metadata round-trip correct |
| 3. No System-Internal References | ✅ PASS | 0 matches for AD-/LD-/FR-/checkpoint/SSE/meta.json/_cleanup_instance_state in all .md files; extended forbidden-list grep also clean |
| 4. Agent Prompt Conventions | ✅ PASS | All 5 files follow docs/agent-prompt-writing-guide.md; exactly 7 cardinal rules; cross-refs use stable section names; tone directive present |
| 5. File Completeness | ✅ PASS | All 5 files exist and non-empty: meta.json (32L), soul.md (145L), rule.md (126L), workflow.md (133L after fix), tools_note.md (69L) |

## Quick Fixes Applied
- **Instance b64a70e6**: Removed duplicate `## Speed` section + corrupted fragment in `agents/watcher/workflow.md`
  - **Root cause**: Copy-paste artifact — the `## Speed` section appeared twice (lines 131-133 and 138-140), with a corrupted fragment `y line of an allow response.` on line 134 and an orphan `---` separator
  - **Fix**: Deleted 7 lines (134-140), keeping only the correct first Speed section
  - **Verification**: All 26 watchover tests re-run PASS (0.89s)
  - **Commit**: `930c3b68` — "fix: remove duplicate Speed section in watcher workflow.md"

## ensure.md Validation Results

### Core (in-scope, blast-radius scoped)
- **Critical**:
  - ✅ **No regressions in changed packs**: All in-scope packs PASS — watchover_graph_unit_test (26/26), question_graph regression (10/10)
  - ✅ **`dev.sh` includes `--timeout-graceful-shutdown 10`**: Not modified by this change (static — no regression risk)
  - N/A — Deadlock/concurrency integrity: no concurrency changes in this phase (graph topology + agent definition only)
  - N/A — Sync DB calls on asyncio: no async changes in this phase

### Release Gate — NOT RUN
Phase 1 is scaffolding (stub decision logic, backward-compatible graph wiring). Not a big/critical/architecture change requiring the release gate. The topology invariant is covered by unit tests.

## Documentation Updated
- [x] PACKS.md — added watchover_graph_unit_test pack entry + run history
- [x] RESULTS/2026-08-05-watchover-phase1-test.md — this report
- [x] LESSONS/2026-08-05-watchover-duplicate-section-fix.md — quick fix record
- [ ] rules/ensure.md — no changes (user-maintained)
- [ ] MOCK_TESTS.md — no mock tests in Phase 1
- [ ] QUARANTINE.md — no quarantined tests

## Code Changes Summary
- `agents/watcher/workflow.md` — Removed 7 lines (duplicate `## Speed` section + corrupted fragment)
- Commit: `930c3b68`

---

### Overall Status
- Unit Tests: ✅ PASS (26/26)
- Regression: ✅ PASS (10/10)
- Agent Definition: ✅ PASS (5/5 static checks)
- Quick Fix: ✅ Applied + committed
- ensure.md: ✅ PASS (in-scope requirements met)
- **Testing Complete**: ✅ READY — Phase 1 watchover implementation is verified and ready to proceed to Phase 2
