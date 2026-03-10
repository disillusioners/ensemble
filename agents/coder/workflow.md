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

## Handling Opencode Questions

When opencode responds with a question or asks for confirmation:

### Auto-Decide (Don't Ask User)

**Trivial/Single-Option Questions** — Respond directly to the opencode session:
- "Should I implement [simple change]?" → **YES, proceed**
- "Should I fix this typo?" → **YES, proceed**
- "Should I use the existing pattern?" → **YES, follow existing patterns**
- "There's only one way to do this, should I proceed?" → **YES, proceed**
- Questions about minor details (variable names, small refactorings)
- Single obvious choice in context

**Response format:** Send message to session: "Yes, proceed with [action]."

### Escalate to User (Ask User)

**Important/Multi-Option Questions** — Ask the user:
- Multiple valid approaches with tradeoffs
- Architectural decisions
- Breaking changes or deletions
- Security implications
- Performance impact questions
- User preference questions (UI/UX choices)
- Scope expansion ("Should I also refactor X?")

### Decision Criteria

Ask yourself:
1. **Is there only one reasonable option?** → Auto-decide YES
2. **Is this a minor implementation detail?** → Auto-decide YES
3. **Does this affect project architecture?** → Ask user
4. **Are there multiple valid approaches?** → Ask user
5. **Could this break something important?** → Ask user

**Default behavior:** When in doubt about importance, auto-decide to keep momentum.

---

## Implementation Loop (Max 3 iterations)

For each iteration:
1. **Implement** — Spawn opencode session via `opencode_skill`
2. **Review** — Spawn review session (also via opencode)
3. **Evaluate Review** — Check if code is good or needs fixes
4. **Iterate or Commit:**
   - **If review passed (no more updates needed)** → **Auto-commit immediately** (reuse review session)
   - **If review found issues** → **Reuse implementation session to fix** (see Fix Strategy below)

---

## Fix Strategy (When Review Finds Issues)

### Prefer Implementation Session

**Always reuse the implementation session for fixes** — it has full context and can fix issues faster.

Send fix instruction to implementation session:
```
"Review found these issues: [list issues]. Please fix them."
```

### When to Spawn New Fix Session (Rare Cases)

Only spawn a **new** opencode session for fixes if the implementation session has problems:

🚫 **Implementation session is unusable when:**
- Session crashed or errored out
- Hallucination detected in session response (claiming things that don't exist)
- Session is stuck in a loop (same mistake repeatedly)
- Session context is corrupted or confused

✅ **Otherwise, always reuse implementation session** — even for significant fixes

### Example Flow

```
1. Implementation session (S1) → implements feature
2. Review session (S2) → reviews code, reports "found 3 issues: ..."
3. Reuse S1 (implementation) → send "Fix these issues: ..."
4. S1 fixes → done (or back to step 2 for re-review)
```

---

## Auto-Commit on Successful Review

When review session confirms code is good (no issues, no improvements needed):

### Commit Process

1. **Reuse the review session** — Send commit instruction to the same session
2. **Commit message format:**
   ```
   [type]: [brief description]
   
   [optional details if complex]
   ```
3. **Commit types:**
   - `feat:` — New feature
   - `fix:` — Bug fix
   - `refactor:` — Code refactoring
   - `docs:` — Documentation changes
   - `test:` — Adding/updating tests
   - `chore:` — Maintenance tasks

4. **Instruction to session:** "The review passed. Please commit these changes with message: '[type]: [description]'"

### When to Auto-Commit

✅ **Auto-commit:**
- Review session confirms no issues
- All tests pass
- Code follows standards
- No further changes recommended

❌ **Don't commit yet:**
- Review found bugs or issues
- Tests are failing
- Reviewer suggests improvements
- Need to iterate on implementation

### Example Flow

```
1. Spawn implementation session → implements feature
2. Spawn review session → reviews code, reports "looks good, no issues"
3. Reuse review session → send "Commit with message: 'feat: add user authentication'"
4. Session commits → done
```

---

## Session Reuse Summary

| Task | Which Session to Use |
|------|---------------------|
| Fix issues from review | **Implementation session** (has context) |
| Commit after good review | **Review session** (already verified) |
| New fix session | **Only if implementation session is broken** |

---

## Post-Task

1. **Report** — Summarize what was done (including commit hash if applicable)
2. **Learn** — Note any observations

---

## Code Quality Standards

Enforce these through opencode sessions:
- Follow language idioms and best practices
- Add comments for complex logic
- Use meaningful variable names
- Keep functions focused and small
