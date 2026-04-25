# Phase 1: Create Innate-Skills Directory

## Objective

Create the `agents/innate-skills/` directory and populate it with all 4 distinct skills extracted from per-agent `skills/` directories. This is a pure file extraction — no code changes.

## Coupling

- **Depends on**: None
- **Coupling type**: — (root phase)
- **Shared files with other phases**: `agents/innate-skills/*/skill.md` (Phase 3 reads these)
- **Shared APIs/interfaces**: None
- **Why this coupling**: Phase 3's loader code references the paths created here

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `agents/innate-skills/` directory | Create the top-level directory | `agents/innate-skills/` |
| 2 | Extract `opencode` skill | Copy from any one of the 6 identical copies (e.g., `coder/skills/opencode/skill.md`) → `agents/innate-skills/opencode/skill.md` | `agents/innate-skills/opencode/skill.md` |
| 3 | Extract `coordination` skill | Copy `leader/skills/coordination/skill.md` → `agents/innate-skills/coordination/skill.md` | `agents/innate-skills/coordination/skill.md` |
| 4 | Extract `job-orchestration` skill | Copy `jober/skills/job-orchestration/skill.md` → `agents/innate-skills/job-orchestration/skill.md` | `agents/innate-skills/job-orchestration/skill.md` |
| 5 | Extract `test-pack` skill | Copy `tester/skills/test-pack/skill.md` → `agents/innate-skills/test-pack/skill.md` | `agents/innate-skills/test-pack/skill.md` |
| 6 | Verify content integrity | `diff` each extracted file against its source to confirm byte-for-byte match | All `skill.md` files |

## Key Files

- `agents/innate-skills/opencode/skill.md` — The opencode skill (220 lines), currently duplicated across 6 agents
- `agents/innate-skills/coordination/skill.md` — Leader coordination skill (54 lines)
- `agents/innate-skills/job-orchestration/skill.md` — Job orchestration skill (232 lines)
- `agents/innate-skills/test-pack/skill.md` — Test pack skill (86 lines)

## Constraints

- **Exact content preservation**: Files must be byte-for-byte identical to their sources. Use `cp`, not manual recreation.
- **No code changes in this phase**: Only create directories and copy files.
- **Do NOT delete source files yet**: Old `skills/` directories stay until Phase 4 (backward compatibility).

## Deliverables

- [ ] `agents/innate-skills/` directory exists with 4 subdirectories
- [ ] Each subdirectory contains a `skill.md` file
- [ ] All 4 files verified identical to their original sources via `diff`
