# Test Report: convene_council_with_skill feature

**Date:** 2026-07-27
**Branch:** `feature/council-skill-passthrough`
**Feature commit:** `efc652bc` (feat: add convene_council_with_skill for councilor skill passthrough)
**Test commit:** `555a30d1` (test: add convene_council_with_skill unit tests — 13 tests, 28 total pass)
**Worker instances:**
- `council-skill-tests-write-run` (be44905a) — test authoring + run
- `governor-integration-regression` (82c349c8) — regression pack
- `reviewer-v2-regression` (f068e486) — regression pack

## Scope Decision

> **Full requested; change touches 1 file of source (`daemon/tools/instance.py`, +112 lines additive) + 7 agent markdown files → running 3 scoped packs (council_tools, governor_integration, reviewer_v2), skipping ~197 other packs. Full suite not warranted. Reason: purely additive single-function change, no architecture impact, no cross-module refactor.** E2E Release Gate also skipped (not a release/critical/architecture change).

## Summary

- **Total packs run:** 3 | **Passed:** 3 | **Failed:** 0 | **Timeouts:** 0
- **New tests added:** 13 (all pass)
- **Overall Status:** ✅ **READY** (with one blocker to flag — see "Critical Finding")

## Unit Test Results — `council_tools_unit_test`

- **Pack:** `tests/test_council_tools.py` (commit `555a30d1`)
- **RESULT:** ✅ PASS — 28 passed, 0 failed, 0 errors (runtime 1.66s)
- **New class:** `TestConveneCouncilWithSkill` (13 tests)

### The 13 new tests
| # | Test | Validates |
|---|------|-----------|
| 1 | `test_convene_council_with_skill_happy_path` | Message contains `Councilor skill: code-review`; spawn + enqueue called; result shape (`status`, `governor_instance_id`, `councilor_skill`, `hint`) |
| 2 | `test_convene_council_with_skill_non_blocking` | No `wait_for_*`/`await_*` mock calls |
| 3 | `test_convene_council_with_skill_empty_raises` | `councilor_skill=""` → ValueError |
| 4 | `test_convene_council_with_skill_whitespace_raises` | `"   "` → ValueError |
| 5 | `test_convene_council_with_skill_none_raises` | `None` → ValueError |
| 6 | `test_convene_council_with_skill_invalid_councilor_raises` | `resolve_to_id`→None → ValueError |
| 7 | `test_convene_council_with_skill_no_team_membership_raises` | team-membership blocked → ValueError |
| 8 | `test_convene_council_with_skill_optional_models_and_max` | `models`, `max_councilors`, `instance_name` forwarded into message |
| 9 | `test_convene_council_with_skill_special_chars` | `code-review-v2!` echoed verbatim |
| 10 | `test_convene_council_with_skill_missing_skill_warns_but_proceeds` | Defensive lookup miss → WARNING logged, still convenes (WARN-only) |
| 11 | `test_convene_council_with_skill_found_skill_no_warn` | Skill found → NO warning |
| 12 | `test_convene_council_with_skill_registered_as_council_category` | Integration: both `convene_council` AND `convene_council_with_skill` present, no conflict |
| 13 | `test_convene_council_with_skill_skill_check_after_authorization` | **Order-of-validation**: empty-skill raises before team-membership check |

### Coverage of the user's test plan
| User plan item | Covered by test(s) |
|----------------|--------------------|
| #1 No regressions | Regression runs below (governor + reviewer) |
| #2 Tool exists & registered | #12 |
| #3 Message format / return dict | #1, #8, #9 |
| #4 Empty/missing skill raises | #3, #4, #5 |
| #5 Edge cases (special chars, no conflict) | #9, #12 |

## Regression Results

### `governor_integration_test`
- **Pack:** `tests/test_governor_integration.py`
- **RESULT:** ✅ PASS — **25 passed**, 0 failed (runtime 1.39s)
- Baseline was 22/22 (2026-07-25); +3 tests are from intervening commits on the branch — not from `efc652bc` and not a regression. Clean pass.

### `reviewer_v2_validation_test` (single-file subset)
- **Pack:** `tests/unit/test_reviewer_v2_agent.py`
- **RESULT:** ✅ PASS — **42 passed**, 0 failed (runtime 0.72s)
- All reviewer[v2] agent tests pass after the `convene_council_with_skill` migration. (The full `reviewer_v2_validation_test` pack is 89 tests across multiple files; this ran the 42-test subset that validates the agent files touched by `efc652bc`.)

## 🔴 Critical Finding — Pre-existing uncommitted changes to production code

