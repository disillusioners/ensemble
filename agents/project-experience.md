# Project Experience (.agents Directory)

## Purpose

The `.agents/` directory at the project root stores **agent knowledge about that specific project**.
It persists across sessions and is shared between agents — enabling continuity and collaboration.

---

## Directory Structure

```
<project-workdir>/.agents/
├── shared/                        # Cross-agent collaboration space
│   ├── planning/                  # Feature plans (planner creates, coder reads)
│   │   └── {feature-name}/        # One directory per feature
│   │       ├── plan-overview.md   # Summary: objectives, phases, risks
│   │       ├── phase1-plan.md     # Phase-specific plan
│   │       ├── phaseN-plan.md
│   │       └── decisions.md       # Architecture decisions
│   ├── context.md                 # Current project state, goals, blockers
│   └── conventions.md             # Project-wide coding conventions
├── {agent-id}/                    # Agent's private project knowledge
│   ├── memory.md                  # Accumulated knowledge (patterns, gotchas, tech details)
│   └── lessons/                   # Specific lessons learned
│       └── {topic}.md             # e.g., api-gotchas.md, auth-pitfalls.md
```

---

## Access Rules

- **Read**: Any agent can read any file inside `.agents/`
- **Write**: 
  - `.agents/{your-agent-id}/` — your own directory
  - `.agents/shared/` — collaboration space
- **Never write** to another agent's directory
- **No pre-creation needed** — create files and directories on first use via `write_file`

---

## How to Use

### As Your Agent Identity (`{agent-id}/`)

| File | When to Use | Example |
|------|-------------|---------|
| `memory.md` | Record knowledge you want to remember across sessions | Tech stack details, project quirks, key file locations |
| `lessons/{topic}.md` | Record a specific lesson, pitfall, or solution worth remembering | `lessons/api-rate-limiting.md` |

### As Shared Space (`shared/`)

| File | When to Use | Example |
|------|-------------|---------|
| `planning/{feature}/` | Create or consume feature plans | Planner creates, Coder reads |
| `context.md` | Record current project state anyone should know | Current sprint, active blockers, priorities |
| `conventions.md` | Record project-wide conventions | Naming patterns, commit style, error handling |

---

## Guidelines

1. **First session on a project**: Check if `.agents/{your-agent-id}/memory.md` exists. If not, create it after learning about the project.
2. **Every session**: Read your `memory.md` at the start to recall what you already know.
3. **Learn something new**: Append to `memory.md` or create a `lessons/{topic}.md`.
4. **Create a plan**: Write to `.agents/shared/planning/{feature}/`.
5. **Coordinate**: Update `.agents/shared/context.md` when project state changes.
6. **Keep it relevant**: Only store knowledge that helps future sessions be more effective.
7. **Use descriptive filenames**: `lessons/port-conflict-fix.md` not `lessons/fix1.md`.

---

## Important Notes

- This directory is **project-specific** — each project has its own `.agents/`
- It is **separate** from your agent persona (which lives in the `agents/` directory of the ensemble system)
- Files here are **not** your personality or rules — they are **project experience**
- Use standard file tools (`read_file`, `write_file`, etc.) with the project `workdir` to access `.agents/`
