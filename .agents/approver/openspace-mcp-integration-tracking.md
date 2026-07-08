# Tracking: OpenSpace MCP Integration

## Iteration 001 (2026-07-08)
**Status:** REJECTED

### Blocking Issues

1. **`get_base_config()` vs `build_config()` transport divergence in HTTP mode**
   - The plan designs `get_base_config()` to always return STDIO, while `build_config()` returns HTTP when `ENS_OPENSPACE_REMOTE_URL` is set.
   - `_init_warmup_pool()` at `daemon/manager.py:1033` calls `get_base_config()` (NOT `build_config()`), then checks `transport != "stdio"` at line 1034.
   - In HTTP mode: DB record gets HTTP config (from `build_config({})` at line 904), but the warmup pool registers OpenSpace as STDIO and spawns a subprocess that the runtime never uses.
   - This is a NEW bug pattern — today's built-ins (webfetch, context7) don't have this because both methods return identical transport.
   - **Expected:** HTTP-mode OpenSpace should be skipped by warmup pool (cold discovery only).
   - **Found:** HTTP-mode OpenSpace gets registered in warmup pool as STDIO, spawning a useless subprocess.
   - **Fix:** Either override `get_base_config()` to reflect env-driven transport, OR change line 1033 to call `build_config({})` instead of `get_base_config()` (3-line patch, recommended).

### Notes (non-blocking)
- Cold-start connection timeout: STDIO uses `STDIO_DEFAULT_TIMEOUT=30s` (not the 15s passed to `connect_instance`). Should fit LiteLLM init. Plan should mention adding `timeout` field to OpenSpace schema for headroom if needed.
- `_tool_discovery_cache` is keyed by `server_name` only (not `(name, timeout)`). Fine in normal operation since registration is once per startup. Worth a co-lifecycle comment.
- Phase 4 (deferred utilities) is well-scoped and correctly marked optional.

---

## Iteration 002 (2026-07-08)
**Status:** REJECTED

### Previous Issue — RESOLVED ✅
- **Transport divergence (iter 001 issue 1):** Fixed. Phase 1 Task 7 now changes `manager.py:1033` from `get_base_config()` to `build_config({})`. Verified correct against actual source code at `daemon/manager.py:1033`.

### New Blocking Issues

