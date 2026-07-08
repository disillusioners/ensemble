# OpenSpace Skill Engine

> OpenSpace is a self-evolving skill engine integrated into agents-ensemble as a builtin MCP server. It gives agents the ability to **search a community skill library**, **delegate complex multi-step tasks to a remote-coding-grade sub-agent**, **repair broken skills**, and **share skills back to the community**.

This guide explains how to install OpenSpace, configure credentials, and grant agents access to its four tools.

---

## 1. Overview

OpenSpace (project: [HKUDS/OpenSpace](https://github.com/HKUDS/OpenSpace), MIT licensed) is a separate package that plugs into agents-ensemble as the **3rd builtin MCP server** in client mode (alongside `webfetch` and `context7`). It connects to an external OpenSpace instance. When enabled, ensemble auto-spawns the OpenSpace MCP server during the bootstrap warmup phase and exposes its tools to agents under the `mcp_openspace_*` prefix.

OpenSpace ships four tools:

| Tool | Purpose |
|------|---------|
| `mcp_openspace_search_skills` | Search the local + community skill library |
| `mcp_openspace_execute_task` | Delegate a complex multi-step task to OpenSpace's internal agent |
| `mcp_openspace_fix_skill` | Repair a broken or outdated skill |
| `mcp_openspace_upload_skill` | Share a local skill with the OpenSpace community |

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
| `OPENSPACE_API_KEY` | Optional | API key for cloud community features (used by `upload_skill`). | — |
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
OPENSPACE_API_KEY=sk-os-...   # only needed if you use upload_skill
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

Switching modes requires a daemon restart — the warmup pool resolves the transport at startup. Restart with:

```bash
lsof -ti:8079 | xargs kill
./dev.sh
```

---

## 5. Agent Configuration

> **IMPORTANT — read this section carefully.**
>
> Adding `"openspace"` to `innate_skills` does **not** grant MCP tool access. The `INNATE_SKILL_TOOL_CATEGORIES` system only maps innate skills to builtin tool categories (like `external_opencode`, `chart`, etc.) — it does **not** map to dynamic MCP tool names. You must **explicitly list all four `mcp_openspace_*` tools** in `tools.allow` for an agent to see and call them.
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
      "mcp_openspace_fix_skill",
      "mcp_openspace_upload_skill"
    ]
  }
}
```

Note: `"mcp"` is the tool **category** that enables any MCP server whose tools are individually allow-listed. The four `mcp_openspace_*` lines are the individual tool grants.

### Adding to an existing agent

If your agent already has a `tools.allow` list, append the four OpenSpace tools to it. Example: an agent that already uses the opencode builtin and just wants OpenSpace added:

```json
{
  "innate_skills": ["opencode", "openspace"],
  "tools": {
    "allow": [
      "spawn_instance",
      "external_opencode_init_session",
      "mcp_openspace_search_skills",
      "mcp_openspace_execute_task",
      "mcp_openspace_fix_skill",
      "mcp_openspace_upload_skill"
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
| `mcp_openspace_fix_skill` | 120s (default) | |
| `mcp_openspace_upload_skill` | 120s (default) | |

If you hit a `mcp_openspace_execute_task` timeout, the task was too large for a single delegation. Break the work into smaller, more focused subtasks and call `execute_task` multiple times rather than one mega-prompt.

The default 120s timeout can be tuned globally via the `tool_call_timeout` config setting; the 900s override on `execute_task` is set in the OpenSpace server definition itself.

---

## 7. Available Tools Summary

| Tool | Timeout | Cost | When to Use |
|------|---------|------|-------------|
| `mcp_openspace_search_skills` | 120s | Low | Before building a new skill — check if one already exists in the community library |
| `mcp_openspace_execute_task` | 900s | **High (double-token)** | Delegate a complex, multi-step coding/research task to OpenSpace's internal agent. Reserve for tasks that justify the cost. |
| `mcp_openspace_fix_skill` | 120s | Low | Repair a skill that is broken, outdated, or no longer works in the current environment |
| `mcp_openspace_upload_skill` | 120s | Low | Share a local skill with the community (requires `OPENSPACE_API_KEY`) |

---

## 8. Troubleshooting

**Tools not visible to the agent**

- Verify `meta.json` has `"innate_skills": ["openspace", ...]` — required for the skill prompt to load.
- Verify `tools.allow` contains all four `mcp_openspace_*` entries — required for tool access.
- Confirm OpenSpace is installed: `python3 -c "import openspace"`.
- Confirm the builtin is not disabled: check that `MCP_DISABLE_BUILT_IN_OPENSPACE` is not set to `true`.
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
