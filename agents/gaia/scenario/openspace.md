# OpenSpace (Self-Evolving Skill Engine)

OpenSpace is an **optional, opt-in** MCP server for ensemble. It provides a self-evolving skill engine that exposes `execute_task`, `search_skills`, `fix_skill`, and `upload_skill` tools — letting agents delegate complex sub-tasks to embedded OpenSpace agents, repair/refine skills, and publish skills to the community.

OpenSpace is **not bundled** with ensemble. You install and configure it separately.

> **Full reference:** `docs/openspace-setup.md` is the source of truth. This script summarizes the install + readiness-check steps. When in doubt, defer to that doc.

---

## What is OpenSpace?

OpenSpace is a self-evolving AI agent task delegation system. It maintains its own skill library and improves it over time by evolving skills based on agent feedback.

| Tool | Purpose |
|------|---------|
| `execute_task` | Run a full embedded OpenSpace agent (long-running, iterative) |
| `search_skills` | Find reusable skills across the local/cloud skill corpus |
| `fix_skill` | Repair or refine an existing skill based on feedback or error analysis |
| `upload_skill` | Upload a skill to the OpenSpace community skill repository |

### Why OpenSpace Matters for This Project

Ensemble registers OpenSpace as a **built-in MCP server**. When installed, it lets agents (e.g. `worker`) delegate open-ended research, multi-step reasoning, and skill-reuse work to a separate agent runtime. When **not** installed, the daemon starts cleanly without it (graceful degradation) — other built-ins (`webfetch`, `context7`, custom servers) keep working.

---

## Prerequisites

| Requirement | Why |
|-------------|-----|
| Python 3.10+ | OpenSpace runtime |
| `pip` (or `uv pip`) | OpenSpace is installed as a Python package |
| LLM API access | OpenSpace's embedded agents need an LLM provider (OpenAI, Anthropic, OpenRouter, etc.) |

### Python check

```bash
python3 --version   # must be >= 3.10
```

If `python3` is missing or too old, install Python 3.10+ first:

- **macOS:** `brew install python@3.12`
- **Linux (Debian/Ubuntu):** `sudo apt-get install -y python3 python3-pip`
- **Linux (Fedora/RHEL):** `sudo dnf install -y python3 python3-pip`
- **Windows:** download from https://www.python.org (or `winget install Python.Python.3.12`)

---

## Installation

OpenSpace supports two transports. Choose **one**.

### Path A: STDIO Mode (default — recommended to start)

Install OpenSpace into the **same Python environment as ensemble**:

```bash
pip install openspace-ai
```

> [!IMPORTANT]
> The PyPI package is `openspace-ai`, but the **importable module** is `openspace` and the **CLI** is `openspace-mcp`. Don't try to `pip install openspace` — use `openspace-ai`.

If the install didn't land in the right environment, repeat with the interpreter-matching form:

```bash
# uv-managed environments (ensemble uses uv)
uv pip install openspace-ai

# Target a specific interpreter explicitly
python3 -m pip install openspace-ai
```

### Path B: HTTP / Remote Mode (no install needed)

If you run OpenSpace as a separate pre-existing service, **do not install it locally**. Just point ensemble at it:

```bash
export ENS_OPENSPACE_REMOTE_URL=http://your-openspace-host:port
```

The remote instance owns its own dependencies and credentials. URL validation at config-build time:

- The URL **must** start with `http://` or `https://` (other schemes like `ftp://`, `file://`, `ws://` raise `McpConfigValidationError`).
- URLs containing userinfo (`user:pass@host`) are **rejected** — the URL is persisted in the DB and surfaces in API responses. Configure auth on the remote instance instead.

For the rest of this script, readiness checks below apply to **STDIO mode**. HTTP mode is "ready" once `ENS_OPENSPACE_REMOTE_URL` is set to a reachable endpoint.

---

## Credentials Configuration (STDIO mode)

OpenSpace needs an LLM API key to power its embedded agents. Set these in `.env` (ensemble loads it into `os.environ` at startup) or in your shell:

```bash
# Required: LLM provider API key
OPENSPACE_LLM_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxx

# Optional: explicit model identifier (default: provider's default model)
OPENSPACE_MODEL=openrouter/anthropic/claude-sonnet-4.5

# Optional: custom LLM endpoint — self-hosted / on-prem gateways, vLLM, LiteLLM-proxy
OPENSPACE_LLM_API_BASE=https://llm.internal.corp/v1

# Optional: extra HTTP headers for LLM requests, as a JSON string
OPENSPACE_LLM_EXTRA_HEADERS=
```

