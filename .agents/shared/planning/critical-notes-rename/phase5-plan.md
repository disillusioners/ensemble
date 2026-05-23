# Phase 5: DB Migration, Tester Docs, & Final Cleanup

## Objective
Create a database migration to rename the `critical_experience` column to `critical_notes`, update tester agent documentation, fix the `project_history.py` docstring, and do a final sweep to ensure zero remaining references to old names.

## Coupling
- **Depends on**: Phase 1, 2, 3, 4 (all prior phases)
- **Coupling type**: loose
- **Shared files with other phases**: Migration file is new (no conflicts). Tester docs and docstring fix are files not touched by other phases.
- **Why this coupling**: Migration must know the exact old→new column name. Cleanup verifies all other phases completed correctly.

## Context
All production code and tests have been updated by prior phases. This phase handles: (1) the database schema change, (2) tester agent documentation, (3) one known docstring catch, and (4) a final verification sweep.

**⚠️ This phase does NOT do any `git mv` file renames.** Each prior phase owns its own file renames. This phase is verification-only for file renames.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create DB migration | Write a new migration file: `ALTER TABLE projects RENAME COLUMN critical_experience TO critical_notes;`. Follow existing naming convention (timestamp-based). Include idempotency comment per project convention. | `daemon/migrations/versions/YYYYMMDD_HHMMSS_rename_critical_experience_to_critical_notes.sql` |
| 2 | Verify migration up/down | Ensure migration has both up and down paths. Down should rename back to `critical_experience`. Check existing migration pattern for the down-migration convention. | (same migration file) |
| 3 | Update tester LESSONS doc | Update `.agents/tester/LESSONS/critical-experience-testing-patterns.md`: rename all 6 references to `create_critical_experience_tools()` → `create_critical_notes_tools()`. Update any other CE references in content. | `.agents/tester/LESSONS/critical-experience-testing-patterns.md` |
| 4 | Update tester PACKS doc | Update `.agents/tester/PACKS.md`: rename 4 references to test file names (old → new names) and test pack definitions. | `.agents/tester/PACKS.md` |
| 5 | Fix `project_history.py` docstring | Update `daemon/tools/project_history.py` line 17: docstring references "critical experience" → "critical notes". | `daemon/tools/project_history.py` |
| 6 | Global grep sweep: code | `grep -ri "critical_experience\|CriticalExperience\|project_ce_" daemon/ agents/ tests/` — must return zero results (except migration SQL files). Fix any stragglers. | All code |
| 7 | Global grep sweep: docs | `grep -ri "critical experience" daemon/ agents/ tests/ *.md docs/ README.md` — update any English-language references to "critical notes". Exclude: migration files (historical), `.agents/tester/RESULTS/` (historical), `.agents/approver/` (historical), `.agents/shared/planning/critical-experience/` (historical). | All docs |
| 8 | Global grep sweep: tool name patterns | `grep -ri "project_ce_" daemon/ agents/ tests/` — must be zero. | All code |
| 9 | Verify file renames completed | Confirm these files no longer exist at old paths: `daemon/tools/critical_experience.py`, `agents/_prompt_system/critical-experience.md`, `tests/unit/tools/test_critical_experience.py`, `tests/unit/test_critical_experience_*.py`. | — |
| 10 | Run full test suite | Execute `pytest tests/` and verify all tests pass. | — |
| 11 | Verify experiencer isolation | `grep -ri "critical" agents/experiencer/` — should return zero results related to critical notes/experience. Note: the generic adjective "critical" (priority level) at `rule.md` line 134 is expected and OK. | `agents/experiencer/` |

## Key Files
- New migration file in `daemon/migrations/versions/`
- `.agents/tester/LESSONS/critical-experience-testing-patterns.md` — 6 function references
- `.agents/tester/PACKS.md` — 4 test file/pack references
- `daemon/tools/project_history.py` — 1 docstring reference (line 17)
- All files modified by prior phases (verification only)

## Migration Template

```sql
-- Rename critical_experience column to critical_notes
-- If column was already renamed (fresh DBs created with correct name from create_all()),
-- the ALTER statement will fail with 'no such column' and the runner will skip them gracefully.

-- Up
ALTER TABLE projects RENAME COLUMN critical_experience TO critical_notes;

-- Down  
ALTER TABLE projects RENAME COLUMN critical_notes TO critical_experience;
```

## Historical Records — Do NOT Modify

These files contain historical records and should be left untouched:
- `.agents/tester/RESULTS/` — past test execution results
- `.agents/approver/` — past approval records
- `.agents/shared/planning/critical-experience/` — past planning documents
- `daemon/migrations/versions/20260520_000001_add_critical_experience_to_projects.sql` — original migration

## Constraints
- Existing data in `critical_experience` column must be preserved — this is a pure rename, no data transformation.
- File renames should NOT be done in this phase — each prior phase owns its own `git mv`.
- The idempotency comment in the migration must match the project convention from `20260522_000001_rename_history_columns.sql`.
- Historical records (`.agents/tester/RESULTS/`, `.agents/approver/`, `.agents/shared/planning/critical-experience/`) must NOT be modified.

## Final Verification Checklist
```bash
# Must return zero (excluding migration SQL files)
grep -ri "critical_experience\|CriticalExperience\|project_ce_" daemon/ agents/ tests/

# Must return zero (excluding migration files and historical records)
grep -ri "critical experience" daemon/ agents/ tests/

# Must NOT exist at old paths
ls daemon/tools/critical_experience.py              # should fail
ls agents/_prompt_system/critical-experience.md      # should fail
ls tests/unit/tools/test_critical_experience.py      # should fail
ls tests/unit/test_critical_experience_*.py          # should fail

# Must exist at new paths
ls daemon/tools/critical_notes.py                    # should succeed
ls agents/_prompt_system/critical-notes.md           # should succeed
ls tests/unit/tools/test_critical_notes.py           # should succeed

# Must pass
pytest tests/

# Experiencer should be clean (only generic "critical" adjective expected)
grep -ri "critical" agents/experiencer/
```

## Deliverables
- [ ] DB migration created with up/down paths and idempotency comment
- [ ] Tester LESSONS doc updated (6 references)
- [ ] Tester PACKS doc updated (4 references)
- [ ] `project_history.py` docstring updated
- [ ] Global grep sweep passes with zero stale references
- [ ] All old file paths confirmed nonexistent
- [ ] Full test suite passes
- [ ] Experiencer agent has zero critical-notes references
- [ ] Historical records left untouched
