# Phase 3: Integration, Wiring & Cross-Agent Testing

## Objective

Validate both new agents work together: run cross-agent integration tests, validate end-to-end job dispatch flows (Ari → Leader, Ari → Worker), verify the Worker permission-escalation flow, and ensure zero regressions across the full test suite.

## Coupling

- **Depends on**: Phase 1 (Worker agent) + Phase 2 (Ari agent) — both must be fully built
- **Coupling type**: tight — integration tests verify the actual dispatch paths between agents, including the escalation flow
- **Shared files with other phases**: All files created in Phase 1 and Phase 2
- **Why this coupling**: Integration tests need both agents operational to verify job dispatch flows work end-to-end

## Context

After Phases 1-2, both agents exist as filesystem definitions and are individually tested. Phase 3 validates that they work *together* and that adding them doesn't break existing agent relationships. The leader's `meta.json` is NOT modified (Decision D7).

---

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Write cross-agent integration tests | Verify Ari → Worker and Ari → Leader job dispatch, no-instance-tools assertions, escalation flow documentation | `tests/unit/test_ari_worker_integration.py` |
| 2 | Run full test suite regression check | Verify 0 regressions across all existing tests | N/A (run command) |
| 3 | Validate agent registry with both new agents | Confirm registry discovers both, no conflicts | N/A (validation script) |
| 4 | Run OpenSpace skill loading test for Worker | Confirm Worker's prompt composition includes OpenSpace tools | N/A (extends existing `test_openspace_skill_loading.py` patterns) |

---

## Key Tasks Detail

### Task 1: Cross-Agent Integration Tests

Create `tests/unit/test_ari_worker_integration.py`:

| Test Class | Tests | What It Verifies |
|------------|-------|------------------|
| `TestAgentCoexistence` | 3 tests | Both agents discoverable simultaneously, no ID conflicts, no SKIP_DIRS issues |
| `TestNoTeamMembers` | 2 tests | Ari has no team_members (or empty), Worker has no team_members (or empty) |
| `TestNoInstanceTools` | 2 tests | Ari tools.allow has no "instance", Worker tools.allow has no "instance" |
| `TestDispatchGraphAcyclic` | 2 tests | Ari→leader via job (not leader→ari), Ari→worker via job (not worker→ari), no cycles |
| `TestPromptCompositionBoth` | 2 tests | Both agents compose prompts successfully with their innate skills |
| `TestAutonomyModelInPrompts` | 2 tests | Ari prompt mentions TrueAuto, Worker prompt mentions SemiAuto |

**Critical integration test assertions:**
```python
# Both agents discovered
ari = registry.get("ari")
worker = registry.get("worker")
assert ari is not None and worker is not None

# Neither agent has instance tools
assert "instance" not in ari.tools.allow
assert "instance" not in worker.tools.allow

# Neither agent has team_members (or empty)
assert not ari.team_members or ari.team_members == []
assert not worker.team_members or worker.team_members == []

# No circular dispatch: leader does NOT list ari or worker
leader = registry.get("leader")
assert "ari" not in leader.team_members
assert "worker" not in leader.team_members

# Ari has job tools for dispatch
assert "job" in ari.tools.allow

# Worker has OpenSpace tools
assert "mcp_openspace_execute_task" in worker.tools.allow

# Worker does NOT have job tools (not a jober)
assert "job" not in worker.tools.allow
```

### Task 2: Full Regression Check

```bash
# Run full unit test suite (excluding integration/postgres)
.venv/bin/pytest tests/ -v -m "not integration and not postgres" -x

# Specifically verify no existing agent tests broke
.venv/bin/pytest tests/unit/test_devops_agent.py -v
.venv/bin/pytest tests/unit/test_openspace_skill_loading.py -v
.venv/bin/pytest tests/test_registry.py -v

# Run new agent tests
.venv/bin/pytest tests/unit/test_worker_agent.py -v
.venv/bin/pytest tests/unit/test_ari_agent.py -v
.venv/bin/pytest tests/unit/test_ari_worker_integration.py -v
```

**Expected**: 0 failures, 0 regressions. New tests add ~64 tests to the suite.

### Task 3: Registry Validation Script

