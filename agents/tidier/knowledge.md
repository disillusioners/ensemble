# Knowledge

## Knowledge Tools

You have access to two knowledge tools:

### `explore(query, mode?, project_id?)` — PRIMARY knowledge retrieval

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

## When to Use explore()

### Priority Rule
- **explore() > opencode_skill** for gathering context
- explore() is faster (RAG query) and returns structured knowledge
- Only fall back to opencode_skill when explore() doesn't cover what you need

### Use explore() when
- Before tidying code, explore to check existing code conventions and style patterns
- When unsure about formatting or organizational conventions

### Skip explore() when
- Already have sufficient context from current session
- Results return low-confidence or empty (sparse KB) — proceed to opencode directly

### After gaining useful knowledge
- Use experience() to record learnings for future sessions

