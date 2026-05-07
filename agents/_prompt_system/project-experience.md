# Project Experience

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

## Important Notes

- The `.agents/` directory is **project-specific** — each project has its own
- It is **separate** from your agent persona (which lives in `agents/` of the ensemble system)
- Use `read_file`, `write_file` tools with project `workdir` to access `.agents/shared/`
