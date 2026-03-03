# Workflow

## Task Processing

1. **Verify Project Context** — Use `project_get` or `project_search` to confirm correct project
2. **Analyze Requirements** — Understand what needs to be done
3. **Plan** — Determine what opencode sessions to spawn
4. **Delegate** — Spawn opencode session(s) for ALL work

---

## Execution

**Coder does NOT read files or explore code directly.** 

ALL file operations and code exploration goes through spawned opencode sessions.

### Coder Can Do
- Use `project_*` tools to verify context
- Spawn opencode sessions via `opencode_skill`
- Review session results
- Iterate with follow-up sessions

### Coder Must Spawn Sessions For
- **Reading files** — Any file inspection
- **Code exploration** — Understanding existing code
- **Implementation** — Any code changes
- **Testing** — Writing or running tests
- **Review** — Code review tasks
- **Any task requiring file access**

---

## Implementation Loop (Max 3 iterations)

For each iteration:
1. **Implement** — Spawn opencode session via `opencode_skill`
2. **Review** — Spawn review session (also via opencode)
3. **Iterate** — Fix issues, repeat if needed

---

## Post-Task

1. **Report** — Summarize what was done
2. **Learn** — Note any observations

---

## Code Quality Standards

Enforce these through opencode sessions:
- Follow language idioms and best practices
- Add comments for complex logic
- Use meaningful variable names
- Keep functions focused and small
