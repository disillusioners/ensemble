# OpenSpace Setup Guide

[OpenSpace](https://github.com/agents-ensemble/openspace) is an optional, **opt-in** MCP server for agents-ensemble. It provides a self-evolving skill engine that exposes `execute_task`, `search_skills`, and `skill_evolution` tools — letting agents delegate complex sub-tasks to embedded OpenSpace agents.

OpenSpace is **not bundled** with ensemble. You install and configure it separately. This guide covers installation, credentials, transport selection, agent enablement, and troubleshooting.

---

## Table of Contents

1. [What is OpenSpace?](#1-what-is-openspace)
2. [How It Integrates with Ensemble](#2-how-it-integrates-with-ensemble)
3. [Prerequisites](#3-prerequisites)
4. [Installation](#4-installation)
5. [Credentials Configuration](#5-credentials-configuration)
6. [Transport Selection](#6-transport-selection)
7. [Enabling for Agents](#7-enabling-for-agents)
8. [Timeout Configuration](#8-timeout-configuration)
9. [Troubleshooting](#9-troubleshooting)
10. [Docker](#10-docker)

---

## 1. What is OpenSpace?

OpenSpace is a self-evolving AI agent task delegation system. Key capabilities:

| Tool | Purpose |
|------|---------|
| `execute_task` | Run a full embedded OpenSpace agent (long-running, iterative) |
| `search_skills` | Find reusable skills across the local/cloud skill corpus |
| `skill_evolution` | Evolve the skill set — generate, refine, or fix skills from task outcomes |

OpenSpace maintains its own skill library and improves it over time by evolving skills based on agent feedback. This makes it useful for delegating open-ended research, multi-step reasoning, or skill-reuse work to a separate agent runtime.

---

## 2. How It Integrates with Ensemble

OpenSpace is registered as a **built-in MCP server** in ensemble. It supports **dual transport**:

| Mode | Default | How It Works |
|------|---------|--------------|
| **STDIO** | ✅ Yes | Ensemble spawns `python3 -m daemon.mcp.safe_stdout openspace.mcp_server` as a subprocess and communicates over stdin/stdout |
| **Streamable-HTTP** | Optional | Ensemble connects to a pre-existing OpenSpace HTTP endpoint via `ENS_OPENSPACE_REMOTE_URL` |

Ensemble's `BuiltinServerDefinition` framework handles transport selection, credential injection, warmup pool registration, and per-server timeout configuration. OpenSpace follows the same `build_config()` pattern as the other built-ins (`webfetch`, `context7`) but with two custom layers (see below).

#### STDIO protection: the `safe_stdout` wrapper

The STDIO command runs through the daemon's `daemon.mcp.safe_stdout` helper module. The wrapper installs itself on `sys.stdout` before importing `openspace.mcp_server`, then:

- Forwards JSON-RPC binary bytes through `sys.stdout.buffer` untouched (the protocol channel).
- Redirects any stray text writes — diagnostic `print()` calls inside OpenSpace or its dependencies — to `sys.stderr`.

Without the wrapper, a single `print()` from the OpenSpace process would land on stdout and corrupt the JSON-RPC stream, breaking the MCP protocol. The wrapper is opt-in per built-in; `webfetch` and `context7` use external CLIs and are deliberately not wrapped.

---

## 3. Prerequisites

| Requirement | Why |
|-------------|-----|
| Python 3.10+ | OpenSpace runtime |
| LLM API access | OpenSpace's embedded agents need an LLM provider (OpenAI, Anthropic, OpenRouter, etc.) |
| `pip` | OpenSpace is installed as a Python package |

### Why OpenSpace Is Not Bundled

OpenSpace pulls in heavy dependencies — LiteLLM (`<1.82.7`), Flask, `rank_bm25`, and Pydantic v2. Bundling these with ensemble risks:

- Version conflicts with ensemble's own LLM stack
- Slower installs for users who don't use OpenSpace
- Import-time side effects from LiteLLM

Instead, ensemble treats OpenSpace like a **user-provided binary** (same pattern as `uvx mcp-server-fetch` or `npx @upstash/context7-mcp`). The subprocess boundary means no shared process space, and HTTP mode has zero dependency overlap.

---

## 4. Installation

### STDIO Mode (default)

Install OpenSpace into the same Python environment as ensemble:

```bash
pip install openspace-ai
```

Verify the module is importable:

```bash
python3 -c "import openspace.mcp_server; print('ok')"
```

If you see `ModuleNotFoundError`, the install didn't land in the right environment. Repeat with the correct `pip` (e.g. `uv pip install openspace-ai` for uv-managed envs, or `python3 -m pip install openspace-ai` to target a specific interpreter).

### HTTP / Remote Mode

**No installation needed.** Point ensemble at a pre-existing OpenSpace HTTP endpoint:

```bash
export ENS_OPENSPACE_REMOTE_URL=http://your-openspace-host:port
```

The remote instance is responsible for its own dependencies and credentials.

#### URL validation (HTTP mode)

When you set `ENS_OPENSPACE_REMOTE_URL`, the value is validated at config build time:

- The URL **must** start with `http://` or `https://`. Other schemes (`ftp://`, `file://`, `ws://`, etc.) raise `McpConfigValidationError` and the build fails.
- URLs containing userinfo (`user:pass@host`) are **rejected** — the URL is persisted in the DB and surfaces in API responses, so embedded credentials would leak. Configure auth on the remote OpenSpace instance instead.

---

## 4a. Graceful Degradation (When `openspace-ai` Is Not Installed)

OpenSpace is an **optional** dependency. If `pip install openspace-ai` was never run, the daemon starts cleanly without it. The graceful-degradation path:

1. **Bootstrap** (`_bootstrap_builtin_servers`): runs an `is_available()` pre-check on the `BuiltinServerDefinition`. For OpenSpace, this consults `required_package = "openspace-ai"` and uses `importlib.util.find_spec()` to detect whether the package is importable. If not, the daemon logs a single INFO line and skips DB record creation:

   ```text
   INFO  Builtin 'openspace' skipped — package 'openspace-ai' not installed (pip install openspace-ai)
   ```

2. **Warmup pool** (`_init_warmup_pool`): the same check runs again before pool registration. A DEBUG-level log fires here (the bootstrap INFO is the canonical "user can act on this" message; DEBUG avoids a duplicate notice).

**No retries, no errors, no stacktraces.** A daemon without OpenSpace simply doesn't register the `openspace` builtin — other builtins (`webfetch`, `context7`, custom servers) continue to work normally. To enable OpenSpace later, `pip install openspace-ai` and restart the daemon.

The `is_available()` pre-check is a **reusable pattern** — any built-in with optional Python dependencies can override `required_package` to inherit the same graceful-degradation behavior.

---

## 5. Credentials Configuration

### STDIO Mode — Set in `.env` or Shell Environment

OpenSpace needs an LLM API key to power its embedded agents. Set one of these in `.env` (which ensemble loads into `os.environ` at startup) or in your shell:

```bash
# Required: LLM provider API key
OPENSPACE_LLM_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxx

# Optional: explicit model identifier
# Default: provider's default model
OPENSPACE_MODEL=openrouter/anthropic/claude-sonnet-4.5
```

#### Why Explicit Injection Is Needed

The MCP SDK's `stdio_client` does **not** inherit the full `os.environ` for subprocesses. It calls `get_default_environment()`, which returns a hardcoded **6-variable POSIX whitelist**:

```
HOME, LOGNAME, PATH, SHELL, TERM, USER
```

That's it. `OPENSPACE_LLM_API_KEY` is **not** in that list. Without explicit injection, the OpenSpace subprocess starts with no API key even when the daemon process has one set.

Ensemble's `OpenSpaceServerDefinition.build_config()` solves this by:

1. Reading `OPENSPACE_LLM_API_KEY` and `OPENSPACE_API_KEY` directly from `os.environ` at config-build time
2. Injecting them into the `env` dict that gets passed to the subprocess
3. The MCP SDK then merges this dict with its 6-var whitelist, so the key reaches OpenSpace

```
User sets OPENSPACE_LLM_API_KEY in .env
  → ensemble loads .env into os.environ at startup
  → build_config() reads os.environ, injects into config["env"]
  → MCP SDK merges {6 POSIX vars, **server.env} → includes the key
  → OpenSpace subprocess receives OPENSPACE_LLM_API_KEY=<key>
  → OpenSpace's resolver picks it up as Tier 1 credential
```

#### Optional: `OPENSPACE_API_KEY` (Tier 2 Fallback)

If you also need a separate API key for OpenSpace's cloud backend (skill search, skill evolution), set:

```bash
OPENSPACE_API_KEY=sk-os-xxxxxxxxxxxxxxxxxxxxxxxx
```

This is read alongside `OPENSPACE_LLM_API_KEY` and injected the same way.

#### Pinning the Transport

Ensemble also pins `OPENSPACE_MCP_TRANSPORT=stdio` in the subprocess env. This prevents OpenSpace's internal TTY auto-detection from picking SSE in subprocess context (where there's no TTY). You don't need to set this — ensemble handles it.

### HTTP / Remote Mode

Configure credentials **on the remote OpenSpace instance** directly. Ensemble only sends HTTP requests; it doesn't pass credentials over the wire.

#### Credential redaction in API responses

Whatever transport you choose, credentials are never exposed over the management API. The `GET /api/mcp-servers` endpoint (and its create / update / configure-builtin / reset-builtin siblings) all route their response through `redact_secrets()` in `daemon/routers/mcp_servers.py`:

- For each `env` sub-dict entry, keys whose name contains `KEY`, `TOKEN`, `SECRET`, or `PASSWORD` (case-insensitive substring match) are replaced with the literal string `"[REDACTED]"`.
- Non-sensitive env keys (`OPENSPACE_MODEL`, `OPENSPACE_MCP_TRANSPORT`, `OPENSPACE_MAX_ITERATIONS`, `OPENSPACE_BACKEND_SCOPE`) are preserved intact.
- For HTTP-mode servers, any userinfo (`user:pass@`) in the `url` is stripped as a defense-in-depth measure.

In practice: `GET /api/mcp-servers/openspace` returns the model, transport, and backends, but API key fields show `"[REDACTED]"`. The same applies to list responses.

---

## 6. Transport Selection

Transport is resolved at **config-build time** and stored in the database. Changing the transport requires a daemon restart (re-bootstrap).

| Mode | Selection | Behavior |
|------|-----------|----------|
| **STDIO** (default) | `ENS_OPENSPACE_REMOTE_URL` unset or empty | Spawns `python3 -m daemon.mcp.safe_stdout openspace.mcp_server` as subprocess |
| **Streamable-HTTP** | `ENS_OPENSPACE_REMOTE_URL=http://host:port` | Connects to remote OpenSpace HTTP endpoint |

### Default: STDIO (Zero-Config)

If you install OpenSpace and set credentials, you're done. Ensemble will spawn the local subprocess on first use.

### Remote Mode

```bash
export ENS_OPENSPACE_REMOTE_URL=http://openspace.internal:9000
python -m uvicorn daemon.api:app --reload --port 8079
```

Ensemble will detect the env var, skip the STDIO subprocess, and connect to the remote endpoint via streamable-http.

### Naming Note: `ENS_` Prefix

Ensemble uses the `ENS_` prefix for its own env vars to avoid collision with OpenSpace's own `OPENSPACE_*` variables. Don't confuse `ENS_OPENSPACE_REMOTE_URL` (ensemble's transport selector) with `OPENSPACE_MCP_TRANSPORT` (OpenSpace's internal transport, which ensemble pins to `stdio` in subprocess mode).

---

## 7. Enabling for Agents

OpenSpace tools are **not added to any agent by default**. You opt-in per-agent:

### Step 1: Add to `innate_skills`

Edit the agent's `meta.json`:

```json
{
  "id": "my-agent",
  "name": "My Agent",
  "innate_skills": ["openspace"]
}
```

This adds the `openspace` skill content to the agent's system prompt. The skill explains how to use the tools.

### Step 2: Verify Tool Access

OpenSpace tools are exposed through the `mcp` tool category, so the agent must have `mcp` in its `tools.allow` list:

```json
{
  "tools": {
    "allow": ["mcp", "knowledge", "time", "help"]
  }
}
```

Most built-in agents already include `mcp`. Custom agents must add it explicitly.

### Step 3: Restart the Daemon

Agent changes are picked up on the next bootstrap (or daemon restart, depending on the reload mode).

### Example: Full Minimal Agent

```json
{
  "id": "researcher",
  "name": "Researcher",
  "description": "Delegates long research tasks to OpenSpace",
  "icon": "🔬",
  "version": "1.0.0",
  "innate_skills": ["openspace"],
  "tools": {
    "allow": ["mcp", "knowledge", "time", "help", "self"]
  }
}
```

---

## 8. Timeout Configuration

OpenSpace's `execute_task` is **long-running by design** — it can iterate up to 20 times with up to 120s per LLM call (~40 minutes worst case). The global MCP timeout of 120 seconds is far too short.

OpenSpace overrides this with a **per-server timeout of 900 seconds (15 minutes)** via `OpenSpaceServerDefinition.tool_call_timeout`:

| Server | Default Timeout | OpenSpace Timeout |
|--------|-----------------|-------------------|
| `webfetch` | 120s | 120s |
| `context7` | 120s | 120s |
| `openspace` | 120s | **900s** |

This applies to all three OpenSpace tools (`execute_task`, `search_skills`, `skill_evolution`). The override is set in the definition class — no config file change required.

### Why 900s and Not Higher?

15 minutes is a reasonable ceiling for a single agent task delegation. Beyond that, the calling agent should probably break the task into smaller pieces. If you have a legitimate use case for longer tasks, file an issue — the timeout can be raised or made configurable.

---

## 9. Troubleshooting

### `OPENSPACE_LLM_API_KEY not set` Error

**Symptom:** OpenSpace tool call fails with `RuntimeError: OPENSPACE_LLM_API_KEY not set`.

**Fix:**

1. Check `.env` (or shell) contains `OPENSPACE_LLM_API_KEY=...`
2. Verify ensemble loaded it: `python3 -c "import os; print(os.environ.get('OPENSPACE_LLM_API_KEY')[:8])"`
3. Restart the daemon (config is built once at bootstrap, not re-read on every call)

### Subprocess Fails to Start

**Symptom:** Logs show `ModuleNotFoundError: No module named 'openspace'` or `python3 -m openspace.mcp_server` exits with code 1.

**Fix:**

1. `pip install openspace-ai` in the same environment as ensemble
2. Verify: `python3 -c "import openspace.mcp_server"` succeeds
3. Check ensemble's `python3` points to the same interpreter: `which python3` should match the daemon's interpreter

### Slow First Call

**Symptom:** First call to `execute_task` takes 5–10 seconds longer than subsequent calls.

**Cause:** STDIO mode requires Python interpreter startup, OpenSpace module import, and LiteLLM provider initialization on the first call. Subsequent calls hit the warmup pool's pre-warmed connection.

**Fix:** This is expected behavior. If you need consistently fast cold starts, switch to HTTP/remote mode and front the OpenSpace instance with a keep-alive HTTP server.

### Zombie Subprocess in HTTP Mode

**Symptom:** A second `python3 -m openspace.mcp_server` process is running even though `ENS_OPENSPACE_REMOTE_URL` is set.

**Status:** This was a known bug, **fixed** in the current ensemble release. The fix patches `_init_warmup_pool()` to call `definition.build_config({})` instead of `definition.get_base_config()`, so the warmup pool honors the resolved transport. If you see this, upgrade to a build that includes the fix.

### High-Concurrency Workloads

**Symptom:** Tool calls queue up or timeout under concurrent load.

**Fix:**

- **STDIO mode:** Each tool call shares one subprocess. Increase `mcp_pool.pool_size` in `config.yaml` to allow multiple parallel OpenSpace connections:

  ```yaml
  mcp_pool:
    pool_size: 4  # default is 1
  ```

- **HTTP mode:** Scale the remote OpenSpace instance horizontally and put it behind a load balancer. Ensemble's HTTP transport is stateless.

### Tool Not Visible to Agent

**Symptom:** Agent says "I don't have an `execute_task` tool" even though OpenSpace is installed.

**Fix:**

1. Confirm `openspace` is in the agent's `innate_skills` in `meta.json`
2. Confirm `mcp` is in the agent's `tools.allow` list
3. Restart the daemon to re-bootstrap the agent
4. Check ensemble's bootstrap logs for `OpenSpace: tool schemas loaded: ['execute_task', 'search_skills', 'skill_evolution']` (or a similar line)

---

## 10. Docker

### Including OpenSpace in Your Image

Add to your `Dockerfile`:

```dockerfile
# Ensemble
RUN pip install agents-ensemble

# OpenSpace (optional — only if you want STDIO mode in this image)
RUN pip install openspace-ai
```

Then in your `.env` or runtime env:

```bash
OPENSPACE_LLM_API_KEY=sk-or-v1-xxx
OPENSPACE_MODEL=openrouter/anthropic/claude-sonnet-4.5
```

### Remote Mode (Recommended for Production)

In production, run OpenSpace as a separate service and point ensemble at it. This gives you:

- Independent scaling of the OpenSpace service
- No LiteLLM/Flask bloat in the ensemble image
- Centralized credential management

```yaml
# docker-compose.yml (excerpt)
services:
  ensemble:
    image: agents-ensemble:latest
    environment:
      ENS_OPENSPACE_REMOTE_URL: http://openspace:9000
    depends_on:
      - openspace

  openspace:
    image: openspace-ai:latest
    environment:
      OPENSPACE_LLM_API_KEY: ${OPENSPACE_LLM_API_KEY}
    ports:
      - "9000:9000"
```

---

## See Also

- [MCP Integration](./mcp-integration.md) — How built-in MCP servers work in ensemble
- [Agent System Guide](./AGENTS.md) — Agent configuration and `meta.json` schema
- [Configuration Reference](./configuration.md) — Full `config.yaml` reference including `mcp_pool` settings
