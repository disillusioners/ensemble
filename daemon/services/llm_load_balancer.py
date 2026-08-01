"""Weighted random LLM model selection.

This module provides the pure selection algorithm used when an agent's
meta.json declares ``llm_models: [{"model": "...", "weight": <int>}, ...]``.

Selection happens ONCE at instance creation. The selected model is frozen
for the instance's lifetime. See
``.agents/shared/planning/llm-model-load-balance/plan-overview.md`` for the
full design.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daemon.registry import LLMModelWeight


def _select_weighted_model(
    llm_models: list["LLMModelWeight"] | None,
    allowed_models: list[str] | None,
) -> str | None:
    """Pick ONE model from ``llm_models`` proportional to (clamped) weights.

    Args:
        llm_models: List of (model, weight) entries from AgentMetadata. May be
            None or empty — both return None.
        allowed_models: Whitelist from ``config.llm.allowed_models``. None or
            empty list means no restriction. Filtering is case-insensitive.

    Returns:
        The selected model name as a string, OR ``None`` if no valid candidates
        (empty input, all filtered out by allowed_models, etc.).

        Callers MUST treat ``None`` as "fall back to the next priority level"
        (e.g., ``llm_model``, then default).

    Behavior:
        - Weights are clamped to ``[1, 100]`` before selection.
        - Selection is proportional: ``P(model) = clamped_weight / sum(clamped_weights)``.
        - Single-entry list always returns that entry.
        - Duplicate model names are allowed (their weights sum additively).
        - Empty/whitespace model names are filtered at this level.
        - Numeric string weights are coerced (``"50"`` → 50).
        - Float weights are truncated to int (``50.7`` → 50).
        - Boolean weights are REJECTED at the Pydantic level (in
          :class:`daemon.registry.LLMModelWeight`) before reaching this
          function — they cannot be distinguished from int here because
          ``isinstance(True, int)`` is True in Python and Pydantic
          auto-coerces them. When a bool is rejected, the
          :meth:`AgentRegistry.discover` retry handler drops the whole
          ``llm_models`` block for that agent (graceful fallback).
        - Non-numeric string weights are filtered at this level.
        - Invalid entries do NOT invalidate the entire list — they are silently
          skipped. Only when ALL entries are invalid does the function return
          ``None``.
    """
    # 1. Early returns — no input or empty list.
    if not llm_models:
        return None

    # 2. Build the allowed-models set (case-insensitive, normalised to .lower()).
    #    ``None`` means "no restriction" (an empty list also means no restriction,
    #    matching the existing ``_resolve_model_override`` convention).
    allowed_lower: set[str] | None
    if allowed_models:
        allowed_lower = {
            str(m).strip().lower()
            for m in allowed_models
            if m is not None and str(m).strip()
        }
        if not allowed_lower:
            # Whitelist present but all entries were blank → treat as no restriction.
            allowed_lower = None
    else:
        allowed_lower = None

    # 3. Per-entry validation, weight coercion, and allowed-models filtering.
    #    A single invalid entry does NOT invalidate the entire list; we skip
    #    the bad entry and continue. Only when ALL entries are invalid do we
    #    return None (the caller then falls back to llm_model / default).
    candidates: list[tuple[str, int]] = []
    for entry in llm_models:
        # `entry` is a LLMModelWeight (Pydantic model). It's typed as
        # ``LLMModelWeight`` in the signature; in practice Pydantic models
        # accept duck-typed access via ``getattr``.
        # Validate model name (skip empty/None/whitespace).
        model_name = getattr(entry, "model", None)
        if model_name is None:
            continue
        try:
            model_name = str(model_name).strip()
        except (TypeError, ValueError):
            continue
        if not model_name:
            continue

        # Validate weight. The Pydantic model already rejects booleans and
        # non-numeric strings at construction time (see
        # ``LLMModelWeight._validate_weight``). At this level we still
        # defensively skip bools (in case the entry was constructed without
        # going through Pydantic) and accept int / float / numeric strings.
        raw_weight = getattr(entry, "weight", 1)
        if isinstance(raw_weight, bool):
            continue  # bool rejected
        if raw_weight is None:
            weight = 1  # default
        elif isinstance(raw_weight, int):
            weight = raw_weight
        elif isinstance(raw_weight, float):
            # Floats get truncated to int (50.5 → 50) to match the plan's
            # "Coerced to int(50.5)=50 via int()" contract.
            try:
                weight = int(raw_weight)
            except (TypeError, ValueError):
                continue
        elif isinstance(raw_weight, str):
            # Numeric string coercion: "50" → 50, "50.5" → 50.
            s = raw_weight.strip()
            if not s:
                weight = 1  # default for empty
            else:
                try:
                    weight = int(s)
                except ValueError:
                    try:
                        weight = int(float(s))
                    except (TypeError, ValueError):
                        continue
        else:
            # Non-numeric, non-string, non-int, non-float, non-bool — skip.
            continue

        # Clamp to [1, 100].
        weight = max(1, min(100, weight))

        # Filter by allowed_models (case-insensitive).
        if allowed_lower is not None and model_name.lower() not in allowed_lower:
            continue

        candidates.append((model_name, weight))

    if not candidates:
        return None

    # 4. Weighted random selection via integer cumulative sum.
    #    Integer arithmetic (max 100 entries × 100 weight = 10,000) avoids the
    #    float-accumulation drift that breaks the proportional invariant.
    models, weights = zip(*candidates)
    total_weight = sum(weights)

    # Single entry shortcut (deterministic — no RNG call).
    if len(models) == 1:
        return models[0]

    r = random.uniform(0, total_weight)
    cumulative = 0
    for model, weight in zip(models, weights):
        cumulative += weight
        if r <= cumulative:
            return model

    # Numerical safety: floating-point fallthrough (uniform can equal
    # total_weight due to inclusive upper bound on some platforms) → return
    # last model. This is the post-loop fallthrough required by the design.
    return models[-1]
