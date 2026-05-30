# Project Knowledge

## Purpose

The RAG knowledge system stores **agent knowledge about projects**. It persists across working sessions and is shared between agents — enabling continuity and collaboration.

---

## Knowledge Tools

### Querying Knowledge: `explore(query)`

Search the RAG knowledge base for project-specific knowledge.

```
explore(query="What database does this project use?")
explore(query="How is authentication implemented?")
```

### Recording Knowledge: `experience(text)`

Record new knowledge about the current project.

```
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

```
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

## Shared Context for External Agent Systems

When controlling external agent systems (opencode, etc.), you MUST include the following context template in your prompt. This ensures the external system has access to shared exploration context accumulated during this session.

**Context template to include**:

> Current context-key is: {{ENSEMBLE_CONTEXT_KEY}}
>
> We are working in a multi-agent system environment named ensemble. Current shared-explored context and knowledge base files are under this directory:
>
> {{ENSEMBLE_SHARED_CONTEXT_DIR}}
>
> Important: Read and understand all shared context files in that directory first before proceeding.

The `{{ENSEMBLE_CONTEXT_KEY}}` and `{{ENSEMBLE_SHARED_CONTEXT_DIR}}` placeholders are automatically resolved when your system prompt is assembled.

---

## Important Notes

- The `.agents/` directory is **project-specific** — each project has its own
- It is **separate** from your agent persona (which lives in `agents/` of the ensemble system)
- **NOT your personality or rules** — this is project experience stored in RAG
- Use `read_file`, `write_file` tools with project `workdir` to access `.agents/shared/`
- Use `explore()` and `experience()` tools for project knowledge access
