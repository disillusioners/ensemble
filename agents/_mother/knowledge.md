# Knowledge

## Knowledge Tools

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
- **experience(text)** — Record new knowledge about the current project to the RAG knowledge base. Use this when you learn something worth remembering for future sessions.


