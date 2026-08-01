# Phase 3: Resolution Integration into `spawn_instance()`

## Objective

Slot weighted-random model selection into `spawn_instance()` (`daemon/services/instance_lifecycle.py`), resolving the model + source **once** before `_build_llm_config()` is called. `_build_llm_config()` is simplified to a pure config-builder (no RNG, no resolution logic). The resolution priority: spawn-time override → `llm_models` weighted random → `llm_model` → config default.

This phase establishes **local-scope variables** (`resolved_model`, `resolved_source`) in `spawn_instance()` that flow directly to Phase 4 persistence — no out-param, no return-type change.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Read current `spawn_instance()` and `_build_llm_config` to confirm call structure | none | Current code reviewed; `_build_llm_config` called at line 849-852; understand current flow |
| 2 | Add import for `_select_weighted_model` from new module | Phase 2 | Import added at top of `instance_lifecycle.py` |
| 3 | Add resolution logic in `spawn_instance()` — resolve model + source ONCE before `_build_llm_config` | Task 2 | `spawn_instance()` resolves `resolved_model` and `resolved_source` before calling `_build_llm_config`; RNG fires at most once per instance |
| 4 | Simplify `_build_llm_config()` to receive resolved model as parameter (no RNG, no resolution logic) | Task 3 | `_build_llm_config` no longer calls `_select_weighted_model`; it receives the resolved model and just builds the config dict |
| 5 | Resolution variables (`resolved_model`, `resolved_source`) are in `spawn_instance()` local scope for Phase 4 persistence | Task 3 | Values flow directly to persistence block at line 939-944; no out-param needed |

## Coupling

- **Tight with Phase 2** — `spawn_instance()` calls `_select_weighted_model`. The `None`-return contract must be honored (fall through to `llm_model` / default).
- **Tight with Phase 4** — `resolved_model` and `resolved_source` are local variables in `spawn_instance()` scope, flowing directly to the persistence block at line 939-944.
- **Independent of Phase 1** — `metadata.llm_models` may be None (backward compat) and Phase 3 must handle that.
- **Independent of Phase 5** — but integration tests in Phase 5 will verify the priority ordering end-to-end.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Wrong insertion point — load balancing fires when it shouldn't (e.g., when spawn override is set) | High | Medium | Explicit guard: `(not override_model or not override_model.strip())` before calling `_select_weighted_model`. Integration test covers this. |
| Re-randomization on every call | High | — | **ELIMINATED by design:** RNG has been moved OUT of `_build_llm_config` into `spawn_instance()`, which runs exactly once. `_build_llm_config` is now a pure config-builder with zero RNG calls. Restore reads the persisted `model_override` from DB, never re-randomizes. |
| Load balancing shadows the `llm_model` even when `llm_models` would naturally fail (e.g., all filtered) | Medium | Low | `_select_weighted_model` returns None → fall through to `llm_model`. Already handled by Phase 2's design. |
| Logging noise — every spawn now logs at INFO | Low | Medium | Use INFO for now (debuggability). If too noisy in production, can drop to DEBUG in a follow-up. |
| Return-type change to `_build_llm_config` breaks callers | Low | Low | **No longer an issue:** `_build_llm_config` keeps its original signature (returns `dict`). Resolution happens in `spawn_instance()` local scope, so `resolved_model` and `resolved_source` are directly available for persistence — no out-param, no return-type change. |

## Code Sketch

### Task 3: Resolution in `spawn_instance()` (NOT in `_build_llm_config`)

