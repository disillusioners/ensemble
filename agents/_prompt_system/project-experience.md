# Project Experience

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

## Shared Project Space (`.agents/shared/`)

The `.agents/shared/` directory at the project root stores cross-agent collaboration files:

```
<project-workdir>/.agents/shared/
├── planning/                  # Feature plans (planner creates, coder reads)
│   └── {feature-name}/        # One directory per feature
│       ├── plan-overview.md   # Summary: objectives, phases, risks
│       ├── phase1-plan.md
│       └── decisions.md
├── context.md                 # Current project state, goals, blockers
└── conventions.md             # Project-wide coding conventions
```

### Access Rules

- **Read/Write**: Any agent can read/write `.agents/shared/`
- **Planning**: Use `read_file` and `write_file` with project `workdir` to access shared files
- **Context**: Update `.agents/shared/context.md` when project state changes
- **Conventions**: Reference `.agents/shared/conventions.md` for project-specific coding standards

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

## Important Notes

- The `.agents/` directory is **project-specific** — each project has its own
- It is **separate** from your agent persona (which lives in `agents/` of the ensemble system)
- **NOT your personality or rules** — this is project experience stored in RAG
- Use `read_file`, `write_file` tools with project `workdir` to access `.agents/shared/`
- Use `explore()` and `experience()` tools for project knowledge access
