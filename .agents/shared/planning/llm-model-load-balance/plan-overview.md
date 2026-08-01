# Plan Overview: LLM Model Load Balancing

Date: 2026-08-01
Author: planner[v2] via plan-creation worker
Status: Draft

## Objective

Add weighted-random LLM model selection to agent meta.json so that an instance picks one of several configured models at creation time (proportional to declared weights), while preserving all existing override semantics and remaining fully backward compatible.

A single sentence that, when true, marks the feature complete:
> When an agent's meta.json declares `llm_models: [{"model": "m1", "weight": 70}, {"model": "m2", "weight": 30}]`, every new instance of that agent resolves to either `m1` (~70%) or `m2` (~30%) at creation, persists the choice in DB, reuses the same choice on restore, and never re-randomizes during the instance's lifetime.

## Scope

### In Scope

- New optional meta.json field `llm_models: [{"model": "<name>", "weight": <int>}]` parsed via Pydantic.
- Weighted-random selection at instance creation time only (once per instance).
- Weight clamping to `[1, 100]` and proportional distribution.
- `allowed_models` filtering of `llm_models` entries (config.llm.allowed_models).
- Persistence of the resolved model to `instance_metadata["model_override"]` so restore re-applies it.
- Backward compatibility when `llm_models` is absent or empty.
- New unit tests + C6 regression test + integration tests for priority ordering.

### Out of Scope

- Per-request / per-turn re-selection (selected once, frozen for instance lifetime).
- Runtime weight changes after instance creation (weights are read from meta.json at creation only).
- Multi-model ensemble inference (selection is pick-one, not fan-out).
- User-facing UI for editing `llm_models` (meta.json only).
- Changes to council/governor model semantics — they keep passing `model` as spawn-time override and naturally skip load balancing.
- Changes to caller_model_overrides in explorer — they already pass through the spawn-time `model` path.
- Changes to `config.llm.allowed_models` itself.

### Adjacent Features Considered and Deferred

- **Per-tenant quotas / fairness** — would require queue-aware metrics. Out of scope for v1.
- **Model health-based failover** — auto-rotate on repeated failures. Out of scope; v1 only does at-creation selection.
- **Per-instance model override API** — out of scope; existing `model_override` DB column already covers this when set explicitly.

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Pydantic model + meta.json loading | Add `llm_models` field to AgentMetadata and load it via `discover()` | 6 | tight with Phase 2 (data shape) | pending |
| 2 | Weighted random selection algorithm | Pure function `_select_weighted_model` with weight clamping, allowed-models filtering, edge cases | 7 | tight with Phase 1 (input shape) | pending |
| 3 | Resolution in `spawn_instance()` | Resolve model + source once in `spawn_instance()`, simplify `_build_llm_config` to pure config-builder | 5 | tight with Phase 2 (calls the algorithm); tight with Phase 4 (local-scope variables for persistence) | pending |
| 4 | DB persistence + restore | Persist load-balanced model as `model_override` ONLY for source=="llm_models"; ensure restore re-uses it | 6 | tight with Phase 3 (consumes resolved_model + resolved_source) | pending |
| 5 | Testing | Unit, statistical, edge-case, C6 regression, integration, spawn-path coverage, restore tests | 9 | independent of 1-4 (but verifies all of them) | pending |

## Coupling Map

|              | Phase 1   | Phase 2    | Phase 3    | Phase 4    | Phase 5 |
|--------------|-----------|------------|------------|------------|---------|
| **Phase 1**  | —         | tight (data shape contract) | independent | independent | independent |
| **Phase 2**  | tight     | —          | tight (caller) | independent | independent |
| **Phase 3**  | independent | tight (caller) | —       | tight (returns resolved model) | independent |
| **Phase 4**  | independent | independent | tight (consumer) | —         | independent |
| **Phase 5**  | independent | tight (algorithm) | tight (priority order) | tight (persistence) | — |

