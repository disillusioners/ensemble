# Phase 4: Tests — Update All 82 Tests Across 6 Files

## Objective
Update all test files: rename imports, function names, test assertions, fixtures, and test descriptions from `critical_experience` / `CriticalExperience` / `project_ce_*` to `critical_notes` / `CriticalNotes` / `project_cn_*`. Includes `git mv` for all 6 test files.

## Coupling
- **Depends on**: Phase 1 (models/tools), Phase 2 (API/service)
- **Coupling type**: loose
- **Shared files with other phases**: Tests import from Phase 1/2 modules, but no shared file writes.
- **Why this coupling**: Tests are pure consumers. They import types and call APIs defined in Phases 1-2.

## Context
Phases 1-3 have renamed all production code. Tests must now match. There are **82 tests across 6 test files** (4 dedicated CE test files + 2 additional test files with CE references).

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update tool tests (36 tests) | Rename imports (`critical_experience` → `critical_notes`), function names (`project_ce_*` → `project_cn_*`), assertions, test descriptions, fixtures. **`git mv`** the test file. | `tests/unit/tools/test_critical_experience.py` → `tests/unit/tools/test_critical_notes.py` |
| 2 | Update schema tests (20 tests) | Rename `CriticalExperience` → `CriticalNotes`, `CriticalExperienceCategory` → `CriticalNotesCategory`, `CriticalExperiencePriority` → `CriticalNotesPriority`. Update imports. **`git mv`** the test file. | `tests/unit/test_critical_experience_schema.py` → `tests/unit/test_critical_notes_schema.py` |
| 3 | Update injection tests (14 tests) | Rename all references to the injection/rendering of critical experience in agent context. Update field names, assertion strings ("Critical Experience" → "Critical Notes"). **`git mv`** the test file. | `tests/unit/test_critical_experience_injection.py` → `tests/unit/test_critical_notes_injection.py` |
| 4 | Update API tests (10 tests) | Rename API field names in request/response bodies (`critical_experience` → `critical_notes`), endpoint assertions, test descriptions. **`git mv`** the test file. | `tests/unit/test_critical_experience_api.py` → `tests/unit/test_critical_notes_api.py` |
| 5 | Update project history functions test | Update `MockProject.critical_experience` attribute → `MockProject.critical_notes` (lines 159, 180). Rename `test_critical_experience_section_present` → `test_critical_notes_section_present` (line 349). Update any other CE references. **No file rename** — this file's name doesn't contain "critical_experience". | `tests/test_project_history_functions.py` |
| 6 | Update project history injection test | Update mock `to_dict.return_value` key from `"critical_experience": []` → `"critical_notes": []` (line 27). Rename `test_both_ce_and_history_sections_present` → `test_both_cn_and_history_sections_present` (line 244). Update any other CE references. **No file rename** — this file's name doesn't contain "critical_experience". | `tests/unit/test_project_history_injection.py` |
| 7 | Final grep sweep of tests | `grep -ri "critical_experience\|CriticalExperience\|project_ce_\|critical experience" tests/` — must return zero results. Fix any stragglers. | `tests/` |
| 8 | Run test suite | Execute `pytest tests/` and verify all 82 tests pass. | — |

## Key Files

### Dedicated CE Test Files (→ `git mv`)

| Old Filename | New Filename | Test Count |
|-------------|-------------|------------|
| `tests/unit/tools/test_critical_experience.py` | `tests/unit/tools/test_critical_notes.py` | 36 |
| `tests/unit/test_critical_experience_schema.py` | `tests/unit/test_critical_notes_schema.py` | 20 |
| `tests/unit/test_critical_experience_injection.py` | `tests/unit/test_critical_notes_injection.py` | 14 |
| `tests/unit/test_critical_experience_api.py` | `tests/unit/test_critical_notes_api.py` | 10 |

### Additional Files with CE References (update in-place, no rename)

| File | References | Action |
|------|-----------|--------|
| `tests/test_project_history_functions.py` | `MockProject.critical_experience` (lines 159, 180), `test_critical_experience_section_present` (line 349) | Rename attributes and test names |
| `tests/unit/test_project_history_injection.py` | `"critical_experience": []` mock (line 27), `test_both_ce_and_history_sections_present` (line 244) | Rename mock key and test name |

**Total: 82 tests across 6 files (80 in 4 dedicated files + 2 in history-related files)**

## Constraints
- Every test must pass after rename. Run full test suite to verify.
- Test logic should remain identical — only names change.
- If any test asserts on exact JSON key names (e.g., `"critical_experience"` in response body), those must update to `"critical_notes"`.
- If any test asserts on rendered markdown heading `"### ⚡ Critical Experience"`, update to `"### ⚡ Critical Notes"`.
- Test function names like `test_both_ce_and_history_sections_present` should update to use `cn` (critical notes) abbreviation.
- **This phase owns all `git mv` of test files** — no other phase should rename them.

## Deliverables
- [ ] All 4 dedicated test files `git mv`'d to new names
- [ ] 2 history test files updated in-place
- [ ] All imports updated to new module/type names
- [ ] All function call assertions use `project_cn_*` names
- [ ] All string assertions use "Critical Notes" terminology
- [ ] `pytest tests/` passes with 0 failures (82 tests)
- [ ] `grep -ri "critical_experience\|CriticalExperience\|project_ce_" tests/` returns zero results
