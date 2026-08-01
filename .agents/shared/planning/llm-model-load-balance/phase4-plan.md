# Phase 4: DB Persistence + Restore

## Objective

Persist the load-balanced model selection to the DB column `instances.instance_metadata["model_override"]` **ONLY when the model was selected via `llm_models` load balancing** (source == "llm_models"). For all other sources, existing behavior is preserved — no new persistence for "llm_model" or "default" sources. This ensures full backward compatibility: only the new feature (load balancing) gets the freeze-and-restore treatment.

This phase makes the "remembered for ENTIRE instance lifetime" guarantee durable.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Trace the `restore_instance` code path to confirm whether it reads `instance_metadata["model_override"]` and passes it to `_build_llm_config` as `override_model` | none | Documented call chain from `restore_instance()` → `spawn_instance()` → `_build_llm_config()`. If it already works, no code change. |
| 2 | Modify the persistence block at line 939-944 to persist `resolved_model` ONLY when `resolved_source == "llm_models"` | Phase 3 | After spawn: `model_override` is written ONLY for load-balanced instances; "override" source still uses existing `validated_model_override` persistence; "llm_model" and "default" sources do NOT write `model_override` |
| 3 | Add a guard to avoid clobbering an existing `model_override` value if `resolved_model` is somehow None/empty | Task 2 | Defensive: if `resolved_model` is falsy, fall back to existing `validated_model_override` logic (preserves prior behavior) |
| 4 | Add a logger.info statement at persistence time when source == "llm_models" | Task 2 | Log line: `instance_model_persisted: instance=... model=... source=llm_models` for load-balanced instances only |
| 5 | Add `model` to the instance spawn response payload if not already present | Task 2 | `POST /spawn` response includes the resolved model name for observability |
| 6 | Write an integration test that spawns an instance, simulates restart, and verifies model is unchanged | Task 1, 2 | Test passes: spawn → kill → restart → restore → model == original |

## Coupling

- **Tight with Phase 3** — consumes the `resolved_model_out` from Phase 3. If Phase 3's contract isn't honored, this phase silently persists the wrong value.
- **Independent of Phase 1, 2** — those are upstream of Phase 3.
- **Independent of Phase 5** — but Phase 5 tests verify this phase's behavior end-to-end.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| ~~Behavioral change: persisting `llm_model` instances~~ | — | — | **RESOLVED:** Only "llm_models" source writes `model_override`. "llm_model" and "default" sources are NOT persisted → no behavioral change for existing agents. Full backward compatibility preserved. |
| Restore path doesn't read `model_override` and instead re-resolves from `metadata.llm_model` | High | Low | Phase 4 Task 1 explicitly traces this path. If broken, fix the restore path. Covered by integration test in Task 6. |
| Persisting to JSONB column with `None` value clobbers existing override | Medium | Low | Task 3 adds a guard: only set if `resolved_model` is truthy. Otherwise fall back to existing `validated_model_override` persistence. |
| Persisted model name differs in case from the original (e.g., `Gpt-4o` vs `gpt-4o`) | Low | Medium | Normalize via `.strip()` at persistence time (already done in `_build_llm_config`). Add case-sensitivity test. |
| `instance_metadata` schema doesn't include `model_override` key on restore (e.g., field added later, old rows missing it) | Low | Medium | Restore path should treat missing key as None (no override). Verify in Task 1. |
| PostgreSQL JSONB column vs SQLite TEXT JSON serialization differs for unicode model names | Low | Low | Model names are ASCII in practice. Existing `validated_model_override` already persists strings; same pattern. |

## Code Sketch

### Task 1: Trace restore path (research task)

Find the `restore_instance` method in `daemon/services/instance_lifecycle.py` and answer:
1. Does it load `instance_metadata["model_override"]` from the DB?
2. Does it pass that value as `model` to a downstream `spawn_instance` call (or to `_build_llm_config` directly)?
3. If yes to both → no code change needed for restore; existing plumbing handles it.
4. If no to either → fix in Task 1b below.

