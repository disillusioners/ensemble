# Phase 2: `spawn_councilor` + `clear_councilor_errors` Tools

> **Revision 2 (2026-07-25):** C3, C4, C5, W6, W7 fixes applied. `clear_councilor_errors` tool added (C1/D7). All pseudo-code corrected against verified source.

## Objective

Implement two tools inside `create_instance_tools()`:
1. **`spawn_councilor`** — strict-validation spawn with REQUIRED model + councilor_agent_id
2. **`clear_councilor_errors`** — clears the dependency bus's sticky parent-error flag (C1 mitigation)

## Coupling

- **Depends on**: Phase 0 (frozen contracts)
- **Coupling type**: loose
- **Shared files**: Modifies `daemon/tools/instance.py` (adds closures inside `create_instance_tools()`) and `daemon/tools/_tool_registry.py` (adds category). Reads from `daemon/services/instance_lifecycle.py` and `daemon/services/dependency_bus.py` (read-only).

---

## Verified Architecture (C5 — the factory reality)

**`create_instance_tools()`** (`daemon/tools/instance.py:679-1314`) is the SINGLE aggregate factory:
- Builds ALL tools inline as closures: `spawn_instance` (706-808), `send_message` (813-1000), `terminate_instance` (1021-1040), `list_instances` (1042-1053), `get_instance_info` (1055-1076)
- Extends with category helpers (project, RAG, etc.) at lines 1129-1301
- **Then filters** the complete list via `_apply_tool_filter(tools, agent_id, mcp_tool_names)` at line 1311-1314

**There is NO per-category factory dispatch.** A standalone `create_council_tools()` would never be invoked. The new tools MUST be defined inside `create_instance_tools()`.

**Closure variables available inside `create_instance_tools()`:**
- `manager` — InstanceManager (has `.config`, `.spawn_instance()`, etc.)
- `current_instance_id` — the calling instance's ID
- `caller_agent_id` (or `agent_id`) — the calling agent's ID (for team membership)

---

## Tasks

### Task 1: Add `SpawnCouncilorInput` Model

**Location:** `daemon/tools/instance.py` (near `SpawnInstanceInput`, ~line 642) OR a new `daemon/tools/council.py` imported at top of `instance.py`.

```python
class SpawnCouncilorInput(BaseModel):
    """Input schema for spawn_councilor. Both fields REQUIRED."""
    councilor_agent_id: Annotated[
        str,
        Field(description="REQUIRED. Agent to spawn as councilor. Must be in governor's team_members.")
    ]
    model: Annotated[
        str,
        Field(description="REQUIRED. LLM model. Must be in <allowed_models>. RAISES on invalid — no fallback.")
    ]
    instance_name: Annotated[
        str | None,
        Field(default=None, description="Optional name, e.g. 'councilor-gpt4o'")
    ] = None
    project_id: Annotated[
        str | None,
        Field(default=None, description="Optional project ID")
    ] = None

    @field_validator("councilor_agent_id", "model")
    @classmethod
    def _reject_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("councilor_agent_id and model are REQUIRED and must be non-empty.")
        return v.strip()
```

### Task 2: Implement `spawn_councilor` — CORRECTED (C3, C4, C5, W6, W7)

**Location:** INSIDE `create_instance_tools()` at `daemon/tools/instance.py`, after `spawn_instance` definition (~line 811).

