# Test Report: LLM Model Load Balancing Feature

Date: 2026-08-01
Branch: `feature/llm-model-load-balance`
Instance IDs: a6ad73a0 (feature-suite), c92a3f18 (distribution), 9109eb5f (mock-verify), 00c1d419 (critical-req), a74e7f01 (regression)

## Summary
- **Total: 5 tasks | All PASS**
- Feature Suite: ✅ 97/97 passed (0.0 failed)
- Distribution Verification: ✅ PASS (50,000 samples, ±0.05% deviation)
- Critical Requirements: ✅ 4/5 VERIFIED, 1/5 PARTIAL (council override — no bug, test-coverage gap)
- Mock Verification: ✅ 12/12 MATCH (2 maintainability concerns, no bugs)
- Regression Check: ✅ 0 new failures (146 pre-existing, matches baseline ~147)
- **Overall Status: ✅ READY TO MERGE**

## Scope Decision
> Full feature test requested. Feature touches 5 production files + 4 test files (all new or feature-specific). Scope correctly bounded to the feature: ran the 4 feature test files + 50k-sample distribution script + broader regression suite for cross-check. No blast-radius reduction needed — the request was well-scoped by the developer.

---

## 1. Feature Test Suite — ✅ PASS (97/97)

| File | Tests | Status |
|------|-------|--------|
| `tests/test_llm_load_balance.py` | 36 | ✅ PASS |
| `tests/test_llm_load_balance_meta_loading.py` | 13 | ✅ PASS |
| `tests/test_llm_load_balance_integration.py` | 17 | ✅ PASS |
| `tests/unit/test_llm_config_override.py` | 31 | ✅ PASS |
| **Total** | **97** | **✅ PASS** |

Runtime: 1.78s. 0 failures, 0 errors, 0 skipped.

---

## 2. Distribution Verification — ✅ PASS

User example: `llm_models: [{model: "agentic", weight: 50}, {model: "coding", weight: 110}]`

Weight 110 clamped to 100 (confirmed at `daemon/services/llm_load_balancer.py:137`: `weight = max(1, min(100, weight))`).

| Model | Count (50k samples) | Percentage | Expected | Tolerance |
|-------|-------------------|------------|----------|-----------|
| agentic | 16,690 | 33.38% | 33.3% | ±2% ✅ |
| coding | 33,310 | 66.62% | 66.7% | ±2% ✅ |

Deviation: ±0.05% — far within the ±2% tolerance.

Key finding: Weight clamping happens defensively inside `_select_weighted_model` at selection time, NOT in the Pydantic `LLMModelWeight` constructor. The raw weight (110) is preserved on the object but clamped during selection. This is correct behavior.

---

## 3. Critical Requirements — ✅ 4/5 VERIFIED, 1/5 PARTIAL

| # | Requirement | Verdict | Key Tests |
|---|-------------|---------|-----------|
| 1 | Single Resolution (model selected once, not re-randomized) | ✅ VERIFIED | `test_rng_fires_once_per_instance`, `test_llm_models_selected_model_frozen_on_restore` |
| 2 | Backward Compatibility (absent/empty llm_models → same behavior) | ✅ VERIFIED | 8 tests across priority, meta-loading, real-agent compat, edge cases |
| 3 | Persistence (load-balanced model persisted, restored after restart) | ✅ VERIFIED | 5 tests in `TestPersistenceGating` (full 4-source matrix) |
| 4 | Council Override (spawn_councilor bypasses load balancing) | ⚠️ PARTIAL | Mechanism correct (elif structurally skips LB when override set); 2 independent tests prove the chain but no single end-to-end test combines spawn_councilor + agent-with-llm_models |
| 5 | Priority Chain (override > llm_models > llm_model > default) | ✅ VERIFIED | 5 tests in `TestResolutionPriority`, one per tier transition |

### Council Override PARTIAL detail
- **Not a bug** — the production code is structurally correct: `spawn_councilor` passes `model` as spawn-time parameter → `validated_model_override` is truthy → `elif` on Priority 2 makes load balancing unreachable.
- **Test gap**: No single test combines (a) spawn_councilor called + (b) spawned agent has llm_models + (c) resulting instance uses forced model not random selection. The contract is proven via two independent tests but implicit across files.
- **Recommendation** (non-blocking 🟢): Add an integration test that calls `spawn_councilor` with an agent whose meta has `llm_models`, asserting the instance's `model_override` is the forced model.

---

## 4. Mock Verification — ✅ 12/12 MATCH

All mock structures in the test files match the real production code signatures.

