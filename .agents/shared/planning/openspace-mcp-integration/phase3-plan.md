# Phase 3: Prompt & Agent Integration

## Objective
Create an innate skill prompt that teaches agents how to use OpenSpace's skill search, task delegation, skill repair, and skill upload tools. Document which agents get these tools by default and how to configure tool filters in `meta.json`.

## Coupling
- **Depends on**: Phase 1 (needs to know the MCP tool names: `mcp_openspace_execute_task`, etc.)
- **Coupling type**: **loose** — adds new prompt file, modifies no existing code. Only needs tool name strings from Phase 1.
- **Shared files with other phases**: None
- **Shared APIs/interfaces**: None
- **Why this coupling**: Prompt content references tool names that Phase 1's definition produces. No code dependency.

## Context

### Innate Skill System
- Skills live in `agents/_prompt_system/innate-skills/{name}/skill.md`
- Loaded via `load_tools_doc_for_agent()` and injected into `compose_system_prompt()`
- An agent's `meta.json` lists innate skills in the `innate_skills` array
- Innate skills implicitly grant the tool categories they require (via `expand_allow_for_innate_skills()`)
- Existing skills: `chart`, `coordination`, `job-orchestration`, `opencode`, `test-pack`

### Tool Naming Convention
MCP tools are named `mcp_{slugified_server}_{tool_name}`:
- `mcp_openspace_execute_task`
- `mcp_openspace_search_skills`
- `mcp_openspace_fix_skill`
- `mcp_openspace_upload_skill`

### Tool Filter System
- Per-agent `meta.json` has `tools.allow` and `tools.deny` arrays
- If `tools` is null/absent → all tools allowed (backward compatible)
- If `tools.allow` is set → only listed tools are available
- MCP tool names **must** be explicitly listed in `allow` to be visible — they are NOT covered by `INNATE_SKILL_TOOL_CATEGORIES` expansion

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create OpenSpace innate skill | Write `skill.md` explaining the 4 tools, when to use them, prerequisites | `agents/_prompt_system/innate-skills/openspace/skill.md` (new) |
| 2 | Document agent configuration | Add guidance on which agents get OpenSpace tools + how to add to meta.json | This skill.md + `decisions.md` |
| 3 | Add tool filter examples | Show example `tools.allow` entries for OpenSpace | This skill.md |
| 4 | Verify innate skill loader picks up new skill | Ensure `load_tools_doc_for_agent()` discovers the new skill directory | No code change expected — just verify |

## Innate Skill Content Outline

### `agents/_prompt_system/innate-skills/openspace/skill.md`

```markdown
# OpenSpace Skill

OpenSpace is a skill marketplace and sub-agent executor. It provides 4 tools:

## Tool Inventory
| Tool | Purpose | When to Use |
|------|---------|-------------|
| `mcp_openspace_search_skills` | Search for existing skills | Before writing complex logic — someone may have already solved this |
| `mcp_openspace_execute_task` | Delegate a task to OpenSpace's agent | For complex, multi-step tasks that benefit from autonomous execution |
| `mcp_openspace_fix_skill` | Repair a broken skill | When a skill has errors after evolution |
| `mcp_openspace_upload_skill` | Upload a skill to cloud community | When you've created a reusable skill worth sharing |

## Prerequisites
- OpenSpace must be installed: `pip install openspace-ai`
- `OPENSPACE_LLM_API_KEY` must be set in environment
- `OPENSPACE_MODEL` must be configured (default: openrouter/anthropic/claude-sonnet-4.5)

## Usage Patterns

### Search Before You Build
Before implementing complex logic, search for existing skills:
```
mcp_openspace_search_skills(query="pdf extraction with ocr")
```

### Delegate Complex Tasks
For tasks requiring multiple steps (file processing, API integration, data transformation):
```
mcp_openspace_execute_task(task="Extract all email addresses from PDF files in /data/ and save to CSV")
```

## Important Notes
- `execute_task` is long-running (up to 15 minutes). Use it for substantial tasks, not quick lookups.
- `search_skills` is fast — use it freely to discover existing solutions.
- OpenSpace runs its own LLM agent internally. It has its own model and API key.
- If tools return errors, check that OpenSpace is installed and credentials are set.
```

## Agent Configuration

### Which Agents Get OpenSpace by Default?

**Recommendation: Do NOT add OpenSpace to any agent's default config.** 

Rationale:
- OpenSpace requires additional installation (`pip install openspace-ai`) and credentials
- Adding it by default would show broken tools to agents if OpenSpace isn't installed
- Users opt-in by adding `"openspace"` to an agent's `innate_skills` array

### How to Enable for an Agent

**IMPORTANT (W3 Fix)**: MCP tools are loaded dynamically per-instance and do NOT belong to the `INNATE_SKILL_TOOL_CATEGORIES` expansion system. Unlike `opencode` or `chart` innate skills which map to static tool categories (see `daemon/tools/instance.py:52-55`), MCP tools like `mcp_openspace_*` are **not** covered by `expand_allow_for_innate_skills()`.

Therefore, agents using OpenSpace MUST explicitly list all 4 MCP tool names in `tools.allow`:

```json
{
  "innate_skills": ["openspace"],
  "tools": {
    "allow": [
      "mcp_openspace_search_skills",
      "mcp_openspace_execute_task",
      "mcp_openspace_fix_skill",
      "mcp_openspace_upload_skill"
    ]
  }
}
```

Or if the agent already has `innate_skills` and `tools.allow`:

```json
{
  "innate_skills": ["opencode", "openspace"],
  "tools": {
    "allow": [
      "spawn_instance",
      "external_opencode_init_session",
      "mcp_openspace_search_skills",
      "mcp_openspace_execute_task"
    ]
  }
}
```

> **Note**: Adding `"openspace"` to `innate_skills` loads the skill prompt into the agent's system prompt, giving it the documentation on how to use the tools. But the `tools.allow` entries are what actually **grant access** to the MCP tools. Both are required.

> **Edge case**: If `tools` is absent/null in `meta.json`, ALL tools are allowed (backward-compatible default). In that case, the MCP tools will be visible without explicit listing — but this also grants access to every other MCP server's tools, which may be undesirable.

### Which Agents Benefit Most

| Agent | Use Case |
|-------|----------|
| Developer | Search for coding skills, delegate complex implementation tasks |
| Planner | Research patterns, find architectural skills |
| Leader | Delegate research tasks to OpenSpace's autonomous agent |
| Any custom agent | Depends on the agent's purpose |

## Key Files

| File | Purpose | Action |
|------|---------|--------|
| `agents/_prompt_system/innate-skills/openspace/skill.md` | Innate skill prompt | **NEW** |

## Constraints
- Do NOT change existing agent `meta.json` files — opt-in only
- Prompt must follow existing innate skill style (see `opencode/skill.md` for reference)
- Tool names must match exactly: `mcp_openspace_*`
- Must NOT auto-add OpenSpace to any agent's tool list

## Deliverables
- [ ] `agents/_prompt_system/innate-skills/openspace/skill.md` created
- [ ] Innate skill loader discovers the new skill (verify, no code change expected)
- [ ] Documentation in skill.md covers: prerequisites, tool usage, agent config
- [ ] Example meta.json configuration documented
