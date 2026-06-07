# Project Knowledge

## Purpose

The RAG knowledge system stores **agent knowledge about projects**. It persists across working sessions and is shared between agents — enabling continuity and collaboration.

---

## Knowledge Tools

### Querying Knowledge: `explore(query)`

Search the RAG knowledge base for project-specific knowledge.

```raw
explore(query="What database does this project use?")
explore(query="How is authentication implemented?")
```

### Recording Knowledge: `experience(text)`

Record new knowledge about the current project.

```raw
experience(text="The project uses PostgreSQL with SQLAlchemy ORM for database access")
experience(text="Authentication is JWT-based with 1-hour token expiry")
```

---

## Knowledge Update Duty

When working, you have a duty to keep the KB accurate. Call `experience()` in these cases:

### When to Update

| Scenario | What to Record |
|----------|---------------|
| **Explored but info was insufficient** — you had to dig through code or ask follow-ups | The missing knowledge that would have helped |
| **Found KB is outdated/wrong** — explore() returned stale or incorrect info | The corrected knowledge |
| **You changed project state** — after implementing changes | What changed at a conceptual level |
| **Discovered new knowledge** — learned something others would benefit from | The insight or pattern |

### What to Record vs Skip

**Record**: Architecture decisions, patterns, how systems connect, configuration approaches, gotchas, conventions, project structure insights, tool behavior quirks

**Skip**: Raw code, file contents, line numbers, temporary state, implementation details that change frequently, exact API signatures (those can be explored)

### How

```raw
experience(text="Architecture: The job queue uses a 7-state lifecycle with lock-first pattern for concurrency")
experience(text="Gotcha: queue_id must propagate through all layers (router → service → repository → SQL)")
```

One call per insight. Keep entries focused and self-contained.

---

## Guidelines

1. **First work session on a project**: Use `explore(query)` to recall what you already know about this project.
2. **Learn something new**: Use `experience(text)` to record the insight for future sessions.
3. **Create a plan**: Write to `.agents/shared/planning/{feature}/`.
4. **Coordinate**: Update `.agents/shared/context.md` when project state changes.
5. **Keep it relevant**: Only record knowledge that helps future work sessions be more effective.

---

## Migration from File-Based Memory

The old file-based memory system (`.agents/{agent-id}/memories/`) has been migrated to the RAG knowledge base. If you see references to `.agents/{agent-id}/memories/` in older documentation, use `explore()` and `experience()` instead.

---

## Exploration Priority Rules

Agents sometimes bypass the internal `explore()` tool and use external agent systems (opencode, etc.) directly for codebase exploration. This is **wrong** — `explore()` has a critical side-effect that benefits the entire system.

### Why This Matters

When you call `explore(query)`, the results are written to the shared context directory keyed by your `CONTEXT_KEY` (see the `## Context Key` section of your system prompt). External agent systems automatically read this directory before starting work. If you skip `explore()` and use external tools directly, the shared context stays empty — both your agent and external systems lose accumulated knowledge.

To inspect the shared context directory from inside an internal agent, use the `list_context(context_key)` and `read_context(context_key, filename)` tools (pass the `CONTEXT_KEY` from your system prompt). External systems should use the hosted MCP `ensemble_context_list` / `ensemble_context_read` tools.

### Rules

1. **`explore()` is MANDATORY before external exploration.** Before using any external agent system (opencode-skill, etc.) to explore or understand code/project state, you MUST first call `explore(query)` for the same topic. No exceptions.

2. **Do not re-explore what `explore()` already answered.** If `explore()` provides sufficient information, do not duplicate the same query via external tools. Only use external tools for follow-up details that `explore()` could not provide.

3. **Follow the explore-first workflow:**
   - Call `explore(query)` with your question
   - Assess whether the result is sufficient for your needs
   - If insufficient, THEN use external tools for deeper investigation
   - Record any new findings with `experience()` so future sessions benefit

4. **This rule is NON-NEGOTIABLE.** Even if external tools seem faster, `explore()` must come first. The shared context benefit for the entire multi-agent system outweighs any perceived speed advantage.


