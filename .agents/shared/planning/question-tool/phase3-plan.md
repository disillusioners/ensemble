# Phase 3: Skill Doc + Agent Config

## Objective
Create the innate skill documentation (`skill.md`) for the question tool and add `"question"` to the leader agent's `tools.allow` so it can use the tool.

## Coupling
- **Depends on**: None (fully independent)
- **Coupling type**: independent — only touches markdown + JSON config
- **Shared files with other phases**: none
- **Shared APIs/interfaces**: none
- **Why this coupling**: Phase 3 is pure configuration + documentation. It can run in parallel with Phases 1 and 2.

## Context
- The `question` tool is NOT an innate skill in the `INNATE_SKILL_TOOL_CATEGORIES` sense (no auto-grant). Instead, the leader gets it via explicit `tools.allow` in meta.json.
- The skill.md still documents how to use the tool (following the existing innate-skill doc pattern).

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create question skill.md | Document the `question` tool: purpose, parameters (questions list with id/text/options/allow_custom/required), behavior (instance pauses, answers come back as a message), and usage examples. | `agents/_prompt_system/innate-skills/question/skill.md` *(new)* |
| 2 | Add `"question"` to leader tools.allow | Surgical single-string append: add `"question"` to the `tools.allow` array in leader's meta.json. (F6: the actual file includes `"image"` and `"shared_context"` — only append `"question"`, don't rewrite the array.) | `agents/leader/meta.json` |

## Detailed Design Notes

### Task 1: skill.md content

Follow the structure of existing skill docs (e.g., `agents/_prompt_system/innate-skills/todo/skill.md`). Key sections:

```markdown
# Question Skill

## Purpose
Ask the user questions when you need clarification, decisions, or input.
The instance pauses until the user answers. Answers are delivered back to you
as a message so you can continue your work.

## Tools

### `question(questions: list)`
Ask the user a batch of questions. Each question object:
- `id` (str, optional): Unique identifier. Auto-generated if missing.
- `text` (str, required): The question text.
- `options` (list[str], optional): Predefined choices the user can select.
- `allow_custom` (bool, default true): Whether the user can type a custom answer.
- `required` (bool, default true): Whether the user must answer this question.

After calling this tool, your instance will PAUSE. When the user answers,
you will receive their answers as a message and your instance will RESUME.

## When to Use
- When you need a decision from the user before proceeding
- When you need clarification on ambiguous requirements
- When you need approval for an approach
- When you need information you cannot find yourself

## Examples
[provide 1-2 JSON examples of question() calls]
```

### Task 2: leader meta.json change (F6)

The actual `agents/leader/meta.json` includes additional entries like `"image"` and `"shared_context"` in `tools.allow`. **Do not rewrite the entire array** — make a surgical single-string append of `"question"`.

Current `tools.allow` (actual, may include more entries):
```json
"tools": {
    "allow": ["instance", "self", "project", "help", "knowledge", "mcp", "critical_notes", "project_history", "image", "shared_context"]
}
```

After change — append `"question"`:
```json
"tools": {
    "allow": ["instance", "self", "project", "help", "knowledge", "mcp", "critical_notes", "project_history", "image", "shared_context", "question"]
}
```

That's the only change. The tool factory (Phase 1) handles the rest.

## Key Files
- `agents/_prompt_system/innate-skills/question/skill.md` *(new)*
- `agents/leader/meta.json` — append one string to the array

## Constraints
- Do NOT add `"question"` to `INNATE_SKILL_TOOL_CATEGORIES` in `instance.py` — leader gets it via tools.allow only.
- Follow the existing skill.md structure and tone.
- The skill.md must clearly explain the pause/resume behavior so the agent understands it will pause.
- **F6**: Read the actual meta.json before editing — don't assume the array contents. Append only `"question"`.

## Deliverables
- [ ] `agents/_prompt_system/innate-skills/question/skill.md` created
- [ ] `"question"` appended to `agents/leader/meta.json` `tools.allow`
- [ ] Verify the tool appears in leader's tool list (can verify after Phase 1 is done)