Manual validation (can be a quick Python snippet or test):
```python
from daemon.registry import AgentRegistry

registry = AgentRegistry(Path("agents"))
registry.discover()

# Both new agents discovered
assert registry.exists("ari"), "ari not discovered!"
assert registry.exists("worker"), "worker not discovered!"

# All agents still present (no regressions)
expected_agents = {"ari", "worker", "leader", "planner", "developer", "reviewer", 
                   "tidier", "approver", "tester", "giter", "devops", "explorer",
                   "jober", "gaia", "charter", "experiencer", "kb-importer"}
discovered = {a.id for a in registry.list_all()}
assert expected_agents.issubset(discovered), f"Missing agents: {expected_agents - discovered}"
```

### Task 4: OpenSpace Skill in Worker Prompt

Extend verification that Worker's prompt composition includes the OpenSpace skill content and tool names. This follows the pattern from `test_openspace_skill_loading.py`:

```python
from daemon.loader import compose_system_prompt

# Worker meta with openspace innate skill
meta = {"innate_skills": ["openspace", "todo"]}
skills = load_agent_skills(WORKER_AGENT_DIR, meta)
assert "openspace" in skills

# Compose prompt and verify tool names appear
prompt = compose_system_prompt(soul="...", rule="...", workflow="...", skills=skills)
for tool_name in OPENSPACE_TOOL_NAMES:
    assert tool_name in prompt
```

---

## Constraints

- **No code changes**: This phase is testing and validation only. Leader's meta.json is NOT modified.
- **Test environment**: MCP tools mocked. Do NOT require real OpenSpace installation.
- **PostgreSQL**: Not needed for agent definition tests (agents are filesystem-based). Standard pytest without `postgres` marker.
- **Acyclic dispatch graph**: The dispatch graph must be acyclic. Ari → {leader, worker} is one-directional via job tools. Verify no cycles exist.

---

## Testing Strategy Summary

### Test Categories

| Category | Test Files | Test Count (est.) | Purpose |
|----------|------------|-------------------|---------|
| Worker unit tests | `test_worker_agent.py` | ~26 | Worker agent definition validity, SemiAuto autonomy |
| Ari unit tests | `test_ari_agent.py` | ~27 | Ari agent definition validity, TrueAuto autonomy, smart personality |
| Cross-agent integration | `test_ari_worker_integration.py` | ~13 | Agent coexistence, dispatch graph, no-instance assertions, autonomy in prompts |
| **Total new tests** | 3 files | **~66** | |

### Test Scenarios

| Scenario | How to Test | Expected Result |
|----------|-------------|-----------------|
| Agent auto-discovery | `registry.discover()` then check `exists()` | Both `ari` and `worker` discovered |
| meta.json validity | Parse JSON, check required fields | All fields present, correct types |
| Tool permissions | Check `tools.allow` lists | Worker has `mcp_openspace_*`; Ari has `job` + `bash` but NOT `mcp_openspace_*` or `instance` |
| No team_members | Check meta.json | Neither agent has team_members (or empty) |
| No instance tools | Check `tools.allow` | Neither agent has `instance` category |
| Innate skills loading | `load_agent_skills()` | Worker loads `openspace`; Ari loads `job-orchestration` + `openspace` |
| Prompt composition | `compose_system_prompt()` | Both agents compose without errors; skill content present |
| Autonomy in prompts | Check soul.md/rule.md content | Ari mentions TrueAuto; Worker mentions SemiAuto |
| Acyclic dispatch graph | Verify leader doesn't list ari/worker | No circular dispatch paths |
| Existing agent regression | Run full suite | 0 new failures |

---

## Deliverables

- [ ] `tests/unit/test_ari_worker_integration.py` — 13 cross-agent tests, all passing
- [ ] Full test suite passes with 0 regressions
- [ ] Registry validation confirms both agents discoverable
- [ ] OpenSpace skill loads in Worker prompt composition
- [ ] Dispatch graph verified acyclic (Ari → {leader, worker} via job tools, no cycles)
- [ ] Neither agent has `instance` tools or `team_members` confirmed in tests
- [ ] Autonomy model (Ari TrueAuto, Worker SemiAuto) present in prompt content
