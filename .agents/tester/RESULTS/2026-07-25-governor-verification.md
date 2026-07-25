# Test Report: Governor Council-Manager Agent — Independent Verification
Date: 2026-07-25 14:50 UTC
Feature commit: `9237acbc` (Merge feature/governor-council-manager into latest)
Branch: `latest`

## Summary
- **Total tests run: 67** | **Passed: 67** | **Failed: 0** | **Errors: 0** | **Skipped: 0**
- Governor tests: 40/40 (11 Phase 2 + 7 Phase 3 + 22 Phase 4) — matches expected count exactly
- Regression: 27/27 spawn_team_members — **NO REGRESSION**
- ensure.md: ✅ Critical requirement "No regressions in changed packs" — PASS
- Quarantined: 0 tests skipped (QUARANTINE.md empty)
- Quick fixes applied: 0 (verification-only run, no modifications)

## Scope Decision
> Independent test verification of the Governor Council-Manager Agent (merged at `9237acbc`).
> Scope: 3 governor test files (Phase 2/3/4) + 1 regression baseline (spawn_team_members).
> Full suite NOT warranted — this is a verification task for a specific merged feature, not a broad refactor.
> Skipped: E2E (governor is backend-only, no UI), full non-integration suite, mock-test packs.
> Reason: Task explicitly requested governor + regression verification; change is feature-scoped.

## Test Execution Results

| Pack | File | Tests | Result | Runtime |
|------|------|-------|--------|---------|
| council_tools_unit_test | tests/test_council_tools.py | 11/11 | ✅ PASS | 1.21s |
| inject_allowed_models_unit_test | daemon/tests/test_inject_allowed_models.py | 7/7 | ✅ PASS | 0.87s |
| governor_integration_test | tests/test_governor_integration.py | 22/22 | ✅ PASS | 1.34s |
| spawn_team_members_unit_test (regression) | tests/test_spawn_team_members.py | 27/27 | ✅ PASS (NO REGRESSION) | 1.62s |

All 4 packs run in parallel via 4 worker instances (load_skill="test-pack-execution").

### Coverage Verified (governor tests)
- **C5** — tool filter survival across council orchestration ✅
- **C6** — inject_allowed_models flag survives AgentRegistry.discover() ✅
- **C1** — clear_councilor_errors clears sticky parent-error flag ✅
- **W7** — model canonicalization (case-insensitive) + strict rejection (no fallback) ✅
- **C2** — manager.config (no underscore) access path ✅
- **W6/W8** — canonical_model passthrough; error path appends status="error" block ✅
- **meta.json contract** — required fields present on disk ✅
- **backward-compat** — spawn_instance still bound for non-governor agents ✅

## Mock Quality Assessment (static code analysis)

**Verdict: STRONG — the 40 passing governor tests can be trusted.**

### A. Mock Fidelity — STRONG
- **DependencyBus**: Phase 4 integration test uses a REAL DependencyBus over in-memory SQLite,
  manipulating real `_parent_errored`/`_parent_error_message` fields and calling real
  `had_parent_error()`/`parent_error_message()` read paths. Field/method names match production
  exactly (`dependency_bus.py:418`, `:436`, `:1487`). Phase 2 unit test uses a loose MagicMock stub
  (acceptable — verifies tool control flow only; integration test covers state mutation).
- **`_resolve_model_override`**: test helper `_make_manager` reimplements the function as a local
  closure. Verified line-by-line to be behaviorally equivalent to real `instance_lifecycle.py:1036-1075`
  (None/empty/whitespace→None, empty allowed→pass-through, case-insensitive exact match, returns
  caller spelling). ⚠️ It is a faithful COPY, not a call to the real function — see §C8.
- **`spawn_instance`**: MagicMock accepts kwargs; tests assert on `model`, `agent_id`, `parent_id`
  via `call_args.kwargs`. Signature matches real call at `instance.py:904-912`.
- **AgentRegistry.discover()**: C6 test loads the REAL governor from `agents/` on disk — exercises
  the real loader path (`registry.py:275`). Genuine integration, not simplified.

### B. Mock Permissiveness — LOW RISK
- `_check_team_membership` runs against the REAL governor `team_members` (from disk), exercising
  BOTH accept (`developer`) and reject (`leader`, non-member) paths. `spawn_instance.assert_not_called()`
  confirmed on reject.
- `manager.spawn_instance` is a bare MagicMock (short-circuits lifecycle validation), but this is
  deliberate — lifecycle validation has dedicated coverage in `test_spawn_instance_instructive_errors.py`
  and `test_llm_config_override.py`. Mock is scoped correctly, not permissively.
- No mock accepts bad input where the real tool would raise in tested validation branches.

### C. Testing the Right Thing
- **C1 (clear_councilor_errors)**: Phase 4 integration test reaches REAL `bus.clear_parent_error()`
  → real `_parent_errored.pop()`. End-to-end verified. Phase 2 unit test is supplementary (tool
  error-handling wrapping).
