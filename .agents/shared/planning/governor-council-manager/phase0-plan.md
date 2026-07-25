# Phase 0: Foundation — Frozen Contracts

> **NEW in Rev 2.** This phase exists to freeze all cross-cutting interfaces BEFORE Phases 1-3 implement against them. Per review suggestion #3: "Lock the schema/tool-contract first (foundation phase), then parallelize implementation against frozen interfaces."

## Objective

Define and freeze the exact signatures, field names, and insertion positions that Phases 1, 2, and 3 depend on. No runtime behavior — just contracts. Once Phase 0 is approved, Phases 1-3 can run fully in parallel with no cross-talk.

## Coupling

- **Depends on**: None (root)
- **Coupling type**: — (defines the contracts all other phases depend on)
- **Why this phase exists**: Phases 2 and 3 both touch `daemon/services/instance_lifecycle.py` and `daemon/registry.py`. Without frozen contracts, they risk merge conflicts. Phase 0 agrees on the exact shapes so each phase knows its boundaries.

---

## Contracts to Freeze

### Contract 1: `spawn_councilor` Tool Signature

**Location (Phase 2):** Defined as a closure INSIDE `create_instance_tools()` at `daemon/tools/instance.py` (after `spawn_instance`, ~line 811).

**Frozen signature:**
```python
@register_tool_category("council")
@tool(args_schema=SpawnCouncilorInput)
async def spawn_councilor(
    councilor_agent_id: Annotated[str, Field(description="REQUIRED. Agent to spawn as councilor. Must be in governor's team_members.")],
    model: Annotated[str, Field(description="REQUIRED. LLM model. Must be in <allowed_models>. RAISES on invalid — no fallback.")],
    instance_name: Annotated[str | None, Field(default=None, description="Optional name, e.g. 'councilor-gpt4o'")] = None,
    project_id: Annotated[str | None, Field(default=None, description="Optional project ID")] = None,
) -> str:
```

**Frozen validation contract (C3/C4 corrections):**
```python
# C4: resolve_to_id returns str|None, never raises
resolved = registry.resolve_to_id(councilor_agent_id)
if resolved is None:
    raise ValueError(f"councilor_agent_id '{councilor_agent_id}' is not a valid agent.")

# C3: _check_team_membership returns str|None, never raises
err = _check_team_membership(caller_agent_id, resolved)
if err is not None:
    raise ValueError(err)

# Model validation — raises on invalid (no silent fallback)
validated = lifecycle._resolve_model_override(model)
if validated is None:
    raise ValueError(f"Model '{model}' is NOT in allowed_models. No fallback.")
```

### Contract 2: `SpawnCouncilorInput` Pydantic Model

**Location (Phase 2):** `daemon/tools/instance.py` (or a new `daemon/tools/council.py` imported by `create_instance_tools`).

**Frozen schema:**
```python
class SpawnCouncilorInput(BaseModel):
    councilor_agent_id: str  # REQUIRED, no default
    model: str               # REQUIRED, no default
    instance_name: str | None = None
    project_id: str | None = None

    @field_validator("councilor_agent_id", "model")
    @classmethod
    def _reject_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("councilor_agent_id and model are REQUIRED.")
        return v.strip()
```

### Contract 3: `clear_councilor_errors` Tool Signature (C1/D7)

**Location (Phase 2):** Defined as a closure INSIDE `create_instance_tools()`.

**Frozen signature:**
```python
@register_tool_category("council")
@tool
async def clear_councilor_errors() -> str:
    """Clear the sticky parent-error flag so the governor can finalize as COMPLETED
    despite individual councilor failures. Call ONLY after successful synthesis."""
    bus = get_dependency_bus()
    if bus is not None:
        bus.clear_parent_error(current_instance_id)
        return f"Cleared parent-error flag for instance {current_instance_id}."
    return "No dependency bus available — no action taken."
```

### Contract 4: `inject_allowed_models` AgentMetadata Field (C6)

**Location (Phase 3):** `daemon/registry.py`

**Frozen field (near `context_injection` at line 129):**
```python
inject_allowed_models: bool = Field(
    default=False,
    description="When true, inject the allowed-models list into this agent's system prompt at spawn time.",
)
```

**Frozen loader line (in `AgentRegistry.discover()`, after `context_injection=meta.get(...)` at line 270):**
```python
inject_allowed_models=meta.get("inject_allowed_models", False),
```

**⚠️ BOTH are required.** `ConfigDict(extra="ignore")` (line 138) discards unknown fields; the per-field loader (254-272) only passes explicitly mapped fields.

### Contract 5: `append_allowed_models` Appender (C2)

**Location (Phase 3):** `daemon/services/instance_lifecycle.py`

**Frozen signature:**
```python
def append_allowed_models(
    system_prompt: str,
    agent_meta: Any,
    manager: Any,  # InstanceManager — use manager.config (NO underscore, C2)
) -> str:
```

**Frozen chain position:** Insert as the 5th appender in `_apply_post_cache_appends` (after `append_context_injection`, before `append_user_language`):
```python
# Position 5 in the chain:
system_prompt = append_allowed_models(system_prompt, agent_meta, manager)
```

**Frozen config access:** `manager.config.llm.allowed_models` (NOT `manager._config` — C2).

### Contract 6: `"council"` Category Registration

**Location (Phase 2):** `daemon/tools/_tool_registry.py:207-236`

**Frozen entry:**
```python
"council": "daemon.tools.instance",  # or "daemon.tools.council" if separate module
```

### Contract 7: Governor meta.json Shape (Phase 1 reference)

```json
{
  "id": "governor",
  "tools": { "allow": ["council", "instance", ...] },
  "context_injection": true,
  "inject_allowed_models": true,
  "team_members": ["developer", "coder", "wanderer", "explorer", "doc-writer", "reviewer"]
}
```

**⚠️ Suggestion #4 (stale team_members):** The team_members list includes both `developer` and `coder`. Per critical notes, coder is now standalone (alias removed). Both are valid agents — keep both. But add a note: the governor should validate each team_member exists in the registry and warn on unknown IDs.

---

## Tasks

| # | Task | Output |
|---|------|--------|
| 1 | Review and approve Contracts 1-7 above | Frozen signatures |
| 2 | Confirm `caller_agent_id` closure variable name in `create_instance_tools()` | Verified closure access |
| 3 | Confirm `current_instance_id` closure variable name | Verified closure access |
| 4 | Confirm `get_dependency_bus()` import path for `clear_councilor_errors` | Verified import |

## Deliverables

- [ ] All 7 contracts approved and frozen
- [ ] Closure variable names verified (`caller_agent_id`, `current_instance_id`, `manager`)
- [ ] `get_dependency_bus()` import path confirmed

## Constraints

- This phase produces NO code. It only defines contracts.
- Once frozen, Phases 1-3 implement against these exact shapes.
- If a contract needs to change after Phase 0, it requires re-approval.
