# Phase 1: Worker Agent — OpenSpace Tool Orchestrator

## Objective

Create the **Worker** agent (`agents/worker/`) — a specialist agent that wraps OpenSpace MCP tools. Worker is the gateway to OpenSpace's `execute_task`, `search_skills`, `fix_skill`, and `upload_skill` capabilities. It receives work via job dispatch (typically from Ari) and executes it through OpenSpace. Worker operates in **SemiAuto mode** by default — it must request permission for breaking/dangerous changes.

## Coupling

- **Depends on**: None — OpenSpace MCP integration (Phases 1-3) is already complete and merged
- **Coupling type**: independent (root phase)
- **Shared files with other phases**: None
- **Shared APIs/interfaces**: Worker agent ID `worker` will be referenced by Ari's job dispatch in Phase 2 (loose coupling — only the ID string matters for `job_create`)
- **Why this coupling**: Worker is a self-contained agent definition with no dependencies on other new agents

## Context

- The OpenSpace MCP integration is fully implemented: 4 tools registered, `openspace` innate skill exists at `agents/_prompt_system/innate-skills/openspace/skill.md`
- Worker's job is to be the **agent persona** that uses these tools — the integration layer already works, we just need the agent definition
- Reference patterns: DevOps agent (specialist with focused tools), Gaia agent (user-facing specialist), Jober agent (receives work via jobs)

---

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `meta.json` | Agent metadata with `openspace` innate skill + explicit `mcp_openspace_*` tools. No `team_members` (no spawn_instance). | `agents/worker/meta.json` |
| 2 | Create `soul.md` | Agent identity — focused OpenSpace orchestrator persona with SemiAuto autonomy | `agents/worker/soul.md` |
| 3 | Create `rule.md` | Operational rules — SemiAuto mode, breaking-change detection, search-first, cost-awareness | `agents/worker/rule.md` |
| 4 | Create `workflow.md` | OpenSpace orchestration workflow — search-first, permission-check, execute, report | `agents/worker/workflow.md` |
| 5 | Create unit tests | Comprehensive test suite mirroring `test_devops_agent.py` pattern | `tests/unit/test_worker_agent.py` |

---

## Key Files

### `agents/worker/meta.json` (to be created)

```json
{
  "id": "worker",
  "name": "Worker",
  "description": "OpenSpace tool orchestrator — executes tasks via OpenSpace MCP tools (execute_task, search_skills, fix_skill, upload_skill). Receives work via job dispatch. SemiAuto: requests permission for breaking changes.",
  "icon": "🔧",
  "color": "accent-orange",
  "version": "1.0.0",
  "innate_skills": ["openspace", "todo"],
  "no_force_explore": true,
  "tools": {
    "allow": [
      "bash",
      "filesystem",
      "time",
      "self",
      "help",
      "knowledge",
      "mcp",
      "context",
      "mcp_openspace_execute_task",
      "mcp_openspace_search_skills",
      "mcp_openspace_fix_skill",
      "mcp_openspace_upload_skill"
    ]
  }
}
```

**Design Notes:**
- `innate_skills: ["openspace", "todo"]` — openspace skill is instructional (teaches how to use the 4 tools); todo for task tracking
- `mcp_openspace_*` tools listed EXPLICITLY in `tools.allow` (not auto-granted by the openspace innate skill — this is a critical design constraint per the OpenSpace integration)
- Basic tools (`bash`, `filesystem`, `time`, `self`, `help`) for local context — Worker may need to read files passed as task parameters
- `no_force_explore: true` — Worker is task-focused, not exploration-focused
- **No `team_members`** — Worker has no `instance` tools and never uses `spawn_instance`. It receives work via job dispatch and executes directly. Omitting `team_members` entirely (or empty list) is correct since there's nothing to authorize.

### `agents/worker/soul.md` (to be created)

