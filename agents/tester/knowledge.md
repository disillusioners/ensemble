# Knowledge

Domain expertise and tool knowledge for the tester agent.

---

## Knowledge Tools

You have access to two knowledge tools:

### `experience(text, project_id?)` — Record knowledge
Use to record project experience, architectural decisions, or learned knowledge into the RAG knowledge base.
- Returns immediately, processes in background

---

## Explore Tool Workflow

**CRITICAL: explore() must always be called ALONE in a turn — never in parallel with other tools.**

### Correct Pattern
1. **Turn 1**: Call `explore("your query")` — alone, no other tools
2. **Turn 2**: Evaluate explore result. Only if insufficient, use file reads / bash / other tools in this turn
3. **Turn 3+**: Continue with implementation or further targeted exploration

### Multiple Explorations
You CAN call multiple explore() calls in the same turn:
- `explore("auth architecture")` + `explore("database schema")` ✅
- `explore("auth architecture")` + `read_file("src/auth.py")` ❌ — file read must be next turn

### Why Sequential?
- explore() invokes a specialist agent that does its own RAG + filesystem analysis
- Parallel calls waste the specialist's work and produce conflicting signals
- Other tools should only be used when explore() was insufficient

---

## Opencode Tools

### agent-browser

Browser automation tool for web frontend projects.

**Capabilities:**
- Automates browser interactions (clicking, typing, navigating)
- Can visually test and fix website bugs
- Takes screenshots and inspects DOM elements
- Handles forms, buttons, navigation flows

**When to Use:**
- Web frontend projects/sub-projects ONLY
- Visual testing and debugging
- UI interaction automation
- Screenshot-based bug fixing

**When NOT to Use:**
- Backend or API projects
- Non-web projects
- Headless/CLI applications

**Usage in Instructions:**
```raw
"Do browser automation (use agent-browser skill) to auto fix the website bug."
```

---

## Project Type Detection

When coordinating testing work, identify project type:

| Type | Indicators | Tool Recommendation |
|------|------------|---------------------|
| Web Frontend | HTML, CSS, React, Vue, Next.js | agent-browser available |
| Backend | Node.js, Python, Go, API routes | Standard tools only |
| Full-stack | Both frontend and backend | agent-browser for frontend parts |
| CLI/Headless | No UI, command-line | Standard tools only |

---

## When to Use explore()

**Priority Rule:**
- explore() > opencode_skill for gathering context
- explore() is faster (RAG query) and returns structured knowledge
- Only fall back to opencode_skill when explore() doesn't cover what you need

**Use explore() when:**
- Before writing tests, explore to understand test patterns and infrastructure
- When test conventions or testing utilities are unclear

**Skip explore() when:**
- Already have sufficient context from current session
- Results return low-confidence or empty (sparse KB) — proceed to opencode directly

**After gaining useful knowledge:**
- Use experience() to record learnings for future sessions