- **W7 (canonicalization)** ⚠️ KEY FINDING: The canonical normalization (`next(m for m in allowed...)`
  at `instance.py:893-896`) IS tested with real code (tool body not mocked). The validation gate
  (`if validated_model is None: raise`) IS tested. BUT the underlying `_resolve_model_override`
  function is NOT called (mocked via `side_effect=_resolve` copy). **Risk is mitigated**: the real
  function has dedicated coverage in `tests/unit/test_llm_config_override.py:298-369`
  (`TestResolveModelOverride`: exact match, substring-attack, case-insensitive, whitespace, prefix-attack).
  Coverage exists but is split across two files — fragile if unmaintained.

## Edge Case Coverage

| Edge Case | Covered? | Evidence |
|-----------|----------|----------|
| empty `allowed_models` (unrestricted) | PARTIAL | `append_allowed_models` unrestricted msg ✅; real `_resolve_model_override` pass-through ✅. **GAP**: `spawn_councilor` tool's empty-list rejection branch (`instance.py:875-882`) NOT directly tested. |
| `GPT-4O` vs `gpt-4o` (canonicalization) | YES | `test_model_canonicalization_uppercase_input`, `test_model_canonicalization_normalizes_casing`, `test_model_canonicalization_mixed_case`, `test_model_canonicalization_dedup_property`. Both directions + dedup. |
| invalid `councilor_agent_id` (nonexistent) | YES | `test_invalid_councilor_agent_id_raises_value_error`, `test_invalid_councilor_agent_raises`. ValueError + spawn not called. |
| valid agent but NOT in team_members | YES | `test_non_team_member_agent_raises_value_error`, `test_non_team_member_councilor_raises`. Real `leader` vs real governor team_members. |
| whitespace/None/empty model | PARTIAL | Empty string: `test_empty_model_raises` ✅. **GAP**: None model + whitespace-only model not tested via spawn_councilor (Pydantic schema blocks at input; real `_resolve` handles them in `test_llm_config_override.py:353`). |
| concurrent spawn_councilor calls | NO | **GAP** — acknowledged; D4 max-4-councilors is LLM-policy (workflow.md), not tool-enforced. No automated concurrency test. |

**Edge case gaps: 3** (2 have adjacent coverage elsewhere; 1 true hole = concurrent spawns).

## ⚠️ Environment Finding (pre-existing, NOT a governor regression)

**`pytest-timeout` plugin is NOT installed in the venv**, despite being declared in `pyproject.toml`:
- `pyproject.toml:43` declares `"pytest-timeout>=2.3"`
- `pyproject.toml:71-72` configures `timeout = 30`, `timeout_method = "thread"`
- `.venv` does not have the plugin (`import pytest_timeout` → ModuleNotFoundError)

**Impact on this run**: The Layer 2 (script-internal) dual-layer timeout guard could not be applied
(`--timeout=120`/`--timeout=240` rejected by pytest with exit 4). All 4 packs fell back to Layer 1
(`timeout 300` command-level) only. **No practical impact** — all tests ran in <2s each. But the
dual-layer timeout contract was technically violated.

**Recommendation**: Install `pytest-timeout` (`pip install pytest-timeout`) or remove the stale
config from `pyproject.toml`. This affects ALL pytest packs in the project, not just governor.

## PACKS.md Integrity
- **Discrepancy found and resolved**: The 3 governor test packs were NOT registered in PACKS.md
  (the feature merge did not update PACKS.md). Registered all 3 packs + refreshed
  spawn_team_members entry. Summary count updated (195 → 198 packs).

## ensure.md Validation Results
- **Critical**: "No regressions in changed packs — every pack in the blast-radius change set returns PASS"
  - ✅ PASS — all 4 packs (3 governor + 1 regression) returned PASS
- ensure.md Release Gate NOT run (governor is feature-scoped, not architecture/release — blast radius
  does not warrant E2E/full-suite gate per ensure.md scoping rules).

## Recommendations (non-blocking, for future hardening)
1. **Add test for `spawn_councilor` with empty `allowed_models`** — covers `instance.py:875-882`
   rejection branch (currently untested via the tool).
2. **Link `_resolve_model_override` coverage** — add a comment in `_make_manager` test helper
   pointing to `tests/unit/test_llm_config_override.py:298` so future maintainers know the real
   function has dedicated coverage (the mock is a faithful copy, not the coverage itself).
3. **Assert `version_tag` propagation** in at least one spawn_councilor happy-path test — currently
   dropped silently if spawn_councilor stops forwarding it.
4. **Install `pytest-timeout`** — restore dual-layer timeout across all project packs.
5. **Concurrent spawns** — acceptable gap for unit/integration scope; consider a load-style test
   if fault-tolerance under concurrency becomes a priority.

## Documentation Updated
- [x] PACKS.md — registered 3 governor packs + refreshed spawn_team_members (195 → 198)
- [x] RESULTS/2026-07-25-governor-verification.md — this report
- [x] LESSONS/2026-07-25-pytest-timeout-missing.md — environment finding

---

### Overall Status
- Governor tests: ✅ PASS (40/40)
- Regression: ✅ PASS (27/27, NO REGRESSION)
- Mock quality: ✅ STRONG (tests can be trusted)
- **Overall Verdict: ✅ PASS WITH NOTES**
  (3 non-blocking edge-case gaps with adjacent coverage; 1 pre-existing environment issue;
  PACKS.md gap resolved.)