**Cloud publishing (optional):** to publish skills via `upload_skill`, also set:

```bash
OPENSPACE_API_KEY=sk-os-xxxxxxxxxxxxxxxxxxxxxxxx
```

> **Why explicit injection is required:** the MCP SDK's `stdio_client` does **not** inherit the full `os.environ` for subprocesses — it forwards only 6 POSIX vars (`HOME, LOGNAME, PATH, SHELL, TERM, USER`). Ensemble's `OpenSpaceServerDefinition.build_config()` reads `OPENSPACE_LLM_API_KEY` / `OPENSPACE_API_KEY` / `OPENSPACE_LLM_API_BASE` / `OPENSPACE_LLM_EXTRA_HEADERS` from `os.environ` and injects them into the subprocess env. So they must be set **before the daemon boots** (config is built once at bootstrap).
>
> Ensemble also pins `OPENSPACE_MCP_TRANSPORT=stdio` automatically — you don't set that yourself.

---

## Verification

After the user installs OpenSpace and sets credentials, verify readiness. Gaia runs **only read-only checks** — never install on the user's behalf.

### 1. Module is importable (the core readiness check)

This is the exact check ensemble's subprocess performs:

```bash
python3 -c "import openspace.mcp_server; print('ok')"
```

**Expected output:** `ok`

If this prints `ok`, the package is installed and importable in the active interpreter.

### 2. CLI works

```bash
openspace-mcp --help
```

**Expected output:** a help/usage message and exit code 0.

### 3. Interpreter alignment (critical)

The `python3` ensemble uses for the subprocess must be the same one OpenSpace was installed into:

```bash
which python3
```

Compare this path against the interpreter the daemon runs under. If they differ, OpenSpace won't be found by the subprocess — reinstall into the daemon's interpreter using `python3 -m pip install openspace-ai` (with that exact `python3`).

### 4. Credentials are present

```bash
# Should print 'set' (prints the first 8 chars only — never dump the full key)
python3 -c "import os; v=os.environ.get('OPENSPACE_LLM_API_KEY'); print('set:'+v[:8]) if v else print('MISSING')"
```

**Expected output:** `set:xxxxxxxx` (first 8 chars of the key).

Repeat for optional vars as needed:

```bash
python3 -c "import os; print('OPENSPACE_MODEL:', os.environ.get('OPENSPACE_MODEL') or '(unset)')"
python3 -c "import os; print('OPENSPACE_API_KEY:', 'set' if os.environ.get('OPENSPACE_API_KEY') else '(unset)')"
```

> If the credentials are set in `.env` but show `MISSING` here, you're likely checking a different shell than the daemon. Restart the daemon — `.env` is loaded into `os.environ` only at ensemble startup, and the config is built once at bootstrap.

### 5. (Optional) Daemon bootstrap log

After restarting ensemble, check the bootstrap logs for one of:

- Success: `OpenSpace: tool schemas loaded: ['execute_task', 'search_skills', 'fix_skill', 'upload_skill']` (or similar)
- Skipped: `Builtin 'openspace' skipped — package 'openspace-ai' not installed (pip install openspace-ai)`

The "skipped" line means the package wasn't found by the daemon's interpreter — go back to step 3.

---

## Readiness Checklist

OpenSpace is **ready** when all of these are true:

| # | Check | Command | Pass when |
|---|-------|---------|-----------|
| 1 | Module importable | `python3 -c "import openspace.mcp_server; print('ok')"` | Prints `ok` |
| 2 | CLI available | `openspace-mcp --help` | Exits 0 |
| 3 | Interpreter aligned | `which python3` | Matches daemon's interpreter |
| 4 | LLM key set | `python3 -c "import os; print('set' if os.environ.get('OPENSPACE_LLM_API_KEY') else 'MISSING')"` | Prints `set` |
| 5 | Daemon sees it | bootstrap log | Tool schemas loaded, **not** "skipped" |
| 6 | (Optional) Cloud key | `python3 -c "import os; print('set' if os.environ.get('OPENSPACE_API_KEY') else 'unset')"` | `set` only if `upload_skill` is wanted |

