# Project Experience (.agents Directory)

## Purpose

The `.agents/` directory at the project root stores **agent knowledge about that specific project**.
It persists across working sessions and is shared between agents — enabling continuity and collaboration.

---

## Directory Structure

```
<project-workdir>/.agents/
├── shared/                        # Cross-agent collaboration space
│   ├── planning/                  # Feature plans (planner creates, coder reads)
│   │   └── {feature-name}/        # One directory per feature
│   │       ├── plan-overview.md   # Summary: objectives, phases, risks
│   │       ├── phase1-plan.md
│   │       ├── phaseN-plan.md
│   │       └── decisions.md
│   ├── context.md                 # Current project state, goals, blockers
│   └── conventions.md             # Project-wide coding conventions
├── {agent-id}/                    # Agent's private project experience
│   └── memories/                   # Individual experience entries
│       └── {timestamp}-{descriptive-title}.md
│       # e.g., 2026-04-01-k8s-db-connection.md
```

---

## Access Rules

- **Read**: Any agent can read any file inside `.agents/`
- **Write**: 
  - `.agents/{your-agent-id}/memories/` — your own memories
  - `.agents/shared/` — collaboration space
- **Never write** to another agent's memories directory
- **No pre-creation needed** — create files on first use via `write_file`

---

## How to Use

### Your Memories (`{agent-id}/memories/`)

Each file is one memory/experience. Name format:

```
{date}-{descriptive-title}.md
# e.g., 2026-04-01-postgresql-telepresence-setup.md
```

Use descriptive titles so future you (and other agents) can find relevant memories quickly.

| When | What to Write |
|------|---------------|
| Learn something new | New memory file with the insight |
| Discover a quirk | New memory file with the gotcha |
| Update understanding | Update existing relevant memory |

### Shared Space (`shared/`)

| File | When to Use |
|------|-------------|
| `planning/{feature}/` | Create or consume feature plans |
| `context.md` | Record current project state |
| `conventions.md` | Record project-wide conventions |

---

## Guidelines

1. **First work session on a project**: Explore `.agents/{your-id}/memories/` to recall what you already know about this project.
2. **Learn something new**: Create a new memory file in `.agents/{your-id}/memories/`.
3. **Create a plan**: Write to `.agents/shared/planning/{feature}/`.
4. **Coordinate**: Update `.agents/shared/context.md` when project state changes.
5. **Keep it relevant**: Only store knowledge that helps future work sessions be more effective.
6. **Descriptive filenames**: `2026-04-01-api-rate-limiting.md` not `memory1.md`.

---

## Important Notes

- This directory is **project-specific** — each project has its own `.agents/`
- It is **separate** from your agent persona (which lives in `agents/` of the ensemble system)
- **NOT your personality or rules** — this is project experience
- Use `read_file`, `write_file` tools with project `workdir` to access `.agents/`
