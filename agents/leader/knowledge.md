# Knowledge

## Knowledge Tools

You have access to two knowledge tools:

### `explore(query, mode?, project_id?)` — PRIMARY knowledge retrieval
**Always try explore() FIRST** when you need project knowledge, architecture info, or answers about a codebase.
- It queries the RAG knowledge base AND browses project files if needed
- It is faster and more comprehensive than manual file browsing
- It may also update the knowledge base asynchronously if it finds stale data
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

### `experience(text, project_id?)` — Record knowledge
Use to record project experience, architectural decisions, or learned knowledge into the RAG knowledge base.
- Returns immediately, processes in background


