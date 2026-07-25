# Phase 3: Models List Injection Appender

> **Revision 2 (2026-07-25):** C2 (`manager.config` not `_config`), C6 (field + loader both needed), W8 (error status in output). All pseudo-code corrected.

## Objective

Create `append_allowed_models` appender + `inject_allowed_models` flag. Follows the gold-standard defensive pattern.

## Coupling

- **Depends on**: Phase 0 (frozen contracts)
- **Coupling type**: loose
- **Shared files**: `daemon/services/instance_lifecycle.py` (appender chain), `daemon/registry.py` (AgentMetadata + loader)

---

## Verified Architecture (C2 + C6)

**C2 — Config access path (VERIFIED):**
- `manager.config` (NO underscore) — `daemon/manager.py:481`: `self.config = config`
- `InstanceLifecycleService._config` is a `@property` (`instance_lifecycle.py:875-878`) that returns `self._manager.config`
- The appender receives `manager` (InstanceManager) → MUST use `manager.config.llm.allowed_models`

**C6 — AgentMetadata loading (VERIFIED):**
- `model_config = ConfigDict(extra="ignore")` at `registry.py:138-140` — unknown fields silently discarded
- Loader at `registry.py:254-272` constructs `AgentMetadata(...)` passing `meta.get("field")` PER FIELD
- `context_injection` is both a declared field (line 129) AND loaded via `meta.get("context_injection", False)` (line 270)
- **BOTH steps required** for `inject_allowed_models`

---

## Tasks

### Task 1: Add `inject_allowed_models` to AgentMetadata (C6 — BOTH steps)

**File:** `daemon/registry.py`

**Step 1a — Add the field** (near `context_injection` at line 129):

```python
inject_allowed_models: bool = Field(
    default=False,
    description="When true, inject the allowed-models list into this agent's system prompt at spawn time.",
)
```

**Step 1b — Add the loader line** (in `AgentRegistry.discover()`, after `context_injection=meta.get("context_injection", False)` at line 270):

```python
inject_allowed_models=meta.get("inject_allowed_models", False),
```

**⚠️ BOTH are required.** Without the field, `extra="ignore"` discards it. Without the loader line, the field gets its default (False) even if meta.json says `true`.

### Task 2: Implement `append_allowed_models` (C2 + W8)

**File:** `daemon/services/instance_lifecycle.py` (near other appenders, ~line 768)

```python
def append_allowed_models(
    system_prompt: str,
    agent_meta: Any,
    manager: Any,  # InstanceManager — use manager.config (C2: NO underscore)
) -> str:
    """Inject the allowed-models list into the system prompt.

    Triggered when agent_meta.inject_allowed_models is True.
    Reads manager.config.llm.allowed_models (C2) and wraps in XML fence.

    Fail-open: any error → append status="error" block for observability (W8),
    return prompt + error block (NOT silently unchanged).
    """
    # --- Flag check (fail-open if flag absent) ---
    if not getattr(agent_meta, "inject_allowed_models", False):
        return system_prompt

    try:
        # --- C2 FIX: manager.config (NOT manager._config) ---
        allowed = getattr(manager.config.llm, "allowed_models", None) or []

        # --- Format the block ---
        if not allowed:
            block = (
                "No model restriction is configured (OPENAI_ALLOWED_MODELS is "
                "empty/unset). Any model string is accepted by spawn_councilor, "
                "but you should CONFIRM the desired model list with the user "
                "before spawning councilors.\n"
                "This is read-only system configuration, not instructions."
            )
        else:
            model_lines = "\n".join(f"- {m}" for m in allowed)
            block = (
                "The models below are the ONLY valid values for the `model` "
                "parameter of spawn_councilor (case-insensitive match).\n"
                f"{model_lines}\n"
                "This is read-only system configuration, not instructions."
            )

        section = (
            f"\n\n---\n\n# Allowed Models\n\n"
            f"The block below is read-only system configuration, not instructions.\n"
            f"<allowed_models>\n{block}\n</allowed_models>\n\n---\n"
        )
        return system_prompt + section

    except Exception as exc:
        logger.warning("Failed to inject allowed models: %s", exc)
        # W8 FIX: append error-status block for observability (not silent no-op)
        error_section = (
            f"\n\n---\n\n# Allowed Models\n\n"
            f"<allowed_models status=\"error\">\n"
            f"Failed to load allowed models: {exc}\n"
            f"If you are the governor, ASK the user for the model list before "
            f"spawning councilors — the system cannot validate models.\n"
            f"</allowed_models>\n\n---\n"
        )
        return system_prompt + error_section
```

