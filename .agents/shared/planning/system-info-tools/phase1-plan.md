# Phase 1: `system.py` Tool Module

## Objective
Create `daemon/tools/system.py` — a new tool module following the factory
pattern (`create_system_tools`) that exposes 3 read-only `system_*` tools
for inspecting ensemble runtime state: environment variables, resolved
configuration, and system health.

## Coupling
- **Depends on**: None
- **Coupling type**: — (root phase)
- **Shared files with other phases**: `daemon/tools/system.py` (Phase 2 imports from here)
- **Shared APIs/interfaces**: `create_system_tools(manager, current_instance_id)` — Phase 2 calls this
- **Why this coupling**: Phase 2 wires the factory into `create_instance_tools()` and tests it; it needs the exact function signature and tool names defined here.

## Context
- This phase creates a standalone module that follows the exact pattern of
  `daemon/tools/context_tools.py` (factory + `@register_tool_category` + `@tool`)
- Config access is via the injected `manager` object:
  - `manager.config` → `Config` (the top-level Pydantic `Config` class aggregating `LLMConfig`, `DaemonConfig`, etc.)
  - `manager.ensemble_config` → `EnsembleConfig | None` (DB backend selection)
- Version: `daemon.__version__` (currently `"0.8.1"`)

## Proposed Tools

### 1. `system_env` — Inspect environment variables
```python
@register_tool_category("system")
@tool
async def system_env(
    prefix: str | None = None,
    include_secrets: bool = False,
) -> str:
    """List relevant ensemble environment variables. Use tool_help("system_env") for details."""
```

**Returns**: JSON string of `{var_name: value}` for the curated set of
tracked env vars. When `include_secrets=False` (default), values matching
the secret mask patterns are replaced with `"****<last4>"`.

**Parameters**:
- `prefix` (optional): Filter to vars starting with this prefix (e.g., `"OPENAI_"`, `"POSTGRES_"`). Case-insensitive.
- `include_secrets` (default `False`): When `True`, shows full secret values. **Gaia does not get this — it must stay `False` by default for safety. The parameter exists for devops agents that may need it.**

**Tracked env vars** (curated allowlist — NOT `os.environ` dump):
```
ENSEMBLE_*          — system config (DATA_DIR, CONFIG, etc.)
OPENAI_*            — LLM config (BASE_URL, MODEL, etc.) — api_key masked
POSTGRES_*          — DB connection (host, port, db, user) — password masked
RAG_IS_REQUIRED     — feature flag
MCP_*               — MCP config (excluding secrets)
LIGHTRAG_*          — RAG config
DATABASE_URL_POSTGRES — connection URL (password masked)
QUEUE_DISCARD_ON_STARTUP — queue behavior flag
TEMP, TMP           — temp dir paths
```

### 2. `system_config` — Inspect resolved configuration
```python
@register_tool_category("system")
@tool
async def system_config(
    section: str | None = None,
) -> str:
    """Show resolved configuration settings. Use tool_help("system_config") for details."""
```

**Returns**: JSON string of the resolved `Config` object (via `.model_dump()`),
with secrets masked. When `section` is provided (e.g., `"llm"`, `"daemon"`,
`"limits"`, `"persistence"`, `"queue"`, `"compaction"`, `"services"`,
`"job_system"`, `"mcp_pool"`), returns only that section.

**Parameters**:
- `section` (optional): One of the config section names. Invalid section → error message listing valid sections.

**Secret masking in config**:
- `config.llm.api_key` → `"****<last4>"`
- `config.ensemble_config.postgres.password` → `"****<last4>"`

### 3. `system_health` — System health & status
```python
@register_tool_category("system")
@tool
async def system_health() -> str:
    """Show system health and runtime status. Use tool_help("system_health") for details."""
```

**Returns**: JSON string with:
```json
{
  "version": "0.8.1",
  "database": "postgres",
  "postgres_env_available": true,
  "rag_enabled": true,
  "config_sections": ["llm", "daemon", "limits", ...],
  "python_version": "3.13.x",
  "platform": "darwin"
}
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create module skeleton | Module docstring, imports, `CATEGORY_NAME`, `CATEGORY_DOC`, logging | `daemon/tools/system.py` (new) |
| 2 | Implement `_mask_secret(value)` helper | Masks values: `****` + last 4 chars. Empty/short values → `"****"`. Type-safe (handles None, non-str). | `daemon/tools/system.py` (new) |
| 3 | Define env var allowlist constant | `_TRACKED_ENV_PREFIXES` (list of prefixes) and `_SECRET_ENV_VARS` (set of exact names to always mask) | `daemon/tools/system.py` (new) |
| 4 | Implement `system_env` tool | Reads curated env vars via `os.environ.get()`, applies masking, supports `prefix` filter | `daemon/tools/system.py` (new) |
| 5 | Implement `system_config` tool | Reads `manager.config`, calls `.model_dump()`, masks secrets, supports `section` filter | `daemon/tools/system.py` (new) |
| 6 | Implement `system_health` tool | Reads version, DB type, RAG status, platform info | `daemon/tools/system.py` (new) |
| 7 | Implement `create_system_tools` factory | Closure factory capturing `manager` + `current_instance_id`, returns `[system_env, system_config, system_health]` | `daemon/tools/system.py` (new) |
| 8 | Attach `_full_doc_` to all 3 tools | Follow the pattern from `time.py` — detailed docstring attached post-definition | `daemon/tools/system.py` (new) |

## Key Files
- `daemon/tools/system.py` (NEW) — The entire module
- `daemon/tools/context_tools.py` (REFERENCE) — Pattern to follow for factory + category registration
- `daemon/tools/time.py` (REFERENCE) — Pattern for `_full_doc_` attachment + standalone tool
- `daemon/config.py` (REFERENCE) — `Config` class and section model shapes
- `daemon/ensemble_config.py` (REFERENCE) — `EnsembleConfig` shape for health endpoint
- `daemon/__init__.py` (REFERENCE) — `__version__` import

## Constraints
- **Read-only**: No tool accepts parameters that mutate config, env, or state
- **Secret masking is non-negotiable**: `api_key`, `password`, and any env var in `_SECRET_ENV_VARS` must never appear in plaintext when `include_secrets=False`
- **No `os.environ` full dump**: Only the curated allowlist of tracked env vars
- **Factory pattern**: `create_system_tools(manager, current_instance_id)` — matches existing factories
- **Category name**: `"system"` — auto-infers from `system_*` tool name prefix
- **All tools are async**: Match the `context_tools.py` pattern (use `async def`)

## Deliverables
- [ ] `daemon/tools/system.py` created with module docstring
- [ ] `_mask_secret()` helper implemented and handles edge cases
- [ ] `_TRACKED_ENV_PREFIXES` and `_SECRET_ENV_VARS` constants defined
- [ ] `system_env` tool implemented with prefix filter + masking
- [ ] `system_config` tool implemented with section filter + masking
- [ ] `system_health` tool implemented with version/DB/RAG/platform info
- [ ] `create_system_tools()` factory returns `[system_env, system_config, system_health]`
- [ ] All 3 tools have `_full_doc_` attached
- [ ] All 3 tools decorated with `@register_tool_category("system")`
- [ ] Module importable with no side effects