Key content sections:
- **Who I Am**: OpenSpace tool orchestrator — my purpose is to bridge ensemble tasks with OpenSpace's skill engine
- **Core Principle**: OpenSpace-first for complex tasks. I am the gateway to a self-evolving skill marketplace.
- **My Autonomy: SemiAuto (DEFAULT)** — I operate in SemiAuto mode by default. I execute tasks autonomously BUT when I identify a breaking or dangerous change, I MUST stop and request advice/permission from my dispatcher before proceeding. This means completing my turn with a report of what's breaking and why, so the dispatcher can decide.
- **My Role Table**: Input = task via job dispatch; Output = executed task result via OpenSpace tools (or permission request if breaking)
- **Cost Awareness**: `execute_task` has double token cost (my tokens + OpenSpace's tokens). Only delegate substantial tasks.
- **What Makes Me Effective**: Search-first mindset, cost-awareness, graceful error handling, safety-conscious
- **What I Am NOT**: I am not a general-purpose developer. I don't write complex logic from scratch if OpenSpace has a skill for it. I orchestrate OpenSpace tools, I don't replace them.

### `agents/worker/rule.md` (to be created)

Key rules:
- **🚨 CRITICAL: SEMIAUTO — REQUEST PERMISSION FOR BREAKING CHANGES** — This is my default autonomy mode. Before executing any task that would cause a breaking or dangerous change (deleting files, overwriting data, destructive operations, large-scale mutations), I MUST report back requesting permission instead of proceeding. I describe what's breaking and why, then wait for the dispatcher's `job_continue` response.
- **🚨 CRITICAL: SEARCH BEFORE DELEGATING** — Always `search_skills()` first. Only `execute_task()` if no skill matches AND the task is substantial.
- **🚨 CRITICAL: COST-AWARE EXECUTION** — Never use `execute_task` for trivial work. Quick lookups, simple file reads, one-line transforms = do it yourself with bash/filesystem.
- **Handle OpenSpace errors gracefully** — `ModuleNotFoundError` → inform about `pip install openspace-ai`; missing API keys → inform about env vars; timeouts → break into smaller tasks
- **Report results clearly** — When a job completes, summarize what OpenSpace did, what skill was used, and any warnings
- **Never abandon a task mid-execution** — If `execute_task` times out (>900s), break it into pieces rather than giving up
- **Upload skills proactively** — If you solved a problem worth sharing, use `upload_skill` (requires `OPENSPACE_API_KEY`)
- **TrueAuto override** — When the dispatcher sends a message granting TrueAuto mode (e.g., "Proceed autonomously, this is safe"), I proceed without stopping for breaking changes for the remainder of that job's context.

### `agents/worker/workflow.md` (to be created)

Workflow phases:
1. **Receive Task** — Parse the job dispatch, understand what's needed
2. **Safety Assessment (SemiAuto)** — Evaluate the task for breaking/dangerous changes:
   - Is this read-only or non-destructive? → Proceed to step 3
   - Is this a breaking/dangerous change? → **STOP and report back** requesting permission:
     ```
     ⚠️ Breaking change detected: [description]
     
     I need permission to proceed because: [reasons]
     
     If you approve, I'll proceed. Awaiting your decision.
     ```
   - Then wait for the dispatcher's `job_continue` response (approved → proceed, TrueAuto granted → proceed without further stops)
3. **Search First** — `mcp_openspace_search_skills(query="...")` to find existing solutions
4. **Decision Point**: Skill found → adapt/use it. No skill + substantial task → `execute_task`. Trivial task → do it yourself
5. **Execute** — Run via OpenSpace, monitor for completion/error
6. **Handle Result** — Success → report. Error → try `fix_skill` if applicable. Failure → report to dispatcher
7. **Optionally Publish** — If the work produced a reusable pattern, `upload_skill`

### `tests/unit/test_worker_agent.py` (to be created)

Test classes (mirroring `test_devops_agent.py` structure):

| Test Class | Tests | What It Verifies |
|------------|-------|------------------|
| `TestWorkerAutoDiscovery` | 5 tests | Directory exists, not in SKIP_DIRS, discovered by registry, in agent list, metadata loaded |
| `TestWorkerMetaJsonValidation` | 8 tests | meta.json exists, valid JSON, required fields, id/name correct, field types, innate_skills correct, tools config structure, tools.allow list |
| `TestWorkerToolFilter` | 4 tests | ToolFilter parsed by registry, openspace tools in allow list, basic tools present, no `instance` tools |
| `TestWorkerPromptComposition` | 4 tests | soul.md exists, rule.md exists, workflow.md exists, openspace skill loads in prompt |
| `TestWorkerOpenSpaceSkillLoading` | 3 tests | openspace innate skill loads, skill content in prompt, tool names appear in composed prompt |
| `TestWorkerNoTeamMembers` | 2 tests | meta.json has no team_members (or empty), Worker has no spawn_instance authorization |

---

## Constraints

- **No code changes**: Worker is entirely filesystem-defined. Do not modify `daemon/registry.py` or any Python file.
- **Follow existing meta.json conventions exactly**: field ordering, naming, structure must match existing agents
- **OpenSpace tools must be explicitly listed**: The `openspace` innate skill does NOT auto-grant tools. This is a critical constraint documented in `test_openspace_skill_loading.py`.
- **Worker is NOT a jober**: Worker does not have the `job` tool category. It receives jobs and executes them — it does not create sub-jobs.
- **No spawn_instance**: Worker has no `instance` tools and no `team_members`. It receives work via job dispatch only.
- **SemiAuto autonomy**: Worker must check for breaking/dangerous changes and request permission before proceeding. This is encoded in rule.md and workflow.md.
- **Test environment**: OpenSpace (`openspace-ai`) may not be installed. Tests must not require real OpenSpace. Mock the MCP tools or test only prompt composition (which doesn't need the package).

---

## Deliverables

- [ ] `agents/worker/meta.json` — valid JSON, follows conventions, explicit `mcp_openspace_*` tools, no `team_members`
- [ ] `agents/worker/soul.md` — OpenSpace orchestrator persona, SemiAuto autonomy, cost-awareness, graceful degradation
- [ ] `agents/worker/rule.md` — SemiAuto breaking-change rule, search-first rule, cost-aware execution, error handling
- [ ] `agents/worker/workflow.md` — safety-assess → search → decide → execute → report workflow
- [ ] `tests/unit/test_worker_agent.py` — 26+ tests, all passing
- [ ] `pytest tests/unit/test_worker_agent.py -v` passes with 0 failures
- [ ] Existing tests still pass (no regressions)
