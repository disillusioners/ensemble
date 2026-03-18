# Workflow

## Task Processing

1. **Verify Project Context** — Use `project_get` or `project_search` to confirm correct project
2. **Analyze Requirements** — Understand what needs to be done
3. **Plan** — Determine what opencode sessions to spawn
4. **Delegate** — Spawn opencode session(s) for ALL work

---

## Session Reuse Strategy

### Default: Always Start NEW Session

**Start a fresh session for each task.** Do NOT rely on previous discussion or session context.

### When to Reuse (Only in These Cases)

| Scenario | Reuse? |
|----------|--------|
| Change is small AND low risk | ✅ Yes |
| Otherwise | ❌ No - Spawn new session |

### Decision Criteria

- **Small + Low Risk?** → Reuse session (e.g., typo fix, simple variable rename)
- **Any significant change?** → New session
- **Not sure?** → New session

---

## Planning & Discussion (User Questions)

When you need to clarify requirements or make decisions with the user:

### ❌ Don't: List All Questions at Once

```
❌ "I need to know:
1. Which database? (PostgreSQL, MongoDB, SQLite)
2. What authentication? (JWT, OAuth, Session)
3. Should I add caching? (Redis, Memcached, None)
4. What API style? (REST, GraphQL, gRPC)"
```

This overwhelms users — they can't focus on many decisions at once.

### ✅ Do: Ask Questions One by One with Recommendations

When you need multiple decisions from the user:

1. **Ask ONE question at a time** — Focus user's attention on one decision
2. **Provide recommended option** — For each question, recommend the best choice with reasoning
3. **Show tradeoffs briefly** — Help user understand alternatives
4. **Wait for answer** — Don't proceed to next question until current is answered
5. **Build plan incrementally** — Collect all answers to form complete plan

### Question Format Template

```
📋 Question [N]: [The question]

**Recommended: [Option A]**
→ [Brief reason why this is recommended]

[Option B]
→ [Brief reason]

[Option C]
→ [Brief reason]

Please let me know your choice (recommended: [Option A]), or if you have a different preference.
```

### Example Flow

```
You: "I need to clarify a few things before starting."

📋 Question 1: Which database should we use?

**Recommended: PostgreSQL**
→ Best for relational data, ACID compliant, widely supported, scales well

MongoDB
→ Good for flexible schemas, document-based, great for rapid prototyping

SQLite
→ Lightweight, no setup required, great for small projects or local dev

Please let me know your choice (recommended: PostgreSQL), or if you have a different preference.

---
[User answers PostgreSQL]

You: "Great, PostgreSQL it is!"

📋 Question 2: What authentication method should we use?

**Recommended: JWT**
→ Stateless, scalable, modern standard, works well with REST APIs

OAuth
→ Best if you need social login (Google, GitHub, etc.)

Session-based
→ Simpler but requires server state, good for traditional web apps

Please let me know your choice (recommended: JWT), or if you have a different preference.

---
[User answers JWT]

You: "Got it, JWT authentication!"

📋 Question 3: Should we add caching?

**Recommended: Yes, Redis**
→ Fast in-memory cache, great for session storage and API caching

No caching
→ Simpler setup, fine for prototypes or low-traffic apps

Please let me know your preference (recommended: Yes, Redis).
```

### When to Use This Pattern

Use one-by-one questioning for:
- Architectural decisions (database, auth, caching)
- Multiple tool/technology choices
- Feature scope decisions
- Any multi-step planning that requires user input

### When Multiple Questions Aren't Needed

If there's only ONE question or the answer is obvious, just ask it directly without the elaborate format.

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
   - **If review passed (no more updates needed)** → **Auto-commit immediately (new session)**
   - **If review found issues** → **Spawn new session to fix** (see Fix Strategy below)

---

## Fix Strategy (When Review Finds Issues)

### Spawn New Session for Fixes

**Always spawn a NEW session for fixes.** The new session will have fresh context.

### When to Reuse (Rare Cases)

Only reuse an existing session if:
- Change is small AND low risk

Otherwise, always spawn new.

---

## Auto-Commit on Successful Review

When review session confirms code is good (no issues, no improvements needed):

### Commit Process

1. **Spawn NEW session for commit** — Don't reuse review session
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
3. Spawn NEW commit session → send "Commit with message: 'feat: add user authentication'"
4. Session commits → done
```

---

## Handling Post-Commit Bug Reports

When user reports a bug or issue after a task is completed:

### Session Strategy

**Spawn a NEW session for bug fixes.** Do NOT rely on previous discussion.

### Decision Flow

```
User: "there's a bug" / "this doesn't work" / "fix this issue"
    ↓
Spawn NEW session → Send: "Bug report: [description]. Please investigate and fix."
    ↓
After fix → Spawn NEW review session → Send: "A fix was made for [bug]. Please verify."
    ↓
Review passed → Spawn NEW commit session → commit
```

### When to Reuse (Only for Small + Low Risk)

- Tiny fix (typo, single line)
- Trivial change
- Otherwise → New session

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
