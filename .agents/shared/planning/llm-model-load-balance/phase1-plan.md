# Phase 1: Pydantic Model + meta.json Loading

## Objective

Add the new `llm_models` field to `AgentMetadata` so it (a) is declared as a Pydantic field with strict-ish validation, and (b) survives the `extra='ignore'` loader path in `AgentRegistry.discover()`. After this phase, `registry.get(agent_id).llm_models` returns the parsed list (or `None` if absent).

This phase delivers the **data-shape contract** that Phase 2 (`_select_weighted_model`) consumes.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Add `LLMModelWeight` Pydantic model in `daemon/services/llm_load_balancer.py` (imported into `daemon/registry.py`) | none | Class declared with `model: str` (required) + `weight: int = 1` (default), `extra="ignore"` |
| 2 | Add `llm_models` field to `AgentMetadata` | Task 1 | Field declared as `list[LLMModelWeight] \| None = None` with descriptive docstring |
| 3 | Add `llm_models=meta.get("llm_models")` line in `AgentRegistry.discover()` | Task 2 | Loader line added at `daemon/registry.py:384` adjacent to `llm_model=meta.get("llm_model")` |
| 4 | Add graceful ValidationError handling around AgentMetadata construction in `discover()` — LAST RESORT only | Task 2, 3 | Catches structural errors (e.g., `llm_models: "not a list"`). Per-entry validation (empty model, bad weight) is handled by `_select_weighted_model` in Phase 2, NOT here. This handler only fires for truly malformed structures that Pydantic itself rejects |
| 5 | Add docstring update on `AgentMetadata.llm_models` documenting semantics | Task 2 | Doc explains: weighted random at instance creation, [1,100] clamping, allowed_models filtering, fallback behavior |
| 6 | Add a manual sanity check by loading an existing agent's meta.json | Task 1, 2, 3 | Run Python REPL: `AgentRegistry(Path("agents")).discover(); r.get("governor")` → confirm `.llm_models is None` (backward compat) |

## Coupling

- **Tight with Phase 2** — Phase 2's `_select_weighted_model` consumes `list[LLMModelWeight]` directly. The Pydantic field type is the **input contract**. If Phase 1 changes the type (e.g., adds a `name` field), Phase 2 must follow.
- **Loose with Phase 5 (testing)** — Phase 5 writes a C6 regression test that asserts `llm_models` survives loading. This test can be drafted in parallel but cannot run until Phase 1 lands.
- **Independent of Phases 3 and 4** — those phases consume the loaded value but don't affect how it's loaded.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Forget the loader line → field silently dropped (C6 pattern) | High | Medium | Land Tasks 1, 2, 3 in a single PR; PR template checkbox for "added loader line for every new field"; Phase 5 C6 test enforces this |
| ValidationError on malformed `llm_models` entry crashes agent discovery | Medium | Low | Task 4 wraps the construction in try/except, logs warning, falls back to `llm_models=None`. Other malformed meta.json fields already follow this pattern in the codebase — confirm with the existing convention |
| ModelWeight conflicts with another model name in the module | Low | Low | RESOLVED: Define `LLMModelWeight` in `daemon/services/llm_load_balancer.py` and import into `daemon/registry.py`. Single canonical location — no namespace collision. |

## Code Sketch

### Task 1 + 2: Add `LLMModelWeight` (in llm_load_balancer.py) and the field to `AgentMetadata`

Location: `daemon/services/llm_load_balancer.py` (new file — define `LLMModelWeight` here) + `daemon/registry.py` (import + add `llm_models` field to `AgentMetadata` around line 117-231)

