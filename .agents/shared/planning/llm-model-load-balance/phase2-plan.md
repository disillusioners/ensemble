# Phase 2: Weighted Random Selection Algorithm

## Objective

Implement a pure, side-effect-free function `_select_weighted_model(llm_models, allowed_models) -> str | None` that picks one model from `llm_models` proportional to weights, with `[1, 100]` clamping, `allowed_models` filtering, and **per-entry type validation/coercion**. Invalid individual entries are skipped (not fatal); only when ALL entries are invalid does the function return `None`. Returns `None` to signal the caller should fall back to the next priority level.

This phase is the **algorithm core**. It must be unit-testable in isolation with no daemon / DB / LLM dependencies.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Decide module location and create the function skeleton | Phase 1 | `_select_weighted_model` exists with correct signature in chosen module |
| 2 | Implement `None` / empty-list early return | Task 1 | Function returns `None` when input is `None` or `[]` |
| 3 | Implement `allowed_models` filtering (case-insensitive) | Task 1 | When `allowed_models` is non-empty, entries whose model isn't in the allowed set are silently skipped |
| 4 | Implement weight clamping to `[1, 100]` | Task 1 | `weight: 0` → 1; `weight: 150` → 100; `weight: -5` → 1; valid weights unchanged |
| 5 | Implement weighted random selection (cumulative-sum on integer total) | Task 1, 4 | Function returns one of the model strings, selected with probability proportional to clamped weight |
| 6 | Handle numerical edge cases (single entry, all-filtered, post-loop fallthrough) | Task 1-5 | Single entry always returned; all-filtered returns `None`; cumulative-sum rounding fallthrough returns last model |
| 7 | Add module-level docstring explaining semantics, return-value contract, and edge-case behavior | Task 1-6 | Public API clearly documented; `# Returns:` notes that `None` triggers caller-side fallback |

## Coupling

- **Tight with Phase 1** — Input is `list[LLMModelWeight] | None`. Phase 2 must agree on the exact type.
- **Tight with Phase 3** — Phase 3 calls this function. The `None` return value is the **fallback signal** for Phase 3 to drop down to `llm_model` / default.
- **Independent of Phase 4** — persistence happens after the model is resolved; doesn't affect the algorithm.
- **Independent of Phase 5** — but Phase 5 will write extensive tests against this function.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Statistical bias from float accumulation | Medium | Low | Use integer cumulative sum with single random uniform on `total_weight` (integer arithmetic) |
| Off-by-one: random value equal to total never selects last model | Medium | Low | Use `<=` comparison or post-loop fallthrough (return last model on no match) |
| Case-sensitivity mismatch with `allowed_models` (config uses lowercase set, meta.json uses mixed case) | Medium | Medium | Normalize both sides to `.lower()` for comparison (matches `_resolve_model_override` pattern at `daemon/services/instance_lifecycle.py:633`) |
| All entries filtered → exception or wrong behavior | Medium | Low | Explicit early return `None` after filtering; covered by unit test |
| Empty string model name passes validation in Phase 1 but is invalid here | Low | Medium | Either: (a) Phase 1's `LLMModelWeight` should reject empty string via `Field(min_length=1)`, OR (b) Phase 2 skips empty-string models. Recommendation: (a) for clarity |
| Duplicate model names in `llm_models` | Low | Medium | Document that duplicates are additive (their weights sum). No deduplication. |

## Code Sketch

Location: **recommend `daemon/services/llm_load_balancer.py`** (new file). Alternative: module-level function in `daemon/services/instance_lifecycle.py`. New file is preferred for testability and separation of concerns.