| # | Mock Structure | Verdict |
|---|----------------|---------|
| 1 | `LLMModelWeight(model=…, weight=…)` construction | ✅ MATCH |
| 2 | `AgentMetadata` instantiation with `llm_model` + `llm_models` | ✅ MATCH |
| 3 | `AgentRegistry` discover → `meta.get("llm_models")` loader | ✅ MATCH (real registry used) |
| 4 | Mock registry (`resolve_to_id`, `get`, `get_version`) | ✅ MATCH |
| 5 | Mock `Config` / `LLMConfig.allowed_models` | ⚠️ MATCH + concern |
| 6 | Mock `InstanceManager` (`create_mock_manager`) | ⚠️ MATCH + concern |
| 7 | `_select_weighted_model(pool, allowed)` call signature | ✅ MATCH |
| 8 | `_build_llm_config(override_model=…)` signature | ✅ MATCH |
| 9 | `spawn_instance(agent_id=…, model=…)` parameter | ✅ MATCH |
| 10 | Resolution priority chain | ✅ MATCH |
| 11 | Persistence gating in `instance_metadata["model_override"]` | ✅ MATCH |
| 12 | `spawn_councilor` ⇄ `spawn_instance` model flow | ✅ MATCH |

### Maintainability Concerns (non-blocking 🟢)
1. **`create_mock_config` (unit test)** — does not set `mock_llm.allowed_models = []`. Current tests work because they use explicit helpers, but a future `_resolve_model_override` caller using the baseline config could get misleading rejections from a truthy MagicMock child. **Fix**: Add `mock_llm.allowed_models = []` to baseline `create_mock_config`.
2. **`create_mock_manager` (both helpers)** — missing `shared_context_metadata_repo` stub. Current tests mask this via `load_and_cache_prompt` patch, but a future maintainer could be surprised. **Fix**: Add `manager.shared_context_metadata_repo = MagicMock()`.

---

## 5. Regression Check — ✅ 0 NEW FAILURES

- **Passed**: 11,615 | **Failed**: 146 | **Skipped**: 195 | **Deselected**: 401 (e2e/integration/postgres)
- Runtime: 6 min 45s

### NEW failures (load-balancing related): **0**

All 4 feature test files pass in the full suite. No failure traceback references any feature module (`llm_load_balancer`, `llm_models`, `_select_weighted_model`, `LLMModelWeight`, `model_override`).

### PRE-EXISTING failures: 146 (across 29 files)

Matches documented Inc 4 baseline (~147). Root causes:

| Error signature | ~Count | Root cause |
|-----------------|--------|-----------|
| `sqlite3.OperationalError: near "CONSTRAINT": syntax error` → `MigrationError` | ~25 | Pre-existing PG-only migration syntax (SQLite doesn't support `DROP CONSTRAINT IF EXISTS`) |
| `ImportError: cannot import name 'clean_llm_config'` (circular import) | ~38 | Pre-existing `daemon.compaction → daemon.graph` circular import; blocks entire `test_manager.py` |
| `TypeError: list_instances() got unexpected keyword argument 'search'` | ~13 | Pre-existing mock drift (`_ManagerStandin` missing kwarg) |
| `AssertionError` (various) | ~55 | Pre-existing mock drift / stale assertions from prior migrations |
| Other (timing, init order) | ~15 | Pre-existing |

None of the 29 failing files are modified by the feature. The feature is **regression-safe to merge**.

---

## Non-blocking Recommendations (🟢 nice-to-have)

1. **Council override end-to-end test** — Add a test combining `spawn_councilor` + agent with `llm_models` to make the bypass contract explicit (currently proven implicitly across 2 tests).
2. **Mock `allowed_models` default** — Set `mock_llm.allowed_models = []` in unit test's `create_mock_config` to prevent future MagicMock-chain footgun.
3. **Mock `shared_context_metadata_repo`** — Add `manager.shared_context_metadata_repo = MagicMock()` to both `create_mock_manager` helpers for symmetry.

---

## ensure.md Validation

### Core (scoped to this feature change)
- [x] No regressions in changed packs — ✅ All 4 feature test files PASS (97/97)
- [x] Deadlock / concurrency integrity — N/A (feature does not touch concurrency paths; `concurrency_atomic_unit_test` not affected)
- [x] No sync DB calls on the asyncio event loop — N/A (feature uses existing persistence patterns, no new sync DB calls)
- [x] `dev.sh` includes `--timeout-graceful-shutdown 10` — N/A (feature does not modify dev.sh)

### Note on `pytest -x` contradiction
The user's request #4 specified `pytest tests/ -x`. Per ensure.md rules (no `-x` for suite runs — hides the full picture), I validated WITHOUT `-x` to collect ALL 146 failures for pre-existing vs. new classification. This honors the intent (regression check) while following the quality gate rule.

---

## Overall Status

| Category | Status |
|----------|--------|
| Feature Test Suite | ✅ PASS (97/97) |
| Distribution Verification | ✅ PASS (33.38% / 66.62%, ±0.05% deviation) |
| Critical Requirements | ✅ 4/5 VERIFIED, 1/5 PARTIAL (no bug, test-coverage gap) |
| Mock Verification | ✅ 12/12 MATCH (2 maintainability concerns) |
| Regression Check | ✅ 0 new failures (146 pre-existing, matches baseline) |
| **Testing Complete** | **✅ READY TO MERGE** |
