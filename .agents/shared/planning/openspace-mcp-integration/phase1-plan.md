# Phase 1: OpenSpaceServerDefinition

## Objective
Create the `OpenSpaceServerDefinition` class implementing dual-transport logic (STDIO default, HTTP/SSE optional via ENV flag), and register it in the builtin server registry. This is the core integration point — once registered, all existing MCP infrastructure (bootstrap, warmup pool, tool loading, per-agent filtering) works automatically.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/mcp/builtin_servers/__init__.py` (registration import only)
- **Shared APIs/interfaces**: Extends `BuiltinServerDefinition` ABC
- **Why this coupling**: Root phase — everything else depends on this existing

## Context
- The `BuiltinServerDefinition` ABC (`daemon/mcp/builtin_servers/base.py`) defines: `name`, `display_name`, `description`, `schema_version`, `get_base_config()`, `get_config_schema()`, `build_config()`, `parse_config()`
- Existing examples: `webfetch.py` (91 lines, overrides `build_config` for validation), `context7.py` (46 lines, minimal)
- The base `build_config()` algorithm iterates schema fields and generates `args`/`env` — OpenSpace needs a custom override because of dual-transport logic

## Dual-Transport Design

```
build_config(user_values) — resolves transport at call time
│
├── Check ENS_OPENSPACE_REMOTE_URL env var
│   ├── SET (non-empty) → HTTP/SSE mode
│   │   └── Return: {transport: "streamable-http", url: <value>, headers: {}}
│   │
│   └── UNSET (empty) → STDIO mode
│       └── Return: {transport: "stdio", command: "python3", args: ["-m", "openspace.mcp_server"], env: {...}}
```

**ENV var name**: `ENS_OPENSPACE_REMOTE_URL` (uses `ENS_` prefix to match ensemble's convention, avoids collision with OpenSpace's own `OPENSPACE_*` vars)

### ⚠️ Critical: `get_base_config()` vs `build_config()` Transport Divergence

`get_base_config()` and `build_config()` are called by different consumers:

| Consumer | Method Called | Purpose |
|----------|-------------|---------|
| `_bootstrap_builtin_servers()` (manager.py:904) | `build_config({})` | Stores resolved config in DB |
| `_init_warmup_pool()` (manager.py:1033) | **`get_base_config()`** | Decides whether to register for STDIO warmup |

**The Bug (if unaddressed):** `get_base_config()` always returns STDIO (base config). In HTTP mode, `build_config()` returns `streamable-http`, but `_init_warmup_pool()` calls `get_base_config()` which returns STDIO → passes the `transport != "stdio"` check at line 1034 → **registers OpenSpace for warmup → spawns a zombie subprocess the runtime never uses**.

**Why existing builtins don't hit this:** `webfetch` and `context7` are STDIO-only, so both methods return identical transport.

**The Fix:** Change `_init_warmup_pool()` at `daemon/manager.py:1033` to call `definition.build_config({})` instead of `definition.get_base_config()`. This makes warmup pool registration honor the resolved transport. See Task 8 below.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `OpenSpaceServerDefinition` class | New file implementing `BuiltinServerDefinition` ABC with dual-transport `build_config()` override | `daemon/mcp/builtin_servers/openspace.py` (new) |
| 2 | Implement `get_base_config()` | Return STDIO base config: `{transport: "stdio", command: "python3", args: ["-m", "openspace.mcp_server"]}` | `daemon/mcp/builtin_servers/openspace.py` |
| 3 | Implement `build_config()` override | Check `ENS_OPENSPACE_REMOTE_URL`. If set → return HTTP config. If empty → call `super().build_config()` for STDIO. | `daemon/mcp/builtin_servers/openspace.py` |
| 4 | Implement `get_config_schema()` | Define user-configurable fields with `OPENSPACE_` prefix: `openspace_model`, `openspace_max_iterations`, `openspace_backend_scope` — all as `env` section fields. Prefix ensures base class uppercasing produces correct env var names (`OPENSPACE_MODEL`, etc.). **No credential fields** — injected from `os.environ` in `build_config()` override (see D3). | `daemon/mcp/builtin_servers/openspace.py` |
| 5 | Add `tool_call_timeout` property | Override to return `900` (15 min). This is read by `_init_warmup_pool()` in Phase 2. | `daemon/mcp/builtin_servers/openspace.py` + `daemon/mcp/builtin_servers/base.py` |
| 6 | Register in `__init__.py` | Import + `_registry.register(OpenSpaceServerDefinition())` | `daemon/mcp/builtin_servers/__init__.py` |
| 7 | **Fix `_init_warmup_pool()` transport divergence** | Change `daemon/manager.py:1033` from `definition.get_base_config()` to `definition.build_config({})` so the warmup pool honors the resolved transport. Without this, HTTP mode spawns a zombie STDIO subprocess. | `daemon/manager.py` (line 1033, ~3-line change) |
| 8 | Write unit tests | Test: STDIO config generation, HTTP config generation when ENV set, schema fields produce correct `OPENSPACE_*` env var names, credential injection from `os.environ`, parse_config round-trip, disable via ENV, **warmup pool gets correct transport in both modes** | `tests/unit/mcp/test_openspace_builtin.py` (new) |

## Detailed Implementation: `build_config()` Override

```python
def build_config(self, user_values: dict[str, Any]) -> dict[str, Any]:
    # Check for remote URL override
    remote_url = os.environ.get("ENS_OPENSPACE_REMOTE_URL", "").strip()
    if remote_url:
        # HTTP/SSE transport — connect to remote OpenSpace instance
        return {
            "transport": "streamable-http",
            "url": remote_url,
            "headers": {},
        }
    
    # STDIO transport — local subprocess (default)
    # Let base class handle schema field → args/env generation
    config = super().build_config(user_values)
    
    # Ensure OPENSPACE_MCP_TRANSPORT is set to stdio explicitly
    # (prevents OpenSpace's auto-detection from picking SSE when run interactively)
    env = config.get("env", {})
    env["OPENSPACE_MCP_TRANSPORT"] = "stdio"
    
    # CRITICAL: Inject OPENSPACE_* credential vars from parent os.environ.
    # The MCP SDK's stdio_client does NOT inherit os.environ — it uses a
    # hardcoded whitelist of only 6 POSIX vars (HOME, LOGNAME, PATH, SHELL,
    # TERM, USER). Without explicit injection, OpenSpace's LLM calls fail.
    for var in ("OPENSPACE_LLM_API_KEY", "OPENSPACE_API_KEY"):
        value = os.environ.get(var, "").strip()
        if value:
            env[var] = value
    
    config["env"] = env
    
    return config