**CRITICAL DESIGN DECISION:** Weighted-random selection MUST happen in `spawn_instance()`, NOT in `_build_llm_config()`. Rationale:
- `spawn_instance()` runs exactly once per instance creation → RNG fires at most once.
- `_build_llm_config()` may be called multiple times during instance lifecycle (e.g., restore, graph rebuild) → calling RNG there risks re-randomization, violating the "frozen for instance lifetime" invariant.
- Moving resolution upstream also makes it easy to determine the source ("llm_models" vs "llm_model" vs "default") for correct persistence gating (Phase 4, Issue #3).

**Resolution logic** is added to `spawn_instance()` BEFORE the `_build_llm_config()` call at line 849:

```python
# In spawn_instance(), AFTER _resolve_model_override() (line 782) and BEFORE
# _build_llm_config() (line 849).

# --- Resolve the final model and its source ONCE ---
resolved_model: str | None = None
resolved_source: str = "default"  # tracks where the model came from

if validated_model_override and validated_model_override.strip():
    # Priority 1: spawn-time override (council, leader, explicit spawn param)
    resolved_model = validated_model_override.strip()
    resolved_source = "override"
elif metadata and metadata.llm_models:
    # Priority 2: weighted load balancing — RNG fires HERE, exactly once.
    selected = _select_weighted_model(
        metadata.llm_models,
        self._config.llm.allowed_models,
    )
    if selected:
        resolved_model = selected
        resolved_source = "llm_models"
        logger.info(
            "llm_load_balance_selected: agent=%s model=%s pool_size=%d",
            resolved_agent_id, selected, len(metadata.llm_models),
        )

if resolved_model is None and metadata and metadata.llm_model and metadata.llm_model.strip():
    # Priority 3: single-model field
    resolved_model = metadata.llm_model.strip()
    resolved_source = "llm_model"

if resolved_model is None:
    # Priority 4: global default
    resolved_model = self._config.llm.model
    resolved_source = "default"
```

**`_build_llm_config()` is simplified** — it no longer contains ANY resolution logic or RNG calls. It receives the already-resolved model:

```python
def _build_llm_config(
    self,
    metadata: "AgentMetadata | None",
    override_model: str | None = None,
) -> dict:
    """Build the LLM config dict for an instance.

    The model has ALREADY been resolved by the caller (spawn_instance).
    This function is a pure config-builder with no side effects and no RNG.

    Args:
        metadata: Agent metadata from registry (may be None).
        override_model: The FULLY RESOLVED model string from spawn_instance's
            resolution chain. This is always set — it may come from spawn-time
            override, llm_models load balancing, llm_model, or config default.

    Returns:
        dict suitable for use as llm_config in build_instance_graph().
    """
    llm_config = {**self._config.llm.llm_config_base}
    llm_config["model"] = override_model.strip() if override_model else self._config.llm.model
    return llm_config
```

### Task 5: Return-value contract with Phase 4

`spawn_instance()` resolves `resolved_model` and `resolved_source` as LOCAL VARIABLES (shown above). These flow directly to the persistence block at line 939-944. No out-param hack needed — the values are in method scope.

### Task 2: Import (top of `instance_lifecycle.py`)

```python
# Add to existing imports in daemon/services/instance_lifecycle.py
from daemon.services.llm_load_balancer import _select_weighted_model
```

### Task 3b: Call site in `spawn_instance()`

Location: `daemon/services/instance_lifecycle.py:849-852`

The resolution logic (Task 3) runs BEFORE this call site. `resolved_model` and `resolved_source` are local variables in `spawn_instance()` scope.

```python
# After resolution (Task 3 code block above), _build_llm_config receives
# the already-resolved model. No RNG, no out-param:
llm_config = self._build_llm_config(metadata, override_model=resolved_model)

# resolved_model and resolved_source are available for Phase 4 persistence
# at line 939-944 — they are in spawn_instance() local scope.
```

## Edge Cases Handled by Phase 3

| Case | Behavior |
|------|----------|
| `metadata=None` | Resolution skips llm_models and llm_model checks. Falls through to default. Source = "default". |
| `metadata.llm_models=None` (absent or empty) | Load-balancing skipped. Falls through to `llm_model` or default. Source = "llm_model" or "default". |
| `metadata.llm_models=[{m,1}]` (single entry) | Load-balancing fires, selects `m`. Source = "llm_models". |
| `validated_model_override="m"` and `metadata.llm_models=[{other, 1}]` | Load-balancing skipped (override is set). Source = "override". |
| `metadata.llm_models` exists but all filtered out by `allowed_models` | `_select_weighted_model` returns `None`. Falls through to `llm_model` (source="llm_model") or default (source="default"). Source correctly reflects the ACTUAL path used, not "llm_models". |
| Both `llm_models` and `llm_model` present | Load-balancing fires if it produces a result (source="llm_models"). If it returns None (all filtered), falls through to `llm_model` (source="llm_model"). |

## Exit Criterion

- Resolution happens in `spawn_instance()`, NOT `_build_llm_config()`. RNG fires at most once.
- `resolved_model` and `resolved_source` are local variables in `spawn_instance()` scope.
- `_build_llm_config()` is simplified — no RNG, no `_select_weighted_model` call, receives resolved model as `override_model` param.
- When `validated_model_override` is set → source = "override", load-balancing skipped.
- When `metadata.llm_models` is non-empty and override is absent → load-balancing fires once, source = "llm_models".
- When load-balancing returns None (all filtered) → falls through to llm_model (source = "llm_model") or default (source = "default").
- Source accurately reflects the ACTUAL path used (Issue #4 fix).
- `resolved_model` and `resolved_source` flow directly to Phase 4 persistence — no out-param needed.
- Ready for Phase 4 to persist `resolved_model` ONLY when source == "llm_models".