Notes:
- Phase 1 ↔ Phase 2 is the **data-shape contract** — the `LLMModelWeight` Pydantic model (defined in `daemon/services/llm_load_balancer.py`, imported into `registry.py`) and `llm_models: list[LLMModelWeight] | None` field must be agreed upon before either phase starts. Define the model in Phase 1, use it in Phase 2.
- Phase 3 ↔ Phase 4 is the **resolved-model handoff** — Phase 3 must return the final resolved model string so Phase 4 can persist it. Establish this return-value contract in Phase 3.

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Silent field drop due to `extra='ignore'` if the loader line in `discover()` is forgotten (C6 pattern) | High | Medium | C6 regression test in Phase 5 + code-review checklist; both Pydantic declaration and loader line land in same PR |
| 2 | Statistical bias in weighted sampling due to float-rounding errors | Medium | Low | Use integer arithmetic (cumulative sum, single random uniform on total_weight); add statistical test (1000-sample distribution within ±5% of expected) |
| 3 | Non-deterministic selection complicates debugging | Medium | High | Log the resolved model at instance creation (logger.info); include it in `instance_metadata["model_override"]` for inspectability via DB |
| 4 | `restore_instance` does not re-apply the persisted `model_override` | High | Low | Phase 4 explicitly traces restore code path (`daemon/services/instance_lifecycle.py` restore branch + any DB→graph rebuild code) and adds a test |
| 5 | All `llm_models` entries filtered out by `allowed_models` → source mislabeled | Medium | Medium | **RESOLVED:** Resolution in `spawn_instance()` tracks `resolved_source` accurately — when `_select_weighted_model` returns None, source falls through to "llm_model" or "default", NOT "llm_models". Persistence only writes `model_override` for source=="llm_models". |
| 6 | Duplicate model names in `llm_models` array | Low | Medium | Document in meta.json schema docs; treat duplicates as additive probability (no deduplication) |
| 7 | Interaction with `caller_model_overrides` in explorer and council spawn paths | Low | Low | Both paths pass `model` as spawn-time parameter, which already has highest priority; load-balancing skipped when `override_model` is set; covered by integration tests |
| 8 | Pydantic validation error on malformed `llm_models` entry breaks all agent loading | Medium | Low | Wrap loader to catch `ValidationError` per-agent (consistent with other meta.json validation); log warning, fall back to no `llm_models` |
| 9 | DB JSONB column serialization of `model_override` differs between PostgreSQL and SQLite | Low | Low | Store as plain string; already proven pattern for `validated_model_override` at `instance_lifecycle.py:939-944` |
| 10 | Weight clamping surprises users who set `weight: 0` expecting zero probability | Low | Medium | Document clamping behavior in meta.json schema; clamp silently (do not error) |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | `llm_models` field survives meta.json loading (no silent drop) | C6-style test loads real `agents/<name>/meta.json` fixture and asserts `registry.get(...).llm_models == [...]` | Test passes; equivalent test pattern as `tests/test_governor_integration.py:229-274` |
| 2 | Weighted random selection is statistically correct | Run 50,000 samples with weights `{70, 30}`; verify each model selected within ±2% of expected proportion | Both models fall within expected window (70%±2%, 30%±2%) |
| 3 | Weight clamping works | Unit test with weights `{0, 50, 150, -5}`; verify all clamp to `[1, 100]` | All clamped values match expected `[1, 50, 100, 1]` |
| 4 | Allowed-models filtering works | Test with `llm_models=[{m1, 1}, {m2, 1}, {m3, 1}]` and `allowed_models=[m2]`; verify only `m2` ever selected | Only `m2` appears in 10,000 samples |
| 5 | All-filtered case falls back to `llm_model` | Test with `llm_models=[m_disallowed]` and `allowed_models=[other]` and `llm_model=m_fallback`; verify final model is `m_fallback` | Resolved model == `m_fallback` |
| 6 | Spawn-time `model` parameter overrides load balancing | Test: `llm_models=[{m1, 100}]` but spawn with `model=m_override`; verify final model is `m_override` | Resolved model == `m_override` |
| 7 | Council/Governor override still works | Integration test: spawn council with `model=council_model`; verify final model is `council_model` regardless of `llm_models` | Resolved model == `council_model` |
| 8 | Selected model persisted to DB | Inspect `instances.instance_metadata["model_override"]` after spawn; verify it matches the resolved model | DB field == resolved model |
| 9 | Restore instance re-uses persisted model | Spawn instance → kill daemon → restart → restore; verify model is identical to pre-restart | Model unchanged after restart |
| 10 | Backward compatibility — `llm_models` absent | Test with no `llm_models` field; verify behaves identically to pre-feature behavior | All existing tests pass unchanged |
| 11 | Backward compatibility — `llm_models: []` | Test with empty array; verify falls back to `llm_model` (or default) | All existing tests pass unchanged |
| 12 | Single-entry `llm_models: [{m, 1}]` always returns that model | Test with single entry, 10,000 samples | All 10,000 samples == m |
| 13 | API exposes selected model in instance response | GET /instances/{id} response includes `model` (or equivalent) field reflecting the resolved model | Field present and correct |

