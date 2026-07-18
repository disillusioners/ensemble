# Phase 1: Backup Current Tester Prompts

## Objective
Snapshot the current (stale) tester prompt files before rewriting, so the pre-edit state is recoverable and reviewable.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: —
- **Shared files with other phases**: soul.md, rule.md, tools_note.md (Phase 2 overwrites these AFTER backup)
- **Why this coupling**: Backup must complete before Phase 2 edits. This is a tight dependency on file-preservation, not on content.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create backup directory | `mkdir -p backup/agents/tester/` | `backup/agents/tester/` |
| 2 | Copy core prompt files | Copy the 3 files that Phase 2 will rewrite | soul.md, rule.md, tools_note.md → `backup/agents/tester/` |
| 3 | Copy skill templates (optional) | Snapshot the 8 skill templates before Phase 3 edits | `skills-template/*.md` → `backup/agents/tester/skills-template/` |
| 4 | Copy skill-set.yaml + workflow.md (reference) | Preserve the files that are NOT being changed, for full-context recovery | skill-set.yaml, workflow.md → `backup/agents/tester/` |
| 5 | Verify backup integrity | `diff` or file-count check to confirm copies match | all copied files |

## Key Files
- **Source**: `agents/tester/soul.md` (4062 bytes), `agents/tester/rule.md` (15533 bytes), `agents/tester/tools_note.md` (1703 bytes)
- **Source**: `agents/tester/skills-template/*.md` (9 files)
- **Source**: `agents/tester/skill-set.yaml`, `agents/tester/workflow.md`
- **Target**: `backup/agents/tester/` (full agent snapshot)

## Exact Commands (reference)
```bash
mkdir -p backup/agents/tester/skills-template
cp agents/tester/soul.md backup/agents/tester/
cp agents/tester/rule.md backup/agents/tester/
cp agents/tester/tools_note.md backup/agents/tester/
cp agents/tester/skills-template/*.md backup/agents/tester/skills-template/
cp agents/tester/skill-set.yaml backup/agents/tester/
cp agents/tester/workflow.md backup/agents/tester/
# Verify:
ls -la backup/agents/tester/
ls -la backup/agents/tester/skills-template/
```

## Constraints
- Backup directory does NOT exist yet (confirmed via `ls`)
- Preserve original file timestamps/modes where possible (`cp` default is fine)
- This is a simple copy — no content transformation

## Deliverables
- [ ] `backup/agents/tester/` exists with soul.md, rule.md, tools_note.md
- [ ] `backup/agents/tester/skills-template/` exists with 9 skill files
- [ ] `backup/agents/tester/skill-set.yaml` and `workflow.md` present
- [ ] File counts match source

## Est. Time: 10 minutes