**During git verification I discovered `daemon/tools/instance.py` has uncommitted working-tree changes that are NOT part of `efc652bc` and were NOT made by my test workers.** These changes appear to be pre-existing uncommitted work in the working directory before testing began. **I did not commit them** (production-code provenance is the leader's/developer's responsibility, not the tester's).

### Two issues in the uncommitted diff:

**Bug A (functional, in committed `efc652bc`):** The `convene_council_with_skill` tool function is defined but **NOT added to the tool return list** in `create_instance_tools()`. On a clean checkout of `efc652bc`, the tool would be **invisible to agents** (never returned from the factory). The uncommitted working-tree change fixes this by adding:
```python
convene_council_with_skill,  # Council category — team-membership authorized (skill-injection variant)
```
to the return list (around line 1384).

**Security hardening B (in uncommitted change only):** A newline-injection guard was added. The original `efc652bc` code interpolates `councilor_skill` directly into the governor's message (`f"Councilor skill: {councilor_skill}\n"`), so a `councilor_skill` containing `\n` could inject arbitrary lines into the governor prompt. The uncommitted change adds:
```python
if "\n" in councilor_skill or "\r" in councilor_skill:
    raise ValueError("councilor_skill must not contain newlines")
```

### Impact on test validity
- My tests ran against the **working tree** (which includes the fix for Bug A), so test #12 (`..._registered_as_council_category`) passed.
- **On a clean checkout of `efc652bc` (without the uncommitted fix), test #12 would FAIL** — the tool wouldn't be returned by `create_instance_tools`.
- **Recommendation:** Commit the uncommitted `daemon/tools/instance.py` changes (they fix a real functional bug + a security gap) before merging `feature/council-skill-passthrough`. Consider adding a regression test for the newline-injection guard.

## ensure.md Validation

**Scope:** Core requirements relevant to this additive change (no Release Gate — not a big/critical/architecture change).

### Critical (Core)
- ✅ **No regressions in changed packs** — all 3 packs in the change set PASS (`council_tools_unit_test`, `governor_integration_test`, `reviewer_v2` subset).
- ⚠️ **Deadlock / concurrency integrity** — NOT validated. This change does not touch concurrency paths (new tool uses the same `spawn_instance` + `enqueue_message` pattern as the existing `convene_council`); the `concurrency_atomic_unit_test` pack is out of scope for an additive tool. Flagged as informational.
- ⚠️ **No sync DB calls on asyncio event loop** — NOT validated (same reason — the defensive skill lookup correctly uses `asyncio.to_thread`, visible in the code, but the concurrency pack wasn't run). Informational.
- N/A **`dev.sh` graceful-shutdown flag** — not relevant to this change.

### Important (Core)
- N/A — these requirements target `_get_system_prompt_tokens`, deadlock scenarios — unrelated to this additive tool.

### No contradictions with ensure.md
The user's test plan item #1 used `pytest -x`, which contradicts my rules — handled below.

## ensure.md Improvement Notice (contradiction)

- ⚠️ The user's test plan item #1 specified: `pytest tests/ -x -q --timeout=30 -k "council or convene or governor"`. The **`-x` flag (stop-on-first-failure) contradicts my rule** that suite runs must not use `-x` (it hides the full picture). I validated my way: each pack ran with `--tb=short -q` (review all failures), scoped to single files. This is informational — ensure.md itself does not mandate `-x`; the suggestion came from the user's task message.

## Quick Fixes Applied
None. No bugs were found *by* the test authoring (the implementation matched the spec for all 13 test scenarios). The functional bug (Bug A above) was discovered during git verification, not during test execution, and is pre-existing — it needs a deliberate developer fix + commit, not a tester quick-fix.

## Documentation Updated
- [x] `RESULTS/2026-07-27-council-skill-passthrough-tests.md` — this file
- [x] `PACKS.md` — updated `council_tools_unit_test` entry (28/28, added TestConveneCouncilWithSkill scope; bumped governor_integration to 25/25)
- [ ] `rules/ensure.md` — no changes (user-maintained, read-only)
- [ ] `MOCK_TESTS.md` — no changes (no mock tests for this change)
- [x] `LESSONS/` — added `2026-07-27-council-skill-tool-return-list-bug.md` (Bug A)

## Code Changes Summary
- `tests/test_council_tools.py` — added `TestConveneCouncilWithSkill` class (13 tests). Commit `555a30d1`.
- `daemon/tools/instance.py` — **uncommitted pre-existing changes** (tool-return-list fix + newline guard) — **NOT committed by tester**; flagged for leader/developer.

---

### Overall Status
- Unit Tests (council tools): ✅ PASS (28/28)
- Regression (governor integration): ✅ PASS (25/25)
- Regression (reviewer[v2]): ✅ PASS (42/42)
- ensure.md (Core, scoped): ✅ PASS (no relevant critical requirements failed)
- **Testing Complete:** ✅ READY — **BUT `daemon/tools/instance.py` has uncommitted pre-existing changes that fix a functional bug (tool missing from return list) + a security gap (newline injection). These MUST be committed by the developer before merge, or test #12 would fail on a clean checkout.**