For **HTTP/remote mode**, "ready" = `ENS_OPENSPACE_REMOTE_URL` is set to a reachable `http(s)` endpoint and the daemon boot log shows streamable-http transport.

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'openspace'`

**Cause:** OpenSpace isn't installed in the interpreter ensemble uses for the subprocess.

**Solutions:**

1. `pip install openspace-ai` in the **same environment as ensemble**.
2. Verify the import succeeds: `python3 -c "import openspace.mcp_server"`.
3. Confirm `which python3` matches the daemon's interpreter. If not, install explicitly: `python3 -m pip install openspace-ai` using that interpreter (or `uv pip install openspace-ai` for uv-managed envs).

### Builtin 'openspace' skipped — package 'openspace-ai' not installed

**Cause:** ensemble's `is_available()` pre-check (`importlib.util.find_spec`) didn't find the package, so no DB record or connection is attempted. This is **graceful degradation**, not a crash — other built-ins still work.

**Solution:** install into the daemon's interpreter and restart the daemon.

### `RuntimeError: OPENSPACE_LLM_API_KEY not set`

**Cause:** the credential wasn't injected into the subprocess env.

**Solutions:**

1. Confirm `.env` (or shell) contains `OPENSPACE_LLM_API_KEY=...`.
2. Verify ensemble loaded it: `python3 -c "import os; print(os.environ.get('OPENSPACE_LLM_API_KEY')[:8])"`.
3. **Restart the daemon** — config is built once at bootstrap, not re-read per call.

### `openspace-mcp: command not found`

**Cause:** the CLI entry point isn't on PATH (common after installing with `--user` or into a venv that isn't activated).

**Solutions:**

1. Ensure the venv/env where you installed OpenSpace is activated in the shell ensemble runs from.
2. Or invoke via module instead: `python3 -m openspace.mcp_server --help`.
3. Confirm with `which openspace-mcp`.

### First call to `execute_task` is slow (5–10s)

**Cause:** STDIO mode requires Python startup, OpenSpace module import, and LiteLLM provider initialization on the first call. Subsequent calls hit the warmup pool.

**Status:** expected behavior. For consistently fast cold starts, switch to HTTP/remote mode with a keep-alive server.

### Tool not visible to the agent ("I don't have an execute_task tool")

**Cause:** OpenSpace is installed but not enabled for the agent.

**Solutions:**

1. Confirm `"openspace"` is in the agent's `innate_skills` in `meta.json`.
2. Confirm `"mcp"` is in the agent's `tools.allow` list.
3. Restart the daemon to re-bootstrap the agent.
4. Check the bootstrap log for the "tool schemas loaded" line.

### High-concurrency timeouts / queued calls

**Cause:** STDIO mode shares one subprocess; the default pool size is 1.

**Solution:** raise the pool size in `config.yaml`:

```yaml
mcp_pool:
  pool_size: 4   # default is 1
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Install (STDIO) | `pip install openspace-ai` |
| Install (uv env) | `uv pip install openspace-ai` |
| Install (explicit interpreter) | `python3 -m pip install openspace-ai` |
| Verify importable | `python3 -c "import openspace.mcp_server; print('ok')"` |
| Verify CLI | `openspace-mcp --help` |
| Check interpreter | `which python3` |
| Check LLM key | `python3 -c "import os; print('set' if os.environ.get('OPENSPACE_LLM_API_KEY') else 'MISSING')"` |
| Enable remote mode | `export ENS_OPENSPACE_REMOTE_URL=http://host:port` |
| Cloud key (optional) | `export OPENSPACE_API_KEY=sk-os-...` |

### Transport cheat sheet

| Mode | When | Config |
|------|------|--------|
| **STDIO** (default) | OpenSpace installed locally | `pip install openspace-ai` + credentials in `.env` |
| **Streamable-HTTP** | Remote OpenSpace service | `ENS_OPENSPACE_REMOTE_URL=http(s)://host:port` (no local install) |

---

## Next Steps

Once OpenSpace is installed and the readiness checklist passes, enable it per-agent: add `"openspace"` to the agent's `innate_skills` and ensure `"mcp"` is in its `tools.allow`, then restart the daemon. See `docs/openspace-setup.md` §7 (Enabling for Agents) and §8 (Timeout Configuration — OpenSpace uses a 900s per-server timeout for its long-running `execute_task`).