```python
@register_tool_category("council")
@tool(args_schema=SpawnCouncilorInput)
async def spawn_councilor(
    councilor_agent_id: Annotated[str, Field(description="REQUIRED. Agent to spawn as councilor.")],
    model: Annotated[str, Field(description="REQUIRED. LLM model. Must be in <allowed_models>.")],
    instance_name: Annotated[str | None, Field(default=None)] = None,
    project_id: Annotated[str | None, Field(default=None)] = None,
) -> str:
    """Spawn a councilor instance with a REQUIRED, validated model.

    Unlike spawn_instance: REQUIRES both params, RAISES on invalid model
    (no silent fallback), RAISES on invalid agent_id.

    Returns: instance_id + send_message instructions.
    """
    # ─── STEP 1: Validate councilor_agent_id (C4: resolve_to_id returns None, never raises) ───
    registry = get_registry()
    resolved_agent_id = registry.resolve_to_id(councilor_agent_id)
    if resolved_agent_id is None:  # C4 FIX: check for None, not exception
        raise ValueError(
            f"councilor_agent_id '{councilor_agent_id}' is not a valid agent in the registry."
        )

    # ─── STEP 2: Validate team membership (C3: _check_team_membership returns str|None, never raises) ───
    err = _check_team_membership(caller_agent_id, resolved_agent_id)
    if err is not None:  # C3 FIX: check return value, not rely on exception
        raise ValueError(err)

    # ─── STEP 3: Validate model STRICTLY (raise, do not fallback) ───
    lifecycle = manager._lifecycle_service
    validated_model = lifecycle._resolve_model_override(model)
    if validated_model is None:
        # STRICT: raise instead of silent fallback
        allowed = getattr(manager.config.llm, "allowed_models", None) or []  # C2 FIX: manager.config not _config
        if not allowed:
            raise ValueError(
                f"Model '{model}' was rejected despite no restriction. Unexpected — report to user."
            )
        raise ValueError(
            f"Model '{model}' is NOT in allowed_models. Valid models: {allowed}. "
            f"No fallback — correct the model and retry."
        )

    # ─── STEP 3b: W7 — Normalize to canonical model name ───
    canonical_model = next(
        (m for m in allowed if m.lower() == validated_model.lower()),
        validated_model,  # fallback to caller spelling if unrestricted
    )

    # ─── STEP 4: Delegate to lifecycle (W6: pass validated/canonical model immutably) ───
    new_instance_id, returned_model = manager.spawn_instance(
        agent_id=resolved_agent_id,
        instance_id=None,
        parent_id=current_instance_id,
        project_id=project_id,
        instance_name=instance_name,
        model=canonical_model,  # W6: pass canonical, not raw — prevents TOCTOU revalidation
    )

    # ─── STEP 5: Return success ───
    return (
        f"Successfully spawned councilor instance: {new_instance_id}\n"
        f"Agent: {resolved_agent_id} | Model: {canonical_model}\n"
        f"To send the request, use: send_message(instance_id=\"{new_instance_id}\", message=\"...\")"
    )
```

**Critical fixes applied:**
- **C3:** `err = _check_team_membership(...)` + `if err is not None: raise ValueError(err)` — the function returns a string, never raises
- **C4:** `if resolved_agent_id is None: raise ValueError(...)` — `resolve_to_id` returns None on miss, never raises
- **C5:** Defined INSIDE `create_instance_tools()` as a closure — uses `manager`, `caller_agent_id`, `current_instance_id` from closure scope
- **C2:** `manager.config.llm.allowed_models` (NOT `manager._config`)
- **W6:** Passes `canonical_model` (not raw `model`) to `manager.spawn_instance()` — prevents TOCTOU revalidation at lifecycle line 1145
- **W7:** Normalizes to canonical name from `allowed_models` list before spawn

### Task 3: Implement `clear_councilor_errors` — NEW (C1/D7)

**Location:** INSIDE `create_instance_tools()`, after `spawn_councilor`.

```python
@register_tool_category("council")
@tool
async def clear_councilor_errors() -> str:
    """Clear the sticky parent-error flag so the governor can finalize as COMPLETED.

    The dependency bus marks the parent as ERROR if ANY child (councilor) fails.
    This flag is STICKY — once set, the parent terminal status is forced to ERROR
    even if synthesis succeeded. Call this tool AFTER successful synthesis to
    clear the flag and allow COMPLETED finalization.

    Do NOT call if synthesis failed (all councilors errored) — let ERROR propagate.
    """
    from daemon.services.dependency_bus import get_dependency_bus

    bus = get_dependency_bus()
    if bus is None:
        return "Warning: No dependency bus available — cannot clear parent-error flag."

    try:
        bus.clear_parent_error(current_instance_id)
        return f"Cleared parent-error flag for instance {current_instance_id[:8]}..."
    except Exception as e:
        return f"Warning: Failed to clear parent-error flag: {e}"
```

