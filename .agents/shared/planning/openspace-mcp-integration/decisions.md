# Decisions: OpenSpace MCP Integration

## D1: Dual-Transport via ENV Flag (`ENS_OPENSPACE_REMOTE_URL`)

**Decision:** Use `ENS_OPENSPACE_REMOTE_URL` env var to select transport at config-build time. Transport is resolved in `build_config()`, and `_init_warmup_pool()` is patched to call `build_config({})` (not `get_base_config()`) so it honors the resolved transport.

**Rationale:**
- `ENS_` prefix avoids collision with OpenSpace's own `OPENSPACE_*` vars
- Empty/unset → STDIO (zero-config default, subprocess)
- Set → streamable-http (remote instance)
- This is a **config-time** decision — evaluated in `build_config()`, stored in DB. Changing the ENV requires daemon restart (re-bootstrap).

**⚠️ Critical divergence bug (fixed):** `_init_warmup_pool()` at `daemon/manager.py:1033` originally called `get_base_config()`, which always returns STDIO. In HTTP mode, this would pass the `transport != "stdio"` check and register OpenSpace for STDIO warmup, spawning a **zombie subprocess** the runtime never uses. Fix: change line 1033 to call `definition.build_config({})` instead. This is a no-op for existing builtins (webfetch/context7 return identical transport from both methods) but prevents the zombie for OpenSpace.