```

### Schema Key Prefixing

The base class `build_config()` uppercases schema keys (e.g., `model` → `MODEL`). OpenSpace reads `OPENSPACE_MODEL`. To bridge this, the schema field names **already include** the `OPENSPACE_` prefix (e.g., `OPENSPACE_MODEL`), and we strip the prefix in the `parse_config()` override to match form pre-fill expectations.

This is the simplest approach: the schema names are already in the `OPENSPACE_*` env var form, the base class uppercases them (no-op since they're already uppercase), and OpenSpace reads them directly. See Config Schema Fields section below.

## Detailed Implementation: Fix `_init_warmup_pool()` Transport Divergence (Task 7)

**File:** `daemon/manager.py`, line 1033

**Before (buggy):**
```python
# manager.py:1033 — uses get_base_config() which ALWAYS returns STDIO
config_dict = definition.get_base_config()
if config_dict.get("transport") != "stdio":
    continue
```

**After (fixed):**
```python
# manager.py:1033 — use build_config({}) to honor resolved transport
config_dict = definition.build_config({})
if config_dict.get("transport") != "stdio":
    continue
```

**Why this is safe for existing builtins:** For `webfetch` and `context7`, `build_config({})` and `get_base_config()` produce identical transport (both STDIO). The change is a no-op for them. Only `OpenSpaceServerDefinition` has divergent transport, and this fix makes the warmup pool correctly skip STDIO warmup when in HTTP mode.

**Risk:** `build_config({})` is slightly more expensive than `get_base_config()` (it iterates schema fields). This is called once per server during startup — negligible.

## Config Schema Fields

> **⚠️ Env Var Naming (W2 Fix):** Schema keys include the `OPENSPACE_` prefix so the base class `build_config()` produces the correct env var names (e.g., `OPENSPACE_MODEL`, not `MODEL`). The base class uppercases keys at `base.py:108` — since our keys are already prefixed and lowercase, they produce `OPENSPACE_MODEL` correctly.

| Key | Label | Type | Section | Default | Description |
|-----|-------|------|---------|---------|-------------|
| `openspace_model` | LLM Model | text | env | `openrouter/anthropic/claude-sonnet-4.5` | Model for OpenSpace's internal LLM agent (env: `OPENSPACE_MODEL`) |
| `openspace_max_iterations` | Max Iterations | number | env | `20` | Max agent iterations per execute_task call (env: `OPENSPACE_MAX_ITERATIONS`) |
| `openspace_backend_scope` | Backend Scope | text | env | `shell,gui,mcp,web,system` | Comma-separated enabled backends (env: `OPENSPACE_BACKEND_SCOPE`) |

### Credential Injection — NOT in Schema (Explicit `os.environ` Read)

Credentials (`OPENSPACE_LLM_API_KEY`, `OPENSPACE_API_KEY`) are deliberately **excluded** from the config schema. Rationale:

- They are secrets — putting them in a schema generates a form field and risks storing them visibly in the DB
- Instead, `build_config()` explicitly reads them from `os.environ` and injects into `config["env"]`

**⚠️ Critical — MCP SDK does NOT inherit full `os.environ`:**

The MCP SDK's `stdio_client` uses `get_default_environment()` which is a **hardcoded whitelist of only 6 POSIX vars**: `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`. It does NOT merge with full `os.environ`:

```python
# mcp/client/stdio/__init__.py — actual SDK code
env = {**get_default_environment(), **server.env}  # NOT os.environ
```

Without explicit injection into `config["env"]`, `OPENSPACE_LLM_API_KEY` is **absent** from the subprocess → OpenSpace LLM calls fail silently → `execute_task` broken.

The `build_config()` override handles this by reading the credential vars from `os.environ` and injecting them into the env dict:

```python
# In build_config() — STDIO mode:
for var in ("OPENSPACE_LLM_API_KEY", "OPENSPACE_API_KEY"):
    value = os.environ.get(var, "").strip()
    if value:
        env[var] = value