```python
# daemon/services/llm_load_balancer.py
"""Weighted random LLM model selection.

This module provides the pure selection algorithm used when an agent's
meta.json declares `llm_models: [{"model": "...", "weight": <int>}, ...]`.

Selection happens ONCE at instance creation. The selected model is frozen
for the instance's lifetime. See plan-overview.md for the full design.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daemon.services.llm_load_balancer import LLMModelWeight


def _select_weighted_model(
    llm_models: list["LLMModelWeight"] | None,
    allowed_models: list[str] | None,
) -> str | None:
    """Pick ONE model from `llm_models` proportional to (clamped) weights.

    Args:
        llm_models: List of (model, weight) entries from AgentMetadata. May be
            None or empty — both return None.
        allowed_models: Whitelist from config.llm.allowed_models. None or empty
            list means no restriction. Filtering is case-insensitive.

    Returns:
        The selected model name as a string, OR None if no valid candidates
        (empty input, all filtered out by allowed_models, etc.).

        Callers MUST treat None as "fall back to the next priority level"
        (e.g., llm_model, then default).

    Behavior:
        - Weights are clamped to [1, 100] before selection.
        - Selection is proportional: P(model) = clamped_weight / sum(clamped_weights).
        - Single-entry list always returns that entry.
        - Duplicate model names are allowed (their weights sum additively).
        - Empty/whitespace model names are skipped.
    """
    # 1. Early returns
    if not llm_models:
        return None

    # 2. Filter by allowed_models (case-insensitive)
    if allowed_models:
        allowed_lower = {m.strip().lower() for m in allowed_models if m and m.strip()}
        candidates = [
            entry for entry in llm_models
            if entry.model and entry.model.strip()
            and entry.model.strip().lower() in allowed_lower
        ]
    else:
        candidates = [
            entry for entry in llm_models
            if entry.model and entry.model.strip()
        ]

    if not candidates:
        return None

    # 3. Clamp weights to [1, 100]
    clamped: list[tuple[str, int]] = [
        (entry.model.strip(), max(1, min(100, entry.weight)))
        for entry in candidates
    ]

    # 4. Weighted random selection via cumulative sum (integer arithmetic)
    models, weights = zip(*clamped)
    total_weight = sum(weights)

    # Single entry shortcut (avoids RNG call for determinism if seed is set elsewhere)
    if len(models) == 1:
        return models[0]

    r = random.uniform(0, total_weight)
    cumulative = 0
    for model, weight in zip(models, weights):
        cumulative += weight
        if r <= cumulative:
            return model

    # Numerical safety: floating-point fallthrough → return last model
    return models[-1]
```

### Type Contract and Per-Entry Validation (Issue #7 Resolution)

Per-entry validation happens INSIDE `_select_weighted_model` (not in the Pydantic model or discover()). This is the canonical policy:

| Input | Behavior |
|-------|----------|
| `model` is empty string or whitespace | Skip entry (not fatal) |
| `model` is None | Skip entry (not fatal) |
| `weight` is int in [1, 100] | Used as-is |
| `weight` is int < 1 (0, negative) | Clamped to 1 |
| `weight` is int > 100 | Clamped to 100 |
| `weight` is float (e.g., 50.5) | Coerced to int(50.5)=50 via `int()` |
| `weight` is None | Defaulted to 1 |
| `weight` is `True` (bool) | **REJECTED** — `isinstance(True, int)` is True in Python, but booleans are semantically invalid. Skip entry. |
| `weight` is `False` (bool) | **REJECTED** — Skip entry. |
| `weight` is non-numeric string (e.g., "heavy") | **REJECTED** — Skip entry. |
| `weight` is numeric string (e.g., "50") | Coerced to int(50)=50 |
| Entry is missing `model` key | Skip entry (Pydantic may catch this first, but belt-and-suspenders) |
| Entry is not a dict | Skip entry |

**Key invariant:** A single invalid entry does NOT invalidate the entire `llm_models` list. Invalid entries are silently skipped. The list is only treated as "no valid candidates" (returns `None`) if ALL entries fail validation.

The updated filtering code (replacing the current candidate-filtering section in the code sketch):