Expected answer (based on the existing pattern at `instance_lifecycle.py:939-944`): yes to both. The current code already persists `validated_model_override` and presumably restores it. Verify by reading the restore code.

If fix is needed (Task 1b):

```python
# Pseudocode — actual location depends on existing restore code
async def restore_instance(self, instance_id: str) -> Instance:
    db_row = await self._instance_repo.get(instance_id)
    persisted_override = db_row.instance_metadata.get("model_override") if db_row.instance_metadata else None
    # ... existing restore logic ...
    return await self.spawn_instance(
        # ... existing args ...
        model=persisted_override,  # pass through as spawn-time override
    )
```

### Task 2 + 3: Modified persistence block

Location: `daemon/services/instance_lifecycle.py:939-944`

```python
# resolved_model and resolved_source come from spawn_instance() local scope
# (set in Phase 3 resolution block).

# Case 1: source == "override" — existing behavior, no change
if validated_model_override:
    instance_metadata["model_override"] = validated_model_override

# Case 2: source == "llm_models" — persist load-balanced selection (NEW)
# This is the ONLY new persistence. "llm_model" and "default" sources do NOT
# write model_override, preserving backward compatibility.
elif resolved_source == "llm_models" and resolved_model and resolved_model.strip():
    instance_metadata["model_override"] = resolved_model.strip()
    logger.info(
        "instance_model_persisted: instance=%s model=%s source=llm_models",
        getattr(metadata, "agent_id", "<unknown>"),
        resolved_model.strip(),
    )

# Case 3: source in ("llm_model", "default") — NO persistence.
# These values remain dynamic (re-resolved on restore), preserving backward compat.
```

