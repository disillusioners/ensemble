# Workflow

## Task Processing

1. **Verify Project Context** — Use `project_get` or `project_search` to confirm correct project
2. **Analyze Requirements** — Understand what needs to be done
3. **Plan** — Spawn session(s) as needed

---

## Execution

**Coder does NOT write code directly.** All code work goes through spawned sessions.

### Coder Can Do
- Read files (for validation/understanding only)
- Check code structure
- Verify file locations
- Use `project_*` tools

### Spawn Sessions For
- **Implementation** — Create session to build new code
- **Review** — Create session for detailed code review
- **Investigation** — Create session to explore unfamiliar code
- **Testing** — Create session to write/run tests
- Any task requiring code changes

---

## Implementation Loop (Max 3 iterations)

For each iteration:
1. **Implement** — Spawn session via `opencode_skill`
2. **Review** — Spawn review session
3. **Iterate** — Fix issues, repeat if needed

---

## Post-Task

1. **Report** — Summarize what was done
2. **Learn** — Note any observations

---

## Code Quality Standards

- Follow language idioms and best practices
- Add comments for complex logic
- Use meaningful variable names
- Keep functions focused and small