```python
    # 2. Filter and validate each entry (Issue #7: per-entry policy)
    allowed_lower = (
        {m.strip().lower() for m in allowed_models if m and m.strip()}
        if allowed_models
        else None
    )

    candidates: list[tuple[str, int]] = []
    for entry in llm_models:
        # Validate model name
        model_name = getattr(entry, "model", None)
        if not model_name or not str(model_name).strip():
            continue  # skip: empty/None model name
        model_name = str(model_name).strip()

        # Validate weight (Issue #7 type contract)
        raw_weight = getattr(entry, "weight", 1)
        if isinstance(raw_weight, bool):
            continue  # skip: booleans are semantically invalid weights
        try:
            weight = int(raw_weight)
        except (TypeError, ValueError):
            # Try float coercion (e.g., "50.5" → 50)
            try:
                weight = int(float(raw_weight))
            except (TypeError, ValueError):
                continue  # skip: non-numeric weight

        # Clamp to [1, 100]
        weight = max(1, min(100, weight))

        # Filter by allowed_models (case-insensitive)
        if allowed_lower is not None and model_name.lower() not in allowed_lower:
            continue  # skip: not in allowed list

        candidates.append((model_name, weight))

    if not candidates:
        return None
```

### Why integer cumulative sum, not float proportions?

Float accumulation (`random.random() * total; cumulative += weight/total`) drifts over many entries. Integer accumulation is exact up to `total_weight` (max 100 entries × 100 weight = 10,000 — trivial). Single `random.uniform(0, total_weight)` is the only RNG call.

### Why case-insensitive matching for `allowed_models`?

Matches the existing `_resolve_model_override` convention at `daemon/services/instance_lifecycle.py:633`. Consistency over novelty.

### Why no seeding?

`random.uniform` uses the global `random` state. No seeding needed — non-determinism is desired (load balancing is the point). For reproducible tests, Phase 5 will use `random.seed(...)` in a fixture.

## Edge Cases (Tests for Each in Phase 5)

| Case | Input | Expected |
|------|-------|----------|
| None input | `llm_models=None` | returns `None` |
| Empty list | `llm_models=[]` | returns `None` |
| Single entry | `llm_models=[{m, 1}]` | always returns `m` (10000 samples) |
| Two-entry, equal weights | `llm_models=[{a, 50}, {b, 50}]` | ~50/50 in 50000 samples (±2%) |
| Heavy weight | `llm_models=[{a, 90}, {b, 10}]` | ~90/10 in 50000 samples (±2%) |
| Clamp low | `llm_models=[{a, 0}]` | `a` always (clamped to 1) |
| Clamp high | `llm_models=[{a, 200}]` | `a` always (clamped to 100) |
| Clamp negative | `llm_models=[{a, -5}, {b, 50}]` | ~50/50 in samples (both clamp to 1, 50) |
| All filtered | `llm_models=[{disallowed, 100}]`, `allowed_models=[other]` | returns `None` |
| Mixed filter | `llm_models=[{a, 50}, {disallowed, 100}, {b, 50}]`, `allowed=[a, b]` | only `a` or `b` ever selected, ~50/50 |
| Case mismatch | `llm_models=[{ModelA, 1}]`, `allowed=[modela]` | returns `ModelA` (case-insensitive) |
| Empty model name | `llm_models=[{"", 100}, {real, 1}]` | only `real` ever selected (or None if real also empty) |
| Duplicate models | `llm_models=[{a, 50}, {a, 50}]` | `a` always (probability sum = 100%) |
| Duplicate models with another | `llm_models=[{a, 30}, {b, 30}, {a, 40}]` | `a` ~70%, `b` ~30% (cumulative) |
| Whitespace model name | `llm_models=[{"  ", 100}]` | returns `None` (whitespace-only) |

## Exit Criterion

- `_select_weighted_model` exists in chosen module.
- All edge cases above behave as documented (verified by Phase 5 tests, but manually checked here too).
- Function has zero side effects beyond the `random` module call.
- Function is importable from both the test suite and from `_build_llm_config` (Phase 3).
- No daemon / DB / LLM dependencies — pure function, fully unit-testable.
