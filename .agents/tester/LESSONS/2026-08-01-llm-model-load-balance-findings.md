# LESSON: LLM Model Load Balancing — Feature Test Findings

Date: 2026-08-01
Branch: `feature/llm-model-load-balance`
Feature: Weighted-random `llm_models` array in agent meta.json

## Key Findings

### Weight clamping location (important for mock verification)
Weight clamping to [1,100] happens INSIDE `_select_weighted_model()` at `daemon/services/llm_load_balancer.py:137` (`weight = max(1, min(100, weight))`), NOT in the Pydantic `LLMModelWeight` constructor. The raw weight (e.g., 110) is preserved on the object but clamped only at selection time. Tests that need to verify clamping should call `_select_weighted_model`, not inspect `LLMModelWeight.weight`.

### `LLMModelWeight` lives in `daemon/registry.py`, not `daemon/services/llm_load_balancer.py`
To avoid circular imports, `LLMModelWeight` (the Pydantic model coupled to `AgentMetadata`) lives in `daemon/registry.py` (the leaf of the dependency graph). The selection algorithm `_select_weighted_model` lives in `daemon/services/llm_load_balancer.py` and imports from registry. Tests must import `LLMModelWeight` from `daemon.registry`.

### C6 pattern regression test is critical
The `extra='ignore'` on `AgentMetadata` silently drops unknown JSON keys. The `meta.get("llm_models")` loader line at `registry.py:514` is the ONLY way the field gets populated. The meta-loading test file (`test_llm_load_balance_meta_loading.py`) correctly tests this by exercising real `AgentRegistry.discover()`.

### Persistence gating by source
`instance_metadata["model_override"]` is written ONLY when `resolved_source == "override"` or `resolved_source == "llm_models"`. Sources `"llm_model"` and `"default"` do NOT persist — this is the backward-compatibility contract. The integration tests in `TestPersistenceGating` cover all 4 source values exhaustively.

## Maintainability Concerns Found (non-blocking)

1. **Unit test `create_mock_config`** — Does not set `mock_llm.allowed_models = []`. Integration test version does. Future tests using the unit baseline with `_resolve_model_override` could hit MagicMock-chain issues. Recommend adding `mock_llm.allowed_models = []` to the unit baseline.

2. **`create_mock_manager`** — Missing `shared_context_metadata_repo` stub. Current tests mask this via `load_and_cache_prompt` patch. Recommend adding `manager.shared_context_metadata_repo = MagicMock()` for symmetry.

## Council Override Test Gap (non-blocking)

The council override bypass mechanism is structurally correct (`elif` on Priority 2 makes load balancing unreachable when override is set), but there is no single end-to-end test that combines `spawn_councilor` + agent with `llm_models`. The contract is proven via two independent tests (council passes model correctly; lifecycle prefers override). Recommend adding an integration test to make the contract explicit.