```

**What users do:**
```bash
# .env file — loaded by ensemble at startup into os.environ
OPENSPACE_LLM_API_KEY=sk-xxx
OPENSPACE_API_KEY=sk-xxx
```
Ensemble loads these into `os.environ` at startup. The `build_config()` override reads them and injects into `config["env"]`. The MCP SDK then passes them to the subprocess via the `server.env` merge.

For HTTP/SSE mode: credentials are configured on the **remote** OpenSpace instance, not in ensemble. The HTTP config does not include env vars.

## Key Files

| File | Purpose | Action |
|------|---------|--------|
| `daemon/mcp/builtin_servers/openspace.py` | OpenSpaceServerDefinition | **NEW** |
| `daemon/mcp/builtin_servers/base.py` | Add `tool_call_timeout` property to ABC (default None) | **MODIFY** (add 1 property) |
| `daemon/mcp/builtin_servers/__init__.py` | Register OpenSpaceServerDefinition | **MODIFY** (add 2 lines) |
| `daemon/manager.py` | Fix `_init_warmup_pool()` line 1033: `get_base_config()` → `build_config({})` | **MODIFY** (1-line change, prevents zombie subprocess in HTTP mode) |
| `tests/unit/mcp/test_openspace_builtin.py` | Unit tests | **NEW** |

## Constraints
- Must NOT break existing `webfetch` or `context7` definitions
- `build_config({})` must return a valid config even if OpenSpace is not installed (subprocess failure is handled by bootstrap)
- ENV var `ENS_OPENSPACE_REMOTE_URL` must be checked at config-build time, not at import time
- Schema version starts at `"1"`
- **`_init_warmup_pool()` fix (Task 7) must be in the same PR** — without it, HTTP mode silently spawns a zombie STDIO subprocess

## Deliverables
- [ ] `daemon/mcp/builtin_servers/openspace.py` with full `OpenSpaceServerDefinition` implementation
- [ ] `tool_call_timeout` property added to `BuiltinServerDefinition` ABC (default: `None`)
- [ ] Registration in `daemon/mcp/builtin_servers/__init__.py`
- [ ] `daemon/manager.py:1033` changed from `get_base_config()` to `build_config({})`
- [ ] Unit tests covering both transport modes, disable pattern, and **warmup pool transport correctness**

## Non-Blocking Notes

### STDIO Connection Timeout (Reviewer Note 1)
The warmup pool's `_create_pooled_connection()` uses a 60s outer timeout and 30s per-initialize-attempt timeout (`asyncio.sleep(2.0)` for cold start). `McpStdioConfig.timeout` defaults to 30s for STDIO. This should be sufficient for LiteLLM + OpenSpace initialization. If slow environments are encountered, add a `connection_timeout` field to the config schema as an escape hatch.

### `_tool_discovery_cache` Keying (Reviewer Note 2)
The warmup pool's `_tool_discovery_cache` is keyed by `server_name` only. This is fine as long as a server's transport doesn't change at runtime (which it can't — `ENS_OPENSPACE_REMOTE_URL` is a config-time decision). If the transport changes between daemon restarts, the old cache is discarded on restart anyway. No code change needed, but worth a co-lifecycle comment during implementation.
- [ ] Existing tests still pass (webfetch, context7, bootstrap)