1. **Credential propagation claim is FALSE — `OPENSPACE_LLM_API_KEY` will NOT reach the subprocess**
   - Plan claim (D3, phase1-plan.md:125): "STDIO mode inherits `OPENSPACE_LLM_API_KEY` from the parent process `os.environ` automatically (MCP SDK merges server env with parent env)"
   - **Found:** The MCP SDK (`mcp/client/stdio/__init__.py:127`) uses `get_default_environment()`, which is a **whitelist of only 6 vars**: `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`. It does NOT merge with full `os.environ`.
   - Verified empirically: `OPENSPACE_LLM_API_KEY` is NOT in `get_default_environment()`.
   - **Impact:** OpenSpace subprocess will not receive API credentials → LLM calls fail → `execute_task` is broken.
   - **Mitigating factor:** OpenSpace's own `_load_env_once()` (`openspace/host_detection/resolver.py:39`) calls `load_dotenv()` from disk, which may partially rescue the integration in dev environments with `.env` files. But the plan's *reasoning* is factually wrong and the runtime is fragile in Docker/CI/daemon environments.
   - **Expected:** Credentials must be explicitly set in `config["env"]` via `build_config()` override so they survive the whitelist filter.
   - **Fix options:** (a) Add credential fields to the schema (plan's D3 rationale for excluding them is now invalid), (b) Explicitly read `OPENSPACE_*` keys from `os.environ` in `build_config()` and inject into config env dict.

2. **Schema env var naming mismatch — configured values won't reach OpenSpace**
   - Plan defines schema fields: `model`, `max_iterations`, `backend_scope` (phase1-plan.md:114-118).
   - Base class `build_config()` uppercases keys: `env[key.upper()]` (`base.py:108`) → produces `MODEL`, `MAX_ITERATIONS`, `BACKEND_SCOPE`.
   - OpenSpace reads: `OPENSPACE_MODEL`, `OPENSPACE_MAX_ITERATIONS`, `OPENSPACE_BACKEND_SCOPE` (confirmed: `openspace/mcp_server.py:151,154`, `openspace/__main__.py:385,387`, `openspace/config/README.md:33-34,41-42`).
   - **Impact:** User-configurable values (model, max_iterations, backend_scope) will be set in subprocess env under wrong names → OpenSpace ignores them → falls back to defaults.
   - **Expected:** Schema env var names must match what OpenSpace reads.
   - **Fix:** Either rename schema keys to `OPENSPACE_MODEL`, `OPENSPACE_MAX_ITERATIONS`, `OPENSPACE_BACKEND_SCOPE`, OR add `OPENSPACE_` prefix in the `build_config()` override when constructing env vars.

### Notes (non-blocking)
- The `build_config({})` warmup pool fix (Task 7) is verified correct and safe for existing builtins.
- Per-server timeout override (Phase 2) is well-designed, including the `timeout=0` semantics handling.
- Phase 3 innate skill and tool filter guidance is correct.
- Phase 4 (deferred utilities) remains well-scoped.
- Issues 1 and 2 are partially related — a single `build_config()` override that properly maps schema keys to `OPENSPACE_*` env vars AND injects credentials from `os.environ` would fix both simultaneously.

---

## Iteration 003 (2026-07-08)
**Status:** APPROVED

### Previous Issues — RESOLVED ✅
- **Credential injection (iter 002 issue 1):** Fixed. `build_config()` override explicitly reads `OPENSPACE_LLM_API_KEY` and `OPENSPACE_API_KEY` from `os.environ` and injects into `config["env"]`. Verified against MCP SDK source: `get_default_environment()` returns only `['HOME', 'LOGNAME', 'PATH', 'SHELL', 'TERM', 'USER']`. The `env = {**get_default_environment(), **server.env}` merge means injected vars survive. The `for var in ... if value:` guard correctly handles unset vars (empty string skip).
- **Schema env var naming (iter 002 issue 2):** Fixed. Schema keys are `openspace_model`, `openspace_max_iterations`, `openspace_backend_scope`. Base class `base.py:108` uppercases: `env[key.upper()]` → produces `OPENSPACE_MODEL`, etc. Verified OpenSpace reads `OPENSPACE_MODEL` at `mcp_server.py:149`, `OPENSPACE_MAX_ITERATIONS` at `:151`, `OPENSPACE_BACKEND_SCOPE` at `:154`.

### Verification Summary (Independent Council + Direct Code Reading)

All 8 plan claims verified against source code:
1. ✅ `manager.py:1033` currently `definition.get_base_config()` → fix to `build_config({})` is correct
2. ✅ MCP SDK `DEFAULT_INHERITED_ENV_VARS` = `['HOME', 'LOGNAME', 'PATH', 'SHELL', 'TERM', 'USER']` — 6-var whitelist confirmed
3. ✅ OpenSpace reads `OPENSPACE_MODEL`, `OPENSPACE_MAX_ITERATIONS`, `OPENSPACE_BACKEND_SCOPE`, `OPENSPACE_LLM_API_KEY` — confirmed in source
4. ✅ `base.py:108`: `env[key.upper()] = str(value)` — uppercase confirmed
5. ✅ `base.py:101`: `section = field.get("section", "args")` — defaults to args, but plan schema table explicitly specifies `section: env`
6. ✅ Schema table (phase1-plan.md:132-136) shows Section column = "env" for all fields
7. ✅ `warmup_pool.py:233`: `adapt_mcp_tools(..., tool_call_timeout=self._tool_call_timeout)` — single global, confirms need for per-server override
8. ✅ `tool_adapter.py:319`: `tool_call_timeout if tool_call_timeout > 0 else None` — confirms 0 = disable timeout semantics

### Notes (non-blocking)
- Council flagged schema `section` default, but plan explicitly specifies `section: env` in schema table (non-issue)
- Council flagged HTTP `headers: {}` missing auth, but this is correct — OpenSpace HTTP MCP endpoint doesn't require auth headers; credentials set on remote instance directly (by design, D3)
- Council flagged `os.environ.get()` returning None, but plan code uses `.get(var, "").strip()` with `if value:` guard — correctly handles unset vars
- Council flagged webfetch `build_config({})` producing different args than `get_base_config()`, but plan claim is about transport (identical=stdio) not full config; the args difference actually fixes a pre-existing warmup-vs-DB inconsistency
- `parse_config()` won't reverse-map credentials — by design (secrets excluded from schema/UI)
- Pool re-init orphaning predates this plan (pre-existing, out of scope)