**Alternatives Considered:**
- `OPENSPACE_MCP_TRANSPORT` (OpenSpace's own var) — rejected because it controls OpenSpace's *internal* transport, not how ensemble *connects to* OpenSpace
- Separate bool flag `ENS_OPENSPACE_USE_REMOTE` — rejected, unnecessary when URL presence/absence is the natural selector
- User-configurable field in schema — rejected because transport is infrastructure-level, not user-tunable per-server
- Override `get_base_config()` to check ENV — rejected because it couples transport resolution logic into two methods (`get_base_config` + `build_config`). The `_init_warmup_pool` fix is cleaner — one canonical config resolution path via `build_config({})`.

---

## D2: Per-Server Timeout via Definition Property

**Decision:** Add `tool_call_timeout` as an optional property on `BuiltinServerDefinition` (default: `None`). OpenSpace returns `900` (15 min).

**Rationale:**
- `execute_task` is inherently long-running (up to 20 iterations × 120s = 2400s worst case)
- 120s default timeout kills it mid-execution
- 900s (15 min) covers most realistic use cases while providing a safety ceiling
- Property-based approach keeps timeout knowledge in the definition, not scattered across config files

**Why 900s and not higher:**
- 900s = 15 min is a reasonable maximum for a single LLM-agent task delegation
- Beyond this, the calling agent should probably break the task into smaller pieces
- If truly long tasks are needed, users can override via config

**Alternatives Considered:**
- Global timeout bump to 900s — rejected, would affect all servers unnecessarily
- Per-tool timeout (different timeout per tool within a server) — rejected, over-engineering for now. All OpenSpace tools can share 900s.
- Config-file-based timeout (`mcp_pool.servers.openspace.timeout`) — rejected, couples to config system when the definition already knows its needs

---

## D3: Credential Injection via Explicit `os.environ` Read (Not Schema, Not Auto-Inheritance)

**Decision:** Credentials (`OPENSPACE_LLM_API_KEY`, `OPENSPACE_API_KEY`) are excluded from the config schema. The `build_config()` override explicitly reads them from `os.environ` and injects into `config["env"]`.

**Rationale:**

1. **MCP SDK does NOT inherit full `os.environ`** (the previous assumption was wrong):
   - `mcp.client.stdio.stdio_client` uses `env = {**get_default_environment(), **server.env}`
   - `get_default_environment()` returns a hardcoded whitelist of only 6 POSIX vars: `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`
   - `OPENSPACE_LLM_API_KEY` is **absent** unless explicitly in `server.env` (= `config["env"]`)

2. **Why not in schema:** Credentials are secrets. Schema fields generate form fields in the UI and are stored in the DB config. Better to read from `os.environ` at build time.

3. **The fix:** `build_config()` explicitly reads `OPENSPACE_LLM_API_KEY` and `OPENSPACE_API_KEY` from `os.environ` and injects them into the env dict:

```python
for var in ("OPENSPACE_LLM_API_KEY", "OPENSPACE_API_KEY"):
    value = os.environ.get(var, "").strip()
    if value:
        env[var] = value
```

**Flow (STDIO mode):**
```
User sets OPENSPACE_LLM_API_KEY in .env
  → ensemble loads .env into os.environ at startup
  → build_config() reads os.environ, injects into config["env"]
  → MCP SDK merges: {**6_POSIX_vars, **server.env} → includes the key
  → OpenSpace subprocess receives OPENSPACE_LLM_API_KEY=<key>
  → OpenSpace's resolver picks it up as Tier 1 credential
```

**Flow (HTTP/SSE mode):**
```
User sets credentials on remote OpenSpace instance
  → ensemble connects via HTTP — no credential passing needed
```

**Previous approach (rejected):** Earlier plan revisions claimed STDIO inherits `os.environ` automatically. This was wrong — verified against MCP SDK source (`mcp/client/stdio/__init__.py`). The `get_default_environment()` whitelist is by design (security: don't leak arbitrary env vars to subprocesses).

---

## D4: Dependency Management — User Responsibility

**Decision:** OpenSpace is NOT bundled with ensemble. Users install separately via `pip install openspace-ai`.

**Rationale:**
- OpenSpace has heavy deps: LiteLLM (<1.82.7), Flask, rank_bm25, Pydantic v2
- Adding these to ensemble's requirements risks version conflicts
- STDIO mode: OpenSpace runs as separate subprocess — no shared process space, no import conflicts
- HTTP/SSE mode: Zero dependency overlap — ensemble just makes HTTP calls
- Following the pattern of `uvx`/`npx` for webfetch/context7: those tools are also "user-provided" binaries

**Failure Mode:**
- If `python3 -m openspace.mcp_server` fails → bootstrap logs error, continues
- If user calls an OpenSpace tool → `ToolException` with message about installation
- Non-fatal: ensemble starts fine without OpenSpace

**Alternatives Considered:**
- Optional dependency group (`pip install agents-ensemble[openspace]`) — possible future enhancement, but deferred. The subprocess model makes it unnecessary for initial integration.
- Docker image with OpenSpace pre-installed — document as option, don't enforce

---

## D5: Warmup Pool — `build_config({})` Fix Required for Transport Selection

**Decision:** Patch `_init_warmup_pool()` to call `definition.build_config({})` instead of `definition.get_base_config()` so the resolved transport is honored during warmup pool registration.

**Rationale:**
- `_init_warmup_pool()` at `daemon/manager.py:1033` originally called `get_base_config()` to get the transport for the `!= "stdio"` check at line 1034
- For dual-transport builtins like OpenSpace, `get_base_config()` always returns STDIO → HTTP mode would incorrectly register for warmup → zombie subprocess
- Changing to `build_config({})` makes warmup pool registration honor the same resolved config that's stored in the DB
- This is a 1-line change, safe for existing builtins (no-op when both methods return the same transport)

**Warmup behavior after fix:**
- STDIO mode: `build_config({})` returns STDIO → passes the check → registers with warmup pool → pre-warmed connection ✅
- HTTP/SSE mode: `build_config({})` returns `streamable-http` → fails the `!= "stdio"` check → skips warmup → cold discovery ✅

**Other warmup pool changes:** Per-server timeout (Phase 2, D2).

---

## D6: No Default Agent Assignment

**Decision:** OpenSpace tools are NOT added to any agent by default. Users opt-in via `meta.json`.

**Rationale:**
- OpenSpace requires separate installation + credentials
- Showing broken tools to agents that can't use them wastes context tokens
- Users who want OpenSpace add `"openspace"` to `innate_skills` and configure `tools.allow`
- Consistent with how `opencode` skill works (opt-in)

---

## D7: Schema Version "1"

**Decision:** Start at schema version `"1"`.

**Rationale:**
- First version of the OpenSpace builtin definition
- Schema version is used for drift detection — if we change `get_config_schema()` later, bump to `"2"` and bootstrap auto-updates DB config
