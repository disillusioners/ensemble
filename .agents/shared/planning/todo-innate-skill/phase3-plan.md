# Phase 3: Innate Skill + Agent Registration

## Objective
Create the `todo` innate skill.md prompt file and register the "todo" innate skill for all agents so they get the todo tools auto-granted and the skill prompt injected into their system prompt.

## Coupling
- **Depends on**: None
- **Coupling type**: independent
- **Shared files with other phases**: None
- **Shared APIs/interfaces**: None
- **Why this coupling**: This phase only creates a markdown file and edits JSON config. It has zero code dependency on Phases 1-2. Can run fully in parallel.

## Context
- Skill files at: `agents/_prompt_system/innate-skills/{skill_name}/skill.md`
- Registered via `innate_skills` array in `agents/{agent_id}/meta.json`
- Loaded by `load_agent_skills()` in `daemon/loader.py:268-298`
- 17 agents total need "todo" added to their meta.json

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create todo skill.md | VERY SHORT — just tool inventory + one-line behavioral hint. LLMs today are well-trained on todo tools. | `agents/_prompt_system/innate-skills/todo/skill.md` (NEW) |
| 2 | Add "todo" to all agent meta.json | Add `"todo"` to `innate_skills` array in all 17 agent meta.json files. | `agents/*/meta.json` (17 files) |

## Key Files
- `agents/_prompt_system/innate-skills/todo/skill.md` (NEW) — Skill prompt
- `agents/leader/meta.json` — Example: add "todo" to existing `["coordination", "chart"]`
- `agents/developer/meta.json`, `agents/planner/meta.json`, etc. (all 17)
- `agents/_baby_template/meta.json` — Template for spawned instances

## Detailed Design

### skill.md Content (KEEP VERY SHORT)

```markdown
# Todo Skill

Track multi-step workflows with a todo list. Use these tools to plan, track progress, and mark items complete.

## Tool Inventory

| Tool | Purpose |
|------|---------|
| `todo_create(items)` | Create/replace the full todo list |
| `todo_update(index, status)` | Update item status (pending/in_progress/done) |
| `todo_list()` | View current todo list |
| `todo_clear()` | Clear all items |

## Behavioral Hint

When you complete a task, mark it `done` via `todo_update`. The system will remind you of the next pending item. Keep your todo list current throughout multi-step work — it helps you track progress and avoid skipping steps.
```

### Decision: Per-Agent vs Global Default

**Chosen approach: Add "todo" to every agent's meta.json individually.**

Reasoning:
- The existing system is per-agent (`innate_skills` in each `meta.json`)
- There's no global default mechanism in the current codebase
- Adding to all 17 files is straightforward and explicit
- `agents/_baby_template/meta.json` ensures dynamically spawned instances also get it

**Alternative considered**: Add a global default innate skills list that's merged into all agents. This would require code changes to `loader.py` and `registry.py`. Not worth the complexity for a single skill — stick with the existing per-agent pattern.

### Agent List (17 files to edit)

```
agents/leader/meta.json
agents/planner/meta.json
agents/developer/meta.json
agents/reviewer/meta.json
agents/approver/meta.json
agents/devops/meta.json
agents/tester/meta.json
agents/tidier/meta.json
agents/explorer/meta.json
agents/giter/meta.json
agents/jober/meta.json
agents/charter/meta.json
agents/gaia/meta.json
agents/kb-importer/meta.json
agents/experiencer/meta.json
agents/_mother/meta.json
agents/_baby_template/meta.json
```

### Edit Pattern

For agents with existing `innate_skills`:
```json
"innate_skills": ["coordination", "chart"]
→
"innate_skills": ["coordination", "chart", "todo"]
```

For agents without `innate_skills`:
```json
→
"innate_skills": ["todo"]
```

## Constraints
- skill.md must be SHORT — LLMs already know how to use todo-style tools
- All 17 agents must be updated (no exceptions — "ALL agents" is a requirement)
- `_baby_template/meta.json` is critical — spawned instances inherit from it
- Don't add tool categories to meta.json `tools.allow` — `INNATE_SKILL_TOOL_CATEGORIES` handles auto-granting (Phase 1)

## Deliverables
- [ ] `agents/_prompt_system/innate-skills/todo/skill.md` created
- [ ] All 17 agent `meta.json` files updated with `"todo"` in `innate_skills`
- [ ] Verify skill loads correctly (startup test or unit test)
