# Plan Approval Tracking: Critical Notes Rename

## Plan: Rename Critical Experience → Critical Notes
- File: `.agents/shared/planning/critical-notes-rename/plan-overview.md`
- Scope: LARGE — ~269 code occurrences, 82 tests, DB migration, 6 tool names, 2 agent definitions
- Phases: 5 (Core → API → Agents → Tests → Migration/Cleanup)

---

### Iteration 001 — APPROVED

**Date**: 2026-05-23
**Verdict**: APPROVED

**Verification Method**: 2 sequential council sessions verifying codebase claims against actual code.

**Phase 1-2 Verification**:
- All model/enum/constant names in `models.py` confirmed (CriticalExperienceCategory, CriticalExperiencePriority, CriticalExperience, CRITICAL_EXPERIENCE_MAX_ENTRIES, Project.critical_experience)
- Tool file and 3 tool functions confirmed (project_ce_add, project_ce_list, project_ce_remove)
- Registry entry format confirmed: `"critical_experience": "daemon.tools.critical_experience"`
- Instance import confirmed: `from .critical_experience import create_critical_experience_tools`
- API schema, router, and manager references confirmed at claimed locations
- Repository layer confirmed clean (no refs beyond models.py)

**Phase 3-4 Verification**:
- Leader meta.json confirmed with `"critical_experience"` in tools.allow
- Experiencer meta.json confirmed with `"critical_experience"` in tools.allow
- Two CE sections in experiencer rule.md confirmed (lines 122 and 199)
- Phase 7.5 in workflow.md confirmed (lines 148-169)
- CE tool docs in tools_note.md confirmed (lines 304-335)
- soul.md CE reference confirmed (line 16)
- All 4 dedicated test files exist at claimed paths
- MockProject and test function references in history test files confirmed at claimed lines
- Priority word "critical" at line 134 correctly identified as non-CE reference

**Notes**:
- CATEGORY_NAME and create_critical_experience_tools not in naming map but covered by Task 5
- ~269 occurrence count is approximate but doesn't affect execution
- Migration template matches existing convention