```python
# daemon/services/llm_load_balancer.py — canonical location for LLMModelWeight
#
# TYPE CONTRACT (resolved per Issue #7):
#   - model: str, required, min_length=1 (Pydantic-enforced)
#   - weight: int, optional, default=1 (Pydantic type annotation)
#   - Per-entry filtering (invalid weight coercion, boolean rejection) happens in
#     _select_weighted_model (Phase 2), NOT in the Pydantic model.
#   - Pydantic ValidationError on a STRUCTURAL level (e.g., llm_models: "string"
#     instead of list) is caught by discover() Task 4 handler as last resort.
from pydantic import BaseModel, ConfigDict, Field


class LLMModelWeight(BaseModel):
    """One entry of meta.json `llm_models` — a model name and its selection weight.

    Weight is clamped to [1, 100] at selection time (not here, to preserve
    the user-supplied value for debugging).
    """

    model_config = ConfigDict(extra="ignore")

    model: str = Field(..., min_length=1, description="The LLM model identifier (e.g., 'gpt-4o', 'claude-sonnet-4'). Must be non-empty.")
    weight: int = Field(default=1, description="Selection weight, clamped to [1, 100] at selection time")


# Inside AgentMetadata class (daemon/registry.py:~135, near llm_model)
class AgentMetadata(BaseModel):
    # ... existing fields ...
    llm_model: str | None = Field(default=None, description="Override the global LLM model for this agent")

    llm_models: list[LLMModelWeight] | None = Field(
        default=None,
        description=(
            "Weighted random selection of LLM models at instance creation. "
            "When present and non-empty, ONE model is picked at instance creation "
            "(proportional to weights, clamped to [1, 100]) and frozen for the "
            "instance's lifetime. Higher priority than llm_model but lower than "
            "spawn-time model override. Models not in config.llm.allowed_models "
            "are filtered out (silent skip). Empty array = backward-compatible."
        ),
    )
```

### Task 3: Loader line in `AgentRegistry.discover()`

Location: `daemon/registry.py:300-410`, the `AgentMetadata(...)` construction block (~line 352-397)

```python
# Existing loader (daemon/registry.py:384)
llm_model=meta.get("llm_model"),

# NEW: directly below it
llm_models=meta.get("llm_models"),
```

`meta.get("llm_models")` returns either `None` or a raw list of dicts. Pydantic auto-validates each dict into `LLMModelWeight`. If validation fails, `ValidationError` is raised — handled by Task 4.

### Task 4: Graceful ValidationError handling

Location: `daemon/registry.py:352-408`, wrap the `AgentMetadata(...)` constructor

```python
try:
    agent_meta = AgentMetadata(
        # ... all fields ...
        llm_model=meta.get("llm_model"),
        llm_models=meta.get("llm_models"),
    )
except ValidationError as e:
    # C6 pattern: don't crash discovery. Try to load without llm_models first.
    logger.warning(
        "agent_load_partial: agent=%s base=%s errors=%s — retrying without llm_models",
        base_agent_id, base_agent_id, e.errors(),
    )
    # Strip llm_models and retry so other valid fields still load.
    meta_without_models = {k: v for k, v in meta.items() if k != "llm_models"}
    agent_meta = AgentMetadata(**meta_without_models)  # may still fail on other fields
```

Note: The existing `discover()` may already have a try/except around the AgentMetadata construction. Verify the current convention and follow it; the sketch above is a fallback if no such handling exists.

## Edge Cases Covered

- `llm_models` absent from meta.json → `meta.get("llm_models")` returns `None` → Pydantic field stays `None`. **Backward compatible.**
- `llm_models: []` empty array → Pydantic field is `[]` (empty list, not None). Phase 2 must check truthiness.
- `llm_models: [{"model": "x", "weight": 5}]` → Pydantic parses successfully.
- `llm_models: [{"model": "x"}]` (weight missing) → Pydantic uses default `weight=1`.
- `llm_models: [{"model": "x", "weight": 150}]` → Pydantic accepts; Phase 2 clamps to 100.
- `llm_models: [{"weight": 5}]` (model missing) → Pydantic ValidationError → Task 4 fallback.
- `llm_models: "not a list"` → Pydantic ValidationError → Task 4 fallback.

## Exit Criterion

- `AgentMetadata` accepts `llm_models` as a typed field.
- `AgentRegistry.discover()` populates `llm_models` from meta.json without silent drop.
- Malformed entries do NOT crash agent discovery (graceful fallback).
- A quick REPL check confirms `registry.get("governor").llm_models is None` (backward compat).
- Ready for Phase 2 to consume `list[LLMModelWeight]` as input.
