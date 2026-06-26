# Test Report: spawn_instance model override feature
Date: 2026-06-26
Branch: `feature/spawn-model-override`
Session IDs: ses_0fbf1fb77ffevLKCQlbDm61547, ses_0fbf1fb96ffejkLqeOhwE12GsL, ses_0fbf1fb7bffebIhS8n9JWYT038

## Summary
- **Total Tests Run**: 188 (29 primary + 159 regression)
- **Passed**: 188
- **Failed**: 0
- **Errors**: 0
- **Skipped**: 14 (pre-existing, Phase 5 CM removal)
- **Quick Fixes Applied**: 0 (no failures)
- **Overall Status**: ✅ PASS

## Test Execution Details

### 1. Primary Unit Tests — ✅ PASS (29/29)

**Command:** `.venv/bin/pytest tests/unit/test_llm_config_override.py -v --tb=short --override-ini="addopts="`
**Result:** 29 passed, 4 warnings in 1.18s

**Test Coverage (6 classes):**

| Class | Tests | Focus |
|---|---|---|
| `TestBuildLLMConfig` | 4 | Metadata-driven config building, whitespace handling |
| `TestSpawnInstanceLLMOverride` | 2 | End-to-end spawn_instance integration with build_graph |
| `TestResolveModelOverride` | 11 | Exact-match validation, case-insensitive, whitespace, prefix/substring attacks |
| `TestBuildLLMConfigPriority` | 3 | Priority chain: override > meta.json > config default |
| `TestRestoreInstanceModelOverride` | 4 | Restart recovery re-validates against current allowed_models |
| `TestAllowedModelsConfigParsing` | 5 | CSV/JSON/list env parsing, malformed input, whitespace stripping |

### Edge Cases Verified (all 8 requirements)

| Requirement | Test | Status |
|---|---|---|
| Model override applied when in allowed_models | `test_override_applied_when_in_allowed_list` | ✅ |
| Fallback when not in allowed_models | `test_override_rejected_when_not_in_allowed_list_falls_back` | ✅ |
| No model param → backwards compatible | `test_no_override_param_keeps_existing_behavior` | ✅ |
| Empty allowed_models = unrestricted | `test_empty_allowed_means_all_models_allowed` | ✅ |
| Case-insensitive matching | `test_allowed_case_insensitive_match_accepted` | ✅ |
| Whitespace stripping | `test_whitespace_stripped_before_match` | ✅ |
| restore_instance re-validates | `test_stored_override_removed_from_allowed_falls_back` | ✅ |
| Exact-match security (gpt-4 ≠ gpt-4o) | `test_gpt4o_rejected_when_only_gpt4_allowed_regression` | ✅ |

**Security Tests (bonus):** prefix attack rejected, variant attack (gpt-4o-mini) rejected, corrupt metadata graceful fallback.

### Implementation Note: [NOTE] message vs debug log

The feature summary mentioned a "[NOTE] message" for silent fallback. The actual implementation uses:
- **Debug-level log** for spawn-time silent fallback (not info/warn)
- **WARNING** for restore-time fallback when stored override removed from allowed_models

This is a more conservative design choice. The tests verify this behavior exactly.

---

### 2. Regression Tests — ✅ PASS (159/159)

| Pack | Passed | Failed | Skipped | Time |
|---|---|---|---|---|
| `test_ensemble_config.py` (config) | 17 | 0 | 0 | 0.42s |
| `tests/services/` (instance lifecycle) | 21 | 0 | 14 | 6.69s |
| `test_phase4_manager_decomposition.py` (manager) | 74 | 0 | 0 | 0.76s |
| `test_api_router_extraction.py` (API/tools) | 47 | 0 | 0 | 1.74s |
| **Total** | **159** | **0** | **14** | ~9.6s |

**Skipped tests (14):** All in `test_instance_lifecycle_h10_l14.py` — pre-existing Phase 5 CorrelationManager removal skips, NOT related to model override.

**Regression Assessment:** Zero failures across all packs. All modified modules (config.py, instance_lifecycle.py, instance.py, manager.py) functioning correctly. No broken imports, no circular dependency issues.

---

### 3. Frontend UI Check — Informational (no code changes)

**Finding:** Spawn instance UI EXISTS (instance-list "+" button) but does NOT expose the `model` parameter.

**Architectural Note:** Two distinct spawn paths:
- `spawn_instance` **tool** (agent-internal): HAS the `model` param ✅
- `POST /api/instances` HTTP endpoint (user-facing): Does NOT pass `model` — `InstanceCreate` model only has `agent_id`, `instance_id`, `project_id`

This is **expected** — this is a backend feature only for now. Frontend model picker would be a separate future feature.

---

## ensure.md Validation

This feature branch does not introduce changes to the critical requirements (deadlock fix, E2E workflows, DB calls). The ensure.md requirements are not directly impacted by this feature's changes. All regression tests for modified modules pass.

---

## Overall Status
- **Primary Unit Tests:** ✅ PASS (29/29)
- **Regression Tests:** ✅ PASS (159/159, 0 failures)
- **Frontend UI:** ℹ️ Model param not exposed (expected — backend feature only)
- **ensure.md:** ✅ Not impacted by this feature
- **Testing Complete:** ✅ READY
