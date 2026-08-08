# Phase 3: Registry & Factory Wiring

## Objective

Register the `"system-log"` category in the tool registry, add the four tool names to `DYNAMIC_TOOL_NAMES` frozenset, and wire the factory call in `create_instance_tools` so the tools are available to all instances. This phase has no logic of its own — it connects Phase 2's module to the daemon's tool infrastructure.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Add `"system-log": "daemon.tools.system_log_tools"` to `CATEGORY_MODULES` in `_tool_registry.py` | Phase 2 complete | Entry exists in dict; category resolves to the module path |
| 2 | Add `ens_system_log_list`, `ens_system_log_read`, `ens_system_log_search`, `ens_system_log_tail` to `DYNAMIC_TOOL_NAMES` frozenset in `_tool_registry.py` | Phase 2 complete | All four names present in the frozenset |
| 3 | Add import + factory call in `create_instance_tools` in `instance.py` | Tasks 1, 2 | `create_system_log_tools` is imported and called; tools extended into the list |
| 4 | Verify startup validation passes — no "unknown tool" errors when an agent has `"system-log"` in tools.allow | Task 3 | Daemon starts cleanly with all four agents configured |

## Detailed File Changes

### `daemon/tools/_tool_registry.py`

**Change 1: CATEGORY_MODULES dict (line ~270)**

Add new entry after `"blueprint"`:

```python
CATEGORY_MODULES: dict[str, str | list[str]] = {
    # ... existing entries ...
    "blueprint": "daemon.tools.blueprint",
    "system-log": "daemon.tools.system_log_tools",   # <-- NEW
}
```

**Change 2: DYNAMIC_TOOL_NAMES frozenset (line 20-47)**

Add four names:

```python
DYNAMIC_TOOL_NAMES: frozenset[str] = frozenset({
    "rag_insert_text",
    # ... existing entries ...
    "tool_help",
    "explore",
    "experience",
    # system-log tools — created by create_system_log_tools() per-instance
    # factory, not registered at import time; see daemon/tools/system_log_tools.py.
    "ens_system_log_list",       # <-- NEW (W5)
    "ens_system_log_read",
    "ens_system_log_search",
    "ens_system_log_tail",
})
```

### `daemon/tools/instance.py`

**Change 3a: Import (line ~200, in the lazy import block)**

Add import alongside other factory imports:

```python
    from .system import create_system_tools
    from .system_log_tools import create_system_log_tools   # <-- NEW
    from .language_tools import create_language_tools
```

**Change 3b: Factory call (after system tools block, line ~2031)**

Insert after the system tools block (after `tools.extend(system_tool_list)`) and before the MCP tools block:

```python
    # ── System Log tools (read-only daemon log access, always available) ──
    # These tools let agents read and search the daemon's own logs for
    # self-healing (investigating runtime bugs by inspecting log output).
    # Read-only with path traversal protection and size caps.
    # The system-log category is auto-granted to agents with
    # "system-log" in tools.allow (see Phase 4 meta.json changes).
    system_log_tool_list = create_system_log_tools(manager, current_instance_id)
    tools.extend(system_log_tool_list)
```

**Placement rationale:** The system-log tools are logically grouped with system tools (both are read-only diagnostic tools). Placing them after `create_system_tools` and before MCP tools keeps the diagnostic tools together. They are "always available" (created for every instance), matching the pattern of chart, image, todo, and system tools — the category filtering in `_apply_tool_filter` gates which agents actually see them.

## Verification Checklist (Task 4)

After all changes:

1. **Module imports** — `from daemon.tools.system_log_tools import create_system_log_tools` resolves without error
2. **Registry entry** — `CATEGORY_MODULES["system-log"]` returns `"daemon.tools.system_log_tools"`
3. **Frozenset membership** — `"ens_system_log_list" in DYNAMIC_TOOL_NAMES` is `True` (same for read/search/tail)
4. **Factory call** — `create_instance_tools(manager, "test-id", "leader")` returns a list containing tools named `ens_system_log_list`, `ens_system_log_read`, `ens_system_log_search`, `ens_system_log_tail`
5. **Category expansion** — `resolve_tool_filter({"system-log"})` expands to the four tool names
6. **Startup validation** — Daemon starts with no config validation errors when `"system-log"` is in any agent's `tools.allow`

## Coupling

- **Tight with:** Phase 2 — references the module path (`daemon.tools.system_log_tools`) and exact tool function names (`ens_system_log_*`). Name mismatches will break startup validation.
- **Loose with:** Phase 4 — agents need the `"system-log"` string in their `tools.allow` for the category to be expanded. But the registry entry must exist first.
- **Independent of:** Phases 1, 5

## Risks

- **R7 (name mismatch):** If `DYNAMIC_TOOL_NAMES` entries don't exactly match the `@tool`-decorated function names, startup validation fails with "unknown tool" errors. Mitigation: exact names are `ens_system_log_list`, `ens_system_log_read`, `ens_system_log_search`, `ens_system_log_tail` — no abbreviations, no aliases. Task 4 verification catches this.

## Exit Criterion

- `CATEGORY_MODULES` has the `"system-log"` entry
- `DYNAMIC_TOOL_NAMES` has all four tool names (`ens_system_log_list`, `ens_system_log_read`, `ens_system_log_search`, `ens_system_log_tail`)
- `create_instance_tools` calls `create_system_log_tools` and extends the tool list
- Daemon starts cleanly with no validation errors
- An instance created for an agent with `"system-log"` in tools.allow has the four tools available