**Design notes:**
- Uses `current_instance_id` from closure scope (the governor's own instance ID)
- Calls `bus.clear_parent_error()` which exists at `dependency_bus.py:1487-1507` — pops both `_parent_errored[parent_id]` and `_parent_error_message[parent_id]`
- Fail-safe: returns a warning string rather than raising (clearing is best-effort)
- Governor calls this in workflow Step 5 (before delivery) per rule.md

### Task 4: Register the "council" Category

**File:** `daemon/tools/_tool_registry.py` (add to `CATEGORY_MODULES`, ~line 207-236)

```python
"council": "daemon.tools.instance",  # tools live in instance.py's create_instance_tools()
```

**Note:** If the tools are defined in `instance.py` (inside `create_instance_tools()`), the category maps to `daemon.tools.instance`. The `@register_tool_category("council")` decorators on both tools ensure they appear in `tool_categories["council"]` after `scan_tools_for_full_docs()`.

### Task 5: Add Tools to the Factory's Return List (C5)

**File:** `daemon/tools/instance.py`, inside `create_instance_tools()`, in the tools list assembly (~line 1107-1127)

```python
# Existing tools list (line 1107-1127):
tools = [
    read_file, write_file, edit_file, ...  # filesystem
    spawn_instance,
    spawn_councilor,       # ← ADD
    clear_councilor_errors, # ← ADD
    send_message,
    terminate_instance,
    list_instances,
    get_instance_info,
    ...
]
```

**Why both the decorator AND the list:** The `@register_tool_category("council")` decorator registers metadata for filtering. But the tool must ALSO be in the `tools` list that `_apply_tool_filter()` receives. Without both, the tool is either unclosured (not in list) or unfilterable (no category metadata).

**The filter then works:** When governor's `tools.allow: ["council"]` is resolved, `resolve_tool_filter()` expands "council" → `["spawn_councilor", "clear_councilor_errors"]` → `_apply_tool_filter` keeps only those tools.

### Task 6: Verify Closure Access

**Confirm the closure variables are available** where the tools are defined:

```python
# Inside create_instance_tools(manager, current_instance_id, agent_id):
# - manager: InstanceManager ✓ (has .config, ._lifecycle_service, .spawn_instance)
# - current_instance_id: str ✓ (governor's instance ID)
# - caller_agent_id: str ✓ (captured from agent_id parameter)
#
# spawn_councilor uses all three: manager, caller_agent_id, current_instance_id
# clear_councilor_errors uses: current_instance_id
```

**⚠️ Implementer must verify** the exact variable names by reading `create_instance_tools()` signature (line 679) and the closure captures used by `spawn_instance` (706-808).

---

## Strict Validation Flow (Corrected)

```raw
spawn_councilor(councilor_agent_id, model, ...) called
  │
  ├─ 1. registry.resolve_to_id(councilor_agent_id)
  │     └─ Returns None? → RAISE ValueError          (C4 FIX)
  │
  ├─ 2. _check_team_membership(caller_agent_id, resolved)
  │     └─ Returns non-None string? → RAISE ValueError  (C3 FIX)
  │
  ├─ 3. lifecycle._resolve_model_override(model)
  │     └─ Returns None? → RAISE ValueError (NO fallback)
  │
  ├─ 3b. Normalize to canonical model name              (W7)
  │
  ├─ 4. manager.spawn_instance(model=canonical_model)   (W6: immutable)
  │
  └─ 5. Return success
```

---

## Key Files

| File | Purpose | Change |
|------|---------|--------|
| `daemon/tools/instance.py` | Add `SpawnCouncilorInput`, `spawn_councilor`, `clear_councilor_errors` INSIDE `create_instance_tools()`; add to tools list | **MODIFIED** |
| `daemon/tools/_tool_registry.py` | Add `"council"` to `CATEGORY_MODULES` | **MODIFIED** (1 line) |

## Constraints

- **Do NOT modify `spawn_instance`.** Backward compatible.
- **C5: Tools MUST be inside `create_instance_tools()`.** A separate factory will not be called.
- **C3: Check `_check_team_membership` return value** — it returns str|None, never raises.
- **C4: Check `resolve_to_id` for None** — it returns str|None, never raises.
- **W6: Pass canonical model** — prevents TOCTOU revalidation.
- **C2: Use `manager.config`** — not `manager._config`.
- Both tools need `@register_tool_category("council")` AND inclusion in the tools list.

## Deliverables

- [ ] `SpawnCouncilorInput` model with REQUIRED fields + empty-rejection validator
- [ ] `spawn_councilor` defined inside `create_instance_tools()` with C3/C4/C5/W6/W7 fixes
- [ ] `clear_councilor_errors` defined inside `create_instance_tools()` (C1/D7)
- [ ] Both tools added to the tools list (~line 1107-1127)
- [ ] `"council"` category in `CATEGORY_MODULES`
- [ ] Unit test: invalid model → raises ValueError (not silent fallback)
- [ ] Unit test: invalid agent_id → raises ValueError (C4)
- [ ] Unit test: non-team-member agent → raises ValueError (C3)
- [ ] Unit test: clear_councilor_errors clears the flag
- [ ] **Integration test (C5):** governor instance has `spawn_councilor` bound (not just unit test with faked factory)