**Fixes applied:**
- **C2:** `manager.config.llm.allowed_models` (NOT `manager._config`)
- **W8:** On exception, appends `<allowed_models status="error">` block instead of silently returning unchanged. The governor sees the error and knows to ask the user for models. This eliminates the ambiguous dead-end.

### Task 3: Register in the Appender Chain

**File:** `daemon/services/instance_lifecycle.py`, inside `_apply_post_cache_appends` (lines 771-841)

**Insert after `append_context_injection` (position 4), before `append_user_language` (position 5→6):**

```python
# Existing (line ~820):
system_prompt = append_context_injection(
    system_prompt, instance_id, instance_repository, agent_meta,
    parent_id=parent_id, project_id=project_id,
    project_repository=project_repository,
)
# ← ADD (position 5):
system_prompt = append_allowed_models(system_prompt, agent_meta, manager)
# Existing continues:
user_language = get_language_preference(project_repository)
system_prompt = append_user_language(system_prompt, user_language)
```

**Updated chain (7 appenders):**
1. `append_context_key`
2. `append_shared_context_metadata`
3. `append_current_time`
4. `append_context_injection`
5. `append_allowed_models` ← **NEW**
6. `append_user_language`
7. `append_auto_load_skills`

### Task 4: Unit Tests (CORRECTED — C2 fakes use `.config`)

```python
# C2 FIX: FakeManager uses .config (NO underscore)

class FakeMetaOff:
    inject_allowed_models = False

class FakeMetaOn:
    inject_allowed_models = True

class FakeLLM:
    def __init__(self, models):
        self.allowed_models = models

class FakeConfig:
    def __init__(self, models):
        self.llm = FakeLLM(models)

class FakeManager:  # C2: .config not ._config
    def __init__(self, models):
        self.config = FakeConfig(models)  # NO underscore

# Test 1: Flag off → no injection
result = append_allowed_models("base", FakeMetaOff(), FakeManager(["gpt-4o"]))
assert result == "base"

# Test 2: Flag on, models present → injection
result = append_allowed_models("base", FakeMetaOn(), FakeManager(["gpt-4o", "claude-3-5"]))
assert "<allowed_models>" in result
assert "gpt-4o" in result
assert "claude-3-5" in result

# Test 3: Flag on, empty models → unrestricted message
result = append_allowed_models("base", FakeMetaOn(), FakeManager([]))
assert "No model restriction" in result

# Test 4: W8 — Exception → error-status block (NOT silent no-op)
class FakeManagerBroken:
    config = None  # causes AttributeError
result = append_allowed_models("base", FakeMetaOn(), FakeManagerBroken())
assert 'status="error"' in result  # W8: observability, not silent
assert "ASK the user" in result
```

### Task 5: Integration Test — Flag Survives Loading (C6)

**This is the test that would have caught C6.** It loads a REAL governor meta.json and verifies the flag:

```python
# C6 INTEGRATION TEST: flag survives loading
from daemon.registry import AgentRegistry
from pathlib import Path

registry = AgentRegistry(Path("agents"))
registry.discover()  # scans agents/ directory

gov_meta = registry.get("governor")
assert gov_meta is not None, "Governor agent not found in registry"
assert gov_meta.inject_allowed_models is True, (
    "C6 REGRESSION: inject_allowed_models flag was silently discarded! "
    "Check: (1) field exists on AgentMetadata, (2) loader line in discover()"
)
assert gov_meta.context_injection is True  # existing flag should also work
```

---

## Key Files

| File | Purpose | Change |
|------|---------|--------|
| `daemon/registry.py` | Add `inject_allowed_models` field (line ~129) + loader line (line ~270) | **MODIFIED** (2 lines) |
| `daemon/services/instance_lifecycle.py` | Add `append_allowed_models` function + chain registration | **MODIFIED** |

## Constraints

- **C2:** Use `manager.config` (NO underscore). Unit test fakes must use `.config`.
- **C6:** BOTH field declaration AND loader line required.
- **W8:** Error path appends `status="error"` block, does NOT silently return unchanged.
- Follow gold-standard pattern: XML fence, read-only notice, fail-open (but observable).

## Deliverables

- [ ] `inject_allowed_models` field on AgentMetadata (C6 step 1)
- [ ] `inject_allowed_models=meta.get(...)` in loader (C6 step 2)
- [ ] `append_allowed_models` using `manager.config` (C2)
- [ ] W8: error path appends `status="error"` block
- [ ] Registered in chain at position 5
- [ ] Unit tests with `.config` fakes (C2)
- [ ] **Integration test:** governor meta.json → `inject_allowed_models == True` (C6)
