# Pre-existing Test Rot: Gaia Scripts + Inner Soul/Memory Fixtures

**Date:** 2026-07-09  
**Discovered during:** system-info-tools testing (commit 9f90f78e)  
**Severity:** Informational (not blocking — pre-existing, not caused by system-info-tools PR)

## Issue 1: TestGaiaScriptAccessibility (7 tests FAIL)

**File:** `tests/unit/test_gaia_agent.py` (class `TestGaiaScriptAccessibility`, lines ~535-555)

**Symptom:** `FileNotFoundError: agents/gaia/scripts` — tests expect `agents/gaia/scripts/npx.md` to exist.

**Root Cause:** Commit `9102e620 "improving gaia"` deleted `agents/gaia/scripts/npx.md` (203 lines removed) but the `TestGaiaScriptAccessibility` tests added in commit `04a6f653` were never updated.

**Failing tests:**
1. `test_scripts_directory_exists`
2. `test_npx_script_exists`
3. `test_npx_script_is_readable`
4. `test_npx_script_has_content`
5. `test_scripts_directory_listable`
6. `test_no_symlinks_in_scripts`
7. `test_scripts_are_markdown_files`

**Fix options (NOT quick fix — content/maintenance decision needed):**
- Option A: Restore `agents/gaia/scripts/npx.md` if the script was deleted in error
- Option B: Delete/update `TestGaiaScriptAccessibility` if the scripts directory was intentionally removed

---

## Issue 2: Inner Soul + Memory Archive Fixture Failures (66 tests FAIL)

**Files:** `test_inner_soul_compound.py`, `test_inner_soul_redirect.py`, `test_inner_soul_rejection.py`, `test_archive_lifecycle.py`, `test_memory_edge_cases.py`

**Symptom:** Various — `re.search` called on `MagicMock`, "Access denied" where "Archived Memory" expected.

**Root Cause:** `daemon/tools/inner_soul.py:1381` — `re.search` receives a `MagicMock` instead of a string (test fixture bypass). Archive access tests return "Access denied" unexpectedly.

**Fix:** Needs investigation into test fixture setup for inner_soul/memory resource stack. Not a quick fix — involves fixture mocking architecture.

---

## Recommendation

Open a follow-up ticket to address these pre-existing test failures separately. They do not block any current PR.