## Research Insights

1. **Model resolution chain** — `daemon/services/instance_lifecycle.py:580-610` (`_build_llm_config`) is the real resolution point. Current priority: `override_model` (spawn-time) > `metadata.llm_model` > default. New priority slots load-balancing between `override_model` and `metadata.llm_model`.
2. **C6 pattern is critical** — `daemon/registry.py:202` has `extra="ignore"`, so a new field requires BOTH a Pydantic declaration AND a `meta.get(...)` loader line at `daemon/registry.py:384` (where `llm_model=meta.get("llm_model")` already exists). Without both, the field is silently dropped.
3. **Council override preserved naturally** — `daemon/tools/instance.py:1039-1171` (`spawn_councilor`) passes its REQUIRED `model` as the spawn-time `model` parameter, which already has highest priority. Load balancing is skipped whenever `override_model` is non-empty.
4. **Allowed-models semantics** — `daemon/config.py:137-159`: empty list = no restriction; non-empty = case-insensitive exact match. Same semantics should apply to filtering `llm_models`.
5. **DB persistence pattern proven** — `daemon/services/instance_lifecycle.py:939-944` already persists `validated_model_override` to `instance_metadata["model_override"]`. The load-balanced selection (source=="llm_models") is persisted the same way; "llm_model" and "default" sources are NOT persisted (backward compatible). Resolution tracks `resolved_source` to gate persistence correctly.
6. **Test pattern proven** — `tests/test_governor_integration.py:229-274` shows the C6 regression test style (load real meta.json, discover, get, assert field). Reuse this exact pattern.
7. **All spawn paths converge on `manager.spawn_instance(model=...)`** — 5 paths identified (spawn_instance tool, spawn_councilor, spawn_instance_with_mcp, explore caller_model_overrides, invoke_agent_and_wait). All preserve priority naturally because the integration point is `manager.spawn_instance` itself.

## Open Questions

1. **Should the resolved model be exposed in the HTTP `GET /instances/{id}` response?** Currently the API has its own model field derivation logic. Need to verify whether the existing response already reflects `model_override` or if a separate field is needed for observability. (Recommend: yes, for debuggability of non-deterministic selection.)
2. **Should weight clamping warn or silently clamp?** The spec says "clamped", but doesn't specify whether to warn. Recommendation: silent clamp (matches existing `weight: 0` semantics in some libraries); document in meta.json schema. To be confirmed with caller.
3. **What happens if `allowed_models` is changed after instance creation?** Spec is silent. Recommendation: instance keeps its original selection (we don't re-resolve). Already the natural behavior since selection happens once.
4. **Should we re-run load balancing on instance restore, or freeze?** Spec says "remembered for ENTIRE instance lifetime". Recommendation: freeze (persist and reuse on restore). Implementation in Phase 4.
5. **Schema documentation location?** Where should `llm_models` schema be documented for users? Likely `docs/agent-meta-schema.md` or similar. Out of scope for implementation but flagged.
