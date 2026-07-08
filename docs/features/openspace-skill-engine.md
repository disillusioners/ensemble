# OpenSpace Skill Engine

> OpenSpace is a self-evolving skill engine integrated into agents-ensemble as a builtin MCP server. It gives agents the ability to **search a community skill library**, **delegate complex multi-step tasks to a remote-coding-grade sub-agent**, and **evolve skills (repair, refine, or share) based on task outcomes**.

This guide explains how to install OpenSpace, configure credentials, and grant agents access to its three tools.

---

## 1. Overview

OpenSpace (project: [HKUDS/OpenSpace](https://github.com/HKUDS/OpenSpace), MIT licensed) is a separate package that plugs into agents-ensemble as the **3rd builtin MCP server** in client mode (alongside `webfetch` and `context7`). It connects to an external OpenSpace instance. When enabled, ensemble auto-spawns the OpenSpace MCP server during the bootstrap warmup phase and exposes its tools to agents under the `mcp_openspace_*` prefix.

OpenSpace ships three tools:

| Tool | Purpose |
|------|---------|
| `mcp_openspace_search_skills` | Search the local + community skill library |
| `mcp_openspace_execute_task` | Delegate a complex multi-step task to OpenSpace's internal agent |
| `mcp_openspace_skill_evolution` | Evolve the skill set — generate, refine, fix, or share skills from task outcomes |

OpenSpace brings its own dependencies (LiteLLM, Flask, rank_bm25, Pydantic v2). It is **not** bundled with ensemble — see the installation step below.

---

## 2. Installation

```bash
pip install openspace-ai
```

OpenSpace is a standalone Python package. agents-ensemble detects it at startup and, if present, registers the `openspace` builtin MCP server. To uninstall, simply `pip uninstall openspace-ai` and restart the daemon.

Verify the install:

```bash
python3 -c "import openspace; print(openspace.__version__)"
python3 -m openspace.mcp_server --help   # STDIO entrypoint used by ensemble
```

---

## 3. Environment Variables

| Variable | Required | Purpose | Default |
|----------|----------|---------|---------|
| `OPENSPACE_LLM_API_KEY` | Yes (STDIO mode) | LLM API key for OpenSpace's internal agent. Read from `os.environ` at config build time. | — |
| `OPENSPACE_API_KEY` | Optional | OpenSpace cloud community key (used by some backend operations such as `skill_evolution` cloud sharing). | Empty |
| `OPENSPACE_MODEL` | Optional | LLM model identifier for OpenSpace's embedded agent (e.g. `gpt-4o`, `claude-3-5-sonnet`). | OpenSpace default |
| `OPENSPACE_MAX_ITERATIONS` | Optional | Maximum iterations per `execute_task` call. | `20` |
| `OPENSPACE_BACKEND_SCOPE` | Optional | Comma-separated backend scope filter (e.g. `cloud,local`). | All backends |
| `ENS_OPENSPACE_REMOTE_URL` | Optional | Set to use HTTP/streamable-http transport against a remote OpenSpace instance. **Unset = STDIO subprocess (default).** | unset (STDIO) |
| `MCP_DISABLE_BUILT_IN_OPENSPACE` | Optional | Set to `true` to disable the OpenSpace builtin entirely. | unset (enabled) |

### Credential flow

The credential handling differs by transport:

**STDIO mode (default)**

OpenSpace runs as a child subprocess spawned by ensemble via the MCP SDK. The MCP SDK's `stdio_client` does **not** pass through the full `os.environ` — it only forwards a small set of POSIX variables. To work around this, ensemble reads `OPENSPACE_LLM_API_KEY` and `OPENSPACE_API_KEY` from `os.environ` at config build time and injects them into the subprocess's `env` block.

In practice, this means you should put credentials in your `.env` file exactly like any other ensemble secret:

```bash
# .env
OPENSPACE_LLM_API_KEY=sk-...
OPENSPACE_API_KEY=sk-os-...   # optional — used by cloud community features
```

Ensemble's normal env-loading machinery picks them up and hands them to OpenSpace on bootstrap.

**HTTP/streamable-http mode**

When `ENS_OPENSPACE_REMOTE_URL` is set, OpenSpace runs on a remote host (or another container) and ensemble connects over HTTP. Credentials are managed on the remote instance itself — **do not** pass `OPENSPACE_LLM_API_KEY` from the ensemble side. You only need to set the URL:

```bash
# .env
ENS_OPENSPACE_REMOTE_URL=http://openspace.internal:8080
```

---

## 4. Transport Modes

| Mode | When to use | Local install? | Credentials |
|------|-------------|----------------|-------------|
| **STDIO** (default) | Single-host dev, simple prod, OpenSpace is local | Yes (`pip install openspace-ai`) | From `os.environ` (in `.env`) |
| **HTTP / streamable-http** | OpenSpace runs as a separate service, shared deployment, no local Python deps | No | Managed on the remote side |

#### STDIO command

OpenSpace is launched as a subprocess via the daemon's Python interpreter, wrapped by the `daemon.mcp.safe_stdout` helper:

```text
sys.executable -m daemon.mcp.safe_stdout openspace.mcp_server
```

The `safe_stdout` wrapper protects the STDIO transport from `print()` corruption: it forwards JSON-RPC binary bytes through `sys.stdout.buffer` untouched, while redirecting any stray text writes (diagnostic `print()` calls inside OpenSpace or its dependencies) to `sys.stderr`. Without this wrapper, a single `print()` inside the OpenSpace process would corrupt the JSON-RPC stream and break the MCP protocol.

`webfetch` and `context7` are deliberately NOT wrapped — they use external CLIs (`uvx`, `npx`) and don't run in-process. The `safe_stdout` wrapper is opt-in per built-in.

#### HTTP / streamable-http mode

When `ENS_OPENSPACE_REMOTE_URL` is set, ensemble connects to a remote OpenSpace instance over HTTP. Two URL-level validations are enforced at config build time:

- The URL **must start with `http://` or `https://`** (other schemes like `ftp://`, `file://`, `ws://` are rejected with a `McpConfigValidationError`).
- URLs containing userinfo (`user:pass@host`) are **rejected** because the URL is persisted in the DB and surfaces in API responses.

```bash
# .env
ENS_OPENSPACE_REMOTE_URL=http://openspace.internal:8080
```

If you also set `OPENSPACE_LLM_API_KEY` or `OPENSPACE_API_KEY` while in HTTP mode, the daemon logs a per-var warning that they are ignored — credentials must be configured on the remote OpenSpace instance, not on the ensemble side.

#### Switching modes

Switching modes requires a daemon restart — the warmup pool resolves the transport at startup. Restart with:

```bash
lsof -ti:8079 | xargs kill
./dev.sh
```

---

## 4a. Security

OpenSpace credentials are handled at two layers — subprocess injection and API response redaction.

### Subprocess credential injection

In STDIO mode, the MCP SDK's `stdio_client` does not pass the full `os.environ` to the subprocess — it only forwards a small POSIX whitelist (HOME, LOGNAME, PATH, SHELL, TERM, USER). To work around this, `OpenSpaceServerDefinition.build_config()` reads `OPENSPACE_LLM_API_KEY` and `OPENSPACE_API_KEY` from `os.environ` at config build time and injects them into the subprocess's `env` dict. The MCP SDK then merges that env with the 6-var POSIX whitelist so the credentials reach OpenSpace.

This is a deliberate deviation from the daemon's other env handling: OpenSpace credentials are **never** stored in the DB — they live in the runtime environment only. After a restart, the daemon re-reads them from `os.environ`.

### API response redaction

Credentials are never exposed over the management API. The `GET /api/mcp-servers` endpoint (and the create/update/configure-builtin/reset-builtin siblings) all route their response through `redact_secrets()` in `daemon/routers/mcp_servers.py`. The redaction rule:

- For each entry in the `env` sub-dict, keys whose name contains `KEY`, `TOKEN`, `SECRET`, or `PASSWORD` (case-insensitive substring match) are replaced with the literal string `"[REDACTED]"`.
- Non-sensitive env keys (`OPENSPACE_MODEL`, `OPENSPACE_MCP_TRANSPORT`, `OPENSPACE_MAX_ITERATIONS`, `OPENSPACE_BACKEND_SCOPE`, etc.) are preserved intact.
- For HTTP-mode servers, any userinfo (`user:pass@`) in the `url` is stripped as a defense-in-depth measure, even though `build_config()` already rejects userinfo upstream.

In practice this means a `GET /api/mcp-servers/openspace` response includes the model, transport, and backends, but the API key fields show `"[REDACTED]"`. The same applies to `GET /api/mcp-servers` list responses.

### URL validation

For HTTP mode, two URL-level checks happen at `build_config()` time:

- The URL must start with `http://` or `https://` — anything else (including `ftp://`, `file://`, `ws://`) raises `McpConfigValidationError` and is translated to a 422 response.
- URLs containing userinfo (`user:pass@host`) are rejected for the same reasons above — the URL is persisted to the DB and surfaces in API responses, so embedded credentials would leak.

### SSRF protection

HTTP-mode URLs are subject to the same SSRF checks as any other MCP HTTP transport in ensemble (see [MCP Integration Guide](../mcp-integration.md#transport-types)):

- Loopback (`127.x.x.x`, `::1`) and private networks (`10.x`, `172.16-31.x`, `192.168.x`) are allowed by default.
- Link-local (`169.254.x.x`) is always blocked.
- Set `MCP_ALLOW_LOCAL=false` to block local addresses for strict SSRF protection.

---

## 4b. Graceful Degradation

OpenSpace is an **optional** dependency. If `openspace-ai` is not installed, the daemon starts cleanly without it — no error, no warning, no broken tools list.

The graceful-degradation path uses an `is_available()` pre-check on `BuiltinServerDefinition`:

```python
# daemon/mcp/builtin_servers/base.py
required_package: ClassVar[str | None] = None

@classmethod
def is_available(cls) -> bool:
    if cls.required_package is None:
        return True
    import importlib.util
    try:
        return importlib.util.find_spec(cls.required_package) is not None
    except (ImportError, ValueError):
        return False
```

`OpenSpaceServerDefinition` overrides `required_package = "openspace-ai"`. The pre-check runs at two points during startup:

1. **Bootstrap** (`_bootstrap_builtin_servers`): if `is_available()` returns `False`, the daemon skips DB record creation for OpenSpace and logs a single INFO line:
   ```text
   INFO  Builtin 'openspace' skipped — package 'openspace-ai' not installed (pip install openspace-ai)
   ```
2. **Warmup pool** (`_init_warmup_pool`): the same check runs again, logged at DEBUG level (the INFO at bootstrap is the canonical "user can act on this" message; DEBUG at warmup avoids a duplicate notice).

This is a **reusable pattern** for any built-in with external Python dependencies. To make a new built-in gracefully degrade when its package is missing, set `required_package = "<your-package>"` on the subclass — the base class handles the rest.

**No retries, no errors, no stacktraces.** A daemon running without OpenSpace simply does not register the `openspace` builtin. To enable it later, `pip install openspace-ai` and restart the daemon.

---

## 5. Agent Configuration

> **IMPORTANT — read this section carefully.**
>
> Adding `"openspace"` to `innate_skills` does **not** grant MCP tool access. The `INNATE_SKILL_TOOL_CATEGORIES` system only maps innate skills to builtin tool categories (like `external_opencode`, `chart`, etc.) — it does **not** map to dynamic MCP tool names. You must **explicitly list all three `mcp_openspace_*` tools** in `tools.allow` for an agent to see and call them.
>
> Both are required:
>
> 1. `"openspace"` in `innate_skills` → loads the skill prompt (the *how/when* documentation) into the system prompt
> 2. `mcp_openspace_*` entries in `tools.allow` → grants actual tool access
>
> Skipping step 2 means the agent reads about the tools in its prompt but cannot invoke them. Skipping step 1 means the agent has the tools but no guidance on when to use them.

### Minimal configuration (new agent)

For a new agent that should have OpenSpace as its only MCP integration:

```json
{
  "innate_skills": ["openspace"],
  "tools": {
    "allow": [
      "bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context",
      "mcp_openspace_execute_task",
      "mcp_openspace_search_skills",
      "mcp_openspace_skill_evolution"
    ]
  }
}
```

Note: `"mcp"` is the tool **category** that enables any MCP server whose tools are individually allow-listed. The three `mcp_openspace_*` lines are the individual tool grants.

### Adding to an existing agent

If your agent already has a `tools.allow` list, append the three OpenSpace tools to it. Example: an agent that already uses the opencode builtin and just wants OpenSpace added:

```json
{
  "innate_skills": ["opencode", "openspace"],
  "tools": {
    "allow": [
      "spawn_instance",
      "external_opencode_init_session",
      "mcp_openspace_search_skills",
      "mcp_openspace_execute_task",
      "mcp_openspace_skill_evolution"
    ]
  }
}
```

The order of items in `tools.allow` does not matter. If your agent also uses `tools.deny`, OpenSpace tools respect deny rules — a tool appearing in both lists is blocked.

### Edge case: `tools` is null or absent

If `meta.json` has no `tools` key (or `"tools": null`), the loader falls back to the **backward-compatible default of allowing all tools**. In that case, MCP tools are visible without explicit listing — but you also lose fine-grained control and the agent gains access to every other MCP server's tools as well.

For any new agent, always specify `tools.allow` explicitly.



---

## 6. Timeout Considerations

Each tool call is wrapped in `asyncio.timeout()`. Timeouts come from the server definition:

| Tool | Timeout | Notes |
|------|---------|-------|
| `mcp_openspace_search_skills` | 120s (default) | Fast BM25 lookup |
| `mcp_openspace_execute_task` | **900s (15 min)** | Extended because it runs a full multi-step sub-agent |
| `mcp_openspace_skill_evolution` | 120s (default) | |

If you hit a `mcp_openspace_execute_task` timeout, the task was too large for a single delegation. Break the work into smaller, more focused subtasks and call `execute_task` multiple times rather than one mega-prompt.

The default 120s timeout can be tuned globally via the `tool_call_timeout` config setting; the 900s override on `execute_task` is set in the OpenSpace server definition itself.

---

## 7. Available Tools Summary

| Tool | Timeout | Cost | When to Use |
|------|---------|------|-------------|
| `mcp_openspace_search_skills` | 120s | Low | Before building a new skill — check if one already exists in the community library |
| `mcp_openspace_execute_task` | 900s | **High (double-token)** | Delegate a complex, multi-step coding/research task to OpenSpace's internal agent. Reserve for tasks that justify the cost. |
| `mcp_openspace_skill_evolution` | 120s | Low | Evolve a skill based on task outcomes — repair a broken/outdated skill, refine an existing one, or promote a reusable skill into the library |

---

## 8. Troubleshooting

**Tools not visible to the agent**

- Verify `meta.json` has `"innate_skills": ["openspace", ...]` — required for the skill prompt to load.
- Verify `tools.allow` contains all three `mcp_openspace_*` entries — required for tool access.
- Confirm OpenSpace is installed: `python3 -c "import openspace"`.
- Confirm the builtin is not disabled: check that `MCP_DISABLE_BUILT_IN_OPENSPACE` is not set to `true`.
- Check the daemon log for the graceful-degradation INFO line:
  ```text
  Builtin 'openspace' skipped — package 'openspace-ai' not installed (pip install openspace-ai)
  ```
  If you see this, the daemon started cleanly without OpenSpace — install the package and restart.
- Restart the daemon after any config change.

**Installation error**

```bash
pip install openspace-ai
# or, in the project's venv:
.venv/bin/pip install openspace-ai
```

**Credential error in STDIO mode**

- Confirm `OPENSPACE_LLM_API_KEY` is set in `.env` (or the daemon's environment).
- Confirm the daemon was **restarted** after editing `.env` — env loading happens at startup.
- Check the daemon log for the OpenSpace subprocess spawn line; missing env vars are visible there.

**`mcp_openspace_execute_task` times out**

- 15 minutes is the hard cap. Break the task into smaller pieces.
- Each call costs roughly double the tokens of a normal agent turn — only use it for tasks where the delegation is clearly worth it.

**Zombie subprocess after switching to HTTP mode**

If you previously ran in STDIO mode and then set `ENS_OPENSPACE_REMOTE_URL`, an orphaned STDIO subprocess may linger. Restart the daemon cleanly:

```bash
lsof -ti:8079 | xargs kill
# optionally clean up any stale openspace processes:
pkill -f "openspace.mcp_server"
./dev.sh
```

**HTTP mode: tools load but every call returns an auth error**

Credentials are not passed in HTTP mode. Configure them on the **remote** OpenSpace instance, not on the ensemble side.

---

## Related Documentation

- [MCP Integration Guide](../mcp-integration.md) — full MCP client/server architecture, custom server setup
- [Agent System Guide](../AGENTS.md) — `meta.json` schema, innate skills, tools configuration
- OpenSpace source: <https://github.com/HKUDS/OpenSpace> (MIT)
