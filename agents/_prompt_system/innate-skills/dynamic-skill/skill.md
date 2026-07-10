# Dynamic Skill System

Dynamic skills are living, evolvable capabilities stored in the ensemble database.
Unlike innate skills (which are static and human-authored), dynamic skills can be
created, searched, and improved over time based on real usage outcomes.

## When to Use Dynamic Skills

- **Automatic injection**: If your agent has `skill_injection: true` in meta.json,
  relevant skills are automatically injected as a `HumanMessage` before each
  real user message. You don't need to do anything — just read and apply them.
- **Explicit search**: Use `skill_search()` when you need to find a skill for a
  specific task that wasn't auto-injected.
- **Browse available skills**: Use `skill_list()` to see all skills, `skill_view()`
  to read full content.

## Tool Reference

| Tool | Purpose |
|------|---------|
| `skill_search(query)` | Search skills by natural language query |
| `skill_list(category?)` | List all available skills |
| `skill_view(skill_id)` | View full skill content + lineage |
| `skill_create(name, description, content)` | Create a new skill |
| `skill_fix(skill_id, issue_description)` | Report a skill issue for evolution |
| `skill_feedback(skill_id, applied?, note?)` | Provide feedback on skill usefulness |

## Feedback is Critical

After using an injected skill, call `skill_feedback()` to report whether it was
helpful. This feedback drives the evolution system — skills that consistently
fail get fixed or deactivated, while successful patterns are reinforced.

- `applied=true` → skill was directly useful
- `applied=false` → skill was not relevant or unhelpful  
- `applied=null` (omit) → unsure, just leaving a note