Note: `resolved_model` and `resolved_source` are local variables in `spawn_instance()` (set in Phase 3's resolution block). They flow directly from the resolution block at line 849 to the persistence block at line 939-944 — no out-param needed.

### Task 5: API response payload

Location: depends on the spawn endpoint. Likely `daemon/api/instances.py` or similar.

Verify that the spawn response already includes `model` (or equivalent). If not:

```python
# Pseudocode for spawn response
return {
    "instance_id": new_instance_id,
    "agent_id": agent_id,
    "model": resolved_model,  # NEW: surface resolved model in response
    # ... other fields ...
}
```

### Task 6: Integration test outline

Location: `tests/test_llm_load_balance_restore.py` (new file)

```python
@pytest.mark.asyncio
async def test_load_balanced_model_survives_restore(
    postgres_db, daemon_factory, test_agent_with_llm_models
):
    """Spawn with llm_models, then call restore_instance, verify model is frozen.

    Agent 'lb_test' is pre-registered with llm_models=[{m1,50},{m2,50}]
    via the test_agent_with_llm_models fixture.
    """
    daemon = await daemon_factory()

    # 1. Spawn instance → capture the load-balanced model
    result = await daemon.spawn_instance(
        agent_id="lb_test",
        model=None,  # no override → triggers load balancing
    )
    instance_id = result[0] if isinstance(result, tuple) else result.id
    original_model = await daemon.get_instance_model(instance_id)
    assert original_model in ("m1", "m2")

    # 2. Verify model_override was persisted to DB (source=="llm_models")
    db_row = await postgres_db.fetch_one(
        "SELECT instance_metadata FROM instances WHERE instance_id = $1",
        instance_id,
    )
    assert db_row["instance_metadata"]["model_override"] == original_model

    # 3. Call the ACTUAL restore_instance path (not get_instance_model)
    #    restore_instance reconstructs the graph from DB state.
    restored_instance = await daemon.restore_instance(instance_id)

    # 4. Verify the restored instance uses the SAME model (not re-randomized)
    restored_model = await daemon.get_instance_model(instance_id)
    assert restored_model == original_model, (
        f"Model changed after restore: {original_model} -> {restored_model}. "
        "Restore path did not read model_override from DB."
    )

    # 5. Also verify by inspecting the restored instance's llm_config
    assert restored_instance.llm_config["model"] == original_model


@pytest.mark.asyncio
async def test_non_load_balanced_model_not_persisted(
    postgres_db, daemon_factory, test_agent_fixture
):
    """Agent with llm_model but NO llm_models → model_override NOT written.

    Verifies backward compatibility: non-load-balanced agents don't get
    model_override persistence. Restore re-resolves from metadata.llm_model.
    """
    daemon = await daemon_factory()
    result = await daemon.spawn_instance(agent_id="plain_test", model=None)
    instance_id = result[0] if isinstance(result, tuple) else result.id

    db_row = await postgres_db.fetch_one(
        "SELECT instance_metadata FROM instances WHERE instance_id = $1",
        instance_id,
    )
    # model_override should NOT be present for llm_model/default sources
    assert "model_override" not in db_row["instance_metadata"], (
        "model_override should not be written for non-load-balanced instances"
    )
```

## Edge Cases Handled by Phase 4

| Case | Behavior |
|------|----------|
| `validated_model_override` set (council/leader) | Source = "override". `model_override` persisted as the override value (existing behavior). Restore uses it. |
| `llm_models` non-empty, no override, load-balancing succeeds | Source = "llm_models". `model_override` persisted as the random selection (NEW). Restore uses it. |
| `llm_models` non-empty but ALL filtered → falls back to `llm_model` | Source = "llm_model". `model_override` NOT persisted (backward compatible). Restore re-resolves from `metadata.llm_model`. |
| `llm_model` set, no `llm_models`, no override | Source = "llm_model". `model_override` NOT persisted (backward compatible). Restore re-resolves from `metadata.llm_model`. |
| No metadata, no override | Source = "default". `model_override` NOT persisted (backward compatible). Restore re-resolves from config default. |
| `resolved_model` is None or empty string | Should not happen (resolution always sets it). If it does, fall back to `validated_model_override`. Defensive guard. |
| DB write fails (rare; DB outage) | Existing error handling in `spawn_instance` propagates the exception. Instance not created. Caller retries. |
| Existing instance row lacks `model_override` key (legacy data) | Restore path returns None for the override → falls through to resolution chain. Same as if no override was ever set. |

## Verification Checklist (Manual + Automated)

- [ ] Spawn with `llm_models=[{a, 1}, {b, 1}]` → DB `model_override` is one of `{a, b}` (source="llm_models").
- [ ] Kill daemon, restart, restore instance → model unchanged.
- [ ] Spawn another instance with same `llm_models` → different selection (with high probability).
- [ ] Spawn with `llm_models=[]` → DB `model_override` is NOT set (backward compatible; no freeze).
- [ ] Spawn with `model="forced"` (council path) → `model_override == "forced"` (source="override"; existing behavior).
- [ ] Spawn with no `llm_models`, `llm_model="z"` → DB `model_override` is NOT set (backward compatible).
- [ ] Spawn with no `llm_models`, no `llm_model` → DB `model_override` is NOT set (backward compatible).
- [ ] Spawn with `llm_models=[{disallowed, 1}]` (all filtered) + `llm_model="fallback"` → DB `model_override` is NOT set (source="llm_model"; backward compatible).

## Exit Criterion

- `model_override` is written ONLY when source == "llm_models" (load-balanced instances).
- `model_override` is NOT written for "llm_model" or "default" sources (backward compatible).
- "override" source uses existing `validated_model_override` persistence (no change).
- Restore path re-applies the persisted `model_override` for load-balanced instances.
- Integration test passes: spawn → restart → restore → model unchanged (only for llm_models source).
- API response surfaces the resolved model (Task 5).
- Logs emitted when source == "llm_models" (Task 4).
- Ready for Phase 5 to add comprehensive test coverage.
